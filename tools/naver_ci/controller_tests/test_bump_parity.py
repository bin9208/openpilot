from types import SimpleNamespace

import pytest

from openpilot.selfdrive.carrot import carrot_serv
from openpilot.selfdrive.carrot.naver_navigation_protocol import NaverProtocolError, parse_naver_navigation_v1
from openpilot.selfdrive.carrot.tests.test_naver_navigation_protocol import inactive_frame
from openpilot.selfdrive.carrot.tests.test_vehicle_speed_camera_control import _car_state


@pytest.fixture
def drive(monkeypatch):
  now = [10.]
  monkeypatch.setattr(carrot_serv.time, 'monotonic', lambda: now[0])
  serv = carrot_serv.CarrotServ()
  serv.params.values.update(AutoNaviSpeedBumpSpeed='25', AutoNaviSpeedBumpTime='5',
    AutoNaviSpeedBumpEndDistance='600', AutoNaviSpeedCtrlMode='2', AutoNaviSpeedSafetyFactor='100',
    AutoNaviSpeedDecelRate='135', AutoRoadSpeedLimitOffset='-1', AutoNaviCountDownMode='2')
  serv.update_params()
  monkeypatch.setattr(serv, '_update_gps', lambda *_args: 0.)
  monkeypatch.setattr(serv, 'update_auto_turn', lambda *_args: (250., 'none', 250., 0.))
  CS = _car_state(speed_limit=0, distance=0, v_ego=50 / 3.6)

  class SM(dict):
    alive = {'carrotNavi': False, 'carState': True, 'selfdriveState': True, 'navInstruction': False}
    valid = {'carrotNavi': False}
    updated = {'carrotNavi': False}

  sm = SM(carState=CS, selfdriveState=SimpleNamespace(distanceTraveled=0.))
  sent = {}
  pm = SimpleNamespace(send=lambda name, message: sent.update({name: message}))

  def tick(traveled=0.):
    sm['selfdriveState'].distanceTraveled = traveled
    serv.update_navi('', sm, pm, 250., [], [], 250., 'gpsLocation')
    return sent['carrotMan'].carrotMan

  return serv, CS, now, tick


def bump_frame(distance=30., category=None, sequence=1, revision=1):
  frame = inactive_frame('idle')
  frame.update(lifecycle='guiding', sequence=sequence)
  frame['safety'] = {'present': True, 'kind': 'speed_bump', 'distanceM': distance, 'revision': revision}
  if category is not None:
    frame['road'] = {'limitValid': False, 'categoryValid': True, 'category': category}
  return frame


@pytest.mark.parametrize('category,allowed', [(None, True), (0, False), (1, False), (8, True)])
def test_verified_naver_bump_distinguishes_absent_category_from_highway(drive, category, allowed):
  serv, CS, now, tick = drive
  serv.accept_navigation_snapshot(parse_naver_navigation_v1(bump_frame(category=category), now[0]))
  result = tick()
  assert result.activeCarrot >= 2 and result.naviOwner == 'naver_v1'
  if allowed:
    assert (result.xSpdType, result.xSpdDist, result.desiredSource, result.desiredSpeed) == (22, 30, 'bump', 25)
    assert result.decelProvider == 'naver_v1'
  else:
    assert result.xSpdType == -1 and result.desiredSource != 'bump'


def test_naver_bump_consumes_wheel_distance_and_releases_at_shared_endpoint(drive):
  serv, CS, now, tick = drive
  serv.accept_navigation_snapshot(parse_naver_navigation_v1(bump_frame(), now[0]))
  assert tick().desiredSource == 'bump'
  assert tick(10).xSpdDist == 20
  assert tick(23).desiredSource == 'bump'  # 7m, above configured 6m endpoint.
  assert tick(24).desiredSource != 'bump'
  result = tick(30)
  assert result.xSpdType == -1 and result.xSpdDist == 0
  now[0] = 10.5
  serv.accept_navigation_snapshot(parse_naver_navigation_v1(bump_frame(sequence=2), now[0]))
  assert tick(30).desiredSource != 'bump'  # Same safety revision cannot rearm it.
  serv.accept_navigation_snapshot(parse_naver_navigation_v1(bump_frame(distance=20, sequence=3, revision=2), now[0]))
  assert tick(30).desiredSource == 'bump'


def test_unchanged_bump_expires_while_guidance_heartbeat_keeps_apn(drive):
  serv, CS, now, tick = drive
  serv.accept_navigation_snapshot(parse_naver_navigation_v1(bump_frame(), now[0]))
  assert tick().desiredSource == 'bump'
  now[0] = 11.
  serv.accept_navigation_snapshot(parse_naver_navigation_v1(bump_frame(sequence=2), now[0]))
  now[0] = 12.01
  serv.accept_navigation_snapshot(parse_naver_navigation_v1(bump_frame(sequence=3), now[0]))
  result = tick()
  assert result.naviOwner == 'naver_v1' and result.activeCarrot >= 2
  assert result.desiredSource != 'bump' and result.xSpdType == -1


@pytest.mark.parametrize('provider', ['naver', 'tmap'])
def test_bump_keeps_shared_gas_override_and_brake_reset(drive, provider):
  serv, CS, now, tick = drive
  if provider == 'naver':
    serv.accept_navigation_snapshot(parse_naver_navigation_v1(bump_frame(), now[0]))
  else:
    serv.update({'nRoadLimitSpeed': 30, 'nSdiType': 22, 'nSdiDist': 30, 'roadcate': 8})
  assert tick().desiredSpeed == 25
  CS.gasPressed = True
  assert (tick().desiredSource, tick().desiredSpeed) == ('gas', 50)
  CS.gasPressed = False
  assert tick().desiredSource == 'gas'
  CS.brakePressed = True
  assert (tick().desiredSource, tick().desiredSpeed) == ('bump', 25)


@pytest.mark.parametrize('distance', [0, -1, float('nan'), float('inf')])
def test_invalid_bump_distance_never_becomes_a_new_control_item(drive, distance):
  serv, CS, now, tick = drive
  with pytest.raises(NaverProtocolError):
    serv.accept_navigation_snapshot(parse_naver_navigation_v1(bump_frame(distance=distance), now[0]))
  assert tick().desiredSource != 'bump'


@pytest.mark.parametrize('mode', [0, 1])
def test_naver_bump_does_not_bypass_disabled_bump_setting(drive, mode):
  serv, CS, now, tick = drive
  serv.params.values['AutoNaviSpeedCtrlMode'] = str(mode)
  serv.accept_navigation_snapshot(parse_naver_navigation_v1(bump_frame(), now[0]))
  assert tick().desiredSource != 'bump'
