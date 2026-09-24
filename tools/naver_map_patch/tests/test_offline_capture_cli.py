from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import subprocess
import sys
import traceback
from types import ModuleType
import uuid

import pytest

import tools.naver_map_patch.naver_patch as naver_patch
from tools.naver_map_patch.offline_capture_cli import (
  CommandResult,
  ExtractCaptureError,
  _default_binary_runner,
  _default_runner,
  _parse_arguments,
  _resolve_adb,
  _run_checked,
  extract_capture,
  run_extract_capture_cli,
)
from tools.naver_map_patch.profile import load_profile


SERIAL = "private-device-serial"
CAPTURE_UUID = uuid.UUID("12345678-1234-5678-9234-567812345678")
CAPTURE_AUTHORITY = "com.nhn.android.nmap.fileprovider"
CAPTURE_ROOT_NAME = "navi_trace"
CAPTURE_RELATIVE_DIRECTORY = "naver-diagnostic"
CAPTURE_FILE_NAMES = ("capture-v3-export.sqlite3",)
LOCAL_DATABASE_NAME = "capture-v3.sqlite3"


def _capture_uri(name: str) -> str:
  return (
    f"content://{CAPTURE_AUTHORITY}/{CAPTURE_ROOT_NAME}/"
    f"{CAPTURE_RELATIVE_DIRECTORY}/{name}"
  )


@dataclass
class FakeReport:
  counts: dict[str, int]

  def as_dict(self) -> dict[str, object]:
    return {
      "file_count": 3,
      "frame_count": sum(self.counts.values()),
      "run_count": 1,
      "complete": False,
      "counts": dict(self.counts),
      "output_files": [
        "hashes.json",
        "summary.json",
        "manifest.json",
        "accessor_shapes.json",
      ],
    }


class RecordingRunner:
  def __init__(
      self,
      *,
      devices: str | None = None,
      package: str | None = None,
      fail_at: str | None = None,
  ) -> None:
    self.devices = (
      "List of devices attached\n"
      f"{SERIAL}\tdevice product:synthetic model:synthetic transport_id:1\n"
      if devices is None else devices
    )
    self.package = (
      "Packages:\n"
      "  Package [com.nhn.android.nmap]\n"
      "    versionCode=60800007 minSdk=23 targetSdk=35\n"
      "    versionName=6.8.0.5\n"
      if package is None else package
    )
    self.fail_at = fail_at
    self.commands: list[tuple[str, ...]] = []
    self.content_payload = b"SQLite format 3\x00\xffsynthetic"

  def __call__(self, argv: tuple[str, ...]) -> CommandResult:
    self.commands.append(argv)
    if argv[1:] == ("devices", "-l"):
      return self._result("devices", self.devices)
    if argv[3:] == ("shell", "dumpsys", "package", "com.nhn.android.nmap"):
      return self._result("dumpsys", self.package)
    raise AssertionError(f"unexpected command shape: {argv!r}")

  def binary(
      self, argv: tuple[str, ...], output: Path,
  ) -> tuple[int, bytes]:
    self.commands.append(argv)
    if argv[3:7] != ("exec-out", "content", "read", "--uri"):
      raise AssertionError(f"unexpected binary command shape: {argv!r}")
    name = argv[7].rsplit("/", 1)[-1]
    if self.fail_at == f"content:{name}":
      return 1, f"{SERIAL}: SecurityException private path".encode()
    if name == CAPTURE_FILE_NAMES[0]:
      output.write_bytes(self.content_payload)
      return 0, b""
    raise AssertionError(f"unexpected capture file: {name}")

  def _result(self, operation: str, stdout: str) -> CommandResult:
    if self.fail_at == operation:
      return CommandResult(1, stdout, f"{SERIAL}: private failure")
    return CommandResult(0, stdout, "")


def _adb_requirement():
  return naver_patch.Requirement(
    name="adb",
    required="Android SDK platform-tools",
    success=True,
    path="C:/Android/platform-tools/adb.exe",
    version="1.0.41",
    package_version="",
    detail="",
  )


def _doctor(_environment):
  return naver_patch.DoctorReport(Path("C:/Android"), (_adb_requirement(),))


