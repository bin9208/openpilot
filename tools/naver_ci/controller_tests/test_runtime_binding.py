import json
import threading
from types import SimpleNamespace

import pytest

from openpilot.selfdrive.carrot import carrot_serv, carrot_man
from openpilot.selfdrive.carrot.naver_navigation_protocol import parse_naver_navigation_v1
from openpilot.selfdrive.carrot.tests.test_naver_navigation_protocol import valid_frame, inactive_frame
from openpilot.selfdrive.carrot.navigation_sources import NavigationSource


class SubMaster(dict):
  alive = {'carrotNavi': False, 'carState': False, 'selfdriveState': False, 'navInstruction': False}
  valid = {'carrotNavi': False}
  updated = {'carrotNavi': False}


@pytest.fixture
def bound(monkeypatch):
  now = [10.]
  monkeypatch.setattr(carrot_serv.time, 'monotonic', lambda: now[0])
  serv = carrot_serv.CarrotServ()
  manager = carrot_man.CarrotMan.__new__(carrot_man.CarrotMan)
  manager.carrot_serv = serv
  manager.remote_addr = None
  manager.params_memory = serv.params_memory
  manager._rgdata_ts_lock = threading.Lock()
  manager._navi_event_lock = threading.Lock()
  manager._last_navi_event_by_type = {}
  assert hasattr(manager, '_init_navigation_ingress'), 'CarrotMan ingress is not bound'
  manager._init_navigation_ingress()
  return manager, serv, now


class Client:
  def __init__(self, *frames):
    self.data = bytearray(b''.join(json.dumps(f).encode() + b'\n' for f in frames))

  def settimeout(self, value):
    pass

  def setsockopt(self, *args):
    pass

  def recv(self, size):
    data = bytes(self.data[:size])
    del self.data[:size]
    return data

  def close(self):
    pass


def test_manager_eof_keeps_real_controller_guidance_until_lease(bound):
  manager, serv, now = bound
  manager._serve_navi_client(Client(valid_frame()), ('192.0.2.1', 1234))
  assert serv._update_carrot_navi(SubMaster())
  assert serv.xTurnInfo == 1 and serv.xDistToTurn == 380
  assert serv.navigation_selection.transport_loss_age_s == 0
  now[0] = 11.999
  assert serv._update_carrot_navi(SubMaster())
  now[0] = 12.
  assert not serv._update_carrot_navi(SubMaster())
  assert serv.active_count == 0 and serv.xTurnInfo == -1


@pytest.mark.parametrize('terminal', ['stopped', 'arrived'])
def test_terminal_tombstone_survives_manager_reconnect(bound, terminal):
  manager, serv, now = bound
  manager._serve_navi_client(Client(valid_frame(sequence=1)), ('192.0.2.1', 1234))
  frame = inactive_frame(terminal)
  frame['sequence'] = 2
  manager._serve_navi_client(Client(frame), ('192.0.2.1', 1235))
  manager._serve_navi_client(Client(valid_frame(sequence=100)), ('192.0.2.1', 1236))
  assert not serv._update_carrot_navi(SubMaster())
  frame = valid_frame(session_id='019f0000-0000-7000-8000-000000000002', sequence=1)
  now[0] = 10.1
  manager._serve_navi_client(Client(frame), ('192.0.2.1', 1237))
  assert serv._update_carrot_navi(SubMaster())


def test_non_navigation_udp_does_not_steal_owner_or_erase_tcp_peer(bound):
  manager, serv, now = bound
  token = object()
  manager._set_navigation_peer('tcp', ('192.0.2.1', 1234), token)
  manager._set_navigation_peer('udp', ('192.0.2.2', 1234))
  manager._clear_navigation_peer('udp')
  assert manager.remote_addr == ('192.0.2.1', 1234)
  assert serv.accept_navigation_snapshot(parse_naver_navigation_v1(valid_frame(), 10.))
  serv.update({'carrotIndex': 17, 'latitude': 0, 'longitude': 0})
  assert serv._update_carrot_navi(SubMaster())
  assert serv.navigation_selection.snapshot.source is NavigationSource.NAVER_V1


def test_real_capnp_publication_keeps_guiding_only_apn_and_owner(bound, monkeypatch):
  manager, serv, now = bound
  frame = inactive_frame('idle')
  frame['lifecycle'] = 'guiding'
  manager._serve_navi_client(Client(frame), ('192.0.2.1', 1234))
  monkeypatch.setattr(serv, '_update_gps', lambda *_args: 0.)
  sent = {}
  pm = SimpleNamespace(send=lambda name, msg: sent.update({name: msg}))
  serv.update_navi('', SubMaster(), pm, 250., [], [], 250., 'gpsLocation')
  result = sent['carrotMan'].carrotMan
  assert result.activeCarrot >= 2
  assert result.naviOwner == 'naver_v1'
  assert result.naviLifecycle == 'guiding'
  assert result.naviSessionId == frame['sessionId']
  assert not result.decelProvider == 'naver_v1'  # No safety target in this frame.
  assert sent['carrotMan'].to_bytes()


