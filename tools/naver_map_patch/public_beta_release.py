from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import zipfile

from tools.naver_map_patch.diagnostic_patch import (
  installed_production_payload_identity,
  verify_production_archive,
)
from tools.naver_map_patch.profile_types import InspectionProfile
from tools.naver_map_patch.split_package import (
  AndroidTools,
  ApkIdentity,
  ErrorCode,
  PackagingError,
  PayloadIdentity,
)
from tools.naver_map_patch.split_tools import (
  inspect_apk,
  run_checked,
  sha256_file,
  tool_environment,
  verify_alignment,
)


APKEDITOR_JAR_NAME = "APKEditor-1.4.5.jar"
APKEDITOR_SHA256 = "71999a1f28cf6b457aff17c139436349cd6ea30d75a0f9cd52f07bd52e21897b"
APKEDITOR_VERSION_OUTPUT = "APKEditor version 1.4.5"
_APKEDITOR_VERSION_LINE = re.compile(r"^APKEditor version 1\.4\.5(?:,|\s|$)", re.MULTILINE)
PUBLIC_BETA_APK_NAME = "NaverMap-6.8.0.5-carrot-public-beta-v2.apk"
REQUIRED_INPUT_APK_NAMES = (
  "base.apk",
  "split_config.arm64_v8a.apk",
  "split_config.xxhdpi.apk",
)
_MERGER_TIMEOUT_SECONDS = 300.0
PUBLIC_BETA_BUILD_ID = "naver-6.8.0.5-public-beta-v2"
PUBLIC_PACKAGE_NAME = "com.nhn.android.nmap"
PUBLIC_VERSION_CODE = 60800007
PUBLIC_VERSION_NAME = "6.8.0.5"
PUBLIC_MIN_SDK = 26
PUBLIC_TARGET_SDK = 35
PUBLIC_LAUNCHER_ALIAS = "com.naver.map.LaunchActivity"
PUBLIC_MAIN_ACTIVITY = "com.naver.map.MainActivity"
PUBLIC_NATIVE_LIBRARY_COUNT = 33
PUBLIC_README_KO_NAME = "README_KO.md"
PUBLIC_README_EN_NAME = "README_EN.md"
PUBLIC_CHECKSUMS_NAME = "SHA256SUMS.txt"
_PUBLIC_FILES = (PUBLIC_BETA_APK_NAME, PUBLIC_README_KO_NAME, PUBLIC_README_EN_NAME)
_ARM64_LIBRARY = re.compile(r"lib/arm64-v8a/[^/]+\.so\Z")
_ABI_ENTRY = re.compile(r"lib/([^/]+)/")
_PERMISSION_TAGS = frozenset({
  "permission", "permission-group", "permission-tree", "uses-permission", "uses-permission-sdk-23",
})
_COMPONENT_TAGS = frozenset({"activity", "activity-alias", "provider", "receiver", "service"})
_FORBIDDEN_STANDALONE_SPLIT_MARKERS = (
  "requiredSplitTypes",
  "splitTypes",
  "isSplitRequired",
  "isFeatureSplit",
  "configForSplit",
  "com.android.vending.splits.required",
  "com.android.vending.splits",
  "com.android.vending.derived.apk.id",
)
_FORBIDDEN_PUBLIC_MARKERS = (
  b"Lai/comma/naver/payload/DiagnosticHooks;",
  b"Lai/comma/naver/payload/FieldAcceptanceHooks;",
  b"capture-v3.sqlite3",
  b"capture-v3-recovery.sqlite3",
  b"capture-v3-export.sqlite3",
  b"127.0.0.1",
  b"naver-6.8.0.5-diagnostic-offline-v3",
  b"naver-6.8.0.5-field-acceptance-v1",
  b"naver-6.8.0.5-field-acceptance-v2",
  b"naver-6.8.0.5-field-acceptance-v3",
  b"naver-6.8.0.5-field-acceptance-v4",
  b"naver-6.8.0.5-public-beta-v1",
  b"Lai/comma/naver/payload/AndroidCaptureSharing;",
  b"Landroidx/core/content/FileProvider;",
  b"Landroidx/core/content/FileProvider$PathStrategy;",
  b"beforeCaptureRead(Ljava/lang/Object;)V",
)


