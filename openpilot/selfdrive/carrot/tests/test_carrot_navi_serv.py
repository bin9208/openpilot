import time
import threading

import pytest

from openpilot.common.constants import CV
from openpilot.selfdrive.carrot.carrot_navi_control import V2_ITEM_TTL_S as CONTROL_V2_ITEM_TTL_S
from openpilot.selfdrive.carrot.carrot_serv import V2_ITEM_TTL_S, CarrotServ
from openpilot.selfdrive.carrot.navigation_sources import (
  NavigationControlState,
  NavigationInstruction,
  NavigationLifecycle,
  NavigationSnapshot,
  NavigationSource,
  NavigationSourceStore,
  SafetyItem,
)


def _meta(sequence, present=True, received_mono_ns=None):
  return {
    "present": present,
    "sequence": sequence,
    "receivedMonoTimeNanos": time.monotonic_ns() if received_mono_ns is None else received_mono_ns,
  }


class _SubMaster:
  def __init__(self, data):
    self.data = data
    self.alive = {"carrotNavi": True}
    self.valid = {"carrotNavi": True}
    self.updated = {"carrotNavi": True}

  def __getitem__(self, service):
    assert service == "carrotNavi"
    return self.data


class _MemoryParams:
  def __init__(self):
    self.writes = []
    self.removed = []

  def put_nonblocking(self, key, value):
    self.writes.append((key, value))

  def remove(self, key):
    self.removed.append(key)


def _serv():
  serv = CarrotServ.__new__(CarrotServ)
  serv.carrot_navi_session_id = ""
  serv.carrot_navi_speed_sequence = -1
  serv.carrot_navi_current_sequence = -1
  serv.carrot_navi_next_sequence = -1
  serv.carrot_navi_lane_sequence = -1
  serv.carrot_navi_vehicle_sequence = -1
  serv.carrot_navi_route_sequence = -1
  serv.carrot_navi_projection_revision = 0
  serv.carrot_navi_speed_revision = None
  serv.carrot_navi_current_revision = None
  serv.carrot_navi_next_revision = None
  serv.carrot_navi_lane_revision = None
  serv.carrot_navi_last_valid_road_category = None
  serv.carrot_navi_last_valid_road_category_received_mono_time_nanos = 0
  serv.road_limit_write_generation = 0
  serv.road_limit_write_source = "init"
  serv.carrot_navi_projected_road_limit_generation = None
  serv.carrot_navi_active = False
  serv.carrot_navi_has_control = False
  serv.carrot_navi_road_limit_valid = False
  serv.carrot_navi_off_route = False
  serv.carrot_navi_traffic_active = False
  serv.carrot_navi_control = None
  serv.goalPosX = 0.0
  serv.goalPosY = 0.0
  serv.active_count = 0
  serv.active_sdi_count = 0
  serv.active_sdi_count_max = 200
  serv.carrotIndex = 0
  serv.nRoadLimitSpeed_counter = 0
  serv.phone_gps_frame = 0
  serv.phone_gps_accuracy = 0.0
  serv.phone_latitude = 0.0
  serv.phone_longitude = 0.0
  serv.nPosAnglePhone = 0.0
  serv.last_update_gps_time_phone = 0.0
  serv.active_kisa_count = 0
  serv.autoNaviSpeedCtrlMode = 3
  serv.autoNaviSpeedSafetyFactor = 1.0
  serv.autoNaviSpeedBumpSpeed = 20
  serv.is_metric = True
  serv.roadcate = 8
  serv.nRoadLimitSpeed = 30
  serv.params_memory = _MemoryParams()

  defaults = {
    "nSdiType": -1,
    "nSdiSpeedLimit": 0,
    "nSdiSection": -1,
    "nSdiDist": 0,
    "nSdiBlockType": -1,
    "nSdiBlockSpeed": 0,
    "nSdiBlockDist": 0,
    "nSdiPlusType": -1,
    "nSdiPlusSpeedLimit": 0,
    "nSdiPlusDist": 0,
    "nSdiPlusBlockType": -1,
    "nSdiPlusBlockSpeed": 0,
    "nSdiPlusBlockDist": 0,
    "xSpdType": -1,
    "xSpdLimit": 0,
    "xSpdDist": 0,
    "nTBTDist": 0,
    "nTBTTurnType": -1,
    "nTBTDistNext": 0,
    "nTBTTurnTypeNext": -1,
    "nTBTNextRoadWidth": 0,
    "szTBTMainText": "",
    "szNearDirName": "",
    "szFarDirName": "",
    "szTBTMainTextNext": "",
    "xTurnInfo": -1,
    "xDistToTurn": 0,
    "xTurnInfoNext": -1,
    "xDistToTurnNext": 0,
    "navType": "invalid",
    "navModifier": "",
    "navTypeNext": "invalid",
    "navModifierNext": "",
    "nGoPosDist": 0,
    "nGoPosTime": 0,
    "vpPosPointLatNavi": 0.0,
    "vpPosPointLonNavi": 0.0,
    "nPosAngle": 0.0,
    "nPosSpeed": 0.0,
    "szPosRoadName": "",
    "last_update_gps_time_navi": 0.0,
    "last_calculate_gps_time": 0.0,
  }
  for name, value in defaults.items():
    setattr(serv, name, value)
  return serv


def _message():
  return {
    "schemaVersion": 1,
    "generation": 7,
    "connected": True,
    "sessionId": "session",
    "speed": {
      "meta": _meta(1),
      "roadLimitValid": True,
      "roadLimitKph": 80,
      "sdiPresent": True,
      "sdiType": 1,
      "sdiDistanceM": 420,
      "sdiSpeedLimitKph": 60,
    },
    "guidanceCurrent": {"meta": _meta(2), "distanceM": 131, "turnType": 117},
    "guidanceNext": {"meta": _meta(3), "distanceM": 3339, "turnType": 104},
    "laneCurrent": {"meta": _meta(1, present=False)},
    "navigationStatus": {"meta": _meta(1, present=False)},
  }


def _bump_message(*, speed_sequence=1, lane_sequence=1, category=8, category_valid=True,
                  speed_received_s=100.0, lane_received_s=100.0):
  data = _message()
  data["speed"] = {
    "meta": _meta(speed_sequence, received_mono_ns=int(speed_received_s * 1e9)),
    "sdiPresent": True,
    "sdiType": 22,
    "sdiDistanceM": 100,
  }
  data["laneCurrent"] = {
    "meta": _meta(lane_sequence, received_mono_ns=int(lane_received_s * 1e9)),
    "roadCategoryValid": category_valid,
    "roadCategory": category,
  }
  data["navigationStatus"] = {
    "meta": _meta(speed_sequence, received_mono_ns=int(speed_received_s * 1e9)),
    "guidanceActive": True,
  }
  return data


