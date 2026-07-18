from __future__ import annotations

import ast
import copy
import json
import socket
import struct
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from selfdrive.carrot.navigation_lease import NavigationMux
from selfdrive.carrot.navigation_protocol import (
  ManeuverKind,
  NavigationEnvelope,
  NavigationStatus,
  ProtocolError,
  ProviderSource,
  ReceivedFrame,
  Transport,
  TransportContext,
)


UINT32_MAX = 2**32 - 1
MANEUVER_CODES = {
  ManeuverKind.STRAIGHT: 0, ManeuverKind.LEFT: 12, ManeuverKind.RIGHT: 13, ManeuverKind.U_TURN: 14,
  ManeuverKind.FORK_LEFT: 7, ManeuverKind.FORK_RIGHT: 6, ManeuverKind.RAMP_LEFT: 102,
  ManeuverKind.RAMP_RIGHT: 101, ManeuverKind.ROUNDABOUT: 131, ManeuverKind.ARRIVE: 201,
}


def new_message(service: str, valid: bool = False) -> SimpleNamespace:
  return SimpleNamespace(
    valid=valid,
    carrotMan=SimpleNamespace(),
    navInstructionCarrot=SimpleNamespace(),
    navRoute=SimpleNamespace(coordinates=[]),
  )


def _load_carrot_manager_class() -> type:
  source_path = Path(__file__).resolve().parents[1] / "carrot_man.py"
  tree = ast.parse(source_path.read_text(encoding="utf-8"))
  class_node = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "CarrotMan")
  namespace = {
    "Any": Any,
    "NAVIGATION_DISCOVERY": "carrot.navigation.discover",
    "NAVIGATION_DISCOVERY_RESPONSE": "carrot.navigation.discover.response",
    "NAVIGATION_SCHEMA_VERSION": 1,
    "NAVIGATION_LEASE_MS": 4_000,
    "NAVIGATION_TCP_PORT": 7712,
    "NAVIGATION_MAX_CLIENTS": 4,
    "NAVIGATION_READ_TIMEOUT": 5.0,
    "NAVIGATION_MAX_LINE_BYTES": 262_144,
    "NAVIGATION_ROUTE_BUFFER_MS": 1_000,
    "NAVIGATION_MAX_PENDING_ROUTES": 8,
    "NAVIGATION_MAX_BINARY_ROUTE_BYTES": 4_096 * 8,
    "NAVIGATION_MANEUVER_CODES": MANEUVER_CODES,
    "NAVIGATION_CONTROL_SOURCES": (
      ProviderSource.TMAP, ProviderSource.NAVER, ProviderSource.TMAP_LEGACY,
    ),
    "ManeuverKind": ManeuverKind,
    "NavigationEnvelope": NavigationEnvelope,
    "NavigationMux": NavigationMux,
    "NavigationStatus": NavigationStatus,
    "ProtocolError": ProtocolError,
    "ProviderSource": ProviderSource,
    "ReceivedFrame": ReceivedFrame,
    "Transport": Transport,
    "TransportContext": TransportContext,
    "UINT32_MAX": UINT32_MAX,
    "json": json,
    "messaging": SimpleNamespace(new_message=new_message),
    "socket": socket,
    "struct": struct,
    "threading": threading,
    "time": time,
    "web": SimpleNamespace(Request=object),
  }
  exec(compile(ast.Module(body=[class_node], type_ignores=[]), str(source_path), "exec"), namespace)
  return namespace["CarrotMan"]


def _load_carrot_serv_class() -> type:
  source_path = Path(__file__).resolve().parents[1] / "carrot_serv.py"
  tree = ast.parse(source_path.read_text(encoding="utf-8"))
  class_node = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "CarrotServ")
  namespace = {"messaging": SimpleNamespace(new_message=new_message)}
  exec(compile(ast.Module(body=[class_node], type_ignores=[]), str(source_path), "exec"), namespace)
  return namespace["CarrotServ"]


CarrotMan = _load_carrot_manager_class()
CarrotServ = _load_carrot_serv_class()


