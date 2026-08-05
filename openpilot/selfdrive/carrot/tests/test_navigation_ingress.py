from __future__ import annotations

import ast
from collections import Counter
import ipaddress
import json
import math
from pathlib import Path
import socket
import threading
from typing import Callable

import pytest

from openpilot.selfdrive.carrot.navigation_ingress import (
  NAVER_DISCOVERY_CLIENT_PORT,
  NAVER_DISCOVERY_SERVER_PORT,
  NAVI_TCP_MAX_CLIENTS,
  NAVI_TCP_MAX_FRAME_BYTES,
  NavigationPeerState,
  NavigationIngress,
  NavigationIngressError,
  bind_tmap_legacy_frame,
  build_legacy_http_session_id,
  build_naver_discovery_response,
  handle_navigation_udp_datagram,
  parse_naver_discovery_request,
  serve_navigation_tcp,
  strict_json_loads,
)
from openpilot.selfdrive.carrot.navigation_sources import (
  NavigationControlState,
  NavigationLifecycle,
  NavigationSnapshot,
  NavigationSource,
  NavigationSourceStore,
)


SESSION_A = "019f0000-0000-7000-8000-000000000001"
SESSION_B = "019f0000-0000-7000-8000-000000000002"


class ManualClock:
  def __init__(self, now_s: float):
    self.now_s = now_s
    self._lock = threading.Lock()

  def __call__(self) -> float:
    with self._lock:
      return self.now_s

  def set(self, now_s: float) -> None:
    with self._lock:
      self.now_s = now_s


class RecordingCarrotServ:
  def __init__(self):
    self.navigation_sources = NavigationSourceStore()
    self.accepted: list[NavigationSnapshot] = []
    self._condition = threading.Condition()

  def accept_navigation_snapshot(self, snapshot: NavigationSnapshot) -> bool:
    accepted = self.navigation_sources.accept(snapshot, snapshot.received_mono_s)
    if accepted:
      with self._condition:
        self.accepted.append(snapshot)
        self._condition.notify_all()
    return accepted

  def wait_for_sources(self, sources: set[NavigationSource], timeout_s: float = 2.0) -> None:
    with self._condition:
      assert self._condition.wait_for(
        lambda: sources <= {snapshot.source for snapshot in self.accepted},
        timeout=timeout_s,
      )


class RecordingLegacyDispatch:
  def __init__(self, carrot_serv: RecordingCarrotServ):
    self.carrot_serv = carrot_serv
    self.calls: list[tuple[dict, object, float, str]] = []
    self._sequences: Counter[str] = Counter()

  def __call__(self, obj: dict, peer: object, received_mono_s: float, session_id: str) -> bool:
    self.calls.append((obj, peer, received_mono_s, session_id))
    if type(obj.get("rgdata")) is not dict:
      return True
    self._sequences[session_id] += 1
    return self.carrot_serv.accept_navigation_snapshot(NavigationSnapshot(
      source=NavigationSource.TMAP_LEGACY,
      session_id=session_id,
      sequence=self._sequences[session_id],
      lifecycle=NavigationLifecycle.GUIDING,
      received_mono_s=received_mono_s,
      activation_epoch=0,
      control=NavigationControlState(),
    ))


class IngressHarness:
  def __init__(self, now_s: float = 10.0):
    self.clock = ManualClock(now_s)
    self.carrot_serv = RecordingCarrotServ()
    self.legacy = RecordingLegacyDispatch(self.carrot_serv)
    self.losses: list[tuple[NavigationSource, str, float]] = []
    self.rejections: list[str] = []
    self._condition = threading.Condition()
    self.ingress = NavigationIngress(
      carrot_serv=self.carrot_serv,
      legacy_dispatch=self.legacy,
      record_transport_loss=self.record_transport_loss,
      monotonic=self.clock,
      reject=self.reject,
    )

  def record_transport_loss(self, source: NavigationSource, session_id: str, now_s: float) -> bool:
    with self._condition:
      self.losses.append((source, session_id, now_s))
      accepted = self.carrot_serv.navigation_sources.record_transport_loss(source, session_id, now_s)
      self._condition.notify_all()
    return accepted

  def reject(self, code: str) -> None:
    with self._condition:
      self.rejections.append(code)
      self._condition.notify_all()

  def wait_for_rejection(self, code: str, timeout_s: float = 2.0) -> None:
    with self._condition:
      assert self._condition.wait_for(lambda: code in self.rejections, timeout=timeout_s)

  def wait_for_losses(self, count: int, timeout_s: float = 2.0) -> None:
    with self._condition:
      assert self._condition.wait_for(lambda: len(self.losses) >= count, timeout=timeout_s)


class ScriptedConnection:
  def __init__(self, actions: list[bytes | BaseException]):
    self.actions = list(actions)
    self.closed = False
    self.recv_count = 0

  def settimeout(self, timeout_s: float) -> None:
    assert timeout_s > 0

  def setsockopt(self, *args) -> None:
    pass

  def recv(self, maximum: int) -> bytes:
    self.recv_count += 1
    action = self.actions.pop(0)
    if isinstance(action, BaseException):
      raise action
    assert len(action) <= maximum
    return action

  def close(self) -> None:
    self.closed = True


