#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import io
import json
import math
import os
import shutil
import tempfile
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import cv2
import numpy as np


LABEL_UNKNOWN = "unknown"
LABEL_WHITE_DASHED = "white_dashed"
LABEL_WHITE_SOLID = "white_solid"
LABEL_YELLOW_SOLID = "yellow_solid"
LABEL_ROAD_EDGE = "road_edge_or_barrier"


@dataclass
class LaneCandidate:
  side: str
  x_eval: float
  slope: float
  intercept: float
  segment_count: int
  avg_length: float
  max_length: float
  color: str
  pattern: str
  label: str
  confidence: float
  visibility: float
  occupied_ratio: float
  longest_run_ratio: float
  gap_ratio: float
  brightness: float
  reason: str


@dataclass
class LogContext:
  source: str
  car_speeds: list[float]
  left_lane_lines: list[int]
  right_lane_lines: list[int]
  left_model_probs: list[float]
  right_model_probs: list[float]


def prepare_capnp_schema(repo_root: Path) -> Path:
  schema_root = Path(tempfile.gettempdir()) / "op_capnp_schema_lane_labeler"
  include_dir = schema_root / "include"
  include_dir.mkdir(parents=True, exist_ok=True)

  copies = [
    (repo_root / "cereal" / "log.capnp", schema_root / "log.capnp"),
    (repo_root / "cereal" / "legacy.capnp", schema_root / "legacy.capnp"),
    (repo_root / "cereal" / "custom.capnp", schema_root / "custom.capnp"),
    (repo_root / "opendbc_repo" / "opendbc" / "car" / "car.capnp", schema_root / "car.capnp"),
    (repo_root / "cereal" / "include" / "c++.capnp", include_dir / "c++.capnp"),
  ]
  for src, dst in copies:
    if not dst.exists() or dst.stat().st_mtime < src.stat().st_mtime:
      shutil.copyfile(src, dst)
  return schema_root


def load_log_context(qcamera: Path, log_mode: str, repo_root: Path) -> LogContext | None:
  if log_mode == "off":
    return None

  log_path: Path | None = None
  if log_mode in ("auto", "rlog"):
    candidate = qcamera.with_name("rlog.zst")
    if candidate.exists() and candidate.stat().st_size > 0:
      log_path = candidate
  if log_path is None and log_mode in ("auto", "qlog"):
    candidate = qcamera.with_name("qlog.zst")
    if candidate.exists() and candidate.stat().st_size > 0:
      log_path = candidate
  if log_path is None:
    return None

  try:
    import capnp
    import zstandard as zstd
  except Exception:
    return None

  schema_root = prepare_capnp_schema(repo_root)
  capnp.remove_import_hook()
  log = capnp.load(str(schema_root / "log.capnp"))

  raw = log_path.read_bytes()
  with zstd.ZstdDecompressor().stream_reader(io.BytesIO(raw)) as reader:
    dat = reader.read()

  car_speeds: list[float] = []
  left_lane_lines: list[int] = []
  right_lane_lines: list[int] = []
  left_model_probs: list[float] = []
  right_model_probs: list[float] = []

  events = log.Event.read_multiple_bytes(dat)
  try:
    for event in events:
      try:
        which = event.which()
      except Exception:
        continue
      if which == "carState":
        cs = event.carState
        car_speeds.append(float(cs.vEgo))
        left_lane_lines.append(int(cs.leftLaneLine))
        right_lane_lines.append(int(cs.rightLaneLine))
      elif which == "modelV2":
        md = event.modelV2
        probs = list(md.laneLineProbs)
        left_model_probs.append(float(probs[1]) if len(probs) > 1 else 0.0)
        right_model_probs.append(float(probs[2]) if len(probs) > 2 else 0.0)
  except Exception:
    # Keep the messages read before a corrupted event, matching openpilot logreader's
    # "best effort" behavior.
    pass

  if not car_speeds and not left_model_probs and not right_model_probs:
    return None

  return LogContext(
    source=str(log_path),
    car_speeds=car_speeds,
    left_lane_lines=left_lane_lines,
    right_lane_lines=right_lane_lines,
    left_model_probs=left_model_probs,
    right_model_probs=right_model_probs,
  )


