#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///

# ─── How to run ───
# 1. Install uv (if not installed):
#      curl -LsSf https://astral.sh/uv/install.sh | sh
# 2. Run directly (no venv, no pip install needed):
#      uv run sanitize.py SOURCE.json OUTPUT.json
# 3. Or make executable and run:
#      chmod +x sanitize.py && ./sanitize.py SOURCE.json OUTPUT.json
# ─────────────────

from __future__ import annotations

import json
import math
import re
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path
from typing import Final, assert_never

if __package__:
  from tools.naver_map_patch.privacy_scan import (
    ArtifactKind,
    DeviceIdentity,
    JsonScalar,
    JsonValue,
    SanitizationRejected,
    ScanFinding,
    _SECRET_KEY_RE,
    _SESSION_KEY_RE,
    _contains_encoded_apk,
    _contains_ip,
    _coordinate_pair,
    _is_identity_key,
    _number,
    assert_sanitized,
    scan_bytes,
    scan_capture,
  )
else:
  from privacy_scan import (
    ArtifactKind,
    DeviceIdentity,
    JsonScalar,
    JsonValue,
    SanitizationRejected,
    ScanFinding,
    _SECRET_KEY_RE,
    _SESSION_KEY_RE,
    _contains_encoded_apk,
    _contains_ip,
    _coordinate_pair,
    _is_identity_key,
    _number,
    assert_sanitized,
    scan_bytes,
    scan_capture,
  )

__all__ = (
  "ArtifactKind",
  "JsonScalar",
  "JsonValue",
  "SanitizationRejected",
  "ScanFinding",
  "assert_sanitized",
  "sanitize_capture",
  "sanitize_file",
  "sanitize_json_text",
  "scan_bytes",
  "scan_capture",
)

_TEXT_KEY_RE: Final = re.compile(r"(?:address|destination|name|place|road|text)", re.IGNORECASE)


class _SanitizerContext:
  __slots__ = ("coordinate_origin", "identity_aliases", "session_aliases", "text_aliases")

  coordinate_origin: tuple[float, float] | None
  identity_aliases: dict[DeviceIdentity, str]
  session_aliases: dict[str, str]
  text_aliases: dict[str, str]

  def __init__(self, coordinate_origin: tuple[float, float] | None) -> None:
    self.coordinate_origin = coordinate_origin
    self.identity_aliases = {}
    self.session_aliases = {}
    self.text_aliases = {}

  def identity_alias(self, value: DeviceIdentity) -> str:
    if value not in self.identity_aliases:
      self.identity_aliases[value] = f"fixture-identity-{len(self.identity_aliases) + 1:04d}"
    return self.identity_aliases[value]

  def session_alias(self, value: str) -> str:
    if value not in self.session_aliases:
      self.session_aliases[value] = f"fixture-session-{len(self.session_aliases) + 1:04d}"
    return self.session_aliases[value]

  def text_alias(self, value: str) -> str:
    if value not in self.text_aliases:
      self.text_aliases[value] = f"guidance-redacted-{len(self.text_aliases) + 1:04d}"
    return self.text_aliases[value]


def _first_coordinate(value: JsonValue) -> tuple[float, float] | None:
  match value:
    case dict() as mapping:
      pair = _coordinate_pair(mapping)
      if pair is not None:
        latitude = _number(mapping[pair[0]])
        longitude = _number(mapping[pair[1]])
        if latitude is not None and longitude is not None:
          return latitude, longitude
      for child in mapping.values():
        coordinate = _first_coordinate(child)
        if coordinate is not None:
          return coordinate
      return None
    case list() as values:
      for child in values:
        coordinate = _first_coordinate(child)
        if coordinate is not None:
          return coordinate
      return None
    case None | bool() | int() | float() | str():
      return None
    case unreachable:
      assert_never(unreachable)


def _relative_coordinate(value: float, origin: float) -> float:
  relative = round(value - origin, 4)
  return 0.0 if relative == 0.0 else relative


