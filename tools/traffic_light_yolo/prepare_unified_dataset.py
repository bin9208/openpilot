#!/usr/bin/env python3
"""Build a unified Korean traffic-light YOLO dataset.

The two Roboflow exports use different class taxonomies. This script maps both
exports into the 6-class driving-state taxonomy expected by the phone app and
openpilot integration:

  0 Caution
  1 LeftTurn
  2 NoSignal
  3 Stop
  4 Straight
  5 StraightLeft
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path


NAMES = ["Caution", "LeftTurn", "NoSignal", "Stop", "Straight", "StraightLeft"]

DATASET_MAPS = {
  "kr6": {0: 0, 1: 1, 2: 2, 3: 3, 4: 4, 5: 5},
  "color4": {0: 4, 1: 1, 2: 3, 3: 0},
}

IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".bmp", ".webp")


@dataclass(frozen=True)
class Record:
  image: Path
  label: Path
  source_dataset: str
  class_map_name: str
  split: str
  group_key: str


def canonical_group_key(stem: str) -> str:
  base = stem.split(".rf.", 1)[0]
  lowered = base.lower()

  # Keep video-derived adjacent frames together. Roboflow filenames usually
  # contain "...mp4_YYYYMMDD..." or "...-mp4_YYYYMMDD...".
  mp4_idx = lowered.find("mp4_")
  if mp4_idx >= 0:
    base = base[: mp4_idx + 3]

  # Keep simple numbered frame collections together, e.g. "1212 (106)_png".
  base = re.sub(r"\s*\(\d+\).*", "", base)

  key = re.sub(r"[^0-9a-zA-Z가-힣]+", "", base).lower()
  return key or stem.lower()


def find_image(images_dir: Path, label_stem: str) -> Path | None:
  for ext in IMAGE_EXTS:
    candidate = images_dir / f"{label_stem}{ext}"
    if candidate.exists():
      return candidate
  lower_map = {p.stem.lower(): p for p in images_dir.iterdir() if p.suffix.lower() in IMAGE_EXTS}
  return lower_map.get(label_stem.lower())


def collect_records(root: Path, dataset_name: str, class_map_name: str) -> list[Record]:
  records: list[Record] = []
  for split_dir in sorted(p for p in root.iterdir() if p.is_dir()):
    labels_dir = split_dir / "labels"
    images_dir = split_dir / "images"
    if not labels_dir.exists() or not images_dir.exists():
      continue

    for label in sorted(labels_dir.glob("*.txt")):
      image = find_image(images_dir, label.stem)
      if image is None:
        raise FileNotFoundError(f"No image found for label {label}")
      records.append(
        Record(
          image=image,
          label=label,
          source_dataset=dataset_name,
          class_map_name=class_map_name,
          split=split_dir.name,
          group_key=canonical_group_key(label.stem),
        )
      )
  return records


def split_group(group_key: str, seed: int, val_ratio: float, test_ratio: float) -> str:
  digest = hashlib.sha1(f"{seed}:{group_key}".encode("utf-8")).hexdigest()
  value = int(digest[:8], 16) / 0xFFFFFFFF
  if value < test_ratio:
    return "test"
  if value < test_ratio + val_ratio:
    return "valid"
  return "train"


def convert_label(src: Path, dst: Path, class_map: dict[int, int]) -> Counter[int]:
  counts: Counter[int] = Counter()
  converted: list[str] = []
  for line_no, raw in enumerate(src.read_text(encoding="utf-8").splitlines(), 1):
    line = raw.strip()
    if not line:
      continue
    parts = line.split()
    if len(parts) < 5:
      raise ValueError(f"Bad YOLO label at {src}:{line_no}: {raw!r}")
    old_cls = int(float(parts[0]))
    if old_cls not in class_map:
      raise ValueError(f"Class {old_cls} from {src}:{line_no} has no mapping")
    new_cls = class_map[old_cls]
    counts[new_cls] += 1
    converted.append(" ".join([str(new_cls), *parts[1:5]]))

  dst.write_text("\n".join(converted) + ("\n" if converted else ""), encoding="utf-8")
  return counts


def link_or_copy(src: Path, dst: Path) -> None:
  if dst.exists():
    return
  try:
    os.link(src, dst)
  except OSError:
    shutil.copy2(src, dst)


def build_dataset(records: list[Record], out: Path, seed: int, val_ratio: float, test_ratio: float) -> dict:
  out.mkdir(parents=True, exist_ok=True)
  for split in ("train", "valid", "test"):
    (out / split / "images").mkdir(parents=True, exist_ok=True)
    (out / split / "labels").mkdir(parents=True, exist_ok=True)

  class_counts: dict[str, Counter[int]] = {split: Counter() for split in ("train", "valid", "test")}
  image_counts: Counter[str] = Counter()
  dataset_counts: Counter[str] = Counter()
  groups_by_split: defaultdict[str, set[str]] = defaultdict(set)

  for i, rec in enumerate(records):
    split = split_group(rec.group_key, seed, val_ratio, test_ratio)
    prefix = f"{rec.source_dataset}_{i:06d}_"
    image_name = f"{prefix}{rec.image.name}"
    label_name = f"{Path(image_name).stem}.txt"
    dst_image = out / split / "images" / image_name
    dst_label = out / split / "labels" / label_name

    link_or_copy(rec.image, dst_image)
    counts = convert_label(rec.label, dst_label, DATASET_MAPS[rec.class_map_name])
    class_counts[split].update(counts)
    image_counts[split] += 1
    dataset_counts[f"{split}:{rec.source_dataset}"] += 1
    groups_by_split[split].add(rec.group_key)

  data_yaml = out / "data.yaml"
  yaml_names = "\n".join(f"  {i}: {name}" for i, name in enumerate(NAMES))
  data_yaml.write_text(
    "\n".join(
      [
        f"path: {out.as_posix()}",
        "train: train/images",
        "val: valid/images",
        "test: test/images",
        "",
        f"nc: {len(NAMES)}",
        "names:",
        yaml_names,
        "",
      ]
    ),
    encoding="utf-8",
  )

  metadata = {
    "names": NAMES,
    "seed": seed,
    "val_ratio": val_ratio,
    "test_ratio": test_ratio,
    "images": dict(image_counts),
    "objects": {split: {NAMES[k]: v for k, v in sorted(counts.items())} for split, counts in class_counts.items()},
    "source_images": dict(dataset_counts),
    "groups": {split: len(groups) for split, groups in groups_by_split.items()},
  }
  (out / "metadata.json").write_text(json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8")
  return metadata


def parse_args() -> argparse.Namespace:
  parser = argparse.ArgumentParser()
  parser.add_argument("--kr6", type=Path, required=True, help="Traffic light.yolo26 root")
  parser.add_argument("--color4", type=Path, required=True, help="Traffic light.v10i.yolo26 root")
  parser.add_argument("--out", type=Path, required=True)
  parser.add_argument("--seed", type=int, default=9208)
  parser.add_argument("--val-ratio", type=float, default=0.10)
  parser.add_argument("--test-ratio", type=float, default=0.10)
  return parser.parse_args()


def main() -> None:
  args = parse_args()
  records = [
    *collect_records(args.kr6, "kr6", "kr6"),
    *collect_records(args.color4, "color4", "color4"),
  ]
  if not records:
    raise RuntimeError("No records found")
  metadata = build_dataset(records, args.out, args.seed, args.val_ratio, args.test_ratio)
  print(json.dumps(metadata, indent=2, ensure_ascii=False))


if __name__ == "__main__":
  main()