@dataclass(frozen=True, slots=True)
class MergeRequest:
  input_directory: Path
  apkeditor_jar: Path
  java_executable: Path
  output_root: Path


@dataclass(frozen=True, slots=True)
class MergeResult:
  apk_path: Path


@dataclass(frozen=True, slots=True)
class StandaloneVerificationRequest:
  original_base_apk: Path
  authoritative_arm64_split_apk: Path
  standalone_apk: Path
  profile: InspectionProfile
  tools: AndroidTools
  environment: Mapping[str, str]


@dataclass(frozen=True, slots=True)
class StandaloneVerificationResult:
  apk_path: Path
  payload_identity: PayloadIdentity


@dataclass(frozen=True, slots=True)
class ReleaseBundleRequest:
  verification: StandaloneVerificationRequest
  readme_ko: Path
  readme_en: Path
  output_directory: Path


@dataclass(frozen=True, slots=True)
class ReleaseBundleResult:
  output_directory: Path
  apk_path: Path
  checksums_path: Path


@dataclass(frozen=True, slots=True)
class _ManifestInventory:
  permissions: tuple[tuple[str, str], ...]
  components: tuple["_Component", ...]


@dataclass(frozen=True, slots=True)
class _ArchiveVerification:
  payload: bytes
  native_libraries: tuple[tuple[str, str], ...]


@dataclass(frozen=True, order=True, slots=True)
class _IntentFilter:
  actions: tuple[str, ...]
  categories: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _Component:
  kind: str
  name: str
  target_activity: str | None
  intent_filters: tuple[_IntentFilter, ...]


def merge_public_beta(request: MergeRequest) -> MergeResult:
  inputs = _required_inputs(request.input_directory)
  output = _output_path(request.input_directory, request.output_root)
  _verify_apkeditor(request.apkeditor_jar)
  environment = os.environ.copy()
  _verify_apkeditor_version(request.java_executable, request.apkeditor_jar, environment)
  try:
    output.parent.mkdir(parents=True, exist_ok=True)
  except OSError as error:
    raise PackagingError(ErrorCode.UNSAFE_PATH, "output directory cannot be created", path=request.output_root) from error

  with tempfile.TemporaryDirectory(prefix=".naver-public-beta-merge-", dir=output.parent) as temporary_directory:
    temporary_root = Path(temporary_directory)
    staged_inputs = temporary_root / "inputs"
    staged_inputs.mkdir()
    temporary_output = temporary_root / "merged.apk"
    for source in inputs:
      try:
        shutil.copy2(source, staged_inputs / source.name)
      except OSError as error:
        raise PackagingError(ErrorCode.MISSING_SPLIT, "input split could not be staged", path=source) from error
    run_checked(
      (
        str(request.java_executable), "-jar", str(request.apkeditor_jar), "m", "-i", str(staged_inputs),
        "-o", str(temporary_output), "-clean-meta", "-f", "-validate-modules",
      ),
      environment,
      _MERGER_TIMEOUT_SECONDS,
    )
    if not temporary_output.is_file():
      raise PackagingError(ErrorCode.TOOL_FAILURE, "APKEditor did not create the expected merged APK", path=temporary_output)
    try:
      os.replace(temporary_output, output)
    except OSError as error:
      raise PackagingError(ErrorCode.TOOL_FAILURE, "merged APK could not be published", path=output) from error
  return MergeResult(apk_path=output)