def _sample_by_frame(values: list[float] | list[int], frame_idx: int, frame_count: int, default):
  if not values:
    return default
  if frame_count <= 1:
    return values[0]
  idx = int(round((frame_idx / max(1, frame_count - 1)) * (len(values) - 1)))
  idx = max(0, min(len(values) - 1, idx))
  return values[idx]


def context_for_side(context: LogContext | None, side: str, frame_idx: int, frame_count: int) -> tuple[float | None, float | None, int | None]:
  if context is None:
    return None, None, None
  v_ego = _sample_by_frame(context.car_speeds, frame_idx, frame_count, None)
  if side == "left":
    model_prob = _sample_by_frame(context.left_model_probs, frame_idx, frame_count, None)
    lane_line = _sample_by_frame(context.left_lane_lines, frame_idx, frame_count, None)
  else:
    model_prob = _sample_by_frame(context.right_model_probs, frame_idx, frame_count, None)
    lane_line = _sample_by_frame(context.right_lane_lines, frame_idx, frame_count, None)
  return v_ego, model_prob, lane_line


def apply_context_gate(cand: LaneCandidate, v_ego: float | None, model_prob: float | None,
                       lane_line: int | None, min_speed_ms: float, min_model_prob: float) -> LaneCandidate:
  # Non-zero CAN lane type/color is stronger than the image heuristic when present.
  if lane_line is not None and lane_line >= 20:
    cand.label = LABEL_YELLOW_SOLID
    cand.color = "yellow"
    cand.pattern = "solid"
    cand.confidence = max(cand.confidence, 0.98)
    cand.reason = f"can_yellow:{lane_line}"
  elif lane_line is not None and lane_line > 0 and lane_line % 10 not in (0, 5):
    cand.label = LABEL_WHITE_SOLID
    cand.color = "white"
    cand.pattern = "solid"
    cand.confidence = max(cand.confidence, 0.95)
    cand.reason = f"can_solid:{lane_line}"

  if v_ego is not None and v_ego < min_speed_ms:
    cand.label = LABEL_UNKNOWN
    cand.pattern = "uncertain"
    cand.confidence = min(cand.confidence, 0.30)
    cand.reason = f"low_speed:{v_ego:.2f}"
    return cand

  if model_prob is not None and model_prob < min_model_prob:
    # Keep very strong yellow detections because centerlines can be visible even when the
    # generic lane-line probability is unstable. Otherwise, send weak cases to review.
    if not (cand.label == LABEL_YELLOW_SOLID and cand.confidence >= 0.90):
      cand.label = LABEL_UNKNOWN
      cand.pattern = "uncertain"
      cand.confidence = min(cand.confidence, 0.35)
      cand.reason = f"low_model_prob:{model_prob:.2f}"
  return cand


def iter_qcamera_paths(roots: Iterable[Path]) -> list[Path]:
  paths: list[Path] = []
  for root in roots:
    if root.is_file() and root.name == "qcamera.ts":
      paths.append(root)
    elif root.exists():
      paths.extend(root.rglob("qcamera.ts"))
  return sorted(p for p in paths if p.exists() and p.stat().st_size > 0)


def run_lengths(flags: np.ndarray) -> tuple[int, int, int]:
  if flags.size == 0:
    return 0, 0, 0

  longest_on = 0
  longest_off = 0
  transitions = 0
  cur = bool(flags[0])
  cur_len = 1

  for value in flags[1:]:
    value = bool(value)
    if value == cur:
      cur_len += 1
      continue

    if cur:
      longest_on = max(longest_on, cur_len)
    else:
      longest_off = max(longest_off, cur_len)
    transitions += 1
    cur = value
    cur_len = 1

  if cur:
    longest_on = max(longest_on, cur_len)
  else:
    longest_off = max(longest_off, cur_len)

  return longest_on, longest_off, transitions


