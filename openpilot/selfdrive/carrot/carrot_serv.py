import fcntl
import json
import math
import os
import socket
import struct
import subprocess
import threading
import time
import numpy as np
from dataclasses import replace
from datetime import datetime

from openpilot.cereal import log
import openpilot.cereal.messaging as messaging
from openpilot.common.realtime import Ratekeeper
from openpilot.common.params import Params
from openpilot.common.filter_simple import MyMovingAverage
from openpilot.system.hardware import PC, TICI
from openpilot.selfdrive.navd.helpers import Coordinate
from openpilot.common.constants import CV
from openpilot.common.gps import get_gps_location_service
from openpilot.selfdrive.carrot.carrot_navi_control import (
  V2_ITEM_TTL_S,
  CarrotNaviControl,
  parse_carrot_navi_control,
)
from openpilot.selfdrive.carrot.navigation_sources import (
  NavigationControlState,
  NavigationInstruction,
  NavigationLifecycle,
  NavigationSelection,
  NavigationSnapshot,
  NavigationSource,
  NavigationSourceStore,
  SafetyItem,
  SafetyDecision,
  choose_safety,
  safety_rejection,
)

_NAVIGATION_DIAGNOSTIC_MAX_CHARS = 64
_NAVIGATION_SESSION_MAX_CHARS = 128
_NAVIGATION_INT32_MAX = 2_147_483_647
_NAVIGATION_UINT64_MAX = 18_446_744_073_709_551_615
_NAVIGATION_OWNERS = frozenset(("tmap_legacy", "carrot_navi_v2", "naver_v1"))
_NAVIGATION_PROVIDERS = _NAVIGATION_OWNERS | frozenset(("hda", "kisa"))
_NAVIGATION_REASONS = frozenset(("cam", "section", "bump", "hda", "police", "waze"))
_NAVIGATION_REJECTIONS = frozenset((
  "no_owner",
  "off_route",
  "invalid_item",
  "unsupported_type",
  "invalid_distance",
  "mode_disabled",
  "mobile_requires_mode_3",
  "road_category_missing",
  "road_category_blocked",
  "safety_stale",
  "safety_absent",
))
_NAVIGATION_LIFECYCLES = frozenset(("idle", "guiding", "stopped", "arrived"))


def _bounded_navigation_token(value, allowed):
  token = str(value or "").strip().lower()
  if not token:
    return ""
  if token not in allowed:
    return "unknown"
  return token[:_NAVIGATION_DIAGNOSTIC_MAX_CHARS]


def _navigation_age_ms(age_s):
  if age_s is None:
    return -1
  try:
    age_s = float(age_s)
  except (TypeError, ValueError, OverflowError):
    return -1
  if not math.isfinite(age_s):
    return -1
  return min(_NAVIGATION_INT32_MAX, max(0, int(age_s * 1000.0)))


def _navigation_sequence(value):
  try:
    value = int(value)
  except (TypeError, ValueError, OverflowError):
    return 0
  return min(_NAVIGATION_UINT64_MAX, max(0, value))


def publish_navigation_diagnostics(target, selection, decision, rejection):
  snapshot = None if selection is None else getattr(selection, "snapshot", None)
  source_value = "" if snapshot is None else getattr(getattr(snapshot, "source", None), "value", "")
  owner = _bounded_navigation_token(source_value, _NAVIGATION_OWNERS)
  provider = _bounded_navigation_token(
    "" if decision is None else getattr(decision, "provider", ""),
    _NAVIGATION_PROVIDERS,
  )
  reason = _bounded_navigation_token(
    "" if decision is None else getattr(decision, "reason", ""),
    _NAVIGATION_REASONS,
  )
  control_allowed = bool(owner and owner != "unknown" and provider == owner)
  rejection_token = "" if control_allowed else _bounded_navigation_token(rejection, _NAVIGATION_REJECTIONS)

  target.naviOwner = owner
  target.naviSessionId = (
    "" if snapshot is None else str(getattr(snapshot, "session_id", "") or "")[:_NAVIGATION_SESSION_MAX_CHARS]
  )
  target.naviSequence = _navigation_sequence(0 if snapshot is None else getattr(snapshot, "sequence", 0))
  target.naviOwnerAgeMs = _navigation_age_ms(None if selection is None else getattr(selection, "owner_age_s", None))
  target.naviSafetyAgeMs = _navigation_age_ms(None if selection is None else getattr(selection, "safety_age_s", None))
  lifecycle_value = "" if snapshot is None else getattr(getattr(snapshot, "lifecycle", None), "value", "")
  target.naviLifecycle = _bounded_navigation_token(lifecycle_value, _NAVIGATION_LIFECYCLES)
  target.naviControlAllowed = control_allowed
  target.naviSafetyRejection = rejection_token
  target.decelProvider = provider
  target.decelReason = reason


nav_type_mapping = {
  12: ("turn", "left", 1),
  16: ("turn", "sharp left", 1),
  1000: ("turn", "slight left", 1),
  1001: ("turn", "slight right", 2),
  1002: ("fork", "slight left", 3),
  1003: ("fork", "slight right", 4),
  1006: ("off ramp", "left", 3),
  1007: ("off ramp", "right", 4),
  13: ("turn", "right", 2),
  19: ("turn", "sharp right", 2),
  102: ("off ramp", "slight left", 3),
  105: ("off ramp", "slight left", 3),
  112: ("off ramp", "slight left", 3),
  115: ("off ramp", "slight left", 3),
  101: ("off ramp", "slight right", 4),
  104: ("off ramp", "slight right", 4),
  111: ("off ramp", "slight right", 4),
  114: ("off ramp", "slight right", 4),
  7: ("fork", "left", 3),
  44: ("fork", "left", 3),
  17: ("fork", "left", 3),
  75: ("fork", "left", 3),
  76: ("fork", "left", 3),
  118: ("fork", "left", 3),
  6: ("fork", "right", 4),
  43: ("fork", "right", 4),
  73: ("fork", "right", 4),
  74: ("fork", "right", 4),
  123: ("fork", "right", 4),
  124: ("fork", "right", 4),
  117: ("fork", "right", 4),
  131: ("rotary", "slight right", 5),
  132: ("rotary", "slight right", 5),
  140: ("rotary", "slight left", 5),
  141: ("rotary", "slight left", 5),
  133: ("rotary", "right", 5),
  134: ("rotary", "sharp right", 5),
  135: ("rotary", "sharp right", 5),
  136: ("rotary", "sharp left", 5),
  137: ("rotary", "sharp left", 5),
  138: ("rotary", "sharp left", 5),
  139: ("rotary", "left", 5),
  142: ("rotary", "straight", 5),
  14: ("turn", "uturn", 5),
  201: ("arrive", "straight", 5),
  51: ("notification", "straight", None),
  52: ("notification", "straight", None),
  53: ("notification", "straight", None),
  54: ("notification", "straight", None),
  55: ("notification", "straight", None),
  153: ("", "", 6),  #TG
  154: ("", "", 6),  #TG
  249: ("", "", 6)   #TG
}

