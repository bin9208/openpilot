from __future__ import annotations

from collections.abc import Mapping
import json
from pathlib import Path
import re
from typing import TypeAlias

from tools.naver_map_patch.profile_types import (
  AnchorProfile,
  BaseProfile,
  CaptureSharingProfile,
  DexEntryProfile,
  DiagnosticHookProfile,
  DiagnosticPayloadProfile,
  InspectionProfile,
  OfflineCaptureProfile,
  SplitProfile,
)


JsonScalar: TypeAlias = None | bool | int | float | str
JsonValue: TypeAlias = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]


class ProfileError(RuntimeError):
  """Raised when an inspection profile is unsupported or malformed."""


_PROFILE_DIR = Path(__file__).with_name("profiles")
_SUPPORTED = {"6.8.0.5": _PROFILE_DIR / "6.8.0.5.json"}
_REQUIRED_ANCHORS = frozenset({
  "status", "tbt_current", "tbt_next", "safety_source", "safety", "route", "lane",
})
_DEX_ENTRY = re.compile(r"classes(?:[2-9]|[1-9][0-9]+)?\.dex\Z")
_EXPECTED_OFFLINE_CAPTURE: dict[str, JsonValue] = {
  "batch_size": 32,
  "channel_quotas": {
    "lane": 1536,
    "route": 256,
    "safety": 6144,
    "status": 256,
    "tbt_current": 1536,
    "tbt_next": 1536,
  },
  "database_name": "capture-v3.sqlite3",
  "database_recovery_name": "capture-v3-recovery.sqlite3",
  "directory_name": "naver-diagnostic",
  "flush_interval_ms": 250,
  "journal_size_limit_bytes": 4_194_304,
  "local_queue_capacity": 256,
  "max_database_bytes": 67_108_864,
  "network_queue_capacity": 64,
  "payload_build_id": "naver-6.8.0.5-diagnostic-offline-v3",
  "pinned_per_channel": 64,
  "retry_initial_ms": 1_000,
  "retry_max_ms": 30_000,
  "schema_version": 1,
  "wal_autocheckpoint_pages": 256,
}
_EXPECTED_CAPTURE_SHARING: dict[str, JsonValue] = {
  "authority": "com.nhn.android.nmap.fileprovider",
  "external_files_root_path": "NaverNavi",
  "export_database_name": "capture-v3-export.sqlite3",
  "file_provider_root_name": "navi_trace",
  "grantee_package": "com.android.shell",
  "quiesce_timeout_ms": 5_000,
  "infrastructure_anchor": {
    "name": "capture_export_quiesce",
    "dex_entry": "classes.dex",
    "class_descriptor": "Landroidx/core/content/FileProvider;",
    "method_descriptor": (
      "openFile(Landroid/net/Uri;Ljava/lang/String;)"
      "Landroid/os/ParcelFileDescriptor;"
    ),
    "instruction_window": [
      (
        "invoke-virtual Landroidx/core/content/FileProvider;->f()"
        "Landroidx/core/content/FileProvider$PathStrategy;"
      ),
      "move-result-object",
      (
        "invoke-interface Landroidx/core/content/FileProvider$PathStrategy;"
        "->b(Landroid/net/Uri;)Ljava/io/File;"
      ),
    ],
    "expected_count": 1,
    "diagnostic_hook": {
      "argument": {"index": 0, "kind": "parameter"},
      "method_descriptor": "beforeCaptureRead(Ljava/lang/Object;)V",
      "placement": {"index": 0, "kind": "method_entry"},
    },
  },
}


def _mapping(value: JsonValue, label: str) -> dict[str, JsonValue]:
  if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
    raise ProfileError(f"{label} must be an object")
  return value


def _sequence(value: JsonValue, label: str) -> list[JsonValue]:
  if not isinstance(value, list):
    raise ProfileError(f"{label} must be an array")
  return value


def _require_keys(item: dict[str, JsonValue], expected: frozenset[str], label: str) -> None:
  actual = frozenset(item)
  if actual != expected:
    missing = ",".join(sorted(expected - actual)) or "none"
    extra = ",".join(sorted(actual - expected)) or "none"
    raise ProfileError(f"{label} keys differ (missing={missing}; extra={extra})")


def _text(value: JsonValue, label: str) -> str:
  if not isinstance(value, str) or not value:
    raise ProfileError(f"{label} must be a non-empty string")
  return value


