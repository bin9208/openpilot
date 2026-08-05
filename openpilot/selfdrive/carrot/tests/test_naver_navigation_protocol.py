from copy import deepcopy
from dataclasses import FrozenInstanceError
import math
import random

import pytest

from openpilot.selfdrive.carrot.naver_navigation_protocol import (
  MAX_ROUTE_POINTS,
  MAX_SEQUENCE,
  NaverProtocolError,
  parse_naver_navigation_v1,
)
from openpilot.selfdrive.carrot.navigation_sources import (
  NavigationLifecycle,
  NavigationSnapshot,
  NavigationSource,
  NavigationSourceStore,
  choose_safety,
)


SESSION_ID = "019f0000-0000-7000-8000-000000000001"
MANEUVERS = {
  "straight": 0,
  "left": 12,
  "right": 13,
  "u_turn": 14,
  "fork_left": 7,
  "fork_right": 6,
  "ramp_left": 102,
  "ramp_right": 101,
  "roundabout": 131,
  "arrive": 201,
  "slight_left": 1000,
  "slight_right": 1001,
}


def valid_frame(*, session_id: str = SESSION_ID, sequence: int = 42) -> dict:
  return {
    "schema": "naver.navigation.v1",
    "sessionId": session_id,
    "sequence": sequence,
    "sentMonotonicMs": 123456,
    "lifecycle": "guiding",
    "guidance": {
      "current": {
        "present": True,
        "maneuver": "left",
        "distanceM": 380,
        "roadName": "도로",
        "mainText": "좌회전",
      },
      "next": {
        "present": True,
        "maneuver": "right",
        "distanceM": 900,
        "roadName": "다음 도로",
        "mainText": "우회전",
      },
    },
    "safety": {
      "present": True,
      "kind": "fixed_camera",
      "distanceM": 420,
      "speedKph": 60,
    },
    "road": {
      "limitValid": True,
      "limitKph": 80,
      "categoryValid": True,
      "category": 8,
    },
    "route": {
      "present": True,
      "remainingDistanceM": 12500,
      "remainingTimeSec": 1320,
      "offRoute": False,
      "destinationValid": True,
      "destinationLatitude": 37.5,
      "destinationLongitude": 127.1,
    },
  }


def inactive_frame(lifecycle: str) -> dict:
  frame = valid_frame()
  frame["lifecycle"] = lifecycle
  frame["guidance"] = {"current": {"present": False}, "next": {"present": False}}
  frame["safety"] = {"present": False}
  frame["road"] = {"limitValid": False, "categoryValid": False}
  frame["route"] = {"present": False}
  return frame


def assert_protocol_error(payload: object, received_mono_s: object = 10.0) -> NaverProtocolError:
  with pytest.raises(NaverProtocolError) as exc_info:
    parse_naver_navigation_v1(payload, received_mono_s)  # type: ignore[arg-type]
  error = exc_info.value
  assert len(error.code) <= 32
  assert len(error.message) <= 96
  assert len(str(error)) <= 132
  return error


def test_parses_complete_naver_frame_without_synthetic_defaults():
  parsed = parse_naver_navigation_v1(valid_frame(sequence=7), received_mono_s=10.0)

  assert parsed.source is NavigationSource.NAVER_V1
  assert parsed.session_id == SESSION_ID
  assert parsed.sequence == 7
  assert parsed.lifecycle is NavigationLifecycle.GUIDING
  assert parsed.received_mono_s == 10.0
  assert parsed.activation_epoch == 0
  assert parsed.control.current.present
  assert parsed.control.current.turn_type == 12
  assert parsed.control.current.received_mono_s == 10.0
  assert parsed.control.next.turn_type == 13
  assert parsed.control.next.received_mono_s == 10.0
  assert parsed.control.safety is not None
  assert parsed.control.safety.type == 1
  assert parsed.control.safety.distance_m == 420.0
  assert parsed.control.safety.speed_limit_kph == 60.0
  assert parsed.control.safety.received_mono_s == 10.0
  assert not parsed.control.safety.section
  assert parsed.control.secondary_safety is None
  assert parsed.control.road_limit_kph == 80.0
  assert parsed.control.road_limit_received_mono_s == 10.0
  assert parsed.control.road_category == 8
  assert parsed.control.road_category_received_mono_s == 10.0
  assert parsed.control.route_present
  assert parsed.control.route_received_mono_s == 10.0
  assert parsed.control.remaining_distance_m == 12500.0
  assert parsed.control.remaining_time_s == 1320.0
  assert not parsed.control.off_route
  assert parsed.control.destination == (37.5, 127.1)
  assert parsed.control.route_points == ()


