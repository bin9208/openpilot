from __future__ import annotations

import json
from pathlib import Path
from typing import get_type_hints

from tools.naver_map_patch import profile as profile_module
from tools.naver_map_patch.profile import load_profile


PROFILE = Path(__file__).resolve().parents[1] / "profiles" / "6.8.0.5.json"


def _raw_profile() -> dict[str, object]:
  return json.loads(PROFILE.read_text(encoding="utf-8"))


def test_profile_function_annotations_resolve() -> None:
  hints = get_type_hints(profile_module._accessor_allowlists)

  assert set(hints) == {"value", "return"}


def test_profile_locks_hooked_dex_sizes_and_hashes() -> None:
  profile = _raw_profile()

  assert profile["dex_entries"] == {
    "classes4.dex": {
      "size": 2_575_784,
      "sha256": "1a9f51a1c9778780976ad8e94a72024b9ca92050a9f0a1214d338da74ec33b9a",
    },
    "classes6.dex": {
      "size": 3_474_604,
      "sha256": "7954680561ebf0acc24577fcb90e52e8308353c4c394afe46e8ca4296a0d4c72",
    },
    "classes11.dex": {
      "size": 5_471_308,
      "sha256": "37cb3063ff38478eae7ac48f782d325ce78b8abae2396fd8adbb3121e8549a3a",
    },
  }


def test_profile_has_no_simulation_payload_mode() -> None:
  profile = _raw_profile()

  assert profile["payload_modes"] == ["diagnostic", "production"]
  assert "simulation" not in profile["payload_modes"]


def test_profile_loader_exposes_typed_dex_entries_and_payload_modes() -> None:
  profile = load_profile("6.8.0.5")

  assert profile.dex_entries["classes4.dex"].size == 2_575_784
  assert profile.dex_entries["classes11.dex"].sha256 == (
    "37cb3063ff38478eae7ac48f782d325ce78b8abae2396fd8adbb3121e8549a3a"
  )
  assert profile.payload_modes == ("diagnostic", "production")
