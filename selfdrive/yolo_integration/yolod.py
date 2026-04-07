#!/usr/bin/env python3
"""
yolod: streams camera frames to Galaxy Fold 7 via UDP,
receives YOLO detection results, publishes as cereal yoloObjectData.

Connection flow:
  1. Discovery: yolod broadcasts hello → phone:8080
  2. Phone replies hello → yolod:8081 → TARGET_IP learned
  3. Streamer: yolod sends JPEG frames → phone:8080
  4. Phone runs YOLO → sends JSON results → yolod:8081
  5. Receiver publishes cereal yoloObjectData
  6. Heartbeat: publishes empty yoloObjectData every 1s to keep UI alive
"""
import json
import socket
import struct
import subprocess
import threading
import time
import numpy as np

import cereal.messaging as messaging
from openpilot.common.swaglog import cloudlog

FRAME_PORT = 8080
RESULT_PORT = 8081
JPEG_QUALITY = 50
FPS = 10.0  # reduced from 20 to lower CPU load
CONNECTION_TIMEOUT = 5.0

# Shared state
TARGET_IP = None
last_result_time = 0.0
frame_counter = 0
frames_sent = 0
results_received = 0
backend_name = "none"
streamer_status = "init"
lock = threading.Lock()


# ── Image backends ────────────────────────────────────────────────────

