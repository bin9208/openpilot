#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import pickle
from dataclasses import replace

import numpy as np

if "JIT_BATCH_SIZE" not in os.environ:
  os.environ["JIT_BATCH_SIZE"] = "0"

from tinygrad import Tensor, TinyJit, Context, GlobalCounters, Device  # noqa: E402
from tinygrad.helpers import DEBUG  # noqa: E402
from tinygrad.nn.onnx import OnnxRunner  # noqa: E402

LANE_MARKING_BATCH_SIZE = 6
LANE_MARKING_INPUT_SHAPE = (LANE_MARKING_BATCH_SIZE, 3, 224, 224)


def compile_lane_marking_model(onnx_path: str):
  run_onnx = OnnxRunner(onnx_path)
  spec = run_onnx.graph_inputs["input"]
  run_onnx.graph_inputs["input"] = replace(spec, shape=LANE_MARKING_INPUT_SHAPE)

  Tensor.manual_seed(100)
  sample = Tensor.randn(*LANE_MARKING_INPUT_SHAPE, dtype=spec.dtype).realize().numpy()
  inputs = {"input": Tensor(sample, device=Device.DEFAULT).realize()}

  run_onnx_jit = TinyJit(
    lambda **kwargs: next(iter(run_onnx({k: v.to(Device.DEFAULT) for k, v in kwargs.items()}).values())).cast("float32"),
    prune=True,
  )

  for i in range(3):
    GlobalCounters.reset()
    print(f"run {i}")
    with Context(DEBUG=max(DEBUG.value, 2 if i == 2 else 1)):
      output = run_onnx_jit(**inputs).numpy()
    if i == 1:
      test_output = np.copy(output)

  np.testing.assert_equal(test_output, output, "JIT run failed")
  print(f"compiled lane marking model output shape: {output.shape}")
  return run_onnx_jit


def main() -> None:
  parser = argparse.ArgumentParser(description="Compile lane marking ONNX to tinygrad TinyJit pickle")
  parser.add_argument("onnx_path")
  parser.add_argument("output_path")
  args = parser.parse_args()

  compiled = compile_lane_marking_model(args.onnx_path)
  with open(args.output_path, "wb") as f:
    pickle.dump(compiled, f)
  print(f"wrote {args.output_path} ({os.path.getsize(args.output_path)} bytes)")


if __name__ == "__main__":
  main()
