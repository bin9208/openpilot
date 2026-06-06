#!/usr/bin/env python3
import argparse
import json
import pathlib
import pandas as pd
import numpy as np
import cv2
import capnp
import zstandard as zstd
import io
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
from tools.lane_marking_labeler.auto_label_qcamera import prepare_capnp_schema

def resolve_config_path(config_arg):
    config_path = pathlib.Path(config_arg)
    if config_path.is_absolute() or config_path.exists():
        return config_path
    return pathlib.Path(__file__).parent / config_path

def check_video_properties(path):
    if not path or not path.exists() or path.stat().st_size == 0:
        return "None", 0.0
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        return "None", 0.0
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    fps = cap.get(cv2.CAP_PROP_FPS)
    cap.release()
    return f"{w}x{h}", fps

def get_log_frame_counts(log_path, log):
    try:
        raw = log_path.read_bytes()
        dctx = zstd.ZstdDecompressor()
        with dctx.stream_reader(io.BytesIO(raw)) as reader:
            dat = reader.read()
        events = log.Event.read_multiple_bytes(dat)
        f_cnt = 0
        q_cnt = 0
        for ev in events:
            try:
                which = ev.which()
            except Exception:
                continue
            if which == "roadEncodeIdx":
                f_cnt += 1
            elif which == "qRoadEncodeIdx":
                q_cnt += 1
        return f_cnt, q_cnt
    except Exception:
        return 0, 0

