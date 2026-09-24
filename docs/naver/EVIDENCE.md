# Naver integration evidence

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
