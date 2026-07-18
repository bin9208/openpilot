import math
from types import SimpleNamespace

import pytest

from opendbc.can.packer import CANPacker
from opendbc.car.hyundai.carcontroller import (
  ANGLE_COMMAND_ACCEL_LIMIT,
  ANGLE_CONTROL_DEFAULT_MAX_TORQUE,
  ANGLE_CONTROL_MAX_TORQUE,
  AngleHandoffState,
  apply_steer_angle_limits_physics,
  get_angle_control_max_torque,
  smooth_angle_command_rate,
)
from opendbc.car.hyundai.hyundaicanfd import create_steering_messages
from opendbc.car.hyundai.values import CarControllerParams


def test_ioniq_angle_control_keeps_full_torque_authority() -> None:
  assert ANGLE_CONTROL_DEFAULT_MAX_TORQUE == 250
  assert ANGLE_CONTROL_MAX_TORQUE == 250
  assert CarControllerParams.ANGLE_MAX_TORQUE == 200
  assert get_angle_control_max_torque(120, 25, force_full_torque=True) == 250
  assert get_angle_control_max_torque(120, 25, force_full_torque=False) == 120
  assert get_angle_control_max_torque(0, 25, force_full_torque=False) == 250
  assert get_angle_control_max_torque(255, 25, force_full_torque=False) == 250


def test_angle_control_packs_full_torque_only_while_active() -> None:
  packer = CANPacker("hyundai_canfd_generated")
  cp = SimpleNamespace(flags=0, openpilotLongitudinalControl=True)
  can = SimpleNamespace(ECAN=0, ACAN=1)

  active = create_steering_messages(packer, cp, can, True, True, 0, 12.3, 250, True)
  inactive = create_steering_messages(packer, cp, can, True, False, 0, 12.3, 250, True)

  assert len(active) == 1
  assert active[0][0] == 0x12A
  assert active[0][1][12] == 250
  assert inactive[0][1][12] == 0


def test_small_low_speed_correction_limits_command_acceleration() -> None:
  command, rate = smooth_angle_command_rate(2.0, 0.0, 0.0, 1.0, 1.5)
  assert command == pytest.approx(0.05)
  assert rate == pytest.approx(0.05)

  command, rate = smooth_angle_command_rate(2.0, command, rate, 1.0, 1.5)
  assert command == pytest.approx(0.15)
  assert rate == pytest.approx(0.10)


def test_low_speed_direction_change_brakes_before_reversing() -> None:
  command, rate = smooth_angle_command_rate(-2.0, 1.0, 0.20, 1.0, 1.5)
  assert command == pytest.approx(1.10)
  assert rate == pytest.approx(0.10)

  command, rate = smooth_angle_command_rate(-2.0, command, rate, 1.0, 1.5)
  assert command == pytest.approx(1.10)
  assert rate == pytest.approx(0.0)


def test_command_rate_resets_when_target_is_reached() -> None:
  command, rate = smooth_angle_command_rate(1.0, 1.0, 0.20, 1.0, 1.5)
  assert command == pytest.approx(1.0)
  assert rate == pytest.approx(0.0)


def test_large_low_speed_error_keeps_fast_curve_response() -> None:
  command, rate = smooth_angle_command_rate(20.0, 0.0, 0.0, 1.0, 1.5)
  assert command == pytest.approx(0.35)
  assert rate == pytest.approx(0.35)


def test_reacquire_disables_large_error_acceleration() -> None:
  command, rate = smooth_angle_command_rate(
    20.0, 0.0, 0.0, 1.0, 1.5, allow_large_error_accel=False,
  )

  assert command == pytest.approx(ANGLE_COMMAND_ACCEL_LIMIT)
  assert rate == pytest.approx(ANGLE_COMMAND_ACCEL_LIMIT)


def test_angle_handoff_requires_continuous_release_hold() -> None:
  handoff = AngleHandoffState(release_frames=40)

  assert handoff.update(steering_pressed=True) == AngleHandoffState.YIELDING
  for _ in range(20):
    assert handoff.update(steering_pressed=False) == AngleHandoffState.YIELDING

  assert handoff.update(steering_pressed=True) == AngleHandoffState.YIELDING
  for _ in range(39):
    assert handoff.update(steering_pressed=False) == AngleHandoffState.YIELDING

  assert handoff.update(steering_pressed=False) == AngleHandoffState.REACQUIRING


def test_angle_handoff_finishes_only_after_reacquire() -> None:
  handoff = AngleHandoffState(release_frames=1)
  handoff.update(steering_pressed=True)
  handoff.finish_reacquiring()
  assert handoff.state == AngleHandoffState.YIELDING

  assert handoff.update(steering_pressed=False) == AngleHandoffState.REACQUIRING

  handoff.finish_reacquiring()
  assert handoff.state == AngleHandoffState.NORMAL


def test_angle_handoff_reset_clears_pending_release() -> None:
  handoff = AngleHandoffState(release_frames=40)
  handoff.update(steering_pressed=True)
  for _ in range(20):
    handoff.update(steering_pressed=False)

  handoff.reset()
  assert handoff.state == AngleHandoffState.NORMAL
  assert handoff.release_count == 0


def test_smoothing_fades_out_at_six_meters_per_second() -> None:
  command, rate = smooth_angle_command_rate(2.0, 0.0, 0.0, 6.0, 3.0)
  assert command == pytest.approx(2.0)
  assert rate == pytest.approx(2.0)


def test_non_finite_angle_inputs_hold_a_finite_command() -> None:
  command, rate = smooth_angle_command_rate(float("nan"), 5.0, float("nan"), float("nan"), float("nan"))
  assert command == pytest.approx(5.0)
  assert rate == 0.0
  assert math.isfinite(command)

  command, rate = apply_steer_angle_limits_physics(
    float("nan"), 2.0, 0.2, float("nan"), 3.0, True, 3.0, 16.2, 175.0,
  )
  assert command == pytest.approx(3.0)
  assert rate == 0.0

  command, rate = apply_steer_angle_limits_physics(
    10.0, 2.0, 0.2, 1.0, float("nan"), True, 3.0, 16.2, 175.0,
  )
  assert command == pytest.approx(2.0)
  assert rate == 0.0


def test_driver_override_resets_command_rate_to_measured_angle() -> None:
  command, rate = apply_steer_angle_limits_physics(10.0, 0.0, 0.0, 1.0, 0.0, True, 3.0, 16.2, 175.0)
  assert rate > 0.0

  command, rate = apply_steer_angle_limits_physics(10.0, command, rate, 1.0, 4.0, False, 3.0, 16.2, 175.0)
  assert command == pytest.approx(4.0)
  assert rate == 0.0

  command, rate = apply_steer_angle_limits_physics(5.0, command, rate, 1.0, 4.0, True, 3.0, 16.2, 175.0)
  assert command == pytest.approx(4.05)
  assert rate == pytest.approx(0.05)