def main():
    parser = argparse.ArgumentParser(description="Prepare dataset and manifest for HD fcamera pipeline")
    parser.add_argument("--config", type=str, default="config.json", help="Path to config file")
    args = parser.parse_args()

    config_path = resolve_config_path(args.config)
    print(f"Loading configuration from {config_path}...")
    with open(config_path, "r", encoding="utf-8") as f:
        config = json.load(f)

    corrected_csv = config["corrected_labels_csv"]
    split_csv = config["split_metadata_csv"]
    output_dir = pathlib.Path(config["output_dir"])
    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"Reading labels from {corrected_csv}...")
    df = pd.read_csv(corrected_csv)

    valid_classes = set(config["classes"])
    df = df[df["corrected_label"].isin(valid_classes)].copy()

    unique_segments = sorted(df["segment"].unique())
    num_segments = len(unique_segments)
    print(f"Total unique segments in corrected labels: {num_segments}")

    # Initialize Cap'n Proto schema
    schema_root = prepare_capnp_schema(REPO_ROOT)
    capnp.remove_import_hook()
    log = capnp.load(str(schema_root / "log.capnp"))

    # 1. Manifest creation
    data_roots = [pathlib.Path(r"E:\comma_backup\2026-06-03"), pathlib.Path(r"E:\media\0\realdata")]
    manifest_rows = []
    fcamera_found_count = 0

    print("Generating fcamera/qcamera manifest (this may take a moment)...")
    for idx, seg in enumerate(unique_segments, start=1):
        qcamera_path = None
        fcamera_path = None
        log_path = None

        for root in data_roots:
            cand_q = root / seg / "qcamera.ts"
            cand_f = root / seg / "fcamera.hevc"
            cand_log = root / seg / "rlog.zst"
            if not cand_log.exists():
                cand_log = root / seg / "qlog.zst"

            if cand_q.exists():
                qcamera_path = cand_q
            if cand_f.exists():
                fcamera_path = cand_f
            if cand_log.exists():
                log_path = cand_log

        q_res, q_cnt, q_fps = "None", 0, 0.0
        f_res, f_cnt, f_fps = "None", 0, 0.0
        usable = False

        if qcamera_path:
            # For speed, we just read properties for qcamera.ts
            cap = cv2.VideoCapture(str(qcamera_path))
            if cap.isOpened():
                q_res = f"{int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))}x{int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))}"
                q_cnt = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
                q_fps = cap.get(cv2.CAP_PROP_FPS)
                cap.release()

        if fcamera_path:
            fcamera_found_count += 1
            f_res, f_fps = check_video_properties(fcamera_path)
            # Count frames from log if available, fallback to q_cnt
            if log_path:
                f_cnt, q_cnt_log = get_log_frame_counts(log_path, log)
            if f_cnt == 0:
                f_cnt = q_cnt

        if qcamera_path and fcamera_path and log_path and f_cnt > 0 and q_cnt > 0:
            usable = True

        manifest_rows.append({
            "segment_id": seg,
            "qcamera_path": str(qcamera_path) if qcamera_path else "",
            "fcamera_path": str(fcamera_path) if fcamera_path else "",
            "log_path": str(log_path) if log_path else "",
            "qcamera_resolution": q_res,
            "fcamera_resolution": f_res,
            "qcamera_frame_count": q_cnt,
            "fcamera_frame_count": f_cnt,
            "fps_estimate": f_fps if fcamera_path else q_fps,
            "usable": usable
        })
        if idx % 50 == 0 or idx == num_segments:
            print(f"  Processed {idx}/{num_segments} segments...")

    manifest_df = pd.DataFrame(manifest_rows)
    manifest_csv_path = output_dir / "manifest.csv"
    manifest_df.to_csv(manifest_csv_path, index=False)
    print(f"Manifest saved to {manifest_csv_path}")

    # 2. Manifest Summary
    success_rate = fcamera_found_count / num_segments if num_segments > 0 else 0.0
    summary = {
        "total_segments_in_labels": num_segments,
        "fcamera_found_count": fcamera_found_count,
        "matching_success_rate": success_rate,
        "usable_segments_count": int(manifest_df["usable"].sum()),
        "fcamera_resolutions_detected": list(manifest_df[manifest_df["fcamera_path"] != ""]["fcamera_resolution"].unique())
    }

    manifest_json_path = output_dir / "manifest_summary.json"
    with open(manifest_json_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)
    print(f"Summary saved to {manifest_json_path}")
    print(json.dumps(summary, indent=2))

    # 3. Train/Val/Test Split (segment-level, reproducible seed)
    np.random.seed(42)
    shuffled_segs = np.random.permutation(unique_segments)

    train_end = int(0.70 * num_segments)
    val_end = train_end + int(0.15 * num_segments)

    train_segs = set(shuffled_segs[:train_end])
    val_segs = set(shuffled_segs[train_end:val_end])
    test_segs = set(shuffled_segs[val_end:])

    def assign_split(seg):
        if seg in train_segs:
            return "train"
        elif seg in val_segs:
            return "val"
        else:
            return "test"

    df["split"] = df["segment"].apply(assign_split)

    # Filter dataset: only keep usable segments
    usable_segs_set = set(manifest_df[manifest_df["usable"] == True]["segment_id"])
    df = df[df["segment"].isin(usable_segs_set)].copy()
    print(f"Filtered to keep only usable segments. Remaining rows: {len(df)}")

    df.to_csv(split_csv, index=False)
    print(f"Split metadata saved to {split_csv}")

    # 4. Split Overlap Verification Check
    tr = set(df[df["split"] == "train"]["segment"])
    va = set(df[df["split"] == "val"]["segment"])
    te = set(df[df["split"] == "test"]["segment"])

    overlap_tr_va = len(tr.intersection(va))
    overlap_tr_te = len(tr.intersection(te))
    overlap_va_te = len(va.intersection(te))

    split_check = {
        "train_segments_count": len(tr),
        "val_segments_count": len(va),
        "test_segments_count": len(te),
        "overlap_train_val": overlap_tr_va,
        "overlap_train_test": overlap_tr_te,
        "overlap_val_test": overlap_va_te,
        "leakage_detected": (overlap_tr_va + overlap_tr_te + overlap_va_te) > 0
    }

    split_check_path = pathlib.Path(config["split_check_json"])
    with open(split_check_path, "w", encoding="utf-8") as f:
        json.dump(split_check, f, indent=2)
    print(f"Split leakage check saved to {split_check_path}")
    print(json.dumps(split_check, indent=2))

if __name__ == "__main__":
    main()
