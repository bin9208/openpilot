from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sqlite3

import pytest

import tools.naver_map_patch.offline_capture_db as offline_capture_db
from tools.naver_map_patch.offline_capture_db import (
  OfflineCaptureError,
  _validate_integrity,
  decode_and_validate_frame,
  validate_offline_database,
)
from tools.naver_map_patch.profile import ProfileError, _diagnostic_payload, load_profile


PROFILE = load_profile("6.8.0.5")
CHANNELS = ("status", "tbt_current", "tbt_next", "safety", "route", "lane")
QUOTAS = {
  "status": 256,
  "route": 256,
  "tbt_current": 1536,
  "tbt_next": 1536,
  "lane": 1536,
  "safety": 6144,
}
RUN_A = "11111111-1111-4111-8111-111111111111"
RUN_B = "22222222-2222-4222-8222-222222222222"
FIELD_ACCEPTANCE_BUILD_ID = "naver-6.8.0.5-field-acceptance-v4"


def _diagnostic_payload_json():
  path = Path(__file__).parents[1] / "profiles" / "6.8.0.5.json"
  return json.loads(path.read_text(encoding="utf-8"))["diagnostic_payload"]


def test_typed_profile_preserves_the_exact_offline_capture_contract():
  offline = _diagnostic_payload(_diagnostic_payload_json()).offline_capture

  assert offline.database_name == "capture-v3.sqlite3"
  assert offline.payload_build_id == "naver-6.8.0.5-diagnostic-offline-v3"
  assert offline.schema_version == 1
  assert offline.pinned_per_channel == 64
  assert dict(offline.channel_quotas) == {
    "lane": 1536,
    "route": 256,
    "safety": 6144,
    "status": 256,
    "tbt_current": 1536,
    "tbt_next": 1536,
  }


def test_typed_profile_preserves_exact_capture_sharing_contract():
  sharing = _diagnostic_payload(_diagnostic_payload_json()).capture_sharing

  assert sharing.authority == "com.nhn.android.nmap.fileprovider"
  assert sharing.external_files_root_path == "NaverNavi"
  assert sharing.export_database_name == "capture-v3-export.sqlite3"
  assert sharing.file_provider_root_name == "navi_trace"
  assert sharing.grantee_package == "com.android.shell"
  assert sharing.quiesce_timeout_ms == 5_000
  assert sharing.infrastructure_anchor.name == "capture_export_quiesce"
  assert sharing.infrastructure_anchor.dex_entry == "classes.dex"


@pytest.mark.parametrize(
  ("path", "drifted"),
  [
    (("authority",), "other.provider"),
    (("external_files_root_path",), "Other"),
    (("export_database_name",), "other-export.sqlite3"),
    (("file_provider_root_name",), "other"),
    (("grantee_package",), "other.shell"),
    (("quiesce_timeout_ms",), 4_999),
    (("infrastructure_anchor", "expected_count"), 2),
    (
      ("infrastructure_anchor", "diagnostic_hook", "method_descriptor"),
      "other(Ljava/lang/Object;)V",
    ),
  ],
)
def test_typed_profile_rejects_capture_sharing_value_drift(path, drifted):
  payload = _diagnostic_payload_json()
  selected = payload["capture_sharing"]
  for component in path[:-1]:
    selected = selected[component]
  selected[path[-1]] = drifted

  with pytest.raises(ProfileError, match="capture_sharing"):
    _diagnostic_payload(payload)


@pytest.mark.parametrize("mutation", ["missing", "extra", "invalid_quota"])
def test_typed_profile_rejects_non_exact_offline_capture_contract(mutation):
  payload = _diagnostic_payload_json()
  offline = payload["offline_capture"]
  if mutation == "missing":
    del offline["database_name"]
  elif mutation == "extra":
    offline["unexpected"] = 1
  else:
    offline["channel_quotas"]["status"] = True

  with pytest.raises(ProfileError, match="offline_capture"):
    _diagnostic_payload(payload)


