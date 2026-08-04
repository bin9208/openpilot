from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError
import math
import threading

import pytest

from openpilot.selfdrive.carrot.navigation_sources import (
  CARROT_NAVI_V2_LEASE_S,
  NavigationControlState,
  NavigationInstruction,
  NavigationLifecycle,
  NavigationSelection,
  NavigationSnapshot,
  NavigationSource,
  NavigationSourceStore,
  SafetyDecision,
  SafetyItem,
  choose_safety,
)


def _control(received_mono_s: float, *, safety: bool = True,
             road_category: int | None = 8,
             safety_received_mono_s: float | None = None,
             category_received_mono_s: float | None = None) -> NavigationControlState:
  safety_item = None
  if safety:
    safety_item = SafetyItem(
      type=1,
      distance_m=420.0,
      speed_limit_kph=60.0,
      received_mono_s=received_mono_s if safety_received_mono_s is None else safety_received_mono_s,
    )
  return NavigationControlState(
    safety=safety_item,
    road_category=road_category,
    road_category_received_mono_s=(
      received_mono_s if category_received_mono_s is None else category_received_mono_s
    ) if road_category is not None else None,
  )


def _snapshot(source: NavigationSource, session_id: str, sequence: int, received_mono_s: float,
              *, lifecycle: NavigationLifecycle = NavigationLifecycle.GUIDING,
              control: NavigationControlState | None = None,
              activation_epoch: int = 0) -> NavigationSnapshot:
  return NavigationSnapshot(
    source=source,
    session_id=session_id,
    sequence=sequence,
    lifecycle=lifecycle,
    received_mono_s=received_mono_s,
    activation_epoch=activation_epoch,
    control=_control(received_mono_s) if control is None else control,
  )


def _terminal(source: NavigationSource, session_id: str, sequence: int, received_mono_s: float,
              lifecycle: NavigationLifecycle = NavigationLifecycle.ARRIVED) -> NavigationSnapshot:
  return _snapshot(
    source,
    session_id,
    sequence,
    received_mono_s,
    lifecycle=lifecycle,
    control=NavigationControlState(),
  )


def test_public_domain_values_are_frozen_and_slotted():
  safety = SafetyItem(type=1, distance_m=100.0, speed_limit_kph=60.0, received_mono_s=1.0)
  control = NavigationControlState(safety=safety)
  snapshot = _snapshot(NavigationSource.NAVER_V1, "immutable", 1, 1.0, control=control)
  selection = NavigationSelection(
    snapshot=snapshot,
    reason="test",
    owner_age_s=0.0,
    safety_age_s=0.0,
    road_category_age_s=None,
    projection_revision=1,
  )
  decision = SafetyDecision(
    provider="naver_v1",
    reason="cam",
    limit_kph=60.0,
    distance_m=100.0,
    type=1,
  )

  for value in (safety, control, snapshot, selection, decision):
    assert not hasattr(value, "__dict__")
    with pytest.raises(FrozenInstanceError):
      setattr(value, value.__slots__[0], None)


def test_new_guiding_activation_takes_owner_without_frame_order_flapping():
  store = NavigationSourceStore()
  assert store.accept(_snapshot(NavigationSource.TMAP_LEGACY, "tmap", 1, 1.0), 1.0)
  first = store.select(1.1)
  assert first.snapshot is not None
  assert first.snapshot.source is NavigationSource.TMAP_LEGACY

  assert store.accept(_snapshot(NavigationSource.NAVER_V1, "naver", 1, 1.2), 1.2)
  second = store.select(1.3)
  assert second.snapshot is not None
  assert second.snapshot.source is NavigationSource.NAVER_V1
  assert second.reason == "new_activation"

  assert store.accept(_snapshot(NavigationSource.TMAP_LEGACY, "tmap", 2, 1.4), 1.4)
  selected = store.select(1.5)
  assert selected.snapshot is not None
  assert selected.snapshot.source is NavigationSource.NAVER_V1


