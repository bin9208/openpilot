from types import SimpleNamespace

import numpy as np
import pytest

from openpilot.selfdrive.controls.controlsd import get_laneless_curvature_smooth_seconds
from openpilot.selfdrive.controls.lib.drive_helpers import CONTROL_N, get_lag_adjusted_curvature
from openpilot.selfdrive.controls.lib.lane_planner_2 import get_laneless_center_offset


def test_curve_smoothing_uses_measured_moderate_tune():
  model = SimpleNamespace(position=SimpleNamespace(yStd=[0.08] * 11))

  assert get_laneless_curvature_smooth_seconds(15.0, 0.002, model) == pytest.approx(0.04)
  assert get_laneless_curvature_smooth_seconds(30.0, 0.0, model) <= 0.16


def test_curve_transition_moves_faster_toward_future_curvature():
  distances = np.linspace(0.1, 30.0, CONTROL_N)
  psis = 0.002 * distances
  curvatures = np.linspace(0.004, -0.004, CONTROL_N)

  current = get_lag_adjusted_curvature(None, 5.1, psis, curvatures, 0.34, distances)
  responsive = get_lag_adjusted_curvature(None, 5.1, psis, curvatures, 0.34, distances,
                                          curve_transition_psi_scale=0.5)

  assert responsive < current
  assert abs(responsive - curvatures[-1]) < abs(current - curvatures[-1])


def test_laneless_center_offset_reduces_high_confidence_path_bias():
  x = np.linspace(0.0, 30.0, 33)
  path_y = np.full(33, 0.20)
  left_y = np.full(33, -1.5)
  right_y = np.full(33, 1.5)

  offset = get_laneless_center_offset(x, path_y, x, left_y, right_y, 0.9, 0.9,
                                      lane_change_multiplier=1.0, lanefull_mode=False)

  assert offset == pytest.approx(-0.13)


@pytest.mark.parametrize(("left_prob", "right_prob", "lane_change_multiplier", "lanefull_mode"), [
  (0.4, 0.9, 1.0, False),
  (float("nan"), 0.9, 1.0, False),
  (0.9, 0.9, 0.0, False),
  (0.9, 0.9, 1.0, True),
])
def test_laneless_center_offset_releases_when_unsafe(left_prob: float, right_prob: float,
                                                     lane_change_multiplier: float, lanefull_mode: bool):
  x = np.linspace(0.0, 30.0, 33)
  path_y = np.full(33, 0.20)
  left_y = np.full(33, -1.5)
  right_y = np.full(33, 1.5)

  offset = get_laneless_center_offset(x, path_y, x, left_y, right_y, left_prob, right_prob,
                                      lane_change_multiplier, lanefull_mode)

  assert offset == 0.0


def test_laneless_center_offset_rejects_nonfinite_path():
  x = np.linspace(0.0, 30.0, 33)
  path_y = np.full(33, 0.20)
  path_y[10] = np.nan
  left_y = np.full(33, -1.5)
  right_y = np.full(33, 1.5)

  offset = get_laneless_center_offset(x, path_y, x, left_y, right_y, 0.9, 0.9, 1.0, False)

  assert offset == 0.0


@pytest.mark.parametrize("invalid_input", ["nonmonotonic", "length_mismatch"])
def test_laneless_center_offset_rejects_invalid_geometry(invalid_input: str):
  path_x = np.linspace(0.0, 30.0, 33)
  path_y = np.full(33, 0.20)
  lane_x = path_x.copy()
  left_y = np.full(33, -1.5)
  right_y = np.full(33, 1.5)
  if invalid_input == "nonmonotonic":
    lane_x[10] = lane_x[9]
  else:
    right_y = right_y[:-1]

  offset = get_laneless_center_offset(path_x, path_y, lane_x, left_y, right_y,
                                      0.9, 0.9, 1.0, False)

  assert offset == 0.0