def test_adb_resolution_ignores_unrelated_failed_doctor_requirements() -> None:
  failed_java = naver_patch.Requirement(
    name="java",
    required="Android Studio JBR/JDK",
    success=False,
    path="",
    version="",
    package_version="",
    detail="not found",
  )

  resolved = _resolve_adb(
    {},
    lambda _environment: naver_patch.DoctorReport(
      Path("C:/Android"), (failed_java, _adb_requirement())
    ),
  )

  assert resolved == "C:/Android/platform-tools/adb.exe"


def test_adb_resolution_fails_closed_without_one_successful_adb_requirement() -> None:
  with pytest.raises(ExtractCaptureError, match="adb requirement"):
    _resolve_adb(
      {},
      lambda _environment: naver_patch.DoctorReport(Path("C:/Android"), ()),
    )


def _run(
    tmp_path: Path,
    runner: RecordingRunner,
    *,
    repository_root: Path | None = None,
    validator_calls: list[tuple[Path, Path, str]] | None = None,
):
  calls = [] if validator_calls is None else validator_calls
  selected_repository = (
    tmp_path / "repository" if repository_root is None else repository_root
  )
  selected_repository.mkdir(parents=True, exist_ok=True)

  def validate(raw: Path, bundle: Path, profile):
    calls.append((raw, bundle, profile.name))
    bundle.mkdir()
    for name in ("hashes.json", "summary.json", "manifest.json", "accessor_shapes.json"):
      (bundle / name).write_text("{}", encoding="utf-8")
    return FakeReport({
      "status": 1,
      "tbt_current": 0,
      "tbt_next": 0,
      "safety": 2,
      "route": 1,
      "lane": 0,
    })

  return extract_capture(
    profile=load_profile("6.8.0.5"),
    output_root=tmp_path / "outside",
    repository_root=selected_repository,
    environment={},
    runner=runner,
    binary_runner=runner.binary,
    primary_probe=lambda *_: True,
    doctor_fn=_doctor,
    validator=validate,
    uuid_factory=lambda: CAPTURE_UUID,
  )


def test_exact_adb_gate_and_command_order_use_the_gated_serial(tmp_path: Path) -> None:
  runner = RecordingRunner()

  result = _run(tmp_path, runner)

  assert runner.commands[:2] == [
    ("C:/Android/platform-tools/adb.exe", "devices", "-l"),
    (
      "C:/Android/platform-tools/adb.exe", "-s", SERIAL,
      "shell", "dumpsys", "package", "com.nhn.android.nmap",
    ),
  ]
  assert runner.commands[2:] == [
    (
      "C:/Android/platform-tools/adb.exe", "-s", SERIAL,
      "exec-out", "content", "read", "--uri", _capture_uri(name),
    )
    for name in CAPTURE_FILE_NAMES
  ]
  capture = tmp_path / "outside" / f"capture-{CAPTURE_UUID}"
  assert result.capture_id == f"capture-{CAPTURE_UUID}"
  assert result.capture_directory == capture.resolve()


@pytest.mark.parametrize(
  "devices",
  [
    "List of devices attached\n",
    f"List of devices attached\n{SERIAL}\toffline\n",
    f"List of devices attached\n{SERIAL}\tunauthorized\n",
    (
      "List of devices attached\n"
      f"{SERIAL}\tdevice\n"
      "second-private-serial\tdevice\n"
    ),
    (
      "List of devices attached\n"
      f"{SERIAL}\tdevice\n"
      "second-private-serial\toffline\n"
    ),
  ],
  ids=["zero", "offline", "unauthorized", "two-authorized", "authorized-plus-offline"],
)
def test_gate_requires_exactly_one_total_transport_row_in_device_state(
    tmp_path: Path, devices: str,
) -> None:
  runner = RecordingRunner(devices=devices)

  with pytest.raises(ExtractCaptureError, match="exactly one authorized device"):
    _run(tmp_path, runner)

  assert len(runner.commands) == 1
  assert runner.commands[0][1:] == ("devices", "-l")


@pytest.mark.parametrize(
  "package",
  [
    "Unable to find package: com.nhn.android.nmap\n",
    "versionCode=60800006\nversionName=6.8.0.5\n",
    "versionCode=60800007\nversionName=6.8.0.4\n",
    "versionCode=60800007\nversionName=6.8.0.5\nversionName=private-duplicate\n",
  ],
)
def test_package_or_version_mismatch_fails_before_content_reads(
    tmp_path: Path, package: str,
) -> None:
  runner = RecordingRunner(package=package)

  with pytest.raises(ExtractCaptureError, match="package version"):
    _run(tmp_path, runner)

  assert len(runner.commands) == 2
  assert runner.commands[-1][3:6] == ("shell", "dumpsys", "package")


