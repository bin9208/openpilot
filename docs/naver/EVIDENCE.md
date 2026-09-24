# Naver integration evidence

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
