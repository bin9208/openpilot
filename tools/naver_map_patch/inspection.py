from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import json
from pathlib import Path
from typing import Protocol

from tools.naver_map_patch.inspection_bundle import materialized_companion
from tools.naver_map_patch.inspect_tools import (
  AnchorScanRequest,
  DexEntryFacts,
  InspectionError,
  ManifestFacts,
  SignerFacts,
  ToolPaths,
  read_archive_facts,
  read_profiled_dex_entries,
  read_manifest,
  read_signers,
  scan_anchors,
  sha256_file,
  validate_profiled_dex_entries,
)
from tools.naver_map_patch.profile import AnchorProfile, DexEntryProfile, InspectionProfile, SplitProfile


@dataclass(frozen=True, slots=True)
class ObservedApk:
  size: int
  sha256: str
  manifest: ManifestFacts
  signer: SignerFacts
  dex_count: int
  native_library_count: int

  @property
  def split_name(self) -> str | None:
    return self.manifest.split_name


@dataclass(frozen=True, slots=True)
class InspectedSplit:
  profile: SplitProfile
  observed: ObservedApk

  @property
  def split_name(self) -> str:
    return self.profile.split_name


@dataclass(frozen=True, slots=True)
class AnchorCount:
  name: str
  count: int


@dataclass(frozen=True, slots=True)
class InspectionRequest:
  base: Path
  splits_from: Path
  profile: InspectionProfile


