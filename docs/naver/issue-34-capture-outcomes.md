# Issue 34: producer/diagnostic grammar parity

The field runtime emits `safety_source_inactive` outside guidance and
`safety_source_ambiguous` when source pairing cannot identify one event.
The offline validator omitted both exact tokens and rejected otherwise valid
captures. Accept those two outcomes without relaxing any privacy, descriptor,
frame-size, scalar or unknown-result checks. No control or APK behavior changes.

TDD: baseline 118 capture tests passed. Four new parser/SQLite cases failed
with the expected observation-grammar error before the allowlist correction.
The negative case appends an unknown suffix and must remain rejected.

Verification: focused capture parser/offline DB suites; source toolkit and
Naver CI on the feature SHA, then independent dev SHA checks. Private captures
are revalidated locally only. Device acceptance is not implied by DB validity.

Docs-Not-Needed: host diagnostic parser compatibility only; no vehicle setting,
control or user-facing navigation behavior changes.