def naver_frame(*, session_id: str = SESSION_A, sequence: int = 1,
                lifecycle: str = "guiding") -> dict:
  active = lifecycle == "guiding"
  return {
    "schema": "naver.navigation.v1",
    "sessionId": session_id,
    "sequence": sequence,
    "sentMonotonicMs": 123456,
    "lifecycle": lifecycle,
    "guidance": {
      "current": {
        "present": True,
        "maneuver": "left",
        "distanceM": 380,
        "roadName": "road",
        "mainText": "turn left",
      } if active else {"present": False},
      "next": {"present": False},
    },
    "safety": {"present": False},
    "road": {
      "limitValid": True,
      "limitKph": 80,
      "categoryValid": True,
      "category": 8,
    } if active else {"limitValid": False, "categoryValid": False},
    "route": {
      "present": True,
      "remainingDistanceM": 12500,
      "remainingTimeSec": 1320,
      "offRoute": False,
      "destinationValid": False,
    } if active else {"present": False},
  }


def encoded(obj: dict) -> bytes:
  return json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n"


def socket_client(handler: Callable[[socket.socket, tuple[str, int]], None],
                  peer_port: int) -> tuple[socket.socket, threading.Thread]:
  server, client = socket.socketpair()
  thread = threading.Thread(target=handler, args=(server, ("192.0.2.10", peer_port)), daemon=True)
  thread.start()
  return client, thread


@pytest.mark.parametrize(("payload", "code"), [
  (b"", "empty_frame"),
  (b" \r\n\t", "empty_frame"),
  (b"\xef\xbb\xbf{}", "invalid_utf8"),
  (b'{"value":"\xff"}', "invalid_utf8"),
  (b"{} {}", "invalid_json"),
  (b"[]", "non_object"),
  (b'{"outer":{"value":1,"value":2}}', "duplicate_key"),
  (b'{"value":NaN}', "non_finite_number"),
  (b'{"value":Infinity}', "non_finite_number"),
  (b'{"value":-Infinity}', "non_finite_number"),
])
def test_strict_json_rejects_ambiguous_or_non_object_input(payload, code):
  with pytest.raises(NavigationIngressError) as exc_info:
    strict_json_loads(payload)
  assert exc_info.value.code == code


def test_strict_json_errors_are_bounded_and_never_echo_payload():
  secret = b'{"secret-location":"do-not-echo",}'
  with pytest.raises(NavigationIngressError) as exc_info:
    strict_json_loads(secret)
  error = exc_info.value
  assert len(error.code) <= 32
  assert len(error.message) <= 96
  assert len(str(error)) <= 132
  assert "secret-location" not in str(error)


def test_strict_json_rejects_excessive_nesting_as_bounded_error():
  payload = b'{"value":' + (b"[" * 2000) + b"0" + (b"]" * 2000) + b"}"
  with pytest.raises(NavigationIngressError) as exc_info:
    strict_json_loads(payload)
  assert exc_info.value.code == "nesting_too_deep"


def test_strict_json_rejects_frame_larger_than_boundary():
  with pytest.raises(NavigationIngressError) as exc_info:
    strict_json_loads(b" " * (NAVI_TCP_MAX_FRAME_BYTES + 1))
  assert exc_info.value.code == "frame_too_large"


def test_fragmented_and_multiple_newline_frames_are_dispatched():
  harness = IngressHarness()
  client, thread = socket_client(harness.ingress.serve_client, 41001)
  first = encoded(naver_frame())
  second = encoded({"rgdata": {"nRoadLimitSpeed": 80}})
  client.sendall(first[:7])
  client.sendall(first[7:] + second)
  harness.carrot_serv.wait_for_sources({NavigationSource.NAVER_V1, NavigationSource.TMAP_LEGACY})
  client.close()
  thread.join(timeout=2.0)
  assert not thread.is_alive()


def test_exact_maximum_frame_is_accepted_when_complete():
  harness = IngressHarness()
  client, thread = socket_client(harness.ingress.serve_client, 41002)
  prefix = b'{"padding":"'
  suffix = b'"}'
  payload = prefix + (b"x" * (NAVI_TCP_MAX_FRAME_BYTES - len(prefix) - len(suffix))) + suffix
  assert len(payload) == NAVI_TCP_MAX_FRAME_BYTES
  client.sendall(payload + b"\n")
  client.close()
  thread.join(timeout=2.0)
  assert not thread.is_alive()
  assert harness.legacy.calls[0][0]["padding"]
  assert harness.rejections == []


@pytest.mark.parametrize("newline", [b"", b"\n"])
def test_maximum_plus_one_frame_is_rejected_and_closed(newline):
  harness = IngressHarness()
  client, thread = socket_client(harness.ingress.serve_client, 41003)
  client.sendall(b"{" + (b"x" * NAVI_TCP_MAX_FRAME_BYTES) + newline)
  harness.wait_for_rejection("frame_too_large")
  thread.join(timeout=2.0)
  assert not thread.is_alive()
  client.close()


def test_eof_with_unterminated_partial_frame_is_rejected():
  harness = IngressHarness()
  client, thread = socket_client(harness.ingress.serve_client, 41004)
  client.sendall(b'{"rgdata":{}')
  client.shutdown(socket.SHUT_WR)
  harness.wait_for_rejection("unterminated_frame")
  thread.join(timeout=2.0)
  assert not thread.is_alive()
  client.close()


