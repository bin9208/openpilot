# Driving Safety Regressions Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox lists so progress remains auditable.

**Goal:** Prevent false hard braking and unstable low-speed steering/curvature commands, and warn the driver before unsupported intersection turns without adding automatic steering behavior.

**Architecture:** Keep each fix at the narrowest responsible boundary: radar lead arbitration in `radard.py`, Hyundai angle handoff in `carcontroller.py`, low-speed lag compensation in `drive_helpers.py`, and an advisory-only turn detector in `selfdrived`. Navigation advisory fields remain separate from navigation-control authorization so shadow navigation can warn without gaining steering authority.

**Tech Stack:** Python 3, Cap'n Proto schemas, openpilot messaging/events, Hyundai CAN controller logic, pytest.

## Global Constraints

- Preserve every unrelated dirty-worktree change and stage only explicitly changed files.
- Add a failing focused test before each production change and record both RED and GREEN results.
- Keep emergency braking available when vision overlap and TTC evidence are strong and stable.
- The intersection feature may only create a warning event; it must not steer, brake, or disengage.
- Existing high-speed curvature behavior must remain bit-for-bit equivalent at and above 5 m/s.
- Runtime thresholds are conservative first-pass values to be refined from later road logs.

---

## Task 1: Guard SCC Track 0 Promotion During Lane Changes

**Files:**

- Modify: `selfdrive/controls/radard.py`
- Modify: `selfdrive/controls/tests/test_radard_cut_in.py`

- [ ] Add focused selector-state tests covering normal confirmation, urgent confirmation, discontinuity rejection, and switch hysteresis.

```python
def test_scc_fallback_requires_three_consistent_frames():
  state = SccFallbackState()
  assert not state.update(candidate_ok=True, urgent_ok=False, continuous=True, alternative_stable=False)
  assert not state.update(candidate_ok=True, urgent_ok=False, continuous=True, alternative_stable=False)
  assert state.update(candidate_ok=True, urgent_ok=False, continuous=True, alternative_stable=False)


def test_scc_fallback_urgent_path_requires_two_frames():
  state = SccFallbackState()
  assert not state.update(candidate_ok=True, urgent_ok=True, continuous=True, alternative_stable=False)
  assert state.update(candidate_ok=True, urgent_ok=True, continuous=True, alternative_stable=False)


def test_scc_fallback_drops_discontinuous_selected_track():
  state = SccFallbackState()
  for _ in range(3):
    selected = state.update(candidate_ok=True, urgent_ok=False, continuous=True, alternative_stable=False)
  assert selected
  assert not state.update(candidate_ok=True, urgent_ok=False, continuous=False, alternative_stable=False)
```

- [ ] Run only these tests and confirm they fail because `SccFallbackState` does not exist.

```powershell
python -m pytest selfdrive/controls/tests/test_radard_cut_in.py -k scc_fallback -q
```

- [ ] Add `SccFallbackState` with three-frame normal confirmation, two-frame urgent confirmation, immediate discontinuity reset, and two-frame alternative-lead hysteresis.
- [ ] Add pure helpers that evaluate SCC/vision longitudinal overlap, lateral path overlap, distance continuity, and stable TTC.
- [ ] During active lane change, permit SCC track 0 only through the guarded state. Preserve explicit SCC-only mode (`EnableRadarTracks == -1`) and existing stopped-SCC behavior outside lane changes.
- [ ] Stop mutating the shared `tracks` dictionary with `pop(0)`; build a local raw-radar view so leadOne/leadTwo receive the same inputs.
- [ ] Run the new selector tests, then the whole radar cut-in test module.

```powershell
python -m pytest selfdrive/controls/tests/test_radard_cut_in.py -q
```

- [ ] Review the diff for fail-open paths, then commit only the two task files.

---

## Task 2: Add a Low-Speed Hyundai Angle Handoff State

**Files:**

