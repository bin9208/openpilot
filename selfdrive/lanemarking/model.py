#!/usr/bin/env python3
from __future__ import annotations

import importlib
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

LANE_MARKING_MODEL_PATH = Path(__file__).resolve().parent / "models" / "lane_marking_fcamera.torchscript"

CLASS_NAMES = (
  "road_edge_or_barrier",
  "white_dashed",
  "white_solid",
  "yellow_solid",
)

ALLOW_LABEL = "white_dashed"
BLOCK_LABELS = frozenset({"road_edge_or_barrier", "white_solid", "yellow_solid", "yellow_double"})
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


class LaneMarkingTorchScriptModel:
  def __init__(self, model_path: Path = LANE_MARKING_MODEL_PATH, class_names: Sequence[str] = CLASS_NAMES) -> None:
    self.model_path = model_path
    self.class_names = tuple(class_names)
    self.model = None
    self.torch = None

  @property
  def loaded(self) -> bool:
    return self.model is not None and self.torch is not None

  def load(self) -> None:
    if not self.model_path.exists():
      raise FileNotFoundError(f"lane marking model not found: {self.model_path}")

    torch = importlib.import_module("torch")
    model = torch.jit.load(str(self.model_path), map_location="cpu")
    model.eval()

    self.torch = torch
    self.model = model

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

    with self.torch.no_grad():
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
