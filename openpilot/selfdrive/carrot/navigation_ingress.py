from __future__ import annotations

import hashlib
import ipaddress
import json
import socket
import threading
import time
from dataclasses import dataclass
from types import MappingProxyType
from typing import Callable
from uuid import uuid4

from openpilot.selfdrive.carrot.naver_navigation_protocol import (
  SCHEMA as NAVER_NAVIGATION_SCHEMA,
  NaverProtocolError,
  parse_naver_navigation_v1,
)
from openpilot.selfdrive.carrot.navigation_sources import NavigationLifecycle, NavigationSource


NAVI_TCP_MAX_FRAME_BYTES = 262_144
NAVI_TCP_MAX_CLIENTS = 4
NAVI_TCP_CLIENT_TIMEOUT_S = 10.0
NAVI_JSON_MAX_NESTING = 128
NAVI_SOCKET_RECV_BYTES = 64 * 1024

NAVER_DISCOVERY_CLIENT_PORT = 7705
NAVER_DISCOVERY_SERVER_PORT = 7706
NAVER_NAVIGATION_TCP_PORT = 7712
NAVER_DISCOVERY_LEASE_MS = 2000
NAVER_DISCOVERY_REQUEST_TYPE = "carrot.navigation.discover"
NAVER_DISCOVERY_RESPONSE_TYPE = "carrot.navigation.discover.response"

_DISCOVERY_REQUEST_KEYS = frozenset(("type", "source", "schema_version"))
_TERMINAL_LIFECYCLES = frozenset((NavigationLifecycle.STOPPED.value, NavigationLifecycle.ARRIVED.value))
_ERROR_MESSAGES = MappingProxyType({
  "ingress": "navigation ingress rejected the input",
  "empty_frame": "navigation frame is empty",
  "frame_too_large": "navigation frame exceeds the size limit",
  "unterminated_frame": "navigation frame is not newline terminated",
  "invalid_utf8": "navigation frame is not canonical UTF-8",
  "invalid_json": "navigation frame is not one complete JSON value",
  "duplicate_key": "navigation frame contains a duplicate object key",
  "non_finite_number": "navigation frame contains a non-finite number",
  "nesting_too_deep": "navigation frame nesting exceeds the limit",
  "non_object": "navigation frame root must be an object",
  "unsupported_schema": "navigation schema is unsupported on this transport",
  "invalid_navigation": "navigation envelope failed protocol validation",
  "invalid_discovery": "navigation discovery request is invalid",
  "invalid_local_ip": "navigation discovery server address is invalid",
  "invalid_peer": "navigation requester address is invalid",
  "server_busy": "navigation client limit reached",
  "listener_error": "navigation listener stopped",
  "client_handler_error": "navigation client handler stopped",
})


class NavigationIngressError(ValueError):
  def __init__(self, code: object, message: object = None):
    safe_code = code if type(code) is str and code in _ERROR_MESSAGES else "ingress"
    self.code = safe_code
    self.message = _ERROR_MESSAGES[safe_code]
    super().__init__(f"{self.code}: {self.message}")


class _NavigationTransportError(Exception):
  pass


def _fail(code: str) -> None:
  raise NavigationIngressError(code)


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict:
  result = {}
  for key, value in pairs:
    if key in result:
      _fail("duplicate_key")
    result[key] = value
  return result


def _reject_non_finite(_value: str) -> None:
  _fail("non_finite_number")


def _check_json_nesting(text: str) -> None:
  depth = 0
  in_string = False
  escaped = False
  for character in text:
    if in_string:
      if escaped:
        escaped = False
      elif character == "\\":
        escaped = True
      elif character == '"':
        in_string = False
      continue
    if character == '"':
      in_string = True
    elif character in "[{":
      depth += 1
      if depth > NAVI_JSON_MAX_NESTING:
        _fail("nesting_too_deep")
    elif character in "]}":
      depth = max(0, depth - 1)


