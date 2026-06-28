#!/usr/bin/env python3
from __future__ import annotations

import argparse
import logging
import time
from dataclasses import dataclass

PROCESS_NAME = "selfdrive.sidevision.sidevisiond"
SIDE_VISION_HZ = 5.0
DEFAULT_THRESHOLD = 0.60
HOLD_SEC = 0.6

# Driver-camera window ROIs. These are intentionally broad and conservative:
# the output is shadow telemetry, not a control input.
LEFT_ROI = (0.02, 0.18, 0.34, 0.86)
RIGHT_ROI = (0.66, 0.18, 0.98, 0.86)


try:
  from openpilot.common.swaglog import cloudlog
except Exception:
  logging.basicConfig(level=logging.INFO)
  cloudlog = logging.getLogger(PROCESS_NAME)


def _normalize_threshold(value: int | float | str | None, default: float = DEFAULT_THRESHOLD) -> float:
  if value is None:
    return default

  try:
    threshold = float(value)
  except (TypeError, ValueError):
    return default

  if threshold > 1.0:
    threshold /= 100.0
  return min(max(threshold, 0.0), 1.0)


def _threshold_from_params(params) -> float:
  return _normalize_threshold(params.get("SideVisionConfidenceThreshold", return_default=True))


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


def _sample_roi_gray(np, yuv_img, width: int, height: int, roi_norm: tuple[float, float, float, float], size: tuple[int, int] = (96, 96)):
  out_h, out_w = size
  x0, y0, x1, y1 = roi_norm
  u0 = int(max(0, min(width - 1, round(x0 * width))))
  v0 = int(max(0, min(height - 1, round(y0 * height))))
  u1 = int(max(u0 + 1, min(width, round(x1 * width))))
  v1 = int(max(v0 + 1, min(height, round(y1 * height))))
  if u1 <= u0 or v1 <= v0:
    return np.zeros((out_h, out_w), dtype=np.uint8)

  ys = np.linspace(v0, v1 - 1, out_h).astype(np.int32)
  xs = np.linspace(u0, u1 - 1, out_w).astype(np.int32)
  return yuv_img[ys[:, None], xs[None, :]]


def _box_blur3(np, gray):
  padded = np.pad(gray.astype(np.float32), 1, mode="edge")
  blurred = (
    padded[:-2, :-2] + padded[:-2, 1:-1] + padded[:-2, 2:] +
    padded[1:-1, :-2] + padded[1:-1, 1:-1] + padded[1:-1, 2:] +
    padded[2:, :-2] + padded[2:, 1:-1] + padded[2:, 2:]
  ) / 9.0
  return blurred.astype(np.uint8)


def _edge_density(np, gray):
  grad_x = np.abs(np.diff(gray.astype(np.int16), axis=1))
  grad_y = np.abs(np.diff(gray.astype(np.int16), axis=0))
  edges = np.zeros_like(gray, dtype=np.bool_)
  edges[:, 1:] |= grad_x > 35
  edges[1:, :] |= grad_y > 35
  return float(np.mean(edges))


@dataclass
class SideScore:
  prob: float = 0.0
  detected: bool = False
  reason: str = ""


