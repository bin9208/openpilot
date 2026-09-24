from __future__ import annotations

import base64
from collections import Counter
from collections.abc import Mapping
import hashlib
import json
import os
from pathlib import Path
import re
import struct
import tempfile
import zipfile

from tools.naver_map_patch.inspect_tools import (
  InspectionError,
  discover_tools,
  run_checked,
  sha256_file,
  verify_profiled_dex_entries,
)
from tools.naver_map_patch.profile import InspectionProfile
from tools.naver_map_patch.split_package import (
  ArchivePatch,
  ErrorCode,
  PackagingError,
  PayloadIdentity,
  Sha256,
)


_COUNT = re.compile(r"([a-z][a-z0-9_]*)\t([0-9]+)\Z")
_REPO_ROOT = Path(__file__).resolve().parents[2]
_PRODUCTION_ANCHORS = ("status", "tbt_current", "tbt_next", "safety_source", "safety", "route")
_PRODUCTION_HOOKS = {
  "status": ("onStatus(Ljava/lang/Object;)V", "method_entry", 0, "parameter", 0),
  "tbt_current": ("onCurrentTbt(Ljava/lang/Object;)V", "after_instruction", 1, "parameter", 0),
  "tbt_next": ("onNextTbt(Ljava/lang/Object;)V", "before_instruction", 2, "parameter", 1),
  "safety_source": ("onSafetySource(Ljava/lang/Object;)V", "method_entry", 0, "parameter", 0),
  "safety": ("onSafety(Ljava/lang/Object;)V", "before_instruction", 2, "window_register", 2),
  "route": ("onRoute(Ljava/lang/Object;)V", "method_entry", 0, "parameter", 0),
}
_PRODUCTION_CONTRACT = _REPO_ROOT / "tools" / "naver_map_patch" / "profiles" / "6.8.0.5-production.json"
FIELD_ACCEPTANCE_BUILD_ID = "naver-6.8.0.5-field-acceptance-v4"
_DIAGNOSTIC_HOOK_MARKER = b"Lai/comma/naver/payload/DiagnosticHooks;"
_FIELD_ACCEPTANCE_HOOK_MARKER = b"Lai/comma/naver/payload/FieldAcceptanceHooks;"
_PRODUCTION_HOOK_MARKER = b"Lai/comma/naver/payload/ProductionHooks;"
_DIAGNOSTIC_CONFIG = "Lai/comma/naver/payload/DiagnosticConfig;"
_FIELD_ACCEPTANCE_TOP_LEVEL_TYPES = frozenset({
  "Lai/comma/naver/payload/AndroidApplicationFiles;",
  "Lai/comma/naver/payload/AndroidCaptureSharing;",
  "Lai/comma/naver/payload/AndroidProcess;",
  "Lai/comma/naver/payload/AndroidSQLiteCaptureDatabase;",
  "Lai/comma/naver/payload/AsyncDiagnosticSink;",
  "Lai/comma/naver/payload/BoundedMappingDiagnostics;",
  "Lai/comma/naver/payload/CaptureDatabase;",
  "Lai/comma/naver/payload/CaptureRetentionPolicy;",
  _DIAGNOSTIC_CONFIG,
  "Lai/comma/naver/payload/DiagnosticCounters;",
  "Lai/comma/naver/payload/DiagnosticFrameSink;",
  "Lai/comma/naver/payload/DiagnosticRuntime;",
  "Lai/comma/naver/payload/DiagnosticSampler;",
  "Lai/comma/naver/payload/EncodedDiagnosticFrame;",
  "Lai/comma/naver/payload/FieldAcceptanceHooks;",
  "Lai/comma/naver/payload/FrameBatchConsumer;",
  "Lai/comma/naver/payload/HookRuntime;",
  "Lai/comma/naver/payload/Naver6805ObjectMapper;",
  "Lai/comma/naver/payload/NaverNavigationAggregator;",
  "Lai/comma/naver/payload/NaverNavigationEnvelope;",
  "Lai/comma/naver/payload/NaverNavigationSender;",
  "Lai/comma/naver/payload/NaverNavigationState;",
  "Lai/comma/naver/payload/NavigationTransport;",
  "Lai/comma/naver/payload/NetworkFrameConsumer;",
  "Lai/comma/naver/payload/OfflineCaptureStore;",
  "Lai/comma/naver/payload/PersistentNavigationTransport;",
  "Lai/comma/naver/payload/ProductionRuntime;",
})
_HOOK_MARKERS = frozenset({
  _DIAGNOSTIC_HOOK_MARKER.decode(),
  _FIELD_ACCEPTANCE_HOOK_MARKER.decode(),
  _PRODUCTION_HOOK_MARKER.decode(),
})