def test_applies_new_navi_control_without_resetting_distance_on_heartbeat():
  serv = _serv()
  sm = _SubMaster(_message())

  assert serv._update_carrot_navi(sm)
  assert (serv.nRoadLimitSpeed, serv.xSpdType, serv.xSpdLimit, serv.xSpdDist) == (80, 1, 60, 420)
  assert (serv.xTurnInfo, serv.xDistToTurn) == (4, 131)
  assert (serv.xTurnInfoNext, serv.xDistToTurnNext) == (4, 3470)
  first_snapshot = serv.navigation_selection.snapshot
  assert first_snapshot is not None

  serv.xSpdDist = 400
  serv.xDistToTurn = 100
  serv.xDistToTurnNext = 3400
  sm.data["publishMonoTimeNanos"] = time.monotonic_ns()
  assert serv._update_carrot_navi(sm)
  assert (serv.xSpdDist, serv.xDistToTurn, serv.xDistToTurnNext) == (400, 100, 3400)
  second_snapshot = serv.navigation_selection.snapshot
  assert second_snapshot is not None
  assert (second_snapshot.sequence, second_snapshot.received_mono_s) == (
    first_snapshot.sequence, first_snapshot.received_mono_s,
  )


def test_primary_bump_uses_valid_category_from_same_snapshot():
  serv = _serv()
  serv.roadcate = 0

  assert serv._update_carrot_navi(_SubMaster(_bump_message()), now_s=100.0)
  assert (serv.roadcate, serv.xSpdType, serv.xSpdLimit, serv.xSpdDist) == (8, 22, 20, 100)


def test_lane_only_category_revision_reapplies_unchanged_fresh_bump():
  serv = _serv()
  data = _bump_message(category=0)
  sm = _SubMaster(data)

  assert serv._update_carrot_navi(sm, now_s=100.0)
  assert serv.xSpdType == -1

  data["laneCurrent"] = {
    "meta": _meta(2, received_mono_ns=100_100_000_000),
    "roadCategoryValid": True,
    "roadCategory": 8,
  }
  assert serv._update_carrot_navi(sm, now_s=100.1)
  assert (serv.xSpdType, serv.xSpdDist) == (22, 100)


@pytest.mark.parametrize(
  ("speed_kind", "expected_type", "raw_distance"),
  (("bump", 22, 100), ("camera", 1, 420), ("section", 4, 2346)),
)
def test_lane_only_revision_does_not_rewind_active_speed_distance(speed_kind, expected_type, raw_distance):
  serv = _serv()
  data = _bump_message(category=8)
  if speed_kind == "camera":
    data["speed"] = {
      "meta": _meta(1, received_mono_ns=100_000_000_000),
      "sdiPresent": True,
      "sdiType": 1,
      "sdiDistanceM": raw_distance,
      "sdiSpeedLimitKph": 60,
    }
  elif speed_kind == "section":
    data["speed"] = {
      "meta": _meta(1, received_mono_ns=100_000_000_000),
      "sectionPresent": True,
      "sectionActive": True,
      "sectionSpeedLimitKph": 80,
      "sectionRemainingDistanceM": 2345.6,
    }
  sm = _SubMaster(data)

  assert serv._update_carrot_navi(sm, now_s=100.0)
  assert (serv.xSpdType, serv.xSpdDist) == (expected_type, raw_distance)
  decremented_distance = raw_distance - 17
  serv.xSpdDist = decremented_distance

  data["laneCurrent"] = {
    "meta": _meta(2, received_mono_ns=101_000_000_000),
    "roadCategoryValid": True,
    "roadCategory": 9,
  }
  assert serv._update_carrot_navi(sm, now_s=101.0)
  assert (serv.xSpdType, serv.xSpdDist) == (expected_type, decremented_distance)


def test_missing_lane_receipt_revision_preserves_category_and_decremented_distance():
  serv = _serv()
  data = _bump_message(category=8)
  sm = _SubMaster(data)
  assert serv._update_carrot_navi(sm, now_s=100.0)
  serv.xSpdDist = 70

  data["laneCurrent"] = {
    "meta": _meta(2, received_mono_ns=109_000_000_000),
    "roadCategoryValid": False,
    "roadCategory": 0,
  }
  assert serv._update_carrot_navi(sm, now_s=109.0)
  assert (serv.roadcate, serv.xSpdType, serv.xSpdDist) == (8, 22, 70)

  revision = serv.carrot_navi_projection_revision
  serv.xSpdDist = 60
  data["laneCurrent"]["meta"] = _meta(2, received_mono_ns=109_500_000_000)
  assert serv._update_carrot_navi(sm, now_s=109.5)
  assert (serv.roadcate, serv.xSpdType, serv.xSpdDist) == (8, 22, 60)
  assert serv.carrot_navi_projection_revision > revision


def test_lane_only_explicit_zero_clears_active_bump():
  serv = _serv()
  data = _bump_message(category=8)
  sm = _SubMaster(data)
  assert serv._update_carrot_navi(sm, now_s=100.0)
  serv.xSpdDist = 70

  data["laneCurrent"] = {
    "meta": _meta(2, received_mono_ns=101_000_000_000),
    "roadCategoryValid": True,
    "roadCategory": 0,
  }
  assert serv._update_carrot_navi(sm, now_s=101.0)
  assert (serv.roadcate, serv.xSpdType, serv.xSpdDist) == (0, -1, 0)


def test_missing_lane_category_preserves_last_valid_category():
  serv = _serv()
  data = _bump_message(category=8)
  sm = _SubMaster(data)
  assert serv._update_carrot_navi(sm, now_s=100.0)

  data["laneCurrent"] = {
    "meta": _meta(2, received_mono_ns=100_100_000_000),
    "roadCategoryValid": False,
    "roadCategory": 0,
  }
  assert serv._update_carrot_navi(sm, now_s=100.1)
  assert serv.roadcate == 8
  assert serv.xSpdType == 22


def test_last_valid_lane_category_keeps_its_own_ttl_after_missing_item():
  serv = _serv()
  data = _bump_message(category=8, speed_received_s=109.0, lane_received_s=100.0)
  sm = _SubMaster(data)
  assert serv._update_carrot_navi(sm, now_s=109.0)

  data["laneCurrent"] = {
    "meta": _meta(2, received_mono_ns=109_000_000_000),
    "roadCategoryValid": False,
    "roadCategory": 0,
  }
  assert serv._update_carrot_navi(sm, now_s=109.1)
  assert serv._update_carrot_navi(sm, now_s=109.999)
  assert (serv.roadcate, serv.xSpdType) == (8, 22)
  revision = serv.carrot_navi_projection_revision

  sm.updated["carrotNavi"] = False
  assert serv._update_carrot_navi(sm, now_s=110.0)
  assert (serv.roadcate, serv.xSpdType, serv.xSpdDist) == (0, -1, 0)
  assert serv.carrot_navi_projection_revision > revision