@pytest.mark.parametrize(("field", "drifted"), [
  ("batch_size", 31),
  ("database_name", "other.sqlite3"),
  ("database_recovery_name", "other-recovery.sqlite3"),
  ("directory_name", "other-diagnostic"),
  ("flush_interval_ms", 251),
  ("journal_size_limit_bytes", 4_194_305),
  ("local_queue_capacity", 255),
  ("max_database_bytes", 67_108_865),
  ("network_queue_capacity", 63),
  ("payload_build_id", "naver-6.8.0.5-diagnostic-offline-v1"),
  ("pinned_per_channel", 63),
  ("retry_initial_ms", 999),
  ("retry_max_ms", 30_001),
  ("schema_version", 2),
  ("wal_autocheckpoint_pages", 255),
])
def test_typed_profile_rejects_every_offline_capture_value_drift(field, drifted):
  payload = _diagnostic_payload_json()
  payload["offline_capture"][field] = drifted

  with pytest.raises(ProfileError, match="offline_capture"):
    _diagnostic_payload(payload)


def test_typed_profile_rejects_channel_quota_value_drift():
  payload = _diagnostic_payload_json()
  payload["offline_capture"]["channel_quotas"]["status"] = 257

  with pytest.raises(ProfileError, match="offline_capture"):
    _diagnostic_payload(payload)


def _frame(channel: str, sequence: int, monotonic_ns: int, *, dropped: int = 0, observation=None):
  return {
    "schema": "naver.diagnostic.v1",
    "channel": channel,
    "sequence": sequence,
    "monotonic_ns": monotonic_ns,
    "dropped": dropped,
    "observation": observation or {
      "descriptor": "Lsample/Value;",
      "accessors": [],
    },
  }


def _create_database(
    raw: Path, *, rows=None, database_name: str = "capture-v3.sqlite3",
) -> Path:
  raw.mkdir(parents=True, exist_ok=True)
  path = raw / database_name
  connection = sqlite3.connect(path)
  connection.executescript(
    """
    PRAGMA user_version=1;
    CREATE TABLE capture_meta (
      key TEXT PRIMARY KEY NOT NULL,
      value TEXT NOT NULL
    );
    CREATE TABLE capture_runs (
      run_id TEXT PRIMARY KEY NOT NULL,
      started_monotonic_ns INTEGER NOT NULL,
      payload_build_id TEXT NOT NULL,
      clean_shutdown INTEGER NOT NULL DEFAULT 0
        CHECK(clean_shutdown IN (0, 1))
    );
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
    CREATE INDEX capture_frames_channel_id
    ON capture_frames(channel, id);
    """
  )
  metadata = {
    "schema_version": "1",
    "payload_build_id": "naver-6.8.0.5-diagnostic-offline-v3",
    "object_queue_dropped": "0",
    "local_queue_dropped": "0",
    "network_queue_dropped": "0",
    "storage_errors": "0",
    "network_errors": "0",
    "retention_deletes": "0",
    "rejected_frames": "0",
  }
  connection.executemany(
    "INSERT INTO capture_meta(key, value) VALUES (?, ?)",
    metadata.items(),
  )
  connection.executemany(
    "INSERT INTO capture_runs VALUES (?, ?, ?, ?)",
    [
      (RUN_A, 100, "naver-6.8.0.5-diagnostic-offline-v3", 1),
      (RUN_B, 200, "naver-6.8.0.5-diagnostic-offline-v3", 0),
    ],
  )
  if rows is None:
    rows = [
      (RUN_A, 1, 101, "status", 0, _frame("status", 1, 101), 1),
      (RUN_A, 2, 102, "route", 0, _frame("route", 2, 102), 1),
    ]
  connection.executemany(
    """
    INSERT INTO capture_frames(
      run_id, sequence, monotonic_ns, channel,
      object_dropped_before, encoded_json, pinned
    ) VALUES (?, ?, ?, ?, ?, ?, ?)
    """,
    [
      (run_id, sequence, monotonic_ns, channel, dropped,
       json.dumps(frame, separators=(",", ":"), sort_keys=True), pinned)
      for run_id, sequence, monotonic_ns, channel, dropped, frame, pinned in rows
    ],
  )
  connection.commit()
  connection.close()
  return path


