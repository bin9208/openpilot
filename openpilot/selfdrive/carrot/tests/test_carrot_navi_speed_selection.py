import sys
import types
from dataclasses import replace

import pytest

try:
  import openpilot.common.params_pyx  # noqa: F401
  _NEEDS_HOST_STUBS = False
except ImportError:
  _NEEDS_HOST_STUBS = True


if sys.platform == "win32" or _NEEDS_HOST_STUBS:
  fcntl = types.ModuleType("fcntl")
  fcntl.LOCK_EX = fcntl.LOCK_UN = 0
  fcntl.flock = lambda *_args: None
  sys.modules.setdefault("fcntl", fcntl)

  messaging = types.ModuleType("openpilot.cereal.messaging")
  messaging.new_message = lambda *_args: None
  sys.modules.setdefault("openpilot.cereal.messaging", messaging)

  realtime = types.ModuleType("openpilot.common.realtime")
  realtime.Ratekeeper = object
  sys.modules.setdefault("openpilot.common.realtime", realtime)

  params = types.ModuleType("openpilot.common.params")
  params.Params = object
  sys.modules.setdefault("openpilot.common.params", params)

  filter_simple = types.ModuleType("openpilot.common.filter_simple")
  filter_simple.MyMovingAverage = object
  sys.modules.setdefault("openpilot.common.filter_simple", filter_simple)

  hardware = types.ModuleType("openpilot.system.hardware")
  hardware.PC = hardware.TICI = False
  sys.modules.setdefault("openpilot.system.hardware", hardware)

  helpers = types.ModuleType("openpilot.selfdrive.navd.helpers")
  helpers.Coordinate = object
  sys.modules.setdefault("openpilot.selfdrive.navd.helpers", helpers)

  gps = types.ModuleType("openpilot.common.gps")
  gps.get_gps_location_service = lambda: "gpsLocationExternal"
  sys.modules.setdefault("openpilot.common.gps", gps)

  navi_control = types.ModuleType("openpilot.selfdrive.carrot.carrot_navi_control")
  navi_control.V2_ITEM_TTL_S = 10.0
  navi_control.CarrotNaviControl = object
  navi_control.parse_carrot_navi_control = lambda _raw: None
  sys.modules.setdefault("openpilot.selfdrive.carrot.carrot_navi_control", navi_control)

from openpilot.selfdrive.carrot.navigation_sources import (
  NavigationControlState,
  NavigationLifecycle,
  NavigationSelection,
  NavigationSnapshot,
  NavigationSource,
  NavigationSourceStore,
  SafetyItem,
  choose_safety,
  safety_rejection,
)
from openpilot.selfdrive.carrot.carrot_serv import CarrotServ
import openpilot.selfdrive.carrot.carrot_serv as carrot_serv_module


def _selection(*, safety=None, secondary_safety=None, source=NavigationSource.TMAP_LEGACY,
               road_category=8, off_route=False, safety_age_s=None):
  return NavigationSelection(
    snapshot=NavigationSnapshot(
      source=source,
      session_id="test",
      sequence=1,
      lifecycle=NavigationLifecycle.GUIDING,
      received_mono_s=100.0,
      activation_epoch=1,
      control=NavigationControlState(
        safety=safety,
        secondary_safety=secondary_safety,
        road_category=road_category,
        off_route=off_route,
      ),
    ),
    reason="owner_sticky",
    owner_age_s=0.0,
    safety_age_s=0.0 if safety is not None and safety_age_s is None else safety_age_s,
    road_category_age_s=None,
    projection_revision=1,
  )


def _no_owner_selection():
  return NavigationSelection(
    snapshot=None,
    reason="no_owner",
    owner_age_s=None,
    safety_age_s=None,
    road_category_age_s=None,
    projection_revision=1,
  )


def test_off_route_selected_navigation_safety_falls_back_to_hda():
  decision = choose_safety(
    _selection(safety=SafetyItem(1, 300.0, 60.0, 100.0), off_route=True),
    40.0,
    100.0,
  )

  assert (decision.provider, decision.reason, decision.rejection) == ("hda", "hda", "off_route")


