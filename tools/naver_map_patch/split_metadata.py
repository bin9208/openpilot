from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
from typing import TypeAlias, assert_never

from tools.naver_map_patch.split_package import (
  ApkFile,
  ApkPlusMetadata,
  ErrorCode,
  PackagingError,
  Sha256,
  SplitFile,
)


JsonScalar: TypeAlias = str | int | float | bool | None
JsonValue: TypeAlias = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_WINDOWS_INVALID_FILENAME_CHARACTERS = frozenset('<>:"/\\|?*')
_WINDOWS_RESERVED_FILENAME_STEMS = frozenset({
  "CON", "PRN", "AUX", "NUL",
  *(f"COM{number}" for number in range(1, 10)),
  *(f"LPT{number}" for number in range(1, 10)),
})


def parse_apk_plus_metadata(text: str) -> ApkPlusMetadata:
  try:
    decoded: JsonValue = json.loads(text)
  except json.JSONDecodeError as error:
    raise PackagingError(ErrorCode.MALFORMED_METADATA, "apk+.json is not valid JSON") from error
  match decoded:
    case dict() as root:
      return _metadata(root)
    case str() | int() | float() | bool() | None | list():
      raise PackagingError(ErrorCode.MALFORMED_METADATA, "apk+.json root must be an object")
    case unreachable:
      assert_never(unreachable)


def read_apk_plus_metadata(path: Path) -> ApkPlusMetadata:
  try:
    return parse_apk_plus_metadata(path.read_text(encoding="utf-8"))
  except (OSError, UnicodeError) as error:
    raise PackagingError(ErrorCode.MALFORMED_METADATA, "apk+.json is not readable UTF-8", path=path) from error


def _metadata(root: dict[str, JsonValue]) -> ApkPlusMetadata:
  schema_version = _integer(root, "schema_version")
  if schema_version != 1:
    raise PackagingError(ErrorCode.MALFORMED_METADATA, "unsupported apk+.json schema_version")
  required_types = tuple(sorted(_string_list(root, "required_split_types")))
  base = _base_file(_mapping(root, "base"))
  split_values = _list(root, "splits")
  splits = tuple(sorted((_split_file(_as_mapping(value, "split item")) for value in split_values), key=lambda item: (item.split_type, item.split_name)))
  if not splits:
    raise PackagingError(ErrorCode.BASE_ONLY, "base APK requires ABI and density companion splits")
  split_types = tuple(item.split_type for item in splits)
  if len(set(split_types)) != len(split_types):
    raise PackagingError(ErrorCode.DUPLICATE_SPLIT, "each required split type must occur exactly once")
  if set(split_types) != set(required_types):
    raise PackagingError(ErrorCode.MISSING_SPLIT, "metadata does not cover every required split type")
  _unique(item.file_name for item in splits)
  _unique(item.split_name for item in splits)
  return ApkPlusMetadata(
    schema_version=1,
    package_name=_string(root, "package_name"),
    version_code=_integer(root, "version_code"),
    version_name=_string(root, "version_name"),
    required_split_types=required_types,
    base=base,
    splits=splits,
  )


def _base_file(value: dict[str, JsonValue]) -> ApkFile:
  return ApkFile(_file_name(value, "file_name"), _digest(value, "sha256"))


def _split_file(value: dict[str, JsonValue]) -> SplitFile:
  return SplitFile(
    file_name=_file_name(value, "file_name"),
    split_name=_string(value, "split_name"),
    split_type=_string(value, "split_type"),
    sha256=_digest(value, "sha256"),
  )


def _file_name(value: dict[str, JsonValue], key: str) -> str:
  raw = _string(value, key)
  posix = PurePosixPath(raw)
  windows = PureWindowsPath(raw)
  windows_stem = raw.split(".", 1)[0].rstrip(" ").upper()
  unsafe = (
    posix.name != raw
    or windows.name != raw
    or posix.is_absolute()
    or windows.is_absolute()
    or bool(windows.drive)
    or bool(windows.root)
    or raw.endswith((" ", "."))
    or any(ord(character) < 32 or character in _WINDOWS_INVALID_FILENAME_CHARACTERS for character in raw)
    or not windows_stem
    or windows_stem in _WINDOWS_RESERVED_FILENAME_STEMS
    or not raw.lower().endswith(".apk")
  )
  if unsafe:
    raise PackagingError(ErrorCode.UNSAFE_PATH, "APK filename must be one safe relative component", path=Path(raw))
  return raw


def _digest(value: dict[str, JsonValue], key: str) -> Sha256:
  raw = _string(value, key).lower()
  if _SHA256.fullmatch(raw) is None:
    raise PackagingError(ErrorCode.MALFORMED_METADATA, f"{key} must be a lowercase SHA-256 digest")
  return Sha256(raw)


def _string(value: dict[str, JsonValue], key: str) -> str:
  selected = value.get(key)
  match selected:
    case str() as text if text:
      return text
    case str() | int() | float() | bool() | None | list() | dict():
      raise PackagingError(ErrorCode.MALFORMED_METADATA, f"{key} must be a non-empty string")
    case unreachable:
      assert_never(unreachable)


def _integer(value: dict[str, JsonValue], key: str) -> int:
  selected = value.get(key)
  match selected:
    case bool():
      raise PackagingError(ErrorCode.MALFORMED_METADATA, f"{key} must be an integer")
    case int() as number:
      return number
    case str() | float() | None | list() | dict():
      raise PackagingError(ErrorCode.MALFORMED_METADATA, f"{key} must be an integer")
    case unreachable:
      assert_never(unreachable)


def _string_list(value: dict[str, JsonValue], key: str) -> list[str]:
  return [_as_string(item, key) for item in _list(value, key)]


def _list(value: dict[str, JsonValue], key: str) -> list[JsonValue]:
  selected = value.get(key)
  match selected:
    case list() as items:
      return items
    case str() | int() | float() | bool() | None | dict():
      raise PackagingError(ErrorCode.MALFORMED_METADATA, f"{key} must be a list")
    case unreachable:
      assert_never(unreachable)


def _mapping(value: dict[str, JsonValue], key: str) -> dict[str, JsonValue]:
  return _as_mapping(value.get(key), key)


def _as_mapping(value: JsonValue, label: str) -> dict[str, JsonValue]:
  match value:
    case dict() as mapping:
      return mapping
    case str() | int() | float() | bool() | None | list():
      raise PackagingError(ErrorCode.MALFORMED_METADATA, f"{label} must be an object")
    case unreachable:
      assert_never(unreachable)


def _as_string(value: JsonValue, label: str) -> str:
  match value:
    case str() as text if text:
      return text
    case str() | int() | float() | bool() | None | list() | dict():
      raise PackagingError(ErrorCode.MALFORMED_METADATA, f"{label} items must be non-empty strings")
    case unreachable:
      assert_never(unreachable)


def _unique(values: Iterable[str]) -> None:
  selected = tuple(values)
  if len(set(selected)) != len(selected):
    raise PackagingError(ErrorCode.DUPLICATE_SPLIT, "split filenames and names must be unique")
