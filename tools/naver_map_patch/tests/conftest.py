from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
import json
import os
from pathlib import Path
import subprocess
import tempfile
import zipfile

import pytest


SDK_ROOT = Path(os.environ.get("ANDROID_SDK_ROOT") or os.environ.get("ANDROID_HOME") or
                str(Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "Android" / "Sdk"))
BUILD_TOOLS = SDK_ROOT / "build-tools" / "36.0.0"
JAVA_HOME = Path(os.environ.get("JAVA_HOME", "C:/Program Files/Android/Android Studio/jbr"))
EXECUTABLE_SUFFIX = ".exe" if os.name == "nt" else ""
SCRIPT_SUFFIX = ".bat" if os.name == "nt" else ""
TEST_PASSWORD = "synthetic-only-password"
PACKAGE_NAME = "com.example.synthetic.split"
VERSION_CODE = 60800007
VERSION_NAME = "6.8.0.5-test"


@dataclass(frozen=True, slots=True)
class SyntheticSet:
  root: Path
  base_apk: Path
  companion: Path
  official_keystore: Path
  alternate_keystore: Path
  patch_keystore: Path
  environment: Mapping[str, str]
  aapt2: Path
  zipalign: Path
  apksigner: Path
  android_jar: Path


def _run(command: Sequence[str], environment: Mapping[str, str]) -> str:
  process = subprocess.run(
    command, capture_output=True, text=True, timeout=30, check=False, env=environment,
  )
  assert process.returncode == 0, f"command failed: {command[0]}\n{process.stdout}\n{process.stderr}"
  return "\n".join(part for part in (process.stdout, process.stderr) if part)


def _make_key(keytool: Path, path: Path, alias: str, environment: Mapping[str, str]) -> None:
  _run((
    str(keytool), "-genkeypair", "-noprompt", "-keystore", str(path), "-storetype", "PKCS12",
    "-storepass:env", "SYNTHETIC_STORE_PASSWORD", "-keypass:env", "SYNTHETIC_KEY_PASSWORD",
    "-alias", alias, "-keyalg", "RSA",
    "-keysize", "2048", "-validity", "3650", "-dname", f"CN={alias},OU=Tests,O=Openpilot,C=KR",
  ), environment)


def _add_trusted_certificate(
  keytool: Path, path: Path, alias: str, environment: Mapping[str, str],
) -> None:
  source = path.with_name(f"{path.stem}-{alias}-source.p12")
  certificate = path.with_name(f"{path.stem}-{alias}.cer")
  _make_key(keytool, source, f"{alias}-source", environment)
  _run((
    str(keytool), "-exportcert", "-keystore", str(source), "-storetype", "PKCS12",
    "-storepass:env", "SYNTHETIC_STORE_PASSWORD", "-alias", f"{alias}-source", "-file", str(certificate),
  ), environment)
  _run((
    str(keytool), "-importcert", "-noprompt", "-keystore", str(path), "-storetype", "PKCS12",
    "-storepass:env", "SYNTHETIC_STORE_PASSWORD", "-alias", alias, "-file", str(certificate),
  ), environment)


def _make_certificate_only_keystore(
  keytool: Path, path: Path, alias: str, environment: Mapping[str, str],
) -> None:
  _add_trusted_certificate(keytool, path, alias, environment)


def _manifest(split_name: str | None, required_types: bool = True) -> str:
  split = "" if split_name is None else f' split="{split_name}"'
  required = ""
  if split_name is None and required_types:
    required = ' android:requiredSplitTypes="base__abi,base__density"'
  has_code = "true" if split_name is None else "false"
  return (
    '<?xml version="1.0" encoding="utf-8"?>\n'
    f'<manifest xmlns:android="http://schemas.android.com/apk/res/android" package="{PACKAGE_NAME}"{split}'
    f' android:versionCode="{VERSION_CODE}" android:versionName="{VERSION_NAME}"{required}>\n'
    '  <uses-sdk android:minSdkVersion="23" android:targetSdkVersion="35"/>\n'
    f'  <application android:hasCode="{has_code}"/>\n'
    '</manifest>\n'
  )


def _add_dex(apk: Path) -> None:
  info = zipfile.ZipInfo("classes.dex", date_time=(2020, 1, 1, 0, 0, 0))
  info.compress_type = zipfile.ZIP_STORED
  with zipfile.ZipFile(apk, "a") as archive:
    archive.writestr(info, b"synthetic-dex-v1")


