from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from tools.naver_map_patch.sanitize import (
  ArtifactKind,
  JsonValue,
  SanitizationRejected,
  assert_sanitized,
  sanitize_json_text,
  scan_bytes,
  scan_capture,
)


def _private_shaped_capture() -> str:
  capture = {
    "peer_ip": "192.0.2.44",
    "deviceId": "android-device-7f3a",
    "session_id": "550e8400-e29b-41d4-a716-446655440000",
    "api_key": "synthetic_test_key_material_1234567890",
    "currentLatitude": 35.123456,
    "currentLongitude": 127.654321,
    "source": "naver",
    "sequence": 7,
    "sender_timestamp_ms": 1200,
    "ttl_ms": 1000,
    "navigation_active": True,
    "rgdata": {
      "szGoalName": "\uc0c8\ub85c\uc6b4 \ubaa9\uc801\uc9c0",
      "szPosRoadName": "\ud14c\uc2a4\ud2b8\ub85c 12\uae38",
      "nTBTTurnType": 12,
      "nTBTDist": 420,
      "nTBTTurnTypeNext": 13,
      "nTBTDistNext": 780,
      "nSdiType": 1,
      "nSdiSpeedLimit": 60,
      "nSdiDist": 310,
      "nGoPosDist": 4200,
      "nGoPosTime": 540,
      "vpPosPointLat": 35.123456,
      "vpPosPointLon": 127.654321,
    },
    "route": {
      "vertices": [
        {"x": 127.654321, "y": 35.123456},
        {"x": 127.654521, "y": 35.123556},
      ],
    },
  }
  return json.dumps(capture, ensure_ascii=False)


def test_sanitizer_preserves_navigation_semantics_when_capture_is_private_shaped() -> None:
  sanitized = json.loads(sanitize_json_text(_private_shaped_capture()))

  assert sanitized["session_id"] == "fixture-session-0001"
  assert sanitized["deviceId"] == "fixture-identity-0001"
  assert sanitized["peer_ip"] == "peer-redacted"
  assert "api_key" not in sanitized
  assert sanitized["currentLatitude"] == 0.0
  assert sanitized["currentLongitude"] == 0.0
  assert sanitized["sequence"] == 7
  assert sanitized["sender_timestamp_ms"] == 1200
  assert sanitized["ttl_ms"] == 1000
  assert sanitized["rgdata"]["nTBTTurnType"] == 12
  assert sanitized["rgdata"]["nTBTTurnTypeNext"] == 13
  assert sanitized["rgdata"]["nSdiType"] == 1
  assert sanitized["rgdata"]["nSdiDist"] == 310
  assert sanitized["rgdata"]["nGoPosTime"] == 540
  assert sanitized["route"]["vertices"] == [
    {"x": 0.0, "y": 0.0},
    {"x": 0.0002, "y": 0.0001},
  ]
  assert_sanitized(sanitized)


@pytest.mark.parametrize(
  ("payload", "kind"),
  [
    ({"latitude": 35.123456, "longitude": 127.654321}, ArtifactKind.COORDINATE),
    ({"name": "\uc2e0\uaddc \uc8fc\uc18c"}, ArtifactKind.KOREAN_TEXT),
    ({"peer": "198.51.100.9"}, ArtifactKind.IP_ADDRESS),
    ({"peer": ".".join(("10", "0", "0", "8"))}, ArtifactKind.IP_ADDRESS),
    ({"peer": ".".join(("8", "8", "8", "8"))}, ArtifactKind.IP_ADDRESS),
    ({"peer": "2001:db8::1"}, ArtifactKind.IP_ADDRESS),
    ({"session_id": "550e8400-e29b-41d4-a716-446655440000"}, ArtifactKind.SESSION_ID),
    ({"access_token": "synthetic_but_forbidden"}, ArtifactKind.KEY_MATERIAL),
    ({"blob": "sk-" + "x" * 20}, ArtifactKind.KEY_MATERIAL),
    ({"opaque": "AKIAIOSFODNN7EXAMPLE"}, ArtifactKind.KEY_MATERIAL),
    ({"currentLatitude": 35.123456, "currentLongitude": 127.654321}, ArtifactKind.COORDINATE),
    ({"xCoord": 127.654321, "yCoord": 35.123456}, ArtifactKind.COORDINATE),
    ({"blob": "UEsDBBQAAAAI"}, ArtifactKind.APK_BYTES),
    ({"blob": "ZGV4CjAzNQ"}, ArtifactKind.APK_BYTES),
    ({"blob": "6465780a303335"}, ArtifactKind.APK_BYTES),
    ({"blob": "504b0304"}, ArtifactKind.APK_BYTES),
    ({"blob": "UEsDBA=="}, ArtifactKind.APK_BYTES),
  ],
)
def test_scanner_rejects_unsanitized_capture_values(payload: JsonValue, kind: ArtifactKind) -> None:
  with pytest.raises(SanitizationRejected) as error:
    assert_sanitized(payload)

  assert kind in {finding.kind for finding in error.value.findings}


