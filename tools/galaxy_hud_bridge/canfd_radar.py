from __future__ import annotations

import time
from dataclasses import asdict, dataclass, replace
from typing import Any


RADAR_POINT_STALE_S = 0.35
RADAR_MIN_LONGITUDINAL_M = 0.0
RADAR_FRONT_MAX_LONGITUDINAL_M = 180.0
MAX_SPEED_KPH = 180.0
DEFAULT_LANE_WIDTH_M = 3.6

RADAR_RAW_MOVING_SPEED_KPH = 8.0
RADAR_CENTER_RAW_LATERAL_LANES = 0.72
RADAR_ADJACENT_RAW_LATERAL_LANES = 1.45
RADAR_OUTER_RAW_LATERAL_LANES = 2.65
RADAR_RAW_CENTER_MIN_VALID_COUNT = 16
RADAR_RAW_ADJACENT_MIN_VALID_COUNT = 24
RADAR_RAW_OUTER_MIN_VALID_COUNT = 35
RADAR_VEHICLE_MIN_PROBABILITY = 0.35


@dataclass(frozen=True, slots=True)
class RadarPoint:
  label: str
  longitudinal_m: float
  lateral_m: float
  source: str
  relative_speed_mps: float | None = None
  absolute_speed_kph: float | None = None
  lateral_speed_mps: float | None = None
  relative_accel_mps2: float | None = None
  probability: float | None = None
  valid: int | None = None
  valid_count: int | None = None
  in_my_lane: int | None = None
  vehicle_candidate: bool = False


class CanFdRadarParser:
  def __init__(self) -> None:
    self.points: dict[str, RadarPoint] = {}
    self.updated_at: dict[str, float] = {}
    self.history: dict[str, tuple[float, float]] = {}
    self.total_frames = 0
    self.total_points = 0
    self.last_error = ""

  def update(self, sm: Any) -> None:
    if not _service_updated(sm, "can"):
      return
    now = time.monotonic()
    ego_speed_kph = _ego_speed_kph(sm)
    can_messages = _service_value(sm, "can")
    if can_messages is None:
      return

    try:
      for can_message in can_messages:
        address = int(_safe_get(can_message, "address", -1))
        data = bytes(_safe_get(can_message, "dat", b""))
        if not is_hyundai_canfd_radar_address(address):
          continue
        labels = hyundai_canfd_radar_labels_for_address(address)
        parsed = parse_hyundai_canfd_radar_message(address, data)
        valid_labels = {point.label for point in parsed}
        for label in labels:
          if label not in valid_labels:
            self.points.pop(label, None)
            self.updated_at.pop(label, None)
            self.history.pop(label, None)
        for point in parsed:
          point = self._with_absolute_speed(point, ego_speed_kph, now)
          point = replace(point, vehicle_candidate=is_vehicle_candidate(point, ego_speed_kph))
          self.points[point.label] = point
          self.updated_at[point.label] = now
          self.total_points += 1
        if parsed:
          self.total_frames += 1
      self._drop_stale(now)
      self.last_error = ""
    except Exception as exc:
      self.last_error = str(exc)[:120]

  def snapshot(self) -> dict[str, Any]:
    now = time.monotonic()
    self._drop_stale(now)
    ordered = sorted(
      self.points.values(),
      key=lambda point: (0 if point.vehicle_candidate else 1, point.longitudinal_m, abs(point.lateral_m), point.label),
    )
    vehicle_points = [point for point in ordered if point.vehicle_candidate]
    return {
      "enabled": True,
      "source": "hyundai-canfd",
      "count": len(ordered),
      "vehicleCount": len(vehicle_points),
      "totalFrames": self.total_frames,
      "totalPoints": self.total_points,
      "lastError": self.last_error,
      "points": [_point_payload(point) for point in ordered[:24]],
      "vehiclePoints": [_point_payload(point) for point in vehicle_points[:12]],
    }

  def _drop_stale(self, now: float) -> None:
    stale = [label for label, updated_at in self.updated_at.items() if now - updated_at > RADAR_POINT_STALE_S]
    for label in stale:
      self.points.pop(label, None)
      self.updated_at.pop(label, None)

  def _with_absolute_speed(self, point: RadarPoint, ego_speed_kph: float, now: float) -> RadarPoint:
    signal_speed_kph = None
    if point.relative_speed_mps is not None:
      signal_speed_kph = max(0.0, ego_speed_kph + point.relative_speed_mps * 3.6)

    observed_speed_kph = None
    previous = self.history.get(point.label)
    if previous is not None:
      previous_distance_m, previous_t = previous
      dt = now - previous_t
      if 0.02 <= dt <= 0.45:
        observed_relative_mps = (point.longitudinal_m - previous_distance_m) / dt
        observed_speed_kph = max(0.0, ego_speed_kph + observed_relative_mps * 3.6)
        if observed_speed_kph > MAX_SPEED_KPH * 1.8:
          observed_speed_kph = None
    self.history[point.label] = (point.longitudinal_m, now)
    return replace(point, absolute_speed_kph=observed_speed_kph if observed_speed_kph is not None else signal_speed_kph)


