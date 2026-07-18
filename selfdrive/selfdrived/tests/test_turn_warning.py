from types import SimpleNamespace

from cereal import car, log

from openpilot.selfdrive.selfdrived.events import EVENTS, ET
from openpilot.selfdrive.selfdrived.turn_warning import TurnPathWarning, update_turn_path_warning


EventName = log.OnroadEvent.EventName
Desire = log.Desire
LaneChangeState = log.LaneChangeState
VisualAlert = car.CarControl.HUDControl.VisualAlert
AudibleAlert = car.CarControl.HUDControl.AudibleAlert


def update_warning(warning: TurnPathWarning, *, lat_active: bool = True, v_ego: float = 10.0,
                   left_blinker: bool = False, right_blinker: bool = False,
                   advisory_valid: bool = True, advisory_turn_info: int = 1,
                   advisory_turn_distance: float = 50.0, model_desire: int = Desire.none,
                   lane_change_state: int = LaneChangeState.off) -> bool:
  return warning.update(
    lat_active=lat_active,
    v_ego=v_ego,
    left_blinker=left_blinker,
    right_blinker=right_blinker,
    advisory_valid=advisory_valid,
    advisory_turn_info=advisory_turn_info,
    advisory_turn_distance=advisory_turn_distance,
    model_desire=model_desire,
    lane_change_state=lane_change_state,
  )


def test_navigation_turn_warns_after_two_confirmed_frames() -> None:
  warning = TurnPathWarning()

  assert not update_warning(warning)
  assert update_warning(warning)


def test_matching_model_turn_desire_suppresses_warning() -> None:
  warning = TurnPathWarning()

  assert not update_warning(warning, model_desire=Desire.turnLeft)
  assert not update_warning(warning, model_desire=Desire.turnLeft)
  assert update_warning(warning, model_desire=Desire.none)


def test_roundabout_warns_without_reliable_matching_desire() -> None:
  warning = TurnPathWarning()

  assert not update_warning(warning, advisory_turn_info=5, model_desire=Desire.turnLeft)
  assert update_warning(warning, advisory_turn_info=5, model_desire=Desire.turnLeft)


def test_navigation_warning_only_uses_three_to_eight_second_window() -> None:
  warning = TurnPathWarning()

  for _ in range(3):
    assert not update_warning(warning, advisory_turn_distance=90.0)

  assert not update_warning(warning, advisory_turn_distance=50.0)
  assert update_warning(warning, advisory_turn_distance=50.0)

  assert not update_warning(warning, advisory_turn_distance=20.0)


def test_navigation_warning_tolerates_one_missing_advisory_frame() -> None:
  warning = TurnPathWarning()
  update_warning(warning)
  assert update_warning(warning)

  assert update_warning(warning, advisory_valid=False, advisory_turn_info=-1, advisory_turn_distance=0.0)
  assert not update_warning(warning, advisory_valid=False, advisory_turn_info=-1, advisory_turn_distance=0.0)


def test_inactive_lateral_control_clears_warning_state() -> None:
  warning = TurnPathWarning()
  update_warning(warning)
  assert update_warning(warning)

  assert not update_warning(warning, lat_active=False)
  assert not update_warning(warning)


def test_low_speed_physical_blinker_fallback_requires_half_second() -> None:
  warning = TurnPathWarning()

  for _ in range(49):
    assert not update_warning(
      warning, v_ego=5.0, left_blinker=True,
      advisory_valid=False, advisory_turn_info=-1, advisory_turn_distance=0.0,
    )

  assert update_warning(
    warning, v_ego=5.0, left_blinker=True,
    advisory_valid=False, advisory_turn_info=-1, advisory_turn_distance=0.0,
  )


def test_lane_change_state_blocks_physical_blinker_fallback() -> None:
  warning = TurnPathWarning()

  for _ in range(60):
    assert not update_warning(
      warning, v_ego=5.0, left_blinker=True,
      advisory_valid=False, advisory_turn_info=-1, advisory_turn_distance=0.0,
      lane_change_state=LaneChangeState.preLaneChange,
    )


def test_manual_steering_event_is_warning_only_with_visible_and_audible_prompt() -> None:
  event_types = EVENTS[EventName.manualSteeringRequired]

  assert set(event_types) == {ET.WARNING}
  alert = event_types[ET.WARNING]
  assert alert.visual_alert == VisualAlert.steerRequired
  assert alert.audible_alert == AudibleAlert.promptRepeat


def test_message_adapter_uses_shadow_advisory_without_control_authority() -> None:
  warning = TurnPathWarning()
  car_state = SimpleNamespace(vEgo=10.0, leftBlinker=False, rightBlinker=False)
  car_control = SimpleNamespace(latActive=True)
  model_v2 = SimpleNamespace(meta=SimpleNamespace(
    desire=Desire.none,
    laneChangeState=LaneChangeState.off,
  ))
  carrot_man = SimpleNamespace(
    naviValid=True,
    naviControlAllowed=False,
    advisoryTurnInfo=1,
    advisoryTurnDistance=50,
  )

  assert not update_turn_path_warning(warning, car_state, car_control, model_v2, carrot_man, carrot_alive=True)
  assert update_turn_path_warning(warning, car_state, car_control, model_v2, carrot_man, carrot_alive=True)