@pytest.mark.parametrize("key", ["deviceId", "android_id", "serial", "hardwareSerial"])
def test_scanner_rejects_explicit_device_identity_fields(key: str) -> None:
  # Given: an independently invented device identity field.
  payload: JsonValue = {key: "android-device-7f3a"}

  # When: the capture crosses the privacy boundary.
  findings = scan_capture(payload)

  # Then: identity has its own typed rejection category.
  assert "device_identity" in {finding.kind.value for finding in findings}


def test_scanner_keeps_navigation_identifiers_and_safe_identity_aliases() -> None:
  # Given: navigation semantics plus the documented synthetic identity form.
  payload: JsonValue = {
    "fixture_id": "maneuver-turn-left",
    "source": "naver",
    "sequence": 7,
    "deviceId": "fixture-identity-0001",
  }

  # When: the synthetic fixture is scanned.
  findings = scan_capture(payload)

  # Then: no navigation identifier is over-redacted.
  assert findings == ()


@pytest.mark.parametrize("build_id", [
  "naver-6.8.0.5-diagnostic-offline-v3",
  "naver-6.8.0.5-field-acceptance-v1",
  "naver-6.8.0.5-field-acceptance-v2",
  "naver-6.8.0.5-field-acceptance-v3",
  "naver-6.8.0.5-field-acceptance-v4",
])
def test_scanner_allows_only_exact_public_payload_build_ids_at_the_root_key(
    build_id: str,
) -> None:
  payload: JsonValue = {
    "payload_build_id": build_id,
  }

  assert scan_capture(payload) == ()


@pytest.mark.parametrize("build_ids", [
  ["naver-6.8.0.5-diagnostic-offline-v3"],
  ["naver-6.8.0.5-field-acceptance-v1"],
  ["naver-6.8.0.5-field-acceptance-v2"],
  ["naver-6.8.0.5-field-acceptance-v3"],
  ["naver-6.8.0.5-field-acceptance-v4"],
  [
    "naver-6.8.0.5-diagnostic-offline-v3",
    "naver-6.8.0.5-field-acceptance-v1",
  ],
  [
    "naver-6.8.0.5-diagnostic-offline-v3",
    "naver-6.8.0.5-field-acceptance-v3",
  ],
  [
    "naver-6.8.0.5-diagnostic-offline-v3",
    "naver-6.8.0.5-field-acceptance-v4",
  ],
  [
    "naver-6.8.0.5-diagnostic-offline-v3",
    "naver-6.8.0.5-field-acceptance-v1",
    "naver-6.8.0.5-field-acceptance-v2",
    "naver-6.8.0.5-field-acceptance-v3",
    "naver-6.8.0.5-field-acceptance-v4",
  ],
])
def test_scanner_allows_exact_run_build_id_sets_only_at_the_root_output_path(
    build_ids: list[str],
) -> None:
  assert scan_capture({"run_payload_build_ids": build_ids}) == ()


@pytest.mark.parametrize(
  "payload",
  [
    {"other": "naver-6.8.0.5-diagnostic-offline-v3"},
    {"nested": {"payload_build_id": "naver-6.8.0.5-diagnostic-offline-v3"}},
  ],
  ids=["other-path", "nested-path"],
)
def test_approved_build_id_at_any_other_path_is_still_ip_detected(
    payload: JsonValue,
) -> None:
  findings = scan_capture(payload)

  assert ArtifactKind.IP_ADDRESS in {finding.kind for finding in findings}