class RoiOccupancyTracker:
  def __init__(self) -> None:
    self.prev_gray = None
    self.background = None
    self.hold_count = 0

  def update(self, np, gray_roi, threshold: float) -> SideScore:
    if gray_roi.size == 0:
      self.hold_count = max(0, self.hold_count - 1)
      return SideScore()

    gray = _box_blur3(np, gray_roi)

    if self.background is None or self.prev_gray is None:
      self.background = gray.astype(np.float32)
      self.prev_gray = gray
      return SideScore(reason="warming")

    diff_prev = np.abs(gray.astype(np.int16) - self.prev_gray.astype(np.int16)).astype(np.uint8)
    background_u8 = np.clip(self.background, 0, 255).astype(np.uint8)
    diff_bg = np.abs(gray.astype(np.int16) - background_u8.astype(np.int16)).astype(np.uint8)
    motion = float(np.mean(diff_prev) / 255.0)
    background_delta = float(np.mean(diff_bg) / 255.0)
    edge_density = _edge_density(np, gray)
    contrast = float(np.std(gray) / 80.0)

    # Broad ROI objectness. This is tuned to be useful for shadow logs:
    # high texture/edges plus either motion or a background change.
    prob = (
      edge_density * 3.0 +
      background_delta * 1.8 +
      motion * 2.5 +
      min(contrast, 1.0) * 0.25
    )
    prob = float(max(0.0, min(1.0, prob)))

    detected_now = prob >= threshold
    if detected_now:
      self.hold_count = max(self.hold_count, int(HOLD_SEC * SIDE_VISION_HZ))
    else:
      self.hold_count = max(0, self.hold_count - 1)

    bg_alpha = 0.02 if self.hold_count > 0 else 0.05
    self.background = (1.0 - bg_alpha) * self.background + bg_alpha * gray.astype(np.float32)
    self.prev_gray = gray

    reason = f"roi={prob:.2f},edge={edge_density:.2f},motion={motion:.2f},bg={background_delta:.2f}"
    return SideScore(prob=prob, detected=self.hold_count > 0, reason=reason)


def _safe_float(obj, attr: str, default: float = 0.0) -> float:
  try:
    return float(getattr(obj, attr))
  except Exception:
    return default


def _safe_bool(obj, attr: str, default: bool = False) -> bool:
  try:
    return bool(getattr(obj, attr))
  except Exception:
    return default


def _radar_side_detected(lead, v_ego: float) -> tuple[bool, float]:
  if lead is None or not _safe_bool(lead, "status"):
    return False, 0.0

  d_rel = _safe_float(lead, "dRel", 0.0)
  v_rel = _safe_float(lead, "vRel", 0.0)
  if d_rel <= 0.0:
    return False, d_rel

  close_dist = max(8.0, min(18.0, v_ego * 1.2))
  closing_dist = d_rel + min(v_rel, 0.0) * 2.0
  detected = d_rel < close_dist or closing_dist < max(6.0, v_ego * 0.8)
  return detected, d_rel


def _road_camera_side_leads(model_data) -> tuple[tuple[bool, float, float], tuple[bool, float, float]]:
  left = (False, 0.0, 0.0)
  right = (False, 0.0, 0.0)
  if model_data is None:
    return left, right

  try:
    leads = list(model_data.leadsV3)
  except Exception:
    return left, right

  for lead in leads:
    try:
      prob = float(lead.prob)
      if prob < 0.45 or len(lead.x) == 0 or len(lead.y) == 0:
        continue
      d_rel = float(lead.x[0])
      y_rel = -float(lead.y[0])
    except Exception:
      continue

    if not (1.0 < d_rel < 35.0 and 2.0 < abs(y_rel) < 5.2):
      continue

    detected = d_rel < 18.0 or prob > 0.70
    candidate = (detected, d_rel, prob)
    if y_rel > 0.0:
      if left[1] == 0.0 or d_rel < left[1]:
        left = candidate
    else:
      if right[1] == 0.0 or d_rel < right[1]:
        right = candidate

  return left, right


def _corner_detected(long_dist: float, lat_dist: float) -> bool:
  if 0.0 < long_dist < 7.0:
    return True
  return 0.0 < long_dist < 10.0 and 0.0 < abs(lat_dist) < 2.2


def _join_reasons(*items: tuple[bool, str]) -> str:
  reasons = [reason for active, reason in items if active and reason]
  return ",".join(reasons) if reasons else "clear"


