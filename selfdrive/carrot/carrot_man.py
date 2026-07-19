try:
  import fcntl
except ImportError:
  fcntl = None
import json
import math
import os
import socket
import struct
import subprocess
import threading
import time
import numpy as np
import zmq
from datetime import datetime
import traceback
from typing import Any, Dict, List, Optional

from aiohttp import web
import asyncio

from ftplib import FTP
from cereal import log
import urllib.request
import urllib.error
import ssl
import requests
import psutil
import ipaddress
import cereal.messaging as messaging
from openpilot.common.realtime import Ratekeeper, set_core_affinity
from openpilot.common.params import Params, ParamKeyType
from openpilot.common.filter_simple import MyMovingAverage
from openpilot.system.hardware import PC, TICI
from openpilot.selfdrive.navd.helpers import Coordinate
from opendbc.car.common.conversions import Conversions as CV

from openpilot.selfdrive.carrot.carrot_serv import CarrotServ
from openpilot.selfdrive.carrot.navigation_lease import NavigationMux
from openpilot.selfdrive.carrot.navigation_protocol import (
  ManeuverKind,
  NavigationEnvelope,
  ProtocolError,
  ProviderSource,
  ReceivedFrame,
  Transport,
  TransportContext,
)

from openpilot.common.gps import get_gps_location_service

try:
  from shapely.geometry import LineString
  SHAPELY_AVAILABLE = True
except ImportError:
  SHAPELY_AVAILABLE = False

NetworkType = log.DeviceState.NetworkType

NAVIGATION_DISCOVERY = "carrot.navigation.discover"
NAVIGATION_DISCOVERY_RESPONSE = "carrot.navigation.discover.response"
NAVIGATION_SCHEMA_VERSION = 1
NAVIGATION_LEASE_MS = 4_000
NAVIGATION_TCP_PORT = 7712
NAVIGATION_MAX_CLIENTS = 4
NAVIGATION_READ_TIMEOUT = 5.0
NAVIGATION_MAX_LINE_BYTES = 262_144
NAVIGATION_ROUTE_BUFFER_MS = 1_000
NAVIGATION_MAX_PENDING_ROUTES = 8
NAVIGATION_MAX_BINARY_ROUTE_BYTES = 4_096 * 8
UINT32_MAX = 2**32 - 1

NAVIGATION_MANEUVER_CODES = {
  ManeuverKind.STRAIGHT: 0,
  ManeuverKind.LEFT: 12,
  ManeuverKind.RIGHT: 13,
  ManeuverKind.U_TURN: 14,
  ManeuverKind.FORK_LEFT: 7,
  ManeuverKind.FORK_RIGHT: 6,
  ManeuverKind.RAMP_LEFT: 102,
  ManeuverKind.RAMP_RIGHT: 101,
  ManeuverKind.ROUNDABOUT: 131,
  ManeuverKind.ARRIVE: 201,
}
NAVIGATION_CONTROL_SOURCES = (
  ProviderSource.TMAP,
  ProviderSource.NAVER,
  ProviderSource.TMAP_LEGACY,
)

################ CarrotNavi
## 국가법령정보센터: 도로설계기준
#V_CURVE_LOOKUP_BP = [0., 1./800., 1./670., 1./560., 1./440., 1./360., 1./265., 1./190., 1./135., 1./85., 1./55., 1./30., 1./15.]
#V_CRUVE_LOOKUP_VALS = [300, 150, 120, 110, 100, 90, 80, 70, 60, 50, 45, 35, 30]
V_CURVE_LOOKUP_BP = [0., 1./800., 1./670., 1./560., 1./440., 1./360., 1./265., 1./190., 1./135., 1./85., 1./55., 1./30., 1./25.]
V_CRUVE_LOOKUP_VALS = [300, 150, 120, 110, 100, 90, 80, 70, 60, 50, 40, 15, 5]

# Haversine formula to calculate distance between two GPS coordinates
#haversine_cache = {}
def haversine(lon1, lat1, lon2, lat2):
    #key = (lon1, lat1, lon2, lat2)
    #if key in haversine_cache:
    #    return haversine_cache[key]

    R = 6371000  # Radius of Earth in meters
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)

    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    distance = 2 * R * math.atan2(math.sqrt(a), math.sqrt(1 - a))

    #haversine_cache[key] = distance
    return distance


# Get the closest point on a segment between two coordinates
def closest_point_on_segment(p1, p2, current_position):
    x1, y1 = p1
    x2, y2 = p2
    px, py = current_position

    dx = x2 - x1
    dy = y2 - y1
    if dx == 0 and dy == 0:
        return p1  # p1 and p2 are the same point

    # Parameter t is the projection factor onto the line segment
    t = ((px - x1) * dx + (py - y1) * dy) / (dx * dx + dy * dy)
    t = max(0, min(1, t))  # Clamp t to the segment

    closest_x = x1 + t * dx
    closest_y = y1 + t * dy

    return (closest_x, closest_y)


# Get path after a certain distance from the current position
def get_path_after_distance(start_index, coordinates, current_position, distance_m):
    total_distance = 0
    path_after_distance = []
    closest_index = -1
    closest_point = None
    min_distance = float('inf')

    start_index = max(0, start_index - 2)

    # 가까운 점만 탐색하도록 수정
    for i in range(start_index, len(coordinates) - 1):
        p1 = coordinates[i]
        p2 = coordinates[i + 1]
        candidate_point = closest_point_on_segment(p1, p2, current_position)
        distance = haversine(current_position[0], current_position[1], candidate_point[0], candidate_point[1])

        if distance < min_distance:
            min_distance = distance
            closest_point = candidate_point
            closest_index = i
        elif distance > min_distance and min_distance < 10:
            break

    start_index = closest_index
    # Start from the closest point and calculate the path after the specified distance
    if closest_index != -1:
        path_after_distance.append(closest_point)

        path_after_distance.append(coordinates[closest_index + 1])
        total_distance = haversine(closest_point[0], closest_point[1], coordinates[closest_index + 1][0],
                                   coordinates[closest_index + 1][1])

        # Traverse the path forward from the next point
        for i in range(closest_index + 1, len(coordinates) - 1):
            coord1 = coordinates[i]
            coord2 = coordinates[i + 1]
            segment_distance = haversine(coord1[0], coord1[1], coord2[0], coord2[1])

            if total_distance + segment_distance >= distance_m and segment_distance > 0:
                remaining_distance = distance_m - total_distance
                ratio = remaining_distance / segment_distance
                interpolated_lon = coord1[0] + ratio * (coord2[0] - coord1[0])
                interpolated_lat = coord1[1] + ratio * (coord2[1] - coord1[1])
                path_after_distance.append((interpolated_lon, interpolated_lat))
                break

            total_distance += segment_distance
            path_after_distance.append(coord2)

    return path_after_distance, start_index, closest_point


def calculate_angle(point1, point2):
    delta_lon = point2[0] - point1[0]
    delta_lat = point2[1] - point1[1]
    return math.degrees(math.atan2(delta_lat, delta_lon))

# Convert GPS coordinates to relative x, y coordinates based on a reference point and heading
def gps_to_relative_xy(gps_path, reference_point, heading_deg):
    ref_lon, ref_lat = reference_point
    relative_coordinates = []

    # Convert heading from degrees to radians
    heading_rad = math.radians(heading_deg)

    for lon, lat in gps_path:
        # Convert lat/lon differences to meters (assuming small distances for simple approximation)
        x = (lon - ref_lon) * 40008000 * math.cos(math.radians(ref_lat)) / 360
        y = (lat - ref_lat) * 40008000 / 360

        # Rotate coordinates based on the heading angle to align with the car's direction
        x_rot = x * math.cos(heading_rad) - y * math.sin(heading_rad)
        y_rot = x * math.sin(heading_rad) + y * math.cos(heading_rad)

        relative_coordinates.append((y_rot, x_rot))

    return relative_coordinates


