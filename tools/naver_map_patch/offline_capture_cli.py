"""Fail-closed extraction of copied Naver offline diagnostic evidence."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Protocol
import uuid

from tools.naver_map_patch.naver_patch import DoctorReport, REPO_ROOT, doctor
from tools.naver_map_patch.offline_capture_db import (
  OfflineCaptureError,
  OfflineCaptureReport,
  primary_database_is_schema_compatible,
  validate_offline_database,
)
from tools.naver_map_patch.profile import ProfileError, load_profile
from tools.naver_map_patch.profile_types import InspectionProfile


_PACKAGE_NAME = "com.nhn.android.nmap"
_VERSION_NAME = "6.8.0.5"
_VERSION_CODE = 60800007
_COMMAND_TIMEOUT_SECONDS = 300.0
_SQLITE_HEADER = b"SQLite format 3\x00"
_PROVIDER_ERROR_PREFIX = b"Error while accessing provider:"
_ERROR_PROBE_BYTES = 4_097


@dataclass(frozen=True, slots=True)
class ExtractCaptureArguments:
  profile: str
  output_root: Path


@dataclass(frozen=True, slots=True)
class CommandResult:
  returncode: int
  stdout: str
  stderr: str


@dataclass(frozen=True, slots=True)
class ExtractCaptureResult:
  capture_id: str
  capture_directory: Path
  raw_directory: Path
  bundle_directory: Path
  report: OfflineCaptureReport

  def as_dict(self) -> dict[str, object]:
    return {
      "schema_version": 1,
      "command": "extract-capture",
      "success": True,
      "profile": _VERSION_NAME,
      "capture_id": self.capture_id,
      "capture_directory": str(self.capture_directory),
      "raw_directory": str(self.raw_directory),
      "bundle_directory": str(self.bundle_directory),
      "report": self.report.as_dict(),
    }


class CommandRunner(Protocol):
  def __call__(self, argv: tuple[str, ...]) -> CommandResult: ...


class BinaryCommandRunner(Protocol):
  def __call__(
      self, argv: tuple[str, ...], output: Path,
  ) -> tuple[int, bytes]: ...


class ExtractCaptureUsageError(RuntimeError):
  pass


class ExtractCaptureError(RuntimeError):
  def __init__(self, code: str, detail: str) -> None:
    super().__init__(detail)
    self.code = code
    self.detail = detail


def _parse_arguments(arguments: Sequence[str]) -> ExtractCaptureArguments:
  tokens = tuple(arguments)
  if not tokens or tokens[0] != "extract-capture":
    raise ExtractCaptureUsageError("command must start with extract-capture")
  values: dict[str, str] = {}
  json_requested = False
  index = 1
  while index < len(tokens):
    token = tokens[index]
    if token == "--json":
      if json_requested:
        raise ExtractCaptureUsageError("--json may be specified only once")
      json_requested = True
      index += 1
      continue
    if token not in ("--profile", "--output-root"):
      raise ExtractCaptureUsageError(f"unsupported argument: {token}")
    if (
      token in values
      or index + 1 >= len(tokens)
      or tokens[index + 1].startswith("--")
    ):
      raise ExtractCaptureUsageError(f"{token} requires exactly one value")
    values[token] = tokens[index + 1]
    index += 2
  missing = tuple(
    name for name in ("--profile", "--output-root") if name not in values
  )
  if missing or not json_requested:
    required = ", ".join(
      (*missing, *(("--json",) if not json_requested else ()))
    )
    raise ExtractCaptureUsageError(f"missing required argument(s): {required}")
  if values["--profile"] != _VERSION_NAME:
    raise ExtractCaptureUsageError(
      f"--profile must be exactly {_VERSION_NAME}"
    )
  return ExtractCaptureArguments(
    profile=values["--profile"],
    output_root=Path(values["--output-root"]),
  )


def _default_runner(argv: tuple[str, ...]) -> CommandResult:
  completed: subprocess.CompletedProcess[str] | None = None
  command_failed = False
  try:
    completed = subprocess.run(
      argv,
      capture_output=True,
      text=True,
      encoding="utf-8",
      errors="replace",
      timeout=_COMMAND_TIMEOUT_SECONDS,
      check=False,
    )
  except (OSError, subprocess.TimeoutExpired):
    command_failed = True
  if command_failed or completed is None:
    raise ExtractCaptureError(
      "adb_command_failed",
      "ADB command could not complete",
    )
  return CommandResult(completed.returncode, completed.stdout, completed.stderr)


def _default_binary_runner(
    argv: tuple[str, ...], output: Path,
) -> tuple[int, bytes]:
  completed: subprocess.CompletedProcess[bytes] | None = None
  command_failed = False
  try:
    with output.open("xb") as stream:
      completed = subprocess.run(
        argv,
        stdout=stream,
        stderr=subprocess.PIPE,
        timeout=_COMMAND_TIMEOUT_SECONDS,
        check=False,
      )
  except (OSError, subprocess.TimeoutExpired):
    command_failed = True
  if command_failed or completed is None:
    raise ExtractCaptureError(
      "content_read_failed",
      "ADB content read could not complete",
    )
  return completed.returncode, completed.stderr


def _resolve_adb(
    environment: Mapping[str, str],
    doctor_fn: Callable[[Mapping[str, str]], DoctorReport],
) -> str:
  report = doctor_fn(environment)
  requirements = tuple(
    requirement
    for requirement in report.requirements
    if requirement.name == "adb"
  )
  if (
    len(requirements) != 1
    or not requirements[0].success
    or not requirements[0].path
  ):
    raise ExtractCaptureError(
      "adb_unavailable",
      "the Android SDK adb requirement is unavailable",
    )
  return requirements[0].path


def _run_checked(
    runner: CommandRunner,
    argv: tuple[str, ...],
    operation: str,
) -> CommandResult:
  result: CommandResult | None = None
  runner_failed = False
  try:
    result = runner(argv)
  except Exception:
    runner_failed = True
  if runner_failed:
    raise ExtractCaptureError(
      "adb_command_failed",
      f"ADB {operation} command could not complete",
    )
  if (
    type(result) is not CommandResult
    or type(result.returncode) is not int
    or type(result.stdout) is not str
    or type(result.stderr) is not str
  ):
    raise ExtractCaptureError(
      "adb_command_failed",
      f"ADB {operation} returned an invalid result",
    )
  if result.returncode != 0:
    raise ExtractCaptureError(
      "adb_command_failed",
      f"ADB {operation} failed",
    )
  return result


def _gated_serial(devices_output: str) -> str:
  lines = tuple(line.strip() for line in devices_output.splitlines() if line.strip())
  if not lines or lines[0] != "List of devices attached":
    raise ExtractCaptureError(
      "device_gate",
      "exactly one authorized device in state device is required",
    )
  rows = lines[1:]
  if len(rows) != 1:
    raise ExtractCaptureError(
      "device_gate",
      "exactly one authorized device in state device is required",
    )
  fields = rows[0].split()
  if len(fields) < 2 or fields[1] != "device" or not fields[0]:
    raise ExtractCaptureError(
      "device_gate",
      "exactly one authorized device in state device is required",
    )
  return fields[0]


def _validate_installed_package(package_output: str) -> None:
  version_codes = re.findall(
    r"(?m)^\s*versionCode=([0-9]+)(?:\s|$)",
    package_output,
  )
  version_names = re.findall(
    r"(?m)^\s*versionName=([^\s]+)\s*$",
    package_output,
  )
  if (
    version_codes != [str(_VERSION_CODE)]
    or version_names != [_VERSION_NAME]
  ):
    raise ExtractCaptureError(
      "package_version",
      "installed package version does not match the fixed 6.8.0.5 profile",
    )


def _capture_file_names(profile: InspectionProfile) -> tuple[str, ...]:
  return (profile.diagnostic_payload.capture_sharing.export_database_name,)


def _capture_uri(profile: InspectionProfile, name: str) -> str:
  payload = profile.diagnostic_payload
  sharing = payload.capture_sharing
  return (
    f"content://{sharing.authority}/{sharing.file_provider_root_name}/"
    f"{payload.offline_capture.directory_name}/{name}"
  )


def _discard_partial(path: Path) -> None:
  try:
    path.unlink(missing_ok=True)
  except OSError:
    pass


def _read_partial_prefix(path: Path) -> bytes:
  try:
    with path.open("rb") as stream:
      return stream.read(_ERROR_PROBE_BYTES)
  except OSError:
    return b""


def _read_capture_file(
    *,
    binary_runner: BinaryCommandRunner,
    adb: str,
    serial: str,
    uri: str,
    destination: Path,
    expected_database: bool,
) -> bool:
  partial = destination.with_name(destination.name + ".part")
  result: tuple[int, bytes] | None = None
  runner_failed = False
  try:
    result = binary_runner(
      (
        adb, "-s", serial,
        "exec-out", "content", "read", "--uri", uri,
      ),
      partial,
    )
  except Exception:
    runner_failed = True
  if runner_failed:
    _discard_partial(partial)
    raise ExtractCaptureError(
      "content_read_failed",
      "ADB content read could not complete",
    )
  if (
    not isinstance(result, tuple)
    or len(result) != 2
    or type(result[0]) is not int
    or type(result[1]) is not bytes
  ):
    _discard_partial(partial)
    raise ExtractCaptureError(
      "content_read_failed",
      "ADB content read returned an invalid result",
    )
  returncode, stderr = result
  if returncode != 0 or stderr:
    _discard_partial(partial)
    raise ExtractCaptureError(
      "content_read_failed",
      "ADB content read failed",
    )
  prefix = _read_partial_prefix(partial)
  if prefix.startswith(_PROVIDER_ERROR_PREFIX):
    _discard_partial(partial)
    raise ExtractCaptureError(
      "content_read_failed",
      "ADB content read failed",
    )
  if expected_database and not prefix.startswith(_SQLITE_HEADER):
    _discard_partial(partial)
    raise ExtractCaptureError(
      "content_read_failed",
      "ADB content read did not return a SQLite database",
    )
  try:
    if not partial.is_file() or destination.exists():
      raise OSError()
    partial.replace(destination)
  except OSError:
    _discard_partial(partial)
    raise ExtractCaptureError(
      "content_read_failed",
      "ADB content read output could not be published",
    )
  return True


def _is_inside(path: Path, directory: Path) -> bool:
  try:
    path.relative_to(directory)
    return True
  except ValueError:
    return False


def _resolve_output_root(output_root: Path, repository_root: Path) -> Path:
  try:
    repository = repository_root.expanduser().resolve(strict=True)
    selected = output_root.expanduser().resolve(strict=False)
  except OSError as error:
    raise ExtractCaptureError(
      "unsafe_output",
      "output root could not be resolved safely",
    ) from error
  if selected == repository or _is_inside(selected, repository):
    raise ExtractCaptureError(
      "unsafe_output",
      "output root must remain outside the repository",
    )
  return selected


def _capture_identity(
    output_root: Path,
    uuid_factory: Callable[[], uuid.UUID],
) -> tuple[str, Path]:
  generated = uuid_factory()
  if type(generated) is not uuid.UUID:
    raise ExtractCaptureError(
      "unsafe_output",
      "capture identifier generation failed",
    )
  capture_id = f"capture-{generated}"
  destination = output_root / capture_id
  if destination.exists() or destination.is_symlink():
    raise ExtractCaptureError(
      "unsafe_output",
      "capture destination must not already exist",
    )
  return capture_id, destination


def extract_capture(
    *,
    profile: InspectionProfile,
    output_root: Path,
    repository_root: Path,
    environment: Mapping[str, str],
    runner: CommandRunner = _default_runner,
    binary_runner: BinaryCommandRunner = _default_binary_runner,
    primary_probe: Callable[
      [Path, InspectionProfile], bool
    ] = primary_database_is_schema_compatible,
    doctor_fn: Callable[[Mapping[str, str]], DoctorReport] = doctor,
    validator: Callable[
      [Path, Path, InspectionProfile], OfflineCaptureReport
    ] = validate_offline_database,
    uuid_factory: Callable[[], uuid.UUID] = uuid.uuid4,
) -> ExtractCaptureResult:
  if (
    type(profile) is not InspectionProfile
    or profile.name != _VERSION_NAME
    or profile.package_name != _PACKAGE_NAME
    or profile.version_name != _VERSION_NAME
    or profile.version_code != _VERSION_CODE
  ):
    raise ExtractCaptureError(
      "profile",
      "only the exact Naver Map 6.8.0.5 profile is supported",
    )

  selected_root = _resolve_output_root(output_root, repository_root)
  capture_id, capture_directory = _capture_identity(
    selected_root, uuid_factory
  )
  raw_directory = capture_directory / "raw" / "naver-diagnostic"
  bundle_directory = capture_directory / "bundle"
  if (
    raw_directory == bundle_directory
    or _is_inside(raw_directory, bundle_directory)
    or _is_inside(bundle_directory, raw_directory)
  ):
    raise ExtractCaptureError(
      "unsafe_output",
      "raw and bundle directories must not be nested",
    )

  adb = _resolve_adb(environment, doctor_fn)
  devices = _run_checked(
    runner,
    (adb, "devices", "-l"),
    "device enumeration",
  )
  serial = _gated_serial(devices.stdout)
  package = _run_checked(
    runner,
    (
      adb, "-s", serial, "shell", "dumpsys", "package", _PACKAGE_NAME,
    ),
    "package inspection",
  )
  _validate_installed_package(package.stdout)

  try:
    selected_root.mkdir(parents=True, exist_ok=True)
    confirmed_root = _resolve_output_root(selected_root, repository_root)
    if confirmed_root != selected_root:
      raise ExtractCaptureError(
        "unsafe_output",
        "output root changed while preparing capture",
      )
    capture_directory.mkdir(exist_ok=False)
    raw_directory.mkdir(parents=True)
  except ExtractCaptureError:
    raise
  except FileExistsError as error:
    raise ExtractCaptureError(
      "unsafe_output",
      "capture destination must not already exist",
    ) from error
  except OSError as error:
    raise ExtractCaptureError(
      "unsafe_output",
      "capture destination could not be created",
    ) from error

  offline = profile.diagnostic_payload.offline_capture
  export_name = profile.diagnostic_payload.capture_sharing.export_database_name
  _read_capture_file(
    binary_runner=binary_runner,
    adb=adb,
    serial=serial,
    uri=_capture_uri(profile, export_name),
    destination=raw_directory / offline.database_name,
    expected_database=True,
  )
  if not primary_probe(raw_directory, profile):
    raise ExtractCaptureError(
      "validation_failed",
      "exported capture database is incompatible",
    )
  try:
    copied_raw = raw_directory.resolve(strict=True)
  except OSError as error:
    raise ExtractCaptureError(
      "pull_incomplete",
      "copied diagnostic directory is missing",
    ) from error
  if (
    not copied_raw.is_dir()
    or raw_directory.is_symlink()
    or copied_raw == bundle_directory
    or _is_inside(copied_raw, bundle_directory)
    or _is_inside(bundle_directory, copied_raw)
  ):
    raise ExtractCaptureError(
      "unsafe_output",
      "copied raw and bundle directories must remain separate",
    )

  try:
    report = validator(copied_raw, bundle_directory, profile)
  except OfflineCaptureError:
    raise
  except Exception as error:
    raise ExtractCaptureError(
      "validation_failed",
      "offline capture validation failed",
    ) from error
  return ExtractCaptureResult(
    capture_id=capture_id,
    capture_directory=capture_directory.resolve(strict=True),
    raw_directory=copied_raw,
    bundle_directory=bundle_directory.resolve(strict=True),
    report=report,
  )


def _error_json(code: str, detail: str) -> str:
  return json.dumps(
    {
      "schema_version": 1,
      "command": "extract-capture",
      "success": False,
      "code": code,
      "detail": detail,
    },
    ensure_ascii=False,
    sort_keys=True,
    separators=(",", ":"),
  )


def run_extract_capture_cli(
    arguments: Sequence[str],
    environment: Mapping[str, str] | None = None,
) -> int:
  try:
    selected = _parse_arguments(arguments)
    profile = load_profile(selected.profile)
    result = extract_capture(
      profile=profile,
      output_root=selected.output_root,
      repository_root=REPO_ROOT,
      environment=os.environ if environment is None else environment,
    )
  except ExtractCaptureUsageError as error:
    print(f"extract-capture usage error: {error}", file=sys.stderr)
    return 64
  except ProfileError as error:
    print(_error_json("profile", str(error)))
    return 2
  except ExtractCaptureError as error:
    print(_error_json(error.code, error.detail))
    return 2
  except OfflineCaptureError as error:
    print(_error_json("validation_failed", str(error)))
    return 2
  print(json.dumps(
    result.as_dict(),
    ensure_ascii=False,
    sort_keys=True,
    separators=(",", ":"),
  ))
  return 0


__all__ = [
  "CommandResult",
  "ExtractCaptureArguments",
  "ExtractCaptureError",
  "ExtractCaptureResult",
  "ExtractCaptureUsageError",
  "_parse_arguments",
  "extract_capture",
  "run_extract_capture_cli",
]
