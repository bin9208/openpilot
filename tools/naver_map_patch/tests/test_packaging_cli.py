from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
import hashlib
import json
import os
import zipfile

import pytest

import tools.naver_map_patch.packaging_cli as packaging_cli
import tools.naver_map_patch.packaging_cli_args as cli_args
from tools.naver_map_patch.profile import (
  AnchorProfile,
  BaseProfile,
  DexEntryProfile,
  InspectionProfile,
  SplitProfile,
)
from tools.naver_map_patch.packaging_cli import (
  PackagingUsageError,
  _build_patches,
  _parse_build_arguments,
  run_packaging_cli,
)
from tools.naver_map_patch.profile import load_profile
from tools.naver_map_patch.split_package import ArchivePatch
from tools.naver_map_patch.split_metadata import read_apk_plus_metadata
from tools.naver_map_patch.split_tools import inspect_apk, sha256_file
from conftest import SyntheticSet
from test_split_packaging_integration import _tools


def test_build_cli_requires_authoritative_base_and_companion() -> None:
  # Given: the public build command omits its authoritative base path.
  tokens = (
    "build", "--splits-from", "companion", "--profile", "6.8.0.5", "--output", "out",
    "--keystore", "patch.p12", "--alias", "patch", "--store-password-env", "STORE",
    "--key-password-env", "KEY", "--json",
  )

  # When: the CLI boundary parses the request.
  with pytest.raises(PackagingUsageError):
    _parse_build_arguments(tokens)


def test_build_cli_parses_repeatable_dex_patches() -> None:
  # Given: a complete authoritative base/companion build request with one DEX patch.
  tokens = (
    "build", "--base", "base.apk", "--splits-from", "companion", "--profile", "6.8.0.5",
    "--output", "out", "--keystore", "patch.p12", "--alias", "patch",
    "--store-password-env", "STORE", "--key-password-env", "KEY", "--patch",
    "classes.dex=patched.dex", "--diagnostic", "--json",
  )

  # When: the CLI boundary parses the request.
  parsed = _parse_build_arguments(tokens)

  # Then: paths remain explicit and diagnostic mode does not alter the source boundary.
  assert parsed.base == Path("base.apk")
  assert parsed.companion_source == Path("companion")
  assert parsed.diagnostic is True
  assert parsed.patches[0].entry_name == "classes.dex"
  assert parsed.patches[0].path == Path("patched.dex")


