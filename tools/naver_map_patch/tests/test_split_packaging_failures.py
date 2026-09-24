from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
import json
from pathlib import Path
import shutil
from typing import Literal, TypedDict, assert_never, cast

import pytest

from tools.naver_map_patch.split_package import ApkIdentity, ArchivePatch, ErrorCode, PackagingError, Sha256, SplitExpectation, VerifyRequest
from tools.naver_map_patch.split_packaging import _validate_identity, build_release, read_apk_plus_metadata, verify_release
from tools.naver_map_patch.split_tools import sha256_file

from conftest import SyntheticSet, _build_apk
from test_split_packaging_integration import _profile, _request, _tools


@pytest.fixture(autouse=True)
def _signing_environment(monkeypatch: pytest.MonkeyPatch) -> None:
  monkeypatch.setenv("SYNTHETIC_STORE_PASSWORD", "synthetic-only-password")
  monkeypatch.setenv("SYNTHETIC_KEY_PASSWORD", "synthetic-only-password")


def _copy_companion(source: Path, destination: Path) -> Path:
  shutil.copytree(source, destination)
  return destination


class SplitPayload(TypedDict):
  file_name: str
  split_name: str
  split_type: str
  sha256: str


class MetadataPayload(TypedDict):
  splits: list[SplitPayload]


Mutation = Literal["base_only", "missing_file", "duplicate"]


def _payload(path: Path) -> MetadataPayload:
  return json.loads(path.read_text(encoding="utf-8"))


def _write_payload(path: Path, payload: MetadataPayload) -> None:
  path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")


@pytest.mark.parametrize(
  ("mutation", "expected"),
  [
    ("base_only", ErrorCode.BASE_ONLY),
    ("missing_file", ErrorCode.MISSING_SPLIT),
    ("duplicate", ErrorCode.DUPLICATE_SPLIT),
  ],
)
def test_build_rejects_incomplete_split_sets_without_output(
  synthetic_set: SyntheticSet, tmp_path: Path, mutation: Mutation, expected: ErrorCode,
) -> None:
  # Given: a companion source with a missing or duplicate required split.
  companion = _copy_companion(synthetic_set.companion, tmp_path / "companion")
  metadata_path = companion / "apk+.json"
  payload = _payload(metadata_path)
  match mutation:
    case "base_only":
      payload["splits"] = []
    case "missing_file":
      (companion / payload["splits"][1]["file_name"]).unlink()
    case "duplicate":
      payload["splits"][1] = dict(payload["splits"][0], file_name="duplicate.apk")
    case unreachable:
      assert_never(unreachable)
  _write_payload(metadata_path, payload)
  output = tmp_path / "release"
  request = replace(_request(synthetic_set, output), companion_source=companion)

  # When: the build attempts to validate the set.
  with pytest.raises(PackagingError) as raised:
    build_release(request)

  # Then: it fails with the precise reason and atomically publishes nothing.
  assert raised.value.code is expected
  assert not output.exists()
  assert not tuple(tmp_path.glob(".naver-packaging-*"))


def test_build_rejects_wrong_version_without_output(synthetic_set: SyntheticSet, tmp_path: Path) -> None:
  # Given: a trusted profile that does not match the selected base set.
  request = _request(synthetic_set, tmp_path / "release")
  request = replace(request, profile=replace(request.profile, version_code=1))

  # When: the build validates identity before publication.
  with pytest.raises(PackagingError) as raised:
    build_release(request)

  # Then: the mismatch is fail-closed and no output survives.
  assert raised.value.code is ErrorCode.WRONG_VERSION
  assert not request.output_root.exists()


def test_split_identity_accepts_inherited_empty_version_name(synthetic_set: SyntheticSet) -> None:
  # Given: a config split that inherits versionName from the matching base APK.
  profile = _profile(synthetic_set, _tools(synthetic_set))
  identity = ApkIdentity(
    path=Path("split_config.arm64_v8a.apk"),
    package_name=profile.package_name,
    version_code=profile.version_code,
    version_name="",
    split_name="config.arm64_v8a",
    signer_sha256=profile.original_signer_sha256,
    required_split_types=(),
  )

  # When/Then: the split identity remains valid because its versionName is inherited.
  _validate_identity(identity, profile, "config.arm64_v8a")


def test_build_rejects_wrong_base_hash_without_output(synthetic_set: SyntheticSet, tmp_path: Path) -> None:
  # Given: a trusted profile whose base digest differs from the selected base.
  request = _request(synthetic_set, tmp_path / "release")
  request = replace(request, profile=replace(request.profile, expected_base_sha256=Sha256("0" * 64)))

  # When: the build validates identity before publication.
  with pytest.raises(PackagingError) as raised:
    build_release(request)

  # Then: the hash mismatch is fail-closed and no output survives.
  assert raised.value.code is ErrorCode.WRONG_HASH
  assert not request.output_root.exists()


