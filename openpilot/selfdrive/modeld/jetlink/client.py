"""Strict contract/deadline boundary around the pinned upstream USB client.

Only the owner worker calls this class: FunctionFS may block in the kernel.
modeld uses the separately bounded Unix-socket proxy, never this transport.
"""
import json
import time
from pathlib import Path

import numpy as np

CONTRACT = json.loads(Path(__file__).with_name('cinque-v3.json').read_text())


class JetlinkClient:
  def __init__(self, peer):
    self.peer = peer
    self.spec = None
    self.dead = False
    self.hello = {}
    self.last_frame = -1

  def connect(self):
    try:
      self.hello = self.peer.hello()
      if self.hello.get('protocol') != 2 or self.hello.get('backend') != 'ort' or self.hello.get('runtime_version') != '1.22.0':
        raise ValueError('Jetlink runtime contract mismatch')
      self.spec = self.peer.ensure_engine(CONTRACT['sha256'], CONTRACT['nbytes'], frame_skip=4, build_timeout=300)
      actual = self.spec.to_dict()
      if any(actual.get(key) != expected for key, expected in CONTRACT.items()):
        raise ValueError('Jetlink model contract mismatch')
      return self.spec
    except Exception:
      self.close()
      raise

  def infer(self, frame_id: int, warped: bytes, packed: np.ndarray, deadline_ns: int, reset: bool = False):
    if self.dead or self.spec is None:
      raise RuntimeError('Jetlink session is not usable')
    try:
      budget = (deadline_ns - time.monotonic_ns()) / 1e9
      if budget <= 0:
        raise TimeoutError('Jetlink end-to-end deadline elapsed')
      if not 0 <= frame_id <= 0xffffffff or (frame_id <= self.last_frame and not reset):
        raise ValueError('Jetlink frame order mismatch')
      if len(warped) != self.spec.warped_nbytes or packed.shape != (self.spec.packed_nelem,) or not np.isfinite(packed).all():
        raise ValueError('Jetlink input contract mismatch')
      # Restart before wire sequence wrap; an old result must never acquire a new identity.
      if getattr(self.peer, 'seq', 0) >= 0xfffffffe:
        raise ValueError('Jetlink sequence exhausted; reconnect offroad')
      result = self.peer.infer(warped, np.asarray(packed, dtype='<f4'), frame_id=frame_id, reset=reset, deadline=budget)
      if time.monotonic_ns() > deadline_ns:
        raise TimeoutError('Jetlink end-to-end deadline elapsed')
      if result.shape != (self.spec.output_nelem,) or not np.isfinite(result).all():
        raise ValueError('Jetlink invalid output')
      self.last_frame = frame_id
      return result
    except Exception:
      self.close()
      raise

  def close(self):
    if not self.dead:
      self.dead = True
      self.peer.close()
