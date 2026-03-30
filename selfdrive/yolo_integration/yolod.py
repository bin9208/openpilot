#!/usr/bin/env python3
import json
import socket
import threading
import time
import numpy as np
import cv2

import cereal.messaging as messaging
from msgq.visionipc import VisionIpcClient, VisionStreamType
from openpilot.common.realtime import Ratekeeper

UDP_PORT = 8080
TARGET_IP = None
JPEG_QUALITY = 30 # Low quality for max speed
FPS = 10.0

def yolo_receiver():
  global TARGET_IP
  sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
  sock.bind(('0.0.0.0', UDP_PORT))
  sock.settimeout(1.0)
  
  pm = messaging.PubMaster(['yoloObjectData'])

  print(f"[YOLO] Receiver listening on UDP port {UDP_PORT}")

  while True:
    try:
      data, addr = sock.recvfrom(4096)
      if TARGET_IP != addr[0]:
        print(f"[YOLO] Client connected from {addr[0]}")
        TARGET_IP = addr[0]

      msg = json.loads(data.decode('utf-8'))
      if msg.get("cmd") == "hello":
        continue

      has_red = False
      dist_est = 0.0
      yolo_class = ""
      primary_x = 0.0
      primary_y = 0.0

      if 'objects' in msg:
        for obj in msg['objects']:
          ob_class = obj['class']
          if ob_class == 'red_light' or ob_class == 'traffic light':
            has_red = True
            yolo_class = ob_class
            dist_est = float(obj.get('distance', 10.0))
            primary_x, primary_y = float(obj.get('x', 0)), float(obj.get('y', 0))
            break

      dat = messaging.new_message('yoloObjectData')
      dat.yoloObjectData.hasRedLight = has_red
      dat.yoloObjectData.distanceEstimate = dist_est
      dat.yoloObjectData.yoloClass = yolo_class
      dat.yoloObjectData.x = primary_x
      dat.yoloObjectData.y = primary_y
      
      pm.send('yoloObjectData', dat)

    except socket.timeout:
      pass
    except Exception as e:
      print(f"[YOLO] Receiver error: {e}")

def yolo_streamer():
  global TARGET_IP
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
    
    # We poll recv until we get a fresh frame (blocking)
    img_data = vipc_client.recv()
    if img_data is None or not img_data.data.any():
       continue

    try:
      # Openpilot uses NV12 format for VISION_STREAM_ROAD
      # The data array size is width * height * 1.5
      yuv = np.frombuffer(img_data.data, dtype=np.uint8).reshape((img_data.height * 3 // 2, img_data.width))
      rgb = cv2.cvtColor(yuv, cv2.COLOR_YUV2BGR_NV12)
      
      # Resize to 640x640 for YOLO input
      resized = cv2.resize(rgb, (640, 640))
      
      # Encode to JPEG
      encode_param = [int(cv2.IMWRITE_JPEG_QUALITY), JPEG_QUALITY]
      result, encimg = cv2.imencode('.jpg', resized, encode_param)
      
      if result:
        # Send over UDP. (Max UDP packet is ~65KB. A 640x640 30-quality JPEG is usually < 20KB)
        sock.sendto(encimg.tobytes(), (TARGET_IP, UDP_PORT))
    except Exception as e:
      print(f"[YOLO Streamer] Error: {e}")
      
    rk.keep_time()

def main():
  t1 = threading.Thread(target=yolo_receiver)
  t2 = threading.Thread(target=yolo_streamer)

  t1.start()
  t2.start()

  t1.join()
  t2.join()

if __name__ == "__main__":
  main()
