from __future__ import annotations

import math
from types import MappingProxyType
from uuid import UUID

from openpilot.selfdrive.carrot.navigation_sources import (
  NavigationControlState,
  NavigationInstruction,
  NavigationLifecycle,
  NavigationSnapshot,
  NavigationSource,
  SafetyItem,
)


SCHEMA = "naver.navigation.v1"
MAX_SEQUENCE = (1 << 63) - 1
MAX_SENT_MONOTONIC_MS = (1 << 64) - 1
MAX_LOCAL_MONOTONIC_S = 1_000_000_000_000
MAX_DISTANCE_M = 2_000_000
MAX_REMAINING_TIME_S = 604_800
MAX_SPEED_KPH = 250
MAX_ROAD_CATEGORY = (1 << 31) - 1
MAX_GUIDANCE_TEXT = 256
MAX_SESSION_TEXT = 64
MAX_ROUTE_POINTS = 4_096

MANEUVER_TYPES = MappingProxyType({
  "straight": 0,
  "left": 12,
  "right": 13,
  "u_turn": 14,
  "fork_left": 7,
  "fork_right": 6,
  "ramp_left": 102,
  "ramp_right": 101,
  "roundabout": 131,
  "arrive": 201,
  "slight_left": 1000,
  "slight_right": 1001,
})

_ROOT_KEYS = frozenset((
  "schema", "sessionId", "sequence", "sentMonotonicMs", "lifecycle",
  "guidance", "safety", "road", "route",
))
_GUIDANCE_KEYS = frozenset(("current", "next"))
_ABSENT_KEYS = frozenset(("present",))
_INSTRUCTION_KEYS = frozenset(("present", "maneuver", "distanceM", "roadName", "mainText"))
_CAMERA_KEYS = frozenset(("present", "kind", "distanceM", "speedKph"))
_BUMP_KEYS = frozenset(("present", "kind", "distanceM"))
_ROAD_BASE_KEYS = frozenset(("limitValid", "categoryValid"))
_ROUTE_BASE_KEYS = frozenset((
  "present", "remainingDistanceM", "remainingTimeSec", "offRoute", "destinationValid",
))
_DESTINATION_KEYS = frozenset(("destinationLatitude", "destinationLongitude"))
_CAMERA_TYPES = MappingProxyType({
  "fixed_camera": (1, False, "cam"),
  "mobile_camera": (7, False, "cam"),
  "section_camera": (2, True, "section"),
})
_INACTIVE_LIFECYCLES = frozenset((
  NavigationLifecycle.IDLE,
  NavigationLifecycle.STOPPED,
  NavigationLifecycle.ARRIVED,
))

_ERROR_MESSAGES = MappingProxyType({
  "protocol": "invalid navigation envelope",
  "payload_type": "envelope must be an object",
  "root_keys": "envelope keys do not match the schema",
  "schema": "unsupported navigation schema",
  "session_id": "session identifier is invalid",
  "sequence": "sequence is outside the signed 63-bit positive range",
  "sent_monotonic_ms": "phone monotonic metadata is invalid",
  "received_mono_s": "local receipt time is invalid",
  "lifecycle": "navigation lifecycle is invalid",
  "guidance": "guidance item is invalid",
  "safety": "safety item is invalid",
  "road": "road item is invalid",
  "route": "route item is invalid",
  "lifecycle_consistency": "inactive lifecycle contains control data",
})


class NaverProtocolError(ValueError):
  def __init__(self, code: object, message: object):
    safe_code = code if type(code) is str and code in _ERROR_MESSAGES else "protocol"
    safe_message = _ERROR_MESSAGES[safe_code]
    self.code = safe_code
    self.message = safe_message
    super().__init__(f"{safe_code}: {safe_message}")


def _fail(code: str) -> None:
  raise NaverProtocolError(code, _ERROR_MESSAGES[code])


def _object(value: object, code: str) -> dict:
  if type(value) is not dict:
    _fail(code)
  return value


def _exact_keys(value: dict, expected: frozenset[str], code: str) -> None:
  if frozenset(value) != expected:
    _fail(code)


def _bool_field(value: dict, key: str, code: str) -> bool:
  try:
    item = value[key]
  except KeyError:
    _fail(code)
  if type(item) is not bool:
    _fail(code)
  return item


def _text_field(value: dict, key: str, maximum: int, code: str) -> str:
  try:
    item = value[key]
  except KeyError:
    _fail(code)
  if type(item) is not str or len(item) > maximum:
    _fail(code)
  return item


