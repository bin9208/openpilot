from cereal import log

from openpilot.common.realtime import DT_CTRL


NAV_TURN_CONFIRM_FRAMES = 2
NAV_TURN_MISSING_TOLERANCE_FRAMES = 1
BLINKER_TURN_CONFIRM_FRAMES = int(0.5 / DT_CTRL)
TURN_WARNING_MIN_TIME = 3.0
TURN_WARNING_MAX_TIME = 8.0
TURN_WARNING_MAX_BLINKER_SPEED = 10.0

TURN_LEFT = 1
TURN_RIGHT = 2
TURN_ROUNDABOUT = 5
SUPPORTED_TURNS = (TURN_LEFT, TURN_RIGHT, TURN_ROUNDABOUT)


class TurnPathWarning:
  def __init__(self):
    self.reset()

  def reset(self) -> None:
    self.nav_candidate = -1
    self.nav_candidate_count = 0
    self.nav_confirmed = -1
    self.nav_missing_count = 0
    self.blinker_candidate = -1
    self.blinker_count = 0

  @staticmethod
  def _model_matches_turn(turn_info: int, model_desire: int) -> bool:
    if turn_info == TURN_LEFT:
      return model_desire == log.Desire.turnLeft
    if turn_info == TURN_RIGHT:
      return model_desire == log.Desire.turnRight
    return False

  def _update_navigation(self, *, advisory_valid: bool, turn_info: int, distance: float,
                         v_ego: float) -> tuple[int, bool]:
    has_advisory = advisory_valid and turn_info in SUPPORTED_TURNS and distance > 0.0
    time_to_turn = distance / max(v_ego, 2.0) if has_advisory else 0.0
    candidate = turn_info if has_advisory and TURN_WARNING_MIN_TIME <= time_to_turn <= TURN_WARNING_MAX_TIME else -1

    if candidate > 0:
      if candidate != self.nav_candidate:
        self.nav_candidate = candidate
        self.nav_candidate_count = 1
        self.nav_confirmed = -1
      else:
        self.nav_candidate_count += 1
      if self.nav_candidate_count >= NAV_TURN_CONFIRM_FRAMES:
        self.nav_confirmed = candidate
      self.nav_missing_count = 0
    elif has_advisory:
      self.nav_candidate = -1
      self.nav_candidate_count = 0
      self.nav_confirmed = -1
      self.nav_missing_count = 0
    else:
      self.nav_missing_count += 1
      if self.nav_missing_count > NAV_TURN_MISSING_TOLERANCE_FRAMES:
        self.nav_candidate = -1
        self.nav_candidate_count = 0
        self.nav_confirmed = -1

    return self.nav_confirmed, has_advisory

  def _update_blinker(self, *, left_blinker: bool, right_blinker: bool,
                      v_ego: float, lane_change_state: int) -> int:
    if left_blinker == right_blinker or v_ego > TURN_WARNING_MAX_BLINKER_SPEED or lane_change_state != log.LaneChangeState.off:
      self.blinker_candidate = -1
      self.blinker_count = 0
      return -1

    candidate = TURN_LEFT if left_blinker else TURN_RIGHT
    if candidate != self.blinker_candidate:
      self.blinker_candidate = candidate
      self.blinker_count = 1
    else:
      self.blinker_count += 1
    return candidate if self.blinker_count >= BLINKER_TURN_CONFIRM_FRAMES else -1

  def update(self, *, lat_active: bool, v_ego: float, left_blinker: bool, right_blinker: bool,
             advisory_valid: bool, advisory_turn_info: int, advisory_turn_distance: float,
             model_desire: int, lane_change_state: int) -> bool:
    if not lat_active:
      self.reset()
      return False

    v_ego = max(0.0, float(v_ego))
    nav_turn, has_advisory = self._update_navigation(
      advisory_valid=bool(advisory_valid),
      turn_info=int(advisory_turn_info),
      distance=float(advisory_turn_distance),
      v_ego=v_ego,
    )
    if nav_turn > 0:
      self.blinker_candidate = -1
      self.blinker_count = 0
      return not self._model_matches_turn(nav_turn, model_desire)

    if has_advisory:
      self.blinker_candidate = -1
      self.blinker_count = 0
      return False

    blinker_turn = self._update_blinker(
      left_blinker=bool(left_blinker),
      right_blinker=bool(right_blinker),
      v_ego=v_ego,
      lane_change_state=lane_change_state,
    )
    return blinker_turn > 0 and not self._model_matches_turn(blinker_turn, model_desire)


def update_turn_path_warning(warning: TurnPathWarning, car_state, car_control, model_v2,
                             carrot_man, *, carrot_alive: bool) -> bool:
  return warning.update(
    lat_active=bool(car_control.latActive),
    v_ego=car_state.vEgo,
    left_blinker=car_state.leftBlinker,
    right_blinker=car_state.rightBlinker,
    advisory_valid=bool(carrot_alive and getattr(carrot_man, "naviValid", False)),
    advisory_turn_info=int(getattr(carrot_man, "advisoryTurnInfo", -1)),
    advisory_turn_distance=float(getattr(carrot_man, "advisoryTurnDistance", 0.0)),
    model_desire=model_v2.meta.desire,
    lane_change_state=model_v2.meta.laneChangeState,
  )