@dataclass(frozen=True, slots=True)
class InspectionReport:
  profile: str
  package_name: str
  version_code: int
  version_name: str
  base: ObservedApk
  dex_entries: Mapping[str, DexEntryFacts]
  companion_base_entry: str
  companion_base_sha256: str
  splits: tuple[InspectedSplit, ...]
  anchors: tuple[AnchorCount, ...]

  @property
  def success(self) -> bool:
    return True

  @property
  def anchor_counts(self) -> dict[str, int]:
    return {item.name: item.count for item in self.anchors}

  def to_json(self) -> str:
    payload = {
      "schema_version": 1,
      "command": "inspect",
      "success": True,
      "profile": self.profile,
      "package_name": self.package_name,
      "version_code": self.version_code,
      "version_name": self.version_name,
      "base": _apk_payload(self.base),
      "dex_entries": {
        name: {"size": item.size, "sha256": item.sha256}
        for name, item in self.dex_entries.items()
      },
      "companion": {
        "base_entry": self.companion_base_entry,
        "base_sha256": self.companion_base_sha256,
        "splits": [
          {
            **_apk_payload(item.observed),
            "entry": item.profile.entry,
            "split_name": item.profile.split_name,
            "split_type": item.profile.split_type,
          }
          for item in self.splits
        ],
      },
      "anchor_counts": self.anchor_counts,
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class InspectionBackend(Protocol):
  def inspect_apk(self, path: Path) -> ObservedApk: ...

  def inspect_dex_entries(
    self, base: Path, expected: Mapping[str, DexEntryProfile],
  ) -> Mapping[str, DexEntryFacts]: ...

  def scan_anchors(self, base: Path, anchors: tuple[AnchorProfile, ...]) -> Mapping[str, int]: ...


@dataclass(frozen=True, slots=True)
class AndroidInspectionBackend:
  tools: ToolPaths
  dexpatch_dir: Path

  def inspect_apk(self, path: Path) -> ObservedApk:
    try:
      size = path.stat().st_size
    except OSError as error:
      raise InspectionError("input_read", "APK input is not readable") from error
    archive = read_archive_facts(path)
    return ObservedApk(
      size=size,
      sha256=sha256_file(path),
      manifest=read_manifest(path, self.tools),
      signer=read_signers(path, self.tools),
      dex_count=archive.dex_count,
      native_library_count=archive.native_library_count,
    )

  def scan_anchors(self, base: Path, anchors: tuple[AnchorProfile, ...]) -> Mapping[str, int]:
    return scan_anchors(AnchorScanRequest(base, anchors, self.dexpatch_dir), self.tools)

  def inspect_dex_entries(
    self, base: Path, expected: Mapping[str, DexEntryProfile],
  ) -> Mapping[str, DexEntryFacts]:
    return read_profiled_dex_entries(base, expected)


def inspect_packages(request: InspectionRequest, backend: InspectionBackend) -> InspectionReport:
  if not request.base.is_file():
    raise InspectionError("base_missing", "base input is not a regular file")
  observed_base = backend.inspect_apk(request.base)
  _validate_base(request.profile, observed_base)
  dex_entries = dict(backend.inspect_dex_entries(request.base, request.profile.dex_entries))
  validate_profiled_dex_entries(dex_entries, request.profile.dex_entries)
  with materialized_companion(request.splits_from, request.profile) as companion:
    if companion.base_size != request.profile.base.size or companion.base_sha256 != request.profile.base.sha256:
      raise InspectionError("companion_base", "companion base does not match the authoritative base profile")
    inspected_splits = tuple(
      InspectedSplit(split, backend.inspect_apk(path))
      for split, path in zip(request.profile.splits, companion.split_paths, strict=True)
    )
    for item in inspected_splits:
      _validate_split(request.profile, item)
    counts = dict(backend.scan_anchors(request.base, request.profile.anchors))
    expected_names = {anchor.name for anchor in request.profile.anchors}
    if set(counts) != expected_names:
      raise InspectionError("anchor_output", "DEX scanner returned an incomplete anchor result set")
    mismatches = tuple(
      f"{anchor.name}={counts[anchor.name]}"
      for anchor in request.profile.anchors
      if counts[anchor.name] != anchor.expected_count
    )
    if mismatches:
      raise InspectionError("anchor_mismatch", f"anchor exact-one mismatch: {','.join(mismatches)}")
  anchors = tuple(AnchorCount(anchor.name, counts[anchor.name]) for anchor in request.profile.anchors)
  return InspectionReport(
    profile=request.profile.name,
    package_name=request.profile.package_name,
    version_code=request.profile.version_code,
    version_name=request.profile.version_name,
    base=observed_base,
    dex_entries=dex_entries,
    companion_base_entry=request.profile.bundle_base_entry,
    companion_base_sha256=companion.base_sha256,
    splits=inspected_splits,
    anchors=anchors,
  )


def _validate_base(profile: InspectionProfile, observed: ObservedApk) -> None:
  if observed.size != profile.base.size or observed.sha256 != profile.base.sha256:
    raise InspectionError("base_identity", "base size or SHA-256 does not match profile")
  manifest = observed.manifest
  if (
    manifest.package != profile.package_name
    or manifest.version_code != profile.version_code
    or manifest.version_name != profile.version_name
    or manifest.split_name is not None
  ):
    raise InspectionError("base_identity", "base package/version does not match profile")
  if manifest.split_types != profile.base.required_split_types:
    raise InspectionError("split_types", "base required split types do not match profile")
  if (
    observed.signer.signer_sha256 != profile.package_signer_sha256
    or observed.signer.source_stamp_sha256 != profile.source_stamp_sha256
  ):
    raise InspectionError("signer_mismatch", "base signer/source stamp does not match profile")
  if (
    observed.dex_count != profile.base.dex_count
    or observed.native_library_count != profile.base.native_library_count
  ):
    raise InspectionError("base_layout", "base DEX/native-library layout does not match profile")


def _validate_split(profile: InspectionProfile, item: InspectedSplit) -> None:
  expected = item.profile
  observed = item.observed
  if observed.size != expected.size or observed.sha256 != expected.sha256:
    raise InspectionError("split_identity", f"split size or SHA-256 does not match profile: {expected.entry}")
  manifest = observed.manifest
  if (
    manifest.package != profile.package_name
    or manifest.version_code != profile.version_code
    or manifest.version_name not in ("", profile.version_name)
    or manifest.split_name != expected.split_name
    or manifest.split_types
  ):
    raise InspectionError("split_identity", f"split package/version/name does not match profile: {expected.entry}")
  if (
    observed.signer.signer_sha256 != profile.package_signer_sha256
    or observed.signer.source_stamp_sha256 != profile.source_stamp_sha256
  ):
    raise InspectionError("signer_mismatch", f"split signer/source stamp does not match profile: {expected.entry}")


def _apk_payload(observed: ObservedApk) -> dict[str, str | int | list[str] | None]:
  return {
    "size": observed.size,
    "sha256": observed.sha256,
    "package_name": observed.manifest.package,
    "version_code": observed.manifest.version_code,
    "version_name": observed.manifest.version_name,
    "split_name": observed.manifest.split_name,
    "required_split_types": list(observed.manifest.split_types),
    "package_signer_sha256": observed.signer.signer_sha256,
    "source_stamp_sha256": observed.signer.source_stamp_sha256,
    "dex_count": observed.dex_count,
    "native_library_count": observed.native_library_count,
  }
