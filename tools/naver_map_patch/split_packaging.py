from __future__ import annotations

import hashlib
import os
from pathlib import Path
import tempfile
import zipfile

from tools.naver_map_patch.split_bundle import (
  materialized_companion,
  provenance_text,
  regenerated_metadata,
  verify_bundle,
  write_distribution,
)
from tools.naver_map_patch.split_metadata import parse_apk_plus_metadata, read_apk_plus_metadata
from tools.naver_map_patch.split_package import (
  AndroidTools,
  ApkIdentity,
  ApkPlusMetadata,
  BuildRequest,
  BuildResult,
  ErrorCode,
  PackagingError,
  PatchProfile,
  PayloadIdentity,
  Sha256,
  SigningConfig,
  VerificationReport,
  VerifyRequest,
)
from tools.naver_map_patch.split_tools import (
  align_and_sign,
  inspect_apk,
  resolve_signing_config,
  rewrite_apk,
  sha256_file,
  verify_alignment,
)


def build_release(request: BuildRequest) -> BuildResult:
  _validate_prepublish_verifier(request)
  output_root = request.output_root.resolve()
  if output_root.exists():
    raise PackagingError(ErrorCode.STALE_OUTPUT, "output root already exists", path=output_root)
  _validate_output_scope(request, output_root)
  output_root.parent.mkdir(parents=True, exist_ok=True)
  with tempfile.TemporaryDirectory(prefix=".naver-packaging-", dir=output_root.parent) as temporary_directory:
    workspace = Path(temporary_directory)
    with materialized_companion(request.companion_source.resolve(), workspace, request.profile) as (metadata, companion_root):
      _validate_profile_metadata(request.profile, metadata)
      _validate_source_set(request, metadata, companion_root)
      staged_root = workspace / "release"
      install_set = staged_root / "install-set"
      install_set.mkdir(parents=True)
      signer = resolve_signing_config(request.signer, request.tools)
      _build_signed_apks(request, metadata, companion_root, workspace / "unsigned", install_set, signer)
      if request.prepublish_base_verifier is not None:
        request.prepublish_base_verifier(install_set / metadata.base.file_name)
      _validate_requested_payload_identity(request)
      output_metadata = regenerated_metadata(metadata, install_set)
      identities = _verify_apks(request.profile, output_metadata, install_set, request.tools)
      signer_sha256 = identities[0].signer_sha256
      if request.installed_signer_sha256 is not None and request.installed_signer_sha256 != signer_sha256:
        raise PackagingError(
          ErrorCode.INSTALLED_SIGNER_MISMATCH,
          "installed package signer differs from the generated install set",
        )
      payload_identity = _verify_payload_identity(
        install_set / output_metadata.base.file_name,
        request.payload_identity,
      )
      provenance = provenance_text(
        request.profile, output_metadata, request.tools, signer_sha256, payload_identity,
      )
      bundle, _ = write_distribution(staged_root, output_metadata, provenance)
      verify_bundle(bundle, install_set)
      _verify_provenance(
        staged_root, request.profile, output_metadata, request.tools, signer_sha256, payload_identity,
      )
      try:
        os.replace(staged_root, output_root)
      except OSError as error:
        raise PackagingError(ErrorCode.TOOL_FAILURE, "verified release could not be atomically published", path=output_root) from error
  final_install_set = output_root / "install-set"
  apk_paths = (final_install_set / metadata.base.file_name, *(final_install_set / item.file_name for item in metadata.splits))
  return BuildResult(
    output_root=output_root,
    bundle_path=output_root / "patched.apk+",
    install_set=final_install_set,
    provenance_path=final_install_set / "provenance.json",
    signer_sha256=signer_sha256,
    apk_paths=apk_paths,
  )


def verify_release(request: VerifyRequest) -> VerificationReport:
  output_root = request.output_root.resolve()
  install_set = output_root / "install-set"
  metadata = read_apk_plus_metadata(install_set / "apk+.json")
  _validate_output_metadata(request.profile, metadata)
  identities = _verify_apks(request.profile, metadata, install_set, request.tools)
  signer_sha256 = identities[0].signer_sha256
  payload_identity = _verify_payload_identity(
    install_set / metadata.base.file_name,
    request.payload_identity,
  )
  _verify_provenance(
    output_root, request.profile, metadata, request.tools, signer_sha256, payload_identity,
  )
  verify_bundle(output_root / "patched.apk+", install_set)
  return VerificationReport(identities, signer_sha256, payload_identity)


def _validate_output_scope(request: BuildRequest, output_root: Path) -> None:
  base = request.base_apk.resolve()
  companion = request.companion_source.resolve()
  if output_root == base or output_root.is_relative_to(companion):
    raise PackagingError(ErrorCode.UNSAFE_PATH, "output root overlaps an input source", path=output_root)


