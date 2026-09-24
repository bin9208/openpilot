from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import warnings
import zipfile

import pytest

from tools.naver_map_patch.inspect_tools import (
  DexEntryFacts,
  InspectionError,
  ManifestFacts,
  SignerFacts,
  discover_tools,
  verify_profiled_dex_entries,
)
from tools.naver_map_patch.inspection import InspectionRequest, ObservedApk, inspect_packages
from tools.naver_map_patch.profile import AnchorProfile, BaseProfile, DexEntryProfile, InspectionProfile, SplitProfile


PACKAGE = "com.example.synthetic"
VERSION_CODE = 60800007
VERSION_NAME = "6.8.0.5-test"
SIGNER = "1" * 64
STAMP = "2" * 64
BASE_BYTES = b"synthetic-base"
ABI_BYTES = b"synthetic-abi"
DENSITY_BYTES = b"synthetic-density"
DEX_BYTES = b"dex\nsynthetic-profiled-entry"


class FakeBackend:
  def __init__(self, anchor_counts: dict[str, int] | None = None) -> None:
    self.anchor_counts = anchor_counts or {name: 1 for name in _anchor_names()}
    self.package_overrides: dict[str, str] = {}
    self.signer_overrides: dict[str, SignerFacts] = {}
    self.dex_overrides: dict[str, DexEntryFacts] = {}

  def inspect_apk(self, path: Path) -> ObservedApk:
    split_names = {
      "split_config.arm64_v8a.apk": "config.arm64_v8a",
      "split_config.xxhdpi.apk": "config.xxhdpi",
    }
    split_name = split_names.get(path.name)
    manifest = ManifestFacts(
      package=self.package_overrides.get(path.name, PACKAGE),
      version_code=VERSION_CODE,
      version_name=VERSION_NAME if split_name is None else "",
      split_name=split_name,
      split_types=("base__abi", "base__density") if split_name is None else (),
    )
    signer = self.signer_overrides.get(path.name, SignerFacts(SIGNER, STAMP))
    return ObservedApk(
      size=path.stat().st_size,
      sha256=_sha256(path.read_bytes()),
      manifest=manifest,
      signer=signer,
      dex_count=1 if split_name is None else 0,
      native_library_count=0,
    )

  def scan_anchors(self, _base: Path, _anchors: tuple[AnchorProfile, ...]) -> dict[str, int]:
    return dict(self.anchor_counts)

  def inspect_dex_entries(
    self, _base: Path, expected: dict[str, DexEntryProfile],
  ) -> dict[str, DexEntryFacts]:
    return {
      name: self.dex_overrides.get(name, DexEntryFacts(item.size, item.sha256))
      for name, item in expected.items()
    }


def test_inspect_reports_exact_identity_when_inputs_match(tmp_path: Path) -> None:
  # Given: exact base/split bytes, compatible metadata, signers, and six exact-one anchors.
  request = _request(tmp_path)

  # When: the package set is inspected without any output destination.
  report = inspect_packages(request, FakeBackend())

  # Then: the report exposes path-independent immutable identities and one count per channel.
  assert report.success is True
  assert report.profile == VERSION_NAME
  assert report.base.sha256 == _sha256(BASE_BYTES)
  assert report.dex_entries["classes.dex"].sha256 == _sha256(DEX_BYTES)
  assert tuple(item.split_name for item in report.splits) == ("config.arm64_v8a", "config.xxhdpi")
  assert report.anchor_counts == {name: 1 for name in _anchor_names()}
  assert set(tmp_path.iterdir()) == {request.base, request.splits_from}


def test_inspect_rejects_mutated_base_hash(tmp_path: Path) -> None:
  # Given: a profile frozen before the authoritative base bytes are mutated.
  request = _request(tmp_path)
  request.base.write_bytes(BASE_BYTES + b"-mutated")

  # When / Then: inspection fails at byte identity before anchor success can be claimed.
  with pytest.raises(InspectionError, match="base size or SHA-256") as error:
    inspect_packages(request, FakeBackend())
  assert error.value.code == "base_identity"


