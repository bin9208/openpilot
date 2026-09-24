from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from typing import TypedDict


REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "tools" / "naver_map_patch" / "naver_patch.py"


class Requirement(TypedDict):
  name: str
  required: str
  success: bool
  path: str
  version: str
  detail: str


class DoctorReport(TypedDict):
  schema_version: int
  command: str
  success: bool
  sdk_root: str
  requirements: list[Requirement]


def run_doctor(environment: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
  process_environment = os.environ.copy()
  if environment is not None:
    process_environment.update(environment)
  return subprocess.run(
    [sys.executable, str(SCRIPT), "doctor", "--json"],
    cwd=REPO_ROOT,
    env=process_environment,
    capture_output=True,
    text=True,
    timeout=30,
    check=False,
  )


def parse_report(process: subprocess.CompletedProcess[str]) -> DoctorReport:
  return json.loads(process.stdout)


def snapshot_tree(path: Path) -> tuple[tuple[str, bool, int, int], ...] | None:
  if not path.exists():
    return None
  return tuple(sorted(
    (
      str(item.relative_to(path)),
      item.is_dir(),
      0 if item.is_dir() else item.stat().st_size,
      item.stat().st_mtime_ns,
    )
    for item in path.rglob("*")
  ))


def test_doctor_reports_resolved_host_toolchain() -> None:
  # Given: the repository host with its installed Android Studio toolchain.

  # When: doctor is invoked through its public CLI boundary.
  process = run_doctor({"TEST_SECRET_SENTINEL": "never-print-this-value"})

  # Then: exit status and clean JSON agree, with exact usable paths and versions.
  report = parse_report(process)
  assert process.returncode == 0
  assert process.stderr == ""
  assert report["schema_version"] == 1
  assert report["command"] == "doctor"
  assert report["success"] is True
  assert "never-print-this-value" not in process.stdout
  requirements = {item["name"]: item for item in report["requirements"]}
  assert set(requirements) == {"python", "java", "aapt2", "d8", "zipalign", "apksigner", "adb", "android_platform"}
  for requirement in requirements.values():
    assert requirement["success"] is True
    assert Path(requirement["path"]).is_absolute()
    assert Path(requirement["path"]).exists()
    assert requirement["version"]


def test_doctor_fails_closed_for_empty_sdk_then_recovers() -> None:
  # Given: an explicitly selected, actually empty SDK root and the current output state.
  out_path = REPO_ROOT / "tools" / "naver_map_patch" / "out"
  output_before = snapshot_tree(out_path)

  # When: failure is followed by a normal doctor invocation.
  with tempfile.TemporaryDirectory(prefix="naver-patch-test-") as temporary_directory:
    empty_sdk = Path(temporary_directory)
    failure = run_doctor({"ANDROID_SDK_ROOT": str(empty_sdk), "ANDROID_HOME": str(empty_sdk)})
    success = run_doctor()

  # Then: the failure is truthful and artifact-free, while the rerun is not contaminated.
  failure_report = parse_report(failure)
  success_report = parse_report(success)
  assert failure.returncode != 0
  assert failure.stderr == ""
  assert failure_report["success"] is False
  failed_names = {item["name"] for item in failure_report["requirements"] if not item["success"]}
  assert {"aapt2", "d8", "zipalign", "apksigner", "adb", "android_platform"} <= failed_names
  assert snapshot_tree(out_path) == output_before
  assert success.returncode == 0
  assert success_report["success"] is True
  assert snapshot_tree(out_path) == output_before


def test_doctor_rejects_directory_decoy_for_platform_jar() -> None:
  # Given: an SDK root whose android.jar path is a directory decoy.
  with tempfile.TemporaryDirectory(prefix="naver-platform-decoy-") as temporary_directory:
    sdk_root = Path(temporary_directory)
    decoy = sdk_root / "platforms" / "android-36" / "android.jar"
    decoy.mkdir(parents=True)

    # When: doctor inspects the decoy through the public CLI boundary.
    process = run_doctor({"ANDROID_SDK_ROOT": str(sdk_root), "ANDROID_HOME": str(sdk_root)})
    report = parse_report(process)

    # Then: the platform component fails as a missing regular file.
    platform = next(item for item in report["requirements"] if item["name"] == "android_platform")
    assert process.returncode != 0
    assert report["success"] is False
    assert platform["success"] is False
    assert platform["detail"] == "regular file not found"
    assert Path(platform["path"]).is_dir()


def test_version_probe_times_out_and_terminates_child() -> None:
  # Given: a real child process that waits indefinitely.
  from tools.naver_map_patch.naver_patch import _probe_version

  command = (sys.executable, "-c", "import threading; threading.Event().wait()")

  # When: the bounded probe deadline expires.
  result = _probe_version(command, {}, 0.05)

  # Then: timeout is a typed failure instead of a hung doctor process.
  assert result.success is False
  assert result.output == ""
  assert "timed out" in result.detail
