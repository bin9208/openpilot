"""The common route slice must never exceed the requested forward horizon."""
import pytest

from openpilot.selfdrive.carrot.carrot_man import get_path_after_distance, haversine


@pytest.mark.parametrize("latitude", [37.0, 37.005])
def test_long_first_segment_is_clipped_without_a_backwards_next_segment(latitude):
  points = [(127., 37.), (127., 37.01), (127.01, 37.01)]
  path, index, start = get_path_after_distance(0, points, (127., latitude), 300.)
  assert index == 0
  assert start == pytest.approx((127., latitude))
  assert len(path) == 2
  assert path[1][0] == 127.
  assert latitude < path[1][1] < 37.01
  assert haversine(*path[0], *path[1]) == pytest.approx(300., abs=.01)


def test_single_long_segment_is_also_clipped():
  path, _, _ = get_path_after_distance(0, [(127., 37.), (127., 37.01)], (127., 37.), 300.)
  assert haversine(*path[0], *path[-1]) == pytest.approx(300., abs=.01)


def test_first_segment_exact_boundary_does_not_append_duplicate_endpoint():
  points = [(127., 37.), (127., 37.001), (127.001, 37.001)]
  horizon = haversine(*points[0], *points[1])
  path, _, _ = get_path_after_distance(0, points, points[0], horizon)
  assert path == points[:2]


def test_short_segments_continue_forward_to_horizon():
  points = [(127., 37.), (127., 37.001), (127., 37.002), (127., 37.01)]
  path, _, _ = get_path_after_distance(0, points, points[0], 300.)
  assert path[:3] == points[:3]
  assert 37.002 < path[-1][1] < 37.01
  assert sum(haversine(*a, *b) for a, b in zip(path, path[1:])) == pytest.approx(300., abs=.01)
