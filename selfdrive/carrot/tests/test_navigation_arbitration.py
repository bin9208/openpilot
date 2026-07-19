from __future__ import annotations

import copy
from typing import Any

import pytest

from selfdrive.carrot.navigation_protocol import ProtocolError, Transport
from selfdrive.carrot.tests.navigation_fail_neutral_harness import canonical, manager as make_manager


TMAP_PEER = ("192.0.2.10", 7712)
NAVER_PEER = ("192.0.2.20", 7712)
NEXT_TMAP_SESSION = "33333333-3333-4333-8333-333333333333"


def legacy(sequence: int, *, route_offset: float = 0.0) -> dict[str, Any]:
  return {
    "timestamp_ms": sequence * 1_000,
    "rgdata": {
      "nRoadLimitSpeed": 80,
      "nSdiType": 4,
      "nSdiSpeedLimit": 70,
      "nTBTTurnType": 12,
      "nTBTDist": 120,
      "nTBTTurnTypeNext": 13,
      "nTBTDistNext": 320,
      "nGoPosDist": 5_000,
      "nGoPosTime": 600,
      "vpPosPointLon": 127.01,
      "vpPosPointLat": 37.51,
    },
    "vrtx": [
      {"x": 127.01 + route_offset, "y": 37.51},
      {"x": 127.02 + route_offset, "y": 37.52},
    ],
  }


def canonical_frame(source: str, session_id: str, sequence: int, *, active: bool = True,
                    route_offset: float | None = 0.0, ttl_ms: int = 1_000,
                    status: str | None = None) -> dict[str, Any]:
  state_status = status or ("guiding" if active else "stopped")
  maneuver = "arrive" if state_status == "arrived" else "left" if active else "straight"
  frame: dict[str, Any] = {
    "source": source,
    "schema_version": 1,
    "session_id": session_id,
    "sequence": sequence,
    "timestamp_ms": sequence * 1_000,
    "navigation_active": active,
    "ttl_ms": ttl_ms,
    "state": {
      "status": state_status,
      "maneuver": maneuver,
      "maneuver_distance_m": 120,
      "remaining_distance_m": 5_000,
      "remaining_time_s": 600,
      "road_limit_kph": 80,
      "position": {"longitude": 127.01, "latitude": 37.51},
    },
  }
  if route_offset is not None:
    frame["route"] = [
      {"longitude": 127.01 + route_offset, "latitude": 37.51},
      {"longitude": 127.02 + route_offset, "latitude": 37.52},
    ]
  return frame


def assert_route(actual: list[tuple[float, float]], expected: list[tuple[float, float]]) -> None:
  assert [point[0] for point in actual] == pytest.approx([point[0] for point in expected])
  assert [point[1] for point in actual] == pytest.approx([point[1] for point in expected])


@pytest.mark.parametrize(
  ("source", "peer", "control_allowed"),
  [("tmap", TMAP_PEER, True), ("naver", NAVER_PEER, True)],
)
def test_canonical_provider_alone_applies_its_state_route_and_control_gate(
  source: str, peer: tuple[str, int], control_allowed: bool,
) -> None:
  # Given: no previous navigation owner.
  manager = make_manager()

  # When: one canonical provider starts guidance.
  manager._dispatch_obj(canonical(source, 1), peer=peer, received_at=10.0)

  # Then: its normalized state and matching route become the only selected snapshot.
  assert manager.carrot_serv.navigation_provider == source
  assert manager.carrot_serv.navigation_control_allowed is control_allowed
  assert manager.carrot_serv.updates[-1]["nTBTTurnType"] == 12
  assert manager.navi_points == [(127.01, 37.51), (127.02, 37.52)]


