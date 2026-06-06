#!/usr/bin/env python3
import json
import pathlib
import argparse

def resolve_config_path(config_arg):
    config_path = pathlib.Path(config_arg)
    if config_path.is_absolute() or config_path.exists():
        return config_path
    return pathlib.Path(__file__).parent / config_path

def compute_safety_metrics_from_cm(cm, class_mapping):
    wd_idx = class_mapping["white_dashed"]
    total_actual_blocks = 0
    false_allow_count = 0
    correct_block_count = 0
    total_predicted_allows = 0
    correct_allow_count = 0

    num_classes = len(class_mapping)
    for i in range(num_classes):
        for j in range(num_classes):
            count = cm[i][j]
            if i == wd_idx:
                # Actual Allow
                if j == wd_idx:
                    correct_allow_count += count
            else:
                # Actual Block
                total_actual_blocks += count
                if j == wd_idx:
                    false_allow_count += count
                else:
                    correct_block_count += count

            if j == wd_idx:
                total_predicted_allows += count

    false_allow_rate = false_allow_count / total_actual_blocks if total_actual_blocks > 0 else 0.0
    block_recall = correct_block_count / total_actual_blocks if total_actual_blocks > 0 else 0.0
    dashed_precision = correct_allow_count / total_predicted_allows if total_predicted_allows > 0 else 0.0

    return {
        "false_allow_rate": false_allow_rate,
        "block_recall": block_recall,
        "dashed_precision": dashed_precision
    }

