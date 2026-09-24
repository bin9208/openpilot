from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
import sys

from tools.naver_map_patch.inspect_tools import InspectionError, discover_tools
from tools.naver_map_patch.inspection import AndroidInspectionBackend, InspectionRequest, inspect_packages
from tools.naver_map_patch.profile import ProfileError, load_profile


@dataclass(frozen=True, slots=True)
class InspectArguments:
  base: Path
  splits_from: Path
  profile: str


class InspectUsageError(RuntimeError):
  pass


def run_inspect_cli(
  arguments: Sequence[str],
  repo_root: Path,
  environ: Mapping[str, str] | None = None,
) -> int:
  try:
    selected = _parse_arguments(arguments)
    profile = load_profile(selected.profile)
    if not selected.base.is_file():
      raise InspectionError("base_missing", "base input is not a regular file")
    if not (selected.splits_from.is_file() or selected.splits_from.is_dir()):
      raise InspectionError("split_source", "companion split source does not exist")
    tools = discover_tools(repo_root, environ)
    backend = AndroidInspectionBackend(tools, repo_root / "tools" / "naver_map_patch" / "dexpatch")
    report = inspect_packages(InspectionRequest(selected.base, selected.splits_from, profile), backend)
  except InspectUsageError as error:
    print(f"inspect usage error: {error}", file=sys.stderr)
    return 64
  except ProfileError as error:
    print(f"inspect failed [profile]: {error}", file=sys.stderr)
    return 2
  except InspectionError as error:
    print(f"inspect failed [{error.code}]: {error.message}", file=sys.stderr)
    return 2
  print(report.to_json())
  return 0


def _parse_arguments(arguments: Sequence[str]) -> InspectArguments:
  tokens = tuple(arguments)
  if not tokens or tokens[0] != "inspect":
    raise InspectUsageError("command must start with inspect")
  values: dict[str, str] = {}
  json_requested = False
  index = 1
  while index < len(tokens):
    token = tokens[index]
    if token == "--json":
      if json_requested:
        raise InspectUsageError("--json may be specified only once")
      json_requested = True
      index += 1
      continue
    if token not in ("--base", "--splits-from", "--profile"):
      raise InspectUsageError(f"unsupported argument: {token}")
    if token in values or index + 1 >= len(tokens) or tokens[index + 1].startswith("--"):
      raise InspectUsageError(f"{token} requires exactly one value")
    values[token] = tokens[index + 1]
    index += 2
  missing = tuple(name for name in ("--base", "--splits-from", "--profile") if name not in values)
  if missing or not json_requested:
    required = ", ".join((*missing, *(("--json",) if not json_requested else ())))
    raise InspectUsageError(f"missing required argument(s): {required}")
  return InspectArguments(Path(values["--base"]), Path(values["--splits-from"]), values["--profile"])