@pytest.mark.parametrize(
  ("safety_kind", "sdi_type", "plus_type", "section", "expected_speed"),
  [
    ("fixed_camera", 1, -1, 0, 70),
    ("mobile_camera", 7, -1, 0, 70),
    ("section_camera", 2, -1, 1, 70),
    ("bump", -1, 22, 0, 0),
  ],
)
def test_naver_extension_maps_next_tbt_and_safety_to_control_fields(
  safety_kind: str, sdi_type: int, plus_type: int, section: int, expected_speed: int,
) -> None:
  manager = make_manager()
  frame = canonical("naver", 1)
  frame["naver"] = {
    "next_maneuver": "ramp_right",
    "next_maneuver_distance_m": 780,
    "safety_kind": safety_kind,
    "safety_distance_m": 180,
    "safety_speed_kph": 70,
    "destination": {"longitude": 127.02, "latitude": 37.52},
  }

  manager._dispatch_obj(frame, peer=NAVER_PEER, received_at=10.0)

  state = manager.carrot_serv.updates[-1]
  assert (state["nTBTTurnTypeNext"], state["nTBTDistNext"]) == (101, 780)
  assert (state["nSdiType"], state["nSdiPlusType"], state["nSdiSection"]) == (
    sdi_type, plus_type, section,
  )
  assert state["nSdiSpeedLimit"] == expected_speed
  assert state["nSdiDist"] == (180 if sdi_type >= 0 else 0)
  assert state["nSdiPlusDist"] == (180 if plus_type == 22 else 0)


def test_canonical_tmap_preserves_pre_naver_normalized_payload() -> None:
  manager = make_manager()

  manager._dispatch_obj(canonical("tmap", 1), peer=TMAP_PEER, received_at=10.0)

  assert manager.carrot_serv.updates[-1] == {
    "nRoadLimitSpeed": 80,
    "nSdiType": -1,
    "nSdiBlockType": -1,
    "nSdiPlusType": -1,
    "nSdiPlusBlockType": -1,
    "nTBTTurnType": 12,
    "nTBTDist": 120,
    "nTBTTurnTypeNext": -1,
    "nTBTDistNext": 0,
    "nGoPosDist": 5_000,
    "nGoPosTime": 600,
    "vpPosPointLon": 127.01,
    "vpPosPointLat": 37.51,
  }


def test_legacy_tmap_alone_preserves_all_existing_rgdata_fields() -> None:
  manager = make_manager()
  frame = legacy(1)

  manager._dispatch_obj(frame, peer=TMAP_PEER, received_at=10.0)

  assert manager.carrot_serv.updates[-1] == frame["rgdata"]
  assert manager.carrot_serv.navigation_provider == "tmap-legacy"
  assert manager.navi_points == [(127.01, 37.51), (127.02, 37.52)]


def test_tmap_to_naver_timeout_returns_to_fresh_tmap_with_one_route_snapshot() -> None:
  manager = make_manager()
  tmap = canonical_frame("tmap", "11111111-1111-4111-8111-111111111111", 1, ttl_ms=2_000)
  naver = canonical_frame("naver", "22222222-2222-4222-8222-222222222222", 1,
                          route_offset=0.1, ttl_ms=250)
  manager._dispatch_obj(tmap, peer=TMAP_PEER, received_at=10.0)
  manager._dispatch_obj(naver, peer=NAVER_PEER, received_at=10.1)

  manager._expire_navigation(10.351)

  assert manager.carrot_serv.navigation_provider == "tmap"
  assert manager.navi_points == [(127.01, 37.51), (127.02, 37.52)]
  routes = [message.navRoute.coordinates for service, message in manager.pm.messages if service == "navRoute"]
  assert [len(route) for route in routes] == [2, 0, 2, 0, 2]


def test_simultaneous_idle_sessions_do_not_select_or_apply_a_provider() -> None:
  manager = make_manager()
  tmap_idle = canonical_frame("tmap", "11111111-1111-4111-8111-111111111111", 1,
                              active=False, route_offset=None, status="idle")
  naver_idle = canonical_frame("naver", "22222222-2222-4222-8222-222222222222", 1,
                               active=False, route_offset=None, status="idle")

  manager._dispatch_obj(tmap_idle, peer=TMAP_PEER, received_at=10.0)
  manager._dispatch_obj(naver_idle, peer=NAVER_PEER, received_at=10.1)

  assert manager._navigation_mux.current is None
  assert manager.carrot_serv.updates == []
  assert manager.navi_points == []
  assert manager.params.puts == []