# Calculate curvature given three points using a faster vector-based method
#curvature_cache = {}
def calculate_curvature(p1, p2, p3):
    #key = (p1, p2, p3)
    #if key in curvature_cache:
    #    return curvature_cache[key]

    v1 = (p2[0] - p1[0], p2[1] - p1[1])
    v2 = (p3[0] - p2[0], p3[1] - p2[1])

    cross_product = v1[0] * v2[1] - v1[1] * v2[0]
    len_v1 = math.sqrt(v1[0] ** 2 + v1[1] ** 2)
    len_v2 = math.sqrt(v2[0] ** 2 + v2[1] ** 2)

    if len_v1 * len_v2 == 0:
        curvature = 0
    else:
        curvature = cross_product / (len_v1 * len_v2 * len_v1)

    #curvature_cache[key] = curvature
    return curvature

class CarrotMan:
  def __init__(self):
    print("************************************************CarrotMan init************************************************")
    self.params = Params()
    self.params_memory = Params("/dev/shm/params")
    self.gps_location_service = get_gps_location_service(self.params)
    self.sm = messaging.SubMaster(['deviceState', 'carState', 'controlsState', 'radarState', 'longitudinalPlan', 'modelV2', 'selfdriveState', 'carControl', 'navRouteNavd', self.gps_location_service, 'navInstruction'])
    self.pm = messaging.PubMaster(['carrotMan', "navRoute", "navInstructionCarrot"])

    self.carrot_serv = CarrotServ()
    self._navigation_mux = NavigationMux()
    self._navigation_apply_lock = threading.RLock()
    self._navigation_applied_key = None
    self._navigation_last_received_at = 0.0
    self._navigation_is_neutral = True

    self.show_panda_debug = False
    self.broadcast_ip = self.get_broadcast_address()
    self.broadcast_port = 7705
    self.carrot_man_port = 7706
    self.connection = None

    self.ip_address = "0.0.0.0"
    self.remote_addr = None

    self.turn_speed_last = 250
    self.curvatureFilter = MyMovingAverage(20)
    self.carrot_curve_speed_params()

    self.carrot_zmq_thread = threading.Thread(target=self.carrot_cmd_zmq, args=[])
    self.carrot_zmq_thread.daemon = True
    self.carrot_zmq_thread.start()

    self.carrot_panda_debug_thread = threading.Thread(target=self.carrot_panda_debug, args=[])
    self.carrot_panda_debug_thread.daemon = True
    self.carrot_panda_debug_thread.start()

    self.carrot_route_thread = threading.Thread(target=self.carrot_route, args=[])
    self.carrot_route_thread.daemon = True
    self.carrot_route_thread.start()

    self.is_running = True
    threading.Thread(target=self.broadcast_version_info).start()

    self.navi_points = []
    self.navi_points_start_index = 0
    self.navi_points_active = False
    self.navd_active = False

    self.active_carrot_last = False

    self._rgdata_ts_lock = threading.Lock()
    self._last_rgdata_timestamp_ms = 0

    self.is_metric = self.params.get_bool("IsMetric")

  def get_broadcast_address(self):
    if fcntl is None:
      return None
    if PC:
      iface = b'br0'
    else:
      iface = b'wlan0'
    try:
      with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        ip = fcntl.ioctl(
          s.fileno(),
          0x8919,
          struct.pack('256s', iface)
        )[20:24]
        return socket.inet_ntoa(ip)
    except (OSError, Exception):
      return None

  def get_local_ip(self):
      try:
          # 외부 서버와의 연결을 통해 로컬 IP 확인
          with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
              s.connect(("8.8.8.8", 80))  # Google DNS로 연결 시도
              return s.getsockname()[0]
      except Exception as e:
          return f"Error: {e}"


  # 브로드캐스트 메시지 전송
  def broadcast_version_info(self):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    frame = 0
    self.save_toggle_values()

    rk = Ratekeeper(20, print_delay_threshold=None)

    while self.is_running:
      try:
        self.sm.update(0)
        with self._navigation_apply_lock:
          if self.sm.updated['navRouteNavd']:
            self.send_routes(self.sm['navRouteNavd'].coordinates, True)
          self._expire_navigation(time.monotonic())
          remote_addr = self.remote_addr
          remote_ip = remote_addr[0] if remote_addr is not None else ""
          vturn_speed = self.carrot_curve_speed(self.sm)
          coords, distances, route_speed = self.carrot_navi_route()

          #print("coords=", coords)
          #print("curvatures=", curvatures)
          self.carrot_serv.update_navi(remote_ip, self.sm, self.pm, vturn_speed, coords, distances, route_speed, self.gps_location_service)

        if frame % 20 == 0 or remote_addr is not None:
          try:
            self.broadcast_ip = self.get_broadcast_address() if remote_addr is None else remote_addr[0]
            if not PC:
              ip_address = socket.gethostbyname(socket.gethostname())
            else:
              ip_address = self.get_local_ip()
            if ip_address != self.ip_address:
              self.ip_address = ip_address
              self.remote_addr = None
            self.params_memory.put_nonblocking("NetworkAddress", self.ip_address)

            msg = self.make_send_message()
            if self.broadcast_ip is not None:
              dat = msg.encode('utf-8')
              sock.sendto(dat, (self.broadcast_ip, self.broadcast_port))
            #for i in range(1, 255):
            #  ip_tuple = socket.inet_aton(self.broadcast_ip)
            #  new_ip = ip_tuple[:-1] + bytes([i])
            #  address = (socket.inet_ntoa(new_ip), self.broadcast_port)
            #  sock.sendto(dat, address)

            if remote_addr is None:
              #print(f"Broadcasting: {self.broadcast_ip}") #:{msg}")
              if not self.navd_active:
                #print("clear path_points: navd_active: ", self.navd_active)
                self.navi_points = []
                self.navi_points_active = False

          except Exception as e:
            if self.connection:
              self.connection.close()
            self.connection = None
            print(f"##### broadcast_error...: {e}")
            traceback.print_exc()

        rk.keep_time()
        frame += 1
      except Exception as e:
        print(f"broadcast_version_info error...: {e}")
        traceback.print_exc()
        time.sleep(1)


  def carrot_navi_route(self):

    if self.carrot_serv.active_carrot > 1:
      if False and self.navd_active:  # mabox always active
        self.navd_active = False
        self.params.remove("NavDestination")
    is_onroad = self.params.get_bool("IsOnroad")
    if not is_onroad or not self.navi_points_active or not SHAPELY_AVAILABLE or (self.carrot_serv.active_carrot <= 1 and not self.navd_active):
      #print(f"navi_points_active: {self.navi_points_active}, active_carrot: {self.carrot_serv.active_carrot}")
      if self.navi_points_active:
        print("navi_points_active: ", self.navi_points_active, "active_carrot: ", self.carrot_serv.active_carrot, "navd_active: ", self.navd_active)
        #haversine_cache.clear()
        #curvature_cache.clear()
        self.navi_points = []
        self.navi_points_active = False
        if self.active_carrot_last > 1:
          #self.params.remove("NavDestination")
          pass
      self.active_carrot_last = self.carrot_serv.active_carrot
      return [],[],300

    current_position = (self.carrot_serv.vpPosPointLon, self.carrot_serv.vpPosPointLat)
    heading_deg = self.carrot_serv.bearing

    distance_interval = 10.0
    out_speed = 300
    path, self.navi_points_start_index, start_point = get_path_after_distance(self.navi_points_start_index, self.navi_points, current_position, 300)
    relative_coords = []
    if path:
        #relative_coords = gps_to_relative_xy(path, current_position, heading_deg)
        relative_coords = gps_to_relative_xy(path, start_point, heading_deg)
        # Resample relative_coords at 5m intervals using LineString
        line = LineString(relative_coords)
        resampled_points = []
        resampled_distances = []
        current_distance = 0
        while current_distance <= line.length:
            point = line.interpolate(current_distance)
            resampled_points.append((point.x, point.y))
            resampled_distances.append(current_distance)
            current_distance += distance_interval

        curvatures = []
        distances = []
        distance = 10.0
        sample = 4
        if len(resampled_points) >= sample * 2 + 1:
            # Calculate curvatures and speeds based on curvature
            speeds = []
            for i in range(len(resampled_points) - sample * 2):
                distance += distance_interval
                p1, p2, p3 = resampled_points[i], resampled_points[i + sample], resampled_points[i + sample * 2]
                curvature = calculate_curvature(p1, p2, p3)
                curvatures.append(curvature)
                speed = np.interp(abs(curvature), V_CURVE_LOOKUP_BP, V_CRUVE_LOOKUP_VALS)
                if abs(curvature) < 0.02:
                  speed = max(speed, self.carrot_serv.nRoadLimitSpeed)
                speeds.append(speed)
                distances.append(distance)
            #print(f"curvatures= {[round(s, 4) for s in curvatures]}")
            #print(f"speeds= {[round(s, 1) for s in speeds]}")
            # Apply acceleration limits in reverse to adjust speeds
            accel_limit = self.carrot_serv.autoNaviSpeedDecelRate # m/s^2
            accel_limit_kmh = accel_limit * 3.6  # Convert to km/h per second
            out_speeds = [0] * len(speeds)
            out_speeds[-1] = speeds[-1]  # Set the last speed as the initial value
            v_ego_kph = self.sm['carState'].vEgo * 3.6

            time_delay = self.carrot_serv.autoNaviSpeedCtrlEnd
            time_wait = 0
            for i in range(len(speeds) - 2, -1, -1):
                target_speed = speeds[i]
                next_out_speed = out_speeds[i + 1]

                if target_speed < next_out_speed:
                  time_delay = max(0, ((v_ego_kph - target_speed) / accel_limit_kmh))
                  time_wait = - time_delay

                # Calculate time interval for the current segment based on speed
                time_interval = distance_interval / (next_out_speed / 3.6) if next_out_speed > 0 else 0

                time_apply = min(time_interval, max(0, time_interval + time_wait))

                # Calculate maximum allowed speed with acceleration limit
                max_allowed_speed = next_out_speed + (accel_limit_kmh * time_apply)
                adjusted_speed = min(target_speed, max_allowed_speed)

                #time_wait += time_interval
                time_wait += min(2.0, time_interval)

                out_speeds[i] = adjusted_speed

            #distance_advance = self.sm['carState'].vEgo * 3.0  # Advance distance by 3.0 seconds
            #out_speed = interp(distance_advance, distances, out_speeds)
            out_speed = out_speeds[0]
            #print(f"out_speeds= {[round(s, 1) for s in out_speeds]}")
    else:
        resampled_points = []
        resampled_distances = []
        curvatures = []
        speeds = []
        distances = []
        #self.params.remove("NavDestination")

    return resampled_points, resampled_distances, out_speed #speeds, distances


  def make_send_message(self):
    msg = {}
    msg['Carrot2'] = self.params.get("Version")
    isOnroad = self.params.get_bool("IsOnroad")
    msg['IsOnroad'] = isOnroad
    msg['CarrotRouteActive'] = self.navi_points_active
    msg['ip'] = self.ip_address
    msg['port'] = self.carrot_man_port
    self.controls_active = False
    self.xState = 0
    self.trafficState = 0
    v_ego_kph = 0
    log_carrot = ""
    v_cruise_kph = 0
    carcruiseSpeed = 0
    if not isOnroad:
      self.xState = 0
      self.trafficState = 0
    else:
      if self.sm.alive['carState']:
        carState = self.sm['carState']
        v_ego_kph = int(carState.vEgoCluster * 3.6 + 0.5)
        log_carrot = carState.logCarrot
        v_cruise_kph = carState.vCruise
        carcruiseSpeed = carState.cruiseState.speed * 3.6
      if self.sm.alive['selfdriveState']:
        selfdrive = self.sm['selfdriveState']
        self.controls_active = selfdrive.active
      if self.sm.alive['longitudinalPlan']:
        lp = self.sm['longitudinalPlan']
        self.xState = lp.xState
        self.trafficState = lp.trafficState

    msg['log_carrot'] = log_carrot
    msg['v_cruise_kph'] = v_cruise_kph
    msg['carcruiseSpeed'] = carcruiseSpeed
    msg['v_ego_kph'] = v_ego_kph
    msg['tbt_dist'] = self.carrot_serv.xDistToTurn
    msg['sdi_dist'] = self.carrot_serv.xSpdDist
    msg['active'] = self.controls_active
    msg['xState'] = self.xState
    msg['trafficState'] = self.trafficState
    return json.dumps(msg)

  def receive_fixed_length_data(self, sock, length):
    buffer = b""
    while len(buffer) < length:
      data = sock.recv(length - len(buffer))
      if not data:
        raise ConnectionError("Connection closed before receiving all data")
      buffer += data
    return buffer

  def _is_navigation_discovery(self, data):
    """Return whether a UDP datagram is a schema-v1 navigation discovery request."""
    if data.strip() == NAVIGATION_DISCOVERY.encode("ascii"):
      return True
    try:
      decoded = data.decode("utf-8")
      payload = json.loads(decoded)
    except (UnicodeDecodeError, json.JSONDecodeError):
      return False
    if not isinstance(payload, dict) or payload.get("type") != NAVIGATION_DISCOVERY:
      return False
    schema = payload.get("schema", payload.get("schema_version", NAVIGATION_SCHEMA_VERSION))
    return schema == NAVIGATION_SCHEMA_VERSION

  def _navigation_discovery_server(self):
    server = getattr(self, "ip_address", "")
    if not server or server == "0.0.0.0":
      try:
        candidate = self.get_local_ip()
      except OSError:
        candidate = ""
      if candidate and not candidate.startswith("Error:"):
        server = candidate
    return server or "127.0.0.1"

  def _handle_navigation_datagram(self, sock, data, remote_addr):
    """Reply to discovery without forwarding the datagram to CarrotServ."""
    if not self._is_navigation_discovery(data):
      return False
    response = {
      "type": NAVIGATION_DISCOVERY_RESPONSE,
      "server": self._navigation_discovery_server(),
      "port": NAVIGATION_TCP_PORT,
      "schema": NAVIGATION_SCHEMA_VERSION,
      "schema_version": NAVIGATION_SCHEMA_VERSION,
      "lease": NAVIGATION_LEASE_MS,
      "lease_ms": NAVIGATION_LEASE_MS,
    }
    try:
      sock.sendto(json.dumps(response, separators=(",", ":")).encode("utf-8"),
                  (remote_addr[0], getattr(self, "broadcast_port", 7705)))
    except OSError as error:
      print(f"navigation discovery response error: {error}")
    return True


  def carrot_man_thread(self):
    while True:
      try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
          sock.settimeout(10)  # 소켓 타임아웃 설정 (10초)
          sock.bind(('0.0.0.0', self.carrot_man_port))  # UDP 포트 바인딩
          print("#########carrot_man_thread: UDP thread started...")

          while True:
            try:
              #self.remote_addr = None
              # 데이터 수신 (UDP는 recvfrom 사용)
              try:
                data, remote_addr = sock.recvfrom(4096)  # 최대 4096 바이트 수신
                #print(f"Received data from {self.remote_addr}")

                if not data:
                  raise ConnectionError("No data received")

                if self._handle_navigation_datagram(sock, data, remote_addr):
                  continue

                if self.remote_addr is None:
                  print("Connected to: ", remote_addr)
                self.remote_addr = remote_addr
                try:
                  json_obj = json.loads(data.decode())
                  envelope_keys = ("schema_version", "source", "rgdata", "vrtx")
                  navigation_keys = ("nRoadLimitSpeed", "nTBTTurnType", "nSdiType", "nGoPosDist")
                  if isinstance(json_obj, dict) and any(key in json_obj for key in envelope_keys):
                    self._dispatch_obj(json_obj, peer=remote_addr, transport=Transport.UDP)
                  elif isinstance(json_obj, dict) and any(key in json_obj for key in navigation_keys):
                    self._dispatch_obj({"rgdata": json_obj}, peer=remote_addr, transport=Transport.UDP)
                  else:
                    self.carrot_serv.update(json_obj)
                except Exception as e:
                  print(f"carrot_man_thread: json error...: {e}")
                  print(data)

                # 응답 메시지 생성 및 송신 (UDP는 sendto 사용)
                #try:
                #  msg = self.make_send_message()
                #  sock.sendto(msg.encode('utf-8'), self.remote_addr)
                #except Exception as e:
                #  print(f"carrot_man_thread: send error...: {e}")

              except TimeoutError:
                #print("Waiting for data (timeout)...")
                self.remote_addr = None
                time.sleep(1)

              except Exception as e:
                print(f"carrot_man_thread: error...: {e}")
                self.remote_addr = None
                break

            except Exception as e:
              print(f"carrot_man_thread: recv error...: {e}")
              self.remote_addr = None
              break

          time.sleep(1)
      except Exception as e:
        self.remote_addr = None
        print(f"Network error, retrying...: {e}")
        time.sleep(2)

  def parse_kisa_data(self, data: bytes):
    result = {}

    try:
      decoded = data.decode('utf-8')
    except UnicodeDecodeError:
      print("Decoding error:", data)
      return result

    parts = decoded.split('/')
    for part in parts:
      if ':' in part:
        key, value = part.split(':', 1)
        try:
          result[key] = int(value)
        except ValueError:
          result[key] = value
    return result

  def kisa_app_thread(self):
    while True:
      try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
          sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
          sock.settimeout(10)  # 소켓 타임아웃 설정 (10초)
          sock.bind(('', 12345))  # UDP 포트 바인딩
          print("#########kisa_app_thread: UDP thread started...")

          while True:
            try:
              #self.remote_addr = None
              # 데이터 수신 (UDP는 recvfrom 사용)
              try:
                data, remote_addr = sock.recvfrom(4096)  # 최대 4096 바이트 수신
                #print(f"Received data from {self.remote_addr}")

                if not data:
                  raise ConnectionError("No data received")

                #if self.remote_addr is None:
                #  print("Connected to: ", remote_addr)
                #self.remote_addr = remote_addr
                try:
                  print(data)
                  kisa_data = self.parse_kisa_data(data)
                  self.carrot_serv.update_kisa(kisa_data)
                  #json_obj = json.loads(data.decode())
                  #print(json_obj)
                except Exception as e:
                  traceback.print_exc()
                  print(f"kisa_app_thread: json error...: {e}")
                  print(data)

              except TimeoutError:
                #print("Waiting for data (timeout)...")
                #self.remote_addr = None
                time.sleep(1)

              except Exception as e:
                print(f"kisa_app_thread: error...: {e}")
                #self.remote_addr = None
                break

            except Exception as e:
              print(f"kisa_app_thread: recv error...: {e}")
              #self.remote_addr = None
              break

          time.sleep(1)
      except Exception as e:
        #self.remote_addr = None
        print(f"Network error, retrying...: {e}")
        time.sleep(2)

  def make_tmux_data(self):
    try:
      subprocess.run("rm /data/media/tmux.log; tmux capture-pane -pq -S-1000 > /data/media/tmux.log", shell=True, capture_output=True, text=False)
      subprocess.run("/data/openpilot/selfdrive/apilot.py", shell=True, capture_output=True, text=False)
    except Exception as e:
      print(f"TMUX creation error: {e}")
      return

  def send_tmux(self, ftp_password, tmux_why, send_settings=False):
    ftp_server = "shind0.synology.me"
    ftp_port = 8021
    ftp_username = "carrotpilot"
    ftp = FTP()
    ftp.connect(ftp_server, ftp_port)
    ftp.login(ftp_username, ftp_password)
    car_selected = Params().get("CarName")
    if car_selected is None:
      car_selected = "none"
    else:
      car_selected = car_selected

    git_branch = Params().get("GitBranch").replace("/", "__")
    try:
      ftp.mkd(git_branch)
    except Exception as e:
      print(f"Directory creation failed: {e}")
    ftp.cwd(git_branch)

    directory = car_selected + " " + Params().get("DongleId")
    current_time = datetime.now().strftime("%Y%m%d-%H%M%S")
    filename = tmux_why + "-" + current_time + "-" + git_branch + ".txt"

    try:
      ftp.mkd(directory)
    except Exception as e:
      print(f"Directory creation failed: {e}")
    ftp.cwd(directory)

    try:
      with open("/data/media/tmux.log", "rb") as file:
        ftp.storbinary(f'STOR {filename}', file)
    except Exception as e:
      print(f"ftp sending error...: {e}")

    if send_settings:
      self.save_toggle_values()
      try:
        #with open("/data/backup_params.json", "rb") as file:
        with open("/data/toggle_values.json", "rb") as file:
          ftp.storbinary(f'STOR toggles-{current_time}.json', file)
      except Exception as e:
        print(f"ftp params sending error...: {e}")

    ftp.quit()

  def send_tmux_http(self, tmux_why, send_settings=False):
    def get_private_ip_by_iface(name="wlan0"):
      addrs = psutil.net_if_addrs().get(name, [])

      for addr in addrs:
          if addr.family == socket.AF_INET:
              try:
                  ip_obj = ipaddress.ip_address(addr.address)
                  if ip_obj.is_private:
                      return addr.address
              except ValueError:
                  continue
      return None

    def _pstr(key):
      v = Params().get(key) or ""
      return v.decode("utf-8", errors="ignore") if isinstance(v, bytes) else v

    url = "https://tmux.carrotpilot.app/upload"

    payload = {
      "car_name"          : _pstr("CarName"),
      "git_branch"        : _pstr("GitBranch"),
      "github_id"         : _pstr("GithubUsername"),
      "git_remote"        : _pstr("GitRemote"),
      "git_commit"        : _pstr("GitCommit"),
      "git_commit_date"   : _pstr("GitCommitDate"),
      "dongle_id"         : _pstr("DongleId"),
      "device_serial"     : _pstr("HardwareSerial"),
      "local_ip"          : get_private_ip_by_iface("wlan0"),
    }

    files = [
        ("files[0]", ("tmux.log", open("/data/media/tmux.log", "rb"), "text/plain")),
    ]

    if send_settings:
      #self.save_toggle_values()
      files.append(("files[1]",("toggle_values.json",open("/data/toggle_values.json", "rb"),"application/json")))

    params = {}
    headers = {}

    try:
      response = requests.post(
          url,
          params=params,
          headers=headers,
          data=payload,
          files=files,
          timeout=10,
      )
      print(response.status_code, response.text)
      return response
    finally:
      for _, fileinfo in files:
        fileobj = fileinfo[1]
        try:
          fileobj.close()
        except Exception:
          pass

  def carrot_panda_debug(self):
    #time.sleep(2)
    while True:
      if self.show_panda_debug:
        self.show_panda_debug = False
        try:
          subprocess.run("/data/openpilot/selfdrive/debug/debug_console_carrot.py", shell=True)
        except Exception as e:
          print(f"debug_console error: {e}")
          time.sleep(2)
      else:
        time.sleep(1)

  def get_all_toggle_values(self):
    toggle_values = {}

    for k in self.params.all_keys():
      # key 정리
      if isinstance(k, (bytes, bytearray, memoryview)):
        try:
          key = k.decode("utf-8")
        except Exception:
          continue
      else:
        key = str(k)

      # 타입 확인 + 제외
      try:
        t = self.params.get_type(key)
      except Exception:
        continue
      if t in (ParamKeyType.BYTES, ParamKeyType.JSON):
        continue

      # default 없는 키 제외
      try:
        dv = self.params.get_default_value(key)
      except Exception:
        continue
      if dv is None:
        continue

      # 값 읽기 (이미 Params.get()이 타입 변환까지 해줌)
      try:
        v = self.params.get(key, block=False, return_default=False)
      except Exception:
        v = None

      # v가 None이면 default로 채우고 싶으면 dv로 대체 (선택)
      if v is None:
        v = dv

      # 최종 stringify (jsonify 용)
      if isinstance(v, (dict, list)):
        toggle_values[key] = json.dumps(v, ensure_ascii=False)
      else:
        toggle_values[key] = str(v)

    return toggle_values

  def save_toggle_values(self):
    try:
      toggle_values = self.get_all_toggle_values()
      file_path = os.path.join('/data', 'toggle_values.json')
      with open(file_path, 'w') as file:
        json.dump(toggle_values, file, indent=2)
    except Exception as e:
      print(f"save_toggle_values error: {e}")

  def carrot_cmd_zmq(self):

    context = zmq.Context()
    def setup_socket():
        socket = context.socket(zmq.REP)
        socket.bind("tcp://*:7710")
        poller = zmq.Poller()
        poller.register(socket, zmq.POLLIN)
        return socket, poller

    socket, poller = setup_socket()
    isOnroadCount = 0
    is_tmux_sent = False

    print("#########carrot_cmd_zmq: thread started...")
    while True:
      try:
        socks = dict(poller.poll(100))

        if socket in socks and socks[socket] == zmq.POLLIN:
          message = socket.recv(zmq.NOBLOCK)
          print(f"Received:7710 request: {message}")
          json_obj = json.loads(message.decode())
        else:
          json_obj = None

        if json_obj is None:
          isOnroadCount = isOnroadCount + 1 if self.params.get_bool("IsOnroad") else 0
          if isOnroadCount == 0:
            is_tmux_sent = False
          if isOnroadCount == 1:
            self.show_panda_debug = True

          network_type = self.sm['deviceState'].networkType # if not force_wifi else NetworkType.wifi
          networkConnected = False if network_type == NetworkType.none else True

          if isOnroadCount == 500:
            self.make_tmux_data()
          if isOnroadCount > 500 and not is_tmux_sent and networkConnected:
            self.send_tmux("Ekdrmsvkdlffjt7710", "onroad", send_settings = True)
            self.send_tmux_http("onroad", send_settings = True)
            is_tmux_sent = True
          carrot_exception = self.params.get("CarrotException")
          if carrot_exception in ["exception", "log", "tmux_send"] and networkConnected:
            self.params.put("CarrotException", "")
            self.make_tmux_data()
            self.send_tmux("Ekdrmsvkdlffjt7710", carrot_exception)
            self.send_tmux_http(carrot_exception, send_settings = False)
        elif 'echo_cmd' in json_obj:
          try:
            result = subprocess.run(json_obj['echo_cmd'], shell=True, capture_output=True, text=False)
            exitStatus = result.returncode
            try:
              stdout = result.stdout.decode('utf-8')
              stderr = result.stderr.decode('utf-8')
            except UnicodeDecodeError:
              stdout = result.stdout.decode('euc-kr', 'ignore')
              stderr = result.stderr.decode('euc-kr', 'ignore')

            echo = json.dumps({"echo_cmd": json_obj['echo_cmd'], "exitStatus": exitStatus, "result": stdout, "error": stderr})
          except Exception as e:
            echo = json.dumps({"echo_cmd": json_obj['echo_cmd'], "exitStatus": exitStatus, "result": "", "error": f"exception error: {str(e)}"})
          #print(echo)
          socket.send(echo.encode())
        elif 'tmux_send' in json_obj:
          self.make_tmux_data()
          self.send_tmux(json_obj['tmux_send'], "tmux_send")
          self.send_tmux_http("tmux_send")
          echo = json.dumps({"tmux_send": json_obj['tmux_send'], "result": "success"})
          socket.send(echo.encode())
      except Exception as e:
        print(f"carrot_cmd_zmq error: {e}")
        socket.close()
        time.sleep(1)
        socket, poller = setup_socket()

  def recvall(self, sock, n):
    """n바이트를 수신할 때까지 반복적으로 데이터를 받는 함수"""
    data = bytearray()
    while len(data) < n:
      packet = sock.recv(n - len(data))
      if not packet:
        return None
      data.extend(packet)
    return data

  def receive_double(self, sock):
    double_data = self.recvall(sock, 8)  # Double은 8바이트
    return struct.unpack('!d', double_data)[0]

  def receive_float(self, sock):
    float_data = self.recvall(sock, 4)  # Float은 4바이트
    return struct.unpack('!f', float_data)[0]


  def send_routes(self, coords, from_navd=False):
    if from_navd:
      if len(coords) > 0:
        self.navi_points = [(c.longitude, c.latitude) for c in coords]
        self.navi_points_start_index = 0
        self.navi_points_active = True
        print("Received points from navd:", len(self.navi_points))
        self.navd_active = True

        # 경로수신 -> carrotman active되고 약간의 시간지연이 발생함..
        if not from_navd:
          self.carrot_serv.active_count = 80
          self.carrot_serv.active_sdi_count = self.carrot_serv.active_sdi_count_max
          self.carrot_serv.active_carrot = 2

        coords = [{"latitude": c.latitude, "longitude": c.longitude} for c in coords]
        #print("navdNaviPoints=", self.navi_points)
      else:
        print("Received points from navd: 0")
        self.navi_points = []
        self.navi_points_start_index = 0
        self.navi_points_active = False
        self.navd_active = False

    msg = messaging.new_message('navRoute', valid=True)
    msg.navRoute.coordinates = coords
    self.pm.send('navRoute', msg)

  def carrot_route(self, port=7709, stop_event=None):
    host = '0.0.0.0'  # 혹은 다른 호스트 주소

    try:
      with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind((host, port))
        s.listen()
        if stop_event is not None:
          s.settimeout(0.5)
        self._navigation_binary_server = s
        self._navigation_binary_bound_port = s.getsockname()[1]

        while stop_event is None or not stop_event.is_set():
          print("################# waiting connection from CarrotMan route #####################")
          try:
            conn, addr = s.accept()
          except socket.timeout:
            continue
          with conn:
            print(f"Connected by {addr}")

            # 전체 데이터 크기 수신
            total_size_bytes = self.recvall(conn, 4)
            if not total_size_bytes:
              print("Connection closed or error occurred")
              continue
            try:
              total_size = struct.unpack('!I', total_size_bytes)[0]
              if total_size > NAVIGATION_MAX_BINARY_ROUTE_BYTES or total_size % 8:
                raise ProtocolError(code="bounds", field="binary_route")
              # 전체 데이터를 한 번에 수신
              all_data = self.recvall(conn, total_size)
              if all_data is None or len(all_data) != total_size:
                  print("Connection closed or incomplete data received")
                  continue

              route = []
              for i in range(0, len(all_data), 8):
                x, y = struct.unpack('!ff', all_data[i:i+8])
                route.append({"x": x, "y": y})
              self._dispatch_obj({"vrtx": route}, peer=addr, transport=Transport.BINARY)
              print("Received points:", len(route))

            except Exception as e:
              print(e)
        self._navigation_binary_server = None
    except Exception as e:
      self._navigation_binary_server = None
      print("################# CarrotMan route server error #####################")
      print(e)

  def carrot_curve_speed_params(self):
    self.autoCurveSpeedFactor = self.params.get_int("AutoCurveSpeedFactor")*0.01
    self.autoCurveSpeedAggressiveness = self.params.get_int("AutoCurveSpeedAggressiveness")*0.01
    self.autoCurveSpeedPreview = self.params.get_int("AutoCurveSpeedPreview")
    self.autoCurveSpeedPreviewDecelRate = max(0.1, self.params.get_int("AutoCurveSpeedPreviewDecelRate") * 0.01)
    self.autoCurveSpeedPreviewTime = max(0.0, self.params.get_int("AutoCurveSpeedPreviewTime") * 0.1)

  def carrot_curve_speed(self, sm):
    self.carrot_curve_speed_params()
    if not sm.alive['carState'] and not sm.alive['modelV2']:
        return 250
    #print(len(sm['modelV2'].orientationRate.z))
    if len(sm['modelV2'].orientationRate.z) == 0:
        return 250

    return self.vturn_speed(sm['carState'], sm)

  def vturn_speed(self, CS, sm):
    TARGET_LAT_A = 1.9  # m/s^2

    modelData = sm['modelV2']
    v_ego = max(CS.vEgo, 0.1)
    # Set the curve sensitivity
    orientation_rate = np.array(modelData.orientationRate.z) * self.autoCurveSpeedFactor
    velocity = np.array(modelData.velocity.x)
    n = min(len(orientation_rate), len(velocity))
    if n == 0:
        return 250
    orientation_rate = orientation_rate[:n]
    velocity = np.maximum(velocity[:n], 0.1)

    # Get the maximum lat accel from the model
    pred_lat_acc = np.abs(orientation_rate) * velocity
    max_index = int(np.argmax(pred_lat_acc))
    curv_direction = np.sign(orientation_rate[max_index])
    max_pred_lat_acc = pred_lat_acc[max_index]
    if max_pred_lat_acc <= 1e-4:
        return 250

    # Get the maximum curve based on the current velocity
    max_curve = max_pred_lat_acc / (v_ego**2)

    # Set the target lateral acceleration
    adjusted_target_lat_a = TARGET_LAT_A * self.autoCurveSpeedAggressiveness

    # Get the target velocity for the maximum curve
    #turnSpeed = max(abs(adjusted_target_lat_a / max_curve)**0.5  * 3.6, self.autoCurveSpeedLowerLimit)
    turnSpeed = max(abs(adjusted_target_lat_a / max_curve)**0.5  * 3.6, 5)
    turnSpeed = min(turnSpeed, 250)

    if self.autoCurveSpeedPreview > 0 and turnSpeed < 250:
      if len(modelData.position.x) >= n:
        distances = np.maximum(np.array(modelData.position.x)[:n], 0.0)
      elif len(modelData.velocity.t) >= n:
        times = np.array(modelData.velocity.t)[:n]
        distances = np.maximum(times, 0.0) * v_ego
      else:
        distances = np.arange(n) * v_ego * 0.2

      preview_speed = 250.0
      preview_direction = curv_direction
      # Apply a braking envelope to each predicted curve point, not just the sharpest one.
      for i in range(n):
        effective_curve = pred_lat_acc[i] / (v_ego**2)
        if effective_curve <= 1e-5:
          continue
        target_speed = max(abs(adjusted_target_lat_a / effective_curve)**0.5 * 3.6, 5)
        target_speed = min(target_speed, 250)
        allowed_speed = self.carrot_serv.calculate_current_speed(
          distances[i],
          target_speed,
          self.autoCurveSpeedPreviewTime,
          self.autoCurveSpeedPreviewDecelRate,
        )
        if allowed_speed < preview_speed:
          preview_speed = allowed_speed
          preview_direction = np.sign(orientation_rate[i]) or preview_direction

      if preview_speed < 250:
        turnSpeed = preview_speed
        curv_direction = preview_direction

    return turnSpeed * curv_direction

  def carrot_navi_thread(self):
    self.carrot_navi_tcp_server(7712)

  def handle_route(self, arr: list):
    if not arr:
      print("Received route: 0")
      # navd route가 비어오면 비활성 처리
      self.navi_points = []
      self.navi_points_start_index = 0
      self.navi_points_active = False
      self.navd_active = False
      return

    # valid만 필터 (필요 없으면 제거)
    valid_pts = [p for p in arr if isinstance(p, dict) and p.get("valid", True)]
    if not valid_pts:
      print("Received route: 0 valid")
      self.navi_points = []
      self.navi_points_start_index = 0
      self.navi_points_active = False
      self.navd_active = False
      return

    # x=lon, y=lat
    coords = []
    navi_points = []

    for p in valid_pts:
      try:
        lon = float(p.get("x"))
        lat = float(p.get("y"))
      except Exception:
        continue

      navi_points.append((lon, lat))
      coords.append({"latitude": lat, "longitude": lon})

    self.navi_points = navi_points
    self.navi_points_start_index = 0
    self.navi_points_active = True
    self.navd_active = True

    print("Received points:", len(self.navi_points))

    self.send_routes(coords)

    if coords:
      dest = dict(coords[-1])
      dest["place_name"] = "External Navi"
      try:
        self.params.put("NavDestination", json.dumps(dest))
      except Exception as e:
        print("NavDestination put error:", e)

  def handle_traffic_light(self, d: dict):
    print(f"[Traffic] {d}")

    # {'distance': 120, 'greenLightRemainTime': 0, 'leftLightRemainTime': 0, 'location': {'coordString': 'x:127.045286, y:37.477032', 'latitude': 37.47703188722564, 'longitude': 127.04528634430659},
    #       'redLightRemainTime': 15, 'rightLightRemainTime': 0, 'uturnLightRemainTime': 0, 'greenLightOn': False, 'leftLightOn': False, 'redLightOn': True, 'rightLightOn': False, 'uturnLightOn': False}
    lamp = None
    remain = 0

    if d.get("redLightOn"):
      lamp = "red"
      remain = d.get("redLightRemainTime", 0)
    elif d.get("leftLightOn"):
      lamp = "left"
      remain = d.get("leftLightRemainTime", 0)
    elif d.get("greenLightOn"):
      lamp = "green"
      remain = d.get("greenLightRemainTime", 0)
    elif d.get("rightLightOn"):
      lamp = "right"
      remain = d.get("rightLightRemainTime", 0)
    elif d.get("uturnLightOn"):
      lamp = "uturn"
      remain = d.get("uturnLightRemainTime", 0)

    if lamp is None:
      return

    traffic_light = {
      "distance": int(d.get("distance", 0)),
      "lamp": lamp,
      "remain": int(remain),
    }
    self.params_memory.put("TrafficLight", json.dumps(traffic_light))


  def handle_carrot_state(self, d: dict):
    try:
      self.carrot_serv.update(d)
    except Exception as e:
      print("carrot_state update error:", e)

  def handle_unknown(self, obj: Any):
    print("[UNKNOWN]", str(obj)[:200])

  def _get_timestamp_ms(self, obj: Any) -> int:
    if not isinstance(obj, dict):
      return 0
    try:
      return int(obj.get("timestamp_ms", 0))
    except Exception:
      return 0


  def _is_stale_rgdata(self, timestamp_ms: int):
    if timestamp_ms <= 0:
      return False, 0

    with self._rgdata_ts_lock:
      last_ts = self._last_rgdata_timestamp_ms
      if timestamp_ms <= last_ts:
        return True, last_ts

      self._last_rgdata_timestamp_ms = timestamp_ms
      return False, last_ts

  def _ensure_navigation_pipeline(self):
    if not hasattr(self, "_navigation_mux"):
      self._navigation_mux = NavigationMux()
    if not hasattr(self, "_navigation_apply_lock"):
      self._navigation_apply_lock = threading.RLock()
    if not hasattr(self, "_navigation_applied_key"):
      self._navigation_applied_key = None
    if not hasattr(self, "_navigation_last_received_at"):
      self._navigation_last_received_at = 0.0
    if not hasattr(self, "_navigation_is_neutral"):
      self._navigation_is_neutral = True
    if not hasattr(self, "_navigation_receipts"):
      self._navigation_receipts = {}
    if not hasattr(self, "_navigation_legacy_state"):
      self._navigation_legacy_state = None
    if not hasattr(self, "_navigation_legacy_context"):
      self._navigation_legacy_context = None
    if not hasattr(self, "_navigation_pending_binary_routes"):
      self._navigation_pending_binary_routes = {}

  def _navigation_state_payload(self, envelope):
    position = envelope.state.position
    payload = {
      "nRoadLimitSpeed": int(envelope.state.road_limit_kph),
      "nSdiType": -1,
      "nSdiBlockType": -1,
      "nSdiPlusType": -1,
      "nSdiPlusBlockType": -1,
      "nTBTTurnType": NAVIGATION_MANEUVER_CODES[envelope.state.maneuver],
      "nTBTDist": int(envelope.state.maneuver_distance_m),
      "nTBTTurnTypeNext": -1,
      "nTBTDistNext": 0,
      "nGoPosDist": int(envelope.state.remaining_distance_m),
      "nGoPosTime": int(envelope.state.remaining_time_s),
      "vpPosPointLon": 0.0 if position is None else position.longitude,
      "vpPosPointLat": 0.0 if position is None else position.latitude,
    }
    naver = envelope.naver
    if naver is None:
      return payload

    safety_kind = naver.safety_kind.value
    safety_type = {"fixed_camera": 1, "mobile_camera": 7, "section_camera": 2}.get(safety_kind, -1)
    safety_distance = int(naver.safety_distance_m)
    bump = safety_kind == "bump"
    payload.update({
      "nSdiType": safety_type,
      "nSdiSpeedLimit": int(naver.safety_speed_kph) if safety_type >= 0 else 0,
      "nSdiDist": safety_distance if safety_type >= 0 else 0,
      "nSdiSection": 1 if safety_kind == "section_camera" else 0,
      "nSdiBlockSpeed": 0,
      "nSdiBlockDist": 0,
      "nSdiPlusType": 22 if bump else -1,
      "nSdiPlusDist": safety_distance if bump else 0,
      "nSdiPlusBlockSpeed": 0,
      "nSdiPlusBlockDist": 0,
      "roadcate": 3 if bump else 8,
      "nTBTTurnTypeNext": NAVIGATION_MANEUVER_CODES[naver.next_maneuver],
      "nTBTDistNext": int(naver.next_maneuver_distance_m),
    })
    return payload

  def _neutralize_navigation(self):
    self._ensure_navigation_pipeline()
    if self._navigation_is_neutral:
      return False

    self.carrot_serv.neutralize_navigation()
    self.navi_points.clear()
    self.navi_points_start_index = 0
    self.navi_points_active = False
    self.navd_active = False
    self.active_carrot_last = False
    self._navigation_applied_key = None
    self._navigation_last_received_at = 0.0
    self.send_routes([])
    self.params.remove("NavDestination")
    instruction = messaging.new_message('navInstructionCarrot')
    instruction.valid = False
    self.pm.send('navInstructionCarrot', instruction)
    self._navigation_is_neutral = True
    return True

  def _apply_navigation_current(self, envelope, root: dict, received_at: float, force_route=False):
    canonical = "schema_version" in root or "source" in root
    session_key = (envelope.source, envelope.session_id)
    applied_session = None if self._navigation_applied_key is None else self._navigation_applied_key[:2]
    force_snapshot = force_route or applied_session != session_key
    apply_route = force_snapshot or "route" in root or "vrtx" in root
    if apply_route and envelope.route is not None:
      route = [{"x": point.longitude, "y": point.latitude} for point in envelope.route]
      self.handle_route(route)

    apply_state = force_snapshot or canonical or "rgdata" in root
    if apply_state:
      if envelope.source is ProviderSource.TMAP_LEGACY and self._navigation_legacy_state is not None:
        state = self._navigation_legacy_state
      else:
        state = self._navigation_state_payload(envelope)
      self.handle_carrot_state(state)

    control_allowed = envelope.source in NAVIGATION_CONTROL_SOURCES
    self.carrot_serv.set_navigation_observability(envelope.source.value, True, 0, control_allowed)
    self._navigation_applied_key = (envelope.source, envelope.session_id, envelope.sequence)
    if apply_state:
      self._navigation_last_received_at = self._navigation_receipts.get(envelope.source, received_at)
    self._navigation_is_neutral = False

  def _update_navigation_age(self, now: float):
    current = self._navigation_mux.current
    if current is None or not current.navigation_active:
      return
    age_ms = min(UINT32_MAX, int(max(0.0, now - self._navigation_last_received_at) * 1_000))
    self.carrot_serv.set_navigation_observability(current.source.value, True, age_ms,
                                                  current.source in NAVIGATION_CONTROL_SOURCES)

  def _prune_navigation_route_buffer(self, now):
    stale_peers = [peer for peer, (_route, received_at) in self._navigation_pending_binary_routes.items()
                   if (now - received_at) * 1_000 > NAVIGATION_ROUTE_BUFFER_MS]
    for peer in stale_peers:
      self._navigation_pending_binary_routes.pop(peer, None)

  def _ingest_navigation_frame(self, root: dict, peer, transport, received_at=None):
    self._ensure_navigation_pipeline()
    with self._navigation_apply_lock:
      now = time.monotonic() if received_at is None else received_at
      peer_name = str(peer[0]) if isinstance(peer, tuple) and peer else str(peer or "unknown")
      self._prune_navigation_route_buffer(now)
      canonical = "schema_version" in root or "source" in root
      context = TransportContext(transport, peer_name)
      pending_route = self._navigation_pending_binary_routes.get(peer_name)
      joined_pending_route = False
      if not canonical and "rgdata" in root and "vrtx" not in root and pending_route is not None:
        root = dict(root)
        root["vrtx"] = pending_route[0]
        joined_pending_route = True
      buffer_binary_route = False
      if (not canonical and transport is Transport.BINARY and "vrtx" in root and "rgdata" not in root):
        legacy_context = self._navigation_legacy_context
        if legacy_context is not None and legacy_context.peer == peer_name:
          context = legacy_context
        else:
          buffer_binary_route = True
      frame = ReceivedFrame(json.dumps(root, separators=(",", ":")).encode("utf-8"),
                            context, now)
      transitions = self._navigation_mux.ingest(frame)
      frame_source = ProviderSource(root["source"]) if canonical else ProviderSource.TMAP_LEGACY
      frame_has_state = canonical or "rgdata" in root
      if frame_has_state:
        self._navigation_receipts[frame_source] = now
      if not canonical and "rgdata" in root:
        self._navigation_legacy_state = dict(root["rgdata"])
        self._navigation_legacy_context = context
        if joined_pending_route or pending_route is not None:
          self._navigation_pending_binary_routes.pop(peer_name, None)
      elif buffer_binary_route:
        if peer_name not in self._navigation_pending_binary_routes and len(self._navigation_pending_binary_routes) >= NAVIGATION_MAX_PENDING_ROUTES:
          self._navigation_pending_binary_routes.pop(next(iter(self._navigation_pending_binary_routes)))
        self._navigation_pending_binary_routes[peer_name] = (list(root["vrtx"]), now)
      switched = any(transition.previous is not None for transition in transitions)
      if switched:
        self._neutralize_navigation()
      current = self._navigation_mux.current
      if current is not None and current.navigation_active:
        key = (current.source, current.session_id, current.sequence)
        if key != self._navigation_applied_key:
          self._apply_navigation_current(current, root, now, force_route=switched)
        else:
          self._update_navigation_age(now)
      return transitions

  def _expire_navigation(self, now: float):
    self._ensure_navigation_pipeline()
    with self._navigation_apply_lock:
      self._prune_navigation_route_buffer(now)
      transitions = self._navigation_mux.expire(now)
      switched = any(transition.previous is not None for transition in transitions)
      if switched:
        self._neutralize_navigation()
      current = self._navigation_mux.current
      if current is not None and current.navigation_active:
        key = (current.source, current.session_id, current.sequence)
        if key != self._navigation_applied_key:
          self._apply_navigation_current(current, {}, now, force_route=True)
        else:
          self._update_navigation_age(now)
      return transitions

  def _dispatch_obj(self, obj: Any, peer=None, transport=None, received_at=None):
    if peer is not None:
      self._navigation_peer = peer
    if obj is None:
      return

    # obj가 str이면 여기서 JSON 파싱
    if isinstance(obj, str):
      s = obj.strip()
      if not s:
        return
      try:
        obj = json.loads(s)
      except Exception:
        # JSON 아니면 unknown 처리
        return self.handle_unknown(s[:200])

    if not isinstance(obj, dict):
      return self.handle_unknown(obj)

    navigation_frame = any(key in obj for key in ("schema_version", "source", "vrtx", "rgdata"))
    if navigation_frame:
      self._ingest_navigation_frame(obj, peer, Transport.TCP if transport is None else transport, received_at)

    if "sinf" in obj:
      self.handle_traffic_light(obj["sinf"])

  def carrot_navi_http_thread(self):
    asyncio.run(self.carrot_navi_http_server(7713))

  def _ensure_navigation_ingress_state(self):
    if not hasattr(self, "_navigation_client_slots"):
      self._navigation_client_slots = threading.BoundedSemaphore(NAVIGATION_MAX_CLIENTS)
    if not hasattr(self, "_navigation_clients"):
      self._navigation_clients = {}
    if not hasattr(self, "_navigation_clients_lock"):
      self._navigation_clients_lock = threading.Lock()

  def _read_navigation_line(self, conn, pending):
    deadline = time.monotonic() + NAVIGATION_READ_TIMEOUT
    while True:
      newline = pending.find(b"\n")
      if newline >= 0:
        line = bytes(pending[:newline])
        del pending[:newline + 1]
        if len(line) > NAVIGATION_MAX_LINE_BYTES:
          raise ValueError("navigation line exceeds maximum size")
        return line.rstrip(b"\r")

      if len(pending) > NAVIGATION_MAX_LINE_BYTES:
        raise ValueError("navigation line exceeds maximum size")
      remaining = deadline - time.monotonic()
      if remaining <= 0:
        raise TimeoutError("navigation client read timeout")
      conn.settimeout(remaining)
      chunk = conn.recv(min(4096, NAVIGATION_MAX_LINE_BYTES + 1 - len(pending)))
      if not chunk:
        if not pending:
          return None
        line = bytes(pending)
        pending.clear()
        if len(line) > NAVIGATION_MAX_LINE_BYTES:
          raise ValueError("navigation line exceeds maximum size")
        return line.rstrip(b"\r")
      pending.extend(chunk)

  def _serve_navigation_client(self, conn, addr):
    self._ensure_navigation_ingress_state()
    current = threading.current_thread()
    pending = bytearray()
    try:
      conn.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
      self.remote_addr = addr
      with self._navigation_clients_lock:
        self._navigation_clients[conn] = current
      while True:
        try:
          line = self._read_navigation_line(conn, pending)
        except (OSError, TimeoutError, ValueError) as error:
          print(f"TCP client {addr} closed: {error}")
          break
        if line is None:
          break
        if not line.strip():
          continue
        try:
          text = line.decode("utf-8")
        except UnicodeDecodeError as error:
          print(f"TCP client {addr} invalid UTF-8: {error}")
          break
        try:
          obj = json.loads(text)
        except (json.JSONDecodeError, TypeError) as error:
          print(f"TCP client {addr} invalid JSON: {error}")
          break
        try:
          self._dispatch_obj(obj, peer=addr)
        except Exception as error:  # noqa: BROAD_EXCEPT_OK - isolate one malformed peer frame
          print("dispatch error:", error, "peer:", addr, "raw:", repr(text[:200]))
    finally:
      with self._navigation_clients_lock:
        self._navigation_clients.pop(conn, None)
      try:
        conn.shutdown(socket.SHUT_RDWR)
      except OSError:
        pass
      conn.close()
      if getattr(self, "remote_addr", None) == addr:
        self.remote_addr = None
      self._navigation_client_slots.release()

  def _close_navigation_clients(self):
    self._ensure_navigation_ingress_state()
    with self._navigation_clients_lock:
      clients = list(self._navigation_clients)
    for conn in clients:
      try:
        conn.shutdown(socket.SHUT_RDWR)
      except OSError:
        pass
      conn.close()

  def carrot_navi_tcp_server(self, port: int = NAVIGATION_TCP_PORT, stop_event=None):
    self._ensure_navigation_ingress_state()
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("0.0.0.0", port))
    server.listen(NAVIGATION_MAX_CLIENTS)
    server.settimeout(0.5)
    self._navigation_server = server
    self._navigation_bound_port = server.getsockname()[1]
    print("TCP server listening", self._navigation_bound_port)

    try:
      while stop_event is None or not stop_event.is_set():
        try:
          conn, addr = server.accept()
        except socket.timeout:
          continue
        except OSError:
          if stop_event is not None and stop_event.is_set():
            break
          raise
        if not self._navigation_client_slots.acquire(blocking=False):
          conn.close()
          continue
        try:
          worker = threading.Thread(target=self._serve_navigation_client, args=(conn, addr), daemon=True)
          with self._navigation_clients_lock:
            self._navigation_clients[conn] = worker
          worker.start()
        except RuntimeError:
          with self._navigation_clients_lock:
            self._navigation_clients.pop(conn, None)
          self._navigation_client_slots.release()
          conn.close()
    finally:
      server.close()
      self._close_navigation_clients()
      self._navigation_server = None

  async def carrot_http_post(self, request: web.Request):
    tmap_version = request.match_info.get("tmap_version", "")

    try:
      peer = request.transport.get_extra_info("peername")
    except Exception:
      peer = None

    #print(f"[HTTP] request from={peer} version={tmap_version}")

    try:
      obj = await request.json()
      #if isinstance(obj, dict):
      #  print(f"[HTTP] json keys={list(obj.keys())[:10]}")
      #else:
      #  print(f"[HTTP] json type={type(obj).__name__}")
    except Exception as e:
      print(f"[HTTP] json parse error: {e}")
      return web.json_response({
        "ok": False,
        "error": f"invalid json: {e}"
      }, status=400)

    if isinstance(obj, dict):
      obj["_tmap_version"] = tmap_version

    try:
      self._dispatch_obj(obj, peer=peer, transport=Transport.HTTP)
      #print(f"[HTTP] dispatch ok version={tmap_version}")
      #print(obj)
      return web.json_response({
        "ok": True,
        "tmap_version": tmap_version
      })
    except Exception as e:
      print(f"[HTTP] dispatch error: {e}")
      traceback.print_exc()
      return web.json_response({
        "ok": False,
        "error": str(e),
        "tmap_version": tmap_version
      }, status=500)

  async def carrot_http_health(self, request: web.Request):
    return web.json_response({
      "ok": True,
      "service": "carrot_navi_http"
    })

  async def carrot_navi_http_server(self, port: int = 7713):
    app = web.Application(client_max_size=1024 * 1024)

    app.router.add_post("/api/navi/{tmap_version}", self.carrot_http_post)
    app.router.add_get("/health", self.carrot_http_health)

    runner = web.AppRunner(app, access_log=None)
    await runner.setup()

    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()

    print("HTTP server listening", port)

    while True:
      await asyncio.sleep(3600)

def main():
  try:
    set_core_affinity([0, 1, 2, 3])
  except Exception:
    print("[carrot_man] failed to set core affinity")

  print("CarrotManager Started")
  #print("Carrot GitBranch = {}, {}".format(Params().get("GitBranch"), Params().get("GitCommitDate")))
  carrot_man = CarrotMan()

  print(f"CarrotMan {carrot_man}")
  threading.Thread(target=carrot_man.kisa_app_thread).start()
  threading.Thread(target=carrot_man.carrot_navi_thread).start()
  threading.Thread(target=carrot_man.carrot_navi_http_thread).start()

  while True:
    try:
      carrot_man.carrot_man_thread()
    except Exception as e:
      print(f"carrot_man error...: {e}")
      traceback.print_exc()
      time.sleep(10)


if __name__ == "__main__":
  main()
