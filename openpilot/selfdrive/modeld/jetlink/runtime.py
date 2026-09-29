"""Overlap external inference with a continuously warm native model.

Off performs no USB work. Shadow never waits for an external result to publish
native output. Active waits only for the remainder of the complete 50ms budget.
"""
from concurrent.futures import ThreadPoolExecutor
import time

from openpilot.selfdrive.modeld.jetlink.model_state import JetlinkModelState
from openpilot.selfdrive.modeld.jetlink.rpc import ProxyClient
from openpilot.selfdrive.modeld.jetlink.transition import Transition, Mode, Outcome, Decision
from openpilot.selfdrive.modeld.jetlink.validation import validation_matches


class Runtime:
  def __init__(self, params, camera_size, warp, client_factory=ProxyClient):
    self.params, self.camera_size, self.warp = params, camera_size, warp
    self.client_factory = client_factory
    self.transition = Transition(params.get_bool('JetlinkActive') or params.get_bool('JetlinkLossLatched'))
    self.decision = Decision('native', 'OFF', self.transition.loss_latched, False)
    self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='jetlink-rpc')
    self.connecting = self.pending = self.model = None
    self.retry_at = 0
    self.mode = Mode.OFF
    self.validated = False
    self.generation = ''
    self.elapsed_ms = 0.0
    self.external_frame = 0
    self.frame_id = 0
    self.started = self.deadline = 0
    self.detail = ''
    self._stored = None

  def _connect(self):
    from third_party.jetlink.transport.priority import background_thread
    background_thread()
    client = self.client_factory()
    try:
      spec = client.connect()
      return JetlinkModelState(*self.camera_size, client, spec, self.warp)
    except Exception:
      client.close()
      raise

  def _persist(self):
    state = self.decision.source == 'jetlink', self.decision.loss_latched
    if state != self._stored:
      self.params.put_bool('JetlinkActive', state[0])
      self.params.put_bool('JetlinkLossLatched', state[1])
      self._stored = state

  def _lost(self, error):
    self.detail = str(error)[:240]
    self.decision = self.transition.update(self.mode, False, self.validated, self.controls, Outcome.LOST)
    if self.model is not None:
      self.model.close()
    self.model = None
    self.pending = None
    self.retry_at = time.monotonic() + 5
    self._persist()

  def begin(self, mode, controls, bufs, transforms, inputs, frame_id, prepare_only, camera_ready=True):
    self.started = time.monotonic_ns()
    self.deadline = self.started + 50_000_000
    self.frame_id, self.controls = frame_id, controls
    self.mode = Mode(mode) if mode in (0, 1, 2) else Mode.OFF
    # Collect a previous Shadow frame without waiting for it.
    if self.pending is not None and self.pending.done():
      try:
        _, ended, started, frame = self.pending.result()
        self.elapsed_ms = (ended - started) / 1e6
        self.external_frame = frame
      except Exception as exc:
        self._lost(exc)
      self.pending = None
    if self.connecting is not None and self.connecting.done():
      try:
        self.model = self.connecting.result()
        self.generation = self.model.client.generation.hex()
        self.detail = ''
      except Exception as exc:
        self.detail = str(exc)[:240]
        self.retry_at = time.monotonic() + 5
      self.connecting = None
    if self.mode == Mode.OFF:
      if self.model is not None:
        self.model.close()
        self.model = None
    elif self.model is None and self.connecting is None and time.monotonic() >= self.retry_at:
      self.connecting = self.pool.submit(self._connect)
    ready = self.model is not None and not self.model.client.dead and camera_ready and self.pending is None
    identity = self.model.client.identity if self.model is not None else {}
    self.validated = validation_matches(self.params.get('JetlinkValidation'), identity)
    self.decision = self.transition.update(self.mode, ready, self.validated, controls, Outcome.NONE)
    self._persist()
    if not ready or self.mode == Mode.OFF:
      return
    try:
      if self.decision.reset_required:
        self.model.reset_next = True
        self.model.prev_desire[:] = 0
      prepared = self.model.prepare(bufs, transforms, inputs, prepare_only)
      if prepared is not None:
        model, deadline, started = self.model, self.deadline, self.started
        def infer():
          result = model.infer_prepared(frame_id, prepared, deadline)
          return result, time.monotonic_ns(), started, frame_id
        self.pending = self.pool.submit(infer)
    except Exception as exc:
      self._lost(exc)

  def finish(self, native_output):
    if native_output is None or self.decision.source != 'jetlink':
      return native_output
    try:
      if self.pending is None:
        raise RuntimeError('No external frame available')
      output, ended, started, frame = self.pending.result(timeout=max(0, (self.deadline - time.monotonic_ns()) / 1e9))
      self.pending = None
      if frame != self.frame_id or time.monotonic_ns() > self.deadline:
        raise TimeoutError('Complete Jetlink frame deadline exceeded')
      self.elapsed_ms = (ended - started) / 1e6
      self.external_frame = frame
      return output
    except Exception as exc:
      self._lost(exc)
      return native_output

  def valid_at_publish(self):
    if self.decision.source == 'jetlink' and time.monotonic_ns() > self.deadline:
      self._lost('Jetlink publication exceeded 50ms')
      return False
    # A timeout followed by native fallback must not relabel this old frame as fresh.
    return not (self.decision.loss_latched and time.monotonic_ns() > self.deadline)

  def status(self):
    return {'source': self.decision.source, 'phase': self.decision.phase,
            'lossLatched': self.decision.loss_latched, 'generation': self.generation,
            'frameId': self.external_frame, 'executionMs': self.elapsed_ms, 'validated': self.validated}
