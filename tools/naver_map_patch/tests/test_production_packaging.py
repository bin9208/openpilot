from __future__ import annotations

import base64
from dataclasses import replace
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import zipfile

import pytest

import tools.naver_map_patch.diagnostic_patch as diagnostic_patch
import tools.naver_map_patch.packaging_cli as packaging_cli
import tools.naver_map_patch.split_packaging as split_packaging
from tools.naver_map_patch.packaging_cli_args import (
  PackagingUsageError,
  _parse_build_arguments,
  _patch_profile,
)
from tools.naver_map_patch.profile import load_profile
from tools.naver_map_patch.split_package import ArchivePatch, ErrorCode, PackagingError, PayloadIdentity, Sha256


def _build_tokens(*extra: str) -> tuple[str, ...]:
  return (
    "build", "--base", "base.apk", "--splits-from", "companion", "--profile", "6.8.0.5",
    "--output", "out", "--keystore", "patch.p12", "--store-password-env", "STORE",
    "--key-password-env", "KEY", *extra, "--json",
  )


def test_build_requires_exactly_one_payload_mode() -> None:
  with pytest.raises(PackagingUsageError, match="exactly one"):
    _parse_build_arguments(_build_tokens())


def test_production_rejects_manual_payload_patches() -> None:
  with pytest.raises(PackagingUsageError, match="production.*patch"):
    _parse_build_arguments(_build_tokens(
      "--production", "--installed-signer-sha256", "a" * 64,
      "--patch", "classes43.dex=unverified.dex",
    ))


def test_production_allows_omitted_installed_signer() -> None:
  parsed = _parse_build_arguments(_build_tokens("--production"))

  assert parsed.installed_signer is None


def test_production_patch_profile_contains_only_six_control_anchors() -> None:
  profile = _patch_profile(load_profile("6.8.0.5"), "production")

  assert [(anchor.name, anchor.count) for anchor in profile.anchor_counts] == [
    ("status", 1),
    ("tbt_current", 1),
    ("tbt_next", 1),
    ("safety_source", 1),
    ("safety", 1),
    ("route", 1),
  ]


def test_production_source_contract_rejects_field_acceptance_hooks() -> None:
  source = (
    Path(diagnostic_patch.__file__).parent / "dexpatch" / "production-payload.gradle"
  ).read_text(encoding="utf-8")
  forbidden_tokens = source.split("def forbiddenProductionSourceTokens = [", 1)[1].split("]\n", 1)[0]

  assert '"FieldAcceptanceHooks"' in forbidden_tokens


def test_production_rules_are_exactly_the_six_production_hooks() -> None:
  encoded = diagnostic_patch._production_patch_rules(load_profile("6.8.0.5"))
  decoded = [base64.urlsafe_b64decode(value + "=" * (-len(value) % 4)).decode() for value in encoded]

  assert [value.split("\x1f", 1)[0] for value in decoded] == [
    "status", "tbt_current", "tbt_next", "safety_source", "safety", "route",
  ]
  source_rule = decoded[3].split("\x1f")
  assert source_rule[2:4] == [
    "Lcom/naver/map/core/navigation/NaviSafetyControllItemManager;",
    "l(Lcom/naver/maps/navi/v2/api/guidance/model/GuidanceSafety;)Lcom/naver/map/core/common/model/SafetyExtra;",
  ]
  assert "onSafetySource(Ljava/lang/Object;)V" in decoded[3]
  assert all("Lai/comma/naver/payload/ProductionHooks;" in value for value in decoded)
  assert "onRoute(Ljava/lang/Object;)V" in decoded[5]
  assert not any("onLane" in value or "beforeCaptureRead" in value for value in decoded)


def test_production_rules_reject_swapped_hook_method() -> None:
  profile = load_profile("6.8.0.5")
  current = profile.anchors[1]
  wrong_hook = replace(current.diagnostic_hook, method_descriptor="onNextTbt(Ljava/lang/Object;)V")
  malformed = replace(profile, anchors=(profile.anchors[0], replace(current, diagnostic_hook=wrong_hook), *profile.anchors[2:]))

  with pytest.raises(PackagingError, match="hook contract differs"):
    diagnostic_patch._production_patch_rules(malformed)


