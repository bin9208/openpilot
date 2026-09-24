from __future__ import annotations

import hashlib
import json
from pathlib import Path
import struct
import zipfile

import pytest

import tools.naver_map_patch.diagnostic_patch as diagnostic_patch
import tools.naver_map_patch.packaging_cli as packaging_cli
from tools.naver_map_patch.diagnostic_patch import (
  FIELD_ACCEPTANCE_BUILD_ID,
  diagnostic_payload_identity,
  field_acceptance_payload_identity,
  installed_diagnostic_payload_identity,
  installed_payload_mode,
  verify_field_acceptance_archive,
)
from tools.naver_map_patch.packaging_cli_args import (
  PackagingUsageError,
  _parse_build_arguments,
  _parse_output_arguments,
)
from tools.naver_map_patch.profile import load_profile
from tools.naver_map_patch.split_package import ArchivePatch, ErrorCode, PackagingError


_FIELD_TOP_LEVEL_TYPES = frozenset({
  "Lai/comma/naver/payload/AndroidApplicationFiles;",
  "Lai/comma/naver/payload/AndroidCaptureSharing;",
  "Lai/comma/naver/payload/AndroidProcess;",
  "Lai/comma/naver/payload/AndroidSQLiteCaptureDatabase;",
  "Lai/comma/naver/payload/AsyncDiagnosticSink;",
  "Lai/comma/naver/payload/BoundedMappingDiagnostics;",
  "Lai/comma/naver/payload/CaptureDatabase;",
  "Lai/comma/naver/payload/CaptureRetentionPolicy;",
  "Lai/comma/naver/payload/DiagnosticConfig;",
  "Lai/comma/naver/payload/DiagnosticCounters;",
  "Lai/comma/naver/payload/DiagnosticFrameSink;",
  "Lai/comma/naver/payload/DiagnosticRuntime;",
  "Lai/comma/naver/payload/DiagnosticSampler;",
  "Lai/comma/naver/payload/EncodedDiagnosticFrame;",
  "Lai/comma/naver/payload/FieldAcceptanceHooks;",
  "Lai/comma/naver/payload/FrameBatchConsumer;",
  "Lai/comma/naver/payload/HookRuntime;",
  "Lai/comma/naver/payload/Naver6805ObjectMapper;",
  "Lai/comma/naver/payload/NaverNavigationAggregator;",
  "Lai/comma/naver/payload/NaverNavigationEnvelope;",
  "Lai/comma/naver/payload/NaverNavigationSender;",
  "Lai/comma/naver/payload/NaverNavigationState;",
  "Lai/comma/naver/payload/NavigationTransport;",
  "Lai/comma/naver/payload/NetworkFrameConsumer;",
  "Lai/comma/naver/payload/OfflineCaptureStore;",
  "Lai/comma/naver/payload/PersistentNavigationTransport;",
  "Lai/comma/naver/payload/ProductionRuntime;",
})


def _uleb128(value: int) -> bytes:
  encoded = bytearray()
  while True:
    current = value & 0x7f
    value >>= 7
    encoded.append(current | (0x80 if value else 0))
    if not value:
      return bytes(encoded)