def test_build_rejects_mixed_original_signers_without_output(synthetic_set: SyntheticSet, tmp_path: Path) -> None:
  # Given: an ABI split signed by a different source key but with an updated claimed hash.
  companion = _copy_companion(synthetic_set.companion, tmp_path / "companion")
  alternate = _build_apk(
    companion, "split_config.arm64_v8a", "config.arm64_v8a",
    synthetic_set.alternate_keystore, "alternate", synthetic_set,
  )
  metadata_path = companion / "apk+.json"
  payload = _payload(metadata_path)
  payload["splits"][0]["sha256"] = sha256_file(alternate)
  _write_payload(metadata_path, payload)
  tools = _tools(synthetic_set)
  metadata = read_apk_plus_metadata(metadata_path)
  profile = replace(
    _profile(synthetic_set, tools),
    expected_splits=tuple(
      SplitExpectation(item.split_name, item.split_type, item.sha256, item.file_name) for item in metadata.splits
    ),
  )
  request = replace(_request(synthetic_set, tmp_path / "release"), companion_source=companion, profile=profile)

  # When: source signer consistency is checked.
  with pytest.raises(PackagingError) as raised:
    build_release(request)

  # Then: the mixed set is rejected before publication.
  assert raised.value.code is ErrorCode.SIGNER_MISMATCH
  assert not request.output_root.exists()


def test_build_rejects_installed_official_signer_without_uninstall_claim(
  synthetic_set: SyntheticSet, tmp_path: Path,
) -> None:
  # Given: the official source-signed package is still installed while outputs use the user's patch key.
  request = _request(synthetic_set, tmp_path / "release")
  request = replace(request, installed_signer_sha256=request.profile.original_signer_sha256)

  # When: post-sign install compatibility is evaluated.
  with pytest.raises(PackagingError) as raised:
    build_release(request)

  # Then: the release is not published and no uninstall command or success claim survives.
  assert raised.value.code is ErrorCode.INSTALLED_SIGNER_MISMATCH
  assert "uninstall" not in str(raised.value).lower()
  assert "removal" not in str(raised.value).lower()
  assert not request.output_root.exists()


def test_verify_rejects_post_sign_mutation(synthetic_set: SyntheticSet, tmp_path: Path) -> None:
  # Given: a fully verified output whose signed base bytes are modified afterward.
  request = _request(synthetic_set, tmp_path / "release")
  result = build_release(request)
  with (result.install_set / "base.apk").open("ab") as stream:
    stream.write(b"post-sign-mutation")

  # When: the release verifier rechecks hashes and Android signatures.
  with pytest.raises(PackagingError) as raised:
    verify_release(VerifyRequest(request.profile, result.output_root, request.tools, request.payload_identity))

  # Then: mutation cannot produce a verified-success report.
  assert raised.value.code in {ErrorCode.OUTPUT_HASH_MISMATCH, ErrorCode.TOOL_FAILURE}


