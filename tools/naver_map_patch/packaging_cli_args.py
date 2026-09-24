from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
import re
from typing import Final, TypedDict

from tools.naver_map_patch.naver_patch import DoctorReport, doctor
from tools.naver_map_patch.profile import InspectionProfile, load_profile
from tools.naver_map_patch.split_package import (
  AnchorCount as PackageAnchorCount,
  AndroidTools,
  ArchivePatch,
  ErrorCode,
  PackagingError,
  PatchProfile,
  ProfileId,
  Sha256,
  SplitExpectation,
  ToolVersion,
)


_HEX_DIGEST: Final = re.compile(r"[0-9a-fA-F]{64}\Z")
_ENV_NAME: Final = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")


class PackagingUsageError(RuntimeError):
  pass


@dataclass(frozen=True, slots=True)
class PatchArgument:
  entry_name: str
  path: Path


@dataclass(frozen=True, slots=True)
class BuildArguments:
  base: Path
  companion_source: Path
  profile: str
  output: Path
  keystore: Path
  alias: str | None
  store_password_env: str
  key_password_env: str
  patches: tuple[PatchArgument, ...]
  diagnostic: bool
  field_acceptance: bool
  production: bool
  installed_signer: Sha256 | None


@dataclass(frozen=True, slots=True)
class OutputArguments:
  output: Path
  profile: str


class InstallCommandPayload(TypedDict):
  argv: list[str]
  requires_all_splits: bool
  schema_version: int


class IdentityPayload(TypedDict):
  path: str
  sha256: str
  package_name: str
  version_code: int
  version_name: str
  split_name: str | None
  signer_sha256: str
  required_split_types: list[str]


def _value(tokens: tuple[str, ...], index: int, option: str) -> tuple[str, int]:
  if index + 1 >= len(tokens) or tokens[index + 1].startswith("--"):
    raise PackagingUsageError(f"{option} requires exactly one value")
  return tokens[index + 1], index + 2


def _required(values: Mapping[str, str], required: tuple[str, ...], json_requested: bool) -> None:
  missing = tuple(item for item in required if item not in values)
  if missing or not json_requested:
    suffix = ("--json",) if not json_requested else ()
    raise PackagingUsageError(f"missing required argument(s): {', '.join((*missing, *suffix))}")


def _parse_patch(raw: str, tokens: tuple[str, ...], index: int) -> tuple[PatchArgument, int]:
  if "=" in raw:
    entry, path = raw.split("=", 1)
  else:
    path, index = _value(tokens, index, "--patch path")
    entry = raw
  if not entry or not path:
    raise PackagingUsageError("--patch requires ENTRY=PATH or ENTRY PATH")
  return PatchArgument(entry, Path(path)), index


def _parse_build_arguments(arguments: Sequence[str]) -> BuildArguments:
  tokens = tuple(arguments)
  if not tokens or tokens[0] != "build":
    raise PackagingUsageError("command must start with build")
  values: dict[str, str] = {}
  patches: list[PatchArgument] = []
  diagnostic = False
  field_acceptance = False
  production = False
  installed_signer: Sha256 | None = None
  json_requested = False
  index = 1
  while index < len(tokens):
    token = tokens[index]
    if token == "--json":
      if json_requested:
        raise PackagingUsageError("--json may be specified only once")
      json_requested = True
      index += 1
      continue
    if token == "--diagnostic":
      if diagnostic:
        raise PackagingUsageError("--diagnostic may be specified only once")
      diagnostic = True
      index += 1
      continue
    if token == "--production":
      if production:
        raise PackagingUsageError("--production may be specified only once")
      production = True
      index += 1
      continue
    if token == "--field-acceptance":
      if field_acceptance:
        raise PackagingUsageError("--field-acceptance may be specified only once")
      field_acceptance = True
      index += 1
      continue
    if token in ("--patch", "--patch-file"):
      raw, index = _value(tokens, index, token)
      if token == "--patch-file":
        patches.append(PatchArgument(Path(raw).name, Path(raw)))
      else:
        patch, index = _parse_patch(raw, tokens, index)
        patches.append(patch)
      continue
    if token == "--installed-signer-sha256":
      raw, index = _value(tokens, index, token)
      if _HEX_DIGEST.fullmatch(raw) is None:
        raise PackagingUsageError("--installed-signer-sha256 must be a SHA-256 digest")
      installed_signer = Sha256(raw.lower())
      continue
    if token not in (
      "--base", "--splits-from", "--profile", "--output", "--output-root", "--keystore", "--alias",
      "--store-password-env", "--key-password-env",
    ):
      raise PackagingUsageError(f"unsupported argument: {token}")
    raw, index = _value(tokens, index, token)
    key = "--output" if token == "--output-root" else token
    if key in values:
      raise PackagingUsageError(f"{token} may be specified only once")
    values[key] = raw
  _required(
    values,
    ("--base", "--splits-from", "--profile", "--output", "--keystore",
     "--store-password-env", "--key-password-env"),
    json_requested,
  )
  for key in ("--store-password-env", "--key-password-env"):
    if _ENV_NAME.fullmatch(values[key]) is None:
      raise PackagingUsageError(f"{key} must be a valid environment variable name")
  if sum((diagnostic, field_acceptance, production)) != 1:
    raise PackagingUsageError(
      "exactly one of --diagnostic, --field-acceptance, or --production is required"
    )
  if (production or field_acceptance) and patches:
    selected = "production" if production else "field-acceptance"
    raise PackagingUsageError(f"{selected} builds do not accept caller-supplied patches")
  return BuildArguments(
    base=Path(values["--base"]), companion_source=Path(values["--splits-from"]), profile=values["--profile"],
    output=Path(values["--output"]), keystore=Path(values["--keystore"]), alias=values.get("--alias"),
    store_password_env=values["--store-password-env"], key_password_env=values["--key-password-env"],
    patches=tuple(patches), diagnostic=diagnostic, field_acceptance=field_acceptance,
    production=production,
    installed_signer=installed_signer,
  )


