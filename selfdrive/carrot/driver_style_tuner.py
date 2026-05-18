import json
import time

import numpy as np

from openpilot.common.conversions import Conversions as CV


ACCEL_SPEED_BPS_KPH = [0.0, 10.0, 40.0, 60.0, 80.0, 110.0, 140.0]
TF_SPEED_BPS_KPH = [0.0, 40.0, 80.0, 120.0]

DT_MDL = 0.05
ACCEL_MIN_SAMPLES = 12
DECEL_MIN_SAMPLES = 12
TF_MIN_SAMPLES = 20
SAVE_INTERVAL = 30.0
SAMPLE_INTERVAL_FRAMES = max(1, int(0.5 / DT_MDL))
OP_SAMPLE_INTERVAL_FRAMES = max(1, int(1.0 / DT_MDL))


def _empty_data():
  return {
    "version": 1,
    "accel": [None] * len(ACCEL_SPEED_BPS_KPH),
    "accel_count": [0] * len(ACCEL_SPEED_BPS_KPH),
    "decel": None,
    "decel_count": 0,
    "t_follow": [None] * len(TF_SPEED_BPS_KPH),
    "t_follow_count": [0] * len(TF_SPEED_BPS_KPH),
  }


def _normalize_float_list(values, size):
  out = [None] * size
  if isinstance(values, list):
    for i, value in enumerate(values[:size]):
      try:
        if value is not None and np.isfinite(float(value)):
          out[i] = float(value)
      except (TypeError, ValueError):
        pass
  return out


def _normalize_int_list(values, size):
  out = [0] * size
  if isinstance(values, list):
    for i, value in enumerate(values[:size]):
      try:
        out[i] = max(0, int(value))
      except (TypeError, ValueError):
        pass
  return out


