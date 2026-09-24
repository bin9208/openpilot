from __future__ import annotations

import os
from pathlib import Path
import sys
import zipfile

import pytest

from tools.naver_map_patch.split_package import ArchivePatch, ErrorCode, PackagingError
from tools.naver_map_patch.split_tools import rewrite_apk, run_checked


def test_tool_runner_times_out_without_leaking_environment_secret() -> None:
  # Given: a child command that never completes and a secret-bearing environment.
  environment = os.environ.copy()
  environment["SYNTHETIC_SECRET"] = "must-not-appear"
  command = (sys.executable, "-c", "import threading; threading.Event().wait()")

  # When: the bounded tool runner reaches its deadline.
  with pytest.raises(PackagingError) as raised:
    run_checked(command, environment, 0.05)

  # Then: the hung process fails closed and the secret is absent from the typed error.
  assert raised.value.code is ErrorCode.TOOL_TIMEOUT
  assert "must-not-appear" not in str(raised.value)


def test_tool_runner_rejects_misleading_success_text_from_failed_process() -> None:
  # Given: a process that prints a success-looking token but exits nonzero.
  command = (sys.executable, "-c", "import sys; print('Verified'); sys.exit(7)")

  # When: the process is executed through the checked runner.
  with pytest.raises(PackagingError) as raised:
    run_checked(command, os.environ, 5.0)

  # Then: exit status wins over untrusted stdout.
  assert raised.value.code is ErrorCode.TOOL_FAILURE
  assert raised.value.exit_code == 7


def test_tool_runner_rejects_missing_executable() -> None:
  # Given: a path that cannot resolve to a process.
  command = (str(Path("missing") / "definitely-not-a-real-tool"), "--version")

  # When: process creation is attempted.
  with pytest.raises(PackagingError) as raised:
    run_checked(command, os.environ, 5.0)

  # Then: launch failure is typed and bounded.
  assert raised.value.code is ErrorCode.TOOL_FAILURE
  assert raised.value.exit_code is None


def test_tool_runner_decodes_utf8_output_independent_of_host_locale() -> None:
  # Given: an Android-tool-shaped child emits valid UTF-8 bytes not decodable as cp949.
  expected = "\uc9c4\ub2e8"
  payload = expected.encode("utf-8").hex()
  command = (sys.executable, "-c", f"import sys; sys.stdout.buffer.write(bytes.fromhex('{payload}'))")

  # When: the checked runner captures the tool output.
  result = run_checked(command, os.environ, 5.0)

  # Then: decoding is deterministic and independent of the Windows host locale.
  assert result.stdout == expected


def test_rewrite_apk_accepts_multidex_entries_in_teens(tmp_path: Path) -> None:
  # Given: a base APK containing the valid Android multidex entry classes11.dex.
  source = tmp_path / "source.apk"
  destination = tmp_path / "rewritten.apk"
  with zipfile.ZipFile(source, "w") as archive:
    archive.writestr("classes11.dex", b"original-dex")

  # When: the diagnostic patch replaces that exact DEX entry.
  rewrite_apk(source, destination, (ArchivePatch("classes11.dex", b"patched-dex"),))

  # Then: the valid classesN.dex entry is replaced rather than rejected by the path gate.
  with zipfile.ZipFile(destination) as archive:
    assert archive.read("classes11.dex") == b"patched-dex"
