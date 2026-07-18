#!/usr/bin/env python3
import math
import numpy as np
from collections import deque
from dataclasses import dataclass
from typing import Any
import heapq
import copy

import capnp
from cereal import messaging, log, car
from openpilot.common.filter_simple import FirstOrderFilter
from openpilot.common.params import Params
from openpilot.common.realtime import DT_MDL, Priority, config_realtime_process
from openpilot.common.swaglog import cloudlog
from openpilot.common.simple_kalman import KF1D


# Default lead acceleration decay set to 50% at 1s
_LEAD_ACCEL_TAU = 1.5

# radar tracks
SPEED, ACCEL = 0, 1     # Kalman filter states enum

# stationary qualification parameters
V_EGO_STATIONARY = 4.   # no stationary object flag below this speed

RADAR_TO_CENTER = 2.7   # (deprecated) RADAR is ~ 2.7m ahead from center of car
RADAR_TO_CAMERA = 1.52  # RADAR is ~ 1.5m ahead from center of mesh frame

CUT_IN_VEHICLE_HALF_WIDTH = 0.9
CUT_IN_MAX_DIST = 50.0
CUT_IN_CLOSE_DIST = 30.0
CUT_IN_MIN_TRACK_AGE = 0.20
CUT_IN_MIN_TRACK_AGE_CLOSE = 0.10
CUT_IN_PREDICTION_HORIZON_MAX = 1.0
LANE_MARKING_MIN_WIDTH = 2.3
LANE_MARKING_MAX_WIDTH = 4.8
LANE_MARKING_ALLOW_LABELS = frozenset({"white_dashed"})
LANE_MARKING_BLOCK_LABELS = frozenset({
  "road_edge_or_barrier",
  "white_solid",
  "yellow_solid",
  "yellow_double",
  "yellow_double_solid",
  "yellow_double_dashed",
})

VISION_MIN_X_STD = 1.5
VISION_MIN_Y_STD = 0.45
VISION_MIN_Y_STD_CUT_IN = 1.4
VISION_MIN_V_STD = 2.0

SCC_FALLBACK_CONFIRM_FRAMES = 3
SCC_FALLBACK_URGENT_CONFIRM_FRAMES = 2
SCC_FALLBACK_ALTERNATIVE_CONFIRM_FRAMES = 2
SCC_FALLBACK_MIN_PROB = 0.50
SCC_FALLBACK_URGENT_MIN_PROB = 0.75
SCC_FALLBACK_URGENT_MAX_TTC = 1.5


def laplacian_pdf(x: float, mu: float, b: float):
  diff = abs(x - mu) / max(b, 1e-4)
  return 0.0 if diff > 50.0 else math.exp(-diff)

def clamp(x: float, lo: float, hi: float) -> float:
  return float(np.clip(x, lo, hi))


def get_cut_in_prediction_horizon(radar_lat_factor_param: float) -> float:
  return clamp(float(radar_lat_factor_param) * 0.01, 0.0, CUT_IN_PREDICTION_HORIZON_MAX)


@dataclass(frozen=True)
class SccFallbackEvidence:
  candidate_ok: bool
  urgent_ok: bool
  continuous: bool
  ttc: float


class SccFallbackState:
  def __init__(self):
    self.candidate_count = 0
    self.urgent_count = 0
    self.alternative_count = 0
    self.selected = False

  def reset(self) -> None:
    self.candidate_count = 0
    self.urgent_count = 0
    self.alternative_count = 0
    self.selected = False

  def update(self, *, candidate_ok: bool, urgent_ok: bool, continuous: bool, alternative_stable: bool) -> bool:
    if not continuous:
      self.reset()
      return False

    if self.selected:
      if not candidate_ok:
        self.reset()
        return False

      self.alternative_count = self.alternative_count + 1 if alternative_stable else 0
      if self.alternative_count >= SCC_FALLBACK_ALTERNATIVE_CONFIRM_FRAMES:
        self.reset()
        return False
      return True

    if not candidate_ok or alternative_stable:
      self.candidate_count = 0
      self.urgent_count = 0
      return False

    self.candidate_count += 1
    self.urgent_count = self.urgent_count + 1 if urgent_ok else 0
    self.selected = (
      self.candidate_count >= SCC_FALLBACK_CONFIRM_FRAMES or
      self.urgent_count >= SCC_FALLBACK_URGENT_CONFIRM_FRAMES
    )
    return self.selected


def get_scc_fallback_evidence(track: "Track", lead: capnp._DynamicStructReader, lead_prob: float,
                              previous_d_rel: float | None) -> SccFallbackEvidence:
  try:
    vision_d_rel = float(lead.x[0]) - RADAR_TO_CAMERA
    vision_y_rel = -float(lead.y[0])
    x_std = max(float(lead.xStd[0]), VISION_MIN_X_STD)
    y_std = max(float(lead.yStd[0]), VISION_MIN_Y_STD)
  except (AttributeError, IndexError, TypeError, ValueError):
    return SccFallbackEvidence(False, False, previous_d_rel is None, math.inf)

  longitudinal_error = abs(float(track.dRel) - vision_d_rel)
  lateral_error = abs(float(track.yRel) - vision_y_rel)
  lane_evidence = max(
    float(getattr(track, "in_lane_prob", 0.0)),
    float(getattr(track, "in_lane_prob_future", 0.0)),
    float(getattr(track, "in_lane_prob_expanded", 0.0)),
    float(getattr(track, "in_lane_prob_future_expanded", 0.0)),
  )
  path_error = abs(float(getattr(track, "dPath", track.yRel)))

  geometry_ok = (
    longitudinal_error <= max(3.0, 2.0 * x_std) and
    lateral_error <= max(1.0, 2.0 * y_std) and
    (lane_evidence >= 0.25 or path_error <= 1.25)
  )
  candidate_ok = lead_prob >= SCC_FALLBACK_MIN_PROB and geometry_ok

  if previous_d_rel is None:
    continuous = True
  else:
    expected_d_rel = float(previous_d_rel) + float(track.vRel) * DT_MDL
    continuity_tolerance = max(2.0, 0.10 * max(float(track.dRel), float(previous_d_rel)))
    continuous = abs(float(track.dRel) - expected_d_rel) <= continuity_tolerance

  closing_speed = max(-float(track.vRel), 0.0)
  ttc = float(track.dRel) / closing_speed if closing_speed > 0.1 else math.inf
  strong_geometry = (
    longitudinal_error <= max(2.0, 1.5 * x_std) and
    lateral_error <= max(0.7, 1.5 * y_std) and
    (lane_evidence >= 0.50 or path_error <= 0.60)
  )
  urgent_ok = (
    lead_prob >= SCC_FALLBACK_URGENT_MIN_PROB and
    strong_geometry and
    float(track.dRel) <= 20.0 and
    float(track.vRel) < -1.0 and
    ttc <= SCC_FALLBACK_URGENT_MAX_TTC
  )
  return SccFallbackEvidence(candidate_ok, urgent_ok, continuous, ttc)


