# Naver re-integration backlog

Base: upstream `43203371004e035bdb70a00a8dad29a4b657c6c3`.
GitHub status is authoritative. Update this index at verified checkpoints;
link CI evidence rather than copying full logs into chat.

Verified checkpoint: #1, #2, #3, #4 and #15 are complete. Next is #5.
Issue #4 landed via PR #18; test and review evidence is in EVIDENCE.md.

| Issue | Scope | Dependency |
|---|---|---|
| [#1](https://github.com/bin9208/openpilot/issues/1) | Branches, mirror, CI and records | none |
| [#2](https://github.com/bin9208/openpilot/issues/2) | Naver protocol/route revision and ingress | #1 |
| [#3](https://github.com/bin9208/openpilot/issues/3) | Schema and all wire readers | #1 |
| [#4](https://github.com/bin9208/openpilot/issues/4) | Provider selection, lease and tombstones | #2 |
| [#5](https://github.com/bin9208/openpilot/issues/5) | Bump mapping/common control | #3, #4 |
| [#6](https://github.com/bin9208/openpilot/issues/6) | Camera lifetime and vehicle fallback | #3, #4 |
| [#7](https://github.com/bin9208/openpilot/issues/7) | Route and gas override | #3, #4 |
| [#8](https://github.com/bin9208/openpilot/issues/8) | Connection and provider display | #3, #4 |
| [#9](https://github.com/bin9208/openpilot/issues/9) | APK source and Linux/Java CI | #1 |
| [#10](https://github.com/bin9208/openpilot/issues/10) | Offline device capture/privacy | #9 |
| [#11](https://github.com/bin9208/openpilot/issues/11) | Actual drive acceptance | #5, #6, #7, #8, #10 |
| [#12](https://github.com/bin9208/openpilot/issues/12) | Standalone artifact and support notes | #11 |
| [#13](https://github.com/bin9208/openpilot/issues/13) | Future upstream integration procedure | #1 |
| [#14](https://github.com/bin9208/openpilot/issues/14) | Actual APN loss incident diagnosis | device evidence |
| [#15](https://github.com/bin9208/openpilot/issues/15) | Actions runtime deprecation | #1 |

## Initial evidence

The older committed parser rejects a route frame containing `revision`.
A synthetic comparison reproduced ownership expiry after two seconds; the
preserved corrected parser accepted route plus bump. This does not prove the
cause of the user's running-device incident. Keep #14 open until runtime
versions and evidence are correlated.

Original device captures and artifacts are preserved locally, not published.
