import capnp
import pytest

import cereal.messaging as messaging
from cereal import car, log
from openpilot.common.params import Params
from openpilot.selfdrive.locationd.lagd import BLOCK_NUM, LateralLagEstimator, retrieve_initial_lag


SteeringParams = tuple[Params, capnp._DynamicStructBuilder]


@pytest.fixture
def steering_params() -> SteeringParams:
  params = Params()
  cp = car.CarParams.new_message()
  cp.carFingerprint = "HYUNDAI IONIQ 5 PE"
  cp.steerControlType = car.CarParams.SteerControlType.angle
  cp.steerRatio = 16.2
  cp.steerActuatorDelay = 0.15
  params.put("CarParamsPrevRoute", cp.to_bytes())
  return params, cp


class TestLateralLagPersistence:
  def test_uses_current_car_params_when_cached_lag_has_no_valid_blocks(self, steering_params: SteeringParams):
    params, cp = steering_params
    # Given an old fallback value that never completed a valid learning block.
    cached = messaging.new_message("liveDelay")
    cached.liveDelay.status = log.LiveDelayData.Status.unestimated
    cached.liveDelay.lateralDelayEstimate = 0.30
    cached.liveDelay.validBlocks = 0
    params.put("LiveDelay", cached.to_bytes())

    # When lagd restores its startup state.
    initial_lag = retrieve_initial_lag(params, cp)

    # Then it ignores the stale fallback so the current CarParams prior is used.
    assert initial_lag is None
    assert params.get("LiveDelay") is None

  def test_keeps_partial_learning_when_cached_lag_has_valid_blocks(self, steering_params: SteeringParams):
    params, cp = steering_params
    # Given a route that completed part of the required lag learning.
    cached = messaging.new_message("liveDelay")
    cached.liveDelay.status = log.LiveDelayData.Status.unestimated
    cached.liveDelay.lateralDelayEstimate = 0.34
    cached.liveDelay.validBlocks = 2
    params.put("LiveDelay", cached.to_bytes())

    # When lagd restores its startup state.
    initial_lag = retrieve_initial_lag(params, cp)

    # Then it preserves the measured partial progress across routes.
    assert initial_lag == pytest.approx((0.34, 2))
    assert params.get("LiveDelay") is not None

  @pytest.mark.parametrize(("previous_steer_ratio", "previous_actuator_delay", "previous_control_type"), [
    (14.26, 0.15, "angle"),
    (16.2, 0.10, "angle"),
    (16.2, 0.15, "torque"),
  ])
  def test_relearns_delay_when_steering_model_changes(self, steering_params: SteeringParams,
                                                      previous_steer_ratio: float, previous_actuator_delay: float,
                                                      previous_control_type: str):
    params, cp = steering_params
    # Given a complete estimate learned with an older steering model.
    previous_cp = car.CarParams.new_message()
    previous_cp.carFingerprint = cp.carFingerprint
    previous_cp.steerControlType = previous_control_type
    previous_cp.steerRatio = previous_steer_ratio
    previous_cp.steerActuatorDelay = previous_actuator_delay
    params.put("CarParamsPrevRoute", previous_cp.to_bytes())

    cached = messaging.new_message("liveDelay")
    cached.liveDelay.status = log.LiveDelayData.Status.estimated
    cached.liveDelay.lateralDelayEstimate = 0.29
    cached.liveDelay.validBlocks = 50
    params.put("LiveDelay", cached.to_bytes())

    # When lagd restores after any steering model component changes.
    initial_lag = retrieve_initial_lag(params, cp)

    # Then it starts from the current model instead of mixing incompatible state.
    assert initial_lag is None
    assert params.get("LiveDelay") is None

  def test_keeps_complete_learning_when_steering_model_is_unchanged(self, steering_params: SteeringParams):
    params, cp = steering_params
    # Given a complete estimate learned with the current steering model.
    cached = messaging.new_message("liveDelay")
    cached.liveDelay.status = log.LiveDelayData.Status.estimated
    cached.liveDelay.lateralDelayEstimate = 0.29
    cached.liveDelay.validBlocks = 50
    params.put("LiveDelay", cached.to_bytes())

    # When lagd restores its startup state.
    initial_lag = retrieve_initial_lag(params, cp)

    # Then it preserves the completed estimate.
    assert initial_lag == pytest.approx((0.29, 50))
    assert params.get("LiveDelay") is not None

  @pytest.mark.parametrize(("status", "valid_blocks"), [
    (log.LiveDelayData.Status.invalid, 1),
    (log.LiveDelayData.Status.estimated, BLOCK_NUM + 1),
  ])
  def test_discards_invalid_cached_lag(self, steering_params: SteeringParams, status: str, valid_blocks: int):
    params, cp = steering_params
    # Given a cache that cannot be a valid lag estimator state.
    cached = messaging.new_message("liveDelay")
    cached.liveDelay.status = status
    cached.liveDelay.lateralDelayEstimate = 0.29
    cached.liveDelay.validBlocks = valid_blocks
    params.put("LiveDelay", cached.to_bytes())

    # When lagd restores while Python assertions are disabled.
    initial_lag = retrieve_initial_lag(params, cp)

    # Then explicit validation removes the corrupt persistent state.
    assert initial_lag is None
    assert params.get("LiveDelay") is None

  def test_fallback_delay_uses_current_car_params(self, steering_params: SteeringParams):
    _, cp = steering_params
    # Given the updated Ioniq 5 PE actuator delay prior.
    estimator = LateralLagEstimator(cp, dt=0.05)

    # When lagd publishes before enough samples have been learned.
    live_delay = estimator.get_msg(valid=True).liveDelay

    # Then consumers receive actuator delay plus the fixed pipeline latency.
    assert live_delay.status == log.LiveDelayData.Status.unestimated
    assert live_delay.lateralDelay == pytest.approx(0.35)
