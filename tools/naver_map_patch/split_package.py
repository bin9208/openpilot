from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum, unique
import json
from pathlib import Path
from typing import Literal, NewType


ProfileId = NewType("ProfileId", str)
Sha256 = NewType("Sha256", str)


@unique
class ErrorCode(StrEnum):
  MALFORMED_METADATA = "malformed_metadata"
  BASE_ONLY = "base_only"
  MISSING_SPLIT = "missing_split"
  DUPLICATE_SPLIT = "duplicate_split"
  WRONG_VERSION = "wrong_version"
  WRONG_HASH = "wrong_hash"
  OUTPUT_HASH_MISMATCH = "output_hash_mismatch"
  SIGNER_MISMATCH = "signer_mismatch"
  INSTALLED_SIGNER_MISMATCH = "installed_signer_mismatch"
  REQUIRED_SPLIT_MISMATCH = "required_split_mismatch"
  INVALID_PATCH_ENTRY = "invalid_patch_entry"
  UNSAFE_PATH = "unsafe_path"
  STALE_OUTPUT = "stale_output"
  TOOL_FAILURE = "tool_failure"
  TOOL_TIMEOUT = "tool_timeout"


class PackagingError(Exception):
  __slots__ = ("code", "detail", "path", "exit_code")

  def __init__(
    self, code: ErrorCode, detail: str, path: Path | None = None, exit_code: int | None = None,
  ) -> None:
    super().__init__()
    self.code = code
    self.detail = detail
    self.path = path
    self.exit_code = exit_code

  def __str__(self) -> str:
    location = "" if self.path is None else f" [{self.path}]"
    return f"{self.code.value}{location}: {self.detail}"


@dataclass(frozen=True, slots=True)
class ToolVersion:
  name: str
  version: str


@dataclass(frozen=True, slots=True)
class AndroidTools:
  aapt2: Path
  zipalign: Path
  apksigner: Path
  java_home: Path
  versions: tuple[ToolVersion, ...]
  timeout_seconds: float


@dataclass(frozen=True, slots=True)
class SigningConfig:
  keystore: Path
  alias: str | None
  store_password_env: str
  key_password_env: str


@dataclass(frozen=True, slots=True)
class AnchorCount:
  name: str
  count: int


@dataclass(frozen=True, slots=True)
class SplitExpectation:
  split_name: str
  split_type: str
  sha256: Sha256
  file_name: str


@dataclass(frozen=True, slots=True)
class PatchProfile:
  profile_id: ProfileId
  package_name: str
  version_code: int
  version_name: str
  base_file_name: str
  expected_base_sha256: Sha256
  original_signer_sha256: Sha256
  required_split_types: tuple[str, ...]
  expected_splits: tuple[SplitExpectation, ...]
  anchor_counts: tuple[AnchorCount, ...]


@dataclass(frozen=True, slots=True)
class ArchivePatch:
  entry_name: str
  content: bytes


@dataclass(frozen=True, slots=True)
class PayloadIdentity:
  mode: Literal["diagnostic", "production"]
  build_id: str
  dex_entry: str
  sha256: Sha256


@dataclass(frozen=True, slots=True)
class ApkFile:
  file_name: str
  sha256: Sha256


@dataclass(frozen=True, slots=True)
class SplitFile:
  file_name: str
  split_name: str
  split_type: str
  sha256: Sha256


@dataclass(frozen=True, slots=True)
class ApkPlusMetadata:
  schema_version: int
  package_name: str
  version_code: int
  version_name: str
  required_split_types: tuple[str, ...]
  base: ApkFile
  splits: tuple[SplitFile, ...]

  def to_json(self) -> str:
    payload = {
      "base": {"file_name": self.base.file_name, "sha256": self.base.sha256},
      "package_name": self.package_name,
      "required_split_types": list(self.required_split_types),
      "schema_version": self.schema_version,
      "splits": [
        {
          "file_name": item.file_name,
          "sha256": item.sha256,
          "split_name": item.split_name,
          "split_type": item.split_type,
        }
        for item in self.splits
      ],
      "version_code": self.version_code,
      "version_name": self.version_name,
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


@dataclass(frozen=True, slots=True)
class BuildRequest:
  profile: PatchProfile
  base_apk: Path
  companion_source: Path
  patches: tuple[ArchivePatch, ...]
  payload_identity: PayloadIdentity
  output_root: Path
  signer: SigningConfig
  tools: AndroidTools
  installed_signer_sha256: Sha256 | None
  prepublish_base_verifier: Callable[[Path], None] | None = None


@dataclass(frozen=True, slots=True)
class BuildResult:
  output_root: Path
  bundle_path: Path
  install_set: Path
  provenance_path: Path
  signer_sha256: Sha256
  apk_paths: tuple[Path, ...]


@dataclass(frozen=True, slots=True)
class VerifyRequest:
  profile: PatchProfile
  output_root: Path
  tools: AndroidTools
  payload_identity: PayloadIdentity


@dataclass(frozen=True, slots=True)
class ApkIdentity:
  path: Path
  package_name: str
  version_code: int
  version_name: str
  split_name: str | None
  signer_sha256: Sha256
  required_split_types: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class VerificationReport:
  apks: tuple[ApkIdentity, ...]
  signer_sha256: Sha256
  payload_identity: PayloadIdentity


@dataclass(frozen=True, slots=True)
class CommandResult:
  stdout: str
  stderr: str
