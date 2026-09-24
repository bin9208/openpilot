import json
from pathlib import Path


PATCH_ROOT = Path(__file__).resolve().parents[1]
PROFILE_PATH = PATCH_ROOT / "profiles" / "6.8.0.5.json"
DIAGNOSTIC_GRADLE_PATH = PATCH_ROOT / "dexpatch" / "diagnostic-payload.gradle"


def _walk(value):
  yield value
  if isinstance(value, dict):
    for child in value.values():
      yield from _walk(child)
  elif isinstance(value, list):
    for child in value:
      yield from _walk(child)


def _strings(value):
  return [child for child in _walk(value) if isinstance(child, str)]


def _hook_subtree(profile, hook_method):
  for value in _walk(profile):
    if not isinstance(value, dict) or "diagnostic_hook" not in value:
      continue
    hook = value["diagnostic_hook"]
    if not isinstance(hook, dict):
      continue
    descriptor = hook.get("method_descriptor", "")
    if descriptor.startswith(hook_method + "("):
      return value
  raise AssertionError(f"missing diagnostic hook {hook_method}")


def test_payload_dex_tracks_the_payload_jar_as_an_incremental_input():
  gradle = DIAGNOSTIC_GRADLE_PATH.read_text(encoding="utf-8")
  payload_dex = gradle.split('tasks.register("payloadDex", Exec) {', 1)[1].split(
    '\ntasks.register("diagnosticPatch"', 1
  )[0]

  assert 'inputs.file(tasks.named("diagnosticPayloadJar", Jar).flatMap' in payload_dex
  assert 'it.archiveFile' in payload_dex


