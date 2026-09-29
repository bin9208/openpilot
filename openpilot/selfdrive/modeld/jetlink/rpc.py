"""Private local RPC: isolate uninterruptible FunctionFS I/O from modeld.

This does not change Jetlink USB wire v2. One persistent local stream is bound
to one owner generation; any error/deadline miss destroys that stream.
"""
import json
import socket
import struct
import time

import numpy as np

from third_party.jetlink.spec import ModelSpec
from openpilot.selfdrive.modeld.jetlink.client import CONTRACT

SOCKET_PATH = '/dev/shm/carrot-jetlink.sock'
MAX_PACKET = 1 << 20
REQUEST = struct.Struct('<16sIQB')  # owner generation, frame, absolute monotonic deadline, reset


def remaining(deadline_ns):
  seconds = (deadline_ns - time.monotonic_ns()) / 1e9
  if seconds <= 0:
    raise TimeoutError('Jetlink frame deadline elapsed')
  return seconds


def recv_exact(sock, length, deadline_ns):
  out = bytearray()
  while len(out) < length:
    sock.settimeout(remaining(deadline_ns))
    part = sock.recv(length - len(out))
    if not part:
      raise ConnectionError('Jetlink owner closed the connection')
    out.extend(part)
  remaining(deadline_ns)
  return bytes(out)


def recv_packet(sock, deadline_ns):
  size, = struct.unpack('<I', recv_exact(sock, 4, deadline_ns))
  if not 0 < size <= MAX_PACKET:
    raise ValueError('Jetlink RPC length outside limit')
  return recv_exact(sock, size, deadline_ns)


def send_packet(sock, payload, deadline_ns):
  if not 0 < len(payload) <= MAX_PACKET:
    raise ValueError('Jetlink RPC length outside limit')
  sock.settimeout(remaining(deadline_ns))
  sock.sendall(struct.pack('<I', len(payload)) + payload)
  remaining(deadline_ns)


class ProxyClient:
  def __init__(self, path=SOCKET_PATH, sock=None):
    self.path = path
    self.sock = sock
    self.spec = None
    self.generation = b''
    self.dead = False
    self.identity = {}

  def connect(self):
    try:
      deadline = time.monotonic_ns() + 500_000_000
      if self.sock is None:
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(.5)
        self.sock.connect(self.path)
      send_packet(self.sock, b'H', deadline)
      reply = recv_packet(self.sock, deadline)
      if reply[:1] != b'J':
        raise ValueError('Jetlink owner handshake failed')
      state = json.loads(reply[1:])
      if not state.get('ready'):
        raise RuntimeError('Jetlink owner is not ready')
      if state['spec'] != CONTRACT:
        raise ValueError('Jetlink owner model contract mismatch')
      self.generation = bytes.fromhex(state['generation'])
      if len(self.generation) != 16:
        raise ValueError('Jetlink owner generation mismatch')
      self.spec = ModelSpec.from_dict(state['spec'])
      self.identity = state.get('identity', {})
      return self.spec
    except Exception:
      self.close()
      raise

  def infer(self, frame_id, warped, packed, deadline_ns, reset=False):
    if self.dead or self.spec is None:
      raise RuntimeError('Jetlink owner session is not usable')
    try:
      packed = np.asarray(packed, dtype='<f4')
      if len(warped) != self.spec.warped_nbytes or packed.shape != (self.spec.packed_nelem,) or not np.isfinite(packed).all():
        raise ValueError('Jetlink RPC input mismatch')
      request = b'I' + REQUEST.pack(self.generation, frame_id, deadline_ns, bool(reset)) + warped + packed.tobytes()
      send_packet(self.sock, request, deadline_ns)
      reply = recv_packet(self.sock, deadline_ns)
      if len(reply) != 21 + self.spec.output_nbytes or reply[:1] != b'R' or reply[1:17] != self.generation or struct.unpack_from('<I', reply, 17)[0] != frame_id:
        raise ValueError('Jetlink RPC output identity mismatch')
      values = np.frombuffer(reply, '<f4', offset=21).copy()
      if not np.isfinite(values).all():
        raise ValueError('Jetlink nonfinite output')
      remaining(deadline_ns)
      return values
    except Exception:
      self.close()
      raise

  def close(self):
    self.dead = True
    if self.sock is not None:
      try:
        self.sock.shutdown(socket.SHUT_RDWR)
      except OSError:
        pass
      self.sock.close()
      self.sock = None