def strict_json_loads(frame: bytes) -> dict:
  if type(frame) is not bytes:
    _fail("invalid_utf8")
  if len(frame) > NAVI_TCP_MAX_FRAME_BYTES:
    _fail("frame_too_large")
  if frame.startswith(b"\xef\xbb\xbf"):
    _fail("invalid_utf8")
  try:
    text = frame.decode("utf-8")
  except UnicodeDecodeError:
    _fail("invalid_utf8")
  if not text.strip():
    _fail("empty_frame")
  _check_json_nesting(text)
  try:
    value = json.loads(
      text,
      object_pairs_hook=_reject_duplicate_keys,
      parse_constant=_reject_non_finite,
    )
  except NavigationIngressError:
    raise
  except RecursionError:
    _fail("nesting_too_deep")
  except (TypeError, ValueError, json.JSONDecodeError):
    _fail("invalid_json")
  if type(value) is not dict:
    _fail("non_object")
  return value


def _validate_discovery_request(value: dict) -> dict:
  if (
    frozenset(value) != _DISCOVERY_REQUEST_KEYS
    or type(value.get("type")) is not str
    or value["type"] != NAVER_DISCOVERY_REQUEST_TYPE
    or type(value.get("source")) is not str
    or value["source"] != "naver"
    or type(value.get("schema_version")) is not int
    or value["schema_version"] != 1
  ):
    _fail("invalid_discovery")
  return value


def parse_naver_discovery_request(data: bytes) -> dict:
  try:
    return _validate_discovery_request(strict_json_loads(data))
  except NavigationIngressError as error:
    if error.code == "invalid_discovery":
      raise
    _fail("invalid_discovery")


def _usable_local_ipv4(local_ip: object) -> str:
  if type(local_ip) is not str:
    _fail("invalid_local_ip")
  try:
    address = ipaddress.ip_address(local_ip)
  except ValueError:
    _fail("invalid_local_ip")
  if (
    address.version != 4
    or address.is_unspecified
    or address.is_loopback
    or address.is_link_local
    or address.is_multicast
    or address.is_reserved
  ):
    _fail("invalid_local_ip")
  return str(address)


def build_naver_discovery_response(local_ip: object) -> bytes:
  response = {
    "type": NAVER_DISCOVERY_RESPONSE_TYPE,
    "server": _usable_local_ipv4(local_ip),
    "port": NAVER_NAVIGATION_TCP_PORT,
    "schema": 1,
    "schema_version": 1,
    "lease_ms": NAVER_DISCOVERY_LEASE_MS,
  }
  encoded = json.dumps(response, separators=(",", ":"), ensure_ascii=True).encode("ascii")
  if len(encoded) > 256:
    _fail("invalid_local_ip")
  return encoded


def _requester_ip(remote_addr: object) -> str:
  if (
    type(remote_addr) is not tuple
    or len(remote_addr) < 2
    or type(remote_addr[0]) is not str
    or type(remote_addr[1]) is not int
    or remote_addr[1] != NAVER_DISCOVERY_CLIENT_PORT
  ):
    _fail("invalid_peer")
  try:
    address = ipaddress.ip_address(remote_addr[0])
  except ValueError:
    _fail("invalid_peer")
  if (
    address.version != 4
    or address.is_unspecified
    or address.is_loopback
    or address.is_link_local
    or address.is_multicast
    or address.is_reserved
  ):
    _fail("invalid_peer")
  return str(address)


def handle_navigation_udp_datagram(sock, data: bytes, remote_addr: object, local_ip: object,
                                   legacy_update: Callable[[dict], object]) -> bool:
  value = strict_json_loads(data)
  message_type = value.get("type")
  discovery_shaped = (
    frozenset(value) == _DISCOVERY_REQUEST_KEYS
    or (type(message_type) is str and message_type.startswith(NAVER_DISCOVERY_REQUEST_TYPE))
  )
  if discovery_shaped:
    _validate_discovery_request(value)
    sock.sendto(
      build_naver_discovery_response(local_ip),
      (_requester_ip(remote_addr), NAVER_DISCOVERY_CLIENT_PORT),
    )
    return True
  legacy_update(value)
  return False


