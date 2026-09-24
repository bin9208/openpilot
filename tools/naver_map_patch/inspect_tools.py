from __future__ import annotations

import base64
from collections.abc import Mapping, Sequence
import hashlib
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
import zipfile

from tools.naver_map_patch.profile import AnchorProfile, DexEntryProfile


class InspectionError(RuntimeError):
  def __init__(self, code: str, message: str) -> None:
    super().__init__(message)
    self.code = code
    self.message = message


@dataclass(frozen=True, slots=True)
class ToolPaths:
  aapt2: Path
  apksigner: Path
  java_home: Path
  gradle: Path


@dataclass(frozen=True, slots=True)
class ManifestFacts:
  package: str
  version_code: int
  version_name: str
  split_name: str | None
  split_types: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SignerFacts:
  signer_sha256: str
  source_stamp_sha256: str


@dataclass(frozen=True, slots=True)
class DexEntryFacts:
  size: int
  sha256: str


@dataclass(frozen=True, slots=True)
class ArchiveFacts:
  dex_count: int
  native_library_count: int


@dataclass(frozen=True, slots=True)
class AnchorScanRequest:
  base: Path
  anchors: tuple[AnchorProfile, ...]
  dexpatch_dir: Path


_FIELD_RE = re.compile(r"([A-Za-z][A-Za-z0-9]*)='([^']*)'")
_SHA_RE = r"([0-9a-fA-F]{64})"
_DEX_ENTRY_RE = re.compile(r"classes(?:[2-9]|[1-9][0-9]+)?\.dex\Z")
_NATIVE_ENTRY_RE = re.compile(r"lib/[^/]+/[^/]+\.so\Z")
_ANCHOR_COUNT_RE = re.compile(r"([a-z][a-z0-9_]*)\t([0-9]+)\Z")
_BUILD_TOOLS_VERSION = "36.0.0"


def _existing(candidates: Sequence[Path], label: str) -> Path:
  for candidate in candidates:
    if candidate.is_file():
      return candidate.resolve()
  raise InspectionError("tool_missing", f"required tool not found: {label}")


def _gradle_candidates(repo_root: Path, env: Mapping[str, str]) -> tuple[Path, ...]:
  executable = "gradle.bat" if os.name == "nt" else "gradle"
  wrapper = "gradlew.bat" if os.name == "nt" else "gradlew"
  explicit = env.get("NAVER_GRADLE")
  if explicit is not None:
    return (Path(explicit),)
  candidates = [
    repo_root / wrapper,
    repo_root / "tools" / "naver_map_patch" / "dexpatch" / wrapper,
  ]
  if gradle_home := env.get("GRADLE_HOME"):
    candidates.append(Path(gradle_home) / "bin" / executable)
  if resolved := shutil.which(executable, path=env.get("PATH")):
    candidates.append(Path(resolved))
  return tuple(candidates)


def discover_tools(repo_root: Path, environ: Mapping[str, str] | None = None) -> ToolPaths:
  env = os.environ if environ is None else environ
  local_app_data = env.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local"))
  sdk_candidates = [
    Path(value) for key in ("ANDROID_HOME", "ANDROID_SDK_ROOT") if (value := env.get(key))
  ] + [Path(local_app_data) / "Android" / "Sdk"]
  sdk = next((candidate for candidate in sdk_candidates if candidate.is_dir()), None)
  if sdk is None:
    raise InspectionError("tool_missing", "Android SDK not found")
  build_tools = sdk / "build-tools" / _BUILD_TOOLS_VERSION
  suffix = ".exe" if os.name == "nt" else ""
  batch = ".bat" if os.name == "nt" else ""
  aapt2 = _existing([build_tools / f"aapt2{suffix}"], f"aapt2 {_BUILD_TOOLS_VERSION}")
  apksigner = _existing([build_tools / f"apksigner{batch}"], f"apksigner {_BUILD_TOOLS_VERSION}")
  java_candidates = []
  if java_home := env.get("JAVA_HOME"):
    java_candidates.append(Path(java_home))
  java_candidates.extend([
    Path("C:/Program Files/Android/Android Studio/jbr"),
    Path("/opt/android-studio/jbr"),
  ])
  selected_java_home = next(
    (path.resolve() for path in java_candidates if (path / "bin" / f"java{suffix}").is_file()),
    None,
  )
  if selected_java_home is None:
    raise InspectionError("tool_missing", "Java 21 runtime not found")
  gradle = _existing(_gradle_candidates(repo_root, env), "Gradle executable")
  return ToolPaths(aapt2=aapt2, apksigner=apksigner, java_home=selected_java_home, gradle=gradle)