@pytest.mark.parametrize("operation", ["devices", "dumpsys"])
def test_command_failures_never_expose_the_serial(
    tmp_path: Path, operation: str,
) -> None:
  runner = RecordingRunner(fail_at=operation)

  with pytest.raises(ExtractCaptureError) as captured:
    _run(tmp_path, runner)

  assert SERIAL not in str(captured.value)


def _exception_chain_text(error: BaseException) -> str:
  pending: list[BaseException] = [error]
  seen: set[int] = set()
  fragments: list[str] = []
  while pending:
    current = pending.pop()
    if id(current) in seen:
      continue
    seen.add(id(current))
    fragments.extend((repr(current), str(current)))
    if current.__cause__ is not None:
      pending.append(current.__cause__)
    if current.__context__ is not None:
      pending.append(current.__context__)
  fragments.extend(traceback.format_exception(error))
  return "\n".join(fragments)


def test_default_runner_drops_timeout_command_and_exception_chain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
  command = ("adb", "-s", SERIAL, "shell", "dumpsys", "package", "private")

  def timeout(*_args, **_kwargs):
    raise subprocess.TimeoutExpired(command, 300)

  monkeypatch.setattr(subprocess, "run", timeout)

  with pytest.raises(ExtractCaptureError) as captured:
    _default_runner(command)

  assert captured.value.code == "adb_command_failed"
  assert captured.value.detail == "ADB command could not complete"
  assert captured.value.__cause__ is None
  assert captured.value.__context__ is None
  assert SERIAL not in _exception_chain_text(captured.value)


def test_default_binary_runner_streams_opaque_stdout_to_partial_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
  output = tmp_path / "capture.part"
  payload = b"SQLite\x00\xffbinary"

  def run(argv, **kwargs):
    assert kwargs["stderr"] is subprocess.PIPE
    assert "text" not in kwargs
    assert "encoding" not in kwargs
    kwargs["stdout"].write(payload)
    return subprocess.CompletedProcess(argv, 0, None, b"")

  monkeypatch.setattr(subprocess, "run", run)

  assert _default_binary_runner(("adb", "exec-out"), output) == (0, b"")
  assert output.read_bytes() == payload


def test_default_binary_runner_drops_timeout_serial_and_uri_chain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
  private_uri = _capture_uri(CAPTURE_FILE_NAMES[0])
  command = ("adb", "-s", SERIAL, "exec-out", "content", "read", "--uri", private_uri)

  def timeout(*_args, **_kwargs):
    raise subprocess.TimeoutExpired(command, 300)

  monkeypatch.setattr(subprocess, "run", timeout)

  with pytest.raises(ExtractCaptureError) as captured:
    _default_binary_runner(command, tmp_path / "capture.part")

  exposed = _exception_chain_text(captured.value)
  assert captured.value.code == "content_read_failed"
  assert captured.value.__cause__ is None
  assert captured.value.__context__ is None
  assert SERIAL not in exposed
  assert private_uri not in exposed


@pytest.mark.parametrize(
  "raised",
  [
    RuntimeError(f"runner retained {SERIAL}"),
    ExtractCaptureError("private", f"runner retained {SERIAL}"),
  ],
  ids=["arbitrary-runner-error", "runner-supplied-extract-error"],
)
def test_checked_runner_drops_all_runner_exception_state(
    raised: Exception,
) -> None:
  def fail(_argv: tuple[str, ...]) -> CommandResult:
    raise raised

  with pytest.raises(ExtractCaptureError) as captured:
    _run_checked(
      fail,
      ("adb", "-s", SERIAL, "shell", "dumpsys", "package", "private"),
      "package inspection",
    )

  assert captured.value is not raised
  assert captured.value.code == "adb_command_failed"
  assert captured.value.detail == "ADB package inspection command could not complete"
  assert captured.value.__cause__ is None
  assert captured.value.__context__ is None
  assert SERIAL not in _exception_chain_text(captured.value)


