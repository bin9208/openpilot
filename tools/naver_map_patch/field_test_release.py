"""Local-only single field TEST APK; never a public-release acceptance gate."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import zipfile

from tools.naver_map_patch import diagnostic_patch as diagnostic
from tools.naver_map_patch import public_beta_release as archive_tools
from tools.naver_map_patch.packaging_cli import _run_verify
from tools.naver_map_patch.packaging_cli_args import OutputArguments, _android_tools
from tools.naver_map_patch.profile import load_profile
from tools.naver_map_patch.split_package import ErrorCode, PackagingError, SigningConfig
from tools.naver_map_patch.split_tools import align_and_sign, resolve_signing_config, sha256_file


TEST_APK_NAME = 'NaverMap-6.8.0.5-carrot-field-TEST.apk'


def validate_output(input_root: Path, output_root: Path) -> Path:
  source, output = input_root.resolve(), output_root.resolve()
  if output == source or source in output.parents:
    raise PackagingError(ErrorCode.UNSAFE_PATH, 'test output must be outside split inputs', path=output)
  if output.exists():
    raise PackagingError(ErrorCode.STALE_OUTPUT, 'test output must be fresh', path=output)
  return output


def _dex_inventory(path: Path) -> dict[str, str]:
  with zipfile.ZipFile(path) as archive:
    names = archive.namelist()
    if len(names) != len(set(names)):
      raise PackagingError(ErrorCode.MALFORMED_METADATA, 'duplicate APK entries', path=path)
    result = {}
    for name in names:
      if name.endswith('.dex'):
        with archive.open(name) as stream:
          result[name] = hashlib.file_digest(stream, 'sha256').hexdigest()
    return result


def verify_field_test(request: archive_tools.StandaloneVerificationRequest, patched_base: Path):
  # Reuse only format/provenance guards, not the production-only verifier.
  archive_tools._verify_authoritative_base(request)
  libraries = archive_tools._verify_authoritative_arm64_split(request)
  archive = archive_tools._verify_archive(request.standalone_apk)
  if archive.native_libraries != libraries:
    raise PackagingError(ErrorCode.OUTPUT_HASH_MISMATCH, 'standalone native libraries differ from original')
  if _dex_inventory(patched_base) != _dex_inventory(request.standalone_apk):
    raise PackagingError(ErrorCode.OUTPUT_HASH_MISMATCH, 'merged DEX differs from verified patched base')
  original = archive_tools.inspect_apk(request.original_base_apk, request.tools)
  standalone = archive_tools.inspect_apk(request.standalone_apk, request.tools)
  expected = archive_tools.inspect_apk(patched_base, request.tools)
  archive_tools._verify_original_identity(original, request.profile)
  archive_tools._verify_standalone_identity(standalone)
  if standalone.signer_sha256 != expected.signer_sha256:
    raise PackagingError(ErrorCode.SIGNER_MISMATCH, 'single test APK signer differs from verified split set')
  for mode, check in [('badging', archive_tools._verify_badging),
                      ('xmltree', archive_tools._verify_manifest_inventory)]:
    check(archive_tools._android_dump(request.original_base_apk, request.tools, mode),
          archive_tools._android_dump(request.standalone_apk, request.tools, mode), request.standalone_apk)
  archive_tools.verify_alignment(request.standalone_apk, request.tools)
  diagnostic.verify_field_acceptance_archive(request.standalone_apk, request.profile)
  identity = diagnostic.installed_diagnostic_payload_identity(request.standalone_apk, request.profile)
  if (identity.mode != 'diagnostic' or identity.build_id != diagnostic.FIELD_ACCEPTANCE_BUILD_ID
      or identity.dex_entry != 'classes43.dex'
      or identity.sha256 != hashlib.sha256(archive.payload).hexdigest()):
    raise PackagingError(ErrorCode.OUTPUT_HASH_MISMATCH, 'single test field identity mismatch')
  return identity


def main(argv=None) -> int:
  parser = argparse.ArgumentParser(description=__doc__)
  for name in ('split-output', 'base', 'arm64', 'output', 'apkeditor', 'keystore'):
    parser.add_argument('--' + name, type=Path, required=True)
  parser.add_argument('--store-password-env', required=True)
  parser.add_argument('--key-password-env', required=True)
  args = parser.parse_args(argv)
  environment = os.environ.copy()
  output = validate_output(args.split_output, args.output)
  split_report = json.loads(_run_verify(OutputArguments(args.split_output, '6.8.0.5'), environment))
  if split_report['payload_identity']['build_id'] != diagnostic.FIELD_ACCEPTANCE_BUILD_ID:
    raise PackagingError(ErrorCode.OUTPUT_HASH_MISMATCH, 'a verified field split set is required')
  profile = load_profile('6.8.0.5')
  tools = _android_tools(environment)
  signer = resolve_signing_config(SigningConfig(args.keystore, None,
    args.store_password_env, args.key_password_env), tools)
  source_root = Path(__file__).resolve().parents[2]
  subprocess.run(['git', '-C', str(source_root), 'diff', '--exit-code', 'HEAD', '--', 'tools/naver_map_patch'],
    check=True, capture_output=True, text=True, timeout=10)
  commit = subprocess.run(['git', '-C', str(source_root), 'rev-parse', 'HEAD'],
    check=True, capture_output=True, text=True, timeout=10).stdout.strip()
  output.parent.mkdir(parents=True, exist_ok=True)
  install_set = args.split_output / 'install-set'
  with tempfile.TemporaryDirectory(prefix='.field-test-', dir=output.parent) as temporary:
    staging = Path(temporary) / 'verified'
    staging.mkdir()
    apk = staging / TEST_APK_NAME
    merged = archive_tools.merge_public_beta(archive_tools.MergeRequest(
      install_set, args.apkeditor, tools.java_home / 'bin' / ('java.exe' if os.name == 'nt' else 'java'),
      Path(temporary) / 'merge'))
    align_and_sign(merged.apk_path, apk, signer, tools)
    identity = verify_field_test(archive_tools.StandaloneVerificationRequest(
      args.base, args.arm64, apk, profile, tools, environment), install_set / 'base.apk')
    for language in ('KO', 'EN'):
      shutil.copyfile(Path(__file__).parent / 'test_release' / f'README_{language}.md', staging / f'README_{language}.md')
    digest = str(sha256_file(apk))
    report = {'schema_version': 1, 'artifact_kind': 'field-test-not-public-release', 'source_commit': commit,
      'packager_sha256': str(sha256_file(Path(__file__))), 'apk': apk.name, 'sha256': digest, 'size': apk.stat().st_size,
      'payload_build_id': identity.build_id, 'payload_sha256': str(identity.sha256),
      'signer_sha256': split_report['signer_sha256'], 'installed': False,
      'hook_verification': 'same-run split build anchor checks plus exact final DEX preservation',
      'device_acceptance': False, 'offline_writer': 'app-internal SQLite; device verification pending'}
    (staging / 'provenance.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    (staging / 'SHA256SUMS.txt').write_text(f'{digest}  {apk.name}\n', encoding='utf-8')
    if output.exists():
      raise PackagingError(ErrorCode.STALE_OUTPUT, 'test output appeared during build', path=output)
    staging.rename(output)
  print(json.dumps({'success': True, 'output': str(output), **report}, ensure_ascii=False))
  return 0


if __name__ == '__main__':
  raise SystemExit(main())
