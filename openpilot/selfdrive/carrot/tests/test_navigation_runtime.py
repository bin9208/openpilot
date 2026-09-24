import importlib.util

import pytest

from openpilot.selfdrive.carrot.navigation_sources import NavigationSource as Source
from openpilot.selfdrive.carrot.naver_navigation_protocol import parse_naver_navigation_v1
from openpilot.selfdrive.carrot.tests.test_naver_navigation_protocol import valid_frame, inactive_frame


@pytest.fixture
def runtime():
  name = 'openpilot.selfdrive.carrot.navigation_runtime'
  assert importlib.util.find_spec(name) is not None, 'Runtime source adapter is not implemented'
  from openpilot.selfdrive.carrot.navigation_runtime import NavigationRuntime
  return NavigationRuntime()


def naver(runtime, now=10., sequence=42, session=None):
  frame = valid_frame(sequence=sequence)
  frame['safety']['revision'] = 7
  if session is not None:
    frame['sessionId'] = session
  assert runtime.accept_snapshot(parse_naver_navigation_v1(frame, now))


def test_naver_projects_into_existing_control_contract(runtime):
  naver(runtime)
  selection, control = runtime.select(10.1)
  assert selection.snapshot.source == Source.NAVER_V1
  assert control.current.turn_type == 12
  assert control.current.distance_m == 380
  assert control.next.turn_type == 13
  assert control.speed.sdi_type == 1
  assert control.speed.sdi_distance_m == 420
  assert control.speed.road_limit_kph == 80
  assert control.road_category == 8
  assert control.route.remaining_distance_m == 12500
  assert control.guidance_active


def test_unchanged_safety_heartbeat_does_not_reset_control_sequence(runtime):
  naver(runtime)
  _, first = runtime.select(10.)
  naver(runtime, 11., 43)
  _, second = runtime.select(11.)
  assert first.speed.sequence == second.speed.sequence
  naver(runtime, 12.1, 44)
  selected, stale = runtime.select(12.1)
  assert selected.snapshot is not None
  assert not stale.speed.sdi_present
  assert stale.speed.sequence != second.speed.sequence


def test_transport_loss_retains_owner_until_exact_lease_boundary(runtime):
  naver(runtime)
  assert runtime.store.record_transport_loss(Source.NAVER_V1, valid_frame()['sessionId'], 10.5)
  assert runtime.select(11.999)[0].snapshot.source == Source.NAVER_V1
  expired, control = runtime.select(12.)
  assert expired.snapshot is None and expired.reason == 'owner_expired'
  assert control is None


@pytest.mark.parametrize('terminal', ['stopped', 'arrived'])
def test_terminal_requires_new_session_even_with_higher_sequence(runtime, terminal):
  naver(runtime)
  frame = inactive_frame(terminal)
  frame['sequence'] = 43
  assert runtime.accept_snapshot(parse_naver_navigation_v1(frame, 10.2))
  assert runtime.select(10.2)[1] is None
  assert not runtime.accept_snapshot(parse_naver_navigation_v1(valid_frame(sequence=1000), 10.3))
  naver(runtime, 10.4, 1, '019f0000-0000-7000-8000-000000000002')
  assert runtime.select(10.4)[1] is not None


def test_guiding_without_items_is_still_navigation_owner(runtime):
  frame = inactive_frame('idle')
  frame['lifecycle'] = 'guiding'
  assert runtime.accept_snapshot(parse_naver_navigation_v1(frame, 10.))
  selected, control = runtime.select(10.1)
  assert selected.snapshot.source == Source.NAVER_V1
  assert control.guidance_active and not control.current.present


def test_latest_activation_wins_not_latest_background_heartbeat(runtime):
  assert runtime.accept_legacy({'nRoadLimitSpeed': 60, 'nTBTTurnType': 13, 'nTBTDist': 100}, 'tmap', 9.)
  assert runtime.select(9.)[0].snapshot.source == Source.TMAP_LEGACY
  naver(runtime)
  assert runtime.select(10.)[0].snapshot.source == Source.NAVER_V1
  assert runtime.accept_legacy({'nRoadLimitSpeed': 60}, 'tmap', 10.1)
  assert runtime.select(10.1)[0].snapshot.source == Source.NAVER_V1
  assert runtime.accept_legacy({'nRoadLimitSpeed': 60, 'nTBTTurnType': 13}, 'new-tmap', 10.2)
  assert runtime.select(10.2)[0].snapshot.source == Source.TMAP_LEGACY