def test_legacy_tmap_and_naver_switch_without_background_overwrite(bound):
  manager, serv, now = bound
  frame = {'rgdata': {'nRoadLimitSpeed': 60, 'nTBTTurnType': 13, 'nTBTDist': 90}}
  manager._dispatch_legacy_navi_frame(frame, ('192.0.2.2', 9), 10., 'legacy-one')
  assert serv._update_carrot_navi(SubMaster())
  assert serv.xTurnInfo == 2
  now[0] = 10.1
  manager._serve_navi_client(Client(valid_frame()), ('192.0.2.1', 1234))
  assert serv._update_carrot_navi(SubMaster()) and serv.xTurnInfo == 1
  now[0] = 10.2
  manager._dispatch_legacy_navi_frame(frame, ('192.0.2.2', 9), 10.2, 'legacy-one')
  assert serv._update_carrot_navi(SubMaster()) and serv.xTurnInfo == 1
  now[0] = 10.3
  manager._dispatch_legacy_navi_frame(frame, ('192.0.2.2', 9), 10.3, 'legacy-two')
  assert serv._update_carrot_navi(SubMaster()) and serv.xTurnInfo == 2


def test_stale_session_eof_does_not_mark_current_session_lost(bound):
  manager, serv, now = bound
  old = valid_frame()
  new = valid_frame(session_id='019f0000-0000-7000-8000-000000000002', sequence=1)
  serv.accept_navigation_snapshot(parse_naver_navigation_v1(old, 10.))
  now[0] = 10.1
  serv.accept_navigation_snapshot(parse_naver_navigation_v1(new, 10.1))
  assert not serv.navigation_sources.record_transport_loss(NavigationSource.NAVER_V1, old['sessionId'], 10.2)
  assert serv._update_carrot_navi(SubMaster())
  assert serv.navigation_selection.transport_loss_age_s is None
  assert serv.navigation_selection.snapshot.session_id == new['sessionId']


def test_duplicate_and_out_of_order_do_not_extend_runtime_lease(bound):
  manager, serv, now = bound
  manager._serve_navi_client(Client(valid_frame(sequence=10)), ('192.0.2.1', 1234))
  now[0] = 11.9
  manager._serve_navi_client(Client(valid_frame(sequence=10), valid_frame(sequence=9)), ('192.0.2.1', 1234))
  now[0] = 12.
  assert not serv._update_carrot_navi(SubMaster())


def test_discovery_response_does_not_activate_navigation(bound, monkeypatch):
  manager, serv, now = bound
  monkeypatch.setattr(manager, 'get_local_ip', lambda target: '192.168.1.2')
  sent = []
  sock = SimpleNamespace(sendto=lambda data, addr: sent.append((json.loads(data), addr)))
  request = {'type': 'carrot.navigation.discover', 'source': 'naver', 'schema_version': 1}
  assert manager._handle_navigation_udp(json.dumps(request).encode(), ('192.168.1.3', 7705), sock)
  assert sent[0][1] == ('192.168.1.3', 7705)
  assert not serv._update_carrot_navi(SubMaster())
  assert manager.remote_addr is None


def test_cached_position_does_not_restart_dead_reckoning_each_control_tick(bound):
  manager, serv, now = bound
  serv.update({'nRoadLimitSpeed': 60, 'vpPosPointLat': 37., 'vpPosPointLon': 127.,
               '_navigation_session_id': 'legacy'})
  assert serv._update_carrot_navi(SubMaster())
  assert serv.last_calculate_gps_time == 10.
  now[0] = 10.05
  assert serv._update_carrot_navi(SubMaster())
  assert serv.last_calculate_gps_time == 10.
  now[0] = 10.5
  serv.update({'nRoadLimitSpeed': 60, 'vpPosPointLat': 37.1, 'vpPosPointLon': 127.1,
               '_navigation_session_id': 'legacy'})
  serv._update_carrot_navi(SubMaster())
  assert serv.last_calculate_gps_time == 10.5


def test_same_frame_guidance_and_route_use_one_local_receipt(bound):
  manager, serv, now = bound
  now[0] = 10.001  # Time advances between receipt and controller acceptance.
  frame = {'rgdata': {'nRoadLimitSpeed': 60},
           'vrtx': [{'x': 127., 'y': 37.}, {'x': 127.1, 'y': 37.1}]}
  manager._dispatch_legacy_navi_frame(frame, ('192.0.2.2', 9), 10., 'legacy')
  assert serv._update_carrot_navi(SubMaster())
  assert serv.carrot_navi_control.route.polyline == ((37., 127.), (37.1, 127.1))


def test_cached_traffic_does_not_refresh_ui_receipt_timestamp(bound):
  manager, serv, now = bound
  frame = {'rgdata': {'nRoadLimitSpeed': 60},
           'sinf': {'redLightOn': True, 'redLightRemainTime': 30, 'distance': 100}}
  manager._dispatch_legacy_navi_frame(frame, ('192.0.2.2', 9), 10., 'legacy')
  assert serv._update_carrot_navi(SubMaster())
  assert json.loads(serv.params_memory.get('TrafficLight'))['ts'] == 10.
  now[0] = 10.05
  assert serv._update_carrot_navi(SubMaster())
  assert json.loads(serv.params_memory.get('TrafficLight'))['ts'] == 10.