@pytest.mark.parametrize("category", (0, 1))
def test_explicit_valid_highway_category_blocks_bump(category):
  serv = _serv()

  assert serv._update_carrot_navi(
    _SubMaster(_bump_message(category=category)), now_s=100.0,
  )
  assert serv.roadcate == category
  assert (serv.xSpdType, serv.xSpdLimit, serv.xSpdDist) == (-1, 0, 0)


def test_lane_category_is_fresh_before_but_stale_at_exact_ttl():
  assert V2_ITEM_TTL_S == CONTROL_V2_ITEM_TTL_S == 10.0
  serv = _serv()
  data = _bump_message(speed_received_s=105.0, lane_received_s=100.0)
  sm = _SubMaster(data)

  assert serv._update_carrot_navi(sm, now_s=109.999)
  assert serv.xSpdType == 22
  revision = serv.carrot_navi_projection_revision

  sm.updated["carrotNavi"] = False
  assert serv._update_carrot_navi(sm, now_s=110.0)
  assert serv.xSpdType == -1
  assert serv.carrot_navi_projection_revision > revision


def test_stale_speed_clears_projection_at_exact_ttl():
  serv = _serv()
  data = _bump_message(speed_received_s=100.0, lane_received_s=105.0)
  data["speed"]["roadLimitValid"] = True
  data["speed"]["roadLimitKph"] = 80
  sm = _SubMaster(data)

  assert serv._update_carrot_navi(sm, now_s=109.999)
  assert serv.xSpdType == 22
  assert serv.carrot_navi_road_limit_valid
  assert serv.nRoadLimitSpeed == 80
  revision = serv.carrot_navi_projection_revision

  sm.updated["carrotNavi"] = False
  assert not serv._update_carrot_navi(sm, now_s=110.0)
  assert (serv.nSdiType, serv.xSpdType, serv.xSpdLimit, serv.xSpdDist) == (-1, -1, 0, 0)
  assert not serv.carrot_navi_road_limit_valid
  assert serv.nRoadLimitSpeed == 0
  assert serv.carrot_navi_projection_revision > revision


def test_stale_guidance_clears_projection_at_exact_ttl():
  serv = _serv()
  data = _message()
  data["speed"] = {"meta": _meta(1, present=False, received_mono_ns=105_000_000_000)}
  data["guidanceCurrent"] = {
    "meta": _meta(2, received_mono_ns=100_000_000_000),
    "distanceM": 131,
    "turnType": 117,
  }
  sm = _SubMaster(data)

  assert serv._update_carrot_navi(sm, now_s=109.999)
  assert (serv.xTurnInfo, serv.xDistToTurn) == (4, 131)
  revision = serv.carrot_navi_projection_revision

  sm.updated["carrotNavi"] = False
  assert not serv._update_carrot_navi(sm, now_s=110.0)
  assert (serv.nTBTTurnType, serv.nTBTDist, serv.xTurnInfo, serv.xDistToTurn) == (-1, 0, -1, 0)
  assert serv.carrot_navi_projection_revision > revision


def test_stale_route_and_guidance_status_do_not_retain_control_authority():
  serv = _serv()
  serv.active_count = 17
  serv.active_sdi_count = 19
  data = _message()
  data["speed"]["meta"] = _meta(1, received_mono_ns=100_000_000_000)
  data["guidanceCurrent"]["meta"] = _meta(2, received_mono_ns=100_000_000_000)
  data["guidanceNext"]["meta"] = _meta(3, received_mono_ns=100_000_000_000)
  data["navigationStatus"] = {
    "meta": _meta(4, received_mono_ns=100_000_000_000),
    "guidanceActive": True,
  }
  data["route"] = {
    "meta": _meta(5, received_mono_ns=100_000_000_000),
    "remainingDistanceM": 12500,
    "remainingTimeSec": 1320,
  }

  assert not serv._update_carrot_navi(_SubMaster(data), now_s=110.0)
  assert not serv.carrot_navi_has_control
  assert (serv.active_count, serv.active_sdi_count) == (17, 19)
  assert (serv.nGoPosDist, serv.nGoPosTime) == (0, 0)


@pytest.mark.parametrize("received_mono_ns", (0, 101_000_000_000))
def test_zero_or_future_item_receipt_has_no_service_control_authority(received_mono_ns):
  serv = _serv()
  data = _bump_message(category=8)
  data["speed"]["meta"] = _meta(1, received_mono_ns=received_mono_ns)
  data["laneCurrent"]["meta"] = _meta(1, received_mono_ns=received_mono_ns)
  data["guidanceCurrent"]["meta"] = _meta(2, received_mono_ns=received_mono_ns)

  assert not serv._update_carrot_navi(_SubMaster(data), now_s=100.0)
  assert not serv.carrot_navi_has_control
  assert (serv.roadcate, serv.xSpdType, serv.xSpdDist) == (0, -1, 0)


def _accept_legacy_frames(serv, legacy_limit, receipt_times, session_id="legacy-fallback"):
  for receipt in receipt_times:
    serv.update({
      "_navigation_source": NavigationSource.TMAP_LEGACY.value,
      "_navigation_session_id": session_id,
      "_navigation_received_mono_s": receipt,
      "nRoadLimitSpeed": legacy_limit,
      "nSdiType": 1,
      "nSdiSpeedLimit": 40,
      "nSdiDist": 240,
      "nTBTTurnType": 12,
      "nTBTDist": 310,
      "roadcate": 6,
    })


@pytest.mark.parametrize("legacy_limit", (50, 80))
def test_non_owner_legacy_road_limit_projects_only_after_v2_exact_expiry(legacy_limit):
  serv = _serv()
  _accept_legacy_frames(serv, legacy_limit, [99.0 + index / 10 for index in range(7)])
  assert serv._project_navigation_selection(serv.navigation_sources.select(99.6))
  assert serv.nRoadLimitSpeed == legacy_limit

  data = _bump_message(speed_received_s=100.0, lane_received_s=100.0)
  data["speed"]["roadLimitValid"] = True
  data["speed"]["roadLimitKph"] = 80
  sm = _SubMaster(data)
  assert serv._update_carrot_navi(sm, now_s=100.0)
  assert serv.nRoadLimitSpeed == 80

  _accept_legacy_frames(serv, legacy_limit, (102.0, 105.0, 108.0))
  assert serv._update_carrot_navi(sm, now_s=108.0)
  assert serv.nRoadLimitSpeed == 80

  data["connected"] = False
  assert serv._update_carrot_navi(sm, now_s=109.999)
  assert serv.nRoadLimitSpeed == 80
  assert serv._update_carrot_navi(sm, now_s=110.0)
  assert serv.nRoadLimitSpeed == legacy_limit