def test_activation_epoch_changes_only_for_activation_or_new_session():
  store = NavigationSourceStore()
  assert store.accept(_snapshot(NavigationSource.TMAP_LEGACY, "route-a", 1, 1.0), 1.0)
  epoch_a = store.select(1.0).snapshot.activation_epoch

  assert store.accept(_snapshot(NavigationSource.TMAP_LEGACY, "route-a", 2, 1.1), 1.1)
  assert store.select(1.1).snapshot.activation_epoch == epoch_a

  assert store.accept(_snapshot(NavigationSource.TMAP_LEGACY, "route-b", 1, 1.2), 1.2)
  replacement = store.select(1.2)
  assert replacement.snapshot is not None
  assert replacement.snapshot.activation_epoch == epoch_a + 1


@pytest.mark.parametrize(("source", "lease_s"), [
  (NavigationSource.NAVER_V1, 2.0),
  (NavigationSource.CARROT_NAVI_V2, 10.0),
])
def test_stale_same_session_nonlegacy_update_does_not_create_activation(source, lease_s):
  store = NavigationSourceStore()
  assert store.accept(_snapshot(source, "background", 1, 0.0), 0.0)
  assert store.select(0.0).snapshot.source is source
  assert store.accept(_snapshot(NavigationSource.TMAP_LEGACY, "owner", 1, 0.5), 0.5)
  assert store.select(0.5).snapshot.source is NavigationSource.TMAP_LEGACY

  resume_time = lease_s + 0.1
  assert store.accept(_snapshot(NavigationSource.TMAP_LEGACY, "owner", 2, resume_time - 0.1), resume_time - 0.1)
  assert store.select(resume_time - 0.1).snapshot.source is NavigationSource.TMAP_LEGACY
  assert store.accept(_snapshot(source, "background", 2, resume_time), resume_time)
  assert store.select(resume_time).snapshot.source is NavigationSource.TMAP_LEGACY


@pytest.mark.parametrize("reverse_order", [False, True])
def test_equal_time_activation_uses_stable_source_order(reverse_order):
  store = NavigationSourceStore()
  candidates = [
    _snapshot(NavigationSource.TMAP_LEGACY, "tmap", 1, 1.0),
    _snapshot(NavigationSource.NAVER_V1, "naver", 1, 1.0),
  ]
  if reverse_order:
    candidates.reverse()
  for candidate in candidates:
    assert store.accept(candidate, 1.0)

  selection = store.select(1.0)
  assert selection.snapshot is not None
  assert selection.snapshot.source is NavigationSource.TMAP_LEGACY


@pytest.mark.parametrize(("source", "lease_s"), [
  (NavigationSource.NAVER_V1, 2.0),
  (NavigationSource.TMAP_LEGACY, 4.0),
  (NavigationSource.CARROT_NAVI_V2, 10.0),
])
def test_source_leases_expire_at_the_exact_boundary(source, lease_s):
  store = NavigationSourceStore()
  assert store.accept(_snapshot(source, "lease", 1, 5.0), 5.0)
  assert store.select(5.0 + lease_s - 0.001).snapshot is not None

  expired = store.select(5.0 + lease_s)
  assert expired.snapshot is None
  assert expired.reason == "owner_expired"


@pytest.mark.parametrize("resume_time", [7.0, 7.1])
def test_frame_at_lease_boundary_cannot_bypass_logical_owner_release(resume_time):
  store = NavigationSourceStore()
  assert store.accept(_snapshot(NavigationSource.NAVER_V1, "resume", 1, 5.0), 5.0)
  initial = store.select(5.0)
  assert initial.snapshot is not None
  initial_epoch = initial.snapshot.activation_epoch

  assert store.accept(_snapshot(NavigationSource.NAVER_V1, "resume", 2, resume_time), resume_time)
  resumed = store.select(resume_time)
  assert resumed.snapshot is not None
  assert resumed.snapshot.session_id == "resume"
  assert resumed.snapshot.activation_epoch == initial_epoch
  assert resumed.reason == "owner_expired"
  assert store.select(resume_time).reason == "owner_sticky"


