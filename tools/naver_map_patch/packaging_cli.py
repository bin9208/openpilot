from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from enum import StrEnum, unique
import json
import os
from pathlib import Path
import sys
from typing import assert_never

from tools.naver_map_patch.diagnostic_patch import (
  FIELD_ACCEPTANCE_BUILD_ID,
  build_diagnostic_archive_patches,
  build_field_acceptance_archive_patches,
  build_production_archive_patches,
  diagnostic_payload_identity,
  field_acceptance_payload_identity,
  installed_diagnostic_payload_identity,
  installed_payload_mode,
  installed_production_payload_identity,
  production_payload_identity,
  verify_field_acceptance_archive,
  verify_production_archive,
)
from tools.naver_map_patch.inspect_tools import InspectionError, verify_profiled_dex_entries
from tools.naver_map_patch.profile import InspectionProfile, ProfileError, load_profile
from tools.naver_map_patch.split_bundle import read_apk_plus_metadata
from tools.naver_map_patch.split_package import (
  ApkPlusMetadata,
  ArchivePatch,
  BuildRequest,
  ErrorCode,
  PackagingError,
  PayloadIdentity,
  SigningConfig,
  VerificationReport,
  VerifyRequest,
)
from tools.naver_map_patch.split_packaging import build_release, verify_release
from tools.naver_map_patch.split_tools import sha256_file
from tools.naver_map_patch.packaging_cli_args import (
  BuildArguments,
  IdentityPayload,
  InstallCommandPayload,
  OutputArguments,
  PackagingUsageError,
  _android_tools,
  _parse_build_arguments,
  _parse_output_arguments,
  _patch_profile,
  _read_patches,
)


@unique
class PackagingCommand(StrEnum):
  BUILD = "build"
  VERIFY = "verify"
  INSTALL_COMMAND = "install-command"


def _build_patches(
  arguments: BuildArguments,
  profile: InspectionProfile,
  base: Path,
  environment: Mapping[str, str],
) -> tuple[ArchivePatch, ...]:
  patches = _read_patches(arguments)
  if arguments.diagnostic and not patches:
    return build_diagnostic_archive_patches(base, profile, environment)
  if arguments.field_acceptance and not patches:
    return build_field_acceptance_archive_patches(base, profile, environment)
  if arguments.production and not patches:
    return build_production_archive_patches(base, profile, environment)
  return patches


def _install_command(output_root: Path, metadata: ApkPlusMetadata) -> InstallCommandPayload:
  install_set = output_root / "install-set"
  command_path = install_set / "install-command.json"
  try:
    command = json.loads(command_path.read_text(encoding="utf-8"))
  except (OSError, UnicodeError, json.JSONDecodeError) as error:
    raise PackagingError(ErrorCode.OUTPUT_HASH_MISMATCH, "install command is unreadable", path=command_path) from error
  names = [metadata.base.file_name, *(item.file_name for item in metadata.splits)]
  expected = ["adb", "install-multiple", "-r", *names]
  if (
    not isinstance(command, dict)
    or command.get("schema_version") != 1
    or command.get("requires_all_splits") is not True
    or command.get("argv") != expected
  ):
    raise PackagingError(ErrorCode.OUTPUT_HASH_MISMATCH, "install command must name base and every required split", path=command_path)
  ps1 = install_set / "install.ps1"
  sh = install_set / "install.sh"
  ps1_args = " ".join(f'"{name}"' for name in names)
  shell_args = " ".join(f"'./{name}'" for name in names)
  expected_ps1 = f"adb install-multiple -r {ps1_args}\n"
  expected_sh = f"#!/bin/sh\nset -eu\nadb install-multiple -r {shell_args}\n"
  try:
    if ps1.read_text(encoding="utf-8") != expected_ps1 or sh.read_text(encoding="utf-8") != expected_sh:
      raise PackagingError(ErrorCode.OUTPUT_HASH_MISMATCH, "install scripts do not match the verified split set", path=install_set)
  except (OSError, UnicodeError) as error:
    raise PackagingError(ErrorCode.OUTPUT_HASH_MISMATCH, "install scripts are unreadable", path=install_set) from error
  return InstallCommandPayload(
    argv=command["argv"], requires_all_splits=command["requires_all_splits"], schema_version=command["schema_version"],
  )


def _identities(report: VerificationReport) -> list[IdentityPayload]:
  return [
    {
      "path": str(item.path), "sha256": str(sha256_file(item.path)), "package_name": item.package_name,
      "version_code": item.version_code, "version_name": item.version_name, "split_name": item.split_name,
      "signer_sha256": item.signer_sha256, "required_split_types": list(item.required_split_types),
    }
    for item in report.apks
  ]


