#!/usr/bin/env python3
"""Loopback-only receiver for bounded Naver diagnostic observations.

Raw frames are intentionally written only to an operator-selected path outside
the repository.  The shareable bundle contains sanitized JSONL plus a summary
and manifest; it never embeds or copies the raw capture.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable
import copy
import json
from pathlib import Path
import re
import socket
import socketserver
import sys
import threading
import time
from typing import Any

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
if str(REPOSITORY_ROOT) not in sys.path:
  sys.path.insert(0, str(REPOSITORY_ROOT))

from tools.naver_map_patch.privacy_scan import SanitizationRejected, assert_sanitized


CHANNELS = (
  "status",
  "tbt_current",
  "tbt_next",
  "safety",
  "route",
  "lane",
)
SCHEMA = "naver.diagnostic.v1"
DEFAULT_MAX_FRAME_BYTES = 4096
MAX_STRING_CHARS = 160
DISCOVERY_SERVER_PORT = 7706
DISCOVERY_CLIENT_PORT = 7705
DISCOVERY_REQUEST_TYPE = "carrot.navigation.discover"
DISCOVERY_RESPONSE_TYPE = "carrot.navigation.discover.response"
LOOPBACK_HOST = "127.0.0.1"
_FRAME_KEYS = frozenset(("schema", "channel", "sequence", "monotonic_ns", "dropped", "observation"))
_NUMERIC_BUCKETS = frozenset((
  "nan", "negative_infinity", "positive_infinity", "zero", "negative_lt_1", "positive_lt_1",
  "negative_1_to_10", "positive_1_to_10", "negative_10_to_100", "positive_10_to_100",
  "negative_100_to_1000", "positive_100_to_1000", "negative_gte_1000", "positive_gte_1000",
))
_JAVA_LONG_MIN = -(2**63)
_JAVA_LONG_MAX = 2**63 - 1
_JAVA_IDENTIFIER_RE = re.compile(r"[A-Za-z_$][A-Za-z0-9_$]{0,159}\Z")
_SENSITIVE_ACCESSOR_TOKENS = (
  "latitude", "longitude", "lat", "lon", "coord", "place", "account", "device", "road",
  "routepoint", "geometry", "token", "secret", "exception", "message", "serial",
  "address", "home",
)

_SENSITIVE_KEYS = (
  "latitude",
  "longitude",
  "lat",
  "lon",
  "coordinate",
  "coord",
  "placeid",
  "place_id",
  "account",
  "device",
  "roadname",
  "road_name",
  "routegeometry",
  "route_geometry",
  "routepoints",
  "route_points",
  "exception",
  "message",
  "token",
  "secret",
)
_SAFE_TEXT_KEYS = {
  "schema",
  "channel",
  "descriptor",
  "name",
  "enum_name",
  "status",
  "numeric_bucket",
  "kind",
  "element_descriptors",
  "result",
  "root_descriptor",
}
_MAPPING_OUTCOME_KEYS = frozenset((
  "channel", "result", "root_descriptor", "input_count", "output_count",
  "revision", "item_present", "distance_valid", "frame_eligible",
))
_MAPPING_OUTCOME_RESULTS = {
  "route": frozenset((
    "route_ok", "route_root_descriptor", "route_accessor",
    "route_point_descriptor", "route_coordinate",
  )),
  "safety": frozenset((
    "safety_source_ok", "safety_source_descriptor", "safety_source_code",
    "safety_source_distance", "safety_source_accessor",
    "safety_ok", "safety_rejected",
  )),
}


class OfflineCaptureError(ValueError):
  """A diagnostic envelope failed the shared fail-closed contract."""


class DiagnosticFrameParser:
  """Incrementally parses newline-delimited JSON without cross-frame failure."""

  def __init__(self, max_frame_bytes: int = DEFAULT_MAX_FRAME_BYTES):
    if max_frame_bytes <= 0:
      raise ValueError("max_frame_bytes must be positive")
    self._max_frame_bytes = max_frame_bytes
    self._buffer = bytearray()
    self._discard_until_newline = False
    self.rejected_frames = 0

  def feed(self, chunk: bytes) -> list[dict[str, Any]]:
    frames: list[dict[str, Any]] = []
    for byte in chunk:
      if self._discard_until_newline:
        if byte == 0x0A:
          self._discard_until_newline = False
        continue
      if byte == 0x0A:
        frame = self._finish_frame()
        if frame is not None:
          frames.append(frame)
        continue
      if len(self._buffer) >= self._max_frame_bytes:
        self._buffer.clear()
        self._discard_until_newline = True
        self.rejected_frames += 1
        continue
      self._buffer.append(byte)
    return frames

  def _finish_frame(self) -> dict[str, Any] | None:
    payload = bytes(self._buffer)
    self._buffer.clear()
    if not payload.strip():
      return None
    try:
      return decode_and_validate_frame(payload)
    except OfflineCaptureError:
      self.rejected_frames += 1
      return None


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
  result: dict[str, object] = {}
  for key, value in pairs:
    if key in result:
      raise ValueError("duplicate JSON field")
    result[key] = value
  return result


def _reject_nonstandard_number(value: str) -> object:
  raise ValueError(f"non-standard JSON number: {value}")


def _has_sensitive_key(value: object) -> bool:
  if type(value) is dict:
    return any(
      _sensitive_key(key) or _has_sensitive_key(child)
      for key, child in value.items()
    )
  if type(value) is list:
    return any(_has_sensitive_key(child) for child in value)
  return False


def _has_sensitive_accessor(value: object) -> bool:
  if type(value) is dict:
    name = value.get("name")
    if (
      type(name) is str
      and "descriptor" in value
      and any(token in name.lower() for token in _SENSITIVE_ACCESSOR_TOKENS)
    ):
      return True
    return any(_has_sensitive_accessor(child) for child in value.values())
  if type(value) is list:
    return any(_has_sensitive_accessor(child) for child in value)
  return False


def decode_and_validate_frame(encoded: str | bytes) -> dict[str, object]:
  """Decode one exact privacy-safe diagnostic envelope.

  The byte bound applies to the original UTF-8 representation. The decoded
  value is scanned as received; this function never redacts or otherwise
  transforms rejected input into an accepted frame.
  """
  if type(encoded) is bytes:
    payload = encoded
    if len(payload) > DEFAULT_MAX_FRAME_BYTES:
      raise OfflineCaptureError("diagnostic frame exceeds 4096 UTF-8 bytes")
    try:
      text = payload.decode("utf-8")
    except UnicodeDecodeError as error:
      raise OfflineCaptureError("diagnostic frame is not valid UTF-8") from error
  elif type(encoded) is str:
    text = encoded
    try:
      payload = text.encode("utf-8")
    except UnicodeEncodeError as error:
      raise OfflineCaptureError("diagnostic frame is not valid UTF-8") from error
    if len(payload) > DEFAULT_MAX_FRAME_BYTES:
      raise OfflineCaptureError("diagnostic frame exceeds 4096 UTF-8 bytes")
  else:
    raise OfflineCaptureError("diagnostic frame must be str or bytes")

  try:
    decoded = json.loads(
      text,
      object_pairs_hook=_reject_duplicate_keys,
      parse_constant=_reject_nonstandard_number,
    )
  except ValueError as error:
    detail = "duplicate field" if "duplicate JSON field" in str(error) else "invalid JSON"
    raise OfflineCaptureError(f"diagnostic frame {detail}") from error
  if type(decoded) is not dict:
    raise OfflineCaptureError("diagnostic frame schema must be an object")
  if frozenset(decoded) != _FRAME_KEYS:
    if _has_sensitive_key(decoded):
      raise OfflineCaptureError("diagnostic frame privacy violation")
    raise OfflineCaptureError("diagnostic frame schema fields are not exact")
  try:
    assert_sanitized(decoded)
  except SanitizationRejected as error:
    raise OfflineCaptureError("diagnostic frame privacy violation") from error
  if _has_sensitive_key(decoded) or _has_sensitive_accessor(decoded):
    raise OfflineCaptureError("diagnostic frame privacy violation")
  if not _is_complete_frame(decoded):
    raise OfflineCaptureError("diagnostic frame schema or observation grammar is invalid")
  return decoded


def parse_discovery_request(data: bytes) -> dict[str, object]:
  if type(data) is not bytes or len(data) > 256:
    raise ValueError("invalid discovery request")
  try:
    value = json.loads(data.decode("utf-8"), object_pairs_hook=_reject_duplicate_keys)
  except (UnicodeDecodeError, ValueError, json.JSONDecodeError) as error:
    raise ValueError("invalid discovery request") from error
  if (
    type(value) is not dict
    or frozenset(value) != {"type", "source", "schema_version"}
    or type(value.get("type")) is not str
    or value["type"] != DISCOVERY_REQUEST_TYPE
    or type(value.get("source")) is not str
    or value["source"] != "naver"
    or type(value.get("schema_version")) is not int
    or value["schema_version"] != 1
  ):
    raise ValueError("invalid discovery request")
  return value


def build_discovery_response() -> bytes:
  return json.dumps(
    {
      "type": DISCOVERY_RESPONSE_TYPE,
      "server": LOOPBACK_HOST,
      "port": 7712,
      "schema": 1,
      "schema_version": 1,
      "lease_ms": 2000,
    }, separators=(",", ":"), ensure_ascii=True,
  ).encode("ascii")


def _sensitive_key(key: str) -> bool:
  lowered = key.lower()
  return any(token in lowered for token in _SENSITIVE_KEYS)


def _sanitize(value: Any, key: str = "") -> Any:
  if _sensitive_key(key):
    return "[redacted]"
  if isinstance(value, dict):
    return {
      str(child_key): _sanitize(child_value, str(child_key))
      for child_key, child_value in value.items()
    }
  if isinstance(value, list):
    if key == "accessors":
      return [_sanitize(child, key) for child in value[:16]]
    if key == "element_descriptors":
      return [_sanitize(child, key) for child in value[:4]]
    return []
  if isinstance(value, str):
    if key.lower() not in _SAFE_TEXT_KEYS:
      return f"[redacted-string:{min(len(value), MAX_STRING_CHARS)}]"
    return value
  if isinstance(value, (bool, int)) or value is None:
    return value
  if isinstance(value, float):
    # Top-level timing/counter fields are integral.  Floats in observations
    # could be precise coordinates or geometry and are therefore bucketed.
    if value == 0.0:
      return "zero"
    magnitude = abs(value)
    if magnitude < 1.0:
      band = "lt_1"
    elif magnitude < 10.0:
      band = "1_to_10"
    elif magnitude < 100.0:
      band = "10_to_100"
    elif magnitude < 1000.0:
      band = "100_to_1000"
    else:
      band = "gte_1000"
    return ("negative_" if value < 0 else "positive_") + band
  return "[unsupported]"


def sanitize_frame(frame: dict[str, Any]) -> dict[str, Any]:
  sanitized = _sanitize(copy.deepcopy(frame))
  if not isinstance(sanitized, dict):
    raise ValueError("diagnostic frame must be an object")
  return sanitized


class CaptureAccumulator:
  def __init__(self):
    self._counts = {channel: 0 for channel in CHANNELS}
    self._last_sequence: int | None = None
    self._last_monotonic_ns: int | None = None
    self._monotonic = True
    self._invalid_frames = 0
    self._reported_drops = 0

  def observe(self, frame: dict[str, Any]) -> bool:
    if not _is_complete_frame(frame):
      self._invalid_frames += 1
      return False

    channel = frame["channel"]
    sequence = frame["sequence"]
    monotonic_ns = frame["monotonic_ns"]
    dropped = frame["dropped"]

    if self._last_sequence is not None and sequence <= self._last_sequence:
      self._monotonic = False
    if (
      self._last_monotonic_ns is not None
      and monotonic_ns <= self._last_monotonic_ns
    ):
      self._monotonic = False
    self._last_sequence = sequence
    self._last_monotonic_ns = monotonic_ns
    self._reported_drops = max(self._reported_drops, dropped)
    self._counts[channel] += 1
    return True

  def summary(self) -> dict[str, Any]:
    missing = [
      channel for channel in CHANNELS if self._counts[channel] == 0
    ]
    return {
      "schema": SCHEMA,
      "complete": not missing,
      "missing_channels": missing,
      "counts": dict(self._counts),
      "monotonic": self._monotonic,
      "invalid_frames": self._invalid_frames,
      "reported_drops": self._reported_drops,
    }


def _is_complete_frame(frame: dict[str, Any]) -> bool:
  try:
    if frozenset(frame) != _FRAME_KEYS or frame.get("schema") != SCHEMA:
      return False
    channel = frame["channel"]
    sequence = frame["sequence"]
    monotonic_ns = frame["monotonic_ns"]
    dropped = frame["dropped"]
  except (KeyError, TypeError):
    return False
  return (
    channel in CHANNELS
    and _is_java_long(sequence) and sequence >= 1
    and _is_java_long(monotonic_ns)
    and _is_java_long(dropped) and dropped >= 0
    and (
      _is_mapping_outcome_observation(frame.get("observation"), channel)
      or _is_observation(frame.get("observation"))
    )
  )


def _is_mapping_outcome_observation(value: object, frame_channel: object) -> bool:
  if type(value) is not dict or frozenset(value) != _MAPPING_OUTCOME_KEYS:
    return False
  channel = value["channel"]
  return (
    channel == frame_channel
    and channel in _MAPPING_OUTCOME_RESULTS
    and value["result"] in _MAPPING_OUTCOME_RESULTS[channel]
    and _is_descriptor(value["root_descriptor"])
    and _is_count(value["input_count"], 2_147_483_647)
    and _is_count(value["output_count"], 4096)
    and _is_java_long(value["revision"])
    and value["revision"] >= 0
    and type(value["item_present"]) is bool
    and type(value["distance_valid"]) is bool
    and type(value["frame_eligible"]) is bool
  )


def _is_observation(value: object, depth: int = 0) -> bool:
  if type(value) is not dict or depth > 4 or not value:
    return False
  observation = value
  if observation == {"kind": "null"}:
    return True
  if observation == {"status": "accessor_error"}:
    return depth > 0
  if observation == {"truncated": True}:
    return depth == 0
  if "descriptor" not in observation or not _is_descriptor(observation["descriptor"]):
    return False

  descriptor = {"descriptor"}
  if frozenset(observation) == descriptor | {"enum_name"}:
    return _is_java_identifier(observation["enum_name"])
  if frozenset(observation) == descriptor | {"enum_name", "accessors"}:
    return (
      _is_java_identifier(observation["enum_name"])
      and _is_accessor_list(observation["accessors"], depth)
    )
  if frozenset(observation) == descriptor | {"numeric_bucket"}:
    return observation["numeric_bucket"] in _NUMERIC_BUCKETS
  if frozenset(observation) == descriptor | {"boolean"}:
    return type(observation["boolean"]) is bool
  if frozenset(observation) in (descriptor | {"string_length"}, descriptor | {"string_length", "truncated"}):
    return (
      _is_count(observation["string_length"], MAX_STRING_CHARS)
      and ("truncated" not in observation or observation["truncated"] is True)
      and ("truncated" not in observation or observation["string_length"] == MAX_STRING_CHARS)
    )
  if frozenset(observation) == descriptor | {"depth_limited"}:
    return observation["depth_limited"] is True
  if frozenset(observation) == descriptor | {"truncated"}:
    return depth == 0 and observation["truncated"] is True
  if frozenset(observation) == descriptor | {"accessors"}:
    return _is_accessor_list(observation["accessors"], depth)
  collection_fields = descriptor | {"collection_size", "sampled_items", "element_descriptors"}
  if frozenset(observation) != collection_fields:
    return False
  if not _is_count(observation["collection_size"], 2_147_483_647):
    return False
  if not _is_count(observation["sampled_items"], 4):
    return False
  if observation["sampled_items"] > observation["collection_size"]:
    return False
  if observation["sampled_items"] != min(observation["collection_size"], 4):
    return False
  elements = observation["element_descriptors"]
  return (
    type(elements) is list
    and len(elements) == observation["sampled_items"]
    and all(_is_descriptor(element) for element in elements)
  )


def _is_accessor(value: object, depth: int) -> bool:
  if type(value) is not dict or not {"name", "descriptor"} <= set(value):
    return False
  if not _is_accessor_name(value["name"]) or not _is_method_descriptor(value["descriptor"]):
    return False
  if frozenset(value) == {"name", "descriptor", "status"}:
    return value["status"] == "not_allowlisted"
  return frozenset(value) == {"name", "descriptor", "value"} and _is_observation(value["value"], depth)


def _is_accessor_list(value: object, depth: int) -> bool:
  return (
    depth < 4
    and type(value) is list
    and len(value) <= 16
    and all(_is_accessor(accessor, depth + 1) for accessor in value)
    and _accessors_are_sorted_and_unique(value)
  )


def _is_accessor_name(value: object) -> bool:
  return (
    _is_java_identifier(value)
    and value not in {"toString", "hashCode"}
    and not any(token in value.lower() for token in _SENSITIVE_ACCESSOR_TOKENS)
  )


def _accessors_are_sorted_and_unique(accessors: list[object]) -> bool:
  pairs = [(accessor["name"], accessor["descriptor"]) for accessor in accessors]
  return pairs == sorted(pairs) and len(pairs) == len(set(pairs))


def _is_count(value: object, maximum: int) -> bool:
  return type(value) is int and 0 <= value <= maximum


def _is_java_long(value: object) -> bool:
  return type(value) is int and _JAVA_LONG_MIN <= value <= _JAVA_LONG_MAX


def _is_short_text(value: object) -> bool:
  return type(value) is str and 0 < len(value) <= 512


def _is_java_identifier(value: object) -> bool:
  return type(value) is str and _JAVA_IDENTIFIER_RE.fullmatch(value) is not None


def _is_descriptor(value: object) -> bool:
  if not _is_short_text(value):
    return False
  return value.startswith(("L", "[")) and _consume_descriptor(value, 0, allow_void=False) == len(value)


def _is_method_descriptor(value: object) -> bool:
  if not _is_short_text(value) or not value.startswith("()"):
    return False
  next_index = _consume_descriptor(value, 2, allow_void=False)
  return next_index == len(value)


def _consume_descriptor(value: str, index: int, *, allow_void: bool) -> int | None:
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

def _resolved(path: Path) -> Path:
  return path.expanduser().resolve(strict=False)


def _inside(path: Path, directory: Path) -> bool:
  try:
    path.relative_to(directory)
    return True
  except ValueError:
    return False


def validate_capture_paths(
    raw_path: Path, bundle_path: Path, repository_root: Path
) -> None:
  raw = _resolved(raw_path)
  bundle = _resolved(bundle_path)
  repository = _resolved(repository_root)
  if _inside(raw, repository) or _inside(bundle, repository):
    raise ValueError("capture output must be outside the repository")
  if raw == bundle or _inside(raw, bundle):
    raise ValueError("raw capture and sanitized bundle must be separate")


class CaptureBundle:
  def __init__(
      self, raw_path: Path, bundle_path: Path, repository_root: Path
  ):
    validate_capture_paths(raw_path, bundle_path, repository_root)
    self.raw_path = _resolved(raw_path)
    self.bundle_path = _resolved(bundle_path)
    self.raw_path.parent.mkdir(parents=True, exist_ok=True)
    self.bundle_path.mkdir(parents=True, exist_ok=True)
    self.sanitized_path = self.bundle_path / "capture.sanitized.jsonl"
    self._accumulator = CaptureAccumulator()
    self._lock = threading.Lock()

  def record(self, frame: dict[str, Any]) -> bool:
    try:
      validated = decode_and_validate_frame(json.dumps(
        frame, ensure_ascii=True, separators=(",", ":"), sort_keys=True
      ))
    except (OfflineCaptureError, TypeError, ValueError):
      return False
    raw_line = json.dumps(
      validated, ensure_ascii=True, separators=(",", ":"), sort_keys=True
    )
    sanitized = sanitize_frame(validated)
    try:
      assert_sanitized(sanitized)
    except SanitizationRejected:
      return False
    if not _is_complete_frame(sanitized):
      return False
    sanitized_line = json.dumps(
      sanitized, ensure_ascii=True, separators=(",", ":"), sort_keys=True
    )
    with self._lock:
      with self.raw_path.open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(raw_line + "\n")
      accepted = self._accumulator.observe(sanitized)
      if accepted:
        with self.sanitized_path.open(
            "a", encoding="utf-8", newline="\n"
        ) as stream:
          stream.write(sanitized_line + "\n")
    return accepted

  def finalize(self, parser_rejections: int = 0) -> dict[str, Any]:
    with self._lock:
      summary = self._accumulator.summary()
      summary["parser_rejections"] = parser_rejections
      manifest = {
        "schema": SCHEMA,
        "capture_kind": "sanitized_diagnostic_bundle",
        "raw_capture_included": False,
        "receiver": {"host": "127.0.0.1", "port": 7712},
        "finalized_monotonic_ns": time.monotonic_ns(),
        "summary": summary,
      }
      _write_json(self.bundle_path / "summary.json", summary)
      _write_json(self.bundle_path / "manifest.json", manifest)
      return manifest


def _write_json(path: Path, value: dict[str, Any]) -> None:
  path.write_text(
    json.dumps(value, indent=2, ensure_ascii=True, sort_keys=True) + "\n",
    encoding="utf-8",
  )


class _LoopbackServer(socketserver.ThreadingTCPServer):
  allow_reuse_address = True
  daemon_threads = False

  def __init__(
      self,
      server_address: tuple[str, int],
      handler_class: type[socketserver.BaseRequestHandler],
  ):
    self._active_requests: set[socket.socket] = set()
    self._active_requests_lock = threading.Lock()
    self._closing = False
    super().__init__(server_address, handler_class)

  def process_request(
      self, request: socket.socket, client_address: tuple[str, int]
  ) -> None:
    with self._active_requests_lock:
      if self._closing:
        reject = True
      else:
        self._active_requests.add(request)
        reject = False
    if reject:
      self._close_request(request)
      return
    try:
      super().process_request(request, client_address)
    except Exception:
      with self._active_requests_lock:
        self._active_requests.discard(request)
      self._close_request(request)
      raise

  def process_request_thread(
      self, request: socket.socket, client_address: tuple[str, int]
  ) -> None:
    try:
      super().process_request_thread(request, client_address)
    finally:
      with self._active_requests_lock:
        self._active_requests.discard(request)

  def close_active_requests(self) -> None:
    with self._active_requests_lock:
      self._closing = True
      requests = tuple(self._active_requests)
    for request in requests:
      self._close_request(request)

  def is_closing(self) -> bool:
    with self._active_requests_lock:
      return self._closing

  def _close_request(self, request: socket.socket) -> None:
    try:
      request.shutdown(socket.SHUT_RDWR)
    except OSError:
      pass
    try:
      self.close_request(request)
    except OSError:
      pass


class _DiscoveryServer(socketserver.ThreadingUDPServer):
  allow_reuse_address = True
  daemon_threads = True


def _serve(
    bundle: CaptureBundle,
    duration_seconds: float,
    *,
    on_handler_started: Callable[[], None] | None = None,
    before_shutdown_snapshot: Callable[[], None] | None = None,
    server_address: tuple[str, int] = (LOOPBACK_HOST, 7712),
    discovery_server_address: tuple[str, int] = (
      LOOPBACK_HOST,
      DISCOVERY_SERVER_PORT,
    ),
    server_ready: Callable[[tuple[str, int]], None] | None = None,
) -> int:
  parsers: list[DiagnosticFrameParser] = []
  parsers_lock = threading.Lock()

  class Handler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
      if on_handler_started is not None:
        on_handler_started()
      if self.server.is_closing():
        return
      parser = DiagnosticFrameParser()
      with parsers_lock:
        parsers.append(parser)
      try:
        while True:
          chunk = self.request.recv(8192)
          if not chunk:
            return
          for frame in parser.feed(chunk):
            bundle.record(frame)
      except OSError:
        if not self.server.is_closing():
          raise

  class DiscoveryHandler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
      data, server = self.request
      try:
        parse_discovery_request(data)
      except ValueError:
        return
      server.sendto(
        build_discovery_response(),
        (self.client_address[0], DISCOVERY_CLIENT_PORT),
      )

  with _LoopbackServer(server_address, Handler) as server, _DiscoveryServer(
      discovery_server_address, DiscoveryHandler) as discovery:
    server.timeout = 0.1
    discovery.timeout = 0.1
    if server_ready is not None:
      server_ready(server.server_address)
    deadline = None if duration_seconds <= 0 else time.monotonic() + duration_seconds
    try:
      while deadline is None or time.monotonic() < deadline:
        server.handle_request()
        discovery.handle_request()
    except KeyboardInterrupt:
      pass
    finally:
      if before_shutdown_snapshot is not None:
        before_shutdown_snapshot()
      server.close_active_requests()
  return sum(parser.rejected_frames for parser in parsers)


def _parser() -> argparse.ArgumentParser:
  parser = argparse.ArgumentParser(
    description="Capture loopback-only Naver diagnostic observations."
  )
  parser.add_argument("--raw", required=True, type=Path)
  parser.add_argument("--bundle", required=True, type=Path)
  parser.add_argument(
    "--repository-root",
    type=Path,
    default=REPOSITORY_ROOT,
  )
  parser.add_argument(
    "--duration-seconds",
    type=float,
    default=0.0,
    help="0 waits until Ctrl-C.",
  )
  return parser


def main() -> int:
  args = _parser().parse_args()
  bundle = CaptureBundle(args.raw, args.bundle, args.repository_root)
  rejected = _serve(bundle, args.duration_seconds)
  manifest = bundle.finalize(rejected)
  print(json.dumps(manifest["summary"], sort_keys=True))
  return 0 if manifest["summary"]["complete"] else 2


if __name__ == "__main__":
  raise SystemExit(main())