def test_disconnect_keeps_7714_owner_until_exact_lease_expiry():
  serv = _serv()
  data = _bump_message(speed_received_s=100.0, lane_received_s=100.0)
  sm = _SubMaster(data)
  assert serv._update_carrot_navi(sm, now_s=100.0)
  expected = (serv.xSpdType, serv.xSpdDist)

  data["connected"] = False
  assert serv._update_carrot_navi(sm, now_s=109.999)
  assert (serv.xSpdType, serv.xSpdDist) == expected
  assert not serv._update_carrot_navi(sm, now_s=110.0)
  assert not serv.carrot_navi_active
  assert serv.active_count == 0
  assert serv.xSpdType == -1
  assert serv.xTurnInfo == -1


def test_active_section_uses_existing_section_speed_control():
  serv = _serv()
  data = _message()
  data["speed"] = {
    "meta": _meta(4),
    "roadLimitValid": True,
    "roadLimitKph": 100,
    "sectionPresent": True,
    "sectionActive": True,
    "sectionSpeedLimitKph": 80,
    "sectionRemainingDistanceM": 2345.6,
  }

  assert serv._update_carrot_navi(_SubMaster(data))
  assert serv.xSpdType == 4
  assert serv.xSpdLimit == 80
  assert serv.xSpdDist == 2346


def test_applies_7714_vehicle_route_traffic_and_secondary_sdi():
  serv = _serv()
  data = _message()
  received_mono_ns = time.monotonic_ns()
  data["guidanceCurrent"]["meta"] = _meta(2, received_mono_ns=received_mono_ns)
  data["guidanceNext"]["meta"] = _meta(3, received_mono_ns=received_mono_ns)
  data.update({
    "vehicle": {
      "meta": _meta(10, received_mono_ns=received_mono_ns),
      "latitude": 37.5,
      "longitude": 127.1,
      "headingDeg": 92.0,
      "speedKph": 42.5,
      "roadName": "Test road",
    },
    "speed": {
      "meta": _meta(11, received_mono_ns=received_mono_ns),
      "roadLimitValid": True,
      "roadLimitKph": 50,
      "sdiPresent": True,
      "sdiType": 1,
      "sdiDistanceM": 420,
      "sdiSpeedLimitKph": 50,
      "sdiBlockType": 2,
      "sdiBlockSpeedKph": 40,
      "sdiBlockDistanceM": 390,
      "secondarySdiPresent": True,
      "secondarySdiType": 22,
      "secondarySdiDistanceM": 93,
    },
    "route": {
      "meta": _meta(12, received_mono_ns=received_mono_ns),
      "remainingDistanceM": 12500,
      "remainingTimeSec": 1320,
      "polyline": [{"latitude": 37.5, "longitude": 127.1}],
    },
    "trafficSignal": {
      "meta": _meta(13, received_mono_ns=received_mono_ns),
      "visible": True,
      "distanceM": 145,
      "source": "ssinf",
      "redValid": True,
      "redOn": True,
      "redRemainSec": 18,
    },
    "navigationStatus": {
      "meta": _meta(14, received_mono_ns=received_mono_ns),
      "guidanceActive": True,
    },
  })

  sm = _SubMaster(data)
  assert serv._update_carrot_navi(sm)
  assert (serv.vpPosPointLatNavi, serv.vpPosPointLonNavi, serv.nPosAngle) == (37.5, 127.1, 92.0)
  assert serv.szPosRoadName == "Test road"
  assert serv.last_update_gps_time_navi > 0
  assert (serv.nGoPosDist, serv.nGoPosTime) == (12500, 1320)
  assert (serv.nSdiBlockType, serv.nSdiBlockSpeed, serv.nSdiBlockDist) == (2, 40, 390)
  assert (serv.nSdiPlusType, serv.nSdiPlusDist) == (22, 93)
  assert serv.params_memory.writes[-1][0] == "TrafficLight"
  assert '"lamp": "red"' in serv.params_memory.writes[-1][1]

  first_gps_update = serv.last_update_gps_time_navi
  assert serv._update_carrot_navi(sm)
  assert serv.last_update_gps_time_navi >= first_gps_update
  assert len(serv.params_memory.writes) == 1

  data["connected"] = False
  assert serv._update_carrot_navi(sm, now_s=first_gps_update + 9.999)
  assert serv.last_update_gps_time_navi == first_gps_update
  assert not serv._update_carrot_navi(sm, now_s=first_gps_update + 10.0)
  assert serv.last_update_gps_time_navi == 0
  assert serv.szPosRoadName == ""
  assert (serv.nGoPosDist, serv.nGoPosTime) == (0, 0)
  assert serv.params_memory.removed[-1] == "TrafficLight"


def test_legacy_7713_navigation_update_path_remains_operational():
  serv = _serv()
  legacy = {
    "nRoadLimitSpeed": 50,
    "nSdiType": 1,
    "nSdiSpeedLimit": 40,
    "nSdiSection": 0,
    "nSdiDist": 240,
    "nSdiBlockType": 2,
    "nSdiBlockSpeed": 40,
    "nSdiBlockDist": 210,
    "nSdiPlusType": 22,
    "nSdiPlusSpeedLimit": 20,
    "nSdiPlusDist": 80,
    "nTBTDist": 310,
    "nTBTTurnType": 12,
    "szTBTMainText": "Legacy left",
    "szNearDirName": "Legacy near",
    "szFarDirName": "Legacy far",
    "nTBTDistNext": 940,
    "nTBTTurnTypeNext": 13,
    "nGoPosDist": 8700,
    "nGoPosTime": 720,
    "szPosRoadName": "Legacy road",
    "vpPosPointLat": 37.4,
    "vpPosPointLon": 126.9,
    "nPosAngle": 183.0,
    "nPosSpeed": 31.0,
    "roadcate": 6,
  }
  for _ in range(7):
    serv.update(legacy)

  selection = serv.navigation_sources.select(time.monotonic())
  assert serv._project_navigation_selection(selection)

  assert serv.nRoadLimitSpeed == 50
  assert (serv.nSdiType, serv.nSdiDist, serv.nSdiPlusType) == (1, 240, 22)
  assert (serv.nSdiBlockType, serv.nSdiBlockSpeed, serv.nSdiBlockDist) == (2, 40, 210)
  assert (serv.nTBTDist, serv.nTBTTurnType, serv.nTBTDistNext) == (310, 12, 940)
  assert (serv.szTBTMainText, serv.szNearDirName, serv.szFarDirName) == (
    "Legacy left", "Legacy near", "Legacy far",
  )
  assert (serv.roadcate, serv.xSpdType, serv.xSpdLimit, serv.xSpdDist) == (6, 4, 40, 210)
  assert (serv.nGoPosDist, serv.nGoPosTime) == (8700, 720)
  assert (serv.szPosRoadName, serv.vpPosPointLatNavi, serv.vpPosPointLonNavi) == ("Legacy road", 37.4, 126.9)


