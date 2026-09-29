import pytest
from openpilot.selfdrive.modeld.jetlink.owner import GadgetOwner


def test_onroad_gadget_change_refused():
  calls = []; owner = GadgetOwner(lambda: calls.append('setup'), lambda: calls.append('close'))
  with pytest.raises(RuntimeError, match='offroad'): owner.enable('shadow', False, False)
  assert not calls


def test_egpu_port_conflict():
  calls = []; owner = GadgetOwner(lambda: calls.append('setup'), lambda: calls.append('close'))
  with pytest.raises(RuntimeError, match='eGPU'): owner.enable('shadow', True, True)
  assert not calls


def test_owner_preserves_onroad_binding_until_offroad():
  calls = []; owner = GadgetOwner(lambda: calls.append('setup'), lambda: calls.append('close'))
  owner.enable('shadow', True, False)
  owner.enable('shadow', False, False)
  assert calls == ['setup']
  with pytest.raises(RuntimeError, match='offroad'): owner.disable(False)
  owner.disable(True)
  assert calls == ['setup', 'close']
