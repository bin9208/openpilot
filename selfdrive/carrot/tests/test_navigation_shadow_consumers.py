from __future__ import annotations

from types import SimpleNamespace

import pytest

from selfdrive.carrot.tests.navigation_fail_neutral_harness import canonical, carrot_serv, manager
from selfdrive.carrot.tests.navigation_shadow_gate_harness import (
  BLINKER_NONE,
  Desire,
  LaneChangeDirection,
  LaneChangeState,
  Messages,
  carrot_candidate_runner,
  controlsd_publish_runner,
  controlsd_state_runner,
  cruise_activation_runner,
  cruise_input_runner,
  load_carrot_functions_method,
  load_desire_helper,
  selfdrived_audio_runner,
)


def navigation_message(*, provider: str, valid: bool, allowed: bool) -> SimpleNamespace:
  return SimpleNamespace(
    provider=provider, naviValid=valid, naviControlAllowed=allowed,
    desiredSpeed=0, nRoadLimitSpeed=5, activeCarrot=9, xDistToTurn=1,
    vTurnSpeed=999, atcType="fork left", trafficState=0,
    carrotCmdIndex=9, carrotCmd="LANECHANGE", carrotArg="LEFT", DT_MDL=0.05,
  )


def missing_flag_message() -> SimpleNamespace:
  message = navigation_message(provider="naver", valid=True, allowed=False)
  del message.naviValid
  del message.naviControlAllowed
  return message


def planner_observation(message: SimpleNamespace) -> tuple[float, bool, int, float, str]:
  planner = SimpleNamespace(
    trafficState_carrot=0, carrot_stay_stop=False, soft_hold_active=0, xState=0,
    traffic_starting_count=0.0, activeCarrot=0, xDistToTurn=0.0, atcType="none",
    add_event=lambda _event: None,
  )
  sm = Messages(
    alive={"carrotMan": True}, carrotMan=message,
    carState=SimpleNamespace(leftBlinker=False),
  )
  v_cruise, atc_active = load_carrot_functions_method()(planner, sm, 60.0, 100.0)
  return v_cruise, atc_active, planner.activeCarrot, planner.xDistToTurn, planner.atcType


@pytest.mark.parametrize(
  "message",
  [
    navigation_message(provider="naver", valid=True, allowed=False),
    navigation_message(provider="naver", valid=False, allowed=True),
    navigation_message(provider="naver", valid=False, allowed=False),
    missing_flag_message(),
  ],
)
def test_carrot_functions_rejects_invalid_stale_or_disabled_navigation(message: SimpleNamespace) -> None:
  # Given/When: each poisoned message crosses the real CarrotPlanner consumer.
  observation = planner_observation(message)

  # Then: cruise, ATC, and internal navigation state equal no navigation.
  assert observation == (100.0, False, 0, 0.0, "none")


def test_carrot_functions_preserves_authorized_tmap_control() -> None:
  # Given/When: authorized legacy Tmap crosses the real CarrotPlanner consumer.
  observation = planner_observation(navigation_message(provider="tmap", valid=True, allowed=True))

  # Then: its established clamp and ATC state remain effective.
  assert observation == (0, True, 9, 1, "fork left")


def controlsd_state_observation(message: SimpleNamespace) -> bool:
  controls = SimpleNamespace(
    sm=Messages(
      alive={"carrotMan": True}, carrotMan=message,
      lateralPlan=SimpleNamespace(useLaneLines=True),
    ),
    params=SimpleNamespace(get_int=lambda _key: 100),
  )
  return controlsd_state_runner()(controls, SimpleNamespace(), SimpleNamespace())


