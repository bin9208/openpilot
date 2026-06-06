#!/usr/bin/env python3
import argparse
import json
import pathlib
import cv2
import torch
from torchvision import transforms
from PIL import Image
import pandas as pd
import numpy as np

from train import get_model

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]

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

def get_transforms():
    return transforms.Compose([
        transforms.ToTensor(),
        transforms.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225])
    ])

def predict_single(image_path, model, transform, idx_to_class, device):
    img = Image.open(image_path).convert("RGB")
    tensor = transform(img).unsqueeze(0).to(device)

    with torch.no_grad():
        outputs = model(tensor)
        probabilities = torch.softmax(outputs, dim=1)[0]
        confidence, predicted_idx = torch.max(probabilities, 0)

    pred_class = idx_to_class[predicted_idx.item()]
    return pred_class, confidence.item(), probabilities.cpu().numpy()

def run_csv_batch(csv_path, crops_dir, model, transform, idx_to_class, class_to_idx, device):
    print(f"Reading batch CSV: {csv_path}")
    df = pd.read_csv(csv_path)

    label_col = "corrected_label" if "corrected_label" in df.columns else "label"

    print(f"Scanning crops directory: {crops_dir}...")
    crop_mapping = {}
    crops_path = pathlib.Path(crops_dir)
    if crops_path.exists():
        for p in crops_path.glob("**/*.jpg"):
            crop_mapping[p.name] = p

    print(f"Found {len(crop_mapping)} crops in directory.")

    match_count = 0
    total_count = 0

    for idx, row in df.iterrows():
        segment = row["segment"]
        frame_idx = int(row["frame_idx"])
        side = row["side"]
        ground_truth = row[label_col]

        if ground_truth not in class_to_idx:
            continue

        # Match crops using prefix to support both qcamera and fcamera formats
        prefix = f"{segment}_f{frame_idx:05d}_{side}"
        matching_filenames = [k for k in crop_mapping if k.startswith(prefix)]

        for crop_filename in matching_filenames:
            image_path = crop_mapping[crop_filename]
            pred_class, conf, _ = predict_single(image_path, model, transform, idx_to_class, device)

            if pred_class == ground_truth:
                match_count += 1
            total_count += 1

            if total_count % 500 == 0:
                print(f"Processed {total_count} images... Current Batch Accuracy: {match_count / total_count * 100:.2f}%")

    if total_count > 0:
        overall_acc = match_count / total_count
        print("\n================ BATCH INFERENCE SUMMARY ================")
        print(f"Total evaluated samples: {total_count}")
        print(f"Matching predictions:    {match_count}")
        print(f"Batch Accuracy:          {overall_acc * 100:.2f}%")
        print("=========================================================")
    else:
        print("No matching crop images found in target crops directory for the rows in the CSV file.")

def main():
    parser = argparse.ArgumentParser(description="Lane Marking Model Inference Tool")
    parser.add_argument("--config", type=str, default="config.json", help="Path to config file")
    parser.add_argument("--image", type=str, help="Path to a single crop image to predict")
    parser.add_argument("--csv", type=str, help="Path to labels.csv or corrected_labels.csv for batch prediction")
    args = parser.parse_args()

    # Load configuration
    config_path = resolve_config_path(args.config)
    print(f"Loading configuration from {config_path}...")
    with open(config_path, "r", encoding="utf-8") as f:
        config = json.load(f)

    output_dir = pathlib.Path(config["output_dir"])
    outputs_dir = output_dir / "outputs"

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    runtime_model_path = resolve_repo_path(config["runtime_model_path"]) if config.get("runtime_model_path") else None

    if runtime_model_path and runtime_model_path.exists():
        print(f"Loading TorchScript runtime model from {runtime_model_path}...")
        model = torch.jit.load(str(runtime_model_path), map_location=device)
        model = model.to(device)
        model.eval()
        runtime_classes = config.get("runtime_classes") or sorted(config["classes"])
        idx_to_class = {idx: name for idx, name in enumerate(runtime_classes)}
        class_to_idx = {name: idx for idx, name in idx_to_class.items()}
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

    transform = get_transforms()

    if args.image:
        img_path = pathlib.Path(args.image)
        if not img_path.exists():
            print(f"Error: Image {args.image} does not exist.")
            return
        pred_class, conf, probs = predict_single(img_path, model, transform, idx_to_class, device)
        print("\n================ SINGLE CROP PREDICTION ================")
        print(f"Image:      {img_path.name}")
        print(f"Prediction: {pred_class} (Confidence: {conf:.4f})")
        print("Class Probabilities:")
        for idx, prob in enumerate(probs):
            print(f"  {idx_to_class[idx]}: {prob:.4f}")
        print("=========================================================")

    elif args.csv:
        run_csv_batch(args.csv, config["crops_dir"], model, transform, idx_to_class, class_to_idx, device)

    else:
        parser.print_help()

if __name__ == "__main__":
    main()