def _validate_requested_payload_identity(request: BuildRequest) -> None:
  identity = request.payload_identity
  _validate_payload_mode(identity)
  matches = tuple(patch for patch in request.patches if patch.entry_name == identity.dex_entry)
  if (
    not identity.build_id
    or len(matches) != 1
    or Sha256(hashlib.sha256(matches[0].content).hexdigest()) != identity.sha256
  ):
    raise PackagingError(
      ErrorCode.OUTPUT_HASH_MISMATCH,
      "requested payload identity does not match the selected DEX patch",
    )


def _validate_payload_mode(identity: PayloadIdentity) -> None:
  if identity.mode not in ("diagnostic", "production"):
    raise PackagingError(ErrorCode.OUTPUT_HASH_MISMATCH, "requested payload mode is unsupported")


def _validate_prepublish_verifier(request: BuildRequest) -> None:
  _validate_payload_mode(request.payload_identity)
  verifier = request.prepublish_base_verifier
  if verifier is not None and not callable(verifier):
    raise PackagingError(ErrorCode.OUTPUT_HASH_MISMATCH, "prepublish base verifier must be callable")
  has_verifier = verifier is not None
  if request.payload_identity.mode == "production" and not has_verifier:
    raise PackagingError(
      ErrorCode.OUTPUT_HASH_MISMATCH,
      "prepublish base verifier is required for production payloads",
    )


def _verify_payload_identity(base_apk: Path, expected: PayloadIdentity) -> PayloadIdentity:
  try:
    with zipfile.ZipFile(base_apk, "r") as archive:
      names = archive.namelist()
      if names.count(expected.dex_entry) != 1:
        raise PackagingError(
          ErrorCode.OUTPUT_HASH_MISMATCH,
          "patched base does not contain the exact payload DEX entry",
          path=base_apk,
        )
      digest = Sha256(hashlib.sha256(archive.read(expected.dex_entry)).hexdigest())
  except PackagingError:
    raise
  except (OSError, KeyError, zipfile.BadZipFile) as error:
    raise PackagingError(
      ErrorCode.OUTPUT_HASH_MISMATCH,
      "patched base payload DEX is unreadable",
      path=base_apk,
    ) from error
  actual = PayloadIdentity(expected.mode, expected.build_id, expected.dex_entry, digest)
  if actual != expected:
    raise PackagingError(
      ErrorCode.OUTPUT_HASH_MISMATCH,
      "patched base payload DEX does not match the requested identity",
      path=base_apk,
    )
  return actual


def _validate_profile_metadata(profile: PatchProfile, metadata: ApkPlusMetadata) -> None:
  if metadata.package_name != profile.package_name:
    raise PackagingError(ErrorCode.WRONG_VERSION, "metadata package does not match the selected profile")
  if (metadata.version_code, metadata.version_name) != (profile.version_code, profile.version_name):
    raise PackagingError(ErrorCode.WRONG_VERSION, "metadata version does not match the selected profile")
  if set(metadata.required_split_types) != set(profile.required_split_types):
    raise PackagingError(ErrorCode.REQUIRED_SPLIT_MISMATCH, "metadata required split types do not match the profile")
  actual = {(item.split_name, item.split_type, item.sha256) for item in metadata.splits}
  expected = {(item.split_name, item.split_type, item.sha256) for item in profile.expected_splits}
  if actual != expected:
    raise PackagingError(ErrorCode.WRONG_HASH, "companion split identity does not match the selected profile")
  anchor_names = tuple(item.name for item in profile.anchor_counts)
  if len(set(anchor_names)) != len(anchor_names) or any(item.count < 0 for item in profile.anchor_counts):
    raise PackagingError(ErrorCode.MALFORMED_METADATA, "profile anchor counts are invalid")


def _validate_output_metadata(profile: PatchProfile, metadata: ApkPlusMetadata) -> None:
  if metadata.package_name != profile.package_name:
    raise PackagingError(ErrorCode.WRONG_VERSION, "output package does not match the selected profile")
  if (metadata.version_code, metadata.version_name) != (profile.version_code, profile.version_name):
    raise PackagingError(ErrorCode.WRONG_VERSION, "output version does not match the selected profile")
  if set(metadata.required_split_types) != set(profile.required_split_types):
    raise PackagingError(ErrorCode.REQUIRED_SPLIT_MISMATCH, "output required split types do not match the profile")
  expected = {(item.split_name, item.split_type) for item in profile.expected_splits}
  actual = {(item.split_name, item.split_type) for item in metadata.splits}
  if actual != expected:
    raise PackagingError(ErrorCode.MISSING_SPLIT, "output split identities do not match the profile")