def test_destination_survives_parser_store_projection_with_actual_receipt():
  parsed = parse_naver_navigation_v1(valid_frame(sequence=8), received_mono_s=10.0)
  assert parsed.control.destination_present
  assert parsed.control.destination_received_mono_s == 10.0

  store = NavigationSourceStore()
  assert store.accept(parsed, 10.0)
  selection = store.select(11.999)
  assert selection.snapshot is not None
  assert selection.snapshot.control.destination_present
  assert selection.snapshot.control.destination == (37.5, 127.1)


def test_safety_revision_prevents_heartbeat_rewind_expires_and_falls_back_to_hda():
  store = NavigationSourceStore()
  first = valid_frame(sequence=1)
  first["safety"]["revision"] = 1
  assert store.accept(parse_naver_navigation_v1(first, 10.0), 10.0)

  heartbeat = valid_frame(sequence=2)
  heartbeat["guidance"]["current"]["distanceM"] = 200
  heartbeat["safety"]["revision"] = 1
  assert store.accept(parse_naver_navigation_v1(heartbeat, 10.5), 10.5)

  live = store.select(11.0)
  assert live.snapshot is not None
  assert live.snapshot.control.safety is not None
  assert live.snapshot.control.safety.distance_m == 420.0
  assert live.safety_age_s == pytest.approx(1.0)

  stale = store.select(12.0)
  assert stale.snapshot is not None
  assert stale.snapshot.source is NavigationSource.NAVER_V1
  assert stale.snapshot.control.safety is None
  fallback = choose_safety(stale, hda_limit_kph=50.0, hda_distance_m=150.0)
  assert fallback is not None
  assert (fallback.provider, fallback.rejection) == ("hda", "safety_stale")

  changed = valid_frame(sequence=3)
  changed["safety"]["revision"] = 2
  changed["safety"]["distanceM"] = 300
  assert store.accept(parse_naver_navigation_v1(changed, 12.1), 12.1)
  assert store.select(12.1).snapshot.control.safety.distance_m == 300.0

  absent = valid_frame(sequence=4)
  absent["safety"] = {"present": False}
  assert store.accept(parse_naver_navigation_v1(absent, 12.2), 12.2)
  assert store.select(12.2).snapshot.control.safety is None


@pytest.mark.parametrize("revision", [0, -1, MAX_SEQUENCE + 1, 1.5, True])
def test_rejects_invalid_safety_revision_without_accepting_extra_safety_keys(revision):
  frame = valid_frame()
  frame["safety"]["revision"] = revision

  assert assert_protocol_error(frame).code == "safety"


@pytest.mark.parametrize("schema", ["naver.navigation.v2", "", 1, True, None])
def test_rejects_unknown_or_wrong_type_schema(schema):
  frame = valid_frame()
  frame["schema"] = schema
  assert_protocol_error(frame)


@pytest.mark.parametrize(("maneuver", "turn_type"), MANEUVERS.items())
def test_maps_every_supported_maneuver_exactly(maneuver, turn_type):
  frame = valid_frame()
  frame["guidance"]["current"]["maneuver"] = maneuver
  parsed = parse_naver_navigation_v1(frame, 1.0)
  assert parsed.control.current.turn_type == turn_type


@pytest.mark.parametrize("maneuver", ["", "LEFT", "teleport", 12, True, None])
def test_rejects_unknown_or_wrong_type_maneuvers(maneuver):
  frame = valid_frame()
  frame["guidance"]["current"]["maneuver"] = maneuver
  assert_protocol_error(frame)


@pytest.mark.parametrize(("kind", "expected_type", "section"), [
  ("fixed_camera", 1, False),
  ("mobile_camera", 7, False),
  ("section_camera", 2, True),
])
def test_maps_camera_safety_kinds_to_primary_items(kind, expected_type, section):
  frame = valid_frame()
  frame["safety"]["kind"] = kind
  parsed = parse_naver_navigation_v1(frame, 2.0)
  assert parsed.control.safety is not None
  assert parsed.control.safety.type == expected_type
  assert parsed.control.safety.section is section
  assert parsed.control.safety.reason == ("section" if section else "cam")
  assert parsed.control.secondary_safety is None