def test_selected_tmap_camera_is_authoritative_over_lower_hda():
  decision = choose_safety(_selection(safety=SafetyItem(1, 300.0, 60.0, 100.0)), 40.0, 100.0)

  assert (decision.provider, decision.reason, decision.limit_kph) == ("tmap_legacy", "cam", 60.0)


def test_selected_naver_camera_is_authoritative_over_lower_hda():
  decision = choose_safety(
    _selection(
      source=NavigationSource.NAVER_V1,
      safety=SafetyItem(1, 300.0, 60.0, 100.0),
    ),
    40.0,
    100.0,
  )

  assert (decision.provider, decision.reason) == ("naver_v1", "cam")


def test_primary_camera_wins_over_secondary_bump():
  decision = choose_safety(
    _selection(
      safety=SafetyItem(1, 300.0, 60.0, 100.0),
      secondary_safety=SafetyItem(22, 80.0, 0.0, 100.0),
    ),
    40.0,
    100.0,
    mode=3,
    bump_speed_kph=20.0,
  )

  assert (decision.reason, decision.type, decision.limit_kph) == ("cam", 1, 60.0)


def test_bump_with_zero_payload_speed_uses_configured_bump_speed():
  decision = choose_safety(
    _selection(safety=SafetyItem(22, 80.0, 0.0, 100.0)),
    40.0,
    100.0,
    mode=2,
    bump_speed_kph=20.0,
  )

  assert (decision.reason, decision.type, decision.limit_kph, decision.distance_m) == ("bump", 22, 20.0, 80.0)


def test_exact_naver_bump_without_road_category_uses_configured_bump_speed():
  decision = choose_safety(
    _selection(
      source=NavigationSource.NAVER_V1,
      safety=SafetyItem(22, 80.0, 0.0, 100.0),
      road_category=None,
    ),
    40.0,
    100.0,
    mode=2,
    bump_speed_kph=20.0,
  )

  assert (decision.provider, decision.reason, decision.type) == ("naver_v1", "bump", 22)
  assert (decision.limit_kph, decision.distance_m, decision.rejection) == (20.0, 80.0, None)


@pytest.mark.parametrize("road_category", [0, 1])
def test_exact_naver_bump_with_explicit_highway_category_is_blocked(road_category):
  decision = choose_safety(
    _selection(
      source=NavigationSource.NAVER_V1,
      safety=SafetyItem(22, 80.0, 0.0, 100.0),
      road_category=road_category,
    ),
    40.0,
    100.0,
    mode=2,
    bump_speed_kph=20.0,
  )

  assert (decision.provider, decision.rejection) == ("hda", "road_category_blocked")


def test_tmap_bump_without_road_category_still_falls_back_to_hda():
  decision = choose_safety(
    _selection(safety=SafetyItem(22, 80.0, 0.0, 100.0), road_category=None),
    40.0,
    100.0,
    mode=2,
    bump_speed_kph=20.0,
  )

  assert (decision.provider, decision.rejection) == ("hda", "road_category_missing")


def test_bump_requires_mode_two_and_non_highway_road_category():
  decision = choose_safety(
    _selection(safety=SafetyItem(22, 80.0, 0.0, 100.0), road_category=1),
    40.0,
    100.0,
    mode=2,
    bump_speed_kph=20.0,
  )

  assert (decision.provider, decision.rejection) == ("hda", "road_category_blocked")


def test_bump_mode_one_is_rejected_as_mode_disabled():
  decision = choose_safety(
    _selection(safety=SafetyItem(22, 80.0, 0.0, 100.0)),
    40.0,
    100.0,
    mode=1,
    bump_speed_kph=20.0,
  )

  assert (decision.provider, decision.rejection) == ("hda", "mode_disabled")


def test_block_item_becomes_section_at_block_distance():
  decision = choose_safety(
    _selection(safety=SafetyItem(1, 300.0, 60.0, 100.0, block_type=2, block_speed_kph=50.0, block_distance_m=220.0)),
    40.0,
    100.0,
    safety_factor=1.1,
  )

  assert (decision.reason, decision.type, decision.distance_m) == ("section", 4, 220.0)
  assert decision.limit_kph == pytest.approx(55.0)


