#!/usr/bin/env python3
from __future__ import annotations

import importlib
import json
import os
from pathlib import Path
from typing import Sequence

TRAFFIC_LIGHT_MODEL_PATH = Path(__file__).resolve().parent / "models" / "traffic_light_crops.onnx"
TRAFFIC_LIGHT_CLASSES_PATH = Path(__file__).resolve().parent / "models" / "traffic_light_crops.classes.json"
TRAFFIC_LIGHT_CROP_SIZE = 160

CLASS_NAMES = (
  "Caution",
  "LeftTurn",
  "NoSignal",
  "Stop",
  "Straight",
  "StraightLeft",
)


def _load_class_names(path: Path = TRAFFIC_LIGHT_CLASSES_PATH, default: Sequence[str] = CLASS_NAMES) -> tuple[str, ...]:
  if path.exists():
    with path.open("r", encoding="utf-8") as f:
      class_names = json.load(f)
    if isinstance(class_names, list) and all(isinstance(cls, str) for cls in class_names):
      return tuple(class_names)
  return tuple(default)


class TrafficLightOnnxRuntimeModel:
  runtime = "onnxruntime"

  def __init__(
    self,
    model_path: Path = TRAFFIC_LIGHT_MODEL_PATH,
    class_names: Sequence[str] = CLASS_NAMES,
  ) -> None:
    self.model_path = model_path
    self.class_names = tuple(class_names)
    self.session = None
    self.input_name = ""
    self.output_name = ""

  @property
  def loaded(self) -> bool:
    return self.session is not None

  def load(self) -> None:
    if not self.model_path.exists():
      raise FileNotFoundError(f"traffic light model not found: {self.model_path}")

    ort = importlib.import_module("onnxruntime")
    available = list(ort.get_available_providers())
    preferred = [
      provider for provider in ("CUDAExecutionProvider", "QNNExecutionProvider", "CPUExecutionProvider")
      if provider in available
    ]
    providers = preferred if preferred else available
    if not providers:
      raise RuntimeError("onnxruntime has no execution providers")

    self.class_names = _load_class_names(default=self.class_names)
    session = ort.InferenceSession(str(self.model_path), providers=providers)
    self.input_name = session.get_inputs()[0].name
    self.output_name = session.get_outputs()[0].name
    self.session = session

  def predict_logits(self, input_array):
    if not self.loaded:
      self.load()

    assert self.session is not None
    return self.session.run([self.output_name], {self.input_name: input_array})[0]


class TrafficLightTinygradOnnxModel:
  runtime = "tinygrad_onnx"

  def __init__(
    self,
    model_path: Path = TRAFFIC_LIGHT_MODEL_PATH,
    class_names: Sequence[str] = CLASS_NAMES,
  ) -> None:
    self.model_path = model_path
    self.class_names = tuple(class_names)
    self.runner = None
    self.Tensor = None
    self.Device = None
    self.input_name = "image"

  @property
  def loaded(self) -> bool:
    return self.runner is not None and self.Tensor is not None

  @staticmethod
  def _setup_tinygrad_env() -> None:
    try:
      from openpilot.system.hardware import TICI
    except Exception:
      TICI = False

    os.environ.setdefault("DEV", "QCOM" if TICI else "CPU")
    os.environ.setdefault("JIT_BATCH_SIZE", "0")
    if not TICI:
      os.environ.setdefault("CPU_LLVM", "1")
      os.environ.setdefault("THREADS", "0")

  @staticmethod
  def _load_onnx_runner():
    for module_name in ("tinygrad.frontend.onnx", "tinygrad.nn.onnx"):
      try:
        module = importlib.import_module(module_name)
      except Exception:
        continue
      runner = getattr(module, "OnnxRunner", None)
      if runner is not None:
        return runner
    raise RuntimeError("tinygrad ONNX runner is not available")

  def load(self) -> None:
    if not self.model_path.exists():
      raise FileNotFoundError(f"traffic light model not found: {self.model_path}")

    self._setup_tinygrad_env()
    from tinygrad import Tensor, Device

    OnnxRunner = self._load_onnx_runner()
    self.class_names = _load_class_names(default=self.class_names)
    self.runner = OnnxRunner(str(self.model_path))
    self.Tensor = Tensor
    self.Device = Device

  def predict_logits(self, input_array):
    if not self.loaded:
      self.load()

    assert self.runner is not None
    assert self.Tensor is not None
    tensor = self.Tensor(input_array, device=getattr(self.Device, "DEFAULT", None)).realize()

    if hasattr(self.runner, "run"):
      outputs = self.runner.run({self.input_name: tensor})
    else:
      try:
        outputs = self.runner(**{self.input_name: tensor})
      except TypeError:
        outputs = self.runner(tensor)

    if isinstance(outputs, dict):
      outputs = next(iter(outputs.values()))
    elif isinstance(outputs, (list, tuple)):
      outputs = outputs[0]

    if hasattr(outputs, "realize"):
      outputs = outputs.realize()
    if hasattr(outputs, "numpy"):
      return outputs.numpy()
    return outputs


class TrafficLightAutoModel:
  runtime = "auto"

  def __init__(self, model_path: Path = TRAFFIC_LIGHT_MODEL_PATH, class_names: Sequence[str] = CLASS_NAMES) -> None:
    self.model_path = model_path
    self.class_names = tuple(class_names)
    self.model = None
    self.load_errors: list[str] = []

  @property
  def loaded(self) -> bool:
    return self.model is not None and self.model.loaded

  def _candidate_backends(self) -> tuple[str, ...]:
    try:
      from openpilot.system.hardware import TICI
    except Exception:
      TICI = False

    if TICI:
      return ("tinygrad", "onnxruntime")
    return ("onnxruntime", "tinygrad")

  def load(self) -> None:
    self.load_errors.clear()
    for backend in self._candidate_backends():
      model = create_traffic_light_model(backend, model_path=self.model_path, class_names=self.class_names)
      try:
        model.load()
      except Exception as e:
        self.load_errors.append(f"{backend}: {e}")
        continue
      self.model = model
      self.runtime = model.runtime
      self.class_names = model.class_names
      return
    raise RuntimeError("; ".join(self.load_errors) if self.load_errors else "no traffic light backend candidates")

  def predict_logits(self, input_array):
    if not self.loaded:
      self.load()
    assert self.model is not None
    return self.model.predict_logits(input_array)


def create_traffic_light_model(backend: str | None = None, **kwargs):
  backend = (backend or os.getenv("TRAFFIC_LIGHT_STOPLINE_BACKEND", "auto")).strip().lower()
  if backend == "auto":
    return TrafficLightAutoModel(**kwargs)
  if backend in ("onnxruntime", "onnx", "ort", "cuda", "gpu"):
    return TrafficLightOnnxRuntimeModel(**kwargs)
  if backend in ("tinygrad", "qcom", "tinygrad_onnx"):
    return TrafficLightTinygradOnnxModel(**kwargs)
  if backend in ("none", "heuristic", "off"):
    raise RuntimeError("traffic light model backend disabled")
  raise ValueError(f"unknown traffic light backend: {backend}")
