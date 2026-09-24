import json
from pathlib import Path
import queue
import socket
import subprocess
import sys
import threading

import pytest

from tools.naver_map_patch.capture_cli import (
  CHANNELS,
  CaptureAccumulator,
  CaptureBundle,
  DiagnosticFrameParser,
  OfflineCaptureError,
  build_discovery_response,
  decode_and_validate_frame,
  parse_discovery_request,
  sanitize_frame,
  _serve,
  validate_capture_paths,
)


def _frame(channel, sequence, monotonic_ns):
  return {
    "schema": "naver.diagnostic.v1",
    "channel": channel,
    "sequence": sequence,
    "monotonic_ns": monotonic_ns,
    "dropped": 0,
    "observation": {"descriptor": "Lsample/Value;", "accessors": []},
  }


def test_public_decoder_accepts_only_the_exact_privacy_safe_frame_contract():
  frame = _frame("status", 1, 10)

  assert decode_and_validate_frame(json.dumps(frame)) == frame
  assert decode_and_validate_frame(json.dumps(frame).encode("utf-8")) == frame

  extra = dict(frame, unexpected=True)
  with pytest.raises(OfflineCaptureError, match="schema"):
    decode_and_validate_frame(json.dumps(extra))


def test_public_decoder_rejects_oversized_invalid_utf8_and_duplicate_keys():
  frame = _frame("status", 1, 10)
  padded = json.dumps(frame) + (" " * 4096)
  duplicate = (
    b'{"schema":"naver.diagnostic.v1","channel":"status","channel":"lane",'
    b'"sequence":1,"monotonic_ns":10,"dropped":0,"observation":{"kind":"null"}}'
  )

  with pytest.raises(OfflineCaptureError, match="4096"):
    decode_and_validate_frame(padded)
  with pytest.raises(OfflineCaptureError, match="UTF-8"):
    decode_and_validate_frame(b"\xff")
  with pytest.raises(OfflineCaptureError, match="duplicate"):
    decode_and_validate_frame(duplicate)


def test_public_decoder_accepts_exact_utf8_byte_boundary_and_rejects_one_more():
  encoded = json.dumps(_frame("status", 1, 10), ensure_ascii=True, separators=(",", ":"))
  exact = encoded + (" " * (4096 - len(encoded.encode("utf-8"))))

  assert decode_and_validate_frame(exact)["channel"] == "status"
  with pytest.raises(OfflineCaptureError, match="4096"):
    decode_and_validate_frame(exact + " ")


@pytest.mark.parametrize("observation", [
  {"descriptor": "Lsample/안내;", "accessors": []},
  {"descriptor": "Lsample/Status;", "enum_name": "안내"},
  {
    "descriptor": "Lsample/Status;",
    "accessors": [{
      "name": "안내",
      "descriptor": "()I",
      "status": "not_allowlisted",
    }],
  },
  {
    "descriptor": "Lsample/Status;",
    "accessors": [{
      "name": "state",
      "descriptor": "()Lsample/안내;",
      "status": "not_allowlisted",
    }],
  },
])
def test_public_decoder_rejects_non_ascii_java_identifiers_and_descriptors(observation):
  frame = _frame("status", 1, 10)
  frame["observation"] = observation

  with pytest.raises(OfflineCaptureError):
    decode_and_validate_frame(json.dumps(frame, ensure_ascii=False))


def test_public_decoder_rejects_private_content_instead_of_redacting_it():
  frame = _frame("status", 1, 10)
  frame["observation"]["account_id"] = "private"

  with pytest.raises(OfflineCaptureError, match="privacy"):
    decode_and_validate_frame(json.dumps(frame))


