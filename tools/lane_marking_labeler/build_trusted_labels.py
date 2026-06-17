#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import random
from collections import Counter, defaultdict
from pathlib import Path


DEFAULT_MIN_CONF = {
  "white_dashed": 0.80,
  "white_solid": 0.68,
  "yellow_solid": 0.82,
  "yellow_double_solid": 0.84,
  "yellow_double_dashed": 0.86,
  "road_edge_or_barrier": 0.70,
}


def parse_thresholds(values: list[str]) -> dict[str, float]:
  thresholds = dict(DEFAULT_MIN_CONF)
  for item in values:
    if "=" not in item:
      raise ValueError(f"invalid threshold override: {item}")
    label, value = item.split("=", 1)
    thresholds[label.strip()] = float(value)
  return thresholds


def as_float(row: dict[str, str], key: str, default: float = 0.0) -> float:
  try:
    return float(row.get(key, "") or default)
  except (TypeError, ValueError):
    return default


def row_is_trusted(row: dict[str, str], thresholds: dict[str, float], min_speed_ms: float,
                   min_model_prob: float, require_positive_model_prob: bool) -> tuple[bool, str]:
  label = row.get("label", "")
  if label not in thresholds:
    return False, "unsupported_label"

  confidence = as_float(row, "confidence")
  if confidence < thresholds[label]:
    return False, "low_confidence"

  v_ego_text = row.get("v_ego", "")
  if v_ego_text and as_float(row, "v_ego") < min_speed_ms:
    return False, "low_speed"

  model_prob_text = row.get("model_lane_prob", "")
  if require_positive_model_prob and not model_prob_text:
    return False, "missing_model_prob"
  if model_prob_text and as_float(row, "model_lane_prob") < min_model_prob:
    if not (label.startswith("yellow") and confidence >= thresholds[label] + 0.08):
      return False, "low_model_prob"

  if label == "white_dashed" and row.get("pattern") != "dashed":
    return False, "dashed_pattern_mismatch"

  if label.startswith("yellow") and row.get("color") != "yellow":
    return False, "yellow_color_mismatch"

  return True, "trusted"


def main() -> int:
  parser = argparse.ArgumentParser(description="Promote high-confidence pseudo labels to corrected_labels.csv.")
  parser.add_argument("--labels", required=True, help="Input labels.csv from auto_label_qcamera.py.")
  parser.add_argument("--out", required=True, help="Output corrected_labels.csv.")
  parser.add_argument("--summary", default="", help="Optional summary JSON path.")
  parser.add_argument("--max-per-class", type=int, default=7000, help="Deterministic cap per class. 0 keeps all trusted rows.")
  parser.add_argument("--seed", type=int, default=42)
  parser.add_argument("--min-speed-ms", type=float, default=5.0)
  parser.add_argument("--min-model-prob", type=float, default=0.25)
  parser.add_argument("--require-positive-model-prob", action="store_true")
  parser.add_argument("--threshold", action="append", default=[], help="Override label threshold, e.g. white_dashed=0.85.")
  args = parser.parse_args()

  labels_path = Path(args.labels)
  out_path = Path(args.out)
  summary_path = Path(args.summary) if args.summary else out_path.with_suffix(".summary.json")
  thresholds = parse_thresholds(args.threshold)

  with labels_path.open("r", newline="", encoding="utf-8") as f:
    reader = csv.DictReader(f)
    fieldnames = list(reader.fieldnames or [])
    rows = list(reader)

  trusted_by_class: dict[str, list[tuple[int, dict[str, str]]]] = defaultdict(list)
  reject_reasons: Counter[str] = Counter()
  source_segments: Counter[str] = Counter()

  for row_id, row in enumerate(rows):
    ok, reason = row_is_trusted(row, thresholds, args.min_speed_ms, args.min_model_prob,
                                args.require_positive_model_prob)
    if not ok:
      reject_reasons[reason] += 1
      continue
    trusted_by_class[row["label"]].append((row_id, row))
    source_segments[row.get("segment", "")] += 1

  rng = random.Random(args.seed)
  kept: list[tuple[int, dict[str, str]]] = []
  selected_counts: dict[str, int] = {}
  for label in sorted(trusted_by_class):
    label_rows = trusted_by_class[label]
    rng.shuffle(label_rows)
    if args.max_per_class > 0:
      label_rows = label_rows[:args.max_per_class]
    label_rows.sort(key=lambda item: item[0])
    selected_counts[label] = len(label_rows)
    kept.extend(label_rows)

  kept.sort(key=lambda item: item[0])

  output_fields = ["row_id", "original_label", "corrected_label", "review_status"]
  output_fields += [name for name in fieldnames if name not in output_fields]
  out_path.parent.mkdir(parents=True, exist_ok=True)
  with out_path.open("w", newline="", encoding="utf-8") as f:
    writer = csv.DictWriter(f, fieldnames=output_fields)
    writer.writeheader()
    for row_id, row in kept:
      out_row = dict(row)
      label = row["label"]
      out_row.update({
        "row_id": str(row_id),
        "original_label": label,
        "corrected_label": label,
        "review_status": "auto_trusted",
      })
      writer.writerow(out_row)

  summary = {
    "input_labels": str(labels_path),
    "output_corrected_labels": str(out_path),
    "total_rows": len(rows),
    "trusted_rows_before_cap": int(sum(len(v) for v in trusted_by_class.values())),
    "trusted_rows_after_cap": len(kept),
    "selected_counts": selected_counts,
    "rejected_counts": dict(reject_reasons),
    "thresholds": thresholds,
    "max_per_class": args.max_per_class,
    "segment_count_with_trusted_rows": len(source_segments),
  }
  with summary_path.open("w", encoding="utf-8") as f:
    json.dump(summary, f, indent=2, ensure_ascii=False)
  print(json.dumps(summary, indent=2, ensure_ascii=False))
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