def _field_dex(
  *,
  top_level_types: frozenset[str] = _FIELD_TOP_LEVEL_TYPES,
  build_id: str = FIELD_ACCEPTANCE_BUILD_ID,
  extra_strings: tuple[str, ...] = (),
) -> bytes:
  field_name = "OFFLINE_CAPTURE_PAYLOAD_BUILD_ID"
  string_type = "Ljava/lang/String;"
  strings = sorted({*top_level_types, string_type, field_name, build_id, *extra_strings})
  string_indices = {value: index for index, value in enumerate(strings)}
  type_descriptors = sorted({*top_level_types, string_type}, key=string_indices.__getitem__)
  type_indices = {value: index for index, value in enumerate(type_descriptors)}
  classes = sorted(top_level_types, key=type_indices.__getitem__)

  header_size = 112
  string_ids_off = header_size
  type_ids_off = string_ids_off + 4 * len(strings)
  field_ids_off = type_ids_off + 4 * len(type_descriptors)
  class_defs_off = field_ids_off + 8
  data_off = class_defs_off + 32 * len(classes)

  string_data = bytearray()
  string_offsets: list[int] = []
  for value in strings:
    string_offsets.append(data_off + len(string_data))
    raw = value.encode("utf-8")
    string_data.extend(_uleb128(len(value)))
    string_data.extend(raw)
    string_data.append(0)

  class_data_off = data_off + len(string_data)
  class_data = b"\x01\x00\x00\x00\x00\x18"
  static_values_off = class_data_off + len(class_data)
  build_index = string_indices[build_id]
  encoded_index = build_index.to_bytes(max(1, (build_index.bit_length() + 7) // 8), "little")
  static_values = _uleb128(1) + bytes((((len(encoded_index) - 1) << 5) | 0x17,)) + encoded_index
  file_size = static_values_off + len(static_values)

  dex = bytearray(file_size)
  dex[:8] = b"dex\n035\x00"
  struct.pack_into("<I", dex, 32, file_size)
  struct.pack_into("<I", dex, 36, header_size)
  struct.pack_into("<I", dex, 40, 0x12345678)
  struct.pack_into("<II", dex, 56, len(strings), string_ids_off)
  struct.pack_into("<II", dex, 64, len(type_descriptors), type_ids_off)
  struct.pack_into("<II", dex, 80, 1, field_ids_off)
  struct.pack_into("<II", dex, 96, len(classes), class_defs_off)
  struct.pack_into("<II", dex, 104, file_size - data_off, data_off)
  for index, offset in enumerate(string_offsets):
    struct.pack_into("<I", dex, string_ids_off + index * 4, offset)
  for index, descriptor in enumerate(type_descriptors):
    struct.pack_into("<I", dex, type_ids_off + index * 4, string_indices[descriptor])
  struct.pack_into(
    "<HHI", dex, field_ids_off,
    type_indices["Lai/comma/naver/payload/DiagnosticConfig;"],
    type_indices[string_type],
    string_indices[field_name],
  )
  for index, descriptor in enumerate(classes):
    descriptor_class_data = class_data_off if descriptor == "Lai/comma/naver/payload/DiagnosticConfig;" else 0
    descriptor_values = static_values_off if descriptor_class_data else 0
    struct.pack_into(
      "<IIIIIIII", dex, class_defs_off + index * 32,
      type_indices[descriptor], 1, 0xffffffff, 0, 0xffffffff, 0,
      descriptor_class_data, descriptor_values,
    )
  dex[data_off:class_data_off] = string_data
  dex[class_data_off:static_values_off] = class_data
  dex[static_values_off:] = static_values
  return bytes(dex)


def _build_tokens(*extra: str) -> tuple[str, ...]:
  return (
    "build", "--base", "base.apk", "--splits-from", "companion", "--profile", "6.8.0.5",
    "--output", "out", "--keystore", "patch.p12", "--store-password-env", "STORE",
    "--key-password-env", "KEY", *extra, "--json",
  )


def _apk_with_marker(tmp_path, *markers: str):
  path = tmp_path / "base.apk"
  content = ("dex\n" + "\n".join(
    f"Lai/comma/naver/payload/{marker};" for marker in markers
  )).encode()
  with zipfile.ZipFile(path, "w") as archive:
    archive.writestr("classes43.dex", content)
  return path, content


def _apk_with_dex(tmp_path: Path, content: bytes, name: str = "base.apk") -> Path:
  path = tmp_path / name
  with zipfile.ZipFile(path, "w") as archive:
    archive.writestr("classes43.dex", content)
  return path


def test_field_acceptance_is_an_explicit_diagnostic_variant() -> None:
  parsed = _parse_build_arguments(_build_tokens("--field-acceptance"))

  assert parsed.field_acceptance is True
  assert parsed.diagnostic is False
  assert parsed.production is False


@pytest.mark.parametrize("extra", [
  ("--field-acceptance", "--diagnostic"),
  ("--field-acceptance", "--production"),
  ("--field-acceptance", "--patch", "classes43.dex=untrusted.dex"),
])
def test_field_acceptance_rejects_mixed_modes_and_manual_patches(extra: tuple[str, ...]) -> None:
  with pytest.raises(PackagingUsageError):
    _parse_build_arguments(_build_tokens(*extra))


def test_field_acceptance_payload_identity_stays_diagnostic() -> None:
  content = _field_dex()
  identity = field_acceptance_payload_identity(
    load_profile("6.8.0.5"), (ArchivePatch("classes43.dex", content),),
  )

  assert identity.mode == "diagnostic"
  assert FIELD_ACCEPTANCE_BUILD_ID == "naver-6.8.0.5-field-acceptance-v4"
  assert identity.build_id == FIELD_ACCEPTANCE_BUILD_ID
  assert identity.sha256 == hashlib.sha256(content).hexdigest()


def test_field_builder_invokes_the_diagnostic_gradle_variant(monkeypatch) -> None:
  captured = {}

  def fake_build(base, profile, environment, payload_mode, *, field_acceptance=False):
    captured.update(
      base=base,
      profile=profile,
      environment=environment,
      payload_mode=payload_mode,
      field_acceptance=field_acceptance,
    )
    return (ArchivePatch("classes43.dex", b"dex\nfield"),)

  monkeypatch.setattr(diagnostic_patch, "_build_profiled_archive_patches", fake_build)
  profile = load_profile("6.8.0.5")

  result = diagnostic_patch.build_field_acceptance_archive_patches(
    Path("base.apk"), profile, {"FIELD": "1"},
  )

  assert result == (ArchivePatch("classes43.dex", b"dex\nfield"),)
  assert captured == {
    "base": Path("base.apk"),
    "profile": profile,
    "environment": {"FIELD": "1"},
    "payload_mode": "diagnostic",
    "field_acceptance": True,
  }


def test_installed_field_marker_resolves_exact_field_identity(tmp_path) -> None:
  content = _field_dex()
  base = _apk_with_dex(tmp_path, content)
  profile = load_profile("6.8.0.5")

  assert installed_payload_mode(base, profile) == "diagnostic"
  identity = installed_diagnostic_payload_identity(base, profile)
  assert identity.mode == "diagnostic"
  assert identity.build_id == FIELD_ACCEPTANCE_BUILD_ID
  assert identity.sha256 == hashlib.sha256(content).hexdigest()


def test_field_provenance_verifies_as_exact_diagnostic_variant(tmp_path) -> None:
  output = tmp_path / "release"
  install_set = output / "install-set"
  install_set.mkdir(parents=True)
  base = install_set / "base.apk"
  content = _field_dex()
  with zipfile.ZipFile(base, "w") as archive:
    archive.writestr("classes43.dex", content)
  (install_set / "provenance.json").write_text(json.dumps({
    "schema_version": 1,
    "payload_identity": {
      "mode": "diagnostic",
      "build_id": FIELD_ACCEPTANCE_BUILD_ID,
      "dex_entry": "classes43.dex",
      "sha256": hashlib.sha256(content).hexdigest(),
    },
  }), encoding="utf-8")

  identity = packaging_cli._installed_payload_identity(
    output, base, load_profile("6.8.0.5"),
  )
  assert identity.build_id == FIELD_ACCEPTANCE_BUILD_ID

  provenance = json.loads((install_set / "provenance.json").read_text(encoding="utf-8"))
  provenance["payload_identity"]["build_id"] = "naver-6.8.0.5-diagnostic-offline-v3"
  (install_set / "provenance.json").write_text(json.dumps(provenance), encoding="utf-8")
  with pytest.raises(PackagingError, match="identity differs"):
    packaging_cli._installed_payload_identity(output, base, load_profile("6.8.0.5"))


@pytest.mark.parametrize("markers", [
  (),
  ("DiagnosticHooks", "FieldAcceptanceHooks"),
  ("FieldAcceptanceHooks", "ProductionHooks"),
])
def test_installed_payload_marker_rejects_missing_or_ambiguous(tmp_path, markers: tuple[str, ...]) -> None:
  base, _content = _apk_with_marker(tmp_path, *markers)

  with pytest.raises(PackagingError, match="marker"):
    installed_payload_mode(base, load_profile("6.8.0.5"))


def test_field_archive_verifier_accepts_only_the_exact_field_dex(tmp_path: Path) -> None:
  base = _apk_with_dex(tmp_path, _field_dex())

  verify_field_acceptance_archive(base, load_profile("6.8.0.5"))


@pytest.mark.parametrize("content", [
  _field_dex(build_id="unrelated-build"),
  _field_dex(build_id="naver-6.8.0.5-diagnostic-offline-v3"),
  _field_dex(extra_strings=("naver-6.8.0.5-diagnostic-offline-v3",)),
  _field_dex(extra_strings=("naver-6.8.0.5-public-beta-v1",)),
  _field_dex(extra_strings=("naver-6.8.0.5-public-beta-v2",)),
  _field_dex(extra_strings=("naver-6.8.0.5-field-acceptance-v1",)),
  _field_dex(extra_strings=("naver-6.8.0.5-field-acceptance-v2",)),
  _field_dex(extra_strings=("naver-6.8.0.5-field-acceptance-v3",)),
  _field_dex(extra_strings=("naver-6.8.0.5-unexpected-v1",)),
  _field_dex(extra_strings=("Lai/comma/naver/payload/DiagnosticHooks;",)),
  _field_dex(top_level_types=_FIELD_TOP_LEVEL_TYPES - {"Lai/comma/naver/payload/FieldAcceptanceHooks;"}),
  _field_dex(top_level_types=_FIELD_TOP_LEVEL_TYPES | {"Lai/comma/naver/payload/Unexpected;"}),
])
def test_field_archive_verifier_rejects_stale_dual_missing_or_extra_identity(
  tmp_path: Path, content: bytes,
) -> None:
  base = _apk_with_dex(tmp_path, content)

  with pytest.raises(PackagingError) as captured:
    verify_field_acceptance_archive(base, load_profile("6.8.0.5"))
  assert captured.value.code is ErrorCode.OUTPUT_HASH_MISMATCH


def test_diagnostic_identity_rejects_a_caller_supplied_field_dex() -> None:
  content = _field_dex()

  with pytest.raises(PackagingError, match="diagnostic.*marker"):
    diagnostic_payload_identity(
      load_profile("6.8.0.5"), (ArchivePatch("classes43.dex", content),),
    )


def test_field_build_rechecks_staged_final_dex_before_publishing(
  tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
  arguments = _parse_build_arguments(_build_tokens("--field-acceptance"))
  good_patch = ArchivePatch("classes43.dex", _field_dex())
  monkeypatch.setattr(packaging_cli, "verify_profiled_dex_entries", lambda *_args: None)
  monkeypatch.setattr(packaging_cli, "_android_tools", lambda _environment: object())
  monkeypatch.setattr(packaging_cli, "_build_patches", lambda *_args: (good_patch,))

  def reject_staged_base(request):
    assert request.prepublish_base_verifier is not None
    staged = _apk_with_dex(
      tmp_path, _field_dex(build_id="naver-6.8.0.5-diagnostic-offline-v3"), "staged.apk",
    )
    request.prepublish_base_verifier(staged)
    pytest.fail("field build published without checking the staged final DEX")

  monkeypatch.setattr(packaging_cli, "build_release", reject_staged_base)

  with pytest.raises(PackagingError):
    packaging_cli._run_build(arguments, {})


def test_verify_rechecks_field_final_dex_before_release_verification(
  tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
  output = tmp_path / "release"
  install_set = output / "install-set"
  install_set.mkdir(parents=True)
  content = _field_dex(build_id="naver-6.8.0.5-diagnostic-offline-v3")
  base = _apk_with_dex(install_set, content)
  (install_set / "provenance.json").write_text(json.dumps({
    "schema_version": 1,
    "payload_identity": {
      "mode": "diagnostic",
      "build_id": FIELD_ACCEPTANCE_BUILD_ID,
      "dex_entry": "classes43.dex",
      "sha256": hashlib.sha256(content).hexdigest(),
    },
  }), encoding="utf-8")
  monkeypatch.setattr(packaging_cli, "_android_tools", lambda _environment: object())
  monkeypatch.setattr(
    packaging_cli, "verify_release",
    lambda *_args: pytest.fail("release verification ran before exact field DEX verification"),
  )
  arguments = _parse_output_arguments(
    ("verify", "--output", str(output), "--profile", "6.8.0.5", "--json"), "verify",
  )

  with pytest.raises(PackagingError):
    packaging_cli._run_verify(arguments, {})