def _replace_schema_sql(path: Path, object_name: str, transform) -> None:
  connection = sqlite3.connect(path)
  connection.execute("PRAGMA writable_schema=ON")
  original = connection.execute(
    "SELECT sql FROM sqlite_master WHERE name=?", (object_name,)
  ).fetchone()[0]
  connection.execute(
    "UPDATE sqlite_master SET sql=? WHERE name=?",
    (transform(original), object_name),
  )
  connection.execute("PRAGMA writable_schema=OFF")
  connection.commit()
  connection.close()


def _validate(tmp_path: Path, *, rows=None):
  raw = tmp_path / "raw"
  database = _create_database(raw, rows=rows)
  bundle = tmp_path / "bundle"
  report = validate_offline_database(raw, bundle, PROFILE)
  return database, bundle, report


def test_decoder_is_shared_and_rejects_private_rows_without_sanitizing():
  frame = _frame("status", 1, 10)
  frame["observation"]["account_id"] = "private"

  with pytest.raises(OfflineCaptureError, match="privacy"):
    decode_and_validate_frame(json.dumps(frame))


def test_missing_database_fails_closed(tmp_path):
  raw = tmp_path / "raw"
  raw.mkdir()

  with pytest.raises(OfflineCaptureError, match="compatible"):
    validate_offline_database(raw, tmp_path / "bundle", PROFILE)


def test_recovery_database_is_selected_when_primary_schema_is_incompatible(tmp_path):
  raw = tmp_path / "raw"
  primary = _create_database(raw)
  connection = sqlite3.connect(primary)
  connection.execute("PRAGMA user_version=2")
  connection.commit()
  connection.close()
  _create_database(raw, database_name="capture-v3-recovery.sqlite3")

  report = validate_offline_database(raw, tmp_path / "bundle", PROFILE)

  assert report.frame_count == 2


def test_selected_primary_content_failure_never_falls_back_to_recovery(tmp_path):
  raw = tmp_path / "raw"
  primary = _create_database(raw)
  connection = sqlite3.connect(primary)
  connection.execute(
    "UPDATE capture_runs SET run_id=? WHERE run_id=?",
    ("not-a-uuid", RUN_A),
  )
  connection.execute(
    "UPDATE capture_frames SET run_id=? WHERE run_id=?",
    ("not-a-uuid", RUN_A),
  )
  connection.commit()
  connection.close()
  _create_database(raw, database_name="capture-v3-recovery.sqlite3")

  with pytest.raises(OfflineCaptureError, match="capture_runs"):
    validate_offline_database(raw, tmp_path / "bundle", PROFILE)
  assert not (tmp_path / "bundle").exists()


def test_both_incompatible_database_candidates_fail_closed(tmp_path):
  raw = tmp_path / "raw"
  for name in ("capture-v3.sqlite3", "capture-v3-recovery.sqlite3"):
    database = _create_database(raw, database_name=name)
    connection = sqlite3.connect(database)
    connection.execute("PRAGMA user_version=2")
    connection.commit()
    connection.close()

  with pytest.raises(OfflineCaptureError, match="compatible"):
    validate_offline_database(raw, tmp_path / "bundle", PROFILE)
  assert not (tmp_path / "bundle").exists()


@pytest.mark.parametrize("invalid_run_id", [
  "run-1",
  "11111111111141118111111111111111",
  "AAAAAAAA-AAAA-4AAA-8AAA-AAAAAAAAAAAA",
])
def test_capture_run_id_must_be_a_canonical_lowercase_uuid(tmp_path, invalid_run_id):
  database = _create_database(tmp_path / "raw")
  connection = sqlite3.connect(database)
  connection.execute(
    "UPDATE capture_runs SET run_id=? WHERE run_id=?",
    (invalid_run_id, RUN_A),
  )
  connection.execute(
    "UPDATE capture_frames SET run_id=? WHERE run_id=?",
    (invalid_run_id, RUN_A),
  )
  connection.commit()
  connection.close()

  with pytest.raises(OfflineCaptureError, match="capture_runs"):
    validate_offline_database(database.parent, tmp_path / "bundle", PROFILE)


