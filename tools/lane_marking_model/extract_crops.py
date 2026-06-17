#!/usr/bin/env python3
import json
import pathlib
import sys
import io
import argparse
import cv2
import numpy as np
import pandas as pd
import capnp
import zstandard as zstd
from concurrent.futures import ProcessPoolExecutor
import tqdm

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

class ModuleAliasFinder:
    def find_spec(self, fullname, path, target=None):
        if fullname.startswith('openpilot.'):
            real_name = fullname[len('openpilot.'):]
            try:
                __import__(real_name)
                sys.modules[fullname] = sys.modules[real_name]
                return sys.modules[real_name].__spec__
            except ImportError:
                pass
        return None

sys.meta_path.insert(0, ModuleAliasFinder())

from tools.lane_marking_labeler.auto_label_qcamera import prepare_capnp_schema
from openpilot.common.transformations.camera import DEVICE_CAMERAS, view_frame_from_device_frame
import openpilot.common.transformations.orientation as orient

def resolve_config_path(config_arg):
    config_path = pathlib.Path(config_arg)
    if config_path.is_absolute() or config_path.exists():
        return config_path
    return pathlib.Path(__file__).parent / config_path

def crop_and_pad(img, u_start, v_start, crop_size):
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

    if patch.shape[0] < crop_size or patch.shape[1] < crop_size:
        pad_top = max(0, -v_start)
        pad_bottom = max(0, v_end - h)
        pad_left = max(0, -u_start)
        pad_right = max(0, u_end - w)
        patch = cv2.copyMakeBorder(patch, pad_top, pad_bottom, pad_left, pad_right, cv2.BORDER_CONSTANT, value=[0, 0, 0])

    return patch

def load_log_events(log_path, log):
    raw = log_path.read_bytes()
    with zstd.ZstdDecompressor().stream_reader(io.BytesIO(raw)) as reader:
        dat = reader.read()

    events = log.Event.read_multiple_bytes(dat)

    model_events = []
    calib_events = []
    road_indices = []
    q_indices = []

    try:
        for ev in events:
            try:
                which = ev.which()
            except Exception:
                continue
            if which == "modelV2":
                if len(ev.modelV2.laneLines) >= 4:
                    model_events.append(ev.modelV2)
            elif which == "liveCalibration":
                calib_events.append(ev.liveCalibration)
            elif which == "roadEncodeIdx":
                road_indices.append(ev.to_dict()["roadEncodeIdx"])
            elif which == "qRoadEncodeIdx":
                q_indices.append(ev.to_dict()["qRoadEncodeIdx"])
    except Exception:
        pass

    return model_events, calib_events, road_indices, q_indices

def map_frame_idx(qcamera_frame_idx, q_indices, road_indices, q_cnt, f_cnt):
    # Method 1: Timestamp-based mapping
    if len(q_indices) > 0 and len(road_indices) > 0:
        if qcamera_frame_idx < len(q_indices):
            ts = q_indices[qcamera_frame_idx]["timestampSof"]
            closest_idx = -1
            min_diff = float("inf")
            for i, r in enumerate(road_indices):
                diff = abs(r["timestampSof"] - ts)
                if diff < min_diff:
                    min_diff = diff
                    closest_idx = i
            return closest_idx, min_diff / 1e6

    # Method 2: Ratio-based fallback
    ratio_idx = int(round(qcamera_frame_idx * (f_cnt - 1) / max(1, q_cnt - 1)))
    return ratio_idx, -1.0

def find_video_and_log(segment, seg_df, camera, data_roots):
    source_video = ""
    source_camera = ""
    if "source_video" in seg_df.columns:
        values = [str(v) for v in seg_df["source_video"].dropna().unique() if str(v)]
        source_video = values[0] if values else ""
    if "source_camera" in seg_df.columns:
        values = [str(v) for v in seg_df["source_camera"].dropna().unique() if str(v)]
        source_camera = values[0] if values else ""

    if source_video:
        video_path = pathlib.Path(source_video)
        if video_path.exists():
            log_path = video_path.with_name("rlog.zst")
            if not log_path.exists():
                log_path = video_path.with_name("qlog.zst")
            return video_path, log_path if log_path.exists() else None, source_camera or camera

    target_name = "fcamera.hevc" if camera == "fcamera" else "qcamera.ts"
    for root_str in data_roots:
        root = pathlib.Path(root_str)
        candidates = []
        direct = root / segment / target_name
        if direct.exists():
            candidates.append(direct)
        elif root.exists():
            candidates.extend(root.rglob(str(pathlib.Path(segment) / target_name)))
            if not candidates:
                candidates.extend(p for p in root.rglob(target_name) if p.parent.name == segment)
        for cand_video in candidates:
            cand_log = cand_video.with_name("rlog.zst")
            if not cand_log.exists():
                cand_log = cand_video.with_name("qlog.zst")
            if cand_video.exists() and cand_log.exists():
                return cand_video, cand_log, camera

    return None, None, source_camera or camera

