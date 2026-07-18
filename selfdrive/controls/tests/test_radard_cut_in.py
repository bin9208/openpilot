from types import SimpleNamespace

import pytest
import cereal.messaging as messaging
from cereal import log

from openpilot.selfdrive.controls.radard import (
  RADAR_TO_CAMERA,
  RadarD,
  SccFallbackState,
  Track,
  get_cut_in_prediction_horizon,
  get_scc_fallback_evidence,
)


def make_track() -> Track:
  track = Track(1)
  track.dRel = 20.0
  track.vLead = 15.0
  track.vLeadK = 15.0
  track.vRel = 0.0
  track.yRel = 2.0
  track.aLead = 0.0
  track.aLeadK = 0.0
  track.jLead = 0.0
  track.yvLead = -0.5
  track.cnt = 10
  track.dPath = 2.0
  track.dPath_future = 1.0
  track.in_lane_prob = 0.10
  track.in_lane_prob_future = 0.20
  track.in_lane_prob_expanded = 0.10
  track.in_lane_prob_future_expanded = 0.20
  return track


def make_scc_track(*, d_rel: float = 7.2, v_rel: float = -6.7) -> Track:
  track = make_track()
  track.identifier = 0
  track.dRel = d_rel
  track.vRel = v_rel
  track.vLead = 0.0
  track.vLeadK = 0.0
  track.yRel = 0.05
  track.dPath = 0.10
  track.in_lane_prob = 0.80
  return track


def make_vision_lead(*, d_rel: float = 7.2):
  return SimpleNamespace(
    x=[d_rel + RADAR_TO_CAMERA],
    xStd=[0.7],
    y=[-0.05],
    yStd=[0.25],
  )


def test_scc_fallback_requires_three_consistent_frames() -> None:
  state = SccFallbackState()

  assert not state.update(candidate_ok=True, urgent_ok=False, continuous=True, alternative_stable=False)
  assert not state.update(candidate_ok=True, urgent_ok=False, continuous=True, alternative_stable=False)
  assert state.update(candidate_ok=True, urgent_ok=False, continuous=True, alternative_stable=False)


def test_scc_fallback_urgent_path_requires_two_frames() -> None:
  state = SccFallbackState()

  assert not state.update(candidate_ok=True, urgent_ok=True, continuous=True, alternative_stable=False)
  assert state.update(candidate_ok=True, urgent_ok=True, continuous=True, alternative_stable=False)


def test_scc_fallback_drops_discontinuous_selected_track() -> None:
  state = SccFallbackState()
  for _ in range(3):
    selected = state.update(candidate_ok=True, urgent_ok=False, continuous=True, alternative_stable=False)

  assert selected
  assert not state.update(candidate_ok=True, urgent_ok=False, continuous=False, alternative_stable=False)


def test_scc_fallback_waits_for_stable_alternative_before_switching() -> None:
  state = SccFallbackState()
  for _ in range(3):
    assert state.update(candidate_ok=True, urgent_ok=False, continuous=True, alternative_stable=False) == (_ == 2)

  assert state.update(candidate_ok=True, urgent_ok=False, continuous=True, alternative_stable=True)
  assert not state.update(candidate_ok=True, urgent_ok=False, continuous=True, alternative_stable=True)


def test_scc_fallback_evidence_rejects_distance_jump() -> None:
  track = make_scc_track(d_rel=15.6)
  evidence = get_scc_fallback_evidence(track, make_vision_lead(d_rel=15.6), lead_prob=0.85, previous_d_rel=5.6)

  assert evidence.candidate_ok
  assert not evidence.continuous


def test_scc_fallback_urgent_evidence_requires_strong_overlap_and_ttc() -> None:
  track = make_scc_track()
  evidence = get_scc_fallback_evidence(track, make_vision_lead(), lead_prob=0.85, previous_d_rel=7.5)

  assert evidence.candidate_ok
  assert evidence.continuous
  assert evidence.urgent_ok


def make_scc_radar() -> RadarD:
  radar = object.__new__(RadarD)
  radar.v_ego = 8.0
  radar.ready = True
  radar.enable_radar_tracks = 2
  radar.enable_corner_radar = 0
  radar.scc_fallback_state = SccFallbackState()
  radar.scc_fallback_d_rel = None
  vision_track = SimpleNamespace(yRel=0.0, get_lead=lambda _md: {"status": True, "radarTrackId": -1})
  radar.vision_tracks = [vision_track, vision_track]
  return radar


