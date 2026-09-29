"""Bounded, data-only contract shared by the host preparer and Android app."""
from __future__ import annotations

import copy
import hashlib
import json
import math
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

MAX_MODEL_BYTES = 4 * 1024**3
MAX_MANIFEST_BYTES = 64 * 1024
DTYPE_BYTES = {'uint8': 1, 'bool': 1, 'float16': 2, 'float32': 4, 'float64': 8, 'int32': 4, 'int64': 8}


@dataclass(frozen=True)
class ModelPackage:
  manifest: dict
  model_path: Path


def integer(value, low=1, high=MAX_MODEL_BYTES):
  if type(value) is not int or not low <= value <= high:
    raise ValueError('integer outside contract limits')
  return value


def text(value, limit=256):
  if not isinstance(value, str) or not value or len(value) > limit or any(ord(c) < 32 for c in value):
    raise ValueError('invalid contract string')
  return value


def contained_file(root: Path, name: str) -> Path:
  text(name, 1024)
  relative = PurePosixPath(name)
  if relative.is_absolute() or chr(92) in name or ':' in name or any(p in ('.', '..') for p in name.split('/')):
    raise ValueError('artifact path must remain inside the package')
  path = (root / relative).resolve()
  if not path.is_relative_to(root.resolve()) or not path.is_file():
    raise ValueError('artifact path missing or outside package')
  return path


def sha256_file(path: Path) -> str:
  with path.open('rb') as stream:
    return hashlib.file_digest(stream, 'sha256').hexdigest()


def tensor_map(value):
  if not isinstance(value, dict) or not 0 < len(value) <= 128:
    raise ValueError('invalid tensor map')
  for name, tensor in value.items():
    text(name)
    if not isinstance(tensor, dict) or tensor.get('dtype') not in DTYPE_BYTES:
      raise ValueError('unsupported tensor dtype')
    shape = tensor.get('shape')
    if not isinstance(shape, list) or not 1 <= len(shape) <= 8:
      raise ValueError('invalid tensor rank')
    for dim in shape:
      integer(dim, high=2**31-1)
    integer(math.prod(shape) * DTYPE_BYTES[tensor['dtype']])
  return value


def validate_manifest(value: dict, root: Path) -> ModelPackage:
  if not isinstance(value, dict):
    raise ValueError('manifest must be an object')
  try:
    integer(value['schema_version'], 1, 1)
    integer(value['prepare_version'], 1, 1)
    if integer(value['frame_skip'], 1, 4) not in (1, 2, 4):
      raise ValueError('unsupported frame_skip')
    for field in ('source', 'artifact'):
      identity = value[field]
      integer(identity['bytes'])
      if not isinstance(identity['sha256'], str) or not re.fullmatch('[0-9a-f]{64}', identity['sha256']):
        raise ValueError('invalid SHA-256')
    text(value['source']['checkpoint'])
    runtime = value['runtime']
    if runtime['name'] != 'onnxruntime' or runtime['backend'] not in ('cpu', 'nnapi', 'qnn'):
      raise ValueError('unsupported runtime')
    text(runtime['version'], 32)
    inputs, outputs = tensor_map(value['inputs']), tensor_map(value['outputs'])
    if 'outputs' not in outputs or outputs['outputs']['dtype'] not in ('float16', 'float32'):
      raise ValueError('missing floating driving outputs')
    limit = math.prod(outputs['outputs']['shape'])
    slices = value['output_slices']
    if not isinstance(slices, dict) or not slices:
      raise ValueError('missing output slices')
    intervals = []
    for name, bounds in slices.items():
      text(name)
      if not isinstance(bounds, list) or len(bounds) != 2:
        raise ValueError('invalid output slice')
      start, stop = integer(bounds[0], 0, limit), integer(bounds[1], 1, limit)
      if start >= stop:
        raise ValueError('empty or reversed output slice')
      intervals.append((start, stop))
    intervals.sort()
    if any(left[1] > right[0] for left, right in zip(intervals, intervals[1:])):
      raise ValueError('overlapping output slices')
    expected = {name: 'next_' + name for name in inputs if name.startswith('state_')}
    if value['state_pairs'] != expected:
      raise ValueError('incomplete recurrent state mapping')
    for name, output in expected.items():
      if output not in outputs or inputs[name] != outputs[output]:
        raise ValueError('recurrent state shape or dtype mismatch')
    model = contained_file(root, value['artifact']['path'])
    if model.stat().st_size != value['artifact']['bytes'] or sha256_file(model) != value['artifact']['sha256']:
      raise ValueError('artifact size or SHA-256 mismatch')
  except (KeyError, TypeError, AttributeError) as exc:
    raise ValueError('incomplete manifest contract') from exc
  return ModelPackage(copy.deepcopy(value), model)


def read_manifest(path: Path) -> ModelPackage:
  if path.stat().st_size > MAX_MANIFEST_BYTES:
    raise ValueError('manifest too large')
  def unique(pairs):
    result = {}
    for key, value in pairs:
      if key in result:
        raise ValueError('duplicate manifest key')
      result[key] = value
    return result
  value = json.loads(path.read_text(encoding='utf-8'), object_pairs_hook=unique)
  return validate_manifest(value, path.parent)
