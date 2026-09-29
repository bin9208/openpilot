import json
from dataclasses import replace
import time
import numpy as np
import pytest

from openpilot.selfdrive.modeld.jetlink.client import JetlinkClient, CONTRACT


class Peer:
  dead = False
  def hello(self): return {'protocol': 2, 'runtime_version': '1.22.0', 'backend': 'ort', 'device': 'android-arm64',
                          'telemetry': {'artifact_sha256': CONTRACT['sha256'], 'source_sha256': CONTRACT['sha256']}}
  def ensure_engine(self, *args, **kwargs):
    from third_party.jetlink.spec import ModelSpec
    return ModelSpec.from_dict(CONTRACT)
  def infer(self, warped, packed, frame_id, reset, deadline): return np.zeros(CONTRACT['output_shapes']['outputs'][-1], np.float32)
  def close(self): self.dead = True


def test_exact_manifest_contract_and_reset():
  peer = Peer(); client = JetlinkClient(peer)
  assert client.connect().sha256 == CONTRACT['sha256']
  output = client.infer(7, bytes(client.spec.warped_nbytes), np.zeros(client.spec.packed_nelem, np.float32), time.monotonic_ns() + 50_000_000, True)
  assert output.size == 18452


def test_wrong_hash_rejected():
  peer = Peer()
  original = peer.ensure_engine
  def wrong(*args, **kwargs):
    return replace(original(), sha256='0' * 64)
  peer.ensure_engine = wrong
  client = JetlinkClient(peer)
  with pytest.raises(ValueError, match='contract'): client.connect()
  assert peer.dead


@pytest.mark.parametrize('artifact', [None, 'a' * 64])
def test_different_or_unidentified_executed_artifact_is_rejected(artifact):
  peer = Peer(); hello = peer.hello()
  hello['telemetry']['artifact_sha256'] = artifact
  peer.hello = lambda: hello
  with pytest.raises(ValueError, match='artifact'): JetlinkClient(peer).connect()
  assert peer.dead


@pytest.mark.parametrize('fault', ['timeout', 'nan', 'short'])
def test_failed_frame_permanently_closes_client(fault, monkeypatch):
  clock = [1_000_000_000]
  monkeypatch.setattr(time, 'monotonic_ns', lambda: clock[0])
  peer = Peer(); client = JetlinkClient(peer); client.connect()
  def infer(*args, **kwargs):
    if fault == 'timeout': clock[0] += 12_000_000
    result = np.zeros(18452 if fault != 'short' else 5, np.float32)
    if fault == 'nan': result[0] = np.nan
    return result
  peer.infer = infer
  with pytest.raises((TimeoutError, ValueError)):
    client.infer(2, bytes(client.spec.warped_nbytes), np.zeros(client.spec.packed_nelem, np.float32), time.monotonic_ns() + 5_000_000)
  assert peer.dead
  with pytest.raises(RuntimeError): client.infer(3, b'', np.zeros(1), time.monotonic_ns() + 50_000_000)
