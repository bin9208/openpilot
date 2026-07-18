from __future__ import annotations

import copy
import runpy
import threading
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from selfdrive.carrot.navigation_protocol import ProtocolError
from selfdrive.carrot.tests.navigation_fail_neutral_harness import (
  CarrotMan,
  CountingList,
  PubRecorder,
  canonical,
  manager as make_manager,
)


def test_legacy_route_and_state_dispatch_preserves_todo7_peer_context() -> None:
  # Given: the established Todo 7 dispatcher and one combined legacy frame.
  manager = CarrotMan.__new__(CarrotMan)
  manager._rgdata_ts_lock = threading.Lock()
  manager._last_rgdata_timestamp_ms = 0
  manager.carrot_serv = SimpleNamespace(set_navigation_observability=lambda *_args: None)
  calls: list[tuple[str, Any]] = []
  manager.handle_route = lambda route: calls.append(("route", route))
  manager.handle_carrot_state = lambda state: calls.append(("state", state))
  manager.handle_traffic_light = lambda state: calls.append(("traffic", state))
  manager.handle_unknown = lambda value: calls.append(("unknown", value))
  peer = ("192.0.2.10", 7712)
  frame = {
    "timestamp_ms": 1,
    "vrtx": [{"x": 127.01, "y": 37.51}],
    "rgdata": {
      "nRoadLimitSpeed": 80,
      "nTBTTurnType": 12,
      "nTBTDist": 120,
      "nGoPosDist": 5_000,
      "nGoPosTime": 600,
      "vpPosPointLon": 127.01,
      "vpPosPointLat": 37.51,
    },
  }

  # When: the frame crosses the existing shared TCP/HTTP dispatch seam.
  manager._dispatch_obj(frame, peer=peer)

  # Then: both legacy observables are delivered once and peer context survives.
  assert [name for name, _value in calls] == ["route", "state"]
  assert calls[0][1] == frame["vrtx"]
  assert calls[1][1] == frame["rgdata"]
  assert manager._navigation_peer == peer


def test_schema_ordinals_types_and_carrot_service_frequency_are_exact() -> None:
  # Given: the owned custom schema and service registry.
  root = Path(__file__).resolve().parents[3]
  schema = (root / "cereal" / "custom.capnp").read_text(encoding="utf-8")

  # When: their machine-consumed declarations are inspected/imported.
  services = runpy.run_path(root / "cereal" / "services.py")["SERVICE_LIST"]

  # Then: Todo 8 observability and 20 Hz liveness are exact.
  assert "provider @29 :Text;" in schema
  assert "naviValid @30 :Bool;" in schema
  assert "naviAgeMs @31 :UInt32;" in schema
  assert "naviControlAllowed @32 :Bool;" in schema
  assert services["carrotMan"].frequency == 20.0


@pytest.mark.parametrize(("source", "control_allowed"), [("tmap", True), ("naver", True)])
def test_valid_provider_frame_publishes_provider_valid_age_and_control(source: str, control_allowed: bool) -> None:
  # Given: a fresh provider frame with state and route at the real mux boundary.
  manager = make_manager()

  # When: it is accepted and observed 200 ms later.
  manager._dispatch_obj(canonical(source, 1), peer=("192.0.2.10", 7712), received_at=10.0)
  manager._expire_navigation(10.2)
  message = SimpleNamespace()
  manager.carrot_serv._fill_navigation_message(message, 35, "atc")

  # Then: both explicitly authorized canonical providers publish bounded telemetry.
  assert (message.provider, message.naviValid, message.naviAgeMs, message.naviControlAllowed) == (
    source, True, 199, control_allowed,
  )


