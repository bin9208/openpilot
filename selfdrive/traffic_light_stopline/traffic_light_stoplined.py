#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import logging
import math
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

PROCESS_NAME = "selfdrive.traffic_light_stopline.traffic_light_stoplined"
TRAFFIC_LIGHT_STOPLINE_HZ = 5.0
PROCESSING_WIDTH = 1280
TRAFFIC_CROP_SIZE = 160
MAX_TRAFFIC_CANDIDATES = 6
MODEL_RETRY_SEC = 30.0
IMAGE_MEAN = (0.45, 0.45, 0.45)
IMAGE_STD = (0.23, 0.23, 0.23)
REPO_PARENT = Path(__file__).resolve().parents[3]
if str(REPO_PARENT) not in sys.path:
  sys.path.insert(0, str(REPO_PARENT))

try:
  from openpilot.common.swaglog import cloudlog
except Exception:
  logging.basicConfig(level=logging.INFO)
  cloudlog = logging.getLogger(PROCESS_NAME)

from openpilot.selfdrive.traffic_light_stopline.model import (
  CLASS_NAMES,
  TRAFFIC_LIGHT_CROP_SIZE as MODEL_CROP_SIZE,
  create_traffic_light_model,
)


@dataclass
class TrafficCandidate:
  x: int
  y: int
  w: int
  h: int
  score: float
  red_pixels: int
  green_pixels: int
  yellow_pixels: int


@dataclass
class TrafficLightResult:
  state: str = "unknown"
  confidence: float = 0.0
  red_prob: float = 0.0
  green_prob: float = 0.0
  yellow_prob: float = 0.0
  left_prob: float = 0.0
  no_signal_prob: float = 1.0
  candidate_count: int = 0
  candidate_x: float = 0.0
  candidate_y: float = 0.0
  candidate_w: float = 0.0
  candidate_h: float = 0.0
  source: str = "heuristic"


@dataclass
class StopLineResult:
  state: str = "no_clear_stopline"
  confidence: float = 0.0
  stopline_score: float = 0.0
  crosswalk_score: float = 0.0
  y_norm: float = 0.0
  width_norm: float = 0.0
  stripe_count: int = 0
  lead_blocked: bool = False
  dark_or_glare: bool = False


def _nv12_planes_valid(yuv_img, width: int, height: int) -> bool:
  return yuv_img.shape[0] >= height + height // 2 and yuv_img.shape[1] >= width


def _yuv_from_vipc(np, vision_buf):
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
  if not _nv12_planes_valid(yuv_img, width, height):
    return None
  return yuv_img, width, height


def _yuv_to_rgb(np, y, u, v):
  c = y - 16
  d = u - 128
  e = v - 128
  r = (298 * c + 409 * e + 128) >> 8
  g = (298 * c - 100 * d - 208 * e + 128) >> 8
  b = (298 * c + 516 * d + 128) >> 8
  return np.clip(np.stack((r, g, b), axis=2), 0, 255).astype(np.uint8)


