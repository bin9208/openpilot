import importlib

import pytest


def test_route_revision_frame_is_accepted_by_naver_input_layer():
  try:
    protocol = importlib.import_module('openpilot.selfdrive.carrot.naver_navigation_protocol')
  except ModuleNotFoundError:
    pytest.fail('Naver input layer is not yet available in the fresh upstream checkout')
  frame = {
    'schema': 'naver.navigation.v1',
    'sessionId': '019f0000-0000-7000-8000-000000000001',
    'sequence': 1, 'sentMonotonicMs': 1000, 'lifecycle': 'guiding',
    'guidance': {'current': {'present': False}, 'next': {'present': False}},
    'safety': {'present': True, 'kind': 'speed_bump', 'distanceM': 100, 'revision': 1},
    'road': {'limitValid': False, 'categoryValid': False},
    'route': {'present': True, 'revision': 3, 'remainingDistanceM': 0,
              'remainingTimeSec': 0, 'offRoute': False, 'destinationValid': False,
              'points': [[37.0, 127.0], [37.001, 127.001]]},
  }
  snapshot = protocol.parse_naver_navigation_v1(frame, 10.0)
  assert snapshot.control.route_revision == 3
  assert snapshot.control.route_points == ((37.0, 127.0), (37.001, 127.001))
  assert snapshot.control.secondary_safety.type == 22
  assert snapshot.control.secondary_safety.distance_m == 100
