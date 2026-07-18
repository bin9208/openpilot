from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Mapping
from pathlib import Path
from typing import NotRequired, TypedDict

from tools.naver_map_patch.sanitize import assert_sanitized


FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "navigation"
REQUIRED_EVENTS = {
  "arrived",
  "bump",
  "current_next_tbt",
  "destination_remaining",
  "dropout",
  "fixed_camera",
  "fork_left",
  "fork_right",
  "guiding",
  "idle",
  "mobile_camera",
  "provider_switch",
  "ramp_left",
  "ramp_right",
  "road_limit",
  "route_vertices",
  "section_camera",
  "stale",
  "turn_left",
  "turn_right",
  "u_turn",
}


class Units(TypedDict):
  coordinates: str
  distance: str
  duration: str
  speed: str


class RouteVertex(TypedDict):
  x: float
  y: float


class Route(TypedDict):
  vertices: list[RouteVertex]


class NavigationData(TypedDict, total=False):
  nGoPosDist: int
  nGoPosTime: int
  nRoadLimitSpeed: int
  nSdiBlockType: int
  nSdiPlusType: int
  nSdiType: int
  nTBTTurnType: int
  nTBTTurnTypeNext: int


class Frame(TypedDict):
  source: str
  schema_version: int
  session_id: str
  sequence: int
  sender_timestamp_ms: int
  ttl_ms: int
  navigation_active: bool
  rgdata: NavigationData
  route: NotRequired[Route]


class Expected(TypedDict):
  accepted: bool
  navigation_active: NotRequired[bool]


class FixtureRecord(TypedDict):
  fixture_group: str
  fixture_id: str
  event: str
  observed_at_ms: int
  units: Units
  frame: Frame | None
  expected: Expected


def _records() -> list[FixtureRecord]:
  records: list[FixtureRecord] = []
  for path in sorted(FIXTURE_ROOT.glob("*.jsonl")):
    for line in path.read_text(encoding="utf-8").splitlines():
      records.append(json.loads(line))
  return records


def _frame(records: Mapping[str, FixtureRecord], event: str) -> Frame:
  frame = records[event]["frame"]
  assert frame is not None
  return frame


def test_fixture_inventory_covers_every_planned_navigation_event() -> None:
  events = {str(record["event"]) for record in _records()}

  assert REQUIRED_EVENTS <= events


def test_fixtures_are_privacy_safe_and_preserve_units() -> None:
  records = _records()

  assert records
  for record in records:
    assert_sanitized(record)
    assert record["units"] == {
      "coordinates": "relative_degrees",
      "distance": "m",
      "duration": "s",
      "speed": "km/h",
    }


def test_fixture_timing_and_session_sequences_are_ordered() -> None:
  by_file: dict[str, list[int]] = defaultdict(list)
  by_session: dict[str, list[int]] = defaultdict(list)

  for record in _records():
    by_file[str(record["fixture_group"])].append(int(record["observed_at_ms"]))
    frame = record.get("frame")
    if frame is not None:
      by_session[str(frame["session_id"])].append(int(frame["sequence"]))

  assert all(times == sorted(times) for times in by_file.values())
  assert all(sequences == sorted(set(sequences)) for sequences in by_session.values())


def test_maneuver_and_safety_codes_match_carrot_contract() -> None:
  records = {str(record["event"]): record for record in _records()}

  assert _frame(records, "turn_left")["rgdata"]["nTBTTurnType"] == 12
  assert _frame(records, "turn_right")["rgdata"]["nTBTTurnType"] == 13
  assert _frame(records, "u_turn")["rgdata"]["nTBTTurnType"] == 14
  assert _frame(records, "fork_left")["rgdata"]["nTBTTurnType"] == 7
  assert _frame(records, "fork_right")["rgdata"]["nTBTTurnType"] == 6
  assert _frame(records, "ramp_left")["rgdata"]["nTBTTurnType"] == 102
  assert _frame(records, "ramp_right")["rgdata"]["nTBTTurnType"] == 101
  assert _frame(records, "fixed_camera")["rgdata"]["nSdiType"] == 1
  assert _frame(records, "mobile_camera")["rgdata"]["nSdiType"] == 7
  assert _frame(records, "section_camera")["rgdata"]["nSdiBlockType"] == 2
  assert _frame(records, "bump")["rgdata"]["nSdiPlusType"] == 22


def test_route_order_stale_age_and_dropout_expectations_are_retained() -> None:
  records = {str(record["event"]): record for record in _records()}

  assert _frame(records, "route_vertices")["route"]["vertices"] == [
    {"x": 0.0, "y": 0.0},
    {"x": 0.0002, "y": 0.0001},
    {"x": 0.0004, "y": 0.0003},
  ]
  stale = records["stale"]
  stale_frame = _frame(records, "stale")
  assert stale["observed_at_ms"] - stale_frame["sender_timestamp_ms"] > stale_frame["ttl_ms"]
  assert stale["expected"]["accepted"] is False
  assert records["dropout"]["frame"] is None
  assert records["dropout"]["expected"]["navigation_active"] is False
