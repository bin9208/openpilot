# 2026-09-29 non-steering repair / dev test checkpoint

Scope: repair confirmed issues, retain user choice of Naver or Tmap, and stop
when actual dev vehicle testing is required. Steering is excluded.
This is not a production/field acceptance report.

## Branch and validation contract

- Issue-scoped feat/fix branches feed `codex/naver-support-20260924` (dev).
- `carrot-wip` remains the unchanged upstream mirror/main role. No repair is
  merged or cherry-picked there. Its existing tests workflow is run separately.
- Feature, integrated dev and untouched main run independently at recorded
  SHAs. A feature pass is not an integrated-dev pass; a main baseline pass does
  not test Naver code that main does not contain.
- APK/raw capture/key/device identity stay off GitHub. Existing local-only
  replay and fixture files are not staged. No phone installation is requested.

## Completed code scope

| Issue | Feature head | Change and boundary |
| --- | --- | --- |
| #34 / PR#37 | `9440584f` | Exact safety lifecycle grammar; no permissive parsing |
| #36 / PR#38 | `e3e1d527` | Clip first route segment to horizon; no curve threshold/steering tuning |
| #35 / PR#39 | `e14ab5ad` | Unknown road publication in both messages, HUD--, downstream unknown-road cruise guard |

Issue notes carry RED/GREEN reproduction and constraints. The final fresh
review found one Important #35 publication mismatch; both messages now share
one publication value and four unknown30/80 cases fail before/pass after the
correction. Four known-road controls also pass. No unresolved Critical or
Important code finding remains from that review after the regression fix.

Local full-controller collection could not import aiohttp, and native cruise
imports lacked opendbc. These are not passing local suites; cloud full-module
and build runs are mandatory. Do not silently ignore their results.

## Review rulings and known limitations

- Reported22km/h cause: unresolved #40. Need actual C3 commit, Params,
  desiredSpeed/desiredSource/decelProvider and vEgo before changing speed floors.
  Cost of this decision: no promise that the low-speed incident is eliminated.
- Camera/road source mapping: #24/#19 remain open. No safety observations in the
  reported short segment cannot establish a missing getter or authorize stale
  safety reuse. Cost: Naver may still require HDA camera fallback; HDA-less
  acceptance is not yet proved. Road limit may correctly remain unknown.
- Actual vehicle timing, loaded behavior and steering are not inferred from
  simulation/CI. Steering was excluded by the user.
- Private DB acceptance is local evidence only; 4866 retained frames validated
  after #34 does not mean lossless entire-trip capture.
- Pre-existing geographic projection and cluster hold design are retained;
  the narrow route/publication repairs do not validate those broader designs.
- Exact-SHA Actions, not the review verdict, determine software-gate results.
- The review's trailing-blank-line nit was removed within the changed test block.

## Required dev evidence before further tuning or main integration

1. Record the installed dev commit and current values of AutoCurveSpeedLowerLimit,
   MapTurnSpeedFactor, TurnSpeedControlMode, AutoTurnControlSpeedTurn,
   ApplyModelSpeed and AutoNaviSpeedCtrlMode. Prefer existing incident logs first.
2. Naver unknown road must show LIMIT-- on the main HUD; the cluster must not
   keep refreshing an invented30/prior-app value. Its existing bounded hold
   and valid vehicle-source priority remain unchanged. Tmap valid road values
   must still appear normally.
3. Check route versus ATC/vision/gas/camera/bump as the winning speed source.
   Distinguish desired speed from actual vehicle speed. Record route/GPS
   alignment and accelerator override without deliberately reproducing danger.
4. For camera availability, correlate Naver's actual camera indication with
   naviOwner, naviSafetyAgeMs, naviSafetyRejection, xSpdType/Limit/Dist,
   desiredSource and decelProvider. Do not disable a working vehicle safeguard
   merely to force an N.cam label; HDA fallback is legitimate without valid input.
5. Confirm N.bump release/target and gas override remain unchanged, then
   Naver→Tmap→no-navigation ownership/HDA behavior. #11 remains the field gate.

The installed TEST APK need not change for these C3/host-only repairs. Restart
Naver before capturing another trip after a DB export, which quiesces its
current diagnostic writer. No installation, main merge or shutdown is part of
this checkpoint. Stop work after the integrated dev software gates pass and
hand the actual-device steps back to the user.

## Evidence links

- #34 feature NaverCI36506864444 / APK36506867049 / full36506869375.
- #36 feature NaverCI36507277187 / APK36507279635 / full36507281922.
- #35 final feature NaverCI36508900548 / APK36508902907 / full36508905611.
- Main baseline43203371: full36508410300 (no code changes).
- Integrated-dev exact SHA and run IDs will be recorded in #11/#34/#35/#36
  and the local D-drive result record after these gates complete.