@pytest.mark.parametrize(
  "payload",
  [
    {"other": ["naver-6.8.0.5-diagnostic-offline-v3"]},
    {"nested": {"run_payload_build_ids": ["naver-6.8.0.5-diagnostic-offline-v3"]}},
    {"run_payload_build_ids": [
      "naver-6.8.0.5-field-acceptance-v1",
      "naver-6.8.0.5-diagnostic-offline-v3",
    ]},
    {"run_payload_build_ids": [
      "naver-6.8.0.5-diagnostic-offline-v3",
      "naver-6.8.0.5-field-acceptance-v1",
      "naver-6.8.0.5-field-acceptance-v1",
    ]},
    {"run_payload_build_ids": ["naver-6.8.0.6-diagnostic-offline-v3"]},
  ],
  ids=["other-path", "nested-path", "wrong-order", "extra-index", "unknown-build"],
)
def test_run_build_id_exemption_rejects_wrong_paths_positions_and_values(
    payload: JsonValue,
) -> None:
  findings = scan_capture(payload)

  assert ArtifactKind.IP_ADDRESS in {finding.kind for finding in findings}


@pytest.mark.parametrize(
  "value",
  [
    "naver-6.8.0.5-diagnostic-offline-v1",
    "naver-6.8.0.6-diagnostic-offline-v3",
    "naver-6.8.1.5-diagnostic-offline-v3",
    "release-1.2.3.4",
    "arbitrary-198.51.100.9-build",
  ],
  ids=["old-build", "one-character-mutation", "different-version", "arbitrary-dotted", "public-ip"],
)
def test_other_dotted_values_at_payload_build_key_receive_no_exemption(
    value: str,
) -> None:
  findings = scan_capture({"payload_build_id": value})

  assert ArtifactKind.IP_ADDRESS in {finding.kind for finding in findings}


@pytest.mark.parametrize(
  "value",
  [
    "sk-" + "x" * 20,
    "Bearer synthetic-forbidden-token",
    "AKIAIOSFODNN7EXAMPLE",
  ],
  ids=["sk-token", "bearer-token", "access-key"],
)
def test_payload_build_key_still_rejects_explicit_secret_material(
    value: str,
) -> None:
  findings = scan_capture({"payload_build_id": value})

  assert ArtifactKind.KEY_MATERIAL in {finding.kind for finding in findings}


@pytest.mark.parametrize(
  "value",
  [
    "a" * 64,
    "aZ0bY1cX2dW3eV4fU5gT6hS7iR8jQ9kP0mN1oL2pK3",
  ],
  ids=["generic-hex", "generic-base64-like"],
)
def test_payload_build_key_still_rejects_generic_encoded_material(
    value: str,
) -> None:
  findings = scan_capture({"payload_build_id": value})

  assert ArtifactKind.KEY_MATERIAL in {finding.kind for finding in findings}


def test_scanner_accepts_jvm_descriptors_only_at_diagnostic_descriptor_paths() -> None:
  payload: JsonValue = {
    "schema": "naver.diagnostic.v1",
    "channel": "status",
    "observation": {
      "descriptor": "Lcom/naver/map/core/navigation/NaviStatusBroadcaster$Status;",
      "accessors": [{
        "name": "name",
        "descriptor": "()Ljava/lang/String;",
        "value": {
          "descriptor": "[Lcom/naver/map/core/navigation/model/TbtDataItem;",
          "collection_size": 1,
          "sampled_items": 1,
          "element_descriptors": ["[I"],
        },
      }, {
        "name": "combined",
        "descriptor": (
          "([Ljava/lang/String;"
          "Lcom/naver/map/core/auto/model/AutoTbtItem;)"
          "[Lcom/naver/map/core/navigation/lane/NaviLaneItem;"
        ),
        "value": None,
      }],
    },
  }

  assert_sanitized(payload)