@pytest.mark.parametrize(
  ("is_metric", "report_id", "road_limit", "alert_distance", "expected_type", "expected_limit_kph", "expected_distance_m"),
  (
    (True, "camera", 50, "300 m", 101, 52.5, 300),
    (False, "police", 25, "1000 ft", 100, 25 * CV.MPH_TO_KPH * 1.05, 304),
  ),
)
def test_waze_alert_uses_navi_speed_limit_ratio(is_metric, report_id, road_limit, alert_distance,
                                                expected_type, expected_limit_kph, expected_distance_m):
  serv = _serv()
  serv.is_metric = is_metric
  serv.autoNaviSpeedSafetyFactor = 1.05

  serv.update_kisa({
    "kisawazeroadspdlimit": road_limit,
    "kisawazereportid": report_id,
    "kisawazealertdist": alert_distance,
  })

  assert serv.kisa_safety_type == expected_type
  assert serv.kisa_safety_limit == pytest.approx(expected_limit_kph)
  assert serv.kisa_safety_distance == expected_distance_m
  assert (serv.xSpdType, serv.xSpdLimit, serv.xSpdDist) == (-1, 0, 0)


def test_waze_alert_without_road_limit_has_no_speed_target():
  serv = _serv()
  serv.nRoadLimitSpeed = 0
  serv.autoNaviSpeedSafetyFactor = 1.05

  serv.update_kisa({
    "kisawazereportid": "camera",
    "kisawazealertdist": "200 m",
  })

  assert (serv.kisa_safety_type, serv.kisa_safety_limit, serv.kisa_safety_distance) == (101, 0, 200)
  assert (serv.xSpdType, serv.xSpdLimit, serv.xSpdDist) == (-1, 0, 0)


def _source_control(*, receipt, turn_type, safety_type, safety_distance,
                    secondary_type, road_limit, category, trip_distance,
                    off_route=False):
  return NavigationControlState(
    current=NavigationInstruction(
      present=True,
      turn_type=turn_type,
      distance_m=310.0,
      main_text=f"turn-{turn_type}",
      road_name=f"road-{turn_type}",
      received_mono_s=receipt,
    ),
    next=NavigationInstruction(
      present=True,
      turn_type=turn_type + 1,
      distance_m=910.0,
      main_text=f"next-{turn_type}",
      received_mono_s=receipt,
    ),
    safety=SafetyItem(safety_type, safety_distance, 50.0, receipt),
    secondary_safety=SafetyItem(secondary_type, 80.0, 20.0, receipt),
    road_limit_kph=road_limit,
    road_limit_received_mono_s=receipt,
    road_category=category,
    road_category_received_mono_s=receipt,
    route_present=True,
    route_received_mono_s=receipt,
    remaining_distance_m=trip_distance,
    remaining_time_s=720.0,
    off_route=off_route,
    route_points=((37.0 + turn_type / 1000.0, 127.1),),
  )


def _source_snapshot(source, session_id, sequence, receipt, control,
                     lifecycle=NavigationLifecycle.GUIDING):
  return NavigationSnapshot(
    source=source,
    session_id=session_id,
    sequence=sequence,
    lifecycle=lifecycle,
    received_mono_s=receipt,
    activation_epoch=0,
    control=control,
  )


def _projected_tuple(serv):
  return (
    serv.nTBTTurnType,
    serv.nSdiType,
    serv.nSdiPlusType,
    serv.roadcate,
    serv.nRoadLimitSpeed,
    serv.nGoPosDist,
    serv.carrot_navi_off_route,
  )


def test_accept_is_store_only_and_non_owner_cannot_mix_selected_fields():
  serv = _serv()
  naver = _source_snapshot(
    NavigationSource.NAVER_V1, "naver-background", 1, 1.0,
    _source_control(
      receipt=1.0, turn_type=13, safety_type=7, safety_distance=700.0,
      secondary_type=22, road_limit=40.0, category=8, trip_distance=2_222.0,
    ),
  )
  tmap = _source_snapshot(
    NavigationSource.TMAP_LEGACY, "tmap-owner", 1, 2.0,
    _source_control(
      receipt=2.0, turn_type=12, safety_type=1, safety_distance=240.0,
      secondary_type=22, road_limit=50.0, category=6, trip_distance=1_111.0,
    ),
  )

  assert serv.accept_navigation_snapshot(naver)
  assert serv.accept_navigation_snapshot(tmap)
  assert _projected_tuple(serv) == (-1, -1, -1, 8, 30, 0, False)
  assert serv._project_navigation_selection(serv.navigation_sources.select(2.0))
  complete_tmap = (12, 1, 22, 6, 50.0, 1_111.0, False)
  assert _projected_tuple(serv) == complete_tmap

  assert serv.accept_navigation_snapshot(_source_snapshot(
    NavigationSource.NAVER_V1, "naver-background", 2, 2.1,
    _source_control(
      receipt=2.1, turn_type=19, safety_type=4, safety_distance=600.0,
      secondary_type=7, road_limit=30.0, category=9, trip_distance=3_333.0,
    ),
  ))
  assert not serv._project_navigation_selection(serv.navigation_sources.select(2.1))
  assert _projected_tuple(serv) == complete_tmap
  assert serv.navigation_selection.snapshot.control.route_points == ((37.012, 127.1),)