def _integer(value: JsonValue, label: str) -> int:
  if not isinstance(value, int) or isinstance(value, bool) or value < 0:
    raise ProfileError(f"{label} must be a non-negative integer")
  return value


def _positive_integer(value: JsonValue, label: str) -> int:
  result = _integer(value, label)
  if result == 0:
    raise ProfileError(f"{label} must be positive")
  return result


def _hex_digest(value: JsonValue, label: str) -> str:
  digest = _text(value, label).lower()
  if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
    raise ProfileError(f"{label} must be a SHA-256 hex digest")
  return digest


def _texts(value: JsonValue, label: str) -> tuple[str, ...]:
  return tuple(_text(item, f"{label}[]") for item in _sequence(value, label))


def _file_name(value: JsonValue, label: str) -> str:
  result = _text(value, label)
  if Path(result).name != result or "/" in result or "\\" in result:
    raise ProfileError(f"{label} must be a path-independent file name")
  return result


def _base(value: JsonValue) -> BaseProfile:
  item = _mapping(value, "profile.base")
  _require_keys(item, frozenset({
    "size", "sha256", "dex_count", "native_library_count", "required_split_types",
  }), "profile.base")
  split_types = _texts(item.get("required_split_types"), "profile.base.required_split_types")
  if not split_types or len(split_types) != len(set(split_types)):
    raise ProfileError("profile.base.required_split_types must be non-empty and unique")
  return BaseProfile(
    size=_positive_integer(item.get("size"), "profile.base.size"),
    sha256=_hex_digest(item.get("sha256"), "profile.base.sha256"),
    dex_count=_positive_integer(item.get("dex_count"), "profile.base.dex_count"),
    native_library_count=_integer(item.get("native_library_count"), "profile.base.native_library_count"),
    required_split_types=split_types,
  )


def _split(value: JsonValue, index: int) -> SplitProfile:
  label = f"profile.companion.splits[{index}]"
  item = _mapping(value, label)
  _require_keys(item, frozenset({"entry", "split_name", "split_type", "size", "sha256"}), label)
  return SplitProfile(
    entry=_file_name(item.get("entry"), f"{label}.entry"),
    split_name=_text(item.get("split_name"), f"{label}.split_name"),
    split_type=_text(item.get("split_type"), f"{label}.split_type"),
    size=_positive_integer(item.get("size"), f"{label}.size"),
    sha256=_hex_digest(item.get("sha256"), f"{label}.sha256"),
  )


def _dex_entries(value: JsonValue) -> dict[str, DexEntryProfile]:
  entries = _mapping(value, "profile.dex_entries")
  if not entries:
    raise ProfileError("profile.dex_entries must not be empty")
  result: dict[str, DexEntryProfile] = {}
  for name, raw in entries.items():
    if _DEX_ENTRY.fullmatch(name) is None:
      raise ProfileError("profile.dex_entries keys must be exact classes*.dex entries")
    label = f"profile.dex_entries.{name}"
    item = _mapping(raw, label)
    _require_keys(item, frozenset({"size", "sha256"}), label)
    result[name] = DexEntryProfile(
      size=_positive_integer(item.get("size"), f"{label}.size"),
      sha256=_hex_digest(item.get("sha256"), f"{label}.sha256"),
    )
  return result


def _payload_modes(value: JsonValue) -> tuple[str, ...]:
  modes = _texts(value, "profile.payload_modes")
  if modes != ("diagnostic", "production"):
    raise ProfileError("profile.payload_modes must be exactly diagnostic and production")
  return modes


