from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
import json
from pathlib import Path
import shutil
from typing import Final, assert_never
import zipfile

from tools.naver_map_patch.split_metadata import JsonValue, parse_apk_plus_metadata, read_apk_plus_metadata
from tools.naver_map_patch.split_package import (
  AndroidTools,
  ApkFile,
  ApkPlusMetadata,
  ErrorCode,
  PackagingError,
  PatchProfile,
  PayloadIdentity,
  Sha256,
  SplitFile,
)
from tools.naver_map_patch.split_tools import sha256_file


_VENDOR_METADATA_KEYS: Final = frozenset({
  "app_name", "min_sdk_version", "package_name", "version_code", "version_name",
})


@contextmanager
def materialized_companion(
  source: Path, workspace: Path, profile: PatchProfile,
) -> Iterator[tuple[ApkPlusMetadata, Path]]:
  if source.is_dir():
    metadata = read_apk_plus_metadata(source / "apk+.json")
    _validate_source_names(source, metadata)
    yield metadata, source
    return
  if not source.is_file():
    raise PackagingError(ErrorCode.MALFORMED_METADATA, "companion source is not a directory or apk+ file", path=source)
  destination = workspace / "companion"
  destination.mkdir()
  try:
    with zipfile.ZipFile(source, "r") as archive:
      names = archive.namelist()
      if len(set(names)) != len(names) or "apk+.json" not in names:
        raise PackagingError(ErrorCode.MALFORMED_METADATA, "companion bundle has duplicate entries or no apk+.json", path=source)
      metadata = _archive_metadata(archive.read("apk+.json").decode("utf-8"), profile)
      allowed = {"apk+.json", metadata.base.file_name, *(item.file_name for item in metadata.splits)}
      apk_names = {name for name in names if name.lower().endswith(".apk")}
      if not apk_names <= allowed:
        raise PackagingError(ErrorCode.DUPLICATE_SPLIT, "companion bundle contains undeclared APK entries", path=source)
      for item in metadata.splits:
        if item.file_name not in names:
          raise PackagingError(ErrorCode.MISSING_SPLIT, "declared split is missing from companion bundle", path=Path(item.file_name))
        target = destination / item.file_name
        with archive.open(item.file_name, "r") as input_stream, target.open("wb") as output_stream:
          shutil.copyfileobj(input_stream, output_stream, length=1024 * 1024)
  except (OSError, UnicodeError, zipfile.BadZipFile, KeyError) as error:
    raise PackagingError(ErrorCode.MALFORMED_METADATA, "companion bundle is unreadable", path=source) from error
  yield metadata, destination


def _archive_metadata(text: str, profile: PatchProfile) -> ApkPlusMetadata:
  try:
    return parse_apk_plus_metadata(text)
  except PackagingError as strict_error:
    try:
      decoded: JsonValue = json.loads(text)
    except json.JSONDecodeError:
      raise strict_error from None
    match decoded:
      case {
        "app_name": str(app_name), "min_sdk_version": int(min_sdk_version),
        "package_name": str(package_name), "version_code": int(version_code),
        "version_name": str(version_name),
      } if (
        set(decoded) == _VENDOR_METADATA_KEYS and app_name
        and not isinstance(min_sdk_version, bool) and not isinstance(version_code, bool)
      ):
        if (package_name, version_code, version_name) != (
          profile.package_name, profile.version_code, profile.version_name,
        ):
          raise PackagingError(ErrorCode.WRONG_VERSION, "vendor apk+.json package/version does not match profile")
        return ApkPlusMetadata(
          schema_version=1, package_name=profile.package_name, version_code=profile.version_code,
          version_name=profile.version_name, required_split_types=profile.required_split_types,
          base=ApkFile(profile.base_file_name, profile.expected_base_sha256),
          splits=tuple(
            SplitFile(item.file_name, item.split_name, item.split_type, item.sha256)
            for item in profile.expected_splits
          ),
        )
      case str() | int() | float() | bool() | None | list() | dict():
        raise strict_error from None
      case unreachable:
        assert_never(unreachable)


def _validate_source_names(source: Path, metadata: ApkPlusMetadata) -> None:
  expected = {item.file_name for item in metadata.splits}
  missing = next((name for name in sorted(expected) if not (source / name).is_file()), None)
  if missing is not None:
    raise PackagingError(ErrorCode.MISSING_SPLIT, "declared split is missing", path=source / missing)
  allowed = expected | {metadata.base.file_name}
  unexpected = next((path for path in sorted(source.glob("*.apk")) if path.name not in allowed), None)
  if unexpected is not None:
    raise PackagingError(ErrorCode.DUPLICATE_SPLIT, "companion directory contains an undeclared APK", path=unexpected)


