"""Report upstream tiny-model numerical conformance; a finite result is insufficient."""
import argparse
import json
from pathlib import Path

import numpy as np


def compare_fixture_outputs(layout: str, candidate: Path) -> dict:
  if layout not in ('queued', 'stateful'):
    raise ValueError('unknown fixture layout')
  golden = Path(__file__).with_name('fixtures') / f'tiny_{layout}.expected.bin'
  report = {'layout': layout, 'status': 'unverified', 'atol': 1e-5, 'rtol': 1e-4,
            'reference': 'upstream Apple arm64 ORT1.29.0 prepared graph, f10f4705', 'max_abs': None}
  if candidate.stat().st_size != golden.stat().st_size:
    report['reason'] = 'output byte count mismatch'
    return report
  reference = np.fromfile(golden, dtype='<f4').astype(np.float64)
  actual = np.fromfile(candidate, dtype='<f4').astype(np.float64)
  if not np.all(np.isfinite(actual)):
    report['reason'] = 'nonfinite output'
    return report
  report['max_abs'] = float(np.max(np.abs(actual - reference)))
  if np.allclose(actual, reference, rtol=report['rtol'], atol=report['atol']):
    report['status'] = 'verified'
  else:
    report['reason'] = 'numerical tolerance exceeded; do not certify this backend/layout'
  return report


def main():
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument('--layout', choices=('queued', 'stateful'), required=True)
  parser.add_argument('--candidate', type=Path, required=True)
  parser.add_argument('--report', type=Path, required=True)
  args = parser.parse_args()
  report = compare_fixture_outputs(args.layout, args.candidate)
  args.report.write_text(json.dumps(report, indent=2, allow_nan=False), encoding='utf-8')
  print(report['status'])
  raise SystemExit(0 if report['status'] == 'verified' else 1)


if __name__ == '__main__':
  main()