def test_mobile_camera_requires_mode_three():
  decision = choose_safety(
    _selection(safety=SafetyItem(7, 300.0, 60.0, 100.0)),
    40.0,
    100.0,
    mode=2,
  )

  assert (decision.provider, decision.rejection) == ("hda", "mobile_requires_mode_3")


def test_mobile_camera_rejects_mode_four_too():
  decision = choose_safety(
    _selection(safety=SafetyItem(7, 300.0, 60.0, 100.0)),
    40.0,
    100.0,
    mode=4,
  )

  assert (decision.provider, decision.rejection) == ("hda", "mobile_requires_mode_3")


def test_mode_zero_rejects_navigation_safety():
  decision = choose_safety(
    _selection(safety=SafetyItem(1, 300.0, 60.0, 100.0)),
    40.0,
    100.0,
    mode=0,
  )

  assert (decision.provider, decision.rejection) == ("hda", "mode_disabled")


def test_stale_navigation_safety_falls_back_to_hda():
  decision = choose_safety(_selection(safety_age_s=3.0), 40.0, 100.0)

  assert (decision.provider, decision.rejection) == ("hda", "safety_stale")


def test_invalid_hda_does_not_create_a_decision():
  assert choose_safety(_selection(), 0.0, 100.0) is None


def test_hda_limit_applies_safety_factor_once():
  decision = choose_safety(_no_owner_selection(), 60.0, 100.0, safety_factor=1.1)

  assert decision.limit_kph == pytest.approx(66.0)


def test_invalid_hda_preserves_navigation_rejection():
  selection = _selection(safety=SafetyItem(1, 300.0, 60.0, 100.0), off_route=True)

  assert choose_safety(selection, 0.0, 100.0) is None
  assert safety_rejection(selection) == "off_route"


@pytest.mark.parametrize(("selection", "expected"), (
  (_no_owner_selection(), "no_owner"),
  (_selection(), "safety_absent"),
  (_selection(safety=SafetyItem(99, 100.0, 60.0, 100.0)), "unsupported_type"),
  (_selection(safety=SafetyItem(1, 0.0, 60.0, 100.0)), "invalid_distance"),
))
def test_precise_rejections_survive_hda_fallback(selection, expected):
  decision = choose_safety(selection, 40.0, 100.0)

  assert (decision.provider, decision.rejection) == ("hda", expected)


def _service(selection, *, mode=3):
  serv = CarrotServ.__new__(CarrotServ)
  serv.navigation_selection = selection
  serv.autoNaviSpeedCtrlMode = mode
  serv.autoNaviSpeedSafetyFactor = 1.0
  serv.autoNaviSpeedBumpSpeed = 20.0
  serv.active_carrot = 99
  serv.xSpdType = -1
  serv.xSpdLimit = 0.0
  serv.xSpdDist = 0.0
  serv.kisa_safety_type = -1
  serv.kisa_safety_limit = 0.0
  serv.kisa_safety_distance = 0.0
  serv._last_safety_key = None
  return serv


def test_no_navigation_owner_uses_hda_without_apn_activation():
  serv = _service(_no_owner_selection())

  decision = serv._apply_navigation_safety(60.0, 180.0)

  assert (decision.provider, serv.active_carrot, serv.last_safety_reason) == ("hda", 0, "hda")
  assert serv._select_final_speed(((decision.limit_kph, decision.reason), (250.0, "road"))) == (60.0, "hda")


def test_guiding_naver_owner_keeps_apn_two_while_hda_is_provider():
  serv = _service(_selection(source=NavigationSource.NAVER_V1))

  decision = serv._apply_navigation_safety(60.0, 180.0)

  assert (decision.provider, serv.active_carrot, serv.last_safety_reason) == ("hda", 2, "hda")
  assert (serv.navigation_owner, serv._select_final_speed(((decision.limit_kph, decision.reason), (250.0, "road")))) == (
    "naver_v1", (60.0, "hda"),
  )


def test_navigation_provider_is_distinct_from_final_desired_reason():
  serv = _service(_selection(source=NavigationSource.NAVER_V1, safety=SafetyItem(1, 100.0, 60.0, 100.0)))

  decision = serv._apply_navigation_safety(40.0, 100.0)

  assert (decision.provider, serv.navigation_owner, serv._select_final_speed(((decision.limit_kph, decision.reason), (250.0, "road")))) == (
    "naver_v1", "naver_v1", (60.0, "cam"),
  )