def _point_payload(point: RadarPoint) -> dict[str, Any]:
  payload = asdict(point)
  payload["dRel"] = payload.pop("longitudinal_m")
  payload["yRel"] = payload.pop("lateral_m")
  payload["vRel"] = payload.pop("relative_speed_mps")
  payload["vAbsKph"] = payload.pop("absolute_speed_kph")
  payload["vLat"] = payload.pop("lateral_speed_mps")
  payload["aRel"] = payload.pop("relative_accel_mps2")
  payload["validCount"] = payload.pop("valid_count")
  payload["inMyLane"] = payload.pop("in_my_lane")
  payload["vehicleCandidate"] = payload.pop("vehicle_candidate")
  return payload


def parse_hyundai_canfd_radar_message(address: int, data: bytes) -> tuple[RadarPoint, ...]:
  if 0x210 <= address <= 0x21F:
    return tuple(
      point
      for point in (
        parse_hyundai_canfd_radar_slot(address, data, 1),
        parse_hyundai_canfd_radar_slot(address, data, 2),
      )
      if point is not None
    )
  if 0x3A5 <= address <= 0x3C4:
    point = parse_hyundai_canfd_radar_point_3a5(address, data)
    return () if point is None else (point,)
  return ()


def is_hyundai_canfd_radar_address(address: int) -> bool:
  return 0x210 <= address <= 0x21F or 0x3A5 <= address <= 0x3C4


def hyundai_canfd_radar_labels_for_address(address: int) -> tuple[str, ...]:
  if 0x210 <= address <= 0x21F:
    index = (address - 0x210) * 2
    return (f"R{index:02d}", f"R{index + 1:02d}")
  if 0x3A5 <= address <= 0x3C4:
    return (f"P{address - 0x3A5:02d}",)
  return ()


def parse_hyundai_canfd_radar_slot(address: int, data: bytes, slot: int) -> RadarPoint | None:
  if len(data) < 32:
    return None
  base = 0 if slot == 1 else 128
  valid_count = dbc_unsigned(data, base + 47, 8, "be")
  if valid_count <= 10:
    return None
  long_dist_m = dbc_unsigned(data, base + 64, 12, "le") * 0.05
  raw_lat_dist_m = dbc_signed(data, base + 76, 12, "le") * 0.05
  rel_speed_mps = dbc_signed(data, base + 88, 14, "le") * 0.01
  raw_lat_speed_mps = dbc_signed(data, base + 104, 13, "le") * 0.01
  rel_accel_mps2 = dbc_signed(data, base + 118, 10, "le") * 0.05
  lat_dist_m = -raw_lat_dist_m
  lat_speed_mps = -raw_lat_speed_mps
  if not -10.0 <= lat_dist_m <= 10.0 or not 2.5 <= long_dist_m <= 180.0:
    return None
  index = (address - 0x210) * 2 + (slot - 1)
  return RadarPoint(
    label=f"R{index:02d}",
    longitudinal_m=long_dist_m,
    lateral_m=lat_dist_m,
    source=f"CAN-FD 0x{address:x}.{slot}",
    relative_speed_mps=rel_speed_mps,
    lateral_speed_mps=lat_speed_mps,
    relative_accel_mps2=rel_accel_mps2,
    valid_count=valid_count,
  )


