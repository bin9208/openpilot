#!/usr/bin/env python3
import argparse
import json
import pathlib
import sys
import io
import cv2
import numpy as np
import torch
from torchvision import transforms
from PIL import Image
import capnp
import zstandard as zstd
import pandas as pd

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

from train import get_model
from tools.lane_marking_labeler.auto_label_qcamera import prepare_capnp_schema
from openpilot.common.transformations.camera import DEVICE_CAMERAS, view_frame_from_device_frame
import openpilot.common.transformations.orientation as orient
from extract_crops import crop_and_pad, load_log_events, map_frame_idx

def resolve_config_path(config_arg):
    config_path = pathlib.Path(config_arg)
    if config_path.is_absolute() or config_path.exists():
        return config_path
    return pathlib.Path(__file__).parent / config_path

def resolve_repo_path(path_arg):
    path = pathlib.Path(path_arg)
    if path.is_absolute():
        return path
    return REPO_ROOT / path

def get_decision_color(pred_class, conf, conf_threshold):
    if conf < conf_threshold:
        return "uncertain_or_block", (128, 128, 128) # Gray
    if pred_class == "white_dashed":
        return "allow_candidate", (0, 255, 0) # Green
    else:
        return "block", (0, 0, 255) # Red

def main():
    parser = argparse.ArgumentParser(description="Lane Marking Model Visualizer (Supports qcamera & fcamera)")
    parser.add_argument("--config", type=str, default="config.json", help="Path to config file")
    parser.add_argument("--segment", type=str, required=True, help="Segment folder name (e.g. 00000b04--22e5755d4b--9)")
    parser.add_argument("--output-video", type=str, help="Path to save the output video file (.mp4)")
    parser.add_argument("--output-images-dir", type=str, help="Directory to save sample overlay frames")
    parser.add_argument("--conf-threshold", type=float, default=0.60, help="Confidence threshold to allow change")
    parser.add_argument("--limit-frames", type=int, default=0, help="Limit frames to process for fast testing")
    parser.add_argument("--camera", type=str, help="Override camera type (qcamera or fcamera)")
    args = parser.parse_args()

    # Load configuration
    config_path = resolve_config_path(args.config)
    print(f"Loading configuration from {config_path}...")
    with open(config_path, "r", encoding="utf-8") as f:
        config = json.load(f)

    # Determine paths and configs
    output_dir = pathlib.Path(config["output_dir"])
    outputs_dir = output_dir / "outputs"

    crop_size = config["crop_size"]
    camera = args.camera if args.camera else config.get("camera", "qcamera")

    if "target_x_evals" in config:
        target_x_evals = config["target_x_evals"]
    else:
        target_x_evals = [config["target_x_eval"]]

    print(f"Visualizing camera: {camera}")
    print(f"Target x_evals: {target_x_evals}")

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    runtime_model_path = resolve_repo_path(config["runtime_model_path"]) if config.get("runtime_model_path") else None

    if runtime_model_path and runtime_model_path.exists():
        print(f"Loading TorchScript runtime model from {runtime_model_path}...")
        model = torch.jit.load(str(runtime_model_path), map_location=device)
        model = model.to(device)
        model.eval()
        runtime_classes = config.get("runtime_classes") or sorted(config["classes"])
        idx_to_class = {idx: name for idx, name in enumerate(runtime_classes)}
    else:
        checkpoint_path = outputs_dir / "best_model.pth"
        if not checkpoint_path.exists():
            checkpoints_dir = pathlib.Path(config["checkpoints_dir"])
            checkpoint_path = checkpoints_dir / "best_model.pth"

        if not checkpoint_path.exists():
            print("Error: Model checkpoint not found and runtime model is unavailable. Please run train.py/export_model.py first!")
            return

        checkpoint = torch.load(checkpoint_path, map_location=device)

        idx_to_class = {int(k): v for k, v in checkpoint["idx_to_class"].items()}
        class_to_idx = checkpoint["class_to_idx"]
        model_arch = checkpoint["config"]["model_arch"]
        num_classes = len(class_to_idx)

        # Initialize model
        model = get_model(model_arch, num_classes)
        model.load_state_dict(checkpoint["model_state_dict"])
        model = model.to(device)
        model.eval()

    val_transform = transforms.Compose([
        transforms.ToTensor(),
        transforms.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225])
    ])

    # Try both data directory roots to locate files
    data_roots = [pathlib.Path(r"E:\comma_backup\2026-06-03"), pathlib.Path(r"E:\media\0\realdata")]
    video_path = None
    log_path = None

    for root in data_roots:
        cand_video = root / args.segment / ("fcamera.hevc" if camera == "fcamera" else "qcamera.ts")
        cand_log = root / args.segment / "rlog.zst"
        if not cand_log.exists():
            cand_log = root / args.segment / "qlog.zst"

        if cand_video.exists() and cand_log.exists():
            video_path = cand_video
            log_path = cand_log
            break

    if video_path is None or log_path is None:
        print(f"Error: Data files not found for segment {args.segment}.")
        return

    # Initialize Cap'n Proto schema
    schema_root = prepare_capnp_schema(REPO_ROOT)
    capnp.remove_import_hook()
    log = capnp.load(str(schema_root / "log.capnp"))

    print(f"Loading log events from {log_path}...")
    model_events, calib_events, road_indices, q_indices = load_log_events(log_path, log)

    if not model_events or not calib_events:
        print(f"Error: Missing modelV2 or liveCalibration events in {log_path}.")
        return

    # Camera Configs
    fcam = DEVICE_CAMERAS[("tici", "unknown")].fcam
    intrinsic = fcam.intrinsics

    # Find total frames and map indices
    q_cnt = 0
    qcamera_path = video_path.parent / "qcamera.ts"
    if qcamera_path.exists():
        cap_q = cv2.VideoCapture(str(qcamera_path))
        if cap_q.isOpened():
            q_cnt = max(0, int(cap_q.get(cv2.CAP_PROP_FRAME_COUNT) or 0))
            cap_q.release()

    f_cnt = len(road_indices)
    if f_cnt == 0:
        f_cnt = q_cnt if q_cnt > 0 else 1200
    if q_cnt == 0:
        q_cnt = len(q_indices) if len(q_indices) > 0 else 1200

    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        print(f"Error: Failed to open video {video_path}")
        return

    fps = cap.get(cv2.CAP_PROP_FPS) or 20.0
    frame_width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 1928)
    frame_height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 1208)
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    if total_frames <= 0:
        total_frames = len(road_indices) if camera == "fcamera" and road_indices else max(q_cnt, 1200)

    print(f"Video details: {frame_width}x{frame_height} @ {fps} fps. Total frames: {total_frames}")

    # Set up VideoWriter if output path specified
    video_writer = None
    if args.output_video:
        pathlib.Path(args.output_video).parent.mkdir(parents=True, exist_ok=True)
        fourcc = cv2.VideoWriter_fourcc(*"mp4v")
        video_writer = cv2.VideoWriter(args.output_video, fourcc, fps, (frame_width, frame_height))
        print(f"Writing output video to {args.output_video}...")

    # Set up Image output directory
    if args.output_images_dir:
        images_dir = pathlib.Path(args.output_images_dir)
        images_dir.mkdir(parents=True, exist_ok=True)
        print(f"Writing sample overlay frames to {images_dir}...")

    # Shadow Predictions List
    shadow_records = []

    # Sequential Read Loop
    current_frame_idx = -1
    ok = True
    frame = None

    limit = args.limit_frames if args.limit_frames > 0 else total_frames

    # We will process each frame_idx in order
    for frame_idx in range(limit):
        # We need to map frame_idx (which is the frame index in video) to logs
        # If camera is fcamera, video index corresponds to f_idx
        # If camera is qcamera, video index corresponds to q_idx

        # We map video index to f_idx and q_idx
        if camera == "fcamera":
            f_idx = frame_idx
            # Find closest q_idx matching f_idx timestamp
            if f_idx < len(road_indices) and len(q_indices) > 0:
                ts = road_indices[f_idx]["timestampSof"]
                q_idx = int(np.argmin([abs(x["timestampSof"] - ts) for x in q_indices]))
            else:
                q_idx = int(round(f_idx * (q_cnt - 1) / max(1, f_cnt - 1)))
        else:
            q_idx = frame_idx
            f_idx, diff_ms = map_frame_idx(q_idx, q_indices, road_indices, q_cnt, f_cnt)

        target_idx = f_idx if camera == "fcamera" else q_idx

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
            print(f"Reached EOF or read failure at frame {target_idx}")
            break

        # Align model and calibration events
        eff_cnt = f_cnt if camera == "fcamera" else q_cnt
        model_idx = int(round((target_idx / max(1, eff_cnt - 1)) * (len(model_events) - 1)))
        model_idx = max(0, min(len(model_events) - 1, model_idx))

        calib_idx = int(round((target_idx / max(1, eff_cnt - 1)) * (len(calib_events) - 1)))
        calib_idx = max(0, min(len(calib_events) - 1, calib_idx))

        model_ev = model_events[model_idx]
        calib_ev = calib_events[calib_idx]

        rpy = list(calib_ev.rpyCalib)
        device_from_calib = orient.rot_from_euler(rpy)
        view_from_calib = view_frame_from_device_frame.dot(device_from_calib)

        side_predictions = {"left": {}, "right": {}}
        side_crop_centers = {"left": {}, "right": {}}
        side_crop_patches = {"left": {}, "right": {}}

        # We run inference for both sides and all target_x_evals
        for side in ["left", "right"]:
            side_idx = 1 if side == "left" else 2
            lane_line = model_ev.laneLines[side_idx]

            for x_eval in target_x_evals:
                lane_xs = list(lane_line.x)
                lane_ys = list(lane_line.y)
                lane_zs = list(lane_line.z)

                y_eval = np.interp(x_eval, lane_xs, lane_ys)
                z_eval = np.interp(x_eval, lane_xs, lane_zs)

                pt_car = np.array([x_eval, y_eval, z_eval])
                pt_view = view_from_calib.dot(pt_car)

                pred_class, conf = "unknown", 0.0
                crop_patch = np.zeros((crop_size, crop_size, 3), dtype=np.uint8)
                u_proj, v_proj = 0, 0

                if pt_view[2] > 0.1:
                    u_norm = pt_view[0] / pt_view[2]
                    v_norm = pt_view[1] / pt_view[2]
                    u_full = u_norm * intrinsic[0, 0] + intrinsic[0, 2]
                    v_full = v_norm * intrinsic[1, 1] + intrinsic[1, 2]

                    if camera == "fcamera":
                        u_proj = u_full
                        v_proj = v_full
                    else:
                        aspect = frame_width / frame_height
                        if aspect > 1.7:
                            scale_x = frame_width / 1928.0
                            scale_y = frame_height / 1084.0
                            u_proj = u_full * scale_x
                            v_proj = (v_full - 62.0) * scale_y
                        else:
                            scale_x = frame_width / 1928.0
                            scale_y = frame_height / 1208.0
                            u_proj = u_full * scale_x
                            v_proj = v_full * scale_y

                    u_start = int(round(u_proj - crop_size / 2))
                    v_start = int(round(v_proj - crop_size / 2))

                    crop_patch = crop_and_pad(frame, u_start, v_start, crop_size)

                    # PyTorch Inference
                    pil_img = Image.fromarray(cv2.cvtColor(crop_patch, cv2.COLOR_BGR2RGB))
                    tensor = val_transform(pil_img).unsqueeze(0).to(device)
                    with torch.no_grad():
                        outputs = model(tensor)
                        probs = torch.softmax(outputs, dim=1)[0]
                        confidence, predicted_idx = torch.max(probs, 0)

                    pred_class = idx_to_class[predicted_idx.item()]
                    conf = confidence.item()

                side_predictions[side][x_eval] = (pred_class, conf)
                side_crop_centers[side][x_eval] = (int(round(u_proj)), int(round(v_proj)))
                side_crop_patches[side][x_eval] = crop_patch

        # ---------------------------------------------
        # Aggregate decisions for both sides
        # ---------------------------------------------
        side_decisions = {}
        for side in ["left", "right"]:
            # Rule: allow if ALL x_evals are white_dashed and >= threshold
            all_allow = True
            min_conf = 1.0
            worst_class = "white_dashed"

            for x_eval in target_x_evals:
                p_cls, p_conf = side_predictions[side][x_eval]
                if p_cls != "white_dashed" or p_conf < args.conf_threshold:
                    all_allow = False
                    if p_cls != "white_dashed":
                        worst_class = p_cls
                    min_conf = min(min_conf, p_conf)
                else:
                    min_conf = min(min_conf, p_conf)

            if all_allow:
                side_decisions[side] = ("allow_candidate", (0, 255, 0), "white_dashed", min_conf)
            elif worst_class == "white_dashed":
                # Low confidence
                side_decisions[side] = ("uncertain_or_block", (128, 128, 128), "white_dashed", min_conf)
            else:
                # Solid line or road edge
                side_decisions[side] = ("block", (0, 0, 255), worst_class, min_conf)

        # Save Shadow Prediction Record
        l_dec, _, l_lbl, l_cnf = side_decisions["left"]
        r_dec, _, r_lbl, r_cnf = side_decisions["right"]
        agg_decision = "allow_candidate" if (l_dec == "allow_candidate" and r_dec == "allow_candidate") else "block"
        if l_dec == "uncertain_or_block" or r_dec == "uncertain_or_block":
            if agg_decision != "block":
                agg_decision = "uncertain_or_block"

        shadow_records.append({
            "segment_id": args.segment,
            "frame_idx": target_idx,
            "left_label": l_lbl,
            "left_confidence": float(l_cnf),
            "left_decision": l_dec,
            "right_label": r_lbl,
            "right_confidence": float(r_cnf),
            "right_decision": r_dec,
            "aggregate_decision": agg_decision
        })

        # ---------------------------------------------
        # Draw Visual Overlay
        # ---------------------------------------------
        overlay = frame.copy()

        # 1. Draw full projected lane lines
        for side, side_idx, path_color in [("left", 1, (0, 255, 0)), ("right", 2, (255, 0, 0))]:
            lane_line = model_ev.laneLines[side_idx]
            pts_car = np.column_stack((lane_line.x, lane_line.y, lane_line.z))
            pts_view = pts_car.dot(view_from_calib.T)
            valid = pts_view[:, 2] > 0.1
            if np.any(valid):
                pts_view = pts_view[valid]
                pts_img_norm = pts_view[:, :2] / pts_view[:, 2:3]
                u_f = pts_img_norm[:, 0] * intrinsic[0, 0] + intrinsic[0, 2]
                v_f = pts_img_norm[:, 1] * intrinsic[1, 1] + intrinsic[1, 2]

                if camera == "fcamera":
                    u_qc, v_qc = u_f, v_f
                else:
                    aspect = frame_width / frame_height
                    if aspect > 1.7:
                        u_qc = u_f * (frame_width / 1928.0)
                        v_qc = (v_f - 62.0) * (frame_height / 1084.0)
                    else:
                        u_qc = u_f * (frame_width / 1928.0)
                        v_qc = v_f * (frame_height / 1208.0)

                for i in range(len(u_qc) - 1):
                    p1 = (int(round(u_qc[i])), int(round(v_qc[i])))
                    p2 = (int(round(u_qc[i+1])), int(round(v_qc[i+1])))
                    if 0 <= p1[0] < frame_width and 0 <= p1[1] < frame_height and 0 <= p2[0] < frame_width and 0 <= p2[1] < frame_height:
                        cv2.line(overlay, p1, p2, path_color, 2)

        # 2. Draw Crop Bounding Boxes (colored by individual x_eval prediction)
        for side in ["left", "right"]:
            for x_eval in target_x_evals:
                p_cls, p_conf = side_predictions[side][x_eval]
                _, box_color = get_decision_color(p_cls, p_conf, args.conf_threshold)
                u_c, v_c = side_crop_centers[side][x_eval]
                if u_c != 0 or v_c != 0:
                    x1 = u_c - crop_size // 2
                    y1 = v_c - crop_size // 2
                    cv2.rectangle(overlay, (x1, y1), (x1 + crop_size, y1 + crop_size), box_color, 2)
                    cv2.putText(overlay, f"{int(x_eval)}m", (x1 + 2, y1 - 4), cv2.FONT_HERSHEY_SIMPLEX, 0.4, box_color, 1, cv2.LINE_AA)

        # 3. Draw Representative PiPs (using 20m x_eval)
        rep_x = 20.0
        # Fallback to first if 20m is not in evals
        if rep_x not in target_x_evals:
            rep_x = target_x_evals[len(target_x_evals) // 2]

        left_pip = side_crop_patches["left"][rep_x]
        right_pip = side_crop_patches["right"][rep_x]

        # Left PiP: Top-Left corner
        overlay[10:10+crop_size, 10:10+crop_size] = left_pip
        cv2.rectangle(overlay, (9, 9), (10+crop_size, 10+crop_size), side_decisions["left"][1], 2)
        cv2.putText(overlay, f"LEFT ({int(rep_x)}m)", (12, 10+crop_size - 6), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (255, 255, 255), 1, cv2.LINE_AA)

        # Right PiP: Top-Right corner
        u_rt = frame_width - crop_size - 10
        overlay[10:10+crop_size, u_rt:u_rt+crop_size] = right_pip
        cv2.rectangle(overlay, (u_rt-1, 9), (u_rt+crop_size, 10+crop_size), side_decisions["right"][1], 2)
        cv2.putText(overlay, f"RIGHT ({int(rep_x)}m)", (u_rt+4, 10+crop_size - 6), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (255, 255, 255), 1, cv2.LINE_AA)

        # 4. Display Bottom HUD Overlay for Decisions
        cv2.rectangle(overlay, (0, frame_height - 120), (frame_width, frame_height), (0, 0, 0), -1)
        cv2.addWeighted(overlay, 0.7, frame, 0.3, 0, frame)

        # Left Detail String
        l_detail = " | ".join(f"{int(x)}m:{side_predictions['left'][x][0][:2].upper()}({side_predictions['left'][x][1]*100:.0f}%)" for x in target_x_evals)
        cv2.putText(frame, f"L: {l_dec.upper()} (worst: {l_lbl} {l_cnf*100:.0f}%)", (20, frame_height - 90), cv2.FONT_HERSHEY_SIMPLEX, 0.5, side_decisions["left"][1], 2, cv2.LINE_AA)
        cv2.putText(frame, l_detail, (20, frame_height - 70), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (200, 200, 200), 1, cv2.LINE_AA)

        # Right Detail String
        r_detail = " | ".join(f"{int(x)}m:{side_predictions['right'][x][0][:2].upper()}({side_predictions['right'][x][1]*100:.0f}%)" for x in target_x_evals)
        cv2.putText(frame, f"R: {r_dec.upper()} (worst: {r_lbl} {r_cnf*100:.0f}%)", (int(frame_width * 0.5) + 10, frame_height - 90), cv2.FONT_HERSHEY_SIMPLEX, 0.5, side_decisions["right"][1], 2, cv2.LINE_AA)
        cv2.putText(frame, r_detail, (int(frame_width * 0.5) + 10, frame_height - 70), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (200, 200, 200), 1, cv2.LINE_AA)

        # Overall decision status
        overall_col = (0, 255, 0) if (l_dec == "allow_candidate" and r_dec == "allow_candidate") else (0, 0, 255)
        overall_text = "ALLOW LANE CHANGE" if overall_col == (0, 255, 0) else "BLOCK LANE CHANGE"
        if l_dec == "uncertain_or_block" or r_dec == "uncertain_or_block":
            overall_text = "UNCERTAIN / BLOCK"
            overall_col = (128, 128, 128)

        cv2.putText(frame, f"STATUS: {overall_text}", (20, frame_height - 25), cv2.FONT_HERSHEY_SIMPLEX, 0.6, overall_col, 2, cv2.LINE_AA)
        cv2.putText(frame, f"FRAME: {frame_idx:05d} / {total_frames:05d}", (frame_width - 200, frame_height - 25), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1, cv2.LINE_AA)

        # Blend lane lines and boxes on top of HUD
        cv2.addWeighted(overlay, 0.5, frame, 0.5, 0, frame)

        # Draw representative PiPs on the final frame
        frame[10:10+crop_size, 10:10+crop_size] = left_pip
        cv2.rectangle(frame, (9, 9), (10+crop_size, 10+crop_size), side_decisions["left"][1], 2)
        cv2.putText(frame, f"LEFT ({int(rep_x)}m)", (12, 10+crop_size - 6), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (255, 255, 255), 1, cv2.LINE_AA)

        frame[10:10+crop_size, u_rt:u_rt+crop_size] = right_pip
        cv2.rectangle(frame, (u_rt-1, 9), (u_rt+crop_size, 10+crop_size), side_decisions["right"][1], 2)
        cv2.putText(frame, f"RIGHT ({int(rep_x)}m)", (u_rt+4, 10+crop_size - 6), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (255, 255, 255), 1, cv2.LINE_AA)

        # Draw decision boxes on top of final frame
        for side in ["left", "right"]:
            for x_eval in target_x_evals:
                p_cls, p_conf = side_predictions[side][x_eval]
                _, box_color = get_decision_color(p_cls, p_conf, args.conf_threshold)
                u_c, v_c = side_crop_centers[side][x_eval]
                if u_c != 0 or v_c != 0:
                    x1 = u_c - crop_size // 2
                    y1 = v_c - crop_size // 2
                    cv2.rectangle(frame, (x1, y1), (x1 + crop_size, y1 + crop_size), box_color, 2)
                    cv2.drawMarker(frame, (u_c, v_c), box_color, cv2.MARKER_CROSS, 8, 1)

        # Write frame to video file
        if video_writer:
            video_writer.write(frame)

        # Save sample overlay frames (e.g. every 40 frames)
        if args.output_images_dir and (frame_idx % 40 == 0):
            cv2.imwrite(str(images_dir / f"frame_{frame_idx:05d}_overlay.jpg"), frame)

        if (frame_idx + 1) % 100 == 0:
            print(f"Processed {frame_idx + 1} frames...")

    cap.release()
    if video_writer:
        video_writer.release()

    # Save shadow predictions to files
    shadow_csv_path = outputs_dir / "shadow_predictions.csv"
    shadow_json_path = outputs_dir / "shadow_predictions.json"

    shadow_df = pd.DataFrame(shadow_records)
    shadow_df.to_csv(shadow_csv_path, index=False)
    print(f"Shadow predictions saved to {shadow_csv_path}")

    with open(shadow_json_path, "w", encoding="utf-8") as f:
        json.dump(shadow_records, f, indent=2)
    print(f"Shadow predictions saved to {shadow_json_path}")

    print(f"\nFinished processing. Visualizations generated for {len(shadow_records)} frames.")

if __name__ == "__main__":
    main()
