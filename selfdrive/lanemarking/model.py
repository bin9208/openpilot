#!/usr/bin/env python3
from __future__ import annotations

import importlib
import json
import os
import pickle
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

LANE_MARKING_MODEL_PATH = Path(__file__).resolve().parent / "models" / "lane_marking_fcamera.torchscript"
LANE_MARKING_ONNX_PATH = Path(__file__).resolve().parent / "models" / "lane_marking_fcamera.onnx"
LANE_MARKING_TINYGRAD_PATH = Path(__file__).resolve().parent / "models" / "lane_marking_fcamera_tinygrad.pkl"
LANE_MARKING_CLASSES_PATH = Path(__file__).resolve().parent / "models" / "lane_marking_fcamera.classes.json"
LANE_MARKING_BATCH_SIZE = 6

CLASS_NAMES = (
  "road_edge_or_barrier",
  "white_dashed",
  "white_solid",
  "yellow_solid",
  "yellow_double_solid",
  "yellow_double_dashed",
)

ALLOW_LABEL = "white_dashed"
BLOCK_LABELS = frozenset({
  "road_edge_or_barrier",
  "white_solid",
  "yellow_solid",
  "yellow_double",
  "yellow_double_solid",
  "yellow_double_dashed",
})
UNKNOWN_LABEL = "unknown"

DECISION_ALLOW_CANDIDATE = "allow_candidate"
DECISION_BLOCK = "block"
DECISION_UNCERTAIN_OR_BLOCK = "uncertain_or_block"


@dataclass(frozen=True)
class LaneMarkingPrediction:
  label: str
  confidence: float
  probabilities: dict[str, float]
  decision: str
  allow_candidate: bool


def normalize_threshold(value: int | float | str | None, default: float = 0.60) -> float:
  if value is None:
    return default

  try:
    threshold = float(value)
  except (TypeError, ValueError):
    return default

  if threshold > 1.0:
    threshold /= 100.0
  return min(max(threshold, 0.0), 1.0)


def decision_from_label(label: str, confidence: float, threshold: float, temporal_consistent: bool) -> tuple[str, bool]:
  if label == ALLOW_LABEL:
    allow_candidate = confidence >= threshold and temporal_consistent
    return (DECISION_ALLOW_CANDIDATE, True) if allow_candidate else (DECISION_UNCERTAIN_OR_BLOCK, False)

  if label in BLOCK_LABELS:
    return DECISION_BLOCK, False

  return DECISION_UNCERTAIN_OR_BLOCK, False


class TemporalConsistencyFilter:
  def __init__(self, required_frames: int = 3) -> None:
    self.required_frames = max(1, required_frames)
    self.last_label = UNKNOWN_LABEL
    self.count = 0

  def update(self, label: str, confidence: float, threshold: float) -> bool:
    if label != ALLOW_LABEL or confidence < threshold:
      self.last_label = label
      self.count = 0
      return False

    if label == self.last_label:
      self.count += 1
    else:
      self.last_label = label
      self.count = 1

    return self.count >= self.required_frames


def _load_class_names(path: Path = LANE_MARKING_CLASSES_PATH, default: Sequence[str] = CLASS_NAMES) -> tuple[str, ...]:
  if path.exists():
    with path.open("r", encoding="utf-8") as f:
      class_names = json.load(f)
    if isinstance(class_names, list) and all(isinstance(cls, str) for cls in class_names):
      return tuple(class_names)
  return tuple(default)


class LaneMarkingTinygradModel:
  runtime = "tinygrad"

  def __init__(
    self,
    model_path: Path = LANE_MARKING_TINYGRAD_PATH,
    class_names: Sequence[str] = CLASS_NAMES,
    batch_size: int = LANE_MARKING_BATCH_SIZE,
  ) -> None:
    self.model_path = model_path
    self.class_names = tuple(class_names)
    self.batch_size = batch_size
    self.model = None
    self.Tensor = None
    self.Device = None
    self.input_buffer = None

  @property
  def loaded(self) -> bool:
    return self.model is not None and self.Tensor is not None and self.Device is not None

  @staticmethod
  def _setup_tinygrad_env() -> None:
    from openpilot.system.hardware import TICI
    os.environ.setdefault("DEV", "QCOM" if TICI else "CPU")
    os.environ.setdefault("JIT_BATCH_SIZE", "0")
    if not TICI:
      os.environ.setdefault("CPU_LLVM", "1")
      os.environ.setdefault("THREADS", "0")

  def load(self) -> None:
    self._setup_tinygrad_env()
    from tinygrad import Tensor, Device
    from openpilot.common.file_chunker import read_file_chunked

    self.class_names = _load_class_names(default=self.class_names)
    model_bytes = read_file_chunked(str(self.model_path))
    self.model = pickle.loads(model_bytes)
    self.Tensor = Tensor
    self.Device = Device

  def predict_logits(self, input_array):
    if not self.loaded:
      self.load()

    assert self.model is not None
    assert self.Tensor is not None
    assert self.Device is not None

    batch = int(input_array.shape[0])
    if batch <= 0:
      return input_array[:0]
    if batch > self.batch_size:
      raise ValueError(f"lane marking batch too large: {batch} > {self.batch_size}")

    if self.input_buffer is None:
      import numpy as np
      self.input_buffer = np.zeros((self.batch_size, 3, 224, 224), dtype=np.float32)

    self.input_buffer.fill(0.0)
    self.input_buffer[:batch] = input_array
    tensor = self.Tensor(self.input_buffer, device=self.Device.DEFAULT).realize()
    logits = self.model(input=tensor).contiguous().realize().numpy()
    return logits[:batch]