def test_production_archive_verifier_receives_exact_six_rules(
  tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
  captured: list[str] = []
  monkeypatch.setattr(
    diagnostic_patch,
    "discover_tools",
    lambda _root, _environment: SimpleNamespace(
      gradle=tmp_path / "gradle", java_home=tmp_path / "java",
    ),
  )

  def fake_run(arguments: list[str], **_kwargs: object) -> str:
    captured.extend(arguments)
    return "\n".join((
      "production_composition\t1",
      "production_bindings\t1",
      "production_global_hooks\t1",
      "production_exact_hooks\t1",
    ))

  monkeypatch.setattr(diagnostic_patch, "run_checked", fake_run)

  diagnostic_patch.verify_production_archive(
    tmp_path / "base.apk", load_profile("6.8.0.5"), {},
  )

  properties = [value for value in captured if value.startswith("-PproductionRules=")]
  assert len(properties) == 1
  encoded = properties[0].split("=", 1)[1].split(",")
  assert len(encoded) == 6
  assert len(set(encoded)) == 6
  assert [
    base64.urlsafe_b64decode(value + "=" * (-len(value) % 4)).decode().split("\x1f", 1)[0]
    for value in encoded
  ] == ["status", "tbt_current", "tbt_next", "safety_source", "safety", "route"]


@pytest.mark.parametrize("raw", [
  "status\t1\nstatus\t1\ntbt_current\t1\n",
  "status\t1\n",
  "status\t2\ntbt_current\t1\n",
  "status\t1\ntbt_current\t1\nextra\t1\n",
])
def test_patch_count_parser_rejects_duplicate_missing_wrong_and_extra(raw: str) -> None:
  with pytest.raises(PackagingError):
    diagnostic_patch._verified_count_lines(raw, {"status": 1, "tbt_current": 1}, "bad counts")


def test_production_payload_identity_uses_exact_contract() -> None:
  content = b"dex\nproduction-only"
  identity = diagnostic_patch.production_payload_identity(
    load_profile("6.8.0.5"), (ArchivePatch("classes43.dex", content),),
  )

  assert identity.mode == "production"
  assert identity.build_id == "naver-6.8.0.5-public-beta-v2"
  assert identity.dex_entry == "classes43.dex"
  assert identity.sha256 == hashlib.sha256(content).hexdigest()


def test_production_transport_contract_uses_device_to_c3_discovery() -> None:
  contract = json.loads(
    (Path(diagnostic_patch.__file__).parent / "profiles" / "6.8.0.5-production.json").read_text(
      encoding="utf-8",
    ),
  )

  assert contract["transport"] == {
    "discovery_host": "255.255.255.255",
    "discovery_port": 7706,
    "response_port": 7705,
    "tcp_port": 7712,
    "discovery_timeout_ms": 500,
    "connect_timeout_ms": 500,
    "write_timeout_ms": 500,
    "reconnect_min_ms": 1000,
    "reconnect_max_ms": 30000,
    "heartbeat_ms": 500,
    "max_frame_bytes": 262144,
  }


def test_production_contract_contains_only_the_exact_route_accessor_chain() -> None:
  contract = json.loads(
    (Path(diagnostic_patch.__file__).parent / "profiles" / "6.8.0.5-production.json").read_text(
      encoding="utf-8",
    ),
  )

  route_accessors = [item for item in contract["accessors"] if item["channel"] == "route"]
  assert [(item["id"], item["key"], item["evidence"]) for item in route_accessors] == [
    (
      "CURRENT_ROUTE_DATA",
      "com.naver.map.core.navigation.model.CurrentRoute#e()Lcom/naver/map/core/navigation/model/NaviRouteData;",
      "static_dex_pending_runtime",
    ),
    (
      "NAVI_ROUTE_INFO",
      "com.naver.map.core.navigation.model.NaviRouteData#h()Lcom/naver/maps/navi/v2/shared/api/route/model/RouteInfo;",
      "static_dex_pending_runtime",
    ),
    (
      "ROUTE_PATH_POINTS",
      "com.naver.maps.navi.v2.shared.api.route.model.RouteInfo#getPathPoints()Ljava/util/List;",
      "static_dex_pending_runtime",
    ),
    (
      "ROUTE_POINT_LATITUDE",
      "com.naver.maps.geometry.LatLng#latitude:D",
      "static_dex_pending_runtime",
    ),
    (
      "ROUTE_POINT_LONGITUDE",
      "com.naver.maps.geometry.LatLng#longitude:D",
      "static_dex_pending_runtime",
    ),
  ]
  assert "route" not in contract["omitted"]
  assert "route_points" not in contract["omitted"]
  assert contract["omitted"]["destination"] == "CurrentRoute.c/d start-goal meaning is unproved"
  assert contract["omitted"]["lane"] == "no envelope field and nested lane semantics are unproved"
  assert contract["omitted"]["off_route"] == "no exact runtime accessor contract"


@pytest.mark.parametrize("mutation", ["loopback", "missing_response_port"])
def test_production_contract_rejects_noncanonical_transport(
  tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mutation: str,
) -> None:
  source = Path(diagnostic_patch.__file__).parent / "profiles" / "6.8.0.5-production.json"
  contract = json.loads(source.read_text(encoding="utf-8"))
  if mutation == "loopback":
    contract["transport"]["discovery_host"] = "127.0.0.1"
  else:
    del contract["transport"]["response_port"]
  malformed = tmp_path / "production.json"
  malformed.write_text(json.dumps(contract), encoding="utf-8")
  monkeypatch.setattr(diagnostic_patch, "_PRODUCTION_CONTRACT", malformed)

  with pytest.raises(PackagingError, match="exact profile") as captured:
    diagnostic_patch._production_contract(load_profile("6.8.0.5"))
  assert captured.value.code is ErrorCode.MALFORMED_METADATA


def test_installed_production_identity_rejects_duplicate_payload(tmp_path: Path) -> None:
  base_apk = tmp_path / "base.apk"
  with pytest.warns(UserWarning, match="Duplicate name"):
    with zipfile.ZipFile(base_apk, "w") as archive:
      archive.writestr("classes43.dex", b"dex\none")
      archive.writestr("classes43.dex", b"dex\ntwo")

  with pytest.raises(PackagingError) as captured:
    diagnostic_patch.installed_production_payload_identity(base_apk, load_profile("6.8.0.5"))
  assert captured.value.code is ErrorCode.OUTPUT_HASH_MISMATCH


def test_verify_identity_selects_production_only_from_exact_provenance(tmp_path: Path) -> None:
  output = tmp_path / "release"
  install_set = output / "install-set"
  install_set.mkdir(parents=True)
  base_apk = install_set / "base.apk"
  content = b"dex\nLai/comma/naver/payload/ProductionHooks;\nproduction-only"
  with zipfile.ZipFile(base_apk, "w") as archive:
    archive.writestr("classes43.dex", content)
  (install_set / "provenance.json").write_text(json.dumps({
    "schema_version": 1,
    "payload_identity": {
      "mode": "production",
      "build_id": "naver-6.8.0.5-public-beta-v2",
      "dex_entry": "classes43.dex",
      "sha256": hashlib.sha256(content).hexdigest(),
    },
  }), encoding="utf-8")

  identity = packaging_cli._installed_payload_identity(output, base_apk, load_profile("6.8.0.5"))
  assert identity.mode == "production"
  assert identity.sha256 == hashlib.sha256(content).hexdigest()


@pytest.mark.parametrize("mode", ["diagnostic", "production"])
def test_requested_payload_identity_accepts_only_exact_supported_modes(mode: str) -> None:
  content = b"dex\npayload"
  identity = PayloadIdentity(mode, "build-v1", "classes43.dex", Sha256(hashlib.sha256(content).hexdigest()))
  request = SimpleNamespace(payload_identity=identity, patches=(ArchivePatch("classes43.dex", content),))

  split_packaging._validate_requested_payload_identity(request)


@pytest.mark.parametrize("mode,patches,digest", [
  ("unknown", (ArchivePatch("classes43.dex", b"dex\npayload"),), hashlib.sha256(b"dex\npayload").hexdigest()),
  ("production", (), hashlib.sha256(b"dex\npayload").hexdigest()),
  ("production", (ArchivePatch("classes43.dex", b"dex\none"), ArchivePatch("classes43.dex", b"dex\ntwo")), hashlib.sha256(b"dex\none").hexdigest()),
  ("production", (ArchivePatch("classes43.dex", b"dex\npayload"),), "0" * 64),
])
def test_requested_payload_identity_rejects_unknown_missing_duplicate_and_hash_mismatch(
  mode: str, patches: tuple[ArchivePatch, ...], digest: str,
) -> None:
  identity = PayloadIdentity(mode, "build-v1", "classes43.dex", Sha256(digest))
  request = SimpleNamespace(payload_identity=identity, patches=patches)

  with pytest.raises(PackagingError) as captured:
    split_packaging._validate_requested_payload_identity(request)
  assert captured.value.code is ErrorCode.OUTPUT_HASH_MISMATCH


@pytest.mark.parametrize(("actual_marker", "claimed_mode", "claimed_build"), [
  ("ProductionHooks", "diagnostic", "naver-6.8.0.5-diagnostic-offline-v3"),
  ("DiagnosticHooks", "production", "naver-6.8.0.5-public-beta-v2"),
])
def test_verify_identity_rejects_provenance_mode_relabeling(
  tmp_path: Path, actual_marker: str, claimed_mode: str, claimed_build: str,
) -> None:
  output = tmp_path / "release"
  install_set = output / "install-set"
  install_set.mkdir(parents=True)
  base_apk = install_set / "base.apk"
  content = f"dex\nLai/comma/naver/payload/{actual_marker};".encode()
  with zipfile.ZipFile(base_apk, "w") as archive:
    archive.writestr("classes43.dex", content)
  (install_set / "provenance.json").write_text(json.dumps({
    "schema_version": 1,
    "payload_identity": {
      "mode": claimed_mode,
      "build_id": claimed_build,
      "dex_entry": "classes43.dex",
      "sha256": hashlib.sha256(content).hexdigest(),
    },
  }), encoding="utf-8")

  with pytest.raises(PackagingError, match="mode differs"):
    packaging_cli._installed_payload_identity(output, base_apk, load_profile("6.8.0.5"))