def _dex_u4(content: bytes, offset: int) -> int:
  if offset < 0 or offset + 4 > len(content):
    raise ValueError("DEX offset is outside the file")
  return struct.unpack_from("<I", content, offset)[0]


def _dex_uleb128(content: bytes, offset: int) -> tuple[int, int]:
  value = 0
  for shift in range(0, 35, 7):
    if offset >= len(content):
      raise ValueError("DEX ULEB128 is truncated")
    current = content[offset]
    offset += 1
    value |= (current & 0x7f) << shift
    if current < 0x80:
      return value, offset
  raise ValueError("DEX ULEB128 is oversized")


def _dex_section(content: bytes, header_offset: int, item_size: int) -> tuple[int, int]:
  size = _dex_u4(content, header_offset)
  offset = _dex_u4(content, header_offset + 4)
  if size == 0:
    if offset != 0:
      raise ValueError("empty DEX section has an offset")
  elif offset < 112 or offset + size * item_size > len(content):
    raise ValueError("DEX section is outside the file")
  return size, offset


def _dex_string(content: bytes, offset: int) -> str:
  utf16_size, cursor = _dex_uleb128(content, offset)
  terminator = content.find(b"\x00", cursor)
  if terminator < 0:
    raise ValueError("DEX string is unterminated")
  raw = content[cursor:terminator].replace(b"\xc0\x80", b"\x00")
  value = raw.decode("utf-8", errors="surrogatepass")
  if len(value.encode("utf-16-le", errors="surrogatepass")) // 2 != utf16_size:
    raise ValueError("DEX string length differs")
  return value


def _dex_encoded_value(content: bytes, offset: int) -> tuple[tuple[int, int | None], int]:
  if offset >= len(content):
    raise ValueError("DEX encoded value is truncated")
  header = content[offset]
  offset += 1
  value_type = header & 0x1f
  value_arg = header >> 5
  if value_type in {0x00, 0x02, 0x03, 0x04, 0x06, 0x10, 0x11, 0x15, 0x16, 0x17, 0x18, 0x19, 0x1a, 0x1b}:
    width = value_arg + 1
    if offset + width > len(content):
      raise ValueError("DEX encoded scalar is truncated")
    value = int.from_bytes(content[offset:offset + width], "little")
    return (value_type, value), offset + width
  if value_type == 0x1c:
    if value_arg != 0:
      raise ValueError("DEX encoded array has invalid flags")
    size, offset = _dex_uleb128(content, offset)
    for _ in range(size):
      _value, offset = _dex_encoded_value(content, offset)
    return (value_type, None), offset
  if value_type == 0x1d:
    if value_arg != 0:
      raise ValueError("DEX encoded annotation has invalid flags")
    _type_index, offset = _dex_uleb128(content, offset)
    size, offset = _dex_uleb128(content, offset)
    for _ in range(size):
      _name_index, offset = _dex_uleb128(content, offset)
      _value, offset = _dex_encoded_value(content, offset)
    return (value_type, None), offset
  if value_type == 0x1e and value_arg == 0:
    return (value_type, None), offset
  if value_type == 0x1f and value_arg in (0, 1):
    return (value_type, value_arg), offset
  raise ValueError("DEX encoded value type is invalid")