def test_public_decoder_rejects_arbitrary_enum_text_and_identity_accessor_names():
  arbitrary_enum = _frame("status", 1, 10)
  arbitrary_enum["observation"] = {
    "descriptor": "Lsample/Status;",
    "enum_name": "Seoul Station",
  }
  identity_accessor = _frame("status", 2, 20)
  identity_accessor["observation"] = {
    "descriptor": "Lsample/Status;",
    "accessors": [{
      "name": "homeAddress",
      "descriptor": "()Ljava/lang/String;",
      "status": "not_allowlisted",
    }],
  }

  with pytest.raises(OfflineCaptureError, match="grammar"):
    decode_and_validate_frame(json.dumps(arbitrary_enum))
  with pytest.raises(OfflineCaptureError, match="privacy"):
    decode_and_validate_frame(json.dumps(identity_accessor))


def test_public_decoder_preserves_real_enum_and_java_method_identifiers():
  frame = _frame("status", 1, 10)
  frame["observation"] = {
    "descriptor": "Lsample/Status;",
    "accessors": [{
      "name": "getGuidanceState",
      "descriptor": "()Lsample/GuidanceState;",
      "value": {
        "descriptor": "Lsample/GuidanceState;",
        "enum_name": "Arrived",
      },
    }],
  }

  assert decode_and_validate_frame(json.dumps(frame)) == frame


def test_public_decoder_accepts_bounded_enum_classifier_accessors():
  frame = _frame("safety", 1, 10)
  frame["observation"] = {
    "descriptor": "Lsample/SafetyCode;",
    "enum_name": "SpeedBump",
    "accessors": [
      {
        "name": "isAllSpeedCameras",
        "descriptor": "()Z",
        "value": {"descriptor": "Ljava/lang/Boolean;", "boolean": False},
      },
      {
        "name": "isSpeedBump",
        "descriptor": "()Z",
        "value": {"descriptor": "Ljava/lang/Boolean;", "boolean": True},
      },
    ],
  }

  assert decode_and_validate_frame(json.dumps(frame)) == frame


def test_public_decoder_accepts_only_exact_mapping_outcome_scalars():
  frame = _frame("route", 1, 10)
  frame["observation"] = {
    "channel": "route",
    "result": "route_ok",
    "root_descriptor": "Lcom/naver/map/core/navigation/model/CurrentRoute;",
    "input_count": 2,
    "output_count": 2,
    "revision": 1,
    "item_present": True,
    "distance_valid": False,
    "frame_eligible": True,
  }
  assert decode_and_validate_frame(json.dumps(frame)) == frame

  for key, value in (
      ("latitude", 37.5),
      ("result", "arbitrary exception text"),
      ("root_descriptor", "not-a-descriptor"),
  ):
    malformed = json.loads(json.dumps(frame))
    malformed["observation"][key] = value
    with pytest.raises(OfflineCaptureError):
      decode_and_validate_frame(json.dumps(malformed))


def test_arbitrary_enum_and_home_accessor_never_reach_raw_capture(tmp_path):
  repository = Path(__file__).resolve().parents[3]
  raw_path = tmp_path / "raw" / "capture.jsonl"
  bundle = CaptureBundle(raw_path, tmp_path / "bundle", repository)
  arbitrary_enum = _frame("status", 1, 10)
  arbitrary_enum["observation"] = {
    "descriptor": "Lsample/Status;",
    "enum_name": "Seoul Station",
  }
  home_accessor = _frame("status", 2, 20)
  home_accessor["observation"] = {
    "descriptor": "Lsample/Status;",
    "accessors": [{
      "name": "homeAddress",
      "descriptor": "()Ljava/lang/String;",
      "status": "not_allowlisted",
    }],
  }

  assert bundle.record(arbitrary_enum) is False
  assert bundle.record(home_accessor) is False
  assert not raw_path.exists()


