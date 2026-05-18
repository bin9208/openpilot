#!/usr/bin/env python3
"""
yolod v3: streams camera frames to phone via UDP,
receives YOLO detection results, publishes as cereal yoloObjectData.

Connection flow:
  1. Discovery: yolod broadcasts hello → phone:8080
  2. Phone replies hello → yolod:8081 → TARGET_IP learned
  3. Streamer: yolod sends JPEG frames → phone:8080
  4. Phone runs YOLO → sends JSON results → yolod:8081
  5. Receiver publishes cereal yoloObjectData
  6. Heartbeat: always publishes yoloObjectData every 1s (keeps UI valid())
"""
import json
import os
import socket
import struct
import subprocess
import threading
import time
import numpy as np

import cereal.messaging as messaging
from openpilot.common.swaglog import cloudlog

VERSION = "v9"

def ylog(msg):
  """Log to both cloudlog AND stdout (tmux visible)."""
  cloudlog.info(msg)
  print(msg, flush=True)
FRAME_PORT = 8080
RESULT_PORT = 8081
JPEG_QUALITY = int(os.getenv("YOLO_JPEG_QUALITY", "55"))
FPS = float(os.getenv("YOLO_FPS", "10.0"))
CONNECTION_TIMEOUT = 15.0
YOLO_INPUT_SIZE = 640      # YOLO model input resolution (square)
YOLO_PAD_VALUE = 114       # Ultralytics default letterbox pad
YOLO_LETTERBOX = os.getenv("YOLO_STRETCH", "0") != "1"
YOLO_ROI_MODE = os.getenv("YOLO_ROI", "traffic_light").lower()
YOLO_ROI_X_MARGIN = float(os.getenv("YOLO_ROI_X_MARGIN", "0.08"))
YOLO_ROI_Y_TOP = float(os.getenv("YOLO_ROI_Y_TOP", "0.00"))
YOLO_ROI_Y_BOTTOM = float(os.getenv("YOLO_ROI_Y_BOTTOM", "0.76"))
DEFAULT_CAMERA_WIDTH = 1928
DEFAULT_CAMERA_HEIGHT = 1208
MIN_TRAFFIC_LIGHT_CONF = 0.25
MAX_TRAFFIC_LIGHT_BOX_W = float(os.getenv("YOLO_MAX_BOX_W", "0.45"))
MAX_TRAFFIC_LIGHT_BOX_H = float(os.getenv("YOLO_MAX_BOX_H", "0.40"))
MAX_TRAFFIC_LIGHT_BOX_AREA = float(os.getenv("YOLO_MAX_BOX_AREA", "0.12"))

# Shared state (protected by lock)
TARGET_IP = None
last_hello_time = 0.0      # last hello from phone (keeps TARGET_IP alive)
last_result_time = 0.0     # last actual detection result
last_detect_pub_time = 0.0 # last time _publish_detections was called
frames_sent = 0
results_received = 0
backend_name = "none"
streamer_status = "init"
frame_metas = {}           # frame_id -> geometry used to map model boxes back to camera
frame_send_times = {}      # frame_id → monotonic_ns (for RTT measurement)
MAX_RTT_TRACKED = 100      # keep at most N entries
lock = threading.Lock()
pub_lock = threading.Lock()  # protects pm.send() across threads


def _configure_udp_socket(sock):
  try:
    sock.setsockopt(socket.SOL_IP, socket.IP_TOS, 0x10)  # IPTOS_LOWDELAY
  except Exception:
    pass
  for opt in (socket.SO_SNDBUF, socket.SO_RCVBUF):
    try:
      sock.setsockopt(socket.SOL_SOCKET, opt, 262144)
    except Exception:
      pass


# ── NV12 conversion ─────────────────────────────────────────────────

def _even_clamped(value, low, high):
  return int(np.clip(int(value), low, high)) & ~1


def _traffic_light_crop(src_w, src_h):
  if YOLO_ROI_MODE in ("0", "false", "off", "full", "none"):
    return 0, 0, src_w & ~1, src_h & ~1, False

  x_margin = int(src_w * np.clip(YOLO_ROI_X_MARGIN, 0.0, 0.35))
  y_top = int(src_h * np.clip(YOLO_ROI_Y_TOP, 0.0, 0.7))
  y_bottom = int(src_h * np.clip(YOLO_ROI_Y_BOTTOM, 0.3, 1.0))
  x1 = _even_clamped(x_margin, 0, src_w - 2)
  x2 = _even_clamped(src_w - x_margin, x1 + 2, src_w)
  y1 = _even_clamped(y_top, 0, src_h - 2)
  y2 = _even_clamped(max(y_bottom, y1 + 2), y1 + 2, src_h)
  return x1, y1, x2 - x1, y2 - y1, True