def test_navigation_bump_upgrades_apn_and_keeps_decremented_distance():
  serv = _service(_selection(safety=SafetyItem(22, 80.0, 0.0, 100.0)), mode=2)

  decision = serv._apply_navigation_safety(0.0, 0.0)
  assert (decision.provider, serv.active_carrot, serv.xSpdDist) == ("tmap_legacy", 5, 80.0)
  serv.xSpdDist = 63.0
  serv._apply_navigation_safety(0.0, 0.0)

  assert serv.xSpdDist == 63.0


def test_navigation_speed_calculation_uses_decremented_distance():
  serv = _service(_selection(safety=SafetyItem(1, 80.0, 60.0, 100.0)))
  decision = serv._apply_navigation_safety(0.0, 0.0)
  serv.xSpdDist = 63.0

  assert serv._safety_distance_for_speed(decision) == 63.0


def test_mode_transition_rejects_unchanged_navigation_safety_and_records_rejection():
  serv = _service(_selection(safety=SafetyItem(1, 300.0, 60.0, 100.0)))
  assert serv._apply_navigation_safety(0.0, 0.0) is not None

  serv.autoNaviSpeedCtrlMode = 0
  assert serv._apply_navigation_safety(0.0, 0.0) is None

  assert (serv.active_carrot, serv.last_safety_rejection, serv.xSpdType) == (2, "mode_disabled", -1)


def test_service_keeps_rejection_when_hda_is_invalid():
  serv = _service(_selection(safety=SafetyItem(1, 300.0, 60.0, 100.0), off_route=True))

  assert serv._apply_navigation_safety(0.0, 100.0) is None
  assert (serv.last_safety_provider, serv.last_safety_rejection, serv.active_carrot) == ("", "off_route", 2)


def test_kisa_activity_remains_active_without_navigation_owner():
  serv = _service(_no_owner_selection())
  serv.active_kisa_count = 5

  serv._apply_navigation_safety(60.0, 180.0)

  assert serv.active_carrot == 2


def test_kisa_waze_remains_authoritative_over_lower_hda():
  serv = _service(_no_owner_selection())
  serv.active_kisa_count = 5
  serv.kisa_safety_type = 101
  serv.kisa_safety_limit = 60.0
  serv.kisa_safety_distance = 100.0

  decision = serv._apply_navigation_safety(40.0, 100.0)

  assert (decision.provider, decision.reason, serv.active_carrot) == ("kisa", "waze", 3)
  assert (serv.xSpdType, serv.xSpdLimit, serv.xSpdDist) == (101, 60.0, 100.0)


def test_kisa_police_after_passed_distance_uses_direct_limit_and_section_apn():
  serv = _service(_no_owner_selection())
  serv.active_kisa_count = 5
  serv.kisa_safety_type = 100
  serv.kisa_safety_limit = 60.0
  serv.kisa_safety_distance = 0.0

  decision = serv._apply_navigation_safety(40.0, 100.0)

  assert (decision.provider, serv.active_carrot, serv._safety_distance_for_speed(decision)) == ("kisa", 4, 0.0)


def test_final_speed_selection_keeps_model_and_route_candidates():
  serv = _service(_no_owner_selection())

  assert serv._select_final_speed(((66.0, "hda"), (45.0, "route"), (42.0, "model"))) == (42.0, "model")


def test_real_lease_expiry_releases_owner_to_hda_same_cycle():
  store = NavigationSourceStore()
  control = NavigationControlState(safety=SafetyItem(1, 300.0, 60.0, 0.0))
  snapshot = NavigationSnapshot(NavigationSource.TMAP_LEGACY, "lease", 1, NavigationLifecycle.GUIDING, 0.0, 1, control)
  assert store.accept(snapshot, 0.0)
  serv = _service(store.select(4.0))

  decision = serv._apply_navigation_safety(40.0, 100.0)

  assert (decision.provider, serv.active_carrot) == ("hda", 0)


