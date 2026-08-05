from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import StrEnum
import math
import threading
from types import MappingProxyType


class NavigationSource(StrEnum):
  TMAP_LEGACY = "tmap_legacy"
  CARROT_NAVI_V2 = "carrot_navi_v2"
  NAVER_V1 = "naver_v1"


class NavigationLifecycle(StrEnum):
  IDLE = "idle"
  GUIDING = "guiding"
  STOPPED = "stopped"
  ARRIVED = "arrived"


@dataclass(frozen=True, slots=True)
class NavigationInstruction:
  present: bool = False
  turn_type: int = -1
  distance_m: float = 0.0
  road_name: str = ""
  main_text: str = ""
  near_direction: str = ""
  far_direction: str = ""
  next_road_width: int = 0
  received_mono_s: float | None = None


@dataclass(frozen=True, slots=True)
class SafetyItem:
  type: int
  distance_m: float
  speed_limit_kph: float
  received_mono_s: float
  reason: str = "cam"
  section: bool = False
  section_type: int = -1
  block_type: int = -1
  block_speed_kph: float = 0.0
  block_distance_m: float = 0.0
  revision: int | None = None

  @property
  def limit_kph(self) -> float:
    return self.speed_limit_kph


@dataclass(frozen=True, slots=True)
class NavigationControlState:
  current: NavigationInstruction = field(default_factory=NavigationInstruction)
  next: NavigationInstruction = field(default_factory=NavigationInstruction)
  safety: SafetyItem | None = None
  secondary_safety: SafetyItem | None = None
  speed_present: bool = False
  speed_received_mono_s: float | None = None
  road_limit_kph: float | None = None
  road_limit_received_mono_s: float | None = None
  road_category: int | None = None
  road_category_received_mono_s: float | None = None
  route_present: bool = False
  route_received_mono_s: float | None = None
  remaining_distance_m: float = 0.0
  remaining_time_s: float = 0.0
  off_route: bool = False
  status_present: bool = False
  status_received_mono_s: float | None = None
  destination: tuple[float, float] | None = None
  destination_present: bool = False
  destination_received_mono_s: float | None = None
  route_points: tuple[tuple[float, float], ...] = ()
  position_present: bool = False
  position_received_mono_s: float | None = None
  position_latitude: float = 0.0
  position_longitude: float = 0.0
  position_heading_deg: float = 0.0
  position_speed_kph: float = 0.0
  position_road_name: str = ""
  traffic_present: bool = False
  traffic_received_mono_s: float | None = None
  traffic_visible: bool = False
  traffic_distance_m: float = 0.0
  traffic_source: str = ""
  traffic_lamp: str = ""
  traffic_remain_s: int = 0


@dataclass(frozen=True, slots=True)
class NavigationSnapshot:
  source: NavigationSource
  session_id: str
  sequence: int
  lifecycle: NavigationLifecycle
  received_mono_s: float
  activation_epoch: int
  control: NavigationControlState
  owner_received_mono_s: float | None = None


@dataclass(frozen=True, slots=True)
class NavigationSelection:
  snapshot: NavigationSnapshot | None
  reason: str
  owner_age_s: float | None
  safety_age_s: float | None
  road_category_age_s: float | None
  projection_revision: int
  transport_loss_age_s: float | None = None
  secondary_safety_age_s: float | None = None

  @property
  def owner_age(self) -> float | None:
    return self.owner_age_s

  @property
  def safety_age(self) -> float | None:
    return self.safety_age_s

  @property
  def category_age(self) -> float | None:
    return self.road_category_age_s


@dataclass(frozen=True, slots=True)
class SafetyDecision:
  provider: str
  reason: str
  limit_kph: float
  distance_m: float
  type: int
  rejection: str | None = None