def test_parser_uses_public_decoder_before_any_raw_capture(tmp_path):
  repository = Path(__file__).resolve().parents[3]
  raw_path = tmp_path / "raw" / "capture.jsonl"
  bundle = CaptureBundle(raw_path, tmp_path / "bundle", repository)
  private = _frame("status", 1, 10)
  private["observation"] = {
    "descriptor": "Lsample/Status;",
    "accessors": [{
      "name": "serialNumber",
      "descriptor": "()Ljava/lang/String;",
      "status": "not_allowlisted",
    }],
  }
  parser = DiagnosticFrameParser()

  assert parser.feed(json.dumps(private).encode("utf-8") + b"\n") == []
  assert parser.rejected_frames == 1
  assert not raw_path.exists()
  assert bundle.record(private) is False
  assert not raw_path.exists()


def test_newline_parser_handles_fragmentation_and_coalescing():
  first = json.dumps(_frame("status", 1, 10)).encode()
  second = json.dumps(_frame("lane", 2, 20)).encode()
  parser = DiagnosticFrameParser()

  assert parser.feed(first[:17]) == []
  parsed = parser.feed(first[17:] + b"\n" + second + b"\n")

  assert [frame["channel"] for frame in parsed] == ["status", "lane"]
  assert parser.rejected_frames == 0


def test_oversize_and_malformed_frames_do_not_poison_next_frame():
  parser = DiagnosticFrameParser(max_frame_bytes=256)
  valid = json.dumps(_frame("route", 3, 30)).encode()

  parsed = parser.feed(
    (b"x" * 257) + b"\n" + b"{malformed}\n" + valid + b"\n"
  )

  assert parsed == [_frame("route", 3, 30)]
  assert parser.rejected_frames == 2


def test_duplicate_frame_keys_are_rejected_before_capture_schema_validation():
  parser = DiagnosticFrameParser()
  duplicate = (
    b'{"schema":"naver.diagnostic.v1","channel":"status","channel":"lane",'
    b'"sequence":1,"monotonic_ns":10,"dropped":0,"observation":{"kind":"null"}}\n'
  )

  assert parser.feed(duplicate) == []
  assert parser.rejected_frames == 1


def test_sanitizer_removes_precise_location_route_and_identity_values():
  raw = _frame("route", 7, 70)
  raw["observation"] = {
    "latitude": 37.566535,
    "longitude": 126.977969,
    "placeId": "SECRET-PLACE",
    "roadName": "PRIVATE ROAD",
    "routeGeometry": [[37.5, 126.9]],
    "exceptionMessage": "token=SECRET",
    "descriptor": "Lcom/naver/Route;",
    "accessors": [{"name": "state", "value": "ACTIVE"}],
  }

  sanitized = sanitize_frame(raw)
  encoded = json.dumps(sanitized, sort_keys=True)

  for secret in (
    "37.566535",
    "126.977969",
    "SECRET-PLACE",
    "PRIVATE ROAD",
    "37.5",
    "126.9",
    "token=SECRET",
  ):
    assert secret not in encoded
  assert sanitized["channel"] == "route"
  assert sanitized["observation"]["descriptor"] == "Lcom/naver/Route;"


def test_accumulator_reports_six_hook_completeness_and_monotonicity():
  accumulator = CaptureAccumulator()
  for sequence, channel in enumerate(CHANNELS, start=1):
    accumulator.observe(_frame(channel, sequence, sequence * 10))

  summary = accumulator.summary()

  assert summary["complete"] is True
  assert summary["missing_channels"] == []
  assert summary["monotonic"] is True
  assert summary["counts"] == {channel: 1 for channel in CHANNELS}


def test_capture_paths_must_be_separate_and_outside_repository(tmp_path):
  repository = Path(__file__).resolve().parents[3]
  raw_path = tmp_path / "raw.jsonl"
  bundle_path = tmp_path / "bundle"

  validate_capture_paths(raw_path, bundle_path, repository)
  with pytest.raises(ValueError):
    validate_capture_paths(repository / "raw.jsonl", bundle_path, repository)
  with pytest.raises(ValueError):
    validate_capture_paths(raw_path, raw_path, repository)


