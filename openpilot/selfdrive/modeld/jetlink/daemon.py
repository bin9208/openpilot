"""Offroad gadget owner and private inference server; default off.

modeld only waits on a deadline-bounded Unix socket. Even a kernel-stuck USB
worker cannot hold its frame loop. Reconfiguration/recovery needs offroad and
an explicit Off -> Shadow/Active-request setting change.
"""
import json
import os
from pathlib import Path
import socket
import struct
import threading
import time
import uuid

import numpy as np

from openpilot.selfdrive.modeld.jetlink.client import JetlinkClient, CONTRACT
from openpilot.selfdrive.modeld.jetlink.owner import GadgetOwner, setup_gadget
from openpilot.selfdrive.modeld.jetlink.rpc import SOCKET_PATH, REQUEST, recv_packet, send_packet, remaining

GADGET = '/sys/kernel/config/usb_gadget/carrot_jetlink'
MOUNT = '/dev/ffs-carrot-jetlink'


class InferenceServer:
  def __init__(self, client, path=SOCKET_PATH):
    self.client = client
    self.path = path
    self.generation = uuid.uuid4().bytes
    self.ready = False
    self.error = ''
    self.stopped = threading.Event()
    self.listener = None
    self.connection = None
    self.worker = threading.Thread(target=self.run, name='jetlink-usb-owner', daemon=True)

  def infer_request(self, payload):
    spec = self.client.spec
    if spec is None or len(payload) != 1 + REQUEST.size + spec.warped_nbytes + spec.packed_nbytes or payload[:1] != b'I':
      raise ValueError('Invalid owner inference request')
    generation, frame, deadline, reset = REQUEST.unpack_from(payload, 1)
    if generation != self.generation or reset not in (0, 1):
      raise ValueError('Invalid owner generation/reset')
    # The local caller cannot authorize a longer-than-50ms operation.
    budget = remaining(deadline)
    if budget > .050:
      raise ValueError('Invalid owner frame budget')
    offset = 1 + REQUEST.size
    image = payload[offset:offset + spec.warped_nbytes]
    packed = np.frombuffer(payload, '<f4', offset=offset + spec.warped_nbytes).copy()
    values = self.client.infer(frame, image, packed, deadline, bool(reset))
    remaining(deadline)
    return b'R' + self.generation + struct.pack('<I', frame) + values.astype('<f4', copy=False).tobytes(), deadline

  def run(self):
    try:
      self.client.connect()
      if self.stopped.is_set():
        return
      listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
      self.listener = listener
      # The manager owns a process flock; only its own stale socket is removed.
      Path(self.path).unlink(missing_ok=True)
      listener.bind(self.path)
      os.chmod(self.path, 0o600)
      listener.listen(1)
      listener.settimeout(.5)
      self.ready = True
      while not self.stopped.is_set():
        try:
          connection, _ = listener.accept()
        except socket.timeout:
          continue
        self.connection = connection
        with connection:
          try:
            hello = recv_packet(connection, time.monotonic_ns() + 500_000_000)
            if hello != b'H':
              raise ValueError('Missing owner handshake')
            hello = self.client.hello
            identity = hello.get('telemetry', {})
            identity = {key: str(identity.get(key, ''))[:200] for key in ('device_model', 'android_api', 'backend_requested', 'app_version', 'artifact_sha256')}
            identity['runtime_version'] = str(hello.get('runtime_version', ''))[:200]
            info = {'ready': True, 'generation': self.generation.hex(), 'spec': CONTRACT, 'identity': identity}
            send_packet(connection, b'J' + json.dumps(info).encode(), time.monotonic_ns() + 500_000_000)
            while not self.stopped.is_set():
              try:
                request = recv_packet(connection, time.monotonic_ns() + 1_000_000_000)
              except (ConnectionError, socket.timeout):
                # modeld absent or preparing the warp. No USB transaction was started.
                break
              response, deadline = self.infer_request(request)
              send_packet(connection, response, deadline)
          except (OSError, ValueError):
            if self.client.dead:
              raise
          finally:
            self.connection = None
    except Exception as exc:
      self.error = str(exc)[:240]
    finally:
      self.ready = False
      if self.listener is not None:
        self.listener.close()
      self.client.close()

  def stop(self):
    self.stopped.set()
    for sock in (self.connection, self.listener):
      if sock is not None:
        try:
          sock.shutdown(socket.SHUT_RDWR)
        except OSError:
          pass
        sock.close()


def main():
  import fcntl
  from openpilot.common.params import Params
  from openpilot.selfdrive.modeld.helpers import usb_device_present, USBGPU_USB_IDS
  from third_party.jetlink.client import JetlinkClient as UpstreamClient
  from third_party.jetlink.transport.ffs import FfsTransport

  lock = open('/dev/shm/carrot-jetlink-owner.lock', 'w')
  fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
  params = Params()
  transport = None
  server = None

  class ParkedTransport(FfsTransport):
    def unbind(self, gadget=None):
      # A USB watchdog never changes the gadget while onroad. The RPC deadline
      # already returned modeld to native; offroad teardown frees kernel I/O.
      if params.get_bool('IsOffroad'):
        super().unbind(gadget)

    def close(self):
      if params.get_bool('IsOffroad'):
        super().close()

  def setup():
    nonlocal transport, server
    setup_gadget()
    if not params.get_bool('IsOffroad'):
      raise RuntimeError('Vehicle left offroad during USB setup')
    transport = ParkedTransport(MOUNT, gadget=GADGET)
    server = InferenceServer(JetlinkClient(UpstreamClient(transport)))
    server.worker.start()

  def teardown():
    nonlocal transport, server
    if server is not None:
      server.stop()
    if transport is not None:
      transport.close()
    if server is not None:
      server.worker.join(timeout=2)
      if server.worker.is_alive():
        raise RuntimeError('USB worker has not stopped; restart device while parked')
    server = transport = None
    Path(SOCKET_PATH).unlink(missing_ok=True)

  owner = GadgetOwner(setup, teardown)
  setup_failed = False
  while True:
    offroad = params.get_bool('IsOffroad')
    mode = params.get_int('JetlinkMode')
    if offroad:
      try:
        if mode == 0:
          owner.disable(True)
          setup_failed = False
        elif mode in (1, 2) and not owner.enabled and not setup_failed:
          owner.enable('shadow' if mode == 1 else 'active_request', True,
                       usb_device_present(USBGPU_USB_IDS) or params.get_bool('UsbGpuActive') or params.get_bool('UsbGpuLoading'))
      except Exception as exc:
        setup_failed = True
        params.put('JetlinkStatus', json.dumps({'phase': 'ERROR', 'detail': str(exc)[:240]}))
    if not setup_failed:
      ready = server and server.ready and not server.client.dead
      phase = 'OFF' if not owner.enabled else 'READY' if ready else 'LOST' if server and (server.error or server.client.dead) else 'PREPARING'
      params.put('JetlinkStatus', json.dumps({'phase': phase, 'detail': server.error if server else '',
                                            'generation': server.generation.hex() if server else ''}))
    time.sleep(.5)


if __name__ == '__main__':
  main()
