#!/usr/bin/env python3
import json
import socket
import struct
import threading
import time
import numpy as np
import cv2

import cereal.messaging as messaging
from msgq.visionipc import VisionIpcClient, VisionStreamType
from openpilot.common.realtime import Ratekeeper

FRAME_PORT = 8080
RESULT_PORT = 8081
JPEG_QUALITY = 50
FPS = 20.0
CONNECTION_TIMEOUT = 3.0

TARGET_IP = None
last_result_time = 0.0
frame_counter = 0


def yolo_receiver():
  global TARGET_IP, last_result_time
  sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
  sock.bind(('0.0.0.0', RESULT_PORT))
  sock.settimeout(1.0)

  pm = messaging.PubMaster(['yoloObjectData'])

  print(f"[YOLO] Receiver listening on UDP port {RESULT_PORT}")

  while True:
    try:
      data, addr = sock.recvfrom(65535)
      if TARGET_IP is None or TARGET_IP != addr[0]:
        print(f"[YOLO] Client connected from {addr[0]}")
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

      # Backward-compatible fields from primary detection
      has_red = False
      has_green = False
      primary_dist = 0.0
      primary_class = ""
      primary_x = 0.0
      primary_y = 0.0
      primary_w = 0.0
      primary_h = 0.0

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

        # If no traffic light found, use first detection as primary
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
      # Check connection timeout
      if TARGET_IP and (time.monotonic() - last_result_time) > CONNECTION_TIMEOUT:
        print("[YOLO] Connection timeout, waiting for reconnect...")
        TARGET_IP = None
    except Exception as e:
      print(f"[YOLO] Receiver error: {e}")


def yolo_streamer():
  global TARGET_IP, frame_counter
  vipc_client = VisionIpcClient("camerad", VisionStreamType.VISION_STREAM_ROAD, True)
  sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
  rk = Ratekeeper(FPS)

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
      yuv = np.frombuffer(img_data.data, dtype=np.uint8).reshape((img_data.height * 3 // 2, img_data.width))
      rgb = cv2.cvtColor(yuv, cv2.COLOR_YUV2BGR_NV12)
      resized = cv2.resize(rgb, (640, 640))

      encode_param = [int(cv2.IMWRITE_JPEG_QUALITY), JPEG_QUALITY]
      result, encimg = cv2.imencode('.jpg', resized, encode_param)

      if result:
        frame_counter += 1
        timestamp_ns = int(time.monotonic() * 1e9)
        header = struct.pack('<QQ', frame_counter, timestamp_ns)
        packet = header + encimg.tobytes()

        # UDP max ~65KB, JPEG at Q50 640x640 is typically < 35KB + 16B header
        if len(packet) < 65000:
          sock.sendto(packet, (TARGET_IP, FRAME_PORT))
    except Exception as e:
      print(f"[YOLO Streamer] Error: {e}")

    rk.keep_time()


def main():
  t1 = threading.Thread(target=yolo_receiver, daemon=True)
  t2 = threading.Thread(target=yolo_streamer, daemon=True)

  t1.start()
  t2.start()

  t1.join()
  t2.join()


if __name__ == "__main__":
  main()