def make_masks(frame: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray, float]:
  h, w = frame.shape[:2]
  hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
  gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
  brightness = float(np.mean(gray[int(h * 0.45):, :]))

  # Yellow lane paint is often dim in qcamera night footage, so keep V/S gates modest.
  yellow = cv2.inRange(hsv, np.array([12, 40, 45]), np.array([45, 255, 255]))

  # White paint can be low saturation, but headlights overexpose it. CLAHE helps edges.
  white = cv2.inRange(hsv, np.array([0, 0, 95]), np.array([180, 105, 255]))
  if brightness < 45:
    white_low = cv2.inRange(hsv, np.array([0, 0, 70]), np.array([180, 125, 255]))
    white = cv2.bitwise_or(white, white_low)

  kernel = np.ones((3, 3), np.uint8)
  yellow = cv2.morphologyEx(yellow, cv2.MORPH_OPEN, kernel)
  white = cv2.morphologyEx(white, cv2.MORPH_OPEN, kernel)

  combined = cv2.bitwise_or(yellow, white)

  roi = np.zeros((h, w), dtype=np.uint8)
  road_poly = np.array([[
    (0, h),
    (0, int(h * 0.50)),
    (int(w * 0.42), int(h * 0.38)),
    (int(w * 0.58), int(h * 0.38)),
    (w, int(h * 0.50)),
    (w, h),
  ]], dtype=np.int32)
  cv2.fillPoly(roi, road_poly, 255)
  combined = cv2.bitwise_and(combined, roi)
  yellow = cv2.bitwise_and(yellow, roi)
  white = cv2.bitwise_and(white, roi)
  return white, yellow, combined, brightness


def detect_line_segments(combined_mask: np.ndarray, frame: np.ndarray) -> list[tuple[int, int, int, int, float, float]]:
  h, _ = combined_mask.shape[:2]
  gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
  clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
  gray = clahe.apply(gray)
  edges = cv2.Canny(gray, 45, 140)
  edges = cv2.bitwise_and(edges, combined_mask)

  lines = cv2.HoughLinesP(edges, 1, np.pi / 180.0, threshold=18,
                          minLineLength=max(16, int(h * 0.06)), maxLineGap=18)
  if lines is None:
    return []

  segments: list[tuple[int, int, int, int, float, float]] = []
  for line in lines[:, 0, :]:
    x1, y1, x2, y2 = [int(v) for v in line]
    dy = y2 - y1
    dx = x2 - x1
    length = math.hypot(dx, dy)
    if length < 14 or abs(dy) < 8:
      continue
    slope = dx / dy
    if abs(slope) < 0.12 or abs(slope) > 3.0:
      continue
    intercept = x1 - slope * y1
    segments.append((x1, y1, x2, y2, slope, intercept))
  return segments


