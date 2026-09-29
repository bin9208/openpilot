"""Prepare a self-contained ONNX package without executing model metadata."""
from __future__ import annotations

import argparse
import base64
import io
import json
import pickle
import tempfile
from pathlib import Path

from tools.jetlink_model.manifest import (MAX_MANIFEST_BYTES, MAX_MODEL_BYTES, ModelPackage, contained_file,
                                          integer, sha256_file, validate_manifest)


class _SlicesUnpickler(pickle.Unpickler):
  def find_class(self, module, name):
    if module == 'builtins' and name == 'slice':
      return slice
    raise ValueError('forbidden metadata global')


def output_slices(encoded: str) -> dict:
  try:
    if len(encoded) > MAX_MANIFEST_BYTES:
      raise ValueError('metadata too large')
    # Python codecs.encode(..., 'base64'), used by the official exporter,
    # inserts line breaks. Only discard ASCII whitespace before strict decoding.
    compact = encoded.translate(str.maketrans('', '', ' \t\r\n'))
    stream = io.BytesIO(base64.b64decode(compact, validate=True))
    value = _SlicesUnpickler(stream).load()
    if stream.read(1) or not isinstance(value, dict) or not value:
      raise ValueError('invalid metadata map')
    result = {}
    for name, item in value.items():
      if not isinstance(name, str) or type(item) is not slice or item.step not in (None, 1):
        raise ValueError('invalid metadata slice')
      result[name] = [integer(item.start, 0), integer(item.stop)]
    return result
  except Exception as exc:
    raise ValueError('invalid output_slices metadata') from exc


def _tensors(graph):
  import onnx
  yield from graph.initializer
  for sparse in graph.sparse_initializer:
    yield sparse.values
    yield sparse.indices
  for node in graph.node:
    for attr in node.attribute:
      if attr.type == onnx.AttributeProto.TENSOR:
        yield attr.t
      elif attr.type == onnx.AttributeProto.TENSORS:
        yield from attr.tensors
      elif attr.type == onnx.AttributeProto.GRAPH:
        yield from _tensors(attr.g)
      elif attr.type == onnx.AttributeProto.GRAPHS:
        for nested in attr.graphs:
          yield from _tensors(nested)


def _load_source(source: Path):
  import onnx
  integer(source.stat().st_size)
  model = onnx.load(str(source), load_external_data=False)
  # Cinque V3 declares Contiguous as a function. Accept only the verified
  # identity definition; never silently erase arithmetic in a custom function.
  for function in model.functions:
    if (function.domain != 'org.tinygrad' or function.name != 'Contiguous'
        or len(function.input) != 1 or len(function.output) != 1 or len(function.node) != 1
        or function.attribute or function.attribute_proto):
      raise ValueError('unsupported ONNX local function')
    node = function.node[0]
    if (node.domain or node.op_type != 'Identity' or node.attribute
        or list(node.input) != list(function.input) or list(node.output) != list(function.output)):
      raise ValueError('Contiguous function is not an identity')
  files = {source.resolve()}
  for tensor in _tensors(model.graph):
    if tensor.data_location != onnx.TensorProto.EXTERNAL:
      continue
    entries = {entry.key: entry.value for entry in tensor.external_data}
    if len(entries) != len(tensor.external_data) or 'location' not in entries:
      raise ValueError('invalid external data descriptor')
    try:
      files.add(contained_file(source.parent, entries['location']))
      for key in ('offset', 'length'):
        if key in entries:
          integer(int(entries[key]), 0)
    except (ValueError, OSError) as exc:
      raise ValueError('unsafe external data path or range') from exc
  if sum(path.stat().st_size for path in files) > MAX_MODEL_BYTES:
    raise ValueError('external data exceeds model size budget')
  onnx.external_data_helper.load_external_data_for_model(model, str(source.parent))
  onnx.external_data_helper.convert_model_from_external_data(model)
  return model


def prepare(source: Path, destination: Path, frame_skip: int = 4) -> ModelPackage:
  import onnx
  import onnxruntime as ort
  source, destination = Path(source).resolve(), Path(destination).absolute()
  if destination.exists():
    raise FileExistsError(destination)
  if source.suffix.lower() != '.onnx':
    raise ValueError('an ONNX export is required; PKL files cannot be imported')
  model = _load_source(source)
  props = {item.key: item.value for item in model.metadata_props}
  if len(props) != len(model.metadata_props):
    raise ValueError('duplicate model metadata')
  slices = output_slices(props.get('output_slices', ''))
  checkpoint = props.get('model_checkpoint')
  if not isinstance(checkpoint, str) or not checkpoint:
    raise ValueError('missing source checkpoint metadata')
  # Keep a supported declared identity function byte-for-byte. Erasing the
  # function can change ORT's fp16 fusion path despite algebraic equivalence.
  declared = {(function.domain, function.name) for function in model.functions}
  for node in model.graph.node:
    if node.domain == 'org.tinygrad' and node.op_type == 'Contiguous':
      if len(node.input) != 1 or len(node.output) != 1 or node.attribute:
        raise ValueError('invalid Contiguous node')
      if (node.domain, node.op_type) not in declared:
        node.domain, node.op_type = '', 'Identity'
  destination.parent.mkdir(parents=True, exist_ok=True)
  with tempfile.TemporaryDirectory(prefix='.jetlink-', dir=destination.parent) as temporary:
    root = Path(temporary)
    artifact = root / 'model.onnx'
    onnx.save(model, str(artifact))
    integer(artifact.stat().st_size)
    onnx.checker.check_model(str(artifact))
    ort.disable_telemetry_events()
    session = ort.InferenceSession(str(artifact), providers=['CPUExecutionProvider'])
    dtypes = {'tensor(float)': 'float32', 'tensor(float16)': 'float16', 'tensor(double)': 'float64',
              'tensor(uint8)': 'uint8', 'tensor(int32)': 'int32', 'tensor(int64)': 'int64', 'tensor(bool)': 'bool'}
    def describe(tensors):
      return {t.name: {'dtype': dtypes.get(t.type, 'unsupported'), 'shape': list(t.shape)} for t in tensors}
    inputs, outputs = describe(session.get_inputs()), describe(session.get_outputs())
    del session
    manifest = {'schema_version': 1, 'prepare_version': 1,
                'source': {'checkpoint': checkpoint, 'sha256': sha256_file(source), 'bytes': source.stat().st_size},
                'artifact': {'path': 'model.onnx', 'sha256': sha256_file(artifact), 'bytes': artifact.stat().st_size},
                'runtime': {'name': 'onnxruntime', 'version': ort.__version__, 'backend': 'cpu'},
                'frame_skip': frame_skip, 'inputs': inputs, 'outputs': outputs, 'output_slices': slices,
                'state_pairs': {name: 'next_' + name for name in inputs if name.startswith('state_')}}
    validate_manifest(manifest, root)
    encoded = json.dumps(manifest, indent=2, ensure_ascii=False).encode('utf-8')
    if len(encoded) > MAX_MANIFEST_BYTES:
      raise ValueError('manifest too large')
    (root / 'manifest.json').write_bytes(encoded)
    root.rename(destination)
  return validate_manifest(manifest, destination)


def main():
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument('source', type=Path)
  parser.add_argument('destination', type=Path)
  parser.add_argument('--frame-skip', type=int, choices=(1, 2, 4), default=4)
  args = parser.parse_args()
  result = prepare(args.source, args.destination, args.frame_skip)
  print(json.dumps(result.manifest, indent=2))


if __name__ == '__main__':
  main()
