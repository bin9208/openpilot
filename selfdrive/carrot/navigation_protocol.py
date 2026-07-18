from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import json
import math
from typing import TYPE_CHECKING, Final, TypeAlias, TypeVar
from uuid import UUID


MAX_FRAME_BYTES: Final = 262_144
MAX_ROUTE_POINTS: Final = 4_096
LEGACY_TTL_MS: Final = 4_000
JsonValue: TypeAlias = str | int | float | bool | None | list["JsonValue"] | dict[str, "JsonValue"]
EnumT = TypeVar("EnumT", bound=StrEnum)

if TYPE_CHECKING:
  from selfdrive.carrot.navigation_lease import NavigationMux


class ProviderSource(StrEnum):
  TMAP = "tmap"
  NAVER = "naver"
  TMAP_LEGACY = "tmap-legacy"


class Transport(StrEnum):
  TCP = "tcp"
  UDP = "udp"
  HTTP = "http"
  BINARY = "binary"


class NavigationStatus(StrEnum):
  GUIDING = "guiding"
  STOPPED = "stopped"
  ARRIVED = "arrived"
  IDLE = "idle"


class ManeuverKind(StrEnum):
  STRAIGHT = "straight"
  LEFT = "left"
  RIGHT = "right"
  U_TURN = "u_turn"
  FORK_LEFT = "fork_left"
  FORK_RIGHT = "fork_right"
  RAMP_LEFT = "ramp_left"
  RAMP_RIGHT = "ramp_right"
  ROUNDABOUT = "roundabout"
  ARRIVE = "arrive"


@dataclass(frozen=True, slots=True)
class Coordinate:
  longitude: float
  latitude: float


@dataclass(frozen=True, slots=True)
class NavigationState:
  status: NavigationStatus
  maneuver: ManeuverKind
  maneuver_distance_m: float
  remaining_distance_m: float
  remaining_time_s: float
  road_limit_kph: float
  position: Coordinate | None


@dataclass(frozen=True, slots=True)
class NavigationEnvelope:
  source: ProviderSource
  schema_version: int
  session_id: UUID
  sequence: int
  sender_timestamp_ms: int
  navigation_active: bool
  ttl_ms: int
  state: NavigationState
  route: tuple[Coordinate, ...] | None


@dataclass(frozen=True, slots=True)
class TransportContext:
  transport: Transport
  peer: str


@dataclass(frozen=True, slots=True)
class ReceivedFrame:
  data: bytes
  context: TransportContext
  received_at: float


@dataclass(frozen=True, slots=True)
class ProviderTransition:
  previous: ProviderSource | None
  current: ProviderSource | None
  reason: str


@dataclass(frozen=True, slots=True)
class ProtocolError(Exception):
  code: str
  field: str

  def __str__(self) -> str:
    return f"{self.code}: {self.field}"


def _mapping(value: JsonValue, field: str) -> dict[str, JsonValue]:
  match value:  # noqa: MATCH_OK - rejects other JSON variants at the boundary
    case dict() as mapping:
      return mapping
    case _:
      raise ProtocolError(code="type", field=field)


def _required(mapping: dict[str, JsonValue], key: str) -> JsonValue:
  if key not in mapping:
    raise ProtocolError(code="missing", field=key)
  return mapping[key]


def _number(value: JsonValue, field: str, limits: tuple[float, float]) -> float:
  match value:  # noqa: MATCH_OK - rejects non-numeric JSON variants
    case bool() | None | str() | list() | dict():
      raise ProtocolError(code="type", field=field)
    case (int() | float()) as number:
      result = float(number)
  if not math.isfinite(result) or not limits[0] <= result <= limits[1]:
    raise ProtocolError(code="bounds", field=field)
  return result


def _integer(value: JsonValue, field: str, limits: tuple[int, int]) -> int:
  match value:  # noqa: MATCH_OK - rejects non-integer JSON variants
    case bool() | None | str() | float() | list() | dict():
      raise ProtocolError(code="type", field=field)
    case int() as number:
      if not limits[0] <= number <= limits[1]:
        raise ProtocolError(code="bounds", field=field)
      return number


