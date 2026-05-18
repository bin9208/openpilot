#!/usr/bin/env python3

import math
import numpy as np

from cereal import messaging, car
from opendbc.car.vehicle_model import VehicleModel
from openpilot.common.realtime import DT_CTRL, Ratekeeper
from openpilot.common.params import Params
from openpilot.common.swaglog import cloudlog

LongCtrlState = car.CarControl.Actuators.LongControlState
MAX_LAT_ACCEL = 2.5
JOYSTICK_TIMEOUT = 0.2
JOYSTICK_ACCEL_SCALE = 2.5
JOYSTICK_FAILSAFE_ACCEL = -1.5
JOYSTICK_ACCEL_JERK = 2.5
JOYSTICK_BRAKE_JERK = 2.5
JOYSTICK_ACCEL_DEADZONE = 0.03
DEADMAN_BUTTON_IDX = 0
CANCEL_BUTTON_IDX = 1


def joystickd_thread():
  params = Params()
  cloudlog.info("joystickd is waiting for CarParams")
  CP = messaging.log_from_bytes(params.get("CarParams", block=True), car.CarParams)
  VM = VehicleModel(CP)

  sm = messaging.SubMaster(['carState', 'onroadEvents', 'liveParameters', 'selfdriveState', 'testJoystick'], frequency=1. / DT_CTRL)
  pm = messaging.PubMaster(['carControl', 'controlsState'])

  rk = Ratekeeper(100, print_delay_threshold=None)
  prev_accel = 0.0
  while 1:
    sm.update(0)

    cc_msg = messaging.new_message('carControl')
    cc_msg.valid = True
    CC = cc_msg.carControl
    CC.enabled = sm['selfdriveState'].enabled
    CC.latActive = sm['selfdriveState'].active and not sm['carState'].steerFaultTemporary and not sm['carState'].steerFaultPermanent
    CC.longActive = CC.enabled and not any(e.overrideLongitudinal for e in sm['onroadEvents']) and CP.openpilotLongitudinalControl
    CC.cruiseControl.cancel = sm['carState'].cruiseState.enabled and (not CC.enabled or not CP.pcmCruise)
    CC.hudControl.leadDistanceBars = 2

    actuators = CC.actuators

    # Missing packets or a released deadman command a controlled stop, not coasting.
    joystick_timed_out = sm.recv_frame['testJoystick'] == 0 or (sm.frame - sm.recv_frame['testJoystick']) * DT_CTRL > JOYSTICK_TIMEOUT

    if not joystick_timed_out:
      joystick_axes = list(sm['testJoystick'].axes)
      joystick_buttons = list(sm['testJoystick'].buttons)
    else:
      joystick_axes = [0.0, 0.0]
      joystick_buttons = []
    joystick_axes = (joystick_axes + [0.0, 0.0])[:2]

    # Old publishers may not send buttons. If buttons are present, button 0 is a deadman.
    deadman_pressed = len(joystick_buttons) <= DEADMAN_BUTTON_IDX or joystick_buttons[DEADMAN_BUTTON_IDX]
    operator_active = not joystick_timed_out and deadman_pressed
    cancel_pressed = len(joystick_buttons) > CANCEL_BUTTON_IDX and joystick_buttons[CANCEL_BUTTON_IDX]
    if cancel_pressed:
      CC.cruiseControl.cancel = True
    if not operator_active:
      joystick_axes = [0.0, 0.0]

    if CC.longActive:
      axis_accel = float(np.clip(joystick_axes[0], -1, 1))
      axis_accel = axis_accel if abs(axis_accel) > JOYSTICK_ACCEL_DEADZONE else 0.0
      requested_accel = JOYSTICK_ACCEL_SCALE * axis_accel

      if not operator_active:
        stop_accel = CP.stopAccel if CP.stopAccel < 0.0 else JOYSTICK_FAILSAFE_ACCEL
        requested_accel = min(JOYSTICK_FAILSAFE_ACCEL, stop_accel)

      accel_delta = requested_accel - prev_accel
      requested_jerk = float(np.clip(accel_delta / DT_CTRL, -JOYSTICK_BRAKE_JERK, JOYSTICK_ACCEL_JERK))
      if abs(accel_delta) < 1e-3:
        requested_jerk = 0.0

      actuators.accel = requested_accel
      actuators.aTarget = requested_accel
      actuators.jerk = requested_jerk
      should_drive = operator_active and (requested_accel > 0.05 or sm['carState'].vEgo > CP.vEgoStopping)
      actuators.longControlState = LongCtrlState.pid if should_drive else LongCtrlState.stopping
      prev_accel = requested_accel

    if CC.latActive:
      max_curvature = MAX_LAT_ACCEL / max(sm['carState'].vEgo ** 2, 5)
      max_angle = math.degrees(VM.get_steer_from_curvature(max_curvature, sm['carState'].vEgo, sm['liveParameters'].roll))

      actuators.torque = float(np.clip(joystick_axes[1], -1, 1))
      actuators.steeringAngleDeg, actuators.curvature = actuators.torque * max_angle, actuators.torque * -max_curvature

    pm.send('carControl', cc_msg)

    cs_msg = messaging.new_message('controlsState')
    cs_msg.valid = True
    controlsState = cs_msg.controlsState
    controlsState.lateralControlState.init('debugState')

    lp = sm['liveParameters']
    steer_angle_without_offset = math.radians(sm['carState'].steeringAngleDeg - lp.angleOffsetDeg)
    controlsState.curvature = -VM.calc_curvature(steer_angle_without_offset, sm['carState'].vEgo, lp.roll)

    pm.send('controlsState', cs_msg)

    rk.keep_time()


def main():
  joystickd_thread()


if __name__ == "__main__":
  main()