def test_frame_at_lease_boundary_falls_back_before_same_session_resume():
  store = NavigationSourceStore()
  assert store.accept(_snapshot(NavigationSource.TMAP_LEGACY, "fallback", 1, 4.5), 4.5)
  assert store.select(4.5).snapshot.source is NavigationSource.TMAP_LEGACY
  assert store.accept(_snapshot(NavigationSource.NAVER_V1, "resume", 1, 5.0), 5.0)
  assert store.select(5.0).snapshot.source is NavigationSource.NAVER_V1

  assert store.accept(_snapshot(NavigationSource.NAVER_V1, "resume", 2, 7.0), 7.0)
  fallback = store.select(7.0)
  assert fallback.snapshot is not None
  assert fallback.snapshot.source is NavigationSource.TMAP_LEGACY
  assert fallback.reason == "owner_expired"
  assert store.select(7.0).snapshot.source is NavigationSource.TMAP_LEGACY


def test_local_accept_time_is_the_only_owner_freshness_clock():
  store = NavigationSourceStore()
  candidate = _snapshot(NavigationSource.NAVER_V1, "local-clock", 1, 1_000_000.0)
  assert store.accept(candidate, 5.0)

  selected = store.select(6.0)
  assert selected.snapshot is not None
  assert selected.snapshot.received_mono_s == 5.0
  assert selected.owner_age_s == pytest.approx(1.0)
  assert store.select(7.0).snapshot is None


@pytest.mark.parametrize("invalid", [math.nan, math.inf, -math.inf])
def test_non_finite_receipt_times_are_rejected_without_mutation(invalid):
  store = NavigationSourceStore()
  original = _snapshot(NavigationSource.NAVER_V1, "finite", 1, 1.0)
  assert store.accept(original, 1.0)

  bad_candidate = _snapshot(NavigationSource.NAVER_V1, "finite", 2, invalid)
  assert not store.accept(bad_candidate, 1.1)
  assert not store.accept(_snapshot(NavigationSource.NAVER_V1, "finite", 2, 1.1), invalid)
  selected = store.select(1.1)
  assert selected.snapshot is not None
  assert selected.snapshot.sequence == 1


def test_select_rejects_non_finite_clock_values():
  store = NavigationSourceStore()
  with pytest.raises(ValueError, match="finite"):
    store.select(math.nan)


def test_duplicate_and_backward_sequences_are_rejected_without_mutation():
  store = NavigationSourceStore()
  first = _snapshot(NavigationSource.NAVER_V1, "sequence", 10, 1.0)
  assert store.accept(first, 1.0)
  assert not store.accept(_snapshot(NavigationSource.NAVER_V1, "sequence", 10, 1.1), 1.1)
  assert not store.accept(_snapshot(NavigationSource.NAVER_V1, "sequence", 9, 1.2), 1.2)

  selected = store.select(1.2)
  assert selected.snapshot is not None
  assert selected.snapshot.sequence == 10
  assert selected.snapshot.received_mono_s == 1.0


def test_terminal_owner_falls_back_atomically():
  store = NavigationSourceStore()
  assert store.accept(_snapshot(NavigationSource.TMAP_LEGACY, "tmap", 1, 1.0), 1.0)
  assert store.accept(_snapshot(NavigationSource.NAVER_V1, "naver", 1, 1.2), 1.2)
  assert store.accept(_snapshot(NavigationSource.TMAP_LEGACY, "tmap", 2, 1.9), 1.9)
  assert store.select(1.9).snapshot.source is NavigationSource.NAVER_V1

  assert store.accept(_terminal(NavigationSource.NAVER_V1, "naver", 2, 2.0), 2.0)
  selection = store.select(2.0)
  assert selection.snapshot is not None
  assert selection.snapshot.source is NavigationSource.TMAP_LEGACY
  assert selection.snapshot.control.safety is not None
  assert selection.reason == "owner_terminal"


def test_expiry_fallback_is_sticky_against_already_known_activation_epochs():
  store = NavigationSourceStore()
  assert store.accept(_snapshot(NavigationSource.TMAP_LEGACY, "tmap", 1, 0.0), 0.0)
  assert store.select(0.0).snapshot.source is NavigationSource.TMAP_LEGACY
  assert store.accept(_snapshot(NavigationSource.CARROT_NAVI_V2, "v2", 1, 0.5), 0.5)
  assert store.accept(_snapshot(NavigationSource.NAVER_V1, "naver", 1, 0.6), 0.6)
  assert store.select(0.6).snapshot.source is NavigationSource.NAVER_V1
  assert store.accept(_snapshot(NavigationSource.TMAP_LEGACY, "tmap", 2, 1.0), 1.0)

  fallback = store.select(2.6)
  assert fallback.snapshot is not None
  assert fallback.snapshot.source is NavigationSource.TMAP_LEGACY
  assert fallback.reason == "owner_expired"
  assert store.select(2.6).snapshot.source is NavigationSource.TMAP_LEGACY