def _sanitize(value: JsonValue, context: _SanitizerContext, key: str | None = None) -> JsonValue:
  match value:
    case dict() as mapping:
      sanitized: dict[str, JsonValue] = {}
      pair = _coordinate_pair(mapping)
      coordinate_keys = set(pair or ())
      for child_key, child in mapping.items():
        if _SECRET_KEY_RE.search(child_key):
          continue
        if _is_identity_key(child_key):
          identity = DeviceIdentity(json.dumps(child, ensure_ascii=True, sort_keys=True))
          sanitized[child_key] = context.identity_alias(identity)
        elif pair is not None and child_key in coordinate_keys and context.coordinate_origin is not None:
          number = _number(child)
          origin = context.coordinate_origin[0 if child_key == pair[0] else 1]
          sanitized[child_key] = child if number is None else _relative_coordinate(number, origin)
        else:
          sanitized[child_key] = _sanitize(child, context, child_key)
      return sanitized
    case list() as values:
      return [_sanitize(child, context, key) for child in values]
    case str() as text:
      if _contains_encoded_apk(text):
        finding = ScanFinding(path=key or "$", kind=ArtifactKind.APK_BYTES, detail="archive or dex material")
        raise SanitizationRejected((finding,))
      if key is not None and _SESSION_KEY_RE.search(key):
        return context.session_alias(text)
      if (key is not None and "ip" in key.casefold()) or _contains_ip(text):
        return "peer-redacted"
      if key is not None and _TEXT_KEY_RE.search(key):
        return context.text_alias(text)
      return text
    case float() as number:
      if not math.isfinite(number):
        finding = ScanFinding(path=key or "$", kind=ArtifactKind.MALFORMED_INPUT, detail="non-finite number")
        raise SanitizationRejected((finding,))
      return number
    case None | bool() | int():
      return value
    case unreachable:
      assert_never(unreachable)


def sanitize_capture(value: JsonValue) -> JsonValue:
  context = _SanitizerContext(_first_coordinate(value))
  sanitized = _sanitize(value, context)
  assert_sanitized(sanitized)
  return sanitized


def _reject_constant(value: str) -> JsonValue:
  finding = ScanFinding(path="$", kind=ArtifactKind.MALFORMED_INPUT, detail=f"unsupported constant {value}")
  raise SanitizationRejected((finding,))


def sanitize_json_text(text: str) -> str:
  try:
    value: JsonValue = json.loads(text, parse_constant=_reject_constant)
  except json.JSONDecodeError as error:
    finding = ScanFinding(path="$", kind=ArtifactKind.MALFORMED_INPUT, detail=f"invalid JSON at offset {error.pos}")
    raise SanitizationRejected((finding,)) from error
  return json.dumps(sanitize_capture(value), ensure_ascii=True, indent=2, sort_keys=True) + "\n"


def sanitize_file(source: Path, destination: Path) -> None:
  destination.unlink(missing_ok=True)
  data = source.read_bytes()
  findings = scan_bytes(data)
  if findings:
    raise SanitizationRejected(findings)
  try:
    text = data.decode("utf-8")
  except UnicodeDecodeError as error:
    finding = ScanFinding(path="$", kind=ArtifactKind.MALFORMED_INPUT, detail="input is not UTF-8 JSON")
    raise SanitizationRejected((finding,)) from error
  sanitized_text = sanitize_json_text(text)
  temporary_path: Path | None = None
  try:
    with tempfile.NamedTemporaryFile(
      mode="w", encoding="utf-8", dir=destination.parent, prefix=f".{destination.name}.", suffix=".tmp", delete=False
    ) as temporary:
      temporary_path = Path(temporary.name)
      temporary.write(sanitized_text)
    temporary_path.replace(destination)
  except OSError:
    if temporary_path is not None:
      temporary_path.unlink(missing_ok=True)
    raise


def main(argv: Sequence[str] | None = None) -> int:
  arguments = tuple(sys.argv[1:] if argv is None else argv)
  if len(arguments) != 2:
    print("usage: sanitize.py SOURCE.json OUTPUT.json", file=sys.stderr)
    return 2
  try:
    sanitize_file(Path(arguments[0]), Path(arguments[1]))
  except (OSError, SanitizationRejected) as error:
    print(f"rejected: {error}", file=sys.stderr)
    return 2
  print("sanitized")
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
