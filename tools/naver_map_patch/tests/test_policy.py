from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tomllib


REPO_ROOT = Path(__file__).resolve().parents[3]
POLICY_PATH = REPO_ROOT / "tools" / "naver_map_patch" / "policy.json"
SCRIPT_PATH = REPO_ROOT / "tools" / "naver_map_patch" / "naver_patch.py"


def test_policy_locks_task9_source_boundary_and_forbids_sensitive_material() -> None:
  # Given: the machine-readable Task 9 import policy.

  # When: a consumer loads its prohibited-material and protected-path rules.
  policy = json.loads(POLICY_PATH.read_text(encoding="utf-8"))

  # Then: only reviewed tool text is owned and unsafe source/material classes are fail-closed.
  assert policy["schema_version"] == 1
  assert policy["task"] == 9
  assert policy["allowed_paths"] == ["tools/naver_map_patch/**"]
  assert {"openpilot/**", "docs/**", ".superpowers/**"} <= set(policy["protected_paths"])
  prohibited_imports = set(policy["prohibited_import_paths"])
  assert {
    "tests/fixtures/**", "navigation_control_replay*.py", "dexpatch/build/**",
    "dexpatch/.gradle/**", "**/__pycache__/**",
  } <= prohibited_imports
  assert "payload/**" not in prohibited_imports  # Reviewed source, never compiled payload bytes.
  prohibited = set(policy["prohibited_material"])
  assert {"proprietary_apk", "proprietary_dex", "proprietary_resources", "raw_route_location_capture",
          "keystore", "password", "certificate", "private_key"} <= prohibited


def test_gitignore_covers_required_artifact_classes() -> None:
  # Given: representative prohibited paths under the patch-tool directory.
  paths = [
    "tools/naver_map_patch/input/source.apk",
    "tools/naver_map_patch/out/patched.apk+",
    "tools/naver_map_patch/work/classes.dex",
    "tools/naver_map_patch/captures/raw.pcapng",
    "tools/naver_map_patch/keys/signing.jks",
    "tools/naver_map_patch/secrets/signing.password",
    "tools/naver_map_patch/private.pem",
    "tools/naver_map_patch/dexpatch/build/classes/Payload.class",
    "tools/naver_map_patch/dexpatch/.gradle/cache.bin",
    "tools/naver_map_patch/tests/__pycache__/test_profile.pyc",
    "tools/naver_map_patch/capture/raw.jsonl",
    "tools/naver_map_patch/run.log",
    "tools/naver_map_patch/payload.jar",
    "tools/naver_map_patch/capture/capture-v1.sqlite3",
    "tools/naver_map_patch/capture/capture-v1.sqlite3-wal",
    "tools/naver_map_patch/capture/capture-v1.sqlite3-shm",
  ]

  # When: Git evaluates the directory-scoped ignore policy without creating files.
  process = subprocess.run(
    ["git", "check-ignore", "--no-index", *paths],
    cwd=REPO_ROOT,
    capture_output=True,
    text=True,
    timeout=10,
    check=False,
  )

  # Then: every representative path is ignored.
  assert process.returncode == 0
  assert set(process.stdout.splitlines()) == set(paths)


def test_executable_script_declares_pep723_contract() -> None:
  # Given: the executable patch-tool source as a structural artifact.
  lines = SCRIPT_PATH.read_text(encoding="utf-8").splitlines()

  # When: its PEP 723 block and run markers are parsed.
  metadata_start = lines.index("# /// script")
  metadata_end = lines.index("# ///", metadata_start + 1)
  metadata = tomllib.loads("\n".join(line.removeprefix("# ") for line in lines[metadata_start + 1:metadata_end]))

  # Then: uv and direct-Python execution contracts are both explicit.
  assert lines[0] == "#!/usr/bin/env -S uv run --script"
  assert metadata == {"requires-python": ">=3.11", "dependencies": []}
  assert "# ─── How to run ───" in lines
  assert any("python tools/naver_map_patch/naver_patch.py doctor --json" in line for line in lines)
  assert any("uv run --script tools/naver_map_patch/naver_patch.py doctor --json" in line for line in lines)


def test_doctor_rejects_extra_protected_path_without_metadata_change() -> None:
  # Given: a protected user-owned source and the machine-readable CLI policy.
  protected_path = REPO_ROOT / "openpilot" / "cereal" / "custom.capnp"
  policy = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
  before = os.lstat(protected_path)

  # When: a caller tries to supply the protected path to the only CLI operation.
  process = subprocess.run(
    [sys.executable, str(SCRIPT_PATH), "doctor", "--json", str(protected_path)],
    cwd=REPO_ROOT,
    capture_output=True,
    text=True,
    timeout=10,
    check=False,
  )
  after = os.lstat(protected_path)

  # Then: the argument is rejected before any path access and metadata stays identical.
  assert policy["cli_surface"] == {
    "operations": ["doctor", "inspect", "build", "verify", "install-command", "extract-capture"],
    "accepts_local_artifact_paths": True,
    "writes_only_to_explicit_output": True,
  }
  assert process.returncode == 64
  assert process.stdout == ""
  assert process.stderr == "usage: naver_patch.py doctor --json\n"
  assert (before.st_mode, before.st_size, before.st_mtime_ns, before.st_ctime_ns, before.st_ino, before.st_dev) == (
    after.st_mode, after.st_size, after.st_mtime_ns, after.st_ctime_ns, after.st_ino, after.st_dev,
  )