def _side_vision_msg(
  messaging,
  frame_id: int = 0,
  timestamp_eof: int = 0,
  road_frame_id: int = 0,
  exec_ms: float = 0.0,
  threshold: float = DEFAULT_THRESHOLD,
  left_score: SideScore | None = None,
  right_score: SideScore | None = None,
  car_state=None,
  radar_state=None,
  model_data=None,
  inference_skipped: bool = True,
):
  left_score = left_score or SideScore()
  right_score = right_score or SideScore()

  v_ego = _safe_float(car_state, "vEgo", 0.0)
  left_bsd = _safe_bool(car_state, "leftBlindspot")
  right_bsd = _safe_bool(car_state, "rightBlindspot")

  left_long = _safe_float(car_state, "leftLongDist")
  right_long = _safe_float(car_state, "rightLongDist")
  left_lat = _safe_float(car_state, "leftLatDist")
  right_lat = _safe_float(car_state, "rightLatDist")
  left_corner = _corner_detected(left_long, left_lat)
  right_corner = _corner_detected(right_long, right_lat)

  left_radar, left_radar_d = _radar_side_detected(getattr(radar_state, "leadLeft", None), v_ego)
  right_radar, right_radar_d = _radar_side_detected(getattr(radar_state, "leadRight", None), v_ego)
  (left_road, left_road_d, left_road_prob), (right_road, right_road_d, right_road_prob) = _road_camera_side_leads(model_data)

  left_camera = left_score.detected and left_score.prob >= threshold
  right_camera = right_score.detected and right_score.prob >= threshold

  left_blocked = left_camera or left_bsd or left_radar or left_corner or left_road
  right_blocked = right_camera or right_bsd or right_radar or right_corner or right_road

  dat = messaging.new_message("sideVisionState")
  dat.valid = True
  state = dat.sideVisionState
  state.frameId = int(frame_id)
  state.timestampEof = int(timestamp_eof)
  state.roadFrameId = int(road_frame_id)
  state.valid = not inference_skipped
  state.modelExecutionTimeMs = float(exec_ms)
  state.inferenceSkipped = bool(inference_skipped)
  state.leftCameraProb = float(left_score.prob)
  state.rightCameraProb = float(right_score.prob)
  state.leftCameraDetected = bool(left_camera)
  state.rightCameraDetected = bool(right_camera)
  state.leftBsd = bool(left_bsd)
  state.rightBsd = bool(right_bsd)
  state.leftRadarDetected = bool(left_radar)
  state.rightRadarDetected = bool(right_radar)
  state.leftRadarDRel = float(left_radar_d)
  state.rightRadarDRel = float(right_radar_d)
  state.leftCornerLongDist = float(left_long)
  state.rightCornerLongDist = float(right_long)
  state.leftCornerLatDist = float(left_lat)
  state.rightCornerLatDist = float(right_lat)
  state.leftBlocked = bool(left_blocked)
  state.rightBlocked = bool(right_blocked)
  state.leftRoadCameraDetected = bool(left_road)
  state.rightRoadCameraDetected = bool(right_road)
  state.leftRoadCameraDRel = float(left_road_d)
  state.rightRoadCameraDRel = float(right_road_d)
  state.leftRoadCameraProb = float(left_road_prob)
  state.rightRoadCameraProb = float(right_road_prob)
  state.leftReason = _join_reasons(
    (left_camera, left_score.reason),
    (left_road, f"road_camera:{left_road_d:.1f}m/{left_road_prob:.2f}"),
    (left_bsd, "bsd"),
    (left_radar, f"radar:{left_radar_d:.1f}m"),
    (left_corner, f"corner:{left_long:.1f}m/{left_lat:.1f}m"),
  )
  state.rightReason = _join_reasons(
    (right_camera, right_score.reason),
    (right_road, f"road_camera:{right_road_d:.1f}m/{right_road_prob:.2f}"),
    (right_bsd, "bsd"),
    (right_radar, f"radar:{right_radar_d:.1f}m"),
    (right_corner, f"corner:{right_long:.1f}m/{right_lat:.1f}m"),
  )
  state.source = "driver_camera_roi+modelV2+bsd+radarState+corner_distance"
  return dat


