"""Fast syntax/whitespace checks; never presented as a build or device test."""
import ast
import os
from pathlib import Path
import subprocess


def run(base=None):
  base = base or os.environ.get('BASE_SHA', '')
  if not base or set(base) == {'0'}:
    # New branch/manual run: inspect its tip commit, rather than an invalid zero SHA.
    base = subprocess.check_output(['git', 'rev-parse', 'HEAD^'], text=True).strip()
  subprocess.run(['git', 'diff', '--check', base, 'HEAD'], check=True)
  paths = subprocess.check_output(['git', 'diff', '--name-only', '-z', '--diff-filter=ACMR', base, 'HEAD'])
  checked = 0
  for raw in paths.split(b'\0'):
    if not raw:
      continue
    path = Path(os.fsdecode(raw))
    if path.suffix == '.py' and path.is_file() and not path.is_symlink():
      ast.parse(path.read_bytes(), filename=str(path))
      checked += 1
  print(f'Whitespace checked; parsed {checked} changed Python files against {base}')


if __name__ == '__main__':
  run()
