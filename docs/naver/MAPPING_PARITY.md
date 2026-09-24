# Naver / Tmap functional mapping parity

Target: users choose either app; both feed the current common controller.
Similar-looking map objects are not evidence of equal types, units or lifetime.
This matrix separates implemented code, synthetic checks and device acceptance.

| Input capability | Naver production adapter | Remaining work |
|---|---|---|
| Guiding/stopped/arrived | Exact lifecycle subtypes; terminal sessions are not reused | Runtime device acceptance #11/#14 |
| Current/next TBT | Distance, main/road text and six recognized maneuver enum names | More turns/forks/ramps/roundabouts #20 |
| Road speed limit/category | Not mapped; both validity flags are false | Exact accessors and unknown/highway distinction #19 |
| Speed bump | Exact source SafetyCode + positive distance, matched final safety code; canonical secondary type 22 | Pairing freshness tests #5; actual driving #11 |
| Cameras | One fixed/mobile/section safety item; no equivalent secondary or detailed section lifecycle | Lifetime/fallback #6; richer source semantics #24 |
| Route shape | Exact CurrentRoute to path-point chain, bounded to 4096 points | Callback ordering, curvature/gas parity #7; real route evidence #11 |
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
Ambiguous multiple unconsumed sources must fail closed; the next complete pair
can recover. A rejected pair emits safety absence, not an older bump.

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

The public JVM test subset contains seven reviewed Java production source files
and synthetic class fixtures. It needs no original APK, private capture, signing
key, Android SDK or DEX build. Full packaging remains #9, offline evidence #10,
real driving #11, and release #12. No APK is installed by this code task.