def verify_public_beta(request: StandaloneVerificationRequest) -> StandaloneVerificationResult:
  _verify_authoritative_base(request)
  authoritative_libraries = _verify_authoritative_arm64_split(request)
  archive_verification = _verify_archive(request.standalone_apk)
  if archive_verification.native_libraries != authoritative_libraries:
    raise PackagingError(
      ErrorCode.OUTPUT_HASH_MISMATCH,
      "standalone native library inventory differs from the authoritative arm64 split",
      path=request.standalone_apk,
    )
  payload = archive_verification.payload
  original_identity = inspect_apk(request.original_base_apk, request.tools)
  standalone_identity = inspect_apk(request.standalone_apk, request.tools)
  _verify_original_identity(original_identity, request.profile)
  _verify_standalone_identity(standalone_identity)
  original_badging = _android_dump(request.original_base_apk, request.tools, "badging")
  standalone_badging = _android_dump(request.standalone_apk, request.tools, "badging")
  _verify_badging(original_badging, standalone_badging, request.standalone_apk)
  original_manifest = _android_dump(request.original_base_apk, request.tools, "xmltree")
  standalone_manifest = _android_dump(request.standalone_apk, request.tools, "xmltree")
  _verify_manifest_inventory(original_manifest, standalone_manifest, request.standalone_apk)
  verify_alignment(request.standalone_apk, request.tools)
  verify_production_archive(request.standalone_apk, request.profile, request.environment)
  payload_identity = installed_production_payload_identity(request.standalone_apk, request.profile)
  if (
    payload_identity.mode != "production"
    or payload_identity.build_id != PUBLIC_BETA_BUILD_ID
    or payload_identity.dex_entry != "classes43.dex"
    or payload_identity.sha256 != hashlib.sha256(payload).hexdigest()
  ):
    raise PackagingError(
      ErrorCode.OUTPUT_HASH_MISMATCH,
      "standalone production payload identity differs from the public contract",
      path=request.standalone_apk,
    )
  marker = next((item for item in _FORBIDDEN_PUBLIC_MARKERS if item in payload), None)
  if marker is not None:
    raise PackagingError(
      ErrorCode.OUTPUT_HASH_MISMATCH,
      f"classes43.dex contains forbidden public marker: {marker.decode('ascii')}",
      path=request.standalone_apk,
    )
  return StandaloneVerificationResult(request.standalone_apk, payload_identity)


def build_release_bundle(request: ReleaseBundleRequest) -> ReleaseBundleResult:
  sources = (
    request.verification.standalone_apk,
    request.readme_ko,
    request.readme_en,
  )
  if tuple(path.name for path in sources) != _PUBLIC_FILES or not all(path.is_file() for path in sources):
    raise PackagingError(ErrorCode.UNSAFE_PATH, "release inputs must be the exact three public files")
  if request.output_directory.exists():
    raise PackagingError(ErrorCode.STALE_OUTPUT, "release output directory already exists", path=request.output_directory)
  verify_public_beta(request.verification)
  try:
    request.output_directory.parent.mkdir(parents=True, exist_ok=True)
  except OSError as error:
    raise PackagingError(
      ErrorCode.UNSAFE_PATH, "release output parent cannot be created", path=request.output_directory.parent,
    ) from error
  try:
    with tempfile.TemporaryDirectory(prefix=".naver-public-beta-release-", dir=request.output_directory.parent) as temporary:
      staging = Path(temporary) / "release"
      staging.mkdir()
      for source in sources:
        shutil.copy2(source, staging / source.name)
      checksums = "".join(f"{sha256_file(staging / name)}  {name}\n" for name in _PUBLIC_FILES)
      (staging / PUBLIC_CHECKSUMS_NAME).write_text(checksums, encoding="utf-8", newline="\n")
      if tuple(sorted(path.name for path in staging.iterdir())) != tuple(sorted((*_PUBLIC_FILES, PUBLIC_CHECKSUMS_NAME))):
        raise PackagingError(ErrorCode.STALE_OUTPUT, "staged release directory has unexpected files", path=staging)
      os.replace(staging, request.output_directory)
  except PackagingError:
    raise
  except OSError as error:
    raise PackagingError(ErrorCode.TOOL_FAILURE, "release bundle could not be published", path=request.output_directory) from error
  return ReleaseBundleResult(
    output_directory=request.output_directory,
    apk_path=request.output_directory / PUBLIC_BETA_APK_NAME,
    checksums_path=request.output_directory / PUBLIC_CHECKSUMS_NAME,
  )


