import json
from openpilot.selfdrive.modeld.jetlink.validation import validation_matches, WARP_CONTRACT
from openpilot.selfdrive.modeld.jetlink.client import CONTRACT


IDENTITY = {'device_model': 'SM-X916N', 'android_api': '35', 'backend_requested': 'NNAPI',
            'runtime_version': '1.22.0', 'app_version': '0.1.1-experimental', 'artifact_sha256': CONTRACT['sha256']}


def receipt():
  return {'approved': True, 'model_sha256': CONTRACT['sha256'], 'identity': IDENTITY.copy(),
          'warp_contract': WARP_CONTRACT, 'duration_seconds': 1800, 'end_to_end_max_ms': 49.5,
          'deadline_misses': 0, 'numerical_parity': True, 'device_test': True, 'validation_id': 'a' * 64}


def test_missing_or_unmeasured_profile_cannot_activate():
  assert not validation_matches(None, IDENTITY)
  assert not validation_matches(json.dumps(receipt()), {})
  assert validation_matches(json.dumps(receipt()), IDENTITY)
  for key, value in [('approved', False), ('duration_seconds', 1799), ('end_to_end_max_ms', 50.1),
                     ('deadline_misses', 1), ('numerical_parity', False), ('device_test', False)]:
    value_dict = receipt(); value_dict[key] = value
    assert not validation_matches(json.dumps(value_dict), IDENTITY)
  assert not validation_matches(json.dumps(receipt()), {**IDENTITY, 'backend_requested': 'CPU'})
  assert not validation_matches(json.dumps(receipt()), {**IDENTITY, 'artifact_sha256': 'b' * 64})