class Track:
  def __init__(self, identifier: int):
    self.identifier = identifier
    self.cnt = 0
    self.aLeadTau = FirstOrderFilter(_LEAD_ACCEL_TAU, 0.45, DT_MDL)

    self.is_stopped_car_count = 0
    self.selected_count = 0
    self.cut_in_count = 0
    self.cut_in_center_count = 0
    self.measured = False
    self.score = 0.0
    self.in_lane_prob = 0.0
    self.in_lane_prob_future = 0.0
    self.in_lane_prob_expanded = 0.0
    self.in_lane_prob_future_expanded = 0.0

    self.dPath = 0.0
    self.dPath_future = 0.0
    self.lane_half_width = 1.8

    # ---- noise filter state (new) ----
    self._vLead_last = 0.0
    self._vLead_filt = 0.0
    self._vLead_filt_init = False

  def update(self, md, pt, ready, radar_reaction_factor, radar_lat_factor):
    self.dRel = pt.dRel
    self.yRel = pt.yRel
    self.vRel = pt.vRel

    self.vLead = self.vLeadK = pt.vLead
    self.aLead = self.aLeadK = pt.aLead
    self.jLead = pt.jLead
    self.yvLead = pt.yvRel

    self.measured = pt.measured
    if not self.measured:
      self.cnt = 0
      # optional: also reset filter init when track is not measured
      self._vLead_filt_init = False

    self.yRel_future = self.yRel + self.yvLead * radar_lat_factor
    self.dRel_future = self.dRel + self.vRel * radar_lat_factor
    if ready:
      self.d_path(md)

    a_lead_threshold = 0.5 * radar_reaction_factor
    if abs(self.aLead) < a_lead_threshold and abs(self.jLead) < 0.5:
      self.aLeadTau.x = _LEAD_ACCEL_TAU * radar_reaction_factor
    else:
      self.aLeadTau.update(0.0)

    self.cnt += 1

  def d_path(self, md):
    lane_xs = md.laneLines[1].x
    left_ys = md.laneLines[1].y
    right_ys = md.laneLines[2].y

    def d_path_interp(dRel, yRel):
      left_lane_y = np.interp(dRel, lane_xs, left_ys)
      right_lane_y = np.interp(dRel, lane_xs, right_ys)
      center_y = (left_lane_y + right_lane_y) / 2.0
      lane_half_width = max(0.1, abs(right_lane_y - left_lane_y) / 2.0)
      dist_from_center = yRel + center_y
      in_lane_prob = max(0.0, 1.0 - (abs(dist_from_center) / lane_half_width))
      expanded_dist = max(0.0, abs(dist_from_center) - CUT_IN_VEHICLE_HALF_WIDTH)
      in_lane_prob_expanded = max(0.0, 1.0 - (expanded_dist / lane_half_width))
      return dist_from_center, in_lane_prob, in_lane_prob_expanded, lane_half_width

    self.dPath, self.in_lane_prob, self.in_lane_prob_expanded, self.lane_half_width = d_path_interp(self.dRel, self.yRel)
    self.dPath_future, self.in_lane_prob_future, self.in_lane_prob_future_expanded, _ = d_path_interp(self.dRel_future, self.yRel_future)

  # ---- noise suppression only when cnt>=2 ----
  def vlead_for_matching(self, dv_max: float = 4.0, alpha: float = 0.35) -> float:
    """
    Returns vLead to be used in matching score.
    - If cnt < 2: raw vLead (no filtering)
    - If cnt >= 2: clamp spike + IIR smooth
    """
    v = float(self.vLead)

    if self.cnt < 2:
      return v

    if not self._vLead_filt_init:
      self._vLead_last = v
      self._vLead_filt = v
      self._vLead_filt_init = True
      return v

    v_last = self._vLead_last
    self._vLead_last = v

    v_clamped = clamp(v, v_last - dv_max, v_last + dv_max)
    self._vLead_filt = alpha * v_clamped + (1.0 - alpha) * self._vLead_filt
    return float(self._vLead_filt)

  def get_RadarState(self, model_prob: float = 0.0, vision_y_rel=0.0):
    return {
      "dRel": float(self.dRel),
      "yRel": float(self.yRel) if self.yRel != 0.0 else vision_y_rel,
      "dPath": float(self.dPath),
      "vRel": float(self.vRel),
      "vLead": float(self.vLead),
      "vLeadK": float(self.vLeadK),
      "aLead": float(self.aLead),
      "aLeadK": float(self.aLeadK),
      "aLeadTau": float(self.aLeadTau.x),
      "jLead": float(self.jLead),
      "vLat": float(self.yvLead),
      "status": True,
      "fcw": self.is_potential_fcw(model_prob),
      "modelProb": model_prob,
      "radar": True,
      "radarTrackId": self.identifier,
      "score": self.score,
    }

  def get_CutInState(self, v_ego: float, model_prob: float = 0.03, vision_y_rel=0.0):
    lead = self.get_RadarState(model_prob, vision_y_rel)

    v_lead = max(0.0, self.vlead_for_matching(dv_max=3.0, alpha=0.35))
    lead["vLead"] = float(v_lead)
    lead["vLeadK"] = float(v_lead)
    lead["vRel"] = float(v_lead - v_ego)

    a_lead = 0.0 if abs(self.aLeadK) > 3.5 else clamp(self.aLeadK, -1.2, 0.8)
    lead["aLead"] = float(a_lead)
    lead["aLeadK"] = float(a_lead)
    lead["aLeadTau"] = _LEAD_ACCEL_TAU
    lead["jLead"] = 0.0
    return lead

  def potential_low_speed_lead(self, v_ego: float):
    return abs(self.yRel) < 1.0 and (v_ego < V_EGO_STATIONARY) and (0.75 < self.dRel < 25)

  def is_potential_fcw(self, model_prob: float):
    return model_prob > .9

  def __str__(self):
    return f"x: {self.dRel:4.1f}  y: {self.yRel:4.1f}  v: {self.vRel:4.1f}  a: {self.aLeadK:4.1f}"