@pytest.mark.parametrize(
  "encoded_component",
  [
    "a" * 64,
    "aZ0bY1cX2dW3eV4fU5gT6hS7iR8jQ9kP0mN1oL2pK3",
  ],
  ids=["hex-64", "base64-style-40-plus"],
)
@pytest.mark.parametrize("descriptor_path", ["root", "accessor-return", "element"])
def test_scanner_rejects_generic_encoded_material_inside_jvm_descriptor_components(
    encoded_component: str, descriptor_path: str,
) -> None:
  if descriptor_path == "root":
    payload: JsonValue = {
      "observation": {"descriptor": f"Lfoo/{encoded_component};"},
    }
  elif descriptor_path == "accessor-return":
    payload = {
      "observation": {
        "descriptor": "Lcom/naver/map/core/navigation/NaviStatusBroadcaster$Status;",
        "accessors": [{
          "name": "combined",
          "descriptor": (
            "([Ljava/lang/String;"
            "Lcom/naver/map/core/auto/model/AutoTbtItem;)"
            f"Lfoo/{encoded_component};"
          ),
          "value": None,
        }],
      },
    }
  else:
    payload = {
      "observation": {
        "descriptor": "[Ljava/lang/Object;",
        "collection_size": 1,
        "sampled_items": 1,
        "element_descriptors": [f"[Lfoo/{encoded_component};"],
      },
    }

  with pytest.raises(SanitizationRejected) as error:
    assert_sanitized(payload)

  assert ArtifactKind.KEY_MATERIAL in {finding.kind for finding in error.value.findings}


def test_scanner_still_rejects_explicit_secret_at_a_descriptor_path() -> None:
  payload: JsonValue = {
    "observation": {"descriptor": "Lfoo/AKIAIOSFODNN7EXAMPLE;"},
  }

  with pytest.raises(SanitizationRejected) as error:
    assert_sanitized(payload)

  assert ArtifactKind.KEY_MATERIAL in {finding.kind for finding in error.value.findings}


def test_accessor_shape_descriptor_path_exempts_only_generic_encoded_secret() -> None:
  payload: JsonValue = {
    "schema": "naver.diagnostic.accessor-shapes.v1",
    "channels": {
      "status": [{
        "shape": {
          "descriptor": (
            "Lcom/naver/map/core/navigation/"
            "NaviStatusBroadcaster$Status$Stopped;"
          ),
        },
        "occurrences": 1,
      }],
    },
  }

  assert scan_capture(payload) == ()


@pytest.mark.parametrize(
  ("descriptor", "kind"),
  [
    ("Lfoo/AKIAIOSFODNN7EXAMPLE;", ArtifactKind.KEY_MATERIAL),
    ("Lfoo/198.51.100.9;", ArtifactKind.IP_ADDRESS),
    ("Lfoo/UEsDBBQAAAAI;", ArtifactKind.APK_BYTES),
  ],
)
def test_accessor_shape_descriptor_exemption_keeps_specific_rejections(
    descriptor: str, kind: ArtifactKind,
) -> None:
  payload: JsonValue = {
    "channels": {
      "status": [{
        "shape": {"descriptor": descriptor},
        "occurrences": 1,
      }],
    },
  }

  assert kind in {finding.kind for finding in scan_capture(payload)}


@pytest.mark.parametrize(
  ("descriptor", "kind"),
  [
    ("Lfoo/AKIAIOSFODNN7EXAMPLE;", ArtifactKind.KEY_MATERIAL),
    ("Lfoo/198.51.100.9;", ArtifactKind.IP_ADDRESS),
    ("Lfoo/UEsDBBQAAAAI;", ArtifactKind.APK_BYTES),
  ],
)
def test_descriptor_path_exemption_keeps_non_generic_privacy_rejections(
    descriptor: str, kind: ArtifactKind,
) -> None:
  findings = scan_capture({"observation": {"descriptor": descriptor}})

  assert kind in {finding.kind for finding in findings}


@pytest.mark.parametrize(
  ("payload", "kind"),
  [
    (b"PK\x03\x04synthetic", ArtifactKind.APK_BYTES),
    (b"dex\n035\x00synthetic", ArtifactKind.APK_BYTES),
  ],
)
def test_binary_scanner_rejects_apk_or_dex_magic(payload: bytes, kind: ArtifactKind) -> None:
  assert kind in {finding.kind for finding in scan_bytes(payload)}