@pytest.mark.parametrize("raised", [KeyboardInterrupt(), SystemExit(17)])
def test_checked_runner_preserves_process_control_exceptions(
    raised: BaseException,
) -> None:
  def stop(_argv: tuple[str, ...]) -> CommandResult:
    raise raised

  with pytest.raises(type(raised)) as captured:
    _run_checked(stop, ("adb", "devices", "-l"), "device enumeration")

  assert captured.value is raised


def test_only_safe_fixed_adb_operations_are_constructed(tmp_path: Path) -> None:
  runner = RecordingRunner()

  _run(tmp_path, runner)

  flattened = " ".join(" ".join(command) for command in runner.commands).lower()
  assert "uninstall" not in flattened
  assert "pm clear" not in flattened
  assert " shell rm" not in flattened
  assert " delete" not in flattened
  assert "force-stop" not in flattened
  assert " pull " not in f" {flattened} "
  assert "chmod" not in flattened
  assert all(command[3:7] == ("exec-out", "content", "read", "--uri")
             for command in runner.commands[2:])


def test_content_read_preserves_binary_stdout_without_text_decoding(
    tmp_path: Path,
) -> None:
  runner = RecordingRunner()

  result = _run(tmp_path, runner)

  assert (
    result.raw_directory / LOCAL_DATABASE_NAME
  ).read_bytes() == runner.content_payload


def test_content_read_rejects_exit_zero_stderr(
    tmp_path: Path,
) -> None:
  runner = RecordingRunner()
  stderr = b"warning despite exit zero"

  def binary(argv: tuple[str, ...], output: Path) -> tuple[int, bytes]:
    runner.commands.append(argv)
    output.write_bytes(b"partial-private")
    return 0, stderr

  repository = tmp_path / "repository"
  repository.mkdir()
  with pytest.raises(ExtractCaptureError) as captured:
    extract_capture(
      profile=load_profile("6.8.0.5"),
      output_root=tmp_path / "outside",
      repository_root=repository,
      environment={},
      runner=runner,
      binary_runner=binary,
      primary_probe=lambda *_: True,
      doctor_fn=_doctor,
      validator=lambda *_: pytest.fail("validator must not run"),
      uuid_factory=lambda: CAPTURE_UUID,
    )

  assert captured.value.code == "content_read_failed"
  capture = tmp_path / "outside" / f"capture-{CAPTURE_UUID}"
  assert not tuple(capture.rglob("*.part"))
  assert SERIAL not in _exception_chain_text(captured.value)


def test_content_read_rejects_provider_error_written_to_stdout_with_exit_zero(
    tmp_path: Path,
) -> None:
  runner = RecordingRunner()

  def binary(argv: tuple[str, ...], output: Path) -> tuple[int, bytes]:
    runner.commands.append(argv)
    output.write_bytes(
      b"Error while accessing provider:com.nhn.android.nmap.fileprovider\n"
      b"java.lang.IllegalStateException: diagnostic capture quiesce failed\n"
    )
    return 0, b""

  repository = tmp_path / "repository"
  repository.mkdir()
  with pytest.raises(ExtractCaptureError) as captured:
    extract_capture(
      profile=load_profile("6.8.0.5"),
      output_root=tmp_path / "outside",
      repository_root=repository,
      environment={},
      runner=runner,
      binary_runner=binary,
      primary_probe=lambda *_: pytest.fail("primary probe must not run"),
      doctor_fn=_doctor,
      validator=lambda *_: pytest.fail("validator must not run"),
      uuid_factory=lambda: CAPTURE_UUID,
    )

  assert captured.value.code == "content_read_failed"
  capture = tmp_path / "outside" / f"capture-{CAPTURE_UUID}"
  assert not tuple(capture.rglob("*.part"))
  assert not tuple(capture.rglob("*.sqlite3"))
  assert SERIAL not in _exception_chain_text(captured.value)


def test_mandatory_database_requires_exact_sqlite_header(tmp_path: Path) -> None:
  runner = RecordingRunner()
  runner.content_payload = b"not a sqlite database"

  with pytest.raises(ExtractCaptureError) as captured:
    _run(tmp_path, runner)

  assert captured.value.code == "content_read_failed"
  capture = tmp_path / "outside" / f"capture-{CAPTURE_UUID}"
  assert not tuple(capture.rglob("*.part"))
  assert not tuple(capture.rglob("*.sqlite3"))