def _number_field(value: dict, key: str, minimum: float, maximum: float, code: str,
                  *, minimum_inclusive: bool = True) -> int | float:
  try:
    item = value[key]
  except KeyError:
    _fail(code)
  if type(item) not in (int, float):
    _fail(code)
  if type(item) is float and not math.isfinite(item):
    _fail(code)
  if item > maximum or (item < minimum if minimum_inclusive else item <= minimum):
    _fail(code)
  return item


def _integer_field(value: dict, key: str, minimum: int, maximum: int, code: str) -> int:
  try:
    item = value[key]
  except KeyError:
    _fail(code)
  if type(item) is not int or item < minimum or item > maximum:
    _fail(code)
  return item


def _parse_session_id(root: dict) -> str:
  session_text = _text_field(root, "sessionId", MAX_SESSION_TEXT, "session_id")
  try:
    canonical = str(UUID(session_text))
  except (AttributeError, TypeError, ValueError):
    _fail("session_id")
  if session_text.lower() != canonical:
    _fail("session_id")
  return canonical


def _parse_lifecycle(root: dict) -> NavigationLifecycle:
  lifecycle_text = _text_field(root, "lifecycle", 16, "lifecycle")
  try:
    return NavigationLifecycle(lifecycle_text)
  except ValueError:
    _fail("lifecycle")


def _parse_instruction(value: object, received_mono_s: int | float) -> NavigationInstruction:
  item = _object(value, "guidance")
  present = _bool_field(item, "present", "guidance")
  _exact_keys(item, _INSTRUCTION_KEYS if present else _ABSENT_KEYS, "guidance")
  if not present:
    return NavigationInstruction()

  maneuver = _text_field(item, "maneuver", 32, "guidance")
  try:
    turn_type = MANEUVER_TYPES[maneuver]
  except KeyError:
    _fail("guidance")
  distance_m = _number_field(item, "distanceM", 0, MAX_DISTANCE_M, "guidance")
  road_name = _text_field(item, "roadName", MAX_GUIDANCE_TEXT, "guidance")
  main_text = _text_field(item, "mainText", MAX_GUIDANCE_TEXT, "guidance")
  return NavigationInstruction(
    present=True,
    turn_type=turn_type,
    distance_m=distance_m,
    road_name=road_name,
    main_text=main_text,
    received_mono_s=received_mono_s,
  )


def _parse_guidance(value: object, received_mono_s: int | float) -> tuple[NavigationInstruction, NavigationInstruction]:
  guidance = _object(value, "guidance")
  _exact_keys(guidance, _GUIDANCE_KEYS, "guidance")
  return (
    _parse_instruction(guidance["current"], received_mono_s),
    _parse_instruction(guidance["next"], received_mono_s),
  )


def _parse_safety(value: object, received_mono_s: int | float) -> tuple[SafetyItem | None, SafetyItem | None]:
  safety = _object(value, "safety")
  present = _bool_field(safety, "present", "safety")
  if not present:
    _exact_keys(safety, _ABSENT_KEYS, "safety")
    return None, None

  kind = _text_field(safety, "kind", 32, "safety")
  if kind == "speed_bump":
    _exact_keys(safety, _BUMP_KEYS, "safety")
    distance_m = _number_field(
      safety, "distanceM", 0, MAX_DISTANCE_M, "safety", minimum_inclusive=False,
    )
    return None, SafetyItem(
      type=22,
      distance_m=distance_m,
      speed_limit_kph=0.0,
      received_mono_s=received_mono_s,
      reason="bump",
    )

  try:
    safety_type, section, reason = _CAMERA_TYPES[kind]
  except KeyError:
    _fail("safety")
  _exact_keys(safety, _CAMERA_KEYS, "safety")
  distance_m = _number_field(
    safety, "distanceM", 0, MAX_DISTANCE_M, "safety", minimum_inclusive=False,
  )
  speed_kph = _number_field(
    safety, "speedKph", 0, MAX_SPEED_KPH, "safety", minimum_inclusive=False,
  )
  return SafetyItem(
    type=safety_type,
    distance_m=distance_m,
    speed_limit_kph=speed_kph,
    received_mono_s=received_mono_s,
    reason=reason,
    section=section,
  ), None


def _parse_road(value: object, received_mono_s: int | float) -> tuple[
  int | float | None, int | float | None, int | None, int | float | None,
]:
  road = _object(value, "road")
  limit_valid = _bool_field(road, "limitValid", "road")
  category_valid = _bool_field(road, "categoryValid", "road")
  expected = _ROAD_BASE_KEYS
  if limit_valid:
    expected |= frozenset(("limitKph",))
  if category_valid:
    expected |= frozenset(("category",))
  _exact_keys(road, expected, "road")

  limit = None
  limit_received = None
  if limit_valid:
    limit = _number_field(road, "limitKph", 0, MAX_SPEED_KPH, "road")
    limit_received = received_mono_s
  category = None
  category_received = None
  if category_valid:
    category = _integer_field(road, "category", 0, MAX_ROAD_CATEGORY, "road")
    category_received = received_mono_s
  return limit, limit_received, category, category_received


