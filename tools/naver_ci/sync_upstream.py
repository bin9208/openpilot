"""Mirror exactly one upstream branch without overwriting divergent history."""
import json
import os
import re
import subprocess

REPOSITORY = 'bin9208/openpilot'
UPSTREAM = 'ajouatom/openpilot'
BRANCH = 'carrot-wip'


def should_update(current, upstream, comparison):
  for sha in (current, upstream):
    if not isinstance(sha, str) or re.fullmatch(r'[0-9a-f]{40}', sha) is None:
      raise ValueError('Invalid commit SHA')
  if current == upstream:
    return False
  if (comparison.get('status') != 'ahead'
      or comparison.get('behind_by') != 0
      or not isinstance(comparison.get('ahead_by'), int)
      or comparison['ahead_by'] <= 0
      or comparison.get('base_commit', {}).get('sha') != current
      or comparison.get('merge_base_commit', {}).get('sha') != current):
    raise ValueError('Mirror is not a fast-forward of the recorded target; manual review required')
  return True


def api(endpoint, *args):
  result = subprocess.run(['gh', 'api', endpoint, *args], check=True,
                          capture_output=True, text=True, timeout=60)
  return json.loads(result.stdout)


def sync():
  if os.environ.get('GITHUB_REPOSITORY') != REPOSITORY:
    raise ValueError('This workflow is restricted to its explicitly configured fork')
  metadata = api(f'repos/{REPOSITORY}')
  if metadata.get('parent', {}).get('full_name') != UPSTREAM:
    raise ValueError('Unexpected fork parent')
  current = api(f'repos/{REPOSITORY}/git/ref/heads/{BRANCH}')['object']['sha']
  upstream = api(f'repos/{UPSTREAM}/git/ref/heads/{BRANCH}')['object']['sha']
  comparison = {} if current == upstream else api(f'repos/{REPOSITORY}/compare/{current}...{upstream}')
  changed = should_update(current, upstream, comparison)
  if changed:
    # GitHub enforces fast-forward again at mutation time, protecting against races.
    api(f'repos/{REPOSITORY}/git/refs/heads/{BRANCH}', '--method', 'PATCH',
        '-f', f'sha={upstream}', '-F', 'force=false')
  actual = api(f'repos/{REPOSITORY}/git/ref/heads/{BRANCH}')['object']['sha']
  if actual != upstream:
    raise RuntimeError('Post-sync commit does not match the observed upstream')
  report = {'repository': REPOSITORY, 'branch': BRANCH, 'before': current,
            'after': actual, 'changed': changed, 'force': False}
  print(json.dumps(report))
  if os.environ.get('GITHUB_STEP_SUMMARY'):
    with open(os.environ['GITHUB_STEP_SUMMARY'], 'a', encoding='utf-8') as stream:
      stream.write(f'Fast-forward-only mirror result:\n\n```json\n{json.dumps(report, indent=2)}\n```\n')


if __name__ == '__main__':
  sync()
