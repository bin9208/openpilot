import hashlib
import json
from pathlib import Path

import numpy as np
import pytest

FIXTURES = Path(__file__).parents[1] / 'fixtures'


def test_fixture_export_verifies_hashes(tmp_path):
  from tools.jetlink_model.export_fixtures import export_fixtures
  result = export_fixtures(tmp_path / 'exported')
  assert len(result) >= 20
  for name, digest in result.items():
    assert hashlib.sha256((tmp_path / 'exported' / name).read_bytes()).hexdigest() == digest


def test_upstream_header_and_infer_bytes():
  from tools.jetlink_model.reference import protocol as p
  fixture = json.loads((FIXTURES / 'conformance/wire.json').read_text())
  for case in fixture['headers']:
    assert p.pack_header(case['msg_type'], case['seq'], case['length'], case['flags'], case['reserved']).hex() == case['hex']
  for case in fixture['infer_req']:
    assert p.pack_infer_req(case['frame_id'], case['flags']).hex() == case['hex']


@pytest.mark.parametrize('skip', [1, 2, 4])
def test_reset_state_and_skip_match_upstream(skip):
  from tools.jetlink_model.reference.queues import PolicyQueues
  from tools.jetlink_model.reference.spec import ModelSpec
  root = FIXTURES / 'conformance'
  fixture = json.loads((root / 'staging.json').read_text())
  case = next(c for c in fixture['cases'] if c['frame_skip'] == skip)
  spec = ModelSpec.from_dict(json.loads((root / case['spec']).read_text()))
  queues = PolicyQueues(spec)
  frames = (root / case['frames']).read_bytes()
  staged = bytearray()
  stride = spec.warped_nbytes + spec.packed_nbytes
  for frame in range(fixture['frames']):
    start = frame * stride
    warped = np.frombuffer(frames, dtype=np.uint8, count=spec.warped_nbytes, offset=start).reshape(spec.warped_shape)
    packed = np.frombuffer(frames, dtype='<f4', count=spec.packed_nelem, offset=start + spec.warped_nbytes)
    if frame == fixture['reset_before']:
      queues.reset()
    feed = queues.step(warped, packed)
    for tensor in case['inputs']:
      staged.extend(feed[tensor['name']].astype('<f2').tobytes())
  assert bytes(staged) == (root / case['staged']).read_bytes()


def test_reference_metadata_uses_restricted_parser(tmp_path):
  from tools.jetlink_model.reference.onnx_meta import OnnxMeta
  meta = OnnxMeta(props={'output_slices': 'not base64'})
  with pytest.raises(ValueError, match='metadata'):
    _ = meta.output_slices