def test_malformed_client_does_not_affect_another_client():
  harness = IngressHarness()
  bad, bad_thread = socket_client(harness.ingress.serve_client, 41005)
  good, good_thread = socket_client(harness.ingress.serve_client, 41006)
  bad.sendall(b'{"bad":"\xff"}\n')
  good.sendall(encoded(naver_frame()))
  harness.wait_for_rejection("invalid_utf8")
  harness.carrot_serv.wait_for_sources({NavigationSource.NAVER_V1})
  bad_thread.join(timeout=2.0)
  assert not bad_thread.is_alive()
  good.close()
  good_thread.join(timeout=2.0)
  bad.close()


@pytest.mark.parametrize("transport_error", [socket.timeout(), ConnectionResetError()])
def test_timeout_or_reset_records_only_the_associated_session(transport_error):
  harness = IngressHarness()

  class FailingConnection:
    def __init__(self):
      self.chunks = [encoded(naver_frame())]
      self.closed = False

    def settimeout(self, timeout_s: float) -> None:
      assert timeout_s > 0

    def setsockopt(self, *args) -> None:
      pass

    def recv(self, maximum: int) -> bytes:
      if self.chunks:
        return self.chunks.pop(0)
      raise transport_error

    def close(self) -> None:
      self.closed = True

  connection = FailingConnection()
  harness.ingress.serve_client(connection, ("192.0.2.10", 41020))
  assert connection.closed
  assert harness.losses == [(NavigationSource.NAVER_V1, SESSION_A, 10.0)]


@pytest.mark.parametrize(("name", "transport_end"), [
  ("eof", b""),
  ("reset", ConnectionResetError()),
  ("timeout", socket.timeout()),
  ("oserror", OSError("recv-error")),
])
def test_normal_transport_end_records_last_parsed_session(name, transport_end):
  harness = IngressHarness()
  connection = ScriptedConnection([encoded(naver_frame(session_id=SESSION_A)), transport_end])
  harness.ingress.serve_client(connection, ("192.0.2.10", 41021))
  assert connection.closed
  assert harness.losses == [(NavigationSource.NAVER_V1, SESSION_A, 10.0)]


def test_rejected_naver_snapshot_still_associates_parsed_session_for_eof_loss():
  class RejectingCarrotServ:
    def __init__(self):
      self.attempts = []

    def accept_navigation_snapshot(self, snapshot: NavigationSnapshot) -> bool:
      self.attempts.append(snapshot)
      return False

  carrot_serv = RejectingCarrotServ()
  losses = []
  ingress = NavigationIngress(
    carrot_serv=carrot_serv,
    legacy_dispatch=lambda *_args: True,
    record_transport_loss=lambda source, session_id, now_s: losses.append((source, session_id, now_s)),
    monotonic=ManualClock(10.0),
  )
  connection = ScriptedConnection([encoded(naver_frame(session_id=SESSION_B)), b""])
  ingress.serve_client(connection, ("192.0.2.10", 41022))
  assert [snapshot.session_id for snapshot in carrot_serv.attempts] == [SESSION_B]
  assert losses == [(NavigationSource.NAVER_V1, SESSION_B, 10.0)]


@pytest.mark.parametrize("lifecycle", ["stopped", "arrived"])
def test_rejected_terminal_tombstone_still_ends_immediately_without_loss(lifecycle):
  class RejectingCarrotServ:
    def accept_navigation_snapshot(self, snapshot: NavigationSnapshot) -> bool:
      return False

  losses = []
  ingress = NavigationIngress(
    carrot_serv=RejectingCarrotServ(),
    legacy_dispatch=lambda *_args: True,
    record_transport_loss=lambda source, session_id, now_s: losses.append((source, session_id, now_s)),
    monotonic=ManualClock(10.0),
  )
  connection = ScriptedConnection([
    encoded(naver_frame(session_id=SESSION_B, lifecycle=lifecycle)),
    socket.timeout(),
  ])
  ingress.serve_client(connection, ("192.0.2.10", 41023))
  assert connection.recv_count == 1
  assert losses == []


@pytest.mark.parametrize("bad_frame", [
  b'{"bad":"\xff"}\n',
  encoded(naver_frame(sequence=2) | {"unexpected": "secret-value"}),
])
def test_malformed_rejection_after_valid_frame_does_not_record_transport_loss(bad_frame):
  harness = IngressHarness()
  connection = ScriptedConnection([encoded(naver_frame()), bad_frame])
  harness.ingress.serve_client(connection, ("192.0.2.10", 41024))
  assert harness.losses == []
  assert harness.rejections


def test_naver_accept_oserror_after_association_rejects_without_transport_loss():
  class RaisingCarrotServ:
    def __init__(self):
      self.attempts = []

    def accept_navigation_snapshot(self, snapshot: NavigationSnapshot) -> bool:
      self.attempts.append(snapshot)
      if len(self.attempts) == 2:
        raise OSError("callback-private-detail")
      return True

  carrot_serv = RaisingCarrotServ()
  losses = []
  rejections = []
  ingress = NavigationIngress(
    carrot_serv=carrot_serv,
    legacy_dispatch=lambda *_args: True,
    record_transport_loss=lambda source, session_id, now_s: losses.append((source, session_id, now_s)),
    monotonic=ManualClock(10.0),
    reject=rejections.append,
  )
  connection = ScriptedConnection([
    encoded(naver_frame(session_id=SESSION_A, sequence=1)),
    encoded(naver_frame(session_id=SESSION_B, sequence=1)),
    b"",
  ])
  ingress.serve_client(connection, ("192.0.2.10", 41025))
  assert connection.closed
  assert connection.recv_count == 2
  assert [snapshot.session_id for snapshot in carrot_serv.attempts] == [SESSION_A, SESSION_B]
  assert losses == []
  assert rejections == ["client_handler_error"]