def match_vision_to_track(v_ego: float, lead: capnp._DynamicStructReader, lead_prob: float, tracks: dict[int, Track]):
  if not tracks:
    return None

  offset_vision_dist = float(lead.x[0] - RADAR_TO_CAMERA)
  x_std = max(float(lead.xStd[0]), VISION_MIN_X_STD)
  y_std = max(float(lead.yStd[0]), VISION_MIN_Y_STD)
  y_std_cut_in = max(y_std * 2.0, VISION_MIN_Y_STD_CUT_IN)
  v_std = max(float(lead.vStd[0]), VISION_MIN_V_STD)

  # distance gates
  max_vision_dist  = max(offset_vision_dist * 1.25, 5.0)
  min_vision_dist  = max(offset_vision_dist * 0.80, 1.0)
  max_vision_dist2 = max(offset_vision_dist * 1.45, 5.0)
  min_vision_dist2 = 1.5

  # velocity tolerance (same intent)
  vel_tol = float(max(lead.v[0] * np.interp(lead_prob, [0.8, 0.98], [0.3, 0.5]), 5.0))
  # hard guardrail for moving-bias (prevents absurd match)
  vel_guard = max(vel_tol * 3.0, 20.0)

  def dist_sane(t: Track, wide: bool = False) -> bool:
    if wide:
      return (min_vision_dist2 < t.dRel < max_vision_dist2)
    return (min_vision_dist < t.dRel < max_vision_dist)

  def y_sane(t: Track, wide: bool = False) -> bool:
    lim = 4.0 if wide else 2.0
    return abs(t.yRel + float(lead.y[0])) < lim

  def vel_sane(t: Track) -> bool:
    """
    Keep your philosophy:
      - if it's moving, likely "the car we should read"
    but add guardrail and (optionally) in-lane preference.
    """
    v_vis = float(lead.v[0])
    v_trk = float(t.vLead)
    dv = abs(v_trk - v_vis)

    # normal strict check
    if dv < vel_tol:
      return True

    # moving-bias: allow more mismatch for moving objects,
    # but only within a reasonable guardrail.
    moving = (v_trk > 3.0)
    if not moving:
      return False

    if dv > vel_guard:
      return False

    # If in-lane probability exists (it does in your Track), use it as safety.
    # When it's clearly not in our lane, don't use moving-bias.
    # (This line is intentionally mild; you can tune 0.2~0.5)
    lane_evidence = max(
      getattr(t, "in_lane_prob", 0.0),
      getattr(t, "in_lane_prob_future", 0.0),
      getattr(t, "in_lane_prob_expanded", 0.0),
      getattr(t, "in_lane_prob_future_expanded", 0.0),
    )
    if hasattr(t, "dPath") and lane_evidence < 0.18:
      return False

    return True

  def score_pair(t: Track):
    """
    score1: normal yStd
    score2: wide yStd for cut-in
    NOTE: uses t.vlead_for_matching() only for scoring (cnt>=2 only).
    """
    pd = laplacian_pdf(float(t.dRel), offset_vision_dist, x_std)
    py = laplacian_pdf(float(t.yRel), -float(lead.y[0]), y_std)
    py2 = laplacian_pdf(float(t.yRel), -float(lead.y[0]), y_std_cut_in)

    v_use = float(t.vlead_for_matching())  # noise suppression only if cnt>=2
    pv = laplacian_pdf(v_use, float(lead.v[0]), v_std)

    s1 = pd * py * pv
    s2 = pd * py2 * pv
    return s1, s2

  # ---- pick best candidates (FIX: true 1st/2nd) ----
  first_track, second_track, extra_track = None, None, None
  first_score, second_score, extra_score = -1e18, -1e18, -1e18

  for t in tracks.values():
    s1, s2 = score_pair(t)
    t.score = s1

    if s1 > first_score:
      second_track, second_score = first_track, first_score
      first_track, first_score = t, s1
    elif s1 > second_score:
      second_track, second_score = t, s1

    if s2 > extra_score:
      extra_track, extra_score = t, s2

  if first_track is None:
    return None

  # ---- selection policy (same logic, cleaner & safer) ----
  best_track = None

  # A) normal match
  if first_score > 1e-6 and dist_sane(first_track) and vel_sane(first_track):
    select_second_track = False
    if second_track is not None and vel_sane(second_track) and second_track.in_lane_prob > 0.3:
      if second_track.cnt > 5 and offset_vision_dist * 0.5 < second_track.dRel < first_track.dRel:
        select_second_track = True
        
    if select_second_track:
      best_track = second_track
    elif y_sane(first_track):
      if lead_prob > 0.5:
        best_track = first_track
      elif lead_prob > 0.4 and first_track.selected_count > 0:
        best_track = first_track
    elif lead_prob > 0.6:
      best_track = first_track

  # B) stopped-car-like (only if not chosen yet)
  if best_track is None and first_score > 1e-6 and dist_sane(first_track) and y_sane(first_track, wide=True):
    if (second_track is not None and second_score > 1e-5 and
        dist_sane(second_track) and y_sane(second_track) and vel_sane(second_track)):
      best_track = second_track
    elif first_track.selected_count > 0:
      best_track = first_track
    else:
      first_track.is_stopped_car_count += 2
      if first_track.is_stopped_car_count > int(1.0 / DT_MDL):
        best_track = first_track

  # C) cut-in wide matching (only if not chosen yet)
  if best_track is None and offset_vision_dist < 90.0 and lead_prob > 0.65:
    # wide-y winner first (cut-in)
    if (extra_track is not None and extra_score > max(first_score, 1e-7) and
        dist_sane(extra_track, wide=True) and vel_sane(extra_track) and y_sane(extra_track, wide=True)):
      best_track = extra_track

    # then allow first/second with wide gates
    elif first_score > 1e-7 and dist_sane(first_track, wide=True) and vel_sane(first_track) and y_sane(first_track, wide=True):
      best_track = first_track

    elif (second_track is not None and second_score > 1e-7 and
          dist_sane(second_track, wide=True) and vel_sane(second_track) and y_sane(second_track, wide=True)):
      best_track = second_track

  # ---- update counters ----
  for t in tracks.values():
    if t is best_track and best_track is not None:
      t.selected_count += 1
    else:
      t.selected_count = 0
      t.is_stopped_car_count = max(0, t.is_stopped_car_count - 1)

  return best_track