def test_sanitizer_rejects_malformed_json() -> None:
  with pytest.raises(SanitizationRejected):
    sanitize_json_text('{"navigation_active":')


def test_cli_sanitizes_capture_through_real_module(tmp_path: Path) -> None:
  # Given: a private-shaped capture containing identity and absolute location.
  source = tmp_path / "private-shaped.json"
  output = tmp_path / "sanitized.json"
  source.write_text(_private_shaped_capture(), encoding="utf-8")

  # When: the real module CLI publishes a sanitized destination.
  completed = subprocess.run(
    [sys.executable, "-B", "-m", "tools.naver_map_patch.sanitize", str(source), str(output)],
    check=False,
    capture_output=True,
    text=True,
  )

  # Then: success is proved from independent expected values, not the scanner under test.
  assert completed.returncode == 0
  assert completed.stdout == "sanitized\n"
  assert completed.stderr == ""
  output_text = output.read_text(encoding="utf-8")
  sanitized = json.loads(output_text)
  assert sanitized["deviceId"] == "fixture-identity-0001"
  assert sanitized["currentLatitude"] == 0.0
  assert sanitized["currentLongitude"] == 0.0
  assert sanitized["rgdata"]["nTBTTurnType"] == 12
  assert sanitized["route"]["vertices"][-1] == {"x": 0.0002, "y": 0.0001}
  assert "android-device-7f3a" not in output_text
  assert "35.123456" not in output_text
  assert "127.654321" not in output_text


def test_cli_redacts_hardware_serial_through_real_module(tmp_path: Path) -> None:
  # Given: a common Android hardware serial field plus unrelated navigation semantics.
  source = tmp_path / "hardware-serial.json"
  output = tmp_path / "sanitized.json"
  source.write_text('{"hardwareSerial":"android-hardware-serial-7f3a","sequence":8}', encoding="utf-8")

  # When: the real module CLI publishes the sanitized destination.
  completed = subprocess.run(
    [sys.executable, "-B", "-m", "tools.naver_map_patch.sanitize", str(source), str(output)],
    check=False,
    capture_output=True,
    text=True,
  )

  # Then: explicit expected values prove that the private serial cannot survive success.
  assert completed.returncode == 0
  assert completed.stdout == "sanitized\n"
  assert completed.stderr == ""
  output_text = output.read_text(encoding="utf-8")
  sanitized = json.loads(output_text)
  assert sanitized["hardwareSerial"] == "fixture-identity-0001"
  assert sanitized["sequence"] == 8
  assert "android-hardware-serial-7f3a" not in output_text


def test_cli_rejects_aws_key_shaped_material_without_success_claim(tmp_path: Path) -> None:
  # Given: common AWS access-key-shaped material under an opaque field.
  source = tmp_path / "aws-shaped.json"
  output = tmp_path / "sanitized.json"
  source.write_text('{"opaque":"AKIAIOSFODNN7EXAMPLE"}', encoding="utf-8")

  # When: the real module CLI evaluates the capture.
  completed = subprocess.run(
    [sys.executable, "-B", "-m", "tools.naver_map_patch.sanitize", str(source), str(output)],
    check=False,
    capture_output=True,
    text=True,
  )

  # Then: rejection is observable and no output is published.
  assert completed.returncode == 2
  assert completed.stdout == ""
  assert "rejected:" in completed.stderr
  assert not output.exists()


def test_cli_rejection_removes_owned_stale_destination(tmp_path: Path) -> None:
  # Given: malformed input and a pre-existing unsafe destination owned by the CLI.
  source = tmp_path / "malformed.json"
  output = tmp_path / "sanitized.json"
  source.write_text('{"navigation_active":', encoding="utf-8")
  output.write_text('{"deviceId":"unsafe-stale-device"}', encoding="utf-8")

  # When: the real module CLI rejects the source.
  completed = subprocess.run(
    [sys.executable, "-B", "-m", "tools.naver_map_patch.sanitize", str(source), str(output)],
    check=False,
    capture_output=True,
    text=True,
  )

  # Then: no stale destination or misleading success claim survives.
  assert completed.returncode == 2
  assert completed.stdout == ""
  assert "rejected:" in completed.stderr
  assert not output.exists()
