"""Check actual Java-produced wire JSON at the real Python ingress boundary."""
import json
import os
from pathlib import Path
import subprocess

from openpilot.selfdrive.carrot.naver_navigation_protocol import parse_naver_navigation_v1
from openpilot.selfdrive.carrot.navigation_runtime import NavigationRuntime


def main():
  target = Path(__file__).with_name('java') / 'target'
  classpath = os.pathsep.join(str(target / name) for name in ('classes', 'test-classes'))
  output = subprocess.check_output(
    ['java', '-cp', classpath, 'ai.comma.naver.payload.BumpWireFixture'], text=True)
  frames = [json.loads(line) for line in output.splitlines()]
  assert len(frames) == 2
  runtime = NavigationRuntime()
  assert runtime.accept_snapshot(parse_naver_navigation_v1(frames[0], 10.))
  selected, control = runtime.select(10.)
  assert selected.snapshot.source.value == 'naver_v1'
  assert control.road_category is None
  assert control.speed.secondary_sdi_present
  assert control.speed.secondary_sdi_type == 22
  assert control.speed.secondary_sdi_distance_m == 30
  assert runtime.accept_snapshot(parse_naver_navigation_v1(frames[1], 10.1))
  assert runtime.select(10.1)[1] is None
  print('Java objects -> production envelope -> strict Python parser -> canonical bump: passed')


if __name__ == '__main__':
  main()
