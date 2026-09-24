# Naver GitHub Workflow Implementation Plan

> Execute inline, issue by issue. The owner approved GitHub Issues plus Markdown
> records, cloud validation, and the Naver integration branch as the default.

**Goal:** Establish the issue and CI infrastructure before re-integrating Naver.
**Architecture:** Keep an exact upstream mirror and a separate integration branch.
**Tech Stack:** GitHub Issues, GitHub Actions, Python 3.12, Git.
**Spec:** `docs/naver/WORKFLOW.md` and acceptance criteria in issues #1-#14.

## Constraints

- Mirror only `ajouatom/openpilot:carrot-wip`; never force divergent history.
- Default branch is `codex/naver-support-20260924`.
- Do not publish private APK/capture/signing artifacts.
- Test claims must identify the tested commit and scope.

## Task 1: Establish workflow (issue #1)

- [x] Create acceptance-driven issue backlog and Markdown index.
- [x] Publish the integration branch and set it as default.
- [x] Implement scoped synchronization guard in `tools/naver_ci/sync_upstream.py`.
- [x] Add guard tests and CI with five-minute job limits.
- [ ] Commit and push only workflow/docs/CI files, referencing #1.
- [ ] Execute `Naver CI` and `Naver upstream mirror` on GitHub.
- [ ] Record exact-SHA results in #1 and close only after successful runs.

Run in Actions: `python -m unittest discover -s tools/naver_ci/tests -v`.
Check no-op, fast-forward, divergence, rewind, malformed SHA, wrong fork parent,
wrong repository and mutation target with `force=false`.

## Task 2: Re-integrate in dependency order

The detailed backlog is `docs/naver/ISSUES.md`. Implement #2 and #3 before #4;
then #5/#6/#7/#8. Prepare #9 before #10 and #11. Keep #14 open until runtime
evidence establishes the actual incident cause. Before each code task, record
its exact changed interfaces, focused regression commands and completion
evidence in the linked issue and a scoped Markdown note.

Do not copy the legacy controller over upstream: preserve new vehicle-navigation
fields and behavior. Run Java and Python suites in Actions when the respective
source has been ported. Add native Capnp round-trip coverage before schema work
is considered complete. A passing bootstrap CI is not controller validation.

## Task 3: External acceptance and release

Use #11 for phone/C3 and actual driving checks. Keep it open while waiting for
the user; finish unaffected code issues first. #12 produces the standalone APK
and paired external support notes only after acceptance, retaining experimental
status for any behavior not proven. Do not install the release APK implicitly.
