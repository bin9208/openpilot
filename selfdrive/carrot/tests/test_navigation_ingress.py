from __future__ import annotations

import ast
import json
import socket
import threading
import time
from types import SimpleNamespace
from typing import Any
from pathlib import Path

import pytest


NAVIGATION_DISCOVERY = "carrot.navigation.discover"
NAVIGATION_MAX_CLIENTS = 4
NAVIGATION_MAX_LINE_BYTES = 262_144
NAVIGATION_TCP_PORT = 7712


def _load_carrot_manager_class() -> type:
  """Load only CarrotMan so host-only tests do not initialize Cap'n Proto."""
  source_path = Path(__file__).resolve().parents[1] / "carrot_man.py"
  tree = ast.parse(source_path.read_text(encoding="utf-8"))
  class_node = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "CarrotMan")
  namespace = {
    "Any": Any,
    "NAVIGATION_DISCOVERY": NAVIGATION_DISCOVERY,
    "NAVIGATION_DISCOVERY_RESPONSE": "carrot.navigation.discover.response",
    "NAVIGATION_SCHEMA_VERSION": 1,
    "NAVIGATION_LEASE_MS": 4_000,
    "NAVIGATION_TCP_PORT": NAVIGATION_TCP_PORT,
    "NAVIGATION_MAX_CLIENTS": NAVIGATION_MAX_CLIENTS,
    "NAVIGATION_READ_TIMEOUT": 5.0,
    "NAVIGATION_MAX_LINE_BYTES": NAVIGATION_MAX_LINE_BYTES,
    "json": json,
    "socket": socket,
    "threading": threading,
    "time": time,
    "web": SimpleNamespace(Request=object),
  }
  module = ast.Module(body=[class_node], type_ignores=[])
  exec(compile(module, str(source_path), "exec"), namespace)
  return namespace["CarrotMan"]


CarrotMan = _load_carrot_manager_class()


TMAP_SESSION = "11111111-1111-4111-8111-111111111111"
NAVER_SESSION = "22222222-2222-4222-8222-222222222222"


def _canonical(source: str, session_id: str, sequence: int) -> bytes:
  return json.dumps({
    "source": source,
    "schema_version": 1,
    "session_id": session_id,
    "sequence": sequence,
    "timestamp_ms": sequence * 1_000,
    "navigation_active": True,
    "ttl_ms": 1_000,
    "state": {
      "status": "guiding",
      "maneuver": "left",
      "maneuver_distance_m": 120,
      "remaining_distance_m": 5_000,
      "remaining_time_s": 600,
      "road_limit_kph": 80,
      "position": {"longitude": 127.01, "latitude": 37.51},
    },
  }, separators=(",", ":")).encode()


def _legacy() -> bytes:
  return json.dumps({
    "rgdata": {
      "nRoadLimitSpeed": 80,
      "nTBTTurnType": 12,
      "nTBTDist": 120,
      "nGoPosDist": 5_000,
      "nGoPosTime": 600,
      "vpPosPointLon": 127.01,
      "vpPosPointLat": 37.51,
    },
  }, separators=(",", ":")).encode()


def _manager() -> CarrotMan:
  manager = CarrotMan.__new__(CarrotMan)
  manager.ip_address = "127.0.0.1"
  manager.broadcast_port = 7705
  manager.carrot_man_port = 7706
  manager.carrot_serv = SimpleNamespace(update=lambda _obj: None)
  manager.remote_addr = None
  return manager


def _start(manager: CarrotMan) -> tuple[threading.Event, threading.Thread, int]:
  stop_event = threading.Event()
  thread = threading.Thread(target=manager.carrot_navi_tcp_server, kwargs={"port": 0, "stop_event": stop_event})
  thread.start()
  deadline = time.monotonic() + 2
  while not hasattr(manager, "_navigation_bound_port") and time.monotonic() < deadline:
    time.sleep(0.01)
  assert hasattr(manager, "_navigation_bound_port")
  return stop_event, thread, manager._navigation_bound_port


def _stop(stop_event: threading.Event, thread: threading.Thread) -> None:
  stop_event.set()
  thread.join(timeout=2)
  assert not thread.is_alive()


def test_discovery_is_intercepted_before_carrot_update_and_replied_on_udp_7705() -> None:
  manager = _manager()
  updates: list[Any] = []
  manager.carrot_serv.update = updates.append
  receive = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
  receive.bind(("127.0.0.1", 7705))
  source = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
  source.bind(("127.0.0.1", 0))
  try:
    request = json.dumps({"type": NAVIGATION_DISCOVERY, "schema": 1}).encode()
    assert manager._handle_navigation_datagram(source, request, ("127.0.0.1", source.getsockname()[1]))
    receive.settimeout(1)
    payload, _peer = receive.recvfrom(4096)
    response = json.loads(payload.decode())
    assert response["server"] == "127.0.0.1"
    assert response["port"] == NAVIGATION_TCP_PORT
    assert response["schema"] == 1
    assert response["lease"] == 4_000
    assert updates == []
  finally:
    source.close()
    receive.close()