def parse_hyundai_canfd_radar_point_3a5(address: int, data: bytes) -> RadarPoint | None:
  if len(data) < 24:
    return None
  valid = dbc_unsigned(data, 25, 2, "be")
  valid2 = dbc_unsigned(data, 28, 2, "be")
  probability = dbc_unsigned(data, 30, 10, "le") / 1023.0
  valid_count = dbc_unsigned(data, 47, 8, "be")
  if valid_count <= 10:
    return None
  long_dist_m = dbc_unsigned(data, 63, 13, "le") * 0.05
  raw_lat_dist_m = dbc_signed(data, 76, 12, "le") * 0.05
  rel_speed_mps = dbc_signed(data, 88, 14, "le") * 0.01
  in_my_lane = dbc_unsigned(data, 103, 2, "be")
  raw_lat_speed_mps = dbc_signed(data, 104, 13, "le") * 0.01
  rel_accel_mps2 = dbc_signed(data, 118, 10, "le") * 0.05
  lat_dist_m = -raw_lat_dist_m
  lat_speed_mps = -raw_lat_speed_mps
  if not -10.0 <= lat_dist_m <= 10.0 or not 2.5 <= long_dist_m <= 180.0:
    return None
  index = address - 0x3A5
  return RadarPoint(
    label=f"P{index:02d}",
    longitudinal_m=long_dist_m,
    lateral_m=lat_dist_m,
    source=f"CAN-FD 0x{address:x}",
    relative_speed_mps=rel_speed_mps,
    lateral_speed_mps=lat_speed_mps,
    relative_accel_mps2=rel_accel_mps2,
    probability=max(0.0, min(1.0, probability)),
    valid=valid or valid2,
    valid_count=valid_count,
    in_my_lane=in_my_lane,
  )


def is_vehicle_candidate(point: RadarPoint, ego_speed_kph: float, lane_width_m: float = DEFAULT_LANE_WIDTH_M) -> bool:
  if not 2.5 <= point.longitudinal_m <= RADAR_FRONT_MAX_LONGITUDINAL_M:
    return False
  if abs(point.lateral_m) > lane_width_m * RADAR_OUTER_RAW_LATERAL_LANES:
    return False
  if point.valid_count is not None and point.valid_count < 11:
    return False
  if point.in_my_lane is not None and point.in_my_lane > 0:
    return True
  if point.probability is not None and point.probability >= RADAR_VEHICLE_MIN_PROBABILITY:
    return True

  absolute_speed_kph = point.absolute_speed_kph
  if absolute_speed_kph is None and point.relative_speed_mps is not None:
    absolute_speed_kph = max(0.0, ego_speed_kph + point.relative_speed_mps * 3.6)
  if absolute_speed_kph is None or absolute_speed_kph < RADAR_RAW_MOVING_SPEED_KPH:
    return False

  valid_count = point.valid_count if point.valid_count is not None else RADAR_RAW_CENTER_MIN_VALID_COUNT
  lateral_lanes = abs(point.lateral_m) / max(0.1, lane_width_m)
  if lateral_lanes <= RADAR_CENTER_RAW_LATERAL_LANES:
    return valid_count >= RADAR_RAW_CENTER_MIN_VALID_COUNT
  if lateral_lanes <= RADAR_ADJACENT_RAW_LATERAL_LANES:
    return valid_count >= RADAR_RAW_ADJACENT_MIN_VALID_COUNT
  return valid_count >= RADAR_RAW_OUTER_MIN_VALID_COUNT


def dbc_unsigned(data: bytes, start: int, length: int, byte_order: str) -> int:
  if byte_order == "le":
    return (int.from_bytes(data, "little") >> start) & ((1 << length) - 1)
  value = 0
  bit = start
  for _ in range(length):
    if bit < 0 or bit // 8 >= len(data):
      return value
    value = (value << 1) | ((data[bit // 8] >> (bit % 8)) & 1)
    bit = bit + 15 if bit % 8 == 0 else bit - 1
  return value


def dbc_signed(data: bytes, start: int, length: int, byte_order: str) -> int:
  value = dbc_unsigned(data, start, length, byte_order)
  sign_bit = 1 << (length - 1)
  return value - (1 << length) if value & sign_bit else value


def _ego_speed_kph(sm: Any) -> float:
  car_state = _service_value(sm, "carState")
  if car_state is None:
    return 0.0
  try:
    return max(0.0, float(_safe_get(car_state, "vEgo", 0.0)) * 3.6)
  except Exception:
    return 0.0


def _service_value(sm: Any, service: str) -> Any:
  try:
    if not bool(sm.alive.get(service, False)):
      return None
    return sm[service]
  except Exception:
    return None


def _service_updated(sm: Any, service: str) -> bool:
  try:
    return bool(sm.updated.get(service, False))
  except Exception:
    return False


def _safe_get(value: Any, name: str, default: Any = None) -> Any:
  if value is None:
    return default
  if isinstance(value, dict):
    return value.get(name, default)
  try:
    return getattr(value, name)
  except Exception:
    return default