def test_inspect_rejects_profiled_dex_hash_before_anchor_scan(tmp_path: Path) -> None:
  request = _request(tmp_path)
  backend = FakeBackend()
  backend.dex_overrides["classes.dex"] = DexEntryFacts(len(DEX_BYTES), "f" * 64)

  with pytest.raises(InspectionError, match="profiled DEX identity") as error:
    inspect_packages(request, backend)

  assert error.value.code == "dex_identity"


def test_inspect_rejects_wrong_package(tmp_path: Path) -> None:
  # Given: exact bytes whose observed manifest belongs to another package.
  request = _request(tmp_path)
  backend = FakeBackend()
  backend.package_overrides[request.base.name] = "com.example.wrong"

  # When / Then: manifest identity fails closed.
  with pytest.raises(InspectionError, match="base package/version") as error:
    inspect_packages(request, backend)
  assert error.value.code == "base_identity"


def test_inspect_rejects_missing_required_split(tmp_path: Path) -> None:
  # Given: a companion archive missing the required density split.
  request = _request(tmp_path, included_splits=("split_config.arm64_v8a.apk",))

  # When / Then: the exact split set check fails before anchors are scanned.
  with pytest.raises(InspectionError, match="companion APK entries") as error:
    inspect_packages(request, FakeBackend())
  assert error.value.code == "split_set"


def test_inspect_rejects_duplicate_split_entry(tmp_path: Path) -> None:
  # Given: a companion archive with the required ABI entry duplicated.
  request = _request(tmp_path)
  with warnings.catch_warnings():
    warnings.simplefilter("ignore", UserWarning)
    with zipfile.ZipFile(request.splits_from, "a") as archive:
      archive.writestr("split_config.arm64_v8a.apk", ABI_BYTES)

  # When / Then: duplicate archive identities fail closed.
  with pytest.raises(InspectionError, match="duplicate") as error:
    inspect_packages(request, FakeBackend())
  assert error.value.code == "malformed_bundle"


def test_inspect_rejects_wrong_split_hash(tmp_path: Path) -> None:
  # Given: a complete companion set whose density bytes differ from the frozen profile.
  request = _request(tmp_path, density_bytes=DENSITY_BYTES + b"-wrong")

  # When / Then: the exact companion identity check rejects it.
  with pytest.raises(InspectionError, match="split size or SHA-256") as error:
    inspect_packages(request, FakeBackend())
  assert error.value.code == "split_identity"


def test_inspect_rejects_wrong_companion_signer(tmp_path: Path) -> None:
  # Given: exact split bytes but one split reports another package signer.
  request = _request(tmp_path)
  backend = FakeBackend()
  backend.signer_overrides["split_config.xxhdpi.apk"] = SignerFacts("3" * 64, STAMP)

  # When / Then: package signer mismatch fails closed.
  with pytest.raises(InspectionError, match="split signer") as error:
    inspect_packages(request, backend)
  assert error.value.code == "signer_mismatch"


def test_inspect_rejects_zero_anchor(tmp_path: Path) -> None:
  # Given: exact package identities but the status window is absent.
  request = _request(tmp_path)
  counts = {name: 1 for name in _anchor_names()}
  counts["status"] = 0

  # When / Then: zero exact matches block compatibility.
  with pytest.raises(InspectionError, match="status=0") as error:
    inspect_packages(request, FakeBackend(counts))
  assert error.value.code == "anchor_mismatch"


def test_inspect_rejects_duplicate_anchor(tmp_path: Path) -> None:
  # Given: exact package identities but the route window occurs twice.
  request = _request(tmp_path)
  counts = {name: 1 for name in _anchor_names()}
  counts["route"] = 2

  # When / Then: duplicate exact matches block compatibility.
  with pytest.raises(InspectionError, match="route=2") as error:
    inspect_packages(request, FakeBackend(counts))
  assert error.value.code == "anchor_mismatch"