def test_diagnostic_profile_has_exact_seven_anchor_dicts():
  profile = json.loads(PROFILE_PATH.read_text(encoding="utf-8"))
  anchors = {anchor["name"]: anchor for anchor in profile["anchors"]}
  expected = {
    "status": ("classes11.dex", "Lcom/naver/map/core/navigation/NaviStatusBroadcaster;", "g(Lcom/naver/map/core/navigation/NaviStatusBroadcaster$Status;)V", ["invoke-interface Lcom/naver/map/core/navigation/NaviStatusBroadcaster$HasGoal;->getCoord()Lcom/naver/maps/geometry/LatLng;", "move-result-object", "iget-wide Lcom/naver/maps/geometry/LatLng;->latitude:D"], {"argument": {"index": 0, "kind": "parameter"}, "method_descriptor": "onStatus(Ljava/lang/Object;)V", "placement": {"index": 0, "kind": "method_entry"}}),
    "tbt_current": (
      "classes11.dex",
      "Lcom/naver/map/core/navigation/model/TbtData;",
      "<init>(Lcom/naver/map/core/navigation/model/TbtItem;Lcom/naver/map/core/navigation/model/TbtItem;)V",
      [
        "invoke-static Lkotlin/jvm/internal/Intrinsics;->checkNotNullParameter(Ljava/lang/Object;Ljava/lang/String;)V",
        "invoke-direct Ljava/lang/Object;-><init>()V",
        "iput-object Lcom/naver/map/core/navigation/model/TbtData;->a:Lcom/naver/map/core/navigation/model/TbtItem;",
      ],
      {
        "argument": {"index": 0, "kind": "parameter"},
        "method_descriptor": "onCurrentTbt(Ljava/lang/Object;)V",
        "placement": {"index": 1, "kind": "after_instruction"},
      },
    ),
    "tbt_next": (
      "classes11.dex",
      "Lcom/naver/map/core/navigation/model/TbtData;",
      "<init>(Lcom/naver/map/core/navigation/model/TbtItem;Lcom/naver/map/core/navigation/model/TbtItem;)V",
      [
        "invoke-direct Ljava/lang/Object;-><init>()V",
        "iput-object Lcom/naver/map/core/navigation/model/TbtData;->a:Lcom/naver/map/core/navigation/model/TbtItem;",
        "iput-object Lcom/naver/map/core/navigation/model/TbtData;->b:Lcom/naver/map/core/navigation/model/TbtItem;",
      ],
      {
        "argument": {"index": 1, "kind": "parameter"},
        "method_descriptor": "onNextTbt(Ljava/lang/Object;)V",
        "placement": {"index": 2, "kind": "before_instruction"},
      },
    ),
    "safety_source": (
      "classes11.dex",
      "Lcom/naver/map/core/navigation/NaviSafetyControllItemManager;",
      "l(Lcom/naver/maps/navi/v2/api/guidance/model/GuidanceSafety;)Lcom/naver/map/core/common/model/SafetyExtra;",
      [
        "invoke-interface Lcom/naver/maps/navi/v2/api/guidance/model/GuidanceSafety;->getCode()Lcom/naver/maps/navi/v2/shared/api/route/constants/SafetyCode;",
        "move-result-object",
        "invoke-interface Lcom/naver/maps/navi/v2/api/guidance/model/GuidanceSafety;->distance()D",
      ],
      {
        "argument": {"index": 0, "kind": "parameter"},
        "method_descriptor": "onSafetySource(Ljava/lang/Object;)V",
        "placement": {"index": 0, "kind": "method_entry"},
      },
    ),
    "safety": ("classes11.dex", "Lcom/naver/map/core/navigation/SafeControlItemStore;", "k()Lcom/naver/map/core/common/model/SafeControlItem;", ["invoke-virtual/range Lcom/naver/map/core/navigation/NaviSafetyControllItemManager;->m(Lcom/naver/maps/navi/v2/api/guidance/model/GuidanceSafety;Lcom/naver/maps/navi/v2/api/guidance/model/GuidanceSafety;ZZZ)Lcom/naver/map/core/common/model/SafeControlItem;", "move-result-object", "return-object"], {"argument": {"index": 2, "kind": "window_register"}, "method_descriptor": "onSafety(Ljava/lang/Object;)V", "placement": {"index": 2, "kind": "before_instruction"}}),
    "route": ("classes11.dex", "Lcom/naver/map/core/navigation/NaviStore;", "q1(Lcom/naver/map/core/navigation/model/CurrentRoute;)V", ["iget-object Lcom/naver/map/core/navigation/NaviStore;->T0:Lcom/naver/map/base/common/lifecycle/MutableLiveData;", "invoke-virtual Lcom/naver/map/base/common/lifecycle/MutableLiveData;->setValue(Ljava/lang/Object;)V", "return-void"], {"argument": {"index": 0, "kind": "parameter"}, "method_descriptor": "onRoute(Ljava/lang/Object;)V", "placement": {"index": 0, "kind": "method_entry"}}),
    "lane": (
      "classes11.dex",
      "Lcom/naver/map/core/navigation/NaviStore;",
      "M0(Lcom/naver/maps/navi/v2/api/GuidanceSession;)V",
      [
        "new-instance Lcom/naver/map/core/navigation/lane/NaviLaneItem;",
        "invoke-interface Lcom/naver/maps/navi/v2/api/guidance/model/GuidanceLane;->getDistance-Y4BO_gI()D",
        "move-result-wide",
        "invoke-interface Lcom/naver/maps/navi/v2/api/guidance/model/GuidanceLane;->getInfo()Lcom/naver/maps/navi/v2/shared/api/route/model/RouteLane;",
        "move-result-object",
        "invoke-interface Lcom/naver/maps/navi/v2/shared/api/route/model/RouteLane;->getUnits()Ljava/util/List;",
        "move-result-object",
        "invoke-static Lcom/naver/map/AppContext;->c()Lcom/naver/map/core/common/util/AppType;",
        "move-result-object",
        "invoke-virtual Lcom/naver/map/core/common/util/AppType;->i()Z",
        "move-result",
        "xor-int/2addr",
        "invoke-static Lcom/naver/map/core/navigation/lane/NaviLaneItemKt;->f(Ljava/util/List;Lcom/naver/maps/navi/v2/shared/api/route/model/RouteGuideImage;Z)Ljava/util/List;",
        "move-result-object",
        "invoke-interface Lcom/naver/maps/navi/v2/api/guidance/model/GuidanceLane;->getInfo()Lcom/naver/maps/navi/v2/shared/api/route/model/RouteLane;",
        "move-result-object",
        "invoke-interface Lcom/naver/maps/navi/v2/shared/api/route/model/RouteLane;->getUnits()Ljava/util/List;",
        "move-result-object",
        "iget-boolean Lcom/naver/map/core/navigation/NaviStore;->h1:Z",
        "invoke-direct/range Lcom/naver/map/core/navigation/lane/NaviLaneItem;-><init>(DLjava/util/List;Ljava/util/List;Z)V",
      ],
      {
        "argument": {"index": 0, "kind": "window_register"},
        "method_descriptor": "onLane(Ljava/lang/Object;)V",
        "placement": {"index": 19, "kind": "after_instruction"},
      },
    ),
  }

  assert set(anchors) == set(expected)
  for name, (dex_entry, class_descriptor, method_descriptor, window, hook) in expected.items():
    assert anchors[name] == {
      "name": name,
      "dex_entry": dex_entry,
      "class_descriptor": class_descriptor,
      "method_descriptor": method_descriptor,
      "instruction_window": window,
      "expected_count": 1,
      "diagnostic_hook": hook,
    }


def test_status_hook_is_method_entry_status_parameter_not_latlng():
  profile = json.loads(PROFILE_PATH.read_text(encoding="utf-8"))
  subtree = _hook_subtree(profile, "onStatus")
  strings = _strings(subtree)

  assert (
    "g(Lcom/naver/map/core/navigation/NaviStatusBroadcaster$Status;)V"
    in strings
  )
  assert "method_entry" in strings
  assert "parameter" in strings
  assert "Lcom/naver/map/common/geometry/LatLng;" not in _strings(
    subtree["diagnostic_hook"]
  )