@pytest.mark.parametrize("mutation, message", [
  ("version", "user_version"),
  ("table", "tables"),
  ("columns", "columns"),
  ("index", "index"),
  ("unique_index", "index"),
])
def test_exact_schema_contract_is_required(tmp_path, mutation, message):
  raw = tmp_path / "raw"
  path = _create_database(raw)
  connection = sqlite3.connect(path)
  if mutation == "version":
    connection.execute("PRAGMA user_version=2")
  elif mutation == "table":
    connection.execute("DROP TABLE capture_meta")
  elif mutation == "columns":
    connection.execute("ALTER TABLE capture_runs ADD COLUMN unexpected TEXT")
  elif mutation == "index":
    connection.execute("DROP INDEX capture_frames_channel_id")
  else:
    connection.execute("DROP INDEX capture_frames_channel_id")
    connection.execute(
      "CREATE UNIQUE INDEX capture_frames_channel_id "
      "ON capture_frames(channel, id)"
    )
  connection.commit()
  connection.close()

  with pytest.raises(OfflineCaptureError, match=message):
    validate_offline_database(raw, tmp_path / "bundle", PROFILE)


@pytest.mark.parametrize("mutation", [
  "missing_autoincrement",
  "extra_constraint",
  "extra_option",
  "extra_comment",
  "index_desc_collation",
])
def test_complete_canonical_sql_is_required(tmp_path, mutation):
  raw = tmp_path / "raw"
  path = _create_database(raw)
  if mutation == "missing_autoincrement":
    _replace_schema_sql(
      path, "capture_frames",
      lambda sql: sql.replace(" AUTOINCREMENT", ""),
    )
  elif mutation == "extra_constraint":
    _replace_schema_sql(
      path, "capture_frames",
      lambda sql: sql.replace(
        "CHECK(pinned IN (0, 1))",
        "CHECK(pinned IN (0, 1)) CHECK(id > 0)",
      ),
    )
  elif mutation == "extra_option":
    _replace_schema_sql(path, "capture_meta", lambda sql: sql + " STRICT")
  elif mutation == "extra_comment":
    _replace_schema_sql(path, "capture_runs", lambda sql: sql + " /* extra */")
  else:
    connection = sqlite3.connect(path)
    connection.execute("DROP INDEX capture_frames_channel_id")
    connection.execute(
      "CREATE INDEX capture_frames_channel_id "
      "ON capture_frames(channel COLLATE NOCASE, id DESC)"
    )
    connection.commit()
    connection.close()

  with pytest.raises(OfflineCaptureError, match="canonical"):
    validate_offline_database(raw, tmp_path / "bundle", PROFILE)


def test_every_regular_file_is_hashed_in_sorted_relative_path_order(tmp_path):
  database, bundle, report = _validate(tmp_path)
  (database.parent / "capture-v3.sqlite3-shm").write_bytes(b"shm-copy")
  nested = database.parent / "nested"
  nested.mkdir()
  (nested / "evidence.bin").write_bytes(b"evidence")

  # Revalidate into a fresh bundle after the complete pulled tree exists.
  bundle2 = tmp_path / "bundle-2"
  report = validate_offline_database(database.parent, bundle2, PROFILE)
  hashes = json.loads((bundle2 / "hashes.json").read_text(encoding="utf-8"))

  assert [entry["path"] for entry in hashes["files"]] == [
    "capture-v3.sqlite3",
    "capture-v3.sqlite3-shm",
    "nested/evidence.bin",
  ]
  assert hashes["files"][2]["sha256"] == ":".join(
    hashlib.sha256(b"evidence").hexdigest()[index:index + 4]
    for index in range(0, 64, 4)
  )
  assert report.file_count == 3


def test_bundle_contains_exact_verified_payload_build_id(tmp_path):
  _, bundle, _ = _validate(tmp_path)

  summary = json.loads((bundle / "summary.json").read_text(encoding="utf-8"))
  manifest = json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))

  assert summary["payload_build_id"] == "naver-6.8.0.5-diagnostic-offline-v3"
  assert manifest["payload_build_id"] == "naver-6.8.0.5-diagnostic-offline-v3"
  assert summary["run_payload_build_ids"] == [
    "naver-6.8.0.5-diagnostic-offline-v3",
  ]
  assert manifest["run_payload_build_ids"] == [
    "naver-6.8.0.5-diagnostic-offline-v3",
  ]


