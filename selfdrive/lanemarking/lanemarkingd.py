#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path
from typing import Any

PROCESS_NAME = "selfdrive.lanemarking.lanemarkingd"
LANE_MARKING_HZ = 5.0
CROP_SIZE = 224
TARGET_X_EVALS = (12.0, 20.0, 28.0)
IMAGE_MEAN = (0.485, 0.456, 0.406)
IMAGE_STD = (0.229, 0.224, 0.225)
REPO_PARENT = Path(__file__).resolve().parents[3]
if str(REPO_PARENT) not in sys.path:
  sys.path.insert(0, str(REPO_PARENT))

try:
  from openpilot.common.swaglog import cloudlog
except Exception:
  logging.basicConfig(level=logging.INFO)
  cloudlog = logging.getLogger(PROCESS_NAME)
from openpilot.selfdrive.lanemarking.model import (
  ALLOW_LABEL,
  BLOCK_LABELS,
  DECISION_ALLOW_CANDIDATE,
  DECISION_BLOCK,
  DECISION_UNCERTAIN_OR_BLOCK,
  LANE_MARKING_MODEL_PATH,
  LaneMarkingPrediction,
  LaneMarkingTorchScriptModel,
  TemporalConsistencyFilter,
  normalize_threshold,
)


def _threshold_from_params(params) -> float:
  return normalize_threshold(params.get("LaneMarkingConfidenceThreshold", return_default=True))


def _crop_and_pad(np, img, u_start: int, v_start: int, crop_size: int):
  h, w = img.shape[:2]
  u_end = u_start + crop_size
  v_end = v_start + crop_size

  u_start_clamped = max(0, u_start)
  u_end_clamped = min(w, u_end)
  v_start_clamped = max(0, v_start)
  v_end_clamped = min(h, v_end)

  if u_start_clamped >= u_end_clamped or v_start_clamped >= v_end_clamped:
    return np.zeros((crop_size, crop_size, 3), dtype=np.uint8)

  patch = img[v_start_clamped:v_end_clamped, u_start_clamped:u_end_clamped]
  if patch.shape[0] == crop_size and patch.shape[1] == crop_size:
    return patch

  out = np.zeros((crop_size, crop_size, 3), dtype=np.uint8)
  dst_y = max(0, -v_start)
  dst_x = max(0, -u_start)
  out[dst_y:dst_y + patch.shape[0], dst_x:dst_x + patch.shape[1]] = patch
  return out


