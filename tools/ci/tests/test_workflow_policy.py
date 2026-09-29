"""Regression guard for required-check routing and fail-closed aggregation."""
from pathlib import Path
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[3]


def workflow(name):
  return yaml.load((ROOT / '.github/workflows' / name).read_text(), Loader=yaml.BaseLoader)


class WorkflowPolicyTests(unittest.TestCase):
  def test_fast_checks_cover_all_branch_pushes_and_protected_prs(self):
    events = workflow('fast-checks.yml')['on']
    self.assertEqual(events['push']['branches'], ['**'])
    self.assertEqual(set(events['pull_request']['branches']), {'dev', 'main'})
    for event in ('push', 'pull_request'):
      self.assertNotIn('paths', events[event])
      self.assertNotIn('paths-ignore', events[event])

  def test_integration_runs_before_and_after_merge_without_path_skips(self):
    spec = workflow('integration.yml')
    for event in ('push', 'pull_request'):
      self.assertEqual(spec['on'][event], {'branches': ['dev', 'main']})
    self.assertEqual(spec['jobs']['build']['with']['run_number'], '1')
    gate = spec['jobs']['gate']
    self.assertEqual(set(gate['needs']), {'build', 'navigation', 'apk'})
    self.assertEqual(gate['if'], '${{ always() }}')
    step = gate['steps'][0]
    for name in ('BUILD', 'NAVIGATION', 'APK'):
      self.assertIn(f'test "${name}" = success', step['run'])
    for job in ('build', 'navigation', 'apk'):
      self.assertNotIn('if', spec['jobs'][job])
      self.assertNotIn('continue-on-error', spec['jobs'][job])
      called = Path(spec['jobs'][job]['uses']).name
      self.assertIn('workflow_call', workflow(called)['on'])

  def test_documentation_check_runs_on_protected_branch_pushes(self):
    events = workflow('user-docs.yaml')['on']
    self.assertTrue({'main', 'dev'}.issubset(events['push']['branches']))
    self.assertIn('pull_request', events)


if __name__ == '__main__':
  unittest.main()
