from __future__ import annotations

import base64
import binascii
import ipaddress
import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Final, NewType, TypeAlias, assert_never


JsonScalar: TypeAlias = None | bool | int | float | str
JsonValue: TypeAlias = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]
DeviceIdentity = NewType("DeviceIdentity", str)


class ArtifactKind(StrEnum):
  APK_BYTES = "apk_bytes"
  COORDINATE = "coordinate"
  DEVICE_IDENTITY = "device_identity"
  IP_ADDRESS = "ip_address"
  KEY_MATERIAL = "key_material"
  KOREAN_TEXT = "korean_text"
  MALFORMED_INPUT = "malformed_input"
  SESSION_ID = "session_id"


@dataclass(frozen=True, slots=True)
class ScanFinding:
  path: str
  kind: ArtifactKind
  detail: str


@dataclass(frozen=True, slots=True)
class SanitizationRejected(Exception):
  findings: tuple[ScanFinding, ...]

  def __str__(self) -> str:
    return "; ".join(f"{finding.path}: {finding.kind}" for finding in self.findings)


_APK_MAGIC: Final = (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08", b"dex\n")
_COORDINATE_KEYS: Final = (
  ("latitude", "longitude"),
  ("lat", "lon"),
  ("lat", "lng"),
  ("vppospointlat", "vppospointlon"),
  ("goalposy", "goalposx"),
  ("y", "x"),
  ("currentlatitude", "currentlongitude"),
  ("currentlat", "currentlon"),
  ("currentlat", "currentlng"),
  ("latitudevalue", "longitudevalue"),
  ("ycoord", "xcoord"),
  ("goaly", "goalx"),
  ("north", "east"),
  ("mapy", "mapx"),
)
_BASE64_TOKEN_RE: Final = re.compile(r"(?<![A-Za-z0-9+/=])[A-Za-z0-9+/]{6,}={0,2}(?![A-Za-z0-9+/=])")
_HEX_TOKEN_RE: Final = re.compile(r"(?<![0-9A-Fa-f])[0-9A-Fa-f]{8,}(?![0-9A-Fa-f])")
_HANGUL_RE: Final = re.compile(r"[\uac00-\ud7a3]")
_IDENTITY_KEY_RE: Final = re.compile(
  r"(?:(?:android|advertising|client|device|dongle|install|installation)id|hardware(?:id|serial)|"
  r"(?:build)?fingerprint|imei|imsi|iccid|serial(?:number)?|mac(?:address)?|bssid)\Z"
)
_IPV4_RE: Final = re.compile(r"(?<![0-9.])(?:[0-9]{1,3}\.){3}[0-9]{1,3}(?![0-9.])")
_IPV6_RE: Final = re.compile(r"(?<![0-9A-Fa-f:])(?:[0-9A-Fa-f]{0,4}:){2,7}[0-9A-Fa-f]{0,4}(?![0-9A-Fa-f:])")
_JVM_OBJECT_TYPE_RE: Final = re.compile(r"L([^;]+);")
_ACCESSOR_SHAPE_PREFIX_RE: Final = re.compile(
  r"\A\$\.channels\.(?:status|tbt_current|tbt_next|safety|route|lane)"
  r"\[[0-9]+\]\.shape"
)
_SAFE_IDENTITY_RE: Final = re.compile(r"fixture-identity-[0-9]{4}\Z")
_SAFE_SESSION_RE: Final = re.compile(r"fixture-session-[0-9]{4}\Z")
_SECRET_KEY_RE: Final = re.compile(
  r"(?:access.?key|api.?key|auth|aws.?key|cookie|credential|password|private.?key|secret|token)", re.IGNORECASE
)
_GENERIC_ENCODED_SECRET_RE: Final = re.compile(
  r"(?<![0-9A-Fa-f])[0-9A-Fa-f]{32,}(?![0-9A-Fa-f])|"
  r"(?<![A-Za-z0-9+/])[A-Za-z0-9+/]{40,}={0,2}(?![A-Za-z0-9+/])"
)
_SPECIFIC_SECRET_VALUE_RE: Final = re.compile(
  r"(?:bearer\s+[A-Za-z0-9._~+/=-]+|sk-[A-Za-z0-9]{16,}|-----BEGIN [A-Z ]+PRIVATE KEY-----|"
  r"(?<![A-Z0-9])(?:A3T[A-Z0-9]|AGPA|AIDA|AIPA|AKIA|ANPA|ANVA|AROA|ASCA|ASIA)[A-Z0-9]{16}(?![A-Z0-9]))",
  re.IGNORECASE,
)
_SESSION_KEY_RE: Final = re.compile(r"session(?:_?id)?\Z", re.IGNORECASE)
# These public, fixed payload identifiers contain a dotted version that resembles
# IPv4. Only exact values at exact manifest/summary output paths are exempt.
_PUBLIC_PAYLOAD_BUILD_IDS: Final = (
  "naver-6.8.0.5-diagnostic-offline-v3",
  "naver-6.8.0.5-field-acceptance-v1",
  "naver-6.8.0.5-field-acceptance-v2",
  "naver-6.8.0.5-field-acceptance-v3",
  "naver-6.8.0.5-field-acceptance-v4",
)
_SAFE_PUBLIC_METADATA: Final = frozenset(
  ("$.payload_build_id", value) for value in _PUBLIC_PAYLOAD_BUILD_IDS
) | frozenset({
  ("$.run_payload_build_ids[0]", "naver-6.8.0.5-diagnostic-offline-v3"),
  ("$.run_payload_build_ids[0]", "naver-6.8.0.5-field-acceptance-v1"),
  ("$.run_payload_build_ids[0]", "naver-6.8.0.5-field-acceptance-v2"),
  ("$.run_payload_build_ids[0]", "naver-6.8.0.5-field-acceptance-v3"),
  ("$.run_payload_build_ids[0]", "naver-6.8.0.5-field-acceptance-v4"),
  ("$.run_payload_build_ids[1]", "naver-6.8.0.5-field-acceptance-v1"),
  ("$.run_payload_build_ids[1]", "naver-6.8.0.5-field-acceptance-v2"),
  ("$.run_payload_build_ids[1]", "naver-6.8.0.5-field-acceptance-v3"),
  ("$.run_payload_build_ids[1]", "naver-6.8.0.5-field-acceptance-v4"),
  ("$.run_payload_build_ids[2]", "naver-6.8.0.5-field-acceptance-v2"),
  ("$.run_payload_build_ids[2]", "naver-6.8.0.5-field-acceptance-v3"),
  ("$.run_payload_build_ids[2]", "naver-6.8.0.5-field-acceptance-v4"),
  ("$.run_payload_build_ids[3]", "naver-6.8.0.5-field-acceptance-v3"),
  ("$.run_payload_build_ids[3]", "naver-6.8.0.5-field-acceptance-v4"),
  ("$.run_payload_build_ids[4]", "naver-6.8.0.5-field-acceptance-v4"),
})


def _number(value: JsonValue) -> float | None:
  match value:
    case bool() | None | str() | list() | dict():
      return None
    case int() | float():
      return float(value)
    case unreachable:
      assert_never(unreachable)


def _normalized_key(key: str) -> str:
  return re.sub(r"[^a-z0-9]", "", key.casefold())


def _coordinate_pair(mapping: Mapping[str, JsonValue]) -> tuple[str, str] | None:
  original_keys = {_normalized_key(key): key for key in mapping}
  for latitude_key, longitude_key in _COORDINATE_KEYS:
    if latitude_key in original_keys and longitude_key in original_keys:
      return original_keys[latitude_key], original_keys[longitude_key]
  return None


def _is_identity_key(key: str) -> bool:
  return _IDENTITY_KEY_RE.fullmatch(_normalized_key(key)) is not None


def _contains_ip(value: str) -> bool:
  for pattern in (_IPV4_RE, _IPV6_RE):
    for match in pattern.finditer(value):
      try:
        ipaddress.ip_address(match.group())
      except ValueError:
        continue
      return True
  return False


def _has_apk_magic(data: bytes) -> bool:
  return any(magic in data for magic in _APK_MAGIC)


def _contains_encoded_apk(value: str) -> bool:
  if _has_apk_magic(value.encode("utf-8")):
    return True
  for match in _BASE64_TOKEN_RE.finditer(value):
    token = match.group()[:16].rstrip("=")
    try:
      decoded = base64.b64decode(token + "=" * (-len(token) % 4), validate=True)
    except binascii.Error:
      continue
    if _has_apk_magic(decoded):
      return True
  for match in _HEX_TOKEN_RE.finditer(value):
    token = match.group()[:16]
    if len(token) % 2 == 0 and _has_apk_magic(bytes.fromhex(token)):
      return True
  return False


def scan_bytes(data: bytes) -> tuple[ScanFinding, ...]:
  if _has_apk_magic(data):
    return (ScanFinding(path="$", kind=ArtifactKind.APK_BYTES, detail="archive or dex magic"),)
  return ()


def _coordinate_is_safe(mapping: Mapping[str, JsonValue], pair: tuple[str, str]) -> bool:
  latitude = _number(mapping[pair[0]])
  longitude = _number(mapping[pair[1]])
  if latitude is None or longitude is None:
    return False
  return abs(latitude) <= 0.5 and abs(longitude) <= 0.5 and latitude == round(latitude, 4) and longitude == round(longitude, 4)


def _scan_string(value: str, path: str) -> list[ScanFinding]:
  findings: list[ScanFinding] = []
  if _HANGUL_RE.search(value):
    findings.append(ScanFinding(path, ArtifactKind.KOREAN_TEXT, "Hangul text"))
  if _contains_ip(value) and (path, value) not in _SAFE_PUBLIC_METADATA:
    findings.append(ScanFinding(path, ArtifactKind.IP_ADDRESS, "IP address"))
  if _contains_encoded_apk(value) or _descriptor_contains_encoded_apk(path, value):
    findings.append(ScanFinding(path, ArtifactKind.APK_BYTES, "encoded archive or dex material"))
  if _SPECIFIC_SECRET_VALUE_RE.search(value) or _contains_generic_encoded_secret(path, value):
    findings.append(ScanFinding(path, ArtifactKind.KEY_MATERIAL, "key-like value"))
  return findings


def _contains_generic_encoded_secret(path: str, value: str) -> bool:
  if not _is_diagnostic_descriptor(path, value):
    return _GENERIC_ENCODED_SECRET_RE.search(value) is not None
  return any(
    _GENERIC_ENCODED_SECRET_RE.search(component)
    for object_type in _JVM_OBJECT_TYPE_RE.findall(value)
    for component in object_type.split("/")
  )


def _descriptor_contains_encoded_apk(path: str, value: str) -> bool:
  if not _is_diagnostic_descriptor(path, value):
    return False
  return any(_contains_encoded_apk(component) for component in value.strip("L;").split("/"))


def _is_diagnostic_descriptor(path: str, value: str) -> bool:
  if len(value) > 512:
    return False
  if path.startswith("$.observation"):
    suffix = path.removeprefix("$.observation")
  else:
    shape_prefix = _ACCESSOR_SHAPE_PREFIX_RE.match(path)
    if shape_prefix is None:
      return False
    suffix = path[shape_prefix.end():]
  while suffix.startswith(".accessors["):
    close = suffix.find("]")
    if close == -1 or not suffix[11:close].isdigit():
      return False
    suffix = suffix[close + 1:]
    if suffix == ".descriptor":
      return _is_method_descriptor(value)
    if not suffix.startswith(".value"):
      return False
    suffix = suffix.removeprefix(".value")
  if suffix in (".descriptor", ".root_descriptor") or _is_element_descriptor_suffix(suffix):
    return _is_jvm_type_descriptor(value)
  return False


def _is_element_descriptor_suffix(suffix: str) -> bool:
  if not suffix.startswith(".element_descriptors[") or not suffix.endswith("]"):
    return False
  return suffix[21:-1].isdigit()


def _is_method_descriptor(value: str) -> bool:
  if not value.startswith("("):
    return False
  index = 1
  while index < len(value) and value[index] != ")":
    next_index = _consume_jvm_type(value, index, allow_void=False)
    if next_index is None:
      return False
    index = next_index
  if index >= len(value) or value[index] != ")":
    return False
  return _consume_jvm_type(value, index + 1, allow_void=True) == len(value)


def _is_jvm_type_descriptor(value: str) -> bool:
  return _consume_jvm_type(value, 0, allow_void=True) == len(value)


def _consume_jvm_type(value: str, index: int, *, allow_void: bool) -> int | None:
  if index >= len(value):
    return None
  array = False
  while index < len(value) and value[index] == "[":
    array = True
    index += 1
  if index >= len(value):
    return None
  token = value[index]
  if token in "ZBCSIJFD" or (allow_void and not array and token == "V"):
    return index + 1
  if token != "L":
    return None
  end = value.find(";", index + 1)
  if end == -1 or end == index + 1:
    return None
  if not all(character.isascii() and (character.isalnum() or character in "_$/") for character in value[index + 1:end]):
    return None
  return end + 1


def _scan(value: JsonValue, path: str) -> list[ScanFinding]:
  match value:
    case dict() as mapping:
      findings: list[ScanFinding] = []
      pair = _coordinate_pair(mapping)
      if pair is not None and not _coordinate_is_safe(mapping, pair):
        findings.append(ScanFinding(path, ArtifactKind.COORDINATE, "coordinate is not synthetic and quantized"))
      for key, child in mapping.items():
        child_path = f"{path}.{key}"
        if _SECRET_KEY_RE.search(key):
          findings.append(ScanFinding(child_path, ArtifactKind.KEY_MATERIAL, "key-like field"))
        if _is_identity_key(key) and (not isinstance(child, str) or _SAFE_IDENTITY_RE.fullmatch(child) is None):
          findings.append(ScanFinding(child_path, ArtifactKind.DEVICE_IDENTITY, "device identity is not aliased"))
        if _SESSION_KEY_RE.search(key) and (not isinstance(child, str) or _SAFE_SESSION_RE.fullmatch(child) is None):
          findings.append(ScanFinding(child_path, ArtifactKind.SESSION_ID, "session identifier is not aliased"))
        findings.extend(_scan(child, child_path))
      return findings
    case list() as values:
      findings = []
      for index, child in enumerate(values):
        findings.extend(_scan(child, f"{path}[{index}]"))
      return findings
    case str() as text:
      return _scan_string(text, path)
    case float() as number:
      if not math.isfinite(number):
        return [ScanFinding(path, ArtifactKind.MALFORMED_INPUT, "non-finite number")]
      return []
    case None | bool() | int():
      return []
    case unreachable:
      assert_never(unreachable)


def scan_capture(value: JsonValue) -> tuple[ScanFinding, ...]:
  return tuple(_scan(value, "$"))


def assert_sanitized(value: JsonValue) -> None:
  findings = scan_capture(value)
  if findings:
    raise SanitizationRejected(findings)
