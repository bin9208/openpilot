"""A model being ready is not a measured device/vehicle acceptance record."""
import json
import math
import re

from openpilot.selfdrive.modeld.jetlink.client import CONTRACT

WARP_CONTRACT = 'native-c3x-512x256-v1'
IDENTITY_KEYS = {'device_model', 'android_api', 'backend_requested', 'runtime_version', 'app_version'}


def validation_matches(raw, identity):
  try:
    if raw is None or len(raw) > 16_384 or set(identity) != IDENTITY_KEYS or not all(identity.values()):
      return False
    record = json.loads(raw)
    duration, maximum = record['duration_seconds'], record['end_to_end_max_ms']
    return (record['approved'] is True and record['device_test'] is True and record['numerical_parity'] is True
            and record['model_sha256'] == CONTRACT['sha256'] and record['identity'] == identity
            and record['warp_contract'] == WARP_CONTRACT and type(duration) in (int, float) and math.isfinite(duration) and duration >= 1800
            and type(maximum) in (int, float) and math.isfinite(maximum) and 0 <= maximum <= 50
            and type(record['deadline_misses']) is int and record['deadline_misses'] == 0
            and isinstance(record['validation_id'], str) and re.fullmatch('[0-9a-f]{64}', record['validation_id']) is not None)
  except (ValueError, TypeError, KeyError):
    return False