class NavigationPeerState:
  def __init__(self):
    self._revision = 0
    self._tcp: dict[object, tuple[object, int]] = {}
    self._fallback: dict[str, tuple[object, int]] = {}

  def _next_revision(self) -> int:
    self._revision += 1
    return self._revision

  def _selected(self):
    candidates = self._tcp if self._tcp else self._fallback
    if not candidates:
      return None
    return max(candidates.values(), key=lambda value: value[1])[0]

  @property
  def selected(self):
    return self._selected()

  def set_tcp(self, token: object, peer: object):
    self._tcp[token] = (peer, self._next_revision())
    return self._selected()

  def clear_tcp(self, token: object):
    self._tcp.pop(token, None)
    return self._selected()

  def set_fallback(self, transport: str, peer: object):
    if transport not in ("udp", "http"):
      raise ValueError("fallback transport must be udp or http")
    self._fallback[transport] = (peer, self._next_revision())
    return self._selected()

  def clear_fallback(self, transport: str):
    if transport not in ("udp", "http"):
      raise ValueError("fallback transport must be udp or http")
    self._fallback.pop(transport, None)
    return self._selected()

  def clear(self):
    self._tcp.clear()
    self._fallback.clear()
    return None


def build_legacy_http_session_id(peer: object, tmap_version: object) -> str:
  peer_ip = peer[0] if isinstance(peer, tuple) and peer and isinstance(peer[0], str) else "unknown"
  version = tmap_version if isinstance(tmap_version, str) else ""
  seed = json.dumps((peer_ip, version), separators=(",", ":")).encode("utf-8")
  return f"tmap-http-{hashlib.blake2s(seed, digest_size=8).hexdigest()}"


def bind_tmap_legacy_frame(obj: dict, session_id: str, received_mono_s: float) -> dict:
  bound = dict(obj)
  rgdata = obj.get("rgdata")
  if isinstance(rgdata, dict):
    bound_rgdata = dict(rgdata)
    bound_rgdata["_navigation_source"] = NavigationSource.TMAP_LEGACY.value
    bound_rgdata["_navigation_session_id"] = session_id
    bound_rgdata["_navigation_received_mono_s"] = received_mono_s
    bound["rgdata"] = bound_rgdata
  return bound


@dataclass(frozen=True, slots=True)
class _NavigationDispatchResult:
  source: NavigationSource | None
  session_id: str | None
  accepted: bool
  terminal: bool = False