def test_new_equal_time_activation_after_fallback_uses_stable_source_order():
  store = NavigationSourceStore()
  assert store.accept(_snapshot(NavigationSource.NAVER_V1, "expired-owner", 1, 0.0), 0.0)
  assert store.select(0.0).snapshot.source is NavigationSource.NAVER_V1

  assert store.accept(_snapshot(NavigationSource.CARROT_NAVI_V2, "fallback", 1, 2.0), 2.0)
  fallback = store.select(2.0)
  assert fallback.snapshot is not None
  assert fallback.snapshot.source is NavigationSource.CARROT_NAVI_V2
  assert fallback.reason == "owner_expired"

  assert store.accept(_snapshot(NavigationSource.TMAP_LEGACY, "new-at-boundary", 1, 2.0), 2.0)
  selected = store.select(2.0)
  assert selected.snapshot is not None
  assert selected.snapshot.source is NavigationSource.TMAP_LEGACY
  assert selected.reason == "new_activation"


def test_transport_loss_preserves_exact_session_until_lease_expiry():
  store = NavigationSourceStore()
  assert store.accept(_snapshot(NavigationSource.NAVER_V1, "lease", 1, 5.0), 5.0)
  assert store.record_transport_loss(NavigationSource.NAVER_V1, "lease", 5.1)

  selected = store.select(6.999)
  assert selected.snapshot is not None
  assert selected.snapshot.session_id == "lease"
  assert selected.transport_loss_age_s == pytest.approx(1.899)
  assert store.select(7.0).snapshot is None


def test_transport_loss_from_old_session_cannot_affect_new_session():
  store = NavigationSourceStore()
  assert store.accept(_snapshot(NavigationSource.NAVER_V1, "old", 1, 4.9), 4.9)
  assert store.accept(_snapshot(NavigationSource.NAVER_V1, "new", 1, 5.0), 5.0)
  assert not store.record_transport_loss(NavigationSource.NAVER_V1, "old", 5.1)

  selected = store.select(5.1)
  assert selected.snapshot is not None
  assert selected.snapshot.session_id == "new"
  assert selected.transport_loss_age_s is None


def test_terminal_from_old_session_tombstones_only_that_session():
  store = NavigationSourceStore()
  assert store.accept(_snapshot(NavigationSource.NAVER_V1, "old", 1, 4.9), 4.9)
  assert store.accept(_snapshot(NavigationSource.NAVER_V1, "new", 1, 5.0), 5.0)
  assert store.select(5.0).snapshot.session_id == "new"

  assert store.accept(_terminal(NavigationSource.NAVER_V1, "old", 2, 5.1), 5.1)
  selected = store.select(5.1)
  assert selected.snapshot is not None
  assert selected.snapshot.session_id == "new"
  assert not store.accept(_snapshot(NavigationSource.NAVER_V1, "old", 3, 5.2), 5.2)


@pytest.mark.parametrize("lifecycle", [NavigationLifecycle.STOPPED, NavigationLifecycle.ARRIVED])
def test_terminal_session_is_tombstoned_even_when_sequence_increases(lifecycle):
  store = NavigationSourceStore()
  assert store.accept(_snapshot(NavigationSource.NAVER_V1, "ended", 1, 5.0), 5.0)
  assert store.accept(_terminal(NavigationSource.NAVER_V1, "ended", 2, 5.1, lifecycle), 5.1)
  assert not store.accept(_snapshot(NavigationSource.NAVER_V1, "ended", 3, 5.2), 5.2)

  assert store.accept(_snapshot(NavigationSource.NAVER_V1, "replacement", 1, 5.3), 5.3)
  selected = store.select(5.3)
  assert selected.snapshot is not None
  assert selected.snapshot.session_id == "replacement"