- Modify: `opendbc_repo/opendbc/car/hyundai/carcontroller.py`
- Modify: `opendbc_repo/opendbc/car/hyundai/tests/test_angle_control.py`

- [ ] Add tests for yielding on driver override, requiring 0.4 seconds of continuously low driver torque before reacquisition, resetting the commanded angle to measured angle while yielding, and disabling large-error acceleration during reacquisition.

```python
def test_angle_handoff_requires_release_hold():
  handoff = AngleHandoffState(release_frames=40)
  assert handoff.update(steering_pressed=True) == AngleHandoffState.YIELDING
  for _ in range(39):
    assert handoff.update(steering_pressed=False) == AngleHandoffState.YIELDING
  assert handoff.update(steering_pressed=False) == AngleHandoffState.REACQUIRING


def test_reacquire_uses_normal_acceleration_for_large_error():
  angle, rate = smooth_angle_command_rate(30.0, 0.0, 0.0, 3.0, 1.0, allow_large_error_accel=False)
  assert rate <= ANGLE_COMMAND_ACCEL_LIMIT
```

- [ ] Run the focused tests and confirm RED failure from missing state/API.
- [ ] Implement explicit `NORMAL`, `YIELDING`, and `REACQUIRING` states inside the Hyundai controller module.
- [ ] On `steeringPressed`, immediately yield and reset command/rate to measured steering angle. Require 40 consecutive 100 Hz frames without `steeringPressed` before reacquisition.
- [ ] While reacquiring at low speed, rate-limit toward the target with `ANGLE_COMMAND_ACCEL_LIMIT` even when error exceeds `ANGLE_COMMAND_LARGE_ERROR`; leave the state after command error enters the normal-error band.
- [ ] Run the focused module and adjacent Hyundai controller tests available in the environment.

```powershell
python -m pytest opendbc_repo/opendbc/car/hyundai/tests/test_angle_control.py -q
```

- [ ] Commit only the controller and test files.

---

## Task 3: Bound Launch-Time Lag-Adjusted Curvature

**Files:**

- Modify: `selfdrive/controls/lib/drive_helpers.py`
- Modify: `selfdrive/controls/tests/test_laneless_response.py`

- [ ] Add parameterized tests at 0.5 m/s and 1.8 m/s proving finite output close to current curvature, plus a 5 m/s regression test comparing the new result to the original formula.

```python
@pytest.mark.parametrize("v_ego", [0.5, 1.8])
def test_launch_curvature_is_bounded(v_ego):
  desired = get_lag_adjusted_curvature(CP, v_ego, psis, curvatures, rates)
  assert np.isfinite(desired[0])
  assert abs(desired[0] - curvatures[0]) < 0.002
```

- [ ] Run the focused launch tests and confirm at least one fails with the current tiny-distance divisor behavior.
- [ ] Preserve raw vehicle speed separately from the existing `MIN_SPEED` numerical floor.
- [ ] Below 5 m/s, floor prediction distance by `MIN_SPEED * delay` and blend from current curvature at 1 m/s to lag-adjusted curvature at 5 m/s.
- [ ] Return current curvature for non-finite low-speed intermediates and keep the existing curvature-rate clip.
- [ ] Run the full laneless response test module and verify the 5 m/s/high-speed expectations are unchanged.

```powershell
python -m pytest selfdrive/controls/tests/test_laneless_response.py -q
```

- [ ] Commit only the helper and test file.

---

## Task 4: Publish Navigation Advisory Turns Without Granting Control

**Files:**

- Modify: `cereal/custom.capnp`
- Modify: `selfdrive/carrot/carrot_serv.py`
- Modify: `selfdrive/carrot/tests/test_navigation_shadow_gates.py`