def _resize_nv12(yuv_flat, src_h, src_w, src_stride, uv_offset, dst_h, dst_w,
                 crop_x=0, crop_y=0, crop_w=None, crop_h=None):
  """Resize NV12 Y+UV planes to dst_h x dst_w before RGB conversion.
  Handles strided/height-padded source (stride=2048, y_rows=1216 for height=1208).
  Returns (y_small, uv_small) as uint8 arrays ready for _nv12_small_to_rgb()."""
  crop_w = src_w if crop_w is None else crop_w
  crop_h = src_h if crop_h is None else crop_h
  crop_x = _even_clamped(crop_x, 0, max(0, src_w - 2))
  crop_y = _even_clamped(crop_y, 0, max(0, src_h - 2))
  crop_w = _even_clamped(crop_w, 2, src_w - crop_x)
  crop_h = _even_clamped(crop_h, 2, src_h - crop_y)

  # Y plane: crop out stride padding and height padding
  y_rows = uv_offset // src_stride
  y_plane = yuv_flat[:uv_offset].reshape(y_rows, src_stride)[:src_h, :src_w]
  y_plane = y_plane[crop_y:crop_y + crop_h, crop_x:crop_x + crop_w]

  # UV plane: interleaved U/V, half height
  uv_data = yuv_flat[uv_offset:]
  uv_rows = len(uv_data) // src_stride
  uv_plane = uv_data[:uv_rows * src_stride].reshape(uv_rows, src_stride)[:src_h // 2, :src_w]
  uv_plane = uv_plane[crop_y // 2:(crop_y + crop_h) // 2, crop_x:crop_x + crop_w]

  # Nearest-neighbor resize (same column indices for Y and UV)
  uv_dst_h = max(1, dst_h // 2)
  r_y  = (np.arange(dst_h)    * crop_h        / dst_h).astype(np.int32)
  c    = (np.arange(dst_w)    * crop_w        / dst_w).astype(np.int32)
  r_uv = (np.arange(uv_dst_h) * (crop_h // 2) / uv_dst_h).astype(np.int32)

  y_small  = y_plane[np.ix_(r_y,  c)]
  uv_small = uv_plane[np.ix_(r_uv, c)]

  return y_small, uv_small


def _nv12_small_to_rgb(y_plane, uv_plane):
  """Convert pre-resized NV12 Y+UV planes to RGB uint8 (no stride complications)."""
  h, w = y_plane.shape
  Y = y_plane.astype(np.float32)
  U = uv_plane[:, 0::2].astype(np.float32)
  V = uv_plane[:, 1::2].astype(np.float32)
  U_f = np.repeat(np.repeat(U, 2, axis=0), 2, axis=1)[:h, :w]
  V_f = np.repeat(np.repeat(V, 2, axis=0), 2, axis=1)[:h, :w]
  if U_f.shape[0] < h or U_f.shape[1] < w:
    U_f = np.pad(U_f, ((0, max(0, h - U_f.shape[0])), (0, max(0, w - U_f.shape[1]))), mode='edge')[:h, :w]
    V_f = np.pad(V_f, ((0, max(0, h - V_f.shape[0])), (0, max(0, w - V_f.shape[1]))), mode='edge')[:h, :w]
  R = np.clip(Y + 1.402  * (V_f - 128),                                   0, 255).astype(np.uint8)
  G = np.clip(Y - 0.344136 * (U_f - 128) - 0.714136 * (V_f - 128),       0, 255).astype(np.uint8)
  B = np.clip(Y + 1.772  * (U_f - 128),                                   0, 255).astype(np.uint8)
  return np.stack([R, G, B], axis=2)


def _frame_meta(src_w=DEFAULT_CAMERA_WIDTH, src_h=DEFAULT_CAMERA_HEIGHT, input_size=YOLO_INPUT_SIZE,
                scale=None, pad_x=0.0, pad_y=0.0, letterboxed=False,
                crop_x=0.0, crop_y=0.0, crop_w=None, crop_h=None):
  if scale is None:
    crop_w = src_w if crop_w is None else crop_w
    crop_h = src_h if crop_h is None else crop_h
    scale = input_size / float(max(crop_w, crop_h))
  return {
    "src_w": float(src_w),
    "src_h": float(src_h),
    "input": float(input_size),
    "scale": float(scale),
    "pad_x": float(pad_x),
    "pad_y": float(pad_y),
    "letterboxed": bool(letterboxed),
    "crop_x": float(crop_x),
    "crop_y": float(crop_y),
    "crop_w": float(src_w if crop_w is None else crop_w),
    "crop_h": float(src_h if crop_h is None else crop_h),
  }


DEFAULT_FRAME_META = _frame_meta()


def _prepare_yolo_rgb(yuv_flat, src_h, src_w, src_stride, uv_offset):
  """Return a YOLO-sized RGB frame and geometry metadata for box reprojection."""
  crop_x, crop_y, crop_w, crop_h, _ = _traffic_light_crop(src_w, src_h)

  if YOLO_LETTERBOX:
    scale = min(YOLO_INPUT_SIZE / float(crop_w), YOLO_INPUT_SIZE / float(crop_h))
    resized_w = max(2, min(YOLO_INPUT_SIZE, int(round(crop_w * scale))))
    resized_h = max(2, min(YOLO_INPUT_SIZE, int(round(crop_h * scale))))
    pad_x = float((YOLO_INPUT_SIZE - resized_w) // 2)
    pad_y = float((YOLO_INPUT_SIZE - resized_h) // 2)

    y_small, uv_small = _resize_nv12(yuv_flat, src_h, src_w, src_stride, uv_offset, resized_h, resized_w,
                                     crop_x, crop_y, crop_w, crop_h)
    rgb_resized = _nv12_small_to_rgb(y_small, uv_small)
    rgb = np.full((YOLO_INPUT_SIZE, YOLO_INPUT_SIZE, 3), YOLO_PAD_VALUE, dtype=np.uint8)
    top = int(pad_y)
    left = int(pad_x)
    rgb[top:top + resized_h, left:left + resized_w] = rgb_resized
    return rgb, _frame_meta(src_w, src_h, YOLO_INPUT_SIZE, scale, pad_x, pad_y, True,
                            crop_x, crop_y, crop_w, crop_h)

  y_small, uv_small = _resize_nv12(yuv_flat, src_h, src_w, src_stride, uv_offset, YOLO_INPUT_SIZE, YOLO_INPUT_SIZE,
                                   crop_x, crop_y, crop_w, crop_h)
  rgb = _nv12_small_to_rgb(y_small, uv_small)
  return rgb, _frame_meta(src_w, src_h, YOLO_INPUT_SIZE, None, 0.0, 0.0, False,
                          crop_x, crop_y, crop_w, crop_h)


# ── Image backend setup ─────────────────────────────────────────────

_img_backend = None

def _setup_backend():
  global _img_backend, backend_name

  # libjpeg-turbo: ~3ms vs PIL's ~15ms for 640×640
  # Install: pip install PyTurboJPEG  (libturbojpeg already on AGNOS)
  try:
    from turbojpeg import TurboJPEG, TJPF_RGB
    _tj = TurboJPEG()
    def _encode_turbo(rgb):
      return _tj.encode(rgb, quality=JPEG_QUALITY, pixel_format=TJPF_RGB)
    _img_backend = _encode_turbo
    backend_name = "turbo"
    ylog("[YOLO] Image backend: libjpeg-turbo")
    return
  except Exception as e:
    cloudlog.info(f"[YOLO] turbo not available: {e}")

  try:
    from PIL import Image as _Image
    import io as _io
    def _encode_pil(rgb):
      img = _Image.fromarray(rgb)
      buf = _io.BytesIO()
      img.save(buf, format='JPEG', quality=JPEG_QUALITY)
      return buf.getvalue()
    _img_backend = _encode_pil
    backend_name = "PIL"
    cloudlog.info("[YOLO] Image backend: PIL")
    return
  except Exception as e:
    cloudlog.info(f"[YOLO] PIL not available: {e}")

  try:
    import cv2 as _cv2
    def _encode_cv2(rgb):
      bgr = _cv2.cvtColor(rgb, _cv2.COLOR_RGB2BGR)
      _, buf = _cv2.imencode('.jpg', bgr, [_cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
      return buf.tobytes()
    _img_backend = _encode_cv2
    backend_name = "cv2"
    cloudlog.info("[YOLO] Image backend: cv2")
    return
  except Exception as e:
    cloudlog.info(f"[YOLO] cv2 not available: {e}")

  try:
    r = subprocess.run(['ffmpeg', '-version'], capture_output=True, timeout=3)
    if r.returncode == 0:
      def _encode_ffmpeg(rgb):
        h, w = rgb.shape[:2]
        cmd = [
          'ffmpeg', '-f', 'rawvideo', '-pix_fmt', 'rgb24',
          '-s', f'{w}x{h}', '-i', 'pipe:0',
          '-q:v', '5', '-f', 'image2', '-vcodec', 'mjpeg', '-frames:v', '1',
          'pipe:1'
        ]
        proc = subprocess.run(cmd, input=rgb.tobytes(), capture_output=True, timeout=2)
        if proc.returncode == 0 and len(proc.stdout) > 0:
          return proc.stdout
        return None
      _img_backend = _encode_ffmpeg
      backend_name = "ffmpeg"
      cloudlog.info("[YOLO] Image backend: ffmpeg")
      return
  except Exception as e:
    cloudlog.info(f"[YOLO] ffmpeg not available: {e}")

  backend_name = "NONE"
  cloudlog.error("[YOLO] NO IMAGE BACKEND. Frame streaming DISABLED.")


# ── Cereal publishing (thread-safe) ─────────────────────────────────

def _float_or_none(value):
  try:
    return float(value)
  except (TypeError, ValueError):
    return None


def _first_float(obj, *keys, default=0.0):
  for key in keys:
    value = _float_or_none(obj.get(key))
    if value is not None:
      return value
  return default


def _class_id(obj):
  for key in ("cls", "classId", "class_id", "id"):
    value = obj.get(key)
    if value is not None:
      try:
        return int(value)
      except (TypeError, ValueError):
        pass
  return 0


def _class_name(obj):
  for key in ("name", "className", "class_name", "label"):
    value = obj.get(key)
    if value is not None:
      return str(value)
  cls = _class_id(obj)
  return f"class_{cls}"


def _confidence(obj):
  conf = _first_float(obj, "conf", "confidence", "score", "prob", default=0.0)
  if conf > 1.0:
    conf *= 0.01 if conf <= 100.0 else 1.0 / 255.0
  return float(np.clip(conf, 0.0, 1.0))


def _traffic_light_state(obj):
  words = []
  for key in ("state", "color", "signal", "name", "className", "class_name", "label"):
    value = obj.get(key)
    if value is not None:
      words.append(str(value).lower().replace("-", "_").replace(" ", "_"))
  text = " ".join(words)

  if any(word in text for word in ("green", "go", "left_green", "arrow_green")):
    return "green"
  if any(word in text for word in ("yellow", "amber", "orange")):
    return "yellow"
  if any(word in text for word in ("red", "stop")):
    return "red"
  return ""


def _box_format(obj):
  for key in ("bbox_format", "box_format", "format"):
    value = obj.get(key)
    if value is not None:
      return str(value).lower()
  return ""


def _coord_space(obj):
  for key in ("coord_space", "box_space", "bbox_space"):
    value = obj.get(key)
    if value is not None:
      return str(value).lower()
  return ""


def _box_from_list(values, fmt):
  if not isinstance(values, (list, tuple)) or len(values) < 4:
    return None
  vals = [_float_or_none(v) for v in values[:4]]
  if any(v is None for v in vals):
    return None
  a, b, c, d = vals
  if "yxyx" in fmt:
    return b, a, d, c
  if "xywh" in fmt:
    if "tl" in fmt or "top" in fmt:
      return a, b, a + c, b + d
    return a - c * 0.5, b - d * 0.5, a + c * 0.5, b + d * 0.5
  return a, b, c, d


def _raw_box(obj):
  fmt = _box_format(obj)
  for key in ("xyxy", "bbox", "box", "rect"):
    box = _box_from_list(obj.get(key), fmt)
    if box is not None:
      return box

  if all(k in obj for k in ("xmin", "ymin", "xmax", "ymax")):
    return (_first_float(obj, "xmin"), _first_float(obj, "ymin"),
            _first_float(obj, "xmax"), _first_float(obj, "ymax"))

  if all(k in obj for k in ("left", "top", "right", "bottom")):
    return (_first_float(obj, "left"), _first_float(obj, "top"),
            _first_float(obj, "right"), _first_float(obj, "bottom"))

  if all(k in obj for k in ("x1", "y1", "x2", "y2")):
    return (_first_float(obj, "x1"), _first_float(obj, "y1"),
            _first_float(obj, "x2"), _first_float(obj, "y2"))

  if all(k in obj for k in ("x", "y", "w", "h")):
    x = _first_float(obj, "x")
    y = _first_float(obj, "y")
    w = _first_float(obj, "w")
    h = _first_float(obj, "h")
    if "xyxy" in fmt:
      return x, y, w, h
    if "yxyx" in fmt:
      return y, x, h, w
    if "xywh" in fmt or "center" in fmt:
      return x - w * 0.5, y - h * 0.5, x + w * 0.5, y + h * 0.5

    # Compatibility with the current Android payload:
    # x,y,w,h are actually [ymin, xmin, ymax, xmax].
    if x <= w and y <= h:
      return y, x, h, w
    return x - w * 0.5, y - h * 0.5, x + w * 0.5, y + h * 0.5

  return None


def _box_to_camera_norm(box, frame_meta, coord_space):
  if box is None:
    return 0.0, 0.0, 0.0, 0.0

  meta = frame_meta or DEFAULT_FRAME_META
  src_w = meta["src_w"]
  src_h = meta["src_h"]
  input_size = meta["input"]
  x1, y1, x2, y2 = box
  max_coord = max(abs(x1), abs(y1), abs(x2), abs(y2))

  source_space = coord_space in ("source", "camera", "original", "src")
  if not coord_space and max_coord > input_size * 1.5:
    source_space = True

  if max_coord <= 2.0:
    if source_space:
      x1, x2 = x1 * src_w, x2 * src_w
      y1, y2 = y1 * src_h, y2 * src_h
    else:
      x1, x2 = x1 * input_size, x2 * input_size
      y1, y2 = y1 * input_size, y2 * input_size

  if not source_space:
    if meta["letterboxed"]:
      scale = max(meta["scale"], 1e-6)
      x1 = (x1 - meta["pad_x"]) / scale
      x2 = (x2 - meta["pad_x"]) / scale
      y1 = (y1 - meta["pad_y"]) / scale
      y2 = (y2 - meta["pad_y"]) / scale
    else:
      x1 = x1 / input_size * meta.get("crop_w", src_w)
      x2 = x2 / input_size * meta.get("crop_w", src_w)
      y1 = y1 / input_size * meta.get("crop_h", src_h)
      y2 = y2 / input_size * meta.get("crop_h", src_h)

    x1 += meta.get("crop_x", 0.0)
    x2 += meta.get("crop_x", 0.0)
    y1 += meta.get("crop_y", 0.0)
    y2 += meta.get("crop_y", 0.0)

  left = float(np.clip(min(x1, x2), 0.0, src_w))
  right = float(np.clip(max(x1, x2), 0.0, src_w))
  top = float(np.clip(min(y1, y2), 0.0, src_h))
  bottom = float(np.clip(max(y1, y2), 0.0, src_h))

  if right <= left or bottom <= top:
    return 0.0, 0.0, 0.0, 0.0

  return ((left + right) * 0.5 / src_w,
          (top + bottom) * 0.5 / src_h,
          (right - left) / src_w,
          (bottom - top) / src_h)


def _normalize_detection(obj, frame_meta):
  state = _traffic_light_state(obj)
  name = _class_name(obj)
  if state and (name.startswith("class_") or name.lower() in ("traffic_light", "light", "signal")):
    name = f"{state}_light"

  x, y, w, h = _box_to_camera_norm(_raw_box(obj), frame_meta, _coord_space(obj))
  return {
    "class_id": _class_id(obj),
    "name": name,
    "conf": _confidence(obj),
    "dist": _first_float(obj, "dist", "distance", "distanceEstimate", default=0.0),
    "state": state,
    "x": x,
    "y": y,
    "w": w,
    "h": h,
  }


def _valid_detection(det):
  if det["conf"] < MIN_TRAFFIC_LIGHT_CONF:
    return False
  if det["w"] <= 0.0 or det["h"] <= 0.0:
    return False
  if det["w"] > MAX_TRAFFIC_LIGHT_BOX_W or det["h"] > MAX_TRAFFIC_LIGHT_BOX_H:
    return False
  if det["w"] * det["h"] > MAX_TRAFFIC_LIGHT_BOX_AREA:
    return False
  return True


def _safe_publish(pm, dat):
  """Thread-safe wrapper for pm.send(). Sets valid=True for SubMaster."""
  dat.valid = True
  with pub_lock:
    pm.send('yoloObjectData', dat)


def _publish_detections(pm, frame_id, inference_ms, objects, rtt_ms=0, frame_meta=None):
  """Build and publish a yoloObjectData cereal message with detection results."""
  global last_detect_pub_time
  try:
    detections = [_normalize_detection(obj, frame_meta) for obj in objects]
    detections = [det for det in detections if _valid_detection(det)]

    dat = messaging.new_message('yoloObjectData')
    yolo = dat.yoloObjectData
    yolo.frameId = frame_id
    yolo.inferenceTimeMs = inference_ms
    yolo.numDetections = len(detections)
    yolo.roundTripMs = rtt_ms

    has_red = False
    has_green = False
    primary = None

    if detections:
      for det in detections:
        if det["conf"] < MIN_TRAFFIC_LIGHT_CONF:
          continue
        if det["state"] == "red":
          has_red = True
        elif det["state"] == "green":
          has_green = True

      red_dets = [det for det in detections if det["state"] == "red"]
      signal_dets = [det for det in detections if det["state"] in ("red", "yellow", "green")]
      primary_pool = red_dets or signal_dets or detections
      primary = max(primary_pool, key=lambda det: det["conf"])

    yolo.hasRedLight = has_red
    yolo.hasGreenLight = has_green
    if primary is not None:
      yolo.distanceEstimate = primary["dist"]
      yolo.yoloClass = primary["name"]
      yolo.x = primary["x"]
      yolo.y = primary["y"]
      yolo.w = primary["w"]
      yolo.h = primary["h"]

    dets = yolo.init('detections', len(detections))
    for i, det in enumerate(detections):
      dets[i].classId = det["class_id"]
      dets[i].className = det["name"]
      dets[i].confidence = det["conf"]
      dets[i].x = det["x"]
      dets[i].y = det["y"]
      dets[i].w = det["w"]
      dets[i].h = det["h"]
      dets[i].distanceEstimate = det["dist"]

    _safe_publish(pm, dat)
    with lock:
      last_detect_pub_time = time.monotonic()
  except Exception as e:
    cloudlog.error(f"[YOLO] Publish error: {e}")


# ── Receiver thread ──────────────────────────────────────────────────

def yolo_receiver(pm):
  global TARGET_IP, last_hello_time, last_result_time, results_received
  sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
  _configure_udp_socket(sock)
  sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
  sock.bind(('0.0.0.0', RESULT_PORT))
  sock.settimeout(1.0)

  ylog(f"[YOLO] Receiver bound UDP 0.0.0.0:{RESULT_PORT}")
  timeout_count = 0

  while True:
    try:
      data, addr = sock.recvfrom(65535)
      timeout_count = 0
      now = time.monotonic()

      msg = json.loads(data.decode('utf-8'))

      with lock:
        if TARGET_IP is None or TARGET_IP != addr[0]:
          ylog(f"[YOLO] Phone discovered: {addr[0]}:{addr[1]} ({len(data)}B)")
          TARGET_IP = addr[0]

      if msg.get("cmd") == "hello":
        with lock:
          last_hello_time = now
        continue

      # Actual detection result
      with lock:
        last_result_time = now
        last_hello_time = now  # results also keep connection alive

      objects = msg.get('objects', [])
      frame_id = int(msg.get('frame_id', 0) or 0)
      inference_ms = int(msg.get('inference_ms', 0) or 0)
      decode_ms = int(msg.get('decode_ms', 0) or 0)
      queue_ms = int(msg.get('queue_ms', 0) or 0)
      phone_total_ms = int(msg.get('phone_total_ms', inference_ms) or inference_ms)

      # Compute round-trip time (frame sent → result received)
      with lock:
        results_received += 1
        send_ns = frame_send_times.pop(frame_id, None)
        frame_meta = frame_metas.pop(frame_id, DEFAULT_FRAME_META)
      rtt_ms = int((time.monotonic() * 1e9 - send_ns) / 1e6) if send_ns else 0
      link_ms = max(0, rtt_ms - phone_total_ms) if rtt_ms else 0

      if results_received <= 5 or results_received % 50 == 0:
        rtt_str = f" rtt={rtt_ms}ms" if rtt_ms else ""
        app_str = f" app={phone_total_ms}ms q={queue_ms}ms dec={decode_ms}ms"
        link_str = f" link={link_ms}ms" if link_ms else ""
        ylog(f"[YOLO] Result #{results_received}: frame={frame_id}, "
             f"inf={inference_ms}ms{app_str}{rtt_str}{link_str}, "
             f"{len(objects)} obj from {addr[0]}")

      _publish_detections(pm, frame_id, inference_ms, objects, rtt_ms, frame_meta)

    except socket.timeout:
      timeout_count += 1
      if timeout_count % 10 == 0:
        with lock:
          cloudlog.info(f"[YOLO] Receiver: no data {timeout_count}s "
                        f"(target={TARGET_IP}, rx={results_received})")
      with lock:
        # Only timeout if no hello AND no results for CONNECTION_TIMEOUT
        last_any = max(last_hello_time, last_result_time)
        if TARGET_IP and last_any > 0 and \
           (time.monotonic() - last_any) > CONNECTION_TIMEOUT:
          ylog("[YOLO] Connection timeout, resetting TARGET_IP")
          TARGET_IP = None
          results_received = 0
    except Exception as e:
      cloudlog.error(f"[YOLO] Receiver error: {e}")


# ── Heartbeat thread ─────────────────────────────────────────────────

def yolo_heartbeat(pm):
  """Always publish yoloObjectData every 1s so C++ SubMaster valid() is true.
  Skips publish when detection results were recently published (within 2s)
  to avoid overwriting actual detection info with numDetections=0."""
  ylog("[YOLO] Heartbeat started")
  hb_count = 0
  while True:
    try:
      with lock:
        connected = TARGET_IP is not None
        target = TARGET_IP
        tx = frames_sent
        rx = results_received
        detect_age = time.monotonic() - last_detect_pub_time

      # Skip heartbeat if receiver is actively publishing detections
      if detect_age < 2.0:
        hb_count += 1
        time.sleep(1.0)
        continue

      dat = messaging.new_message('yoloObjectData')
      yolo = dat.yoloObjectData
      yolo.numDetections = 0

      if connected:
        yolo.yoloClass = f"connected|{backend_name}|tx={tx}|rx={rx}"
      else:
        yolo.yoloClass = f"waiting|{backend_name}|{streamer_status}"

      _safe_publish(pm, dat)
      hb_count += 1

      if hb_count <= 5 or hb_count % 30 == 0:
        status = f"phone={target}" if connected else "phone=none"
        ylog(f"[YOLO] HB#{hb_count}: {status}, tx={tx}, rx={rx}")
    except Exception as e:
      ylog(f"[YOLO] Heartbeat ERROR: {e}")
    time.sleep(1.0)


# ── Discovery thread ─────────────────────────────────────────────────

def yolo_discovery():
  """Broadcast hello on FRAME_PORT so the phone discovers comma's IP."""
  sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
  _configure_udp_socket(sock)
  sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
  hello = b'{"cmd":"hello"}'

  cloudlog.info(f"[YOLO] Discovery started (port {FRAME_PORT})")

  while True:
    with lock:
      connected = TARGET_IP is not None

    if not connected:
      try:
        sock.sendto(hello, ('255.255.255.255', FRAME_PORT))
        for subnet in ['192.168.43.255', '192.168.49.255',
                       '192.168.0.255', '192.168.1.255']:
          try:
            sock.sendto(hello, (subnet, FRAME_PORT))
          except Exception:
            pass
      except Exception as e:
        cloudlog.warning(f"[YOLO] Discovery error: {e}")
      time.sleep(2.0)
    else:
      time.sleep(5.0)


# ── Streamer thread ──────────────────────────────────────────────────

def yolo_streamer():
  global frames_sent, streamer_status

  if _img_backend is None:
    streamer_status = "no_backend"
    cloudlog.error("[YOLO] Streamer DISABLED: no image backend")
    return

  try:
    from msgq.visionipc import VisionIpcClient, VisionStreamType
  except ImportError as e:
    streamer_status = "no_vipc"
    cloudlog.error(f"[YOLO] Streamer DISABLED: no VisionIPC: {e}")
    return

  vipc_client = VisionIpcClient("camerad", VisionStreamType.VISION_STREAM_ROAD, True)
  sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
  _configure_udp_socket(sock)

  streamer_status = "waiting"
  cloudlog.info("[YOLO] Streamer ready, waiting for phone...")

  while True:
    with lock:
      target = TARGET_IP

    if not target:
      time.sleep(1.0)
      continue

    if not vipc_client.is_connected():
      streamer_status = "vipc_connecting"
      cloudlog.info("[YOLO] Connecting to camerad VisionIPC...")
      vipc_client.connect(True)
      time.sleep(0.1)
      continue

    streamer_status = "streaming"
    img_data = vipc_client.recv()
    if img_data is None:
      continue

    try:
      width = img_data.width
      height = img_data.height
      stride = getattr(img_data, 'stride', width)
      uv_offset = getattr(img_data, 'uv_offset', stride * height)
      data_bytes = img_data.data

      if data_bytes is None or len(data_bytes) == 0:
        continue

      yuv_flat = np.frombuffer(data_bytes, dtype=np.uint8)

      # Resize NV12 first (cheap), THEN convert small image to RGB.
      # Letterbox keeps the traffic light aspect ratio aligned with YOLO training/export.
      rgb_small, frame_meta = _prepare_yolo_rgb(yuv_flat, height, width, stride, uv_offset)
      jpeg_bytes = _img_backend(rgb_small)

      if frames_sent == 0:
        ylog(f"[YOLO] VisionBuf: {width}x{height}, stride={stride}, "
             f"uv_offset={uv_offset}, data={len(yuv_flat)}B, "
             f"roi=({frame_meta['crop_x']:.0f},{frame_meta['crop_y']:.0f},"
             f"{frame_meta['crop_w']:.0f},{frame_meta['crop_h']:.0f}) "
             f"letterbox={frame_meta['letterboxed']} pad=({frame_meta['pad_x']:.0f},{frame_meta['pad_y']:.0f})")

      if jpeg_bytes and len(jpeg_bytes) < 65000:
        frames_sent += 1
        frame_id = frames_sent
        timestamp_ns = int(time.monotonic() * 1e9)
        header = struct.pack('<QQ', frame_id, timestamp_ns)

        # Track send time for RTT measurement (receiver thread reads this)
        with lock:
          frame_send_times[frame_id] = timestamp_ns
          frame_metas[frame_id] = frame_meta
          if len(frame_send_times) > MAX_RTT_TRACKED:
            oldest = min(frame_send_times.keys())
            del frame_send_times[oldest]
            frame_metas.pop(oldest, None)

        sock.sendto(header + jpeg_bytes, (target, FRAME_PORT))

        if frames_sent <= 3 or frames_sent % 100 == 0:
          ylog(f"[YOLO] Frame #{frames_sent} ({len(jpeg_bytes)}B) -> {target}")
      elif jpeg_bytes:
        cloudlog.warning(f"[YOLO] Frame too large: {len(jpeg_bytes)}B, skipped")
    except Exception as e:
      ylog(f"[YOLO] Streamer error: {e}")

    time.sleep(1.0 / FPS)


# ── Main ─────────────────────────────────────────────────────────────

def main():
  ylog(f"[YOLO] ========== yolod {VERSION} starting ==========")
  _setup_backend()

  try:
    pm = messaging.PubMaster(['yoloObjectData'])
    ylog("[YOLO] PubMaster created OK")
  except Exception as e:
    ylog(f"[YOLO] PubMaster FAILED: {e}")
    return

  # Test publish immediately
  try:
    test_msg = messaging.new_message('yoloObjectData')
    test_msg.valid = True
    test_msg.yoloObjectData.yoloClass = "test"
    pm.send('yoloObjectData', test_msg)
    ylog("[YOLO] Test publish OK")
  except Exception as e:
    ylog(f"[YOLO] Test publish FAILED: {e}")

  threads = [
    ("receiver", lambda: yolo_receiver(pm)),
    ("heartbeat", lambda: yolo_heartbeat(pm)),
    ("discovery", yolo_discovery),
    ("streamer", yolo_streamer),
  ]

  for name, func in threads:
    t = threading.Thread(target=func, daemon=True, name=f"yolo_{name}")
    t.start()
    ylog(f"[YOLO] Thread '{name}' started")

  ylog(f"[YOLO] All {len(threads)} threads running")

  while True:
    time.sleep(10.0)
    with lock:
      ylog(f"[YOLO] Status: target={TARGET_IP}, backend={backend_name}, "
           f"streamer={streamer_status}, tx={frames_sent}, rx={results_received}")


if __name__ == "__main__":
  main()