def test_item_ages_and_projection_revision_advance_on_independent_expiry():
  store = NavigationSourceStore()
  control = _control(
    5.0,
    safety_received_mono_s=1.0,
    category_received_mono_s=2.0,
  )
  assert store.accept(
    _snapshot(NavigationSource.CARROT_NAVI_V2, "items", 7, 5.0, control=control),
    5.0,
  )

  before = store.select(10.999)
  assert before.snapshot is not None
  assert before.snapshot.control.safety is not None
  assert before.snapshot.control.road_category == 8
  assert before.owner_age_s == pytest.approx(5.999)
  assert before.safety_age_s == pytest.approx(9.999)
  assert before.road_category_age_s == pytest.approx(8.999)
  assert store.select(10.999).projection_revision == before.projection_revision

  safety_expired = store.select(11.0)
  assert safety_expired.snapshot is not None
  assert safety_expired.snapshot.sequence == 7
  assert safety_expired.snapshot.control.safety is None
  assert safety_expired.snapshot.control.road_category == 8
  assert safety_expired.safety_age_s == pytest.approx(10.0)
  assert safety_expired.projection_revision == before.projection_revision + 1

  category_expired = store.select(12.0)
  assert category_expired.snapshot is not None
  assert category_expired.snapshot.sequence == 7
  assert category_expired.snapshot.control.road_category is None
  assert category_expired.road_category_age_s == pytest.approx(10.0)
  assert category_expired.projection_revision == safety_expired.projection_revision + 1


def test_all_projected_items_expire_at_the_exact_source_lease_boundary():
  store = NavigationSourceStore()
  receipt = 100.0
  control = NavigationControlState(
    current=NavigationInstruction(
      present=True, turn_type=12, distance_m=300.0, received_mono_s=receipt,
    ),
    next=NavigationInstruction(
      present=True, turn_type=13, distance_m=900.0, received_mono_s=receipt,
    ),
    safety=SafetyItem(1, 250.0, 60.0, receipt),
    secondary_safety=SafetyItem(22, 80.0, 20.0, receipt),
    road_limit_kph=80.0,
    road_limit_received_mono_s=receipt,
    road_category=8,
    road_category_received_mono_s=receipt,
    route_present=True,
    route_received_mono_s=receipt,
    remaining_distance_m=12_500.0,
    remaining_time_s=1_200.0,
    route_points=((37.5, 127.1),),
  )
  assert store.accept(_snapshot(
    NavigationSource.CARROT_NAVI_V2, "v2-items", 1, receipt, control=control,
  ), receipt)
  assert store.accept(_snapshot(
    NavigationSource.CARROT_NAVI_V2, "v2-items", 2, receipt + 9.0, control=control,
  ), receipt + 9.0)

  before = store.select(receipt + CARROT_NAVI_V2_LEASE_S - 0.001)
  assert before.snapshot is not None
  assert before.snapshot.control.current.present
  assert before.snapshot.control.next.present
  assert before.snapshot.control.safety is not None
  assert before.snapshot.control.secondary_safety is not None
  assert before.snapshot.control.road_limit_kph == 80.0
  assert before.snapshot.control.road_category == 8
  assert before.snapshot.control.route_present

  expired = store.select(receipt + CARROT_NAVI_V2_LEASE_S)
  assert expired.snapshot is not None
  assert not expired.snapshot.control.current.present
  assert not expired.snapshot.control.next.present
  assert expired.snapshot.control.safety is None
  assert expired.snapshot.control.secondary_safety is None
  assert expired.snapshot.control.road_limit_kph is None
  assert expired.snapshot.control.road_category is None
  assert not expired.snapshot.control.route_present
  assert expired.projection_revision == before.projection_revision + 1


def test_choose_safety_exposes_minimal_navigation_then_hda_interface():
  store = NavigationSourceStore()
  assert store.accept(_snapshot(NavigationSource.NAVER_V1, "safety", 1, 1.0), 1.0)

  navigation = choose_safety(store.select(1.0), hda_limit_kph=40.0, hda_distance_m=100.0)
  assert navigation is not None
  assert (navigation.provider, navigation.reason) == ("naver_v1", "cam")
  assert (navigation.limit_kph, navigation.distance_m, navigation.type) == (60.0, 420.0, 1)

  hda = choose_safety(NavigationSourceStore().select(1.0), 40.0, 100.0)
  assert hda is not None
  assert (hda.provider, hda.reason, hda.limit_kph, hda.distance_m) == ("hda", "hda", 40.0, 100.0)