def test_accepted_state_and_route_are_applied_together_after_mux_acceptance() -> None:
  # Given: a manager whose state sink snapshots the installed route.
  manager = make_manager()
  route_snapshots: list[tuple[tuple[float, float], ...]] = []
  manager.carrot_serv.update = lambda payload: (
    manager.carrot_serv.updates.append(copy.deepcopy(payload)), route_snapshots.append(tuple(manager.navi_points))
  )

  # When: a valid Tmap provider envelope crosses the shared ingress seam.
  manager._dispatch_obj(canonical("tmap", 1), peer=("192.0.2.10", 7712), received_at=10.0)

  # Then: the mapped state sees the matching route, with one real navRoute publication.
  assert manager.carrot_serv.updates[0]["nTBTTurnType"] == 12
  assert manager.carrot_serv.updates[0]["nGoPosDist"] == 5_000
  assert route_snapshots == [((127.01, 37.51), (127.02, 37.52))]
  route_messages = [message.navRoute.coordinates for service, message in manager.pm.messages if service == "navRoute"]
  assert route_messages == [[{"latitude": 37.51, "longitude": 127.01}, {"latitude": 37.52, "longitude": 127.02}]]


def _assert_neutral(manager: CarrotMan, counted_route: CountingList) -> None:
  serv = manager.carrot_serv
  message = SimpleNamespace()
  serv._fill_navigation_message(message, 20, "route")
  assert (message.activeCarrot, message.desiredSpeed, message.desiredSource, message.atcType) == (0, 250, "none", "none")
  assert (message.xSpdType, message.xTurnInfo, message.provider, message.naviValid, message.naviAgeMs,
          message.naviControlAllowed) == (-1, -1, "none", False, 0, False)
  assert (serv.nSdiType, serv.nSdiBlockType, serv.nSdiPlusType, serv.nSdiPlusBlockType) == (-1, -1, -1, -1)
  assert (serv.nTBTTurnType, serv.nTBTTurnTypeNext, serv.navType, serv.navTypeNext) == (-1, -1, "invalid", "invalid")
  assert manager.navi_points == [] and manager.navi_points_start_index == 0
  assert not manager.navi_points_active and not manager.navd_active
  assert counted_route.clear_calls == 1
  assert manager.params.removes == ["NavDestination"]
  empty_routes = [message for service, message in manager.pm.messages
                  if service == "navRoute" and message.navRoute.coordinates == []]
  invalid_instructions = [message for service, message in manager.pm.messages
                          if service == "navInstructionCarrot" and not message.valid]
  assert len(empty_routes) == len(invalid_instructions) == 1


@pytest.mark.parametrize("trigger", ["stop", "expiry"], ids=["explicit-stop", "app-kill-network-stall-lease-expiry"])
def test_navigation_loss_is_exactly_once_fail_neutral(trigger: str) -> None:
  # Given: one active route with non-neutral safety, TBT, destination, and localization state.
  manager = make_manager()
  manager._dispatch_obj(canonical("tmap", 1), peer=("192.0.2.10", 7712), received_at=10.0)
  counted_route = CountingList(manager.navi_points)
  manager.navi_points = counted_route

  # When: the sender explicitly stops or its lease expires, followed by a duplicate timeout tick.
  if trigger == "stop":
    manager._dispatch_obj(canonical("tmap", 2, active=False), peer=("192.0.2.10", 7712), received_at=10.2)
  else:
    manager._expire_navigation(11.0)
  manager._expire_navigation(12.0)

  # Then: every neutral field and side effect is exact and idempotent.
  _assert_neutral(manager, counted_route)


def test_provider_switch_neutralizes_old_route_once_then_applies_new_route() -> None:
  # Given: Tmap owns one installed route.
  manager = make_manager()
  manager._dispatch_obj(canonical("tmap", 1), peer=("192.0.2.10", 7712), received_at=10.0)
  counted_route = CountingList(manager.navi_points)
  manager.navi_points = counted_route

  # When: newly guiding Naver wins, then its identical frame is replayed.
  manager._dispatch_obj(canonical("naver", 1, route_offset=0.1), peer=("192.0.2.11", 7712), received_at=10.1)
  side_effect_counts = (len(manager.params.removes), len(manager.pm.messages), counted_route.clear_calls)
  with pytest.raises(ProtocolError):
    manager._dispatch_obj(canonical("naver", 1, route_offset=0.2), peer=("192.0.2.11", 7712), received_at=10.2)

  # Then: one empty transition separates routes and replay mutates nothing.
  routes = [message.navRoute.coordinates for service, message in manager.pm.messages if service == "navRoute"]
  assert [len(route) for route in routes] == [2, 0, 2]
  assert manager.navi_points == [(127.11, 37.51), (127.11999999999999, 37.52)]
  assert manager.params.removes == ["NavDestination"] and counted_route.clear_calls == 1
  assert (len(manager.params.removes), len(manager.pm.messages), counted_route.clear_calls) == side_effect_counts
  assert (manager.carrot_serv.navigation_provider, manager.carrot_serv.navigation_control_allowed) == ("naver", True)