def _rgb_from_vipc(np, cv2, vision_buf):
  width = int(getattr(vision_buf, "width", 0))
  height = int(getattr(vision_buf, "height", 0))
  stride = int(getattr(vision_buf, "stride", width))
  data = getattr(vision_buf, "data", None)
  if width <= 0 or height <= 0 or stride <= 0 or data is None:
    return None

  yuv = np.frombuffer(data, dtype=np.uint8)
  if yuv.size < stride * height * 3 // 2:
    return None

  yuv_img = yuv.reshape((yuv.size // stride, stride))
  return cv2.cvtColor(yuv_img[:height * 3 // 2, :width], cv2.COLOR_YUV2RGB_NV12)


def _project_lane_point(np, lane_line, x_eval: float, view_from_calib, intrinsic) -> tuple[float, float, float] | None:
  lane_xs = list(lane_line.x)
  lane_ys = list(lane_line.y)
  lane_zs = list(lane_line.z)
  if len(lane_xs) == 0 or len(lane_ys) == 0 or len(lane_zs) == 0:
    return None

  y_eval = float(np.interp(x_eval, lane_xs, lane_ys))
  z_eval = float(np.interp(x_eval, lane_xs, lane_zs))
  pt_view = view_from_calib.dot(np.array([x_eval, y_eval, z_eval]))
  if not np.isfinite(pt_view).all() or pt_view[2] <= 0.1:
    return None

  u = float(pt_view[0] / pt_view[2] * intrinsic[0, 0] + intrinsic[0, 2])
  v = float(pt_view[1] / pt_view[2] * intrinsic[1, 1] + intrinsic[1, 2])
  if not np.isfinite(u) or not np.isfinite(v):
    return None
  return u, v, y_eval


def _preprocess_crops(np, torch, crops: list[Any]):
  arr = np.stack(crops).astype(np.float32) / 255.0
  mean = np.asarray(IMAGE_MEAN, dtype=np.float32).reshape((1, 1, 1, 3))
  std = np.asarray(IMAGE_STD, dtype=np.float32).reshape((1, 1, 1, 3))
  arr = (arr - mean) / std
  arr = np.ascontiguousarray(arr.transpose(0, 3, 1, 2))
  return torch.from_numpy(arr)


def _aggregate_side(
  predictions: list[LaneMarkingPrediction],
  threshold: float,
  temporal_filter: TemporalConsistencyFilter,
) -> LaneMarkingPrediction:
  if not predictions:
    temporal_filter.update("unknown", 0.0, threshold)
    return LaneMarkingPrediction("unknown", 0.0, {}, DECISION_UNCERTAIN_OR_BLOCK, False)

  dashed = [p for p in predictions if p.label == ALLOW_LABEL]
  all_dashed = len(dashed) == len(predictions)
  min_dashed_conf = min((p.confidence for p in dashed), default=0.0)
  if all_dashed and min_dashed_conf >= threshold:
    temporal_consistent = temporal_filter.update(ALLOW_LABEL, min_dashed_conf, threshold)
    decision = DECISION_ALLOW_CANDIDATE if temporal_consistent else DECISION_UNCERTAIN_OR_BLOCK
    return LaneMarkingPrediction(ALLOW_LABEL, min_dashed_conf, {}, decision, temporal_consistent)

  block_predictions = [p for p in predictions if p.label in BLOCK_LABELS]
  if block_predictions:
    best_block = max(block_predictions, key=lambda p: p.confidence)
    temporal_filter.update(best_block.label, best_block.confidence, threshold)
    decision = DECISION_BLOCK if best_block.confidence >= threshold else DECISION_UNCERTAIN_OR_BLOCK
    return LaneMarkingPrediction(best_block.label, best_block.confidence, {}, decision, False)

  best = max(predictions, key=lambda p: p.confidence)
  temporal_filter.update(best.label, best.confidence, threshold)
  return LaneMarkingPrediction(best.label, best.confidence, {}, DECISION_UNCERTAIN_OR_BLOCK, False)


def _prediction_from_probs(class_names: tuple[str, ...], probs) -> LaneMarkingPrediction:
  confidence, predicted_idx = probs.max(0)
  idx = int(predicted_idx.item())
  label = class_names[idx] if idx < len(class_names) else "unknown"
  conf = float(confidence.item())
  return LaneMarkingPrediction(label, conf, {}, DECISION_UNCERTAIN_OR_BLOCK, False)


def _lane_marking_msg(
  messaging,
  frame_id: int = 0,
  timestamp_eof: int = 0,
  left_prediction: LaneMarkingPrediction | None = None,
  right_prediction: LaneMarkingPrediction | None = None,
  left_y: float = 0.0,
  right_y: float = 0.0,
  lane_width: float = 0.0,
  exec_ms: float = 0.0,
  valid: bool = False,
  inference_skipped: bool = True,
  cut_in_assist: bool = False,
  threshold: float = 0.60,
):
  left_prediction = left_prediction or LaneMarkingPrediction("unknown", 0.0, {}, DECISION_UNCERTAIN_OR_BLOCK, False)
  right_prediction = right_prediction or LaneMarkingPrediction("unknown", 0.0, {}, DECISION_UNCERTAIN_OR_BLOCK, False)
  dat = messaging.new_message("laneMarkingState")
  dat.valid = True
  state = dat.laneMarkingState
  state.frameId = int(frame_id)
  state.timestampEof = int(timestamp_eof)
  state.modelExecutionTimeMs = float(exec_ms)
  state.valid = bool(valid)
  state.leftLabel = left_prediction.label
  state.rightLabel = right_prediction.label
  state.leftConfidence = float(left_prediction.confidence)
  state.rightConfidence = float(right_prediction.confidence)
  state.leftDecision = left_prediction.decision
  state.rightDecision = right_prediction.decision
  state.leftBoundaryY = float(left_y)
  state.rightBoundaryY = float(right_y)
  state.laneWidth = float(lane_width)
  state.leftBlock = left_prediction.label in BLOCK_LABELS and left_prediction.confidence >= threshold
  state.rightBlock = right_prediction.label in BLOCK_LABELS and right_prediction.confidence >= threshold
  state.leftNoBlock = left_prediction.allow_candidate
  state.rightNoBlock = right_prediction.allow_candidate
  state.cutInAssist = bool(cut_in_assist)
  state.inferenceSkipped = bool(inference_skipped)
  return dat


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
  import numpy as np
  import cv2
  from cereal import messaging
  from msgq.visionipc import VisionIpcClient, VisionStreamType
  from openpilot.common.params import Params
  from openpilot.common.transformations.camera import DEVICE_CAMERAS, view_frame_from_device_frame
  import openpilot.common.transformations.orientation as orient

  params = Params()
  pm = messaging.PubMaster(["laneMarkingState"])
  sm = messaging.SubMaster(["modelV2", "liveCalibration", "roadCameraState", "deviceState"], poll="roadCameraState")
  model: LaneMarkingTorchScriptModel | None = None
  temporal_filters = {
    "left": TemporalConsistencyFilter(required_frames=3),
    "right": TemporalConsistencyFilter(required_frames=3),
  }
  vipc_client = VisionIpcClient("camerad", VisionStreamType.VISION_STREAM_ROAD, True)
  load_error_logged = False
  vipc_error_logged = False
  last_inference_t = 0.0

  while True:
    try:
      if not params.get_bool("LaneMarkingModelEnabled"):
        model = None
        load_error_logged = False
        vipc_error_logged = False
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

      if not vipc_client.is_connected():
        if not vipc_error_logged:
          cloudlog.info("lanemarkingd connecting to camerad VisionIPC")
          vipc_error_logged = True
        vipc_client.connect(True)
        threshold = _threshold_from_params(params)
        pm.send("laneMarkingState", _lane_marking_msg(
          messaging,
          inference_skipped=True,
          cut_in_assist=params.get_bool("LaneMarkingInterventionEnabled"),
          threshold=threshold,
        ))
        time.sleep(0.2)
        continue
      vipc_error_logged = False

      vision_buf = vipc_client.recv()
      sm.update(0)
      if vision_buf is None:
        continue

      frame_id = int(getattr(vipc_client, "frame_id", 0))
      timestamp_eof = int(getattr(vipc_client, "timestamp_eof", 0))

      now = time.monotonic()
      if now - last_inference_t < 1.0 / LANE_MARKING_HZ:
        continue
      last_inference_t = now

      threshold = _threshold_from_params(params)
      if not sm.seen["modelV2"] or not sm.seen["liveCalibration"] or len(sm["modelV2"].laneLines) < 3:
        pm.send("laneMarkingState", _lane_marking_msg(
          messaging,
          frame_id=frame_id,
          timestamp_eof=timestamp_eof,
          inference_skipped=True,
          cut_in_assist=params.get_bool("LaneMarkingInterventionEnabled"),
          threshold=threshold,
        ))
        continue

      rgb = _rgb_from_vipc(np, cv2, vision_buf)
      if rgb is None:
        continue

      model_data = sm["modelV2"]
      live_calib = sm["liveCalibration"]
      rpy = list(live_calib.rpyCalib)
      device_from_calib = orient.rot_from_euler(rpy)
      view_from_calib = view_frame_from_device_frame.dot(device_from_calib)

      device_type = str(sm["deviceState"].deviceType) if sm.seen["deviceState"] else "tici"
      sensor = str(sm["roadCameraState"].sensor) if sm.seen["roadCameraState"] else "unknown"
      camera = DEVICE_CAMERAS.get((device_type, sensor), DEVICE_CAMERAS[("tici", "unknown")]).fcam
      intrinsic = camera.intrinsics

      crops = []
      crop_meta: list[tuple[str, float, float]] = []
      side_boundary_y = {"left": 0.0, "right": 0.0}
      for side, lane_idx in (("left", 1), ("right", 2)):
        lane_line = model_data.laneLines[lane_idx]
        for x_eval in TARGET_X_EVALS:
          projected = _project_lane_point(np, lane_line, x_eval, view_from_calib, intrinsic)
          if projected is None:
            continue
          u_proj, v_proj, y_eval = projected
          if abs(x_eval - 20.0) < 0.1:
            side_boundary_y[side] = y_eval
          crops.append(_crop_and_pad(
            np,
            rgb,
            int(round(u_proj - CROP_SIZE / 2)),
            int(round(v_proj - CROP_SIZE / 2)),
            CROP_SIZE,
          ))
          crop_meta.append((side, x_eval, y_eval))

      if not crops:
        pm.send("laneMarkingState", _lane_marking_msg(
          messaging,
          frame_id=frame_id,
          timestamp_eof=timestamp_eof,
          inference_skipped=True,
          cut_in_assist=params.get_bool("LaneMarkingInterventionEnabled"),
          threshold=threshold,
        ))
        continue

      assert model.torch is not None
      assert model.model is not None
      start_t = time.monotonic()
      input_tensor = _preprocess_crops(np, model.torch, crops)
      with model.torch.no_grad():
        logits = model.model(input_tensor)
        probs_batch = model.torch.softmax(logits, dim=1)
      exec_ms = (time.monotonic() - start_t) * 1000.0

      side_predictions = {"left": [], "right": []}
      for probs, (side, _x_eval, _y_eval) in zip(probs_batch, crop_meta):
        side_predictions[side].append(_prediction_from_probs(model.class_names, probs))

      left_prediction = _aggregate_side(side_predictions["left"], threshold, temporal_filters["left"])
      right_prediction = _aggregate_side(side_predictions["right"], threshold, temporal_filters["right"])
      lane_width = abs(side_boundary_y["right"] - side_boundary_y["left"])

      pm.send("laneMarkingState", _lane_marking_msg(
        messaging,
        frame_id=frame_id,
        timestamp_eof=timestamp_eof,
        left_prediction=left_prediction,
        right_prediction=right_prediction,
        left_y=side_boundary_y["left"],
        right_y=side_boundary_y["right"],
        lane_width=lane_width,
        exec_ms=exec_ms,
        valid=True,
        inference_skipped=False,
        cut_in_assist=params.get_bool("LaneMarkingInterventionEnabled"),
        threshold=threshold,
      ))

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
