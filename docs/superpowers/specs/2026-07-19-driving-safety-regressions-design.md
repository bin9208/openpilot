# Driving Safety Regression Fixes Design

## Purpose

Fix four independently observed driving-safety regressions while preserving the
existing navigation authority boundary and unrelated local worktree changes.
The implementation order is:

1. Prevent false SCC track 0 promotion during lane changes.
2. Stop low-speed steering override/re-entry oscillation.
3. Bound launch-time MPC lag compensation.
4. Warn before unsupported intersection turns so the driver can steer manually.

The changes must remain separable, testable, and reversible. They must not add
steering authority, increase longitudinal braking authority, or silently turn
navigation advisory data into a control input.

## Confirmed Failure Mechanisms

### False braking during a lane change

`RadarD.get_lead()` can replace a weak or missing vision match with SCC track 0
without checking lane-change state, vision geometry, distance continuity, or a
multi-frame confirmation state. A discontinuous adjacent-lane track can
therefore become `leadOne` and immediately reach longitudinal MPC.

### Low-speed steering oscillation

Hyundai `steeringPressed` requires multiple asserted samples to engage but
clears after one unasserted sample. The angle controller immediately resets its
command to the measured steering angle while asserted, then immediately starts
returning to the target on the first clear sample. Controller return torque can
cross the driver threshold again, producing an override/re-entry loop.

### Launch-time curvature amplification

`get_lag_adjusted_curvature()` divides interpolated heading by an interpolated
MPC path distance. Immediately after launch that distance can be only a few
centimetres, so a small heading error produces a large lag-compensated
curvature. The result is then used as soon as lateral control becomes active.

### Unsupported intersection turn

At an island or intersection the model can confidently predict a straight or
opposite path while the route requires a turn. Model uncertainty alone cannot
detect this case. Navigation fields currently become neutral when
`naviControlAllowed` is false, which correctly prevents shadow navigation data
from affecting control but also removes the information needed for a
warning-only consumer.

## Design

### 1. SCC Track 0 Arbitration

Apply the new stateful gate only to raw-radar modes that use SCC track 0 as a
fallback (`EnableRadarTracks >= 2`). Preserve the explicit stock-SCC mode
(`EnableRadarTracks == -1`). Do not mutate the caller's track dictionary while
separating track 0 from ordinary radar-track vision matching.

Maintain independent fallback state for each lead index with:

- consecutive candidate count;
- previous distance, relative speed, and TTC;
- whether SCC is currently selected;
- consecutive alternative-source count for switch hysteresis.

A normal SCC fallback candidate must satisfy all of the following:

- the existing mode, age, and stopped-lead eligibility checks;
- a centre-path gate;
- distance continuity against `previous_dRel + previous_vRel * DT_MDL`;
- three consecutive valid radar frames;
- during lane changes, geometric agreement with the corresponding vision lead
  in longitudinal and lateral position.

Distance discontinuities reset confirmation immediately. A jump comparable to
the logged 5.6 m to 15.6 m transition must never retain a confirmed state.
Once a source is selected, an alternative must remain valid for two frames
before a non-emergency source switch.

An urgent obstacle may use a two-frame fast path only when all of these are
true:

- short and stable TTC;
- strong centre-path overlap;
- strong vision distance and lateral overlap;
- continuous distance and TTC across the two frames.

This keeps a real close obstacle responsive without allowing low vision
probability alone to promote an adjacent-lane SCC object.

### 2. Hyundai Angle-Control Driver Handoff

Keep the generic `CarState.update_steering_pressed()` behaviour unchanged so
other cars and torque controllers do not inherit an angle-control-specific
policy. Add a controller-local handoff state for Hyundai angle-control cars:

- `normal`: existing angle control is allowed;
- `yielding`: command follows the measured angle and steering authority remains
  reduced;
- `reacquiring`: control returns from the measured angle with conservative
  command-rate acceleration.

Confirmed `steeringPressed` enters `yielding` immediately. The controller may
leave `yielding` only after raw driver torque remains below 60% of
`STEER_THRESHOLD` for 0.4 seconds. Any renewed torque above the release
threshold resets that timer.

When release is confirmed, the command already equals the measured angle.
During `reacquiring`, disable the existing large-angle-error acceleration
shortcut. Return to `normal` after the command-to-target error falls below the
existing 8-degree large-error boundary. A new override at any point returns to
`yielding`. Lateral disengagement clears the handoff state and rate history.

The normal-state large-error acceleration remains unchanged so ordinary tight
turn response is not degraded.

### 3. Low-Speed Lag-Adjusted Curvature

Retain the existing lag-adjustment formula at normal road speeds. At low speed:

1. Preserve the raw ego speed before applying the existing numerical minimum.
2. Floor the heading divisor by the distance that would be travelled during
   the actuator delay at the numerical minimum control speed.
3. Compute the lag-adjusted curvature using the protected divisor.
4. Blend from the current MPC curvature (`curvatures[0]`) to the protected
   lag-adjusted curvature as raw ego speed rises from 1 m/s to 5 m/s.

