# Execution ledger: Naver re-integration

Plan: `docs/superpowers/plans/2026-09-24-naver-github-workflow.md`.
Specification: `docs/naver/WORKFLOW.md`; issue-specific acceptance criteria.

- #5 complete (code/synthetic scope): PR #25 merged as b2cf28bd. Exact tested
  HEAD e8594c4d passed 36015111635 and PR CI 36015421201 / docs 36015421154:
  403 input +121 controller +73 JVM +4 wire +8 infrastructure tests, plus
  Java-produced envelope -> strict Python parser -> canonical bump integration.
  Review findings on delayed/cross-session/overlapping finals are covered;
  real hook ordering and driving acceptance remain mandatory in #9/#11.

- #5 started from 3a35c615 on codex/naver-issue-5-bump. C3 RED run
  36012296572 at 9098d8e2 reproduced four absent-category bump failures;
  explicit highway, settings and invalid-distance cases remained blocked.
- C3 GREEN run 36012913714 at af24ae14: 121 controller tests passed. This run's
  new JVM job failed to compile because the synthetic Android Log fixture was
  not yet included; no Java mapping success is inferred from that failure.
- Ruling: retain the approved exact-Naver-bump category-missing exception, but
  never substitute category 8 or override explicit 0/1. Cost if wrong: false
  bump acceptance, bounded by exact source mapping, freshness and real-drive gate.
- Ruling: publish only the pure-JVM production/fixture subset early from #9 so
  #5 can test source-to-envelope mapping in Actions; full APK/DEX/signing and
  capture tooling remain unpublished and #9 stays open.
- Mapping audit confirmed gaps and created #19-#24. Route-before-guiding loss
  is recorded in #7. Production bump source pairing needs same-session, bounded
  age and ambiguity rejection before this task is complete.
- JVM RED 36013169310 confirmed three retained-source bugs (66 passed, 3 failed).
  36013547591 then exposed an empty diagnostic outcome token; use explicit
  bounded inactive/ambiguous reason tokens instead of the empty sentinel.
- GitHub receive-pack returned repeated HTTP 500 for e1e464e3. Local fallback
  used Android Studio JBR21 and checksum-verified JUnit1.11.4 on D: only to
  reproduce review cases. Three expected pairing failures reproduced; the
  unchanged discoveryUsesBoundClientPortAndConnectsToValidatedPacketSource test
  additionally failed on Windows JBR's loopback selector initialization.
- Review fix: ambiguity persists across time/session boundaries and through all
  outstanding finals, not only the first one. Single-use capture has a <1000ms
  budget. Ruling: lost final callbacks may suppress later bumps until restart;
  do not guess correlation. Cost: availability, which needs real hook-order
  confirmation in #9/#11 before production acceptance.
- Local focused GREEN: ProductionHooksTest 14/14 passed after the overlap fix.
  GitHub push recovered and published both retained local commits normally,
  without force or alternative ref writes. Cloud re-validation follows.
- Added a cross-language check: synthetic exact-class Java objects pass through
  ProductionRuntime/real envelope encoding, then strict Python parsing and the
  canonical type-22 adapter; a terminal frame then clears the session.

- #1 and #15 complete; exact Actions evidence is in EVIDENCE.md.
- #2 complete: PR #16 merged as 156ffd94; 389 input tests passed in run
  36002977499, plus PR checks 36003032281 and 36003032364. Runtime binding
  remains #4 and actual APN incident confirmation remains #14.
- #3 started: real Capnp roundtrip and compiled native/Python/browser comparison
  must fail before adding diagnostic fields. Preserve upstream ordinals 29-32.
- #3 complete: PR #17 merged as 238fbc85. GREEN run 36004264992 and PR runs
  36004519269/36004519276 passed: 4 real wire tests and 389 input tests.
- #4 complete: PR #18 merged as 530e3dbf. Exact code HEAD eb18a4ba passed
  Actions 36010063200, PR Actions 36010312912 and docs 36010312913:
  403 input + 107 controller + 4 real wire + 8 infrastructure tests.
- Next task: #6 camera lifetime and fallback. Runtime/bump code is connected, but
  bump/camera/route/UI and actual device acceptance remain open. Do not treat
  this milestone as a demonstrated fix for the historical driving incident.
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
- Fresh read-only review found GPS re-stamping and the legacy 4096/256 route
  capacity mismatch. Run 36009498704 reproduced both and the same-frame receipt
  race. Run 36009767192 verified their fixes and reproduced stale traffic TS.
- Ruling: treat the review's minor traffic TS finding as important because old
  traffic must not appear freshly received. Gate GPS/traffic projection by item
  receipt revision and publish the actual receiver receipt, not the 20 Hz tick.
- Ruling: a V2 connection with neither items nor guiding status is idle, not an
  owner. A guiding session with no items remains owner. Disconnect is transport
  loss and expires after its 10-second lease. Update the existing V2 fixtures
  to these approved owner semantics and include that suite in controller CI.
- Review boundary retained: phone mapping/physical braking and #5-#8 acceptance
  are not established here. Exact-head CI is checked by the executor, not inferred
  from the review. The reviewer found no other concrete EOF/tombstone regression.
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