def test_raw_capture_and_sanitized_bundle_are_physically_separate(tmp_path):
  repository = Path(__file__).resolve().parents[3]
  raw_path = tmp_path / "raw" / "capture.jsonl"
  bundle_path = tmp_path / "sanitized-bundle"
  bundle = CaptureBundle(raw_path, bundle_path, repository)
  raw = _frame("status", 1, 10)
  raw["observation"] = {
    "descriptor": "Lsample/Status;",
    "accessors": [{
      "name": "state",
      "descriptor": "()I",
      "status": "not_allowlisted",
    }],
  }

  bundle.record(raw)
  manifest = bundle.finalize()

  assert raw_path.exists()
  assert (bundle_path / "capture.sanitized.jsonl").exists()
  assert raw_path.resolve() != (bundle_path / "capture.sanitized.jsonl").resolve()
  assert manifest["summary"]["complete"] is False
  assert set(manifest["summary"]["missing_channels"]) == set(CHANNELS) - {"status"}


def test_typed_discovery_request_has_fixed_response_contract():
  request = b'{"type":"carrot.navigation.discover","source":"naver","schema_version":1}'

  assert parse_discovery_request(request) == {
    "type": "carrot.navigation.discover",
    "source": "naver",
    "schema_version": 1,
  }
  assert json.loads(build_discovery_response()) == {
    "type": "carrot.navigation.discover.response",
    "server": "127.0.0.1",
    "port": 7712,
    "schema": 1,
    "schema_version": 1,
    "lease_ms": 2000,
  }


def test_capture_cli_direct_script_help_bootstraps_repository_imports():
  repository = Path(__file__).resolve().parents[3]
  script = repository / "tools" / "naver_map_patch" / "capture_cli.py"

  process = subprocess.run(
    [sys.executable, str(script), "--help"],
    cwd=repository,
    capture_output=True,
    text=True,
    timeout=10,
    check=False,
  )

  assert process.returncode == 0
  assert "loopback-only" in process.stdout
  assert "--discovery-server" not in process.stdout


def test_invalid_full_schema_never_reaches_raw_capture(tmp_path):
  repository = Path(__file__).resolve().parents[3]
  raw_path = tmp_path / "raw" / "capture.jsonl"
  bundle = CaptureBundle(raw_path, tmp_path / "bundle", repository)
  invalid = _frame("status", 1, 10)
  invalid["sequence"] = True

  assert bundle.record(invalid) is False
  assert not raw_path.exists()


def test_top_level_counters_use_java_long_bounds_and_non_boolean_sequence(tmp_path):
  repository = Path(__file__).resolve().parents[3]
  raw_path = tmp_path / "raw" / "capture.jsonl"
  bundle = CaptureBundle(raw_path, tmp_path / "bundle", repository)
  zero_sequence = _frame("status", 0, 10)
  too_large = _frame("status", 2**63, 10)
  valid_signed_timing = _frame("status", 1, -(2**63))
  valid_signed_timing["dropped"] = 2**63 - 1
  negative_drops = _frame("status", 2, 20)
  negative_drops["dropped"] = -1

  assert bundle.record(zero_sequence) is False
  assert bundle.record(too_large) is False
  assert bundle.record(valid_signed_timing) is True
  assert bundle.record(negative_drops) is False


def test_unknown_or_coordinate_shaped_observation_numbers_never_reach_raw_capture(tmp_path):
  repository = Path(__file__).resolve().parents[3]
  raw_path = tmp_path / "raw" / "capture.jsonl"
  bundle = CaptureBundle(raw_path, tmp_path / "bundle", repository)
  coordinate = _frame("route", 1, 10)
  coordinate["observation"] = {
    "descriptor": "Lsample/Route;",
    "latitudeE7": 37566535,
  }
  unknown_number = _frame("route", 2, 20)
  unknown_number["observation"] = {
    "descriptor": "Lsample/Route;",
    "unreviewed_numeric": 126977969,
  }

  assert bundle.record(coordinate) is False
  assert bundle.record(unknown_number) is False
  assert not raw_path.exists()