def test_legacy_dispatch_oserror_rejects_without_transport_loss():
  calls = []
  losses = []
  rejections = []

  def legacy_dispatch(obj, peer, received_mono_s, session_id):
    calls.append((obj, peer, received_mono_s, session_id))
    if len(calls) == 2:
      raise OSError("callback-private-detail")
    return True

  ingress = NavigationIngress(
    carrot_serv=RecordingCarrotServ(),
    legacy_dispatch=legacy_dispatch,
    record_transport_loss=lambda source, session_id, now_s: losses.append((source, session_id, now_s)),
    monotonic=ManualClock(10.0),
    reject=rejections.append,
  )
  connection = ScriptedConnection([
    encoded({"rgdata": {"nRoadLimitSpeed": 80}}),
    encoded({"rgdata": {"nRoadLimitSpeed": 90}}),
    b"",
  ])
  ingress.serve_client(connection, ("192.0.2.10", 41026))
  assert connection.closed
  assert connection.recv_count == 2
  assert len(calls) == 2
  assert losses == []
  assert rejections == ["client_handler_error"]


def test_multiple_legacy_frames_on_one_tcp_connection_share_synthetic_session():
  harness = IngressHarness()
  connection = ScriptedConnection([
    encoded({"rgdata": {"nRoadLimitSpeed": 80}}),
    encoded({"rgdata": {"nRoadLimitSpeed": 90}}),
    b"",
  ])
  harness.ingress.serve_client(connection, ("192.0.2.10", 41027))
  session_ids = [call[3] for call in harness.legacy.calls]
  assert len(session_ids) == 2
  assert session_ids[0] == session_ids[1]
  assert harness.losses == [(NavigationSource.TMAP_LEGACY, session_ids[0], 10.0)]


def test_exact_schema_dispatches_only_naver_and_rgdata_only_legacy():
  harness = IngressHarness()
  assert harness.ingress.dispatch_frame(naver_frame(), ("192.0.2.1", 1), 10.0) is NavigationSource.NAVER_V1
  assert harness.ingress.dispatch_frame(
    {"rgdata": {"provider": "naver", "source": "naver"}}, ("192.0.2.1", 2), 10.1,
  ) is NavigationSource.TMAP_LEGACY
  assert len(harness.carrot_serv.accepted) == 2


@pytest.mark.parametrize("schema", ["naver.navigation.v2", "", 1, True, None])
def test_wrong_schema_is_not_reinterpreted_as_naver_or_tmap(schema):
  harness = IngressHarness()
  frame = {"schema": schema, "rgdata": {"nRoadLimitSpeed": 80}}
  with pytest.raises(NavigationIngressError) as exc_info:
    harness.ingress.dispatch_frame(frame, ("192.0.2.1", 3), 10.0)
  assert exc_info.value.code == "unsupported_schema"
  assert harness.carrot_serv.accepted == []
  assert harness.legacy.calls == []


def test_connection_association_follows_last_successfully_accepted_frame():
  harness = IngressHarness()
  client, thread = socket_client(harness.ingress.serve_client, 41007)
  client.sendall(encoded(naver_frame(session_id=SESSION_A)))
  harness.carrot_serv.wait_for_sources({NavigationSource.NAVER_V1})
  client.sendall(encoded({"rgdata": {"nRoadLimitSpeed": 80}}))
  harness.carrot_serv.wait_for_sources({NavigationSource.TMAP_LEGACY})
  legacy_session = harness.legacy.calls[-1][3]
  client.close()
  harness.wait_for_losses(1)
  thread.join(timeout=2.0)
  assert harness.losses == [(NavigationSource.TMAP_LEGACY, legacy_session, 10.0)]


def test_old_socket_close_cannot_affect_replacement_session():
  harness = IngressHarness()
  old, old_thread = socket_client(harness.ingress.serve_client, 41008)
  new, new_thread = socket_client(harness.ingress.serve_client, 41009)
  old.sendall(encoded(naver_frame(session_id=SESSION_A)))
  harness.carrot_serv.wait_for_sources({NavigationSource.NAVER_V1})
  harness.clock.set(10.1)
  new.sendall(encoded(naver_frame(session_id=SESSION_B)))
  with harness.carrot_serv._condition:
    assert harness.carrot_serv._condition.wait_for(
      lambda: any(snapshot.session_id == SESSION_B for snapshot in harness.carrot_serv.accepted), timeout=2.0,
    )
  old.close()
  harness.wait_for_losses(1)
  selected = harness.carrot_serv.navigation_sources.select(10.2)
  assert selected.snapshot is not None
  assert selected.snapshot.session_id == SESSION_B
  new.close()
  old_thread.join(timeout=2.0)
  new_thread.join(timeout=2.0)


def test_naver_eof_keeps_owner_until_exact_lease_boundary():
  harness = IngressHarness()
  client, thread = socket_client(harness.ingress.serve_client, 41010)
  client.sendall(encoded(naver_frame()))
  harness.carrot_serv.wait_for_sources({NavigationSource.NAVER_V1})
  client.close()
  harness.wait_for_losses(1)
  thread.join(timeout=2.0)
  assert harness.carrot_serv.navigation_sources.select(11.999).snapshot is not None
  assert harness.carrot_serv.navigation_sources.select(12.0).snapshot is None


