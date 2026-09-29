import time
from types import SimpleNamespace
import numpy as np
import pytest
from third_party.jetlink.spec import ModelSpec
from openpilot.selfdrive.modeld.jetlink.client import CONTRACT
from openpilot.selfdrive.modeld.jetlink.daemon import InferenceServer
from openpilot.selfdrive.modeld.jetlink.rpc import REQUEST


def test_owner_rejects_old_generation_and_never_accepts_longer_frame_budget():
  calls = []
  spec = ModelSpec.from_dict(CONTRACT)
  client = SimpleNamespace(spec=spec, infer=lambda *args: (calls.append(args), np.zeros(18452, np.float32))[1])
  server = InferenceServer(client)
  data = bytes(spec.warped_nbytes + spec.packed_nbytes)
  def request(generation, deadline): return b'I' + REQUEST.pack(generation, 7, deadline, 1) + data
  with pytest.raises(ValueError): server.infer_request(request(bytes(16), time.monotonic_ns() + 40_000_000))
  with pytest.raises(ValueError): server.infer_request(request(server.generation, time.monotonic_ns() + 1_000_000_000))
  assert not calls
  reply, _ = server.infer_request(request(server.generation, time.monotonic_ns() + 40_000_000))
  assert reply[:17] == b'R' + server.generation and len(calls) == 1 and calls[0][-1] is True