def run_checked(
  command: Sequence[str],
  *,
  env: Mapping[str, str] | None = None,
  timeout_seconds: float = 180.0,
  cwd: Path | None = None,
) -> str:
  try:
    completed = subprocess.run(
      command,
      capture_output=True,
      text=True,
      encoding="utf-8",
      errors="replace",
      check=False,
      timeout=timeout_seconds,
      cwd=cwd,
      env=None if env is None else dict(env),
    )
  except subprocess.TimeoutExpired as error:
    raise InspectionError("tool_timeout", f"tool timed out after {timeout_seconds:g}s: {command[0]}") from error
  except OSError as error:
    raise InspectionError("tool_launch", f"cannot launch {command[0]}: {error}") from error
  if completed.returncode != 0:
    detail = (completed.stderr or completed.stdout).strip().splitlines()
    suffix = f": {detail[-1]}" if detail else ""
    raise InspectionError("tool_failed", f"tool exited {completed.returncode}: {command[0]}{suffix}")
  return completed.stdout + completed.stderr


def read_manifest(apk: Path, tools: ToolPaths) -> ManifestFacts:
  badging = run_checked([str(tools.aapt2), "dump", "badging", str(apk)])
  first_line = next((line for line in badging.splitlines() if line.startswith("package:")), "")
  fields = dict(_FIELD_RE.findall(first_line))
  try:
    package = fields["name"]
    version_code = int(fields["versionCode"])
    version_name = fields["versionName"]
  except (KeyError, ValueError) as error:
    raise InspectionError("manifest_parse", "aapt2 returned malformed package identity") from error
  split_name = fields.get("split") or None
  xmltree = run_checked([
    str(tools.aapt2), "dump", "xmltree", "--file", "AndroidManifest.xml", str(apk),
  ])
  split_line = next((line for line in xmltree.splitlines() if "requiredSplitTypes" in line), "")
  split_match = re.search(r'(?:Raw: )?"([^"]+)"', split_line)
  split_types = tuple(part for part in split_match.group(1).split(",") if part) if split_match else ()
  return ManifestFacts(package, version_code, version_name, split_name, split_types)


def read_signers(apk: Path, tools: ToolPaths) -> SignerFacts:
  output = run_checked([
    str(tools.apksigner), "verify", "--verbose", "--print-certs", str(apk),
  ], env={**os.environ, "JAVA_HOME": str(tools.java_home)})
  for required in ("Verified using v2 scheme (APK Signature Scheme v2): true", "Verified using v3 scheme (APK Signature Scheme v3): true"):
    if required not in output:
      raise InspectionError("signer_verify", f"APK signature verification missing: {required}")
  signer_match = re.search(rf"Signer #1 certificate SHA-256 digest: {_SHA_RE}", output)
  stamp_match = re.search(rf"Source Stamp Signer certificate SHA-256 digest: {_SHA_RE}", output)
  if "Verified for SourceStamp: true" not in output or signer_match is None or stamp_match is None:
    raise InspectionError("signer_parse", "APK signer or verified source stamp is missing")
  return SignerFacts(signer_match.group(1).lower(), stamp_match.group(1).lower())


def sha256_file(path: Path) -> str:
  digest = hashlib.sha256()
  try:
    with path.open("rb") as stream:
      for chunk in iter(lambda: stream.read(1024 * 1024), b""):
        digest.update(chunk)
  except OSError as error:
    raise InspectionError("input_read", "APK input is not readable") from error
  return digest.hexdigest()


