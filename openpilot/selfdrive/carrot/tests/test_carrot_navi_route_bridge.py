import pytest

from openpilot.selfdrive.carrot.carrot_man import CarrotMan
from openpilot.selfdrive.carrot.carrot_navi_control import parse_carrot_navi_control
from openpilot.selfdrive.carrot.navigation_sources import NavigationSource


class _Params:
  def __init__(self):
    self.onroad = True
    self.values = {}

  def get_bool(self, key):
    assert key == "IsOnroad"
    return self.onroad

  def put(self, key, value):
    self.values[key] = value


def _control(sequence=1, present=True):
  return parse_carrot_navi_control({
    "schemaVersion": 1,
    "connected": True,
    "sessionId": "session",
    "speed": {"meta": {"present": False, "sequence": 0}},
    "guidanceCurrent": {"meta": {"present": False, "sequence": 0}},
    "guidanceNext": {"meta": {"present": False, "sequence": 0}},
    "laneCurrent": {"meta": {"present": False, "sequence": 0}},
    "navigationStatus": {"meta": {"present": True, "sequence": 1}, "guidanceActive": True},
    "route": {
      "meta": {"present": present, "sequence": sequence},
      "polyline": [
        {"latitude": 37.5, "longitude": 127.1},
        {"latitude": 37.6, "longitude": 127.2},
      ] if present else [],
    },
  })


def _man():
  man = CarrotMan.__new__(CarrotMan)
  man.params = _Params()
  man.carrot_navi_route_session_id = ""
  man.carrot_navi_route_sequence = -1
  man.carrot_navi_route_owned = False
  man.navi_points = []
  man.navi_points_start_index = 0
  man.navi_points_active = False
  man.navd_active = False
  man.sent_routes = []
  man.send_routes = lambda coords: man.sent_routes.append(coords)
  return man


def test_7714_route_updates_existing_route_consumer_and_clears_on_tombstone():
  man = _man()
  control = _control()
  assert control is not None

  man._update_carrot_navi_route(control)
  assert man.navi_points == [(127.1, 37.5), (127.2, 37.6)]
  assert man.navi_points_active and man.navd_active
  assert man.carrot_navi_route_owned
  assert man.sent_routes[-1][0] == {"latitude": 37.5, "longitude": 127.1}

  man._update_carrot_navi_route(control)
  assert len(man.sent_routes) == 1

  man.navi_points = []
  man.navi_points_active = False
  man._update_carrot_navi_route(control)
  assert len(man.sent_routes) == 2
  assert man.navi_points_active

  tombstone = _control(sequence=2, present=False)
  assert tombstone is not None
  man._update_carrot_navi_route(tombstone)
  assert not man.navi_points_active
  assert not man.navd_active
  assert not man.carrot_navi_route_owned
  assert man.sent_routes[-1] == []


def test_7714_absent_route_does_not_clear_native_nav_route():
  man = _man()
  tombstone = _control(sequence=1, present=False)
  assert tombstone is not None

  man._update_carrot_navi_route(tombstone, force=True)
  assert man.sent_routes == []
  assert not man.carrot_navi_route_owned


def test_native_route_update_wins_when_owned_7714_route_is_no_longer_available():
  man = _man()
  control = _control()
  assert control is not None
  man._update_carrot_navi_route(control)
  assert man.carrot_navi_route_owned

  native_points = [(126.9, 37.4)]
  man.navi_points = native_points.copy()
  man.navi_points_active = True
  man.navd_active = True
  sent_count = len(man.sent_routes)

  tombstone = _control(sequence=2, present=False)
  assert tombstone is not None
  man._update_carrot_navi_route(tombstone, force=True)

  assert man.navi_points == native_points
  assert man.navi_points_active and man.navd_active
  assert not man.carrot_navi_route_owned
  assert len(man.sent_routes) == sent_count


def test_native_route_update_wins_when_7714_disconnects():
  man = _man()
  control = _control()
  assert control is not None
  man._update_carrot_navi_route(control)

  native_points = [(126.9, 37.4)]
  man.navi_points = native_points.copy()
  man.navi_points_active = True
  man.navd_active = True
  sent_count = len(man.sent_routes)

  man._update_carrot_navi_route(None, force=True)

  assert man.navi_points == native_points
  assert man.navi_points_active and man.navd_active
  assert not man.carrot_navi_route_owned
  assert len(man.sent_routes) == sent_count


def test_legacy_7713_route_path_remains_operational():
  man = _man()
  man.handle_route([
    {"x": 126.9, "y": 37.4, "valid": True},
    {"x": 127.0, "y": 37.5, "valid": True},
  ])

  assert man.navi_points == [(126.9, 37.4), (127.0, 37.5)]
  assert man.navi_points_active and man.navd_active
  assert man.sent_routes[-1][0] == {"latitude": 37.4, "longitude": 126.9}
  assert "NavDestination" in man.params.values


