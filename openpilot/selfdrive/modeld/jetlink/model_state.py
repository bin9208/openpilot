"""Stateful CTV3 adapter: local camera warp, scalar packing and existing parser."""
import time
import numpy as np

from openpilot.selfdrive.modeld.jetlink.client import CONTRACT
from openpilot.selfdrive.modeld.parse_model_outputs import Parser


class JetlinkModelState:
  def __init__(self, cam_w, cam_h, client, spec, warp):
    if spec.to_dict() != CONTRACT:
      raise ValueError('Jetlink model metadata differs from the verified contract')
    self.camera_size = cam_w, cam_h
    self.client, self.spec, self.warp = client, spec, warp
    self.vision_input_names = ['img', 'big_img']
    self.input_shapes = spec.input_shapes
    self.output_slices = spec.output_slices
    self.checkpoint = spec.checkpoint
    self.prev_desire = np.zeros(8, np.float32)
    self.parser = Parser()
    self.reset_next = True
    self.usbgpu = False

  def prepare(self, bufs, transforms, inputs, prepare_only=False):
    if prepare_only:
      self.reset_next = True
      self.prev_desire[:] = 0
      return None
    images = np.asarray(self.warp(bufs, transforms))
    if images.dtype != np.uint8 or images.shape != (2, 6, 128, 256):
      raise ValueError('Jetlink warped camera shape/dtype mismatch')
    desire = np.asarray(inputs['desire_pulse'], np.float32).copy()
    traffic = np.asarray(inputs['traffic_convention'], np.float32)
    action = np.asarray(inputs['action_t'], np.float32)
    if desire.shape != (8,) or traffic.shape != (2,) or action.shape != (2,):
      raise ValueError('Jetlink scalar input shape mismatch')
    desire[0] = 0
    pulse = np.where(desire - self.prev_desire > .99, desire, 0)
    self.prev_desire[:] = desire
    packed = np.concatenate((pulse, traffic, action)).astype('<f4')
    if not np.isfinite(packed).all():
      raise ValueError('Jetlink nonfinite input')
    return images.tobytes(), packed

  def infer_prepared(self, frame_id, prepared, deadline_ns):
    output = self.client.infer(frame_id, *prepared, deadline_ns, self.reset_next)
    if time.monotonic_ns() > deadline_ns:
      raise TimeoutError('Jetlink output exceeded complete frame budget')
    if output.shape != (self.spec.output_nelem,) or not np.isfinite(output).all():
      raise ValueError('Jetlink output is invalid')
    self.reset_next = False
    # Parser can modify its input arrays; keep the transport's data isolated.
    parsed = self.parser.parse_outputs({key: output[np.newaxis, value].copy() for key, value in self.output_slices.items()})
    if any(not np.isfinite(value).all() for value in parsed.values() if isinstance(value, np.ndarray)):
      raise ValueError('Jetlink parsed output is invalid')
    if time.monotonic_ns() > deadline_ns:
      raise TimeoutError('Jetlink parser exceeded complete frame budget')
    return parsed

  def run(self, bufs, transforms, inputs, prepare_only, frame_id=0, deadline_ns=None):
    deadline_ns = deadline_ns or time.monotonic_ns() + 50_000_000
    prepared = self.prepare(bufs, transforms, inputs, prepare_only)
    return None if prepared is None else self.infer_prepared(frame_id, prepared, deadline_ns)

  def close(self):
    self.client.close()