def read_archive_facts(apk: Path) -> ArchiveFacts:
  try:
    with zipfile.ZipFile(apk, "r") as archive:
      names = archive.namelist()
  except (OSError, zipfile.BadZipFile) as error:
    raise InspectionError("apk_archive", "APK is not a readable ZIP archive") from error
  if len(names) != len(set(names)):
    raise InspectionError("apk_archive", "APK contains duplicate archive entries")
  return ArchiveFacts(
    dex_count=sum(_DEX_ENTRY_RE.fullmatch(name) is not None for name in names),
    native_library_count=sum(_NATIVE_ENTRY_RE.fullmatch(name) is not None for name in names),
  )


def read_profiled_dex_entries(
  apk: Path,
  expected: Mapping[str, DexEntryProfile],
) -> dict[str, DexEntryFacts]:
  try:
    with zipfile.ZipFile(apk, "r") as archive:
      infos = archive.infolist()
      archive_names = tuple(info.filename for info in infos)
      if len(archive_names) != len(set(archive_names)):
        raise InspectionError("apk_archive", "APK contains duplicate archive entries")
      observed: dict[str, DexEntryFacts] = {}
      for name, profile in expected.items():
        try:
          info = archive.getinfo(name)
        except KeyError as error:
          raise InspectionError("dex_identity", f"profiled DEX entry is missing: {name}") from error
        if info.file_size != profile.size:
          raise InspectionError("dex_identity", f"profiled DEX identity mismatch: {name}")
        digest = hashlib.sha256()
        with archive.open(info, "r") as stream:
          for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
        observed[name] = DexEntryFacts(info.file_size, digest.hexdigest())
      return observed
  except InspectionError:
    raise
  except (OSError, RuntimeError, zipfile.BadZipFile) as error:
    raise InspectionError("apk_archive", "APK is not a readable ZIP archive") from error


def validate_profiled_dex_entries(
  observed: Mapping[str, DexEntryFacts],
  expected: Mapping[str, DexEntryProfile],
) -> None:
  if set(observed) != set(expected):
    raise InspectionError("dex_identity", "profiled DEX result set is incomplete")
  mismatches = tuple(
    name
    for name, profile in expected.items()
    if observed[name].size != profile.size or observed[name].sha256 != profile.sha256
  )
  if mismatches:
    raise InspectionError("dex_identity", f"profiled DEX identity mismatch: {','.join(mismatches)}")


def verify_profiled_dex_entries(
  apk: Path,
  expected: Mapping[str, DexEntryProfile],
) -> dict[str, DexEntryFacts]:
  observed = read_profiled_dex_entries(apk, expected)
  validate_profiled_dex_entries(observed, expected)
  return observed


def scan_anchors(request: AnchorScanRequest, tools: ToolPaths) -> dict[str, int]:
  encoded_rules = ",".join(_encoded_rule(anchor) for anchor in request.anchors)
  environment = os.environ.copy()
  java_bin = tools.java_home / "bin"
  environment.update({
    "JAVA_HOME": str(tools.java_home),
    "PATH": f"{java_bin}{os.pathsep}{environment.get('PATH', '')}",
  })
  output = run_checked(
    [
      str(tools.gradle), "-p", str(request.dexpatch_dir), "scan", "--no-daemon", "-q",
      "--dependency-verification=strict",
      f"-Papk={request.base}", f"-Prules={encoded_rules}",
    ],
    env=environment,
    timeout_seconds=300.0,
    cwd=request.dexpatch_dir,
  )
  counts: dict[str, int] = {}
  for line in output.splitlines():
    match = _ANCHOR_COUNT_RE.fullmatch(line.strip())
    if match is None:
      continue
    name = match.group(1)
    if name in counts:
      raise InspectionError("anchor_output", f"DEX scanner repeated anchor result: {name}")
    counts[name] = int(match.group(2))
  expected_names = {anchor.name for anchor in request.anchors}
  if set(counts) != expected_names:
    raise InspectionError("anchor_output", "DEX scanner returned an incomplete anchor result set")
  return counts


def _encoded_rule(anchor: AnchorProfile) -> str:
  separator = chr(0x1f)
  raw = separator.join((
    anchor.name,
    anchor.dex_entry,
    anchor.class_descriptor,
    anchor.method_descriptor,
    *anchor.instruction_window,
  )).encode("utf-8")
  return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
