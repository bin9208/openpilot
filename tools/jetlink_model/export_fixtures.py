"""Export the pinned upstream fixture bytes for Android/host conformance tests."""
from __future__ import annotations

import argparse
import json
import shutil
import tempfile
from pathlib import Path

from tools.jetlink_model.manifest import contained_file, sha256_file

FIXTURES = Path(__file__).with_name('fixtures')
REVISION = 'f10f4705243812518e6441dfb06bf2178c408310'


def export_fixtures(destination: Path) -> dict[str, str]:
  destination = Path(destination).absolute()
  if destination.exists():
    raise FileExistsError(destination)
  index = json.loads((FIXTURES / 'index.json').read_text(encoding='utf-8'))
  if index['revision'] != REVISION:
    raise ValueError('fixture revision mismatch')
  sources = {}
  for name, digest in index['files'].items():
    source = contained_file(FIXTURES, name)
    if sha256_file(source) != digest:
      raise ValueError(f'fixture hash mismatch: {name}')
    sources[name] = source
  destination.parent.mkdir(parents=True, exist_ok=True)
  with tempfile.TemporaryDirectory(prefix='.fixtures-', dir=destination.parent) as temporary:
    root = Path(temporary)
    for name, source in sources.items():
      target = root / name
      target.parent.mkdir(parents=True, exist_ok=True)
      shutil.copyfile(source, target)
      if sha256_file(target) != index['files'][name]:
        raise ValueError(f'fixture changed while copying: {name}')
    (root / 'index.json').write_text(json.dumps(index, indent=2), encoding='utf-8')
    root.rename(destination)
  return dict(index['files'])


def main():
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument('destination', type=Path)
  result = export_fixtures(parser.parse_args().destination)
  print(f'Exported {len(result)} verified fixture files from {REVISION}')


if __name__ == '__main__':
  main()
