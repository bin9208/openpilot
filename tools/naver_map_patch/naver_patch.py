#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///

# ─── How to run ───
# 1. Install uv: curl -LsSf https://astral.sh/uv/install.sh | sh
# 2. Direct: python tools/naver_map_patch/naver_patch.py doctor --json
# 3. With uv: uv run --script tools/naver_map_patch/naver_patch.py doctor --json
# ──────────────────

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Final


REPO_ROOT: Final = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
  sys.path.insert(0, str(REPO_ROOT))


BUILD_TOOLS_VERSION: Final = "36.0.0"
MINIMUM_PYTHON: Final = (3, 11)
PROBE_TIMEOUT_SECONDS: Final = 5.0


@dataclass(frozen=True, slots=True)
class VersionProbe:
  success: bool
  output: str
  detail: str


@dataclass(frozen=True, slots=True)
class Requirement:
  name: str
  required: str
  success: bool
  path: str
  version: str
  package_version: str
  detail: str


@dataclass(frozen=True, slots=True)
class ToolSpec:
  name: str
  required: str
  path: Path
  arguments: tuple[str, ...]
  version_pattern: str
  package_version: str


@dataclass(frozen=True, slots=True)
class DoctorReport:
  sdk_root: Path
  requirements: tuple[Requirement, ...]

  @property
  def success(self) -> bool:
    return all(requirement.success for requirement in self.requirements)

  def to_json(self) -> str:
    payload = {
      "schema_version": 1,
      "command": "doctor",
      "success": self.success,
      "sdk_root": str(self.sdk_root),
      "requirements": [
        {
          "name": item.name,
          "required": item.required,
          "success": item.success,
          "path": item.path,
          "version": item.version,
          "package_version": item.package_version,
          "detail": item.detail,
        }
        for item in self.requirements
      ],
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _probe_version(
  command: tuple[str, ...], environment: Mapping[str, str], timeout_seconds: float,
) -> VersionProbe:
  process_environment = os.environ.copy()
  process_environment.update(environment)
  try:
    process = subprocess.run(
      command,
      capture_output=True,
      text=True,
      timeout=timeout_seconds,
      check=False,
      env=process_environment,
    )
  except subprocess.TimeoutExpired:
    return VersionProbe(False, "", f"version probe timed out after {timeout_seconds:g}s")
  except OSError as error:
    return VersionProbe(False, "", f"version probe could not start: {error.strerror or error.__class__.__name__}")
  output = "\n".join(part.strip() for part in (process.stdout, process.stderr) if part.strip())
  if process.returncode != 0:
    return VersionProbe(False, "", f"version probe exited {process.returncode}")
  return VersionProbe(True, output, "")


def _resolved(path: Path) -> str:
  return str(path.expanduser().resolve())


def _sdk_root(environment: Mapping[str, str]) -> Path:
  if "ANDROID_SDK_ROOT" in environment:
    return Path(environment["ANDROID_SDK_ROOT"])
  if "ANDROID_HOME" in environment:
    return Path(environment["ANDROID_HOME"])
  if os.name == "nt":
    local_app_data = environment.get("LOCALAPPDATA")
    if local_app_data:
      return Path(local_app_data) / "Android" / "Sdk"
    return Path.home() / "AppData" / "Local" / "Android" / "Sdk"
  if sys.platform == "darwin":
    return Path.home() / "Library" / "Android" / "sdk"
  return Path.home() / "Android" / "Sdk"


def _java_path(environment: Mapping[str, str]) -> Path:
  executable = "java.exe" if os.name == "nt" else "java"
  candidates: list[Path] = []
  for variable in ("ANDROID_STUDIO_JBR", "JAVA_HOME"):
    value = environment.get(variable)
    if value:
      candidates.append(Path(value) / "bin" / executable)
  default_jbr_bins = {
    "win32": Path("C:/Program Files/Android/Android Studio/jbr/bin"),
    "darwin": Path("/Applications/Android Studio.app/Contents/jbr/Contents/Home/bin"),
  }
  candidates.append(default_jbr_bins.get(sys.platform, Path("/opt/android-studio/jbr/bin")) / executable)
  return next((candidate for candidate in candidates if candidate.is_file()), candidates[0])


def _tool_path(directory: Path, name: str, script: bool = False) -> Path:
  if os.name != "nt":
    return directory / name
  suffix = ".bat" if script else ".exe"
  return directory / f"{name}{suffix}"


def _check_tool(spec: ToolSpec, environment: Mapping[str, str]) -> Requirement:
  path = Path(_resolved(spec.path))
  if not path.is_file():
    return Requirement(spec.name, spec.required, False, str(path), "", spec.package_version, "not found")
  if not spec.arguments:
    return Requirement(spec.name, spec.required, True, str(path), spec.package_version, spec.package_version, "")
  probe = _probe_version((str(path), *spec.arguments), environment, PROBE_TIMEOUT_SECONDS)
  if not probe.success:
    return Requirement(spec.name, spec.required, False, str(path), "", spec.package_version, probe.detail)
  matched = re.search(spec.version_pattern, probe.output, flags=re.MULTILINE | re.IGNORECASE)
  if matched is None:
    return Requirement(spec.name, spec.required, False, str(path), "", spec.package_version, "unparseable version output")
  return Requirement(spec.name, spec.required, True, str(path), matched.group(1), spec.package_version, "")


def _platform_requirement(sdk_root: Path) -> Requirement:
  candidates = tuple((sdk_root / "platforms").glob("android-*/android.jar"))
  jars = tuple(path for path in candidates if path.is_file())
  if not jars:
    expected = candidates[0] if candidates else sdk_root / "platforms" / "android-*" / "android.jar"
    return Requirement(
      "android_platform", "one or more installed android.jar", False, _resolved(expected), "", "", "regular file not found",
    )
  selected = max(jars, key=lambda path: tuple(int(part) for part in re.findall(r"\d+", path.parent.name)))
  version = selected.parent.name.removeprefix("android-")
  return Requirement("android_platform", "one or more installed android.jar", True, _resolved(selected), version, version, "")


def doctor(environment: Mapping[str, str]) -> DoctorReport:
  sdk_root = Path(_resolved(_sdk_root(environment)))
  java_path = _java_path(environment)
  java_home = java_path.parent.parent
  java_environment = {
    "JAVA_HOME": str(java_home),
    "PATH": f"{java_path.parent}{os.pathsep}{environment.get('PATH', '')}",
  }
  python_version = ".".join(str(part) for part in sys.version_info[:3])
  requirements: list[Requirement] = [
    Requirement(
      "python", ">=3.11", sys.version_info >= MINIMUM_PYTHON, _resolved(Path(sys.executable)), python_version,
      python_version, "" if sys.version_info >= MINIMUM_PYTHON else "Python 3.11 or newer is required",
    ),
  ]
  java_spec = ToolSpec("java", "Android Studio JBR/JDK", java_path, ("-version",), r'(?:openjdk|java) version "([^\"]+)"', "")
  requirements.append(_check_tool(java_spec, java_environment))
  build_tools = sdk_root / "build-tools" / BUILD_TOOLS_VERSION
  specs = (
    ToolSpec("aapt2", "Build Tools 36.0.0", _tool_path(build_tools, "aapt2"), ("version",), r"([0-9]+(?:\.[0-9]+)*(?:-[0-9]+)?)$", BUILD_TOOLS_VERSION),
    ToolSpec("d8", "Build Tools 36.0.0", _tool_path(build_tools, "d8", script=True), ("--version",), r"\bD8\s+([^\s]+)", BUILD_TOOLS_VERSION),
    ToolSpec("zipalign", "Build Tools 36.0.0", _tool_path(build_tools, "zipalign"), (), "", BUILD_TOOLS_VERSION),
    ToolSpec("apksigner", "Build Tools 36.0.0", _tool_path(build_tools, "apksigner", script=True), ("version",), r"^([^\s]+)$", BUILD_TOOLS_VERSION),
    ToolSpec("adb", "Android SDK platform-tools", _tool_path(sdk_root / "platform-tools", "adb"), ("version",), r"^Version\s+([^\s]+)", ""),
  )
  requirements.extend(_check_tool(spec, java_environment) for spec in specs)
  requirements.append(_platform_requirement(sdk_root))
  return DoctorReport(sdk_root, tuple(requirements))


def main(arguments: Sequence[str] | None = None) -> int:
  selected_arguments = tuple(sys.argv[1:] if arguments is None else arguments)
  if selected_arguments and selected_arguments[0] == "inspect":
    from tools.naver_map_patch.inspection_cli import run_inspect_cli

    return run_inspect_cli(selected_arguments, REPO_ROOT, os.environ)
  if selected_arguments and selected_arguments[0] in {"build", "verify", "install-command"}:
    from tools.naver_map_patch.packaging_cli import run_packaging_cli

    return run_packaging_cli(selected_arguments, os.environ)
  if selected_arguments and selected_arguments[0] == "extract-capture":
    from tools.naver_map_patch.offline_capture_cli import run_extract_capture_cli

    return run_extract_capture_cli(selected_arguments, os.environ)
  if selected_arguments not in (("doctor", "--json"), ("--json", "doctor")):
    print("usage: naver_patch.py doctor --json", file=sys.stderr)
    return 64
  report = doctor(os.environ)
  print(report.to_json())
  return 0 if report.success else 2


if __name__ == "__main__":
  raise SystemExit(main())
