from openpilot.cereal import log
from openpilot.common.realtime import DT_CTRL
from openpilot.selfdrive.selfdrived.events import Events, ET
from openpilot.selfdrive.selfdrived.state import StateMachine, SOFT_DISABLE_TIME


def test_jetlink_loss_soft_disables_and_remains_no_entry():
  events = Events()
  events.add(log.OnroadEvent.EventName.jetlinkLost)
  assert events.contains(ET.NO_ENTRY) and events.contains(ET.SOFT_DISABLE)
  machine = StateMachine()
  machine.state = log.SelfdriveState.OpenpilotState.enabled
  machine.update(events)
  assert machine.state == log.SelfdriveState.OpenpilotState.softDisabling
  for _ in range(int(SOFT_DISABLE_TIME / DT_CTRL) + 1):
    machine.update(events)
  assert machine.state == log.SelfdriveState.OpenpilotState.disabled
  events.add(log.OnroadEvent.EventName.buttonEnable)
  machine.update(events)
  assert machine.state == log.SelfdriveState.OpenpilotState.disabled


def test_loss_and_source_survive_native_fallback_frame_serialization():
  message = log.ModelDataV2.new_message()
  message.jetlink = {'source': 'native', 'lossLatched': True, 'phase': 'LOST', 'generation': 'test', 'frameId': 12}
  with log.ModelDataV2.from_bytes(message.to_bytes()) as received:
    assert received.jetlink.lossLatched and received.jetlink.source == 'native'