@pytest.mark.parametrize(
  ("field", "value"),
  [
    ("mode", "production"),
    ("build_id", "synthetic-production-v1"),
    ("dex_entry", "classes2.dex"),
    ("sha256", "0" * 64),
  ],
)
def test_verify_rejects_payload_identity_provenance_mutation(
  synthetic_set: SyntheticSet, tmp_path: Path, field: str, value: str,
) -> None:
  # Given: a verified diagnostic release whose provenance identity is relabeled or altered.
  request = _request(synthetic_set, tmp_path / "release")
  result = build_release(request)
  provenance_path = result.install_set / "provenance.json"
  provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
  provenance["payload_identity"][field] = value
  provenance_path.write_text(
    json.dumps(provenance, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
    encoding="utf-8",
  )

  # When: verification binds provenance back to the expected diagnostic payload.
  with pytest.raises(PackagingError) as raised:
    verify_release(VerifyRequest(request.profile, result.output_root, request.tools, request.payload_identity))

  # Then: every identity mutation fails closed with the same output-integrity code.
  assert raised.value.code is ErrorCode.OUTPUT_HASH_MISMATCH


def test_build_rejects_unpermitted_base_replacement(synthetic_set: SyntheticSet, tmp_path: Path) -> None:
  # Given: a patch attempts to replace the package manifest instead of a DEX entry.
  request = replace(
    _request(synthetic_set, tmp_path / "release"),
    patches=(ArchivePatch("AndroidManifest.xml", b"malicious"),),
  )

  # When: the patch boundary validates owned archive entries.
  with pytest.raises(PackagingError) as raised:
    build_release(request)

  # Then: the unsafe patch fails before output creation.
  assert raised.value.code is ErrorCode.INVALID_PATCH_ENTRY
  assert not request.output_root.exists()


def test_build_rejects_stale_output_without_modifying_it(synthetic_set: SyntheticSet, tmp_path: Path) -> None:
  # Given: a prior output directory with an ownership sentinel.
  output = tmp_path / "release"
  output.mkdir()
  sentinel = output / "user-owned.txt"
  sentinel.write_text("preserve", encoding="utf-8")

  # When: another build targets the stale location.
  with pytest.raises(PackagingError) as raised:
    build_release(_request(synthetic_set, output))

  # Then: stale state is rejected without deleting or altering it.
  assert raised.value.code is ErrorCode.STALE_OUTPUT
  assert sentinel.read_text(encoding="utf-8") == "preserve"


def test_production_build_requires_prepublish_verifier_before_output(
  synthetic_set: SyntheticSet, tmp_path: Path,
) -> None:
  output = tmp_path / "release"
  request = _request(synthetic_set, output)
  request = replace(
    request,
    payload_identity=replace(request.payload_identity, mode="production", build_id="synthetic-production-v1"),
  )

  with pytest.raises(PackagingError) as raised:
    build_release(request)

  assert raised.value.code is ErrorCode.OUTPUT_HASH_MISMATCH
  assert "verifier" in raised.value.detail
  assert not output.exists()
  assert not tuple(tmp_path.glob(".naver-packaging-*"))


def test_diagnostic_variant_runs_optional_prepublish_verifier_before_output(
  synthetic_set: SyntheticSet, tmp_path: Path,
) -> None:
  output = tmp_path / "release"
  inspected: list[Path] = []

  def inspect_staged_base(staged_base: Path) -> None:
    assert staged_base.name == "base.apk"
    assert staged_base.is_file()
    inspected.append(staged_base)

  request = replace(
    _request(synthetic_set, output),
    prepublish_base_verifier=inspect_staged_base,
  )

  result = build_release(request)

  assert len(inspected) == 1
  assert not inspected[0].exists()
  assert result.output_root == output
  assert (output / "install-set" / "base.apk").is_file()
  assert not tuple(tmp_path.glob(".naver-packaging-*"))


def test_production_build_rejects_non_callable_verifier_before_output(
  synthetic_set: SyntheticSet, tmp_path: Path,
) -> None:
  output = tmp_path / "uncreated-parent" / "release"
  request = _request(synthetic_set, output)
  request = replace(
    request,
    payload_identity=replace(request.payload_identity, mode="production", build_id="synthetic-production-v1"),
    prepublish_base_verifier=cast(Callable[[Path], None], object()),
  )

  with pytest.raises(PackagingError) as raised:
    build_release(request)

  assert raised.value.code is ErrorCode.OUTPUT_HASH_MISMATCH
  assert "callable" in raised.value.detail
  assert not output.parent.exists()
  assert not tuple(tmp_path.glob(".naver-packaging-*"))


def test_production_composition_verifier_failure_publishes_nothing(
  synthetic_set: SyntheticSet, tmp_path: Path,
) -> None:
  output = tmp_path / "release"
  inspected: list[Path] = []

  def reject_staged_base(staged_base: Path) -> None:
    inspected.append(staged_base)
    assert staged_base.name == "base.apk"
    assert staged_base.is_file()
    raise PackagingError(ErrorCode.TOOL_FAILURE, "synthetic production composition failure", path=staged_base)

  request = _request(synthetic_set, output)
  request = replace(
    request,
    payload_identity=replace(request.payload_identity, mode="production", build_id="synthetic-production-v1"),
    prepublish_base_verifier=reject_staged_base,
  )

  with pytest.raises(PackagingError, match="synthetic production composition failure"):
    build_release(request)

  assert len(inspected) == 1
  assert not inspected[0].exists()
  assert not output.exists()
  assert not tuple(tmp_path.glob(".naver-packaging-*"))
  assert not tuple(tmp_path.rglob("install-command.json"))
  assert not tuple(tmp_path.rglob("install.ps1"))
  assert not tuple(tmp_path.rglob("install.sh"))
  assert not tuple(tmp_path.rglob("patched.apk+"))


def test_build_rejects_base_without_required_split_declaration(synthetic_set: SyntheticSet, tmp_path: Path) -> None:
  # Given: an otherwise matching base whose manifest omits requiredSplitTypes.
  base = _build_apk(
    tmp_path, "base_without_required_types", None,
    synthetic_set.official_keystore, "official", synthetic_set, required_types=False,
  )
  request = _request(synthetic_set, tmp_path / "release")
  request = replace(
    request,
    base_apk=base,
    profile=replace(request.profile, expected_base_sha256=sha256_file(base)),
  )

  # When: the base manifest is checked before repacking.
  with pytest.raises(PackagingError) as raised:
    build_release(request)

  # Then: the missing declaration blocks every output.
  assert raised.value.code is ErrorCode.REQUIRED_SPLIT_MISMATCH
  assert not request.output_root.exists()


def test_interrupted_signing_cleans_workspace_and_allows_clean_retry(
  synthetic_set: SyntheticSet, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
  # Given: a valid request whose user-supplied signing password is temporarily unavailable.
  request = _request(synthetic_set, tmp_path / "release")
  monkeypatch.delenv("SYNTHETIC_STORE_PASSWORD")

  # When: the failed attempt is followed by a clean retry with restored credentials.
  with pytest.raises(PackagingError) as raised:
    build_release(request)
  monkeypatch.setenv("SYNTHETIC_STORE_PASSWORD", "synthetic-only-password")
  result = build_release(request)

  # Then: interruption leaves no partial workspace and the retry publishes one verified release.
  assert raised.value.code is ErrorCode.TOOL_FAILURE
  assert result.output_root == request.output_root.resolve()
  assert not tuple(tmp_path.glob(".naver-packaging-*"))