def test_legacy_route_before_state_is_buffered_then_applied_atomically() -> None:
  manager = make_manager()
  route = legacy(1)["vrtx"]

  manager._dispatch_obj({"timestamp_ms": 1_000, "vrtx": route}, peer=TMAP_PEER, received_at=10.0)
  assert manager.carrot_serv.updates == [] and manager.navi_points == []
  manager._dispatch_obj({"timestamp_ms": 1_001, "rgdata": legacy(2)["rgdata"]},
                        peer=TMAP_PEER, received_at=10.1)

  assert manager.carrot_serv.updates[-1]["nSdiType"] == 4
  assert manager.navi_points == [(127.01, 37.51), (127.02, 37.52)]


def test_binary_route_before_udp_state_is_joined_by_peer_without_route_only_activation() -> None:
  manager = make_manager()
  route = legacy(1, route_offset=0.2)["vrtx"]

  manager._dispatch_obj({"timestamp_ms": 1_000, "vrtx": route}, peer=TMAP_PEER,
                        transport=Transport.BINARY, received_at=10.0)
  assert manager.carrot_serv.updates == [] and manager.navi_points == []
  manager._dispatch_obj({"timestamp_ms": 1_001, "rgdata": legacy(2)["rgdata"]}, peer=TMAP_PEER,
                        transport=Transport.UDP, received_at=10.1)

  assert manager.carrot_serv.navigation_provider == "tmap-legacy"
  assert_route(manager.navi_points, [(127.21, 37.51), (127.22, 37.52)])


def test_binary_route_after_udp_state_updates_only_the_same_peer_candidate() -> None:
  manager = make_manager()
  manager._dispatch_obj({"timestamp_ms": 1_000, "rgdata": legacy(1)["rgdata"]}, peer=TMAP_PEER,
                        transport=Transport.UDP, received_at=10.0)

  manager._dispatch_obj({"timestamp_ms": 2_000, "vrtx": legacy(2, route_offset=0.3)["vrtx"]},
                        peer=TMAP_PEER, transport=Transport.BINARY, received_at=10.1)

  assert manager.carrot_serv.navigation_provider == "tmap-legacy"
  assert manager.navi_points == [(127.31, 37.51), (127.32, 37.52)]
  assert len(manager.carrot_serv.updates) == 1


def test_naver_stop_restores_the_selected_legacy_candidate_not_the_stopping_packet() -> None:
  # Given: legacy Tmap owns fields outside the canonical schema before Naver takes over.
  manager = make_manager()
  tmap = legacy(1)
  manager._dispatch_obj(tmap, peer=TMAP_PEER, received_at=10.0)
  manager._dispatch_obj(canonical("naver", 1, route_offset=0.1), peer=NAVER_PEER, received_at=10.1)

  # When: Naver stops while the Tmap candidate is still fresh.
  manager._dispatch_obj(canonical("naver", 2, active=False), peer=NAVER_PEER, received_at=10.2)

  # Then: state and route are restored from Tmap's accepted candidate as one selection.
  restored = manager.carrot_serv.updates[-1]
  assert restored["nSdiType"] == 4
  assert restored["nSdiSpeedLimit"] == 70
  assert restored["nTBTTurnTypeNext"] == 13
  assert restored["nTBTDistNext"] == 320
  assert manager.navi_points == [(127.01, 37.51), (127.02, 37.52)]
  assert manager.carrot_serv.navigation_provider == "tmap-legacy"


