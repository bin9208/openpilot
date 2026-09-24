# Execution ledger: Naver re-integration

Plan: `docs/superpowers/plans/2026-09-24-naver-github-workflow.md`.
Specification: `docs/naver/WORKFLOW.md`; issue-specific acceptance criteria.

- #1 and #15 complete; exact Actions evidence is in EVIDENCE.md.
- #2 started from ff9a26a3. RED: route-revision input smoke test on fresh upstream.
- Ruling: #2 introduces protocol, standalone socket ingress and required immutable
  source-state types/store. #4 binds them to current CarrotMan/CarrotServ and
  tests runtime source selection. This avoids overwriting the newer controller.
- Ruling: the preserved ingress suite has two CarrotMan wiring tests; keep those
  for #4 rather than claiming manager integration from standalone input tests.
- Cloud runs replace local full-suite execution as explicitly requested by the
  owner. Local checks remain syntax/diff inspection; exact run URLs are the gate.
