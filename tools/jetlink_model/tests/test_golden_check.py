from pathlib import Path

import numpy as np

FIXTURES = Path(__file__).parents[1] / 'fixtures'


def test_golden_checker_rejects_numerically_different_finite_output(tmp_path):
  from tools.jetlink_model.golden_check import compare_fixture_outputs
  values = np.fromfile(FIXTURES/'tiny_queued.expected.bin', dtype='<f4')
  values[0] += 1
  path = tmp_path/'changed.bin'
  values.tofile(path)
  result = compare_fixture_outputs('queued', path)
  assert result['status'] == 'unverified'
  assert result['max_abs'] == 1


def test_golden_checker_accepts_exact_recording():
  from tools.jetlink_model.golden_check import compare_fixture_outputs
  result = compare_fixture_outputs('stateful', FIXTURES/'tiny_stateful.expected.bin')
  assert result['status'] == 'verified'
  assert result['max_abs'] == 0