def _anchor(value: JsonValue, index: int) -> AnchorProfile:
  label = f"profile.anchors[{index}]"
  item = _mapping(value, label)
  _require_keys(item, frozenset({
    "name", "dex_entry", "class_descriptor", "method_descriptor", "instruction_window", "expected_count",
    "diagnostic_hook",
  }), label)
  expected_count = _integer(item.get("expected_count"), f"{label}.expected_count")
  if expected_count != 1:
    raise ProfileError(f"{label}.expected_count must be exactly 1")
  dex_entry = _text(item.get("dex_entry"), f"{label}.dex_entry")
  if _DEX_ENTRY.fullmatch(dex_entry) is None:
    raise ProfileError(f"{label}.dex_entry must be an exact classes*.dex entry")
  class_descriptor = _text(item.get("class_descriptor"), f"{label}.class_descriptor")
  method_descriptor = _text(item.get("method_descriptor"), f"{label}.method_descriptor")
  window = _texts(item.get("instruction_window"), f"{label}.instruction_window")
  if not class_descriptor.startswith("L") or not class_descriptor.endswith(";"):
    raise ProfileError(f"{label}.class_descriptor must be an exact DEX class descriptor")
  if "(" not in method_descriptor or ")" not in method_descriptor:
    raise ProfileError(f"{label}.method_descriptor must be an exact DEX method descriptor")
  if not window:
    raise ProfileError(f"{label}.instruction_window must not be empty")
  hook = _mapping(item.get("diagnostic_hook"), f"{label}.diagnostic_hook")
  _require_keys(hook, frozenset({"method_descriptor", "placement", "argument"}), f"{label}.diagnostic_hook")
  placement = _mapping(hook.get("placement"), f"{label}.diagnostic_hook.placement")
  argument = _mapping(hook.get("argument"), f"{label}.diagnostic_hook.argument")
  _require_keys(placement, frozenset({"kind", "index"}), f"{label}.diagnostic_hook.placement")
  _require_keys(argument, frozenset({"kind", "index"}), f"{label}.diagnostic_hook.argument")
  placement_kind = _text(placement.get("kind"), f"{label}.diagnostic_hook.placement.kind")
  argument_kind = _text(argument.get("kind"), f"{label}.diagnostic_hook.argument.kind")
  if placement_kind not in {"method_entry", "before_instruction", "after_instruction"}:
    raise ProfileError(f"{label}.diagnostic_hook.placement.kind is unsupported")
  if argument_kind not in {"parameter", "window_register"}:
    raise ProfileError(f"{label}.diagnostic_hook.argument.kind is unsupported")
  return AnchorProfile(
    name=_text(item.get("name"), f"{label}.name"),
    dex_entry=dex_entry,
    class_descriptor=class_descriptor,
    method_descriptor=method_descriptor,
    instruction_window=window,
    expected_count=expected_count,
    diagnostic_hook=DiagnosticHookProfile(
      method_descriptor=_text(hook.get("method_descriptor"), f"{label}.diagnostic_hook.method_descriptor"),
      placement_kind=placement_kind,
      placement_index=_integer(placement.get("index"), f"{label}.diagnostic_hook.placement.index"),
      argument_kind=argument_kind,
      argument_index=_integer(argument.get("index"), f"{label}.diagnostic_hook.argument.index"),
    ),
  )


def _diagnostic_payload(value: JsonValue) -> DiagnosticPayloadProfile:
  label = "profile.diagnostic_payload"
  item = _mapping(value, label)
  fields = frozenset(DiagnosticPayloadProfile.__dataclass_fields__)
  _require_keys(item, fields, label)
  return DiagnosticPayloadProfile(
    accessor_allowlists=_accessor_allowlists(item.get("accessor_allowlists")),
    android_platform_api=_positive_integer(item.get("android_platform_api"), f"{label}.android_platform_api"),
    build_tools_version=_text(item.get("build_tools_version"), f"{label}.build_tools_version"),
    hook_class_descriptor=_text(item.get("hook_class_descriptor"), f"{label}.hook_class_descriptor"),
    main_process=_text(item.get("main_process"), f"{label}.main_process"),
    max_accessors=_positive_integer(item.get("max_accessors"), f"{label}.max_accessors"),
    max_collection_items=_positive_integer(item.get("max_collection_items"), f"{label}.max_collection_items"),
    max_sample_chars=_positive_integer(item.get("max_sample_chars"), f"{label}.max_sample_chars"),
    min_api=_positive_integer(item.get("min_api"), f"{label}.min_api"),
    network_thread_name=_text(item.get("network_thread_name"), f"{label}.network_thread_name"),
    payload_dex_entry=_file_name(item.get("payload_dex_entry"), f"{label}.payload_dex_entry"),
    queue_capacity=_positive_integer(item.get("queue_capacity"), f"{label}.queue_capacity"),
    receiver_host=_text(item.get("receiver_host"), f"{label}.receiver_host"),
    receiver_port=_positive_integer(item.get("receiver_port"), f"{label}.receiver_port"),
    socket_timeout_ms=_positive_integer(item.get("socket_timeout_ms"), f"{label}.socket_timeout_ms"),
    offline_capture=_offline_capture(item.get("offline_capture")),
    capture_sharing=_capture_sharing(item.get("capture_sharing")),
  )


