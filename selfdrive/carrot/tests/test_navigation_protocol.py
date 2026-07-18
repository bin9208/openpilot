import json
from dataclasses import dataclass, replace
import subprocess
import sys

import pytest

from selfdrive.carrot.navigation_protocol import (
  MAX_FRAME_BYTES,
  MAX_ROUTE_POINTS,
  ManeuverKind,
  NavigationMux,
  NavigationStatus,
  ProtocolError,
  ProviderSource,
  ReceivedFrame,
  Transport,
  TransportContext,
  parse_canonical,
)


TMAP_SESSION = "11111111-1111-4111-8111-111111111111"
NAVER_SESSION = "22222222-2222-4222-8222-222222222222"
NEXT_SESSION = "33333333-3333-4333-8333-333333333333"
TMAP_CONTEXT = TransportContext(transport=Transport.TCP, peer="192.0.2.10")
NAVER_CONTEXT = TransportContext(transport=Transport.TCP, peer="192.0.2.20")


@dataclass(frozen=True, slots=True)
class WireSpec:
  source: str = "tmap"
  session_id: str = TMAP_SESSION
  sequence: int = 1
  timestamp_ms: int = 1_000
  active: bool = True
  ttl_ms: int = 1_000
  status: str = "guiding"
  maneuver: str = "left"
  distance_m: float = 120.0
  remaining_distance_m: float = 5_000.0
  remaining_time_s: float = 600.0
  road_limit_kph: float = 80.0
  longitude: float = 127.01
  latitude: float = 37.51
  route: tuple[tuple[float, float], ...] | None = ((127.01, 37.51), (127.02, 37.52))


BASE = WireSpec()


def canonical(spec: WireSpec = BASE) -> bytes:
  state = {
    "status": spec.status,
    "maneuver": spec.maneuver,
    "maneuver_distance_m": spec.distance_m,
    "remaining_distance_m": spec.remaining_distance_m,
    "remaining_time_s": spec.remaining_time_s,
    "road_limit_kph": spec.road_limit_kph,
    "position": {"longitude": spec.longitude, "latitude": spec.latitude},
  }
  payload = {
    "source": spec.source,
    "schema_version": 1,
    "session_id": spec.session_id,
    "sequence": spec.sequence,
    "timestamp_ms": spec.timestamp_ms,
    "navigation_active": spec.active,
    "ttl_ms": spec.ttl_ms,
    "state": state,
  }
  if spec.route is not None:
    payload["route"] = [{"longitude": lon, "latitude": lat} for lon, lat in spec.route]
  return json.dumps(payload, allow_nan=True, separators=(",", ":")).encode()


def legacy(*, state: bool, route: tuple[tuple[float, float], ...] | None = None, turn_type: int = 12) -> bytes:
  payload = {"timestamp_ms": 1_000}
  if state:
    payload["rgdata"] = {
      "nRoadLimitSpeed": 80,
      "nTBTTurnType": turn_type,
      "nTBTDist": 120,
      "nGoPosDist": 5_000,
      "nGoPosTime": 600,
      "vpPosPointLon": 127.01,
      "vpPosPointLat": 37.51,
    }
  if route is not None:
    payload["vrtx"] = [{"x": lon, "y": lat} for lon, lat in route]
  return json.dumps(payload, separators=(",", ":")).encode()


def received(data: bytes, at: float, context: TransportContext = TMAP_CONTEXT) -> ReceivedFrame:
  return ReceivedFrame(data=data, context=context, received_at=at)


def test_lease_imports_before_public_mux_reexport() -> None:
  code = "import selfdrive.carrot.navigation_lease; from selfdrive.carrot.navigation_protocol import NavigationMux"
  result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=5, check=False)

  assert result.returncode == 0, result.stderr


def test_canonical_frame_parses_to_typed_schema_v1() -> None:
  envelope = parse_canonical(canonical())

  assert envelope.source is ProviderSource.TMAP
  assert envelope.state.status is NavigationStatus.GUIDING
  assert envelope.state.maneuver is ManeuverKind.LEFT
  assert envelope.route is not None and envelope.route[0].longitude == 127.01


def test_source_less_legacy_frame_uses_peer_session_and_fixed_lease() -> None:
  mux = NavigationMux()

  mux.ingest(received(legacy(state=True, route=((127.01, 37.51),)), 4.0))
  envelope = mux.current

  assert envelope is not None
  assert envelope.source is ProviderSource.TMAP_LEGACY
  assert envelope.ttl_ms == 4_000
  assert envelope.route is not None and envelope.route[0].latitude == 37.51


