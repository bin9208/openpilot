from __future__ import annotations

import ast
from enum import IntEnum
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[3]
DT_MDL = 0.05
BLINKER_NONE = 0
BLINKER_LEFT = 1
BLINKER_RIGHT = 2
LANE_CHANGE_SPEED_MIN = 30 / 3.6
LANE_CHANGE_TIME_MAX = 10.0


class LaneChangeState(IntEnum):
  off = 0
  preLaneChange = 1
  laneChangeStarting = 2
  laneChangeFinishing = 3


class LaneChangeDirection(IntEnum):
  none = 0
  left = 1
  right = 2


class TurnDirection(IntEnum):
  none = 0
  turnLeft = 1
  turnRight = 2


class Desire(IntEnum):
  none = 0
  turnLeft = 1
  turnRight = 2
  keepLeft = 3
  keepRight = 4
  laneChangeLeft = 5
  laneChangeRight = 6


DESIRES = {
  LaneChangeDirection.none: {state: Desire.none for state in LaneChangeState},
  LaneChangeDirection.left: {
    LaneChangeState.off: Desire.none, LaneChangeState.preLaneChange: Desire.none,
    LaneChangeState.laneChangeStarting: Desire.laneChangeLeft,
    LaneChangeState.laneChangeFinishing: Desire.laneChangeLeft,
  },
  LaneChangeDirection.right: {
    LaneChangeState.off: Desire.none, LaneChangeState.preLaneChange: Desire.none,
    LaneChangeState.laneChangeStarting: Desire.laneChangeRight,
    LaneChangeState.laneChangeFinishing: Desire.laneChangeRight,
  },
}
TURN_DESIRES = {
  TurnDirection.none: Desire.none,
  TurnDirection.turnLeft: Desire.turnLeft,
  TurnDirection.turnRight: Desire.turnRight,
}


class ParamsFake:
  def get_int(self, _key: str) -> int:
    return 0

  def get_float(self, _key: str) -> float:
    return 0.0


class Counter:
  def __init__(self) -> None:
    self.counter = 0


class SideFake:
  def __init__(self, name: str) -> None:
    self.name = name
    self.lane_change_available = True
    self.edge_available = True
    self.lane_available = False
    self.lane_available_trigger = True
    self.lane_appeared = True
    self.side_object_detected = False
    self.bsd_hold_counter = 0
    self.lane_exist_count = Counter()
    self.lane_change_available_geom = True
    self.lane_line_info_edge_detect = False
    self.dist_to_edge_far = 3.0

  def commit_last(self) -> None:
    pass


class Messages(dict[str, SimpleNamespace]):
  def __init__(self, *, alive: dict[str, bool], **messages: SimpleNamespace) -> None:
    super().__init__(messages)
    self.alive = alive


def _tree(relative: str) -> ast.Module:
  path = ROOT / relative
  return ast.parse(path.read_text(encoding="utf-8", errors="replace"))


def _class(relative: str, name: str) -> ast.ClassDef:
  return next(node for node in _tree(relative).body if isinstance(node, ast.ClassDef) and node.name == name)


def _method(relative: str, class_name: str, name: str) -> ast.FunctionDef:
  class_node = _class(relative, class_name)
  return [node for node in class_node.body if isinstance(node, ast.FunctionDef) and node.name == name][-1]


def _runner(arguments: str, body: list[ast.stmt], result: str, namespace: dict[str, object]):
  function = ast.parse(f"def run({arguments}):\n  pass").body[0]
  assert isinstance(function, ast.FunctionDef)
  function.body = body + [ast.parse(f"return {result}").body[0]]
  module = ast.fix_missing_locations(ast.Module(body=[function], type_ignores=[]))
  exec(compile(module, "<production-slice>", "exec"), namespace)
  return namespace["run"]


def _stores(statement: ast.stmt, name: str) -> bool:
  return any(isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store) and node.id == name for node in ast.walk(statement))


def _mentions(statement: ast.stmt, name: str) -> bool:
  return any(
    (isinstance(node, ast.Name) and node.id == name) or
    (isinstance(node, ast.Attribute) and node.attr == name) or
    (isinstance(node, ast.Constant) and node.value == name)
    for node in ast.walk(statement)
  )


def load_carrot_functions_method():
  method = _method("selfdrive/carrot/carrot_functions.py", "CarrotPlanner", "_update_carrot_man")
  class_node = ast.ClassDef(name="CarrotPlanner", bases=[], keywords=[], decorator_list=[], body=[method])
  namespace = {
    "DT_MDL": DT_MDL,
    "EventName": SimpleNamespace(trafficSignChanged="trafficSignChanged"),
    "XState": SimpleNamespace(e2eStop=3, e2eStopped=4, e2eCruise=5),
  }
  exec(compile(ast.fix_missing_locations(ast.Module(body=[class_node], type_ignores=[])), "<carrot-functions>", "exec"), namespace)
  return namespace["CarrotPlanner"]._update_carrot_man


