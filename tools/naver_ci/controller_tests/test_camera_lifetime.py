import pytest

from tools.naver_ci.controller_tests.test_bump_parity import drive  # noqa: F401
from openpilot.selfdrive.carrot.naver_navigation_protocol import parse_naver_navigation_v1
from openpilot.selfdrive.carrot.tests.test_naver_navigation_protocol import inactive_frame


def camera_frame(sequence=1, revision=1, road_limit=80, distance=30):
  frame = inactive_frame('idle')
  frame.update(lifecycle='guiding', sequence=sequence)
  frame['safety'] = {'present': True, 'kind': 'fixed_camera', 'distanceM': distance, 'speedKph': 60}
  if revision is not None:
    frame['safety']['revision'] = revision
  frame['road'] = {'limitValid': True, 'limitKph': road_limit, 'categoryValid': False}
  return frame


def set_hda(serv, CS):
  serv.params.values.update(VehicleSpeedCameraControlMode='1', VehicleNaviCanControl='1', AutoNaviSpeedCtrlEnd='10')
  CS.speedLimit, CS.speedLimitDistance = 40, 10
  CS.vehicleNaviActive = CS.vehicleNaviAvailable = True
  CS.vehicleNaviSpeed = 40


@pytest.mark.parametrize('provider', ['naver', 'tmap'])
def test_guidance_owner_survives_safety_fallback_to_hda(drive, provider):
  serv, CS, now, tick = drive
  set_hda(serv, CS)
  if provider == 'naver':
    frame = inactive_frame('idle')
    frame['lifecycle'] = 'guiding'
    serv.accept_navigation_snapshot(parse_naver_navigation_v1(frame, now[0]))
  else:
    serv.update({'nRoadLimitSpeed': 60, 'nTBTTurnType': 12, 'nTBTDist': 500})
  result = tick()
  assert result.naviOwner == ('naver_v1' if provider == 'naver' else 'tmap_legacy')
  assert result.activeCarrot >= 2
  assert (result.desiredSource, result.desiredSpeed, result.decelProvider) == ('hda', 40, 'hda')
  assert result.vehicleNaviActive and result.vehicleNaviSpeed == 40


@pytest.mark.parametrize('revision', [None, 1])
def test_identical_safety_heartbeat_cannot_restore_a_passed_camera(drive, revision):
  serv, CS, now, tick = drive
  serv.accept_navigation_snapshot(parse_naver_navigation_v1(camera_frame(revision=revision), now[0]))
  assert tick().desiredSource == 'cam'
  assert tick(30).desiredSource != 'cam'
  now[0] = 10.5
  serv.accept_navigation_snapshot(parse_naver_navigation_v1(camera_frame(sequence=2, revision=revision), now[0]))
  assert tick(30).desiredSource != 'cam'
  assert tick(30).xSpdType == -1


def test_road_limit_update_does_not_restore_camera_distance(drive):
  serv, CS, now, tick = drive
  serv.accept_navigation_snapshot(parse_naver_navigation_v1(camera_frame(), now[0]))
  assert tick().xSpdDist == 30
  assert tick(30).xSpdType == -1
  now[0] = 10.2
  serv.accept_navigation_snapshot(parse_naver_navigation_v1(camera_frame(sequence=2, road_limit=50), now[0]))
  result = tick(30)
  assert result.nRoadLimitSpeed == 50
  assert result.xSpdType == -1 and result.desiredSource != 'cam'


def test_fresh_camera_wins_then_stale_camera_falls_back_without_losing_owner(drive):
  serv, CS, now, tick = drive
  set_hda(serv, CS)
  serv.accept_navigation_snapshot(parse_naver_navigation_v1(camera_frame(), now[0]))
  result = tick()
  assert (result.desiredSource, result.decelProvider, result.desiredSpeed) == ('cam', 'naver_v1', 60)
  now[0] = 11.
  serv.accept_navigation_snapshot(parse_naver_navigation_v1(camera_frame(sequence=2), now[0]))
  now[0] = 12.01
  serv.accept_navigation_snapshot(parse_naver_navigation_v1(camera_frame(sequence=3), now[0]))
  result = tick()
  assert result.naviOwner == 'naver_v1'
  assert result.desiredSource == 'hda' and result.decelProvider == 'hda'
  assert result.naviSafetyRejection == 'safety_stale'


def test_category_only_update_does_not_restore_a_passed_camera(drive):
  serv, CS, now, tick = drive
  serv.accept_navigation_snapshot(parse_naver_navigation_v1(camera_frame(), now[0]))
  tick()
  assert tick(30).xSpdType == -1
  now[0] = 10.2
  frame = camera_frame(sequence=2)
  frame['road'].update(categoryValid=True, category=8)
  serv.accept_navigation_snapshot(parse_naver_navigation_v1(frame, now[0]))
  assert tick(30).xSpdType == -1


def test_new_revision_replaces_passed_camera_and_terminal_returns_to_hda(drive):
  serv, CS, now, tick = drive
  set_hda(serv, CS)
  serv.accept_navigation_snapshot(parse_naver_navigation_v1(camera_frame(), now[0]))
  tick()
  assert tick(30).desiredSource == 'hda'
  now[0] = 10.2
  serv.accept_navigation_snapshot(parse_naver_navigation_v1(camera_frame(sequence=2, revision=2), now[0]))
  assert tick(30).desiredSource == 'cam'
  stopped = inactive_frame('stopped')
  stopped['sequence'] = 3
  serv.accept_navigation_snapshot(parse_naver_navigation_v1(stopped, now[0]))
  result = tick(30)
  assert result.naviOwner == '' and result.desiredSource == 'hda'