def test_route_before_state_is_bounded_and_attached_without_activation() -> None:
  mux = NavigationMux()

  assert mux.ingest(received(legacy(state=False, route=((127.01, 37.51),)), 0.0)) == ()
  assert mux.current is None
  mux.ingest(received(legacy(state=True), 0.1))

  assert mux.current is not None and mux.current.route is not None


def test_route_buffer_expires_before_late_legacy_state() -> None:
  mux = NavigationMux()
  mux.ingest(received(legacy(state=False, route=((127.01, 37.51),)), 0.0))

  mux.ingest(received(legacy(state=True), 1.001))

  assert mux.current is not None and mux.current.route is None


def test_owner_is_stable_until_another_session_newly_starts_guidance() -> None:
  mux = NavigationMux()
  naver = replace(BASE, source="naver", session_id=TMAP_SESSION)

  mux.ingest(received(canonical(), 0.0))
  mux.ingest(received(canonical(naver), 0.1, NAVER_CONTEXT))
  events = mux.ingest(received(canonical(replace(BASE, sequence=2, timestamp_ms=2_000)), 0.2))

  assert events == ()
  assert mux.current is not None and mux.current.source is ProviderSource.NAVER


def test_route_less_heartbeat_retains_the_session_route() -> None:
  mux = NavigationMux()
  mux.ingest(received(canonical(), 0.0))

  mux.ingest(received(canonical(replace(BASE, sequence=2, timestamp_ms=2_000, route=None)), 0.1))

  assert mux.current is not None and mux.current.route is not None


def test_tmap_to_naver_timeout_returns_to_fresh_tmap() -> None:
  mux = NavigationMux()
  tmap = replace(BASE, ttl_ms=2_000)
  naver = replace(BASE, source="naver", session_id=NAVER_SESSION, ttl_ms=250)

  mux.ingest(received(canonical(tmap), 0.0))
  mux.ingest(received(canonical(naver), 0.1, NAVER_CONTEXT))
  events = mux.expire(0.351)

  assert [(event.previous, event.current) for event in events] == [(ProviderSource.NAVER, ProviderSource.TMAP)]


@pytest.mark.parametrize("status", ["stopped", "arrived"])
def test_stop_or_arrival_releases_owner_to_fresh_candidate(status: str) -> None:
  mux = NavigationMux()
  naver = replace(BASE, source="naver", session_id=NAVER_SESSION)
  terminal = replace(naver, sequence=2, timestamp_ms=2_000, active=False, status=status,
                     maneuver="arrive" if status == "arrived" else "straight", route=None)
  mux.ingest(received(canonical(), 0.0))
  mux.ingest(received(canonical(naver), 0.1, NAVER_CONTEXT))

  mux.ingest(received(canonical(terminal), 0.2, NAVER_CONTEXT))

  assert mux.current is not None and mux.current.source is ProviderSource.TMAP


@pytest.mark.parametrize(
  ("sequence", "timestamp_ms", "code"),
  [(2, 2_000, "sequence"), (1, 3_000, "sequence"), (3, 1_999, "timestamp")],
)
def test_duplicate_out_of_order_and_replay_are_rejected(sequence: int, timestamp_ms: int, code: str) -> None:
  mux = NavigationMux()
  baseline = replace(BASE, sequence=2, timestamp_ms=2_000)
  mux.ingest(received(canonical(baseline), 0.0))

  with pytest.raises(ProtocolError, match=code):
    mux.ingest(received(canonical(replace(BASE, sequence=sequence, timestamp_ms=timestamp_ms)), 0.1))

  assert mux.current is not None and mux.current.sequence == 2


def test_retired_cross_session_frames_cannot_reclaim_a_source() -> None:
  mux = NavigationMux()
  replacement = replace(BASE, session_id=NEXT_SESSION)
  mux.ingest(received(canonical(), 0.0))
  mux.ingest(received(canonical(replacement), 0.1))

  with pytest.raises(ProtocolError, match="session"):
    mux.ingest(received(canonical(replace(BASE, sequence=2, timestamp_ms=2_000)), 0.2))

  assert mux.current is not None and str(mux.current.session_id) == NEXT_SESSION


def test_new_non_guiding_session_cannot_replace_live_source_session() -> None:
  mux = NavigationMux()
  stopped = replace(BASE, session_id=NEXT_SESSION, active=False, status="stopped", maneuver="straight", route=None)
  mux.ingest(received(canonical(), 0.0))

  with pytest.raises(ProtocolError, match="session"):
    mux.ingest(received(canonical(stopped), 0.1))


