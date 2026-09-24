# Execution ledger: Naver re-integration

Plan: `docs/superpowers/plans/2026-09-24-naver-github-workflow.md`.
Specification: `docs/naver/WORKFLOW.md`; issue-specific acceptance criteria.

- #1 and #15 complete; exact Actions evidence is in EVIDENCE.md.
- #2 complete: PR #16 merged as 156ffd94; 389 input tests passed in run
  36002977499, plus PR checks 36003032281 and 36003032364. Runtime binding
  remains #4 and actual APN incident confirmation remains #14.
- #3 started: real Capnp roundtrip and compiled native/Python/browser comparison
  must fail before adding diagnostic fields. Preserve upstream ordinals 29-32.
- #3 complete: PR #17 merged as 238fbc85. GREEN run 36004264992 and PR runs
  36004519269/36004519276 passed: 4 real wire tests and 389 input tests.
- Next task: #4 runtime provider binding. Current modules are not yet connected
  to CarrotMan/CarrotServ, so do not install this branch as a Naver driving fix.
- #4 started from 4aa63bfe on codex/naver-issue-4-runtime. RED covers canonical
  controller projection, sticky owner switching, guiding-only sessions, exact
  lease expiry, terminal tombstones and item freshness independent of heartbeat.
- Ruling: normalize inputs into existing CarrotNaviControl rather than copying
  the old controller. Preserve upstream speed candidates and gas behavior;
  safety policy refinements remain #5/#6 and route acceptance remains #7.
- #4 RED: run 36006904050 at 0a13c6e6 confirms missing runtime adapter;
  existing 389 tests pass. First adapter run 36007357358: 397 pass, two fixture
  defects. Lease assertion must inspect one selection (the transition reason
  is consumed); road-width fixture must contain a present turn instruction.
  Revised-safety freshness fixture explicitly supplies its source revision.
- #4 intermediate GREEN: run 36008397536 at 021a1c4e passed 399 input,
  88 controller/upstream, 4 wire and 8 infrastructure tests. The real controller
  harness uses real Capnp builders, not permissive message mocks.
- #4 additional RED: run 36008563260 reproduced returning HTTP identity sequence
  reset and missing source-bound auxiliary routing. Run 36009080011 passed
  those cases and exposed only pre-guidance route buffering (401 input passed).
  Fix: monotonic receiver legacy sequence, source/session-bound auxiliary data,
  and at most four pending auxiliary sessions without extending owner leases.
- #3 RED confirmed: run 36003733463 at 30340ced, two missing-diagnostic failures
  and one upstream-ordinal test passed. Initial runner include-path error was
  corrected before interpreting RED. Added fields use ordinals 33-42; compact
  fields are append-only after all existing upstream fields.
- Wire test correction: the Python compact encoder consumes a Capnp reader,
  not a dictionary. Use the same real message as C++ (run 36004009626 exposed
  the fixture mismatch before reaching browser verification).
- #2 started from ff9a26a3. RED: route-revision input smoke test on fresh upstream.
- RED: https://github.com/bin9208/openpilot/actions/runs/36002651544 (912f4ec5), one expected failure.
- GREEN candidate: preserved parser, ingress and immutable store ported without
  controller changes. Real socket tests cover fragmentation, bounded clients,
  lease retention, terminal frames and UDP discovery response ports.
- Ruling: #2 introduces protocol, standalone socket ingress and required immutable
  source-state types/store. #4 binds them to current CarrotMan/CarrotServ and
  tests runtime source selection. This avoids overwriting the newer controller.
- Ruling: the preserved ingress suite has two CarrotMan wiring tests; keep those
  for #4 rather than claiming manager integration from standalone input tests.
- Cloud runs replace local full-suite execution as explicitly requested by the
  owner. Local checks remain syntax/diff inspection; exact run URLs are the gate.
