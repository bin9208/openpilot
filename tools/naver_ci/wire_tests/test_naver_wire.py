import base64
import json
import os
from pathlib import Path
import subprocess
import capnp

from openpilot.cereal import custom, log
from openpilot.selfdrive.carrot.realtime.compact_state import encode_carrot_state_frame

EXPECTED = {
  'activeCarrot': 5, 'xSpdType': 22, 'xSpdLimit': 25, 'xSpdDist': 120,
  'vehicleNaviActive': True, 'vehicleNaviSpeed': 60,
  'vehicleNaviSectionActive': True, 'vehicleNaviAvailable': True,
  'naviOwner': 'naver_v1', 'naviSessionId': 'synthetic-session', 'naviSequence': 1234,
  'naviOwnerAgeMs': 200, 'naviSafetyAgeMs': 100, 'naviLifecycle': 'guiding',
  'naviControlAllowed': True, 'naviSafetyRejection': '',
  'decelProvider': 'naver_v1', 'decelReason': 'bump',
}


def make_event():
  fields = custom.CarrotMan.schema.fields
  assert set(EXPECTED) <= set(fields), 'Naver wire diagnostics are not yet added to upstream schema'
  message = log.Event.new_message()
  message.valid = True
  message.logMonoTime = 1000000
  carrot = message.init('carrotMan')
  for name, value in EXPECTED.items():
    setattr(carrot, name, value)
  return message


def test_vehicle_fields_keep_their_upstream_ordinals():
  expected = {'vehicleNaviActive': 29, 'vehicleNaviSpeed': 30,
              'vehicleNaviSectionActive': 31, 'vehicleNaviAvailable': 32}
  for name, ordinal in expected.items():
    assert custom.CarrotMan.schema.fields[name].proto.ordinal.explicit == ordinal


def test_real_pre_naver_schema_message_remains_readable():
  fixture = Path(__file__).with_name('legacy_carrot.capnp')
  cereal_root = Path(__file__).resolve().parents[3] / 'openpilot' / 'cereal'
  parser = capnp.SchemaParser()
  old = parser.load(str(fixture), imports=[str(cereal_root)])
  message = old.CarrotMan.new_message(vehicleNaviActive=True, vehicleNaviSpeed=73,
                                     vehicleNaviSectionActive=True, vehicleNaviAvailable=True)
  with custom.CarrotMan.from_bytes(message.to_bytes()) as decoded:
    assert decoded.vehicleNaviActive
    assert decoded.vehicleNaviSpeed == 73
    assert decoded.vehicleNaviSectionActive
    assert decoded.vehicleNaviAvailable
    assert decoded.naviOwner == ''
    assert decoded.naviSequence == 0


def test_actual_capnp_roundtrip_and_all_display_encoders_agree(tmp_path):
  message = make_event()
  raw = message.to_bytes()
  with log.Event.from_bytes(raw) as decoded:
    for name, value in EXPECTED.items():
      assert getattr(decoded.carrotMan, name) == value
  source = tmp_path / 'synthetic-event.bin'
  source.write_bytes(raw)
  native = subprocess.run([os.environ['NAVER_WIRE_PROBE'], str(source)],
                          capture_output=True, check=True).stdout
  compact = encode_carrot_state_frame('carrotMan', EXPECTED, 7)
  assert native == compact, 'Real C++ and Python compact encoders disagree'
  fixture = tmp_path / 'synthetic-wire.json'
  fixture.write_text(json.dumps({'expected': EXPECTED,
    'raw': base64.b64encode(raw).decode(), 'compact': base64.b64encode(compact).decode()}))
  subprocess.run(['node', 'tools/naver_ci/check_wire.mjs', str(fixture)], check=True)


def test_new_fields_default_when_not_published():
  fields = custom.CarrotMan.schema.fields
  assert 'naviOwner' in fields, 'Naver diagnostic fields are missing'
  event = log.Event.new_message()
  event.init('carrotMan').vehicleNaviAvailable = True
  with log.Event.from_bytes(event.to_bytes()) as decoded:
    assert decoded.carrotMan.vehicleNaviAvailable
    assert decoded.carrotMan.naviOwner == ''
    assert decoded.carrotMan.naviSequence == 0
    assert not decoded.carrotMan.naviControlAllowed