def load_desire_helper() -> type:
  class_node = _class("selfdrive/controls/lib/desire_helper.py", "DesireHelper")
  classifier = next(
    node for node in _tree("selfdrive/controls/lib/desire_lib/maneuver_classifier.py").body
    if isinstance(node, ast.FunctionDef) and node.name == "classify_maneuver_type"
  )
  namespace = {
    "Params": ParamsFake, "SideState": SideFake, "DT_MDL": DT_MDL,
    "BLINKER_NONE": BLINKER_NONE, "BLINKER_LEFT": BLINKER_LEFT, "BLINKER_RIGHT": BLINKER_RIGHT,
    "LANE_CHANGE_SPEED_MIN": LANE_CHANGE_SPEED_MIN, "LANE_CHANGE_TIME_MAX": LANE_CHANGE_TIME_MAX,
    "LaneChangeState": LaneChangeState, "LaneChangeDirection": LaneChangeDirection,
    "TurnDirection": TurnDirection, "DESIRES": DESIRES, "TURN_DESIRES": TURN_DESIRES,
    "log": SimpleNamespace(Desire=Desire), "CV": SimpleNamespace(MS_TO_KPH=3.6),
  }
  module = ast.fix_missing_locations(ast.Module(body=[classifier, class_node], type_ignores=[]))
  exec(compile(module, "<desire-helper>", "exec"), namespace)
  return namespace["DesireHelper"]


def controlsd_state_runner():
  body = _method("selfdrive/controls/controlsd.py", "Controls", "state_control").body
  start = next(i for i, statement in enumerate(body) if _stores(statement, "lat_plan"))
  end = next(i for i, statement in enumerate(body) if _stores(statement, "laneless_mode"))
  return _runner("self, CS, CC", body[start:end + 1], "self.lanefull_mode_enabled", {})


def controlsd_publish_runner():
  body = _method("selfdrive/controls/controlsd.py", "Controls", "publish").body
  end = next(i for i, statement in enumerate(body) if _mentions(statement, "atcDistance"))
  return _runner(
    "self, CC", body[:end + 1],
    "(desired_kph, setSpeed, CC.hudControl.activeCarrot, CC.hudControl.atcDistance)",
    {"CV": SimpleNamespace(KPH_TO_MS=1 / 3.6)},
  )


def cruise_input_runner():
  body = _method("selfdrive/car/cruise.py", "VCruiseCarrot", "update_v_cruise").body
  start = next(i for i, statement in enumerate(body) if _mentions(statement, "carControl"))
  end = next(i for i, statement in enumerate(body) if isinstance(statement, ast.If) and _mentions(statement, "carrotMan"))
  return _runner(
    "self, CS, sm", body[start:end + 1],
    "(self.nRoadLimitSpeed, self.desiredSpeed, self.carrot_cmd_index, self.carrot_cmd, self.carrot_arg)", {},
  )


def cruise_activation_runner():
  method = _method("selfdrive/car/cruise.py", "VCruiseCarrot", "_update_cruise_state")
  condition = next(
    node for node in ast.walk(method)
    if isinstance(node, ast.If) and _mentions(node.test, "desiredSpeed") and _mentions(node.test, "v_ego_kph_set")
  )
  return _runner("self", [condition], "None", {})


def carrot_candidate_runner():
  body = _method("selfdrive/carrot/carrot_serv.py", "CarrotServ", "update_navi").body
  start = next(i for i, statement in enumerate(body) if isinstance(statement, ast.If) and _mentions(statement, "autoTurnControl"))
  end = next(i for i, statement in enumerate(body) if _stores(statement, "desired_speed"))
  prelude = ast.parse(
    "navigation_control_authorized=self._navigation_control_authorized()\n"
    "atc_desired=250\natc_desired_next=250\nsdi_speed=250\nhda_active=False\nlimit_speed=250"
  ).body
  return _runner(
    "self, sm, vturn_speed, route_speed", prelude + body[start:end + 1],
    "(desired_speed, source, tuple(speed_n_sources))", {},
  )


def selfdrived_audio_runner():
  method = _method("selfdrive/selfdrived/selfdrived.py", "SelfdriveD", "update_events")
  statement = next(
    node for node in method.body
    if isinstance(node, ast.If) and _stores(node, "atc_type") and _mentions(node, "atc_type_last")
  )
  namespace = {"EventName": SimpleNamespace(audioLaneChange="audioLaneChange", audioTurn="audioTurn")}
  return _runner("self", [statement], "self.atc_type_last", namespace)