def test_legacy_eof_keeps_owner_until_exact_lease_boundary():
  harness = IngressHarness()
  client, thread = socket_client(harness.ingress.serve_client, 41011)
  client.sendall(encoded({"rgdata": {"nRoadLimitSpeed": 80}}))
  harness.carrot_serv.wait_for_sources({NavigationSource.TMAP_LEGACY})
  client.close()
  harness.wait_for_losses(1)
  thread.join(timeout=2.0)
  assert harness.carrot_serv.navigation_sources.select(13.999).snapshot is not None
  assert harness.carrot_serv.navigation_sources.select(14.0).snapshot is None


@pytest.mark.parametrize("lifecycle", ["stopped", "arrived"])
def test_explicit_terminal_ends_immediately_without_transport_loss(lifecycle):
  harness = IngressHarness()
  client, thread = socket_client(harness.ingress.serve_client, 41012)
  client.sendall(encoded(naver_frame(sequence=1)))
  harness.carrot_serv.wait_for_sources({NavigationSource.NAVER_V1})
  client.sendall(encoded(naver_frame(sequence=2, lifecycle=lifecycle)))
  with harness.carrot_serv._condition:
    assert harness.carrot_serv._condition.wait_for(
      lambda: any(snapshot.lifecycle.value == lifecycle for snapshot in harness.carrot_serv.accepted), timeout=2.0,
    )
  thread.join(timeout=2.0)
  assert not thread.is_alive()
  assert harness.losses == []
  assert harness.carrot_serv.navigation_sources.select(10.0).snapshot is None
  client.close()


def test_persistent_naver_connection_does_not_block_tmap_client():
  harness = IngressHarness()
  listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
  listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
  listener.bind(("127.0.0.1", 0))
  listener.listen(NAVI_TCP_MAX_CLIENTS)
  stop = threading.Event()
  server_thread = threading.Thread(
    target=serve_navigation_tcp,
    args=(listener, harness.ingress.serve_client),
    kwargs={"should_run": lambda: not stop.is_set()},
    daemon=True,
  )
  server_thread.start()
  naver = socket.create_connection(listener.getsockname(), timeout=2.0)
  naver.sendall(encoded(naver_frame()))
  harness.carrot_serv.wait_for_sources({NavigationSource.NAVER_V1})
  tmap = socket.create_connection(listener.getsockname(), timeout=2.0)
  tmap.sendall(encoded({"rgdata": {"nRoadLimitSpeed": 80}}))
  harness.carrot_serv.wait_for_sources({NavigationSource.NAVER_V1, NavigationSource.TMAP_LEGACY})
  tmap.close()
  naver.close()
  stop.set()
  listener.close()


def test_listener_admits_exactly_four_handlers_and_promptly_rejects_fifth():
  listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
  listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
  listener.bind(("127.0.0.1", 0))
  listener.listen(NAVI_TCP_MAX_CLIENTS)
  release = threading.Event()
  rejected = threading.Event()
  condition = threading.Condition()
  active = 0
  maximum_active = 0

  def handler(conn: socket.socket, addr: tuple[str, int]) -> None:
    nonlocal active, maximum_active
    with condition:
      active += 1
      maximum_active = max(maximum_active, active)
      condition.notify_all()
    assert release.wait(timeout=2.0)
    with condition:
      active -= 1

  server_thread = threading.Thread(
    target=serve_navigation_tcp,
    args=(listener, handler),
    kwargs={"on_reject": lambda code: rejected.set()},
    daemon=True,
  )
  server_thread.start()
  clients = []
  for count in range(1, NAVI_TCP_MAX_CLIENTS + 1):
    clients.append(socket.create_connection(listener.getsockname(), timeout=2.0))
    with condition:
      assert condition.wait_for(lambda: active == count, timeout=2.0)
  fifth = socket.create_connection(listener.getsockname(), timeout=2.0)
  assert rejected.wait(timeout=2.0)
  assert maximum_active == NAVI_TCP_MAX_CLIENTS
  assert active == NAVI_TCP_MAX_CLIENTS
  fifth.close()
  release.set()
  for client in clients:
    client.close()
  listener.close()


@pytest.mark.parametrize("handler_raises", [False, True])
def test_listener_reuses_slots_after_handler_exit(handler_raises):
  listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
  listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
  listener.bind(("127.0.0.1", 0))
  listener.listen(NAVI_TCP_MAX_CLIENTS)
  release = threading.Event()
  condition = threading.Condition()
  calls = 0
  errors: list[str] = []

  def handler(conn: socket.socket, addr: tuple[str, int]) -> None:
    nonlocal calls
    with condition:
      calls += 1
      call = calls
      condition.notify_all()
    if call <= NAVI_TCP_MAX_CLIENTS:
      assert release.wait(timeout=2.0)
      if handler_raises:
        raise RuntimeError("secret handler payload must not escape")

  threading.Thread(
    target=serve_navigation_tcp,
    args=(listener, handler),
    kwargs={"on_reject": errors.append},
    daemon=True,
  ).start()
  first_wave = [
    socket.create_connection(listener.getsockname(), timeout=2.0)
    for _ in range(NAVI_TCP_MAX_CLIENTS)
  ]
  with condition:
    assert condition.wait_for(lambda: calls == NAVI_TCP_MAX_CLIENTS, timeout=2.0)
  release.set()
  for client in first_wave:
    client.settimeout(2.0)
    assert client.recv(1) == b""
    client.close()

  replacement = socket.create_connection(listener.getsockname(), timeout=2.0)
  with condition:
    assert condition.wait_for(lambda: calls == NAVI_TCP_MAX_CLIENTS + 1, timeout=2.0)
  replacement.settimeout(2.0)
  assert replacement.recv(1) == b""
  assert "server_busy" not in errors
  if handler_raises:
    assert errors.count("client_handler_error") == NAVI_TCP_MAX_CLIENTS
  replacement.close()
  listener.close()