def test_real_terminal_owner_release_uses_hda_same_cycle():
  store = NavigationSourceStore()
  control = NavigationControlState(safety=SafetyItem(1, 300.0, 60.0, 0.0))
  guiding = NavigationSnapshot(NavigationSource.NAVER_V1, "terminal", 1, NavigationLifecycle.GUIDING, 0.0, 1, control)
  terminal = NavigationSnapshot(NavigationSource.NAVER_V1, "terminal", 2, NavigationLifecycle.ARRIVED, 0.1, 1, control)
  assert store.accept(guiding, 0.0)
  assert store.accept(terminal, 0.1)
  serv = _service(store.select(0.1))

  decision = serv._apply_navigation_safety(40.0, 100.0)

  assert (decision.provider, serv.active_carrot) == ("hda", 0)


def test_real_status_only_guiding_owner_keeps_apn_two():
  store = NavigationSourceStore()
  control = NavigationControlState(status_present=True, status_received_mono_s=0.0)
  snapshot = NavigationSnapshot(NavigationSource.NAVER_V1, "status", 1, NavigationLifecycle.GUIDING, 0.0, 1, control)
  assert store.accept(snapshot, 0.0)
  serv = _service(store.select(0.1))

  decision = serv._apply_navigation_safety(40.0, 100.0)

  assert (serv.navigation_owner, decision.provider, serv.active_carrot) == ("naver_v1", "hda", 2)


class _Node:
  pass


class _Publisher:
  def __init__(self):
    self.messages = {}

  def send(self, name, message):
    self.messages[name] = message


class _CycleSM:
  def __init__(self, *, hda_limit=0.0, hda_distance=0.0, distance_traveled=0.0, model_speed=250.0):
    self.alive = {name: False for name in ("carState", "selfdriveState", "navInstruction", "carrotNavi")}
    self.valid = dict(self.alive)
    self.alive.update(carState=True, selfdriveState=True)
    self.valid.update(carState=True, selfdriveState=True)
    self.data = {
      "carState": type("CarState", (), {
        "vEgo": 10.0, "speedLimit": hda_limit, "speedLimitDistance": hda_distance,
        "gasPressed": False, "brakePressed": False,
      })(),
      "selfdriveState": type("SelfdriveState", (), {"distanceTraveled": distance_traveled})(),
      "modelV2": type("Model", (), {"meta": type("Meta", (), {"modelTurnSpeed": model_speed})()})(),
    }

  def __getitem__(self, name):
    return self.data[name]


def _published_cycle(monkeypatch, selection, *, hda_limit=0.0, hda_distance=0.0,
                     route_speed=250.0, model_speed=250.0, distance_traveled=0.0):
  serv = _service(selection)
  serv.totalDistance = 0.0
  serv.nRoadLimitSpeed = serv.nRoadLimitSpeed_last = 30.0
  serv.autoRoadSpeedLimitOffset = -1
  serv.is_metric = True
  serv.autoNaviSpeedBumpTime = serv.autoNaviSpeedCtrlEnd = 1.0
  serv.autoNaviSpeedDecelRate = 1.0
  serv.autoNaviCountDownMode = 0
  serv.autoCurveSpeedLowerLimit = 0.0
  serv.turnSpeedControlMode = 3
  serv.mapTurnSpeedFactor = 1.0
  serv.autoTurnControl = 0
  serv.source_last = "none"
  serv.gas_override_speed = 0.0
  serv.gas_pressed_state = False
  serv.left_spd_sec = serv.left_tbt_sec = serv.left_sec = serv.max_left_sec = serv.carrot_left_sec = 100
  serv.sdi_inform = False
  serv.xTurnInfo = serv.xTurnInfoNext = -1
  serv.xDistToTurn = serv.xDistToTurnNext = 0.0
  serv.nSdiType = serv.nSdiDist = 0
  serv.nTBTTurnType = serv.nTBTTurnTypeNext = -1
  serv.nTBTDist = serv.nTBTDistNext = 0.0
  serv.nGoPosDist = serv.nGoPosTime = 0
  serv.szPosRoadName = serv.szTBTMainText = serv.szNearDirName = serv.szFarDirName = ""
  serv.lang = "en"
  serv.bearing = serv.vpPosPointLat = serv.vpPosPointLon = 0.0
  serv.carrotCmdIndex = serv.traffic_state = 0
  serv.carrotCmd = serv.carrotArg = ""
  serv.carrot_navi_road_limit_valid = False
  serv.active_count = serv.active_sdi_count = 0
  serv.active_sdi_count_max = 200
  serv.active_kisa_count = 0
  serv.update_params = lambda: None
  serv._update_gps = lambda *_args: 0.0
  serv.update_auto_turn = lambda *_args: (250.0, "none", 0.0, 0.0)
  serv._update_cmd = lambda: None
  serv.calculate_current_speed = lambda _distance, limit, *_args: limit

  def new_message(name):
    message = _Node()
    message.valid = False
    message.carrotMan = _Node()
    message.navInstructionCarrot = _Node()
    return message

  monkeypatch.setattr(carrot_serv_module.messaging, "new_message", new_message)
  sm = _CycleSM(hda_limit=hda_limit, hda_distance=hda_distance,
                distance_traveled=distance_traveled, model_speed=model_speed)
  publisher = _Publisher()
  serv.update_navi("", sm, publisher, 0.0, (), (), route_speed, "gpsLocationExternal", navigation_prepared=True)
  return serv, publisher.messages["carrotMan"].carrotMan


