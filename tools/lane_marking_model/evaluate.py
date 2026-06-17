#!/usr/bin/env python3
import json
import pathlib
import shutil
import argparse
import torch
import torch.nn as nn
from torch.utils.data import DataLoader
from torchvision import datasets, transforms
import numpy as np
import matplotlib.pyplot as plt
from train import get_model, make_image_folder

def resolve_config_path(config_arg):
    config_path = pathlib.Path(config_arg)
    if config_path.is_absolute() or config_path.exists():
        return config_path
    return pathlib.Path(__file__).parent / config_path

def main():
    parser = argparse.ArgumentParser(description="Evaluate lane marking classification model")
    parser.add_argument("--config", type=str, default="config.json", help="Path to config file")
    args = parser.parse_args()

    config_path = resolve_config_path(args.config)
    print(f"Loading configuration from {config_path}...")
    with open(config_path, "r", encoding="utf-8") as f:
        config = json.load(f)

    crops_dir = pathlib.Path(config["crops_dir"])
    output_dir = pathlib.Path(config["output_dir"])
    outputs_dir = output_dir / "outputs"
    outputs_dir.mkdir(parents=True, exist_ok=True)

    misclassified_dir = outputs_dir / "misclassified"
    false_allow_dir = outputs_dir / "false_allow_cases"

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print(f"Using device: {device}")

    # Find the checkpoint in checkpoints_dir (config["checkpoints_dir"]) or outputs_dir
    # Try outputs_dir first as per the new train.py logic
    checkpoint_path = outputs_dir / "best_model.pth"
    if not checkpoint_path.exists():
        # Fallback to old path
        checkpoints_dir = pathlib.Path(config["checkpoints_dir"])
        checkpoint_path = checkpoints_dir / "best_model.pth"

    if not checkpoint_path.exists():
        print(f"Error: Model checkpoint not found. Please run train.py first!")
        return

    print(f"Loading checkpoint from {checkpoint_path}...")
    checkpoint = torch.load(checkpoint_path, map_location=device)

    # Load parameters from checkpoint
    idx_to_class = checkpoint["idx_to_class"]
    # Convert string keys back to integer indices
    idx_to_class = {int(k): v for k, v in idx_to_class.items()}
    class_to_idx = checkpoint["class_to_idx"]
    model_arch = checkpoint["config"]["model_arch"]
    num_classes = len(class_to_idx)

    # Initialize model and load weights
    model = get_model(model_arch, num_classes, pretrained=False)
    model.load_state_dict(checkpoint["model_state_dict"])
    model = model.to(device)
    model.eval()

    # Set up transforms
    val_transform = transforms.Compose([
        transforms.ToTensor(),
        transforms.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225])
    ])

    test_dir = crops_dir / "test"
    if not test_dir.exists():
        print(f"Error: Test folder not found at {test_dir}. Please run extract_crops.py first!")
        return

    class_names = [idx_to_class[i] for i in range(num_classes)]
    test_dataset = make_image_folder(test_dir, class_names, transform=val_transform)
    test_loader = DataLoader(test_dataset, batch_size=config["batch_size"], shuffle=False, num_workers=0)

    # Class names in order of indices
    print(f"Evaluation classes: {class_names}")

    # Accumulate predictions and probabilities
    all_preds = []
    all_targets = []
    all_probs = []

    with torch.no_grad():
        for inputs, labels in test_loader:
            inputs = inputs.to(device)
            outputs = model(inputs)
            probs = torch.softmax(outputs, dim=1)
            _, predicted = torch.max(outputs, 1)

            all_preds.extend(predicted.cpu().numpy())
            all_targets.extend(labels.numpy())
            all_probs.extend(probs.cpu().numpy())

    all_preds = np.array(all_preds)
    all_targets = np.array(all_targets)
    all_probs = np.array(all_probs)

    predicted_confidences = np.max(all_probs, axis=1)

    # Compute standard overall accuracy
    overall_accuracy = float(np.mean(all_preds == all_targets))

    # Confusion Matrix
    cm = np.zeros((num_classes, num_classes), dtype=int)
    for t, p in zip(all_targets, all_preds, strict=False):
        cm[t, p] += 1

    per_class_metrics = {}
    for i, cls in enumerate(class_names):
        true_pos = cm[i, i]
        actual_pos = np.sum(cm[i, :])
        pred_pos = np.sum(cm[:, i])

        accuracy = float(true_pos / actual_pos) if actual_pos > 0 else 0.0
        precision = float(true_pos / pred_pos) if pred_pos > 0 else 0.0
        recall = float(true_pos / actual_pos) if actual_pos > 0 else 0.0
        f1 = float(2 * precision * recall / (precision + recall)) if (precision + recall) > 0 else 0.0

        per_class_metrics[cls] = {
            "samples": int(actual_pos),
            "accuracy": accuracy,
            "precision": precision,
            "recall": recall,
            "f1": f1
        }

    # Safety Metrics & Sweeps
    white_dashed_idx = class_to_idx["white_dashed"]
    thresholds = [0.50, 0.60, 0.70, 0.80, 0.90]
    sweep_results = {}

    for thresh in thresholds:
        # If predicted == white_dashed and prob >= thresh, allow candidate
        decisions = []
        for pred, prob_array in zip(all_preds, all_probs):
            if prob_array[white_dashed_idx] >= thresh:
                decisions.append("allow")
            else:
                decisions.append("block")
        decisions = np.array(decisions)

        true_decisions = np.array(["allow" if t == white_dashed_idx else "block" for t in all_targets])

        # false_allow: true label is block (solid/yellow/road_edge) but predicted allow
        actual_block_mask = (true_decisions == "block")
        total_actual_blocks = np.sum(actual_block_mask)
        false_allow_count = np.sum((actual_block_mask) & (decisions == "allow"))
        false_allow_rate = float(false_allow_count / total_actual_blocks) if total_actual_blocks > 0 else 0.0

        # block_recall: actual label is block, and decision is block
        correct_block_count = np.sum((actual_block_mask) & (decisions == "block"))
        block_recall = float(correct_block_count / total_actual_blocks) if total_actual_blocks > 0 else 0.0

        # dashed_precision (precision of allow candidates)
        predicted_allow_mask = (decisions == "allow")
        total_predicted_allows = np.sum(predicted_allow_mask)
        correct_allow_count = np.sum((predicted_allow_mask) & (true_decisions == "allow"))
        dashed_precision = float(correct_allow_count / total_predicted_allows) if total_predicted_allows > 0 else 0.0

        sweep_results[f"thresh_{thresh:.2f}"] = {
            "false_allow_rate": false_allow_rate,
            "block_recall": block_recall,
            "dashed_precision": dashed_precision,
            "allow_candidate_count": int(total_predicted_allows),
            "false_allow_count": int(false_allow_count)
        }

    # Print results
    print("\n================ EVALUATION METRICS ================")
    print(f"Overall Test Accuracy: {overall_accuracy * 100:.2f}%")
    print("\nPer-Class Metrics:")
    for cls, metrics in per_class_metrics.items():
        print(f"  Class: {cls.upper()}")
        print(f"    Samples:   {metrics['samples']}")
        print(f"    Precision: {metrics['precision'] * 100:.2f}%")
        print(f"    Recall:    {metrics['recall'] * 100:.2f}%")
        print(f"    F1-Score:  {metrics['f1'] * 100:.2f}%")

    print("\nSafety metrics at sweeps:")
    for key, val in sweep_results.items():
        print(f"  {key.upper()}:")
        print(f"    allow_candidate_count: {val['allow_candidate_count']}")
        print(f"    false_allow_count:     {val['false_allow_count']}")
        print(f"    false_allow_rate:      {val['false_allow_rate']*100:.2f}%")
        print(f"    block_recall:          {val['block_recall']*100:.2f}%")
        print(f"    dashed_precision:      {val['dashed_precision']*100:.2f}%")

    print("\nConfusion Matrix:")
    header = f"{'True \\ Pred':<22}" + "".join(f"{cls:>22}" for cls in class_names)
    print(header)
    for i, cls_true in enumerate(class_names):
        row_str = f"{cls_true:<22}" + "".join(f"{cm[i, j]:>22}" for j in range(num_classes))
        print(row_str)
    print("====================================================")

    # Save metrics JSON
    metrics_summary = {
        "overall_accuracy": overall_accuracy,
        "confusion_matrix": cm.tolist(),
        "per_class": per_class_metrics,
        "safety_sweeps": sweep_results,
        "class_mapping": class_to_idx
    }

    metrics_path = outputs_dir / "metrics.json"
    with open(metrics_path, "w", encoding="utf-8") as f:
        json.dump(metrics_summary, f, indent=2)
    print(f"Evaluation metrics saved to {metrics_path}")

    # Plot Confusion Matrix and save as confusion_matrix.png
    fig, ax = plt.subplots(figsize=(8, 6))
    im = ax.imshow(cm, interpolation='nearest', cmap=plt.cm.Blues)
    ax.figure.colorbar(im, ax=ax)
    ax.set(xticks=np.arange(num_classes),
           yticks=np.arange(num_classes),
           xticklabels=class_names, yticklabels=class_names,
           title="Confusion Matrix",
           ylabel="True Label",
           xlabel="Predicted Label")

    fmt = 'd'
    thresh = cm.max() / 2.
    for i in range(num_classes):
        for j in range(num_classes):
            ax.text(j, i, format(cm[i, j], fmt),
                    ha="center", va="center",
                    color="white" if cm[i, j] > thresh else "black")
    fig.tight_layout()
    cm_path = outputs_dir / "confusion_matrix.png"
    plt.savefig(str(cm_path))
    plt.close()
    print(f"Confusion matrix saved to {cm_path}")

    # Copy misclassified samples
    if misclassified_dir.exists():
        shutil.rmtree(misclassified_dir)
    misclassified_dir.mkdir(parents=True, exist_ok=True)

    misclassified_count = 0
    for idx, (filepath, true_label_idx) in enumerate(test_dataset.imgs):
        pred_label_idx = all_preds[idx]
        if true_label_idx != pred_label_idx:
            true_label = idx_to_class[true_label_idx]
            pred_label = idx_to_class[pred_label_idx]

            dest_folder = misclassified_dir / f"{true_label}_pred_{pred_label}"
            dest_folder.mkdir(parents=True, exist_ok=True)

            shutil.copy(filepath, dest_folder / pathlib.Path(filepath).name)
            misclassified_count += 1

    print(f"Saved {misclassified_count} misclassified crop images to {misclassified_dir}")

    # Copy false allow cases (true label is block, predicted is white_dashed)
    if false_allow_dir.exists():
        shutil.rmtree(false_allow_dir)
    false_allow_dir.mkdir(parents=True, exist_ok=True)

    false_allow_count = 0
    for idx, (filepath, true_label_idx) in enumerate(test_dataset.imgs):
        pred_label_idx = all_preds[idx]
        prob_dashed = all_probs[idx][white_dashed_idx]

        # If true label is not white_dashed but decision (softmax prob of white_dashed) >= 0.5
        if true_label_idx != white_dashed_idx and prob_dashed >= 0.5:
            true_label = idx_to_class[true_label_idx]
            dest_folder = false_allow_dir / f"{true_label}_to_white_dashed"
            dest_folder.mkdir(parents=True, exist_ok=True)

            # Append confidence to filename
            fname = pathlib.Path(filepath).stem + f"_conf_{prob_dashed:.2f}.jpg"
            shutil.copy(filepath, dest_folder / fname)
            false_allow_count += 1

    print(f"Saved {false_allow_count} false allow crop images to {false_allow_dir}")

if __name__ == "__main__":
    main()
