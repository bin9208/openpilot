"""Frame-local source selection. Native success never erases an active loss."""
from dataclasses import dataclass
from enum import IntEnum, Enum


def required_process_failures(not_running, external_source):
  """The new owner is optional for native/Shadow; all existing processes stay required."""
  return set(not_running) if external_source else set(not_running) - {'jetlinkd'}


class Mode(IntEnum):
  OFF = 0
  SHADOW = 1
  ACTIVE_REQUEST = 2


class Outcome(Enum):
  NONE = 'none'
  VALID = 'valid'
  LOST = 'lost'


@dataclass(frozen=True)
class ControlState:
  standstill: bool
  cruise_enabled: bool
  lateral_active: bool
  enabled: bool

  @property
  def inactive_stop(self):
    return self.standstill and not (self.cruise_enabled or self.lateral_active or self.enabled)


@dataclass(frozen=True)
class Decision:
  source: str
  phase: str
  loss_latched: bool
  reset_required: bool


class Transition:
  def __init__(self, previously_active=False):
    self.active = False
    self.loss_latched = bool(previously_active)
    # A restart with setting=2 cannot create a new activation request.
    self.armed = False
    self.previous_mode = Mode.ACTIVE_REQUEST

  def update(self, mode, ready, validated, controls, outcome):
    mode = Mode(mode)
    reset = False
    if self.active and (outcome == Outcome.LOST or not ready or not validated or mode != Mode.ACTIVE_REQUEST):
      self.active = False
      self.loss_latched = True
      self.armed = False
    if not self.active and mode != Mode.ACTIVE_REQUEST and controls.inactive_stop:
      # Explicit acknowledgement while stopped and all control is inactive.
      if ready or mode == Mode.OFF:
        self.loss_latched = False
      self.armed = True
    request_edge = mode == Mode.ACTIVE_REQUEST and self.previous_mode != Mode.ACTIVE_REQUEST
    if not self.active and request_edge and self.armed and not self.loss_latched and ready and validated and controls.inactive_stop:
      self.active = True
      reset = True
      self.armed = False
    self.previous_mode = mode
    phase = 'LOST' if self.loss_latched else 'ACTIVE' if self.active else 'OFF' if mode == Mode.OFF else 'READY' if ready else 'PREPARING'
    return Decision('jetlink' if self.active else 'native', phase, self.loss_latched, reset)