def test_maps_bump_to_secondary_type_22_without_synthetic_positive_speed():
  frame = valid_frame()
  frame["safety"] = {"present": True, "kind": "speed_bump", "distanceM": 75}

  parsed = parse_naver_navigation_v1(frame, 2.0)

  assert parsed.control.safety is None
  assert parsed.control.secondary_safety is not None
  assert parsed.control.secondary_safety.type == 22
  assert parsed.control.secondary_safety.reason == "bump"
  assert parsed.control.secondary_safety.speed_limit_kph == 0.0
  assert parsed.control.secondary_safety.received_mono_s == 2.0


@pytest.mark.parametrize("kind", ["camera", "bump", "fixed", "", 1, True, None])
def test_rejects_unknown_or_wrong_type_safety_kinds(kind):
  frame = valid_frame()
  frame["safety"]["kind"] = kind
  assert_protocol_error(frame)


@pytest.mark.parametrize("lifecycle", ["idle", "guiding", "stopped", "arrived"])
def test_accepts_every_lifecycle_with_consistent_control(lifecycle):
  frame = valid_frame() if lifecycle == "guiding" else inactive_frame(lifecycle)
  parsed = parse_naver_navigation_v1(frame, 3.0)
  assert parsed.lifecycle is NavigationLifecycle(lifecycle)
  if lifecycle != "guiding":
    assert not parsed.control.current.present
    assert not parsed.control.next.present
    assert parsed.control.safety is None
    assert parsed.control.secondary_safety is None
    assert parsed.control.road_limit_kph is None
    assert parsed.control.road_category is None
    assert not parsed.control.route_present


@pytest.mark.parametrize("lifecycle", ["idle", "stopped", "arrived"])
@pytest.mark.parametrize("control_item", ["current", "next", "safety", "road_limit", "road_category", "route"])
def test_inactive_lifecycle_rejects_present_or_valid_control_items(lifecycle, control_item):
  frame = inactive_frame(lifecycle)
  if control_item in ("current", "next"):
    frame["guidance"][control_item] = deepcopy(valid_frame()["guidance"][control_item])
  elif control_item == "safety":
    frame["safety"] = deepcopy(valid_frame()["safety"])
  elif control_item == "road_limit":
    frame["road"].update(limitValid=True, limitKph=80)
  elif control_item == "road_category":
    frame["road"].update(categoryValid=True, category=0)
  else:
    frame["route"] = deepcopy(valid_frame()["route"])
  assert_protocol_error(frame)


@pytest.mark.parametrize("lifecycle", ["paused", "GUIDING", "", 1, True, None])
def test_rejects_unknown_or_wrong_type_lifecycle(lifecycle):
  frame = valid_frame()
  frame["lifecycle"] = lifecycle
  assert_protocol_error(frame)


def test_terminal_snapshot_can_feed_store_tombstone():
  store = NavigationSourceStore()
  active = parse_naver_navigation_v1(valid_frame(sequence=1), 1.0)
  stopped = parse_naver_navigation_v1(inactive_frame("stopped") | {"sequence": 2}, 1.1)

  assert store.accept(active, 1.0)
  assert store.accept(stopped, 1.1)
  assert store.select(1.1).snapshot is None
  assert not store.accept(parse_naver_navigation_v1(valid_frame(sequence=3), 1.2), 1.2)


def test_canonicalizes_uuid_and_uses_only_local_receipt_time_for_freshness():
  frame = valid_frame(session_id=SESSION_ID.upper())
  frame["sentMonotonicMs"] = 9_007_199_254_740_991
  parsed = parse_naver_navigation_v1(frame, 0.25)

  assert parsed.session_id == SESSION_ID
  assert parsed.received_mono_s == 0.25
  assert parsed.control.current.received_mono_s == 0.25
  assert parsed.control.safety.received_mono_s == 0.25
  assert parsed.control.route_received_mono_s == 0.25


