import pytest

from tools.naver_ci.controller_tests.test_bump_parity import drive, bump_frame  # noqa: F401
from tools.naver_ci.controller_tests.test_camera_lifetime import camera_frame, set_hda
from openpilot.selfdrive.carrot.naver_navigation_protocol import parse_naver_navigation_v1


@pytest.mark.parametrize('provider', ['naver', 'tmap'])
@pytest.mark.parametrize('bump,new_mode', [(False, 0), (True, 1)])
def test_live_disable_invalidates_cached_sdi_without_ending_guidance(drive, provider, bump, new_mode):
  serv, CS, now, tick = drive
  set_hda(serv, CS)
  if provider == 'naver':
    frame = bump_frame() if bump else camera_frame()
    if not bump:
      frame['safety']['speedKph'] = 30
    serv.accept_navigation_snapshot(parse_naver_navigation_v1(frame, now[0]))
  else:
    serv.update({'nRoadLimitSpeed': 80, 'roadcate': 8, 'nSdiType': 22 if bump else 1,
      'nSdiDist': 30, 'nSdiSpeedLimit': 0 if bump else 30})
  assert tick().desiredSource == ('bump' if bump else 'cam')
  serv.params.values['AutoNaviSpeedCtrlMode'] = str(new_mode)
  result = tick()
  assert result.naviOwner == ('naver_v1' if provider == 'naver' else 'tmap_legacy')
  assert result.xSpdType == -1 and result.xSpdLimit == 0
  assert (result.desiredSource, result.decelProvider) == ('hda', 'hda')