def test_sampler_structural_counts_are_the_only_observation_integers_accepted(tmp_path):
  repository = Path(__file__).resolve().parents[3]
  raw_path = tmp_path / "raw" / "capture.jsonl"
  bundle = CaptureBundle(raw_path, tmp_path / "bundle", repository)
  frame = _frame("lane", 1, 10)
  frame["observation"] = {
    "descriptor": "Lsample/Lane;",
    "collection_size": 4,
    "sampled_items": 4,
    "element_descriptors": ["Lsample/LaneItem;"] * 4,
  }

  assert bundle.record(frame) is True
  assert "collection_size" in raw_path.read_text(encoding="utf-8")


def test_observation_schema_accepts_only_sampler_union_variants(tmp_path):
  repository = Path(__file__).resolve().parents[3]
  raw_path = tmp_path / "raw" / "capture.jsonl"
  bundle = CaptureBundle(raw_path, tmp_path / "bundle", repository)
  valid_accessor_error = _frame("status", 1, 10)
  valid_accessor_error["observation"] = {
    "descriptor": "Lcom/naver/map/core/navigation/NaviStatusBroadcaster$Status;",
    "accessors": [{
      "name": "name",
      "descriptor": "()Ljava/lang/String;",
      "value": {"status": "accessor_error"},
    }],
  }
  mixed_variant = _frame("status", 2, 20)
  mixed_variant["observation"] = {
    "descriptor": "Lsample/Status;",
    "numeric_bucket": "positive_1_to_10",
    "boolean": True,
  }
  invalid_accessor_status = _frame("status", 3, 30)
  invalid_accessor_status["observation"] = {
    "descriptor": "Lsample/Status;",
    "accessors": [{
      "name": "name",
      "descriptor": "()Ljava/lang/String;",
      "status": "accessor_error",
    }],
  }
  impossible_collection = _frame("status", 4, 40)
  impossible_collection["observation"] = {
    "descriptor": "Lsample/Status;",
    "collection_size": 6,
    "sampled_items": 3,
    "element_descriptors": ["Lsample/Item;"] * 3,
  }
  false_truncation = _frame("status", 5, 50)
  false_truncation["observation"] = {
    "descriptor": "Lsample/Status;",
    "string_length": 1,
    "truncated": False,
  }
  nested_truncation = _frame("status", 6, 60)
  nested_truncation["observation"] = {
    "descriptor": "Lsample/Status;",
    "accessors": [{
      "name": "name",
      "descriptor": "()Ljava/lang/String;",
      "value": {"descriptor": "Lsample/Value;", "truncated": True},
    }],
  }

  assert bundle.record(valid_accessor_error) is True
  assert bundle.record(mixed_variant) is False
  assert bundle.record(invalid_accessor_status) is False
  assert bundle.record(impossible_collection) is False
  assert bundle.record(false_truncation) is False
  assert bundle.record(nested_truncation) is False


def test_schema_critical_text_survives_sanitization_before_raw_write(tmp_path):
  repository = Path(__file__).resolve().parents[3]
  raw_path = tmp_path / "raw" / "capture.jsonl"
  bundle = CaptureBundle(raw_path, tmp_path / "bundle", repository)
  long_descriptor = (
    "Lcom/naver/map/core/navigation/diagnostic/currentroute/activeguidance/"
    "roadcontext/lanecontext/safetycontext/turncontext/routecontext/"
    "CurrentRouteGuidanceDiagnosticSnapshot;"
  )
  frame = _frame("status", 1, 10)
  frame["observation"] = {"descriptor": long_descriptor, "accessors": []}

  assert bundle.record(frame) is True
  assert long_descriptor in raw_path.read_text(encoding="utf-8")


