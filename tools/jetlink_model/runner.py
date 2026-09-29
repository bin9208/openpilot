"""Run recorded warped frames through the real CPU backend for parity checks."""
from __future__ import annotations

import argparse
import json
import platform
import tempfile
import time
from pathlib import Path

import numpy as np
import onnxruntime as ort

from tools.jetlink_model.manifest import MAX_MANIFEST_BYTES, MAX_MODEL_BYTES, ModelPackage, read_manifest, sha256_file, validate_manifest
from tools.jetlink_model.reference.queues import for_model
from tools.jetlink_model.reference.spec import ModelSpec


def model_spec(package: ModelPackage) -> ModelSpec:
  m = package.manifest
  return ModelSpec.from_dict({'sha256': m['source']['sha256'], 'nbytes': m['source']['bytes'],
                             'frame_skip': m['frame_skip'], 'checkpoint': m['source']['checkpoint'],
                             'input_shapes': {k: v['shape'] for k, v in m['inputs'].items()},
                             'output_shapes': {k: v['shape'] for k, v in m['outputs'].items()},
                             'output_slices': m['output_slices']})


class _Engine:
  def __init__(self, package):
    self.feed = {name: np.zeros(info['shape'], dtype=info['dtype']) for name, info in package.manifest['inputs'].items()}

  def host_input(self, name):
    return self.feed[name]


def run_sequence(package: ModelPackage, frames: Path, destination: Path, source_model: Path | None = None) -> dict:
  package = validate_manifest(package.manifest, package.model_path.parent)
  destination = Path(destination).absolute()
  if destination.exists():
    raise FileExistsError(destination)
  spec = model_spec(package)
  stride = spec.warped_nbytes + spec.packed_nbytes
  size = frames.stat().st_size
  if not 0 < size <= MAX_MODEL_BYTES or stride <= 0 or size % stride or size // stride > 4096:
    raise ValueError('input must contain complete frames within the recording limit')
  count = size // stride
  if count * spec.output_nbytes > MAX_MODEL_BYTES:
    raise ValueError('output recording exceeds size limit')
  path = package.model_path
  if source_model is not None:
    path = Path(source_model)
    if sha256_file(path) != package.manifest['source']['sha256']:
      raise ValueError('source model hash mismatch')
  ort.disable_telemetry_events()
  session = ort.InferenceSession(str(path), providers=['CPUExecutionProvider'])
  engine = _Engine(package)
  queues = for_model(spec, engine)
  queues.reset()
  output_names = [item.name for item in session.get_outputs()]
  destination.parent.mkdir(parents=True, exist_ok=True)
  timings = []
  with tempfile.TemporaryDirectory(prefix='.sequence-', dir=destination.parent) as temporary:
    root = Path(temporary)
    with frames.open('rb') as stream, (root / 'outputs.bin').open('wb') as output:
      for _ in range(count):
        raw = stream.read(stride)
        if len(raw) != stride:
          raise ValueError('truncated frame')
        warped = np.frombuffer(raw, dtype=np.uint8, count=spec.warped_nbytes).reshape(spec.warped_shape)
        packed = np.frombuffer(raw, dtype='<f4', count=spec.packed_nelem, offset=spec.warped_nbytes)
        if not np.all(np.isfinite(packed)):
          raise ValueError('nonfinite frame input')
        start = time.perf_counter_ns()
        queues.step_into(warped, packed, engine.feed)
        results = dict(zip(output_names, session.run(output_names, engine.feed), strict=True))
        if any(not np.all(np.isfinite(value)) for value in results.values()):
          raise ValueError('nonfinite model output or state')
        values = results['outputs'].reshape(-1)
        if values.size != spec.output_nelem:
          raise ValueError('model output shape mismatch')
        after = getattr(queues, 'after_run', None)
        if after:
          after(results, engine.feed)
        timings.append((time.perf_counter_ns() - start) / 1e6)
        output.write(values.astype('<f4').tobytes())
    meta = {'model_sha256': package.manifest['artifact']['sha256'], 'source_sha256': spec.sha256,
            'executed_model_sha256': sha256_file(path), 'inputs_sha256': sha256_file(frames),
            'frame_ids': list(range(count)), 'output_count': spec.output_nelem,
            'output_slices': package.manifest['output_slices'], 'runtime': f'onnxruntime-{ort.__version__}-cpu',
            'platform': platform.platform(), 'host_inference_ms': timings,
            'scope': 'host CPU recording; excludes USB, camera warp, Android and vehicle control'}
    encoded = json.dumps(meta, indent=2, allow_nan=False)
    if len(encoded.encode()) > MAX_MANIFEST_BYTES:
      raise ValueError('recording metadata exceeds limit')
    (root / 'run.json').write_text(encoded, encoding='utf-8')
    root.rename(destination)
  return meta


def main():
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument('--package', type=Path, required=True)
  parser.add_argument('--frames', type=Path, required=True)
  parser.add_argument('--output', type=Path, required=True)
  parser.add_argument('--source-model', type=Path)
  args = parser.parse_args()
  result = run_sequence(read_manifest(args.package / 'manifest.json'), args.frames, args.output, args.source_model)
  print(f"Recorded {len(result['frame_ids'])} frames using {result['runtime']}")


if __name__ == '__main__':
  main()
