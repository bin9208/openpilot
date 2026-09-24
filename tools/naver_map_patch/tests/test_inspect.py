from __future__ import annotations

from pathlib import Path
import subprocess
import sys


REPO_ROOT = Path(__file__).resolve().parents[3]
PATCHER = REPO_ROOT / "tools" / "naver_map_patch" / "naver_patch.py"
def test_inspect_rejects_unknown_profile_before_stdout(tmp_path: Path) -> None:
  # Given: an existing but unsupported input file and profile name.
  base = tmp_path / "input.bin"
  base.write_bytes(b"unsupported")

  # When: the public CLI is asked to inspect it.
  process = subprocess.run(
    [sys.executable, str(PATCHER), "inspect", "--base", str(base), "--splits-from", str(tmp_path),
     "--profile", "unknown", "--json"],
    cwd=REPO_ROOT,
    capture_output=True,
    text=True,
    check=False,
  )

  # Then: the boundary fails closed and emits no success-shaped stdout.
  assert process.returncode != 0
  assert process.stdout == ""
  assert "unsupported profile" in process.stderr


def test_inspect_rejects_missing_base_before_stdout(tmp_path: Path) -> None:
  # Given: the supported profile but a missing authoritative input path.
  missing = tmp_path / "missing.apk"

  # When: the public CLI is asked to inspect it.
  process = subprocess.run(
    [sys.executable, str(PATCHER), "inspect", "--base", str(missing), "--splits-from", str(tmp_path),
     "--profile", "6.8.0.5", "--json"],
    cwd=REPO_ROOT,
    capture_output=True,
    text=True,
    check=False,
  )

  # Then: the boundary emits no success-shaped JSON or output artifact.
  assert process.returncode != 0
  assert process.stdout == ""
  assert "base input is not a regular file" in process.stderr
  assert tuple(tmp_path.iterdir()) == ()