def controlsd_publish_observation(message: SimpleNamespace) -> tuple[float, float, int, float]:
  controls = SimpleNamespace(
    sm=Messages(
      alive={"carrotMan": True}, carrotMan=message,
      carState=SimpleNamespace(vCruiseCluster=100.0, cruiseState=SimpleNamespace(enabled=False)),
      longitudinalPlan=SimpleNamespace(speeds=[]),
    ),
    calibrated_pose=None,
    CP=SimpleNamespace(openpilotLongitudinalControl=False),
  )
  control = SimpleNamespace(
    enabled=True, longActive=True, cruiseControl=SimpleNamespace(),
    hudControl=SimpleNamespace(),
  )
  return controlsd_publish_runner()(controls, control)


def test_controlsd_shadow_matches_no_navigation_mpc_and_hud() -> None:
  # Given: extreme Naver curvature, speed, activation, and turn-distance fields.
  shadow = navigation_message(provider="naver", valid=True, allowed=False)

  # When/Then: both real controlsd slices produce the exact no-nav observables.
  assert controlsd_state_observation(shadow) is False
  assert controlsd_publish_observation(shadow) == (100.0, 100.0 / 3.6, 0, 0.0)


def test_controlsd_preserves_authorized_tmap_mpc_and_hud() -> None:
  # Given: the same fields from authorized legacy Tmap.
  tmap = navigation_message(provider="tmap", valid=True, allowed=True)

  # When/Then: legacy curve, clamp, and HUD fields remain effective.
  assert controlsd_state_observation(tmap) is True
  assert controlsd_publish_observation(tmap) == (0, 0, 9, 1)


def cruise_observation(message: SimpleNamespace) -> tuple[tuple[int, int, int, str, str], list[tuple[int, int, str]]]:
  calls: list[tuple[int, int, str]] = []
  helper = SimpleNamespace(
    nRoadLimitSpeed=30, desiredSpeed=250, carrot_cmd_index=0, carrot_cmd="", carrot_arg="",
    v_ego_kph_set=80, _cruise_control=lambda *args: calls.append(args),
  )
  sm = Messages(alive={"carrotMan": True}, carrotMan=message, carControl=SimpleNamespace())
  inputs = cruise_input_runner()(helper, SimpleNamespace(), sm)
  cruise_activation_runner()(helper)
  return inputs, calls


def test_cruise_shadow_cannot_clamp_or_activate_but_remote_command_survives() -> None:
  # Given/When: poisoned Naver navigation plus a remote Carrot command reaches cruise.
  inputs, calls = cruise_observation(navigation_message(provider="naver", valid=True, allowed=False))

  # Then: navigation is neutral, no desired-speed activation occurs, and the command remains.
  assert inputs == (30, 250, 9, "LANECHANGE", "LEFT")
  assert calls == []


def test_cruise_preserves_authorized_tmap_control() -> None:
  # Given/When: authorized Tmap reaches the same real cruise slices.
  inputs, calls = cruise_observation(navigation_message(provider="tmap", valid=True, allowed=True))

  # Then: legacy road/desired speed still clamps and activates.
  assert inputs[:2] == (5, 0)
  assert calls == [(1, -1, "Cruise on (desired speed)")]


def desire_observation(message: SimpleNamespace) -> tuple[LaneChangeState, LaneChangeDirection, Desire, int]:
  helper = load_desire_helper()()
  helper._update_params_periodic = lambda: None
  helper._make_model_turn_speed = lambda _model: None
  helper._process_sides = lambda _car, _model, _radar: None
  helper._check_desire_state = lambda _model, _car, _maneuver: None
  carstate = SimpleNamespace(
    vEgo=20.0, aEgo=0.0, leftBlinker=False, rightBlinker=False,
    steeringAngleDeg=0.0, steeringPressed=False, steeringTorque=0.0,
  )
  for _ in range(2):
    helper.update(carstate, SimpleNamespace(), True, 1.0, message, SimpleNamespace())
  return helper.lane_change_state, helper.lane_change_direction, helper.desire, helper.carrot_lane_change_count


def test_desire_helper_shadow_cannot_enter_lane_change_fsm() -> None:
  # Given/When: poisoned Naver fork and LANECHANGE requests run for two real FSM ticks.
  observation = desire_observation(navigation_message(provider="naver", valid=True, allowed=False))

  # Then: state and desire exactly match the no-navigation baseline.
  assert observation == (LaneChangeState.off, LaneChangeDirection.none, Desire.none, 0)