def test_two_clients_progress_while_one_is_idle_and_peer_context_is_preserved() -> None:
  manager = _manager()
  received: list[tuple[Any, tuple[str, int] | None]] = []
  manager._dispatch_obj = lambda obj, peer=None: received.append((obj, peer))
  stop_event, thread, port = _start(manager)
  idle = socket.create_connection(("127.0.0.1", port), timeout=1)
  active = socket.create_connection(("127.0.0.1", port), timeout=1)
  try:
    active.sendall(_legacy() + b"\n")
    deadline = time.monotonic() + 1
    while not received and time.monotonic() < deadline:
      time.sleep(0.01)
    assert received and received[0][1] is not None
  finally:
    idle.close()
    active.close()
    _stop(stop_event, thread)


def test_simultaneous_active_tmap_and_naver_sessions_keep_peer_provider_context_distinct() -> None:
  manager = _manager()
  received: list[tuple[Any, tuple[str, int] | None]] = []
  manager._dispatch_obj = lambda obj, peer=None: received.append((obj, peer))
  stop_event, thread, port = _start(manager)
  tmap = socket.create_connection(("127.0.0.1", port), timeout=1)
  naver = socket.create_connection(("127.0.0.1", port), timeout=1)
  barrier = threading.Barrier(3)

  def send_tmap() -> None:
    barrier.wait()
    tmap.sendall(_legacy() + b"\n")

  def send_naver() -> None:
    barrier.wait()
    naver.sendall(_canonical("naver", NAVER_SESSION, 1) + b"\n")

  tmap_sender = threading.Thread(target=send_tmap)
  naver_sender = threading.Thread(target=send_naver)
  tmap_sender.start()
  naver_sender.start()
  try:
    barrier.wait()
    tmap_sender.join(timeout=1)
    naver_sender.join(timeout=1)
    deadline = time.monotonic() + 1
    while len(received) < 2 and time.monotonic() < deadline:
      time.sleep(0.01)
    assert len(received) >= 2
    contexts = {peer for _obj, peer in received}
    providers = {
      "tmap-legacy" if "rgdata" in obj else obj["source"]
      for obj, _peer in received
    }
    assert len(contexts) >= 2
    assert providers == {"tmap-legacy", "naver"}
  finally:
    tmap.close()
    naver.close()
    _stop(stop_event, thread)


def test_fifth_client_is_rejected_and_idle_clients_time_out_and_clean_up() -> None:
  manager = _manager()
  stop_event, thread, port = _start(manager)
  clients = [socket.create_connection(("127.0.0.1", port), timeout=1) for _ in range(NAVIGATION_MAX_CLIENTS)]
  fifth = socket.create_connection(("127.0.0.1", port), timeout=1)
  try:
    clients[0].sendall(b"{")
    fifth.settimeout(1)
    assert fifth.recv(1) == b""
    deadline = time.monotonic() + 7
    while getattr(manager, "_navigation_clients", {}) and time.monotonic() < deadline:
      time.sleep(0.05)
    assert not getattr(manager, "_navigation_clients", {})
  finally:
    fifth.close()
    for client in clients:
      client.close()
    _stop(stop_event, thread)


@pytest.mark.parametrize(
  "payload",
  [b"{\"rgdata\":\n", b"\xff\xfe\n", b"x" * (NAVIGATION_MAX_LINE_BYTES + 1) + b"\n"],
  ids=["invalid-json", "invalid-utf8", "oversize"],
)
def test_invalid_utf8_json_and_oversize_frames_close_without_dispatch(payload: bytes) -> None:
  manager = _manager()
  received: list[Any] = []
  manager._dispatch_obj = lambda obj, peer=None: received.append(obj)
  stop_event, thread, port = _start(manager)
  client = socket.create_connection(("127.0.0.1", port), timeout=1)
  try:
    client.sendall(payload)
    client.settimeout(2)
    try:
      assert client.recv(1) == b""
    except ConnectionResetError:
      pass
    assert received == []
  finally:
    client.close()
    _stop(stop_event, thread)


def test_reconnect_after_disconnect_and_discovery_flood_remain_bounded() -> None:
  manager = _manager()
  received: list[Any] = []
  manager._dispatch_obj = lambda obj, peer=None: received.append(obj)
  stop_event, thread, port = _start(manager)
  udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
  try:
    for _ in range(64):
      manager._handle_navigation_datagram(udp, json.dumps({"type": NAVIGATION_DISCOVERY, "schema": 1}).encode(), ("127.0.0.1", 9))
    first = socket.create_connection(("127.0.0.1", port), timeout=1)
    first.close()
    second = socket.create_connection(("127.0.0.1", port), timeout=1)
    second.sendall(_canonical("naver", NAVER_SESSION, 1) + b"\n")
    deadline = time.monotonic() + 1
    while not received and time.monotonic() < deadline:
      time.sleep(0.01)
    assert received
    second.close()
  finally:
    udp.close()
    _stop(stop_event, thread)