def test_generic_encoded_descriptor_component_is_rejected_before_raw_write(tmp_path):
  repository = Path(__file__).resolve().parents[3]
  raw_path = tmp_path / "raw" / "capture.jsonl"
  bundle = CaptureBundle(raw_path, tmp_path / "bundle", repository)
  frame = _frame("status", 1, 10)
  frame["observation"] = {
    "descriptor": f"Lcom/naver/map/core/navigation/{'a' * 64};",
    "accessors": [],
  }

  assert bundle.record(frame) is False
  assert not raw_path.exists()


def test_observation_schema_accepts_jvm_array_descriptors(tmp_path):
  repository = Path(__file__).resolve().parents[3]
  raw_path = tmp_path / "raw" / "capture.jsonl"
  bundle = CaptureBundle(raw_path, tmp_path / "bundle", repository)
  frame = _frame("lane", 1, 10)
  frame["observation"] = {
    "descriptor": "[Ljava/lang/Object;",
    "collection_size": 2,
    "sampled_items": 2,
    "element_descriptors": ["[I", "[Lcom/naver/map/core/navigation/lane/NaviLaneItem;"],
  }

  assert bundle.record(frame) is True


def test_sanitized_bundle_preserves_all_allowed_accessors_and_element_descriptors(tmp_path):
  repository = Path(__file__).resolve().parents[3]
  raw_path = tmp_path / "raw" / "capture.jsonl"
  bundle_path = tmp_path / "bundle"
  bundle = CaptureBundle(raw_path, bundle_path, repository)
  frame = _frame("lane", 1, 10)
  frame["observation"] = {
    "descriptor": "[Lcom/naver/map/core/navigation/lane/NaviLaneItem;",
    "collection_size": 4,
    "sampled_items": 4,
    "element_descriptors": ["[I"] * 4,
  }
  frame["observation"] = {
    "descriptor": "Lcom/naver/map/core/navigation/lane/NaviLaneItem;",
    "accessors": [{
      "name": f"allowed{index:02d}",
      "descriptor": "()Ljava/util/List;",
      "status": "not_allowlisted",
    } for index in range(16)],
  }
  collection = _frame("lane", 2, 20)
  collection["observation"] = {
    "descriptor": "[Lcom/naver/map/core/navigation/lane/NaviLaneItem;",
    "collection_size": 4,
    "sampled_items": 4,
    "element_descriptors": ["[I"] * 4,
  }

  assert bundle.record(frame) is True
  assert bundle.record(collection) is True
  sanitized = [json.loads(line) for line in (bundle_path / "capture.sanitized.jsonl").read_text(encoding="utf-8").splitlines()]

  assert len(sanitized[0]["observation"]["accessors"]) == 16
  assert sanitized[1]["observation"]["element_descriptors"] == ["[I"] * 4


def test_accessors_match_sampler_sensitive_name_sort_and_uniqueness_rules(tmp_path):
  repository = Path(__file__).resolve().parents[3]
  raw_path = tmp_path / "raw" / "capture.jsonl"
  bundle = CaptureBundle(raw_path, tmp_path / "bundle", repository)
  sensitive = _frame("status", 1, 10)
  sensitive["observation"] = {
    "descriptor": "Lsample/Status;",
    "accessors": [{"name": "getDeviceId", "descriptor": "()I", "status": "not_allowlisted"}],
  }
  unsorted = _frame("status", 2, 20)
  unsorted["observation"] = {
    "descriptor": "Lsample/Status;",
    "accessors": [
      {"name": "zeta", "descriptor": "()I", "status": "not_allowlisted"},
      {"name": "alpha", "descriptor": "()I", "status": "not_allowlisted"},
    ],
  }
  duplicate = _frame("status", 3, 30)
  duplicate["observation"] = {
    "descriptor": "Lsample/Status;",
    "accessors": [
      {"name": "alpha", "descriptor": "()I", "status": "not_allowlisted"},
      {"name": "alpha", "descriptor": "()I", "status": "not_allowlisted"},
    ],
  }

  assert bundle.record(sensitive) is False
  assert bundle.record(unsorted) is False
  assert bundle.record(duplicate) is False