@pytest.mark.parametrize(("overrides", "rejection"), [
  ({"type": True}, "invalid_item"),
  ({"type": 1.0}, "invalid_item"),
  ({"type": "1"}, "invalid_item"),
  ({"type": 99}, "unsupported_type"),
  ({"speed_limit_kph": math.nan}, "invalid_item"),
  ({"speed_limit_kph": math.inf}, "invalid_item"),
  ({"speed_limit_kph": 0.0}, "invalid_item"),
  ({"speed_limit_kph": "60"}, "invalid_item"),
  ({"distance_m": math.nan}, "invalid_distance"),
  ({"distance_m": math.inf}, "invalid_distance"),
  ({"distance_m": 0.0}, "invalid_distance"),
  ({"distance_m": "100"}, "invalid_distance"),
])
def test_invalid_or_unsupported_navigation_safety_falls_back_to_hda(overrides, rejection):
  values = {
    "type": 1,
    "distance_m": 100.0,
    "speed_limit_kph": 60.0,
    "received_mono_s": 1.0,
  }
  values.update(overrides)
  control = NavigationControlState(safety=SafetyItem(**values))
  store = NavigationSourceStore()
  assert store.accept(
    _snapshot(NavigationSource.NAVER_V1, "invalid-safety", 1, 1.0, control=control),
    1.0,
  )

  decision = choose_safety(store.select(1.0), hda_limit_kph=40.0, hda_distance_m=100.0)
  assert decision is not None
  assert (decision.provider, decision.reason) == ("hda", "hda")
  assert decision.rejection == rejection


def test_invalid_navigation_safety_without_valid_hda_returns_none():
  control = NavigationControlState(safety=SafetyItem(
    type=99,
    distance_m=100.0,
    speed_limit_kph=60.0,
    received_mono_s=1.0,
  ))
  store = NavigationSourceStore()
  assert store.accept(
    _snapshot(NavigationSource.NAVER_V1, "unsupported", 1, 1.0, control=control),
    1.0,
  )
  assert choose_safety(store.select(1.0), hda_limit_kph=0.0, hda_distance_m=100.0) is None


def test_concurrent_accept_and_select_is_atomic_for_one_thousand_updates():
  store = NavigationSourceStore()
  race_start = threading.Barrier(2)
  race_done = threading.Barrier(2)
  failures: list[str] = []

  def update() -> None:
    for sequence in range(1, 1001):
      control = NavigationControlState(
        safety=SafetyItem(
          type=sequence,
          distance_m=float(sequence),
          speed_limit_kph=float(sequence),
          received_mono_s=1.0,
        ),
        road_category=sequence,
        road_category_received_mono_s=1.0,
      )
      race_start.wait()
      if not store.accept(
        _snapshot(
          NavigationSource.CARROT_NAVI_V2,
          "concurrent",
          sequence,
          1.0,
          control=control,
        ),
        1.0,
      ):
        failures.append(f"rejected sequence {sequence}")
      race_done.wait()

  def select() -> None:
    for _ in range(1000):
      race_start.wait()
      selection = store.select(1.0)
      if selection.snapshot is not None:
        snapshot = selection.snapshot
        safety = snapshot.control.safety
        correlated = safety is not None and (
          snapshot.sequence == safety.type
          and snapshot.sequence == safety.distance_m
          and snapshot.sequence == safety.speed_limit_kph
          and snapshot.sequence == snapshot.control.road_category
        )
        if snapshot.session_id != "concurrent" or not correlated:
          failures.append("observed partial snapshot")
      race_done.wait()

  with ThreadPoolExecutor(max_workers=2) as executor:
    update_future = executor.submit(update)
    select_future = executor.submit(select)
    update_future.result()
    select_future.result()

  assert failures == []
  final = store.select(1.0)
  assert final.snapshot is not None
  assert final.snapshot.sequence == 1000
