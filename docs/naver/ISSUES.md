# Naver re-integration backlog

Base: upstream `43203371004e035bdb70a00a8dad29a4b657c6c3`.
GitHub status is authoritative. Update this index at verified checkpoints;
link CI evidence rather than copying full logs into chat.

Verified code checkpoints: #1-#9, #15 and #28 are complete. Local single TEST
APK delivery #27 is complete via PR #33; evidence is in EVIDENCE.md.
Device/drive acceptance and full Tmap mapping parity remain open.

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
| [#19](https://github.com/bin9208/openpilot/issues/19) | Road limit/category source mapping | #5, #9 |
| [#20](https://github.com/bin9208/openpilot/issues/20) | Full TBT maneuver parity | #9 |
| [#21](https://github.com/bin9208/openpilot/issues/21) | Trip/ETA/destination/off-route mapping | #7, #9 |
| [#22](https://github.com/bin9208/openpilot/issues/22) | Lane mapping and consumers | #8, #9 |
| [#23](https://github.com/bin9208/openpilot/issues/23) | Traffic signal availability/mapping | #9 |
| [#24](https://github.com/bin9208/openpilot/issues/24) | Detailed/secondary/section camera mapping | #6, #9 |
| [#27](https://github.com/bin9208/openpilot/issues/27) | Local single FIELD TEST APK (complete; not installed) | #5-#9 |
| [#28](https://github.com/bin9208/openpilot/issues/28) | Live safety-mode invalidation (complete) | #6 |

The functional target and current evidence levels are in [MAPPING_PARITY.md](MAPPING_PARITY.md).

## Initial evidence

The older committed parser rejects a route frame containing `revision`.
A synthetic comparison reproduced ownership expiry after two seconds; the
preserved corrected parser accepted route plus bump. This does not prove the
cause of the user's running-device incident. Keep #14 open until runtime
versions and evidence are correlated.

Original device captures and artifacts are preserved locally, not published.