def test_all_six_real_root_descriptors_reach_the_sanitized_capture(tmp_path):
  repository = Path(__file__).resolve().parents[3]
  bundle_path = tmp_path / "bundle"
  bundle = CaptureBundle(tmp_path / "raw.jsonl", bundle_path, repository)
  descriptors = {
    "status": "Lcom/naver/map/core/navigation/NaviStatusBroadcaster$Status;",
    "tbt_current": "Lcom/naver/map/core/auto/model/AutoTbtItem;",
    "tbt_next": "Lcom/naver/map/core/auto/model/AutoTbtItem;",
    "safety": "Lcom/naver/map/core/common/model/SafeControlItem;",
    "route": "Lcom/naver/map/core/navigation/model/CurrentRoute;",
    "lane": "Lcom/naver/map/core/navigation/lane/NaviLaneItem;",
  }
  for sequence, (channel, descriptor) in enumerate(descriptors.items(), start=1):
    frame = _frame(channel, sequence, sequence * 10)
    frame["observation"] = {"descriptor": descriptor, "accessors": []}
    assert bundle.record(frame) is True

  assert bundle.finalize()["summary"]["complete"] is True
  assert len((bundle_path / "capture.sanitized.jsonl").read_text(encoding="utf-8").splitlines()) == 6


def test_capture_server_closes_active_clients_before_duration_finalizes(tmp_path):
  repository = Path(__file__).resolve().parents[3]
  bundle = CaptureBundle(tmp_path / "raw.jsonl", tmp_path / "bundle", repository)
  result = []
  ready = queue.Queue()
  worker = threading.Thread(target=lambda: result.append(_serve(
    bundle,
    0.5,
    server_address=("127.0.0.1", 0),
    discovery_server_address=("127.0.0.1", 0),
    server_ready=ready.put,
  )))
  worker.start()
  client = socket.create_connection(ready.get(timeout=1), timeout=1)
  try:
    client.sendall(json.dumps(_frame("status", 1, 10)).encode() + b"\n")
    worker.join(timeout=2)

    assert not worker.is_alive()
    assert result == [0]
    before = bundle.raw_path.read_text(encoding="utf-8")
    client.settimeout(1)
    assert client.recv(1) == b""
    assert bundle.raw_path.read_text(encoding="utf-8") == before
  finally:
    client.close()
    worker.join(timeout=1)


def test_shutdown_closes_connection_that_pauses_before_active_registration(tmp_path):
  repository = Path(__file__).resolve().parents[3]
  bundle = CaptureBundle(tmp_path / "raw.jsonl", tmp_path / "bundle", repository)
  entered = threading.Event()
  release = threading.Event()
  shutdown_started = threading.Event()
  allow_snapshot = threading.Event()
  ready = queue.Queue()
  result = []
  worker = threading.Thread(target=lambda: result.append(_serve(
    bundle,
    0.5,
    on_handler_started=lambda: (entered.set(), release.wait()),
    before_shutdown_snapshot=lambda: (shutdown_started.set(), allow_snapshot.wait()),
    server_address=("127.0.0.1", 0),
    discovery_server_address=("127.0.0.1", 0),
    server_ready=ready.put,
  )))
  worker.start()
  client = socket.create_connection(ready.get(timeout=1), timeout=1)
  try:
    assert entered.wait(1)
    assert shutdown_started.wait(1)
    allow_snapshot.set()
    client.settimeout(1)
    try:
      peer_data = client.recv(1)
    except TimeoutError:
      pytest.fail("shutdown did not close the accepted connection before handler registration")
    assert peer_data == b""
  finally:
    allow_snapshot.set()
    release.set()
    client.close()
    worker.join(timeout=1)
  assert not worker.is_alive()
  assert result == [0]