def _capture_sharing(value: JsonValue) -> CaptureSharingProfile:
  label = "profile.diagnostic_payload.capture_sharing"
  item = _mapping(value, label)
  fields = frozenset(CaptureSharingProfile.__dataclass_fields__)
  _require_keys(item, fields, label)
  if item != _EXPECTED_CAPTURE_SHARING:
    raise ProfileError(f"{label} values differ from the fixed contract")
  anchor = _anchor(item.get("infrastructure_anchor"), 0)
  if anchor.name != "capture_export_quiesce" or anchor.dex_entry != "classes.dex":
    raise ProfileError(f"{label}.infrastructure_anchor is invalid")
  return CaptureSharingProfile(
    authority=_text(item.get("authority"), f"{label}.authority"),
    external_files_root_path=_file_name(
      item.get("external_files_root_path"),
      f"{label}.external_files_root_path",
    ),
    export_database_name=_file_name(
      item.get("export_database_name"),
      f"{label}.export_database_name",
    ),
    file_provider_root_name=_file_name(
      item.get("file_provider_root_name"),
      f"{label}.file_provider_root_name",
    ),
    grantee_package=_text(
      item.get("grantee_package"), f"{label}.grantee_package"
    ),
    quiesce_timeout_ms=_positive_integer(
      item.get("quiesce_timeout_ms"), f"{label}.quiesce_timeout_ms"
    ),
    infrastructure_anchor=anchor,
  )


def _offline_capture(value: JsonValue) -> OfflineCaptureProfile:
  label = "profile.diagnostic_payload.offline_capture"
  item = _mapping(value, label)
  fields = frozenset(OfflineCaptureProfile.__dataclass_fields__)
  _require_keys(item, fields, label)
  if item != _EXPECTED_OFFLINE_CAPTURE:
    raise ProfileError(f"{label} values differ from the fixed contract")
  quotas = _mapping(item.get("channel_quotas"), f"{label}.channel_quotas")
  channels = frozenset(("status", "tbt_current", "tbt_next", "safety", "route", "lane"))
  if frozenset(quotas) != channels:
    raise ProfileError(f"{label}.channel_quotas must cover exactly six channels")
  return OfflineCaptureProfile(
    batch_size=_positive_integer(item.get("batch_size"), f"{label}.batch_size"),
    channel_quotas={
      channel: _positive_integer(quota, f"{label}.channel_quotas.{channel}")
      for channel, quota in quotas.items()
    },
    database_name=_file_name(item.get("database_name"), f"{label}.database_name"),
    database_recovery_name=_file_name(
      item.get("database_recovery_name"), f"{label}.database_recovery_name"
    ),
    directory_name=_file_name(item.get("directory_name"), f"{label}.directory_name"),
    flush_interval_ms=_positive_integer(
      item.get("flush_interval_ms"), f"{label}.flush_interval_ms"
    ),
    journal_size_limit_bytes=_positive_integer(
      item.get("journal_size_limit_bytes"), f"{label}.journal_size_limit_bytes"
    ),
    local_queue_capacity=_positive_integer(
      item.get("local_queue_capacity"), f"{label}.local_queue_capacity"
    ),
    max_database_bytes=_positive_integer(
      item.get("max_database_bytes"), f"{label}.max_database_bytes"
    ),
    network_queue_capacity=_positive_integer(
      item.get("network_queue_capacity"), f"{label}.network_queue_capacity"
    ),
    payload_build_id=_text(item.get("payload_build_id"), f"{label}.payload_build_id"),
    pinned_per_channel=_positive_integer(
      item.get("pinned_per_channel"), f"{label}.pinned_per_channel"
    ),
    retry_initial_ms=_positive_integer(
      item.get("retry_initial_ms"), f"{label}.retry_initial_ms"
    ),
    retry_max_ms=_positive_integer(
      item.get("retry_max_ms"), f"{label}.retry_max_ms"
    ),
    schema_version=_positive_integer(
      item.get("schema_version"), f"{label}.schema_version"
    ),
    wal_autocheckpoint_pages=_positive_integer(
      item.get("wal_autocheckpoint_pages"), f"{label}.wal_autocheckpoint_pages"
    ),
  )