def process_segment(args_tuple):
    segment, seg_df_dict, crops_dir_str, crop_size, camera, target_x_evals, projection_debug_dir_str, data_roots = args_tuple

    import pathlib
    import cv2
    import numpy as np
    import pandas as pd
    import capnp
    import zstandard as zstd
    import sys
    import io

    repo_root = pathlib.Path(__file__).resolve().parents[2]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))

    class ModuleAliasFinder:
        def find_spec(self, fullname, path, target=None):
            if fullname.startswith('openpilot.'):
                real_name = fullname[len('openpilot.'):]
                try:
                    __import__(real_name)
                    sys.modules[fullname] = sys.modules[real_name]
                    return sys.modules[real_name].__spec__
                except ImportError:
                    pass
            return None

    if not any(isinstance(finder, ModuleAliasFinder) for finder in sys.meta_path):
        sys.meta_path.insert(0, ModuleAliasFinder())

    from tools.lane_marking_labeler.auto_label_qcamera import prepare_capnp_schema
    from openpilot.common.transformations.camera import DEVICE_CAMERAS, view_frame_from_device_frame
    import openpilot.common.transformations.orientation as orient

    crops_dir = pathlib.Path(crops_dir_str)
    projection_debug_dir = pathlib.Path(projection_debug_dir_str)

    seg_df = pd.DataFrame(seg_df_dict)

    video_path, log_path, label_source_camera = find_video_and_log(segment, seg_df, camera, data_roots)

    if video_path is None or log_path is None:
        return [], []

    try:
        schema_root = prepare_capnp_schema(repo_root)
        capnp.remove_import_hook()
        log = capnp.load(str(schema_root / "log.capnp"))
        model_events, calib_events, road_indices, q_indices = load_log_events(log_path, log)
    except Exception as e:
        return [], []

    if not model_events or not calib_events:
        return [], []

    q_cnt = 0
    qcamera_path = video_path.parent / "qcamera.ts"
    if qcamera_path.exists():
        cap_q = cv2.VideoCapture(str(qcamera_path))
        if cap_q.isOpened():
            q_cnt = int(cap_q.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
            cap_q.release()

    f_cnt = len(road_indices)
    if f_cnt == 0:
        f_cnt = q_cnt if q_cnt > 0 else 1200
    if q_cnt == 0:
        q_cnt = len(q_indices) if len(q_indices) > 0 else 1200

    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        return [], []

    frame_width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 1928)
    frame_height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 1208)

    fcam = DEVICE_CAMERAS[("tici", "unknown")].fcam
    intrinsic = fcam.intrinsics

    mapped_rows = []
    local_mappings = []

    for _, row in seg_df.iterrows():
        source_camera = str(row.get("source_camera", "") or label_source_camera or "")
        source_idx = int(row["frame_idx"])
        if camera == "fcamera" and source_camera == "fcamera":
            f_idx = source_idx
            diff_ms = 0.0
        else:
            f_idx, diff_ms = map_frame_idx(source_idx, q_indices, road_indices, q_cnt, f_cnt)

        local_mappings.append({
            "segment": segment,
            "source_camera": source_camera,
            "source_frame_idx": source_idx,
            "qcamera_frame_idx": source_idx if source_camera != "fcamera" else -1,
            "fcamera_frame_idx": f_idx,
            "timestamp_diff_ms": diff_ms
        })

        row_dict = row.to_dict()
        row_dict["target_decode_frame_idx"] = f_idx if camera == "fcamera" else q_idx
        mapped_rows.append(row_dict)

    mapped_df = pd.DataFrame(mapped_rows).sort_values(by="target_decode_frame_idx")

    current_frame_idx = -1
    ok = True
    frame = None
    debug_frame_saved = False
    local_metadata = []

    for _, row in mapped_df.iterrows():
        target_idx = int(row["target_decode_frame_idx"])
        side = row["side"]
        label = row["corrected_label"]
        split = row["split"]

        # Read sequentially until target_idx
        if current_frame_idx < target_idx:
            while current_frame_idx < target_idx - 1:
                ok = cap.grab()
                current_frame_idx += 1
                if not ok:
                    break
            if current_frame_idx == target_idx - 1:
                ok, frame = cap.read()
                current_frame_idx += 1

        if not ok or frame is None:
            break

        # Align model and calibration events using target_idx
        eff_cnt = f_cnt if camera == "fcamera" else q_cnt
        model_idx = int(round((target_idx / max(1, eff_cnt - 1)) * (len(model_events) - 1)))
        model_idx = max(0, min(len(model_events) - 1, model_idx))

        calib_idx = int(round((target_idx / max(1, eff_cnt - 1)) * (len(calib_events) - 1)))
        calib_idx = max(0, min(len(calib_events) - 1, calib_idx))

        model_ev = model_events[model_idx]
        calib_ev = calib_events[calib_idx]

        # Get 3D coordinates for target side
        side_idx = 1 if side == "left" else 2
        lane_line = model_ev.laneLines[side_idx]

        for x_eval in target_x_evals:
            lane_xs = list(lane_line.x)
            lane_ys = list(lane_line.y)
            lane_zs = list(lane_line.z)

            y_eval = np.interp(x_eval, lane_xs, lane_ys)
            z_eval = np.interp(x_eval, lane_xs, lane_zs)

            pt_car = np.array([x_eval, y_eval, z_eval])

            # Project to image
            rpy = list(calib_ev.rpyCalib)
            device_from_calib = orient.rot_from_euler(rpy)
            view_from_calib = view_frame_from_device_frame.dot(device_from_calib)

            pt_view = view_from_calib.dot(pt_car)
            if pt_view[2] <= 0.1:
                continue

            u_norm = pt_view[0] / pt_view[2]
            v_norm = pt_view[1] / pt_view[2]

            u_full = u_norm * intrinsic[0, 0] + intrinsic[0, 2]
            v_full = v_norm * intrinsic[1, 1] + intrinsic[1, 2]

            # Scale coordinates if qcamera
            if camera == "fcamera":
                u_proj = u_full
                v_proj = v_full
            else:
                aspect = frame_width / frame_height
                if aspect > 1.7:  # 16:9 crop
                    scale_x = frame_width / 1928.0
                    scale_y = frame_height / 1084.0
                    u_proj = u_full * scale_x
                    v_proj = (v_full - 62.0) * scale_y
                else:  # 4:3 full frame scale
                    scale_x = frame_width / 1928.0
                    scale_y = frame_height / 1208.0
                    u_proj = u_full * scale_x
                    v_proj = v_full * scale_y

            # Center crop box
            u_start = int(round(u_proj - crop_size / 2))
            v_start = int(round(v_proj - crop_size / 2))

            crop = crop_and_pad(frame, u_start, v_start, crop_size)

            # Save crop
            target_dir = crops_dir / split / label
            target_dir.mkdir(parents=True, exist_ok=True)

            crop_name = f"{segment}_f{target_idx:05d}_{side}_x{int(x_eval)}.jpg"
            cv2.imwrite(str(target_dir / crop_name), crop)

            local_metadata.append({
                "crop_path": str(target_dir / crop_name),
                "segment": segment,
                "source_video": str(video_path),
                "source_camera": label_source_camera,
                "frame_idx": target_idx,
                "side": side,
                "x_eval": x_eval,
                "label": label,
                "split": split
            })

        # Create debug overlay once per segment (first row processed)
        if not debug_frame_saved:
            debug_frame_saved = True
            debug_img = frame.copy()

            # Draw left and right lane lines
            for line_idx, color in [(1, (0, 255, 0)), (2, (255, 0, 0))]:
                line = model_ev.laneLines[line_idx]
                pts = []
                l_xs = list(line.x)
                l_ys = list(line.y)
                l_zs = list(line.z)

                rpy = list(calib_ev.rpyCalib)
                device_from_calib = orient.rot_from_euler(rpy)
                view_from_calib = view_frame_from_device_frame.dot(device_from_calib)

                for x in np.arange(5.0, 60.0, 2.0):
                    y = np.interp(x, l_xs, l_ys)
                    z = np.interp(x, l_xs, l_zs)
                    pt = view_from_calib.dot(np.array([x, y, z]))
                    if pt[2] <= 0.1:
                        continue
                    uf = pt[0] / pt[2] * intrinsic[0, 0] + intrinsic[0, 2]
                    vf = pt[1] / pt[2] * intrinsic[1, 1] + intrinsic[1, 2]

                    if camera == "fcamera":
                        up, vp = uf, vf
                    else:
                        aspect = frame_width / frame_height
                        if aspect > 1.7:
                            up = uf * (frame_width / 1928.0)
                            vp = (vf - 62.0) * (frame_height / 1084.0)
                        else:
                            up = uf * (frame_width / 1928.0)
                            vp = vf * (frame_height / 1208.0)
                    pts.append((int(round(up)), int(round(vp))))

                if len(pts) > 1:
                    cv2.polylines(debug_img, [np.array(pts)], False, color, 3)

            # Draw crop boxes for current side and all x_evals
            for x_eval in target_x_evals:
                l_xs = list(lane_line.x)
                l_ys = list(lane_line.y)
                l_zs = list(lane_line.z)

                y_eval = np.interp(x_eval, l_xs, l_ys)
                z_eval = np.interp(x_eval, l_xs, l_zs)
                pt = view_from_calib.dot(np.array([x_eval, y_eval, z_eval]))
                if pt[2] <= 0.1:
                    continue
                uf = pt[0] / pt[2] * intrinsic[0, 0] + intrinsic[0, 2]
                vf = pt[1] / pt[2] * intrinsic[1, 1] + intrinsic[1, 2]
                if camera == "fcamera":
                    up, vp = uf, vf
                else:
                    aspect = frame_width / frame_height
                    if aspect > 1.7:
                        up = uf * (frame_width / 1928.0)
                        vp = (vf - 62.0) * (frame_height / 1084.0)
                    else:
                        up = uf * (frame_width / 1928.0)
                        vp = vf * (frame_height / 1208.0)

                x1 = int(round(up - crop_size / 2))
                y1 = int(round(vp - crop_size / 2))
                x2 = x1 + crop_size
                y2 = y1 + crop_size
                cv2.rectangle(debug_img, (x1, y1), (x2, y2), (0, 0, 255), 2)
                cv2.putText(debug_img, f"{int(x_eval)}m", (x1, y1 - 5), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 255), 1)

            cv2.putText(debug_img, f"Seg: {segment} F: {target_idx} Side: {side}", (50, 50), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (255, 255, 255), 2)

            debug_name = f"{segment}_f{target_idx:05d}_overlay.jpg"
            cv2.imwrite(str(projection_debug_dir / debug_name), debug_img)

    cap.release()
    return local_metadata, local_mappings