@pytest.mark.parametrize("navd_route_updated", (False, True))
def test_navigation_cycle_projects_and_bridges_before_route_consumer_and_publish(navd_route_updated):
  calls = []
  selected = type("Selection", (), {
    "snapshot": type("Snapshot", (), {
      "source": NavigationSource.NAVER_V1,
      "session_id": "new-owner",
      "control": type("Control", (), {
        "route_present": True,
        "route_received_mono_s": 20.0,
        "route_points": ((37.7, 127.3),),
      })(),
    })(),
  })()

  class _Serv:
    navigation_selection = None

    def prepare_navigation(self, sm):
      calls.append(("prepare", sm))
      self.navigation_selection = selected

    def update_navi(self, *args, **kwargs):
      calls.append(("publish", kwargs.get("navigation_prepared")))

  man = CarrotMan.__new__(CarrotMan)
  man.carrot_serv = _Serv()
  man.sm = object()
  man.pm = object()
  man.gps_location_service = "gps"
  man.carrot_navi_route_session_id = "old-owner"
  man.carrot_navi_route_sequence = 19.0
  man.carrot_navi_route_owned = True
  man.navi_points = [(126.9, 37.4)]
  man.navi_points_start_index = 0
  man.navi_points_active = True
  man.navd_active = True
  man.send_routes = lambda coords: None
  bridge = man._update_selected_navigation_route
  man._update_selected_navigation_route = lambda force=False: (
    calls.append(("bridge", force)), bridge(force=force),
  )
  man.carrot_navi_route = lambda: (
    calls.append(("route", tuple(man.navi_points)))
    or ("coords", "distances", "speed")
  )

  man._update_navigation_cycle("10.0.0.2", 42.0, navd_route_updated=navd_route_updated)

  assert calls == [
    ("prepare", man.sm),
    ("bridge", navd_route_updated),
    ("route", ((127.3, 37.7),)),
    ("publish", True),
  ]


def test_legacy_aux_objects_keep_top_level_source_session_and_receipt_binding():
  calls = []

  class _Serv:
    def update_legacy_navigation_aux(self, kind, payload, source, session_id, receipt):
      calls.append((kind, payload, source, session_id, receipt))
      return True

  man = CarrotMan.__new__(CarrotMan)
  man.carrot_serv = _Serv()
  man._store_navi_event = lambda *args: None
  man._write_navi_debug_param = lambda *args: None
  man._last_rgdata_timestamp_ms = 0
  man._rgdata_ts_lock = __import__("threading").Lock()

  assert man._dispatch_legacy_navi_frame({
    "route": [{"x": 127.1, "y": 37.5, "valid": True}],
    "sinf": {
      "redLightOn": True,
      "redLightRemainTime": 12,
      "distance": 90,
    },
  }, ("10.0.0.2", 7713), 12.5, "legacy-exact")

  assert [(kind, source, session_id, receipt) for kind, _, source, session_id, receipt in calls] == [
    ("traffic", NavigationSource.TMAP_LEGACY, "legacy-exact", 12.5),
    ("route", NavigationSource.TMAP_LEGACY, "legacy-exact", 12.5),
  ]
  assert calls[1][1]["route_points"] == ((37.5, 127.1),)


def test_navd_route_stays_available_when_selected_owner_has_no_route():
  observed = []
  selected = type("Selection", (), {
    "snapshot": type("Snapshot", (), {
      "source": NavigationSource.NAVER_V1,
      "session_id": "guidance-only",
      "control": type("Control", (), {
        "route_present": False,
        "route_received_mono_s": None,
        "route_points": (),
      })(),
    })(),
  })()

  class _Serv:
    navigation_selection = None

    def prepare_navigation(self, sm):
      self.navigation_selection = selected

    def update_navi(self, *args, **kwargs):
      observed.append(kwargs.get("navigation_prepared"))

  man = _man()
  man.carrot_serv = _Serv()
  man.sm = object()
  man.pm = object()
  man.gps_location_service = "gps"
  man.carrot_navi_route_session_id = "old-selected-route"
  man.carrot_navi_route_sequence = 1
  man.carrot_navi_route_owned = True
  native_points = [(126.9, 37.4)]
  man.navi_points = native_points.copy()
  man.carrot_navi_route = lambda: (
    observed.append(tuple(man.navi_points)) or ("coords", "distances", "speed")
  )

  man._update_navigation_cycle("10.0.0.2", 42.0, navd_route_updated=True)

  assert man.navi_points == native_points
  assert observed == [tuple(native_points), True]


def test_unbound_aux_objects_are_dropped_instead_of_mutating_direct_state():
  class _Serv:
    def update_legacy_navigation_aux(self, *args, **kwargs):
      raise AssertionError("unbound aux must not enter the source store")

  man = CarrotMan.__new__(CarrotMan)
  man.carrot_serv = _Serv()
  man._store_navi_event = lambda *args: None
  man._write_navi_debug_param = lambda *args: None
  man.handle_route = lambda payload: (_ for _ in ()).throw(AssertionError("direct route mutation"))
  man.handle_traffic_light = lambda payload: (_ for _ in ()).throw(AssertionError("direct traffic mutation"))
  man._dispatch_obj({
    "route": [{"x": 127.1, "y": 37.5, "valid": True}],
    "sinf": {"redLightOn": True, "redLightRemainTime": 12},
  })
