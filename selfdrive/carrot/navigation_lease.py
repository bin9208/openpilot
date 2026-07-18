from collections import OrderedDict
from dataclasses import dataclass, replace
import math
from threading import Lock
from uuid import NAMESPACE_URL, UUID, uuid5

from selfdrive.carrot.navigation_protocol import (
  Coordinate,
  LEGACY_TTL_MS,
  JsonValue,
  ManeuverKind,
  NavigationEnvelope,
  NavigationState,
  NavigationStatus,
  ProtocolError,
  ProviderSource,
  ProviderTransition,
  ReceivedFrame,
  TransportContext,
  _canonical,
  _decode,
  _integer,
  _mapping,
  _number,
  _route,
)


_ROUTE_BUFFER_MS, _MAX_LEGACY_CONTEXTS, _MAX_RETIRED_SESSIONS = 1_000, 8, 64


@dataclass(frozen=True, slots=True)
class _Candidate:
  envelope: NavigationEnvelope
  received_at: float


@dataclass(frozen=True, slots=True)
class _Prepared:
  envelope: NavigationEnvelope
  lease_at: float
  renews_lease: bool


@dataclass(frozen=True, slots=True)
class _LegacyEntry:
  session_id: UUID
  sequence: int = 0
  timestamp_ms: int = 0
  state: NavigationState | None = None
  state_at: float = 0.0
  pending_route: tuple[Coordinate, ...] | None = None
  pending_at: float = 0.0


def _legacy_session(context: TransportContext) -> UUID:
  return uuid5(NAMESPACE_URL, f"{context.transport}:{context.peer}")


def _legacy_maneuver(code: int) -> ManeuverKind:
  groups = (((-1, 0, 51, 52, 53, 54, 55, 153, 154, 249), ManeuverKind.STRAIGHT),
            ((12, 16, 1000), ManeuverKind.LEFT), ((13, 19, 1001), ManeuverKind.RIGHT),
            ((7, 17, 44, 75, 76, 118, 1002), ManeuverKind.FORK_LEFT),
            ((6, 43, 73, 74, 117, 123, 124, 1003), ManeuverKind.FORK_RIGHT),
            ((102, 105, 112, 115, 1006), ManeuverKind.RAMP_LEFT),
            ((101, 104, 111, 114, 1007), ManeuverKind.RAMP_RIGHT),
            (range(131, 143), ManeuverKind.ROUNDABOUT), ((14,), ManeuverKind.U_TURN), ((201,), ManeuverKind.ARRIVE))
  for codes, maneuver in groups:
    if code in codes:
      return maneuver
  raise ProtocolError(code="enum", field="state.maneuver")


def _legacy_state(value: JsonValue) -> NavigationState:
  state = _mapping(value, "rgdata")
  maneuver = _legacy_maneuver(_integer(state.get("nTBTTurnType"), "rgdata.nTBTTurnType", (-1, 10_000)))
  longitude = _number(state.get("vpPosPointLon", 0), "rgdata.longitude", (-180, 180))
  latitude = _number(state.get("vpPosPointLat", 0), "rgdata.latitude", (-90, 90))
  return NavigationState(NavigationStatus.ARRIVED if maneuver is ManeuverKind.ARRIVE else NavigationStatus.GUIDING,
                         maneuver, _number(state.get("nTBTDist", 0), "rgdata.nTBTDist", (0, 10_000_000)),
                         _number(state.get("nGoPosDist", 0), "rgdata.nGoPosDist", (0, 10_000_000)),
                         _number(state.get("nGoPosTime", 0), "rgdata.nGoPosTime", (0, 604_800)),
                         _number(state.get("nRoadLimitSpeed"), "rgdata.nRoadLimitSpeed", (0, 2_000)),
                         None if longitude == latitude == 0 else Coordinate(longitude, latitude))