def _accessor_allowlists(value: JsonValue) -> Mapping[str, tuple[str, ...]]:
  allowlists = _mapping(value, "profile.diagnostic_payload.accessor_allowlists")
  channels = frozenset(("status", "tbt_current", "tbt_next", "safety", "route", "lane"))
  if frozenset(allowlists) != channels:
    raise ProfileError("profile.diagnostic_payload.accessor_allowlists must cover exactly six channels")
  result = {
    channel: _texts(entries, f"profile.diagnostic_payload.accessor_allowlists.{channel}")
    for channel, entries in allowlists.items()
  }
  empty = sorted(channel for channel, entries in result.items() if channel != "status" and not entries)
  if empty:
    raise ProfileError(
      "profile.diagnostic_payload.accessor_allowlists must be non-empty except status: "
      + ",".join(empty)
    )
  return result


def load_profile(name: str) -> InspectionProfile:
  path = _SUPPORTED.get(name)
  if path is None:
    raise ProfileError(f"unsupported profile: {name}")
  try:
    raw: JsonValue = json.loads(path.read_text(encoding="utf-8"))
  except (OSError, json.JSONDecodeError) as error:
    raise ProfileError(f"cannot load profile {name}: {error}") from error
  root = _mapping(raw, "profile")
  _require_keys(root, frozenset({
    "schema_version", "profile_id", "package_name", "version_code", "version_name",
    "package_signer_sha256", "source_stamp_sha256", "base", "dex_entries", "payload_modes",
    "diagnostic_payload", "companion", "anchors",
  }), "profile")
  if _integer(root.get("schema_version"), "profile.schema_version") != 1:
    raise ProfileError("profile.schema_version must be 1")
  profile_id = _text(root.get("profile_id"), "profile.profile_id")
  if profile_id != name:
    raise ProfileError("profile.profile_id must match the selected profile")
  base = _base(root.get("base"))
  dex_entries = _dex_entries(root.get("dex_entries"))
  payload_modes = _payload_modes(root.get("payload_modes"))
  companion = _mapping(root.get("companion"), "profile.companion")
  _require_keys(companion, frozenset({"base_entry", "splits"}), "profile.companion")
  splits = tuple(
    _split(item, index)
    for index, item in enumerate(_sequence(companion.get("splits"), "profile.companion.splits"))
  )
  split_entries = tuple(item.entry for item in splits)
  split_names = tuple(item.split_name for item in splits)
  split_types = tuple(item.split_type for item in splits)
  if not splits or len(split_entries) != len(set(split_entries)) or len(split_names) != len(set(split_names)):
    raise ProfileError("profile companion splits must be non-empty with unique entries and names")
  if split_types != base.required_split_types:
    raise ProfileError("profile companion split types must exactly match required split types")
  anchors = tuple(
    _anchor(item, index)
    for index, item in enumerate(_sequence(root.get("anchors"), "profile.anchors"))
  )
  anchor_names = tuple(anchor.name for anchor in anchors)
  if frozenset(anchor_names) != _REQUIRED_ANCHORS or len(anchor_names) != len(_REQUIRED_ANCHORS):
    raise ProfileError("profile must contain each of the seven required anchors exactly once")
  missing_anchor_dex = sorted({anchor.dex_entry for anchor in anchors} - set(dex_entries))
  if missing_anchor_dex:
    raise ProfileError(f"profile.dex_entries missing anchor entries: {','.join(missing_anchor_dex)}")
  return InspectionProfile(
    name=profile_id,
    package_name=_text(root.get("package_name"), "profile.package_name"),
    version_code=_positive_integer(root.get("version_code"), "profile.version_code"),
    version_name=_text(root.get("version_name"), "profile.version_name"),
    package_signer_sha256=_hex_digest(root.get("package_signer_sha256"), "profile.package_signer_sha256"),
    source_stamp_sha256=_hex_digest(root.get("source_stamp_sha256"), "profile.source_stamp_sha256"),
    base=base,
    dex_entries=dex_entries,
    payload_modes=payload_modes,
    bundle_base_entry=_file_name(companion.get("base_entry"), "profile.companion.base_entry"),
    splits=splits,
    anchors=anchors,
    diagnostic_payload=_diagnostic_payload(root.get("diagnostic_payload")),
  )