def classify_side(frame: np.ndarray, side: str, segments: list[tuple[int, int, int, int, float, float]],
                  white: np.ndarray, yellow: np.ndarray, brightness: float) -> LaneCandidate:
  h, w = frame.shape[:2]
  y_eval = int(h * 0.86)
  y_top = int(h * 0.43)
  y_bottom = int(h * 0.96)
  expected = w * (0.36 if side == "left" else 0.64)
  slope_sign = -1 if side == "left" else 1

  side_segments = []
  for x1, y1, x2, y2, slope, intercept in segments:
    if slope * slope_sign <= 0:
      continue
    x_eval = slope * y_eval + intercept
    if side == "left" and not (w * 0.02 <= x_eval <= w * 0.58):
      continue
    if side == "right" and not (w * 0.42 <= x_eval <= w * 0.98):
      continue
    length = math.hypot(x2 - x1, y2 - y1)
    dist_score = abs(x_eval - expected) / max(w * 0.22, 1.0)
    side_segments.append((dist_score, -length, x1, y1, x2, y2, slope, intercept, length, x_eval))

  if not side_segments:
    return LaneCandidate(side, 0.0, 0.0, 0.0, 0, 0.0, 0.0, "none", "none",
                         LABEL_UNKNOWN, 0.0, 0.0, 0.0, 0.0, 1.0, brightness, "no_line")

  side_segments.sort()
  seed = side_segments[0]
  seed_slope = seed[6]
  seed_intercept = seed[7]

  grouped = []
  for item in side_segments:
    _, _, x1, y1, x2, y2, slope, intercept, length, x_eval = item
    slope_close = abs(slope - seed_slope) < 0.35
    x_close = abs((slope * y_eval + intercept) - (seed_slope * y_eval + seed_intercept)) < w * 0.08
    if slope_close and x_close:
      grouped.append(item)

  lengths = [item[8] for item in grouped]
  slope = float(np.average([item[6] for item in grouped], weights=lengths))
  intercept = float(np.average([item[7] for item in grouped], weights=lengths))
  x_eval = slope * y_eval + intercept

  band = max(5, int(w * 0.018))
  y_values = np.arange(y_top, y_bottom)
  x_values = (slope * y_values + intercept).astype(np.int32)
  valid = (0 <= x_values) & (x_values < w)
  y_values = y_values[valid]
  x_values = x_values[valid]

  if y_values.size == 0:
    return LaneCandidate(side, x_eval, slope, intercept, len(grouped), float(np.mean(lengths)),
                         float(np.max(lengths)), "none", "none", LABEL_UNKNOWN, 0.0,
                         0.0, 0.0, 0.0, 1.0, brightness, "line_outside_frame")

  occupied_white = []
  occupied_yellow = []
  for y, x in zip(y_values, x_values, strict=False):
    xl = max(0, x - band)
    xr = min(w, x + band + 1)
    occupied_white.append(np.count_nonzero(white[y, xl:xr]) >= 2)
    occupied_yellow.append(np.count_nonzero(yellow[y, xl:xr]) >= 2)

  occupied_white_arr = np.array(occupied_white, dtype=bool)
  occupied_yellow_arr = np.array(occupied_yellow, dtype=bool)
  yellow_ratio = float(np.mean(occupied_yellow_arr))
  white_ratio = float(np.mean(occupied_white_arr))

  if yellow_ratio > max(0.05, white_ratio * 0.55):
    color = "yellow"
    occupied = occupied_yellow_arr
  elif white_ratio > 0.04:
    color = "white"
    occupied = occupied_white_arr
  else:
    color = "unknown"
    occupied = occupied_white_arr | occupied_yellow_arr

  occupied_ratio = float(np.mean(occupied)) if occupied.size else 0.0
  longest_on, longest_off, transitions = run_lengths(occupied)
  denom = max(1, occupied.size)
  longest_run_ratio = longest_on / denom
  gap_ratio = longest_off / denom
  visibility = max(yellow_ratio, white_ratio, occupied_ratio)
  max_length = float(np.max(lengths))
  avg_length = float(np.mean(lengths))

  if color == "yellow":
    pattern = "solid"
    label = LABEL_YELLOW_SOLID
    confidence = min(0.98, 0.45 + yellow_ratio * 1.7 + min(max_length / 130.0, 0.25))
    reason = "yellow_mask"
  elif color == "white":
    solid_score = 0.0
    solid_score += min(max_length / 150.0, 1.0) * 0.35
    solid_score += min(longest_run_ratio / 0.45, 1.0) * 0.35
    solid_score += min(occupied_ratio / 0.40, 1.0) * 0.25
    solid_score += 0.05 if transitions <= 3 else 0.0

    dashed_score = 0.0
    dashed_score += min(len(grouped) / 4.0, 1.0) * 0.25
    dashed_score += min(transitions / 8.0, 1.0) * 0.30
    dashed_score += 0.25 if 0.06 <= occupied_ratio <= 0.36 else 0.0
    dashed_score += 0.20 if gap_ratio > 0.18 and longest_run_ratio < 0.45 else 0.0

    near_edge = x_eval < w * 0.12 or x_eval > w * 0.88
    if near_edge and solid_score > 0.50:
      pattern = "edge"
      label = LABEL_ROAD_EDGE
      confidence = min(0.85, solid_score)
      reason = "near_image_edge"
    elif solid_score >= dashed_score + 0.12:
      pattern = "solid"
      label = LABEL_WHITE_SOLID
      confidence = min(0.95, solid_score)
      reason = f"solid_score={solid_score:.2f},dashed_score={dashed_score:.2f}"
    elif dashed_score > 0.36:
      pattern = "dashed"
      label = LABEL_WHITE_DASHED
      confidence = min(0.90, dashed_score)
      reason = f"solid_score={solid_score:.2f},dashed_score={dashed_score:.2f}"
    else:
      pattern = "uncertain"
      label = LABEL_UNKNOWN
      confidence = min(0.45, max(solid_score, dashed_score))
      reason = f"weak_white,solid_score={solid_score:.2f},dashed_score={dashed_score:.2f}"
  else:
    pattern = "uncertain"
    label = LABEL_UNKNOWN
    confidence = min(0.35, visibility)
    reason = "weak_color"

  if brightness < 20 and confidence < 0.75:
    label = LABEL_UNKNOWN
    pattern = "uncertain"
    confidence = min(confidence, 0.35)
    reason = "very_dark_" + reason

  return LaneCandidate(side, float(x_eval), float(slope), float(intercept), len(grouped),
                       avg_length, max_length, color, pattern, label, float(confidence),
                       float(visibility), occupied_ratio, float(longest_run_ratio),
                       float(gap_ratio), brightness, reason)


