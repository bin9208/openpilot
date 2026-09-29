"""Road-display validity must not change shared control or camera semantics."""
import pytest

from tools.naver_ci.controller_tests.test_bump_parity import drive  # noqa: F401
from openpilot.selfdrive.carrot.naver_navigation_protocol import parse_naver_navigation_v1
from openpilot.selfdrive.carrot.tests.test_naver_navigation_protocol import inactive_frame
from openpilot.selfdrive.carrot import carrot_serv


def frame(sequence=1, road_limit=None, camera=False):
  value = inactive_frame('idle')
  value.update(lifecycle='guiding', sequence=sequence)
  if road_limit is not None:
    value['road'] = {'limitValid': True, 'limitKph': road_limit, 'categoryValid': False}
  if camera:
    value['safety'] = {'present': True, 'kind': 'fixed_camera', 'distanceM': 10.,
      'speedKph': 60, 'revision': 1}
  return value


@pytest.mark.parametrize('previous_limit', [30, 80])
def test_naver_unknown_road_does_not_publish_initial_or_previous_limit(drive, previous_limit):
  serv, CS, now, tick = drive
  serv.nRoadLimitSpeed = previous_limit
  serv.accept_navigation_snapshot(parse_naver_navigation_v1(frame(), now[0]))
  result = tick()
  assert result.naviOwner == 'naver_v1'
  assert result.nRoadLimitSpeed == 0
  assert serv.nRoadLimitSpeed == previous_limit  # Display only: do not reset control/gas state.


def test_naver_road_validity_can_clear_and_recover_without_reusing_old_limit(drive):
  serv, CS, now, tick = drive
  for sequence, limit, expected in [(1, 60, 60), (2, None, 0), (3, 80, 80)]:
    now[0] += .1
    serv.accept_navigation_snapshot(parse_naver_navigation_v1(frame(sequence, limit), now[0]))
    assert tick().nRoadLimitSpeed == expected


def test_unknown_naver_road_does_not_hide_or_reclassify_a_valid_camera(drive):
  serv, CS, now, tick = drive
  serv.accept_navigation_snapshot(parse_naver_navigation_v1(frame(camera=True), now[0]))
  result = tick()
  assert result.nRoadLimitSpeed == 0
  assert (result.xSpdLimit, result.desiredSource, result.decelProvider) == (60, 'cam', 'naver_v1')


def test_tmap_road_limit_publication_is_unchanged(drive):
  serv, CS, now, tick = drive
  for _ in range(6):  # Preserve the existing Tmap six-sample road-limit debounce.
    serv.update({'nRoadLimitSpeed': 80, 'roadcate': 8})
  assert tick().nRoadLimitSpeed == 80


@pytest.mark.parametrize('road_limit,expected', [(None, 0), (60, 60)])
@pytest.mark.parametrize('camera', [False, True])
@pytest.mark.parametrize('previous_limit', [30, 80])
def test_cluster_instruction_uses_same_road_validity_as_carrot_message(drive, monkeypatch, road_limit, expected, camera, previous_limit):
  serv, CS, now, tick = drive
  serv.nRoadLimitSpeed = previous_limit
  messages = {}
  original_new_message = carrot_serv.messaging.new_message
  def capture_message(service, *args, **kwargs):
    message = original_new_message(service, *args, **kwargs)
    messages[service] = message
    return message
  monkeypatch.setattr(carrot_serv.messaging, 'new_message', capture_message)
  serv.accept_navigation_snapshot(parse_naver_navigation_v1(frame(road_limit=road_limit, camera=camera), now[0]))
  result = tick()
  instruction = messages['navInstructionCarrot']
  assert instruction.valid
  assert result.nRoadLimitSpeed == expected
  assert instruction.navInstructionCarrot.speedLimit == pytest.approx(expected / 3.6)