NAVER_V1_LEASE_S = 2.0
TMAP_LEGACY_LEASE_S = 4.0
CARROT_NAVI_V2_LEASE_S = 10.0
SOURCE_LEASE_S = MappingProxyType({
  NavigationSource.TMAP_LEGACY: TMAP_LEGACY_LEASE_S,
  NavigationSource.CARROT_NAVI_V2: CARROT_NAVI_V2_LEASE_S,
  NavigationSource.NAVER_V1: NAVER_V1_LEASE_S,
})
SUPPORTED_SAFETY_TYPES = frozenset((0, 1, 2, 3, 4, 7, 8, 22, 75, 76))

_SOURCE_ORDER = {source: index for index, source in enumerate(NavigationSource)}
_TERMINAL_LIFECYCLES = frozenset((NavigationLifecycle.STOPPED, NavigationLifecycle.ARRIVED))


def _finite(value: object) -> bool:
  return not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value)


def _age(now_s: float, received_mono_s: float) -> float:
  return max(0.0, now_s - received_mono_s)


def _owner_receipt(snapshot: NavigationSnapshot) -> float:
  return snapshot.received_mono_s if snapshot.owner_received_mono_s is None else snapshot.owner_received_mono_s


def _fresh(snapshot: NavigationSnapshot, now_s: float) -> bool:
  return (
    snapshot.lifecycle is NavigationLifecycle.GUIDING
    and _age(now_s, _owner_receipt(snapshot)) < SOURCE_LEASE_S[snapshot.source]
  )