def test_malformed_active_source_cannot_mutate_then_expiry_neutralizes_once() -> None:
  # Given: a valid active source and captured state/route side-effect counts.
  manager = make_manager()
  manager._dispatch_obj(canonical("tmap", 1), peer=("192.0.2.10", 7712), received_at=10.0)
  before = (copy.deepcopy(manager.carrot_serv.updates), list(manager.navi_points), len(manager.pm.messages), len(manager.params.puts))
  malformed = canonical("tmap", 2, route_offset=0.2)
  malformed["state"]["maneuver"] = "unknown"

  # When: malformed active-source input is rejected and the original lease later expires twice.
  with pytest.raises(ProtocolError):
    manager._dispatch_obj(malformed, peer=("192.0.2.10", 7712), received_at=10.2)
  assert (manager.carrot_serv.updates, manager.navi_points, len(manager.pm.messages), len(manager.params.puts)) == before
  counted_route = CountingList(manager.navi_points)
  manager.navi_points = counted_route
  manager._expire_navigation(11.0)
  manager._expire_navigation(12.0)

  # Then: rejection was mutation-free and normal expiry performs one complete neutral transition.
  _assert_neutral(manager, counted_route)


def test_post_loss_publish_tick_cannot_rehydrate_fallback_nav_instruction() -> None:
  # Given: a previously accepted session that is now neutral, while navd fallback remains alive.
  serv = make_manager().carrot_serv
  serv.set_navigation_observability("tmap", True, 0, True)
  serv.neutralize_navigation()
  for field, value in {
    "autoRoadSpeedLimitOffset": -1, "autoNaviSpeedBumpTime": 1.0, "autoNaviSpeedCtrlEnd": 1.0,
    "autoNaviSpeedDecelRate": 1.0, "autoTurnControl": 0, "turnSpeedControlMode": 0,
    "autoCurveSpeedLowerLimit": 20, "mapTurnSpeedFactor": 1.0, "autoNaviCountDownMode": 0,
    "carrotCmdIndex": 0, "carrotCmd": "", "carrotArg": "", "traffic_state": 0,
  }.items():
    setattr(serv, field, value)
  serv.update_params = lambda: None
  serv._update_gps = lambda *_args: 0.0
  serv.update_nav_instruction = lambda _sm: None
  serv.update_auto_turn = lambda *_args: (250, "none", 250, 0)
  serv._update_cmd = lambda: None
  serv._get_sdi_descr = lambda _kind: ""
  fallback = SimpleNamespace(marker="navd-fallback")

  class _SubMaster:
    alive = {"carState": False, "selfdriveState": False, "navInstruction": True}
    valid = {"navInstruction": True}

    def __getitem__(self, service: str) -> SimpleNamespace:
      if service == "modelV2":
        return SimpleNamespace(meta=SimpleNamespace(modelTurnSpeed=250))
      return fallback

  publisher = PubRecorder()

  # When: the real CarrotServ publication method runs on the same loss tick.
  serv.update_navi("", _SubMaster(), publisher, 250, [], [], 300, "gps")

  # Then: carrotMan is neutral and the emitted instruction stays invalid/empty.
  carrot = next(message.carrotMan for service, message in publisher.messages if service == "carrotMan")
  instruction = next(message for service, message in publisher.messages if service == "navInstructionCarrot")
  assert (carrot.activeCarrot, carrot.desiredSpeed, carrot.desiredSource, carrot.atcType) == (0, 250, "none", "none")
  assert (serv.desired_speed, serv.desired_source, serv.atcType) == (250, "none", "none")
  assert not instruction.valid
  assert vars(instruction.navInstructionCarrot) == {}
