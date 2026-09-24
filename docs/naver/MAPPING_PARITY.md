# Naver / Tmap functional mapping parity

Target: users choose either app; both feed the current common controller.
Similar-looking map objects are not evidence of equal types, units or lifetime.
This matrix separates implemented code, synthetic checks and device acceptance.

| Input capability | Naver production adapter | Remaining work |
|---|---|---|
| Guiding/stopped/arrived | Exact lifecycle subtypes; terminal sessions are not reused | Runtime device acceptance #11/#14 |
| Current/next TBT | Distance, main/road text and six recognized maneuver enum names | More turns/forks/ramps/roundabouts #20 |
| Road speed limit/category | Not mapped; both validity flags are false | Exact accessors and unknown/highway distinction #19 |
| Speed bump | Exact source SafetyCode + positive distance, matched final safety code; canonical secondary type 22; pairing tests #5 complete | Actual callback ordering and driving #11 |
| Cameras | One fixed/mobile/section safety item; lifetime/HDA fallback tests #6 complete | Richer source semantics #24; actual driving #11 |
| Route shape | Exact CurrentRoute to path-point chain, 4096-point bound; initial buffer/cursor/common gas tests #7 complete | Real route and curve-speed evidence #11 |
| Trip distance/time, destination, off-route | Zero/false placeholders, not source-backed values | Exact trip/status mapping #21 |
| Lane guidance | Not mapped; diagnostic observations are not production support | Schema/accessor and consumer parity #22 |
| Traffic signal/countdown | Not mapped | First establish whether the app exposes usable data #23 |
| Vehicle position/heading | No Naver production envelope field; existing phone/device GPS path is separate | Evaluate with route mapping #7/#21; do not infer from route points |

## Exact bump boundary (6.8.0.5)

`GuidanceSafety.getCode()` must yield the profiled SafetyCode enum and
`isSpeedBump()` must be true. `GuidanceSafety.distance()` supplies the finite,
strictly positive remaining distance. The final `SafeControlItem.getSign()` /
`SafetySign.getType()` / safety type `getCode()` must independently identify
the same bump code. Live display extras were observed absent in the preserved
investigation; production must not invent a distance from those absent extras.

Only the reduced immutable source values are retained, not the source object.
Pairing is same-thread, same-guiding-session, single-use and time bounded.
Ambiguous multiple unconsumed sources fail closed until all expected final
callbacks drain; a later complete pair can then recover. Lost final callbacks
may suppress subsequent bump mapping on that thread until the app is restarted.
This availability trade-off must be checked in #9/#11 against actual injected
hook ordering; it is safer than guessing which old/new equal-code object belongs
to a distance. A rejected pair emits safety absence, not an older bump.

C3 permits this verified bump without a road category, while explicitly provided
0/1 still means highway and blocks bump control. The stored road category is not
rewritten to a guessed non-highway value. Existing Tmap category rules and shared
bump timing, target, endpoint and pedal calculations remain unchanged.

## Evidence boundaries

Historical profile notes mark current/next TBT accessor values and Guiding/Stopped
roots as observed. Bump nested/source and route accessors still require current
field confirmation. Passing reflection fixtures proves code behavior against
those shapes, not that a current app build invokes them during an actual drive.
No feature in this table is production-complete merely because an APK compiled.

The pure JVM subset needs no original APK, capture or key. Full source-only
packaging/Android DEX tests (#9) now pass on synthetic inputs. A local single
field TEST APK (#27) was built and statically verified on 2026-09-25, without
installation. New-device offline evidence #10, real driving #11 and public
release #12 remain open. The test artifact is not full Tmap mapping parity.