def _dex_inventory(content: bytes) -> tuple[tuple[str, ...], tuple[str, ...], str]:
  if (
    len(content) < 112
    or content[:4] != b"dex\n"
    or content[7] != 0
    or _dex_u4(content, 32) != len(content)
    or _dex_u4(content, 36) != 112
    or _dex_u4(content, 40) != 0x12345678
  ):
    raise ValueError("DEX header differs")
  string_count, string_ids_off = _dex_section(content, 56, 4)
  type_count, type_ids_off = _dex_section(content, 64, 4)
  field_count, field_ids_off = _dex_section(content, 80, 8)
  class_count, class_defs_off = _dex_section(content, 96, 32)
  strings = tuple(
    _dex_string(content, _dex_u4(content, string_ids_off + index * 4))
    for index in range(string_count)
  )
  if len(strings) != len(set(strings)):
    raise ValueError("DEX string inventory is not unique")
  type_string_indices = tuple(_dex_u4(content, type_ids_off + index * 4) for index in range(type_count))
  if any(index >= len(strings) for index in type_string_indices):
    raise ValueError("DEX type string index is invalid")
  types = tuple(strings[index] for index in type_string_indices)
  class_defs = tuple(
    struct.unpack_from("<IIIIIIII", content, class_defs_off + index * 32)
    for index in range(class_count)
  )
  class_indices = tuple(item[0] for item in class_defs)
  if len(class_indices) != len(set(class_indices)) or any(index >= len(types) for index in class_indices):
    raise ValueError("DEX class inventory is invalid")
  classes = tuple(types[index] for index in class_indices)

  fields: list[tuple[str, str, str]] = []
  for index in range(field_count):
    class_index, type_index, name_index = struct.unpack_from("<HHI", content, field_ids_off + index * 8)
    if class_index >= len(types) or type_index >= len(types) or name_index >= len(strings):
      raise ValueError("DEX field index is invalid")
    fields.append((types[class_index], types[type_index], strings[name_index]))
  target_fields = tuple(
    index for index, field in enumerate(fields)
    if field == (_DIAGNOSTIC_CONFIG, "Ljava/lang/String;", "OFFLINE_CAPTURE_PAYLOAD_BUILD_ID")
  )
  config_defs = tuple(item for item in class_defs if types[item[0]] == _DIAGNOSTIC_CONFIG)
  if len(target_fields) != 1 or len(config_defs) != 1:
    raise ValueError("DiagnosticConfig build identity field differs")
  target_field = target_fields[0]
  class_data_off = config_defs[0][6]
  static_values_off = config_defs[0][7]
  if class_data_off == 0 or static_values_off == 0:
    raise ValueError("DiagnosticConfig build identity has no value")
  static_size, cursor = _dex_uleb128(content, class_data_off)
  _instance_size, cursor = _dex_uleb128(content, cursor)
  _direct_size, cursor = _dex_uleb128(content, cursor)
  _virtual_size, cursor = _dex_uleb128(content, cursor)
  static_fields: list[tuple[int, int]] = []
  field_index = 0
  for _ in range(static_size):
    field_delta, cursor = _dex_uleb128(content, cursor)
    access_flags, cursor = _dex_uleb128(content, cursor)
    field_index += field_delta
    if field_index >= len(fields):
      raise ValueError("DEX static field index is invalid")
    static_fields.append((field_index, access_flags))
  matching_static = tuple(
    (ordinal, access) for ordinal, (index, access) in enumerate(static_fields)
    if index == target_field
  )
  if len(matching_static) != 1 or matching_static[0][1] & 0x18 != 0x18:
    raise ValueError("DiagnosticConfig build identity field flags differ")
  value_count, cursor = _dex_uleb128(content, static_values_off)
  values: list[tuple[int, int | None]] = []
  for _ in range(value_count):
    value, cursor = _dex_encoded_value(content, cursor)
    values.append(value)
  target_ordinal = matching_static[0][0]
  if target_ordinal >= len(values):
    raise ValueError("DiagnosticConfig build identity value is absent")
  value_type, string_index = values[target_ordinal]
  if value_type != 0x17 or string_index is None or string_index >= len(strings):
    raise ValueError("DiagnosticConfig build identity value is not a string")
  return strings, classes, strings[string_index]


