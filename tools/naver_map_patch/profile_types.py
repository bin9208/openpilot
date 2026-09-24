from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class BaseProfile:
  size: int
  sha256: str
  dex_count: int
  native_library_count: int
  required_split_types: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SplitProfile:
  entry: str
  split_name: str
  split_type: str
  size: int
  sha256: str


@dataclass(frozen=True, slots=True)
class DexEntryProfile:
  size: int
  sha256: str


@dataclass(frozen=True, slots=True)
class DiagnosticHookProfile:
  method_descriptor: str
  placement_kind: str
  placement_index: int
  argument_kind: str
  argument_index: int


@dataclass(frozen=True, slots=True)
class OfflineCaptureProfile:
  batch_size: int
  channel_quotas: Mapping[str, int]
  database_name: str
  database_recovery_name: str
  directory_name: str
  flush_interval_ms: int
  journal_size_limit_bytes: int
  local_queue_capacity: int
  max_database_bytes: int
  network_queue_capacity: int
  payload_build_id: str
  pinned_per_channel: int
  retry_initial_ms: int
  retry_max_ms: int
  schema_version: int
  wal_autocheckpoint_pages: int


@dataclass(frozen=True, slots=True)
class CaptureSharingProfile:
  authority: str
  external_files_root_path: str
  export_database_name: str
  file_provider_root_name: str
  grantee_package: str
  quiesce_timeout_ms: int
  infrastructure_anchor: AnchorProfile


@dataclass(frozen=True, slots=True)
class DiagnosticPayloadProfile:
  accessor_allowlists: Mapping[str, tuple[str, ...]]
  android_platform_api: int
  build_tools_version: str
  hook_class_descriptor: str
  main_process: str
  max_accessors: int
  max_collection_items: int
  max_sample_chars: int
  min_api: int
  network_thread_name: str
  payload_dex_entry: str
  queue_capacity: int
  receiver_host: str
  receiver_port: int
  socket_timeout_ms: int
  offline_capture: OfflineCaptureProfile
  capture_sharing: CaptureSharingProfile


@dataclass(frozen=True, slots=True)
class AnchorProfile:
  name: str
  dex_entry: str
  class_descriptor: str
  method_descriptor: str
  instruction_window: tuple[str, ...]
  expected_count: int
  diagnostic_hook: DiagnosticHookProfile | None = None


@dataclass(frozen=True, slots=True)
class InspectionProfile:
  name: str
  package_name: str
  version_code: int
  version_name: str
  package_signer_sha256: str
  source_stamp_sha256: str
  base: BaseProfile
  dex_entries: Mapping[str, DexEntryProfile]
  payload_modes: tuple[str, ...]
  bundle_base_entry: str
  splits: tuple[SplitProfile, ...]
  anchors: tuple[AnchorProfile, ...]
  diagnostic_payload: DiagnosticPayloadProfile | None = None