def _verify_authoritative_base(request: StandaloneVerificationRequest) -> None:
  try:
    size = request.original_base_apk.stat().st_size
  except OSError as error:
    raise PackagingError(ErrorCode.WRONG_HASH, "authoritative original base is not readable", path=request.original_base_apk) from error
  if size != request.profile.base.size or sha256_file(request.original_base_apk) != request.profile.base.sha256:
    raise PackagingError(
      ErrorCode.WRONG_HASH,
      "authoritative original base does not match the frozen profile",
      path=request.original_base_apk,
    )


def _verify_authoritative_arm64_split(
  request: StandaloneVerificationRequest,
) -> tuple[tuple[str, str], ...]:
  matching = tuple(
    split for split in request.profile.splits
    if (
      split.entry == "split_config.arm64_v8a.apk"
      and split.split_name == "config.arm64_v8a"
      and split.split_type == "base__abi"
    )
  )
  if len(matching) != 1:
    raise PackagingError(ErrorCode.MALFORMED_METADATA, "profile has no exact authoritative arm64 split")
  expected = matching[0]
  path = request.authoritative_arm64_split_apk
  try:
    size = path.stat().st_size
  except OSError as error:
    raise PackagingError(ErrorCode.WRONG_HASH, "authoritative arm64 split is not readable", path=path) from error
  if path.name != expected.entry or size != expected.size or sha256_file(path) != expected.sha256:
    raise PackagingError(ErrorCode.WRONG_HASH, "authoritative arm64 split does not match the frozen profile", path=path)
  try:
    with zipfile.ZipFile(path, "r") as archive:
      names = archive.namelist()
      if len(names) != len(set(names)) or archive.testzip() is not None:
        raise PackagingError(ErrorCode.MALFORMED_METADATA, "authoritative arm64 split archive is not intact", path=path)
      return _native_library_inventory(archive, path)
  except PackagingError:
    raise
  except (OSError, RuntimeError, zipfile.BadZipFile) as error:
    raise PackagingError(ErrorCode.MALFORMED_METADATA, "authoritative arm64 split is not a readable ZIP", path=path) from error


def _verify_original_identity(identity: ApkIdentity, profile: InspectionProfile) -> None:
  if (
    identity.package_name != PUBLIC_PACKAGE_NAME
    or identity.version_code != PUBLIC_VERSION_CODE
    or identity.version_name != PUBLIC_VERSION_NAME
    or identity.split_name is not None
  ):
    raise PackagingError(ErrorCode.WRONG_VERSION, "authoritative original base identity differs", path=identity.path)
  if identity.required_split_types != profile.base.required_split_types:
    raise PackagingError(
      ErrorCode.REQUIRED_SPLIT_MISMATCH,
      "authoritative original base required split types differ from the frozen profile",
      path=identity.path,
    )


def _verify_standalone_identity(identity: ApkIdentity) -> None:
  if (
    identity.package_name != PUBLIC_PACKAGE_NAME
    or identity.version_code != PUBLIC_VERSION_CODE
    or identity.version_name != PUBLIC_VERSION_NAME
  ):
    raise PackagingError(ErrorCode.WRONG_VERSION, "standalone package or version differs", path=identity.path)
  if identity.split_name is not None or identity.required_split_types:
    raise PackagingError(
      ErrorCode.REQUIRED_SPLIT_MISMATCH,
      "standalone APK retains split name or required split types",
      path=identity.path,
    )