class DriverStyleTuner:
  def __init__(self, params):
    self.params = params
    self.mode = 0
    self.apply_ratio = 0.5
    self.op_learning = False
    self.frame = 0
    self.op_frame = 0
    self._dirty = False
    self._last_save_t = 0.0
    self.data = self._read_data()
    self.update_params()

  @staticmethod
  def _normalize_data(raw):
    data = _empty_data()
    if not isinstance(raw, dict):
      return data

    data["accel"] = _normalize_float_list(raw.get("accel"), len(ACCEL_SPEED_BPS_KPH))
    data["accel_count"] = _normalize_int_list(raw.get("accel_count"), len(ACCEL_SPEED_BPS_KPH))
    data["t_follow"] = _normalize_float_list(raw.get("t_follow"), len(TF_SPEED_BPS_KPH))
    data["t_follow_count"] = _normalize_int_list(raw.get("t_follow_count"), len(TF_SPEED_BPS_KPH))

    try:
      decel = raw.get("decel")
      data["decel"] = float(decel) if decel is not None and np.isfinite(float(decel)) else None
    except (TypeError, ValueError):
      data["decel"] = None

    try:
      data["decel_count"] = max(0, int(raw.get("decel_count", 0)))
    except (TypeError, ValueError):
      data["decel_count"] = 0

    return data

  def _read_data(self):
    try:
      raw = self.params.get("LongitudinalAutoTuneData", return_default=True)
    except Exception:
      raw = None

    if isinstance(raw, (bytes, bytearray)):
      raw = raw.decode("utf-8", errors="ignore")
    if isinstance(raw, str):
      try:
        raw = json.loads(raw)
      except json.JSONDecodeError:
        raw = None

    return self._normalize_data(raw)

  def update_params(self):
    try:
      self.mode = int(np.clip(self.params.get_int("LongitudinalAutoTune"), 0, 2))
      self.apply_ratio = float(np.clip(self.params.get_int("LongitudinalAutoTuneRatio"), 0, 100)) / 100.0
      self.op_learning = self.params.get_int("LongitudinalAutoTuneOpLearning") > 0
    except Exception:
      self.mode = 0
      self.apply_ratio = 0.0
      self.op_learning = False

  def _save_if_needed(self):
    now = time.monotonic()
    if self._dirty and now - self._last_save_t >= SAVE_INTERVAL:
      try:
        self.params.put_nonblocking("LongitudinalAutoTuneData", self.data)
        self._dirty = False
        self._last_save_t = now
      except Exception:
        pass

  @staticmethod
  def _ewma(old_value, sample, count):
    if old_value is None or count <= 0:
      return float(sample)
    alpha = float(np.interp(count, [0, 80], [0.18, 0.025]))
    return float((1.0 - alpha) * old_value + alpha * sample)

  def _update_accel_sample(self, v_ego, a_ego):
    v_kph = v_ego * CV.MS_TO_KPH
    idx = int(np.clip(np.searchsorted(ACCEL_SPEED_BPS_KPH[1:], v_kph, side="right"), 0, len(ACCEL_SPEED_BPS_KPH) - 1))
    sample = float(np.clip(a_ego, 0.25, 2.5))
    count = self.data["accel_count"][idx]
    self.data["accel"][idx] = self._ewma(self.data["accel"][idx], sample, count)
    self.data["accel_count"][idx] = count + 1
    self._dirty = True

  def _update_decel_sample(self, a_ego):
    sample = float(np.clip(-a_ego, 0.35, 3.0))
    count = self.data["decel_count"]
    self.data["decel"] = self._ewma(self.data["decel"], sample, count)
    self.data["decel_count"] = count + 1
    self._dirty = True

  def _update_t_follow_sample(self, v_ego, lead, stop_distance):
    tf_sample = (lead.dRel - stop_distance) / max(v_ego, 0.1)
    self._update_t_follow_value(v_ego, tf_sample)

  def _update_t_follow_value(self, v_ego, t_follow):
    v_kph = v_ego * CV.MS_TO_KPH
    idx = int(np.clip(np.searchsorted(TF_SPEED_BPS_KPH[1:], v_kph, side="right"), 0, len(TF_SPEED_BPS_KPH) - 1))
    sample = float(np.clip(t_follow, 0.9, 2.4))
    count = self.data["t_follow_count"][idx]
    self.data["t_follow"][idx] = self._ewma(self.data["t_follow"][idx], sample, count)
    self.data["t_follow_count"][idx] = count + 1
    self._dirty = True

  def observe(self, sm, carstate, lead, stop_distance):
    self.frame += 1
    if self.mode <= 0:
      return
    if self.frame % SAMPLE_INTERVAL_FRAMES != 0:
      return

    try:
      enabled = bool(sm["selfdriveState"].enabled)
    except Exception:
      enabled = False

    v_ego = float(carstate.vEgo)
    a_ego = float(carstate.aEgo)
    steering_abs = abs(float(carstate.steeringAngleDeg))
    gas_pressed = bool(carstate.gasPressed)
    brake_pressed = bool(carstate.brakePressed)

    if v_ego > 1.0 and steering_abs < 15.0:
      if gas_pressed and not brake_pressed and a_ego > 0.25:
        self._update_accel_sample(v_ego, a_ego)
      elif brake_pressed and not gas_pressed and a_ego < -0.35:
        self._update_decel_sample(a_ego)

    lead_valid = (
      not enabled and
      lead.status and
      v_ego > 4.0 and
      lead.dRel > stop_distance + 2.0 and
      lead.dRel < 120.0 and
      abs(lead.vRel) < 2.0 and
      abs(a_ego) < 1.5
    )
    if lead_valid:
      self._update_t_follow_sample(v_ego, lead, stop_distance)

    self._save_if_needed()

  def observe_openpilot(self, sm, carstate, lead, stop_distance, desired_distance, t_follow, source, a_target):
    self.op_frame += 1
    if self.mode <= 0 or not self.op_learning:
      return
    if self.op_frame % OP_SAMPLE_INTERVAL_FRAMES != 0:
      return

    try:
      enabled = bool(sm["selfdriveState"].enabled)
    except Exception:
      enabled = False
    if not enabled:
      return

    v_ego = float(carstate.vEgo)
    a_ego = float(carstate.aEgo)
    steering_abs = abs(float(carstate.steeringAngleDeg))
    if (
      not lead.status or source != "lead0" or v_ego < 4.0 or lead.dRel > 120.0 or
      steering_abs > 15.0 or bool(carstate.gasPressed) or bool(carstate.brakePressed)
    ):
      return

    gap_error = float(lead.dRel - desired_distance)
    v_rel = float(lead.vRel)
    a_target = float(a_target)

    if v_rel > 0.8 and gap_error > 3.0 and a_target > 0.35:
      self._update_accel_sample(v_ego, max(a_target, a_ego))

    if v_rel < -0.8 and gap_error < 6.0 and a_target < -0.35:
      self._update_decel_sample(-max(-a_target, -a_ego, 2.4))

    gap_error_t = gap_error / max(v_ego, 1.0)
    if gap_error_t < -0.25 or (v_rel < -1.0 and lead.dRel < desired_distance + 5.0):
      self._update_t_follow_value(v_ego, t_follow + 0.05)
    elif gap_error_t > 0.55 and abs(v_rel) < 0.6 and abs(a_ego) < 0.4:
      self._update_t_follow_value(v_ego, t_follow - 0.03)

    self._save_if_needed()

  @staticmethod
  def _limited_target(base, learned, lower, upper, max_down, max_up):
    target = float(np.clip(learned, lower, upper))
    return float(np.clip(target, base - max_down, base + max_up))

  def apply_accel(self, base_values):
    values = [float(v) for v in base_values]
    if self.mode != 2 or self.apply_ratio <= 0.0:
      return values

    tuned = []
    for base, learned, count in zip(values, self.data["accel"], self.data["accel_count"]):
      if learned is None or count < ACCEL_MIN_SAMPLES:
        tuned.append(base)
        continue
      target = self._limited_target(base, learned, 0.35, 2.5, base * 0.25, base * 0.25)
      tuned.append(float((1.0 - self.apply_ratio) * base + self.apply_ratio * target))
    return tuned

  def apply_t_follow_gaps(self, base_gaps):
    gaps = [float(v) for v in base_gaps]
    if self.mode != 2 or self.apply_ratio <= 0.0:
      return gaps

    tuned = []
    for base, learned, count in zip(gaps, self.data["t_follow"], self.data["t_follow_count"]):
      if learned is None or count < TF_MIN_SAMPLES:
        tuned.append(base)
        continue
      target = self._limited_target(base, learned, 0.9, 2.4, 0.25, 0.35)
      tuned.append(float((1.0 - self.apply_ratio) * base + self.apply_ratio * target))

    return np.maximum.accumulate(tuned).tolist()

  def apply_comfort_brake(self, base_comfort_brake):
    base = float(base_comfort_brake)
    if self.mode != 2 or self.apply_ratio <= 0.0:
      return base
    if self.data["decel"] is None or self.data["decel_count"] < DECEL_MIN_SAMPLES:
      return base

    target = self._limited_target(base, self.data["decel"], 1.6, 3.0, 0.4, 0.4)
    return float((1.0 - self.apply_ratio) * base + self.apply_ratio * target)

  def apply_decel_limit(self, base_min_accel):
    base = abs(float(base_min_accel))
    if self.mode != 2 or self.apply_ratio <= 0.0:
      return float(base_min_accel)
    if self.data["decel"] is None or self.data["decel_count"] < DECEL_MIN_SAMPLES:
      return float(base_min_accel)

    target = self._limited_target(base, self.data["decel"], 1.0, 3.0, 0.4, 0.4)
    tuned = float((1.0 - self.apply_ratio) * base + self.apply_ratio * target)
    return -tuned