class _LegacyAdapter:
  """Prepare peer state without mutation so rejected frames cannot leak into later routes."""

  def __init__(self) -> None:
    self.entries: OrderedDict[TransportContext, _LegacyEntry] = OrderedDict()

  def prepare(self, root: dict[str, JsonValue], frame: ReceivedFrame,
              protected: UUID | None) -> tuple[_Prepared | None, OrderedDict[TransportContext, _LegacyEntry]]:
    route = _route(root["vrtx"], True) if "vrtx" in root else None
    state = _legacy_state(root["rgdata"]) if "rgdata" in root else None
    timestamp = _integer(root.get("timestamp_ms", 0), "timestamp_ms", (0, 2**63 - 1))
    if route is None and state is None:
      raise ProtocolError(code="shape", field="legacy")
    entries = self.entries.copy()
    entry = entries.get(frame.context)
    if entry is None:
      if len(entries) >= _MAX_LEGACY_CONTEXTS:
        removable = next((context for context, item in entries.items() if item.session_id != protected), None)
        if removable is None:
          raise ProtocolError(code="capacity", field="legacy")
        entries.pop(removable)
      entry = _LegacyEntry(session_id=_legacy_session(frame.context))
    if state is None and entry.state is None:
      entries[frame.context] = replace(entry, pending_route=route, pending_at=frame.received_at)
      entries.move_to_end(frame.context)
      return None, entries
    if state is None:
      if frame.received_at >= entry.state_at + LEGACY_TTL_MS / 1_000:
        raise ProtocolError(code="expired", field="legacy.state")
      next_entry = replace(entry, sequence=entry.sequence + 1,
                           timestamp_ms=max(timestamp, entry.timestamp_ms + 1), pending_route=None)
      lease_at, renews = entry.state_at, False
    else:
      if route is None and entry.pending_route is not None and (frame.received_at - entry.pending_at) * 1_000 <= _ROUTE_BUFFER_MS:
        route = entry.pending_route
      next_entry = replace(entry, sequence=entry.sequence + 1, timestamp_ms=max(timestamp, entry.timestamp_ms + 1,
                           int(frame.received_at * 1_000) + 1), state=state, state_at=frame.received_at, pending_route=None)
      lease_at, renews = frame.received_at, True
    entries[frame.context] = next_entry
    entries.move_to_end(frame.context)
    assembled_state = next_entry.state
    if assembled_state is None:
      raise ProtocolError(code="missing", field="legacy.state")
    active = assembled_state.status is NavigationStatus.GUIDING
    envelope = NavigationEnvelope(ProviderSource.TMAP_LEGACY, 1, next_entry.session_id, next_entry.sequence,
                                  next_entry.timestamp_ms, active, LEGACY_TTL_MS, assembled_state, route)
    return _Prepared(envelope, lease_at, renews), entries