@pytest.mark.parametrize("session_id", [
  "",
  "not-a-uuid",
  "019f0000-0000-7000-8000-00000000000z",
  "{" + SESSION_ID + "}",
  SESSION_ID + "-suffix",
  1,
  True,
  None,
])
def test_rejects_invalid_or_noncanonical_uuid_inputs(session_id):
  frame = valid_frame()
  frame["sessionId"] = session_id
  assert_protocol_error(frame)


@pytest.mark.parametrize("sequence", [0, 1, (1 << 63) - 1])
def test_accepts_java_long_compatible_sequence_range(sequence):
  parsed = parse_naver_navigation_v1(valid_frame(sequence=sequence), 1.0)
  assert parsed.sequence == sequence


@pytest.mark.parametrize("sequence", [True, False, -1, 1 << 63, (1 << 64) - 1, 1.0, "1", None])
def test_rejects_sequence_outside_java_long_compatible_range(sequence):
  error = assert_protocol_error(valid_frame(sequence=sequence))
  assert error.code == "sequence"


def test_named_sequence_maximum_and_error_describe_signed_63_bit_positive_range():
  assert MAX_SEQUENCE == (1 << 63) - 1
  error = assert_protocol_error(valid_frame(sequence=1 << 63))
  assert error.message == "sequence is outside the signed 63-bit positive range"


@pytest.mark.parametrize("sent_ms", [0, 1, 1.5, 9_007_199_254_740_991])
def test_accepts_nonnegative_finite_phone_monotonic_metadata(sent_ms):
  frame = valid_frame()
  frame["sentMonotonicMs"] = sent_ms
  assert parse_naver_navigation_v1(frame, 1.0).received_mono_s == 1.0


@pytest.mark.parametrize("sent_ms", [True, False, -1, math.nan, math.inf, -math.inf, "1", None, 10**500])
def test_rejects_invalid_phone_monotonic_metadata_without_numeric_exceptions(sent_ms):
  frame = valid_frame()
  frame["sentMonotonicMs"] = sent_ms
  assert_protocol_error(frame)


@pytest.mark.parametrize("received_mono_s", [True, False, -0.001, math.nan, math.inf, -math.inf, "1", None, 10**500])
def test_rejects_invalid_local_receipt_clock(received_mono_s):
  assert_protocol_error(valid_frame(), received_mono_s)


ROOT_KEYS = frozenset((
  "schema", "sessionId", "sequence", "sentMonotonicMs", "lifecycle",
  "guidance", "safety", "road", "route",
))


@pytest.mark.parametrize("missing_key", sorted(ROOT_KEYS))
def test_rejects_every_missing_root_key(missing_key):
  frame = valid_frame()
  del frame[missing_key]
  assert_protocol_error(frame)


def test_rejects_unknown_root_key():
  frame = valid_frame()
  frame["provider"] = "naver"
  assert_protocol_error(frame)


_NESTED_SCHEMA_NAMES = (
  "guidance",
  "current_present",
  "current_absent",
  "next_present",
  "next_absent",
  "safety_camera",
  "safety_bump",
  "safety_absent",
  "road_both",
  "road_base",
  "road_limit",
  "road_category",
  "route_destination",
  "route_destination_points",
  "route_no_destination",
  "route_no_destination_points",
  "route_absent",
)


def _nested_schema_case(name: str) -> tuple[dict, tuple[str, ...]]:
  frame = valid_frame()
  path: tuple[str, ...]
  if name == "guidance":
    path = ("guidance",)
  elif name in ("current_present", "current_absent"):
    path = ("guidance", "current")
    if name == "current_absent":
      frame["guidance"]["current"] = {"present": False}
  elif name in ("next_present", "next_absent"):
    path = ("guidance", "next")
    if name == "next_absent":
      frame["guidance"]["next"] = {"present": False}
  elif name.startswith("safety_"):
    path = ("safety",)
    if name == "safety_bump":
      frame["safety"] = {"present": True, "kind": "speed_bump", "distanceM": 75}
    elif name == "safety_absent":
      frame["safety"] = {"present": False}
  elif name.startswith("road_"):
    path = ("road",)
    if name == "road_base":
      frame["road"] = {"limitValid": False, "categoryValid": False}
    elif name == "road_limit":
      frame["road"] = {"limitValid": True, "limitKph": 80, "categoryValid": False}
    elif name == "road_category":
      frame["road"] = {"limitValid": False, "categoryValid": True, "category": 0}
  else:
    path = ("route",)
    if "no_destination" in name:
      frame["route"]["destinationValid"] = False
      del frame["route"]["destinationLatitude"]
      del frame["route"]["destinationLongitude"]
    if name.endswith("points"):
      frame["route"]["points"] = [[37.5, 127.1]]
    if name == "route_absent":
      frame["route"] = {"present": False}
  return frame, path