def _verify_field_acceptance_dex(
  content: bytes,
  path: Path | None = None,
) -> None:
  try:
    strings, classes, declared_build_id = _dex_inventory(content)
  except (UnicodeError, ValueError, struct.error) as error:
    raise PackagingError(
      ErrorCode.OUTPUT_HASH_MISMATCH,
      "field-acceptance payload DEX is malformed",
      path=path,
    ) from error
  top_level_types = frozenset(
    value[:value.index("$")] + ";" if "$" in value else value
    for value in classes
  )
  hook_markers = frozenset(value for value in strings if value in _HOOK_MARKERS)
  build_markers = frozenset(value for value in strings if value.startswith("naver-6.8.0.5-"))
  if (
    top_level_types != _FIELD_ACCEPTANCE_TOP_LEVEL_TYPES
    or hook_markers != {_FIELD_ACCEPTANCE_HOOK_MARKER.decode()}
    or build_markers != {FIELD_ACCEPTANCE_BUILD_ID}
    or declared_build_id != FIELD_ACCEPTANCE_BUILD_ID
  ):
    raise PackagingError(
      ErrorCode.OUTPUT_HASH_MISMATCH,
      "field-acceptance hook, build identity, or class inventory differs",
      path=path,
    )


def _verified_count_lines(
  raw: str,
  expected: Mapping[str, int],
  detail: str,
  path: Path | None = None,
) -> dict[str, int]:
  values = _count_lines(raw, set(expected), detail, path)
  if values != dict(expected):
    raise PackagingError(ErrorCode.TOOL_FAILURE, detail, path=path)
  return values


def _count_lines(
  raw: str,
  expected_names: set[str],
  detail: str,
  path: Path | None = None,
) -> dict[str, int]:
  parsed = [
    (matched.group(1), int(matched.group(2)))
    for line in raw.splitlines()
    if (matched := _COUNT.fullmatch(line.strip()))
  ]
  names = Counter(name for name, _count in parsed)
  values = {name: count for name, count in parsed}
  if set(names) != expected_names or any(names[name] != 1 for name in expected_names):
    raise PackagingError(ErrorCode.TOOL_FAILURE, detail, path=path)
  return values


def _production_contract(profile: InspectionProfile) -> dict[str, object]:
  try:
    contract = json.loads(_PRODUCTION_CONTRACT.read_text(encoding="utf-8"))
  except (OSError, UnicodeError, json.JSONDecodeError) as error:
    raise PackagingError(ErrorCode.MALFORMED_METADATA, "production contract is unreadable", path=_PRODUCTION_CONTRACT) from error
  payload = profile.diagnostic_payload
  if (
    not isinstance(contract, dict)
    or payload is None
    or profile.name != "6.8.0.5"
    or profile.payload_modes != ("diagnostic", "production")
    or contract.get("schema_version") != 1
    or contract.get("profile_id") != "6.8.0.5-production"
    or contract.get("source_profile_id") != profile.name
    or contract.get("package_name") != profile.package_name
    or contract.get("version_code") != profile.version_code
    or contract.get("payload_build_id") != "naver-6.8.0.5-public-beta-v2"
    or contract.get("payload_dex_entry") != payload.payload_dex_entry
    or contract.get("hook_class_descriptor") != "Lai/comma/naver/payload/ProductionHooks;"
    or contract.get("transport") != {
      "discovery_host": "255.255.255.255",
      "discovery_port": 7706,
      "response_port": 7705,
      "tcp_port": 7712,
      "discovery_timeout_ms": 500,
      "connect_timeout_ms": 500,
      "write_timeout_ms": 500,
      "reconnect_min_ms": 1000,
      "reconnect_max_ms": 30000,
      "heartbeat_ms": 500,
      "max_frame_bytes": 262144,
    }
  ):
    raise PackagingError(ErrorCode.MALFORMED_METADATA, "production contract does not match the exact profile")
  return contract