def test_field_acceptance_database_allows_only_exact_mixed_diagnostic_runs(tmp_path):
  raw = tmp_path / "raw"
  database = _create_database(raw)
  connection = sqlite3.connect(database)
  connection.execute(
    "UPDATE capture_meta SET value=? WHERE key='payload_build_id'",
    (FIELD_ACCEPTANCE_BUILD_ID,),
  )
  connection.execute(
    "UPDATE capture_runs SET payload_build_id=? WHERE run_id=?",
    (FIELD_ACCEPTANCE_BUILD_ID, RUN_B),
  )
  connection.commit()
  connection.close()

  bundle = tmp_path / "bundle"
  validate_offline_database(raw, bundle, PROFILE)
  summary = json.loads((bundle / "summary.json").read_text(encoding="utf-8"))
  manifest = json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))

  assert summary["payload_build_id"] == FIELD_ACCEPTANCE_BUILD_ID
  assert manifest["payload_build_id"] == FIELD_ACCEPTANCE_BUILD_ID
  assert summary["run_payload_build_ids"] == [
    "naver-6.8.0.5-diagnostic-offline-v3",
    FIELD_ACCEPTANCE_BUILD_ID,
  ]
  assert manifest["run_payload_build_ids"] == [
    "naver-6.8.0.5-diagnostic-offline-v3",
    FIELD_ACCEPTANCE_BUILD_ID,
  ]


@pytest.mark.parametrize("historical_build_id", [
  "naver-6.8.0.5-field-acceptance-v1",
  "naver-6.8.0.5-field-acceptance-v2",
  "naver-6.8.0.5-field-acceptance-v3",
])
def test_historical_field_capture_ids_remain_readable(tmp_path, historical_build_id):
  raw = tmp_path / "raw"
  database = _create_database(raw)
  connection = sqlite3.connect(database)
  connection.execute(
    "UPDATE capture_meta SET value=? WHERE key='payload_build_id'",
    (historical_build_id,),
  )
  connection.execute(
    "UPDATE capture_runs SET payload_build_id=?",
    (historical_build_id,),
  )
  connection.commit()
  connection.close()

  bundle = tmp_path / "bundle"
  validate_offline_database(raw, bundle, PROFILE)
  summary = json.loads((bundle / "summary.json").read_text(encoding="utf-8"))
  assert summary["payload_build_id"] == historical_build_id


def test_mapping_outcome_frame_is_exact_and_coordinate_free():
  observation = {
    "channel": "route",
    "result": "route_ok",
    "root_descriptor": "Lcom/naver/map/core/navigation/model/CurrentRoute;",
    "input_count": 123,
    "output_count": 123,
    "revision": 7,
    "item_present": True,
    "distance_valid": False,
    "frame_eligible": True,
  }
  frame = _frame("route", 1, 100, observation=observation)

  assert decode_and_validate_frame(json.dumps(frame))["observation"] == observation

  observation["latitude"] = 37.5
  with pytest.raises(OfflineCaptureError, match="privacy"):
    decode_and_validate_frame(json.dumps(frame))


def test_field_mapping_outcome_database_is_summarized_without_descriptor(tmp_path):
  observation = {
    "channel": "route",
    "result": "route_accessor",
    "root_descriptor": "Lcom/naver/map/core/navigation/model/CurrentRoute;",
    "input_count": 0,
    "output_count": 0,
    "revision": 1,
    "item_present": False,
    "distance_valid": False,
    "frame_eligible": False,
  }
  raw = tmp_path / "raw"
  database = _create_database(raw, rows=[
    (RUN_B, 1, 201, "route", 0, _frame(
      "route", 1, 201, observation=observation,
    ), 1),
  ])
  connection = sqlite3.connect(database)
  connection.execute(
    "UPDATE capture_meta SET value=? WHERE key='payload_build_id'",
    (FIELD_ACCEPTANCE_BUILD_ID,),
  )
  connection.execute(
    "UPDATE capture_runs SET payload_build_id=? WHERE run_id=?",
    (FIELD_ACCEPTANCE_BUILD_ID, RUN_B),
  )
  connection.commit()
  connection.close()

  bundle = tmp_path / "bundle"
  validate_offline_database(raw, bundle, PROFILE)
  shapes = json.loads((bundle / "accessor_shapes.json").read_text(encoding="utf-8"))

  assert shapes["channels"]["route"] == [{
    "occurrences": 1,
    "shape": {
      "channel": "route",
      "result": "route_accessor",
      "root_descriptor": "Lcom/naver/map/core/navigation/model/CurrentRoute;",
      "input_count_bucket": "zero",
      "output_count_bucket": "zero",
      "item_present": False,
      "distance_valid": False,
      "frame_eligible": False,
    },
  }]