def main() -> None:
  import numpy as np
  from cereal import messaging
  from msgq.visionipc import VisionIpcClient, VisionStreamType
  from openpilot.common.params import Params

  params = Params()
  pm = messaging.PubMaster(["sideVisionState"])
  sm = messaging.SubMaster(
    ["carState", "radarState", "roadCameraState", "driverCameraState", "modelV2"],
    poll="driverCameraState",
    ignore_avg_freq=["radarState"],
  )

  left_tracker = RoiOccupancyTracker()
  right_tracker = RoiOccupancyTracker()
  vipc_client = VisionIpcClient("camerad", VisionStreamType.VISION_STREAM_DRIVER, True)
  vipc_error_logged = False
  last_inference_t = 0.0

  while True:
    try:
      if not params.get_bool("SideVisionModelEnabled"):
        time.sleep(1.0)
        continue

      threshold = _threshold_from_params(params)
      if not vipc_client.is_connected():
        if not vipc_error_logged:
          cloudlog.info("sidevisiond connecting to driver camera VisionIPC")
          vipc_error_logged = True
        vipc_client.connect(True)
        pm.send("sideVisionState", _side_vision_msg(messaging, threshold=threshold))
        time.sleep(0.2)
        continue
      vipc_error_logged = False

      vision_buf = vipc_client.recv()
      sm.update(0)
      if vision_buf is None:
        continue

      now = time.monotonic()
      if now - last_inference_t < 1.0 / SIDE_VISION_HZ:
        continue
      last_inference_t = now

      frame_id = int(getattr(vipc_client, "frame_id", 0))
      timestamp_eof = int(getattr(vipc_client, "timestamp_eof", 0))
      road_frame_id = int(sm["roadCameraState"].frameId) if sm.seen["roadCameraState"] else 0

      start_t = time.monotonic()
      yuv_frame = _yuv_from_vipc(np, vision_buf)
      if yuv_frame is None:
        pm.send("sideVisionState", _side_vision_msg(
          messaging,
          frame_id=frame_id,
          timestamp_eof=timestamp_eof,
          road_frame_id=road_frame_id,
          threshold=threshold,
          car_state=sm["carState"] if sm.seen["carState"] else None,
          radar_state=sm["radarState"] if sm.seen["radarState"] else None,
          model_data=sm["modelV2"] if sm.seen["modelV2"] else None,
          inference_skipped=True,
        ))
        continue
      yuv_img, width, height = yuv_frame

      left_roi = _sample_roi_gray(np, yuv_img, width, height, LEFT_ROI)
      right_roi = _sample_roi_gray(np, yuv_img, width, height, RIGHT_ROI)
      left_score = left_tracker.update(np, left_roi, threshold)
      right_score = right_tracker.update(np, right_roi, threshold)
      exec_ms = (time.monotonic() - start_t) * 1000.0

      pm.send("sideVisionState", _side_vision_msg(
        messaging,
        frame_id=frame_id,
        timestamp_eof=timestamp_eof,
        road_frame_id=road_frame_id,
        exec_ms=exec_ms,
        threshold=threshold,
        left_score=left_score,
        right_score=right_score,
        car_state=sm["carState"] if sm.seen["carState"] else None,
        radar_state=sm["radarState"] if sm.seen["radarState"] else None,
        model_data=sm["modelV2"] if sm.seen["modelV2"] else None,
        inference_skipped=False,
      ))

    except Exception:
      cloudlog.exception("sidevisiond unexpected error; continuing disabled/log-only")
      time.sleep(5.0)


def run_roi_smoke_test() -> int:
  import numpy as np

  tracker = RoiOccupancyTracker()
  threshold = DEFAULT_THRESHOLD
  blank = np.zeros((160, 160), dtype=np.uint8)
  textured = blank.copy()
  textured[30:135, 35:130] = 210
  for offset in range(-2, 3):
    ys = np.arange(30, 136)
    xs = 35 + ((ys - 30) * (130 - 35) // (135 - 30)) + offset
    valid = (xs >= 0) & (xs < textured.shape[1])
    textured[ys[valid], xs[valid]] = 20

  tracker.update(np, blank, threshold)
  score = tracker.update(np, textured, threshold)
  print(f"prob={score.prob:.3f} detected={score.detected} reason={score.reason}")
  return 0


if __name__ == "__main__":
  parser = argparse.ArgumentParser(description="Side vision shadow-mode runtime")
  parser.add_argument("--smoke-test", action="store_true", help="Run a synthetic ROI scorer smoke test.")
  args = parser.parse_args()

  if args.smoke_test:
    raise SystemExit(run_roi_smoke_test())
  main()