class NavigationSourceStore:
  def __init__(self) -> None:
    self._lock = threading.RLock()
    self._snapshots: dict[NavigationSource, NavigationSnapshot] = {}
    self._sequences: dict[tuple[NavigationSource, str], int] = {}
    self._terminal_sessions: set[tuple[NavigationSource, str]] = set()
    self._transport_losses: dict[tuple[NavigationSource, str], float] = {}
    self._owner: NavigationSource | None = None
    self._owner_epoch_floor = 0
    self._owner_seen_activation_revision = 0
    self._activation_epoch = 0
    self._activation_revision = 0
    self._source_activation_revisions: dict[NavigationSource, int] = {}
    self._last_activation_mono_s: float | None = None
    self._pending_owner_expiry: tuple[NavigationSource, str] | None = None
    self._projection_key: tuple[object, ...] | None = None
    self._projection_revision = 0

  @staticmethod
  def _valid_candidate(candidate: object, now_s: object) -> bool:
    if not isinstance(candidate, NavigationSnapshot) or not _finite(now_s):
      return False
    if not isinstance(candidate.source, NavigationSource):
      return False
    if not isinstance(candidate.lifecycle, NavigationLifecycle):
      return False
    if not isinstance(candidate.session_id, str) or not candidate.session_id:
      return False
    if isinstance(candidate.sequence, bool) or not isinstance(candidate.sequence, int) or candidate.sequence < 0:
      return False
    return (
      _finite(candidate.received_mono_s)
      and (candidate.owner_received_mono_s is None or _finite(candidate.owner_received_mono_s))
      and isinstance(candidate.control, NavigationControlState)
    )

  @staticmethod
  def _normalize_control(candidate: NavigationSnapshot, now_s: float) -> NavigationControlState | None:
    control = candidate.control
    updates: dict[str, object] = {}
    for field_name in ("safety", "secondary_safety"):
      item = getattr(control, field_name)
      if item is None:
        continue
      if not isinstance(item, SafetyItem) or not _finite(item.received_mono_s):
        return None
      received_mono_s = now_s if item.received_mono_s == candidate.received_mono_s else item.received_mono_s
      if received_mono_s > now_s:
        return None
      if received_mono_s != item.received_mono_s:
        updates[field_name] = replace(item, received_mono_s=received_mono_s)

    category_time = control.road_category_received_mono_s
    if control.road_category is not None:
      if category_time is None:
        updates["road_category_received_mono_s"] = now_s
      elif not _finite(category_time):
        return None
      else:
        normalized_time = now_s if category_time == candidate.received_mono_s else category_time
        if normalized_time > now_s:
          return None
        if normalized_time != category_time:
          updates["road_category_received_mono_s"] = normalized_time
    elif category_time is not None:
      return None

    if control.route_present and not control.status_present:
      updates["status_present"] = True
      updates["status_received_mono_s"] = control.route_received_mono_s
    if control.destination is not None and not control.destination_present:
      updates["destination_present"] = True
      if control.destination_received_mono_s is None:
        updates["destination_received_mono_s"] = now_s
    if not control.speed_present:
      speed_receipts = [
        receipt for receipt in (
          None if control.safety is None else control.safety.received_mono_s,
          None if control.secondary_safety is None else control.secondary_safety.received_mono_s,
          control.road_limit_received_mono_s,
        )
        if receipt is not None
      ]
      if speed_receipts:
        updates["speed_present"] = True
        updates["speed_received_mono_s"] = max(speed_receipts)

    for field_name in ("current", "next"):
      instruction = getattr(control, field_name)
      if not instruction.present:
        continue
      received_mono_s = instruction.received_mono_s
      if received_mono_s is None:
        received_mono_s = now_s
      elif not _finite(received_mono_s):
        return None
      elif received_mono_s == candidate.received_mono_s:
        received_mono_s = now_s
      if received_mono_s > now_s:
        return None
      if received_mono_s != instruction.received_mono_s:
        updates[field_name] = replace(instruction, received_mono_s=received_mono_s)

    for value_name, receipt_name in (
      ("speed_present", "speed_received_mono_s"),
      ("road_limit_kph", "road_limit_received_mono_s"),
      ("route_present", "route_received_mono_s"),
      ("status_present", "status_received_mono_s"),
      ("destination_present", "destination_received_mono_s"),
      ("position_present", "position_received_mono_s"),
      ("traffic_present", "traffic_received_mono_s"),
    ):
      present = getattr(control, value_name)
      receipt = getattr(control, receipt_name)
      if not present and value_name != "road_limit_kph":
        if receipt is not None:
          return None
        continue
      if value_name == "road_limit_kph" and present is None:
        if receipt is not None:
          return None
        continue
      if receipt is None:
        updates[receipt_name] = now_s
      elif not _finite(receipt):
        return None
      else:
        normalized_time = now_s if receipt == candidate.received_mono_s else receipt
        if normalized_time > now_s:
          return None
        if normalized_time != receipt:
          updates[receipt_name] = normalized_time
    return replace(control, **updates) if updates else control

  def _new_epoch(self, now_s: float) -> int:
    if self._last_activation_mono_s != now_s:
      self._activation_epoch += 1
      self._last_activation_mono_s = now_s
    return self._activation_epoch

  @staticmethod
  def _retain_unchanged_naver_safety(
      candidate: NavigationSnapshot, current: NavigationSnapshot | None,
  ) -> NavigationSnapshot:
    if (
      candidate.source is not NavigationSource.NAVER_V1
      or current is None
      or current.session_id != candidate.session_id
      or current.lifecycle is not NavigationLifecycle.GUIDING
      or candidate.lifecycle is not NavigationLifecycle.GUIDING
    ):
      return candidate
    updates: dict[str, SafetyItem] = {}
    for field_name in ("safety", "secondary_safety"):
      incoming = getattr(candidate.control, field_name)
      cached = getattr(current.control, field_name)
      if (
        incoming is not None
        and cached is not None
        and incoming.revision is not None
        and incoming.revision == cached.revision
        and replace(incoming, received_mono_s=cached.received_mono_s) == cached
      ):
        updates[field_name] = cached
    if not updates:
      return candidate
    return replace(candidate, control=replace(candidate.control, **updates))

  def accept(self, candidate: NavigationSnapshot, now_s: float) -> bool:
    if not self._valid_candidate(candidate, now_s):
      return False
    now_s = float(now_s)
    with self._lock:
      if candidate.owner_received_mono_s is not None and candidate.owner_received_mono_s > now_s:
        return False
      key = (candidate.source, candidate.session_id)
      if key in self._terminal_sessions:
        return False
      previous_sequence = self._sequences.get(key)
      if previous_sequence is not None and candidate.sequence <= previous_sequence:
        return False

      current = self._snapshots.get(candidate.source)
      if current is not None and now_s < current.received_mono_s:
        return False
      candidate = self._retain_unchanged_naver_safety(candidate, current)
      control = self._normalize_control(candidate, now_s)
      if control is None:
        return False
      if (
        candidate.lifecycle in _TERMINAL_LIFECYCLES
        and current is not None
        and current.session_id != candidate.session_id
      ):
        self._sequences[key] = candidate.sequence
        self._transport_losses.pop(key, None)
        self._terminal_sessions.add(key)
        return True

      expired_owner_refresh = (
        candidate.source is self._owner
        and candidate.source is not NavigationSource.TMAP_LEGACY
        and current is not None
        and current.session_id == candidate.session_id
        and current.lifecycle is NavigationLifecycle.GUIDING
        and candidate.lifecycle is NavigationLifecycle.GUIDING
        and not _fresh(current, now_s)
      )
      activating = candidate.lifecycle is NavigationLifecycle.GUIDING and (
        current is None
        or current.lifecycle is not NavigationLifecycle.GUIDING
        or current.session_id != candidate.session_id
        or (candidate.source is NavigationSource.TMAP_LEGACY and not _fresh(current, now_s))
      )
      if activating:
        activation_epoch = self._new_epoch(now_s)
        self._activation_revision += 1
        self._source_activation_revisions[candidate.source] = self._activation_revision
      elif current is not None:
        activation_epoch = current.activation_epoch
      else:
        activation_epoch = 0

      accepted = replace(
        candidate,
        received_mono_s=now_s,
        activation_epoch=activation_epoch,
        control=control,
      )
      self._snapshots[candidate.source] = accepted
      self._sequences[key] = candidate.sequence
      self._transport_losses.pop(key, None)
      if expired_owner_refresh:
        self._pending_owner_expiry = key
      elif (
        self._pending_owner_expiry is not None
        and self._pending_owner_expiry[0] is candidate.source
        and (
          self._pending_owner_expiry[1] != candidate.session_id
          or candidate.lifecycle is not NavigationLifecycle.GUIDING
        )
      ):
        self._pending_owner_expiry = None
      if candidate.lifecycle in _TERMINAL_LIFECYCLES:
        self._terminal_sessions.add(key)
      return True

  def record_transport_loss(self, source: NavigationSource, session_id: str, now_s: float) -> bool:
    if not isinstance(source, NavigationSource) or not isinstance(session_id, str) or not _finite(now_s):
      return False
    now_s = float(now_s)
    with self._lock:
      current = self._snapshots.get(source)
      if current is None or current.session_id != session_id or now_s < current.received_mono_s:
        return False
      self._transport_losses[(source, session_id)] = now_s
      return True

  @staticmethod
  def _activation_choice(snapshots: list[NavigationSnapshot]) -> NavigationSnapshot | None:
    if not snapshots:
      return None
    newest_epoch = max(snapshot.activation_epoch for snapshot in snapshots)
    tied = (snapshot for snapshot in snapshots if snapshot.activation_epoch == newest_epoch)
    return min(tied, key=lambda snapshot: _SOURCE_ORDER[snapshot.source])

  @staticmethod
  def _fallback_choice(snapshots: list[NavigationSnapshot]) -> NavigationSnapshot | None:
    if not snapshots:
      return None
    newest_receipt = max(_owner_receipt(snapshot) for snapshot in snapshots)
    tied = (snapshot for snapshot in snapshots if _owner_receipt(snapshot) == newest_receipt)
    return min(tied, key=lambda snapshot: _SOURCE_ORDER[snapshot.source])

  def _select_owner(self, now_s: float) -> tuple[NavigationSnapshot | None, str]:
    active = [snapshot for snapshot in self._snapshots.values() if _fresh(snapshot, now_s)]
    current = self._snapshots.get(self._owner) if self._owner is not None else None
    if self._owner is None:
      selected = self._activation_choice(active)
      self._owner = None if selected is None else selected.source
      if selected is not None:
        self._owner_epoch_floor = selected.activation_epoch
        self._owner_seen_activation_revision = self._activation_revision
      return selected, "no_owner" if selected is None else "activated"

    if self._pending_owner_expiry is not None:
      pending_source, pending_session = self._pending_owner_expiry
      self._pending_owner_expiry = None
      if (
        pending_source is self._owner
        and current is not None
        and current.session_id == pending_session
      ):
        selected = self._fallback_choice([
          snapshot for snapshot in active if snapshot.source is not current.source
        ])
        if selected is None and current in active:
          selected = current
        self._owner = None if selected is None else selected.source
        self._owner_epoch_floor = (
          0 if selected is None else max(snapshot.activation_epoch for snapshot in active)
        )
        self._owner_seen_activation_revision = self._activation_revision
        return selected, "owner_expired"

    if current is None:
      reason = "owner_missing"
    elif current.lifecycle in _TERMINAL_LIFECYCLES:
      reason = "owner_terminal"
    elif current.lifecycle is not NavigationLifecycle.GUIDING:
      reason = "owner_inactive"
    elif not _fresh(current, now_s):
      reason = "owner_expired"
    else:
      new_activations = [
        snapshot for snapshot in active
        if self._source_activation_revisions.get(snapshot.source, 0) > self._owner_seen_activation_revision
      ]
      challenger = self._activation_choice(new_activations)
      if challenger is not None and (
        challenger.activation_epoch > self._owner_epoch_floor
        or (
          challenger.activation_epoch == self._owner_epoch_floor
          and _SOURCE_ORDER[challenger.source] < _SOURCE_ORDER[current.source]
        )
      ):
        self._owner = challenger.source
        self._owner_epoch_floor = challenger.activation_epoch
        self._owner_seen_activation_revision = self._activation_revision
        return challenger, "new_activation"
      if challenger is not None:
        self._owner_epoch_floor = max(self._owner_epoch_floor, challenger.activation_epoch)
        self._owner_seen_activation_revision = self._activation_revision
      return current, "owner_sticky"

    selected = self._fallback_choice(active)
    self._owner = None if selected is None else selected.source
    if selected is None:
      self._owner_epoch_floor = 0
    else:
      self._owner_epoch_floor = max(snapshot.activation_epoch for snapshot in active)
    self._owner_seen_activation_revision = self._activation_revision
    return selected, reason

  def select(self, now_s: float) -> NavigationSelection:
    if not _finite(now_s):
      raise ValueError("now_s must be a finite local monotonic time")
    now_s = float(now_s)
    with self._lock:
      raw, reason = self._select_owner(now_s)
      if raw is None:
        projection_key = None
        owner_age_s = safety_age_s = secondary_age_s = category_age_s = transport_loss_age_s = None
        projected = None
      else:
        lease_s = SOURCE_LEASE_S[raw.source]
        owner_age_s = _age(now_s, _owner_receipt(raw))
        safety = raw.control.safety
        safety_age_s = None if safety is None else _age(now_s, safety.received_mono_s)
        safety_fresh = safety_age_s is not None and safety_age_s < lease_s
        secondary = raw.control.secondary_safety
        secondary_age_s = None if secondary is None else _age(now_s, secondary.received_mono_s)
        secondary_fresh = secondary_age_s is not None and secondary_age_s < lease_s
        speed_time = raw.control.speed_received_mono_s
        speed_age_s = None if speed_time is None else _age(now_s, speed_time)
        speed_fresh = raw.control.speed_present and speed_age_s is not None and speed_age_s < lease_s
        current_time = raw.control.current.received_mono_s
        current_age_s = None if current_time is None else _age(now_s, current_time)
        current_fresh = raw.control.current.present and current_age_s is not None and current_age_s < lease_s
        next_time = raw.control.next.received_mono_s
        next_age_s = None if next_time is None else _age(now_s, next_time)
        next_fresh = raw.control.next.present and next_age_s is not None and next_age_s < lease_s
        road_limit_time = raw.control.road_limit_received_mono_s
        road_limit_age_s = None if road_limit_time is None else _age(now_s, road_limit_time)
        road_limit_fresh = (
          raw.control.road_limit_kph is not None
          and road_limit_age_s is not None
          and road_limit_age_s < lease_s
        )
        category_time = raw.control.road_category_received_mono_s
        category_age_s = None if category_time is None else _age(now_s, category_time)
        category_fresh = raw.control.road_category is not None and category_age_s is not None and category_age_s < lease_s
        route_time = raw.control.route_received_mono_s
        route_age_s = None if route_time is None else _age(now_s, route_time)
        route_fresh = raw.control.route_present and route_age_s is not None and route_age_s < lease_s
        status_time = raw.control.status_received_mono_s
        status_age_s = None if status_time is None else _age(now_s, status_time)
        status_fresh = raw.control.status_present and status_age_s is not None and status_age_s < lease_s
        destination_time = raw.control.destination_received_mono_s
        destination_age_s = None if destination_time is None else _age(now_s, destination_time)
        destination_fresh = (
          raw.control.destination_present
          and raw.control.destination is not None
          and destination_age_s is not None
          and destination_age_s < lease_s
        )
        position_time = raw.control.position_received_mono_s
        position_age_s = None if position_time is None else _age(now_s, position_time)
        position_fresh = raw.control.position_present and position_age_s is not None and position_age_s < lease_s
        traffic_time = raw.control.traffic_received_mono_s
        traffic_age_s = None if traffic_time is None else _age(now_s, traffic_time)
        traffic_fresh = raw.control.traffic_present and traffic_age_s is not None and traffic_age_s < lease_s

        projected_control = replace(
          raw.control,
          current=raw.control.current if current_fresh else NavigationInstruction(),
          next=raw.control.next if next_fresh else NavigationInstruction(),
          safety=safety if safety_fresh else None,
          secondary_safety=secondary if secondary_fresh else None,
          speed_present=speed_fresh,
          speed_received_mono_s=speed_time if speed_fresh else None,
          road_limit_kph=raw.control.road_limit_kph if road_limit_fresh else None,
          road_limit_received_mono_s=road_limit_time if road_limit_fresh else None,
          road_category=raw.control.road_category if category_fresh else None,
          road_category_received_mono_s=category_time if category_fresh else None,
          route_present=route_fresh,
          route_received_mono_s=route_time if route_fresh else None,
          remaining_distance_m=raw.control.remaining_distance_m if route_fresh else 0.0,
          remaining_time_s=raw.control.remaining_time_s if route_fresh else 0.0,
          off_route=raw.control.off_route if status_fresh else False,
          status_present=status_fresh,
          status_received_mono_s=status_time if status_fresh else None,
          destination=raw.control.destination if destination_fresh else None,
          destination_present=destination_fresh,
          destination_received_mono_s=destination_time if destination_fresh else None,
          route_points=raw.control.route_points if route_fresh else (),
          position_present=position_fresh,
          position_received_mono_s=position_time if position_fresh else None,
          traffic_present=traffic_fresh,
          traffic_received_mono_s=traffic_time if traffic_fresh else None,
        )
        projected = replace(raw, control=projected_control)
        projection_key = (
          raw.source,
          raw.session_id,
          raw.sequence,
          current_fresh,
          next_fresh,
          safety_fresh,
          secondary_fresh,
          speed_fresh,
          road_limit_fresh,
          category_fresh,
          route_fresh,
          status_fresh,
          destination_fresh,
          position_fresh,
          traffic_fresh,
        )
        loss_time = self._transport_losses.get((raw.source, raw.session_id))
        transport_loss_age_s = None if loss_time is None else _age(now_s, loss_time)

      if projection_key != self._projection_key:
        self._projection_revision += 1
        self._projection_key = projection_key
      return NavigationSelection(
        snapshot=projected,
        reason=reason,
        owner_age_s=owner_age_s,
        safety_age_s=safety_age_s,
        road_category_age_s=category_age_s,
        projection_revision=self._projection_revision,
        transport_loss_age_s=transport_loss_age_s,
        secondary_safety_age_s=secondary_age_s,
      )