class NavigationMux:
  """Serialize validated frames through one stable provider-lease state machine."""

  def __init__(self) -> None:
    self._lock = Lock()
    self._legacy = _LegacyAdapter()
    self._candidates: dict[tuple[ProviderSource, UUID], _Candidate] = {}
    self._sessions: dict[ProviderSource, UUID] = {}
    self._retired: OrderedDict[tuple[ProviderSource, UUID], None] = OrderedDict()
    self._orders: dict[tuple[ProviderSource, UUID], tuple[int, int]] = {}
    self._active: tuple[ProviderSource, UUID] | None = None
    self._clock = 0.0

  @property
  def current(self) -> NavigationEnvelope | None:
    with self._lock:
      return self._candidates[self._active].envelope if self._active is not None else None

  def ingest(self, frame: ReceivedFrame) -> tuple[ProviderTransition, ...]:
    root = _decode(frame.data)
    canonical = "schema_version" in root or "source" in root
    parsed = _Prepared(_canonical(root), frame.received_at, True) if canonical else None
    with self._lock:
      if not math.isfinite(frame.received_at) or frame.received_at < self._clock or not frame.context.peer or len(frame.context.peer) > 255:
        raise ProtocolError(code="bounds", field="receipt")
      legacy_entries = None
      if parsed is None:
        session = _legacy_session(frame.context)
        if (ProviderSource.TMAP_LEGACY, session) in self._retired:
          raise ProtocolError(code="session", field="session_id")
        parsed, legacy_entries = self._legacy.prepare(root, frame, self._sessions.get(ProviderSource.TMAP_LEGACY))
        if parsed is None:
          self._legacy.entries, self._clock = legacy_entries, frame.received_at
          return ()
      envelope = parsed.envelope
      key = (envelope.source, envelope.session_id)
      if key in self._retired:
        raise ProtocolError(code="session", field="session_id")
      order = self._orders.get(key)
      if order is not None and envelope.sequence <= order[0]:
        raise ProtocolError(code="sequence", field="sequence")
      if order is not None and envelope.sender_timestamp_ms <= order[1]:
        raise ProtocolError(code="timestamp", field="timestamp_ms")
      source_session = self._sessions.get(envelope.source)
      if source_session is not None and source_session != envelope.session_id and not envelope.navigation_active:
        raise ProtocolError(code="session", field="session_id")
      previous_before_expiry = self._candidates.get(key)
      if not parsed.renews_lease and previous_before_expiry is None:
        raise ProtocolError(code="route_only", field="legacy")
      events = list(self._expire(frame.received_at))
      if source_session is not None and source_session != envelope.session_id:
        old_key = (envelope.source, source_session)
        self._retired[old_key] = None
        self._candidates.pop(old_key, None)
        self._orders.pop(old_key, None)
        while len(self._retired) > _MAX_RETIRED_SESSIONS:
          self._retired.popitem(last=False)
      self._sessions[envelope.source] = envelope.session_id
      self._orders[key] = (envelope.sequence, envelope.sender_timestamp_ms)
      previous = self._candidates.get(key)
      newly_guiding = parsed.renews_lease and envelope.navigation_active and (previous is None or not previous.envelope.navigation_active)
      if envelope.navigation_active and envelope.route is None and previous is not None:
        envelope = replace(envelope, route=previous.envelope.route)
      lease_at = frame.received_at if parsed.renews_lease else previous_before_expiry.received_at
      self._candidates[key] = _Candidate(envelope, lease_at)
      if legacy_entries is not None:
        self._legacy.entries = legacy_entries
      self._clock = frame.received_at
      event = self._switch(key, "new_guidance") if newly_guiding else None
      if not envelope.navigation_active and self._active == key:
        event = self._switch(self._fallback(), envelope.state.status.value)
      if event is not None:
        events.append(event)
      return tuple(events)

  def expire(self, now: float) -> tuple[ProviderTransition, ...]:
    with self._lock:
      if not math.isfinite(now) or now < self._clock:
        raise ProtocolError(code="bounds", field="receipt")
      self._clock = now
      return self._expire(now)

  def _expire(self, now: float) -> tuple[ProviderTransition, ...]:
    stale = [key for key, candidate in self._candidates.items()
             if now >= candidate.received_at + candidate.envelope.ttl_ms / 1_000]
    active_expired = self._active in stale
    for key in stale:
      self._candidates.pop(key)
    event = self._switch(self._fallback(), "expiry") if active_expired else None
    return (event,) if event is not None else ()

  def _fallback(self) -> tuple[ProviderSource, UUID] | None:
    guiding = ((candidate.received_at, key[0].value, str(key[1]), key) for key, candidate in self._candidates.items()
               if candidate.envelope.navigation_active)
    return max(guiding, default=(0.0, "", "", None))[3]

  def _switch(self, key: tuple[ProviderSource, UUID] | None, reason: str) -> ProviderTransition | None:
    if key == self._active:
      return None
    previous = self._active[0] if self._active is not None else None
    self._active = key
    return ProviderTransition(previous=previous, current=key[0] if key is not None else None, reason=reason)
