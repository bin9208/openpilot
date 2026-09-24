# Naver integration workflow

Approved by the repository owner on 2026-09-24. GitHub Issues are the backlog;
Markdown is the second durable record. Working source and private artifacts
reside on the D drive.

## Branch policy

- `carrot-wip` mirrors `ajouatom/openpilot:carrot-wip`, fast-forward only.
- `codex/naver-support-20260924` is the default integration branch.
- The default branch runs the upstream mirror every six hours and on demand.
  A divergent mirror fails visibly; no force push or automatic merge commit.
- Upstream changes are integrated into the Naver branch deliberately with an
  issue and CI. The mirror job never updates the integration branch.
- After initial bootstrap, use issue-scoped commits and reviewable pull requests.

## Completion rule

Create an issue with acceptance criteria before changes. Link design notes,
commit/PR and the successful Actions run for that exact commit. Close only
after every criterion is satisfied. New defects get new issues. Device/drive
issues remain open until actual device evidence is recorded.

Run cheap local diff/syntax checks; use Actions for repetitive tests and builds.
CI failure requires investigation, not disabling a test. Scope jobs with paths,
timeouts and concurrency cancellation. Initial Naver CI tests infrastructure
only: it is not evidence that the Naver controller or APK works.

Never publish original APKs, signing keys, credentials, device identifiers,
raw SQLite or route captures. Use synthetic fixtures in cloud CI. Package and
sign actual field APKs locally and keep user documentation outside the APK.

## Integration invariants

- Users can choose Tmap or Naver; preserve the latest upstream common controller.
- EOF records exact-session transport loss; only lease expiry or an explicit
  terminal ends ownership. Stopped/arrived IDs remain terminal at higher sequence.
- Navigation owner and actual deceleration provider are distinct diagnostics.
- New route revisions and compatible older frames both parse correctly.
- Preserve upstream CarrotMan ordinals 29-32; allocate diagnostic fields after
  the upstream fields and update every native/compact/web reader consistently.
- Never reuse obsolete safety information to conceal a missing current input.
- Preserve upstream vehicle-navigation capabilities, including newly added
  candidates; do not import outdated assumptions about vehicle bump support.

## References

- [Backlog](https://github.com/bin9208/openpilot/issues)
- [GitHub scheduled workflow rules](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule)
- [Workflow token event behavior](https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/trigger-a-workflow)

Updates made by GITHUB_TOKEN do not automatically launch another push workflow.
The mirror result is checked explicitly. Controller validation runs on Naver
branch pushes/PRs, not automatically on a mirror update.