def test_arrival_also_releases_owner_to_the_fresh_candidate() -> None:
  manager = make_manager()
  manager._dispatch_obj(canonical("tmap", 1), peer=TMAP_PEER, received_at=10.0)
  manager._dispatch_obj(canonical("naver", 1, route_offset=0.1), peer=NAVER_PEER, received_at=10.1)
  arrived = canonical_frame("naver", "22222222-2222-4222-8222-222222222222", 2,
                            active=False, route_offset=None, status="arrived")

  manager._dispatch_obj(arrived, peer=NAVER_PEER, received_at=10.2)

  assert manager.carrot_serv.navigation_provider == "tmap"
  assert manager.navi_points == [(127.01, 37.51), (127.02, 37.52)]


def test_reconnect_same_peer_ip_keeps_legacy_session_and_route() -> None:
  manager = make_manager()
  manager._dispatch_obj(legacy(1), peer=(TMAP_PEER[0], 40_001), received_at=10.0)
  first_session = manager._navigation_mux.current.session_id
  state_only = {"timestamp_ms": 2_000, "rgdata": legacy(2)["rgdata"]}

  manager._dispatch_obj(state_only, peer=(TMAP_PEER[0], 40_002), received_at=10.2)

  assert manager._navigation_mux.current.session_id == first_session
  assert manager.navi_points == [(127.01, 37.51), (127.02, 37.52)]


def test_replay_foreign_route_lingering_candidate_and_conflicting_sessions_do_not_flicker() -> None:
  manager = make_manager()
  initial = canonical_frame("tmap", "11111111-1111-4111-8111-111111111111", 1)
  manager._dispatch_obj(initial, peer=TMAP_PEER, received_at=10.0)
  before = (copy.deepcopy(manager.carrot_serv.updates), list(manager.navi_points), len(manager.pm.messages))

  with pytest.raises(ProtocolError):
    manager._dispatch_obj(initial, peer=TMAP_PEER, received_at=10.1)
  manager._dispatch_obj({"timestamp_ms": 2_000, "vrtx": legacy(2, route_offset=0.4)["vrtx"]},
                        peer=("192.0.2.99", 7712), received_at=10.2)
  assert (manager.carrot_serv.updates, manager.navi_points, len(manager.pm.messages)) == before

  replacement = canonical_frame("tmap", NEXT_TMAP_SESSION, 1, route_offset=0.5)
  manager._dispatch_obj(replacement, peer=(TMAP_PEER[0], 40_003), received_at=10.3)
  with pytest.raises(ProtocolError):
    manager._dispatch_obj(canonical_frame("tmap", "11111111-1111-4111-8111-111111111111", 2),
                          peer=TMAP_PEER, received_at=10.4)

  assert str(manager._navigation_mux.current.session_id) == NEXT_TMAP_SESSION
  assert manager.carrot_serv.navigation_provider == "tmap"
  assert manager.navi_points == [(127.51, 37.51), (127.52, 37.52)]


def test_lingering_tmap_candidate_cannot_flicker_naver_but_is_selected_after_timeout() -> None:
  manager = make_manager()
  manager._dispatch_obj(legacy(1), peer=TMAP_PEER, received_at=10.0)
  manager._dispatch_obj(canonical_frame("naver", "22222222-2222-4222-8222-222222222222", 1,
                                        route_offset=0.1, ttl_ms=250),
                        peer=NAVER_PEER, received_at=10.1)

  manager._dispatch_obj({"timestamp_ms": 2_000, "vrtx": legacy(2, route_offset=0.2)["vrtx"]},
                        peer=TMAP_PEER, received_at=10.2)
  assert manager.carrot_serv.navigation_provider == "naver"
  assert manager.navi_points == [(127.11, 37.51), (127.11999999999999, 37.52)]
  manager._expire_navigation(10.351)

  assert manager.carrot_serv.navigation_provider == "tmap-legacy"
  assert_route(manager.navi_points, [(127.21, 37.51), (127.22, 37.52)])
  assert all(key != "NavigationProvider" for key, _value in manager.params.puts)