def test_unknown_capture_run_build_id_remains_rejected(tmp_path):
  raw = tmp_path / "raw"
  database = _create_database(raw)
  connection = sqlite3.connect(database)
  connection.execute(
    "UPDATE capture_runs SET payload_build_id='naver-unknown' WHERE run_id=?",
    (RUN_B,),
  )
  connection.commit()
  connection.close()

  with pytest.raises(OfflineCaptureError, match="capture_runs"):
    validate_offline_database(raw, tmp_path / "bundle", PROFILE)


def test_mid_publication_failure_leaves_no_partial_target_or_tool_staging(
    tmp_path, monkeypatch,
):
  raw = tmp_path / "raw"
  _create_database(raw)
  bundle = tmp_path / "bundle"
  calls = 0
  real_write = offline_capture_db._write_json_file

  def fail_third_write(path, value):
    nonlocal calls
    calls += 1
    if calls == 3:
      raise OSError("injected publication failure")
    real_write(path, value)

  monkeypatch.setattr(offline_capture_db, "_write_json_file", fail_third_write)

  with pytest.raises(OfflineCaptureError, match="publish"):
    validate_offline_database(raw, bundle, PROFILE)
  assert not bundle.exists()
  assert not list(tmp_path.glob(".bundle.publish-*"))


def test_preexisting_bundle_is_never_reused_or_changed(tmp_path):
  raw = tmp_path / "raw"
  _create_database(raw)
  bundle = tmp_path / "bundle"
  bundle.mkdir()
  marker = bundle / "owner.txt"
  marker.write_text("other owner", encoding="utf-8")

  with pytest.raises(OfflineCaptureError, match="must not already exist"):
    validate_offline_database(raw, bundle, PROFILE)
  assert marker.read_text(encoding="utf-8") == "other owner"
  assert list(bundle.iterdir()) == [marker]


def test_destination_created_during_publication_is_not_overwritten(
    tmp_path, monkeypatch,
):
  raw = tmp_path / "raw"
  _create_database(raw)
  bundle = tmp_path / "bundle"
  real_write = offline_capture_db._write_json_file
  raced = False

  def create_raced_destination(path, value):
    nonlocal raced
    real_write(path, value)
    if not raced:
      raced = True
      bundle.mkdir()
      (bundle / "owner.txt").write_text("raced owner", encoding="utf-8")

  monkeypatch.setattr(
    offline_capture_db, "_write_json_file", create_raced_destination
  )

  with pytest.raises(OfflineCaptureError, match="appeared during publication"):
    validate_offline_database(raw, bundle, PROFILE)
  assert (bundle / "owner.txt").read_text(encoding="utf-8") == "raced owner"
  assert list(bundle.iterdir()) == [bundle / "owner.txt"]
  assert not list(tmp_path.glob(".bundle.publish-*"))


def test_concurrent_publication_lock_is_not_removed_or_bypassed(tmp_path):
  raw = tmp_path / "raw"
  _create_database(raw)
  bundle = tmp_path / "bundle"
  lock = tmp_path / ".bundle.publish-lock"
  lock.mkdir()

  with pytest.raises(OfflineCaptureError, match="already in progress"):
    validate_offline_database(raw, bundle, PROFILE)
  assert lock.is_dir()
  assert not bundle.exists()


def test_private_accessor_in_database_never_publishes_shape_bundle(tmp_path):
  observation = {
    "descriptor": "Lsample/Status;",
    "accessors": [{
      "name": "homeAddress",
      "descriptor": "()Ljava/lang/String;",
      "status": "not_allowlisted",
    }],
  }
  rows = [
    (RUN_A, 1, 101, "status", 0,
     _frame("status", 1, 101, observation=observation), 1),
  ]

  with pytest.raises(OfflineCaptureError, match="privacy"):
    _validate(tmp_path, rows=rows)
  assert not (tmp_path / "bundle").exists()


