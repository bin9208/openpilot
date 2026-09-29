# Issue 35: unknown Naver road limit

Naver's current adapter declares road.limitValid=false. CarrotServ initialized
nRoadLimitSpeed to30 and retained prior provider values, then published them
without validity. Publish unknown0 for a Naver owner with invalid road data;
the native HUD renders `--`, matching the web mini HUD's existing convention.
Valid road updates and independent camera limits remain distinct. Do not copy
camera values into road values or invent a missing Naver accessor (#19).

Preserve CarrotServ's internal retained value, road-control validity gate and
gas/change state. However the published field is also consumed by cruise.py:
ApplyModelSpeed<0 used an unknown0 road value as a zero model-speed cap before
its existing road-adjustment guard. Add an early nonpositive-road guard so
unknown road state preserves the current cruise set speed. Valid-road model
behavior remains enabled even when AutoSpeedUptoRoadSpeedLimit=0.

TDD evidence:
- Four real-controller Naver publication cases failed; Tmap's existing six
  sample debounce case passed after correcting the fixture to its real contract.
- Three unknown native-HUD cases failed, camera and known-road cases passed.
- The real cruise method with the same seven regression bodies failed two
  unknown-road/negative-model cases; five valid or already-protected cases passed.
- Focused WSL limit/camera/bump tests and native HUD tests are local checks.
  Full local controller collection lacks aiohttp; full native cruise import
  lacks the opendbc module. Do not count these as passing suites. Full imports,
  build and regression suites run in GitHub Actions with repository dependencies.

No steering or speed-threshold tuning. User-visible validity handling is
documented in the paired speed/deceleration guides. This fixes false LIMIT
presentation and its zero-value consumer hazard, not missing road mapping or
the unproven real-drive22km/h root cause. Dev vehicle acceptance remains open.