def _payload_identity(identity: PayloadIdentity) -> dict[str, str]:
  return {
    "mode": identity.mode,
    "build_id": identity.build_id,
    "dex_entry": identity.dex_entry,
    "sha256": identity.sha256,
  }


def _installed_payload_identity(
  output_root: Path,
  base_apk: Path,
  profile: InspectionProfile,
) -> PayloadIdentity:
  provenance_path = output_root / "install-set" / "provenance.json"
  try:
    provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
  except (OSError, UnicodeError, json.JSONDecodeError) as error:
    raise PackagingError(ErrorCode.OUTPUT_HASH_MISMATCH, "provenance is unreadable", path=provenance_path) from error
  if not isinstance(provenance, dict):
    raise PackagingError(ErrorCode.OUTPUT_HASH_MISMATCH, "provenance is malformed", path=provenance_path)
  claimed = provenance.get("payload_identity")
  if (
    provenance.get("schema_version") != 1
    or not isinstance(claimed, dict)
    or set(claimed) != {"mode", "build_id", "dex_entry", "sha256"}
  ):
    raise PackagingError(ErrorCode.OUTPUT_HASH_MISMATCH, "provenance payload identity is malformed", path=provenance_path)
  actual_mode = installed_payload_mode(base_apk, profile)
  if claimed.get("mode") != actual_mode:
    raise PackagingError(ErrorCode.OUTPUT_HASH_MISMATCH, "provenance payload mode differs from the actual DEX", path=provenance_path)
  if actual_mode == "diagnostic":
    identity = installed_diagnostic_payload_identity(base_apk, profile)
  elif actual_mode == "production":
    identity = installed_production_payload_identity(base_apk, profile)
  else:
    raise PackagingError(ErrorCode.OUTPUT_HASH_MISMATCH, "payload mode is unsupported", path=base_apk)
  if claimed != _payload_identity(identity):
    raise PackagingError(ErrorCode.OUTPUT_HASH_MISMATCH, "provenance payload identity differs from the actual DEX", path=provenance_path)
  return identity