def test_legacy_bump_and_secondary_data_keep_common_controller_fields(runtime):
  assert runtime.accept_legacy({'nRoadLimitSpeed': 60, 'roadcate': 8,
    'nSdiType': 22, 'nSdiDist': 70, 'nSdiSpeedLimit': 30,
    'nSdiPlusType': 1, 'nSdiPlusDist': 400, 'nSdiPlusSpeedLimit': 50,
    'nTBTTurnType': 12, 'nTBTNextRoadWidth': 12}, 'tmap', 10.)
  selected, control = runtime.select(10.)
  assert control.speed.sdi_type == 22 and control.speed.sdi_distance_m == 70
  assert control.speed.secondary_sdi_type == 1
  assert control.speed.secondary_sdi_distance_m == 400
  assert control.road_category == 8
  assert selected.snapshot.control.current.next_road_width == 12


def test_v2_disconnected_service_records_loss_without_instant_clear(runtime):
  payload = {'schemaVersion': 1, 'connected': True, 'sessionId': 'v2',
    'navigationStatus': {'meta': {'present': True, 'sequence': 1}, 'guidanceActive': True},
    'guidanceCurrent': {'meta': {'present': True, 'sequence': 1, 'receivedMonoTimeNanos': 10_000_000_000},
                        'turnType': 13, 'distanceM': 500}}
  assert runtime.accept_v2(payload, 10.)
  assert runtime.select(10.)[1].current.turn_type == 13
  runtime.v2_transport_lost(10.1)
  assert runtime.select(19.9)[0].snapshot.source == Source.CARROT_NAVI_V2
  assert runtime.select(20.)[1] is None


def test_v2_unchanged_item_does_not_gain_freshness_from_service_heartbeat(runtime):
  payload = {'schemaVersion': 1, 'connected': True, 'sessionId': 'v2',
    'navigationStatus': {'meta': {'present': True, 'sequence': 1}, 'guidanceActive': True},
    'speed': {'meta': {'present': True, 'sequence': 1, 'receivedMonoTimeNanos': 10_000_000_000},
              'sdiPresent': True, 'sdiType': 1, 'sdiDistanceM': 500, 'sdiSpeedLimitKph': 60}}
  runtime.accept_v2(payload, 10.)
  assert runtime.select(10.)[1].speed.sdi_present
  runtime.accept_v2(payload, 20.1)
  assert runtime.select(20.1)[0].snapshot is not None
  assert not runtime.select(20.1)[1].speed.sdi_present


def test_legacy_http_identity_can_return_after_another_legacy_transport(runtime):
  for index in range(8):
    assert runtime.accept_legacy({'nRoadLimitSpeed': 60}, 'http', 10. + index / 10)
  assert runtime.accept_legacy({'nRoadLimitSpeed': 60}, 'tcp', 11.)
  assert runtime.accept_legacy({'nRoadLimitSpeed': 60}, 'http', 11.1)
  assert runtime.select(11.1)[0].snapshot.session_id == 'http'


def test_legacy_route_aux_is_session_bound_and_does_not_renew_owner_lease(runtime):
  assert hasattr(runtime, 'accept_legacy_aux'), 'Legacy route is not session-bound'
  runtime.accept_legacy({'nRoadLimitSpeed': 60}, 'tmap', 10.)
  assert runtime.accept_legacy_aux('tmap', 11., route_points=((37., 127.), (37.1, 127.1)))
  assert runtime.select(11.)[1].route.polyline == ((37., 127.), (37.1, 127.1))
  assert not runtime.accept_legacy_aux('old-session', 11.1, route_points=((38., 128.),))
  assert runtime.select(11.1)[1].route.polyline == ((37., 127.), (37.1, 127.1))
  assert runtime.select(14.)[1] is None


def test_legacy_route_before_first_guidance_is_deferred_without_activating_owner(runtime):
  points = ((37., 127.), (37.1, 127.1))
  runtime.accept_legacy_aux('tmap', 9.9, route_points=points)
  assert runtime.select(9.9)[1] is None
  runtime.accept_legacy({'nRoadLimitSpeed': 60}, 'tmap', 10.)
  assert runtime.select(10.)[1].route.polyline == points