def _sample_nv12_rgb(np, yuv_img, width: int, height: int, target_width: int = PROCESSING_WIDTH):
  target_width = max(160, min(int(target_width), width))
  target_height = max(90, int(round(height * target_width / max(width, 1))))
  xs = np.linspace(0, width - 1, target_width).astype(np.int32)
  ys = np.linspace(0, height - 1, target_height).astype(np.int32)
  y = yuv_img[ys[:, None], xs[None, :]].astype(np.int32)

  uv_xs = (xs // 2) * 2
  uv_ys = height + ys // 2
  u = yuv_img[uv_ys[:, None], uv_xs[None, :]].astype(np.int32)
  v = yuv_img[uv_ys[:, None], uv_xs[None, :] + 1].astype(np.int32)
  return _yuv_to_rgb(np, y, u, v)


def _softmax(np, logits):
  logits = logits.astype(np.float32)
  logits = logits - np.max(logits, axis=1, keepdims=True)
  exp_logits = np.exp(logits)
  return exp_logits / np.sum(exp_logits, axis=1, keepdims=True)


def _resize_nearest(np, img, size: int):
  if img.size == 0:
    return np.zeros((size, size, 3), dtype=np.uint8)
  ys = np.linspace(0, img.shape[0] - 1, size).astype(np.int32)
  xs = np.linspace(0, img.shape[1] - 1, size).astype(np.int32)
  return img[ys[:, None], xs[None, :]]


def _crop_with_padding(np, rgb, x0: int, y0: int, x1: int, y1: int):
  h, w = rgb.shape[:2]
  out_w = max(1, x1 - x0)
  out_h = max(1, y1 - y0)
  crop = np.zeros((out_h, out_w, 3), dtype=np.uint8)

  sx0 = max(0, x0)
  sy0 = max(0, y0)
  sx1 = min(w, x1)
  sy1 = min(h, y1)
  if sx1 <= sx0 or sy1 <= sy0:
    return crop

  dx0 = sx0 - x0
  dy0 = sy0 - y0
  crop[dy0:dy0 + sy1 - sy0, dx0:dx0 + sx1 - sx0] = rgb[sy0:sy1, sx0:sx1]
  return crop


def _preprocess_crops(np, crops: list[Any]):
  arr = np.stack(crops).astype(np.float32) / 255.0
  mean = np.asarray(IMAGE_MEAN, dtype=np.float32).reshape((1, 1, 1, 3))
  std = np.asarray(IMAGE_STD, dtype=np.float32).reshape((1, 1, 1, 3))
  arr = (arr - mean) / std
  return np.ascontiguousarray(arr.transpose(0, 3, 1, 2))


def _iou(a: TrafficCandidate, b: TrafficCandidate) -> float:
  ax1, ay1 = a.x + a.w, a.y + a.h
  bx1, by1 = b.x + b.w, b.y + b.h
  ix0, iy0 = max(a.x, b.x), max(a.y, b.y)
  ix1, iy1 = min(ax1, bx1), min(ay1, by1)
  iw, ih = max(0, ix1 - ix0), max(0, iy1 - iy0)
  inter = iw * ih
  union = a.w * a.h + b.w * b.h - inter
  return inter / union if union > 0 else 0.0


def _dedupe_candidates(candidates: list[TrafficCandidate]) -> list[TrafficCandidate]:
  selected: list[TrafficCandidate] = []
  for cand in sorted(candidates, key=lambda c: c.score, reverse=True):
    if any(_iou(cand, chosen) > 0.35 for chosen in selected):
      continue
    selected.append(cand)
    if len(selected) >= MAX_TRAFFIC_CANDIDATES:
      break
  return selected


def _grid_candidate_boxes(np, red_mask, green_mask, yellow_mask) -> list[TrafficCandidate]:
  h, w = red_mask.shape
  light_mask = red_mask | green_mask | yellow_mask
  cell_w = max(12, w // 48)
  cell_h = max(10, h // 40)
  y_min = int(0.04 * h)
  y_max = int(0.62 * h)
  x_min = int(0.08 * w)
  x_max = int(0.92 * w)
  candidates: list[TrafficCandidate] = []

  for y0 in range(y_min, y_max, cell_h):
    y1 = min(y_max, y0 + cell_h)
    for x0 in range(x_min, x_max, cell_w):
      x1 = min(x_max, x0 + cell_w)
      count = int(light_mask[y0:y1, x0:x1].sum())
      if count < 4:
        continue

      ey0 = max(0, y0 - cell_h)
      ey1 = min(h, y1 + cell_h)
      ex0 = max(0, x0 - cell_w)
      ex1 = min(w, x1 + cell_w)
      local = light_mask[ey0:ey1, ex0:ex1]
      ys, xs = np.nonzero(local)
      if len(xs) < 5:
        continue

      bx0 = int(ex0 + xs.min())
      bx1 = int(ex0 + xs.max() + 1)
      by0 = int(ey0 + ys.min())
      by1 = int(ey0 + ys.max() + 1)
      bw, bh = bx1 - bx0, by1 - by0
      if bw < 3 or bh < 3 or bw > 180 or bh > 130:
        continue
      aspect = bw / max(bh, 1)
      if aspect < 0.25 or aspect > 5.0:
        continue

      area = max(1, bw * bh)
      red_pixels = int(red_mask[by0:by1, bx0:bx1].sum())
      green_pixels = int(green_mask[by0:by1, bx0:bx1].sum())
      yellow_pixels = int(yellow_mask[by0:by1, bx0:bx1].sum())
      color_pixels = red_pixels + green_pixels + yellow_pixels
      fill = color_pixels / area
      score = min(1.0, fill * 3.0 + min(color_pixels / 70.0, 1.0) * 0.5)
      candidates.append(TrafficCandidate(bx0, by0, bw, bh, score, red_pixels, green_pixels, yellow_pixels))

  return _dedupe_candidates(candidates)


def _traffic_masks(np, rgb):
  arr = rgb.astype(np.int16)
  r, g, b = arr[:, :, 0], arr[:, :, 1], arr[:, :, 2]
  brightness = np.maximum.reduce([r, g, b])
  upper = np.zeros(r.shape, dtype=np.bool_)
  h, w = r.shape
  upper[int(0.04 * h):int(0.62 * h), int(0.08 * w):int(0.92 * w)] = True

  red = (r > 135) & (r > g * 1.30) & (r > b * 1.18) & (brightness > 145) & upper
  green = (g > 120) & (g > r * 1.10) & (g > b * 1.08) & (brightness > 135) & upper
  yellow = (r > 140) & (g > 115) & (b < 135) & (np.abs(r - g) < 95) & (brightness > 145) & upper
  return red, green, yellow


def _heuristic_traffic_result(candidates: list[TrafficCandidate], width: int, height: int) -> TrafficLightResult:
  if not candidates:
    return TrafficLightResult(state="NoSignal", confidence=0.0, no_signal_prob=1.0, candidate_count=0)

  best = max(candidates, key=lambda c: c.score)
  red = sum(c.red_pixels for c in candidates)
  green = sum(c.green_pixels for c in candidates)
  yellow = sum(c.yellow_pixels for c in candidates)
  total = max(1, red + green + yellow)
  red_prob = red / total
  green_prob = green / total
  yellow_prob = yellow / total

  if red_prob >= 0.45:
    state = "Stop"
    confidence = red_prob * max(best.score, 0.35)
  elif yellow_prob >= 0.35:
    state = "Caution"
    confidence = yellow_prob * max(best.score, 0.35)
  elif green_prob >= 0.35:
    state = "Straight"
    confidence = green_prob * max(best.score, 0.35)
  else:
    state = "NoSignal"
    confidence = max(best.score * 0.25, 0.0)

  return TrafficLightResult(
    state=state,
    confidence=float(min(1.0, confidence)),
    red_prob=float(red_prob),
    green_prob=float(green_prob),
    yellow_prob=float(yellow_prob),
    left_prob=0.0,
    no_signal_prob=float(1.0 - min(1.0, max(red_prob, green_prob, yellow_prob))),
    candidate_count=len(candidates),
    candidate_x=float((best.x + best.w / 2) / width),
    candidate_y=float((best.y + best.h / 2) / height),
    candidate_w=float(best.w / width),
    candidate_h=float(best.h / height),
    source="heuristic",
  )


def _candidate_crop(np, rgb, candidate: TrafficCandidate):
  cx = candidate.x + candidate.w / 2.0
  cy = candidate.y + candidate.h / 2.0
  side = max(candidate.w, candidate.h, 24) * 4.0
  x0 = int(round(cx - side / 2.0))
  y0 = int(round(cy - side / 2.0))
  x1 = int(round(cx + side / 2.0))
  y1 = int(round(cy + side / 2.0))
  return _resize_nearest(np, _crop_with_padding(np, rgb, x0, y0, x1, y1), MODEL_CROP_SIZE)


def detect_traffic_light(np, rgb, model=None) -> TrafficLightResult:
  red_mask, green_mask, yellow_mask = _traffic_masks(np, rgb)
  candidates = _grid_candidate_boxes(np, red_mask, green_mask, yellow_mask)
  heuristic = _heuristic_traffic_result(candidates, rgb.shape[1], rgb.shape[0])
  if not candidates or model is None or not getattr(model, "loaded", False):
    return heuristic

  crops = [_candidate_crop(np, rgb, cand) for cand in candidates]
  try:
    logits = model.predict_logits(_preprocess_crops(np, crops))
    probs = _softmax(np, logits)
  except Exception:
    cloudlog.exception("traffic_light_stoplined model inference failed; using heuristic result")
    return heuristic

  class_names = getattr(model, "class_names", CLASS_NAMES)
  best_idx = 0
  best_score = -1.0
  for i, cand in enumerate(candidates):
    no_signal_idx = class_names.index("NoSignal") if "NoSignal" in class_names else 2
    signal_prob = 1.0 - float(probs[i][no_signal_idx])
    score = signal_prob * 0.75 + cand.score * 0.25
    if score > best_score:
      best_score = score
      best_idx = i

  best_probs = probs[best_idx]
  best_cand = candidates[best_idx]
  pred_idx = int(np.argmax(best_probs))
  label = class_names[pred_idx] if pred_idx < len(class_names) else "unknown"
  confidence = float(best_probs[pred_idx])

  prob_by_name = {
    cls: float(best_probs[i]) if i < len(best_probs) else 0.0
    for i, cls in enumerate(class_names)
  }
  red_prob = prob_by_name.get("Stop", 0.0)
  green_prob = max(prob_by_name.get("Straight", 0.0), prob_by_name.get("StraightLeft", 0.0))
  yellow_prob = prob_by_name.get("Caution", 0.0)
  left_prob = max(prob_by_name.get("LeftTurn", 0.0), prob_by_name.get("StraightLeft", 0.0))
  no_signal_prob = prob_by_name.get("NoSignal", 0.0)

  if confidence < 0.35 or label == "NoSignal":
    if heuristic.state != "NoSignal" and heuristic.confidence > 0.25:
      label = heuristic.state
      confidence = heuristic.confidence
    else:
      label = "NoSignal"

  return TrafficLightResult(
    state=label,
    confidence=float(confidence),
    red_prob=red_prob,
    green_prob=green_prob,
    yellow_prob=yellow_prob,
    left_prob=left_prob,
    no_signal_prob=no_signal_prob,
    candidate_count=len(candidates),
    candidate_x=float((best_cand.x + best_cand.w / 2) / rgb.shape[1]),
    candidate_y=float((best_cand.y + best_cand.h / 2) / rgb.shape[0]),
    candidate_w=float(best_cand.w / rgb.shape[1]),
    candidate_h=float(best_cand.h / rgb.shape[0]),
    source=f"traffic_light_crop_{getattr(model, 'runtime', 'model')}",
  )


def _moving_average(np, values, window: int):
  if len(values) == 0:
    return values
  window = max(1, int(window))
  kernel = np.ones(window, dtype=np.float32) / float(window)
  return np.convolve(values.astype(np.float32), kernel, mode="same")


def _count_row_groups(np, active_rows, row_score, width_by_row) -> int:
  groups = []
  start = None
  for i, active in enumerate(active_rows):
    if active and start is None:
      start = i
    elif not active and start is not None:
      groups.append((start, i))
      start = None
  if start is not None:
    groups.append((start, len(active_rows)))

  count = 0
  last_center = -999
  for start, end in groups:
    center = (start + end) // 2
    if end - start < 2 or center - last_center < 5:
      continue
    if float(np.max(row_score[start:end])) < 0.035:
      continue
    if float(np.max(width_by_row[start:end])) < 0.20:
      continue
    last_center = center
    count += 1
  return count


def detect_stopline(np, rgb) -> StopLineResult:
  h, w = rgb.shape[:2]
  arr = rgb.astype(np.float32)
  gray = arr[:, :, 0] * 0.299 + arr[:, :, 1] * 0.587 + arr[:, :, 2] * 0.114
  maxc = np.max(arr, axis=2)
  minc = np.min(arr, axis=2)
  saturation = maxc - minc

  y_norm = np.arange(h, dtype=np.float32)[:, None] / max(h - 1, 1)
  x_norm = np.arange(w, dtype=np.float32)[None, :] / max(w - 1, 1)
  half_width = 0.11 + 0.46 * y_norm
  road_mask = (y_norm >= 0.36) & (y_norm <= 0.94) & (np.abs(x_norm - 0.5) <= half_width)
  roi_gray = gray[road_mask]
  if roi_gray.size == 0:
    return StopLineResult()

  roi_mean = float(np.mean(roi_gray))
  roi_std = float(np.std(roi_gray))
  dark_or_glare = roi_mean < 38.0 or roi_std < 8.0 or (roi_mean > 218.0 and roi_std < 18.0)
  dynamic_floor = max(112.0, min(205.0, roi_mean + roi_std * 0.85))
  white_mask = (gray > dynamic_floor) & (saturation < 58.0) & road_mask

  road_count = np.maximum(1, road_mask.sum(axis=1).astype(np.float32))
  row_white = white_mask.sum(axis=1).astype(np.float32)
  row_score = _moving_average(np, row_white / road_count, 7)
  row_min = int(0.38 * h)
  row_max = int(0.92 * h)
  if row_max <= row_min:
    return StopLineResult(dark_or_glare=dark_or_glare)

  search_scores = row_score[row_min:row_max]
  best_rel = int(np.argmax(search_scores))
  best_row = row_min + best_rel
  best_fraction = float(search_scores[best_rel])
  band0 = max(0, best_row - 3)
  band1 = min(h, best_row + 4)
  band = white_mask[band0:band1]
  xs = np.nonzero(band)[1]
  width_norm = 0.0
  if len(xs) > 0:
    width_norm = float((xs.max() - xs.min() + 1) / w)

  line_score = max(0.0, (best_fraction - 0.035) / 0.18) * min(1.0, width_norm / 0.44)
  line_score = float(min(1.0, line_score))

  width_by_row = np.zeros(h, dtype=np.float32)
  for y in range(row_min, row_max):
    row_xs = np.nonzero(white_mask[y])[0]
    if len(row_xs) > 0:
      width_by_row[y] = (row_xs.max() - row_xs.min() + 1) / w

  active = (row_score[row_min:row_max] > max(0.030, best_fraction * 0.42))
  stripes = _count_row_groups(np, active, row_score[row_min:row_max], width_by_row[row_min:row_max])
  crosswalk_score = min(1.0, stripes / 6.0 + max(0.0, best_fraction - 0.03) * 1.4)
  lower_center = gray[int(0.52 * h):int(0.88 * h), int(0.33 * w):int(0.67 * w)]
  lead_blocked = bool(lower_center.size > 0 and np.mean(lower_center) < roi_mean * 0.60 and np.std(lower_center) > 18.0)

  best_y_norm = best_row / max(h - 1, 1)
  if dark_or_glare and line_score < 0.25 and crosswalk_score < 0.35:
    state = "blocked_or_dark"
    confidence = 0.0
  elif stripes >= 4 and crosswalk_score >= 0.50:
    state = "crosswalk_stop_area"
    confidence = float(crosswalk_score)
  elif line_score >= 0.55:
    if best_y_norm < 0.58:
      state = "stopline_far"
    elif best_y_norm < 0.76:
      state = "stopline_mid"
    else:
      state = "stopline_near"
    confidence = line_score
  elif line_score >= 0.28 or crosswalk_score >= 0.32:
    state = "stopline_candidate_lowconf"
    confidence = max(line_score, crosswalk_score)
  elif lead_blocked:
    state = "blocked_or_lead_vehicle"
    confidence = 0.0
  else:
    state = "no_clear_stopline"
    confidence = 0.0

  return StopLineResult(
    state=state,
    confidence=float(confidence),
    stopline_score=line_score,
    crosswalk_score=float(crosswalk_score),
    y_norm=float(best_y_norm),
    width_norm=width_norm,
    stripe_count=int(stripes),
    lead_blocked=lead_blocked,
    dark_or_glare=dark_or_glare,
  )


def _traffic_light_stopline_msg(
  messaging,
  frame_id: int = 0,
  timestamp_eof: int = 0,
  exec_ms: float = 0.0,
  processing_width: int = PROCESSING_WIDTH,
  model_loaded: bool = False,
  model_runtime: str = "none",
  traffic: TrafficLightResult | None = None,
  stopline: StopLineResult | None = None,
  inference_skipped: bool = True,
):
  traffic = traffic or TrafficLightResult()
  stopline = stopline or StopLineResult()
  dat = messaging.new_message("trafficLightStopLineState")
  dat.valid = True
  state = dat.trafficLightStopLineState
  state.frameId = int(frame_id)
  state.timestampEof = int(timestamp_eof)
  state.valid = not inference_skipped
  state.inferenceSkipped = bool(inference_skipped)
  state.modelLoaded = bool(model_loaded)
  state.modelRuntime = model_runtime
  state.modelExecutionTimeMs = float(exec_ms)
  state.processingWidth = int(processing_width)
  state.trafficState = traffic.state
  state.trafficConfidence = float(traffic.confidence)
  state.trafficRedProb = float(traffic.red_prob)
  state.trafficGreenProb = float(traffic.green_prob)
  state.trafficYellowProb = float(traffic.yellow_prob)
  state.trafficLeftProb = float(traffic.left_prob)
  state.trafficNoSignalProb = float(traffic.no_signal_prob)
  state.trafficCandidateCount = int(traffic.candidate_count)
  state.trafficCandidateX = float(traffic.candidate_x)
  state.trafficCandidateY = float(traffic.candidate_y)
  state.trafficCandidateW = float(traffic.candidate_w)
  state.trafficCandidateH = float(traffic.candidate_h)
  state.stopLineState = stopline.state
  state.stopLineConfidence = float(stopline.confidence)
  state.stopLineScore = float(stopline.stopline_score)
  state.crosswalkScore = float(stopline.crosswalk_score)
  state.stopLineYNorm = float(stopline.y_norm)
  state.stopLineWidthNorm = float(stopline.width_norm)
  state.stopLineStripeCount = int(stopline.stripe_count)
  state.leadBlocked = bool(stopline.lead_blocked)
  state.darkOrGlare = bool(stopline.dark_or_glare)
  state.source = f"{traffic.source}+road_roi_projection"
  return dat


def _load_model_once(backend: str | None = None):
  model = create_traffic_light_model(backend=backend)
  model.load()
  return model


def main() -> None:
  import numpy as np
  from cereal import messaging
  from msgq.visionipc import VisionIpcClient, VisionStreamType
  from openpilot.common.params import Params

  params = Params()
  pm = messaging.PubMaster(["trafficLightStopLineState"])
  sm = messaging.SubMaster(["roadCameraState"], poll="roadCameraState")
  vipc_client = VisionIpcClient("camerad", VisionStreamType.VISION_STREAM_ROAD, True)
  vipc_error_logged = False
  load_error_logged = False
  last_inference_t = 0.0
  next_model_retry_t = 0.0
  model = None

  while True:
    try:
      if not params.get_bool("TrafficLightStopLineModelEnabled"):
        model = None
        load_error_logged = False
        time.sleep(1.0)
        continue

      now = time.monotonic()
      if model is None and now >= next_model_retry_t:
        try:
          model = _load_model_once()
          cloudlog.info("traffic_light_stoplined loaded %s model", getattr(model, "runtime", "unknown"))
        except Exception:
          next_model_retry_t = now + MODEL_RETRY_SEC
          model = None
          if not load_error_logged:
            cloudlog.exception("traffic_light_stoplined model load failed; using heuristic telemetry")
            load_error_logged = True

      if not vipc_client.is_connected():
        if not vipc_error_logged:
          cloudlog.info("traffic_light_stoplined connecting to camerad VisionIPC")
          vipc_error_logged = True
        vipc_client.connect(True)
        pm.send("trafficLightStopLineState", _traffic_light_stopline_msg(
          messaging,
          model_loaded=model is not None and getattr(model, "loaded", False),
          model_runtime=getattr(model, "runtime", "none") if model is not None else "none",
        ))
        time.sleep(0.2)
        continue
      vipc_error_logged = False

      vision_buf = vipc_client.recv()
      sm.update(0)
      if vision_buf is None:
        continue

      now = time.monotonic()
      if now - last_inference_t < 1.0 / TRAFFIC_LIGHT_STOPLINE_HZ:
        continue
      last_inference_t = now

      frame_id = int(getattr(vipc_client, "frame_id", 0))
      timestamp_eof = int(getattr(vipc_client, "timestamp_eof", 0))
      start_t = time.monotonic()
      yuv_frame = _yuv_from_vipc(np, vision_buf)
      if yuv_frame is None:
        pm.send("trafficLightStopLineState", _traffic_light_stopline_msg(
          messaging,
          frame_id=frame_id,
          timestamp_eof=timestamp_eof,
          model_loaded=model is not None and getattr(model, "loaded", False),
          model_runtime=getattr(model, "runtime", "none") if model is not None else "none",
          inference_skipped=True,
        ))
        continue

      yuv_img, width, height = yuv_frame
      rgb = _sample_nv12_rgb(np, yuv_img, width, height, PROCESSING_WIDTH)
      traffic = detect_traffic_light(np, rgb, model=model)
      stopline = detect_stopline(np, rgb)
      exec_ms = (time.monotonic() - start_t) * 1000.0

      pm.send("trafficLightStopLineState", _traffic_light_stopline_msg(
        messaging,
        frame_id=frame_id,
        timestamp_eof=timestamp_eof,
        exec_ms=exec_ms,
        processing_width=rgb.shape[1],
        model_loaded=model is not None and getattr(model, "loaded", False),
        model_runtime=getattr(model, "runtime", "none") if model is not None else "none",
        traffic=traffic,
        stopline=stopline,
        inference_skipped=False,
      ))

    except Exception:
      cloudlog.exception("traffic_light_stoplined unexpected error; continuing shadow telemetry")
      time.sleep(5.0)


def _draw_disc(np, img, cx: int, cy: int, radius: int, color: tuple[int, int, int]) -> None:
  yy, xx = np.ogrid[:img.shape[0], :img.shape[1]]
  mask = (xx - cx) ** 2 + (yy - cy) ** 2 <= radius ** 2
  img[mask] = color


def run_smoke_test() -> int:
  import numpy as np

  img = np.zeros((720, 1280, 3), dtype=np.uint8)
  img[:] = (48, 50, 52)
  img[int(0.36 * img.shape[0]):] = (76, 78, 80)
  _draw_disc(np, img, 640, 130, 14, (235, 30, 22))
  for row in (460, 490, 520, 550):
    img[row:row + 8, 260:1020] = (235, 235, 232)
  img[610:620, 210:1070] = (245, 245, 242)

  traffic = detect_traffic_light(np, img, model=None)
  stopline = detect_stopline(np, img)
  print(json.dumps({
    "traffic": asdict(traffic),
    "stopline": asdict(stopline),
  }, indent=2))
  return 0 if traffic.state in ("Stop", "Caution", "Straight") and stopline.state != "no_clear_stopline" else 1


def run_model_smoke_test(backend: str | None = None) -> int:
  import numpy as np

  model = _load_model_once(backend=backend)
  dummy = np.zeros((1, 3, TRAFFIC_CROP_SIZE, TRAFFIC_CROP_SIZE), dtype=np.float32)
  probs = _softmax(np, model.predict_logits(dummy))
  pred_idx = int(np.argmax(probs[0]))
  class_names = getattr(model, "class_names", CLASS_NAMES)
  label = class_names[pred_idx] if pred_idx < len(class_names) else "unknown"
  print(json.dumps({
    "backend": getattr(model, "runtime", "unknown"),
    "label": label,
    "confidence": float(probs[0][pred_idx]),
    "classes": list(class_names),
  }, indent=2))
  return 0


if __name__ == "__main__":
  parser = argparse.ArgumentParser(description="traffic light and stopline shadow daemon")
  parser.add_argument("--smoke-test", action="store_true")
  parser.add_argument("--model-smoke-test", action="store_true")
  parser.add_argument("--backend", default=None)
  args = parser.parse_args()

  if args.smoke_test:
    raise SystemExit(run_smoke_test())
  if args.model_smoke_test:
    raise SystemExit(run_model_smoke_test(args.backend))
  main()