def _coordinate(value: object, minimum: float, maximum: float) -> int | float:
  if type(value) not in (int, float):
    _fail("route")
  if type(value) is float and not math.isfinite(value):
    _fail("route")
  if value < minimum or value > maximum:
    _fail("route")
  return value


def _parse_route_points(value: object) -> tuple[tuple[float, float], ...]:
  if type(value) is not list or len(value) > MAX_ROUTE_POINTS:
    _fail("route")
  parsed = []
  for point in value:
    if type(point) is not list or len(point) != 2:
      _fail("route")
    latitude = _coordinate(point[0], -90, 90)
    longitude = _coordinate(point[1], -180, 180)
    parsed.append((latitude, longitude))
  return tuple(parsed)


def _parse_route(value: object, received_mono_s: int | float) -> tuple[
  bool, int | float | None, int | float, int | float, bool, tuple[float, float] | None,
  tuple[tuple[float, float], ...],
]:
  route = _object(value, "route")
  present = _bool_field(route, "present", "route")
  if not present:
    _exact_keys(route, _ABSENT_KEYS, "route")
    return False, None, 0.0, 0.0, False, None, ()

  destination_valid = _bool_field(route, "destinationValid", "route")
  expected = _ROUTE_BASE_KEYS
  if destination_valid:
    expected |= _DESTINATION_KEYS
  if "points" in route:
    expected |= frozenset(("points",))
  _exact_keys(route, expected, "route")

  remaining_distance = _number_field(route, "remainingDistanceM", 0, MAX_DISTANCE_M, "route")
  remaining_time = _number_field(route, "remainingTimeSec", 0, MAX_REMAINING_TIME_S, "route")
  off_route = _bool_field(route, "offRoute", "route")
  destination = None
  if destination_valid:
    destination = (
      _coordinate(route["destinationLatitude"], -90, 90),
      _coordinate(route["destinationLongitude"], -180, 180),
    )
  points = _parse_route_points(route["points"]) if "points" in route else ()
  return True, received_mono_s, remaining_distance, remaining_time, off_route, destination, points


def parse_naver_navigation_v1(payload: object, received_mono_s: object) -> NavigationSnapshot:
  if type(payload) is not dict:
    _fail("payload_type")
  root = payload
  _exact_keys(root, _ROOT_KEYS, "root_keys")
  if _text_field(root, "schema", len(SCHEMA), "schema") != SCHEMA:
    _fail("schema")

  local_receipt = _number_field(
    {"received": received_mono_s}, "received", 0, MAX_LOCAL_MONOTONIC_S, "received_mono_s",
  )
  session_id = _parse_session_id(root)
  sequence = _integer_field(root, "sequence", 0, MAX_SEQUENCE, "sequence")
  _number_field(root, "sentMonotonicMs", 0, MAX_SENT_MONOTONIC_MS, "sent_monotonic_ms")
  lifecycle = _parse_lifecycle(root)
  current, next_instruction = _parse_guidance(root["guidance"], local_receipt)
  safety, secondary_safety = _parse_safety(root["safety"], local_receipt)
  road_limit, road_limit_received, road_category, road_category_received = _parse_road(
    root["road"], local_receipt,
  )
  (
    route_present,
    route_received,
    remaining_distance,
    remaining_time,
    off_route,
    destination,
    route_points,
  ) = _parse_route(root["route"], local_receipt)

  if lifecycle in _INACTIVE_LIFECYCLES and (
    current.present
    or next_instruction.present
    or safety is not None
    or secondary_safety is not None
    or road_limit is not None
    or road_category is not None
    or route_present
  ):
    _fail("lifecycle_consistency")

  control = NavigationControlState(
    current=current,
    next=next_instruction,
    safety=safety,
    secondary_safety=secondary_safety,
    road_limit_kph=road_limit,
    road_limit_received_mono_s=road_limit_received,
    road_category=road_category,
    road_category_received_mono_s=road_category_received,
    route_present=route_present,
    route_received_mono_s=route_received,
    remaining_distance_m=remaining_distance,
    remaining_time_s=remaining_time,
    off_route=off_route,
    destination=destination,
    destination_present=destination is not None,
    destination_received_mono_s=route_received if destination is not None else None,
    route_points=route_points,
  )
  return NavigationSnapshot(
    source=NavigationSource.NAVER_V1,
    session_id=session_id,
    sequence=sequence,
    lifecycle=lifecycle,
    received_mono_s=local_receipt,
    activation_epoch=0,
    control=control,
  )
