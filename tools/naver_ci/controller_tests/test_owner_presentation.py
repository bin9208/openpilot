import pytest

from openpilot.selfdrive.carrot.deceleration_source import navigation_status_presentation, deceleration_source_presentation


@pytest.mark.parametrize('owner,label', [('naver_v1', 'NAVER'), ('tmap_legacy', 'TMAP'), ('carrot_navi_v2', 'NAVI')])
def test_owner_survives_transport_loss_and_empty_trip(owner, label):
  assert navigation_status_presentation(True, False, owner, 'guiding') == (label, 4)


@pytest.mark.parametrize('lifecycle', ['stopped', 'arrived', 'idle'])
def test_terminal_or_expired_owner_does_not_fall_back_to_stale_socket(lifecycle):
  assert navigation_status_presentation(False, True, '', lifecycle) is None
  assert navigation_status_presentation(True, True, '', lifecycle) == ('vNAVI', 3)


def test_safety_provider_is_separate_from_owner():
  assert navigation_status_presentation(True, False, 'naver_v1', 'guiding') == ('NAVER', 4)
  assert deceleration_source_presentation('hda', 'hda') == ('HDA cam', 3)
  assert deceleration_source_presentation('bump', 'naver_v1') == ('N bump', 4)
  assert deceleration_source_presentation('route', 'tmap_legacy') == ('T route', 4)
  assert navigation_status_presentation(False, True) == ('NAVI', 4)