def _verify_archive(path: Path) -> _ArchiveVerification:
  try:
    with zipfile.ZipFile(path, "r") as archive:
      names = archive.namelist()
      if len(names) != len(set(names)):
        raise PackagingError(ErrorCode.MALFORMED_METADATA, "standalone APK has duplicate archive entries", path=path)
      if archive.testzip() is not None:
        raise PackagingError(ErrorCode.MALFORMED_METADATA, "standalone APK archive integrity check failed", path=path)
      native_libraries = _native_library_inventory(archive, path)
      if names.count("classes43.dex") != 1:
        raise PackagingError(
          ErrorCode.OUTPUT_HASH_MISMATCH, "standalone APK must contain exactly one classes43.dex", path=path,
        )
      return _ArchiveVerification(archive.read("classes43.dex"), native_libraries)
  except PackagingError:
    raise
  except (OSError, KeyError, RuntimeError, zipfile.BadZipFile) as error:
    raise PackagingError(ErrorCode.MALFORMED_METADATA, "standalone APK is not an intact ZIP archive", path=path) from error


def _native_library_inventory(archive: zipfile.ZipFile, path: Path) -> tuple[tuple[str, str], ...]:
  names = archive.namelist()
  native_names = tuple(name for name in names if name.startswith("lib/") and name.endswith(".so"))
  abis = {match.group(1) for name in names if (match := _ABI_ENTRY.match(name)) is not None}
  if (
    len(native_names) != PUBLIC_NATIVE_LIBRARY_COUNT
    or any(_ARM64_LIBRARY.fullmatch(name) is None for name in native_names)
    or abis != {"arm64-v8a"}
  ):
    raise PackagingError(
      ErrorCode.MALFORMED_METADATA,
      "archive must contain exactly 33 arm64-v8a libraries and no other ABI",
      path=path,
    )
  inventory: list[tuple[str, str]] = []
  for name in sorted(native_names):
    digest = hashlib.sha256()
    with archive.open(name, "r") as stream:
      for chunk in iter(lambda: stream.read(1024 * 1024), b""):
        digest.update(chunk)
    inventory.append((name, digest.hexdigest()))
  return tuple(inventory)


def _android_dump(path: Path, tools: AndroidTools, mode: str) -> str:
  if mode == "badging":
    command = (str(tools.aapt2), "dump", "badging", str(path))
  else:
    command = (str(tools.aapt2), "dump", "xmltree", "--file", "AndroidManifest.xml", str(path))
  return run_checked(command, tool_environment(tools), tools.timeout_seconds).stdout


def _badging_field(badging: str, field: str, path: Path) -> str:
  matches = re.findall(rf"(?m)^{re.escape(field)}:'([^']*)'\s*$", badging)
  if len(matches) != 1:
    raise PackagingError(ErrorCode.MALFORMED_METADATA, f"aapt2 badging has invalid {field}", path=path)
  return matches[0]


def _verify_badging(original: str, standalone: str, path: Path) -> None:
  original_values = (
    _badging_field(original, "minSdkVersion", path),
    _badging_field(original, "targetSdkVersion", path),
  )
  standalone_values = (
    _badging_field(standalone, "minSdkVersion", path),
    _badging_field(standalone, "targetSdkVersion", path),
  )
  expected = (str(PUBLIC_MIN_SDK), str(PUBLIC_TARGET_SDK))
  if original_values != expected or standalone_values != expected:
    raise PackagingError(
      ErrorCode.MALFORMED_METADATA,
      "standalone min SDK or target SDK differs from the authoritative base",
      path=path,
    )


def _attribute_value(lines: list[str], attribute: str) -> str | None:
  raw = re.compile(rf"\b(?:android:)?{re.escape(attribute)}\([^)]*\).*\(Raw: \"([^\"]*)\"\)")
  quoted = re.compile(rf"\b(?:android:)?{re.escape(attribute)}\([^)]*\)=\"([^\"]*)\"")
  for line in lines:
    if (match := raw.search(line)) is not None or (match := quoted.search(line)) is not None:
      return match.group(1)
  return None


