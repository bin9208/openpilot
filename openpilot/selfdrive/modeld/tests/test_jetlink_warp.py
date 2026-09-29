from types import SimpleNamespace
import numpy as np
import pytest
from openpilot.selfdrive.modeld.jetlink.warp import make_jetlink_warp


def test_warp_keeps_native_camera_order_and_pixels():
  reference = np.arange(2 * 6 * 128 * 256, dtype=np.uint8).reshape(2, 6, 128, 256)
  bufs = {key: SimpleNamespace(width=1928, height=1208) for key in ('img', 'big_img')}
  transforms = {key: np.eye(3, dtype=np.float32) for key in bufs}
  seen = []
  native = SimpleNamespace(WARP_DEV='QCOM', warp_images=lambda b, t: (seen.append((b, t)), reference)[1])
  warp = make_jetlink_warp(1928, 1208, (128, 256), native)
  np.testing.assert_array_equal(warp(bufs, transforms), reference)
  assert seen == [(bufs, transforms)]
  transforms['big_img'][0, 0] = np.nan
  with pytest.raises(ValueError): warp(bufs, transforms)
  assert len(seen) == 1


def test_wrong_camera_and_non_qcom_are_rejected():
  native = SimpleNamespace(WARP_DEV='CPU')
  with pytest.raises(ValueError): make_jetlink_warp(1928, 1208, (128, 256), native)
  native.WARP_DEV = 'QCOM'
  warp = make_jetlink_warp(1928, 1208, (128, 256), native)
  bufs = {key: SimpleNamespace(width=10, height=10) for key in ('img', 'big_img')}
  with pytest.raises(ValueError): warp(bufs, {key: np.eye(3) for key in bufs})