def test_desire_helper_preserves_authorized_tmap_lane_change() -> None:
  # Given/When: the same request is authorized for legacy Tmap.
  observation = desire_observation(navigation_message(provider="tmap", valid=True, allowed=True))

  # Then: the established lane-change request still enters preLaneChange.
  assert observation[0] == LaneChangeState.preLaneChange


class EventRecorder:
  def __init__(self) -> None:
    self.values: list[str] = []

  def add(self, value: str) -> None:
    self.values.append(value)


def audio_observation(message: SimpleNamespace) -> tuple[list[str], str]:
  events = EventRecorder()
  daemon = SimpleNamespace(
    sm=Messages(alive={"carrotMan": True}, carrotMan=message),
    events=events, atc_type_last="prepare fork left",
  )
  selfdrived_audio_runner()(daemon)
  return events.values, daemon.atc_type_last


def test_selfdrived_shadow_audio_matches_no_navigation() -> None:
  # Given/When: a poisoned Naver turn transition reaches the real audio block.
  observation = audio_observation(navigation_message(provider="naver", valid=True, allowed=False))

  # Then: it produces no event and retains neutral transition state.
  assert observation == ([], "none")


def test_selfdrived_preserves_authorized_tmap_audio() -> None:
  # Given/When: the same transition is authorized for legacy Tmap.
  observation = audio_observation(navigation_message(provider="tmap", valid=True, allowed=True))

  # Then: the existing lane-change audio transition remains.
  assert observation == (["audioLaneChange"], "fork left")


def candidate_observation(provider: str, valid: bool, allowed: bool):
  serv = carrot_serv()
  serv.set_navigation_observability(provider, valid, 0, allowed)
  serv.autoTurnControl = 2
  serv.turnSpeedControlMode = 2
  serv.mapTurnSpeedFactor = 1.0
  serv.autoCurveSpeedLowerLimit = 5.0
  serv.xDistToTurn = 10.0
  sm = {"modelV2": SimpleNamespace(meta=SimpleNamespace(modelTurnSpeed=250.0))}
  return carrot_candidate_runner()(serv, sm, 0.0, 0.0)


def test_naver_shadow_omits_route_and_vturn_candidates() -> None:
  # Given/When: extreme zero-speed Naver route/turn candidates reach real selection code.
  desired_speed, source, candidates = candidate_observation("naver", True, False)

  # Then: output is neutral and neither candidate can participate.
  assert (desired_speed, source) == (250, "none")
  assert not {"route", "vturn"} & {candidate_source for _, candidate_source in candidates}


def test_authorized_tmap_retains_route_and_vturn_candidates() -> None:
  # Given/When: the same candidates come from authorized legacy Tmap.
  desired_speed, source, candidates = candidate_observation("tmap", True, True)

  # Then: the established candidate selection remains active.
  assert (desired_speed, source) == (5.0, "vturn")
  assert {"route", "vturn"} <= {candidate_source for _, candidate_source in candidates}


def test_authorized_naver_source_and_route_display_state_remain_populated() -> None:
  # Given: a valid Naver state+route envelope from the authorized product path.
  carrot_manager = manager()

  # When: the real manager dispatch method applies it.
  carrot_manager._dispatch_obj(canonical("naver", 1), peer=("192.0.2.12", 7712), received_at=10.0)

  # Then: source state and navRoute coordinates remain available for display/logging.
  assert carrot_manager.carrot_serv.navigation_provider == "naver"
  assert carrot_manager.carrot_serv.navigation_valid is True
  assert carrot_manager.carrot_serv.navigation_control_allowed is True
  assert carrot_manager.navi_points == [(127.01, 37.51), (127.02, 37.52)]
  assert carrot_manager.carrot_serv.updates[-1]["nTBTTurnType"] == 12