def test_copied_wal_and_shm_are_hashed_and_wal_rows_are_visible(tmp_path):
  raw = tmp_path / "raw"
  database = _create_database(raw)
  writer = sqlite3.connect(database)
  assert writer.execute("PRAGMA journal_mode=WAL").fetchone() == ("wal",)
  writer.execute("PRAGMA wal_autocheckpoint=0")
  frame = _frame("lane", 3, 103)
  writer.execute(
    "INSERT INTO capture_frames(run_id,sequence,monotonic_ns,channel,object_dropped_before,encoded_json,pinned)"
    " VALUES (?,?,?,?,?,?,?)",
    (RUN_A, 3, 103, "lane", 0, json.dumps(frame), 1),
  )
  writer.commit()
  try:
    assert (raw / "capture-v3.sqlite3-wal").is_file()
    assert (raw / "capture-v3.sqlite3-shm").is_file()

    report = validate_offline_database(raw, tmp_path / "bundle", PROFILE)
    hashes = json.loads(
      (tmp_path / "bundle" / "hashes.json").read_text(encoding="utf-8")
    )

    assert report.counts["lane"] == 1
    assert [entry["path"] for entry in hashes["files"]] == [
      "capture-v3.sqlite3",
      "capture-v3.sqlite3-shm",
      "capture-v3.sqlite3-wal",
    ]
  finally:
    writer.close()


def test_quick_check_must_return_one_exact_ok_row():
  class Result:
    def fetchall(self):
      return [("ok",), ("unexpected",)]

  class Connection:
    def execute(self, sql):
      assert sql == "PRAGMA quick_check"
      return Result()

  with pytest.raises(OfflineCaptureError, match="exactly ok"):
    _validate_integrity(Connection())


def test_unknown_channel_fails(tmp_path):
  database = _create_database(tmp_path / "raw")
  connection = sqlite3.connect(database)
  connection.execute("PRAGMA ignore_check_constraints=ON")
  frame = _frame("unknown", 3, 103)
  connection.execute(
    "INSERT INTO capture_frames(run_id,sequence,monotonic_ns,channel,object_dropped_before,encoded_json,pinned)"
    " VALUES (?,?,?,?,?,?,?)",
    (RUN_A, 3, 103, "unknown", 0, json.dumps(frame), 0),
  )
  connection.commit()
  connection.close()
  with pytest.raises(OfflineCaptureError, match="unknown channel"):
    validate_offline_database(database.parent, tmp_path / "bundle", PROFILE)


@pytest.mark.parametrize("channel", CHANNELS)
def test_each_channel_quota_overflow_fails(tmp_path, channel):
  rows = []
  for sequence in range(1, QUOTAS[channel] + 2):
    frame = _frame(channel, sequence, sequence)
    rows.append((RUN_A, sequence, sequence, channel, 0, frame, int(sequence <= 64)))
  with pytest.raises(OfflineCaptureError, match="quota"):
    _validate(tmp_path / channel, rows=rows)


def test_invalid_pinning_and_missing_run_reference_fail(tmp_path):
  database = _create_database(tmp_path / "pinned" / "raw")
  connection = sqlite3.connect(database)
  connection.execute("PRAGMA ignore_check_constraints=ON")
  connection.execute("UPDATE capture_frames SET pinned=2 WHERE id=1")
  connection.commit()
  connection.close()
  with pytest.raises(OfflineCaptureError, match="pinned"):
    validate_offline_database(database.parent, tmp_path / "pinned" / "bundle", PROFILE)

  rows = [
    ("missing-run", 1, 1, "status", 0, _frame("status", 1, 1), 1),
  ]
  with pytest.raises(OfflineCaptureError, match="capture_runs"):
    _validate(tmp_path / "missing-run", rows=rows)


def test_more_than_64_pinned_rows_per_channel_fail(tmp_path):
  rows = [
    (RUN_A, sequence, sequence, "status", 0,
     _frame("status", sequence, sequence), 1)
    for sequence in range(1, 66)
  ]

  with pytest.raises(OfflineCaptureError, match="64"):
    _validate(tmp_path, rows=rows)


