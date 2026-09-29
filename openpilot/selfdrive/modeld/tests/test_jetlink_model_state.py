import time
import numpy as np
import pytest
from third_party.jetlink.spec import ModelSpec
from openpilot.selfdrive.modeld.jetlink.client import CONTRACT
from openpilot.selfdrive.modeld.jetlink.model_state import JetlinkModelState


class Client:
  def __init__(self): self.calls = []; self.dead = False
  def infer(self, frame, warped, packed, deadline, reset):
    self.calls.append((frame, warped, packed.copy(), reset))
    return np.zeros(18452, np.float32)
  def close(self): self.dead = True


def create():
  client = Client()
  image = np.zeros((2, 6, 128, 256), np.uint8)
  model = JetlinkModelState(1928, 1208, client, ModelSpec.from_dict(CONTRACT), lambda b, t: image)
  inputs = {'desire_pulse': np.array([0, 1, 0, 0, 0, 0, 0, 0], np.float32),
            'traffic_convention': np.array([1, 0], np.float32), 'action_t': np.array([.1, .3], np.float32)}
  return client, model, inputs


def test_camera_contract_and_first_frame_reset():
  client, model, inputs = create()
  assert model.vision_input_names == ['img', 'big_img']
  out = model.run({}, {}, inputs, False, frame_id=7)
  assert out['plan'].shape == (1, 33, 15)
  assert client.calls[0][3] is True
  np.testing.assert_array_equal(client.calls[0][2], np.array([0, 1, 0, 0, 0, 0, 0, 0, 1, 0, .1, .3], np.float32))
  model.run({}, {}, inputs, False, frame_id=8)
  assert client.calls[1][3] is False and client.calls[1][2][1] == 0


def test_prepare_only_never_publishes_and_next_request_resets():
  client, model, inputs = create()
  assert model.run({}, {}, inputs, True, frame_id=1) is None
  assert not client.calls
  model.run({}, {}, inputs, False, frame_id=2)
  assert client.calls[0][3]


def test_late_output_discarded_and_bad_slices_rejected(monkeypatch):
  client, model, inputs = create()
  clock = [1_000_000_000]
  monkeypatch.setattr(time, 'monotonic_ns', lambda: clock[0])
  def late(*args): clock[0] += 51_000_000; return np.zeros(18452, np.float32)
  client.infer = late
  with pytest.raises(TimeoutError): model.run({}, {}, inputs, False)
  broken = {**CONTRACT, 'output_slices': {**CONTRACT['output_slices'], 'plan': [0, 1]}}
  with pytest.raises(ValueError): JetlinkModelState(1928, 1208, Client(), ModelSpec.from_dict(broken), lambda b, t: None)
