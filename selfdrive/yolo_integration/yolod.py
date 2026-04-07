#!/usr/bin/env python3
import json
import socket
import struct
import subprocess
import threading
import time
import numpy as np

import cereal.messaging as messaging
from msgq.visionipc import VisionIpcClient, VisionStreamType
from openpilot.common.realtime import Ratekeeper
from openpilot.common.swaglog import cloudlog

FRAME_PORT = 8080
RESULT_PORT = 8081
JPEG_QUALITY = 50
FPS = 20.0
CONNECTION_TIMEOUT = 3.0

TARGET_IP = None
last_result_time = 0.0
frame_counter = 0

# Image encoding backend
_img_backend = None


def _nv12_to_rgb(yuv_flat, height, width):
  Y = yuv_flat[:height * width].reshape(height, width).astype(np.float32)
  uv = yuv_flat[height * width:].reshape(height // 2, width)
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


def _setup_backend():
  global _img_backend

  # Try cv2
  try:
    import cv2 as _cv2
    def _encode_cv2(yuv_flat, height, width):
      yuv_mat = yuv_flat.reshape((height * 3 // 2, width))
      rgb = _cv2.cvtColor(yuv_mat, _cv2.COLOR_YUV2BGR_NV12)
      resized = _cv2.resize(rgb, (640, 640))
      _, buf = _cv2.imencode('.jpg', resized, [_cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
      return buf.tobytes()
    _img_backend = _encode_cv2
    cloudlog.info("[YOLO] Image backend: cv2")
    return
  except Exception:
    pass

  # Try PIL/Pillow
  try:
    from PIL import Image as _Image
    import io as _io
    def _encode_pil(yuv_flat, height, width):
      rgb = _nv12_to_rgb(yuv_flat, height, width)
      rgb = _numpy_resize(rgb, 640, 640)
      img = _Image.fromarray(rgb)
      buf = _io.BytesIO()
      img.save(buf, format='JPEG', quality=JPEG_QUALITY)
      return buf.getvalue()
    _img_backend = _encode_pil
    cloudlog.info("[YOLO] Image backend: PIL")
    return
  except Exception:
    pass

  # Try ffmpeg subprocess (available on most Linux)
  try:
    r = subprocess.run(['ffmpeg', '-version'], capture_output=True, timeout=3)
    if r.returncode == 0:
      def _encode_ffmpeg(yuv_flat, height, width):
        cmd = [
          'ffmpeg', '-f', 'rawvideo', '-pix_fmt', 'nv12',
          '-s', f'{width}x{height}', '-i', 'pipe:0',
          '-vf', 'scale=640:640', '-q:v', '5',
          '-f', 'image2', '-vcodec', 'mjpeg', '-frames:v', '1',
          'pipe:1'
        ]
        proc = subprocess.run(cmd, input=yuv_flat.tobytes(),
                              capture_output=True, timeout=2)
        if proc.returncode == 0 and len(proc.stdout) > 0:
          return proc.stdout
        return None
      _img_backend = _encode_ffmpeg
      cloudlog.info("[YOLO] Image backend: ffmpeg")
      return
  except Exception:
    pass

  cloudlog.error("[YOLO] No image backend found (need cv2, Pillow, or ffmpeg). Streamer disabled.")


def yolo_receiver():
  global TARGET_IP, last_result_time
  sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
  sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
  sock.bind(('0.0.0.0', RESULT_PORT))
  sock.settimeout(1.0)

  pm = messaging.PubMaster(['yoloObjectData'])

  cloudlog.info(f"[YOLO] Receiver listening on UDP port {RESULT_PORT}")

  while True:
    try:
      data, addr = sock.recvfrom(65535)
      if TARGET_IP is None or TARGET_IP != addr[0]:
        cloudlog.info(f"[YOLO] Client connected from {addr[0]}")
        TARGET_IP = addr[0]

      last_result_time = time.monotonic()
      msg = json.loads(data.decode('utf-8'))

      if msg.get("cmd") == "hello":
        continue

      objects = msg.get('objects', [])
      frame_id = msg.get('frame_id', 0)
      inference_ms = msg.get('inference_ms', 0)

      # Build cereal message
      dat = messaging.new_message('yoloObjectData')
      yolo = dat.yoloObjectData
      yolo.frameId = frame_id
      yolo.inferenceTimeMs = inference_ms
      yolo.numDetections = len(objects)

      # Backward-compatible fields
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

      # Multi-object detections list
      detections = yolo.init('detections', len(objects))
      for i, obj in enumerate(objects):
        detections[i].classId = int(obj.get('cls', 0))
        detections[i].className = obj.get('name', '')
        detections[i].confidence = float(obj.get('conf', 0))
        detections[i].x = float(obj.get('x', 0))
        detections[i].y = float(obj.get('y', 0))
        detections[i].w = float(obj.get('w', 0))
        detections[i].h = float(obj.get('h', 0))
        detections[i].distanceEstimate = float(obj.get('dist', 0))

      pm.send('yoloObjectData', dat)

    except socket.timeout:
      if TARGET_IP and (time.monotonic() - last_result_time) > CONNECTION_TIMEOUT:
        cloudlog.info("[YOLO] Connection timeout, waiting for reconnect...")
        TARGET_IP = None
    except Exception as e:
      cloudlog.error(f"[YOLO] Receiver error: {e}")


def yolo_discovery():
  """Broadcast hello on FRAME_PORT so the phone discovers comma's IP."""
  global TARGET_IP
  sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
  sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
  hello = b'{"cmd":"hello"}'

  cloudlog.info("[YOLO] Discovery broadcaster started on port %d", FRAME_PORT)

  while True:
    if TARGET_IP is None:
      try:
        # Broadcast to all subnets
        sock.sendto(hello, ('255.255.255.255', FRAME_PORT))
        # Also try common hotspot subnets
        for subnet in ['192.168.43.255', '192.168.49.255', '192.168.0.255', '192.168.1.255']:
          try:
            sock.sendto(hello, (subnet, FRAME_PORT))
          except Exception:
            pass
      except Exception as e:
        cloudlog.warning(f"[YOLO] Discovery broadcast error: {e}")
      time.sleep(2.0)
    else:
      time.sleep(5.0)


def yolo_streamer():
  global TARGET_IP, frame_counter

  if _img_backend is None:
    cloudlog.error("[YOLO] Streamer not started: no image backend")
    return

  vipc_client = VisionIpcClient("camerad", VisionStreamType.VISION_STREAM_ROAD, True)
  sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
  rk = Ratekeeper(FPS)

  cloudlog.info("[YOLO] Streamer started")

  while True:
    if not TARGET_IP:
      time.sleep(1.0)
      continue

    if not vipc_client.is_connected():
      vipc_client.connect(True)
      time.sleep(0.1)
      continue

    img_data = vipc_client.recv()
    if img_data is None or not img_data.data.any():
      continue

    try:
      yuv_flat = np.frombuffer(img_data.data, dtype=np.uint8)
      jpeg_bytes = _img_backend(yuv_flat, img_data.height, img_data.width)

      if jpeg_bytes and len(jpeg_bytes) < 65000:
        frame_counter += 1
        timestamp_ns = int(time.monotonic() * 1e9)
        header = struct.pack('<QQ', frame_counter, timestamp_ns)
        sock.sendto(header + jpeg_bytes, (TARGET_IP, FRAME_PORT))
    except Exception as e:
      cloudlog.error(f"[YOLO] Streamer error: {e}")

    rk.keep_time()


def main():
  cloudlog.info("[YOLO] yolod starting...")
  _setup_backend()

  t_recv = threading.Thread(target=yolo_receiver, daemon=True)
  t_stream = threading.Thread(target=yolo_streamer, daemon=True)
  t_disc = threading.Thread(target=yolo_discovery, daemon=True)

  t_recv.start()
  t_stream.start()
  t_disc.start()

  cloudlog.info("[YOLO] All threads started (receiver, streamer, discovery)")

  # Keep main thread alive
  t_recv.join()


if __name__ == "__main__":
  main()