def test_diagnostic_payload_bounds_and_loopback_are_fixed():
  profile = json.loads(PROFILE_PATH.read_text(encoding="utf-8"))
  payload = profile["diagnostic_payload"]

  assert payload["main_process"] == "com.nhn.android.nmap"
  assert payload["min_api"] == 26
  assert payload["android_platform_api"] == 36
  assert payload["max_accessors"] == 16
  assert payload["max_collection_items"] == 4
  assert payload["max_sample_chars"] == 160
  allowlists = payload["accessor_allowlists"]
  assert set(allowlists) == {"status", "tbt_current", "tbt_next", "safety", "route", "lane"}
  assert allowlists["status"] == []
  assert all(allowlists[channel] for channel in allowlists if channel != "status")
  allowed = "\n".join(item for entries in allowlists.values() for item in entries)
  assert "TbtItem#h()D" in allowed
  assert "TbtItem#i()Lcom/naver/map/core/navigation/model/TbtDataItem;" in allowed
  assert "TbtDataItem#s()Lcom/naver/maps/navi/v2/shared/api/route/constants/TurnPointType;" in allowed
  assert "SafeControlItem#getSign()" in allowed
  assert "SafetySign#getSpeed()Ljava/lang/Integer;" in allowed
  assert "SafetySign#getType()Lcom/naver/map/core/common/model/SafetySign$SignType;" in allowed
  assert "CurrentRoute#e()" in allowed
  assert "CurrentRoute#c()Lcom/naver/map/core/common/model/RouteParam;" in allowed
  assert "NaviRouteData#h()Lcom/naver/maps/navi/v2/shared/api/route/model/RouteInfo;" in allowed
  assert "NaviLaneItem#g()D" in allowed
  assert "NaviLaneItem#h()Z" in allowed
  assert "AutoRouteGuidanceStore" not in allowed
  assert "androidx.car.app" not in allowed
  assert "getCoord" not in allowed
  assert "getLatLng" not in allowed
  assert "getPlaceId" not in allowed
  assert payload["queue_capacity"] == 16
  assert payload["receiver_host"] == "127.0.0.1"
  assert payload["receiver_port"] == 7712
  assert payload["socket_timeout_ms"] == 100
  assert payload["payload_dex_entry"] == "classes43.dex"


def test_offline_capture_profile_is_fixed_and_channel_aware():
  profile = json.loads(PROFILE_PATH.read_text(encoding="utf-8"))
  offline = profile["diagnostic_payload"]["offline_capture"]
  assert offline == {
    "batch_size": 32,
    "database_name": "capture-v3.sqlite3",
    "database_recovery_name": "capture-v3-recovery.sqlite3",
    "directory_name": "naver-diagnostic",
    "flush_interval_ms": 250,
    "journal_size_limit_bytes": 4_194_304,
    "local_queue_capacity": 256,
    "max_database_bytes": 67_108_864,
    "network_queue_capacity": 64,
    "payload_build_id": "naver-6.8.0.5-diagnostic-offline-v3",
    "pinned_per_channel": 64,
    "retry_initial_ms": 1_000,
    "retry_max_ms": 30_000,
    "schema_version": 1,
    "wal_autocheckpoint_pages": 256,
    "channel_quotas": {
      "status": 256,
      "route": 256,
      "tbt_current": 1_536,
      "tbt_next": 1_536,
      "lane": 1_536,
      "safety": 6_144,
    },
  }
  assert sum(offline["channel_quotas"].values()) == 11_264


def test_capture_sharing_profile_is_exact_and_separate_from_seven_navigation_hooks():
  profile = json.loads(PROFILE_PATH.read_text(encoding="utf-8"))
  sharing = profile["diagnostic_payload"]["capture_sharing"]

  assert len(profile["anchors"]) == 7
  assert sharing == {
    "authority": "com.nhn.android.nmap.fileprovider",
    "external_files_root_path": "NaverNavi",
    "export_database_name": "capture-v3-export.sqlite3",
    "file_provider_root_name": "navi_trace",
    "grantee_package": "com.android.shell",
    "quiesce_timeout_ms": 5_000,
    "infrastructure_anchor": {
      "name": "capture_export_quiesce",
      "dex_entry": "classes.dex",
      "class_descriptor": "Landroidx/core/content/FileProvider;",
      "method_descriptor": (
        "openFile(Landroid/net/Uri;Ljava/lang/String;)"
        "Landroid/os/ParcelFileDescriptor;"
      ),
      "instruction_window": [
        (
          "invoke-virtual Landroidx/core/content/FileProvider;->f()"
          "Landroidx/core/content/FileProvider$PathStrategy;"
        ),
        "move-result-object",
        (
          "invoke-interface Landroidx/core/content/FileProvider$PathStrategy;"
          "->b(Landroid/net/Uri;)Ljava/io/File;"
        ),
      ],
      "expected_count": 1,
      "diagnostic_hook": {
        "argument": {"index": 0, "kind": "parameter"},
        "method_descriptor": "beforeCaptureRead(Ljava/lang/Object;)V",
        "placement": {"index": 0, "kind": "method_entry"},
      },
    },
  }