def _coordinate(value: JsonValue, legacy: bool = False) -> Coordinate:
  point = _mapping(value, "coordinate")
  longitude_key, latitude_key = ("x", "y") if legacy else ("longitude", "latitude")
  longitude = _number(_required(point, longitude_key), "coordinate.longitude", (-180.0, 180.0))
  latitude = _number(_required(point, latitude_key), "coordinate.latitude", (-90.0, 90.0))
  return Coordinate(longitude=longitude, latitude=latitude)


def _route(value: JsonValue, legacy: bool = False) -> tuple[Coordinate, ...]:
  match value:  # noqa: MATCH_OK - rejects non-array JSON variants
    case list() as points:
      if len(points) > MAX_ROUTE_POINTS:
        raise ProtocolError(code="bounds", field="route")
      return tuple(_coordinate(point, legacy) for point in points)
    case _:
      raise ProtocolError(code="type", field="route")


def _enum_value(enum_type: type[EnumT], value: JsonValue, field: str) -> EnumT:
  match value:  # noqa: MATCH_OK - rejects non-string JSON variants
    case str() as text:
      try:
        return enum_type(text)
      except ValueError:
        raise ProtocolError(code="enum", field=field) from None
    case _:
      raise ProtocolError(code="type", field=field)


def _state(value: JsonValue) -> NavigationState:
  state = _mapping(value, "state")
  position = _coordinate(state["position"]) if "position" in state else None
  return NavigationState(
    status=_enum_value(NavigationStatus, _required(state, "status"), "state.status"),
    maneuver=_enum_value(ManeuverKind, _required(state, "maneuver"), "state.maneuver"),
    maneuver_distance_m=_number(_required(state, "maneuver_distance_m"), "state.maneuver_distance_m", (0, 10_000_000)),
    remaining_distance_m=_number(_required(state, "remaining_distance_m"), "state.remaining_distance_m", (0, 10_000_000)),
    remaining_time_s=_number(_required(state, "remaining_time_s"), "state.remaining_time_s", (0, 604_800)),
    road_limit_kph=_number(_required(state, "road_limit_kph"), "state.road_limit_kph", (0, 250)),
    position=position,
  )


def _decode(data: bytes) -> dict[str, JsonValue]:
  if len(data) > MAX_FRAME_BYTES:
    raise ProtocolError(code="bounds", field="frame")
  try:
    value = json.loads(data.decode("utf-8"), parse_constant=lambda token: (_ for _ in ()).throw(ProtocolError("number", token)))
  except (UnicodeDecodeError, json.JSONDecodeError) as error:
    raise ProtocolError(code="malformed", field="frame") from error
  return _mapping(value, "frame")


def _canonical(root: dict[str, JsonValue]) -> NavigationEnvelope:
  source = _enum_value(ProviderSource, _required(root, "source"), "source")
  if source is ProviderSource.TMAP_LEGACY:
    raise ProtocolError(code="source", field="source")
  session_text = _required(root, "session_id")
  if not isinstance(session_text, str):
    raise ProtocolError(code="type", field="session_id")
  try:
    session_id = UUID(session_text)
  except ValueError:
    raise ProtocolError(code="format", field="session_id") from None
  if session_id.version != 4:
    raise ProtocolError(code="format", field="session_id")
  state = _state(_required(root, "state"))
  active = _required(root, "navigation_active")
  if not isinstance(active, bool) or active != (state.status is NavigationStatus.GUIDING):
    raise ProtocolError(code="consistency", field="navigation_active")
  schema = _integer(_required(root, "schema_version"), "schema_version", (1, 1))
  route = _route(root["route"]) if "route" in root else None
  return NavigationEnvelope(source, schema, session_id,
                            _integer(_required(root, "sequence"), "sequence", (0, 2**63 - 1)),
                            _integer(_required(root, "timestamp_ms"), "timestamp_ms", (1, 2**63 - 1)), active,
                            _integer(_required(root, "ttl_ms"), "ttl_ms", (250, 2_000)), state, route)


def parse_canonical(data: bytes) -> NavigationEnvelope:
  return _canonical(_decode(data))


def __getattr__(name: str) -> type[NavigationMux]:
  if name != "NavigationMux":
    raise AttributeError(name)
  from selfdrive.carrot.navigation_lease import NavigationMux
  return NavigationMux