def _production_patch_rules(profile: InspectionProfile) -> tuple[str, ...]:
  contract = _production_contract(profile)
  anchors = {anchor.name: anchor for anchor in profile.anchors}
  if set(anchors) != {*_PRODUCTION_ANCHORS, "route", "lane"}:
    raise PackagingError(ErrorCode.MALFORMED_METADATA, "production anchor inventory is not exact")
  encoded: list[str] = []
  for name in _PRODUCTION_ANCHORS:
    anchor = anchors[name]
    hook = anchor.diagnostic_hook
    actual_hook = (
      hook.method_descriptor, hook.placement_kind, hook.placement_index,
      hook.argument_kind, hook.argument_index,
    )
    if anchor.expected_count != 1 or actual_hook != _PRODUCTION_HOOKS[name]:
      raise PackagingError(ErrorCode.MALFORMED_METADATA, f"production anchor {name} hook contract differs")
    fields = (
      anchor.name, anchor.dex_entry, anchor.class_descriptor, anchor.method_descriptor,
      str(len(anchor.instruction_window)), *anchor.instruction_window,
      str(contract["hook_class_descriptor"]), hook.method_descriptor,
      hook.placement_kind, str(hook.placement_index), hook.argument_kind, str(hook.argument_index),
    )
    encoded.append(base64.urlsafe_b64encode("\x1f".join(fields).encode()).decode().rstrip("="))
  return tuple(encoded)


def verify_production_archive(
  base_apk: Path,
  profile: InspectionProfile,
  environment: Mapping[str, str],
) -> None:
  _production_contract(profile)
  production_rules = _production_patch_rules(profile)
  try:
    tools = discover_tools(_REPO_ROOT, environment)
    java_bin = tools.java_home / "bin"
    process_environment = dict(environment)
    process_environment.update({
      "JAVA_HOME": str(tools.java_home),
      "PATH": f"{java_bin}{os.pathsep}{environment.get('PATH', '')}",
    })
    dexpatch = _REPO_ROOT / "tools" / "naver_map_patch" / "dexpatch"
    raw = run_checked(
      [
        str(tools.gradle), "-p", str(dexpatch), "verifyProductionArchive", "--no-daemon", "-q",
        "--dependency-verification=strict", "-PpayloadMode=production", f"-Papk={base_apk}",
        f"-PproductionRules={','.join(production_rules)}",
      ],
      env=process_environment,
      timeout_seconds=300.0,
      cwd=dexpatch,
    )
  except InspectionError as error:
    raise PackagingError(ErrorCode.TOOL_FAILURE, error.message, path=base_apk) from error
  _verified_count_lines(
    raw,
    {
      "production_composition": 1,
      "production_bindings": 1,
      "production_global_hooks": 1,
      "production_exact_hooks": 1,
    },
    "production archive verifier returned duplicate, missing, unexpected, or incorrect counts",
    base_apk,
  )