def exact_discovery_request() -> bytes:
  return b'{"type":"carrot.navigation.discover","source":"naver","schema_version":1}'


def test_discovery_request_requires_exact_keys_types_and_values():
  assert parse_naver_discovery_request(exact_discovery_request()) == {
    "type": "carrot.navigation.discover",
    "source": "naver",
    "schema_version": 1,
  }
  invalid = [
    b'{"type":"carrot.navigation.discover","source":"naver","schema_version":true}',
    b'{"type":"carrot.navigation.discover","source":"naver","schema_version":1.0}',
    b'{"type":"carrot.navigation.discover","source":"tmap","schema_version":1}',
    b'{"type":"carrot.navigation.discover","source":"naver","schema_version":2}',
    b'{"type":"carrot.navigation.discover","source":"naver","schema_version":1,"extra":0}',
    b'{"type":"carrot.navigation.discover","source":"naver","source":"naver","schema_version":1}',
  ]
  for request in invalid:
    with pytest.raises(NavigationIngressError) as exc_info:
      parse_naver_discovery_request(request)
    assert exc_info.value.code == "invalid_discovery"


@pytest.mark.parametrize("local_ip", [None, "", "0.0.0.0", "127.0.0.1", "224.0.0.1", "::1", True])
def test_discovery_response_rejects_unusable_local_ipv4(local_ip):
  with pytest.raises(NavigationIngressError) as exc_info:
    build_naver_discovery_response(local_ip)  # type: ignore[arg-type]
  assert exc_info.value.code == "invalid_local_ip"


def test_discovery_response_has_only_fixed_bounded_fields():
  response = build_naver_discovery_response("192.168.43.1")
  assert len(response) <= 256
  assert strict_json_loads(response) == {
    "type": "carrot.navigation.discover.response",
    "server": "192.168.43.1",
    "port": 7712,
    "schema": 1,
    "schema_version": 1,
    "lease_ms": 2000,
  }


def test_udp_discovery_uses_bound_7706_source_and_requester_7705_destination():
  route_probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
  try:
    route_probe.connect(("8.8.8.8", 80))
    local_ip = route_probe.getsockname()[0]
  except OSError as error:
    pytest.skip(f"non-loopback IPv4 route unavailable: {error.errno}")
  finally:
    route_probe.close()
  if ipaddress.ip_address(local_ip).is_loopback:
    pytest.skip("non-loopback IPv4 route unavailable")
  server = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
  client = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
  try:
    server.bind((local_ip, NAVER_DISCOVERY_SERVER_PORT))
    client.bind((local_ip, NAVER_DISCOVERY_CLIENT_PORT))
  except OSError as error:
    server.close()
    client.close()
    pytest.skip(f"fixed discovery port unavailable: {error.errno}")
  server.settimeout(2.0)
  client.settimeout(2.0)
  legacy_updates: list[dict] = []
  completed = threading.Event()

  def receive_two() -> None:
    for _ in range(2):
      data, remote_addr = server.recvfrom(4096)
      handle_navigation_udp_datagram(
        server, data, remote_addr, "192.168.43.1", legacy_updates.append,
      )
    completed.set()

  thread = threading.Thread(target=receive_two, daemon=True)
  thread.start()
  client.sendto(exact_discovery_request(), (local_ip, NAVER_DISCOVERY_SERVER_PORT))
  response, source = client.recvfrom(4096)
  assert source[1] == NAVER_DISCOVERY_SERVER_PORT
  assert strict_json_loads(response)["port"] == 7712
  assert legacy_updates == []
  legacy = {"type": "legacy", "value": 7}
  client.sendto(encoded(legacy).rstrip(b"\n"), (local_ip, NAVER_DISCOVERY_SERVER_PORT))
  assert completed.wait(timeout=2.0)
  assert legacy_updates == [legacy]
  client.close()
  server.close()


def test_invalid_typed_discovery_is_not_forwarded_to_legacy_update():
  class DatagramSocket:
    def __init__(self):
      self.sent = []

    def sendto(self, data: bytes, destination: tuple[str, int]) -> None:
      self.sent.append((data, destination))

  sock = DatagramSocket()
  updates = []
  with pytest.raises(NavigationIngressError) as exc_info:
    handle_navigation_udp_datagram(
      sock,
      b'{"type":"carrot.navigation.discover","source":"naver","schema_version":2}',
      ("192.0.2.20", 43000),
      "192.168.43.1",
      updates.append,
    )
  assert exc_info.value.code == "invalid_discovery"
  assert updates == []
  assert sock.sent == []