def test_new_activation_atomically_replaces_complete_projection_without_empty_state():
  serv = _serv()
  old = _source_snapshot(
    NavigationSource.TMAP_LEGACY, "old", 1, 1.0,
    _source_control(
      receipt=1.0, turn_type=12, safety_type=1, safety_distance=200.0,
      secondary_type=22, road_limit=50.0, category=6, trip_distance=1_000.0,
    ),
  )
  new = _source_snapshot(
    NavigationSource.NAVER_V1, "new", 1, 1.1,
    _source_control(
      receipt=1.1, turn_type=13, safety_type=7, safety_distance=300.0,
      secondary_type=4, road_limit=70.0, category=8, trip_distance=2_000.0,
      off_route=True,
    ),
  )
  publications = []
  assert serv.accept_navigation_snapshot(old)
  assert serv._project_navigation_selection(serv.navigation_sources.select(1.0))
  publications.append(_projected_tuple(serv))
  assert serv.accept_navigation_snapshot(new)
  assert serv._project_navigation_selection(serv.navigation_sources.select(1.1))
  publications.append(_projected_tuple(serv))

  assert publications == [
    (12, 1, 22, 6, 50.0, 1_000.0, False),
    (13, 7, 4, 8, 70.0, 2_000.0, True),
  ]
  assert serv.carrot_navi_has_control
  assert serv.navigation_selection.snapshot.control.route_points == ((37.013, 127.1),)


def test_terminal_owner_projects_fresh_fallback_in_the_same_call():
  serv = _serv()
  fallback = _source_snapshot(
    NavigationSource.NAVER_V1, "fallback", 1, 1.0,
    _source_control(
      receipt=1.0, turn_type=13, safety_type=7, safety_distance=400.0,
      secondary_type=22, road_limit=40.0, category=8, trip_distance=2_000.0,
    ),
  )
  owner = _source_snapshot(
    NavigationSource.TMAP_LEGACY, "owner", 1, 1.1,
    _source_control(
      receipt=1.1, turn_type=12, safety_type=1, safety_distance=200.0,
      secondary_type=4, road_limit=50.0, category=6, trip_distance=1_000.0,
    ),
  )
  assert serv.accept_navigation_snapshot(fallback)
  assert serv.accept_navigation_snapshot(owner)
  assert serv._project_navigation_selection(serv.navigation_sources.select(1.1))

  terminal = _source_snapshot(
    NavigationSource.TMAP_LEGACY, "owner", 2, 1.2,
    NavigationControlState(), lifecycle=NavigationLifecycle.ARRIVED,
  )
  assert serv.accept_navigation_snapshot(terminal)
  assert serv._project_navigation_selection(serv.navigation_sources.select(1.2))
  assert _projected_tuple(serv) == (13, 7, 22, 8, 40.0, 2_000.0, False)


def test_transport_loss_keeps_owner_until_exact_lease_boundary():
  serv = _serv()
  owner = _source_snapshot(
    NavigationSource.NAVER_V1, "naver-owner", 1, 10.0,
    _source_control(
      receipt=10.0, turn_type=13, safety_type=1, safety_distance=200.0,
      secondary_type=22, road_limit=50.0, category=8, trip_distance=1_000.0,
    ),
  )
  assert serv.accept_navigation_snapshot(owner)
  assert serv._project_navigation_selection(serv.navigation_sources.select(10.0))
  expected = _projected_tuple(serv)
  assert serv.navigation_sources.record_transport_loss(
    NavigationSource.NAVER_V1, "naver-owner", 10.1,
  )

  assert not serv._project_navigation_selection(serv.navigation_sources.select(11.999))
  assert _projected_tuple(serv) == expected
  assert serv._project_navigation_selection(serv.navigation_sources.select(12.0))
  assert _projected_tuple(serv) == (-1, -1, -1, 0, 0, 0, False)


def test_integrated_terminal_tombstone_requires_a_new_session_id():
  serv = _serv()
  control = _source_control(
    receipt=1.0, turn_type=13, safety_type=1, safety_distance=200.0,
    secondary_type=22, road_limit=50.0, category=8, trip_distance=1_000.0,
  )
  assert serv.accept_navigation_snapshot(_source_snapshot(
    NavigationSource.NAVER_V1, "terminal", 1, 1.0, control,
  ))
  assert serv.accept_navigation_snapshot(_source_snapshot(
    NavigationSource.NAVER_V1, "terminal", 2, 1.1, NavigationControlState(),
    lifecycle=NavigationLifecycle.STOPPED,
  ))
  assert not serv.accept_navigation_snapshot(_source_snapshot(
    NavigationSource.NAVER_V1, "terminal", 999, 1.2, control,
  ))
  assert serv.accept_navigation_snapshot(_source_snapshot(
    NavigationSource.NAVER_V1, "replacement", 1, 1.3, control,
  ))


def test_legacy_adapter_round_trips_section_blocks_guidance_route_and_traffic():
  serv = _serv()
  legacy = {
    "_navigation_source": NavigationSource.TMAP_LEGACY.value,
    "_navigation_session_id": "legacy-round-trip",
    "_navigation_received_mono_s": 10.0,
    "nRoadLimitSpeed": 50,
    "nSdiType": 4,
    "nSdiSpeedLimit": 40,
    "nSdiSection": 3,
    "nSdiDist": 240,
    "nSdiBlockType": 2,
    "nSdiBlockSpeed": 40,
    "nSdiBlockDist": 210,
    "nSdiPlusType": 22,
    "nSdiPlusSection": 5,
    "nSdiPlusBlockType": 6,
    "nSdiPlusBlockSpeed": 20,
    "nSdiPlusBlockDist": 70,
    "nSdiPlusDist": 80,
    "nTBTTurnType": 12,
    "nTBTDist": 310,
    "nTBTTurnTypeNext": 13,
    "nTBTDistNext": 940,
    "szTBTMainText": "left",
    "szNearDirName": "near",
    "szFarDirName": "far",
    "nTBTNextRoadWidth": 7,
    "nGoPosDist": 8_700,
    "nGoPosTime": 720,
    "roadcate": 6,
  }
  for sequence in range(7):
    legacy["_navigation_received_mono_s"] = 10.0 + sequence / 10.0
    serv.update(legacy)
  assert serv.update_legacy_navigation_aux(
    "route", {"route_points": ((37.5, 127.1),)},
    NavigationSource.TMAP_LEGACY, "legacy-round-trip", 10.7,
  )
  assert serv.update_legacy_navigation_aux(
    "traffic", {
      "traffic_present": True, "traffic_visible": True,
      "traffic_distance_m": 120, "traffic_source": "sinf",
      "traffic_lamp": "red", "traffic_remain_s": 15,
    },
    NavigationSource.TMAP_LEGACY, "legacy-round-trip", 10.8,
  )
  assert serv._project_navigation_selection(serv.navigation_sources.select(10.8))

  selected = serv.navigation_selection.snapshot
  assert selected is not None
  assert selected.control.safety is not None
  assert selected.control.secondary_safety is not None
  assert selected.control.safety.section_type == 3
  assert selected.control.secondary_safety.section_type == 5
  assert (serv.nSdiSection, serv.nSdiBlockType, serv.nSdiBlockDist) == (3, 2, 210)
  assert (serv.nSdiPlusBlockType, serv.nSdiPlusBlockDist) == (6, 70)
  assert (serv.nTBTTurnType, serv.nTBTTurnTypeNext, serv.nTBTNextRoadWidth) == (12, 13, 7)
  assert (serv.nGoPosDist, selected.control.route_points) == (8_700, ((37.5, 127.1),))
  assert serv.params_memory.writes[-1][0] == "TrafficLight"


