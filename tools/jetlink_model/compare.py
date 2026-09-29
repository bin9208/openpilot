"""Compare identified multi-frame inference recordings; return nonzero on failure."""
from __future__ import annotations

import argparse
import json
import math
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np

from tools.jetlink_model.manifest import MAX_MANIFEST_BYTES, MAX_MODEL_BYTES, integer


@dataclass
class ComparisonReport:
  passed: bool = False
  frames: int = 0
  reasons: list[str] = field(default_factory=list)
  slices: dict = field(default_factory=dict)
  identities: dict = field(default_factory=dict)
  atol: float = 1e-5
  rtol: float = 1e-4


def _read(root: Path):
  path = root / 'run.json'
  if path.stat().st_size > MAX_MANIFEST_BYTES:
    raise ValueError('recording metadata too large')
  meta = json.loads(path.read_text(encoding='utf-8'))
  for key in ('model_sha256', 'source_sha256', 'inputs_sha256'):
    if not re.fullmatch('[0-9a-f]{64}', meta[key]):
      raise ValueError('invalid recording identity')
  ids = meta['frame_ids']
  if not isinstance(ids, list) or not 0 < len(ids) <= 4096:
    raise ValueError('invalid frame count')
  for frame in ids:
    integer(frame, 0, 2**32-1)
  if len(set(ids)) != len(ids):
    raise ValueError('duplicate frame id')
  count = integer(meta['output_count'], 1, 2**20)
  expected = len(ids) * count * 4
  if expected > MAX_MODEL_BYTES or (root / 'outputs.bin').stat().st_size != expected:
    raise ValueError('output byte count mismatch')
  intervals = []
  if not isinstance(meta['output_slices'], dict) or not meta['output_slices']:
    raise ValueError('missing output slices')
  for name, bounds in meta['output_slices'].items():
    if not isinstance(name, str) or not isinstance(bounds, list) or len(bounds) != 2:
      raise ValueError('invalid recording slice')
    start, stop = integer(bounds[0], 0, count), integer(bounds[1], 1, count)
    if start >= stop:
      raise ValueError('invalid recording slice bounds')
    intervals.append((start, stop))
  intervals.sort()
  if any(a[1] > b[0] for a, b in zip(intervals, intervals[1:])):
    raise ValueError('overlapping recording slices')
  values = np.fromfile(root / 'outputs.bin', dtype='<f4').reshape(len(ids), count)
  if not np.all(np.isfinite(values)):
    raise ValueError('nonfinite output')
  return meta, values


def compare(reference: Path, candidate: Path, atol: float = 1e-5, rtol: float = 1e-4) -> ComparisonReport:
  if not all(math.isfinite(v) and v >= 0 for v in (atol, rtol)):
    raise ValueError('invalid comparison tolerance')
  report = ComparisonReport(atol=atol, rtol=rtol)
  try:
    ref_meta, ref = _read(Path(reference))
    got_meta, got = _read(Path(candidate))
    report.frames = len(ref_meta['frame_ids'])
    report.identities = {'reference': ref_meta, 'candidate': got_meta}
    for key in ('model_sha256', 'source_sha256', 'inputs_sha256', 'frame_ids', 'output_count', 'output_slices'):
      if ref_meta[key] != got_meta[key]:
        report.reasons.append(f'{key} mismatch')
    if report.reasons:
      return report
    for name, (start, stop) in ref_meta['output_slices'].items():
      a, b = ref[:, start:stop].astype(np.float64), got[:, start:stop].astype(np.float64)
      error = np.abs(a - b)
      report.slices[name] = {'max_abs': float(error.max()),
                             'max_relative': float((error / np.maximum(np.abs(a), 1e-12)).max())}
    # Compare every element, including gaps between named output slices.
    if not np.allclose(ref, got, rtol=rtol, atol=atol, equal_nan=False):
      report.reasons.append('numerical tolerance exceeded')
    report.passed = not report.reasons
  except (ValueError, KeyError, TypeError, OSError) as exc:
    report.reasons.append(str(exc))
  return report


def main():
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument('--reference', type=Path, required=True)
  parser.add_argument('--candidate', type=Path, required=True)
  parser.add_argument('--output', type=Path, required=True)
  args = parser.parse_args()
  report = compare(args.reference, args.candidate)
  args.output.write_text(json.dumps(asdict(report), indent=2, allow_nan=False), encoding='utf-8')
  print('PASS' if report.passed else 'FAIL', ', '.join(report.reasons))
  raise SystemExit(0 if report.passed else 1)


if __name__ == '__main__':
  main()