def regenerated_metadata(source: ApkPlusMetadata, install_set: Path) -> ApkPlusMetadata:
  splits = tuple(
    replace(item, sha256=sha256_file(install_set / item.file_name)) for item in source.splits
  )
  return replace(
    source,
    base=ApkFile(source.base.file_name, sha256_file(install_set / source.base.file_name)),
    splits=splits,
  )


def provenance_text(
  profile: PatchProfile, metadata: ApkPlusMetadata, tools: AndroidTools, signer_sha256: Sha256,
  payload_identity: PayloadIdentity,
) -> str:
  expected_by_name = {item.split_name: item for item in profile.expected_splits}
  input_records = [{"file_name": metadata.base.file_name, "sha256": profile.expected_base_sha256}]
  input_records.extend(
    {"file_name": item.file_name, "sha256": expected_by_name[item.split_name].sha256} for item in metadata.splits
  )
  output_records = [{"file_name": metadata.base.file_name, "sha256": metadata.base.sha256}]
  output_records.extend({"file_name": item.file_name, "sha256": item.sha256} for item in metadata.splits)
  payload = {
    "anchor_counts": {item.name: item.count for item in sorted(profile.anchor_counts, key=lambda item: item.name)},
    "inputs": input_records,
    "outputs": output_records,
    "payload_identity": {
      "mode": payload_identity.mode,
      "build_id": payload_identity.build_id,
      "dex_entry": payload_identity.dex_entry,
      "sha256": payload_identity.sha256,
    },
    "profile_id": profile.profile_id,
    "schema_version": 1,
    "signer_sha256": signer_sha256,
    "steps": ["repack", "zipalign", "sign", "verify"],
    "tools": [{"name": item.name, "version": item.version} for item in sorted(tools.versions, key=lambda item: item.name)],
  }
  return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def write_distribution(staged_root: Path, metadata: ApkPlusMetadata, provenance: str) -> tuple[Path, Path]:
  install_set = staged_root / "install-set"
  metadata_path = install_set / "apk+.json"
  provenance_path = install_set / "provenance.json"
  metadata_path.write_text(metadata.to_json(), encoding="utf-8", newline="\n")
  provenance_path.write_text(provenance, encoding="utf-8", newline="\n")
  apk_names = (metadata.base.file_name, *(item.file_name for item in metadata.splits))
  command = {"argv": ["adb", "install-multiple", "-r", *apk_names], "requires_all_splits": True, "schema_version": 1}
  (install_set / "install-command.json").write_text(
    json.dumps(command, sort_keys=True, separators=(",", ":")), encoding="utf-8", newline="\n",
  )
  quoted = " ".join(f'"{name}"' for name in apk_names)
  (install_set / "install.ps1").write_text(f"adb install-multiple -r {quoted}\n", encoding="utf-8", newline="\n")
  shell_quoted = " ".join(f"'./{name}'" for name in apk_names)
  (install_set / "install.sh").write_text(
    f"#!/bin/sh\nset -eu\nadb install-multiple -r {shell_quoted}\n",
    encoding="utf-8",
    newline="\n",
  )
  bundle = staged_root / "patched.apk+"
  with zipfile.ZipFile(bundle, "w", compression=zipfile.ZIP_STORED) as archive:
    for path in sorted(install_set.iterdir(), key=lambda item: item.name):
      info = zipfile.ZipInfo(path.name, date_time=(1980, 1, 1, 0, 0, 0))
      info.compress_type = zipfile.ZIP_STORED
      archive.writestr(info, path.read_bytes())
  return bundle, provenance_path


def verify_bundle(bundle: Path, install_set: Path) -> None:
  try:
    with zipfile.ZipFile(bundle, "r") as archive:
      expected = {path.name for path in install_set.iterdir() if path.is_file()}
      if set(archive.namelist()) != expected:
        raise PackagingError(ErrorCode.OUTPUT_HASH_MISMATCH, "outer bundle entries do not match the install set", path=bundle)
      for path in install_set.iterdir():
        if path.is_file() and archive.read(path.name) != path.read_bytes():
          raise PackagingError(ErrorCode.OUTPUT_HASH_MISMATCH, "outer bundle content differs from install set", path=path)
  except (OSError, zipfile.BadZipFile, KeyError) as error:
    raise PackagingError(ErrorCode.OUTPUT_HASH_MISMATCH, "outer bundle is unreadable", path=bundle) from error