def main():
    parser = argparse.ArgumentParser(description="Extract crops from video frame sequences using coordinates projected from logs")
    parser.add_argument("--config", type=str, default="config.json", help="Path to config file")
    parser.add_argument("--workers", type=int, default=24, help="Number of parallel processes")
    args = parser.parse_args()

    config_path = resolve_config_path(args.config)
    print(f"Loading configuration from {config_path}...")
    with open(config_path, "r", encoding="utf-8") as f:
        config = json.load(f)

    split_csv = config["split_metadata_csv"]
    crops_dir = pathlib.Path(config["crops_dir"])
    crop_size = config["crop_size"]
    camera = config.get("camera", "qcamera")
    data_roots = config.get("data_roots", [r"E:\comma_backup\2026-06-03", r"E:\media\0\realdata"])

    # Determine target_x_evals
    if "target_x_evals" in config:
        target_x_evals = config["target_x_evals"]
    else:
        target_x_evals = [config["target_x_eval"]]

    projection_debug_dir = pathlib.Path(config.get("projection_debug_dir", "C:\\tmp\\lane_marking_model_pipeline_fcamera\\projection_debug"))
    projection_debug_dir.mkdir(parents=True, exist_ok=True)

    print(f"Running parallel crop extraction for camera: {camera}")
    print(f"Target x_evals: {target_x_evals}")
    print(f"Workers: {args.workers}")

    print(f"Loading split metadata from {split_csv}...")
    df = pd.read_csv(split_csv)

    grouped = df.groupby("segment")
    total_segments = len(grouped)
    print(f"Processing crops for {total_segments} segments...")

    tasks = []
    for segment, seg_df in grouped:
        tasks.append((
            segment,
            seg_df.to_dict(orient="list"),
            str(crops_dir),
            crop_size,
            camera,
            target_x_evals,
            str(projection_debug_dir),
            data_roots
        ))

    all_metadata = []
    all_mappings = []

    print("Launching parallel crop extraction...")
    with ProcessPoolExecutor(max_workers=args.workers) as executor:
        results = list(tqdm.tqdm(executor.map(process_segment, tasks), total=len(tasks)))

    for meta, maps in results:
        all_metadata.extend(meta)
        all_mappings.extend(maps)

    # Save frame mapping and crop metadata
    frame_mapping_df = pd.DataFrame(all_mappings)
    frame_mapping_csv = config["frame_mapping_csv"]
    frame_mapping_df.to_csv(frame_mapping_csv, index=False)
    print(f"Frame mapping saved to {frame_mapping_csv}")

    crop_metadata_df = pd.DataFrame(all_metadata)
    crop_metadata_csv = config["crop_metadata_csv"]
    crop_metadata_df.to_csv(crop_metadata_csv, index=False)
    print(f"Crop metadata saved to {crop_metadata_csv}")

    print(f"\nDone! Extracted {len(crop_metadata_df)} crop images to {crops_dir}")

if __name__ == "__main__":
    main()