def _build_profiled_archive_patches(
  base: Path,
  profile: InspectionProfile,
  environment: Mapping[str, str],
  payload_mode: str,
  *,
  field_acceptance: bool = False,
) -> tuple[ArchivePatch, ...]:
  if payload_mode not in profile.payload_modes:
    raise PackagingError(ErrorCode.MALFORMED_METADATA, f"unsupported payload mode: {payload_mode}")
  if field_acceptance and payload_mode != "diagnostic":
    raise PackagingError(ErrorCode.MALFORMED_METADATA, "field acceptance must remain diagnostic")
  payload = profile.diagnostic_payload
  if payload is None:
    raise PackagingError(ErrorCode.MALFORMED_METADATA, "profile has no diagnostic payload configuration")
  try:
    base_size = base.stat().st_size
    base_sha256 = sha256_file(base)
  except (OSError, InspectionError) as error:
    raise PackagingError(ErrorCode.WRONG_HASH, "authoritative base is not readable", path=base) from error
  if base_size != profile.base.size or base_sha256 != profile.base.sha256:
    raise PackagingError(ErrorCode.WRONG_HASH, "authoritative base does not match the diagnostic profile", path=base)
  try:
    verify_profiled_dex_entries(base, profile.dex_entries)
    tools = discover_tools(_REPO_ROOT, environment)
    process_environment = dict(environment)
    java_bin = tools.java_home / "bin"
    process_environment.update({
      "JAVA_HOME": str(tools.java_home),
      "PATH": f"{java_bin}{os.pathsep}{environment.get('PATH', '')}",
    })
    dexpatch = _REPO_ROOT / "tools" / "naver_map_patch" / "dexpatch"
    profile_path = _REPO_ROOT / "tools" / "naver_map_patch" / "profiles" / f"{profile.name}.json"
    variant = "field-acceptance" if field_acceptance else payload_mode
    with tempfile.TemporaryDirectory(prefix=f"naver-{variant}-patch-") as temporary:
      output = Path(temporary) / "patches"
      if payload_mode == "diagnostic":
        task = "diagnosticPatch"
        extra_properties = ["-PfieldAcceptance=true"] if field_acceptance else []
        navigation_counts = {anchor.name for anchor in profile.anchors}
        infrastructure_anchor = payload.capture_sharing.infrastructure_anchor
        infrastructure_counts = {infrastructure_anchor.name}
        entries = tuple(dict.fromkeys((
          *(anchor.dex_entry for anchor in profile.anchors),
          infrastructure_anchor.dex_entry,
          payload.payload_dex_entry,
        )))
      else:
        task = "productionPatch"
        production_rules = _production_patch_rules(profile)
        extra_properties = [f"-PproductionRules={','.join(production_rules)}"]
        navigation_counts = set(_PRODUCTION_ANCHORS)
        infrastructure_counts = set()
        production_anchors = tuple(anchor for anchor in profile.anchors if anchor.name in navigation_counts)
        entries = tuple(dict.fromkeys((
          *(anchor.dex_entry for anchor in production_anchors), payload.payload_dex_entry,
        )))
      raw = run_checked(
        [
          str(tools.gradle), "-p", str(dexpatch), task, "--no-daemon", "-q",
          "--dependency-verification=strict", f"-Pprofile={profile_path}", f"-PpayloadMode={payload_mode}",
          f"-Papk={base}", f"-Poutput={output}", *extra_properties,
        ],
        env=process_environment,
        timeout_seconds=300.0,
        cwd=dexpatch,
      )
      expected_counts = navigation_counts | infrastructure_counts
      _verified_count_lines(
        raw,
        {name: 1 for name in expected_counts},
        f"{variant} patcher returned duplicate, missing, unexpected, or incorrect anchor counts",
      )
      patches = tuple(ArchivePatch(entry, (output / entry).read_bytes()) for entry in entries)
  except InspectionError as error:
    raise PackagingError(ErrorCode.TOOL_FAILURE, error.message) from error
  except OSError as error:
    raise PackagingError(ErrorCode.TOOL_FAILURE, "diagnostic patch output is unreadable") from error
  if any(not patch.content.startswith(b"dex\n") for patch in patches):
    raise PackagingError(ErrorCode.TOOL_FAILURE, "diagnostic patch output is not DEX")
  return patches


def build_diagnostic_archive_patches(
  base: Path,
  profile: InspectionProfile,
  environment: Mapping[str, str],
) -> tuple[ArchivePatch, ...]:
  return _build_profiled_archive_patches(base, profile, environment, "diagnostic")


def build_field_acceptance_archive_patches(
  base: Path,
  profile: InspectionProfile,
  environment: Mapping[str, str],
) -> tuple[ArchivePatch, ...]:
  return _build_profiled_archive_patches(
    base, profile, environment, "diagnostic", field_acceptance=True,
  )