def get_RadarState_from_vision(md, lead_msg: capnp._DynamicStructReader, v_ego: float, model_v_ego: float, lead_prob: float):
  lead_v_rel_pred = lead_msg.v[0] - model_v_ego
  dRel = float(lead_msg.x[0] - RADAR_TO_CAMERA)
  yRel = float(-lead_msg.y[0])
  dPath = yRel + np.interp(dRel, md.position.x, md.position.y)
  return {
    "dRel": float(dRel),
    "yRel": yRel,
    "dPath" : float(dPath),
    "vRel": float(lead_v_rel_pred),
    "vLead": float(v_ego + lead_v_rel_pred),
    "vLeadK": float(v_ego + lead_v_rel_pred),
    "aLead": float(lead_msg.a[0]),
    "aLeadK": float(lead_msg.a[0]),
    "aLeadTau": 0.3,
    "jLead": 0.0,
    "vLat" : 0.0,
    "fcw": False,
    "modelProb": float(lead_prob),
    "status": True,
    "radar": False,
    "radarTrackId": -1,
  }

class VisionTrack:
  def __init__(self, radar_ts):
    self.radar_ts = radar_ts
    self.dRel = 0.0
    self.vRel = 0.0
    self.yRel = 0.0
    self.vLead = 0.0
    self.aLead = 0.0
    self.vLeadK = 0.0
    self.aLeadK = 0.0
    self.aLeadTau = _LEAD_ACCEL_TAU
    self.prob = 0.0
    self.status = False

    self.dRel_last = 0.0
    self.vLead_last = 0.0
    self.alpha = 0.02
    self.alpha_a = 0.02

    self.vLat = 0.0

    self.v_ego = 0.0
    self.cnt = 0

    self.dPath = 0.0

  def get_lead(self, md):
    #aLeadK = 0.0 if self.mixRadarInfo in [3] else clip(self.aLeadK, self.aLead - 1.0, self.aLead + 1.0)
    return {
      "dRel": self.dRel,
      "yRel": self.yRel,
      #"dPath": self.dPath,
      "vRel": self.vRel,
      "vLead": self.vLead,
      "vLeadK": self.vLeadK,    ## TODO: 아직 vLeadK는 엉망인듯...
      "aLead": self.aLead,
      "aLeadK": self.aLeadK,
      "aLeadTau": self.aLeadTau,
      "jLead": 0.0,
      "vLat": 0.0,
      "fcw": False,
      "modelProb": self.prob,
      "status": self.status,
      "radar": False,
      "radarTrackId": -1,
      #"aLead": self.aLead,
      #"vLat": self.vLat,
    }

  def reset(self):
    self.status = False
    self.aLeadTau = _LEAD_ACCEL_TAU

    self.vRel = 0.0
    self.vLead = self.vLeadK = self.v_ego
    self.aLead = self.aLeadK = 0.0
    self.vLat = 0.0

  def update(self, lead_msg, lead_prob, model_v_ego, v_ego, md):

    lead_v_rel_pred = lead_msg.v[0] - model_v_ego
    self.prob = lead_prob
    self.v_ego = v_ego
    if self.prob > .5:
      dRel = float(lead_msg.x[0]) - RADAR_TO_CAMERA
      if abs(self.dRel - dRel) > 5.0:
        self.cnt = 0
      self.dRel = dRel

      self.yRel = float(-lead_msg.y[0])
      dPath = self.yRel + np.interp(self.dRel, md.position.x, md.position.y)
      a_lead_vision = lead_msg.a[0]
      if self.cnt < 20 or self.prob < 0.97: # 레이더측정시 cnt는 0, 레이더사라지고 1초간 비젼데이터 그대로 사용
        self.vRel = lead_v_rel_pred
        self.vLead = float(v_ego + lead_v_rel_pred)
        self.aLead = a_lead_vision
        self.vLat = 0.0
      else:
        v_rel = (self.dRel - self.dRel_last) / self.radar_ts
        v_rel = self.vRel * (1. - self.alpha) + v_rel * self.alpha

        #self.vRel = lead_v_rel_pred if self.mixRadarInfo == 3 else (lead_v_rel_pred + self.vRel) / 2
        model_weight = np.interp(self.prob, [0.97, 1.0], [0.4, 0.0])  # prob가 높으면 v_rel(dRel미분값)에 가중치를 줌.
        self.vRel = float(lead_v_rel_pred * model_weight + v_rel * (1. - model_weight))
        #self.vRel = (lead_v_rel_pred + v_rel) / 2
        self.vLead = float(v_ego + self.vRel)

        a_lead = (self.vLead - self.vLead_last) / self.radar_ts * 0.2 #0.5 -> 0.2 vel 미분적용을 줄임.
        self.aLead = self.aLead * (1. - self.alpha_a) + a_lead * self.alpha_a
        if abs(a_lead_vision) > abs(self.aLead): # or self.mixRadarInfo == 3:
          self.aLead = a_lead_vision

        vLat_alpha = 0.002
        self.vLat = self.vLat * (1. - vLat_alpha) + (dPath - self.dPath) / self.radar_ts * vLat_alpha

      self.dPath = dPath

      self.vLeadK= self.vLead
      self.aLeadK = self.aLead

      self.status = True
      self.cnt += 1
    else:
      self.reset()
      self.cnt = 0
      self.dPath = self.yRel + np.interp(v_ego ** 2 / (2 * 2.5), md.position.x, md.position.y)

    self.dRel_last = self.dRel
    self.vLead_last = self.vLead

    # Learn if constant acceleration
    #aLeadTauValue = self.aLeadTauPos if self.aLead > self.aLeadTauThreshold else self.aLeadTauNeg
    if abs(self.aLead) < 0.3: #self.aLeadTauThreshold:
      self.aLeadTau = 0.2 #aLeadTauValue
    else:
      #self.aLeadTau = min(self.aLeadTau * 0.9, aLeadTauValue)
      self.aLeadTau *= 0.9

