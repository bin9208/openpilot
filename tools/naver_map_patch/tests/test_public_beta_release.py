from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import replace
import hashlib
from pathlib import Path
import subprocess
import types
from types import SimpleNamespace
import zipfile

import pytest

from tools.naver_map_patch.split_package import (
  AndroidTools,
  ApkIdentity,
  CommandResult,
  ErrorCode,
  PackagingError,
  PayloadIdentity,
  Sha256,
)
import tools.naver_map_patch.public_beta_release as public_beta_release


_INPUT_NAMES = (
  "base.apk",
  "split_config.arm64_v8a.apk",
  "split_config.xxhdpi.apk",
)
_APPROVED_APKEDITOR_SHA256 = "71999a1f28cf6b457aff17c139436349cd6ea30d75a0f9cd52f07bd52e21897b"


def test_source_only_and_user_docs_do_not_imply_public_acceptance() -> None:
  repository = Path(__file__).parents[3]
  guide = (repository / "tools/naver_map_patch/README.md").read_text(encoding="utf-8")
  assert "public release #12" in guide
  assert "synthetic" in guide
  for language in ("ko", "en"):
    content = (repository / f"docs/user/{language}/speed-deceleration.md").read_text(encoding="utf-8")
    for label in ("NAVER", "TMAP", "HDA cam"):
      assert label in content


def _request(tmp_path: Path, *, output_root: Path | None = None) -> public_beta_release.MergeRequest:
  inputs = tmp_path / "verified-splits"
  inputs.mkdir()
  for name in _INPUT_NAMES:
    (inputs / name).write_bytes(f"original:{name}".encode())
  jar = tmp_path / "APKEditor-1.4.5.jar"
  jar.write_bytes(b"official-apkeditor-fixture")
  return public_beta_release.MergeRequest(
    input_directory=inputs,
    apkeditor_jar=jar,
    java_executable=tmp_path / "java.exe",
    output_root=tmp_path / "release" if output_root is None else output_root,
  )


def _allow_fixture_jar(monkeypatch: pytest.MonkeyPatch) -> None:
  monkeypatch.setattr(public_beta_release, "sha256_file", lambda _: _APPROVED_APKEDITOR_SHA256)


def test_apkeditor_hash_is_pinned_to_the_approved_literal() -> None:
  assert public_beta_release.APKEDITOR_SHA256 == _APPROVED_APKEDITOR_SHA256