def test_diagnostic_build_generates_profile_patches_when_manual_patches_are_absent(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  # Given: a diagnostic build request with no caller-supplied DEX replacement.
  parsed = _parse_build_arguments((
    "build", "--base", "base.apk", "--splits-from", "companion", "--profile", "6.8.0.5",
    "--output", "out", "--keystore", "patch.p12", "--alias", "patch",
    "--store-password-env", "STORE", "--key-password-env", "KEY", "--diagnostic", "--json",
  ))
  generated = (ArchivePatch("classes43.dex", b"dex\nsynthetic"),)
  calls: list[tuple[Path, str]] = []

  def build_from_profile(
    base: Path, profile: InspectionProfile, _environment: Mapping[str, str],
  ) -> tuple[ArchivePatch, ...]:
    calls.append((base, profile.name))
    return generated

  monkeypatch.setattr(packaging_cli, "build_diagnostic_archive_patches", build_from_profile)

  # When: the build boundary resolves its archive replacements.
  result = _build_patches(parsed, load_profile("6.8.0.5"), Path("base.apk"), {})

  # Then: it selects the profile engine exactly once while legacy explicit patches stay supported.
  assert result == generated
  assert calls == [(Path("base.apk"), "6.8.0.5")]


def test_production_build_generates_production_payload_when_manual_patches_are_absent(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  # Given: an exact-profile production request with no caller-supplied DEX replacement.
  parsed = _parse_build_arguments((
    "build", "--base", "base.apk", "--splits-from", "companion", "--profile", "6.8.0.5",
    "--output", "out", "--keystore", "patch.p12", "--alias", "patch",
    "--store-password-env", "STORE", "--key-password-env", "KEY", "--production", "--json",
  ))
  generated = (ArchivePatch("classes43.dex", b"dex\nproduction"),)
  calls: list[tuple[Path, str]] = []

  def build_from_profile(
    base: Path, profile: InspectionProfile, _environment: Mapping[str, str],
  ) -> tuple[ArchivePatch, ...]:
    calls.append((base, profile.name))
    return generated

  monkeypatch.setattr(packaging_cli, "build_production_archive_patches", build_from_profile)

  # When: the build boundary resolves its generated production payload.
  result = _build_patches(parsed, load_profile("6.8.0.5"), Path("base.apk"), {})

  # Then: production and diagnostic modes remain distinct and exact-profile driven.
  assert parsed.production is True
  assert parsed.diagnostic is False
  assert result == generated
  assert calls == [(Path("base.apk"), "6.8.0.5")]


def test_build_cli_rejects_simulation_as_unknown() -> None:
  tokens = (
    "build", "--base", "base.apk", "--splits-from", "companion", "--profile", "6.8.0.5",
    "--output", "out", "--keystore", "patch.p12", "--alias", "patch",
    "--store-password-env", "STORE", "--key-password-env", "KEY", "--simulation", "--json",
  )

  with pytest.raises(PackagingUsageError, match="unsupported argument: --simulation"):
    _parse_build_arguments(tokens)


def test_cli_build_verify_and_install_command_use_real_signed_splits(
  synthetic_set: SyntheticSet, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
  # Given: a synthetic base, typed ABI/density companion, and user-owned patch key.
  tools = _tools(synthetic_set)
  metadata = read_apk_plus_metadata(synthetic_set.companion / "apk+.json")
  signer = inspect_apk(synthetic_set.base_apk, tools).signer_sha256
  with zipfile.ZipFile(synthetic_set.base_apk, "r") as archive:
    dex_bytes = archive.read("classes.dex")
  profile = InspectionProfile(
    name="synthetic-cli",
    package_name=metadata.package_name,
    version_code=metadata.version_code,
    version_name=metadata.version_name,
    package_signer_sha256=str(signer),
    source_stamp_sha256="0" * 64,
    base=BaseProfile(synthetic_set.base_apk.stat().st_size, str(sha256_file(synthetic_set.base_apk)), 1, 0,
                     metadata.required_split_types),
    dex_entries={"classes.dex": DexEntryProfile(len(dex_bytes), hashlib.sha256(dex_bytes).hexdigest())},
    payload_modes=("diagnostic", "production"),
    bundle_base_entry=metadata.base.file_name,
    splits=tuple(
      SplitProfile(item.file_name, item.split_name, item.split_type,
                   (synthetic_set.companion / item.file_name).stat().st_size, str(item.sha256))
      for item in metadata.splits
    ),
    anchors=(AnchorProfile("status", "classes.dex", "Lx;", "m()V", ("return-void",), 1),),
    diagnostic_payload=replace(
      load_profile("6.8.0.5").diagnostic_payload,
      payload_dex_entry="classes.dex",
    ),
  )
  monkeypatch.setattr(cli_args, "load_profile", lambda name: profile)
  monkeypatch.setattr(packaging_cli, "load_profile", lambda name: profile)
  monkeypatch.setenv("SYNTHETIC_STORE_PASSWORD", "synthetic-only-password")
  monkeypatch.setenv("SYNTHETIC_KEY_PASSWORD", "synthetic-only-password")
  patch = tmp_path / "patched.dex"
  patch_content = b"synthetic-dex-patched\nLai/comma/naver/payload/DiagnosticHooks;"
  patch.write_bytes(patch_content)
  output = tmp_path / "release"
  base_args = [
    "--base", str(synthetic_set.base_apk), "--splits-from", str(synthetic_set.companion),
    "--profile", "synthetic-cli", "--output", str(output), "--keystore", str(synthetic_set.patch_keystore),
    "--alias", "patch", "--store-password-env", "SYNTHETIC_STORE_PASSWORD", "--key-password-env",
    "SYNTHETIC_KEY_PASSWORD", "--patch", f"classes.dex={patch}", "--diagnostic", "--json",
  ]

  # When: the real entrypoint builds, verifies, then emits the install command.
  assert run_packaging_cli(("build", *base_args), os.environ) == 0
  build_output = json.loads(capsys.readouterr().out)
  assert build_output["success"] is True
  assert build_output["install_command"]["argv"] == [
    "adb", "install-multiple", "-r", "base.apk", "split_config.arm64_v8a.apk", "split_config.xxhdpi.apk",
  ]
  assert build_output["payload_identity"] == {
    "mode": "diagnostic",
    "build_id": "naver-6.8.0.5-diagnostic-offline-v3",
    "dex_entry": "classes.dex",
    "sha256": hashlib.sha256(patch_content).hexdigest(),
  }
  assert run_packaging_cli(("verify", "--output", str(output), "--profile", "synthetic-cli", "--json"), os.environ) == 0
  verify_output = json.loads(capsys.readouterr().out)
  assert len(verify_output["apks"]) == 3
  assert verify_output["payload_identity"] == build_output["payload_identity"]
  assert run_packaging_cli(("install-command", "--output", str(output), "--profile", "synthetic-cli", "--json"), os.environ) == 0
  install_capture = capsys.readouterr()
  install_output = json.loads(install_capture.out)

  # Then: no base-only success claim exists and the signed outer bundle is present.
  assert install_output["requires_all_splits"] is True
  assert "uninstall" not in install_capture.err.lower()
  assert (output / "patched.apk+").is_file()