def _navigation_safety(selection: NavigationSelection, mode: int, safety_factor: float,
                       bump_speed_kph: float) -> tuple[SafetyDecision | None, str | None]:
  snapshot = selection.snapshot
  if snapshot is None:
    return None, "no_owner"
  control = snapshot.control
  if control.off_route:
    return None, "off_route"

  def item_decision(item: SafetyItem | None) -> tuple[SafetyDecision | None, str | None]:
    if item is None:
      return None, None
    if type(item.type) is not int:
      return None, "invalid_item"
    if item.type not in SUPPORTED_SAFETY_TYPES:
      return None, "unsupported_type"
    if not _finite(item.distance_m) or item.distance_m <= 0.0:
      return None, "invalid_distance"
    if mode <= 0:
      return None, "mode_disabled"
    if item.type == 7 and mode != 3:
      return None, "mobile_requires_mode_3"
    if item.type == 22:
      if mode < 2:
        return None, "mode_disabled"
      if control.road_category is None and snapshot.source is not NavigationSource.NAVER_V1:
        return None, "road_category_missing"
      if control.road_category is not None and control.road_category <= 1:
        return None, "road_category_blocked"
      if not _finite(bump_speed_kph) or bump_speed_kph <= 0.0:
        return None, "invalid_item"
      return SafetyDecision(snapshot.source.value, "bump", float(bump_speed_kph), item.distance_m, 22), None

    limit_kph = item.speed_limit_kph
    distance_m = item.distance_m
    decision_type = item.type
    reason = "section" if item.type == 4 else "cam"
    if item.block_type in (2, 3):
      limit_kph = item.block_speed_kph
      distance_m = item.block_distance_m
      decision_type = 4
      reason = "section"
    if not _finite(limit_kph) or limit_kph <= 0.0:
      return None, "invalid_item"
    if not _finite(distance_m) or distance_m <= 0.0:
      return None, "invalid_distance"
    return SafetyDecision(
      snapshot.source.value,
      reason,
      float(limit_kph) * safety_factor,
      float(distance_m),
      decision_type,
    ), None

  primary, primary_rejection = item_decision(control.safety)
  if primary is not None:
    return primary, None
  secondary, secondary_rejection = item_decision(control.secondary_safety)
  if secondary is not None:
    return secondary, None
  if primary_rejection is not None:
    return None, primary_rejection
  if secondary_rejection is not None:
    return None, secondary_rejection
  if selection.safety_age_s is not None or selection.secondary_safety_age_s is not None:
    return None, "safety_stale"
  return None, "safety_absent"


def safety_rejection(selection: NavigationSelection, mode: int = 3, safety_factor: float = 1.0,
                     bump_speed_kph: float = 20.0) -> str | None:
  _, rejection = _navigation_safety(selection, mode, safety_factor, bump_speed_kph)
  return rejection


def choose_safety(selection: NavigationSelection, hda_limit_kph: float,
                  hda_distance_m: float, *, mode: int = 3, safety_factor: float = 1.0,
                  bump_speed_kph: float = 20.0) -> SafetyDecision | None:
  factor = safety_factor if _finite(safety_factor) and safety_factor > 0.0 else 1.0
  navigation, rejection = _navigation_safety(selection, mode, factor, bump_speed_kph)
  if navigation is not None:
    return navigation
  if (
    _finite(hda_limit_kph)
    and _finite(hda_distance_m)
    and hda_limit_kph > 0.0
    and hda_distance_m > 0.0
  ):
    return SafetyDecision(
      provider="hda",
      reason="hda",
      limit_kph=float(hda_limit_kph) * factor,
      distance_m=float(hda_distance_m),
      type=-1,
      rejection=rejection,
    )
  return None