@pytest.mark.parametrize("schema_name", _NESTED_SCHEMA_NAMES)
def test_rejects_every_missing_required_key_in_each_nested_exact_schema(schema_name):
  frame, path = _nested_schema_case(schema_name)
  target = _nested_target(frame, path)
  for missing_key in (key for key in tuple(target) if key != "points"):
    bad = deepcopy(frame)
    del _nested_target(bad, path)[missing_key]
    assert_protocol_error(bad)
  if "points" in target:
    without_optional_points = deepcopy(frame)
    del _nested_target(without_optional_points, path)["points"]
    assert parse_naver_navigation_v1(without_optional_points, 10.0).control.route_points == ()


@pytest.mark.parametrize("schema_name", _NESTED_SCHEMA_NAMES)
def test_rejects_extra_key_in_each_nested_exact_schema(schema_name):
  frame, path = _nested_schema_case(schema_name)
  _nested_target(frame, path)["unexpected"] = "must-not-be-ignored"
  assert_protocol_error(frame)


@pytest.mark.parametrize(("item_path", "contradictory_key", "contradictory_value"), [
  (("guidance", "current"), "maneuver", "left"),
  (("guidance", "next"), "distanceM", 100),
  (("safety",), "kind", "fixed_camera"),
  (("route",), "remainingDistanceM", 100),
])
def test_present_false_rejects_all_contradictory_item_values(item_path, contradictory_key, contradictory_value):
  frame = valid_frame()
  item = frame
  for key in item_path:
    item = item[key]
  item.clear()
  item.update(present=False)
  item[contradictory_key] = contradictory_value
  assert_protocol_error(frame)


def test_present_false_items_remain_absent_and_invalid():
  frame = valid_frame()
  frame["guidance"] = {"current": {"present": False}, "next": {"present": False}}
  frame["safety"] = {"present": False}
  frame["route"] = {"present": False}

  parsed = parse_naver_navigation_v1(frame, 4.0)

  assert not parsed.control.current.present
  assert parsed.control.current.turn_type == -1
  assert parsed.control.current.received_mono_s is None
  assert not parsed.control.next.present
  assert parsed.control.safety is None
  assert parsed.control.secondary_safety is None
  assert not parsed.control.route_present
  assert parsed.control.route_received_mono_s is None
  assert parsed.control.destination is None
  assert parsed.control.route_points == ()


@pytest.mark.parametrize("path", [
  ("guidance", "current", "present"),
  ("guidance", "next", "present"),
  ("safety", "present"),
  ("route", "present"),
])
@pytest.mark.parametrize("value", [0, 1, "true", None])
def test_present_flags_require_exact_booleans(path, value):
  frame = valid_frame()
  target = frame
  for key in path[:-1]:
    target = target[key]
  target[path[-1]] = value
  assert_protocol_error(frame)


@pytest.mark.parametrize(("road", "limit", "category"), [
  ({"limitValid": False, "categoryValid": False}, None, None),
  ({"limitValid": True, "limitKph": 0, "categoryValid": False}, 0.0, None),
  ({"limitValid": False, "categoryValid": True, "category": 0}, None, 0),
  ({"limitValid": True, "limitKph": 250, "categoryValid": True, "category": 0}, 250.0, 0),
])
def test_road_validity_distinguishes_invalid_from_explicit_zero(road, limit, category):
  frame = valid_frame()
  frame["road"] = road
  parsed = parse_naver_navigation_v1(frame, 5.0)
  assert parsed.control.road_limit_kph == limit
  assert parsed.control.road_category == category
  assert parsed.control.road_limit_received_mono_s == (5.0 if limit is not None else None)
  assert parsed.control.road_category_received_mono_s == (5.0 if category is not None else None)


