#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import logging
import time
from pathlib import Path

PROCESS_NAME = "selfdrive.lanemarking.lanemarkingd"

try:
  from openpilot.common.swaglog import cloudlog
except Exception:
  logging.basicConfig(level=logging.INFO)
  cloudlog = logging.getLogger(PROCESS_NAME)
from openpilot.selfdrive.lanemarking.model import (
  LANE_MARKING_MODEL_PATH,
  LaneMarkingTorchScriptModel,
  TemporalConsistencyFilter,
  normalize_threshold,
)


def _threshold_from_params(params) -> float:
  return normalize_threshold(params.get("LaneMarkingConfidenceThreshold", return_default=True))


def run_smoke_test(model_path: Path = LANE_MARKING_MODEL_PATH) -> int:
  model = LaneMarkingTorchScriptModel(model_path=model_path)
  model.load()

  assert model.torch is not None
  dummy_input = model.torch.zeros((1, 3, 224, 224), dtype=model.torch.float32)
  prediction = model.predict_tensor(dummy_input, threshold=0.60)

  print(json.dumps({
    "model_path": str(model_path),
    "label": prediction.label,
    "confidence": prediction.confidence,
    "decision": prediction.decision,
    "allow_candidate": prediction.allow_candidate,
  }, indent=2))
  return 0


def main() -> None:
  from openpilot.common.params import Params

  params = Params()
  model: LaneMarkingTorchScriptModel | None = None
  temporal_filter = TemporalConsistencyFilter(required_frames=3)
  load_error_logged = False

  while True:
    try:
      if not params.get_bool("LaneMarkingModelEnabled"):
        model = None
        load_error_logged = False
        time.sleep(1.0)
        continue

      if model is None:
        model = LaneMarkingTorchScriptModel()
        try:
          model.load()
          cloudlog.info("lanemarkingd loaded model: %s", model.model_path)
        except Exception:
          if not load_error_logged:
            cloudlog.exception("lanemarkingd model load failed; staying disabled/log-only")
            load_error_logged = True
          model = None
          time.sleep(5.0)
          continue

      # The runtime hook is intentionally shadow-only. The next integration step
      # should feed road camera crops from VisionIPC, run predict_tensor(), and
      # log LaneMarkingPrediction without writing any control command.
      _ = _threshold_from_params(params)
      _ = temporal_filter
      time.sleep(1.0)

    except Exception:
      cloudlog.exception("lanemarkingd unexpected error; continuing disabled/log-only")
      time.sleep(5.0)


if __name__ == "__main__":
  parser = argparse.ArgumentParser(description="Lane marking shadow-mode runtime")
  parser.add_argument("--smoke-test", action="store_true", help="Load the runtime model and run one dummy inference.")
  parser.add_argument("--model", type=Path, default=LANE_MARKING_MODEL_PATH, help="Runtime TorchScript model path.")
  args = parser.parse_args()

  if args.smoke_test:
    raise SystemExit(run_smoke_test(args.model))
  main()