def test_legacy_aux_before_base_is_pending_and_does_not_activate():
  serv = _serv()
  assert not serv.update_legacy_navigation_aux(
    "route", {"route_points": ((37.5, 127.1),)},
    NavigationSource.TMAP_LEGACY, "pending", 1.0,
  )
  serv.update({
    "_navigation_source": NavigationSource.TMAP_LEGACY.value,
    "_navigation_session_id": "pending",
    "_navigation_received_mono_s": 1.1,
    "goalPosX": 127.2,
    "goalPosY": 37.6,
  })
  assert serv.navigation_sources.select(1.1).snapshot is None
  assert (serv.goalPosX, serv.goalPosY) == (0.0, 0.0)

  serv.update({
    "_navigation_source": NavigationSource.TMAP_LEGACY.value,
    "_navigation_session_id": "pending",
    "_navigation_received_mono_s": 1.2,
    "nRoadLimitSpeed": 50,
    "nSdiType": -1,
    "nTBTTurnType": 12,
    "nTBTDist": 300,
    "roadcate": 6,
  })
  assert serv._project_navigation_selection(serv.navigation_sources.select(1.2))
  selected = serv.navigation_selection.snapshot
  assert selected is not None
  assert selected.control.route_points == ((37.5, 127.1),)
  assert selected.control.destination == (37.6, 127.2)
  assert (serv.goalPosX, serv.goalPosY) == (127.2, 37.6)


def test_legacy_new_base_destination_replaces_previous_session_destination():
  serv = _serv()
  for receipt, latitude, longitude in (
    (1.0, 37.5, 127.1),
    (1.1, 37.7, 127.3),
  ):
    serv.update({
      "_navigation_source": NavigationSource.TMAP_LEGACY.value,
      "_navigation_session_id": "destination-update",
      "_navigation_received_mono_s": receipt,
      "nRoadLimitSpeed": 50,
      "nSdiType": -1,
      "nTBTTurnType": 12,
      "nTBTDist": 300,
      "goalPosX": longitude,
      "goalPosY": latitude,
      "roadcate": 6,
    })

  assert serv._project_navigation_selection(serv.navigation_sources.select(1.1))
  selected = serv.navigation_selection.snapshot
  assert selected is not None
  assert selected.control.destination == (37.7, 127.3)
  assert selected.control.destination_received_mono_s == 1.1
  assert (serv.goalPosX, serv.goalPosY) == (127.3, 37.7)


def test_non_owner_legacy_aux_waits_for_owner_fallback():
  serv = _serv()
  serv.update({
    "_navigation_source": NavigationSource.TMAP_LEGACY.value,
    "_navigation_session_id": "legacy",
    "_navigation_received_mono_s": 1.0,
    "nRoadLimitSpeed": 50,
    "nSdiType": 1,
    "nSdiSpeedLimit": 40,
    "nSdiDist": 200,
    "nTBTTurnType": 12,
    "nTBTDist": 300,
    "roadcate": 6,
  })
  assert serv._project_navigation_selection(serv.navigation_sources.select(1.0))
  naver = _source_snapshot(
    NavigationSource.NAVER_V1, "naver", 1, 2.0,
    _source_control(
      receipt=2.0, turn_type=13, safety_type=7, safety_distance=500.0,
      secondary_type=22, road_limit=70.0, category=8, trip_distance=2_000.0,
    ),
  )
  assert serv.accept_navigation_snapshot(naver)
  assert serv._project_navigation_selection(serv.navigation_sources.select(2.0))
  naver_state = _projected_tuple(serv)

  assert serv.update_legacy_navigation_aux(
    "route", {"route_points": ((37.7, 127.3),)},
    NavigationSource.TMAP_LEGACY, "legacy", 2.1,
  )
  assert not serv._project_navigation_selection(serv.navigation_sources.select(2.1))
  assert _projected_tuple(serv) == naver_state

  assert serv.accept_navigation_snapshot(_source_snapshot(
    NavigationSource.NAVER_V1, "naver", 2, 2.2, NavigationControlState(),
    lifecycle=NavigationLifecycle.ARRIVED,
  ))
  assert serv._project_navigation_selection(serv.navigation_sources.select(2.2))
  assert serv.navigation_selection.snapshot.control.route_points == ((37.7, 127.3),)


def test_old_navigation_selection_cannot_roll_back_newer_projection():
  serv = _serv()
  first = _source_snapshot(
    NavigationSource.TMAP_LEGACY, "first", 1, 1.0,
    _source_control(
      receipt=1.0, turn_type=12, safety_type=1, safety_distance=200.0,
      secondary_type=22, road_limit=50.0, category=6, trip_distance=1_000.0,
    ),
  )
  second = _source_snapshot(
    NavigationSource.NAVER_V1, "second", 1, 1.1,
    _source_control(
      receipt=1.1, turn_type=13, safety_type=7, safety_distance=300.0,
      secondary_type=4, road_limit=70.0, category=8, trip_distance=2_000.0,
    ),
  )
  assert serv.accept_navigation_snapshot(first)
  old_selection = serv.navigation_sources.select(1.0)
  assert serv._project_navigation_selection(old_selection)
  assert serv.accept_navigation_snapshot(second)
  new_selection = serv.navigation_sources.select(1.1)
  assert serv._project_navigation_selection(new_selection)
  new_state = _projected_tuple(serv)

  assert not serv._project_navigation_selection(old_selection)
  assert _projected_tuple(serv) == new_state


