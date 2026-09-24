import unittest
from unittest.mock import patch

from tools.naver_ci import sync_upstream as sync

A = 'a' * 40
B = 'b' * 40


class SyncGuards(unittest.TestCase):
  def comparison(self):
    return {'status': 'ahead', 'behind_by': 0, 'ahead_by': 1,
            'base_commit': {'sha': A}, 'merge_base_commit': {'sha': A}}

  def test_identical_is_noop(self):
    self.assertFalse(sync.should_update(A, A, {}))

  def test_fast_forward_is_allowed(self):
    self.assertTrue(sync.should_update(A, B, self.comparison()))

  def test_divergent_or_rewound_upstream_is_rejected(self):
    for status in ('diverged', 'behind', 'identical'):
      comparison = self.comparison()
      comparison['status'] = status
      with self.subTest(status=status), self.assertRaises(ValueError):
        sync.should_update(A, B, comparison)

  def test_racing_or_unrelated_comparison_is_rejected(self):
    for key, value in [('base_commit', {'sha': B}), ('merge_base_commit', {'sha': B}), ('behind_by', 1), ('ahead_by', 0)]:
      comparison = self.comparison()
      comparison[key] = value
      with self.subTest(key=key), self.assertRaises(ValueError):
        sync.should_update(A, B, comparison)

  def test_malformed_sha_is_rejected(self):
    for value in ('', 'main', None, 'a' * 39):
      with self.subTest(value=value), self.assertRaises(ValueError):
        sync.should_update(value, B, self.comparison())

  def test_other_repository_cannot_mutate(self):
    with patch.dict('os.environ', {'GITHUB_REPOSITORY': 'someone/other'}), patch.object(sync, 'api') as api:
      with self.assertRaises(ValueError):
        sync.sync()
      api.assert_not_called()

  def test_only_expected_ref_is_updated_without_force(self):
    responses = [{'parent': {'full_name': sync.UPSTREAM}}, {'object': {'sha': A}},
                 {'object': {'sha': B}}, self.comparison(), {}, {'object': {'sha': B}}]
    with patch.dict('os.environ', {'GITHUB_REPOSITORY': sync.REPOSITORY, 'GITHUB_STEP_SUMMARY': ''}), patch.object(sync, 'api', side_effect=responses) as api:
      sync.sync()
    self.assertEqual(api.call_args_list[4].args,
                     ('repos/bin9208/openpilot/git/refs/heads/carrot-wip', '--method', 'PATCH', '-f', f'sha={B}', '-F', 'force=false'))

  def test_wrong_parent_is_rejected_before_reading_refs(self):
    with patch.dict('os.environ', {'GITHUB_REPOSITORY': sync.REPOSITORY}), patch.object(sync, 'api', return_value={'parent': {'full_name': 'other/repo'}}) as api:
      with self.assertRaises(ValueError):
        sync.sync()
      self.assertEqual(api.call_count, 1)


if __name__ == '__main__':
  unittest.main()