def _run_build(arguments: BuildArguments, environment: Mapping[str, str]) -> str:
  inspection_profile = load_profile(arguments.profile)
  payload_mode = "production" if arguments.production else "diagnostic"
  profile = _patch_profile(inspection_profile, payload_mode)
  base = arguments.base.expanduser().resolve()
  companion = arguments.companion_source.expanduser().resolve()
  if base == companion or base.is_relative_to(companion):
    raise PackagingError(ErrorCode.UNSAFE_PATH, "authoritative base must be separate from companion source", path=base)
  try:
    verify_profiled_dex_entries(base, inspection_profile.dex_entries)
  except InspectionError as error:
    raise PackagingError(ErrorCode.WRONG_HASH, error.message, path=base) from error
  tools = _android_tools(environment)
  patches = _build_patches(arguments, inspection_profile, base, environment)
  if arguments.production:
    payload_identity = production_payload_identity(inspection_profile, patches)
  elif arguments.field_acceptance:
    payload_identity = field_acceptance_payload_identity(inspection_profile, patches)
  else:
    payload_identity = diagnostic_payload_identity(inspection_profile, patches)
  prepublish_base_verifier: Callable[[Path], None] | None = None
  if arguments.production:
    def verify_staged_production_base(staged_base: Path) -> None:
      verify_production_archive(staged_base, inspection_profile, environment)

    prepublish_base_verifier = verify_staged_production_base
  elif arguments.field_acceptance:
    def verify_staged_field_base(staged_base: Path) -> None:
      verify_field_acceptance_archive(staged_base, inspection_profile)

    prepublish_base_verifier = verify_staged_field_base
  request = BuildRequest(
    profile=profile, base_apk=base, companion_source=companion, patches=patches,
    payload_identity=payload_identity,
    output_root=arguments.output.expanduser().resolve(),
    signer=SigningConfig(arguments.keystore.expanduser().resolve(), arguments.alias,
                         arguments.store_password_env, arguments.key_password_env),
    tools=tools, installed_signer_sha256=arguments.installed_signer,
    prepublish_base_verifier=prepublish_base_verifier,
  )
  result = build_release(request)
  verification = verify_release(VerifyRequest(profile, result.output_root, tools, payload_identity))
  metadata = read_apk_plus_metadata(result.install_set / "apk+.json")
  command = _install_command(result.output_root, metadata)
  payload = {
    "schema_version": 1, "command": "build", "success": True, "profile": profile.profile_id,
    "diagnostic": arguments.diagnostic, "field_acceptance": arguments.field_acceptance,
    "production": arguments.production,
    "output_root": str(result.output_root), "bundle": str(result.bundle_path),
    "install_set": str(result.install_set), "provenance": str(result.provenance_path),
    "signer_sha256": verification.signer_sha256, "apks": _identities(verification), "install_command": command,
    "payload_identity": _payload_identity(verification.payload_identity),
  }
  return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _run_verify(arguments: OutputArguments, environment: Mapping[str, str]) -> str:
  inspection_profile = load_profile(arguments.profile)
  tools = _android_tools(environment)
  output_root = arguments.output.expanduser().resolve()
  base_apk = output_root / "install-set" / inspection_profile.bundle_base_entry
  payload_identity = _installed_payload_identity(output_root, base_apk, inspection_profile)
  profile = _patch_profile(inspection_profile, payload_identity.mode)
  if payload_identity.mode == "production":
    verify_production_archive(base_apk, inspection_profile, environment)
  elif payload_identity.build_id == FIELD_ACCEPTANCE_BUILD_ID:
    verify_field_acceptance_archive(base_apk, inspection_profile)
  report = verify_release(VerifyRequest(profile, output_root, tools, payload_identity))
  metadata = read_apk_plus_metadata(output_root / "install-set" / "apk+.json")
  command = _install_command(output_root, metadata)
  payload = {
    "schema_version": 1, "command": "verify", "success": True, "profile": profile.profile_id,
    "output_root": str(output_root), "signer_sha256": report.signer_sha256, "apks": _identities(report),
    "install_command": command, "payload_identity": _payload_identity(report.payload_identity),
  }
  return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _run_install_command(arguments: OutputArguments, environment: Mapping[str, str]) -> str:
  inspection_profile = load_profile(arguments.profile)
  tools = _android_tools(environment)
  output_root = arguments.output.expanduser().resolve()
  base_apk = output_root / "install-set" / inspection_profile.bundle_base_entry
  payload_identity = _installed_payload_identity(output_root, base_apk, inspection_profile)
  profile = _patch_profile(inspection_profile, payload_identity.mode)
  if payload_identity.mode == "production":
    verify_production_archive(base_apk, inspection_profile, environment)
  elif payload_identity.build_id == FIELD_ACCEPTANCE_BUILD_ID:
    verify_field_acceptance_archive(base_apk, inspection_profile)
  report = verify_release(VerifyRequest(profile, output_root, tools, payload_identity))
  metadata = read_apk_plus_metadata(output_root / "install-set" / "apk+.json")
  command = _install_command(output_root, metadata)
  payload = {
    "schema_version": 1, "command": "install-command", "success": True, "profile": profile.profile_id,
    "output_root": str(output_root), "signer_sha256": report.signer_sha256, "argv": command["argv"],
    "requires_all_splits": True, "install_set": str(output_root / "install-set"),
  }
  return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _error_json(command: str, code: str, detail: str, path: Path | None = None) -> str:
  payload: dict[str, str | bool | int] = {
    "schema_version": 1, "command": command, "success": False, "code": code, "detail": detail,
  }
  if path is not None:
    payload["path"] = str(path)
  return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def run_packaging_cli(arguments: Sequence[str], environment: Mapping[str, str] | None = None) -> int:
  selected = tuple(arguments)
  if not selected:
    print("usage: naver_patch.py build|verify|install-command ...", file=sys.stderr)
    return 64
  command_name = selected[0]
  try:
    env = os.environ if environment is None else environment
    try:
      command = PackagingCommand(command_name)
    except ValueError as error:
      raise PackagingUsageError(f"unsupported command: {command_name}") from error
    match command:
      case PackagingCommand.BUILD:
        output = _run_build(_parse_build_arguments(selected), env)
      case PackagingCommand.VERIFY:
        output = _run_verify(_parse_output_arguments(selected, "verify"), env)
      case PackagingCommand.INSTALL_COMMAND:
        output = _run_install_command(_parse_output_arguments(selected, "install-command"), env)
      case unreachable:
        assert_never(unreachable)
  except PackagingUsageError as error:
    print(f"{command_name} usage error: {error}", file=sys.stderr)
    return 64
  except ProfileError as error:
    print(_error_json(command_name, "profile", str(error)))
    return 2
  except PackagingError as error:
    print(_error_json(command_name, error.code.value, error.detail, error.path))
    return 2
  print(output)
  return 0


__all__ = [
  "BuildArguments", "OutputArguments", "PackagingUsageError", "_parse_build_arguments", "run_packaging_cli",
]