def _official_version_probe(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, ...]]:
  commands: list[tuple[str, ...]] = []

  def probe(command: Sequence[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
    commands.append(tuple(command))
    assert kwargs["shell"] is False
    return subprocess.CompletedProcess(command, 2, "", "APKEditor version 1.4.5\n")

  monkeypatch.setattr(public_beta_release, "subprocess", types.SimpleNamespace(run=probe), raising=False)
  return commands


def _successful_merger(commands: list[tuple[str, ...]]):
  def runner(command: Sequence[str], environment: Mapping[str, str], timeout_seconds: float) -> CommandResult:
    commands.append(tuple(command))
    input_directory = Path(command[command.index("-i") + 1])
    output = Path(command[command.index("-o") + 1])
    assert tuple(sorted(path.name for path in input_directory.iterdir())) == _INPUT_NAMES
    (input_directory / "base.apk").write_bytes(b"merger-may-mutate-only-copy")
    output.write_bytes(b"merged")
    return CommandResult("", "")

  return runner


def test_merge_requires_the_exact_three_apk_input_names(tmp_path: Path) -> None:
  request = _request(tmp_path)
  (request.input_directory / "split_config.xxhdpi.apk").rename(request.input_directory / "wrong.apk")

  with pytest.raises(PackagingError) as raised:
    public_beta_release.merge_public_beta(request)

  assert raised.value.code is ErrorCode.MISSING_SPLIT


def test_merge_rejects_an_unexpected_apk_input(tmp_path: Path) -> None:
  request = _request(tmp_path)
  (request.input_directory / "unexpected.apk").write_bytes(b"unexpected")

  with pytest.raises(PackagingError) as raised:
    public_beta_release.merge_public_beta(request)

  assert raised.value.code is ErrorCode.REQUIRED_SPLIT_MISMATCH


def test_merge_requires_the_exact_official_apkeditor_jar_hash(tmp_path: Path) -> None:
  request = _request(tmp_path)

  with pytest.raises(PackagingError) as raised:
    public_beta_release.merge_public_beta(request)

  assert raised.value.code is ErrorCode.WRONG_HASH
  assert raised.value.path == request.apkeditor_jar


def test_merge_accepts_the_official_apkeditor_version_from_stderr_exit_two(
  tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
  request = _request(tmp_path)
  _allow_fixture_jar(monkeypatch)
  version_commands = _official_version_probe(monkeypatch)
  commands: list[tuple[str, ...]] = []
  monkeypatch.setattr(public_beta_release, "run_checked", _successful_merger(commands))

  result = public_beta_release.merge_public_beta(request)

  assert result.apk_path.is_file()
  assert version_commands == [
    (str(request.java_executable), "-jar", str(request.apkeditor_jar), "-version"),
  ]
  assert len(commands) == 1


def test_merge_rejects_an_apkeditor_version_output_other_than_1_4_5(
  tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
  request = _request(tmp_path)
  _allow_fixture_jar(monkeypatch)

  def wrong_version(command: Sequence[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(command, 2, "", "APKEditor version 1.4.4\n")

  monkeypatch.setattr(public_beta_release, "subprocess", types.SimpleNamespace(run=wrong_version), raising=False)
  monkeypatch.setattr(public_beta_release, "run_checked", pytest.fail)

  with pytest.raises(PackagingError) as raised:
    public_beta_release.merge_public_beta(request)

  assert raised.value.code is ErrorCode.TOOL_FAILURE


def test_merge_rejects_an_apkeditor_version_with_a_1_4_5_prefix(
  tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
  request = _request(tmp_path)
  _allow_fixture_jar(monkeypatch)

  def prefixed_version(command: Sequence[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(command, 2, "", "APKEditor version 1.4.50\n")

  monkeypatch.setattr(public_beta_release, "subprocess", types.SimpleNamespace(run=prefixed_version), raising=False)
  monkeypatch.setattr(public_beta_release, "run_checked", pytest.fail)

  with pytest.raises(PackagingError) as raised:
    public_beta_release.merge_public_beta(request)

  assert raised.value.code is ErrorCode.TOOL_FAILURE


def test_merge_publishes_the_versioned_public_beta_output_name(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
  request = _request(tmp_path)
  _allow_fixture_jar(monkeypatch)
  _official_version_probe(monkeypatch)
  commands: list[tuple[str, ...]] = []
  monkeypatch.setattr(public_beta_release, "run_checked", _successful_merger(commands))

  result = public_beta_release.merge_public_beta(request)

  assert result.apk_path == request.output_root / "NaverMap-6.8.0.5-carrot-public-beta-v2.apk"
  assert result.apk_path.read_bytes() == b"merged"


def test_merge_rejects_an_output_root_inside_the_input_set(tmp_path: Path) -> None:
  request = _request(tmp_path, output_root=tmp_path / "verified-splits" / "release")

  with pytest.raises(PackagingError) as raised:
    public_beta_release.merge_public_beta(request)

  assert raised.value.code is ErrorCode.UNSAFE_PATH


def test_merge_rejects_an_existing_versioned_output(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
  request = _request(tmp_path)
  _allow_fixture_jar(monkeypatch)
  request.output_root.mkdir()
  (request.output_root / public_beta_release.PUBLIC_BETA_APK_NAME).write_bytes(b"stale")

  with pytest.raises(PackagingError) as raised:
    public_beta_release.merge_public_beta(request)

  assert raised.value.code is ErrorCode.STALE_OUTPUT


def test_merge_propagates_a_nonzero_merger_exit_without_publishing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
  request = _request(tmp_path)
  _allow_fixture_jar(monkeypatch)
  _official_version_probe(monkeypatch)
  original = {path.name: path.read_bytes() for path in request.input_directory.iterdir()}

  def failing_runner(command: Sequence[str], environment: Mapping[str, str], timeout_seconds: float) -> CommandResult:
    Path(command[command.index("-o") + 1]).write_bytes(b"partial merger output")
    raise PackagingError(ErrorCode.TOOL_FAILURE, "java exited 23", exit_code=23)

  monkeypatch.setattr(public_beta_release, "run_checked", failing_runner)

  with pytest.raises(PackagingError) as raised:
    public_beta_release.merge_public_beta(request)

  assert raised.value.code is ErrorCode.TOOL_FAILURE
  assert raised.value.exit_code == 23
  assert not (request.output_root / public_beta_release.PUBLIC_BETA_APK_NAME).exists()
  assert {path.name: path.read_bytes() for path in request.input_directory.iterdir()} == original


def test_merge_fails_closed_when_the_merger_produces_no_output(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
  request = _request(tmp_path)
  _allow_fixture_jar(monkeypatch)
  _official_version_probe(monkeypatch)
  monkeypatch.setattr(public_beta_release, "run_checked", lambda command, environment, timeout_seconds: CommandResult("", ""))

  with pytest.raises(PackagingError) as raised:
    public_beta_release.merge_public_beta(request)

  assert raised.value.code is ErrorCode.TOOL_FAILURE
  assert "did not create" in raised.value.detail


def test_merge_uses_a_literal_argument_vector_and_an_isolated_input_copy(
  tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
  request = _request(tmp_path, output_root=tmp_path / "release & literal")
  _allow_fixture_jar(monkeypatch)
  version_commands = _official_version_probe(monkeypatch)
  original = {path.name: path.read_bytes() for path in request.input_directory.iterdir()}
  commands: list[tuple[str, ...]] = []
  monkeypatch.setattr(public_beta_release, "run_checked", _successful_merger(commands))

  result = public_beta_release.merge_public_beta(request)

  assert version_commands == [(str(request.java_executable), "-jar", str(request.apkeditor_jar), "-version")]
  assert len(commands) == 1
  command = commands[0]
  assert command[:4] == (str(request.java_executable), "-jar", str(request.apkeditor_jar), "m")
  assert command[4] == "-i"
  staging_directory = Path(command[5])
  assert staging_directory != request.input_directory
  assert not staging_directory.exists()
  temporary_output = Path(command[command.index("-o") + 1])
  assert temporary_output != result.apk_path
  assert not temporary_output.exists()
  assert command[6:] == ("-o", str(temporary_output), "-clean-meta", "-f", "-validate-modules")
  assert {path.name: path.read_bytes() for path in request.input_directory.iterdir()} == original


_PUBLIC_BUILD_ID = "naver-6.8.0.5-public-beta-v2"
_ORIGINAL_REQUIRED_SPLITS = ("config.arm64_v8a", "config.xxhdpi")
_PERMISSIONS = ("android.permission.ACCESS_FINE_LOCATION", "android.permission.INTERNET")
_COMPONENTS = (
  ("activity", "com.naver.map.MainActivity", None),
  ("activity-alias", "com.naver.map.LaunchActivity", "com.naver.map.MainActivity"),
  ("provider", "com.naver.map.provider.MapProvider", None),
  ("receiver", "com.naver.map.receiver.BootReceiver", None),
  ("service", "com.naver.map.service.NavigationService", None),
)


def _manifest_tree(
  *,
  permissions: tuple[str, ...] = _PERMISSIONS,
  components: tuple[tuple[str, str, str | None], ...] = _COMPONENTS,
  include_split_metadata: bool = False,
  include_query_provider: bool = False,
) -> str:
  lines = [
    "N: android=http://schemas.android.com/apk/res/android",
    "  E: manifest (line=2)",
  ]
  if include_split_metadata:
    lines.append('    A: android:requiredSplitTypes(0x0101064e)="config.arm64_v8a,config.xxhdpi"')
  for permission in permissions:
    lines.extend([
      "    E: uses-permission (line=3)",
      f'      A: android:name(0x01010003)="{permission}" (Raw: "{permission}")',
    ])
  if include_query_provider:
    lines.extend([
      "    E: queries (line=4)",
      "      E: provider (line=5)",
      '        A: android:authorities(0x01010018)="com.example.visible" (Raw: "com.example.visible")',
    ])
  lines.append("    E: application (line=5)")
  if include_split_metadata:
    for split_name in (
      "com.android.vending.splits.required",
      "com.android.vending.splits",
      "com.android.vending.derived.apk.id",
    ):
      lines.extend([
        "      E: meta-data (line=5)",
        f'        A: android:name(0x01010003)="{split_name}" (Raw: "{split_name}")',
      ])
  for kind, name, target in components:
    lines.extend([
      f"      E: {kind} (line=6)",
      f'        A: android:name(0x01010003)="{name}" (Raw: "{name}")',
    ])
    if target is not None:
      lines.append(f'        A: android:targetActivity(0x01010202)="{target}" (Raw: "{target}")')
    if kind == "activity-alias" and name == "com.naver.map.LaunchActivity":
      lines.extend([
        "        E: intent-filter (line=7)",
        "          E: action (line=8)",
        '            A: android:name(0x01010003)="android.intent.action.MAIN" (Raw: "android.intent.action.MAIN")',
        "          E: category (line=9)",
        '            A: android:name(0x01010003)="android.intent.category.LAUNCHER" (Raw: "android.intent.category.LAUNCHER")',
        "        E: intent-filter (line=10)",
        "          E: action (line=11)",
        '            A: android:name(0x01010003)="android.intent.action.VIEW" (Raw: "android.intent.action.VIEW")',
        "          E: category (line=12)",
        '            A: android:name(0x01010003)="android.intent.category.DEFAULT" (Raw: "android.intent.category.DEFAULT")',
      ])
  return "\n".join(lines) + "\n"


def _badging(*, min_sdk: int = 26, target_sdk: int = 35) -> str:
  return (
    "package: name='com.nhn.android.nmap' versionCode='60800007' versionName='6.8.0.5'\n"
    f"minSdkVersion:'{min_sdk}'\n"
    f"targetSdkVersion:'{target_sdk}'\n"
  )


def _write_apk(
  path: Path,
  payload: bytes,
  *,
  arm64_libraries: int = 33,
  other_abi: str | None = None,
  library_mutation: str | None = None,
) -> None:
  with zipfile.ZipFile(path, "w") as archive:
    archive.writestr("AndroidManifest.xml", b"binary-manifest-fixture")
    archive.writestr("classes43.dex", payload)
    for index in range(arm64_libraries):
      name = f"lib/arm64-v8a/libfixture{index:02d}.so"
      content = f"library-{index}".encode()
      if index == 0 and library_mutation == "rename":
        name = "lib/arm64-v8a/libfixture00-renamed.so"
      elif index == 0 and library_mutation == "replacement":
        name = "lib/arm64-v8a/libreplacement.so"
      elif index == 0 and library_mutation == "content":
        content = b"modified-library-content"
      archive.writestr(name, content)
    if other_abi is not None:
      archive.writestr(f"lib/{other_abi}/libforeign.so", b"foreign")


def _write_arm64_split(path: Path) -> None:
  with zipfile.ZipFile(path, "w") as archive:
    for index in range(33):
      archive.writestr(f"lib/arm64-v8a/libfixture{index:02d}.so", f"library-{index}".encode())


def _verification_request(
  tmp_path: Path,
  monkeypatch: pytest.MonkeyPatch,
  *,
  payload: bytes = b"dex\nLai/comma/naver/payload/ProductionHooks;\nnaver-6.8.0.5-public-beta-v2",
  arm64_libraries: int = 33,
  other_abi: str | None = None,
  library_mutation: str | None = None,
  original_manifest: str | None = None,
  standalone_manifest: str | None = None,
  standalone_badging: str | None = None,
  standalone_identity: ApkIdentity | None = None,
) -> tuple[public_beta_release.StandaloneVerificationRequest, list[str]]:
  original = tmp_path / "original-base.apk"
  arm64_split = tmp_path / "split_config.arm64_v8a.apk"
  standalone = tmp_path / public_beta_release.PUBLIC_BETA_APK_NAME
  _write_apk(original, b"dex\noriginal", arm64_libraries=0)
  _write_arm64_split(arm64_split)
  _write_apk(
    standalone,
    payload,
    arm64_libraries=arm64_libraries,
    other_abi=other_abi,
    library_mutation=library_mutation,
  )
  tools = AndroidTools(
    aapt2=tmp_path / "aapt2.exe",
    zipalign=tmp_path / "zipalign.exe",
    apksigner=tmp_path / "apksigner.bat",
    java_home=tmp_path / "java-home",
    versions=(),
    timeout_seconds=30.0,
  )
  original_digest = hashlib.sha256(original.read_bytes()).hexdigest()
  arm64_digest = hashlib.sha256(arm64_split.read_bytes()).hexdigest()
  profile = SimpleNamespace(
    package_name="com.nhn.android.nmap",
    version_code=60800007,
    version_name="6.8.0.5",
    base=SimpleNamespace(
      size=original.stat().st_size,
      sha256=original_digest,
      required_split_types=_ORIGINAL_REQUIRED_SPLITS,
    ),
    splits=(SimpleNamespace(
      entry="split_config.arm64_v8a.apk",
      split_name="config.arm64_v8a",
      split_type="base__abi",
      size=arm64_split.stat().st_size,
      sha256=arm64_digest,
    ),),
  )
  request = public_beta_release.StandaloneVerificationRequest(
    original_base_apk=original,
    authoritative_arm64_split_apk=arm64_split,
    standalone_apk=standalone,
    profile=profile,
    tools=tools,
    environment={"PUBLIC_BETA_TEST": "1"},
  )
  standalone_identity = standalone_identity or ApkIdentity(
    standalone,
    "com.nhn.android.nmap",
    60800007,
    "6.8.0.5",
    None,
    Sha256("b" * 64),
    (),
  )
  original_identity = ApkIdentity(
    original,
    "com.nhn.android.nmap",
    60800007,
    "6.8.0.5",
    None,
    Sha256("a" * 64),
    _ORIGINAL_REQUIRED_SPLITS,
  )
  monkeypatch.setattr(
    public_beta_release,
    "inspect_apk",
    lambda path, _: original_identity if path == original else standalone_identity,
  )
  calls: list[str] = []
  monkeypatch.setattr(public_beta_release, "verify_alignment", lambda path, _: calls.append(f"align:{path.name}"))
  monkeypatch.setattr(
    public_beta_release,
    "verify_production_archive",
    lambda path, actual_profile, environment: calls.append(f"production:{path.name}"),
  )
  monkeypatch.setattr(
    public_beta_release,
    "installed_production_payload_identity",
    lambda path, actual_profile: PayloadIdentity(
      "production", _PUBLIC_BUILD_ID, "classes43.dex", Sha256(hashlib.sha256(payload).hexdigest()),
    ),
  )
  original_manifest = original_manifest or _manifest_tree(include_split_metadata=True)
  standalone_manifest = standalone_manifest or _manifest_tree()
  standalone_badging = standalone_badging or _badging()

  def android_runner(command: Sequence[str], environment: Mapping[str, str], timeout_seconds: float) -> CommandResult:
    apk = Path(command[-1])
    if "badging" in command:
      return CommandResult(_badging() if apk == original else standalone_badging, "")
    if "xmltree" in command:
      return CommandResult(original_manifest if apk == original else standalone_manifest, "")
    raise AssertionError(f"unexpected command: {command}")

  monkeypatch.setattr(public_beta_release, "run_checked", android_runner)
  return request, calls


def test_standalone_verifier_enforces_the_complete_public_contract(
  tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
  request, calls = _verification_request(tmp_path, monkeypatch)

  result = public_beta_release.verify_public_beta(request)

  assert result.apk_path == request.standalone_apk
  assert result.payload_identity.build_id == _PUBLIC_BUILD_ID
  assert calls == [
    f"align:{public_beta_release.PUBLIC_BETA_APK_NAME}",
    f"production:{public_beta_release.PUBLIC_BETA_APK_NAME}",
  ]


def test_standalone_verifier_rejects_a_non_authoritative_original_base(
  tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
  request, _ = _verification_request(tmp_path, monkeypatch)
  with request.original_base_apk.open("ab") as stream:
    stream.write(b"not-the-profiled-base")

  with pytest.raises(PackagingError) as raised:
    public_beta_release.verify_public_beta(request)

  assert raised.value.code is ErrorCode.WRONG_HASH


def test_standalone_verifier_rejects_a_non_authoritative_arm64_split(
  tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
  request, _ = _verification_request(tmp_path, monkeypatch)
  with request.authoritative_arm64_split_apk.open("ab") as stream:
    stream.write(b"not-the-profiled-arm64-split")

  with pytest.raises(PackagingError) as raised:
    public_beta_release.verify_public_beta(request)

  assert raised.value.code is ErrorCode.WRONG_HASH


@pytest.mark.parametrize(("build_id", "dex_entry", "digest"), [
  ("naver-6.8.0.5-production-v8", "classes43.dex", None),
  ("naver-6.8.0.5-public-beta-v1", "classes43.dex", None),
  (_PUBLIC_BUILD_ID, "classes42.dex", None),
  (_PUBLIC_BUILD_ID, "classes43.dex", "0" * 64),
])
def test_standalone_verifier_requires_the_exact_installed_production_identity(
  tmp_path: Path,
  monkeypatch: pytest.MonkeyPatch,
  build_id: str,
  dex_entry: str,
  digest: str | None,
) -> None:
  request, _ = _verification_request(tmp_path, monkeypatch)
  if digest is None:
    with zipfile.ZipFile(request.standalone_apk) as archive:
      digest = hashlib.sha256(archive.read("classes43.dex")).hexdigest()
  monkeypatch.setattr(
    public_beta_release,
    "installed_production_payload_identity",
    lambda path, profile: PayloadIdentity("production", build_id, dex_entry, Sha256(digest)),
  )

  with pytest.raises(PackagingError) as raised:
    public_beta_release.verify_public_beta(request)

  assert raised.value.code is ErrorCode.OUTPUT_HASH_MISMATCH


@pytest.mark.parametrize(("field", "value"), [
  ("package_name", "com.example.relabel"),
  ("version_code", 60800008),
  ("version_name", "6.8.0.6"),
  ("split_name", "config.arm64_v8a"),
  ("required_split_types", ("config.arm64_v8a",)),
])
def test_standalone_verifier_rejects_wrong_identity_or_split_metadata(
  tmp_path: Path, monkeypatch: pytest.MonkeyPatch, field: str, value: object,
) -> None:
  standalone = tmp_path / public_beta_release.PUBLIC_BETA_APK_NAME
  valid_identity = ApkIdentity(
    standalone,
    "com.nhn.android.nmap",
    60800007,
    "6.8.0.5",
    None,
    Sha256("b" * 64),
    (),
  )
  identity = replace(valid_identity, **{field: value})
  request, _ = _verification_request(tmp_path, monkeypatch, standalone_identity=identity)

  with pytest.raises(PackagingError):
    public_beta_release.verify_public_beta(request)


@pytest.mark.parametrize("mutation", [
  "min_sdk", "target_sdk", "alias_name", "alias_target", "main_action", "launcher_category", "permission", "component",
])
def test_standalone_verifier_preserves_sdk_launcher_permissions_and_components(
  tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mutation: str,
) -> None:
  badging = _badging(
    min_sdk=25 if mutation == "min_sdk" else 26,
    target_sdk=34 if mutation == "target_sdk" else 35,
  )
  components = list(_COMPONENTS)
  permissions = _PERMISSIONS
  if mutation == "alias_target":
    components[1] = ("activity-alias", "com.naver.map.LaunchActivity", "com.naver.map.WrongActivity")
  elif mutation == "alias_name":
    components[1] = ("activity-alias", "com.naver.map.WrongLaunchActivity", "com.naver.map.MainActivity")
  elif mutation == "component":
    components.pop()
  elif mutation == "permission":
    permissions = ("android.permission.INTERNET",)
  manifest = _manifest_tree(permissions=permissions, components=tuple(components))
  if mutation == "main_action":
    manifest = manifest.replace("android.intent.action.MAIN", "android.intent.action.VIEW")
  elif mutation == "launcher_category":
    manifest = manifest.replace("android.intent.category.LAUNCHER", "android.intent.category.DEFAULT")
  request, _ = _verification_request(
    tmp_path,
    monkeypatch,
    standalone_badging=badging,
    standalone_manifest=manifest,
  )

  with pytest.raises(PackagingError):
    public_beta_release.verify_public_beta(request)


def test_standalone_verifier_fails_closed_on_duplicate_component_with_mixed_target_types(
  tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
  malformed_components = (*_COMPONENTS, ("activity-alias", "com.naver.map.LaunchActivity", None))
  request, _ = _verification_request(
    tmp_path,
    monkeypatch,
    standalone_manifest=_manifest_tree(components=malformed_components),
  )

  with pytest.raises(PackagingError):
    public_beta_release.verify_public_beta(request)


def test_manifest_inventory_ignores_query_provider_as_a_non_component(
  tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
  manifest = _manifest_tree(include_query_provider=True)
  request, _ = _verification_request(
    tmp_path,
    monkeypatch,
    original_manifest=manifest,
    standalone_manifest=manifest,
  )

  public_beta_release.verify_public_beta(request)


@pytest.mark.parametrize("marker", [
  "requiredSplitTypes",
  "splitTypes",
  "isSplitRequired",
  "isFeatureSplit",
  "configForSplit",
  "com.android.vending.splits.required",
  "com.android.vending.splits",
  "com.android.vending.derived.apk.id",
])
def test_standalone_verifier_rejects_residual_split_install_metadata(
  tmp_path: Path, monkeypatch: pytest.MonkeyPatch, marker: str,
) -> None:
  manifest = _manifest_tree()
  if marker in ("requiredSplitTypes", "splitTypes", "isSplitRequired", "isFeatureSplit", "configForSplit"):
    manifest = manifest.replace(
      "  E: manifest (line=2)",
      f'  E: manifest (line=2)\n    A: android:{marker}(0x0101064e)="true" (Raw: "true")',
    )
  else:
    manifest = manifest.replace(
      "    E: application (line=5)",
      (
        "    E: application (line=5)\n"
        "      E: meta-data (line=5)\n"
        f'        A: android:name(0x01010003)="{marker}" (Raw: "{marker}")'
      ),
    )
  request, _ = _verification_request(tmp_path, monkeypatch, standalone_manifest=manifest)

  with pytest.raises(PackagingError, match="split metadata"):
    public_beta_release.verify_public_beta(request)


@pytest.mark.parametrize(("arm64_libraries", "other_abi"), [(32, None), (34, None), (33, "x86_64")])
def test_standalone_verifier_requires_exactly_33_arm64_libraries_and_no_other_abi(
  tmp_path: Path,
  monkeypatch: pytest.MonkeyPatch,
  arm64_libraries: int,
  other_abi: str | None,
) -> None:
  request, _ = _verification_request(
    tmp_path, monkeypatch, arm64_libraries=arm64_libraries, other_abi=other_abi,
  )

  with pytest.raises(PackagingError):
    public_beta_release.verify_public_beta(request)


@pytest.mark.parametrize("library_mutation", ["rename", "replacement", "content"])
def test_standalone_verifier_preserves_the_authoritative_arm64_library_inventory(
  tmp_path: Path,
  monkeypatch: pytest.MonkeyPatch,
  library_mutation: str,
) -> None:
  request, _ = _verification_request(tmp_path, monkeypatch, library_mutation=library_mutation)

  with pytest.raises(PackagingError, match="library inventory"):
    public_beta_release.verify_public_beta(request)


@pytest.mark.parametrize("marker", [
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
  b"Lai/comma/naver/payload/AndroidCaptureSharing;",
  b"Landroidx/core/content/FileProvider;",
  b"Landroidx/core/content/FileProvider$PathStrategy;",
  b"beforeCaptureRead(Ljava/lang/Object;)V",
])
def test_standalone_verifier_rejects_exact_forbidden_public_markers_only_in_classes43(
  tmp_path: Path, monkeypatch: pytest.MonkeyPatch, marker: bytes,
) -> None:
  request, _ = _verification_request(tmp_path, monkeypatch, payload=b"dex\nproduction\n" + marker)

  with pytest.raises(PackagingError, match="forbidden"):
    public_beta_release.verify_public_beta(request)


def test_standalone_verifier_does_not_scan_broad_original_app_words(
  tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
  payload = b"dex\nSQLiteDatabase FileProvider localhost route lane capture export"
  request, _ = _verification_request(tmp_path, monkeypatch, payload=payload)

  public_beta_release.verify_public_beta(request)


def test_standalone_verifier_scans_forbidden_markers_only_in_classes43(
  tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
  request, _ = _verification_request(tmp_path, monkeypatch)
  with zipfile.ZipFile(request.standalone_apk, "a") as archive:
    archive.writestr("classes42.dex", b"dex\nLai/comma/naver/payload/DiagnosticHooks;")

  public_beta_release.verify_public_beta(request)


def test_release_bundle_has_exact_files_and_deterministic_checksums(
  tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
  verification, _ = _verification_request(tmp_path, monkeypatch)
  readme_ko = tmp_path / "README_KO.md"
  readme_en = tmp_path / "README_EN.md"
  readme_ko.write_text("Korean release notes\n", encoding="utf-8")
  readme_en.write_text("English release notes\n", encoding="utf-8")
  request = public_beta_release.ReleaseBundleRequest(
    verification=verification,
    readme_ko=readme_ko,
    readme_en=readme_en,
    output_directory=tmp_path / "external-release",
  )

  result = public_beta_release.build_release_bundle(request)

  assert tuple(sorted(path.name for path in result.output_directory.iterdir())) == (
    "NaverMap-6.8.0.5-carrot-public-beta-v2.apk",
    "README_EN.md",
    "README_KO.md",
    "SHA256SUMS.txt",
  )
  public_files = (
    public_beta_release.PUBLIC_BETA_APK_NAME,
    "README_KO.md",
    "README_EN.md",
  )
  expected = "".join(
    f"{hashlib.sha256((result.output_directory / name).read_bytes()).hexdigest()}  {name}\n"
    for name in public_files
  )
  assert result.checksums_path.read_text(encoding="utf-8") == expected
  assert "SHA256SUMS.txt" not in expected


@pytest.mark.parametrize(("apk_name", "ko_name", "en_name"), [
  ("wrong.apk", "README_KO.md", "README_EN.md"),
  (public_beta_release.PUBLIC_BETA_APK_NAME, "README.md", "README_EN.md"),
  (public_beta_release.PUBLIC_BETA_APK_NAME, "README_KO.md", "README.md"),
])
def test_release_bundle_rejects_noncanonical_public_file_names(
  tmp_path: Path,
  monkeypatch: pytest.MonkeyPatch,
  apk_name: str,
  ko_name: str,
  en_name: str,
) -> None:
  verification, _ = _verification_request(tmp_path, monkeypatch)
  renamed_apk = verification.standalone_apk.with_name(apk_name)
  if renamed_apk != verification.standalone_apk:
    verification.standalone_apk.rename(renamed_apk)
    verification = public_beta_release.StandaloneVerificationRequest(
      original_base_apk=verification.original_base_apk,
      authoritative_arm64_split_apk=verification.authoritative_arm64_split_apk,
      standalone_apk=renamed_apk,
      profile=verification.profile,
      tools=verification.tools,
      environment=verification.environment,
    )
  readme_ko = tmp_path / ko_name
  readme_en = tmp_path / en_name
  readme_ko.write_text("ko", encoding="utf-8")
  readme_en.write_text("en", encoding="utf-8")
  request = public_beta_release.ReleaseBundleRequest(
    verification=verification,
    readme_ko=readme_ko,
    readme_en=readme_en,
    output_directory=tmp_path / "release-output",
  )

  with pytest.raises(PackagingError):
    public_beta_release.build_release_bundle(request)