def test_legacy_session_identity_is_stable_and_transport_scoped() -> None:
  tcp_mux, udp_mux = NavigationMux(), NavigationMux()
  tcp_mux.ingest(received(legacy(state=True), 0.0))
  udp_context = TransportContext(transport=Transport.UDP, peer=TMAP_CONTEXT.peer)
  udp_mux.ingest(received(legacy(state=True), 0.0, udp_context))

  assert tcp_mux.current is not None and udp_mux.current is not None
  assert tcp_mux.current.session_id != udp_mux.current.session_id


@pytest.mark.parametrize(
  "spec",
  [
    replace(BASE, source="naver", session_id=NAVER_SESSION, ttl_ms=249),
    replace(BASE, source="naver", session_id=NAVER_SESSION, ttl_ms=2_001),
    replace(BASE, distance_m=-1),
    replace(BASE, distance_m=float("nan")),
    replace(BASE, remaining_distance_m=float("inf")),
    replace(BASE, route=((37.51, 127.01),)),
    replace(BASE, maneuver="teleport"),
    replace(BASE, status="paused"),
    replace(BASE, source="unknown"),
    replace(BASE, route=((127.0, 37.0),) * (MAX_ROUTE_POINTS + 1)),
  ],
)
def test_invalid_bounds_coordinates_and_enums_fail_closed(spec: WireSpec) -> None:
  with pytest.raises(ProtocolError):
    parse_canonical(canonical(spec))


def test_oversized_or_malformed_frames_fail_before_state_changes() -> None:
  mux = NavigationMux()
  mux.ingest(received(canonical(), 0.0))
  before = mux.current

  for frame in (b"{", b"x" * (MAX_FRAME_BYTES + 1), canonical()):
    with pytest.raises(ProtocolError):
      mux.ingest(received(frame, 0.1))
    assert mux.current == before


def test_expired_session_replay_does_not_restore_state_or_route() -> None:
  mux = NavigationMux()
  short = replace(BASE, ttl_ms=250)
  mux.ingest(received(canonical(short), 0.0))
  mux.expire(0.251)

  with pytest.raises(ProtocolError, match="sequence"):
    mux.ingest(received(canonical(short), 0.3))

  assert mux.current is None


def test_fresh_guidance_reactivates_the_same_session_after_expiry() -> None:
  mux = NavigationMux()
  mux.ingest(received(canonical(replace(BASE, ttl_ms=250)), 0.0))

  events = mux.ingest(received(canonical(replace(BASE, sequence=2, timestamp_ms=2_000, ttl_ms=250)), 0.3))

  assert mux.current is not None and events[-1].current is ProviderSource.TMAP


def test_legacy_route_only_frame_never_renews_or_restores_guidance() -> None:
  mux = NavigationMux()
  mux.ingest(received(legacy(state=True), 0.0))
  mux.ingest(received(legacy(state=False, route=((127.02, 37.52),)), 3.9))

  mux.expire(4.001)
  with pytest.raises(ProtocolError, match="expired"):
    mux.ingest(received(legacy(state=False, route=((127.03, 37.53),)), 4.1))

  assert mux.current is None


def test_rejected_legacy_state_cannot_leak_through_a_later_route() -> None:
  mux = NavigationMux()
  payload = json.loads(legacy(state=True))
  payload["timestamp_ms"] = -1

  with pytest.raises(ProtocolError, match="timestamp"):
    mux.ingest(received(json.dumps(payload).encode(), 0.0))
  assert mux.ingest(received(legacy(state=False, route=((127.01, 37.51),)), 0.1)) == ()
  assert mux.current is None


def test_legacy_unknown_turn_and_wrong_coordinate_order_fail_closed() -> None:
  mux = NavigationMux()

  with pytest.raises(ProtocolError, match="maneuver"):
    mux.ingest(received(legacy(state=True, turn_type=9_999), 0.0))
  with pytest.raises(ProtocolError, match="coordinate"):
    mux.ingest(received(legacy(state=False, route=((37.51, 127.01),)), 0.0))

  assert mux.current is None


def test_navigation_active_must_match_typed_status() -> None:
  with pytest.raises(ProtocolError, match="navigation_active"):
    parse_canonical(canonical(replace(BASE, active=False)))


def test_route_buffer_has_a_fixed_context_limit() -> None:
  mux = NavigationMux()
  for index in range(9):
    context = TransportContext(transport=Transport.TCP, peer=f"192.0.2.{index + 30}")
    mux.ingest(received(legacy(state=False, route=((127.0 + index / 100, 37.0),)), index / 100, context))

  first = TransportContext(transport=Transport.TCP, peer="192.0.2.30")
  mux.ingest(received(legacy(state=True), 0.2, first))

  assert mux.current is not None and mux.current.route is None
