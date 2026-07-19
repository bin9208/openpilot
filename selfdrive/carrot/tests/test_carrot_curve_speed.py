from types import SimpleNamespace

import numpy as np
import pytest

from openpilot.selfdrive.carrot.carrot_man import CarrotMan


class CapnpListLike:
  """Match pycapnp list readers: iterable, but integer-only subscription."""

  def __init__(self, values):
    self._values = list(values)

  def __len__(self):
    return len(self._values)

  def __iter__(self):
    return iter(self._values)

  def __getitem__(self, index):
    if not isinstance(index, int):
      raise TypeError("an integer is required")
    return self._values[index]


@pytest.mark.parametrize(
  ("position", "times"),
  [
    (CapnpListLike([0.0, 10.0, 20.0]), CapnpListLike([])),
    (CapnpListLike([]), CapnpListLike([0.0, 0.2, 0.4])),
  ],
)
def test_curve_speed_preview_accepts_capnp_list_readers(position, times):
  carrot = object.__new__(CarrotMan)
  carrot.autoCurveSpeedFactor = 1.0
  carrot.autoCurveSpeedAggressiveness = 1.0
  carrot.autoCurveSpeedPreview = 1
  carrot.autoCurveSpeedPreviewTime = 0.0
  carrot.autoCurveSpeedPreviewDecelRate = 1.0
  carrot.carrot_serv = SimpleNamespace(
    calculate_current_speed=lambda distance, target, preview_time, decel_rate: target,
  )
  model = SimpleNamespace(
    orientationRate=SimpleNamespace(z=CapnpListLike([0.1, 0.12, 0.08])),
    velocity=SimpleNamespace(
      x=CapnpListLike([10.0, 10.0, 10.0]),
      t=times,
    ),
    position=SimpleNamespace(x=position),
  )

  speed = carrot.vturn_speed(SimpleNamespace(vEgo=10.0), {"modelV2": model})

  assert np.isfinite(speed)
  assert 5.0 <= abs(speed) < 250.0
