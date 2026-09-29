import importlib.util
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / 'check_changed_files.py'
spec = importlib.util.spec_from_file_location('changed_files', SCRIPT)
checker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checker)


class InitialPushTests(unittest.TestCase):
  def test_initial_push_checks_earlier_commits(self):
    with tempfile.TemporaryDirectory() as directory:
      previous = Path.cwd()
      try:
        os.chdir(directory)
        def git(*args):
          return subprocess.run(['git', *args], check=True, capture_output=True)
        git('init')
        git('config', 'user.name', 'CI test')
        git('config', 'user.email', 'ci@example.invalid')
        Path('base.txt').write_text('base\n')
        git('add', '.')
        git('commit', '-m', 'base')
        git('update-ref', 'refs/remotes/origin/dev', 'HEAD')
        Path('broken.py').write_text('def broken(:\n')
        git('add', '.')
        git('commit', '-m', 'earlier syntax error')
        Path('last.txt').write_text('last\n')
        git('add', '.')
        git('commit', '-m', 'later unrelated change')
        with patch.dict(os.environ, {'BASE_SHA': '0' * 40}):
          with self.assertRaises(SyntaxError):
            checker.run()
      finally:
        os.chdir(previous)