def _element_blocks(lines: list[str], wanted: frozenset[str]) -> tuple[tuple[str, list[str]], ...]:
  elements: list[tuple[int, int, str]] = []
  for index, line in enumerate(lines):
    stripped = line.lstrip()
    match = re.match(r"E: ([A-Za-z0-9_-]+)\b", stripped)
    if match is not None:
      elements.append((index, len(line) - len(stripped), match.group(1)))
  blocks: list[tuple[str, list[str]]] = []
  for element_index, (start, indent, tag) in enumerate(elements):
    if tag not in wanted:
      continue
    end = len(lines)
    for next_start, next_indent, _ in elements[element_index + 1:]:
      if next_indent <= indent:
        end = next_start
        break
    blocks.append((tag, lines[start + 1:end]))
  return tuple(blocks)


def _intent_filters(component_lines: list[str], path: Path) -> tuple[_IntentFilter, ...]:
  filters: list[_IntentFilter] = []
  for _, filter_lines in _element_blocks(component_lines, frozenset({"intent-filter"})):
    actions: list[str] = []
    categories: list[str] = []
    for tag, child_lines in _element_blocks(filter_lines, frozenset({"action", "category"})):
      name = _attribute_value(child_lines, "name")
      if name is None:
        raise PackagingError(ErrorCode.MALFORMED_METADATA, f"manifest {tag} has no literal name", path=path)
      (actions if tag == "action" else categories).append(name)
    filters.append(_IntentFilter(tuple(sorted(actions)), tuple(sorted(categories))))
  return tuple(sorted(filters))


def _manifest_inventory(xmltree: str, path: Path) -> _ManifestInventory:
  lines = xmltree.splitlines()
  permissions: list[tuple[str, str]] = []
  components: list[_Component] = []
  for tag, block in _element_blocks(lines, _PERMISSION_TAGS):
    name = _attribute_value(block, "name")
    if name is None:
      raise PackagingError(ErrorCode.MALFORMED_METADATA, f"manifest {tag} has no literal name", path=path)
    permissions.append((tag, name))
  applications = _element_blocks(lines, frozenset({"application"}))
  if len(applications) != 1:
    raise PackagingError(ErrorCode.MALFORMED_METADATA, "manifest must contain exactly one application", path=path)
  for tag, block in _element_blocks(applications[0][1], _COMPONENT_TAGS):
    name = _attribute_value(block, "name")
    if name is None:
      raise PackagingError(ErrorCode.MALFORMED_METADATA, f"manifest {tag} has no literal name", path=path)
    components.append(_Component(tag, name, _attribute_value(block, "targetActivity"), _intent_filters(block, path)))
  ordered_components = tuple(sorted(
    components,
    key=lambda item: (
      item.kind,
      item.name,
      item.target_activity or "",
      tuple((intent_filter.actions, intent_filter.categories) for intent_filter in item.intent_filters),
    ),
  ))
  return _ManifestInventory(tuple(sorted(permissions)), ordered_components)


def _verify_manifest_inventory(original: str, standalone: str, path: Path) -> None:
  if any(marker in standalone for marker in _FORBIDDEN_STANDALONE_SPLIT_MARKERS):
    raise PackagingError(ErrorCode.REQUIRED_SPLIT_MISMATCH, "standalone manifest retains split metadata", path=path)
  original_inventory = _manifest_inventory(original, path)
  standalone_inventory = _manifest_inventory(standalone, path)
  if standalone_inventory != original_inventory:
    raise PackagingError(
      ErrorCode.MALFORMED_METADATA,
      "standalone permission or component inventory differs from the authoritative base",
      path=path,
    )
  launcher_filter = _IntentFilter(
    ("android.intent.action.MAIN",),
    ("android.intent.category.LAUNCHER",),
  )
  launcher_aliases = tuple(
    component for component in standalone_inventory.components
    if (
      component.kind == "activity-alias"
      and component.name == PUBLIC_LAUNCHER_ALIAS
      and component.target_activity == PUBLIC_MAIN_ACTIVITY
    )
  )
  if len(launcher_aliases) != 1 or launcher_aliases[0].intent_filters.count(launcher_filter) != 1:
    raise PackagingError(
      ErrorCode.MALFORMED_METADATA,
      "standalone launcher alias must target the Naver main activity with one MAIN/LAUNCHER filter",
      path=path,
    )