def _nv12_to_rgb_strided(yuv_flat, height, width, stride, uv_offset):
  """Convert NV12 with stride/uv_offset to RGB.
  Y plane may have padding rows (e.g. 1216 rows for height=1208)."""
  # Y plane: use uv_offset // stride to get actual row count (includes padding rows)
  y_rows = uv_offset // stride
  y_plane = yuv_flat[:y_rows * stride].reshape(y_rows, stride)[:height, :width].astype(np.float32)

  # UV plane: similarly handle padding
  uv_data = yuv_flat[uv_offset:]
  uv_rows = len(uv_data) // stride
  uv_plane = uv_data[:uv_rows * stride].reshape(uv_rows, stride)[:height // 2, :width]
  U = uv_plane[:, 0::2].astype(np.float32)
  V = uv_plane[:, 1::2].astype(np.float32)

  U_full = np.repeat(np.repeat(U, 2, axis=0), 2, axis=1)[:height, :width]
  V_full = np.repeat(np.repeat(V, 2, axis=0), 2, axis=1)[:height, :width]

  R = np.clip(y_plane + 1.402 * (V_full - 128), 0, 255)
  G = np.clip(y_plane - 0.344136 * (U_full - 128) - 0.714136 * (V_full - 128), 0, 255)
  B = np.clip(y_plane + 1.772 * (U_full - 128), 0, 255)
  return np.stack([R, G, B], axis=2).astype(np.uint8)


def _nv12_to_rgb_simple(yuv_flat, height, width):
  """Convert NV12 without stride (stride == width) to RGB."""
  y_size = width * height
  Y = yuv_flat[:y_size].reshape(height, width).astype(np.float32)
  uv = yuv_flat[y_size:y_size + width * (height // 2)].reshape(height // 2, width)
  U = uv[:, 0::2].astype(np.float32)
  V = uv[:, 1::2].astype(np.float32)
  U_full = np.repeat(np.repeat(U, 2, axis=0), 2, axis=1)[:height, :width]
  V_full = np.repeat(np.repeat(V, 2, axis=0), 2, axis=1)[:height, :width]
  R = np.clip(Y + 1.402 * (V_full - 128), 0, 255)
  G = np.clip(Y - 0.344136 * (U_full - 128) - 0.714136 * (V_full - 128), 0, 255)
  B = np.clip(Y + 1.772 * (U_full - 128), 0, 255)
  return np.stack([R, G, B], axis=2).astype(np.uint8)


def _numpy_resize(img, target_h, target_w):
  h, w = img.shape[:2]
  y_idx = (np.arange(target_h) * h / target_h).astype(int)
  x_idx = (np.arange(target_w) * w / target_w).astype(int)
  return img[np.ix_(y_idx, x_idx)]


_img_backend = None

def _setup_backend():
  global _img_backend, backend_name

  # Try PIL/Pillow (preferred - lightweight, pure Python)
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

  # Try cv2
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

  # Fallback: raw numpy to JPEG via ffmpeg
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
        proc = subprocess.run(cmd, input=rgb.tobytes(),
                              capture_output=True, timeout=2)
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
  cloudlog.error("[YOLO] NO IMAGE BACKEND FOUND. Need cv2, Pillow, or ffmpeg. Frame streaming DISABLED.")


# ── Receiver thread ───────────────────────────────────────────────────

def yolo_receiver(pm):
  global TARGET_IP, last_result_time, results_received
  sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
  sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
  sock.bind(('0.0.0.0', RESULT_PORT))
  sock.settimeout(1.0)

  cloudlog.info(f"[YOLO] Receiver listening on UDP port {RESULT_PORT}")

  while True:
    try:
      data, addr = sock.recvfrom(65535)
      with lock:
        if TARGET_IP is None or TARGET_IP != addr[0]:
          cloudlog.info(f"[YOLO] Phone discovered: {addr[0]}")
          TARGET_IP = addr[0]
        last_result_time = time.monotonic()

      msg = json.loads(data.decode('utf-8'))

      if msg.get("cmd") == "hello":
        cloudlog.info(f"[YOLO] Hello from phone {addr[0]}")
        continue

      objects = msg.get('objects', [])
      frame_id = msg.get('frame_id', 0)
      inference_ms = msg.get('inference_ms', 0)

      with lock:
        results_received += 1

      if results_received <= 3 or results_received % 50 == 0:
        cloudlog.info(f"[YOLO] Result #{results_received}: frame={frame_id}, {inference_ms}ms, {len(objects)} objects")

      # Build cereal message
      _publish_detections(pm, frame_id, inference_ms, objects)

    except socket.timeout:
      with lock:
        if TARGET_IP and last_result_time > 0 and (time.monotonic() - last_result_time) > CONNECTION_TIMEOUT:
          cloudlog.info("[YOLO] Connection timeout, resetting...")
          TARGET_IP = None
          results_received = 0
    except Exception as e:
      cloudlog.error(f"[YOLO] Receiver error: {e}")


def _publish_detections(pm, frame_id, inference_ms, objects):
  """Build and publish a yoloObjectData cereal message."""
  try:
    dat = messaging.new_message('yoloObjectData')
    yolo = dat.yoloObjectData
    yolo.frameId = frame_id
    yolo.inferenceTimeMs = inference_ms
    yolo.numDetections = len(objects)

    has_red = False
    has_green = False
    primary_dist = 0.0
    primary_class = ""
    primary_x = primary_y = primary_w = primary_h = 0.0

    if objects:
      for obj in objects:
        name = obj.get('name', '')
        if name == 'red_light' or (obj.get('cls', -1) == 9 and 'green' not in name):
          has_red = True
          primary_dist = float(obj.get('dist', 10.0))
          primary_class = name
          primary_x = float(obj.get('x', 0))
          primary_y = float(obj.get('y', 0))
          primary_w = float(obj.get('w', 0))
          primary_h = float(obj.get('h', 0))
          break
        elif name == 'green_light':
          has_green = True

      if not has_red and not has_green and not primary_class:
        obj = objects[0]
        primary_class = obj.get('name', '')
        primary_dist = float(obj.get('dist', 0))
        primary_x = float(obj.get('x', 0))
        primary_y = float(obj.get('y', 0))
        primary_w = float(obj.get('w', 0))
        primary_h = float(obj.get('h', 0))

    yolo.hasRedLight = has_red
    yolo.hasGreenLight = has_green
    yolo.distanceEstimate = primary_dist
    yolo.yoloClass = primary_class
    yolo.x = primary_x
    yolo.y = primary_y
    yolo.w = primary_w
    yolo.h = primary_h

    dets = yolo.init('detections', len(objects))
    for i, obj in enumerate(objects):
      dets[i].classId = int(obj.get('cls', 0))
      dets[i].className = obj.get('name', '')
      dets[i].confidence = float(obj.get('conf', 0))
      dets[i].x = float(obj.get('x', 0))
      dets[i].y = float(obj.get('y', 0))
      dets[i].w = float(obj.get('w', 0))
      dets[i].h = float(obj.get('h', 0))
      dets[i].distanceEstimate = float(obj.get('dist', 0))

    pm.send('yoloObjectData', dat)
  except Exception as e:
    cloudlog.error(f"[YOLO] Publish error: {e}")


# ── Heartbeat thread ─────────────────────────────────────────────────

def yolo_heartbeat(pm):
  """Publish empty yoloObjectData every second when phone is connected.
     This keeps sm.alive('yoloObjectData') true for the UI indicator."""
  cloudlog.info("[YOLO] Heartbeat thread started")
  while True:
    with lock:
      connected = TARGET_IP is not None
    if connected:
      try:
        dat = messaging.new_message('yoloObjectData')
        yolo = dat.yoloObjectData
        yolo.numDetections = 0
        yolo.yoloClass = f"hb|{backend_name}|tx={frames_sent}|rx={results_received}"
        pm.send('yoloObjectData', dat)
      except Exception as e:
        cloudlog.error(f"[YOLO] Heartbeat error: {e}")
    time.sleep(1.0)


# ── Discovery thread ─────────────────────────────────────────────────

def yolo_discovery():
  """Broadcast hello on FRAME_PORT so the phone discovers comma's IP."""
  sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
  sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
  hello = b'{"cmd":"hello"}'

  cloudlog.info(f"[YOLO] Discovery broadcaster started (port {FRAME_PORT})")

  while True:
    with lock:
      connected = TARGET_IP is not None

    if not connected:
      try:
        sock.sendto(hello, ('255.255.255.255', FRAME_PORT))
        for subnet in ['192.168.43.255', '192.168.49.255', '192.168.0.255', '192.168.1.255']:
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
    cloudlog.error("[YOLO] Streamer DISABLED: no image backend available")
    return

  try:
    from msgq.visionipc import VisionIpcClient, VisionStreamType
  except ImportError as e:
    streamer_status = "no_vipc"
    cloudlog.error(f"[YOLO] Streamer DISABLED: VisionIPC not available: {e}")
    return

  vipc_client = VisionIpcClient("camerad", VisionStreamType.VISION_STREAM_ROAD, True)
  sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

  streamer_status = "waiting"
  cloudlog.info("[YOLO] Streamer initialized, waiting for connection...")

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

      # Log buffer info on first frame
      if frames_sent == 0:
        cloudlog.info(f"[YOLO] VisionBuf: {width}x{height}, stride={stride}, "
                       f"uv_offset={uv_offset}, data={len(yuv_flat)} bytes")

      # Convert NV12 to RGB (with stride handling)
      if stride > width:
        rgb = _nv12_to_rgb_strided(yuv_flat, height, width, stride, uv_offset)
      else:
        rgb = _nv12_to_rgb_simple(yuv_flat, height, width)

      # Resize to 640x640
      rgb_small = _numpy_resize(rgb, 640, 640)

      # Encode to JPEG
      jpeg_bytes = _img_backend(rgb_small)

      if jpeg_bytes and len(jpeg_bytes) < 65000:
        frames_sent += 1
        timestamp_ns = int(time.monotonic() * 1e9)
        header = struct.pack('<QQ', frames_sent, timestamp_ns)
        sock.sendto(header + jpeg_bytes, (target, FRAME_PORT))

        if frames_sent <= 3 or frames_sent % 100 == 0:
          cloudlog.info(f"[YOLO] Frame #{frames_sent} sent ({len(jpeg_bytes)} bytes) to {target}")
      elif jpeg_bytes:
        cloudlog.warning(f"[YOLO] Frame too large: {len(jpeg_bytes)} bytes, skipped")
    except Exception as e:
      cloudlog.error(f"[YOLO] Streamer error: {e}")

    time.sleep(1.0 / FPS)


# ── Main ─────────────────────────────────────────────────────────────

def main():
  cloudlog.info("[YOLO] ========== yolod starting ==========")
  _setup_backend()
  cloudlog.info(f"[YOLO] Backend: {backend_name}")

  pm = messaging.PubMaster(['yoloObjectData'])

  threads = [
    ("receiver", lambda: yolo_receiver(pm)),
    ("heartbeat", lambda: yolo_heartbeat(pm)),
    ("discovery", yolo_discovery),
    ("streamer", yolo_streamer),
  ]

  started = []
  for name, func in threads:
    t = threading.Thread(target=func, daemon=True, name=f"yolo_{name}")
    t.start()
    started.append(t)
    cloudlog.info(f"[YOLO] Thread '{name}' started")

  cloudlog.info(f"[YOLO] All {len(started)} threads running")

  # Keep main alive
  while True:
    time.sleep(10.0)
    with lock:
      cloudlog.info(f"[YOLO] Status: target={TARGET_IP}, backend={backend_name}, "
                     f"streamer={streamer_status}, tx={frames_sent}, rx={results_received}")


if __name__ == "__main__":
  main()