def diagnostic_payload_identity(
  profile: InspectionProfile,
  patches: tuple[ArchivePatch, ...],
) -> PayloadIdentity:
  payload = profile.diagnostic_payload
  if payload is None:
    raise PackagingError(ErrorCode.MALFORMED_METADATA, "profile has no diagnostic payload configuration")
  matching = tuple(patch for patch in patches if patch.entry_name == payload.payload_dex_entry)
  if len(matching) != 1:
    raise PackagingError(
      ErrorCode.INVALID_PATCH_ENTRY,
      "diagnostic build must contain exactly one profiled payload DEX",
    )
  content = matching[0].content
  if _payload_marker(content) != "diagnostic":
    raise PackagingError(
      ErrorCode.OUTPUT_HASH_MISMATCH,
      "diagnostic payload DEX marker is absent or ambiguous",
    )
  return PayloadIdentity(
    mode="diagnostic",
    build_id=payload.offline_capture.payload_build_id,
    dex_entry=payload.payload_dex_entry,
    sha256=Sha256(hashlib.sha256(content).hexdigest()),
  )


def field_acceptance_payload_identity(
  profile: InspectionProfile,
  patches: tuple[ArchivePatch, ...],
) -> PayloadIdentity:
  payload = profile.diagnostic_payload
  if payload is None:
    raise PackagingError(ErrorCode.MALFORMED_METADATA, "profile has no diagnostic payload configuration")
  matching = tuple(patch for patch in patches if patch.entry_name == payload.payload_dex_entry)
  if len(matching) != 1:
    raise PackagingError(
      ErrorCode.INVALID_PATCH_ENTRY,
      "field-acceptance build must contain exactly one profiled payload DEX",
    )
  content = matching[0].content
  _verify_field_acceptance_dex(content)
  return PayloadIdentity(
    mode="diagnostic",
    build_id=FIELD_ACCEPTANCE_BUILD_ID,
    dex_entry=payload.payload_dex_entry,
    sha256=Sha256(hashlib.sha256(content).hexdigest()),
  )


def verify_field_acceptance_archive(
  base_apk: Path,
  profile: InspectionProfile,
) -> None:
  payload = profile.diagnostic_payload
  if payload is None:
    raise PackagingError(ErrorCode.MALFORMED_METADATA, "profile has no diagnostic payload configuration")
  try:
    with zipfile.ZipFile(base_apk, "r") as archive:
      names = archive.namelist()
      if names.count(payload.payload_dex_entry) != 1:
        raise PackagingError(
          ErrorCode.OUTPUT_HASH_MISMATCH,
          "patched base does not contain the exact field-acceptance payload DEX",
          path=base_apk,
        )
      content = archive.read(payload.payload_dex_entry)
  except PackagingError:
    raise
  except (OSError, KeyError, zipfile.BadZipFile) as error:
    raise PackagingError(
      ErrorCode.OUTPUT_HASH_MISMATCH,
      "patched base field-acceptance payload DEX is unreadable",
      path=base_apk,
    ) from error
  _verify_field_acceptance_dex(content, base_apk)


def installed_diagnostic_payload_identity(
  base_apk: Path,
  profile: InspectionProfile,
) -> PayloadIdentity:
  payload = profile.diagnostic_payload
  if payload is None:
    raise PackagingError(ErrorCode.MALFORMED_METADATA, "profile has no diagnostic payload configuration")
  try:
    with zipfile.ZipFile(base_apk, "r") as archive:
      names = archive.namelist()
      if names.count(payload.payload_dex_entry) != 1:
        raise PackagingError(
          ErrorCode.OUTPUT_HASH_MISMATCH,
          "patched base does not contain the exact profiled payload DEX",
          path=base_apk,
        )
      content = archive.read(payload.payload_dex_entry)
      digest = Sha256(hashlib.sha256(content).hexdigest())
  except PackagingError:
    raise
  except (OSError, KeyError, zipfile.BadZipFile) as error:
    raise PackagingError(
      ErrorCode.OUTPUT_HASH_MISMATCH,
      "patched base payload DEX is unreadable",
      path=base_apk,
    ) from error
  marker = _payload_marker(content)
  if marker not in ("diagnostic", "field_acceptance"):
    raise PackagingError(
      ErrorCode.OUTPUT_HASH_MISMATCH,
      "installed diagnostic payload DEX marker is absent or ambiguous",
      path=base_apk,
    )
  if marker == "field_acceptance":
    _verify_field_acceptance_dex(content, base_apk)
  return PayloadIdentity(
    mode="diagnostic",
    build_id=(
      FIELD_ACCEPTANCE_BUILD_ID
      if marker == "field_acceptance"
      else payload.offline_capture.payload_build_id
    ),
    dex_entry=payload.payload_dex_entry,
    sha256=digest,
  )