def test_get_lead_guards_scc_without_mutating_shared_tracks() -> None:
  radar = make_scc_radar()
  track = make_scc_track(v_rel=-1.0)
  tracks = {0: track}
  md = SimpleNamespace(meta=SimpleNamespace(laneChangeState=log.LaneChangeState.laneChangeStarting))
  lead = make_vision_lead()

  for _ in range(2):
    _, radar_selected = radar.get_lead(SimpleNamespace(), md, tracks, 0, lead, 8.0, 0.85, low_speed_override=False)
    assert not radar_selected
    assert 0 in tracks

  lead_dict, radar_selected = radar.get_lead(SimpleNamespace(), md, tracks, 0, lead, 8.0, 0.85, low_speed_override=False)
  assert radar_selected
  assert lead_dict["radarTrackId"] == 0
  assert 0 in tracks


def test_get_lead_drops_selected_scc_on_distance_jump() -> None:
  radar = make_scc_radar()
  track = make_scc_track(v_rel=-1.0)
  tracks = {0: track}
  md = SimpleNamespace(meta=SimpleNamespace(laneChangeState=log.LaneChangeState.laneChangeStarting))
  for _ in range(3):
    _, radar_selected = radar.get_lead(SimpleNamespace(), md, tracks, 0, make_vision_lead(), 8.0, 0.85, low_speed_override=False)
  assert radar_selected

  track.dRel = 15.6
  _, radar_selected = radar.get_lead(SimpleNamespace(), md, tracks, 0, make_vision_lead(d_rel=15.6), 8.0, 0.85, low_speed_override=False)
  assert not radar_selected


def make_radar(*, lane_line_available: bool) -> RadarD:
  radar = object.__new__(RadarD)
  radar.lane_line_available = lane_line_available
  radar.lane_marking_cut_in_available = False
  return radar


def make_model():
  model_message = messaging.new_message("modelV2")
  model_message.modelV2.position.x = [0.0] * 33
  model_message.modelV2.init("leadsV3", 1)
  model_message.modelV2.leadsV3[0].y = [0.0]
  return model_message.modelV2


def make_compute_radar(*, lane_line_available: bool) -> RadarD:
  radar = make_radar(lane_line_available=lane_line_available)
  radar.radar_state = log.RadarState.new_message()
  return radar


def test_close_cut_in_candidate_with_lane_evidence() -> None:
  assert make_radar(lane_line_available=True)._cut_in_candidate(make_track(), "left")


def test_lane_unavailable_fallback_accepts_strong_boundary_overlap() -> None:
  track = make_track()
  track.dPath = 1.4
  track.dPath_future = 2.5
  track.in_lane_prob = 0.22
  track.in_lane_prob_future = 0.0

  assert make_radar(lane_line_available=False)._cut_in_candidate(track, "left")


def test_lane_unavailable_fallback_rejects_expanded_only_distant_track() -> None:
  track = make_track()
  track.dPath = 3.3
  track.dPath_future = 2.2
  track.in_lane_prob = 0.0
  track.in_lane_prob_future = 0.0
  track.in_lane_prob_expanded = 0.20
  track.in_lane_prob_future_expanded = 0.20

  assert not make_radar(lane_line_available=False)._cut_in_candidate(track, "left")


def test_lane_evidence_rejects_weak_expanded_overlap() -> None:
  track = make_track()
  track.dPath = 2.86
  track.dPath_future = 2.28
  track.in_lane_prob = 0.0
  track.in_lane_prob_future = 0.0
  track.in_lane_prob_expanded = 0.0
  track.in_lane_prob_future_expanded = 0.16

  assert not make_radar(lane_line_available=True)._cut_in_candidate(track, "left")


def test_lane_evidence_accepts_strong_expanded_approach() -> None:
  track = make_track()
  track.dRel = 32.0
  track.dPath = -2.73
  track.dPath_future = -1.92
  track.in_lane_prob = 0.0
  track.in_lane_prob_future = 0.0
  track.in_lane_prob_expanded = 0.0
  track.in_lane_prob_future_expanded = 0.34

  assert make_radar(lane_line_available=True)._cut_in_candidate(track, "right")


def test_lane_unavailable_fallback_accepts_strong_expanded_approach() -> None:
  track = make_track()
  track.dRel = 32.0
  track.dPath = -2.73
  track.dPath_future = -1.92
  track.in_lane_prob = 0.0
  track.in_lane_prob_future = 0.0
  track.in_lane_prob_expanded = 0.0
  track.in_lane_prob_future_expanded = 0.34

  assert make_radar(lane_line_available=False)._cut_in_candidate(track, "right")


def test_cut_in_confirmation_requires_consecutive_frames() -> None:
  radar = make_radar(lane_line_available=True)
  track = make_track()

  assert not radar._update_cut_in_confirmation(track, True, close=True)
  assert not radar._update_cut_in_confirmation(track, False, close=True)
  assert not radar._update_cut_in_confirmation(track, True, close=True)
  assert radar._update_cut_in_confirmation(track, True, close=True)


