from types import SimpleNamespace

from openpilot.common.conversions import Conversions as CV
from openpilot.selfdrive.carrot.driver_style_tuner import (
  ACCEL_MIN_SAMPLES,
  DECEL_MIN_SAMPLES,
  SAMPLE_INTERVAL_FRAMES,
  TF_MIN_SAMPLES,
  DriverStyleTuner,
)


class FakeParams:
  def __init__(self, mode=2, ratio=100, op_learning=0, data=None):
    self.mode = mode
    self.ratio = ratio
    self.op_learning = op_learning
    self.data = data

  def get_int(self, key):
    if key == "LongitudinalAutoTune":
      return self.mode
    if key == "LongitudinalAutoTuneRatio":
      return self.ratio
    if key == "LongitudinalAutoTuneOpLearning":
      return self.op_learning
    raise KeyError(key)

  def get(self, key, return_default=False):
    if key == "LongitudinalAutoTuneData":
      return self.data
    raise KeyError(key)

  def put_nonblocking(self, key, data):
    self.data = data


def _sm(enabled=False):
  return {"selfdriveState": SimpleNamespace(enabled=enabled)}


def _carstate(v_kph, a_ego, gas=False, brake=False):
  return SimpleNamespace(
    vEgo=v_kph * CV.KPH_TO_MS,
    aEgo=a_ego,
    steeringAngleDeg=0.0,
    gasPressed=gas,
    brakePressed=brake,
  )


def _lead(status=True, d_rel=40.0, v_rel=0.0):
  return SimpleNamespace(status=status, dRel=d_rel, vRel=v_rel)


def _observe_many(tuner, count, sm, carstate, lead=None):
  lead = lead if lead is not None else _lead(status=False)
  for _ in range(count * SAMPLE_INTERVAL_FRAMES):
    tuner.observe(sm, carstate, lead, stop_distance=6.0)


def test_accel_learning_blends_with_existing_limits():
  tuner = DriverStyleTuner(FakeParams(mode=2, ratio=100))
  _observe_many(tuner, ACCEL_MIN_SAMPLES, _sm(), _carstate(45.0, 2.0, gas=True))

  base = [1.0] * 7
  tuned = tuner.apply_accel(base)

  assert max(tuned) > 1.0
  assert max(tuned) <= 1.25 + 1e-6


def test_t_follow_is_not_learned_from_openpilot_controlled_gap():
  tuner = DriverStyleTuner(FakeParams(mode=2, ratio=100))
  _observe_many(tuner, TF_MIN_SAMPLES, _sm(enabled=True), _carstate(72.0, 0.0), _lead(d_rel=36.0))

  base = [1.1, 1.2, 1.4, 1.6]
  assert tuner.apply_t_follow_gaps(base) == base


def test_decel_learning_updates_comfort_brake_and_min_accel_limit():
  tuner = DriverStyleTuner(FakeParams(mode=2, ratio=100))
  _observe_many(tuner, DECEL_MIN_SAMPLES, _sm(), _carstate(55.0, -2.8, brake=True))

  assert tuner.apply_comfort_brake(2.4) > 2.4
  assert tuner.apply_decel_limit(-2.0) < -2.0


def test_manual_t_follow_learning_applies_monotonically():
  tuner = DriverStyleTuner(FakeParams(mode=2, ratio=100))
  _observe_many(tuner, TF_MIN_SAMPLES, _sm(enabled=False), _carstate(72.0, 0.0), _lead(d_rel=36.0))

  tuned = tuner.apply_t_follow_gaps([1.1, 1.2, 1.4, 1.6])

  assert tuned[1] > 1.2
  assert tuned == sorted(tuned)


def test_openpilot_learning_requires_dedicated_toggle():
  tuner = DriverStyleTuner(FakeParams(mode=2, ratio=100, op_learning=0))
  lead = _lead(d_rel=20.0, v_rel=-1.2)

  for _ in range(40):
    tuner.observe_openpilot(_sm(enabled=True), _carstate(72.0, -0.5), lead, 6.0, 28.0, 1.2, "lead0", -0.8)

  assert tuner.apply_decel_limit(-2.0) == -2.0


def test_openpilot_learning_uses_flow_pressure_when_enabled():
  tuner = DriverStyleTuner(FakeParams(mode=2, ratio=100, op_learning=1))
  lead = _lead(d_rel=20.0, v_rel=-1.2)

  for _ in range(420):
    tuner.observe_openpilot(_sm(enabled=True), _carstate(72.0, -0.5), lead, 6.0, 28.0, 1.2, "lead0", -0.8)

  assert tuner.apply_decel_limit(-2.0) < -2.0
  assert tuner.apply_t_follow_gaps([1.1, 1.2, 1.4, 1.6])[1] > 1.2
