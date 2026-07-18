from __future__ import annotations

import json
from pathlib import Path
import socket
import struct
import threading
import time

from selfdrive.carrot.tests.navigation_fail_neutral_harness import manager as make_manager
from selfdrive.carrot.tests.test_navigation_arbitration import assert_route, canonical_frame, legacy


def wait_for(predicate, label: str) -> None:
  deadline = time.monotonic() + 2.0
  while not predicate() and time.monotonic() < deadline:
    time.sleep(0.005)
  assert predicate(), label


def start_server(target, bound_attribute: str) -> tuple[threading.Event, threading.Thread, int]:
  stop_event = threading.Event()
  thread = threading.Thread(target=target, kwargs={"port": 0, "stop_event": stop_event})
  thread.start()
  wait_for(lambda: hasattr(target.__self__, bound_attribute), f"missing {bound_attribute}")
  return stop_event, thread, getattr(target.__self__, bound_attribute)


def stop_server(stop_event: threading.Event, thread: threading.Thread) -> None:
  stop_event.set()
  thread.join(timeout=2)
  assert not thread.is_alive()


def send_json_line(client: socket.socket, frame: dict) -> None:
  client.sendall(json.dumps(frame, separators=(",", ":")).encode("utf-8") + b"\n")


def test_real_tcp_and_binary_socket_story_preserves_legacy_endpoints_and_arbitrates() -> None:
  # Given: production socket methods on ephemeral ports and the unchanged endpoint declarations.
  source = (Path(__file__).resolve().parents[1] / "carrot_man.py").read_text(encoding="utf-8")
  assert 'app.router.add_post("/api/navi/{tmap_version}", self.carrot_http_post)' in source
  assert "self.carrot_man_port = 7706" in source
  assert "NAVIGATION_TCP_PORT = 7712" in source
  assert "def carrot_route(self, port=7709" in source
  assert "NavigationProvider" not in source

  manager = make_manager()
  tcp_stop, tcp_thread, tcp_port = start_server(manager.carrot_navi_tcp_server, "_navigation_bound_port")
  binary_stop, binary_thread, binary_port = start_server(manager.carrot_route, "_navigation_binary_bound_port")
  tmap = socket.create_connection(("127.0.0.1", tcp_port), timeout=1)
  naver = socket.create_connection(("127.0.0.1", tcp_port), timeout=1)
  reconnected = None
  try:
    # When: Tmap guides, later Naver owns, Naver expires, reconnects, stops, and a 7709 route follows.
    send_json_line(tmap, legacy(1))
    wait_for(lambda: manager.carrot_serv.navigation_provider == "tmap-legacy", "Tmap did not own")
    send_json_line(naver, canonical_frame("naver", "22222222-2222-4222-8222-222222222222", 1,
                                          route_offset=0.1, ttl_ms=250))
    wait_for(lambda: manager.carrot_serv.navigation_provider == "naver", "Naver did not own")
    time.sleep(0.26)
    manager._expire_navigation(time.monotonic())
    assert manager.carrot_serv.navigation_provider == "tmap-legacy"

    naver.close()
    reconnected = socket.create_connection(("127.0.0.1", tcp_port), timeout=1)
    send_json_line(reconnected, canonical_frame("naver", "44444444-4444-4444-8444-444444444444", 1,
                                                route_offset=0.2, ttl_ms=250))
    wait_for(lambda: manager.carrot_serv.navigation_provider == "naver", "reconnected Naver did not own")
    send_json_line(reconnected, canonical_frame("naver", "44444444-4444-4444-8444-444444444444", 2,
                                                active=False, route_offset=None, ttl_ms=250))
    wait_for(lambda: manager.carrot_serv.navigation_provider == "tmap-legacy", "stop did not restore Tmap")

    binary = socket.create_connection(("127.0.0.1", binary_port), timeout=1)
    route_bytes = b"".join(struct.pack("!ff", lon, lat) for lon, lat in ((127.41, 37.51), (127.42, 37.52)))
    binary.sendall(struct.pack("!I", len(route_bytes)) + route_bytes)
    binary.close()
    wait_for(lambda: manager.navi_points and manager.navi_points[0][0] > 127.4, "binary route did not apply")

    # Then: the selected legacy state survives both fallbacks and the binary route joins the same peer.
    restored = manager.carrot_serv.updates[-1]
    assert (restored["nSdiType"], restored["nTBTTurnTypeNext"]) == (4, 13)
    assert_route(manager.navi_points, [(127.41, 37.51), (127.42, 37.52)])
    assert all(key != "NavigationProvider" for key, _value in manager.params.puts)
    print(json.dumps({
      "story": ["tmap", "naver", "tmap-timeout", "naver-reconnect", "tmap-stop", "binary-route"],
      "provider": manager.carrot_serv.navigation_provider,
      "legacy_fields": [restored["nSdiType"], restored["nTBTTurnTypeNext"]],
      "route_points": len(manager.navi_points),
      "ports": {"tcp_default": 7712, "udp": 7706, "binary_default": 7709, "http": 7713},
    }, separators=(",", ":")))
  finally:
    tmap.close()
    naver.close()
    if reconnected is not None:
      reconnected.close()
    stop_server(binary_stop, binary_thread)
    stop_server(tcp_stop, tcp_thread)