def test_v2_vehicle_and_traffic_only_cannot_preempt_tmap_owner():
  serv = _serv()
  tmap = _source_snapshot(
    NavigationSource.TMAP_LEGACY, "tmap", 1, 100.0,
    _source_control(
      receipt=100.0, turn_type=12, safety_type=1, safety_distance=200.0,
      secondary_type=22, road_limit=50.0, category=6, trip_distance=1_000.0,
    ),
  )
  assert serv.accept_navigation_snapshot(tmap)
  assert serv._project_navigation_selection(serv.navigation_sources.select(100.0))
  expected = _projected_tuple(serv)

  data = _message()
  for name in ("speed", "guidanceCurrent", "guidanceNext", "laneCurrent", "navigationStatus"):
    data[name] = {"meta": _meta(1, present=False, received_mono_ns=100_000_000_000)}
  data["vehicle"] = {
    "meta": _meta(2, received_mono_ns=100_000_000_000),
    "latitude": 37.5, "longitude": 127.1,
  }
  data["trafficSignal"] = {
    "meta": _meta(3, received_mono_ns=100_000_000_000),
    "visible": True, "redValid": True, "redOn": True, "redRemainSec": 10,
  }
  assert serv._update_carrot_navi(_SubMaster(data), now_s=100.0)
  assert _projected_tuple(serv) == expected
  assert serv.navigation_selection.snapshot.source is NavigationSource.TMAP_LEGACY


def test_v2_vehicle_change_does_not_extend_expired_guidance_ownership():
  serv = _serv()
  tmap_control = _source_control(
    receipt=99.0, turn_type=12, safety_type=1, safety_distance=200.0,
    secondary_type=22, road_limit=50.0, category=6, trip_distance=1_000.0,
  )
  assert serv.accept_navigation_snapshot(_source_snapshot(
    NavigationSource.TMAP_LEGACY, "fallback", 1, 99.0, tmap_control,
  ))
  assert serv._project_navigation_selection(serv.navigation_sources.select(99.0))

  data = _message()
  data["speed"] = {"meta": _meta(1, present=False, received_mono_ns=100_000_000_000)}
  data["guidanceCurrent"] = {
    "meta": _meta(2, received_mono_ns=100_000_000_000),
    "distanceM": 300, "turnType": 13,
  }
  data["guidanceNext"] = {"meta": _meta(3, present=False, received_mono_ns=100_000_000_000)}
  data["navigationStatus"] = {"meta": _meta(4, present=False, received_mono_ns=100_000_000_000)}
  data["vehicle"] = {
    "meta": _meta(5, received_mono_ns=100_000_000_000),
    "latitude": 37.5, "longitude": 127.1,
  }
  sm = _SubMaster(data)
  assert serv._update_carrot_navi(sm, now_s=100.0)
  assert serv.navigation_selection.snapshot.source is NavigationSource.CARROT_NAVI_V2

  for sequence, receipt in enumerate((102.0, 105.0, 108.0), start=2):
    refreshed_tmap_control = _source_control(
      receipt=receipt, turn_type=12, safety_type=1, safety_distance=200.0,
      secondary_type=22, road_limit=50.0, category=6, trip_distance=1_000.0,
    )
    assert serv.accept_navigation_snapshot(_source_snapshot(
      NavigationSource.TMAP_LEGACY, "fallback", sequence, receipt, refreshed_tmap_control,
    ))
  data["generation"] = 8
  data["vehicle"]["meta"] = _meta(6, received_mono_ns=109_999_000_000)
  assert serv._update_carrot_navi(sm, now_s=109.999)
  assert serv.navigation_selection.snapshot.source is NavigationSource.CARROT_NAVI_V2

  data["generation"] = 9
  data["vehicle"]["meta"] = _meta(7, received_mono_ns=110_000_000_000)
  assert serv._update_carrot_navi(sm, now_s=110.0)
  assert serv.navigation_selection.snapshot.source is NavigationSource.TMAP_LEGACY


def test_legacy_update_normalizes_into_bound_store_and_keeps_non_navigation_side_effects():
  serv = _serv()
  legacy = {
    "_navigation_source": NavigationSource.TMAP_LEGACY.value,
    "_navigation_session_id": "bound-session",
    "_navigation_received_mono_s": 10.0,
    "provider": NavigationSource.NAVER_V1.value,
    "nRoadLimitSpeed": 50,
    "nSdiType": 1,
    "nSdiSpeedLimit": 40,
    "nSdiDist": 240,
    "nSdiPlusType": 22,
    "nSdiPlusSpeedLimit": 20,
    "nSdiPlusDist": 80,
    "nTBTTurnType": 12,
    "nTBTDist": 310,
    "nGoPosDist": 8_700,
    "nGoPosTime": 720,
    "roadcate": 6,
    "carrotCmd": "echo",
    "carrotArg": "ok",
    "latitude": 37.4,
    "longitude": 126.9,
    "accuracy": 3.0,
  }
  for sequence in range(7):
    legacy["_navigation_received_mono_s"] = 10.0 + sequence * 0.1
    serv.update(legacy)

  assert serv.carrotCmd == "echo"
  assert serv.phone_latitude == 37.4
  assert serv.phone_longitude == 126.9
  assert _projected_tuple(serv) == (-1, -1, -1, 8, 30, 0, False)
  selection = serv.navigation_sources.select(10.6)
  assert selection.snapshot is not None
  assert selection.snapshot.source is NavigationSource.TMAP_LEGACY
  assert selection.snapshot.session_id == "bound-session"
  assert serv._project_navigation_selection(selection)
  assert _projected_tuple(serv) == (12, 1, 22, 6, 50.0, 8_700.0, False)


def test_concurrent_accept_select_and_projection_exposes_only_complete_generations():
  serv = _serv()
  complete_states = {
    (12, 1, 22, 6, 50.0, 1_000.0, False),
    (13, 7, 4, 8, 70.0, 2_000.0, True),
  }
  observed = []
  observed_lock = threading.Lock()
  barrier = threading.Barrier(3)

  def worker(source, session, turn, safety, secondary, limit, category, trip, off_route):
    barrier.wait()
    for sequence in range(1, 101):
      now = 100.0 + sequence / 1000.0
      snapshot = _source_snapshot(
        source, session, sequence, now,
        _source_control(
          receipt=now, turn_type=turn, safety_type=safety, safety_distance=200.0,
          secondary_type=secondary, road_limit=limit, category=category,
          trip_distance=trip, off_route=off_route,
        ),
      )
      serv.accept_navigation_snapshot(snapshot)
      serv._project_navigation_selection(serv.navigation_sources.select(now))
      with observed_lock:
        observed.append(_projected_tuple(serv))

  threads = (
    threading.Thread(target=worker, args=(
      NavigationSource.TMAP_LEGACY, "tmap", 12, 1, 22, 50.0, 6, 1_000.0, False,
    )),
    threading.Thread(target=worker, args=(
      NavigationSource.NAVER_V1, "naver", 13, 7, 4, 70.0, 8, 2_000.0, True,
    )),
  )
  for thread in threads:
    thread.start()
  barrier.wait()
  for thread in threads:
    thread.join()

  assert observed
  assert set(observed) <= complete_states