def _required_inputs(input_directory: Path) -> tuple[Path, ...]:
  if not input_directory.is_dir():
    raise PackagingError(ErrorCode.MISSING_SPLIT, "input split directory is not readable", path=input_directory)
  apk_names = tuple(sorted(path.name for path in input_directory.glob("*.apk") if path.is_file()))
  expected_names = tuple(sorted(REQUIRED_INPUT_APK_NAMES))
  if apk_names != expected_names:
    missing = next((name for name in expected_names if name not in apk_names), None)
    if missing is not None:
      raise PackagingError(ErrorCode.MISSING_SPLIT, "required input split is missing", path=input_directory / missing)
    raise PackagingError(ErrorCode.REQUIRED_SPLIT_MISMATCH, "input directory has an unexpected APK split", path=input_directory)
  return tuple(input_directory / name for name in REQUIRED_INPUT_APK_NAMES)


def _verify_apkeditor(apkeditor_jar: Path) -> None:
  if apkeditor_jar.name != APKEDITOR_JAR_NAME:
    raise PackagingError(ErrorCode.TOOL_FAILURE, "APKEditor JAR must be version 1.4.5", path=apkeditor_jar)
  try:
    actual_hash = sha256_file(apkeditor_jar)
  except PackagingError as error:
    raise PackagingError(ErrorCode.TOOL_FAILURE, "APKEditor JAR is not readable", path=apkeditor_jar) from error
  if actual_hash != APKEDITOR_SHA256:
    raise PackagingError(ErrorCode.WRONG_HASH, "APKEditor JAR hash does not match the approved release", path=apkeditor_jar)


def _verify_apkeditor_version(java_executable: Path, apkeditor_jar: Path, environment: dict[str, str]) -> None:
  command = (str(java_executable), "-jar", str(apkeditor_jar), "-version")
  try:
    process = subprocess.run(
      command,
      capture_output=True,
      text=True,
      encoding="utf-8",
      errors="replace",
      timeout=_MERGER_TIMEOUT_SECONDS,
      check=False,
      env=environment,
      shell=False,
    )
  except subprocess.TimeoutExpired as error:
    raise PackagingError(ErrorCode.TOOL_TIMEOUT, "APKEditor version probe exceeded 300s", path=apkeditor_jar) from error
  except OSError as error:
    raise PackagingError(ErrorCode.TOOL_FAILURE, "APKEditor version probe could not start", path=apkeditor_jar) from error
  if process.returncode != 2 or _APKEDITOR_VERSION_LINE.search(process.stderr) is None:
    raise PackagingError(ErrorCode.TOOL_FAILURE, "APKEditor version output is not 1.4.5", path=apkeditor_jar)


def _output_path(input_directory: Path, output_root: Path) -> Path:
  source_root = input_directory.resolve()
  destination_root = output_root.resolve()
  try:
    destination_root.relative_to(source_root)
  except ValueError:
    pass
  else:
    raise PackagingError(ErrorCode.UNSAFE_PATH, "merged output must be outside the input split directory", path=output_root)
  output = destination_root / PUBLIC_BETA_APK_NAME
  if output.exists():
    raise PackagingError(ErrorCode.STALE_OUTPUT, "merged output already exists", path=output)
  return output