def test_single_export_snapshot_does_not_read_sidecars_or_recovery(
    tmp_path: Path,
) -> None:
  runner = RecordingRunner()

  result = _run(tmp_path, runner)

  assert sorted(path.name for path in result.raw_directory.iterdir()) == [
    LOCAL_DATABASE_NAME,
  ]
  assert len(runner.commands[2:]) == 1
  assert runner.commands[2][7] == _capture_uri(CAPTURE_FILE_NAMES[0])


def test_binary_runner_exception_chain_hides_serial_uri_and_physical_path(
    tmp_path: Path,
) -> None:
  runner = RecordingRunner()
  private_uri = _capture_uri(CAPTURE_FILE_NAMES[0])
  private_path = tmp_path / "outside" / "private-target.part"

  def binary(_argv: tuple[str, ...], _output: Path) -> tuple[int, bytes]:
    raise RuntimeError(f"{SERIAL} {private_uri} {private_path}")

  repository = tmp_path / "repository"
  repository.mkdir()
  with pytest.raises(ExtractCaptureError) as captured:
    extract_capture(
      profile=load_profile("6.8.0.5"),
      output_root=tmp_path / "outside",
      repository_root=repository,
      environment={},
      runner=runner,
      binary_runner=binary,
      primary_probe=lambda *_: True,
      doctor_fn=_doctor,
      validator=lambda *_: pytest.fail("validator must not run"),
      uuid_factory=lambda: CAPTURE_UUID,
    )

  exposed = _exception_chain_text(captured.value)
  assert captured.value.code == "content_read_failed"
  assert SERIAL not in exposed
  assert private_uri not in exposed
  assert str(private_path) not in exposed


def test_schema_incompatible_export_fails_without_recovery_or_sidecar_reads(
    tmp_path: Path,
) -> None:
  runner = RecordingRunner()
  repository = tmp_path / "repository"
  repository.mkdir()

  with pytest.raises(ExtractCaptureError, match="incompatible"):
    extract_capture(
      profile=load_profile("6.8.0.5"),
      output_root=tmp_path / "outside",
      repository_root=repository,
      environment={},
      runner=runner,
      binary_runner=runner.binary,
      primary_probe=lambda *_: False,
      doctor_fn=_doctor,
      validator=lambda *_: pytest.fail("validator must not run"),
      uuid_factory=lambda: CAPTURE_UUID,
    )

  assert runner.commands[2:] == [
    (
      "C:/Android/platform-tools/adb.exe", "-s", SERIAL,
      "exec-out", "content", "read", "--uri", _capture_uri(name),
    )
    for name in CAPTURE_FILE_NAMES
  ]


def test_validation_receives_only_the_pulled_raw_and_new_bundle(
    tmp_path: Path,
) -> None:
  runner = RecordingRunner()
  calls: list[tuple[Path, Path, str]] = []

  result = _run(tmp_path, runner, validator_calls=calls)

  assert calls == [(
    result.capture_directory / "raw" / "naver-diagnostic",
    result.capture_directory / "bundle",
    "6.8.0.5",
  )]
  assert result.report.counts["status"] == 1


def test_existing_capture_destination_is_rejected_without_adb_access(
    tmp_path: Path,
) -> None:
  capture = tmp_path / "outside" / f"capture-{CAPTURE_UUID}"
  capture.mkdir(parents=True)
  runner = RecordingRunner()

  with pytest.raises(ExtractCaptureError, match="must not already exist"):
    _run(tmp_path, runner)

  assert runner.commands == []


@pytest.mark.parametrize("relation", ["inside", "same"])
def test_output_root_inside_repository_is_rejected(
    tmp_path: Path, relation: str,
) -> None:
  repository = tmp_path / "repository"
  repository.mkdir()
  output = repository if relation == "same" else repository / "capture-output"
  runner = RecordingRunner()

  with pytest.raises(ExtractCaptureError, match="outside the repository"):
    extract_capture(
      profile=load_profile("6.8.0.5"),
      output_root=output,
      repository_root=repository,
      environment={},
      runner=runner,
      doctor_fn=_doctor,
      validator=lambda *_: pytest.fail("validator must not run"),
      uuid_factory=lambda: CAPTURE_UUID,
    )

  assert runner.commands == []


