from dataclasses import replace
import importlib
from pathlib import Path
from types import SimpleNamespace
import sys

import pytest


CLUSTER_DIR = Path(__file__).resolve().parents[1] / "cluster"
sys.path.insert(0, str(CLUSTER_DIR))

from cluster_live import (
  OpenpilotLiveSource,
  deceleration_source_display_label,
  navigation_source_diagnostic,
  standby_state,
)


@pytest.mark.parametrize(
  ("source", "expected"),
  (
    ("cam", "cam:n"),
    ("hda", "cam:v"),
    ("route", "route:v"),
    ("vturn", "turn:c"),
    ("model", "turn:c"),
    ("atc", "turn:n"),
    ("section", "section:n"),
    ("longsource:c", "longsource:c"),
    ("custom-source", "custom-s"),
    (None, "apply"),
  ),
)
def test_deceleration_source_display_label(source, expected) -> None:
  assert deceleration_source_display_label(source) == expected


@pytest.mark.parametrize(
  ("desired_source", "expected_label"),
  (
    ("cam", "cam:n"),
    ("hda", "cam:v"),
    ("route", "route:v"),
    ("model", "turn:c"),
  ),
)
def test_live_deceleration_override_adds_source_origin(desired_source, expected_label) -> None:
  source = object.__new__(OpenpilotLiveSource)
  source._max_lateral_accel = 3.0
  source._energy_gauge_label = "fuel"
  source._carrot_navi_media = None
  source._current_carrot_navi = lambda _now: None
  carrot_man = SimpleNamespace(activeCarrot=3, desiredSpeed=55.0, desiredSource=desired_source)
  source._service_data = lambda service: carrot_man if service == "carrotMan" else None
  source._service_alive = lambda _service: False
  state = replace(standby_state(), cruise_kph=100, cruise_display_state="engaged")

  decorated = source._with_live_hud_state(state)

  assert decorated.cruise_override_kph == 55.0
  assert decorated.cruise_override_label == expected_label
  assert decorated.cruise_override_color_mode == 2


@pytest.mark.parametrize(
  ("owner", "provider", "reason", "expected"),
  (
    ("naver_v1", "hda", "cam", ("naver", "hda", "cam")),
    ("tmap_legacy", "tmap_legacy", "section", ("tmap", "tmap", "section")),
    ("", "hda", "hda", ("none", "hda", "hda")),
  ),
)
def test_navigation_owner_provider_and_reason_are_separate(owner, provider, reason, expected) -> None:
  carrot_man = SimpleNamespace(naviOwner=owner, decelProvider=provider, decelReason=reason)

  assert navigation_source_diagnostic(carrot_man) == expected


@pytest.mark.parametrize(
  ("owner", "provider", "reason", "expected"),
  (
    ("naver_v1", "naver_v1", "cam", "naver>naver:cam"),
    ("carrot_navi_v2", "carrot_navi_v2", "bump", "v2>v2:bump"),
  ),
)
def test_live_deceleration_override_shows_owner_provider_and_reason(
  owner, provider, reason, expected
) -> None:
  source = object.__new__(OpenpilotLiveSource)
  source._max_lateral_accel = 3.0
  source._energy_gauge_label = "fuel"
  source._carrot_navi_media = None
  source._current_carrot_navi = lambda _now: None
  carrot_man = SimpleNamespace(
    activeCarrot=2,
    desiredSpeed=55.0,
    desiredSource="route",
    naviOwner=owner,
    decelProvider=provider,
    decelReason=reason,
  )
  source._service_data = lambda service: carrot_man if service == "carrotMan" else None
  source._service_alive = lambda _service: False
  state = replace(standby_state(), cruise_kph=100, cruise_display_state="engaged")

  decorated = source._with_live_hud_state(state)

  assert decorated.cruise_override_label == expected


def test_old_log_without_provider_fields_keeps_generic_source_fallback() -> None:
  source = object.__new__(OpenpilotLiveSource)
  source._max_lateral_accel = 3.0
  source._energy_gauge_label = "fuel"
  source._carrot_navi_media = None
  source._current_carrot_navi = lambda _now: None
  carrot_man = SimpleNamespace(activeCarrot=3, desiredSpeed=55.0, desiredSource="cam")
  source._service_data = lambda service: carrot_man if service == "carrotMan" else None
  source._service_alive = lambda _service: False
  state = replace(standby_state(), cruise_kph=100, cruise_display_state="engaged")

  decorated = source._with_live_hud_state(state)

  assert decorated.cruise_override_label == "cam:n"


def _carrot_serv_module():
  return importlib.import_module("openpilot.selfdrive.carrot.carrot_serv")