def _build_apk(
  root: Path,
  name: str,
  split_name: str | None,
  keystore: Path,
  alias: str,
  fixture: SyntheticSet,
  required_types: bool = True,
) -> Path:
  manifest = root / f"{name}.xml"
  unsigned = root / f"{name}-unsigned.apk"
  aligned = root / f"{name}-aligned.apk"
  signed = root / f"{name}.apk"
  manifest.write_text(_manifest(split_name, required_types), encoding="utf-8")
  _run((str(fixture.aapt2), "link", "-o", str(unsigned), "--manifest", str(manifest),
        "-I", str(fixture.android_jar)), fixture.environment)
  if split_name is None:
    _add_dex(unsigned)
  _run((str(fixture.zipalign), "-f", "-P", "16", "4", str(unsigned), str(aligned)), fixture.environment)
  _run((
    str(fixture.apksigner), "sign", "--ks", str(keystore), "--ks-key-alias", alias,
    "--ks-pass", "env:SYNTHETIC_STORE_PASSWORD", "--key-pass", "env:SYNTHETIC_KEY_PASSWORD",
    "--v4-signing-enabled", "false", "--out", str(signed), str(aligned),
  ), fixture.environment)
  manifest.unlink()
  unsigned.unlink()
  aligned.unlink()
  return signed


@pytest.fixture(scope="session")
def synthetic_set() -> Iterator[SyntheticSet]:
  with tempfile.TemporaryDirectory(prefix="naver-synthetic-splits-") as temporary_directory:
    root = Path(temporary_directory)
    environment = os.environ.copy()
    environment.update({
      "JAVA_HOME": str(JAVA_HOME),
      "PATH": f"{JAVA_HOME / 'bin'}{os.pathsep}{environment.get('PATH', '')}",
      "SYNTHETIC_STORE_PASSWORD": TEST_PASSWORD,
      "SYNTHETIC_KEY_PASSWORD": TEST_PASSWORD,
    })
    android_jar = max((SDK_ROOT / "platforms").glob("android-*/android.jar"))
    empty = root / "placeholder.apk"
    fixture = SyntheticSet(
      root=root,
      base_apk=empty,
      companion=root / "companion",
      official_keystore=root / "official.p12",
      alternate_keystore=root / "alternate.p12",
      patch_keystore=root / "patch.p12",
      environment=environment,
      aapt2=BUILD_TOOLS / ("aapt2" + EXECUTABLE_SUFFIX),
      zipalign=BUILD_TOOLS / ("zipalign" + EXECUTABLE_SUFFIX),
      apksigner=BUILD_TOOLS / ("apksigner" + SCRIPT_SUFFIX),
      android_jar=android_jar,
    )
    keytool = JAVA_HOME / "bin" / ("keytool" + EXECUTABLE_SUFFIX)
    _make_key(keytool, fixture.official_keystore, "official", environment)
    _make_key(keytool, fixture.alternate_keystore, "alternate", environment)
    _make_key(keytool, fixture.patch_keystore, "patch", environment)
    base = _build_apk(root, "base", None, fixture.official_keystore, "official", fixture)
    companion = fixture.companion
    companion.mkdir()
    abi = _build_apk(companion, "split_config.arm64_v8a", "config.arm64_v8a",
                     fixture.official_keystore, "official", fixture)
    density = _build_apk(companion, "split_config.xxhdpi", "config.xxhdpi",
                         fixture.official_keystore, "official", fixture)
    metadata = {
      "schema_version": 1,
      "package_name": PACKAGE_NAME,
      "version_code": VERSION_CODE,
      "version_name": VERSION_NAME,
      "required_split_types": ["base__abi", "base__density"],
      "base": {"file_name": "base.apk", "sha256": "0" * 64},
      "splits": [
        {"file_name": abi.name, "split_name": "config.arm64_v8a", "split_type": "base__abi", "sha256": _sha256(abi)},
        {"file_name": density.name, "split_name": "config.xxhdpi", "split_type": "base__density", "sha256": _sha256(density)},
      ],
    }
    (companion / "apk+.json").write_text(json.dumps(metadata, sort_keys=True), encoding="utf-8")
    yield SyntheticSet(
      root=root,
      base_apk=base,
      companion=companion,
      official_keystore=fixture.official_keystore,
      alternate_keystore=fixture.alternate_keystore,
      patch_keystore=fixture.patch_keystore,
      environment=environment,
      aapt2=fixture.aapt2,
      zipalign=fixture.zipalign,
      apksigner=fixture.apksigner,
      android_jar=android_jar,
    )


def _sha256(path: Path) -> str:
  import hashlib

  digest = hashlib.sha256()
  with path.open("rb") as stream:
    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
      digest.update(chunk)
  return digest.hexdigest()