def test_symlink_output_resolving_into_repository_is_rejected(
    tmp_path: Path,
) -> None:
  repository = tmp_path / "repository"
  repository.mkdir()
  link = tmp_path / "capture-link"
  try:
    link.symlink_to(repository, target_is_directory=True)
  except OSError:
    pytest.skip("symbolic links are unavailable on this host")
  runner = RecordingRunner()

  with pytest.raises(ExtractCaptureError, match="outside the repository"):
    extract_capture(
      profile=load_profile("6.8.0.5"),
      output_root=link,
      repository_root=repository,
      environment={},
      runner=runner,
      doctor_fn=_doctor,
      validator=lambda *_: pytest.fail("validator must not run"),
      uuid_factory=lambda: CAPTURE_UUID,
    )

  assert runner.commands == []


def test_parser_accepts_only_the_fixed_public_surface() -> None:
  parsed = _parse_arguments((
    "extract-capture",
    "--profile", "6.8.0.5",
    "--output-root", "C:/tmp/naver-map-capture-6805",
    "--json",
  ))
  assert parsed.profile == "6.8.0.5"
  assert parsed.output_root == Path("C:/tmp/naver-map-capture-6805")

  for forbidden in ("--serial", "--device-path", "--package", "--version", "--overwrite"):
    with pytest.raises(Exception, match=f"unsupported argument: {forbidden}"):
      _parse_arguments((
        "extract-capture",
        "--profile", "6.8.0.5",
        "--output-root", "C:/tmp/naver-map-capture-6805",
        forbidden, "private",
        "--json",
      ))


def test_success_json_and_bundle_never_contain_the_device_serial(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch,
) -> None:
  runner = RecordingRunner()
  monkeypatch.setattr(
    "tools.naver_map_patch.offline_capture_cli.extract_capture",
    lambda **_kwargs: _run(tmp_path, runner),
  )

  assert run_extract_capture_cli((
    "extract-capture", "--profile", "6.8.0.5",
    "--output-root", str(tmp_path / "ignored"), "--json",
  ), {}) == 0

  output = capsys.readouterr()
  payload = json.loads(output.out)
  assert payload["success"] is True
  assert SERIAL not in output.out
  assert SERIAL not in output.err
  for path in (tmp_path / "outside" / f"capture-{CAPTURE_UUID}" / "bundle").iterdir():
    assert SERIAL not in path.read_text(encoding="utf-8")


def test_root_dispatches_extract_capture_without_importing_packaging(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
  calls: list[tuple[tuple[str, ...], object]] = []
  offline_module = ModuleType("tools.naver_map_patch.offline_capture_cli")

  def run(arguments, environment):
    calls.append((tuple(arguments), environment))
    return 23

  offline_module.run_extract_capture_cli = run  # type: ignore[attr-defined]
  packaging_module = ModuleType("tools.naver_map_patch.packaging_cli")

  def packaging(*_args, **_kwargs):
    raise AssertionError("packaging dispatch must not be entered")

  packaging_module.run_packaging_cli = packaging  # type: ignore[attr-defined]
  monkeypatch.setitem(sys.modules, offline_module.__name__, offline_module)
  monkeypatch.setitem(sys.modules, packaging_module.__name__, packaging_module)

  assert naver_patch.main((
    "extract-capture", "--profile", "6.8.0.5",
    "--output-root", "C:/tmp/naver-map-capture-6805", "--json",
  )) == 23
  assert calls and calls[0][0][0] == "extract-capture"


def test_offline_workflow_documentation_preserves_safety_and_evidence_boundaries() -> None:
  root = Path(__file__).parents[1]
  combined = "\n".join(
    path.read_text(encoding="utf-8")
    for path in (root / "README.md", root / "DIAGNOSTIC_CAPTURE.md")
  ).lower()
  combined = " ".join(combined.split())

  assert "extract-capture" in combined
  assert "adb connection" in combined
  assert "adb reverse" in combined
  assert "does not use force-stop" in combined
  assert "content read" in combined
  assert "fileprovider" in combined
  assert "never clears, uninstalls, or deletes" in combined
  assert "c:\\tmp" in combined
  assert "separate future explicit action" in combined
  assert "gps spoofing" in combined
  assert "simulation" in combined
  assert "all six real channels" in combined
