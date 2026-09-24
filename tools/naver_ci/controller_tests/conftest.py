"""Host substitutes only for native Params, hardware and IPC; use real Capnp."""
import sys
from types import ModuleType

from openpilot.cereal import log


class MemoryParams:
  def __init__(self, *_args):
    self.values = {'IsMetric': '1', 'AutoNaviSpeedCtrlMode': '2',
      'AutoNaviSpeedSafetyFactor': '100', 'AutoNaviSpeedBumpSpeed': '25',
      'AutoNaviSpeedDecelRate': '135', 'AutoRoadSpeedLimitOffset': '-1'}

  def get(self, key, *args, **kwargs):
    return self.values.get(key, '0')

  def get_int(self, key):
    return int(self.get(key))

  def get_float(self, key):
    return float(self.get(key))

  def get_bool(self, key):
    return bool(self.get_int(key))

  def put_nonblocking(self, key, value):
    self.values[key] = value

  put = put_nonblocking

  def remove(self, key):
    self.values.pop(key, None)


params = ModuleType('openpilot.common.params')
params.Params = MemoryParams
params.ParamKeyType = type('ParamKeyType', (), {})
sys.modules[params.__name__] = params
hardware = ModuleType('openpilot.system.hardware')
hardware.PC, hardware.TICI = True, False
sys.modules[hardware.__name__] = hardware
messaging = ModuleType('openpilot.cereal.messaging')


def new_message(service, size=None):
  message = log.Event.new_message()
  message.valid = True
  message.init(service) if size is None else message.init(service, size)
  return message


messaging.new_message = new_message
sys.modules[messaging.__name__] = messaging
