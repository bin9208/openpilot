"""Source arbitration and adapters; longitudinal control remains in CarrotServ."""
from dataclasses import replace
import math
import threading

from openpilot.selfdrive.carrot.carrot_navi_control import (
  CarrotNaviControl, NaviGuidanceControl, NaviRouteControl, NaviSpeedControl,
  NaviTrafficControl, NaviVehicleControl, parse_carrot_navi_control,
)
from openpilot.selfdrive.carrot.navigation_sources import (
  NavigationControlState, NavigationInstruction, NavigationLifecycle,
  NavigationSnapshot, NavigationSource, NavigationSourceStore, SafetyItem,
)


def _value(obj, key, default=None):
  return obj.get(key, default) if isinstance(obj, dict) else getattr(obj, key, default)


class NavigationRuntime:
  def __init__(self):
    self.lock = threading.RLock()
    self.store = NavigationSourceStore()
    self._legacy_sequence = 0
    self._legacy_snapshot = None
    self._legacy_road_limit_state = {}
    self._legacy_navigation_controls = {}
    self._legacy_navigation_pending = {}
    self._v2_session = ''
    self._v2_sequence = 0
    self._v2_items = {}
    self._projection_session = None
    self._component_keys = {}
    self._component_sequences = {}

  def accept_snapshot(self, snapshot):
    with self.lock:
      if snapshot.source is NavigationSource.NAVER_V1 and snapshot.control.route_present:
        # Naver's route envelope carries the off-route status.
        snapshot = replace(snapshot, control=replace(snapshot.control, status_present=True,
          status_received_mono_s=snapshot.control.route_received_mono_s))
      return self.store.accept(snapshot, snapshot.received_mono_s)

  def accept_legacy(self, data, session_id, now_s):
    if not isinstance(data, dict) or 'nRoadLimitSpeed' not in data or not session_id:
      return False
    with self.lock:
      snapshot = self._legacy_navigation_snapshot(data, NavigationSource.TMAP_LEGACY, session_id, now_s)
      accepted = self.accept_snapshot(snapshot)
      if accepted:
        # Only the most recent legacy session can be a candidate.
        self._legacy_navigation_controls = {session_id: snapshot.control}
        self._legacy_road_limit_state = {session_id: self._legacy_road_limit_state[session_id]}
        self._legacy_snapshot = snapshot
        self._legacy_navigation_pending.pop(session_id, None)
      return accepted

  def accept_legacy_aux(self, session_id, now_s, *, route_points=None, traffic=None):
    with self.lock:
      previous = self._legacy_snapshot
      if not session_id or not math.isfinite(now_s):
        return False
      if previous is not None and previous.session_id == session_id and now_s < previous.received_mono_s:
        return False
      updates = {}
      if route_points is not None:
        points = tuple(route_points)
        if len(points) > 4096 or any(len(p) != 2 or not all(math.isfinite(v) for v in p)
          or not (-90 <= p[0] <= 90 and -180 <= p[1] <= 180) for p in points):
          return False
        updates.update(route_present=bool(points), route_points=points,
                       route_received_mono_s=now_s if points else None)
      if traffic is not None:
        updates.update(traffic)
        updates['traffic_received_mono_s'] = now_s if traffic.get('traffic_present') else None
      if not updates:
        return False
      if previous is None or previous.session_id != session_id:
        pending = self._legacy_navigation_pending
        if len(pending) >= 4 and session_id not in pending:
          pending.pop(next(iter(pending)))
        pending.setdefault(session_id, {}).update(updates)
        return False  # Buffered, not an activation or an accepted owner update.
      self._legacy_sequence += 1
      snapshot = replace(previous, sequence=self._legacy_sequence, received_mono_s=now_s,
        owner_received_mono_s=previous.received_mono_s if previous.owner_received_mono_s is None else previous.owner_received_mono_s,
        control=replace(previous.control, **updates))
      if not self.accept_snapshot(snapshot):
        return False
      self._legacy_snapshot = snapshot
      self._legacy_navigation_controls = {session_id: snapshot.control}
      return True

  def _v2_receipt(self, payload, name, now_s):
    meta = _value(_value(payload, name), 'meta')
    sequence = int(_value(meta, 'sequence', 0))
    nanos = int(_value(meta, 'receivedMonoTimeNanos', 0))
    if nanos > 0:
      return nanos / 1e9
    previous = self._v2_items.get(name)
    receipt = previous[1] if previous is not None and previous[0] == sequence else now_s
    self._v2_items[name] = (sequence, receipt)
    return receipt

  def accept_v2(self, payload, now_s):
    navi = parse_carrot_navi_control(payload)
    if navi is None or not navi.session_id:
      self.v2_transport_lost(now_s)
      return False
    with self.lock:
      if self._v2_session != navi.session_id:
        self._v2_items.clear()
      self._v2_session = navi.session_id
      self._v2_sequence += 1
      receipts = {name: self._v2_receipt(payload, name, now_s) for name in (
        'speed', 'guidanceCurrent', 'guidanceNext', 'vehicle', 'route', 'trafficSignal',
        'laneCurrent', 'navigationStatus')}
      if any(not math.isfinite(t) or t > now_s for t in receipts.values()):
        return False
      def guidance(item, name):
        return NavigationInstruction(present=item.present, turn_type=item.turn_type,
          distance_m=item.distance_m, main_text=item.main_text, near_direction=item.near_direction,
          far_direction=item.far_direction, received_mono_s=receipts[name] if item.present else None)
      speed = navi.speed
      def safety(secondary=False):
        prefix = 'secondary_sdi_' if secondary else 'sdi_'
        if not getattr(speed, prefix + 'present'):
          return None
        kind = getattr(speed, prefix + 'type')
        return SafetyItem(type=kind, distance_m=getattr(speed, prefix + 'distance_m'),
          speed_limit_kph=getattr(speed, prefix + 'speed_limit_kph'), received_mono_s=receipts['speed'],
          reason='bump' if kind == 22 else 'cam', section_type=getattr(speed, prefix + 'section_type'),
          block_type=getattr(speed, prefix + 'block_type'), block_speed_kph=getattr(speed, prefix + 'block_speed_kph'),
          block_distance_m=getattr(speed, prefix + 'block_distance_m'))
      primary = safety()
      if speed.section_active:
        primary = SafetyItem(4, speed.section_remaining_distance_m, speed.section_speed_limit_kph,
          receipts['speed'], reason='section', section=True, section_type=1, block_type=2,
          block_speed_kph=speed.section_speed_limit_kph, block_distance_m=speed.section_remaining_distance_m)
      status_present = bool(_value(_value(_value(payload, 'navigationStatus'), 'meta'), 'present', False))
      control = NavigationControlState(
        current=guidance(navi.current, 'guidanceCurrent'), next=guidance(navi.next, 'guidanceNext'),
        safety=primary, secondary_safety=safety(True), speed_present=speed.present,
        speed_received_mono_s=receipts['speed'] if speed.present else None,
        road_limit_kph=speed.road_limit_kph,
        road_limit_received_mono_s=receipts['speed'] if speed.road_limit_kph is not None else None,
        road_category=navi.road_category,
        road_category_received_mono_s=receipts['laneCurrent'] if navi.road_category is not None else None,
        route_present=navi.route.present, route_revision=navi.route.sequence,
        route_received_mono_s=receipts['route'] if navi.route.present else None,
        remaining_distance_m=navi.route.remaining_distance_m, remaining_time_s=navi.route.remaining_time_sec,
        route_points=navi.route.polyline, off_route=navi.off_route, status_present=status_present,
        status_received_mono_s=receipts['navigationStatus'] if status_present else None,
        position_present=navi.vehicle.present,
        position_received_mono_s=receipts['vehicle'] if navi.vehicle.present else None,
        position_latitude=navi.vehicle.latitude, position_longitude=navi.vehicle.longitude,
        position_heading_deg=navi.vehicle.heading_deg, position_speed_kph=navi.vehicle.speed_kph,
        position_road_name=navi.vehicle.road_name, traffic_present=navi.traffic.present,
        traffic_received_mono_s=receipts['trafficSignal'] if navi.traffic.present else None,
        traffic_visible=navi.traffic.visible, traffic_distance_m=navi.traffic.distance_m,
        traffic_source=navi.traffic.source, traffic_lamp=navi.traffic.lamp, traffic_remain_s=navi.traffic.remain_sec)
      guiding = navi.guidance_active or speed.present or navi.current.present or navi.next.present or navi.route.present
      snapshot = NavigationSnapshot(NavigationSource.CARROT_NAVI_V2, navi.session_id, self._v2_sequence,
        NavigationLifecycle.GUIDING if guiding else NavigationLifecycle.IDLE, now_s, 0, control)
      return self.accept_snapshot(snapshot)

  def v2_transport_lost(self, now_s):
    with self.lock:
      return self.store.record_transport_loss(NavigationSource.CARROT_NAVI_V2, self._v2_session, now_s)

  def _revision(self, component, key):
    if self._component_keys.get(component) != key:
      self._component_keys[component] = key
      self._component_sequences[component] = self._component_sequences.get(component, 0) + 1
    return self._component_sequences.get(component, 0)

  def select(self, now_s):
    with self.lock:
      selection = self.store.select(now_s)
      snapshot = selection.snapshot
      if snapshot is None:
        return selection, None
      session = (snapshot.source, snapshot.session_id)
      if session != self._projection_session:
        self._projection_session = session
        self._component_keys.clear()
      c = snapshot.control
      def guidance(item, name):
        return NaviGuidanceControl(item.present and not c.off_route, self._revision(name, item),
          round(item.distance_m), item.turn_type, item.main_text, item.near_direction, item.far_direction)
      primary, secondary = (None, None) if c.off_route else (c.safety, c.secondary_safety)
      fields = {}
      for prefix, item in (('sdi_', primary), ('secondary_sdi_', secondary)):
        if item is not None:
          fields.update({prefix + 'present': True, prefix + 'type': item.type,
            prefix + 'distance_m': round(item.distance_m), prefix + 'speed_limit_kph': round(item.speed_limit_kph),
            prefix + 'section_type': item.section_type, prefix + 'block_type': item.block_type,
            prefix + 'block_speed_kph': round(item.block_speed_kph), prefix + 'block_distance_m': round(item.block_distance_m)})
      if primary is not None and primary.section:
        fields.update(section_active=True, section_speed_limit_kph=round(primary.speed_limit_kph),
                      section_remaining_distance_m=round(primary.distance_m))
      speed = NaviSpeedControl(c.speed_present, self._revision('speed',
        (primary, secondary, c.road_limit_kph, c.road_category, c.off_route)),
        None if c.road_limit_kph is None else round(c.road_limit_kph), **fields)
      route_key = (c.route_present, c.route_revision, c.route_points, c.remaining_distance_m, c.remaining_time_s)
      return selection, CarrotNaviControl(
        session_id=f'{snapshot.source.value}:{snapshot.session_id}', speed=speed,
        current=guidance(c.current, 'current'), next=guidance(c.next, 'next'),
        vehicle=NaviVehicleControl(c.position_present, self._revision('vehicle', (c.position_present, c.position_received_mono_s)),
          c.position_latitude, c.position_longitude, c.position_heading_deg, c.position_speed_kph, c.position_road_name),
        route=NaviRouteControl(c.route_present, self._revision('route', route_key),
          round(c.remaining_distance_m), round(c.remaining_time_s), c.route_points),
        traffic=NaviTrafficControl(c.traffic_present, self._revision('traffic', (c.traffic_present, c.traffic_received_mono_s)),
          c.traffic_visible, round(c.traffic_distance_m), c.traffic_source, c.traffic_lamp, c.traffic_remain_s),
        off_route=c.off_route, guidance_active=True, road_category=c.road_category)

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
    self._legacy_sequence += 1
    sequence = self._legacy_sequence
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
