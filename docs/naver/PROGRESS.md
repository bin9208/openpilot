# Execution ledger: Naver re-integration

Plan: `docs/superpowers/plans/2026-09-24-naver-github-workflow.md`.
Specification: `docs/naver/WORKFLOW.md`; issue-specific acceptance criteria.

- #1 and #15 complete; exact Actions evidence is in EVIDENCE.md.
- #2 complete: PR #16 merged as 156ffd94; 389 input tests passed in run
  36002977499, plus PR checks 36003032281 and 36003032364. Runtime binding
  remains #4 and actual APN incident confirmation remains #14.
- #3 started: real Capnp roundtrip and compiled native/Python/browser comparison
  must fail before adding diagnostic fields. Preserve upstream ordinals 29-32.
- #3 RED confirmed: run 36003733463 at 30340ced, two missing-diagnostic failures
  and one upstream-ordinal test passed. Initial runner include-path error was
  corrected before interpreting RED. Added fields use ordinals 33-42; compact
  fields are append-only after all existing upstream fields.
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
