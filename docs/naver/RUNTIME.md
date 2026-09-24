# Runtime source integration (#4)

Base: `4aa63bfe`. The controller is the current upstream CarrotServ, not the
archived controller. NavigationRuntime converts Naver v1, Tmap legacy and
Carrot Navi v2 into immutable source snapshots and then CarrotNaviControl.
Only the selected source is projected before route-speed calculation.

## Lifecycle and transport

- New guiding activation can change the owner. Background updates from a
  previously active source do not repeatedly steal ownership.
- TCP EOF/reset/timeout records transport loss for its exact source/session.
  It does not clear the snapshot. Default leases: Naver 2 s, legacy Tmap 4 s,
  Carrot Navi v2 10 s. The clock is receiver-local monotonic time.
- Explicit Naver stopped/arrived ends the session. A higher sequence cannot
  reactivate that ID; a new guidance session needs a new ID.
- Duplicate/out-of-order frames do not refresh the owner lease.
- UDP discovery is not a navigation activation. UDP timeout does not erase a
  live TCP peer. The bounded TCP ingress serves at most four clients.
- HTTP remains a legacy Tmap transport; Naver schema envelopes are rejected.

## Freshness and compatibility

The owner lease and item freshness are separate. V2 item receipt timestamps
are consumed without renewing them from the cereal publication heartbeat.
Revised Naver safety objects keep their original receipt when unchanged.
Guiding without a current maneuver still owns navigation/APN until termination
or lease expiry. No stale safety object is resurrected to fill missing input.

CarrotMan diagnostics carry the selected navigation owner/session separately
from the actual winning deceleration provider/reason. Visual presentation and
detailed rejection text remain #8. Current upstream vehicle-camera, rear-camera,
gas override and countdown logic is preserved here. Changes to fallback policy
are #6; bump mapping is #5; route/gas acceptance is #7.

## Evidence boundary

Actions runs pure input tests, real CarrotMan/CarrotServ integration with only
native Params/hardware/IPC substituted, real Capnp publication, unchanged
upstream candidate/gas regressions and native/Python/browser wire comparisons.
These tests do not establish phone object mapping, physical braking behavior,
or the cause of the user's historical APN disappearance (#14).

Raw captures and private APK/signing files are not part of this repository.
