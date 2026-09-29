"""USB role changes belong to the offroad owner, never the realtime loop."""
from pathlib import Path
import subprocess


class GadgetOwner:
  def __init__(self, setup, teardown):
    self.setup = setup
    self.teardown = teardown
    self.enabled = False

  def enable(self, mode: str, offroad: bool, egpu_present: bool):
    if mode not in ('shadow', 'active_request'):
      raise ValueError('Unknown Jetlink mode')
    if self.enabled:
      return
    if not offroad:
      raise RuntimeError('Jetlink USB setup requires offroad')
    if egpu_present:
      raise RuntimeError('Jetlink conflicts with the eGPU USB port')
    self.setup()
    self.enabled = True

  def disable(self, offroad: bool):
    if not self.enabled:
      return
    if not offroad:
      raise RuntimeError('Jetlink USB teardown requires offroad')
    self.teardown()
    self.enabled = False


def setup_gadget():
  # Fixed command, no caller-provided path/arguments and no installed sudoers policy.
  script = Path(__file__).with_name('setup_gadget.sh')
  subprocess.run(['sudo', '-n', 'bash', str(script)], check=True, timeout=15)