def _validate_source_set(request: BuildRequest, metadata: ApkPlusMetadata, companion_root: Path) -> None:
  if sha256_file(request.base_apk) != request.profile.expected_base_sha256:
    raise PackagingError(ErrorCode.WRONG_HASH, "base APK hash does not match the selected profile", path=request.base_apk)
  base_identity = inspect_apk(request.base_apk, request.tools)
  _validate_identity(base_identity, request.profile, None)
  if set(base_identity.required_split_types) != set(request.profile.required_split_types):
    raise PackagingError(ErrorCode.REQUIRED_SPLIT_MISMATCH, "base manifest does not declare every required split type", path=request.base_apk)
  if base_identity.signer_sha256 != request.profile.original_signer_sha256:
    raise PackagingError(ErrorCode.SIGNER_MISMATCH, "base signer does not match the selected profile", path=request.base_apk)
  for item in metadata.splits:
    path = companion_root / item.file_name
    if sha256_file(path) != item.sha256:
      raise PackagingError(ErrorCode.WRONG_HASH, "companion split hash does not match metadata", path=path)
    identity = inspect_apk(path, request.tools)
    _validate_identity(identity, request.profile, item.split_name)
    if identity.signer_sha256 != request.profile.original_signer_sha256:
      raise PackagingError(ErrorCode.SIGNER_MISMATCH, "companion split signer differs from base", path=path)


def _validate_identity(identity: ApkIdentity, profile: PatchProfile, split_name: str | None) -> None:
  if identity.package_name != profile.package_name:
    raise PackagingError(ErrorCode.WRONG_VERSION, "APK package does not match the selected profile", path=identity.path)
  allowed_version_names = {profile.version_name} if split_name is None else {"", profile.version_name}
  if identity.version_code != profile.version_code or identity.version_name not in allowed_version_names:
    raise PackagingError(ErrorCode.WRONG_VERSION, "APK version does not match the selected profile", path=identity.path)
  if identity.split_name != split_name:
    raise PackagingError(ErrorCode.MISSING_SPLIT, "APK split name does not match metadata", path=identity.path)


def _build_signed_apks(
  request: BuildRequest, metadata: ApkPlusMetadata, companion_root: Path, unsigned_root: Path, install_set: Path,
  signer: SigningConfig,
) -> None:
  unsigned_root.mkdir()
  sources = ((request.base_apk, metadata.base.file_name, request.patches), *(
    (companion_root / item.file_name, item.file_name, ()) for item in metadata.splits
  ))
  for source, file_name, patches in sources:
    unsigned = unsigned_root / file_name
    rewrite_apk(source, unsigned, patches)
    align_and_sign(unsigned, install_set / file_name, signer, request.tools)


def _verify_apks(
  profile: PatchProfile, metadata: ApkPlusMetadata, install_set: Path, tools: AndroidTools,
) -> tuple[ApkIdentity, ...]:
  expected_names = {metadata.base.file_name, *(item.file_name for item in metadata.splits)}
  actual_names = {path.name for path in install_set.glob("*.apk")}
  if actual_names != expected_names:
    raise PackagingError(ErrorCode.MISSING_SPLIT, "install set APK files do not match metadata", path=install_set)
  records = ((metadata.base.file_name, metadata.base.sha256, None), *(
    (item.file_name, item.sha256, item.split_name) for item in metadata.splits
  ))
  identities: list[ApkIdentity] = []
  for file_name, expected_hash, split_name in records:
    path = install_set / file_name
    if sha256_file(path) != expected_hash:
      raise PackagingError(ErrorCode.OUTPUT_HASH_MISMATCH, "signed APK hash does not match regenerated metadata", path=path)
    verify_alignment(path, tools)
    identity = inspect_apk(path, tools)
    _validate_identity(identity, profile, split_name)
    identities.append(identity)
  signers = {item.signer_sha256 for item in identities}
  if len(signers) != 1:
    raise PackagingError(ErrorCode.SIGNER_MISMATCH, "output APKs are not signed by one certificate", path=install_set)
  return tuple(identities)


def _verify_provenance(
  output_root: Path, profile: PatchProfile, metadata: ApkPlusMetadata, tools: AndroidTools, signer_sha256: Sha256,
  payload_identity: PayloadIdentity,
) -> None:
  path = output_root / "install-set" / "provenance.json"
  expected = provenance_text(profile, metadata, tools, signer_sha256, payload_identity)
  try:
    actual = path.read_text(encoding="utf-8")
  except (OSError, UnicodeError) as error:
    raise PackagingError(ErrorCode.OUTPUT_HASH_MISMATCH, "provenance is unreadable", path=path) from error
  if actual != expected:
    raise PackagingError(ErrorCode.OUTPUT_HASH_MISMATCH, "provenance does not match verified APK bytes", path=path)