@pytest.mark.parametrize("kind", ["malformed", "duplicate", "oversized", "privacy"])
def test_every_encoded_envelope_is_strictly_validated(tmp_path, kind):
  database = _create_database(tmp_path / "raw")
  connection = sqlite3.connect(database)
  if kind == "malformed":
    encoded = "{"
  elif kind == "duplicate":
    encoded = (
      '{"schema":"naver.diagnostic.v1","channel":"status","channel":"lane",'
      '"sequence":1,"monotonic_ns":101,"dropped":0,"observation":{"kind":"null"}}'
    )
  elif kind == "oversized":
    encoded = json.dumps(_frame("status", 1, 101)) + (" " * 4096)
  else:
    private = _frame("status", 1, 101)
    private["observation"]["account_id"] = "private"
    encoded = json.dumps(private)
  connection.execute("UPDATE capture_frames SET encoded_json=? WHERE id=1", (encoded,))
  connection.commit()
  connection.close()

  with pytest.raises(OfflineCaptureError):
    validate_offline_database(database.parent, tmp_path / "bundle", PROFILE)


@pytest.mark.parametrize("column", ["channel", "sequence", "monotonic_ns", "object_dropped_before"])
def test_sql_columns_must_equal_the_encoded_envelope(tmp_path, column):
  database = _create_database(tmp_path / "raw")
  connection = sqlite3.connect(database)
  connection.execute("PRAGMA ignore_check_constraints=ON")
  values = {
    "channel": "lane",
    "sequence": 9,
    "monotonic_ns": 999,
    "object_dropped_before": 7,
  }
  connection.execute(f"UPDATE capture_frames SET {column}=? WHERE id=1", (values[column],))
  connection.commit()
  connection.close()

  with pytest.raises(OfflineCaptureError, match="envelope"):
    validate_offline_database(database.parent, tmp_path / "bundle", PROFILE)


def test_per_run_summaries_and_shape_dedup_are_deterministic_and_identity_free(tmp_path):
  observation = {
    "descriptor": "Lsample/Status;",
    "accessors": [{
      "name": "state",
      "descriptor": "()Lsample/State;",
      "value": {"descriptor": "Lsample/State;", "enum_name": "Guiding"},
    }],
  }
  rows = [
    (RUN_B, 2, 202, "status", 1, _frame("status", 2, 202, dropped=1, observation=observation), 1),
    (RUN_A, 1, 101, "status", 0, _frame("status", 1, 101, observation=observation), 1),
    (RUN_B, 1, 201, "route", 0, _frame("route", 1, 201), 1),
  ]
  _, bundle, report = _validate(tmp_path, rows=rows)
  summary = json.loads((bundle / "summary.json").read_text(encoding="utf-8"))
  shapes = json.loads((bundle / "accessor_shapes.json").read_text(encoding="utf-8"))
  all_output = "\n".join(
    (bundle / name).read_text(encoding="utf-8")
    for name in ("hashes.json", "summary.json", "manifest.json", "accessor_shapes.json")
  )

  assert len(summary["runs"]) == 2
  assert summary["runs"][0]["sequence_monotonic"] is True
  assert summary["runs"][1]["sequence_monotonic"] is False
  assert report.run_count == 2
  assert shapes["channels"]["status"][0]["occurrences"] == 2
  for forbidden in (
    RUN_A, RUN_B, "run_id", "sequence", "monotonic_ns", "dropped",
    "serial", str((tmp_path / "raw").resolve()),
  ):
    assert forbidden not in json.dumps(shapes, sort_keys=True)
  assert RUN_A not in all_output
  assert RUN_B not in all_output
  assert sorted(path.name for path in bundle.iterdir()) == [
    "accessor_shapes.json", "hashes.json", "manifest.json", "summary.json",
  ]


def test_corrupt_database_fails_integrity_validation_without_publishing(tmp_path):
  raw = tmp_path / "raw"
  database = _create_database(raw)
  content = database.read_bytes()
  database.write_bytes(content[:128])

  with pytest.raises(OfflineCaptureError, match="SQLite|quick_check"):
    validate_offline_database(raw, tmp_path / "bundle", PROFILE)
  assert not (tmp_path / "bundle").exists()
