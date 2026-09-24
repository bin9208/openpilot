from __future__ import annotations

from dataclasses import replace
import hashlib
import json
from pathlib import Path
import zipfile

import pytest

from tools.naver_map_patch.split_package import (
  AnchorCount,
  AndroidTools,
  ArchivePatch,
  BuildRequest,
  PatchProfile,
  PayloadIdentity,
  ProfileId,
  Sha256,
  SigningConfig,
  SplitExpectation,
  ToolVersion,
  VerifyRequest,
)
from tools.naver_map_patch.split_packaging import build_release, read_apk_plus_metadata, verify_release
from tools.naver_map_patch.split_tools import inspect_apk, sha256_file

from conftest import PACKAGE_NAME, VERSION_CODE, VERSION_NAME, SyntheticSet


def _tools(fixture: SyntheticSet) -> AndroidTools:
  return AndroidTools(
    aapt2=fixture.aapt2,
    zipalign=fixture.zipalign,
    apksigner=fixture.apksigner,
    java_home=Path("C:/Program Files/Android/Android Studio/jbr"),
    versions=(
      ToolVersion("aapt2", "2.20-13193326"),
      ToolVersion("zipalign", "36.0.0"),
      ToolVersion("apksigner", "0.9"),
    ),
    timeout_seconds=30.0,
  )


def _profile(fixture: SyntheticSet, tools: AndroidTools) -> PatchProfile:
  metadata = read_apk_plus_metadata(fixture.companion / "apk+.json")
  signer = inspect_apk(fixture.base_apk, tools).signer_sha256
  return PatchProfile(
    profile_id=ProfileId("synthetic-6.8.0.5"),
    package_name=PACKAGE_NAME,
    version_code=VERSION_CODE,
    version_name=VERSION_NAME,
    base_file_name=metadata.base.file_name,
    expected_base_sha256=sha256_file(fixture.base_apk),
    original_signer_sha256=signer,
    required_split_types=("base__abi", "base__density"),
    expected_splits=tuple(
      SplitExpectation(item.split_name, item.split_type, item.sha256, item.file_name) for item in metadata.splits
    ),
    anchor_counts=(AnchorCount("status", 1), AnchorCount("route", 1)),
  )


def _request(fixture: SyntheticSet, output: Path, installed: Sha256 | None = None) -> BuildRequest:
  tools = _tools(fixture)
  payload = b"synthetic-dex-patched"
  return BuildRequest(
    profile=_profile(fixture, tools),
    base_apk=fixture.base_apk,
    companion_source=fixture.companion,
    patches=(ArchivePatch("classes.dex", payload),),
    payload_identity=PayloadIdentity(
      mode="diagnostic",
      build_id="synthetic-diagnostic-offline-v1",
      dex_entry="classes.dex",
      sha256=Sha256(hashlib.sha256(payload).hexdigest()),
    ),
    output_root=output,
    signer=SigningConfig(
      keystore=fixture.patch_keystore,
      alias="patch",
      store_password_env="SYNTHETIC_STORE_PASSWORD",
      key_password_env="SYNTHETIC_KEY_PASSWORD",
    ),
    tools=tools,
    installed_signer_sha256=installed,
  )


@pytest.fixture(autouse=True)
def _signing_environment(monkeypatch: pytest.MonkeyPatch) -> None:
  monkeypatch.setenv("SYNTHETIC_STORE_PASSWORD", "synthetic-only-password")
  monkeypatch.setenv("SYNTHETIC_KEY_PASSWORD", "synthetic-only-password")


def test_build_aligns_then_same_key_signs_every_required_apk(synthetic_set: SyntheticSet, tmp_path: Path) -> None:
  # Given: one exact synthetic base and its ABI and density companion splits.
  request = _request(synthetic_set, tmp_path / "release")

  # When: the packaging core builds and verifies the release.
  result = build_release(request)
  report = verify_release(VerifyRequest(request.profile, result.output_root, request.tools, request.payload_identity))

  # Then: all required APKs are aligned, signed by one key, and retain one package/version.
  assert len(report.apks) == 3
  assert {item.split_name for item in report.apks} == {None, "config.arm64_v8a", "config.xxhdpi"}
  assert {item.signer_sha256 for item in report.apks} == {result.signer_sha256}
  assert {item.package_name for item in report.apks} == {PACKAGE_NAME}
  assert {item.version_code for item in report.apks} == {VERSION_CODE}
  assert report.payload_identity == request.payload_identity


def test_build_patches_only_base_content(synthetic_set: SyntheticSet, tmp_path: Path) -> None:
  # Given: a patch that names only the permitted base DEX entry.
  split_hashes = {path.name: sha256_file(path) for path in synthetic_set.companion.glob("*.apk")}
  request = _request(synthetic_set, tmp_path / "release")

  # When: the split release is built.
  result = build_release(request)

  # Then: the output base contains the replacement and source split inputs were untouched.
  with zipfile.ZipFile(result.install_set / "base.apk") as archive:
    assert archive.read("classes.dex") == b"synthetic-dex-patched"
  assert {path.name: sha256_file(path) for path in synthetic_set.companion.glob("*.apk")} == split_hashes


