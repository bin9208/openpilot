from dataclasses import replace

import pytest

from tools.naver_ci.controller_tests.test_bump_parity import drive  # noqa: F401
from openpilot.selfdrive.carrot.navigation_runtime import NavigationRuntime
from openpilot.selfdrive.carrot.navigation_sources import NavigationControlState, NavigationLifecycle, NavigationSnapshot, NavigationSource
from openpilot.selfdrive.carrot.carrot_man import CarrotMan


def route_snapshot(source=NavigationSource.NAVER_V1, **updates):
  control = NavigationControlState(route_present=True, route_revision=1,
    route_points=((37., 127.), (37.001, 127.001)), remaining_distance_m=1000,
    remaining_time_s=100)
  return NavigationSnapshot(source, 'route-session', 1, NavigationLifecycle.GUIDING, 10., 0,
    replace(control, **updates))


def test_trip_progress_does_not_rebuild_geometry_or_reset_route_cursor():
  runtime = NavigationRuntime()
  initial = route_snapshot()
  runtime.accept_snapshot(initial)
  first = runtime.select(10.)[1]
  runtime.accept_snapshot(replace(initial, sequence=2, received_mono_s=10.1,
    control=replace(initial.control, remaining_distance_m=900, remaining_time_s=90)))
  second = runtime.select(10.1)[1]
  assert second.route.sequence == first.route.sequence
  assert second.route.remaining_distance_m == 900


@pytest.mark.parametrize('provider', [NavigationSource.NAVER_V1, NavigationSource.TMAP_LEGACY])
def test_route_keeps_common_gas_floor_and_brake_reset(drive, monkeypatch, provider):
  serv, CS, now, tick = drive
  serv.accept_navigation_snapshot(route_snapshot(provider))
  serv.params.values.update(TurnSpeedControlMode='3', MapTurnSpeedFactor='100', AutoCurveSpeedLowerLimit='20')
  original_update = serv.update_navi
  def with_route_speed(*args, **kwargs):
    args = list(args)
    args[6] = 30.
    return original_update(*args, **kwargs)
  monkeypatch.setattr(serv, 'update_navi', with_route_speed)
  assert (tick().desiredSource, tick().desiredSpeed) == ('route', 30)
  CS.gasPressed = True
  assert (tick().desiredSource, tick().desiredSpeed) == ('gas', 50)
  CS.gasPressed = False
  assert tick().desiredSource == 'gas'
  CS.brakePressed = True
  assert (tick().desiredSource, tick().desiredSpeed) == ('route', 30)


def test_manager_cursor_survives_same_route_and_resets_on_new_session():
  runtime = NavigationRuntime()
  initial = route_snapshot()
  runtime.accept_snapshot(initial)
  navi = runtime.select(10.)[1]
  manager = CarrotMan.__new__(CarrotMan)
  manager.carrot_navi_route_session_id = ''
  manager.carrot_navi_route_sequence = -1
  manager.carrot_navi_route_owned = False
  manager.send_routes = lambda coords: None
  manager._update_carrot_navi_route(navi)
  manager.navi_points_start_index = 1
  manager._update_carrot_navi_route(navi)
  assert manager.navi_points_start_index == 1
  manager._update_carrot_navi_route(replace(navi, session_id='new-session'))
  assert manager.navi_points_start_index == 0
  manager._update_carrot_navi_route(None)
  assert not manager.navi_points_active and not manager.navi_points
