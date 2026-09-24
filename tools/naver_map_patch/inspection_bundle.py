from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
from typing import TypeAlias
import zipfile

from tools.naver_map_patch.inspect_tools import InspectionError, sha256_file
from tools.naver_map_patch.profile import InspectionProfile


JsonScalar: TypeAlias = None | bool | int | float | str
JsonValue: TypeAlias = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]
_MAX_METADATA_BYTES = 64 * 1024


@dataclass(frozen=True, slots=True)
class MaterializedCompanion:
  base_size: int
  base_sha256: str
  split_paths: tuple[Path, ...]


@contextmanager
def materialized_companion(source: Path, profile: InspectionProfile) -> Iterator[MaterializedCompanion]:
  if source.is_file():
    with _archive_companion(source, profile) as materialized:
      yield materialized
    return
  if source.is_dir():
    yield _directory_companion(source, profile)
    return
  raise InspectionError("split_source", "companion split source is not a regular file or directory")


@contextmanager
def _archive_companion(source: Path, profile: InspectionProfile) -> Iterator[MaterializedCompanion]:
  try:
    with zipfile.ZipFile(source, "r") as archive:
      infos = archive.infolist()
      names = tuple(info.filename for info in infos)
      if len(names) != len(set(names)):
        raise InspectionError("malformed_bundle", "companion archive contains duplicate entries")
      _validate_apk_names(names, profile)
      _validate_metadata(_read_metadata_archive(archive), profile)
      base_info = archive.getinfo(profile.bundle_base_entry)
      base_sha256 = _hash_zip_entry(archive, base_info)
      with tempfile.TemporaryDirectory(prefix="naver-profile-inspect-") as temporary_directory:
        root = Path(temporary_directory)
        split_paths = tuple(root / split.entry for split in profile.splits)
        for split, destination in zip(profile.splits, split_paths, strict=True):
          with archive.open(split.entry, "r") as input_stream, destination.open("wb") as output_stream:
            shutil.copyfileobj(input_stream, output_stream, length=1024 * 1024)
        yield MaterializedCompanion(base_info.file_size, base_sha256, split_paths)
  except InspectionError:
    raise
  except (OSError, KeyError, RuntimeError, zipfile.BadZipFile) as error:
    raise InspectionError("malformed_bundle", "companion archive is unreadable or incomplete") from error


def _directory_companion(source: Path, profile: InspectionProfile) -> MaterializedCompanion:
  try:
    names = tuple(path.name for path in source.glob("*.apk") if path.is_file())
    _validate_apk_names(names, profile)
    _validate_metadata(_read_metadata_file(source / "apk+.json"), profile)
    base = source / profile.bundle_base_entry
    splits = tuple(source / split.entry for split in profile.splits)
    if not base.is_file() or not all(path.is_file() for path in splits):
      raise InspectionError("split_set", "companion APK entries do not match profile")
    return MaterializedCompanion(base.stat().st_size, sha256_file(base), splits)
  except InspectionError:
    raise
  except OSError as error:
    raise InspectionError("split_source", "companion directory is unreadable") from error


def _validate_apk_names(names: tuple[str, ...], profile: InspectionProfile) -> None:
  expected = {profile.bundle_base_entry, *(split.entry for split in profile.splits)}
  actual = {name for name in names if name.lower().endswith(".apk")}
  if actual != expected:
    raise InspectionError("split_set", "companion APK entries do not match profile")


def _read_metadata_archive(archive: zipfile.ZipFile) -> JsonValue:
  try:
    info = archive.getinfo("apk+.json")
    if info.file_size > _MAX_METADATA_BYTES:
      raise InspectionError("bundle_metadata", "apk+.json exceeds the inspection limit")
    with archive.open(info, "r") as stream:
      return _decode_metadata(stream.read(_MAX_METADATA_BYTES + 1))
  except KeyError as error:
    raise InspectionError("bundle_metadata", "companion apk+.json is missing") from error


def _read_metadata_file(path: Path) -> JsonValue:
  try:
    if path.stat().st_size > _MAX_METADATA_BYTES:
      raise InspectionError("bundle_metadata", "apk+.json exceeds the inspection limit")
    return _decode_metadata(path.read_bytes())
  except FileNotFoundError as error:
    raise InspectionError("bundle_metadata", "companion apk+.json is missing") from error
  except OSError as error:
    raise InspectionError("bundle_metadata", "companion apk+.json is unreadable") from error


def _decode_metadata(content: bytes) -> JsonValue:
  try:
    return json.loads(content.decode("utf-8"))
  except (UnicodeDecodeError, json.JSONDecodeError) as error:
    raise InspectionError("bundle_metadata", "companion apk+.json is malformed") from error


def _validate_metadata(value: JsonValue, profile: InspectionProfile) -> None:
  if not isinstance(value, dict):
    raise InspectionError("bundle_metadata", "companion apk+.json must be an object")
  if (
    value.get("package_name") != profile.package_name
    or value.get("version_code") != profile.version_code
    or value.get("version_name") != profile.version_name
  ):
    raise InspectionError("bundle_metadata", "companion apk+.json package/version does not match profile")


def _hash_zip_entry(archive: zipfile.ZipFile, info: zipfile.ZipInfo) -> str:
  digest = hashlib.sha256()
  with archive.open(info, "r") as stream:
    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
      digest.update(chunk)
  return digest.hexdigest()
