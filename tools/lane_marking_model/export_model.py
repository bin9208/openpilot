#!/usr/bin/env python3
import json
import pathlib
import sys
import argparse
import shutil
import torch
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

def main():
    parser = argparse.ArgumentParser(description="Export trained model to TorchScript and ONNX formats")
    parser.add_argument("--config", type=str, default="config.json", help="Path to config file")
    args = parser.parse_args()

    config_path = resolve_config_path(args.config)
    print(f"Loading configuration from {config_path}...")
    with open(config_path, "r", encoding="utf-8") as f:
        config = json.load(f)

    output_dir = pathlib.Path(config["output_dir"])
    outputs_dir = output_dir / "outputs"
    outputs_dir.mkdir(parents=True, exist_ok=True)

    # Model checkpoint paths
    checkpoint_path = outputs_dir / "best_model.pth"
    if not checkpoint_path.exists():
        # Fallback to old path
        checkpoints_dir = pathlib.Path(config["checkpoints_dir"])
        checkpoint_path = checkpoints_dir / "best_model.pth"

    if not checkpoint_path.exists():
        print(f"Error: Model checkpoint not found. Please run train.py first!")
        return

    device = torch.device("cpu") # Export on CPU for portability
    checkpoint = torch.load(checkpoint_path, map_location=device)

    class_to_idx = checkpoint["class_to_idx"]
    model_arch = checkpoint["config"]["model_arch"]
    num_classes = len(class_to_idx)
    crop_size = config["crop_size"]

    print(f"Loading model weights from {checkpoint_path}...")
    model = get_model(model_arch, num_classes, pretrained=False)
    model.load_state_dict(checkpoint["model_state_dict"])
    model.eval()

    # Create dummy input tensor
    dummy_input = torch.randn(1, 3, crop_size, crop_size)
    print(f"Created dummy input of shape: {dummy_input.shape}")

    # 1. Save best_model.pth copy in outputs_dir
    final_pth_path = outputs_dir / "best_model.pth"
    if checkpoint_path != final_pth_path:
        torch.save(checkpoint, final_pth_path)
        print(f"  --> Copied model checkpoint to {final_pth_path}")

    # 2. Export to TorchScript
    print("Exporting model to TorchScript JIT tracing...")
    torchscript_path = outputs_dir / "best_model.torchscript"
    try:
        traced_model = torch.jit.trace(model, dummy_input)
        traced_model.save(str(torchscript_path))
        print(f"  --> TorchScript model saved to {torchscript_path}")
    except Exception as e:
        print(f"  WARNING: Failed to export to TorchScript: {e}")
        torchscript_path = None

    # 3. Export to ONNX
    print("Exporting model to ONNX...")
    onnx_path = outputs_dir / "best_model.onnx"
    try:
        torch.onnx.export(
            model,
            dummy_input,
            str(onnx_path),
            export_params=True,
            opset_version=11,
            do_constant_folding=True,
            input_names=["input"],
            output_names=["output"],
            dynamic_axes={"input": {0: "batch_size"}, "output": {0: "batch_size"}}
        )
        print(f"  --> ONNX model saved to {onnx_path}")
    except Exception as e:
        print(f"  WARNING: Failed to export to ONNX: {e}")
        onnx_path = None

    # 4. Verification Check
    print("Verifying outputs between PyTorch, TorchScript, and ONNX models...")
    diff_ts = -1.0
    diff_onnx = -1.0

    with torch.no_grad():
        pytorch_out = model(dummy_input).numpy()

    if torchscript_path and torchscript_path.exists():
        try:
            ts_model = torch.jit.load(str(torchscript_path))
            with torch.no_grad():
                ts_out = ts_model(dummy_input).numpy()
            diff_ts = float(np.max(np.abs(pytorch_out - ts_out)))
            print(f"  --> TorchScript max absolute difference: {diff_ts:.6e}")
        except Exception as e:
            print(f"  WARNING: Failed to run validation on TorchScript: {e}")

    runtime_model_path = config.get("runtime_model_path")
    if torchscript_path and torchscript_path.exists() and runtime_model_path:
        runtime_path = resolve_repo_path(runtime_model_path)
        runtime_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(torchscript_path, runtime_path)
        print(f"  --> Runtime TorchScript model copied to {runtime_path}")
        idx_to_class = {int(k): v for k, v in checkpoint["idx_to_class"].items()}
        class_names = [idx_to_class[i] for i in range(num_classes)]
        runtime_classes_path = runtime_path.with_suffix(".classes.json")
        with runtime_classes_path.open("w", encoding="utf-8") as f:
            json.dump(class_names, f, indent=2)
        print(f"  --> Runtime class order copied to {runtime_classes_path}")

    if onnx_path and onnx_path.exists():
        try:
            import onnxruntime as ort
            ort_session = ort.InferenceSession(str(onnx_path))
            ort_inputs = {ort_session.get_inputs()[0].name: dummy_input.numpy()}
            onnx_out = ort_session.run(None, ort_inputs)[0]
            diff_onnx = float(np.max(np.abs(pytorch_out - onnx_out)))
            print(f"  --> ONNX max absolute difference: {diff_onnx:.6e}")
        except Exception as e:
            print(f"  WARNING: Failed to run validation on ONNX: {e}")

    check_results = {
        "torchscript_diff": diff_ts,
        "onnx_diff": diff_onnx,
        "torchscript_match": bool(diff_ts >= 0 and diff_ts < 1e-4),
        "onnx_match": bool(diff_onnx >= 0 and diff_onnx < 1e-4)
    }

    check_json_path = outputs_dir / "export_check.json"
    with open(check_json_path, "w", encoding="utf-8") as f:
        json.dump(check_results, f, indent=2)
    print(f"Export check saved to {check_json_path}")

if __name__ == "__main__":
    main()