At or below 1 m/s the output therefore stays at current MPC curvature. At or
above 5 m/s (18 km/h) the existing lag-adjusted response is fully restored.
The existing curvature-rate, lateral-jerk, transition damping, and final
`clip_curvature()` limits remain in force.

This addresses the invalid low-distance geometry rather than hiding the result
with an additional output smoother.

### 4. Manual-Steering Warning Before Turns

Add two explicitly advisory fields to `CarrotMan`:

- `advisoryTurnInfo`: the observed route manoeuvre type;
- `advisoryTurnDistance`: distance to the observed route manoeuvre.

Populate these fields whenever navigation observation is valid, regardless of
`naviControlAllowed`. Existing control fields (`atcType`, `xTurnInfo`,
`xDistToTurn`, desired speed, and active control state) remain neutral when
navigation control is not allowed. The new fields are consumed only by the
warning detector and must never be read by steering, longitudinal planning, or
desire generation.

Create a pure, stateful warning detector in the self-driving event layer. It
raises a warning when:

- lateral control is active;
- model lane-change state is off;
- the model is not already expressing the matching turn desire; and
- either a valid advisory left/right/roundabout turn is 3 to 8 seconds ahead,
  or a physical turn signal is held for 0.5 seconds at low speed without valid
  route advisory data.

The time-to-turn calculation uses a minimum 2 m/s denominator and also rejects
non-positive or implausibly distant advisory values. Route confirmation and a
per-manoeuvre latch prevent single-frame route noise and repeated alert starts.
The latch resets after the manoeuvre is passed, the route changes, or the
signal is cancelled.

Add a warning-only on-road event with:

- title: `교차로 자동조향 경로 없음`;
- instruction: `핸들을 잡고 직접 조향하십시오`;
- `VisualAlert.steerRequired`;
- a repeating prompt sound;
- no `NO_ENTRY`, disable, or lateral-override event type.

The driver remains able to take over immediately, and the handoff state from
section 2 prevents control from fighting the driver. If neither valid route
advisory data nor a physical turn signal exists, the system cannot know the
intended turn and must not invent one.

## Data Flow and Boundaries

```text
navigation observation
  -> CarrotMan advisory fields (display/warning only)
  -> SelfdriveD turn-warning detector
  -> warning-only OnroadEvent
  -> UI + audible prompt

model/radar/car state
  -> existing control paths
  -> local safety gates in RadarD, drive_helpers, and Hyundai CarController
```

No new persistent parameter is required. Initial thresholds are constants with
unit tests; later road-test tuning can adjust them in isolated follow-up
changes.

## Testing Strategy

### Radar arbitration tests

- A lane-changing SCC candidate with weak vision overlap is not selected.
- Three continuous, path-consistent frames confirm a normal fallback.
- A distance jump resets confirmation and removes a selected fallback.
- A strong-overlap, stable short-TTC obstacle confirms in two frames.
- A one-frame short-TTC spike does not use the emergency path.
- Switching away from a confirmed SCC source requires two stable alternative
  frames.

### Steering handoff tests

- One clear `steeringPressed` sample does not resume control.
- Torque below the release threshold for 0.39 seconds remains yielding.
- Torque below the threshold for 0.4 seconds enters reacquisition.
- Renewed driver torque resets the release timer.
- Reacquisition starts from measured angle without large-error acceleration.
- Normal-state large-error response retains its existing rate.

### Curvature tests

- A launch geometry matching the reported small-distance case returns current
  MPC curvature at 0.5 m/s.
- The correction is partially blended at 1.8 m/s.
- The existing high-speed result is unchanged at 5 m/s and above.
- Invalid/non-finite input handling remains finite.

### Warning tests

- A valid advisory turn with missing model intent warns inside the time window.
- Matching model turn intent suppresses the warning.
- Lane changes do not trigger the turn warning.
- Shadow navigation retains advisory fields while every control field remains
  neutral.
- A held low-speed physical turn signal provides the no-navigation fallback.
- A transient signal or stale navigation message does not warn.
- The event is warning-only and has both visual and audible presentation.

### Verification

Run the focused unit suites for radar, Hyundai angle control, laneless response,
navigation authority/shadow behaviour, and self-driving alerts. Then run schema
validation and Python compilation for every modified module. Because the native
Windows checkout does not resolve the repository's Linux symlinks, verification
must use a Linux/WSL environment with the repository test dependencies present.

## Rollout and Road-Test Follow-up

The first road test should validate the four logged scenarios in priority
order: false braking, steering handoff, launch curvature, and intersection
warning timing. Record lead source/track ID, SCC confirmation state, desired and
measured steering angle, handoff state, raw driver torque, desired curvature,
advisory turn information, and emitted warning event.

Threshold tuning after the road test is explicitly follow-up work. If any
fallback produces unexpected braking or steering authority, disable or revert
that isolated component rather than weakening unrelated gates.