class RadarD:
  def __init__(self, delay: float = 0.0):
    self.current_time = 0.0

    self.tracks: dict[int, Track] = {}

    self.lead_prob_filters = [FirstOrderFilter(0.0, 0.2, DT_MDL) for _ in range(2)]

    self.v_ego = 0.0
    print("###RadarD.. : delay = ", delay, int(round(delay / DT_MDL))+1)
    self.v_ego_hist = deque([0.0], maxlen=int(round(delay / DT_MDL))+1)
    self.last_v_ego_frame = -1

    self.radar_state: capnp._DynamicStructBuilder | None = None
    self.radar_state_valid = False

    self.ready = False

    self.vision_tracks = [VisionTrack(DT_MDL), VisionTrack(DT_MDL)]

    self.params = Params()
    self.enable_radar_tracks = self.params.get_int("EnableRadarTracks")
    self.enable_corner_radar = self.params.get_int("EnableCornerRadar")
    self.radar_lat_factor = 0.0

    self.radar_detected = False
    self.scc_fallback_state = SccFallbackState()
    self.scc_fallback_d_rel: float | None = None
    self.lane_line_available = False
    self.lane_marking_intervention_enabled = False
    self.lane_marking_cut_in_available = False
    self.lane_marking_threshold = 0.60
    self.lane_marking_state: dict[str, Any] = {"valid": False}

    self._corner_lat_hist = {
      "L": deque(maxlen=10),
      "R": deque(maxlen=10),
    }
    self._corner_state = {"L": 0, "R": 0}  # -1,0,+1


  def update(self, sm: messaging.SubMaster, rr: car.RadarData):
    self.ready = sm.seen['modelV2']
    self.current_time = 1e-9*max(sm.logMonoTime.values())

    self.enable_radar_tracks = self.params.get_int("EnableRadarTracks")
    self.enable_corner_radar = self.params.get_int("EnableCornerRadar")
    self.radar_lat_factor = get_cut_in_prediction_horizon(self.params.get_float("RadarLatFactor"))
    self.radar_reaction_factor = self.params.get_float("RadarReactionFactor") * 0.01
    self.detect_cut_in = self.radar_lat_factor > 0
    self.lane_marking_intervention_enabled = self.params.get_bool("LaneMarkingInterventionEnabled")
    self.lane_marking_threshold = clamp(self.params.get_int("LaneMarkingConfidenceThreshold") * 0.01, 0.0, 1.0)
    self.lane_marking_state = self._read_lane_marking_state(sm)
    self.lane_marking_cut_in_available = self.lane_marking_intervention_enabled and self.lane_marking_state.get("valid", False)

    leads_v3 = sm['modelV2'].leadsV3
    if sm.recv_frame['carState'] != self.last_v_ego_frame:
      self.v_ego = sm['carState'].vEgo
      self.v_ego_hist.append(self.v_ego)
      self.last_v_ego_frame = sm.recv_frame['carState']

    valid_ids = set()
    for pt in rr.points:
      track_id = pt.trackId
      valid_ids.add(track_id)      

      if track_id not in self.tracks:
        self.tracks[track_id] = Track(track_id)

      self.tracks[track_id].update(sm['modelV2'], pt, self.ready, self.radar_reaction_factor, self.radar_lat_factor)

    for tid in list(self.tracks.keys()):
      if tid not in valid_ids:
        self.tracks.pop(tid)

    # *** publish radarState ***
    self.radar_state_valid = sm.all_checks()
    if not self.radar_state_valid:
      print("radarState invalid: sm.all_checks() failed")

      for name in sm.data.keys():
        alive = sm.alive.get(name, None)
        valid = sm.valid.get(name, None)
        freq_ok = sm.freq_ok.get(name, None)
        updated = sm.updated.get(name, None)

        if not alive or not valid or not freq_ok:
          print(
            f"  {name}: "
            f"alive={alive}, "
            f"valid={valid}, "
            f"freq_ok={freq_ok}, "
            f"updated={updated}"
          )
      self.radar_state = log.RadarState.new_message()

    model_updated = False if self.radar_state.mdMonoTime == sm.logMonoTime['modelV2'] else True

    self.radar_state.mdMonoTime = sm.logMonoTime['modelV2']
    self.radar_state.radarErrors = rr.errors
    self.radar_state.carStateMonoTime = sm.logMonoTime['carState']

    if len(sm['modelV2'].velocity.x):
      model_v_ego = sm['modelV2'].velocity.x[0]
    else:
      model_v_ego = self.v_ego

    if len(leads_v3) > 1:
      for i in range(2):
        lead_prob = leads_v3[i].prob
        if lead_prob > self.lead_prob_filters[i].x:
          self.lead_prob_filters[i].x = lead_prob
        else:
          self.lead_prob_filters[i].update(lead_prob)
          
      md = sm['modelV2']
      if model_updated:
        if self.radar_detected:
          self.vision_tracks[0].cnt = 0
          self.vision_tracks[1].cnt = 0
        self.vision_tracks[0].update(leads_v3[0], self.lead_prob_filters[0].x, model_v_ego, self.v_ego, md)
        self.vision_tracks[1].update(leads_v3[1], self.lead_prob_filters[1].x, model_v_ego, self.v_ego, md)

      alive_tracks = {tid: trk for tid, trk in self.tracks.items() if trk.cnt > 2 }
      self.radar_state.leadOne, self.radar_detected = self.get_lead(sm['carState'], md, alive_tracks, 0, leads_v3[0], model_v_ego, self.lead_prob_filters[0].x, low_speed_override=False)
      self.radar_state.leadTwo, _ = self.get_lead(sm['carState'], md, alive_tracks, 1, leads_v3[1], model_v_ego, self.lead_prob_filters[1].x, low_speed_override=False)

      self.lane_line_available = self._lane_lines_available(md)
      self.lane_marking_cut_in_available = (
        self.lane_marking_intervention_enabled and
        self.lane_marking_state.get("valid", False)
      )
      self.compute_leads(self.v_ego, alive_tracks, md, self.lead_prob_filters[0].x)
      if self.leadTwo is not None:
        self.radar_state.leadTwo = self.leadTwo
      if self.enable_radar_tracks >= 3:
        self._pick_lead_one_from_state()
    else:
      self.scc_fallback_state.reset()
      self.scc_fallback_d_rel = None
      self._reset_cut_in_confirmation(self.tracks)
      self.radar_state.leadsCutIn = []
      self.leadCutIn = {'status': False}

  def publish(self, pm: messaging.PubMaster):
    assert self.radar_state is not None

    radar_msg = messaging.new_message("radarState")
    radar_msg.valid = self.radar_state_valid
    radar_msg.radarState = self.radar_state
    pm.send("radarState", radar_msg)

  def get_lead(self, CS, md, tracks: dict[int, Track], index: int, lead_msg: capnp._DynamicStructReader,
               model_v_ego: float, lead_prob: float, low_speed_override: bool = True) -> dict[str, Any]:

    v_ego = self.v_ego
    ready = self.ready

    # Keep SCC track 0 separate from raw radar without mutating the shared track map.
    if self.enable_radar_tracks <= 0:
      track_scc = tracks.get(0)
      radar_tracks = tracks
    else:
      track_scc = tracks.get(0)
      radar_tracks = {track_id: radar_track for track_id, radar_track in tracks.items() if track_id != 0}

    # Determine leads, this is where the essential logic happens
    if len(radar_tracks) > 0 and ready and lead_prob > .4:
      track = match_vision_to_track(v_ego, lead_msg, lead_prob, radar_tracks)
    else:
      track = None

    if (track is None or lead_prob < .6) and track_scc is not None and track_scc.cnt > 2:
      #if self.enable_radar_tracks in [-1, 2] or model_v_ego < 5 or track_scc.vLead < 5.0:
      if self.enable_radar_tracks == -1:
        track = track_scc

    guarded_scc_mode = self.enable_radar_tracks >= 2 and index == 0 and track_scc is not None and track_scc.cnt > 2 and track_scc.vLead < 5.0
    lane_change_active = md.meta.laneChangeState != log.LaneChangeState.off
    if guarded_scc_mode and lane_change_active:
      evidence = get_scc_fallback_evidence(track_scc, lead_msg, lead_prob, self.scc_fallback_d_rel)
      alternative_stable = track is not None and track is not track_scc and lead_prob >= .6
      use_scc = self.scc_fallback_state.update(
        candidate_ok=evidence.candidate_ok,
        urgent_ok=evidence.urgent_ok,
        continuous=evidence.continuous,
        alternative_stable=alternative_stable,
      )
      self.scc_fallback_d_rel = float(track_scc.dRel)
      if use_scc:
        track = track_scc
    elif index == 0:
      self.scc_fallback_state.reset()
      self.scc_fallback_d_rel = None
      if guarded_scc_mode and (track is None or lead_prob < .6):
        track = track_scc

    lead_dict = {'status': False}
    radar = False
    if track is not None:
      lead_dict = track.get_RadarState(lead_prob, self.vision_tracks[0].yRel)
      radar = True
    elif (track is None) and ready and (lead_prob > .5):
        lead_dict = self.vision_tracks[index].get_lead(md)

    if self.enable_corner_radar > 1:
      lead_dict = self.corner_radar(CS, lead_dict)

    if low_speed_override:
      low_speed_tracks = [c for c in radar_tracks.values() if c.potential_low_speed_lead(v_ego)]
      if len(low_speed_tracks) > 0:
        closest_track = min(low_speed_tracks, key=lambda c: c.dRel)

        # Only choose new track if it is actually closer than the previous one
        if (not lead_dict['status']) or (closest_track.dRel < lead_dict['dRel']):
          #lead_dict = closest_track.get_RadarState(lead_prob, self.vision_tracks[0].yRel, self.vision_tracks[0].vLat)
          lead_dict = closest_track.get_RadarState(lead_prob, self.vision_tracks[0].yRel)

    return lead_dict, radar

  def _read_lane_marking_state(self, sm: messaging.SubMaster) -> dict[str, Any]:
    empty = {"valid": False}
    if not self.lane_marking_intervention_enabled or "laneMarkingState" not in sm.data:
      return empty
    if not sm.alive.get("laneMarkingState", False) or not sm.valid.get("laneMarkingState", False):
      return empty

    state = sm["laneMarkingState"]
    lane_width = float(state.laneWidth)
    valid = (
      bool(state.valid) and
      not bool(state.inferenceSkipped) and
      LANE_MARKING_MIN_WIDTH < lane_width < LANE_MARKING_MAX_WIDTH
    )
    if not valid:
      return empty

    def side_state(side: str) -> dict[str, Any]:
      if side == "left":
        label = str(state.leftLabel)
        confidence = float(state.leftConfidence)
        block = bool(state.leftBlock)
        allow = bool(state.leftNoBlock)
      else:
        label = str(state.rightLabel)
        confidence = float(state.rightConfidence)
        block = bool(state.rightBlock)
        allow = bool(state.rightNoBlock)
      return {
        "label": label,
        "confidence": confidence,
        "block": block or (label in LANE_MARKING_BLOCK_LABELS and confidence >= self.lane_marking_threshold),
        "allow": allow or (label in LANE_MARKING_ALLOW_LABELS and confidence >= self.lane_marking_threshold),
      }

    return {
      "valid": True,
      "lane_width": lane_width,
      "left": side_state("left"),
      "right": side_state("right"),
    }

  def _lane_marking_side(self, side: str) -> dict[str, Any]:
    if not self.lane_marking_state.get("valid", False):
      return {"label": "unknown", "confidence": 0.0, "block": False, "allow": False}
    return self.lane_marking_state.get(side, {"label": "unknown", "confidence": 0.0, "block": False, "allow": False})

  def _lane_lines_available(self, md) -> bool:
    if len(md.laneLineProbs) <= 2 or len(md.laneLines) <= 2:
      return False

    if len(md.laneLines[1].y) == 0 or len(md.laneLines[2].y) == 0:
      return False

    left_prob = float(md.laneLineProbs[1])
    right_prob = float(md.laneLineProbs[2])
    lane_width = abs(float(md.laneLines[2].y[0]) - float(md.laneLines[1].y[0]))
    return left_prob > 0.35 and right_prob > 0.35 and 2.3 < lane_width < 4.8

  def _cut_in_candidate(self, c: Track, side: str) -> bool:
    if not (3.0 < c.dRel < CUT_IN_MAX_DIST and c.vLead > 4.0):
      return False

    close = c.dRel < CUT_IN_CLOSE_DIST
    min_track_age = CUT_IN_MIN_TRACK_AGE_CLOSE if close else CUT_IN_MIN_TRACK_AGE
    if c.cnt * DT_MDL < min_track_age:
      return False

    if abs(c.dPath) > 4.5 and abs(c.dPath_future) > 4.5:
      return False

    lane_now = max(c.in_lane_prob, c.in_lane_prob_expanded * 0.65)
    lane_future = max(c.in_lane_prob_future, c.in_lane_prob_future_expanded * 0.65)
    moving_toward_center = abs(c.dPath_future) < abs(c.dPath) - (0.05 if close else 0.12)
    lane_evidence_growing = lane_future > lane_now + 0.04
    center_entering_threshold = 0.02 if close else 0.06

    center_entering = max(c.in_lane_prob, c.in_lane_prob_future) > center_entering_threshold
    base_overlap = max(c.in_lane_prob, c.in_lane_prob_future)
    expanded_overlap = max(c.in_lane_prob_expanded, c.in_lane_prob_future_expanded)
    strong_expanded_approach = (
      moving_toward_center and lane_evidence_growing and
      expanded_overlap > (0.28 if close else 0.32)
    )
    touching_our_lane = self._direct_cut_in_overlap(c, close) or strong_expanded_approach

    if not (self.lane_line_available or self.lane_marking_cut_in_available):
      boundary_overlap = min(abs(c.dPath), abs(c.dPath_future)) < 1.8
      strong_overlap = base_overlap > (0.12 if close else 0.16)
      close_boundary_entry = close and abs(c.dPath) < 1.6 and c.in_lane_prob > 0.18
      model_boundary_entry = (
        boundary_overlap and strong_overlap and
        (moving_toward_center or lane_evidence_growing or close_boundary_entry)
      )
      expanded_boundary_entry = (
        min(abs(c.dPath), abs(c.dPath_future)) < 2.1 and strong_expanded_approach
      )
      if not (model_boundary_entry or expanded_boundary_entry):
        return False

    if self.lane_marking_cut_in_available:
      marking = self._lane_marking_side(side)
      if marking["block"]:
        crossing_solid = (
          moving_toward_center and
          (lane_future > lane_now + (0.07 if close else 0.10) or max(c.in_lane_prob, c.in_lane_prob_future) > (0.12 if close else 0.18))
        )
        if not (touching_our_lane and crossing_solid):
          return False
      elif not marking["allow"] and not self.lane_line_available:
        if not (touching_our_lane and moving_toward_center and (lane_evidence_growing or center_entering)):
          return False

    if close:
      return touching_our_lane and (moving_toward_center or lane_evidence_growing or center_entering)
    return touching_our_lane and (moving_toward_center or lane_evidence_growing)

  def _update_cut_in_confirmation(self, c: Track, candidate: bool, close: bool) -> bool:
    confirm_frames = 2 if close else 3
    if not candidate:
      c.cut_in_count = 0
      c.cut_in_center_count = 0
      return False

    c.cut_in_center_count = 0
    c.cut_in_count = min(c.cut_in_count + 1, confirm_frames)
    return c.cut_in_count >= confirm_frames

  def _direct_cut_in_overlap(self, c: Track, close: bool) -> bool:
    base_overlap = max(c.in_lane_prob, c.in_lane_prob_future)
    if close:
      return base_overlap > 0.08
    return c.in_lane_prob > 0.25 and base_overlap > 0.28

  def _confirmed_cut_in_ready(self, c: Track, candidate: bool, close: bool) -> bool:
    confirmed = self._update_cut_in_confirmation(c, candidate, close)
    return confirmed and self._direct_cut_in_overlap(c, close)

  def _consume_cut_in_center_entry(self, c: Track, candidate: bool) -> bool:
    if not candidate or c.cut_in_count <= 0 or c.in_lane_prob <= 0.3:
      c.cut_in_count = 0
      c.cut_in_center_count = 0
      return False

    c.cut_in_center_count += 1
    if c.cut_in_center_count < 2:
      return False

    c.cut_in_count = 0
    c.cut_in_center_count = 0
    return True

  @staticmethod
  def _reset_cut_in_confirmation(tracks: dict[int, Track]) -> None:
    for track in tracks.values():
      track.cut_in_count = 0
      track.cut_in_center_count = 0

  def compute_leads(self, v_ego, tracks, md, lead_prob):
    lead_msg = md.leadsV3[0] if (md is not None and len(md.position.x) == 33) else None
    self.leadCutIn = {'status': False}
    if lead_msg is None:
      self._reset_cut_in_confirmation(tracks)
      # reset
      self.radar_state.leadsLeft = []
      self.radar_state.leadsCenter = []
      self.radar_state.leadsRight = []
      self.radar_state.leadsCutIn = []
      self.radar_state.leadLeft = {'status': False}
      self.radar_state.leadRight = {'status': False}
      return
    
    left_list, right_list, center_list, cutin_list = [], [], [], []

    def maybe_add_cut_in(c: Track, side: str):
      candidate = self._cut_in_candidate(c, side)
      if self._confirmed_cut_in_ready(c, candidate, c.dRel < CUT_IN_CLOSE_DIST):
        cutin_list.append(c.get_CutInState(v_ego, 0.03, float(-lead_msg.y[0])))

    for c in tracks.values():
      y_rel_neg = - c.yRel
      # center
      if c.in_lane_prob > 0.3:
        side = "left" if y_rel_neg < 0 else "right"
        if self._consume_cut_in_center_entry(c, self._cut_in_candidate(c, side)):
          cutin_list.append(c.get_CutInState(v_ego, 0.03, float(-lead_msg.y[0])))
        if c.cnt > 3:
          ld = c.get_RadarState(lead_prob, float(-lead_msg.y[0]))
          ld['modelProb'] = 0.01
          center_list.append(ld)

      # left/right
      elif y_rel_neg < 0: #left_lane_y:
        ld = c.get_RadarState(0, 0)
        maybe_add_cut_in(c, "left")
        left_list.append(ld)
      else:
        ld = c.get_RadarState(0, 0)
        maybe_add_cut_in(c, "right")
        right_list.append(ld)

    self.radar_state.leadsLeft   = left_list
    self.radar_state.leadsRight  = right_list
    self.radar_state.leadsCenter = center_list
    self.radar_state.leadsCutIn = cutin_list
    self.leadCutIn = min(
      (ld for ld in cutin_list if 3 < ld['dRel'] < 50 and ld['vLead'] > 4),
      key=lambda d: d['dRel'],
      default={'status': False}
    )

    self.radar_state.leadLeft  = min(
        (ld for ld in left_list if ld['dRel'] > 5 and abs(ld['dPath']) < 3.5),
        key=lambda d: d['dRel'],
        default={'status': False}
    )
    self.radar_state.leadRight = min(
        (ld for ld in right_list if ld['dRel'] > 5 and abs(ld['dPath']) < 3.5),
        key=lambda d: d['dRel'],
        default={'status': False}
    )
   
    self.leadTwo = None
    if self.lane_line_available:
      self.leadCenter = min(
          (ld for ld in center_list if ld['vLead'] > 5 and ld['radar'] and ld['dRel'] > 3.5),
          key=lambda d: d['dRel'],
          default=None
      )
      if self.radar_state.leadOne.status and self.radar_state.leadOne.radar:
        self.leadTwo = min(
            (ld for ld in center_list if ld['vLead'] > 5 and ld['radar'] and self.radar_state.leadOne.dRel < ld['dRel'] < 80),
            key=lambda d: d['dRel'],
            default=None
        )
        if self.leadTwo is not None:
          self.leadTwo = copy.deepcopy(self.leadTwo)
          #gap = self.leadTwo['dRel'] - self.radar_state.leadOne.dRel
          #offset = 3.0 + min(gap * 0.2, 10)
          #self.leadTwo['dRel'] = self.radar_state.leadOne.dRel + offset
          self.leadTwo['dRel'] = max(self.radar_state.leadOne.dRel + 3.0, self.leadTwo['dRel'] - 8.0) # lead+1 차를 뒤로 8M후퇴하여, mpc에서  감자하도록함.. 최소 lead보다 3M앞에 위치하도록
    else:
      self.leadCenter = None

    def _ok(ld):
        return (ld.get('vLead', 0) > 2 and
                abs(ld.get('dPath', 0)) < 4.2 and
                ld.get('dRel', 0) > 2)

    def _pick_two_with_gap(cands, min_gap=5.0):
        xs = sorted((ld for ld in cands if _ok(ld)), key=lambda d: d['dRel'])
        if not xs:
            return []
        first = xs[0]
        second = None
        for ld in xs[1:]:
            # 5m 이상 떨어진 후보만 허용 (>= 5.0)
            if (ld['dRel'] - first['dRel']) >= min_gap:
                second = ld
                break
        return [first] if second is None else [first, second]

    self.radar_state.leadsLeft2  = _pick_two_with_gap(left_list,  min_gap=5.0)
    self.radar_state.leadsRight2 = _pick_two_with_gap(right_list, min_gap=5.0)

  def _pick_lead_one_from_state(self):
    chosen = None
    detected = self.radar_detected

    if self.leadCutIn and self.leadCutIn.get("status") and self.detect_cut_in:
      if self.radar_state.leadOne.status:
        if self.leadCutIn["dRel"] < self.radar_state.leadOne.dRel:
          chosen = self.leadCutIn
          chosen["modelProb"] = 0.03
          detected = True
      else:
        chosen = self.leadCutIn
        chosen["modelProb"] = 0.03
        detected = True

    elif self.leadCenter and self.leadCenter["status"]:
      if self.radar_detected:
        if self.radar_state.leadOne.status and self.leadCenter["dRel"] < self.radar_state.leadOne.dRel:
          chosen = self.leadCenter
          chosen["modelProb"] = 0.01
      else:
        chosen = self.leadCenter
        chosen["modelProb"] = 0.02
        detected = True

    if chosen is not None:
        self.radar_state.leadOne = chosen
        self.radar_detected = detected

  def _corner_update_state(self, side: str, cur_lat: float, enter_lat: float = 2.8) -> int:
    # 유효 범위 밖이면 리셋
    if not (0.0 < cur_lat < enter_lat):
      self._corner_lat_hist[side].clear()
      self._corner_state[side] = 0
      return 0

    h = self._corner_lat_hist[side]
    h.append(cur_lat)

    n = len(h)
    if n < 3:
      # 데이터 너무 적으면 이전 상태 유지
      return self._corner_state[side]

    delta = h[-1] - h[0]
    th = 0.02 # 3 * (20 / n)

    if delta < -th:
      self._corner_state[side] = +1   # approaching
    elif delta > th:
      self._corner_state[side] = -1   # leaving
    else:
      self._corner_state[side] = 0    # maintain

    return self._corner_state[side]
 
  def corner_radar(self, CS, lead_dict):
    ENTER_LAT = 2.2
    KEEP_LAT  = 2.0
    EXIT_LAT  = 1.2

    left_lat, right_lat = abs(CS.leftLatDist), abs(CS.rightLatDist)
    left_state  = self._corner_update_state("L", left_lat)
    right_state = self._corner_update_state("R", right_lat)

    # 1) left usable?
    left_ok = False
    if left_state > 0:
      left_ok = left_lat < ENTER_LAT
    elif left_state == 0:
      left_ok = 0 < left_lat < KEEP_LAT
    else:  # leaving
      left_ok = left_lat <= EXIT_LAT

    # 2) right usable?
    right_ok = False
    if right_state > 0:
      right_ok = right_lat < ENTER_LAT
    elif right_state == 0:
      right_ok = 0 < right_lat < KEEP_LAT
    else:
      right_ok = right_lat <= EXIT_LAT

    # 3) 아무도 못 쓰면 skip
    if not left_ok and not right_ok:
      return lead_dict

    # 4) 둘 다 되면 longDist로 선택
    if left_ok and right_ok:
      if CS.leftLongDist <= CS.rightLongDist:
        lat_dist, long_dist = +left_lat, CS.leftLongDist
      else:
        lat_dist, long_dist = -right_lat, CS.rightLongDist
    elif left_ok:
      lat_dist, long_dist = +left_lat, CS.leftLongDist
    else:
      lat_dist, long_dist = -right_lat, CS.rightLongDist
    
    if lead_dict['status']:
      if lead_dict['dRel'] > long_dist:
        lead_dict['dRel'] = long_dist
        lead_dict['yRel'] = lat_dist
        lead_dict['vRel'] = 0.0
        lead_dict['vLead'] = CS.vEgo if CS.vEgo < lead_dict['vLead'] else lead_dict['vLead']
        lead_dict['vLeadK'] = lead_dict['vLead']
        lead_dict['aLead'] = CS.aEgo if CS.aEgo < lead_dict['aLead'] else lead_dict['aLead']
        lead_dict['aLeadK'] = lead_dict['aLead']
        lead_dict['aLeadTau'] = _LEAD_ACCEL_TAU
        lead_dict['jLead'] = 0.0
        lead_dict['vLat'] = 0.0
        lead_dict['modelProb'] = 1.0
        lead_dict['radarTrackId'] = -1
        lead_dict['radar'] = True
    else:
      lead_dict['status'] = True
      lead_dict['dRel'] = long_dist
      lead_dict['yRel'] = lat_dist
      lead_dict['vRel'] = 0.0
      lead_dict['vLead'] = CS.vEgo
      lead_dict['vLeadK'] = CS.vEgo
      lead_dict['aLead'] = CS.aEgo
      lead_dict['aLeadK'] = CS.aEgo
      lead_dict['aLeadTau'] = _LEAD_ACCEL_TAU
      lead_dict['jLead'] = 0.0
      lead_dict['vLat'] = 0.0
      lead_dict['modelProb'] = 1.0
      lead_dict['radarTrackId'] = -1
      lead_dict['radar'] = True

    return lead_dict

# fuses camera and radar data for best lead detection
def main() -> None:
  config_realtime_process(5, Priority.CTRL_LOW)

  # wait for stats about the car to come in from controls
  cloudlog.info("radard is waiting for CarParams")
  CP = messaging.log_from_bytes(Params().get("CarParams", block=True), car.CarParams)
  cloudlog.info("radard got CarParams")

  # *** setup messaging
  sm = messaging.SubMaster(
    ['modelV2', 'carState', 'liveTracks', 'laneMarkingState'],
    poll='modelV2',
    ignore_alive=['laneMarkingState'],
    ignore_avg_freq=['laneMarkingState'],
    ignore_valid=['laneMarkingState'],
  )
  #sm = messaging.SubMaster(['modelV2', 'carState', 'liveTracks'])
  pm = messaging.PubMaster(['radarState'])

  RD = RadarD(CP.radarDelay)

  while 1:
    sm.update()

    if sm.updated['modelV2']:
      RD.update(sm, sm['liveTracks'])
      RD.publish(pm)


if __name__ == "__main__":
  main()
