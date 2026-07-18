from __future__ import annotations

from types import SimpleNamespace

import pytest

from selfdrive.carrot.tests.navigation_fail_neutral_harness import canonical, manager
from selfdrive.carrot.tests.navigation_shadow_gate_harness import (
  LaneChangeState,
  Messages,
  load_carrot_functions_method,
  load_desire_helper,
)


def _navigation_message(*, desired_speed: int = 30) -> SimpleNamespace:
  return SimpleNamespace(
    provider="naver", naviValid=True, naviControlAllowed=True,
    desiredSpeed=desired_speed, activeCarrot=9, xDistToTurn=1,
    atcType="fork left", trafficState=0,
    carrotCmdIndex=9, carrotCmd="LANECHANGE", carrotArg="LEFT",
  )


def _car_state(*, left: bool = False, right: bool = False) -> SimpleNamespace:
  return SimpleNamespace(
    vEgo=20.0, aEgo=0.0, leftBlinker=left, rightBlinker=right,
    leftBlindspot=False, rightBlindspot=False,
    steeringAngleDeg=0.0, steeringPressed=False, steeringTorque=0.0,
  )


def _lane_helper():
  helper = load_desire_helper()()
  helper._update_params_periodic = lambda: None
  helper._make_model_turn_speed = lambda _model: None
  helper._process_sides = lambda _car, _model, _radar: None
  helper._check_desire_state = lambda _model, _car, _maneuver: None
  helper.laneChangeBsd = 1
  helper.laneLineCheck = 2
  for side in (helper.left, helper.right):
    side.lane_change_available = True
    side.lane_change_available_geom = True
    side.lane_line_info_mod = 0
    side.side_object_detected = False
    side.bsd_hold_counter = 0
  return helper


def _tick(helper, car_state: SimpleNamespace, message: SimpleNamespace, *, lateral_active: bool = True) -> None:
  helper.update(car_state, SimpleNamespace(), lateral_active, 1.0, message, SimpleNamespace())


def _starting_lane_change() -> tuple[object, SimpleNamespace, SimpleNamespace]:
  helper = _lane_helper()
  message = _navigation_message()
  car_state = _car_state()
  _tick(helper, car_state, message)
  _tick(helper, car_state, message)
  assert helper.lane_change_state == LaneChangeState.preLaneChange
  car_state.leftBlinker = True
  _tick(helper, car_state, message)
  assert helper.lane_change_state == LaneChangeState.laneChangeStarting
  return helper, car_state, message


def test_fresh_canonical_naver_lease_is_authorized_then_expiry_clears_authority() -> None:
  # Given: the approved schema-v1 Naver product path on the local vehicle network.
  carrot_manager = manager()

  # When: one valid canonical frame is accepted, then its monotonic lease expires.
  carrot_manager._dispatch_obj(canonical("naver", 1), peer=("192.0.2.20", 7712), received_at=10.0)
  fresh = SimpleNamespace()
  carrot_manager.carrot_serv._fill_navigation_message(fresh, 35, "atc")
  carrot_manager._expire_navigation(11.0)
  expired = SimpleNamespace()
  carrot_manager.carrot_serv._fill_navigation_message(expired, 35, "atc")

  # Then: only the fresh validated lease carries authority and expiry is neutral.
  assert (fresh.provider, fresh.naviValid, fresh.naviControlAllowed, fresh.desiredSpeed) == (
    "naver", True, True, 35,
  )
  assert (expired.provider, expired.naviValid, expired.naviControlAllowed, expired.desiredSpeed) == (
    "none", False, False, 250,
  )


@pytest.mark.parametrize(
  ("desired_speed", "expected_target"),
  [(30, 30.0), (140, 100.0)],
  ids=["lower-target", "cannot-raise-target"],
)
def test_authorized_naver_speed_can_only_reduce_existing_target(
  desired_speed: int, expected_target: float,
) -> None:
  # Given: a 100 km/h existing cruise target and a fresh authorized Naver value.
  planner = SimpleNamespace(
    trafficState_carrot=0, carrot_stay_stop=False, soft_hold_active=0, xState=0,
    traffic_starting_count=0.0, activeCarrot=0, xDistToTurn=0.0, atcType="none",
    add_event=lambda _event: None,
  )
  message = _navigation_message(desired_speed=desired_speed)
  messages = Messages(
    alive={"carrotMan": True}, carrotMan=message,
    carState=SimpleNamespace(leftBlinker=False),
  )

  # When: the real production planner consumer applies the navigation target.
  target, _atc_active = load_carrot_functions_method()(planner, messages, 60.0, 100.0)

  # Then: the target is the lower value, never a navigation-requested increase.
  assert target == expected_target


def test_naver_request_only_prearms_but_same_direction_physical_confirmation_starts() -> None:
  # Given: fresh Naver lane guidance with clear dashed geometry and no hazards.
  request_only = _lane_helper()
  request_message = _navigation_message()
  no_blinker = _car_state()

  # When: the request runs without a physical blinker for multiple FSM ticks.
  for _ in range(4):
    _tick(request_only, no_blinker, request_message)

  # Then: it remains display/pre-arm only; a separately confirmed case may start.
  assert request_only.lane_change_state == LaneChangeState.preLaneChange
  confirmed, _car_state_value, _message = _starting_lane_change()
  assert confirmed.lane_change_state == LaneChangeState.laneChangeStarting


def test_wrong_side_physical_confirmation_cancels_naver_request() -> None:
  # Given: a pre-armed left Naver request.
  helper = _lane_helper()
  message = _navigation_message()
  car_state = _car_state()
  _tick(helper, car_state, message)
  _tick(helper, car_state, message)
  assert helper.lane_change_state == LaneChangeState.preLaneChange

  # When: the driver physically confirms the opposite side.
  car_state.rightBlinker = True
  _tick(helper, car_state, message)

  # Then: the navigation request is canceled, not redirected into a right change.
  assert helper.lane_change_state == LaneChangeState.off


@pytest.mark.parametrize(
  "hazard",
  ["expiry", "opposite_blinker", "opposite_torque", "bsd", "object", "solid", "low_speed", "inactive_lateral"],
)
def test_naver_lane_change_aborts_when_a_safety_gate_fails(hazard: str) -> None:
  # Given: an already-starting, physically confirmed Naver lane change.
  helper, car_state, message = _starting_lane_change()
  lateral_active = True

  # When: one required safety input fails on the next control tick.
  if hazard == "expiry":
    message.naviValid = False
    message.naviControlAllowed = False
  elif hazard == "opposite_blinker":
    car_state.leftBlinker = False
    car_state.rightBlinker = True
  elif hazard == "opposite_torque":
    car_state.steeringPressed = True
    car_state.steeringTorque = -1.0
  elif hazard == "bsd":
    car_state.leftBlindspot = True
    helper.left.bsd_hold_counter = 1
  elif hazard == "object":
    helper.left.side_object_detected = True
  elif hazard == "solid":
    helper.left.lane_change_available = False
    helper.left.lane_change_available_geom = False
    helper.left.lane_line_info_mod = 1
  elif hazard == "low_speed":
    car_state.vEgo = 5.0
  elif hazard == "inactive_lateral":
    lateral_active = False

  _tick(helper, car_state, message, lateral_active=lateral_active)

  # Then: no Naver-initiated lateral actuation survives the failed gate.
  assert helper.lane_change_state == LaneChangeState.off
