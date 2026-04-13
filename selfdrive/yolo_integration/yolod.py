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
import socket
import struct
import subprocess
import threading
import time
import numpy as np

import cereal.messaging as messaging
from openpilot.common.swaglog import cloudlog

VERSION = "v6"

def ylog(msg):
  """Log to both cloudlog AND stdout (tmux visible)."""
  cloudlog.info(msg)
  print(msg, flush=True)
FRAME_PORT = 8080
RESULT_PORT = 8081
JPEG_QUALITY = 50
FPS = 10.0
CONNECTION_TIMEOUT = 15.0
YOLO_INPUT_SIZE = 640      # YOLO model input resolution (square)

# Shared state (protected by lock)
TARGET_IP = None
last_hello_time = 0.0      # last hello from phone (keeps TARGET_IP alive)
last_result_time = 0.0     # last actual detection result
last_detect_pub_time = 0.0 # last time _publish_detections was called
frames_sent = 0
results_received = 0
backend_name = "none"
streamer_status = "init"
frame_send_times = {}      # frame_id → monotonic_ns (for RTT measurement)
MAX_RTT_TRACKED = 100      # keep at most N entries
lock = threading.Lock()
pub_lock = threading.Lock()  # protects pm.send() across threads


# ── NV12 conversion ─────────────────────────────────────────────────

def _resize_nv12(yuv_flat, src_h, src_w, src_stride, uv_offset, dst_size):
  """Resize NV12 Y+UV planes to dst_size×dst_size BEFORE RGB conversion.
  Handles strided/height-padded source (stride=2048, y_rows=1216 for height=1208).
  Returns (y_small, uv_small) as uint8 arrays ready for _nv12_small_to_rgb()."""
  # Y plane: crop out stride padding and height padding
  y_rows = uv_offset // src_stride
  y_plane = yuv_flat[:uv_offset].reshape(y_rows, src_stride)[:src_h, :src_w]

  # UV plane: interleaved U/V, half height
  uv_data = yuv_flat[uv_offset:]
  uv_rows = len(uv_data) // src_stride
  uv_plane = uv_data[:uv_rows * src_stride].reshape(uv_rows, src_stride)[:src_h // 2, :src_w]

  # Nearest-neighbor resize (same column indices for Y and UV)
  r_y  = (np.arange(dst_size)       * src_h        / dst_size).astype(np.int32)
  c    = (np.arange(dst_size)       * src_w        / dst_size).astype(np.int32)
  r_uv = (np.arange(dst_size // 2) * (src_h // 2) / (dst_size // 2)).astype(np.int32)

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
  R = np.clip(Y + 1.402  * (V_f - 128),                                   0, 255).astype(np.uint8)
  G = np.clip(Y - 0.344136 * (U_f - 128) - 0.714136 * (V_f - 128),       0, 255).astype(np.uint8)
  B = np.clip(Y + 1.772  * (U_f - 128),                                   0, 255).astype(np.uint8)
  return np.stack([R, G, B], axis=2)


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

def _safe_publish(pm, dat):
  """Thread-safe wrapper for pm.send(). Sets valid=True for SubMaster."""
  dat.valid = True
  with pub_lock:
    pm.send('yoloObjectData', dat)


def _publish_detections(pm, frame_id, inference_ms, objects, rtt_ms=0):
  """Build and publish a yoloObjectData cereal message with detection results."""
  global last_detect_pub_time
  try:
    dat = messaging.new_message('yoloObjectData')
    yolo = dat.yoloObjectData
    yolo.frameId = frame_id
    yolo.inferenceTimeMs = inference_ms
    yolo.numDetections = len(objects)
    yolo.roundTripMs = rtt_ms

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

    _safe_publish(pm, dat)
    with lock:
      last_detect_pub_time = time.monotonic()
  except Exception as e:
    cloudlog.error(f"[YOLO] Publish error: {e}")


# ── Receiver thread ──────────────────────────────────────────────────

def yolo_receiver(pm):
  global TARGET_IP, last_hello_time, last_result_time, results_received
  sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
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
      frame_id = msg.get('frame_id', 0)
      inference_ms = msg.get('inference_ms', 0)

      # Compute round-trip time (frame sent → result received)
      with lock:
        results_received += 1
        send_ns = frame_send_times.pop(frame_id, None)
      rtt_ms = int((time.monotonic() * 1e9 - send_ns) / 1e6) if send_ns else 0

      if results_received <= 5 or results_received % 50 == 0:
        rtt_str = f" rtt={rtt_ms}ms" if rtt_ms else ""
        ylog(f"[YOLO] Result #{results_received}: frame={frame_id}, "
             f"{inference_ms}ms{rtt_str}, {len(objects)} obj from {addr[0]}")

      _publish_detections(pm, frame_id, inference_ms, objects, rtt_ms)

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

      if frames_sent == 0:
        ylog(f"[YOLO] VisionBuf: {width}x{height}, stride={stride}, "
             f"uv_offset={uv_offset}, data={len(yuv_flat)}B")

      # Resize NV12 first (cheap), THEN convert small image to RGB
      y_small, uv_small = _resize_nv12(yuv_flat, height, width, stride, uv_offset, YOLO_INPUT_SIZE)
      rgb_small = _nv12_small_to_rgb(y_small, uv_small)
      jpeg_bytes = _img_backend(rgb_small)

      if jpeg_bytes and len(jpeg_bytes) < 65000:
        frames_sent += 1
        timestamp_ns = int(time.monotonic() * 1e9)
        header = struct.pack('<QQ', frames_sent, timestamp_ns)
        sock.sendto(header + jpeg_bytes, (target, FRAME_PORT))

        # Track send time for RTT measurement (receiver thread reads this)
        with lock:
          frame_send_times[frames_sent] = timestamp_ns
          if len(frame_send_times) > MAX_RTT_TRACKED:
            oldest = min(frame_send_times.keys())
            del frame_send_times[oldest]

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