def test_update_navi_publishes_hda_without_navigation_apn(monkeypatch):
  serv, message = _published_cycle(monkeypatch, _no_owner_selection(), hda_limit=60.0, hda_distance=100.0)

  assert (message.activeCarrot, message.desiredSource, serv.last_safety_provider) == (0, "hda", "hda")


def test_update_navi_publishes_navigation_reason_not_provider(monkeypatch):
  selection = _selection(source=NavigationSource.NAVER_V1, safety=SafetyItem(1, 100.0, 60.0, 100.0))
  serv, message = _published_cycle(monkeypatch, selection, hda_limit=40.0, hda_distance=100.0)

  assert (message.activeCarrot, message.desiredSource, serv.navigation_owner, serv.last_safety_provider) == (
    3, "cam", "naver_v1", "naver_v1",
  )


@pytest.mark.parametrize(("route_speed", "model_speed", "expected"), ((45.0, 250.0, "route"), (250.0, 42.0, "model")))
def test_update_navi_route_and_model_can_win_without_losing_navigation_provider(monkeypatch, route_speed, model_speed, expected):
  selection = _selection(safety=SafetyItem(1, 100.0, 60.0, 100.0))
  serv, message = _published_cycle(monkeypatch, selection, route_speed=route_speed, model_speed=model_speed)

  assert (message.desiredSource, serv.last_safety_provider) == (expected, "tmap_legacy")


def test_update_navi_decrements_navigation_distance_without_rewind(monkeypatch):
  selection = _selection(safety=SafetyItem(1, 100.0, 60.0, 100.0))
  serv, first = _published_cycle(monkeypatch, selection)
  assert first.xSpdDist == 100
  sm = _CycleSM(distance_traveled=5.0)
  publisher = _Publisher()
  serv.update_navi("", sm, publisher, 0.0, (), (), 250.0, "gpsLocationExternal", navigation_prepared=True)
  second = publisher.messages["carrotMan"].carrotMan

  assert second.xSpdDist == 95


def test_kisa_input_survives_new_navigation_projection_in_actual_speed_cycle(monkeypatch):
  selection = _selection(safety=SafetyItem(1, 100.0, 60.0, 100.0))
  serv, _ = _published_cycle(monkeypatch, selection)
  serv.update_kisa({"kisawazereportid": "camera", "kisawazealertdist": "90 m"})
  serv._ensure_navigation_sources()
  assert serv.accept_navigation_snapshot(replace(selection.snapshot, sequence=2))
  serv.prepare_navigation(_CycleSM(), now_s=100.0)
  assert (serv.xSpdType, serv.xSpdLimit, serv.xSpdDist) == (1, 60.0, 100.0)
  publisher = _Publisher()
  serv.update_navi("", _CycleSM(hda_limit=40.0, hda_distance=100.0), publisher, 0.0, (), (), 250.0,
                   "gpsLocationExternal", navigation_prepared=True)
  message = publisher.messages["carrotMan"].carrotMan

  assert (message.desiredSource, serv.last_safety_provider, message.xSpdType) == ("waze", "kisa", 101)