import collections
class CarrotServ:
  def __init__(self):
    self.params = Params()
    self.params_memory = Params("/dev/shm/params")

    self.nRoadLimitSpeed = 30
    self.nRoadLimitSpeed_last = 30
    self.nRoadLimitSpeed_counter = 0
    self.road_limit_write_generation = 0
    self.road_limit_write_source = "init"

    self.active_carrot = 0     ## 1: CarrotMan Active, 2: sdi active , 3: speed decel active, 4: section active, 5: bump active, 6: speed limit active
    self.active_count = 0
    self.active_sdi_count = 0
    self.active_sdi_count_max = 200 # 20 sec

    self.active_kisa_count = 0
    self.kisa_safety_type = -1
    self.kisa_safety_limit = 0.0
    self.kisa_safety_distance = 0.0

    self.nSdiType = -1
    self.nSdiSpeedLimit = 0
    self.nSdiSection = 0
    self.nSdiDist = 0
    self.nSdiBlockType = -1
    self.nSdiBlockSpeed = 0
    self.nSdiBlockDist = 0

    self.nTBTDist = 0
    self.nTBTTurnType = -1
    self.szTBTMainText = ""
    self.szNearDirName = ""
    self.szFarDirName = ""
    self.nTBTNextRoadWidth = 0

    self.nTBTDistNext = 0
    self.nTBTTurnTypeNext = -1
    self.szTBTMainTextNext = ""

    self.nGoPosDist = 0
    self.nGoPosTime = 0
    self.szPosRoadName = ""
    self.nSdiPlusType = -1
    self.nSdiPlusSpeedLimit = 0
    self.nSdiPlusDist = 0
    self.nSdiPlusBlockType = -1
    self.nSdiPlusBlockSpeed = 0
    self.nSdiPlusBlockDist = 0

    self.goalPosX = 0.0
    self.goalPosY = 0.0
    self.szGoalName = ""
    self.vpPosPointLatNavi = 0.0
    self.vpPosPointLonNavi = 0.0
    self.vpPosPointLat = 0.0
    self.vpPosPointLon = 0.0
    self.roadcate = 8

    self.nPosSpeed = 0.0
    self.nPosAngle = 0.0
    self.nPosAnglePhone = 0.0

    self.diff_angle_count = 0
    self.last_calculate_gps_time = 0
    self.last_update_gps_time = 0
    self.last_update_gps_time_phone = 0
    self.last_update_gps_time_navi = 0
    self.bearing_offset = 0.0
    self.bearing_measured = 0.0
    self.bearing = 0.0
    self.gps_valid = False

    self.phone_gps_accuracy = 0.0
    self.gps_accuracy_device = 0.0
    self.phone_latitude = 0.0
    self.phone_longitude = 0.0
    self.phone_gps_frame = 0

    self.totalDistance = 0
    self.xSpdLimit = 0
    self.xSpdDist = 0
    self.xSpdType = -1

    self.xTurnInfo = -1
    self.xDistToTurn = 0
    self.xTurnInfoNext = -1
    self.xDistToTurnNext = 0

    self.navType, self.navModifier = "invalid", ""
    self.navTypeNext, self.navModifierNext = "invalid", ""

    self.carrotIndex = 0
    self.carrotCmdIndex = 0
    self.carrotCmd = ""
    self.carrotArg = ""
    self.carrotCmdIndex_last = 0

    self.traffic_light_q = collections.deque(maxlen=int(2.0/0.1))  # 2 secnods
    self.traffic_light_count = -1
    self.traffic_state = 0

    self.left_spd_sec = 0
    self.left_tbt_sec = 0
    self.left_sec = 100
    self.max_left_sec = 100
    self.carrot_left_sec = 100
    self.sdi_inform = False


    self.atc_paused = False
    self.atc_activate_count = 0
    self.gas_override_speed = 0
    self.gas_pressed_state = False
    self.source_last = "none"
    self.navigation_owner = ""
    self.last_safety_decision = None
    self.last_safety_provider = ""
    self.last_safety_reason = ""
    self.last_safety_rejection = "no_owner"
    self._last_safety_key = None

    self.carrot_navi_session_id = ""
    self.carrot_navi_speed_sequence = -1
    self.carrot_navi_current_sequence = -1
    self.carrot_navi_next_sequence = -1
    self.carrot_navi_lane_sequence = -1
    self.carrot_navi_vehicle_sequence = -1
    self.carrot_navi_route_sequence = -1
    self.carrot_navi_projection_revision = 0
    self.carrot_navi_speed_revision = None
    self.carrot_navi_current_revision = None
    self.carrot_navi_next_revision = None
    self.carrot_navi_lane_revision = None
    self.carrot_navi_last_valid_road_category = None
    self.carrot_navi_last_valid_road_category_received_mono_time_nanos = 0
    self.carrot_navi_active = False
    self.carrot_navi_has_control = False
    self.carrot_navi_road_limit_valid = False
    self.carrot_navi_projected_road_limit_generation = None
    self.carrot_navi_off_route = False
    self.carrot_navi_traffic_active = False
    self.carrot_navi_control = None

    self.navigation_sources = NavigationSourceStore()
    self.navigation_selection = self.navigation_sources.select(time.monotonic())
    self._navigation_projection_lock = threading.RLock()
    self._projected_navigation_revision = -1
    self._projected_navigation_safety_key = None
    self._projected_navigation_current_key = None
    self._projected_navigation_next_key = None
    self._projected_navigation_road_limit_key = None
    self._legacy_navigation_sequences = {}
    self._legacy_road_limit_state = {}
    self._v2_navigation_revisions = {}
    self._v2_navigation_sequences = {}
    self._v2_last_valid_categories = {}

    self.debugText = ""

    # 默认语言，稍后在 update_params 中从 Params 读取覆盖，
    # 规则：main_ko -> 韩语；main_zh-CHS -> 中文；其他 -> 英文
    self.lang = "en"

    self.update_params()

  def update_params(self):
    self.autoNaviSpeedBumpSpeed = float(self.params.get_int("AutoNaviSpeedBumpSpeed"))
    self.autoNaviSpeedBumpTime = float(self.params.get_int("AutoNaviSpeedBumpTime"))
    self.autoNaviSpeedCtrlEnd = float(self.params.get_int("AutoNaviSpeedCtrlEnd"))
    self.autoNaviSpeedCtrlMode = self.params.get_int("AutoNaviSpeedCtrlMode")
    self.autoNaviSpeedSafetyFactor = float(self.params.get_int("AutoNaviSpeedSafetyFactor")) * 0.01
    self.autoNaviSpeedDecelRate = float(self.params.get_int("AutoNaviSpeedDecelRate")) * 0.01
    self.autoNaviCountDownMode = self.params.get_int("AutoNaviCountDownMode")
    self.turnSpeedControlMode= self.params.get_int("TurnSpeedControlMode")
    self.mapTurnSpeedFactor= self.params.get_float("MapTurnSpeedFactor") * 0.01

    self.autoTurnControlSpeedTurn = self.params.get_int("AutoTurnControlSpeedTurn")
    self.autoTurnMapChange = self.params.get_int("AutoTurnMapChange")
    self.autoTurnControl = self.params.get_int("AutoTurnControl")
    self.autoTurnControlTurnEnd = self.params.get_int("AutoTurnControlTurnEnd")
    #self.autoNaviSpeedDecelRate = float(self.params.get_int("AutoNaviSpeedDecelRate")) * 0.01
    self.autoCurveSpeedLowerLimit = int(self.params.get("AutoCurveSpeedLowerLimit"))
    self.is_metric = self.params.get_bool("IsMetric")
    self.autoRoadSpeedLimitOffset = self.params.get_int("AutoRoadSpeedLimitOffset")

    # 读取语言设置：优先使用 LanguageSetting，与 UI 保持一致；回退读取可能存在的 "lang"
    try:
      lang_val = self.params.get('LanguageSetting') or self.params.get('lang')
    except Exception:
      lang_val = None
    if isinstance(lang_val, bytes):
      try:
        lang_val = lang_val
      except Exception:
        lang_val = None
    if lang_val == "main_ko":
      self.lang = "ko"
    elif lang_val == "main_zh-CHS":
      self.lang = "zh"
    else:
      self.lang = "en"


  def _update_cmd(self):
    if self.carrotCmdIndex != self.carrotCmdIndex_last:
      self.carrotCmdIndex_last = self.carrotCmdIndex
      command_handlers = {
        "DETECT": self._handle_detect_command,
      }

      handler = command_handlers.get(self.carrotCmd)
      if handler:
        handler(self.carrotArg)

    self.traffic_light_q.append((-1, -1, "none", 0.0))
    self.traffic_light_count -= 1
    if self.traffic_light_count < 0:
      self.traffic_light_count = -1
      self.traffic_state = 0

  def _handle_detect_command(self, xArg):
    elements = [e.strip() for e in xArg.split(',')]
    if len(elements) >= 4:
      try:
        state = elements[0]
        value1 = float(elements[1])
        value2 = float(elements[2])
        value3 = float(elements[3])
        self.traffic_light(value1, value2, state, value3)
        self.traffic_light_count = int(0.5 / 0.1)
      except ValueError:
        pass

  def traffic_light(self, x, y, color, cnf):
    traffic_red = 0
    traffic_green = 0
    traffic_left = 0
    traffic_red_trig = 0
    traffic_green_trig = 0
    traffic_left_trig = 0
    for pdata in self.traffic_light_q:
      px, py, pcolor,pcnf = pdata
      if abs(x - px) < 0.2 and abs(y - py) < 0.2:
        if pcolor in ["Green Light", "Left turn"]:
          if color in ["Red Light", "Yellow Light"]:
            traffic_red_trig += cnf
            traffic_red += cnf
          elif color in ["Green Light", "Left turn"]:
            traffic_green += cnf
        elif pcolor in ["Red Light", "Yellow Light"]:
          if color in ["Green Light"]: #, "Left turn"]:
            traffic_green_trig += cnf
            traffic_green += cnf
          elif color in ["Left turn"]:
            traffic_left_trig += cnf
            traffic_left += cnf
          elif color in ["Red Light", "Yellow Light"]:
            traffic_red += cnf

    #print(self.traffic_light_q)
    if traffic_red_trig > 0:
      self.traffic_state = 1
      #self._add_log("Red light triggered")
      #print("Red light triggered")
    elif traffic_green_trig > 0 and traffic_green > traffic_red:  #주변에 red light의 cnf보다 더 크면 출발... 감지오류로 출발하는경우가 생김.
      self.traffic_state = 2
      #self._add_log("Green light triggered")
      #print("Green light triggered")
    elif traffic_left_trig > 0:
      self.traffic_state = 3
    elif traffic_red > 0:
      self.traffic_state = 1
      #self._add_log("Red light continued")
      #print("Red light continued")
    elif traffic_green > 0:
      self.traffic_state = 2
      #self._add_log("Green light continued")
      #print("Green light continued")
    else:
      self.traffic_state = 0
      #print("TrafficLight none")

    self.traffic_light_q.append((x,y,color,cnf))


  def calculate_current_speed(self, left_dist, safe_speed_kph, safe_time, safe_decel_rate):
    safe_speed = safe_speed_kph / 3.6
    safe_dist = safe_speed * safe_time
    decel_dist = left_dist - safe_dist

    if decel_dist <= 0:
      return safe_speed_kph

    # v_i^2 = v_f^2 + 2ad
    temp = safe_speed**2 + 2 * safe_decel_rate * decel_dist  # 공식에서 감속 적용

    if temp < 0:
      speed_mps = safe_speed
    else:
      speed_mps = math.sqrt(temp)
    return max(safe_speed_kph, min(250, speed_mps * 3.6))

  def _update_tbt(self):
    #xTurnInfo : 1: left turn, 2: right turn, 3: left lane change, 4: right lane change, 5: rotary, 6: tg, 7: arrive or uturn
    turn_type_mapping = {
      12: ("turn", "left", 1),
      16: ("turn", "sharp left", 1),
      13: ("turn", "right", 2),
      19: ("turn", "sharp right", 2),
      102: ("off ramp", "slight left", 3),
      105: ("off ramp", "slight left", 3),
      112: ("off ramp", "slight left", 3),
      115: ("off ramp", "slight left", 3),
      101: ("off ramp", "slight right", 4),
      104: ("off ramp", "slight right", 4),
      111: ("off ramp", "slight right", 4),
      114: ("off ramp", "slight right", 4),
      7: ("fork", "left", 3),
      44: ("fork", "left", 3),
      17: ("fork", "left", 3),
      75: ("fork", "left", 3),
      76: ("fork", "left", 3),
      118: ("fork", "left", 3),
      6: ("fork", "right", 4),
      43: ("fork", "right", 4),
      73: ("fork", "right", 4),
      74: ("fork", "right", 4),
      123: ("fork", "right", 4),
      124: ("fork", "right", 4),
      117: ("fork", "right", 4),
      131: ("rotary", "slight right", 5),
      132: ("rotary", "slight right", 5),
      140: ("rotary", "slight left", 5),
      141: ("rotary", "slight left", 5),
      133: ("rotary", "right", 5),
      134: ("rotary", "sharp right", 5),
      135: ("rotary", "sharp right", 5),
      136: ("rotary", "sharp left", 5),
      137: ("rotary", "sharp left", 5),
      138: ("rotary", "sharp left", 5),
      139: ("rotary", "left", 5),
      142: ("rotary", "straight", 5),
      14: ("turn", "uturn", 7),
      201: ("arrive", "straight", 8),
      51: ("notification", "straight", 0),
      52: ("notification", "straight", 0),
      53: ("notification", "straight", 0),
      54: ("notification", "straight", 0),
      55: ("notification", "straight", 0),
      153: ("", "", 6),  #TG
      154: ("", "", 6),  #TG
      249: ("", "", 6)   #TG
    }

    if self.nTBTTurnType in turn_type_mapping:
      self.navType, self.navModifier, self.xTurnInfo = turn_type_mapping[self.nTBTTurnType]
    else:
      self.navType, self.navModifier, self.xTurnInfo = "invalid", "", -1

    if self.nTBTTurnTypeNext in turn_type_mapping:
      self.navTypeNext, self.navModifierNext, self.xTurnInfoNext = turn_type_mapping[self.nTBTTurnTypeNext]
    else:
      self.navTypeNext, self.navModifierNext, self.xTurnInfoNext = "invalid", "", -1

    if self.nTBTDist > 0 and self.xTurnInfo > 0:
      self.xDistToTurn = self.nTBTDist
    if self.nTBTDistNext > 0 and self.xTurnInfoNext > 0:
      self.xDistToTurnNext = self.nTBTDistNext + self.nTBTDist

  def _get_sdi_descr(self, nSdiType):
    # 多语言映射：ko（韩语，原始），zh（简体中文），en（英文）。
    sdi_ko = {
        0: "신호과속",
        1: "과속 (고정식)",
        2: "구간단속 시작",
        3: "구간단속 끝",
        4: "구간단속중",
        5: "꼬리물기단속카메라",
        6: "신호 단속",
        7: "과속 (이동식)",
        8: "고정식 과속위험 구간(박스형)",
        9: "버스전용차로구간",
        10: "가변 차로 단속",
        11: "갓길 감시 지점",
        12: "끼어들기 금지",
        13: "교통정보 수집지점",
        14: "방범용cctv",
        15: "과적차량 위험구간",
        16: "적재 불량 단속",
        17: "주차단속 지점",
        18: "일방통행도로",
        19: "철길 건널목",
        20: "어린이 보호구역(스쿨존 시작 구간)",
        21: "어린이 보호구역(스쿨존 끝 구간)",
        22: "과속방지턱",
        23: "lpg충전소",
        24: "터널 구간",
        25: "휴게소",
        26: "톨게이트",
        27: "안개주의 지역",
        28: "유해물질 지역",
        29: "사고다발",
        30: "급커브지역",
        31: "급커브구간1",
        32: "급경사구간",
        33: "야생동물 교통사고 잦은 구간",
        34: "우측시야불량지점",
        35: "시야불량지점",
        36: "좌측시야불량지점",
        37: "신호위반다발구간",
        38: "과속운행다발구간",
        39: "교통혼잡지역",
        40: "방향별차로선택지점",
        41: "무단횡단사고다발지점",
        42: "갓길 사고 다발 지점",
        43: "과속 사발 다발 지점",
        44: "졸음 사고 다발 지점",
        45: "사고다발지점",
        46: "보행자 사고다발지점",
        47: "차량도난사고 상습발생지점",
        48: "낙석주의지역",
        49: "결빙주의지역",
        50: "병목지점",
        51: "합류 도로",
        52: "추락주의지역",
        53: "지하차도 구간",
        54: "주택밀집지역(교통진정지역)",
        55: "인터체인지",
        56: "분기점",
        57: "휴게소(lpg충전가능)",
        58: "교량",
        59: "제동장치사고다발지점",
        60: "중앙선침범사고다발지점",
        61: "통행위반사고다발지점",
        62: "목적지 건너편 안내",
        63: "졸음 쉼터 안내",
        64: "노후경유차단속",
        65: "터널내 차로변경단속",
        66: "",
    }

    sdi_en = {
        0: "Signal speed enforcement",
        1: "Speed camera (fixed)",
        2: "Section control start",
        3: "Section control end",
        4: "Under section control",
        5: "Block-the-box camera",
        6: "Signal violation enforcement",
        7: "Speed camera (mobile)",
        8: "Fixed speed camera zone (box)",
        9: "Bus-only lane zone",
        10: "Reversible/variable lane enforcement",
        11: "Shoulder surveillance point",
        12: "No cut-in",
        13: "Traffic data collection point",
        14: "Security CCTV",
        15: "Overloaded vehicle risk zone",
        16: "Improper loading enforcement",
        17: "Parking enforcement point",
        18: "One-way road",
        19: "Railroad crossing",
        20: "School zone start",
        21: "School zone end",
        22: "Speed bump",
        23: "LPG station",
        24: "Tunnel section",
        25: "Rest area",
        26: "Toll gate",
        27: "Fog caution area",
        28: "Hazardous materials area",
        29: "Accident-prone section",
        30: "Sharp curve area",
        31: "Sharp curve section 1",
        32: "Steep slope section",
        33: "Wild animal crossing area",
        34: "Poor visibility (right)",
        35: "Poor visibility",
        36: "Poor visibility (left)",
        37: "Frequent signal violations",
        38: "Frequent speeding",
        39: "Traffic congestion area",
        40: "Lane selection by direction",
        41: "Frequent jaywalking accidents",
        42: "Frequent shoulder accidents",
        43: "Frequent speeding accidents",
        44: "Frequent drowsy driving accidents",
        45: "Accident-prone spot",
        46: "Frequent pedestrian accidents",
        47: "Frequent vehicle theft",
        48: "Falling rock caution area",
        49: "Icy road caution area",
        50: "Bottleneck point",
        51: "Merging road",
        52: "Cliff/Drop caution area",
        53: "Underpass section",
        54: "Residential area (traffic calming)",
        55: "Interchange",
        56: "Junction",
        57: "Rest area (LPG available)",
        58: "Bridge",
        59: "Frequent brake failure accidents",
        60: "Center line invasion accidents",
        61: "Violation-of-passage accidents",
        62: "Destination on opposite side",
        63: "Drowsy rest area",
        64: "Old diesel control",
        65: "Lane change enforcement in tunnel",
        66: "",
    }

    sdi_zh = {
        0: "信号测速/闯灯拍照",
        1: "固定测速摄像头",
        2: "区间测速开始",
        3: "区间测速结束",
        4: "区间测速中",
        5: "路口压线摄像头",
        6: "闯红灯拍照",
        7: "流动测速摄像头",
        8: "测速拍照",
        9: "公交专用车道区间",
        10: "可变/潮汐车道拍照",
        11: "应急车道拍照",
        12: "禁止加塞",
        13: "交通信息采集点",
        14: "治安监控",
        15: "超载车辆风险区",
        16: "装载不当拍照",
        17: "违停拍照点",
        18: "单行道",
        19: "铁路道口",
        20: "学校区域开始",
        21: "学校区域结束",
        22: "减速带",
        23: "LPG加气站",
        24: "隧道区间",
        25: "服务区",
        26: "ETC计费拍照",
        27: "多雾路段",
        28: "危险品区域",
        29: "事故多发路段",
        30: "急弯路段",
        31: "急弯区段1",
        32: "陡坡路段",
        33: "野生动物出没路段",
        34: "右侧视野不良点",
        35: "视野不良点",
        36: "左侧视野不良点",
        37: "闯红灯多发",
        38: "超速多发",
        39: "交通拥堵区域",
        40: "按方向选择车道点",
        41: "行人乱穿马路多发处",
        42: "应急车道事故多发",
        43: "超速事故多发",
        44: "疲劳驾驶事故多发",
        45: "事故多发点",
        46: "行人事故多发点",
        47: "车辆盗窃多发点",
        48: "落石危险路段",
        49: "路面结冰危险",
        50: "瓶颈路段",
        51: "汇入道路",
        52: "坠落危险路段",
        53: "地下车道区间",
        54: "居民区（交通缓和）",
        55: "立交",
        56: "分岔点",
        57: "服务区（可加气）",
        58: "桥梁",
        59: "制动故障事故多发点",
        60: "越线事故多发点",
        61: "违法通行事故多发点",
        62: "目的地在对面",
        63: "瞌睡停车区",
        64: "老旧柴油车管制",
        65: "隧道内变道拍照",
        66: "",
    }

    sdi_map = sdi_en
    if self.lang == "ko":
      sdi_map = sdi_ko
    elif self.lang == "zh":
      sdi_map = sdi_zh

    return sdi_map.get(nSdiType, "")

  def _update_sdi(self):
    #sdiBlockType
    # 1: startOSEPS: 구간단속시작
    # 2: inOSEPS: 구간단속중
    # 3: endOSEPS: 구간단속종료
    # 0:감속안함,1:과속카메라,2:+사고방지턱,3:+이동식카메라
    if self.nSdiType in [0,1,2,3,4,7,8, 75, 76] and self.nSdiSpeedLimit > 0 and self.autoNaviSpeedCtrlMode > 0:
      self.xSpdLimit = self.nSdiSpeedLimit * self.autoNaviSpeedSafetyFactor
      self.xSpdDist = self.nSdiDist
      self.xSpdType = self.nSdiType
      if self.nSdiBlockType in [2,3]:
        self.xSpdDist = self.nSdiBlockDist
        self.xSpdType = 4
      elif self.nSdiType == 7 and self.autoNaviSpeedCtrlMode < 3: #이동식카메라
        self.xSpdLimit = self.xSpdDist = 0
    elif (self.nSdiPlusType == 22 or self.nSdiType == 22) and self.roadcate > 1 and self.autoNaviSpeedCtrlMode >= 2: # speed bump, roadcate:0,1: highway
      self.xSpdLimit = self.autoNaviSpeedBumpSpeed
      self.xSpdDist = self.nSdiPlusDist if self.nSdiPlusType == 22 else self.nSdiDist
      self.xSpdType = 22
    else:
      self.xSpdLimit = 0
      self.xSpdType = -1
      self.xSpdDist = 0

  def _reset_carrot_navi_sequences(self, session_id):
    self.carrot_navi_session_id = session_id
    self.carrot_navi_speed_sequence = -1
    self.carrot_navi_current_sequence = -1
    self.carrot_navi_next_sequence = -1
    self.carrot_navi_lane_sequence = -1
    self.carrot_navi_vehicle_sequence = -1
    self.carrot_navi_route_sequence = -1
    self.carrot_navi_speed_revision = None
    self.carrot_navi_current_revision = None
    self.carrot_navi_next_revision = None
    self.carrot_navi_lane_revision = None
    self.carrot_navi_last_valid_road_category = None
    self.carrot_navi_last_valid_road_category_received_mono_time_nanos = 0

  def _clear_carrot_navi_traffic(self):
    if not getattr(self, "carrot_navi_traffic_active", False):
      return
    params_memory = getattr(self, "params_memory", None)
    if params_memory is not None:
      try:
        params_memory.remove("TrafficLight")
      except Exception:
        pass
    self.carrot_navi_traffic_active = False

  def _write_road_limit_speed(self, value, source):
    self.nRoadLimitSpeed = value
    self.road_limit_write_generation = getattr(self, "road_limit_write_generation", 0) + 1
    self.road_limit_write_source = source
    return self.road_limit_write_generation

  def _ensure_navigation_sources(self):
    if not hasattr(self, "navigation_sources"):
      self.navigation_sources = NavigationSourceStore()
    if not hasattr(self, "_navigation_projection_lock"):
      self._navigation_projection_lock = threading.RLock()
    defaults = {
      "navigation_selection": None,
      "_projected_navigation_revision": -1,
      "_projected_navigation_safety_key": None,
      "_projected_navigation_current_key": None,
      "_projected_navigation_next_key": None,
      "_projected_navigation_position_key": None,
      "_projected_navigation_traffic_key": None,
      "_projected_navigation_road_limit_key": None,
      "_legacy_navigation_sequences": {},
      "_legacy_road_limit_state": {},
      "_legacy_navigation_controls": {},
      "_legacy_navigation_base_sessions": set(),
      "_legacy_navigation_pending": {},
      "_v2_navigation_revisions": {},
      "_v2_navigation_sequences": {},
      "_v2_last_valid_categories": {},
      "navigation_owner": "",
      "last_safety_decision": None,
      "last_safety_provider": "",
      "last_safety_reason": "",
      "last_safety_rejection": "no_owner",
      "_last_safety_key": None,
      "kisa_safety_type": -1,
      "kisa_safety_limit": 0.0,
      "kisa_safety_distance": 0.0,
    }
    for name, value in defaults.items():
      if not hasattr(self, name):
        setattr(self, name, value)

  def _apply_navigation_safety(self, hda_limit_kph, hda_distance_m):
    selection = getattr(self, "navigation_selection", None)
    if selection is None:
      self.navigation_owner = ""
      self.last_safety_decision = None
      self.last_safety_provider = ""
      self.last_safety_reason = ""
      self.last_safety_rejection = "no_owner"
      self._last_safety_key = None
      if getattr(self, "active_kisa_count", 0) <= 0:
        self.active_carrot = 0
      return None
    snapshot = selection.snapshot
    self.navigation_owner = "" if snapshot is None else snapshot.source.value
    kisa_type = self.kisa_safety_type
    kisa_active = (
      getattr(self, "active_kisa_count", 0) > 0
      and kisa_type in (100, 101)
      and self.kisa_safety_limit > 0.0
      and self.kisa_safety_distance >= -250.0
    )
    decision = (
      SafetyDecision("kisa", "police" if kisa_type == 100 else "waze", self.kisa_safety_limit, self.kisa_safety_distance, kisa_type)
      if kisa_active else choose_safety(
        selection,
        hda_limit_kph,
        hda_distance_m,
        mode=self.autoNaviSpeedCtrlMode,
        safety_factor=self.autoNaviSpeedSafetyFactor,
        bump_speed_kph=self.autoNaviSpeedBumpSpeed,
      )
    )
    self.last_safety_decision = decision
    self.last_safety_provider = "" if decision is None else decision.provider
    self.last_safety_reason = "" if decision is None else decision.reason
    self.last_safety_rejection = (
      safety_rejection(
        selection,
        self.autoNaviSpeedCtrlMode,
        self.autoNaviSpeedSafetyFactor,
        self.autoNaviSpeedBumpSpeed,
      ) if decision is None else decision.rejection
    )

    self.active_carrot = 2 if snapshot is not None or getattr(self, "active_kisa_count", 0) > 0 else 0
    key = None if decision is None else (
      decision.provider,
      decision.reason,
      decision.limit_kph,
      decision.distance_m,
      decision.type,
    )
    if decision is not None and decision.provider != "hda":
      if decision.provider == "kisa" or key != self._last_safety_key:
        self.xSpdLimit = decision.limit_kph
        self.xSpdDist = decision.distance_m
      self.xSpdType = decision.type
      self.active_carrot = 5 if decision.type == 22 else 4 if (
        decision.type == 4 or (decision.provider == "kisa" and decision.distance_m <= 0.0)
      ) else 3
    else:
      self.xSpdType = -1
      self.xSpdLimit = self.xSpdDist = 0
    self._last_safety_key = key
    return decision

  def _safety_distance_for_speed(self, decision):
    if decision is None or decision.provider == "hda":
      return 0.0 if decision is None else decision.distance_m
    return self.kisa_safety_distance if decision.provider == "kisa" else self.xSpdDist

  @staticmethod
  def _select_final_speed(speed_n_sources):
    return min(speed_n_sources, key=lambda item: item[0])

  def accept_navigation_snapshot(self, snapshot: NavigationSnapshot) -> bool:
    self._ensure_navigation_sources()
    with self._navigation_projection_lock:
      return self.navigation_sources.accept(snapshot, snapshot.received_mono_s)

  def prepare_navigation(self, sm, now_s=None):
    return self._update_carrot_navi(sm, now_s=now_s)

  @staticmethod
  def _navigation_safety_key(snapshot, item):
    if snapshot is None or item is None:
      return None
    return (snapshot.source, snapshot.session_id, item)

  @staticmethod
  def _navigation_instruction_key(snapshot, item):
    if snapshot is None or not item.present:
      return None
    return (snapshot.source, snapshot.session_id, item)

  def _clear_projected_navigation_fields(self):
    projected_generation = getattr(self, "carrot_navi_projected_road_limit_generation", None)
    if projected_generation is not None and self.road_limit_write_generation == projected_generation:
      self._write_road_limit_speed(0, "navigation_clear")
    self.carrot_navi_projected_road_limit_generation = None
    self._projected_navigation_road_limit_key = None

    self.nSdiType = self.nSdiBlockType = self.nSdiPlusType = self.nSdiPlusBlockType = -1
    self.nSdiSection = -1
    self.nSdiSpeedLimit = self.nSdiDist = 0
    self.nSdiBlockSpeed = self.nSdiBlockDist = 0
    self.nSdiPlusSpeedLimit = self.nSdiPlusDist = 0
    self.nSdiPlusBlockSpeed = self.nSdiPlusBlockDist = 0
    self.xSpdType = -1
    self.xSpdLimit = self.xSpdDist = 0

    self.nTBTTurnType = self.nTBTTurnTypeNext = -1
    self.nTBTDist = self.nTBTDistNext = 0
    self.nTBTNextRoadWidth = 0
    self.szTBTMainText = self.szTBTMainTextNext = ""
    self.szNearDirName = self.szFarDirName = ""
    self.xTurnInfo = self.xTurnInfoNext = -1
    self.xDistToTurn = self.xDistToTurnNext = 0
    self.navType = self.navTypeNext = "invalid"
    self.navModifier = self.navModifierNext = ""

    self.roadcate = 0
    self.nGoPosDist = self.nGoPosTime = 0
    self.goalPosX = self.goalPosY = 0.0
    self.szGoalName = ""
    self.carrot_navi_off_route = False
    self.carrot_navi_road_limit_valid = False
    self.carrot_navi_has_control = False
    self.carrot_navi_active = False
    self.active_count = 0
    self.active_sdi_count = 0
    self.last_update_gps_time_navi = 0
    self.szPosRoadName = ""
    self._projected_navigation_safety_key = None
    self._projected_navigation_current_key = None
    self._projected_navigation_next_key = None
    self._projected_navigation_position_key = None
    self._projected_navigation_traffic_key = None
    self._clear_carrot_navi_traffic()

  def _project_navigation_traffic(self, snapshot, control):
    key = None if not control.traffic_present else (
      snapshot.source,
      snapshot.session_id,
      control.traffic_received_mono_s,
      control.traffic_visible,
      control.traffic_distance_m,
      control.traffic_source,
      control.traffic_lamp,
      control.traffic_remain_s,
    )
    if key == self._projected_navigation_traffic_key:
      return
    self._projected_navigation_traffic_key = key
    if (
      not control.traffic_present
      or not control.traffic_visible
      or not control.traffic_lamp
      or control.traffic_remain_s <= 0
    ):
      self._clear_carrot_navi_traffic()
      return
    params_memory = getattr(self, "params_memory", None)
    if params_memory is None:
      return
    value = {
      "distance": control.traffic_distance_m,
      "lamp": control.traffic_lamp,
      "remain": control.traffic_remain_s,
      "source": control.traffic_source,
      "ts": control.traffic_received_mono_s,
    }
    try:
      params_memory.put_nonblocking("TrafficLight", json.dumps(value))
      self.carrot_navi_traffic_active = True
    except Exception:
      pass

  def _project_navigation_selection(self, selection: NavigationSelection) -> bool:
    self._ensure_navigation_sources()
    with self._navigation_projection_lock:
      if selection.projection_revision <= self._projected_navigation_revision:
        return False

      snapshot = selection.snapshot
      if snapshot is None:
        previous_selection = self.navigation_selection
        if previous_selection is not None and previous_selection.snapshot is not None:
          self._clear_projected_navigation_fields()
        self.navigation_selection = selection
        self._projected_navigation_revision = selection.projection_revision
        self.carrot_navi_projection_revision = selection.projection_revision
        return True

      control = snapshot.control
      previous_safety_key = self._projected_navigation_safety_key
      previous_speed_type = self.xSpdType
      previous_speed_distance = self.xSpdDist
      previous_current_key = self._projected_navigation_current_key
      previous_current_distance = self.xDistToTurn
      previous_next_key = self._projected_navigation_next_key
      previous_next_distance = self.xDistToTurnNext

      self.roadcate = control.road_category if control.road_category is not None else 0

      road_limit_key = None if control.road_limit_kph is None else (
        snapshot.source,
        snapshot.session_id,
        control.road_limit_received_mono_s,
        control.road_limit_kph,
      )
      if road_limit_key != self._projected_navigation_road_limit_key:
        projected_generation = getattr(self, "carrot_navi_projected_road_limit_generation", None)
        if control.road_limit_kph is not None:
          self.carrot_navi_projected_road_limit_generation = self._write_road_limit_speed(
            control.road_limit_kph, f"navigation:{snapshot.source.value}",
          )
        else:
          if projected_generation is not None and self.road_limit_write_generation == projected_generation:
            self._write_road_limit_speed(0, "navigation_clear")
          self.carrot_navi_projected_road_limit_generation = None
        self._projected_navigation_road_limit_key = road_limit_key
      self.carrot_navi_road_limit_valid = control.road_limit_kph is not None

      safety = control.safety
      if safety is None:
        self.nSdiType = -1
        self.nSdiSpeedLimit = self.nSdiDist = 0
        self.nSdiSection = self.nSdiBlockType = -1
        self.nSdiBlockSpeed = self.nSdiBlockDist = 0
      else:
        self.nSdiType = safety.type
        self.nSdiSpeedLimit = safety.speed_limit_kph
        self.nSdiDist = safety.distance_m
        self.nSdiSection = 1 if safety.section else safety.section_type
        self.nSdiBlockType = safety.block_type
        self.nSdiBlockSpeed = safety.block_speed_kph
        self.nSdiBlockDist = safety.block_distance_m

      secondary = control.secondary_safety
      if secondary is None:
        self.nSdiPlusType = self.nSdiPlusBlockType = -1
        self.nSdiPlusSpeedLimit = self.nSdiPlusDist = 0
        self.nSdiPlusBlockSpeed = self.nSdiPlusBlockDist = 0
      else:
        self.nSdiPlusType = secondary.type
        self.nSdiPlusSpeedLimit = secondary.speed_limit_kph
        self.nSdiPlusDist = secondary.distance_m
        self.nSdiPlusBlockType = secondary.block_type
        self.nSdiPlusBlockSpeed = secondary.block_speed_kph
        self.nSdiPlusBlockDist = secondary.block_distance_m
      self._update_sdi()
      safety_key = self._navigation_safety_key(snapshot, safety)
      if safety_key == previous_safety_key and self.xSpdType == previous_speed_type and previous_speed_type >= 0:
        self.xSpdDist = previous_speed_distance
      self._projected_navigation_safety_key = safety_key

      current = control.current
      self.nTBTDist = current.distance_m if current.present else 0
      self.nTBTTurnType = current.turn_type if current.present else -1
      self.szTBTMainText = current.main_text if current.present else ""
      self.szNearDirName = current.near_direction if current.present else ""
      self.szFarDirName = current.far_direction if current.present else ""
      self.nTBTNextRoadWidth = current.next_road_width if current.present else 0
      next_instruction = control.next
      self.nTBTDistNext = next_instruction.distance_m if next_instruction.present else 0
      self.nTBTTurnTypeNext = next_instruction.turn_type if next_instruction.present else -1
      self.szTBTMainTextNext = next_instruction.main_text if next_instruction.present else ""
      self._update_tbt()
      if not current.present:
        self.xTurnInfo = -1
        self.xDistToTurn = 0
      if not next_instruction.present:
        self.xTurnInfoNext = -1
        self.xDistToTurnNext = 0
      current_key = self._navigation_instruction_key(snapshot, current)
      next_key = self._navigation_instruction_key(snapshot, next_instruction)
      if current_key == previous_current_key and current_key is not None:
        self.xDistToTurn = previous_current_distance
      if next_key == previous_next_key and next_key is not None:
        self.xDistToTurnNext = previous_next_distance
      self._projected_navigation_current_key = current_key
      self._projected_navigation_next_key = next_key

      self.nGoPosDist = control.remaining_distance_m if control.route_present else 0
      self.nGoPosTime = control.remaining_time_s if control.route_present else 0
      self.carrot_navi_off_route = control.off_route if control.route_present else False
      if not control.destination_present or control.destination is None:
        self.goalPosX = self.goalPosY = 0.0
      else:
        self.goalPosY, self.goalPosX = control.destination

      position_key = None if not control.position_present else (
        snapshot.source,
        snapshot.session_id,
        control.position_received_mono_s,
        control.position_latitude,
        control.position_longitude,
        control.position_heading_deg,
        control.position_speed_kph,
        control.position_road_name,
      )
      if position_key != self._projected_navigation_position_key:
        if control.position_present:
          self.vpPosPointLatNavi = control.position_latitude
          self.vpPosPointLonNavi = control.position_longitude
          self.nPosAngle = control.position_heading_deg
          self.nPosSpeed = control.position_speed_kph
          self.szPosRoadName = control.position_road_name
          self.last_update_gps_time_navi = self.last_calculate_gps_time = control.position_received_mono_s
        else:
          self.last_update_gps_time_navi = 0
          self.szPosRoadName = current.road_name if current.present else ""
        self._projected_navigation_position_key = position_key
      if not control.position_present:
        self.last_update_gps_time_navi = 0
        self.szPosRoadName = current.road_name if current.present else ""

      self._project_navigation_traffic(snapshot, control)
      self.carrot_navi_has_control = bool(
        control.speed_present or current.present or next_instruction.present
      )
      self.carrot_navi_active = snapshot.source is NavigationSource.CARROT_NAVI_V2
      if self.carrot_navi_has_control:
        self.active_count = 80
        self.active_sdi_count = self.active_sdi_count_max

      self.navigation_selection = selection
      self._projected_navigation_revision = selection.projection_revision
      self.carrot_navi_projection_revision = selection.projection_revision
      return True

  def _clear_carrot_navi_control(self):
    projected_generation = getattr(self, "carrot_navi_projected_road_limit_generation", None)
    self.active_count = 0
    self.active_sdi_count = 0
    self.carrot_navi_has_control = False
    self.carrot_navi_road_limit_valid = False
    self.carrot_navi_off_route = False
    self.carrot_navi_control = None
    self.carrot_navi_projection_revision = getattr(self, "carrot_navi_projection_revision", 0) + 1
    self._reset_carrot_navi_sequences("")
    self.roadcate = 0
    if projected_generation is not None and self.road_limit_write_generation == projected_generation:
      self._write_road_limit_speed(0, "carrot_navi_clear")
    self.carrot_navi_projected_road_limit_generation = None

    self.nSdiType = self.nSdiBlockType = self.nSdiPlusType = self.nSdiPlusBlockType = -1
    self.nSdiSection = -1
    self.nSdiSpeedLimit = self.nSdiDist = 0
    self.nSdiBlockSpeed = self.nSdiBlockDist = 0
    self.nSdiPlusSpeedLimit = self.nSdiPlusDist = 0
    self.nSdiPlusBlockSpeed = self.nSdiPlusBlockDist = 0
    self.xSpdType = -1
    self.xSpdLimit = self.xSpdDist = 0

    self.nTBTTurnType = self.nTBTTurnTypeNext = -1
    self.nTBTDist = self.nTBTDistNext = 0
    self.xTurnInfo = self.xTurnInfoNext = -1
    self.xDistToTurn = self.xDistToTurnNext = 0
    self.navType = self.navTypeNext = "invalid"
    self.navModifier = self.navModifierNext = ""

    self.nGoPosDist = self.nGoPosTime = 0
    self.last_update_gps_time_navi = 0
    self.szPosRoadName = ""
    self._clear_carrot_navi_traffic()

  def _apply_carrot_navi_speed(self, navi: CarrotNaviControl, fresh=True, project_road_limit=True):
    speed = navi.speed
    speed_active = fresh and speed.present
    if project_road_limit:
      projected_generation = getattr(self, "carrot_navi_projected_road_limit_generation", None)
      self.carrot_navi_road_limit_valid = speed_active and speed.road_limit_kph is not None
      if self.carrot_navi_road_limit_valid:
        self.carrot_navi_projected_road_limit_generation = self._write_road_limit_speed(
          speed.road_limit_kph, "carrot_navi",
        )
      else:
        if projected_generation is not None and self.road_limit_write_generation == projected_generation:
          self._write_road_limit_speed(0, "carrot_navi_clear")
        self.carrot_navi_projected_road_limit_generation = None

    if speed_active and speed.section_active:
      self.nSdiType = 4
      self.nSdiSpeedLimit = speed.section_speed_limit_kph
      self.nSdiDist = speed.section_remaining_distance_m
      self.nSdiSection = 1
      self.nSdiBlockType = 2
      self.nSdiBlockSpeed = speed.section_speed_limit_kph
      self.nSdiBlockDist = speed.section_remaining_distance_m
    elif speed_active and speed.sdi_present:
      self.nSdiType = speed.sdi_type
      self.nSdiSpeedLimit = speed.sdi_speed_limit_kph
      self.nSdiDist = speed.sdi_distance_m
      self.nSdiSection = speed.sdi_section_type
      self.nSdiBlockType = speed.sdi_block_type
      self.nSdiBlockSpeed = speed.sdi_block_speed_kph
      self.nSdiBlockDist = speed.sdi_block_distance_m
    else:
      self.nSdiType = -1
      self.nSdiSpeedLimit = 0
      self.nSdiDist = 0
      self.nSdiSection = -1
      self.nSdiBlockType = -1
      self.nSdiBlockSpeed = 0
      self.nSdiBlockDist = 0

    if speed_active and speed.secondary_sdi_present:
      self.nSdiPlusType = speed.secondary_sdi_type
      self.nSdiPlusSpeedLimit = speed.secondary_sdi_speed_limit_kph
      self.nSdiPlusDist = speed.secondary_sdi_distance_m
      self.nSdiPlusBlockType = speed.secondary_sdi_block_type
      self.nSdiPlusBlockSpeed = speed.secondary_sdi_block_speed_kph
      self.nSdiPlusBlockDist = speed.secondary_sdi_block_distance_m
    else:
      self.nSdiPlusType = -1
      self.nSdiPlusSpeedLimit = 0
      self.nSdiPlusDist = 0
      self.nSdiPlusBlockType = -1
      self.nSdiPlusBlockSpeed = 0
      self.nSdiPlusBlockDist = 0
    self._update_sdi()

  def _apply_carrot_navi_vehicle(self, navi: CarrotNaviControl):
    vehicle = navi.vehicle
    self.carrot_navi_vehicle_sequence = vehicle.sequence
    if not vehicle.present:
      self.last_update_gps_time_navi = 0
      self.szPosRoadName = ""
      return

    now = time.monotonic()
    self.vpPosPointLatNavi = vehicle.latitude
    self.vpPosPointLonNavi = vehicle.longitude
    self.nPosAngle = vehicle.heading_deg
    self.nPosSpeed = vehicle.speed_kph
    self.szPosRoadName = vehicle.road_name
    self.last_update_gps_time_navi = self.last_calculate_gps_time = now

  def _apply_carrot_navi_route(self, navi: CarrotNaviControl):
    route = navi.route
    self.carrot_navi_route_sequence = route.sequence
    self.nGoPosDist = route.remaining_distance_m if route.present else 0
    self.nGoPosTime = route.remaining_time_sec if route.present else 0

  def _apply_carrot_navi_traffic(self, navi: CarrotNaviControl):
    traffic = navi.traffic
    if not traffic.present or not traffic.visible or not traffic.lamp or traffic.remain_sec <= 0:
      self._clear_carrot_navi_traffic()
      return

    params_memory = getattr(self, "params_memory", None)
    if params_memory is None:
      return
    value = {
      "distance": traffic.distance_m,
      "lamp": traffic.lamp,
      "remain": traffic.remain_sec,
      "source": traffic.source,
      "ts": time.monotonic(),
    }
    try:
      params_memory.put_nonblocking("TrafficLight", json.dumps(value))
      self.carrot_navi_traffic_active = True
    except Exception:
      pass

  def _apply_carrot_navi_guidance(self, navi: CarrotNaviControl, force=False,
                                  current_fresh=True, next_fresh=True,
                                  current_revision_changed=False, next_revision_changed=False):
    current = navi.current
    current_changed = force or current_revision_changed or current.sequence != self.carrot_navi_current_sequence
    next_guidance = navi.next
    next_changed = force or next_revision_changed or next_guidance.sequence != self.carrot_navi_next_sequence
    if not current_changed and not next_changed:
      return

    old_current_dist = self.xDistToTurn
    old_next_dist = self.xDistToTurnNext
    if current_changed:
      self.carrot_navi_current_sequence = current.sequence
      current_active = current.present and current_fresh
      self.nTBTDist = current.distance_m if current_active else 0
      self.nTBTTurnType = current.turn_type if current_active else -1
      self.szTBTMainText = current.main_text if current_active else ""
      self.szNearDirName = current.near_direction if current_active else ""
      self.szFarDirName = current.far_direction if current_active else ""

    if next_changed:
      self.carrot_navi_next_sequence = next_guidance.sequence
      next_active = next_guidance.present and next_fresh
      self.nTBTDistNext = next_guidance.distance_m if next_active else 0
      self.nTBTTurnTypeNext = next_guidance.turn_type if next_active else -1
      self.szTBTMainTextNext = next_guidance.main_text if next_active else ""

    self._update_tbt()
    if current_changed and not (current.present and current_fresh):
      self.xTurnInfo = -1
      self.xDistToTurn = 0
    if next_changed and not (next_guidance.present and next_fresh):
      self.xTurnInfoNext = -1
      self.xDistToTurnNext = 0
    if not current_changed:
      self.xDistToTurn = old_current_dist
    if not next_changed:
      self.xDistToTurnNext = old_next_dist

  @staticmethod
  def _carrot_navi_item_fresh(present, received_mono_time_nanos, now_s):
    if not present or received_mono_time_nanos <= 0:
      return False
    age_s = now_s - received_mono_time_nanos / 1_000_000_000.0
    return 0.0 <= age_s < V2_ITEM_TTL_S

  @staticmethod
  def _raw_navigation_value(obj, name, default=None):
    if isinstance(obj, dict):
      return obj.get(name, default)
    try:
      return getattr(obj, name)
    except Exception:
      return default

  @classmethod
  def _raw_navigation_meta(cls, data, item_name):
    item = cls._raw_navigation_value(data, item_name)
    meta = cls._raw_navigation_value(item, "meta")
    try:
      present = bool(cls._raw_navigation_value(meta, "present", False))
      sequence = max(0, int(cls._raw_navigation_value(meta, "sequence", 0)))
      received_ns = max(0, int(cls._raw_navigation_value(meta, "receivedMonoTimeNanos", 0)))
    except (TypeError, ValueError, OverflowError):
      return False, 0, 0
    return present, sequence, received_ns

  def _v2_navigation_revision(self, raw, navi):
    try:
      generation = max(0, int(self._raw_navigation_value(raw, "generation", 0)))
    except (TypeError, ValueError, OverflowError):
      generation = 0
    metas = tuple(
      self._raw_navigation_meta(raw, name)
      for name in (
        "speed", "guidanceCurrent", "guidanceNext", "laneCurrent",
        "route", "vehicle", "trafficSignal", "navigationStatus",
      )
    )
    return (generation, metas, navi)

  def _v2_navigation_snapshot(self, raw, navi, now_s):
    session_id = navi.session_id
    revision = self._v2_navigation_revision(raw, navi)
    if self._v2_navigation_revisions.get(session_id) == revision:
      return None
    self._v2_navigation_revisions[session_id] = revision
    sequence = self._v2_navigation_sequences.get(session_id, 0) + 1
    self._v2_navigation_sequences[session_id] = sequence

    speed_fresh = self._carrot_navi_item_fresh(
      navi.speed.present, navi.speed.received_mono_time_nanos, now_s,
    )
    speed_received = navi.speed.received_mono_time_nanos / 1_000_000_000.0 if speed_fresh else None
    safety = None
    secondary = None
    if speed_fresh and navi.speed.section_active:
      safety = SafetyItem(
        type=4,
        distance_m=navi.speed.section_remaining_distance_m,
        speed_limit_kph=navi.speed.section_speed_limit_kph,
        received_mono_s=speed_received,
        reason="section",
        section=True,
        section_type=1,
        block_type=2,
        block_speed_kph=navi.speed.section_speed_limit_kph,
        block_distance_m=navi.speed.section_remaining_distance_m,
      )
    elif speed_fresh and navi.speed.sdi_present:
      safety = SafetyItem(
        type=navi.speed.sdi_type,
        distance_m=navi.speed.sdi_distance_m,
        speed_limit_kph=navi.speed.sdi_speed_limit_kph,
        received_mono_s=speed_received,
        reason="bump" if navi.speed.sdi_type == 22 else "cam",
        section_type=navi.speed.sdi_section_type,
        block_type=navi.speed.sdi_block_type,
        block_speed_kph=navi.speed.sdi_block_speed_kph,
        block_distance_m=navi.speed.sdi_block_distance_m,
      )
    if speed_fresh and navi.speed.secondary_sdi_present:
      secondary = SafetyItem(
        type=navi.speed.secondary_sdi_type,
        distance_m=navi.speed.secondary_sdi_distance_m,
        speed_limit_kph=navi.speed.secondary_sdi_speed_limit_kph,
        received_mono_s=speed_received,
        reason="bump" if navi.speed.secondary_sdi_type == 22 else "cam",
        section_type=navi.speed.secondary_sdi_section_type,
        block_type=navi.speed.secondary_sdi_block_type,
        block_speed_kph=navi.speed.secondary_sdi_block_speed_kph,
        block_distance_m=navi.speed.secondary_sdi_block_distance_m,
      )

    current_fresh = self._carrot_navi_item_fresh(
      navi.current.present, navi.current.received_mono_time_nanos, now_s,
    )
    current = NavigationInstruction(
      present=current_fresh,
      turn_type=navi.current.turn_type if current_fresh else -1,
      distance_m=navi.current.distance_m if current_fresh else 0.0,
      main_text=navi.current.main_text if current_fresh else "",
      near_direction=navi.current.near_direction if current_fresh else "",
      far_direction=navi.current.far_direction if current_fresh else "",
      received_mono_s=(
        navi.current.received_mono_time_nanos / 1_000_000_000.0 if current_fresh else None
      ),
    )
    next_fresh = self._carrot_navi_item_fresh(
      navi.next.present, navi.next.received_mono_time_nanos, now_s,
    )
    next_instruction = NavigationInstruction(
      present=next_fresh,
      turn_type=navi.next.turn_type if next_fresh else -1,
      distance_m=navi.next.distance_m if next_fresh else 0.0,
      main_text=navi.next.main_text if next_fresh else "",
      near_direction=navi.next.near_direction if next_fresh else "",
      far_direction=navi.next.far_direction if next_fresh else "",
      received_mono_s=navi.next.received_mono_time_nanos / 1_000_000_000.0 if next_fresh else None,
    )

    lane_fresh = self._carrot_navi_item_fresh(
      navi.lane_present, navi.lane_received_mono_time_nanos, now_s,
    )
    last_category = self._v2_last_valid_categories.get(session_id)
    if lane_fresh and navi.road_category_valid and navi.road_category is not None:
      road_category = navi.road_category
      category_received = navi.lane_received_mono_time_nanos / 1_000_000_000.0
      self._v2_last_valid_categories[session_id] = (road_category, category_received)
    elif lane_fresh and last_category is not None and 0.0 <= now_s - last_category[1] < V2_ITEM_TTL_S:
      road_category, category_received = last_category
    else:
      road_category = None
      category_received = None

    route_present, _, route_received_ns = self._raw_navigation_meta(raw, "route")
    route_fresh = self._carrot_navi_item_fresh(route_present, route_received_ns, now_s)
    route_fresh = route_fresh and navi.route.present
    route_received = route_received_ns / 1_000_000_000.0 if route_fresh else None
    status_present, _, status_received_ns = self._raw_navigation_meta(raw, "navigationStatus")
    status_fresh = self._carrot_navi_item_fresh(status_present, status_received_ns, now_s)
    status_received = status_received_ns / 1_000_000_000.0 if status_fresh else None
    guidance_receipts = [
      receipt for receipt in (
        current.received_mono_s if current.present else None,
        next_instruction.received_mono_s if next_instruction.present else None,
        status_received if status_fresh and navi.guidance_active else None,
      )
      if receipt is not None
    ]
    owner_received = max(guidance_receipts) if guidance_receipts else None

    vehicle_present, _, vehicle_received_ns = self._raw_navigation_meta(raw, "vehicle")
    vehicle_fresh = self._carrot_navi_item_fresh(vehicle_present, vehicle_received_ns, now_s)
    vehicle_fresh = vehicle_fresh and navi.vehicle.present
    vehicle_received = vehicle_received_ns / 1_000_000_000.0 if vehicle_fresh else None
    traffic_present, _, traffic_received_ns = self._raw_navigation_meta(raw, "trafficSignal")
    traffic_fresh = self._carrot_navi_item_fresh(traffic_present, traffic_received_ns, now_s)
    traffic_fresh = traffic_fresh and navi.traffic.present
    traffic_received = traffic_received_ns / 1_000_000_000.0 if traffic_fresh else None

    control = NavigationControlState(
      current=current,
      next=next_instruction,
      safety=safety,
      secondary_safety=secondary,
      speed_present=speed_fresh,
      speed_received_mono_s=speed_received,
      road_limit_kph=navi.speed.road_limit_kph if speed_fresh else None,
      road_limit_received_mono_s=speed_received if speed_fresh and navi.speed.road_limit_kph is not None else None,
      road_category=road_category,
      road_category_received_mono_s=category_received,
      route_present=route_fresh,
      route_received_mono_s=route_received,
      remaining_distance_m=navi.route.remaining_distance_m if route_fresh else 0.0,
      remaining_time_s=navi.route.remaining_time_sec if route_fresh else 0.0,
      off_route=navi.off_route if status_fresh else False,
      status_present=status_fresh,
      status_received_mono_s=status_received,
      route_points=navi.route.polyline if route_fresh else (),
      position_present=vehicle_fresh,
      position_received_mono_s=vehicle_received,
      position_latitude=navi.vehicle.latitude if vehicle_fresh else 0.0,
      position_longitude=navi.vehicle.longitude if vehicle_fresh else 0.0,
      position_heading_deg=navi.vehicle.heading_deg if vehicle_fresh else 0.0,
      position_speed_kph=navi.vehicle.speed_kph if vehicle_fresh else 0.0,
      position_road_name=navi.vehicle.road_name if vehicle_fresh else "",
      traffic_present=traffic_fresh,
      traffic_received_mono_s=traffic_received,
      traffic_visible=navi.traffic.visible if traffic_fresh else False,
      traffic_distance_m=navi.traffic.distance_m if traffic_fresh else 0.0,
      traffic_source=navi.traffic.source if traffic_fresh else "",
      traffic_lamp=navi.traffic.lamp if traffic_fresh else "",
      traffic_remain_s=navi.traffic.remain_sec if traffic_fresh else 0,
    )
    return NavigationSnapshot(
      source=NavigationSource.CARROT_NAVI_V2,
      session_id=session_id,
      sequence=sequence,
      lifecycle=NavigationLifecycle.GUIDING if owner_received is not None else NavigationLifecycle.IDLE,
      received_mono_s=now_s,
      activation_epoch=0,
      control=control,
      owner_received_mono_s=owner_received,
    )

  def _update_carrot_navi(self, sm, now_s=None):
    now_s = time.monotonic() if now_s is None else now_s
    self._ensure_navigation_sources()
    service_active = sm.alive['carrotNavi'] and sm.valid['carrotNavi']
    raw = sm['carrotNavi'] if service_active else None
    navi = parse_carrot_navi_control(raw) if service_active else None
    self.carrot_navi_control = navi
    if navi is not None and navi.session_id:
      snapshot = self._v2_navigation_snapshot(raw, navi, now_s)
      if snapshot is not None:
        self.accept_navigation_snapshot(snapshot)

    selection = self.navigation_sources.select(now_s)
    self._project_navigation_selection(selection)
    snapshot = selection.snapshot
    has_control = bool(snapshot is not None and (
      snapshot.control.speed_present
      or snapshot.control.current.present
      or snapshot.control.next.present
    ))
    if has_control:
      self.active_count = 80
      self.active_sdi_count = self.active_sdi_count_max
    return has_control

  def _update_gps(self, v_ego, sm, gps_service):
    gps = sm[gps_service]
    #print(f"location = {sm.valid[llk]}, {sm.updated[llk]}, {sm.recv_frame[llk]}, {sm.recv_time[llk]}")
    if not sm.updated['carState'] or not sm.updated['carControl']: # or not sm.updated[llk]:
      return self.nPosAngle
    CS = sm['carState']
    CC = sm['carControl']
    self.gps_valid = sm.updated[gps_service] and gps.hasFix

    now = time.monotonic()
    gps_updated_phone = (now - self.last_update_gps_time_phone) < 3
    gps_updated_navi = (now - self.last_update_gps_time_navi) < 3

    bearing = self.nPosAngle
    if gps_updated_navi:
      bearing = self.nPosAngle
    elif gps_updated_phone:
      bearing = self.nPosAnglePhone
    elif self.gps_valid:
      bearing = self.nPosAngle = gps.bearingDeg

    self.bearing_offset = 0.0
    # TODO:  여기서 bearing 보정로직 추가 필요함. CC.orientationNED[2]를 이용하여.

    #print(f"bearing = {bearing:.1f}, posA=={self.nPosAngle:.1f}, posP=={self.nPosAnglePhone:.1f}, offset={self.bearing_offset:.1f}, {gps_updated_phone}, {gps_updated_navi}")
    gpsDelayTimeAdjust = 0.0
    if gps_updated_navi:
      gpsDelayTimeAdjust = 0 #1.0

    external_gps_update_timedout = not (gps_updated_phone or gps_updated_navi)
    #print(f"gps_valid = {self.gps_valid}, bearing = {bearing:.1f}, pos = {location.positionGeodetic.value[0]:.6f}, {location.positionGeodetic.value[1]:.6f}")
    if self.gps_valid and external_gps_update_timedout:    # 내부GPS가 작동하고 carrotman으로부터 gps신호가 없는경우
      self.vpPosPointLatNavi = gps.latitude
      self.vpPosPointLonNavi = gps.longitude
      self.last_calculate_gps_time = now #sm.recv_time[llk]
    elif gps_updated_navi:  # carrot navi로부터 gps신호가 수신되는 경우..
      if abs(self.bearing_measured - bearing) < 0.1:
          self.diff_angle_count += 1
      else:
          self.diff_angle_count = 0
      self.bearing_measured = bearing

      if self.diff_angle_count > 5: # 조향각도변화가 거의 없을때만 업데이트
        diff_angle = (self.nPosAngle - bearing) % 360
        if diff_angle > 180:
          diff_angle -= 360
        self.bearing_offset = self.bearing_offset * 0.9 + diff_angle * 0.1

    bearing_calculated = (bearing + self.bearing_offset) % 360

    dt = now - self.last_calculate_gps_time
    #print(f"dt = {dt:.1f}, {self.vpPosPointLatNavi}, {self.vpPosPointLonNavi}")
    if dt > 5.0:
      self.vpPosPointLat, self.vpPosPointLon = 0.0, 0.0
    elif dt == 0:
      self.vpPosPointLat, self.vpPosPointLon = self.vpPosPointLatNavi, self.vpPosPointLonNavi
    else:
      self.vpPosPointLat, self.vpPosPointLon = self.estimate_position(float(self.vpPosPointLatNavi), float(self.vpPosPointLonNavi), v_ego, bearing_calculated, dt + gpsDelayTimeAdjust)

    #self.debugText = " {} {:.1f},{:.1f}={:.1f}+{:.1f}".format(self.active_sdi_count, self.nPosAngle, bearing_calculated, bearing, self.bearing_offset)
    #print("nPosAngle = {:.1f},{:.1f} = {:.1f}+{:.1f}".format(self.nPosAngle, bearing_calculated, bearing, self.bearing_offset))

    return float(bearing_calculated)


  def estimate_position(self, lat, lon, speed, angle, dt):
    R = 6371000
    angle_rad = math.radians(angle)
    delta_d = speed * dt
    delta_lat = delta_d * math.cos(angle_rad) / R
    new_lat = lat + math.degrees(delta_lat)
    delta_lon = delta_d * math.sin(angle_rad) / (R * math.cos(math.radians(lat)))
    new_lon = lon + math.degrees(delta_lon)

    return new_lat, new_lon

  def update_auto_turn(self, v_ego_kph, sm, x_turn_info, x_dist_to_turn, check_steer=False):
    turn_speed = self.autoTurnControlSpeedTurn
    fork_speed = self.nRoadLimitSpeed
    stop_speed = 1
    turn_dist_for_speed = self.autoTurnControlTurnEnd * turn_speed / 3.6 # 5
    fork_dist_for_speed = self.autoTurnControlTurnEnd * fork_speed / 3.6 # 5
    stop_dist_for_speed = 5
    start_fork_dist = np.interp(self.nRoadLimitSpeed, [30, 50, 100], [160, 200, 350])
    start_turn_dist = np.interp(self.nTBTNextRoadWidth, [5, 10], [43, 60])
    turn_info_mapping = {
        1: {"type": "turn left", "speed": turn_speed, "dist": turn_dist_for_speed, "start": start_fork_dist},
        2: {"type": "turn right", "speed": turn_speed, "dist": turn_dist_for_speed, "start": start_fork_dist},
        5: {"type": "straight", "speed": turn_speed, "dist": turn_dist_for_speed, "start": start_turn_dist},
        3: {"type": "fork left", "speed": fork_speed, "dist": fork_dist_for_speed, "start": start_fork_dist},
        4: {"type": "fork right", "speed": fork_speed, "dist": fork_dist_for_speed, "start": start_fork_dist},
        6: {"type": "straight", "speed": fork_speed, "dist": fork_dist_for_speed, "start": start_fork_dist},
        7: {"type": "straight", "speed": stop_speed, "dist": stop_dist_for_speed, "start": 1000},
        8: {"type": "straight", "speed": stop_speed, "dist": stop_dist_for_speed, "start": 1000},
    }

    default_mapping = {"type": "none", "speed": 0, "dist": 0, "start": 1000}

    mapping = turn_info_mapping.get(x_turn_info, default_mapping)

    atc_type = mapping["type"]
    atc_speed = mapping["speed"]
    atc_dist = mapping["dist"]
    atc_start_dist = mapping["start"]

    if x_dist_to_turn > atc_start_dist:
      atc_type += " prepare"
      if check_steer:
        self.atc_activate_count = min(0, self.atc_activate_count - 1)
    else:
      if check_steer:
        self.atc_activate_count = max(0, self.atc_activate_count + 1)
      if atc_type in ["turn left", "turn right"] and x_dist_to_turn > start_turn_dist:
        atc_type = "atc left" if atc_type == "turn left" else "atc right"

    if self.autoTurnMapChange > 0 and check_steer:
      #print(f"x_dist_to_turn: {x_dist_to_turn}, atc_start_dist: {atc_start_dist}")
      #print(f"atc_activate_count: {self.atc_activate_count}")
      if self.atc_activate_count == 2:
        self.carrotCmdIndex += 100
        self.carrotCmd = "DISPLAY";
        self.carrotArg = "MAP";
      elif self.atc_activate_count == -50:
        self.carrotCmdIndex += 100
        self.carrotCmd = "DISPLAY";
        self.carrotArg = "ROAD";

    if check_steer:
      if 0 <= x_dist_to_turn < atc_start_dist and atc_type in ["fork left", "fork right"]:
        if not self.atc_paused:
          steering_pressed = sm["carState"].steeringPressed
          steering_torque = sm["carState"].steeringTorque
          if steering_pressed and steering_torque < 0 and atc_type in ["fork left", "atc left"]:
            self.atc_paused = True
          elif steering_pressed and steering_torque > 0 and atc_type in ["fork right", "atc right"]:
            self.atc_paused = True
      else:
        self.atc_paused = False

      if self.atc_paused:
        atc_type += " canceled"

    atc_desired = 250
    if atc_speed > 0 and x_dist_to_turn > 0:
      decel = self.autoNaviSpeedDecelRate
      safe_sec = 2.0
      atc_desired = min(atc_desired, self.calculate_current_speed(x_dist_to_turn - atc_dist, atc_speed, safe_sec, decel))


    return atc_desired, atc_type, atc_speed, atc_dist

  def update_nav_instruction(self, sm):
    if sm.alive['navInstruction'] and sm.valid['navInstruction']:
      msg_nav = sm['navInstruction']

      self.nGoPosDist = int(msg_nav.distanceRemaining)
      self.nGoPosTime = int(msg_nav.timeRemaining)
      if self.active_kisa_count <= 0 and msg_nav.speedLimit > 0:
        self._write_road_limit_speed(
          max(30, round(msg_nav.speedLimit * CV.MS_TO_KPH)), "nav_instruction",
        )
      self.xDistToTurn = int(msg_nav.maneuverDistance)
      self.szTBTMainText = msg_nav.maneuverPrimaryText
      self.xTurnInfo = -1
      for key, value in nav_type_mapping.items():
        if value[0] == msg_nav.maneuverType and value[1] == msg_nav.maneuverModifier:
          self.xTurnInfo = value[2]
          break

      self.debugText = f"{self.nRoadLimitSpeed if self.is_metric else self.nRoadLimitSpeed * CV.KPH_TO_MPH:.0f},{msg_nav.maneuverType},{msg_nav.maneuverModifier} "
      #print(msg_nav)
      #print(f"navInstruction: {self.xTurnInfo}, {self.xDistToTurn}, {self.szTBTMainText}")

  def update_kisa(self, data):
    self.active_kisa_count = 100
    if "kisawazecurrentspd" in data:
      pass
    if "kisawazeroadspdlimit" in data:
      road_limit_speed = data["kisawazeroadspdlimit"]
      if road_limit_speed > 0:
        print(f"kisawazeroadspdlimit: {road_limit_speed} km/h")
        if not self.is_metric:
          road_limit_speed *= CV.MPH_TO_KPH
        self._write_road_limit_speed(road_limit_speed, "kisa_waze")
    if "kisawazealert" in data:
      pass
    if "kisawazeendalert" in data:
      pass
    if "kisawazeroadname" in data:
      print(f"kisawazeroadname: {data['kisawazeroadname']}")
      self.szPosRoadName = data["kisawazeroadname"]
    if "kisawazereportid" in data and "kisawazealertdist" in data:
      id_str = data["kisawazereportid"]
      dist_str = data["kisawazealertdist"].lower()
      import re
      match = re.search(r'(\d+)', dist_str)
      distance = int(match.group(1)) if match else 0
      if not self.is_metric:
        distance = int(distance * 0.3048)
      print(f"{id_str}: {distance} m")
      xSpdType = -1
      if 'camera' in id_str:
        xSpdType = 101    # 101: waze speed cam, 100: police
      elif 'police' in id_str:
        xSpdType = 100

      if xSpdType >= 0:
        self.kisa_safety_limit = self.nRoadLimitSpeed * self.autoNaviSpeedSafetyFactor if self.nRoadLimitSpeed > 0 else 0
        self.kisa_safety_distance = distance
        self.kisa_safety_type = xSpdType

  def update_navi(self, remote_ip, sm, pm, vturn_speed, coords, distances, route_speed, gps_service,
                  navigation_prepared=False):

    self.debugText = ""
    self.update_params()
    if navigation_prepared:
      selection = getattr(self, "navigation_selection", None)
      snapshot = None if selection is None else selection.snapshot
      carrot_navi_active = bool(snapshot is not None and (
        snapshot.control.speed_present
        or snapshot.control.current.present
        or snapshot.control.next.present
      ))
    else:
      carrot_navi_active = self._update_carrot_navi(sm)
    if sm.alive['carState'] and sm.alive['selfdriveState']:
      CS = sm['carState']
      v_ego = CS.vEgo
      v_ego_kph = v_ego * 3.6
      distanceTraveled = sm['selfdriveState'].distanceTraveled
      delta_dist = distanceTraveled - self.totalDistance
      self.totalDistance = distanceTraveled
      if CS.speedLimit > 0 and self.active_carrot <= 1:
        self._write_road_limit_speed(CS.speedLimit, "vehicle")
    else:
      v_ego = v_ego_kph = 0
      delta_dist = 0
      CS = None

    road_speed_limit_changed = True if self.nRoadLimitSpeed != self.nRoadLimitSpeed_last else False
    self.nRoadLimitSpeed_last = self.nRoadLimitSpeed
    #self.bearing = self.nPosAngle #self._update_gps(v_ego, sm)
    self.bearing = self._update_gps(v_ego, sm, gps_service)

    self.xSpdDist = max(self.xSpdDist - delta_dist, -1000)
    self.kisa_safety_distance = max(self.kisa_safety_distance - delta_dist, -1000)
    self.xDistToTurn = self.xDistToTurn - delta_dist
    self.xDistToTurnNext = self.xDistToTurnNext - delta_dist
    self.active_count = max(self.active_count - 1, 0)
    self.active_sdi_count = max(self.active_sdi_count - 1, 0)
    self.active_kisa_count = max(self.active_kisa_count - 1, 0)
    if self.active_kisa_count > 0:
      self.active_carrot = 2

    elif self.active_count > 0:
      self.active_carrot = 2 if self.active_sdi_count > 0 else 1
    else:
      self.active_carrot = 0

    hda_limit_kph = 0.0 if CS is None else CS.speedLimit
    hda_distance_m = 0.0 if CS is None else CS.speedLimitDistance
    safety_decision = self._apply_navigation_safety(hda_limit_kph, hda_distance_m)

    limit_speed = 200
    road_limit_valid = not carrot_navi_active or self.carrot_navi_road_limit_valid
    if self.autoRoadSpeedLimitOffset >= 0 and self.active_carrot>=2 and road_limit_valid:
      if self.nRoadLimitSpeed >= 30:
        road_speed_limit_offset = self.autoRoadSpeedLimitOffset
        if not self.is_metric:
          road_speed_limit_offset *= CV.KPH_TO_MPH
        limit_speed = self.nRoadLimitSpeed + road_speed_limit_offset

    if self.active_carrot <= 1:
      self.xSpdType = self.navType = self.xTurnInfo = self.xTurnInfoNext = -1
      self.nSdiType = self.nSdiBlockType = self.nSdiPlusBlockType = -1
      self.nTBTTurnType = self.nTBTTurnTypeNext = -1
      self.roadcate = 8
      self.nGoPosDist = 0
    if self.active_carrot <= 1 or self.active_kisa_count > 0:
      self.update_nav_instruction(sm)

    if self.xSpdType < 0 or (self.xSpdType not in [100,101] and self.xSpdDist <= 0) or (self.xSpdType in [100,101] and self.xSpdDist < -250):
      self.xSpdType = -1
      self.xSpdDist = self.xSpdLimit = 0
    if self.xTurnInfo < 0 or self.xDistToTurn < -50:
      if self.xDistToTurn > 0:
        self.xDistToTurn = 0
      self.xTurnInfo = -1
      self.xDistToTurnNext = 0
      self.xTurnInfoNext = -1

    sdi_speed = 250
    safety_source = "none"
    if safety_decision is not None:
      safety_source = safety_decision.reason
      safe_sec = self.autoNaviSpeedBumpTime if safety_decision.type == 22 else self.autoNaviSpeedCtrlEnd
      sdi_speed = min(sdi_speed, self.calculate_current_speed(
        self._safety_distance_for_speed(safety_decision),
        safety_decision.limit_kph,
        safe_sec,
        self.autoNaviSpeedDecelRate,
      ))
      if safety_decision.type == 4 or (
        safety_decision.provider == "kisa" and safety_decision.distance_m <= 0.0
      ):
        sdi_speed = safety_decision.limit_kph

    #print(f"sdi_speed: {sdi_speed}, hda_active: {hda_active}, xSpdType: {self.xSpdType}, xSpdDist: {self.xSpdDist}, active_carrot: {self.active_carrot}, v_ego_kph: {v_ego_kph}, nRoadLimitSpeed: {self.nRoadLimitSpeed}")
    ### TBT 속도제어
    atc_desired, self.atcType, self.atcSpeed, self.atcDist = self.update_auto_turn(v_ego*3.6, sm, self.xTurnInfo, self.xDistToTurn, True)
    atc_desired_next, _, _, _ = self.update_auto_turn(v_ego*3.6, sm, self.xTurnInfoNext, self.xDistToTurnNext, False)

    if self.nSdiType  >= 0: # or self.active_carrot > 0:
      pass
      # self.debugText = (
      #   f"Atc:{atc_desired:.1f}," +
      #   f"{self.xTurnInfo}:{self.xDistToTurn:.1f}, " +
      #   f"I({self.nTBTNextRoadWidth},{self.roadcate}) " +
      #   f"Atc2:{atc_desired_next:.1f}," +
      #   f"{self.xTurnInfoNext},{self.xDistToTurnNext:.1f}"
      # )
      #self.debugText = "" #f" {self.nSdiType}/{self.nSdiSpeedLimit}/{self.nSdiDist},BLOCK:{self.nSdiBlockType}/{self.nSdiBlockSpeed}/{self.nSdiBlockDist}, PLUS:{self.nSdiPlusType}/{self.nSdiPlusSpeedLimit}/{self.nSdiPlusDist}"
    #elif self.nGoPosDist > 0 and self.active_carrot > 1:
    #  self.debugText = " 목적지:{:.1f}km/{:.1f}분 남음".format(self.nGoPosDist/1000., self.nGoPosTime / 60)
    else:
      #self.debugText = ""
      pass

    if self.autoTurnControl not in [2, 3]:    # auto turn speed control
      atc_desired = atc_desired_next = 250

    if self.autoTurnControl not in [1,2]:    # auto turn control
      self.atcType = "none"


    speed_n_sources = [
      (atc_desired, "atc"),
      (atc_desired_next, "atc2"),
      (sdi_speed, safety_source),
      (limit_speed, "road"),
    ]
    if self.turnSpeedControlMode in [1,2]:
      speed_n_sources.append((max(abs(vturn_speed), self.autoCurveSpeedLowerLimit), "vturn"))

    route_speed = max(route_speed * self.mapTurnSpeedFactor, self.autoCurveSpeedLowerLimit)
    if self.turnSpeedControlMode == 2:
      if -500 < self.xDistToTurn < 500:
        speed_n_sources.append((route_speed, "route"))
    elif self.turnSpeedControlMode in [3, 4]:
      speed_n_sources.append((route_speed, "route"))
      #speed_n_sources.append((self.calculate_current_speed(dist, speed * self.mapTurnSpeedFactor, 0, 1.2), "route"))

    model_turn_speed = max(sm['modelV2'].meta.modelTurnSpeed, self.autoCurveSpeedLowerLimit)
    if model_turn_speed < 200 and abs(vturn_speed) < 120:
      speed_n_sources.append((model_turn_speed, "model"))

    desired_speed, source = self._select_final_speed(speed_n_sources)

    if CS is not None:
      if source != self.source_last:
        self.gas_override_speed = 0
        self.gas_pressed_state = CS.gasPressed
      if CS.vEgo < 0.1 or desired_speed > 150 or source in ["cam", "section", "police"] or CS.brakePressed or road_speed_limit_changed:
        self.gas_override_speed = 0
      elif CS.gasPressed and not self.gas_pressed_state:
        self.gas_override_speed = max(v_ego_kph, self.gas_override_speed)
      else:
        self.gas_pressed_state = False
      self.source_last = source

      if desired_speed < self.gas_override_speed:
        source = "gas"
        desired_speed = self.gas_override_speed

      self.debugText += f"route={route_speed:.1f}"#f"desired={desired_speed:.1f},{source},g={self.gas_override_speed:.0f}"

    left_spd_sec = 100
    left_tbt_sec = 100
    if self.autoNaviCountDownMode > 0:
      if self.xSpdType == 22 and self.autoNaviCountDownMode == 1: # speed bump
        pass
      else:
        if self.xSpdDist > 0:
          left_spd_sec = min(self.left_spd_sec, int(max(self.xSpdDist - v_ego, 1) / max(1, v_ego) + 0.5))

      if self.xDistToTurn > 0:
        left_tbt_sec = min(self.left_tbt_sec, int(max(self.xDistToTurn - v_ego, 1) / max(1, v_ego) + 0.5))

    self.left_spd_sec = left_spd_sec
    self.left_tbt_sec = left_tbt_sec

    left_sec = min(left_spd_sec, left_tbt_sec)

    if left_sec > 11:
      self.left_sec = 100
      self.max_left_sec = 100
    else:
      self.sdi_inform = True if source in ["cam", "hda"] else False
      self.max_left_sec = min(11, max(6, int(v_ego_kph/10) + 1))

    if left_sec != self.left_sec:
      if left_sec == self.max_left_sec and self.sdi_inform:
        self.carrot_left_sec = 11
      elif 1 <= left_sec < self.max_left_sec:
        self.carrot_left_sec = left_sec
      elif left_sec == 0 and self.left_sec == 1:
        self.carrot_left_sec = left_sec

      self.left_sec = left_sec


    self._update_cmd()
    msg = messaging.new_message('carrotMan')
    msg.valid = True
    msg.carrotMan.activeCarrot = self.active_carrot
    msg.carrotMan.nRoadLimitSpeed = int(self.nRoadLimitSpeed)
    msg.carrotMan.remote = remote_ip
    msg.carrotMan.xSpdType = int(self.xSpdType)
    msg.carrotMan.xSpdLimit = int(self.xSpdLimit)
    msg.carrotMan.xSpdDist = int(self.xSpdDist)
    msg.carrotMan.xSpdCountDown = int(left_spd_sec)
    msg.carrotMan.xTurnInfo = int(self.xTurnInfo)
    msg.carrotMan.xDistToTurn = int(self.xDistToTurn)
    msg.carrotMan.xTurnCountDown = int(left_tbt_sec)
    msg.carrotMan.atcType = self.atcType
    msg.carrotMan.vTurnSpeed = int(vturn_speed)
    msg.carrotMan.szPosRoadName = f"{self.szPosRoadName} {self.debugText}".strip()
    msg.carrotMan.szTBTMainText = self.szTBTMainText
    msg.carrotMan.desiredSpeed = int(desired_speed)
    msg.carrotMan.desiredSource = source
    publish_navigation_diagnostics(
      msg.carrotMan,
      getattr(self, "navigation_selection", None),
      getattr(self, "last_safety_decision", None),
      getattr(self, "last_safety_rejection", None),
    )
    msg.carrotMan.carrotCmdIndex = int(self.carrotCmdIndex)
    msg.carrotMan.carrotCmd = self.carrotCmd
    msg.carrotMan.carrotArg = self.carrotArg
    msg.carrotMan.trafficState = self.traffic_state

    msg.carrotMan.xPosSpeed = float(v_ego_kph) #float(self.nPosSpeed)
    msg.carrotMan.xPosAngle = float(self.bearing)
    msg.carrotMan.xPosLat = float(self.vpPosPointLat)
    msg.carrotMan.xPosLon = float(self.vpPosPointLon)

    msg.carrotMan.nGoPosDist = self.nGoPosDist
    msg.carrotMan.nGoPosTime = self.nGoPosTime
    msg.carrotMan.szSdiDescr = self._get_sdi_descr(-1 if self.nSdiType == 0 and self.nSdiDist == 0 else self.nSdiType)

    #coords_str = ";".join([f"{x},{y}" for x, y in coords])
    coords_str = ";".join([f"{x:.2f},{y:.2f},{d:.2f}" for (x, y), d in zip(coords, distances, strict=False)])
    msg.carrotMan.naviPaths = coords_str

    msg.carrotMan.leftSec = int(self.carrot_left_sec)
    pm.send('carrotMan', msg)

    inst = messaging.new_message('navInstructionCarrot')
    if self.active_carrot > 1 and self.active_kisa_count <= 0:
      inst.valid = True

      instruction = inst.navInstructionCarrot
      instruction.distanceRemaining = self.nGoPosDist
      instruction.timeRemaining = self.nGoPosTime
      instruction.speedLimit = self.nRoadLimitSpeed / 3.6 if self.nRoadLimitSpeed > 0 else 0
      instruction.maneuverDistance = float(self.nTBTDist)
      instruction.maneuverSecondaryText = self.szNearDirName
      if self.szFarDirName and len(self.szFarDirName):
        instruction.maneuverSecondaryText += "[{}]".format(self.szFarDirName)
      instruction.maneuverPrimaryText = self.szTBTMainText
      instruction.timeRemainingTypical = self.nGoPosTime

      navType, navModifier, xTurnInfo1 = "invalid", "", -1
      if self.nTBTTurnType in nav_type_mapping:
        navType, navModifier, xTurnInfo1 = nav_type_mapping[self.nTBTTurnType]
      navTypeNext, navModifierNext, xTurnInfoNext = "invalid", "", -1
      if self.nTBTTurnTypeNext in nav_type_mapping:
        navTypeNext, navModifierNext, xTurnInfoNext = nav_type_mapping[self.nTBTTurnTypeNext]

      instruction.maneuverType = navType
      instruction.maneuverModifier = navModifier

      maneuvers = []
      if self.nTBTTurnType >= 0:
        maneuver = {}
        maneuver['distance'] = float(self.xDistToTurn)
        maneuver['type'] = navType
        maneuver['modifier'] = navModifier
        maneuvers.append(maneuver)
        if self.nTBTDistNext >= self.nTBTDist:
          maneuver = {}
          maneuver['distance'] = float(self.nTBTDistNext)
          maneuver['type'] = navTypeNext
          maneuver['modifier'] = navModifierNext
          maneuvers.append(maneuver)

      instruction.allManeuvers = maneuvers
    elif sm.alive['navInstruction'] and sm.valid['navInstruction']:
      inst.navInstructionCarrot = sm['navInstruction']

    pm.send('navInstructionCarrot', inst)

  def _update_system_time(self, epoch_time_remote, timezone_remote):
    epoch_time = int(time.time())
    if epoch_time_remote > 0:
      epoch_time_offset = epoch_time_remote - epoch_time
      print(f"epoch_time_offset = {epoch_time_offset}")
      if abs(epoch_time_offset) > 60:
        os.system(f"sudo timedatectl set-timezone {timezone_remote}")
        formatted_time = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(epoch_time_remote))
        print(f"Setting system time to: {formatted_time}")
        os.system(f'sudo date -s "{formatted_time}"')

  def set_time(self, epoch_time, timezone):
    import datetime
    new_time = datetime.datetime.utcfromtimestamp(epoch_time)
    localtime_path = "/data/etc/localtime"

    no_timezone = False
    try:
      if os.path.getsize(localtime_path) == 0:
        no_timezone = True
    except (FileNotFoundError, OSError):
      no_timezone = True

    diff = datetime.datetime.utcnow() - new_time
    if abs(diff) < datetime.timedelta(seconds=10) and not no_timezone:
      #print(f"Time diff too small: {diff}")
      return

    print(f"Setting time to {new_time}, diff={diff}")
    zoneinfo_path = f"/usr/share/zoneinfo/{timezone}"
    if os.path.exists(localtime_path) or os.path.islink(localtime_path):
        try:
            subprocess.run(["sudo", "rm", "-f", localtime_path], check=True)
            print(f"Removed existing file or link: {localtime_path}")
        except subprocess.CalledProcessError as e:
            print(f"Error removing {localtime_path}: {e}")
            return
    try:
        subprocess.run(["sudo", "ln", "-s", zoneinfo_path, localtime_path], check=True)
        print(f"Timezone successfully set to: {timezone}")
        # 앱이 보낸 타임존은 최고 우선순위로 기록 -> timed.py의 wifi/gps 추정이 덮어쓰지 않음
        self.params.put("TimezoneName", timezone)
        self.params.put("TimezoneSource", "app")
    except subprocess.CalledProcessError as e:
        print(f"Failed to set timezone to {timezone}: {e}")


    try:
      subprocess.run(f"TZ=UTC date -s '{new_time}'", shell=True, check=True)
      #subprocess.run()
    except subprocess.CalledProcessError:
      print("timed.failed_setting_time")

  @staticmethod
  def _legacy_number(data, name, default=0.0):
    try:
      value = float(data.get(name, default))
      return value if math.isfinite(value) else float(default)
    except (TypeError, ValueError, OverflowError):
      return float(default)

  @staticmethod
  def _legacy_integer(data, name, default=0):
    try:
      return int(data.get(name, default))
    except (TypeError, ValueError, OverflowError):
      return int(default)

  def _legacy_road_limit(self, data, session_id):
    road_limit = self._legacy_integer(data, "nRoadLimitSpeed", 20)
    if road_limit > 200:
      road_limit = (road_limit - 20) / 10
    elif road_limit == 120:
      road_limit = 115
    elif road_limit <= 0:
      road_limit = 30

    state = self._legacy_road_limit_state.get(session_id)
    if state is None:
      state = [30, None, 0]
      self._legacy_road_limit_state[session_id] = state
    current, candidate, count = state
    if road_limit == current:
      state[:] = [road_limit, road_limit, 0]
    else:
      count = count + 1 if candidate == road_limit else 1
      if count > 5:
        current = road_limit
        count = 0
      state[:] = [current, road_limit, count]
    return float(state[0])

  def _legacy_navigation_snapshot(self, data, source, session_id, received_mono_s):
    if source is not NavigationSource.TMAP_LEGACY:
      return None
    sequence = self._legacy_navigation_sequences.get(session_id, 0) + 1
    self._legacy_navigation_sequences[session_id] = sequence
    road_limit = self._legacy_road_limit(data, session_id)

    primary_type = self._legacy_integer(data, "nSdiType", -1)
    safety = None
    if primary_type >= 0:
      safety = SafetyItem(
        type=primary_type,
        distance_m=self._legacy_number(data, "nSdiDist", 0),
        speed_limit_kph=self._legacy_number(data, "nSdiSpeedLimit", 0),
        received_mono_s=received_mono_s,
        reason="bump" if primary_type == 22 else "section" if primary_type == 4 else "cam",
        # 7713 provides the exact public nSdiSection value.  Only the v2
        # section adapter uses SafetyItem.section to force public value 1.
        section=False,
        section_type=self._legacy_integer(data, "nSdiSection", -1),
        block_type=self._legacy_integer(data, "nSdiBlockType", -1),
        block_speed_kph=self._legacy_number(data, "nSdiBlockSpeed", 0),
        block_distance_m=self._legacy_number(data, "nSdiBlockDist", 0),
      )
    secondary_type = self._legacy_integer(data, "nSdiPlusType", -1)
    secondary = None
    if secondary_type >= 0:
      secondary = SafetyItem(
        type=secondary_type,
        distance_m=self._legacy_number(data, "nSdiPlusDist", 0),
        speed_limit_kph=self._legacy_number(data, "nSdiPlusSpeedLimit", 0),
        received_mono_s=received_mono_s,
        reason="bump" if secondary_type == 22 else "cam",
        section_type=self._legacy_integer(data, "nSdiPlusSection", -1),
        block_type=self._legacy_integer(data, "nSdiPlusBlockType", -1),
        block_speed_kph=self._legacy_number(data, "nSdiPlusBlockSpeed", 0),
        block_distance_m=self._legacy_number(data, "nSdiPlusBlockDist", 0),
      )

    current_type = self._legacy_integer(data, "nTBTTurnType", -1)
    next_type = self._legacy_integer(data, "nTBTTurnTypeNext", -1)
    current = NavigationInstruction(
      present=current_type >= 0,
      turn_type=current_type,
      distance_m=self._legacy_number(data, "nTBTDist", 0),
      road_name=str(data.get("szPosRoadName") or ""),
      main_text=str(data.get("szTBTMainText") or ""),
      near_direction=str(data.get("szNearDirName") or ""),
      far_direction=str(data.get("szFarDirName") or ""),
      next_road_width=self._legacy_integer(data, "nTBTNextRoadWidth", 0),
      received_mono_s=received_mono_s if current_type >= 0 else None,
    )
    next_instruction = NavigationInstruction(
      present=next_type >= 0,
      turn_type=next_type,
      distance_m=self._legacy_number(data, "nTBTDistNext", 0),
      main_text=str(data.get("szTBTMainTextNext", data.get("szTBTMainText")) or ""),
      received_mono_s=received_mono_s if next_type >= 0 else None,
    )
    category = self._legacy_integer(data, "roadcate", 0) if "roadcate" in data else None
    latitude = self._legacy_number(data, "vpPosPointLat", 0.0)
    longitude = self._legacy_number(data, "vpPosPointLon", 0.0)
    position_present = latitude != 0.0 and -90.0 <= latitude <= 90.0 and -180.0 <= longitude <= 180.0
    route_present = any(name in data for name in ("nGoPosDist", "nGoPosTime", "goalPosX", "goalPosY"))
    destination = None
    if data.get("goalPosX") is not None and data.get("goalPosY") is not None:
      destination = (
        self._legacy_number(data, "goalPosY", 0.0),
        self._legacy_number(data, "goalPosX", 0.0),
      )
    control = NavigationControlState(
      current=current,
      next=next_instruction,
      safety=safety,
      secondary_safety=secondary,
      speed_present=True,
      speed_received_mono_s=received_mono_s,
      road_limit_kph=road_limit,
      road_limit_received_mono_s=received_mono_s,
      road_category=category,
      road_category_received_mono_s=received_mono_s if category is not None else None,
      route_present=route_present,
      route_received_mono_s=received_mono_s if route_present else None,
      remaining_distance_m=self._legacy_number(data, "nGoPosDist", 0),
      remaining_time_s=self._legacy_number(data, "nGoPosTime", 0),
      destination=destination,
      destination_present=destination is not None,
      destination_received_mono_s=received_mono_s if destination is not None else None,
      position_present=position_present,
      position_received_mono_s=received_mono_s if position_present else None,
      position_latitude=latitude if position_present else 0.0,
      position_longitude=longitude if position_present else 0.0,
      position_heading_deg=self._legacy_number(data, "nPosAngle", 0.0),
      position_speed_kph=self._legacy_number(data, "nPosSpeed", 0.0),
      position_road_name=(
        "" if str(data.get("szPosRoadName") or "") == "null"
        else str(data.get("szPosRoadName") or "")
      ),
    )
    previous = self._legacy_navigation_controls.get(session_id)
    pending = self._legacy_navigation_pending.get(session_id, {})
    merged_updates = {}
    if previous is not None:
      if previous.route_points:
        merged_updates.update(
          route_present=True,
          route_received_mono_s=(
            control.route_received_mono_s if control.route_present
            else previous.route_received_mono_s
          ),
          route_points=previous.route_points,
        )
      if previous.traffic_present:
        merged_updates.update(
          traffic_present=True,
          traffic_received_mono_s=previous.traffic_received_mono_s,
          traffic_visible=previous.traffic_visible,
          traffic_distance_m=previous.traffic_distance_m,
          traffic_source=previous.traffic_source,
          traffic_lamp=previous.traffic_lamp,
          traffic_remain_s=previous.traffic_remain_s,
        )
      if (
        not control.destination_present
        and previous.destination_present
        and previous.destination is not None
      ):
        merged_updates.update(
          destination=previous.destination,
          destination_present=True,
          destination_received_mono_s=previous.destination_received_mono_s,
        )
    pending_updates = dict(pending)
    if control.destination_present:
      for field_name in ("destination", "destination_present", "destination_received_mono_s"):
        pending_updates.pop(field_name, None)
    merged_updates.update(pending_updates)
    if merged_updates:
      control = replace(control, **merged_updates)
    return NavigationSnapshot(
      source=source,
      session_id=session_id,
      sequence=sequence,
      lifecycle=NavigationLifecycle.GUIDING,
      received_mono_s=received_mono_s,
      activation_epoch=0,
      control=control,
    )

  @staticmethod
  def _legacy_route_points(payload):
    raw_points = payload.get("route_points", ()) if isinstance(payload, dict) else ()
    points = []
    for point in raw_points:
      if not isinstance(point, (tuple, list)) or len(point) != 2:
        continue
      try:
        latitude, longitude = float(point[0]), float(point[1])
      except (TypeError, ValueError, OverflowError):
        continue
      if math.isfinite(latitude) and math.isfinite(longitude) and -90.0 <= latitude <= 90.0 and -180.0 <= longitude <= 180.0:
        points.append((latitude, longitude))
    return tuple(points)

  def _legacy_navigation_aux_updates(self, kind, payload, received_mono_s):
    if kind == "route":
      route_points = self._legacy_route_points(payload)
      return {
        "route_present": bool(route_points),
        "route_received_mono_s": received_mono_s if route_points else None,
        "route_points": route_points,
      }
    if kind == "destination":
      destination = payload.get("destination") if isinstance(payload, dict) else None
      if not isinstance(destination, (tuple, list)) or len(destination) != 2:
        return None
      try:
        latitude, longitude = float(destination[0]), float(destination[1])
      except (TypeError, ValueError, OverflowError):
        return None
      if not (math.isfinite(latitude) and math.isfinite(longitude)):
        return None
      return {
        "destination": (latitude, longitude),
        "destination_present": True,
        "destination_received_mono_s": received_mono_s,
      }
    if kind == "traffic" and isinstance(payload, dict):
      present = bool(payload.get("traffic_present", False))
      return {
        "traffic_present": present,
        "traffic_received_mono_s": received_mono_s if present else None,
        "traffic_visible": bool(payload.get("traffic_visible", False)) if present else False,
        "traffic_distance_m": self._legacy_number(payload, "traffic_distance_m", 0.0) if present else 0.0,
        "traffic_source": str(payload.get("traffic_source") or "") if present else "",
        "traffic_lamp": str(payload.get("traffic_lamp") or "") if present else "",
        "traffic_remain_s": self._legacy_integer(payload, "traffic_remain_s", 0) if present else 0,
      }
    return None

  def update_legacy_navigation_aux(self, kind, payload, source, session_id, received_mono_s):
    self._ensure_navigation_sources()
    if source is not NavigationSource.TMAP_LEGACY or not isinstance(session_id, str) or not session_id:
      return False
    try:
      received_mono_s = float(received_mono_s)
      if not math.isfinite(received_mono_s) or received_mono_s < 0.0:
        return False
    except (TypeError, ValueError, OverflowError):
      return False
    updates = self._legacy_navigation_aux_updates(kind, payload, received_mono_s)
    if updates is None:
      return False

    with self._navigation_projection_lock:
      if session_id not in self._legacy_navigation_base_sessions:
        self._legacy_navigation_pending.setdefault(session_id, {}).update(updates)
        return False

      previous = self._legacy_navigation_controls.get(session_id)
      if previous is None:
        return False
      control = replace(previous, **updates)
      sequence = self._legacy_navigation_sequences.get(session_id, 0) + 1
      snapshot = NavigationSnapshot(
        source=source,
        session_id=session_id,
        sequence=sequence,
        lifecycle=NavigationLifecycle.GUIDING,
        received_mono_s=received_mono_s,
        activation_epoch=0,
        control=control,
      )
      if not self.accept_navigation_snapshot(snapshot):
        return False
      self._legacy_navigation_sequences[session_id] = sequence
      self._legacy_navigation_controls[session_id] = control
      return True

  def update(self, json, source=NavigationSource.TMAP_LEGACY, received_mono_s=None):
    def _f(v, default=0.0):
      return default if v is None else float(v)  
    if json is None:
      return
    self._ensure_navigation_sources()
    bound_source_value = json.get("_navigation_source", source)
    try:
      bound_source = NavigationSource(bound_source_value)
    except (TypeError, ValueError):
      bound_source = None
    session_id = str(json.get("_navigation_session_id") or "tmap-legacy-compat")
    receipt_value = received_mono_s
    if receipt_value is None:
      receipt_value = json.get("_navigation_received_mono_s")
    try:
      navigation_received_mono_s = float(receipt_value)
      if not math.isfinite(navigation_received_mono_s) or navigation_received_mono_s < 0:
        raise ValueError
    except (TypeError, ValueError, OverflowError):
      navigation_received_mono_s = time.monotonic()
    if "carrotIndex" in json:
      self.carrotIndex = int(json.get("carrotIndex") or self.carrotIndex + 1)

    #print(json)
    if self.carrotIndex % 60 == 0 and "epochTime" in json:
      epoch = json.get("epochTime")
      if epoch is not None:
        # op는 ntp를 사용하기때문에... 필요없는 루틴으로 보임.
        timezone_remote = json.get("timezone", "Asia/Seoul")

        if not PC:
          self.set_time(int(epoch), timezone_remote)

      #self._update_system_time(int(json.get("epochTime")), timezone_remote)

    if "carrotCmd" in json:
      #print(json.get("carrotCmd"), json.get("carrotArg"))
      self.carrotCmdIndex = self.carrotIndex
      self.carrotCmd = json.get("carrotCmd")
      self.carrotArg = json.get("carrotArg")
      print(f"carrotCmd = {self.carrotCmd}, {self.carrotArg}")

    now = time.monotonic()

    if "goalPosX" in json and "nRoadLimitSpeed" not in json:
      gx = json.get("goalPosX")
      gy = json.get("goalPosY")
      if gx is not None and gy is not None:
        self.update_legacy_navigation_aux(
          "destination",
          {"destination": (gy, gx)},
          bound_source,
          session_id,
          navigation_received_mono_s,
        )

    if "nRoadLimitSpeed" in json:
      snapshot = self._legacy_navigation_snapshot(
        json, bound_source, session_id, navigation_received_mono_s,
      )
      if snapshot is not None:
        if self.accept_navigation_snapshot(snapshot):
          self._legacy_navigation_controls[session_id] = snapshot.control
          self._legacy_navigation_base_sessions.add(session_id)
          self._legacy_navigation_pending.pop(session_id, None)

    # 3초간 navi 데이터가 없으면, phone gps로 업데이트
    if "latitude" in json:
      self.nPosAnglePhone = _f(json.get("heading"), self.nPosAngle)
      self.phone_latitude = _f(json.get("latitude"), self.vpPosPointLatNavi)
      self.phone_longitude = _f(json.get("longitude"), self.vpPosPointLonNavi)
      self.phone_gps_accuracy = _f(json.get("accuracy"), 0)
      if self.phone_gps_accuracy < 15.0:
        self.phone_gps_frame += 1
      if (now - self.last_update_gps_time_navi) > 3.0:
        self.vpPosPointLatNavi = self.phone_latitude
        self.vpPosPointLonNavi = self.phone_longitude

        self.nPosAngle = self.nPosAnglePhone
        # self.nPosSpeed = self.ve # TODO speed from v_ego
        self.last_update_gps_time_phone = self.last_calculate_gps_time = now        
        self.nPosSpeed = float(json.get("gps_speed", 0))
        print(f"phone gps: {self.vpPosPointLatNavi}, {self.vpPosPointLonNavi}, {self.phone_gps_accuracy}, {self.nPosSpeed}")


import traceback

def main():
  print("CarrotManager Started")
  #print("Carrot GitBranch = {}, {}".format(Params().get("GitBranch"), Params().get("GitCommitDate")))
  # 延迟导入，避免与 carrot_man 中导入 CarrotServ 的循环依赖
  from openpilot.selfdrive.carrot.carrot_man import CarrotMan
  carrot_man = CarrotMan()

  print(f"CarrotMan {carrot_man}")
  threading.Thread(target=carrot_man.kisa_app_thread).start()
  while True:
    try:
      carrot_man.carrot_man_thread()
    except Exception as e:
      print(f"carrot_man error...: {e}")
      traceback.print_exc()
      time.sleep(10)


if __name__ == "__main__":
  main()
