import json
from pathlib import Path

import numpy as np
import pytest

FIXTURES = Path(__file__).parents[1] / 'fixtures'


@pytest.mark.parametrize('layout', ['queued', 'stateful'])
def test_actual_tiny_model_sequence_and_reset(tmp_path, layout):
  from tools.jetlink_model.prepare import prepare
  from tools.jetlink_model.runner import run_sequence
  package = prepare(FIXTURES / f'tiny_{layout}.onnx', tmp_path/'model')
  run_sequence(package, FIXTURES/f'tiny_{layout}.frames.bin', tmp_path/'run')
  meta = json.loads((tmp_path/'run/run.json').read_text())
  output = np.fromfile(tmp_path/'run/outputs.bin', dtype='<f4')
  assert meta['frame_ids'] == list(range(8))
  assert len(output) == 8 * 64
  assert np.all(np.isfinite(output))
  run_sequence(package, FIXTURES/f'tiny_{layout}.frames.bin', tmp_path/'again')
  assert (tmp_path/'again/outputs.bin').read_bytes() == (tmp_path/'run/outputs.bin').read_bytes()


def test_partial_frame_never_publishes_recording(tmp_path):
  from tools.jetlink_model.prepare import prepare
  from tools.jetlink_model.runner import run_sequence
  package = prepare(FIXTURES/'tiny_stateful.onnx', tmp_path/'model')
  (tmp_path/'broken.bin').write_bytes(b'bad')
  with pytest.raises(ValueError, match='frame'):
    run_sequence(package, tmp_path/'broken.bin', tmp_path/'run')
  assert not (tmp_path/'run').exists()
