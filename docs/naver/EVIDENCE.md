# Naver integration evidence

## Single field TEST checkpoint: 2026-09-25

- #6 camera lifetime/HDA: PR #26, eb6f1494, runs 36019899030 / 36019902027.
- #7 route/common gas: PR #29, aa0c0ba9, runs 36020322484 / 36020410745.
- #8 owner/provider UI: PR #30, a9daa38a, runs 36021787798 / 36021963273.
- #9 source-only APK tooling: PR #31, 1d346a76, runs 36023948126 / 36023956000.
- #28 live-mode invalidation: PR #32, d3b4db7f, runs 36024999919 / 36025005211.
- #27 packager: PR #33, 0755579bf404f9b2dfdcff94c4e4ba7c5d8b8b7b.
  [Naver CI](https://github.com/bin9208/openpilot/actions/runs/36025875033)
  and [APK source tests](https://github.com/bin9208/openpilot/actions/runs/36025875001)
  passed: 466 host tests plus diagnostic/field/production Java/DEX suites.
  PR checks 36026267627 / 36026267839 and docs 36026267650 also passed;
  GitHub check rollup was verified before merge.

Local artifact, relative to the D work root:
`artifacts/20260925-field-test-1/single/NaverMap-6.8.0.5-carrot-field-TEST.apk`.
Size: 206870985 bytes.
SHA256: `255392587c75d713130b9b423ee54db094cc06c57ae6cbfd1e1f39094694bb01`.
Field-v4 payload SHA256:
`dcfa462d9e78ef72fc24bc1047ea11f37e733fbe11dffcf461088e4b26373aa1`.
KO/EN notes, checksums and provenance sit beside the APK, never inside it.

The frozen original-input hashes and fresh split-build anchor checks passed.
The merged artifact preserves all DEX bytes and 33 original arm64 libraries;
standalone identity, SDK/manifest inventory, signature and alignment passed.
Final hash was independently reread after atomic publication.
The original signing key was preserved; its legacy DPAPI password was not
decryptable in this account. A separate local TEST key was used under the
owner's prior permission allowing a different signature. It may prevent an
in-place update of the installed app. No install, uninstall, data clear or shutdown.

Not acceptance: new-device offline recording (#10), actual bump/route/camera
driving (#11), historical APN incident (#14), production/full mapping (#19-#24).
Earlier sections below are historical checkpoints, not current artifact claims.

## Bump mapping/common control: issue #5

[PR #25](https://github.com/bin9208/openpilot/pull/25) merged as
`b2cf28bd68c507f742eeb2fbb3c4c66390ea42d0`.
Tested code HEAD: `e8594c4d5a034377d26f81aee7f7c3404dccfb5b`.

- [Code CI: success](https://github.com/bin9208/openpilot/actions/runs/36015111635)
- [PR CI: success](https://github.com/bin9208/openpilot/actions/runs/36015421201)
- [Paired user docs: success](https://github.com/bin9208/openpilot/actions/runs/36015421154)
- 609 tests: 403 input/session, 121 real controller, 73 JVM mapping/runtime,
  4 native/Python/browser wire, 8 infrastructure. No skips in the JVM suite.
- Actual Java-produced bump/terminal JSON also passed the strict Python parser
  and canonical adapter. Exact-class inputs are synthetic, not live phone data.
- Fresh read-only review exposed additional delayed-final overlap cases; these
  were reproduced and fixed. Ambiguity now lasts through all outstanding finals.
- Local syntax/diff and user-doc checker passed. A Windows JBR selector error
  occurred during the temporary local fallback; unchanged sender tests passed
  again in the final Linux CI. No Android APK was rebuilt or installed.

Production safety mapping and driving acceptance are not asserted. Missing final
callbacks can suppress later bumps until restart; actual 1:1 hook ordering is a
required #9/#11 gate. Remaining Tmap mapping gaps are listed in MAPPING_PARITY.md
and issues #19-#24; only the reviewed pure-JVM toolkit subset is public.

## Runtime binding: issue #4

[PR #18](https://github.com/bin9208/openpilot/pull/18) merged as
`530e3dbf8c33905020be1e88aaa213d492af2356`.
Tested code HEAD: `eb18a4baeb794cbb9d86b8c473cbb875c0aa943a`.

- [Code CI: 522 passed](https://github.com/bin9208/openpilot/actions/runs/36010063200)
- [PR CI: success](https://github.com/bin9208/openpilot/actions/runs/36010312912)
- [Paired user docs: success](https://github.com/bin9208/openpilot/actions/runs/36010312913)
- Coverage: 403 input/session, 107 runtime/upstream controller, 4 real wire,
  8 infrastructure tests. Native Params/hardware/IPC are host substitutes;
  controller logic and Capnp message builders are real.
- Read-only review findings were reproduced and fixed: GPS sample re-stamping,
  legacy route capacity and stale traffic timestamp. Same-frame receipt ordering
  and pre-guidance route buffering have additional regressions. RED run links
  and rulings are in PROGRESS.md.
- Local syntax checks and `python tools/docs/check_user_docs.py --base 4aa63bfe`
  passed. No phone install, private captures or signing data were published.

This milestone establishes runtime source integration, not real-world braking,
production phone mapping or the cause of historical APN loss. Those remain open.

## Workflow bootstrap: issue #1

Commit: `56975cf421e6689e568ed64a8f6c11f7ef0c9607`.

- [Naver CI: success](https://github.com/bin9208/openpilot/actions/runs/36001662212)
- [Upstream mirror: success](https://github.com/bin9208/openpilot/actions/runs/36001681025)
- Fork and upstream `carrot-wip` both resolved to
  `43203371004e035bdb70a00a8dad29a4b657c6c3` after execution.
- The mirror run verified the already-current no-op case. Guard tests exercise
  fast-forward/refusal logic; this run is not a live divergent-branch trial.
- Local checks: scoped diff whitespace check. Repeatable tests ran on GitHub.

Scope: repository workflow tests and shared-entry-point Python syntax only.
No Naver controller/phone/vehicle acceptance is claimed. Issues #2-#14 remain
separate acceptance gates. Node 20 deprecation warnings are tracked in #15.

## Current Actions runtime: issues #1 and #15 complete

Commit: `1838b1f2499bd723ce0ed5639aa875d7644d029d`.

- [Naver CI: success](https://github.com/bin9208/openpilot/actions/runs/36001902423)
- [Upstream mirror: success](https://github.com/bin9208/openpilot/actions/runs/36001932366)
- Uses checkout v7 and setup-python v6, matching current upstream versions.
- Issues #1 and #15 were closed with the above exact-commit evidence.
- Remaining integration and device issues are open. Next implementation target
  is #2 (protocol/ingress), followed by #3 (wire schema) and #4 (source lifecycle).