def test_profiled_dex_reader_hashes_selected_entries_without_extraction(tmp_path: Path) -> None:
  apk = tmp_path / "base.apk"
  with zipfile.ZipFile(apk, "w") as archive:
    archive.writestr("classes4.dex", DEX_BYTES)
    archive.writestr("classes6.dex", b"dex\nsecond")
  expected = {
    "classes4.dex": DexEntryProfile(len(DEX_BYTES), _sha256(DEX_BYTES)),
  }

  observed = verify_profiled_dex_entries(apk, expected)

  assert observed == {
    "classes4.dex": DexEntryFacts(len(DEX_BYTES), _sha256(DEX_BYTES)),
  }
  assert tuple(tmp_path.iterdir()) == (apk,)


def test_discover_tools_accepts_explicit_portable_gradle_path(tmp_path: Path) -> None:
  sdk = tmp_path / "sdk"
  build_tools = sdk / "build-tools" / "36.0.0"
  java_home = tmp_path / "jdk"
  suffix = ".exe" if os.name == "nt" else ""
  batch = ".bat" if os.name == "nt" else ""
  aapt2 = build_tools / f"aapt2{suffix}"
  apksigner = build_tools / f"apksigner{batch}"
  java = java_home / "bin" / f"java{suffix}"
  gradle = tmp_path / f"gradle{batch}"
  for path in (aapt2, apksigner, java, gradle):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch()

  tools = discover_tools(
    tmp_path / "repo",
    {"ANDROID_SDK_ROOT": str(sdk), "JAVA_HOME": str(java_home), "NAVER_GRADLE": str(gradle)},
  )

  assert tools.gradle == gradle.resolve()


def _request(
  root: Path,
  *,
  included_splits: tuple[str, ...] = ("split_config.arm64_v8a.apk", "split_config.xxhdpi.apk"),
  density_bytes: bytes = DENSITY_BYTES,
) -> InspectionRequest:
  base = root / "authoritative.apk"
  base.write_bytes(BASE_BYTES)
  bundle = root / "companion.apk+"
  entries = {
    "split_config.arm64_v8a.apk": ABI_BYTES,
    "split_config.xxhdpi.apk": density_bytes,
  }
  with zipfile.ZipFile(bundle, "w") as archive:
    archive.writestr("apk+.json", json.dumps({
      "package_name": PACKAGE,
      "version_code": VERSION_CODE,
      "version_name": VERSION_NAME,
    }))
    archive.writestr("base.apk", BASE_BYTES)
    for name in included_splits:
      archive.writestr(name, entries[name])
  profile = InspectionProfile(
    name=VERSION_NAME,
    package_name=PACKAGE,
    version_code=VERSION_CODE,
    version_name=VERSION_NAME,
    package_signer_sha256=SIGNER,
    source_stamp_sha256=STAMP,
    base=BaseProfile(
      size=len(BASE_BYTES),
      sha256=_sha256(BASE_BYTES),
      dex_count=1,
      native_library_count=0,
      required_split_types=("base__abi", "base__density"),
    ),
    dex_entries={"classes.dex": DexEntryProfile(len(DEX_BYTES), _sha256(DEX_BYTES))},
    payload_modes=("diagnostic", "production"),
    bundle_base_entry="base.apk",
    splits=(
      SplitProfile("split_config.arm64_v8a.apk", "config.arm64_v8a", "base__abi", len(ABI_BYTES), _sha256(ABI_BYTES)),
      SplitProfile("split_config.xxhdpi.apk", "config.xxhdpi", "base__density", len(DENSITY_BYTES), _sha256(DENSITY_BYTES)),
    ),
    anchors=tuple(
      AnchorProfile(name, "classes.dex", "Lsample/Target;", "emit()V", ("return-void",), 1)
      for name in _anchor_names()
    ),
  )
  return InspectionRequest(base=base, splits_from=bundle, profile=profile)


def _anchor_names() -> tuple[str, ...]:
  return ("status", "tbt_current", "tbt_next", "safety", "route", "lane")


def _sha256(value: bytes) -> str:
  return hashlib.sha256(value).hexdigest()