def test_provenance_and_bundle_are_deterministic(synthetic_set: SyntheticSet, tmp_path: Path) -> None:
  # Given: identical inputs, key, profile, anchors, and installed tool versions.
  first_request = _request(synthetic_set, tmp_path / "first")
  second_request = _request(synthetic_set, tmp_path / "second")

  # When: two independent releases are produced.
  first = build_release(first_request)
  second = build_release(second_request)

  # Then: provenance and the deterministic outer bundle are byte-identical.
  assert first.provenance_path.read_bytes() == second.provenance_path.read_bytes()
  assert sha256_file(first.bundle_path) == sha256_file(second.bundle_path)
  provenance = json.loads(first.provenance_path.read_text(encoding="utf-8"))
  assert provenance["profile_id"] == "synthetic-6.8.0.5"
  assert provenance["anchor_counts"] == {"route": 1, "status": 1}
  assert provenance["signer_sha256"] == first.signer_sha256
  assert provenance["payload_identity"] == {
    "mode": "diagnostic",
    "build_id": "synthetic-diagnostic-offline-v1",
    "dex_entry": "classes.dex",
    "sha256": hashlib.sha256(b"synthetic-dex-patched").hexdigest(),
  }
  assert {item["name"] for item in provenance["tools"]} == {"aapt2", "zipalign", "apksigner"}
  assert len(provenance["inputs"]) == 3
  assert len(provenance["outputs"]) == 3


def test_bundle_and_install_set_expose_split_only_install_surface(synthetic_set: SyntheticSet, tmp_path: Path) -> None:
  # Given: a verified synthetic release.
  result = build_release(_request(synthetic_set, tmp_path / "release"))

  # When: a consumer parses the bundle and machine-readable install command.
  command = json.loads((result.install_set / "install-command.json").read_text(encoding="utf-8"))
  with zipfile.ZipFile(result.bundle_path) as bundle:
    bundle_names = set(bundle.namelist())

  # Then: all three APKs and regenerated metadata are mandatory; base-alone is never emitted as a command.
  assert command["argv"] == [
    "adb", "install-multiple", "-r", "base.apk", "split_config.arm64_v8a.apk", "split_config.xxhdpi.apk",
  ]
  install_ps1 = (result.install_set / "install.ps1").read_text(encoding="utf-8")
  install_sh = (result.install_set / "install.sh").read_text(encoding="utf-8")
  assert install_ps1.count("adb install-multiple -r") == 1
  assert install_sh.count("adb install-multiple -r") == 1
  assert command["requires_all_splits"] is True
  assert {"base.apk", "split_config.arm64_v8a.apk", "split_config.xxhdpi.apk", "apk+.json",
          "provenance.json", "install-command.json", "install.ps1", "install.sh"} <= bundle_names
  output_metadata = read_apk_plus_metadata(result.install_set / "apk+.json")
  assert {item.sha256 for item in output_metadata.splits} == {
    sha256_file(result.install_set / "split_config.arm64_v8a.apk"),
    sha256_file(result.install_set / "split_config.xxhdpi.apk"),
  }


def test_build_accepts_typed_outer_companion_bundle(synthetic_set: SyntheticSet, tmp_path: Path) -> None:
  # Given: a companion apk+ archive containing typed metadata and only its declared splits.
  source_bundle = tmp_path / "companion.apk+"
  with zipfile.ZipFile(source_bundle, "w") as archive:
    archive.write(synthetic_set.companion / "apk+.json", "apk+.json")
    for path in sorted(synthetic_set.companion.glob("*.apk")):
      archive.write(path, path.name)
  request = replace(_request(synthetic_set, tmp_path / "release"), companion_source=source_bundle)

  # When: the same packaging boundary consumes the outer companion source.
  result = build_release(request)

  # Then: all declared APKs are preserved as a verified split install set.
  report = verify_release(VerifyRequest(request.profile, result.output_root, request.tools, request.payload_identity))
  assert {item.split_name for item in report.apks} == {None, "config.arm64_v8a", "config.xxhdpi"}


def test_build_accepts_vendor_metadata_outer_companion_bundle(
  synthetic_set: SyntheticSet, tmp_path: Path,
) -> None:
  # Given: the original vendor apk+ metadata shape with the exact profiled APK entries.
  source_bundle = tmp_path / "vendor-companion.apk+"
  vendor_metadata = {
    "app_name": "synthetic",
    "min_sdk_version": 26,
    "package_name": PACKAGE_NAME,
    "version_code": VERSION_CODE,
    "version_name": VERSION_NAME,
  }
  with zipfile.ZipFile(source_bundle, "w") as archive:
    archive.writestr("apk+.json", json.dumps(vendor_metadata, sort_keys=True))
    archive.writestr("icon.png", b"synthetic-icon")
    for path in sorted(synthetic_set.companion.glob("*.apk")):
      archive.write(path, path.name)
  request = replace(_request(synthetic_set, tmp_path / "release"), companion_source=source_bundle)

  # When: the packaging boundary consumes the original vendor companion source.
  result = build_release(request)

  # Then: profile validation still produces the complete verified split install set.
  report = verify_release(VerifyRequest(request.profile, result.output_root, request.tools, request.payload_identity))
  assert {item.split_name for item in report.apks} == {None, "config.arm64_v8a", "config.xxhdpi"}
