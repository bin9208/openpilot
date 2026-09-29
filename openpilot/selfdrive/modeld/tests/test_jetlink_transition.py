from openpilot.selfdrive.modeld.jetlink.transition import Transition, ControlState, Mode, Outcome, required_process_failures

STOP = ControlState(True, False, False, False)
DRIVE = ControlState(False, True, True, True)


def test_optional_owner_failure_does_not_disable_native_shadow():
  assert required_process_failures({'jetlinkd'}, False) == set()
  assert required_process_failures({'jetlinkd', 'modeld'}, False) == {'modeld'}
  assert required_process_failures({'jetlinkd'}, True) == {'jetlinkd'}


def activate(state):
  state.update(Mode.SHADOW, True, True, STOP, Outcome.NONE)
  return state.update(Mode.ACTIVE_REQUEST, True, True, STOP, Outcome.VALID)


def test_shadow_never_changes_control():
  state = Transition()
  assert state.update(Mode.SHADOW, True, True, DRIVE, Outcome.VALID).source == 'native'
  assert not state.update(Mode.SHADOW, False, True, DRIVE, Outcome.LOST).loss_latched


def test_ready_without_validation_stays_native():
  state = Transition()
  state.update(Mode.SHADOW, True, False, STOP, Outcome.NONE)
  assert state.update(Mode.ACTIVE_REQUEST, True, False, STOP, Outcome.VALID).source == 'native'


def test_lateral_only_blocks_switch():
  state = Transition()
  state.update(Mode.SHADOW, True, True, STOP, Outcome.NONE)
  assert state.update(Mode.ACTIVE_REQUEST, True, True, ControlState(True, False, True, False), Outcome.VALID).source == 'native'


def test_single_late_frame_latches_loss_and_native_success_cannot_clear():
  state = Transition()
  assert activate(state).source == 'jetlink'
  lost = state.update(Mode.ACTIVE_REQUEST, False, True, DRIVE, Outcome.LOST)
  assert lost.loss_latched and lost.source == 'native'
  assert state.update(Mode.ACTIVE_REQUEST, True, True, DRIVE, Outcome.VALID).loss_latched
  assert state.update(Mode.OFF, True, True, DRIVE, Outcome.VALID).loss_latched


def test_reconnect_requires_manual_inactive_stop():
  state = Transition(); activate(state)
  state.update(Mode.ACTIVE_REQUEST, False, True, DRIVE, Outcome.LOST)
  assert state.update(Mode.ACTIVE_REQUEST, True, True, STOP, Outcome.VALID).loss_latched
  cleared = state.update(Mode.SHADOW, True, True, STOP, Outcome.NONE)
  assert not cleared.loss_latched and cleared.source == 'native'
  assert state.update(Mode.ACTIVE_REQUEST, True, True, STOP, Outcome.VALID).reset_required


def test_owner_death_or_process_restart_does_not_auto_activate():
  state = Transition(previously_active=True)
  decision = state.update(Mode.ACTIVE_REQUEST, True, True, STOP, Outcome.VALID)
  assert decision.source == 'native' and decision.loss_latched
  fresh = Transition()
  assert fresh.update(Mode.ACTIVE_REQUEST, True, True, STOP, Outcome.VALID).source == 'native'


def test_setting_change_or_validation_loss_while_active_latches():
  state = Transition(); activate(state)
  assert state.update(Mode.SHADOW, True, True, DRIVE, Outcome.VALID).loss_latched
  state = Transition(); activate(state)
  assert state.update(Mode.ACTIVE_REQUEST, True, False, DRIVE, Outcome.VALID).loss_latched