@pytest.mark.parametrize("road", [
  {"limitValid": False, "limitKph": 0, "categoryValid": False},
  {"limitValid": False, "categoryValid": False, "category": 0},
  {"limitValid": True, "categoryValid": False},
  {"limitValid": False, "categoryValid": True},
  {"limitValid": 1, "limitKph": 80, "categoryValid": False},
  {"limitValid": False, "categoryValid": 0, "category": 0},
])
def test_rejects_contradictory_or_incomplete_road_validity(road):
  frame = valid_frame()
  frame["road"] = road
  assert_protocol_error(frame)


def test_destination_coordinates_are_forbidden_when_invalid():
  frame = valid_frame()
  frame["route"].update(destinationValid=False)
  del frame["route"]["destinationLatitude"]
  del frame["route"]["destinationLongitude"]
  assert parse_naver_navigation_v1(frame, 1.0).control.destination is None

  frame["route"]["destinationLatitude"] = 37.5
  assert_protocol_error(frame)


@pytest.mark.parametrize("missing", ["destinationLatitude", "destinationLongitude"])
def test_destination_coordinates_are_required_as_a_pair_when_valid(missing):
  frame = valid_frame()
  del frame["route"][missing]
  assert_protocol_error(frame)


@pytest.mark.parametrize(("key", "value"), [
  ("destinationLatitude", -90.0001),
  ("destinationLatitude", 90.0001),
  ("destinationLongitude", -180.0001),
  ("destinationLongitude", 180.0001),
  ("destinationLatitude", True),
  ("destinationLongitude", math.nan),
])
def test_rejects_invalid_destination_coordinates(key, value):
  frame = valid_frame()
  frame["route"][key] = value
  assert_protocol_error(frame)


def test_route_points_are_bounded_immutable_and_do_not_mutate_input():
  frame = valid_frame()
  frame["route"]["points"] = [[37.5, 127.1], [-90, -180], [90, 180]]
  original = deepcopy(frame)

  parsed = parse_naver_navigation_v1(frame, 6.0)

  assert parsed.control.route_points == ((37.5, 127.1), (-90.0, -180.0), (90.0, 180.0))
  assert isinstance(parsed.control.route_points, tuple)
  assert all(isinstance(point, tuple) for point in parsed.control.route_points)
  assert frame == original
  with pytest.raises(FrozenInstanceError):
    parsed.control.route_points = ()


def test_accepts_maximum_route_point_count():
  frame = valid_frame()
  frame["route"]["points"] = [[0, 0] for _ in range(MAX_ROUTE_POINTS)]
  assert len(parse_naver_navigation_v1(frame, 1.0).control.route_points) == MAX_ROUTE_POINTS


@pytest.mark.parametrize("points", [
  [[0, 0] for _ in range(MAX_ROUTE_POINTS + 1)],
  None,
  {},
  [1, 2],
  [[0]],
  [[0, 0, 0]],
  [[True, 0]],
  [[0, math.inf]],
  [[-90.001, 0]],
  [[0, 180.001]],
  [{"latitude": 0, "longitude": 0}],
  [(0, 0)],
])
def test_rejects_overflow_or_invalid_route_points(points):
  frame = valid_frame()
  frame["route"]["points"] = points
  assert_protocol_error(frame)


@pytest.mark.parametrize(("path", "value"), [
  (("guidance", "current", "distanceM"), -0.001),
  (("guidance", "current", "distanceM"), 2_000_000.001),
  (("guidance", "current", "distanceM"), True),
  (("guidance", "current", "distanceM"), math.nan),
  (("safety", "distanceM"), 0),
  (("safety", "distanceM"), 2_000_000.001),
  (("safety", "speedKph"), 0),
  (("safety", "speedKph"), 250.001),
  (("safety", "speedKph"), True),
  (("road", "limitKph"), -0.001),
  (("road", "limitKph"), 250.001),
  (("road", "category"), -1),
  (("road", "category"), 2**31),
  (("road", "category"), True),
  (("route", "remainingDistanceM"), -0.001),
  (("route", "remainingDistanceM"), 2_000_000.001),
  (("route", "remainingTimeSec"), -0.001),
  (("route", "remainingTimeSec"), 604_800.001),
  (("route", "offRoute"), 0),
  (("route", "destinationValid"), 1),
])
def test_rejects_boolean_nonfinite_or_out_of_range_control_values(path, value):
  frame = valid_frame()
  target = frame
  for key in path[:-1]:
    target = target[key]
  target[path[-1]] = value
  assert_protocol_error(frame)