- [ ] Extend the shadow-gate tests so valid shadow navigation exposes advisory turn type/distance while steering/audio control fields remain neutral, and invalid navigation exposes no advisory.
- [ ] Run the focused test and confirm RED failure from absent message fields.
- [ ] Append `advisoryTurnInfo @33 :Int32` and `advisoryTurnDistance @34 :Int32` to `CarrotMan`; do not renumber existing fields.
- [ ] Fill the advisory fields from valid fresh navigation before checking `naviControlAllowed`; use `-1/0` for invalid or stale navigation.
- [ ] Keep every existing control-authority neutralization rule intact.
- [ ] Run the navigation shadow and arbitration tests.

```powershell
python -m pytest selfdrive/carrot/tests/test_navigation_shadow_gates.py selfdrive/carrot/tests/test_navigation_arbitration.py -q
```

- [ ] Commit only the schema, publisher, and shadow-gate test.

---

## Task 5: Warn Before an Unsupported Intersection Turn

**Files:**

- Create: `selfdrive/selfdrived/turn_warning.py`
- Create: `selfdrive/selfdrived/tests/test_turn_warning.py`
- Modify: `cereal/log.capnp`
- Modify: `selfdrive/selfdrived/events.py`
- Modify: `selfdrive/selfdrived/selfdrived.py`

- [ ] Add pure state-machine tests covering: valid advisory 3–8 seconds ahead plus missing model turn desire, matching model desire suppression, inactive lateral control suppression, two-frame route confirmation, and a 0.5-second low-speed blinker fallback when no advisory exists.
- [ ] Add an event-definition test proving `manualSteeringRequired` is warning-only and uses both visible steering-required and repeating audible prompts.
- [ ] Run the new module and confirm RED failures from missing detector/event.
- [ ] Implement `TurnPathWarning` with two-frame navigation confirmation and 50-frame blinker fallback. Use turn codes 1=left, 2=right, and 5=roundabout; calculate time-to-turn using `max(vEgo, 2.0)` and activate only in the 3–8 second window.
- [ ] Suppress the warning if model lane-change desire matches the advisory turn direction. For a roundabout, require manual warning because no reliable matching model desire exists.
- [ ] Append `manualSteeringRequired @119` in `OnroadEvent.EventName` without renumbering existing events.
- [ ] Define a Korean warning-only alert with `VisualAlert.steerRequired`, `AudibleAlert.promptRepeat`, high priority, and no soft-disable/no-entry behavior.
- [ ] Instantiate the detector in `SelfdriveD`, feed it `carState`, `carControl`, `modelV2`, and `carrotMan`, and add the event while the detector remains active. Do not change lateral/longitudinal activation.
- [ ] Run the warning tests and the generic alert consistency test.

```powershell
python -m pytest selfdrive/selfdrived/tests/test_turn_warning.py selfdrive/selfdrived/tests/test_alerts.py -q
```

- [ ] Commit only the five event/warning files.

---

## Task 6: Integrated Verification and Safety Audit

- [ ] Run all modified focused suites together from a Linux-compatible Python environment.

```bash
python3 -m pytest \
  selfdrive/controls/tests/test_radard_cut_in.py \
  opendbc_repo/opendbc/car/hyundai/tests/test_angle_control.py \
  selfdrive/controls/tests/test_laneless_response.py \
  selfdrive/carrot/tests/test_navigation_shadow_gates.py \
  selfdrive/carrot/tests/test_navigation_arbitration.py \
  selfdrive/selfdrived/tests/test_turn_warning.py \
  selfdrive/selfdrived/tests/test_alerts.py -q
```

- [ ] Compile every modified Python file and validate Cap'n Proto schema loading.
- [ ] Inspect `git diff --check`, `git diff --stat`, and scoped diffs for accidental edits or schema-number collisions.
- [ ] Confirm `manualSteeringRequired` has no control-disable event type and advisory navigation fields do not bypass `naviControlAllowed`.
- [ ] Confirm radar urgent bypass still requires two stable frames and that lane-change discontinuity cannot retain SCC selection.
- [ ] Confirm the final status contains all pre-existing user changes untouched and only planned files/commits were added.