def _parse_output_arguments(arguments: Sequence[str], command: str) -> OutputArguments:
  tokens = tuple(arguments)
  if not tokens or tokens[0] != command:
    raise PackagingUsageError(f"command must start with {command}")
  values: dict[str, str] = {}
  json_requested = False
  index = 1
  while index < len(tokens):
    token = tokens[index]
    if token == "--json":
      if json_requested:
        raise PackagingUsageError("--json may be specified only once")
      json_requested = True
      index += 1
      continue
    if token not in ("--output", "--output-root", "--profile"):
      raise PackagingUsageError(f"unsupported argument: {token}")
    raw, index = _value(tokens, index, token)
    key = "--output" if token == "--output-root" else token
    if key in values:
      raise PackagingUsageError(f"{token} may be specified only once")
    values[key] = raw
  _required(values, ("--output", "--profile"), json_requested)
  return OutputArguments(Path(values["--output"]), values["--profile"])


def _patch_profile(profile: InspectionProfile, payload_mode: str = "diagnostic") -> PatchProfile:
  if payload_mode not in profile.payload_modes or payload_mode not in ("diagnostic", "production"):
    raise PackagingError(ErrorCode.MALFORMED_METADATA, f"unsupported payload mode: {payload_mode}")
  anchors = profile.anchors
  if payload_mode == "production":
    expected_names = ("status", "tbt_current", "tbt_next", "safety_source", "safety", "route")
    by_name = {item.name: item for item in anchors}
    if tuple(item.name for item in anchors[:6]) != expected_names or set(by_name) != {
      *expected_names, "lane",
    }:
      raise PackagingError(ErrorCode.MALFORMED_METADATA, "production profile anchor inventory is not exact")
    anchors = tuple(by_name[name] for name in expected_names)
  return PatchProfile(
    profile_id=ProfileId(profile.name), package_name=profile.package_name,
    version_code=profile.version_code, version_name=profile.version_name, base_file_name=profile.bundle_base_entry,
    expected_base_sha256=Sha256(profile.base.sha256), original_signer_sha256=Sha256(profile.package_signer_sha256),
    required_split_types=profile.base.required_split_types,
    expected_splits=tuple(
      SplitExpectation(item.split_name, item.split_type, Sha256(item.sha256), item.entry) for item in profile.splits
    ),
    anchor_counts=tuple(PackageAnchorCount(item.name, item.expected_count) for item in anchors),
  )


def _android_tools(environment: Mapping[str, str]) -> AndroidTools:
  report: DoctorReport = doctor(environment)
  if not report.success:
    failed = tuple(item.name for item in report.requirements if not item.success)
    raise PackagingError(ErrorCode.TOOL_FAILURE, f"Android toolchain preflight failed: {','.join(failed)}")
  by_name = {item.name: item for item in report.requirements}
  java_path = Path(by_name["java"].path)
  return AndroidTools(
    aapt2=Path(by_name["aapt2"].path), zipalign=Path(by_name["zipalign"].path),
    apksigner=Path(by_name["apksigner"].path), java_home=java_path.parent.parent,
    versions=tuple(ToolVersion(name, by_name[name].version) for name in ("aapt2", "zipalign", "apksigner")),
    timeout_seconds=300.0,
  )


def _read_patches(arguments: BuildArguments) -> tuple[ArchivePatch, ...]:
  patches: list[ArchivePatch] = []
  for item in arguments.patches:
    try:
      patches.append(ArchivePatch(item.entry_name, item.path.read_bytes()))
    except OSError as error:
      raise PackagingError(ErrorCode.INVALID_PATCH_ENTRY, "DEX patch file is not readable", path=item.path) from error
  return tuple(patches)