def test_duplicate_discovery_type_is_not_downgraded_to_legacy_update():
  class DatagramSocket:
    def sendto(self, data: bytes, destination: tuple[str, int]) -> None:
      raise AssertionError("invalid discovery datagram must not send a response")

  updates = []
  with pytest.raises(NavigationIngressError) as exc_info:
    handle_navigation_udp_datagram(
      DatagramSocket(),
      b'{"type":"carrot.navigation.discover","type":"legacy","source":"naver",'
      b'"schema_version":1,"extra":0}',
      ("192.0.2.20", 43000),
      "192.168.43.1", updates.append,
    )
  assert exc_info.value.code == "invalid_discovery"
  assert updates == []


@pytest.mark.parametrize("remote_addr", [
  None,
  (),
  [],
  ("", 7705),
  ("not-an-ip", 7705),
  ("0.0.0.0", 7705),
  ("127.0.0.1", 7705),
  ("169.254.1.2", 7705),
  ("224.0.0.1", 7705),
  ("255.255.255.255", 7705),
  ("192.168.43.2", 7704),
  ("192.168.43.2", True),
  ("192.168.43.2", "7705"),
])
def test_typed_discovery_rejects_malformed_requester(remote_addr):
  class DatagramSocket:
    def __init__(self):
      self.sent = []

    def sendto(self, data: bytes, destination: tuple[str, int]) -> None:
      self.sent.append((data, destination))

  sock = DatagramSocket()
  updates = []
  with pytest.raises(NavigationIngressError) as exc_info:
    handle_navigation_udp_datagram(
      sock, exact_discovery_request(), remote_addr, "192.168.43.1", updates.append,
    )
  assert exc_info.value.code == "invalid_peer"
  assert updates == []
  assert sock.sent == []


@pytest.mark.parametrize("wrong_type", [
  "carrot.navigation.discover.response",
  "carrot.navigation.discovr",
])
def test_discovery_shaped_wrong_type_is_not_forwarded_to_legacy_update(wrong_type):
  class DatagramSocket:
    def __init__(self):
      self.sent = []

    def sendto(self, data: bytes, destination: tuple[str, int]) -> None:
      self.sent.append((data, destination))

  sock = DatagramSocket()
  updates = []
  payload = json.dumps({
    "type": wrong_type,
    "source": "naver",
    "schema_version": 1,
  }, separators=(",", ":")).encode()
  with pytest.raises(NavigationIngressError) as exc_info:
    handle_navigation_udp_datagram(
      sock, payload, ("192.0.2.20", 43000), "192.168.43.1", updates.append,
    )
  assert exc_info.value.code == "invalid_discovery"
  assert updates == []
  assert sock.sent == []


def test_general_legacy_type_still_reaches_udp_update_path():
  class DatagramSocket:
    def sendto(self, data: bytes, destination: tuple[str, int]) -> None:
      raise AssertionError("legacy datagram must not send discovery response")

  legacy = {"type": "legacy", "value": 7}
  updates = []
  assert not handle_navigation_udp_datagram(
    DatagramSocket(), json.dumps(legacy).encode(), ("192.0.2.20", 43000),
    "192.168.43.1", updates.append,
  )
  assert updates == [legacy]


def test_udp_legacy_flat_position_nan_reaches_update_path():
  class DatagramSocket:
    def sendto(self, data: bytes, destination: tuple[str, int]) -> None:
      raise AssertionError("legacy datagram must not send discovery response")

  updates = []
  assert not handle_navigation_udp_datagram(
    DatagramSocket(),
    b'{"nRoadLimitSpeed":80,"vpPosPointLat":NaN,"vpPosPointLon":127.1}',
    ("192.0.2.20", 43000),
    "192.168.43.1", updates.append,
  )
  assert updates[0]["nRoadLimitSpeed"] == 80
  assert math.isnan(updates[0]["vpPosPointLat"])
  assert updates[0]["vpPosPointLon"] == 127.1


@pytest.mark.parametrize("payload", [
  b'{"nRoadLimitSpeed":80,"nRoadLimitSpeed":90}',
  b'{"nRoadLimitSpeed":80,"vpPosPointLat":NaN,"vpPosPointLat":37.5}',
])
def test_udp_legacy_duplicate_keys_are_rejected(payload):
  class DatagramSocket:
    def sendto(self, data: bytes, destination: tuple[str, int]) -> None:
      raise AssertionError("legacy datagram must not send discovery response")

  updates = []
  with pytest.raises(NavigationIngressError) as exc_info:
    handle_navigation_udp_datagram(
      DatagramSocket(), payload, ("192.0.2.20", 43000),
      "192.168.43.1", updates.append,
    )
  assert exc_info.value.code == "duplicate_key"
  assert updates == []


def test_cross_transport_peer_selection_and_restore_policy():
  peers = NavigationPeerState()
  udp = ("192.0.2.10", 45000)
  http = ("192.0.2.20", NAVER_DISCOVERY_CLIENT_PORT)
  tcp_old = ("192.0.2.30", 46000)
  tcp_new = ("192.0.2.40", 47000)
  old_token, new_token = object(), object()

  assert peers.set_fallback("udp", udp) == udp
  assert peers.set_fallback("http", http) == http
  assert peers.set_tcp(old_token, tcp_old) == tcp_old
  assert peers.set_tcp(new_token, tcp_new) == tcp_new
  assert peers.clear_tcp(old_token) == tcp_new
  assert peers.clear_tcp(new_token) == http
  assert peers.clear_fallback("http") == udp
  assert peers.clear_fallback("udp") is None


