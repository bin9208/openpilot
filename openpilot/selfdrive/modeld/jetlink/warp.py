"""Reuse the native model's compiled camera warp without changing its math."""
import numpy as np


def make_jetlink_warp(cam_w, cam_h, model_hw, native_model):
  if model_hw != (128, 256) or native_model.WARP_DEV.split(':')[0] != 'QCOM':
    raise ValueError('Jetlink requires the verified C3X QCOM warp geometry')

  def warp(bufs, transforms):
    if set(bufs) != {'img', 'big_img'} or set(transforms) != {'img', 'big_img'}:
      raise ValueError('Jetlink needs both camera inputs')
    for name, buf in bufs.items():
      if (buf.width, buf.height) != (cam_w, cam_h):
        raise ValueError('Jetlink camera size mismatch')
      matrix = np.asarray(transforms[name])
      if matrix.shape != (3, 3) or not np.isfinite(matrix).all():
        raise ValueError('Jetlink camera transform is invalid')
    return native_model.warp_images(bufs, transforms)

  return warp