def test_expanded_only_candidate_waits_for_center_entry() -> None:
  radar = make_radar(lane_line_available=True)
  track = make_track()
  track.dRel = 32.0
  track.dPath = -2.73
  track.dPath_future = -1.92
  track.in_lane_prob = 0.0
  track.in_lane_prob_future = 0.0
  track.in_lane_prob_expanded = 0.0
  track.in_lane_prob_future_expanded = 0.34

  candidate = radar._cut_in_candidate(track, "right")
  assert candidate
  assert not radar._confirmed_cut_in_ready(track, candidate, close=False)
  assert not radar._confirmed_cut_in_ready(track, candidate, close=False)
  assert not radar._confirmed_cut_in_ready(track, candidate, close=False)

  track.in_lane_prob = 0.31
  assert not radar._consume_cut_in_center_entry(track, candidate=True)
  assert radar._consume_cut_in_center_entry(track, candidate=True)
  assert track.cut_in_count == 0


def test_center_entry_revalidates_current_candidate() -> None:
  radar = make_radar(lane_line_available=True)
  track = make_track()
  track.cut_in_count = 3
  track.cut_in_center_count = 1
  track.in_lane_prob = 0.40

  assert not radar._consume_cut_in_center_entry(track, candidate=False)
  assert track.cut_in_count == 0
  assert track.cut_in_center_count == 0


def test_compute_leads_emits_confirmed_side_to_center_cut_in() -> None:
  radar = make_compute_radar(lane_line_available=True)
  assert radar.radar_state is not None
  radar_state = radar.radar_state
  track = make_track()
  track.dRel = 32.0
  track.dPath = -2.73
  track.dPath_future = -1.92
  track.yRel = -2.7
  track.in_lane_prob = 0.0
  track.in_lane_prob_future = 0.0
  track.in_lane_prob_expanded = 0.0
  track.in_lane_prob_future_expanded = 0.34
  tracks = {track.identifier: track}
  model = make_model()

  radar.compute_leads(20.0, tracks, model, 0.0)
  assert len(radar_state.leadsCutIn) == 0

  track.dPath = -1.0
  track.dPath_future = -0.7
  track.in_lane_prob = 0.31
  track.in_lane_prob_future = 0.50
  radar.compute_leads(20.0, tracks, model, 0.0)
  assert len(radar_state.leadsCutIn) == 0

  track.in_lane_prob = 0.32
  track.in_lane_prob_future = 0.31
  radar.compute_leads(20.0, tracks, model, 0.0)
  assert len(radar_state.leadsCutIn) == 1
  assert radar_state.leadsCutIn[0].radarTrackId == track.identifier


def test_far_track_requires_strong_direct_overlap() -> None:
  radar = make_radar(lane_line_available=True)
  track = make_track()
  track.dRel = 32.0
  track.in_lane_prob = 0.0
  track.in_lane_prob_future = 0.27

  assert not radar._direct_cut_in_overlap(track, close=False)

  track.in_lane_prob_future = 0.29
  assert not radar._direct_cut_in_overlap(track, close=False)

  track.in_lane_prob = 0.24
  assert not radar._direct_cut_in_overlap(track, close=False)

  track.in_lane_prob = 0.26
  assert radar._direct_cut_in_overlap(track, close=False)


def test_cut_in_confirmation_resets_when_model_input_is_missing() -> None:
  radar = make_compute_radar(lane_line_available=True)
  assert radar.radar_state is not None
  radar_state = radar.radar_state
  track = make_track()
  track.cut_in_count = 2
  track.cut_in_center_count = 1
  radar_state.init("leadsCutIn", 1)
  radar_state.leadsCutIn[0].status = True

  radar.compute_leads(20.0, {track.identifier: track}, None, 0.0)

  assert track.cut_in_count == 0
  assert track.cut_in_center_count == 0
  assert len(radar_state.leadsLeft) == 0
  assert len(radar_state.leadsCenter) == 0
  assert len(radar_state.leadsCutIn) == 0


def test_cut_in_prediction_horizon_is_bounded() -> None:
  assert get_cut_in_prediction_horizon(0) == 0.0
  assert get_cut_in_prediction_horizon(100) == 1.0
  assert get_cut_in_prediction_horizon(300) == 1.0


def test_future_path_uses_relative_longitudinal_speed() -> None:
  track = Track(1)
  point = SimpleNamespace(
    dRel=20.0,
    yRel=2.0,
    vRel=0.0,
    vLead=25.0,
    aLead=0.0,
    jLead=0.0,
    yvRel=0.0,
    measured=True,
  )
  model = SimpleNamespace(laneLines=[
    SimpleNamespace(x=[], y=[]),
    SimpleNamespace(x=[0.0, 100.0], y=[-1.8, -0.8]),
    SimpleNamespace(x=[0.0, 100.0], y=[1.8, 2.8]),
  ])

  track.update(model, point, ready=True, radar_reaction_factor=1.0, radar_lat_factor=1.0)

  assert track.dRel_future == pytest.approx(track.dRel)
  assert track.dPath_future == pytest.approx(track.dPath)