class LaneMarkingTorchScriptModel:
  runtime = "torchscript"

  def __init__(
    self,
    model_path: Path = LANE_MARKING_MODEL_PATH,
    class_names: Sequence[str] = CLASS_NAMES,
    device: str | None = None,
  ) -> None:
    self.model_path = model_path
    self.class_names = tuple(class_names)
    self.requested_device = device or os.getenv("LANE_MARKING_TORCH_DEVICE", "auto")
    self.device = None
    self.model = None
    self.torch = None

  @property
  def loaded(self) -> bool:
    return self.model is not None and self.torch is not None

  @staticmethod
  def _allow_cpu() -> bool:
    return os.getenv("LANE_MARKING_ALLOW_CPU", "0").strip().lower() in ("1", "true", "yes", "on")

  def _resolve_device(self, torch):
    requested = (self.requested_device or "auto").strip().lower()

    if requested in ("auto", "gpu"):
      if getattr(torch, "cuda", None) is not None and torch.cuda.is_available():
        return torch.device("cuda")

      mps_backend = getattr(getattr(torch, "backends", None), "mps", None)
      if mps_backend is not None and mps_backend.is_available():
        return torch.device("mps")

      if self._allow_cpu():
        return torch.device("cpu")

      raise RuntimeError("lane marking GPU backend is not available; set LANE_MARKING_ALLOW_CPU=1 to permit CPU fallback")

    if requested.startswith("cuda"):
      if getattr(torch, "cuda", None) is not None and torch.cuda.is_available():
        return torch.device(requested)
      raise RuntimeError(f"requested lane marking CUDA device is not available: {self.requested_device}")

    if requested == "mps":
      mps_backend = getattr(getattr(torch, "backends", None), "mps", None)
      if mps_backend is not None and mps_backend.is_available():
        return torch.device("mps")
      raise RuntimeError("requested lane marking MPS device is not available")

    if requested == "cpu" and not self._allow_cpu():
      raise RuntimeError("lane marking CPU device requested without LANE_MARKING_ALLOW_CPU=1")

    return torch.device(self.requested_device)

  def load(self) -> None:
    if not self.model_path.exists():
      raise FileNotFoundError(f"lane marking model not found: {self.model_path}")

    torch = importlib.import_module("torch")
    device = self._resolve_device(torch)
    model = torch.jit.load(str(self.model_path), map_location=device)
    model.to(device)
    model.eval()

    self.class_names = _load_class_names(self.model_path.with_suffix(".classes.json"), self.class_names)

    self.torch = torch
    self.device = device
    self.model = model

  def to_device(self, tensor):
    if self.device is None:
      return tensor
    return tensor.to(self.device, non_blocking=True)

  def predict_logits(self, input_array):
    if not self.loaded:
      self.load()

    assert self.torch is not None
    assert self.model is not None

    tensor = self.to_device(self.torch.from_numpy(input_array))
    inference_context = getattr(self.torch, "inference_mode", self.torch.no_grad)
    with inference_context():
      logits = self.model(tensor)
    return logits.detach().cpu().numpy()

  def predict_tensor(
    self,
    tensor,
    threshold: float,
    temporal_filter: TemporalConsistencyFilter | None = None,
  ) -> LaneMarkingPrediction:
    if not self.loaded:
      self.load()

    assert self.torch is not None
    assert self.model is not None

    tensor = self.to_device(tensor)
    inference_context = getattr(self.torch, "inference_mode", self.torch.no_grad)
    with inference_context():
      logits = self.model(tensor)
      probs = self.torch.softmax(logits, dim=1)[0]
      confidence, predicted_idx = self.torch.max(probs, 0)

    idx = int(predicted_idx.item())
    label = self.class_names[idx] if idx < len(self.class_names) else UNKNOWN_LABEL
    conf = float(confidence.item())
    prob_values = [float(v) for v in probs.cpu().tolist()]
    probabilities = {
      cls: prob_values[i] if i < len(prob_values) else 0.0
      for i, cls in enumerate(self.class_names)
    }

    temporal_consistent = temporal_filter.update(label, conf, threshold) if temporal_filter is not None else True
    decision, allow_candidate = decision_from_label(label, conf, threshold, temporal_consistent)

    return LaneMarkingPrediction(
      label=label,
      confidence=conf,
      probabilities=probabilities,
      decision=decision,
      allow_candidate=allow_candidate,
    )


def create_lane_marking_model(backend: str | None = None, **kwargs):
  backend = (backend or os.getenv("LANE_MARKING_BACKEND", "tinygrad")).strip().lower()
  if backend in ("tinygrad", "qcom", "gpu"):
    return LaneMarkingTinygradModel(**kwargs)
  if backend in ("torch", "torchscript"):
    return LaneMarkingTorchScriptModel(**kwargs)
  raise ValueError(f"unknown lane marking backend: {backend}")
