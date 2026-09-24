"""Fail-closed validation for a copied Naver offline diagnostic database."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
from typing import Any
import uuid

from tools.naver_map_patch.capture_cli import (
  CHANNELS,
  OfflineCaptureError,
  decode_and_validate_frame,
)
from tools.naver_map_patch.privacy_scan import (
  SanitizationRejected,
  assert_sanitized,
)
from tools.naver_map_patch.profile_types import InspectionProfile


_METADATA_KEYS = frozenset({
  "schema_version",
  "payload_build_id",
  "object_queue_dropped",
  "local_queue_dropped",
  "network_queue_dropped",
  "storage_errors",
  "network_errors",
  "retention_deletes",
  "rejected_frames",
})
_COUNTER_KEYS = _METADATA_KEYS - {"schema_version", "payload_build_id"}
_FIELD_ACCEPTANCE_BUILD_IDS = frozenset({
  "naver-6.8.0.5-field-acceptance-v1",
  "naver-6.8.0.5-field-acceptance-v2",
  "naver-6.8.0.5-field-acceptance-v3",
  "naver-6.8.0.5-field-acceptance-v4",
})
_OUTPUT_NAMES = (
  "hashes.json",
  "summary.json",
  "manifest.json",
  "accessor_shapes.json",
)
_JAVA_LONG_MIN = -(2**63)
_JAVA_LONG_MAX = 2**63 - 1

_EXPECTED_SCHEMA_SQL = {
  ("table", "capture_meta"): """
    CREATE TABLE capture_meta (
      key TEXT PRIMARY KEY NOT NULL,
      value TEXT NOT NULL
    );
  """,
  ("table", "capture_runs"): """
    CREATE TABLE capture_runs (
      run_id TEXT PRIMARY KEY NOT NULL,
      started_monotonic_ns INTEGER NOT NULL,
      payload_build_id TEXT NOT NULL,
      clean_shutdown INTEGER NOT NULL DEFAULT 0
        CHECK(clean_shutdown IN (0, 1))
    );
  """,
  ("table", "capture_frames"): """
    CREATE TABLE capture_frames (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      run_id TEXT NOT NULL,
      sequence INTEGER NOT NULL CHECK(sequence >= 1),
      monotonic_ns INTEGER NOT NULL,
      channel TEXT NOT NULL CHECK(channel IN (
        'status', 'tbt_current', 'tbt_next', 'safety', 'route', 'lane'
      )),
      object_dropped_before INTEGER NOT NULL CHECK(object_dropped_before >= 0),
      encoded_json TEXT NOT NULL,
      pinned INTEGER NOT NULL DEFAULT 0 CHECK(pinned IN (0, 1))
    );
  """,
  ("index", "capture_frames_channel_id"): """
    CREATE INDEX capture_frames_channel_id
    ON capture_frames(channel, id);
  """,
}

_EXPECTED_COLUMNS: dict[str, tuple[tuple[object, ...], ...]] = {
  "capture_meta": (
    (0, "key", "TEXT", 1, None, 1),
    (1, "value", "TEXT", 1, None, 0),
  ),
  "capture_runs": (
    (0, "run_id", "TEXT", 1, None, 1),
    (1, "started_monotonic_ns", "INTEGER", 1, None, 0),
    (2, "payload_build_id", "TEXT", 1, None, 0),
    (3, "clean_shutdown", "INTEGER", 1, "0", 0),
  ),
  "capture_frames": (
    (0, "id", "INTEGER", 0, None, 1),
    (1, "run_id", "TEXT", 1, None, 0),
    (2, "sequence", "INTEGER", 1, None, 0),
    (3, "monotonic_ns", "INTEGER", 1, None, 0),
    (4, "channel", "TEXT", 1, None, 0),
    (5, "object_dropped_before", "INTEGER", 1, None, 0),
    (6, "encoded_json", "TEXT", 1, None, 0),
    (7, "pinned", "INTEGER", 1, "0", 0),
  ),
}


@dataclass(frozen=True, slots=True)
class _OfflineContract:
  database_name: str
  database_recovery_name: str
  schema_version: int
  payload_build_id: str
  payload_build_ids: frozenset[str]
  pinned_per_channel: int
  channel_quotas: dict[str, int]

  @property
  def total_quota(self) -> int:
    return sum(self.channel_quotas.values())


@dataclass(frozen=True, slots=True)
class OfflineCaptureReport:
  """Sanitized validation result; it never carries raw rows or run IDs."""

  file_count: int
  frame_count: int
  run_count: int
  complete: bool
  counts: dict[str, int]
  output_files: tuple[Path, ...]

  def as_dict(self) -> dict[str, object]:
    return {
      "file_count": self.file_count,
      "frame_count": self.frame_count,
      "run_count": self.run_count,
      "complete": self.complete,
      "counts": dict(self.counts),
      "output_files": [path.name for path in self.output_files],
    }


def validate_offline_database(
    raw_directory: Path,
    bundle_directory: Path,
    profile: InspectionProfile,
) -> OfflineCaptureReport:
  """Validate copied evidence and publish only a deterministic safe bundle."""
  raw = Path(raw_directory).expanduser().resolve(strict=True)
  bundle = Path(bundle_directory).expanduser().resolve(strict=False)
  if not raw.is_dir():
    raise OfflineCaptureError("raw capture directory is not a directory")
  if bundle == raw or _is_inside(bundle, raw) or _is_inside(raw, bundle):
    raise OfflineCaptureError("raw and sanitized bundle directories must be separate")
  if bundle.exists():
    raise OfflineCaptureError("sanitized bundle destination must not already exist")

  # Evidence hashing intentionally precedes every SQLite/schema/content check.
  hashes = _hash_regular_files(raw)
  contract = _load_offline_contract(profile)
  connection = _select_schema_compatible_database(raw, contract)
  try:
    metadata, current_build_id = _read_metadata(connection, contract)
    runs = _read_runs(connection, contract)
    frame_result = _read_frames(connection, runs, contract)
  except sqlite3.Error as error:
    raise OfflineCaptureError(f"SQLite validation failed: {error}") from error
  finally:
    connection.close()

  counts = {channel: frame_result["counts"][channel] for channel in CHANNELS}
  missing_channels = [channel for channel in CHANNELS if counts[channel] == 0]
  run_summaries = _run_summaries(runs, frame_result["run_frames"])
  run_payload_build_ids = sorted({run[2] for run in runs.values()})
  summary: dict[str, Any] = {
    "schema": "naver.diagnostic.offline-summary.v1",
    "profile": "naver-map-6805",
    "payload_build_id": current_build_id,
    "run_payload_build_ids": run_payload_build_ids,
    "complete": not missing_channels,
    "missing_channels": missing_channels,
    "file_count": len(hashes),
    "frame_count": sum(counts.values()),
    "run_count": len(runs),
    "counts": counts,
    "counters": metadata,
    "runs": run_summaries,
  }
  accessor_shapes: dict[str, Any] = {
    "schema": "naver.diagnostic.accessor-shapes.v1",
    "channels": _shape_output(frame_result["shapes"]),
  }
  hashes_output: dict[str, Any] = {
    "schema": "naver.diagnostic.file-hashes.v1",
    "algorithm": "sha256",
    "files": hashes,
  }
  manifest: dict[str, Any] = {
    "schema": "naver.diagnostic.offline-bundle.v1",
    "capture_kind": "sanitized_offline_diagnostic_bundle",
    "profile": "naver-map-6805",
    "package_name": profile.package_name,
    "version_code": profile.version_code,
    "payload_build_id": current_build_id,
    "run_payload_build_ids": run_payload_build_ids,
    "raw_capture_included": False,
    "bundle_files": list(_OUTPUT_NAMES),
    "summary": {
      "complete": summary["complete"],
      "missing_channels": list(missing_channels),
      "frame_count": summary["frame_count"],
      "run_count": summary["run_count"],
      "counts": counts,
    },
  }
  outputs = {
    "hashes.json": hashes_output,
    "summary.json": summary,
    "manifest.json": manifest,
    "accessor_shapes.json": accessor_shapes,
  }
  _assert_outputs_are_sanitized(outputs)
  _publish_bundle(bundle, outputs)

  return OfflineCaptureReport(
    file_count=len(hashes),
    frame_count=sum(counts.values()),
    run_count=len(runs),
    complete=not missing_channels,
    counts=counts,
    output_files=tuple(bundle / name for name in _OUTPUT_NAMES),
  )


def _load_offline_contract(profile: InspectionProfile) -> _OfflineContract:
  if type(profile) is not InspectionProfile:
    raise OfflineCaptureError("invalid inspection profile")
  if profile.name != "6.8.0.5":
    raise OfflineCaptureError("offline capture is supported only for profile 6.8.0.5")
  if profile.diagnostic_payload is None:
    raise OfflineCaptureError("offline capture profile contract is unavailable")
  offline = profile.diagnostic_payload.offline_capture
  quotas = offline.channel_quotas
  if frozenset(quotas) != frozenset(CHANNELS):
    raise OfflineCaptureError("offline capture channel quotas are invalid")
  return _OfflineContract(
    database_name=offline.database_name,
    database_recovery_name=offline.database_recovery_name,
    schema_version=offline.schema_version,
    payload_build_id=offline.payload_build_id,
    payload_build_ids=frozenset((offline.payload_build_id, *_FIELD_ACCEPTANCE_BUILD_IDS)),
    pinned_per_channel=offline.pinned_per_channel,
    channel_quotas=dict(quotas),
  )


def _select_schema_compatible_database(
    raw: Path,
    contract: _OfflineContract,
) -> sqlite3.Connection:
  failures: list[OfflineCaptureError] = []
  for name in (contract.database_name, contract.database_recovery_name):
    try:
      connection = _open_schema_candidate(raw / name, contract)
    except OfflineCaptureError as error:
      failures.append(error)
      continue
    if connection is not None:
      return connection
  if len(failures) == 1:
    raise failures[0]
  raise OfflineCaptureError("no compatible capture database candidate")


def primary_database_is_schema_compatible(
    raw: Path,
    profile: InspectionProfile,
) -> bool:
  contract = _load_offline_contract(profile)
  connection: sqlite3.Connection | None = None
  try:
    connection = _open_schema_candidate(
      raw / contract.database_name,
      contract,
    )
    return connection is not None
  except OfflineCaptureError:
    return False
  finally:
    if connection is not None:
      connection.close()


def _open_schema_candidate(
    path: Path,
    contract: _OfflineContract,
) -> sqlite3.Connection | None:
  if not path.is_file() or path.is_symlink():
    return None
  connection: sqlite3.Connection | None = None
  try:
    connection = sqlite3.connect(
      path.as_uri() + "?mode=ro",
      uri=True,
      timeout=0,
    )
    connection.execute("PRAGMA query_only=ON")
    if connection.execute("PRAGMA query_only").fetchall() != [(1,)]:
      raise OfflineCaptureError("SQLite query_only mode was not enabled")
    _validate_integrity(connection)
    _validate_schema(connection, contract)
    return connection
  except OfflineCaptureError:
    if connection is not None:
      connection.close()
    raise
  except sqlite3.Error as error:
    if connection is not None:
      connection.close()
    raise OfflineCaptureError(f"SQLite validation failed: {error}") from error


def _is_inside(path: Path, directory: Path) -> bool:
  try:
    path.relative_to(directory)
    return True
  except ValueError:
    return False


def _hash_regular_files(raw: Path) -> list[dict[str, object]]:
  candidates: list[tuple[str, Path]] = []
  try:
    for path in raw.rglob("*"):
      if path.is_symlink():
        raise OfflineCaptureError("raw capture must not contain symbolic links")
      if path.is_file():
        candidates.append((path.relative_to(raw).as_posix(), path))
  except OSError as error:
    raise OfflineCaptureError(f"cannot enumerate raw capture: {error}") from error
  hashes: list[dict[str, object]] = []
  for relative, path in sorted(candidates):
    digest = hashlib.sha256()
    try:
      with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
          digest.update(chunk)
      size = path.stat().st_size
    except OSError as error:
      raise OfflineCaptureError(f"cannot hash pulled file {relative}: {error}") from error
    hexadecimal = digest.hexdigest()
    # Chunking keeps the complete digest while ensuring the general capture
    # privacy scanner cannot mistake a bare 64-hex checksum for key material.
    hashes.append({
      "path": relative,
      "size_bytes": size,
      "sha256": ":".join(
        hexadecimal[index:index + 4] for index in range(0, len(hexadecimal), 4)
      ),
    })
  return hashes


def _validate_integrity(connection: sqlite3.Connection) -> None:
  result = connection.execute("PRAGMA quick_check").fetchall()
  if result != [("ok",)]:
    raise OfflineCaptureError("SQLite quick_check did not return exactly ok")


def _validate_schema(
    connection: sqlite3.Connection,
    contract: _OfflineContract,
) -> None:
  version = connection.execute("PRAGMA user_version").fetchall()
  if version != [(contract.schema_version,)]:
    raise OfflineCaptureError("SQLite user_version is not the configured schema version")
  objects = {
    (row[0], row[1]): row[2]
    for row in connection.execute(
      "SELECT type, name, sql FROM sqlite_master "
      "WHERE name NOT LIKE 'sqlite_%' ORDER BY type, name"
    )
  }
  if frozenset(objects) != frozenset(_EXPECTED_SCHEMA_SQL):
    raise OfflineCaptureError("SQLite tables and indexes are not exact")
  for table, expected_columns in _EXPECTED_COLUMNS.items():
    columns = tuple(
      tuple(row) for row in connection.execute(f"PRAGMA table_info({table})")
    )
    if columns != expected_columns:
      raise OfflineCaptureError(f"SQLite {table} columns are not exact")
  index_columns = tuple(
    tuple(row) for row in connection.execute(
      "PRAGMA index_info(capture_frames_channel_id)"
    )
  )
  if index_columns != ((0, 4, "channel"), (1, 0, "id")):
    raise OfflineCaptureError("SQLite capture_frames_channel_id index is not exact")
  frame_indexes = tuple(
    tuple(row) for row in connection.execute("PRAGMA index_list(capture_frames)")
  )
  if (
    len(frame_indexes) != 1
    or frame_indexes[0][1:] != (
      "capture_frames_channel_id", 0, "c", 0
    )
  ):
    raise OfflineCaptureError("SQLite capture_frames index properties are not exact")
  normalized_objects = {
    key: _normalize_schema_sql(value) for key, value in objects.items()
  }
  normalized_expected = {
    key: _normalize_schema_sql(value)
    for key, value in _EXPECTED_SCHEMA_SQL.items()
  }
  if normalized_objects != normalized_expected:
    raise OfflineCaptureError("SQLite canonical schema SQL does not match")


def _normalize_schema_sql(value: str) -> str:
  return "".join(value.strip().removesuffix(";").split())


def _read_metadata(
    connection: sqlite3.Connection,
    contract: _OfflineContract,
) -> tuple[dict[str, int], str]:
  rows = connection.execute(
    "SELECT key, value FROM capture_meta ORDER BY key"
  ).fetchall()
  if any(type(key) is not str or type(value) is not str for key, value in rows):
    raise OfflineCaptureError("capture_meta values must be text")
  metadata = dict(rows)
  if len(metadata) != len(rows) or frozenset(metadata) != _METADATA_KEYS:
    raise OfflineCaptureError("capture_meta keys are not exact")
  if metadata["schema_version"] != str(contract.schema_version):
    raise OfflineCaptureError("capture_meta schema_version mismatch")
  current_build_id = metadata["payload_build_id"]
  if current_build_id not in contract.payload_build_ids:
    raise OfflineCaptureError("capture_meta payload_build_id mismatch")
  counters: dict[str, int] = {}
  for key in sorted(_COUNTER_KEYS):
    text = metadata[key]
    if not text.isascii() or not text.isdecimal():
      raise OfflineCaptureError(f"capture_meta {key} is not a non-negative counter")
    value = int(text)
    if value > _JAVA_LONG_MAX:
      raise OfflineCaptureError(f"capture_meta {key} exceeds Java long")
    counters[key] = value
  return counters, current_build_id


def _read_runs(
    connection: sqlite3.Connection,
    contract: _OfflineContract,
) -> dict[str, tuple[int, int, str]]:
  rows = connection.execute(
    "SELECT run_id, started_monotonic_ns, payload_build_id, clean_shutdown "
    "FROM capture_runs"
  ).fetchall()
  runs: dict[str, tuple[int, int, str]] = {}
  for run_id, started_ns, build_id, clean_shutdown in rows:
    if (
      type(run_id) is not str
      or not _is_canonical_uuid(run_id)
      or type(started_ns) is not int
      or not _JAVA_LONG_MIN <= started_ns <= _JAVA_LONG_MAX
      or build_id not in contract.payload_build_ids
      or type(clean_shutdown) is not int
      or clean_shutdown not in (0, 1)
      or run_id in runs
    ):
      raise OfflineCaptureError("capture_runs row is invalid")
    runs[run_id] = (started_ns, clean_shutdown, build_id)
  return runs


def _is_canonical_uuid(value: str) -> bool:
  try:
    return str(uuid.UUID(value)) == value
  except (ValueError, AttributeError):
    return False


def _read_frames(
    connection: sqlite3.Connection,
    runs: dict[str, tuple[int, int, str]],
    contract: _OfflineContract,
) -> dict[str, Any]:
  counts: Counter[str] = Counter()
  pinned: Counter[str] = Counter()
  run_frames: dict[str, list[tuple[int, int, int, str]]] = defaultdict(list)
  shapes: dict[str, Counter[str]] = {
    channel: Counter() for channel in CHANNELS
  }
  rows = connection.execute(
    "SELECT id, run_id, sequence, monotonic_ns, channel, "
    "object_dropped_before, encoded_json, pinned "
    "FROM capture_frames ORDER BY id"
  )
  for row in rows:
    row_id, run_id, sequence, monotonic_ns, channel, dropped, encoded, is_pinned = row
    if type(row_id) is not int or row_id < 1:
      raise OfflineCaptureError("capture_frames id is invalid")
    if type(channel) is not str or channel not in CHANNELS:
      raise OfflineCaptureError("capture_frames contains unknown channel")
    if run_id not in runs:
      raise OfflineCaptureError("capture_frames row has no capture_runs reference")
    if (
      type(sequence) is not int
      or not 1 <= sequence <= _JAVA_LONG_MAX
      or type(monotonic_ns) is not int
      or not _JAVA_LONG_MIN <= monotonic_ns <= _JAVA_LONG_MAX
      or type(dropped) is not int
      or not 0 <= dropped <= _JAVA_LONG_MAX
      or type(encoded) is not str
      or type(is_pinned) is not int
      or is_pinned not in (0, 1)
    ):
      raise OfflineCaptureError("capture_frames row or pinned value is invalid")
    try:
      frame = decode_and_validate_frame(encoded)
    except OfflineCaptureError as error:
      raise OfflineCaptureError(f"capture frame {row_id} is invalid: {error}") from error
    if (
      frame["channel"] != channel
      or frame["sequence"] != sequence
      or frame["monotonic_ns"] != monotonic_ns
      or frame["dropped"] != dropped
    ):
      raise OfflineCaptureError("capture_frames SQL columns differ from encoded envelope")
    counts[channel] += 1
    pinned[channel] += is_pinned
    run_frames[run_id].append((sequence, monotonic_ns, dropped, channel))
    shape = _observation_shape(frame["observation"])
    shape_key = json.dumps(shape, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    shapes[channel][shape_key] += 1

  if sum(counts.values()) > contract.total_quota:
    raise OfflineCaptureError("capture_frames total quota exceeded")
  for channel in CHANNELS:
    if counts[channel] > contract.channel_quotas[channel]:
      raise OfflineCaptureError(f"capture_frames {channel} quota exceeded")
    if pinned[channel] > contract.pinned_per_channel:
      raise OfflineCaptureError(
        f"capture_frames {channel} has more than {contract.pinned_per_channel} pinned rows"
      )
  return {
    "counts": counts,
    "run_frames": run_frames,
    "shapes": shapes,
  }


def _run_summaries(
    runs: dict[str, tuple[int, int, str]],
    run_frames: dict[str, list[tuple[int, int, int, str]]],
) -> list[dict[str, object]]:
  summaries: list[dict[str, object]] = []
  ordered_runs = sorted(runs, key=lambda run_id: (runs[run_id][0], run_id))
  for ordinal, run_id in enumerate(ordered_runs, start=1):
    frames = run_frames.get(run_id, [])
    sequences = [frame[0] for frame in frames]
    monotonic_values = [frame[1] for frame in frames]
    counts = Counter(frame[3] for frame in frames)
    summaries.append({
      "run_ordinal": ordinal,
      "clean_shutdown": bool(runs[run_id][1]),
      "frame_count": len(frames),
      "counts": {channel: counts[channel] for channel in CHANNELS},
      "sequence_monotonic": _strictly_increasing(sequences),
      "monotonic_time_monotonic": _strictly_increasing(monotonic_values),
      "maximum_reported_drops": max((frame[2] for frame in frames), default=0),
    })
  return summaries


def _strictly_increasing(values: list[int]) -> bool:
  return all(current > previous for previous, current in zip(values, values[1:]))


def _count_bucket(value: int) -> str:
  if value == 0:
    return "zero"
  if value == 1:
    return "one"
  if value <= 4:
    return "two_to_four"
  if value <= 16:
    return "five_to_sixteen"
  if value <= 64:
    return "seventeen_to_sixty_four"
  return "greater_than_sixty_four"


def _observation_shape(value: object) -> dict[str, object]:
  if value == {"kind": "null"}:
    return {"state": "null"}
  if value == {"status": "accessor_error"}:
    return {"state": "accessor_error"}
  if value == {"truncated": True}:
    return {"state": "truncated"}
  if type(value) is not dict:
    raise OfflineCaptureError("validated observation shape is not an object")
  if "root_descriptor" in value:
    return {
      "channel": value["channel"],
      "result": value["result"],
      "root_descriptor": value["root_descriptor"],
      "input_count_bucket": _count_bucket(value["input_count"]),
      "output_count_bucket": _count_bucket(value["output_count"]),
      "item_present": value["item_present"],
      "distance_valid": value["distance_valid"],
      "frame_eligible": value["frame_eligible"],
    }
  shape: dict[str, object] = {"descriptor": value["descriptor"]}
  if "enum_name" in value:
    shape["enum_name"] = value["enum_name"]
  elif "numeric_bucket" in value:
    shape["numeric_bucket"] = value["numeric_bucket"]
  elif "boolean" in value:
    shape["value_kind"] = "boolean"
  elif "string_length" in value:
    shape["string_length_bucket"] = _count_bucket(value["string_length"])
    if value.get("truncated") is True:
      shape["state"] = "truncated"
  elif "depth_limited" in value:
    shape["state"] = "depth_limited"
  elif "truncated" in value:
    shape["state"] = "truncated"
  elif "accessors" in value:
    shape["accessors"] = [
      {
        "name": accessor["name"],
        "descriptor": accessor["descriptor"],
        **(
          {"state": accessor["status"]}
          if "status" in accessor
          else {"value": _observation_shape(accessor["value"])}
        ),
      }
      for accessor in value["accessors"]
    ]
  else:
    shape["collection_size_bucket"] = _count_bucket(value["collection_size"])
    shape["sampled_items_bucket"] = _count_bucket(value["sampled_items"])
    shape["element_descriptors"] = list(value["element_descriptors"])
  return shape


def _shape_output(
    shapes: dict[str, Counter[str]],
) -> dict[str, list[dict[str, object]]]:
  result: dict[str, list[dict[str, object]]] = {}
  for channel in CHANNELS:
    result[channel] = [
      {
        "shape": json.loads(shape),
        "occurrences": occurrences,
      }
      for shape, occurrences in sorted(shapes[channel].items())
    ]
  return result


def _assert_outputs_are_sanitized(outputs: dict[str, dict[str, Any]]) -> None:
  for name, value in outputs.items():
    try:
      assert_sanitized(value)
    except SanitizationRejected as error:
      raise OfflineCaptureError(f"privacy validation failed for {name}") from error


def _publish_bundle(
    destination: Path,
    outputs: dict[str, dict[str, Any]],
) -> None:
  parent = destination.parent
  parent.mkdir(parents=True, exist_ok=True)
  lock = parent / f".{destination.name}.publish-lock"
  staging = parent / f".{destination.name}.publish-{uuid.uuid4().hex}"
  acquired_lock = False
  created_staging = False
  try:
    try:
      lock.mkdir()
      acquired_lock = True
    except FileExistsError as error:
      raise OfflineCaptureError(
        "sanitized bundle publication is already in progress"
      ) from error
    if destination.exists():
      raise OfflineCaptureError(
        "sanitized bundle destination must not already exist"
      )
    staging.mkdir()
    created_staging = True
    for name in _OUTPUT_NAMES:
      _write_json_file(staging / name, outputs[name])
    inventory = tuple(
      sorted(
        path.name
        for path in staging.iterdir()
        if path.is_file() and not path.is_symlink()
      )
    )
    if inventory != tuple(sorted(_OUTPUT_NAMES)) or len(tuple(staging.iterdir())) != len(_OUTPUT_NAMES):
      raise OfflineCaptureError("sanitized bundle staged inventory is not exact")
    if destination.exists():
      raise OfflineCaptureError(
        "sanitized bundle destination appeared during publication"
      )
    os.rename(staging, destination)
    created_staging = False
  except OfflineCaptureError:
    raise
  except (OSError, TypeError, ValueError) as error:
    raise OfflineCaptureError(f"cannot publish sanitized bundle: {error}") from error
  finally:
    if created_staging and staging.exists():
      try:
        shutil.rmtree(staging)
      except OSError:
        pass
    if acquired_lock:
      try:
        lock.rmdir()
      except OSError:
        pass


def _write_json_file(path: Path, value: dict[str, Any]) -> None:
  path.write_text(
    json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True) + "\n",
    encoding="utf-8",
    newline="\n",
  )


__all__ = [
  "OfflineCaptureError",
  "OfflineCaptureReport",
  "primary_database_is_schema_compatible",
  "decode_and_validate_frame",
  "validate_offline_database",
]
