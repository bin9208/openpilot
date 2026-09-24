from pathlib import Path

import pytest

from tools.naver_map_patch.diagnostic_patch import build_production_archive_patches
from tools.naver_map_patch.profile import load_profile
from tools.naver_map_patch.split_package import PackagingError


PATCH_ROOT = Path(__file__).resolve().parents[1]


def test_production_payload_fails_closed_before_building_from_an_unverified_base():
  with pytest.raises(PackagingError, match="authoritative base is not readable"):
    build_production_archive_patches(Path("not-used.apk"), load_profile("6.8.0.5"), {})
