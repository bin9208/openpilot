from concurrent.futures import Future
from types import SimpleNamespace
import time
from openpilot.selfdrive.modeld.jetlink.runtime import Runtime
from openpilot.selfdrive.modeld.jetlink.transition import ControlState, Decision, Mode


class Params:
  def __init__(self): self.data = {}
  def get_bool(self, key): return bool(self.data.get(key, False))
  def put_bool(self, key, value): self.data[key] = value
  def get(self, key): return self.data.get(key)


def test_off_has_no_transport_and_shadow_does_not_wait():
  params = Params()
  runtime = Runtime(params, (1928, 1208), None, lambda: (_ for _ in ()).throw(AssertionError('USB opened in Off')))
  runtime.begin(0, ControlState(True, False, False, False), {}, {}, {}, 1, False)
  output = {'native': 1}
  assert runtime.finish(output) is output
  assert runtime.connecting is None
  runtime.decision = Decision('native', 'READY', False, False)
  runtime.pending = Future()
  assert runtime.finish(output) is output
  assert not runtime.pending.done()
  runtime.pool.shutdown(wait=False)


def test_active_deadline_returns_same_native_output_and_latches():
  params = Params(); runtime = Runtime(params, (1928, 1208), None)
  runtime.mode = Mode.ACTIVE_REQUEST
  runtime.controls = ControlState(False, True, True, True)
  runtime.transition.active = True
  runtime.decision = Decision('jetlink', 'ACTIVE', False, False)
  runtime.pending = Future()
  runtime.deadline = time.monotonic_ns() - 1
  runtime.model = SimpleNamespace(close=lambda: None)
  native = {'fresh_native_history': True}
  assert runtime.finish(native) is native
  assert runtime.decision.loss_latched
  assert params.data['JetlinkLossLatched'] and not params.data['JetlinkActive']
  assert not runtime.valid_at_publish()
  runtime.pool.shutdown(wait=False)


def test_publication_overrun_invalidates_external_frame():
  runtime = Runtime(Params(), (1928, 1208), None)
  runtime.mode = Mode.ACTIVE_REQUEST; runtime.controls = ControlState(False, True, True, True)
  runtime.transition.active = True
  runtime.decision = Decision('jetlink', 'ACTIVE', False, False)
  runtime.deadline = time.monotonic_ns() - 1
  assert not runtime.valid_at_publish()
  assert runtime.status()['lossLatched']
  runtime.pool.shutdown(wait=False)
