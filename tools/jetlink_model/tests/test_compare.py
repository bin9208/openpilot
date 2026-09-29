import json

import numpy as np
import pytest


def recording(path, values, **updates):
  path.mkdir()
  values = np.asarray(values, dtype='<f4')
  meta = {'model_sha256': 'a'*64, 'source_sha256': 'b'*64, 'inputs_sha256': 'c'*64,
          'frame_ids': list(range(len(values))), 'output_count': values.shape[1],
          'output_slices': {'plan': [0, values.shape[1]]}, 'runtime': 'fixture'}
  meta.update(updates)
  (path/'run.json').write_text(json.dumps(meta))
  (path/'outputs.bin').write_bytes(values.tobytes())
  return path


def test_identical_sequence_passes(tmp_path):
  from tools.jetlink_model.compare import compare
  a = recording(tmp_path/'a', [[1, 2], [3, 4]])
  b = recording(tmp_path/'b', [[1, 2], [3, 4]])
  result = compare(a, b)
  assert result.passed and result.frames == 2
  assert result.slices['plan']['max_abs'] == 0


@pytest.mark.parametrize('case', ['drift', 'nan', 'shape', 'frame', 'hash', 'empty'])
def test_invalid_or_drifting_sequence_fails(tmp_path, case):
  from tools.jetlink_model.compare import compare
  a = recording(tmp_path/'a', [[1, 2], [3, 4]])
  values = [[1, 2], [3, 4]]
  updates = {}
  if case == 'drift': values[1][1] = 4.1
  if case == 'nan': values[0][0] = float('nan')
  if case == 'shape': values = [[1], [2]]
  if case == 'frame': updates['frame_ids'] = [1, 2]
  if case == 'hash': updates['inputs_sha256'] = 'd'*64
  b = recording(tmp_path/'b', values, **updates)
  if case == 'empty': (b/'outputs.bin').write_bytes(b'')
  result = compare(a, b)
  assert not result.passed
  assert result.reasons


def test_matching_nan_sequences_never_pass(tmp_path):
  from tools.jetlink_model.compare import compare
  a = recording(tmp_path/'a', [[float('nan')]])
  b = recording(tmp_path/'b', [[float('nan')]])
  assert not compare(a, b).passed