def _diagnostic_selection(*, owner_age_s=1.25, safety_age_s=0.375, source="naver_v1"):
  return SimpleNamespace(
    snapshot=SimpleNamespace(
      source=SimpleNamespace(value=source),
      session_id="naver-session",
      sequence=42,
      lifecycle=SimpleNamespace(value="guiding"),
      control=SimpleNamespace(
        speed_present=False,
        current=SimpleNamespace(present=False),
        next=SimpleNamespace(present=False),
      ),
    ),
    owner_age_s=owner_age_s,
    safety_age_s=safety_age_s,
  )


def test_carrot_serv_update_navi_publishes_diagnostics_without_changing_desired_source(monkeypatch) -> None:
  carrot_serv = _carrot_serv_module()

  class HarnessCarrotServ(carrot_serv.CarrotServ):
    def __getattr__(self, _name):
      return 0

  serv = object.__new__(HarnessCarrotServ)
  serv.navigation_selection = _diagnostic_selection()
  decision = SimpleNamespace(
    provider="naver_v1",
    reason="cam",
    type=1,
    distance_m=100.0,
    limit_kph=50.0,
  )
  serv.update_params = lambda: None
  serv._update_gps = lambda *_args: 0.0
  serv.update_nav_instruction = lambda *_args: None
  serv.calculate_current_speed = lambda *_args: 50.0
  serv.update_auto_turn = lambda *_args: (250.0, "none", 0.0, 0.0)
  serv._select_final_speed = lambda _items: (55.0, "route")
  serv._update_cmd = lambda: None
  serv._get_sdi_descr = lambda _kind: ""

  def apply_navigation_safety(*_args):
    serv.last_safety_decision = decision
    serv.last_safety_rejection = None
    return decision

  serv._apply_navigation_safety = apply_navigation_safety

  def new_message(service):
    return SimpleNamespace(valid=False, **{service: SimpleNamespace()})

  monkeypatch.setattr(carrot_serv.messaging, "new_message", new_message)
  sent = {}
  pm = SimpleNamespace(send=lambda service, message: sent.__setitem__(service, message))

  class FakeSubMaster:
    alive = {"carState": False, "selfdriveState": False, "navInstruction": False}
    valid = {"navInstruction": False}

    def __getitem__(self, service):
      if service == "modelV2":
        return SimpleNamespace(meta=SimpleNamespace(modelTurnSpeed=250.0))
      return SimpleNamespace()

  sm = FakeSubMaster()

  carrot_serv.CarrotServ.update_navi(
    serv,
    "127.0.0.1",
    sm,
    pm,
    250.0,
    (),
    (),
    250.0,
    "gpsLocationExternal",
    navigation_prepared=True,
  )

  target = sent["carrotMan"].carrotMan
  assert target.desiredSource == "route"
  expected_diagnostics = {
    "naviOwner": "naver_v1",
    "naviSessionId": "naver-session",
    "naviSequence": 42,
    "naviOwnerAgeMs": 1250,
    "naviSafetyAgeMs": 375,
    "naviLifecycle": "guiding",
    "naviControlAllowed": True,
    "naviSafetyRejection": "",
    "decelProvider": "naver_v1",
    "decelReason": "cam",
  }
  for name, expected in expected_diagnostics.items():
    assert getattr(target, name) == expected


def test_service_diagnostic_missing_ages_and_clamp_boundaries() -> None:
  publish = _carrot_serv_module().publish_navigation_diagnostics
  target = SimpleNamespace()
  publish(
    target,
    _diagnostic_selection(owner_age_s=None, safety_age_s=3_000_000.0),
    None,
    "safety_absent",
  )

  assert target.naviOwnerAgeMs == -1
  assert target.naviSafetyAgeMs == 2_147_483_647
  assert target.naviControlAllowed is False
  assert target.decelProvider == ""
  assert target.decelReason == ""


def test_service_accepts_selected_navigation_safety_for_control() -> None:
  publish = _carrot_serv_module().publish_navigation_diagnostics
  target = SimpleNamespace()
  decision = SimpleNamespace(provider="naver_v1", reason="cam")

  publish(target, _diagnostic_selection(), decision, None)

  assert target.naviControlAllowed is True
  assert target.naviSafetyRejection == ""
  assert (target.decelProvider, target.decelReason) == ("naver_v1", "cam")


def test_service_diagnostic_tokens_are_bounded_and_never_echo_location_payload() -> None:
  publish = _carrot_serv_module().publish_navigation_diagnostics
  target = SimpleNamespace()
  private_payload = "37.123456,127.654321 Secret Road " * 20
  decision = SimpleNamespace(provider=private_payload, reason=private_payload)

  publish(target, _diagnostic_selection(), decision, private_payload)

  for value in (target.decelProvider, target.decelReason, target.naviSafetyRejection):
    assert len(value) <= 64
    assert "37.123456" not in value
    assert "Secret Road" not in value