@pytest.mark.parametrize("field", ["roadName", "mainText"])
def test_guidance_text_is_bounded_without_coercion(field):
  frame = valid_frame()
  frame["guidance"]["current"][field] = "가" * 256
  assert parse_naver_navigation_v1(frame, 1.0).control.current.present

  frame["guidance"]["current"][field] = "가" * 257
  assert_protocol_error(frame)
  frame["guidance"]["current"][field] = 123
  assert_protocol_error(frame)


def test_error_text_is_bounded_and_never_echoes_payload_or_arbitrary_values():
  secrets = [
    "019f9999-9999-7999-8999-secret-session",
    "37.55555,127.11111 secret-location",
    "X" * 1000,
  ]
  frames = []
  for secret in secrets:
    frame = valid_frame()
    frame["sessionId"] = secret
    frame["guidance"]["current"]["roadName"] = secret
    frames.append(frame)

  for frame, secret in zip(frames, secrets, strict=True):
    error = assert_protocol_error(frame)
    assert secret not in error.code
    assert secret not in error.message
    assert secret not in str(error)
    assert repr(frame) not in str(error)

  direct = NaverProtocolError(secrets[0], secrets[1])
  assert direct.code == "protocol"
  assert secrets[0] not in str(direct)
  assert secrets[1] not in str(direct)
  assert len(str(direct)) <= 132


@pytest.mark.parametrize(("section", "expected_code"), [
  ("guidance", "guidance"),
  ("safety", "safety"),
  ("route", "route"),
])
def test_nested_validation_errors_never_echo_secret_after_valid_session(section, expected_code):
  secret = "비밀-location-session-value-" + ("X" * 300)
  frame = valid_frame()
  if section == "guidance":
    frame["guidance"]["current"]["roadName"] = secret
  elif section == "safety":
    frame["safety"]["kind"] = secret
  else:
    frame["route"]["points"] = [[secret, 127.1]]

  error = assert_protocol_error(frame)

  assert frame["sessionId"] == SESSION_ID
  assert error.code == expected_code
  assert secret not in error.code
  assert secret not in error.message
  assert secret not in str(error)


def test_parser_failure_leaves_existing_source_store_unchanged():
  store = NavigationSourceStore()
  original = parse_naver_navigation_v1(valid_frame(sequence=1), 1.0)
  assert store.accept(original, 1.0)
  before = store.select(1.0)

  bad = valid_frame(sequence=2)
  bad["safety"]["distanceM"] = math.nan
  assert_protocol_error(bad, 1.1)

  after = store.select(1.1)
  assert after.snapshot == before.snapshot
  assert after.projection_revision == before.projection_revision


_FUZZ_FAMILIES = (
  "valid",
  "schema",
  "identity",
  "sequence",
  "phone_clock",
  "local_clock",
  "lifecycle",
  "guidance_current",
  "guidance_next",
  "safety",
  "road",
  "route",
  "destination",
  "points",
  "nested_key_delete",
  "nested_key_add",
)
_NESTED_FUZZ_PATHS = (
  ("guidance",),
  ("guidance", "current"),
  ("guidance", "next"),
  ("safety",),
  ("road",),
  ("route",),
)


def _fuzz_value(rng: random.Random):
  return deepcopy(rng.choice((
    None,
    True,
    False,
    -1,
    0,
    1,
    1.5,
    1 << 63,
    10**100,
    math.nan,
    math.inf,
    -math.inf,
    "",
    "x",
    "도로",
    "가" * 256,
    "X" * 257,
    [],
    [0, 0],
    [[37.5, 127.1]],
    {},
    {"x": 1},
  )))


def _nested_target(frame: dict, path: tuple[str, ...]) -> dict:
  target = frame
  for key in path:
    target = target[key]
  return target