def test_transport_timeout_removes_only_that_peer_entry():
  peers = NavigationPeerState()
  udp = ("192.0.2.10", 45000)
  http = ("192.0.2.20", NAVER_DISCOVERY_CLIENT_PORT)
  tcp = ("192.0.2.30", 46000)
  token = object()

  peers.set_fallback("udp", udp)
  peers.set_fallback("http", http)
  peers.set_tcp(token, tcp)
  assert peers.clear_fallback("udp") == tcp
  assert peers.clear_tcp(token) == http
  assert peers.clear_fallback("http") is None


def test_http_legacy_binding_is_transport_derived_and_payload_cannot_change_source():
  peer = ("192.0.2.50", 48000)
  session_id = build_legacy_http_session_id(peer, "8.2.2")
  assert session_id == build_legacy_http_session_id((peer[0], 49000), "8.2.2")
  assert session_id != build_legacy_http_session_id(peer, "9.0.0")
  assert session_id.startswith("tmap-http-")
  assert len(session_id) <= 32

  original = {
    "_tmap_version": "8.2.2",
    "rgdata": {
      "provider": "naver",
      "source": "naver",
      "nRoadLimitSpeed": 80,
    },
  }
  bound = bind_tmap_legacy_frame(original, session_id, 10.0)
  assert "_navigation_source" not in original["rgdata"]
  assert bound["rgdata"]["provider"] == "naver"
  assert bound["rgdata"]["source"] == "naver"
  assert bound["rgdata"]["_navigation_source"] == NavigationSource.TMAP_LEGACY.value
  assert bound["rgdata"]["_navigation_session_id"] == session_id
  assert bound["rgdata"]["_navigation_received_mono_s"] == 10.0


def test_carrot_man_keeps_ingress_as_thin_wiring_without_unbounded_readline():
  source_path = Path(__file__).parents[1] / "carrot_man.py"
  source = source_path.read_text(encoding="utf-8")
  tree = ast.parse(source)
  carrot_man = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "CarrotMan")
  methods = {node.name: node for node in carrot_man.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
  assert "_dispatch_navi_frame" in methods
  assert "_serve_navi_client" in methods
  assert "carrot_navi_tcp_server" in methods
  assert "_set_navigation_peer" in methods
  assert "_clear_navigation_peer" in methods
  tcp_source = ast.get_source_segment(source, methods["carrot_navi_tcp_server"])
  client_source = ast.get_source_segment(source, methods["_serve_navi_client"])
  assert tcp_source is not None and "serve_navigation_tcp" in tcp_source
  assert client_source is not None and ".readline(" not in client_source
  assert "errors=\"ignore\"" not in client_source
  assert '_set_navigation_peer("tcp"' in client_source
  assert '_clear_navigation_peer("tcp"' in client_source
  udp_source = ast.get_source_segment(source, methods["carrot_man_thread"])
  assert udp_source is not None and 'self.remote_addr = None' not in udp_source
  assert "self.get_local_ip(remote_addr[0])" in udp_source
  assert '_set_navigation_peer("udp"' in udp_source
  assert '_clear_navigation_peer("udp"' in udp_source
  http_source = ast.get_source_segment(source, methods["carrot_http_post"])
  assert http_source is not None and "_dispatch_legacy_navi_frame" in http_source
  assert "_dispatch_navi_frame" not in http_source
  assert 'obj["_tmap_version"] = tmap_version' in http_source
  assert '_set_navigation_peer("http"' in http_source
  assert "build_legacy_http_session_id" in http_source


def test_carrot_man_local_ip_route_selection_defaults_and_targets_requester():
  source_path = Path(__file__).parents[1] / "carrot_man.py"
  tree = ast.parse(source_path.read_text(encoding="utf-8"))
  carrot_man = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "CarrotMan")
  get_local_ip_node = next(
    node for node in carrot_man.body if isinstance(node, ast.FunctionDef) and node.name == "get_local_ip"
  )
  namespace = {"socket": _RecordingSocketModule()}
  ast.fix_missing_locations(get_local_ip_node)
  exec(compile(ast.Module(body=[get_local_ip_node], type_ignores=[]), str(source_path), "exec"), namespace)
  get_local_ip = namespace["get_local_ip"]

  assert get_local_ip(object()) == "192.168.43.1"
  assert namespace["socket"].destinations == [("8.8.8.8", 80)]
  assert get_local_ip(object(), "192.168.43.25") == "192.168.43.1"
  assert namespace["socket"].destinations[-1] == ("192.168.43.25", 80)


class _RecordingSocket:
  def __init__(self, destinations):
    self.destinations = destinations

  def __enter__(self):
    return self

  def __exit__(self, exc_type, exc_value, traceback):
    return False

  def connect(self, destination):
    self.destinations.append(destination)

  def getsockname(self):
    return ("192.168.43.1", 12345)


class _RecordingSocketModule:
  AF_INET = socket.AF_INET
  SOCK_DGRAM = socket.SOCK_DGRAM

  def __init__(self):
    self.destinations = []

  def socket(self, family, socket_type):
    assert (family, socket_type) == (self.AF_INET, self.SOCK_DGRAM)
    return _RecordingSocket(self.destinations)
