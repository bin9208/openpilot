import hashlib
import json
from copy import deepcopy
from pathlib import Path

import pytest


def package(root):
  data = b'fixture artifact'
  (root / 'model.onnx').write_bytes(data)
  digest = hashlib.sha256(data).hexdigest()
  return {'schema_version': 1, 'source': {'checkpoint': 'test-checkpoint', 'sha256': digest, 'bytes': len(data)},
          'artifact': {'path': 'model.onnx', 'sha256': digest, 'bytes': len(data)},
          'runtime': {'name': 'onnxruntime', 'version': '1.22.0', 'backend': 'cpu'}, 'prepare_version': 1,
          'frame_skip': 4, 'inputs': {'new_img': {'dtype': 'uint8', 'shape': [2, 6, 2, 2]},
                                    'state_x': {'dtype': 'float32', 'shape': [1, 4]}},
          'outputs': {'outputs': {'dtype': 'float32', 'shape': [1, 4]},
                      'next_state_x': {'dtype': 'float32', 'shape': [1, 4]}},
          'output_slices': {'plan': [0, 4]}, 'state_pairs': {'state_x': 'next_state_x'}}


def test_valid_package_roundtrip(tmp_path):
  from tools.jetlink_model.manifest import validate_manifest
  value = package(tmp_path)
  parsed = validate_manifest(value, tmp_path)
  assert parsed.manifest == value
  assert parsed.model_path == tmp_path / 'model.onnx'


@pytest.mark.parametrize('change', ['hash', 'size', 'slice', 'overflow', 'state', 'bool', 'skip', 'path', 'version'])
def test_invalid_contract_rejected(tmp_path, change):
  from tools.jetlink_model.manifest import validate_manifest
  value = package(tmp_path)
  if change == 'hash': value['artifact']['sha256'] = '0' * 64
  if change == 'size': value['artifact']['bytes'] += 1
  if change == 'slice': value['output_slices']['other'] = [2, 4]
  if change == 'overflow': value['inputs']['new_img']['shape'] = [2**63, 6]
  if change == 'state': value['outputs']['next_state_x']['shape'] = [1, 8]
  if change == 'bool': value['schema_version'] = True
  if change == 'skip': value['frame_skip'] = 3
  if change == 'path': value['artifact']['path'] = '../model.onnx'
  if change == 'version': value['schema_version'] = 2
  with pytest.raises(ValueError): validate_manifest(value, tmp_path)


def test_external_symlink_escape(tmp_path):
  from tools.jetlink_model.manifest import validate_manifest
  root = tmp_path / 'package'
  root.mkdir()
  value = package(root)
  (root / 'model.onnx').unlink()
  target = tmp_path / 'outside.onnx'
  target.write_bytes(b'fixture artifact')
  try: (root / 'model.onnx').symlink_to(target)
  except OSError: pytest.skip('host does not permit symlinks')
  with pytest.raises(ValueError): validate_manifest(value, root)


def test_oversize_before_file_read(tmp_path):
  from tools.jetlink_model.manifest import validate_manifest
  value = package(tmp_path)
  value['artifact']['bytes'] = 4 * 1024**3 + 1
  with pytest.raises(ValueError): validate_manifest(value, tmp_path)


def test_validator_does_not_mutate_input(tmp_path):
  from tools.jetlink_model.manifest import validate_manifest
  value = package(tmp_path)
  original = deepcopy(value)
  parsed = validate_manifest(value, tmp_path)
  parsed.manifest['source']['checkpoint'] = 'changed'
  assert value == original


def test_json_schema_accepts_contract_and_rejects_unbounded_shape(tmp_path):
  import jsonschema
  schema = json.loads((Path(__file__).parents[1] / 'manifest.schema.json').read_text())
  value = package(tmp_path)
  jsonschema.validate(value, schema)
  value['inputs']['new_img']['shape'] = [-1, 4]
  with pytest.raises(jsonschema.ValidationError): jsonschema.validate(value, schema)


def test_read_manifest_rejects_duplicate_keys(tmp_path):
  from tools.jetlink_model.manifest import read_manifest
  path = tmp_path / 'manifest.json'
  path.write_text('{"schema_version":1,"schema_version":2}')
  with pytest.raises(ValueError, match='duplicate'): read_manifest(path)


def test_read_manifest_rejects_large_json_before_parse(tmp_path):
  from tools.jetlink_model.manifest import read_manifest
  path = tmp_path / 'manifest.json'
  path.write_bytes(b' ' * (64 * 1024 + 1))
  with pytest.raises(ValueError, match='large'): read_manifest(path)