class ParamsRecorder:
  def __init__(self) -> None:
    self.puts: list[tuple[str, str]] = []
    self.removes: list[str] = []

  def put(self, key: str, value: str) -> None:
    self.puts.append((key, value))

  def remove(self, key: str) -> None:
    self.removes.append(key)


class PubRecorder:
  def __init__(self) -> None:
    self.messages: list[tuple[str, SimpleNamespace]] = []

  def send(self, service: str, message: SimpleNamespace) -> None:
    self.messages.append((service, copy.deepcopy(message)))


class CountingList(list[tuple[float, float]]):
  def __init__(self, values: list[tuple[float, float]]) -> None:
    super().__init__(values)
    self.clear_calls = 0

  def clear(self) -> None:
    self.clear_calls += 1
    super().clear()


def carrot_serv() -> CarrotServ:
  serv = CarrotServ.__new__(CarrotServ)
  serv.updates = []
  serv.update = lambda payload: serv.updates.append(copy.deepcopy(payload))
  for field, value in {
    "active_carrot": 6, "active_count": 80, "active_sdi_count": 200, "active_kisa_count": 100,
    "xSpdType": 22, "xSpdLimit": 40, "xSpdDist": 100, "nSdiType": 4, "nSdiSpeedLimit": 80,
    "nSdiSection": 1, "nSdiDist": 100, "nSdiBlockType": 4, "nSdiBlockSpeed": 80, "nSdiBlockDist": 100,
    "nSdiPlusType": 22, "nSdiPlusSpeedLimit": 40, "nSdiPlusDist": 200, "nSdiPlusBlockType": 22,
    "nSdiPlusBlockSpeed": 40, "nSdiPlusBlockDist": 200, "nTBTTurnType": 12, "nTBTDist": 120,
    "nTBTTurnTypeNext": 13, "nTBTDistNext": 300, "xTurnInfo": 1, "xDistToTurn": 120,
    "xTurnInfoNext": 2, "xDistToTurnNext": 420, "navType": "turn", "navModifier": "left",
    "navTypeNext": "turn", "navModifierNext": "right", "szTBTMainText": "left", "szTBTMainTextNext": "right",
    "szNearDirName": "near", "szFarDirName": "far", "nGoPosDist": 5_000, "nGoPosTime": 600,
    "atcType": "turn left", "atcSpeed": 40, "atcDist": 120, "desired_speed": 40, "desired_source": "atc",
    "navigation_provider": "none", "navigation_valid": False, "navigation_age_ms": 0,
    "navigation_control_allowed": False,
  }.items():
    setattr(serv, field, value)
  return serv


def manager() -> CarrotMan:
  instance = CarrotMan.__new__(CarrotMan)
  instance.carrot_serv = carrot_serv()
  instance.params = ParamsRecorder()
  instance.pm = PubRecorder()
  instance.navi_points = []
  instance.navi_points_start_index = 0
  instance.navi_points_active = False
  instance.navd_active = False
  instance._rgdata_ts_lock = threading.Lock()
  instance._last_rgdata_timestamp_ms = 0
  instance.handle_traffic_light = lambda _state: None
  instance.handle_unknown = lambda _value: None
  return instance


def canonical(source: str, sequence: int, *, active: bool = True, route_offset: float = 0.0) -> dict[str, Any]:
  return {
    "source": source,
    "schema_version": 1,
    "session_id": "11111111-1111-4111-8111-111111111111" if source == "tmap" else "22222222-2222-4222-8222-222222222222",
    "sequence": sequence,
    "timestamp_ms": sequence * 1_000,
    "navigation_active": active,
    "ttl_ms": 1_000,
    "state": {
      "status": "guiding" if active else "stopped", "maneuver": "left", "maneuver_distance_m": 120,
      "remaining_distance_m": 5_000, "remaining_time_s": 600, "road_limit_kph": 80,
      "position": {"longitude": 127.01, "latitude": 37.51},
    },
    **({"route": [
      {"longitude": 127.01 + route_offset, "latitude": 37.51},
      {"longitude": 127.02 + route_offset, "latitude": 37.52},
    ]} if active else {}),
  }