def _mutate_valid_envelope(rng: random.Random, index: int) -> tuple[str, dict, object]:
  family = _FUZZ_FAMILIES[index % len(_FUZZ_FAMILIES)]
  frame = deepcopy(valid_frame(sequence=index))
  received_mono_s: object = 1.0

  if family == "valid":
    frame["guidance"]["current"]["roadName"] = rng.choice(("", "도로", "가" * 256))
    frame["route"]["points"] = deepcopy(rng.choice(([], [[37.5, 127.1]], [[-90, -180], [90, 180]])))
  elif family == "schema":
    frame["schema"] = rng.choice(("naver.navigation.v1", "", "naver.navigation.v2", 1, True))
  elif family == "identity":
    frame["sessionId"] = rng.choice((SESSION_ID, SESSION_ID.upper(), "", "not-a-uuid", "X" * 65, 1, True))
  elif family == "sequence":
    frame["sequence"] = rng.choice((0, 1, MAX_SEQUENCE, MAX_SEQUENCE + 1, -1, 1.5, True, 10**100))
  elif family == "phone_clock":
    frame["sentMonotonicMs"] = rng.choice((0, 1.5, (1 << 64) - 1, 1 << 64, -1, math.nan, math.inf, True))
  elif family == "local_clock":
    received_mono_s = rng.choice((0, 1.5, -1, math.nan, math.inf, True, "1", 10**100))
  elif family == "lifecycle":
    frame["lifecycle"] = rng.choice(("guiding", "idle", "stopped", "arrived", "paused", "", 1, True))
  elif family in ("guidance_current", "guidance_next"):
    item_name = "current" if family == "guidance_current" else "next"
    item = frame["guidance"][item_name]
    item[rng.choice(tuple(item))] = _fuzz_value(rng)
  elif family == "safety":
    frame["safety"][rng.choice(tuple(frame["safety"]))] = _fuzz_value(rng)
  elif family == "road":
    frame["road"][rng.choice(tuple(frame["road"]))] = _fuzz_value(rng)
  elif family == "route":
    route_fields = ("present", "remainingDistanceM", "remainingTimeSec", "offRoute", "destinationValid")
    frame["route"][rng.choice(route_fields)] = _fuzz_value(rng)
  elif family == "destination":
    key = rng.choice(("destinationLatitude", "destinationLongitude"))
    frame["route"][key] = _fuzz_value(rng)
  elif family == "points":
    frame["route"]["points"] = _fuzz_value(rng)
  elif family == "nested_key_delete":
    target = _nested_target(frame, rng.choice(_NESTED_FUZZ_PATHS))
    del target[rng.choice(tuple(target))]
  elif family == "nested_key_add":
    target = _nested_target(frame, rng.choice(_NESTED_FUZZ_PATHS))
    target["fuzzExtra"] = _fuzz_value(rng)
  return family, frame, received_mono_s


def test_ten_thousand_deterministic_valid_envelope_mutations_reach_all_parser_paths():
  rng = random.Random(0x6805)
  exercised = {family: 0 for family in _FUZZ_FAMILIES}
  errors = {family: set() for family in _FUZZ_FAMILIES}
  successes = 0
  nested_failures = 0

  for index in range(10_000):
    family, payload, received_mono_s = _mutate_valid_envelope(rng, index)
    exercised[family] += 1
    try:
      snapshot = parse_naver_navigation_v1(payload, received_mono_s)
    except NaverProtocolError as error:
      errors[family].add(error.code)
      if family.startswith("nested_key_"):
        nested_failures += 1
      assert len(str(error)) <= 132
    else:
      successes += 1
      assert isinstance(snapshot, NavigationSnapshot)
      assert snapshot.source is NavigationSource.NAVER_V1

  assert set(exercised) == set(_FUZZ_FAMILIES)
  assert all(count > 0 for count in exercised.values())
  assert successes > 0
  assert nested_failures > 0
  assert errors["schema"] == {"schema"}
  assert errors["identity"] == {"session_id"}
  assert errors["sequence"] == {"sequence"}
  assert errors["phone_clock"] == {"sent_monotonic_ms"}
  assert errors["local_clock"] == {"received_mono_s"}
  assert errors["lifecycle"] <= {"lifecycle", "lifecycle_consistency"}
  assert errors["lifecycle"]
  for family in ("guidance_current", "guidance_next"):
    assert errors[family] == {"guidance"}
  assert errors["safety"] == {"safety"}
  assert errors["road"] == {"road"}
  for family in ("route", "destination", "points"):
    assert errors[family] == {"route"}


def test_cyclic_nested_direct_object_raises_bounded_error_without_recursion_or_repr():
  frame = valid_frame()
  cycle = []
  cycle.append(cycle)
  frame["route"]["points"] = cycle

  error = assert_protocol_error(frame)

  assert error.code == "route"
  assert len(str(error)) <= 132
