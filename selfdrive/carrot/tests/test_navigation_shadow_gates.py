from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import pytest

from selfdrive.carrot.tests.navigation_fail_neutral_harness import carrot_serv


BLINKER_NONE = 0
BLINKER_LEFT = 1
BLINKER_RIGHT = 2


def _load_blinker_manager() -> type:
  source_path = Path(__file__).resolve().parents[2] / "controls/lib/desire_lib/blinker_manager.py"
  tree = ast.parse(source_path.read_text(encoding="utf-8", errors="replace"))
  nodes = [
    node for node in tree.body
    if isinstance(node, ast.ClassDef) and node.name in ("BlinkerOutput", "BlinkerManager")
  ]
  namespace = {
    "dataclass": dataclass,
    "BLINKER_NONE": BLINKER_NONE,
    "BLINKER_LEFT": BLINKER_LEFT,
    "BLINKER_RIGHT": BLINKER_RIGHT,
  }
  exec(compile(ast.Module(body=nodes, type_ignores=[]), str(source_path), "exec"), namespace)
  return namespace["BlinkerManager"]


BlinkerManager = _load_blinker_manager()


def test_legacy_tmap_control_output_is_characterized() -> None:
  # Given: an accepted, control-authorized legacy Tmap navigation session.
  serv = carrot_serv()
  serv.set_navigation_observability("tmap", True, 123, True)
  message = SimpleNamespace()

  # When: the real CarrotServ message filler publishes current control values.
  serv._fill_navigation_message(message, 41, "atc")

  # Then: the legacy Tmap control contract remains unchanged.
  assert (
    message.provider, message.naviValid, message.naviAgeMs, message.naviControlAllowed,
    message.activeCarrot, message.desiredSpeed, message.desiredSource, message.atcType,
    message.xSpdType, message.xSpdLimit, message.xSpdDist, message.xTurnInfo,
    message.advisoryTurnInfo, message.advisoryTurnDistance,
  ) == ("tmap", True, 123, True, 6, 41, "atc", "turn left", 22, 40, 100, 1, 1, 120)


def test_no_navigation_control_output_is_characterized() -> None:
  # Given: no valid navigation provider and poisoned internal navigation controls.
  serv = carrot_serv()
  serv.set_navigation_observability("none", False, 999, False)
  message = SimpleNamespace()

  # When: the real CarrotServ message filler publishes the state.
  serv._fill_navigation_message(message, 0, "route")

  # Then: the public control surface is exactly neutral.
  assert (
    message.provider, message.naviValid, message.naviAgeMs, message.naviControlAllowed,
    message.activeCarrot, message.desiredSpeed, message.desiredSource, message.atcType,
    message.xSpdType, message.xSpdLimit, message.xSpdDist, message.xTurnInfo,
    message.advisoryTurnInfo, message.advisoryTurnDistance,
  ) == ("none", False, 0, False, 0, 250, "none", "none", -1, 0, 0, -1, -1, 0)


def test_physical_driver_blinker_baseline_is_characterized() -> None:
  # Given: no navigation request and a physical left blinker.
  manager = BlinkerManager()
  carstate = SimpleNamespace(leftBlinker=True, rightBlinker=False)
  carrot_man = SimpleNamespace(
    atcType="none", carrotCmdIndex=0, carrotCmd="", carrotArg="",
    naviValid=False, naviControlAllowed=False,
  )

  # When: the real BlinkerManager combines driver and navigation inputs.
  output = manager.run(carstate, carrot_man, laneChangeNeedTorque=0)

  # Then: physical driver intent remains enabled without navigation.
  assert (
    output.driver_blinker_state, output.driver_desire_enabled,
    output.atc_blinker_state, output.atc_desire_enabled,
    output.blinker_state, output.desire_enabled,
  ) == (BLINKER_LEFT, True, BLINKER_NONE, False, BLINKER_LEFT, True)


def test_naver_shadow_message_keeps_display_but_neutralizes_audio_countdown() -> None:
  # Given: valid Naver display data with deliberately poisoned control fields.
  serv = carrot_serv()
  serv.set_navigation_observability("naver", True, 7, False)
  message = SimpleNamespace(leftSec=0, szTBTMainText="Extreme left", naviPaths="127.0,37.0,0.0")

  # When: the real CarrotServ message filler applies the provider gate.
  serv._fill_navigation_message(message, 0, "route")

  # Then: display data remains while every control/audio field is neutral.
  assert (
    message.provider, message.naviValid, message.naviAgeMs, message.naviControlAllowed,
    message.desiredSpeed, message.desiredSource, message.atcType, message.activeCarrot,
    message.xSpdType, message.xTurnInfo, message.leftSec,
    message.advisoryTurnInfo, message.advisoryTurnDistance,
    message.szTBTMainText, message.naviPaths,
  ) == (
    "naver", True, 7, False, 250, "none", "none", 0, -1, -1, 100, 1, 120,
    "Extreme left", "127.0,37.0,0.0",
  )


@pytest.mark.parametrize(
  ("atc_type", "command"),
  [("turn left", ""), ("fork left", "LANECHANGE")],
)
def test_blinker_manager_ignores_naver_shadow_navigation_requests(atc_type: str, command: str) -> None:
  # Given: no physical blinker and a poisoned Naver ATC or lane-change request.
  manager = BlinkerManager()
  carstate = SimpleNamespace(leftBlinker=False, rightBlinker=False)
  carrot_man = SimpleNamespace(
    atcType=atc_type, carrotCmdIndex=9, carrotCmd=command, carrotArg="LEFT",
    naviValid=True, naviControlAllowed=False, DT_MDL=0.05,
  )

  # When: the real BlinkerManager combines the inputs.
  output = manager.run(carstate, carrot_man, laneChangeNeedTorque=0)

  # Then: shadow navigation is indistinguishable from no navigation.
  assert (
    output.atc_blinker_state, output.atc_desire_enabled,
    output.blinker_state, output.desire_enabled,
  ) == (BLINKER_NONE, False, BLINKER_NONE, False)
