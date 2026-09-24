from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import replace
from pathlib import Path

import pytest

from tools.naver_map_patch.packaging_cli import _parse_build_arguments
from tools.naver_map_patch.split_package import CommandResult, ErrorCode, PackagingError
from tools.naver_map_patch.split_packaging import build_release
import tools.naver_map_patch.split_tools as split_tools

from conftest import (
  TEST_PASSWORD,
  SyntheticSet,
  _add_trusted_certificate,
  _make_certificate_only_keystore,
  _make_key,
)
from test_split_packaging_integration import _request


@pytest.fixture(autouse=True)
def _signing_environment(monkeypatch: pytest.MonkeyPatch) -> None:
  monkeypatch.setenv("SYNTHETIC_STORE_PASSWORD", TEST_PASSWORD)
  monkeypatch.setenv("SYNTHETIC_KEY_PASSWORD", TEST_PASSWORD)


def _record_tool_commands(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, ...]]:
  commands: list[tuple[str, ...]] = []
  original = split_tools.run_checked

  def recording(
    command: Sequence[str], environment: Mapping[str, str], timeout_seconds: float,
  ) -> CommandResult:
    commands.append(tuple(command))
    return original(command, environment, timeout_seconds)

  monkeypatch.setattr(split_tools, "run_checked", recording)
  return commands


def _keytool_commands(commands: Sequence[Sequence[str]]) -> list[tuple[str, ...]]:
  return [tuple(command) for command in commands if Path(command[0]).stem.lower() == "keytool"]


def _sign_commands(commands: Sequence[Sequence[str]]) -> list[tuple[str, ...]]:
  return [
    tuple(command) for command in commands
    if Path(command[0]).stem.lower() == "apksigner" and len(command) > 1 and command[1] == "sign"
  ]


def _assert_closed_failure(error: PackagingError, output: Path, commands: Sequence[Sequence[str]]) -> None:
  assert error.code is ErrorCode.TOOL_FAILURE
  assert len(error.detail) <= 160
  assert TEST_PASSWORD not in error.detail
  assert not output.exists()
  assert not tuple(output.parent.glob(".naver-packaging-*"))
  assert not _sign_commands(commands)


def test_build_cli_accepts_omitted_signing_alias() -> None:
  parsed = _parse_build_arguments((
    "build", "--base", "base.apk", "--splits-from", "companion", "--profile", "6.8.0.5",
    "--output", "out", "--keystore", "patch.p12", "--store-password-env", "STORE",
    "--key-password-env", "KEY", "--diagnostic", "--json",
  ))

  assert parsed.alias is None


def test_single_private_key_alias_is_resolved_once_and_signs_all_apks(
  synthetic_set: SyntheticSet, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
  output = tmp_path / "release"
  request = _request(synthetic_set, output)
  request = replace(request, signer=replace(request.signer, alias=None))
  commands = _record_tool_commands(monkeypatch)

  result = build_release(request)

  assert len(result.apk_paths) == 3
  keytool_commands = _keytool_commands(commands)
  assert len(keytool_commands) == 1
  assert keytool_commands[0] == (
    str(request.tools.java_home / "bin" / "keytool.exe"),
    "-J-Duser.language=en",
    "-J-Duser.country=US",
    "-list",
    "-v",
    "-keystore",
    str(request.signer.keystore),
    "-storetype",
    "PKCS12",
    "-storepass:env",
    "SYNTHETIC_STORE_PASSWORD",
  )
  assert TEST_PASSWORD not in keytool_commands[0]
  assert len(_sign_commands(commands)) == 3


def test_certificate_only_pkcs12_is_rejected_before_signing_or_publication(
  synthetic_set: SyntheticSet, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
  keystore = tmp_path / "certificate-only.p12"
  output = tmp_path / "release"
  request = _request(synthetic_set, output)
  keytool = request.tools.java_home / "bin" / "keytool.exe"
  _make_certificate_only_keystore(keytool, keystore, "trusted", synthetic_set.environment)
  request = replace(request, signer=replace(request.signer, keystore=keystore, alias="trusted"))
  commands = _record_tool_commands(monkeypatch)

  with pytest.raises(PackagingError) as raised:
    build_release(request)

  _assert_closed_failure(raised.value, output, commands)
  assert len(_keytool_commands(commands)) == 1


def test_private_key_plus_trusted_certificate_is_rejected_before_signing_or_publication(
  synthetic_set: SyntheticSet, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
  keystore = tmp_path / "mixed-entries.p12"
  request = _request(synthetic_set, tmp_path / "release")
  keytool = request.tools.java_home / "bin" / "keytool.exe"
  _make_key(keytool, keystore, "patch", synthetic_set.environment)
  _add_trusted_certificate(keytool, keystore, "trusted", synthetic_set.environment)
  request = replace(request, signer=replace(request.signer, keystore=keystore, alias="patch"))
  commands = _record_tool_commands(monkeypatch)

  with pytest.raises(PackagingError) as raised:
    build_release(request)

  _assert_closed_failure(raised.value, request.output_root, commands)
  assert len(_keytool_commands(commands)) == 1


def test_two_private_key_pkcs12_is_rejected_before_signing_or_publication(
  synthetic_set: SyntheticSet, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
  keystore = tmp_path / "two-private-keys.p12"
  request = _request(synthetic_set, tmp_path / "release")
  keytool = request.tools.java_home / "bin" / "keytool.exe"
  _make_key(keytool, keystore, "first", synthetic_set.environment)
  _make_key(keytool, keystore, "second", synthetic_set.environment)
  request = replace(request, signer=replace(request.signer, keystore=keystore, alias="first"))
  commands = _record_tool_commands(monkeypatch)

  with pytest.raises(PackagingError) as raised:
    build_release(request)

  _assert_closed_failure(raised.value, request.output_root, commands)
  assert len(_keytool_commands(commands)) == 1


def test_explicit_wrong_alias_is_rejected_against_the_sole_private_key(
  synthetic_set: SyntheticSet, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
  request = _request(synthetic_set, tmp_path / "release")
  request = replace(request, signer=replace(request.signer, alias="not-patch"))
  commands = _record_tool_commands(monkeypatch)

  with pytest.raises(PackagingError) as raised:
    build_release(request)

  _assert_closed_failure(raised.value, request.output_root, commands)
  assert "explicit signing alias" in raised.value.detail
  assert len(_keytool_commands(commands)) == 1


def test_explicit_correct_sole_alias_remains_compatible(
  synthetic_set: SyntheticSet, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
  request = _request(synthetic_set, tmp_path / "release")
  commands = _record_tool_commands(monkeypatch)

  result = build_release(request)

  assert len(result.apk_paths) == 3
  assert len(_keytool_commands(commands)) == 1
  assert len(_sign_commands(commands)) == 3


def test_uncertain_keytool_output_fails_closed_without_publishing(
  synthetic_set: SyntheticSet, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
  request = _request(synthetic_set, tmp_path / "release")
  request = replace(request, signer=replace(request.signer, alias=None))
  commands: list[tuple[str, ...]] = []
  original = split_tools.run_checked

  def uncertain(
    command: Sequence[str], environment: Mapping[str, str], timeout_seconds: float,
  ) -> CommandResult:
    commands.append(tuple(command))
    if Path(command[0]).stem.lower() == "keytool":
      return CommandResult("unrecognized localized listing", "")
    return original(command, environment, timeout_seconds)

  monkeypatch.setattr(split_tools, "run_checked", uncertain)

  with pytest.raises(PackagingError) as raised:
    build_release(request)

  _assert_closed_failure(raised.value, request.output_root, commands)
  assert len(_keytool_commands(commands)) == 1