class NavigationIngress:
  def __init__(self, *, carrot_serv, legacy_dispatch: Callable[[dict, object, float, str], object],
               record_transport_loss: Callable[[NavigationSource, str, float], object],
               monotonic: Callable[[], float] = time.monotonic,
               reject: Callable[[str], object] | None = None,
               client_timeout_s: float = NAVI_TCP_CLIENT_TIMEOUT_S,
               legacy_session_factory: Callable[[], str] | None = None):
    self._carrot_serv = carrot_serv
    self._legacy_dispatch = legacy_dispatch
    self._record_transport_loss = record_transport_loss
    self._monotonic = monotonic
    self._reject = reject
    self._client_timeout_s = client_timeout_s
    self._legacy_session_factory = legacy_session_factory or self._default_legacy_session_id

  @staticmethod
  def _default_legacy_session_id() -> str:
    return f"tmap-tcp-{uuid4()}"

  def _notify_rejection(self, code: str) -> None:
    safe_code = code if code in _ERROR_MESSAGES else "ingress"
    if self._reject is not None:
      try:
        self._reject(safe_code)
      except Exception:
        pass

  def _dispatch_frame(self, obj: dict, peer: object, received_mono_s: float,
                      *, legacy_session_id: str | None = None) -> _NavigationDispatchResult:
    if type(obj) is not dict:
      _fail("non_object")
    if "schema" in obj:
      if type(obj["schema"]) is not str or obj["schema"] != NAVER_NAVIGATION_SCHEMA:
        _fail("unsupported_schema")
      snapshot = parse_naver_navigation_v1(obj, received_mono_s)
      accepted = bool(self._carrot_serv.accept_navigation_snapshot(snapshot))
      return _NavigationDispatchResult(
        source=snapshot.source,
        session_id=snapshot.session_id,
        accepted=accepted,
        terminal=snapshot.lifecycle.value in _TERMINAL_LIFECYCLES,
      )

    session_id = legacy_session_id or self._legacy_session_factory()
    accepted = self._legacy_dispatch(obj, peer, received_mono_s, session_id)
    if type(obj.get("rgdata")) is dict:
      return _NavigationDispatchResult(
        source=NavigationSource.TMAP_LEGACY,
        session_id=session_id,
        accepted=accepted is not False,
      )
    return _NavigationDispatchResult(source=None, session_id=None, accepted=accepted is not False)

  def dispatch_frame(self, obj: dict, peer: object, received_mono_s: float,
                     *, legacy_session_id: str | None = None) -> NavigationSource | None:
    result = self._dispatch_frame(
      obj, peer, received_mono_s, legacy_session_id=legacy_session_id,
    )
    return result.source if result.accepted else None

  @staticmethod
  def _frames(conn):
    buffer = bytearray()
    while True:
      remaining = (NAVI_TCP_MAX_FRAME_BYTES + 1) - len(buffer)
      if remaining <= 0:
        _fail("frame_too_large")
      try:
        data = conn.recv(min(NAVI_SOCKET_RECV_BYTES, remaining))
      except (TimeoutError, ConnectionResetError, OSError) as error:
        raise _NavigationTransportError from error
      if not data:
        if buffer:
          _fail("unterminated_frame")
        return
      buffer.extend(data)

      while True:
        newline = buffer.find(b"\n")
        if newline < 0:
          break
        if newline > NAVI_TCP_MAX_FRAME_BYTES:
          _fail("frame_too_large")
        frame = bytes(buffer[:newline])
        del buffer[:newline + 1]
        yield frame

      if len(buffer) > NAVI_TCP_MAX_FRAME_BYTES:
        _fail("frame_too_large")

  def serve_client(self, conn, addr: object) -> None:
    association: tuple[NavigationSource, str] | None = None
    transport_lost = False
    legacy_session_id = self._legacy_session_factory()
    try:
      conn.settimeout(self._client_timeout_s)
      try:
        conn.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
      except OSError:
        pass
      for frame in self._frames(conn):
        obj = strict_json_loads(frame)
        received_mono_s = self._monotonic()
        result = self._dispatch_frame(
          obj,
          addr,
          received_mono_s,
          legacy_session_id=legacy_session_id,
        )
        if result.source is None or result.session_id is None:
          continue
        association = (result.source, result.session_id)
        if result.terminal:
          association = None
          return
      transport_lost = True
    except NavigationIngressError as error:
      self._notify_rejection(error.code)
    except NaverProtocolError:
      self._notify_rejection("invalid_navigation")
    except _NavigationTransportError:
      transport_lost = True
    except OSError:
      self._notify_rejection("client_handler_error")
    finally:
      if association is not None and transport_lost:
        try:
          self._record_transport_loss(association[0], association[1], self._monotonic())
        except Exception:
          pass
      try:
        conn.close()
      except OSError:
        pass


def _close_client(conn) -> None:
  try:
    conn.shutdown(socket.SHUT_RDWR)
  except OSError:
    pass
  try:
    conn.close()
  except OSError:
    pass


def serve_navigation_tcp(listener, client_handler: Callable[[object, object], None], *,
                          max_clients: int = NAVI_TCP_MAX_CLIENTS,
                          on_reject: Callable[[str], object] | None = None,
                          should_run: Callable[[], bool] | None = None) -> None:
  if type(max_clients) is not int or max_clients < 1 or max_clients > NAVI_TCP_MAX_CLIENTS:
    raise ValueError("max_clients must be between one and four")
  running = should_run or (lambda: True)
  slots = threading.BoundedSemaphore(max_clients)

  def reject(code: str) -> None:
    if on_reject is not None:
      try:
        on_reject(code)
      except Exception:
        pass

  def run_client(conn, addr) -> None:
    try:
      client_handler(conn, addr)
    except Exception:
      reject("client_handler_error")
    finally:
      slots.release()
      _close_client(conn)

  while running():
    try:
      conn, addr = listener.accept()
    except OSError:
      if running():
        reject("listener_error")
      return
    if not slots.acquire(blocking=False):
      _close_client(conn)
      reject("server_busy")
      continue
    thread = threading.Thread(target=run_client, args=(conn, addr), daemon=True)
    try:
      thread.start()
    except Exception:
      slots.release()
      _close_client(conn)
      reject("listener_error")
