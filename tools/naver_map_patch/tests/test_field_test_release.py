from dataclasses import replace
import hashlib
import shutil
import zipfile

import pytest

from tools.naver_map_patch import field_test_release as field
from tools.naver_map_patch.split_package import PackagingError, PayloadIdentity, Sha256
from test_public_beta_release import _verification_request, _badging, _write_apk


def field_request(tmp_path, monkeypatch, **kwargs):
  payload = b'dex\nsynthetic-field-v4'
  request, calls = _verification_request(tmp_path, monkeypatch, payload=payload, **kwargs)
  patched = tmp_path / 'verified-patched-base.apk'
  shutil.copyfile(request.standalone_apk, patched)
  monkeypatch.setattr(field.diagnostic, 'verify_field_acceptance_archive',
    lambda path, profile: calls.append('field-check'))
  monkeypatch.setattr(field.diagnostic, 'installed_diagnostic_payload_identity',
    lambda path, profile: PayloadIdentity('diagnostic', field.diagnostic.FIELD_ACCEPTANCE_BUILD_ID,
      'classes43.dex', Sha256(hashlib.sha256(payload).hexdigest())))
  return request, patched, calls


def test_single_field_keeps_diagnostics_without_claiming_public_release(tmp_path, monkeypatch):
  request, patched, calls = field_request(tmp_path, monkeypatch)
  identity = field.verify_field_test(request, patched)
  assert identity.build_id == field.diagnostic.FIELD_ACCEPTANCE_BUILD_ID
  assert 'field-check' in calls
  assert not any(call.startswith('production:') for call in calls)
  assert field.TEST_APK_NAME.endswith('-TEST.apk')
  assert 'public-beta' not in field.TEST_APK_NAME


@pytest.mark.parametrize('kwargs', [{'arm64_libraries': 32}, {'library_mutation': 'content'},
                                  {'standalone_badging': _badging(min_sdk=23)}])
def test_field_verifier_retains_native_library_and_sdk_guards(tmp_path, monkeypatch, kwargs):
  request, patched, calls = field_request(tmp_path, monkeypatch, **kwargs)
  with pytest.raises(PackagingError):
    field.verify_field_test(request, patched)


def test_merge_cannot_mutate_patched_hook_or_payload_dex(tmp_path, monkeypatch):
  request, patched, calls = field_request(tmp_path, monkeypatch)
  _write_apk(request.standalone_apk, b'dex\ntampered-field')
  with pytest.raises(PackagingError, match='DEX'):
    field.verify_field_test(request, patched)


def test_duplicate_dex_is_rejected(tmp_path, monkeypatch):
  request, patched, calls = field_request(tmp_path, monkeypatch)
  with zipfile.ZipFile(request.standalone_apk, 'a') as archive:
    with pytest.warns(UserWarning):
      archive.writestr('classes43.dex', b'duplicate')
  with pytest.raises(PackagingError):
    field.verify_field_test(request, patched)


def test_output_must_be_fresh_and_outside_split_inputs(tmp_path):
  inputs = tmp_path / 'split'
  inputs.mkdir()
  with pytest.raises(PackagingError):
    field.validate_output(inputs, inputs / 'output')
  with pytest.raises(PackagingError):
    field.validate_output(inputs, tmp_path)
  fresh = tmp_path / 'test-output'
  assert field.validate_output(inputs, fresh) == fresh.resolve()


def test_wrong_field_identity_cannot_pass_as_diagnostic(tmp_path, monkeypatch):
  request, patched, calls = field_request(tmp_path, monkeypatch)
  original = field.diagnostic.installed_diagnostic_payload_identity
  monkeypatch.setattr(field.diagnostic, 'installed_diagnostic_payload_identity',
    lambda path, profile: replace(original(path, profile), build_id='wrong-field-id'))
  with pytest.raises(PackagingError, match='identity'):
    field.verify_field_test(request, patched)