def label_frame(frame: np.ndarray) -> tuple[LaneCandidate, LaneCandidate]:
  white, yellow, combined, brightness = make_masks(frame)
  segments = detect_line_segments(combined, frame)
  left = classify_side(frame, "left", segments, white, yellow, brightness)
  right = classify_side(frame, "right", segments, white, yellow, brightness)
  return left, right


def draw_overlay(frame: np.ndarray, candidates: Iterable[LaneCandidate]) -> np.ndarray:
  out = frame.copy()
  h, w = out.shape[:2]
  for cand in candidates:
    color = (0, 255, 255) if cand.color == "yellow" else (255, 255, 255)
    if cand.label == LABEL_UNKNOWN:
      color = (0, 0, 255)
    elif cand.label == LABEL_WHITE_DASHED:
      color = (255, 180, 0)
    elif cand.label in (LABEL_WHITE_SOLID, LABEL_ROAD_EDGE):
      color = (255, 255, 255)
    y1, y2 = int(h * 0.43), int(h * 0.96)
    if cand.segment_count > 0:
      x1 = int(cand.slope * y1 + cand.intercept)
      x2 = int(cand.slope * y2 + cand.intercept)
      cv2.line(out, (x1, y1), (x2, y2), color, 2)
    text = f"{cand.side}:{cand.label} {cand.confidence:.2f}"
    y = 22 if cand.side == "left" else 44
    cv2.putText(out, text, (8, y), cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1, cv2.LINE_AA)
  cv2.line(out, (w // 2, int(h * 0.40)), (w // 2, h), (80, 80, 80), 1)
  return out


def update_preview(preview_dir: Path, frame: np.ndarray, qcamera: Path, frame_idx: int,
                   candidates: Iterable[LaneCandidate], preview_counts: Counter,
                   max_preview_per_label: int) -> None:
  if max_preview_per_label <= 0:
    return

  candidate_list = list(candidates)
  for cand in candidate_list:
    key = f"{cand.side}_{cand.label}"
    if preview_counts[key] >= max_preview_per_label:
      continue
    preview_counts[key] += 1
    label_dir = preview_dir / key
    label_dir.mkdir(parents=True, exist_ok=True)
    safe_segment = qcamera.parent.name.replace(":", "_").replace("\\", "_").replace("/", "_")
    out = draw_overlay(frame, candidate_list)
    cv2.imwrite(str(label_dir / f"{safe_segment}_f{frame_idx:05d}.jpg"), out)


def dominant_segment_label(labels: list[dict[str, str]], side: str, min_conf: float) -> tuple[str, float, int]:
  side_rows = [r for r in labels if r["side"] == side]
  if not side_rows:
    return LABEL_UNKNOWN, 0.0, 0

  good = [r for r in side_rows if float(r["confidence"]) >= min_conf and r["label"] != LABEL_UNKNOWN]
  if not good:
    return LABEL_UNKNOWN, 0.0, 0

  counts = Counter(r["label"] for r in good)
  label, count = counts.most_common(1)[0]
  share = count / max(1, len(good))
  return label, share, len(good)


def process_video(qcamera: Path, sample_sec: float, min_conf: float,
                  min_speed_ms: float, min_model_prob: float, log_mode: str, repo_root: Path,
                  preview_dir: Path, preview_counts: Counter, max_preview_per_label: int) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
  cap = cv2.VideoCapture(str(qcamera))
  if not cap.isOpened():
    return [], [{
      "qcamera": str(qcamera),
      "side": "both",
      "dominant_label": LABEL_UNKNOWN,
      "dominant_share": "0.000",
      "high_conf_samples": "0",
      "sample_count": "0",
      "status": "video_open_failed",
    }]

  context = load_log_context(qcamera, log_mode, repo_root)
  fps = cap.get(cv2.CAP_PROP_FPS) or 20.0
  frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
  step = max(1, int(round(fps * sample_sec)))

  rows: list[dict[str, str]] = []
  for frame_idx in range(0, frame_count, step):
    cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
    ok, frame = cap.read()
    if not ok or frame is None:
      continue
    time_sec = frame_idx / fps
    left, right = label_frame(frame)
    enriched: list[tuple[LaneCandidate, float | None, float | None, int | None]] = []
    for cand in (left, right):
      v_ego, model_prob, lane_line = context_for_side(context, cand.side, frame_idx, frame_count)
      cand = apply_context_gate(cand, v_ego, model_prob, lane_line, min_speed_ms, min_model_prob)
      enriched.append((cand, v_ego, model_prob, lane_line))
    for cand, v_ego, model_prob, lane_line in enriched:
      rows.append({
        "qcamera": str(qcamera),
        "log_source": context.source if context is not None else "",
        "segment": qcamera.parent.name,
        "frame_idx": str(frame_idx),
        "time_sec": f"{time_sec:.3f}",
        "side": cand.side,
        "label": cand.label,
        "color": cand.color,
        "pattern": cand.pattern,
        "confidence": f"{cand.confidence:.3f}",
        "visibility": f"{cand.visibility:.3f}",
        "occupied_ratio": f"{cand.occupied_ratio:.3f}",
        "longest_run_ratio": f"{cand.longest_run_ratio:.3f}",
        "gap_ratio": f"{cand.gap_ratio:.3f}",
        "x_eval": f"{cand.x_eval:.1f}",
        "slope": f"{cand.slope:.3f}",
        "segment_count": str(cand.segment_count),
        "avg_segment_length": f"{cand.avg_length:.1f}",
        "max_segment_length": f"{cand.max_length:.1f}",
        "brightness": f"{cand.brightness:.1f}",
        "v_ego": "" if v_ego is None else f"{float(v_ego):.3f}",
        "model_lane_prob": "" if model_prob is None else f"{float(model_prob):.3f}",
        "can_lane_line": "" if lane_line is None else str(int(lane_line)),
        "reason": cand.reason,
      })
    update_preview(preview_dir, frame, qcamera, frame_idx, [item[0] for item in enriched],
                   preview_counts, max_preview_per_label)

  segment_rows = []
  for side in ("left", "right"):
    dominant, share, high_conf = dominant_segment_label(rows, side, min_conf)
    segment_rows.append({
      "qcamera": str(qcamera),
      "segment": qcamera.parent.name,
      "side": side,
      "dominant_label": dominant,
      "dominant_share": f"{share:.3f}",
      "high_conf_samples": str(high_conf),
      "sample_count": str(sum(1 for r in rows if r["side"] == side)),
      "status": "ok",
    })
  return rows, segment_rows


def write_csv(path: Path, rows: list[dict[str, str]], fieldnames: list[str]) -> None:
  path.parent.mkdir(parents=True, exist_ok=True)
  with path.open("w", newline="", encoding="utf-8") as f:
    writer = csv.DictWriter(f, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(rows)


def main() -> int:
  parser = argparse.ArgumentParser(description="Pseudo-label qcamera lane markings for lane-change blocking.")
  parser.add_argument("--root", action="append", required=True, help="Directory or qcamera.ts path. Can be passed multiple times.")
  parser.add_argument("--out-dir", required=True, help="Directory for labels.csv, segment_labels.csv, and previews.")
  parser.add_argument("--sample-sec", type=float, default=1.0, help="Frame sampling interval in seconds.")
  parser.add_argument("--min-conf", type=float, default=0.55, help="Confidence threshold for segment dominant labels.")
  parser.add_argument("--min-speed-ms", type=float, default=5.0, help="Below this speed, labels are forced to unknown.")
  parser.add_argument("--min-model-prob", type=float, default=0.25, help="Below this model lane probability, labels are forced to unknown.")
  parser.add_argument("--log-mode", choices=["auto", "rlog", "qlog", "off"], default="auto", help="Use sibling logs for speed/model gates.")
  parser.add_argument("--max-segments", type=int, default=0, help="Limit processed qcamera files for testing.")
  parser.add_argument("--preview-per-label", type=int, default=8, help="Max preview images per side/label.")
  args = parser.parse_args()

  roots = [Path(p) for p in args.root]
  qcamera_paths = iter_qcamera_paths(roots)
  if args.max_segments > 0:
    qcamera_paths = qcamera_paths[:args.max_segments]

  out_dir = Path(args.out_dir)
  preview_dir = out_dir / "previews"
  preview_dir.mkdir(parents=True, exist_ok=True)

  all_rows: list[dict[str, str]] = []
  all_segment_rows: list[dict[str, str]] = []
  preview_counts: Counter = Counter()
  repo_root = Path(__file__).resolve().parents[2]

  for idx, qcamera in enumerate(qcamera_paths, start=1):
    print(f"[{idx}/{len(qcamera_paths)}] {qcamera}", flush=True)
    rows, segment_rows = process_video(qcamera, args.sample_sec, args.min_conf,
                                       args.min_speed_ms, args.min_model_prob, args.log_mode, repo_root,
                                       preview_dir, preview_counts, args.preview_per_label)
    all_rows.extend(rows)
    all_segment_rows.extend(segment_rows)

  label_fields = [
    "qcamera", "log_source", "segment", "frame_idx", "time_sec", "side", "label", "color", "pattern",
    "confidence", "visibility", "occupied_ratio", "longest_run_ratio", "gap_ratio",
    "x_eval", "slope", "segment_count", "avg_segment_length", "max_segment_length",
    "brightness", "v_ego", "model_lane_prob", "can_lane_line", "reason",
  ]
  segment_fields = [
    "qcamera", "segment", "side", "dominant_label", "dominant_share",
    "high_conf_samples", "sample_count", "status",
  ]
  write_csv(out_dir / "labels.csv", all_rows, label_fields)
  write_csv(out_dir / "segment_labels.csv", all_segment_rows, segment_fields)

  frame_counts = Counter(row["label"] for row in all_rows)
  side_label_counts = Counter(f"{row['side']}:{row['label']}" for row in all_rows)
  segment_counts = Counter(row["dominant_label"] for row in all_segment_rows)
  summary = {
    "roots": [str(r) for r in roots],
    "qcamera_count": len(qcamera_paths),
    "sample_sec": args.sample_sec,
    "min_conf": args.min_conf,
    "min_speed_ms": args.min_speed_ms,
    "min_model_prob": args.min_model_prob,
    "log_mode": args.log_mode,
    "frame_side_sample_count": len(all_rows),
    "frame_label_counts": dict(frame_counts),
    "frame_side_label_counts": dict(side_label_counts),
    "segment_side_label_counts": dict(segment_counts),
    "outputs": {
      "labels_csv": str(out_dir / "labels.csv"),
      "segment_labels_csv": str(out_dir / "segment_labels.csv"),
      "previews": str(preview_dir),
    },
  }
  with (out_dir / "summary.json").open("w", encoding="utf-8") as f:
    json.dump(summary, f, indent=2, ensure_ascii=False)

  print(json.dumps(summary, indent=2, ensure_ascii=False))
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
