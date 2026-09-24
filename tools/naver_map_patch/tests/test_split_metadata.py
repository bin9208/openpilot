from __future__ import annotations

import json
from pathlib import Path
from typing import TypedDict

import pytest

from tools.naver_map_patch.split_package import ApkPlusMetadata, ErrorCode, PackagingError
from tools.naver_map_patch.split_packaging import read_apk_plus_metadata


class SplitPayload(TypedDict):
  file_name: str
  split_name: str
  split_type: str
  sha256: str


def _write_metadata(path: Path, splits: list[SplitPayload]) -> Path:
  payload = {
    "schema_version": 1,
    "package_name": "com.example.synthetic.split",
    "version_code": 60800007,
    "version_name": "6.8.0.5-test",
    "required_split_types": ["base__abi", "base__density"],
    "base": {"file_name": "base.apk", "sha256": "a" * 64},
    "splits": splits,
  }
  path.write_text(json.dumps(payload), encoding="utf-8")
  return path


def _split(file_name: str, split_name: str, split_type: str, digest: str) -> SplitPayload:
  return {"file_name": file_name, "split_name": split_name, "split_type": split_type, "sha256": digest}


def test_metadata_round_trip_is_typed_and_deterministic(tmp_path: Path) -> None:
  # Given: a valid base plus ABI and density metadata set in non-canonical order.
  metadata_path = _write_metadata(tmp_path / "apk+.json", [
    _split("density.apk", "config.xxhdpi", "base__density", "d" * 64),
    _split("abi.apk", "config.arm64_v8a", "base__abi", "b" * 64),
  ])

  # When: the trust-boundary parser loads and serializes it twice.
  metadata = read_apk_plus_metadata(metadata_path)
  first = metadata.to_json()
  second = read_apk_plus_metadata_text(first).to_json()

  # Then: the machine surface is canonical and contains both required split types.
  assert first == second
  assert metadata.required_split_types == ("base__abi", "base__density")
  assert tuple(item.split_name for item in metadata.splits) == ("config.arm64_v8a", "config.xxhdpi")


def read_apk_plus_metadata_text(text: str) -> ApkPlusMetadata:
  from tools.naver_map_patch.split_packaging import parse_apk_plus_metadata

  return parse_apk_plus_metadata(text)


@pytest.mark.parametrize("content", ["{", "[]", '{"schema_version": 1}', '{"schema_version": "1"}'])
def test_metadata_rejects_malformed_input(content: str) -> None:
  # Given: malformed JSON or a structurally incomplete metadata document.

  # When: it crosses the typed metadata boundary.
  with pytest.raises(PackagingError) as raised:
    read_apk_plus_metadata_text(content)

  # Then: parsing fails closed with a stable machine-readable code.
  assert raised.value.code is ErrorCode.MALFORMED_METADATA


def test_metadata_rejects_base_only_set(tmp_path: Path) -> None:
  # Given: metadata that claims a base without any required companion split.
  metadata_path = _write_metadata(tmp_path / "apk+.json", [])

  # When: the split set is parsed.
  with pytest.raises(PackagingError) as raised:
    read_apk_plus_metadata(metadata_path)

  # Then: base-alone never becomes an installable success state.
  assert raised.value.code is ErrorCode.BASE_ONLY


def test_metadata_rejects_missing_and_duplicate_split_types(tmp_path: Path) -> None:
  # Given: one required type is absent while the other is duplicated.
  metadata_path = _write_metadata(tmp_path / "apk+.json", [
    _split("abi-one.apk", "config.arm64_v8a", "base__abi", "b" * 64),
    _split("abi-two.apk", "config.x86_64", "base__abi", "c" * 64),
  ])

  # When: the metadata is parsed.
  with pytest.raises(PackagingError) as raised:
    read_apk_plus_metadata(metadata_path)

  # Then: duplicate split coverage is rejected before file access.
  assert raised.value.code is ErrorCode.DUPLICATE_SPLIT


def test_metadata_rejects_path_traversal(tmp_path: Path) -> None:
  # Given: a split filename attempts to escape the owned companion root.
  metadata_path = _write_metadata(tmp_path / "apk+.json", [
    _split("../abi.apk", "config.arm64_v8a", "base__abi", "b" * 64),
    _split("density.apk", "config.xxhdpi", "base__density", "d" * 64),
  ])

  # When: the metadata boundary parses file ownership.
  with pytest.raises(PackagingError) as raised:
    read_apk_plus_metadata(metadata_path)

  # Then: unsafe relative paths fail before extraction or output creation.
  assert raised.value.code is ErrorCode.UNSAFE_PATH


@pytest.mark.parametrize("file_name", [
  r"..\escape.apk",
  r"C:\temp\escape.apk",
  r"C:escape.apk",
  r"\\server\share\escape.apk",
  "../escape.apk",
  "/tmp/escape.apk",
  "CON.apk",
  "nul.APK",
  "COM1.apk",
  "LPT9.apk",
])
def test_metadata_rejects_cross_platform_escape_and_windows_reserved_names(
  tmp_path: Path, file_name: str,
) -> None:
  metadata_path = _write_metadata(tmp_path / "apk+.json", [
    _split(file_name, "config.arm64_v8a", "base__abi", "b" * 64),
    _split("density.apk", "config.xxhdpi", "base__density", "d" * 64),
  ])

  with pytest.raises(PackagingError) as raised:
    read_apk_plus_metadata(metadata_path)

  assert raised.value.code is ErrorCode.UNSAFE_PATH


@pytest.mark.parametrize("file_name", ["네이버 지도.apk", "split_config.arm64_v8a.apk"])
def test_metadata_preserves_safe_unicode_and_simple_apk_names(tmp_path: Path, file_name: str) -> None:
  metadata_path = _write_metadata(tmp_path / "apk+.json", [
    _split(file_name, "config.arm64_v8a", "base__abi", "b" * 64),
    _split("density.apk", "config.xxhdpi", "base__density", "d" * 64),
  ])

  metadata = read_apk_plus_metadata(metadata_path)

  assert {item.file_name for item in metadata.splits} == {file_name, "density.apk"}