def production_payload_identity(
  profile: InspectionProfile,
  patches: tuple[ArchivePatch, ...],
) -> PayloadIdentity:
  contract = _production_contract(profile)
  dex_entry = str(contract["payload_dex_entry"])
  matching = tuple(patch for patch in patches if patch.entry_name == dex_entry)
  if len(matching) != 1:
    raise PackagingError(
      ErrorCode.INVALID_PATCH_ENTRY,
      "production build must contain exactly one profiled payload DEX",
    )
  return PayloadIdentity(
    mode="production",
    build_id=str(contract["payload_build_id"]),
    dex_entry=dex_entry,
    sha256=Sha256(hashlib.sha256(matching[0].content).hexdigest()),
  )


def installed_production_payload_identity(
  base_apk: Path,
  profile: InspectionProfile,
) -> PayloadIdentity:
  contract = _production_contract(profile)
  dex_entry = str(contract["payload_dex_entry"])
  try:
    with zipfile.ZipFile(base_apk, "r") as archive:
      names = archive.namelist()
      if names.count(dex_entry) != 1:
        raise PackagingError(
          ErrorCode.OUTPUT_HASH_MISMATCH,
          "patched base does not contain the exact production payload DEX",
          path=base_apk,
        )
      digest = Sha256(hashlib.sha256(archive.read(dex_entry)).hexdigest())
  except PackagingError:
    raise
  except (OSError, KeyError, zipfile.BadZipFile) as error:
    raise PackagingError(
      ErrorCode.OUTPUT_HASH_MISMATCH,
      "patched base production payload DEX is unreadable",
      path=base_apk,
    ) from error
  return PayloadIdentity(
    mode="production",
    build_id=str(contract["payload_build_id"]),
    dex_entry=dex_entry,
    sha256=digest,
  )


def installed_payload_mode(base_apk: Path, profile: InspectionProfile) -> str:
  payload = profile.diagnostic_payload
  if payload is None:
    raise PackagingError(ErrorCode.MALFORMED_METADATA, "profile has no payload configuration")
  try:
    with zipfile.ZipFile(base_apk, "r") as archive:
      names = archive.namelist()
      if names.count(payload.payload_dex_entry) != 1:
        raise PackagingError(
          ErrorCode.OUTPUT_HASH_MISMATCH,
          "patched base does not contain exactly one profiled payload DEX",
          path=base_apk,
        )
      content = archive.read(payload.payload_dex_entry)
  except PackagingError:
    raise
  except (OSError, KeyError, zipfile.BadZipFile) as error:
    raise PackagingError(ErrorCode.OUTPUT_HASH_MISMATCH, "patched base payload DEX is unreadable", path=base_apk) from error
  marker = _payload_marker(content)
  if marker is None:
    raise PackagingError(ErrorCode.OUTPUT_HASH_MISMATCH, "payload DEX mode marker is absent or ambiguous", path=base_apk)
  return "production" if marker == "production" else "diagnostic"


def _payload_marker(content: bytes) -> str | None:
  markers = {
    "diagnostic": _DIAGNOSTIC_HOOK_MARKER in content,
    "field_acceptance": _FIELD_ACCEPTANCE_HOOK_MARKER in content,
    "production": _PRODUCTION_HOOK_MARKER in content,
  }
  present = tuple(name for name, found in markers.items() if found)
  return present[0] if len(present) == 1 else None


def build_production_archive_patches(
  base: Path,
  profile: InspectionProfile,
  environment: Mapping[str, str],
) -> tuple[ArchivePatch, ...]:
  return _build_profiled_archive_patches(base, profile, environment, "production")
