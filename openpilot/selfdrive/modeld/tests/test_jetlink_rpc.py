import socket
import threading
import time
import struct
import json
import numpy as np
import pytest

from openpilot.selfdrive.modeld.jetlink.client import CONTRACT
from openpilot.selfdrive.modeld.jetlink.rpc import ProxyClient, recv_packet, send_packet


@pytest.mark.parametrize('fault', ['none', 'generation', 'frame', 'partial', 'late'])
def test_rpc_frame_identity_and_deadline(fault):
  left, right = socket.socketpair()
  generation = bytes.fromhex('ab' * 16)
  def server():
    try:
      assert recv_packet(right, time.monotonic_ns() + 1_000_000_000) == b'H'
      send_packet(right, b'J' + json.dumps({'ready': True, 'generation': generation.hex(), 'spec': CONTRACT}).encode(), time.monotonic_ns() + 1_000_000_000)
      request = recv_packet(right, time.monotonic_ns() + 1_000_000_000)
      assert request[0:1] == b'I'
      if fault == 'late': time.sleep(.06)
      payload = b'R' + (bytes(16) if fault == 'generation' else generation) + struct.pack('<I', 8 if fault == 'frame' else 7) + np.zeros(18452, '<f4').tobytes()
      if fault == 'partial':
        right.sendall(struct.pack('<I', len(payload)) + payload[:20]); time.sleep(.06)
      else: send_packet(right, payload, time.monotonic_ns() + 1_000_000_000)
    except OSError:
      pass
    finally: right.close()
  worker = threading.Thread(target=server); worker.start()
  proxy = ProxyClient(sock=left); spec = proxy.connect()
  try:
    args = (7, bytes(spec.warped_nbytes), np.zeros(spec.packed_nelem, np.float32), time.monotonic_ns() + 40_000_000)
    if fault == 'none': assert proxy.infer(*args).size == 18452
    else:
      with pytest.raises((ValueError, TimeoutError, OSError)): proxy.infer(*args)
      assert proxy.dead
  finally: proxy.close(); worker.join(1)
