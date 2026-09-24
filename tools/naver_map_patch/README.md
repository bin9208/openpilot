# Naver Map 6.8.0.5 source-only patch toolkit

This tree contains our host packaging code, Java hooks, bounded offline SQLite
recorder and synthetic tests. Original apps and derived binaries are local-only.
Current integration evidence is in `docs/naver/PROGRESS.md`; imported historical
captures are not acceptance evidence for this checkout.

## Build target and boundary

- Frozen package `com.nhn.android.nmap`, version 6.8.0.5 / 60800007.
- Android 11+ arm64 test target. No promise of every Android device working.
- Java21, Gradle9.0.0, SDK platform36 / BuildTools36.0.0. Gradle dependencies
  use strict checksum verification metadata.
- GitHub tests create synthetic APKs/keys only. Never publish original APK,
  DEX, resources, keys, credentials, raw DB/routes/logs or device identifiers.
- `payload/` and `dexpatch/src/` are reviewed source, not compiled payloads.
- Capture-derived accessor fixtures and old replay helpers remain local.

## Local test build

Run `python tools/naver_map_patch/naver_patch.py doctor --json` first.
Use `build --base <original-base> --splits-from <original-apk+> --profile 6.8.0.5
--output <fresh-D-output> --keystore <local-p12> --store-password-env <ENV_NAME>
--key-password-env <ENV_NAME> --field-acceptance --json`.
Passwords belong only in process environment, never command arguments or Git.
Set TEMP/TMP/TMPDIR and GRADLE_USER_HOME to D-drive directories before building.

The existing build creates a split set. Single TEST packaging is tracked in
issue #27 and is distinct from public release #12. The public verifier rejects
field/SQLite instrumentation by design. Do not bypass it to claim public release.
Do not install implicitly; preserve user app data and capture history.

## Offline diagnostics

The field hooks run the navigation sender and a separate bounded SQLite recorder
in the map process. ADB/PC is not required by the writer. Only profiled structural
and bounded mapping outcomes are recorded: no raw route geometry, exact location,
destination, arbitrary object strings or exception messages. Actual on-device
recording for a newly built APK still requires #10/#11 evidence.

See `DIAGNOSTIC_CAPTURE.md` for extraction mechanics. Use a fresh private D-drive
output, never a repository or Actions artifact, and exactly one authorized device.
No GPS spoofing or simulated replay counts as real-drive acceptance.

## Validation

`.github/workflows/naver-apk-tools.yml` runs synthetic Python packaging/privacy
and diagnostic, field and production Java/DEX contracts on Ubuntu. Naver CI
separately checks common control, wire and UI behavior. A passing source build
is not physical braking, runtime hook ordering or device-compatibility proof.