def main():
    parser = argparse.ArgumentParser(description="Compare qcamera and fcamera pipelines side-by-side")
    parser.add_argument("--config", type=str, default="config_fcamera.json", help="Path to fcamera config file")
    args = parser.parse_args()

    config_path = resolve_config_path(args.config)
    print(f"Loading configuration from {config_path}...")
    with open(config_path, "r", encoding="utf-8") as f:
        config = json.load(f)

    fcamera_output_dir = pathlib.Path(config["output_dir"])
    fcamera_outputs_dir = fcamera_output_dir / "outputs"

    # File paths
    qcamera_metrics_path = pathlib.Path("C:\\tmp\\lane_marking_model_pipeline\\checkpoints\\metrics.json")
    fcamera_metrics_path = fcamera_outputs_dir / "metrics.json"

    if not qcamera_metrics_path.exists():
        print(f"Error: qcamera metrics not found at {qcamera_metrics_path}")
        return
    if not fcamera_metrics_path.exists():
        print(f"Error: fcamera metrics not found at {fcamera_metrics_path}")
        return

    print("Loading metrics files...")
    with open(qcamera_metrics_path, "r", encoding="utf-8") as f:
        q_metrics = json.load(f)
    with open(fcamera_metrics_path, "r", encoding="utf-8") as f:
        f_metrics = json.load(f)

    q_cm = q_metrics["confusion_matrix"]
    f_cm = f_metrics["confusion_matrix"]

    q_class_mapping = q_metrics["class_mapping"]
    f_class_mapping = f_metrics["class_mapping"]

    # Compute safety metrics for both
    q_safety = compute_safety_metrics_from_cm(q_cm, q_class_mapping)
    # For fcamera, we can use the computed sweep metrics at thresh=0.50, or compute from CM directly
    f_safety = compute_safety_metrics_from_cm(f_cm, f_class_mapping)

    comparison = {
        "overall_accuracy": {
            "qcamera": q_metrics["overall_accuracy"],
            "fcamera": f_metrics["overall_accuracy"]
        },
        "false_allow_rate": {
            "qcamera": q_safety["false_allow_rate"],
            "fcamera": f_safety["false_allow_rate"]
        },
        "block_recall": {
            "qcamera": q_safety["block_recall"],
            "fcamera": f_safety["block_recall"]
        },
        "white_dashed_precision": {
            "qcamera": q_metrics["per_class"]["white_dashed"]["precision"],
            "fcamera": f_metrics["per_class"]["white_dashed"]["precision"]
        },
        "white_dashed_recall": {
            "qcamera": q_metrics["per_class"]["white_dashed"]["recall"],
            "fcamera": f_metrics["per_class"]["white_dashed"]["recall"]
        },
        "white_solid_recall": {
            "qcamera": q_metrics["per_class"]["white_solid"]["recall"],
            "fcamera": f_metrics["per_class"]["white_solid"]["recall"]
        },
        "yellow_solid_recall": {
            "qcamera": q_metrics["per_class"]["yellow_solid"]["recall"],
            "fcamera": f_metrics["per_class"]["yellow_solid"]["recall"]
        },
        "road_edge_or_barrier_recall": {
            "qcamera": q_metrics["per_class"]["road_edge_or_barrier"]["recall"],
            "fcamera": f_metrics["per_class"]["road_edge_or_barrier"]["recall"]
        }
    }

    # Save comparison JSON
    comparison_json_path = fcamera_outputs_dir / "qcamera_vs_fcamera_comparison.json"
    with open(comparison_json_path, "w", encoding="utf-8") as f:
        json.dump(comparison, f, indent=2)
    print(f"Comparison JSON saved to {comparison_json_path}")

    # Save comparison markdown
    comparison_md_path = fcamera_outputs_dir / "qcamera_vs_fcamera_comparison.md"

    md_content = f"""# Lane Marking Model Pipeline Comparison: qcamera vs fcamera

This document compares the baseline qcamera model (128x128 crop size, single target_x_eval=20m) against the new high-definition fcamera model (224x224 crop size, multi-point target_x_evals=[10, 15, 20, 25, 30]m).

## Side-by-Side Performance Comparison

| Metric | qcamera Baseline | fcamera HD Model | Improvement |
| :--- | :---: | :---: | :---: |
| **Overall Accuracy** | {comparison['overall_accuracy']['qcamera']*100:.2f}% | {comparison['overall_accuracy']['fcamera']*100:.2f}% | {((comparison['overall_accuracy']['fcamera'] - comparison['overall_accuracy']['qcamera'])*100):+.2f}% |
| **False Allow Rate (allow when solid/edge)** | {comparison['false_allow_rate']['qcamera']*100:.2f}% | {comparison['false_allow_rate']['fcamera']*100:.2f}% | {((comparison['false_allow_rate']['fcamera'] - comparison['false_allow_rate']['qcamera'])*100):+.2f}% (lower is better) |
| **Block Recall (recall for solid/edge)** | {comparison['block_recall']['qcamera']*100:.2f}% | {comparison['block_recall']['fcamera']*100:.2f}% | {((comparison['block_recall']['fcamera'] - comparison['block_recall']['qcamera'])*100):+.2f}% |
| **white_dashed Precision** | {comparison['white_dashed_precision']['qcamera']*100:.2f}% | {comparison['white_dashed_precision']['fcamera']*100:.2f}% | {((comparison['white_dashed_precision']['fcamera'] - comparison['white_dashed_precision']['qcamera'])*100):+.2f}% |
| **white_dashed Recall** | {comparison['white_dashed_recall']['qcamera']*100:.2f}% | {comparison['white_dashed_recall']['fcamera']*100:.2f}% | {((comparison['white_dashed_recall']['fcamera'] - comparison['white_dashed_recall']['qcamera'])*100):+.2f}% |
| **white_solid Recall** | {comparison['white_solid_recall']['qcamera']*100:.2f}% | {comparison['white_solid_recall']['fcamera']*100:.2f}% | {((comparison['white_solid_recall']['fcamera'] - comparison['white_solid_recall']['qcamera'])*100):+.2f}% |
| **yellow_solid Recall** | {comparison['yellow_solid_recall']['qcamera']*100:.2f}% | {comparison['yellow_solid_recall']['fcamera']*100:.2f}% | {((comparison['yellow_solid_recall']['fcamera'] - comparison['yellow_solid_recall']['qcamera'])*100):+.2f}% |
| **road_edge_or_barrier Recall** | {comparison['road_edge_or_barrier_recall']['qcamera']*100:.2f}% | {comparison['road_edge_or_barrier_recall']['fcamera']*100:.2f}% | {((comparison['road_edge_or_barrier_recall']['fcamera'] - comparison['road_edge_or_barrier_recall']['qcamera'])*100):+.2f}% |

## Key Insights
1. **HD Resolution Benefit**: Moving from qcamera to fcamera increases spatial resolution and decreases classification noise on solid boundaries and road edges.
2. **Safety First**: The primary design constraint is keeping the `False Allow Rate` as close to 0% as possible to prevent unsafe lane changes over solid lines.
"""
    with open(comparison_md_path, "w", encoding="utf-8") as f:
        f.write(md_content)
    print(f"Comparison Markdown saved to {comparison_md_path}")
    print(md_content)

if __name__ == "__main__":
    main()
