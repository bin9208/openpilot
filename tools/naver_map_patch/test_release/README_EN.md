# Naver Map 6.8.0.5 single TEST APK

Not a public production release. Target: Android 11+ arm64; compatibility with
every device is not guaranteed. Use c3 branch `codex/naver-support-20260924`.
This artifact has not been installed or road-tested. Stay ready to control the
vehicle; never approach a camera or bump assuming automatic braking will work.

## Scope

- Start guidance in either Tmap or Naver; both feed the shared controller.
- Test code supports lifecycle, basic TBT, current camera/bump and route points.
- Camera release, Naver bump/curve braking and accelerator speed override need
  fresh real-drive validation. HDA fallback cannot supply nonexistent bump data.
- Full Tmap-level lane, destination/ETA, maneuver and traffic-light mapping is incomplete.
- Existing status shows NAVER/TMAP owner separately from N/T/HDA reduction provider.
- If no new route arrives after a stopped session, restart the app and guidance.

## Before installation

A different signing key prevents an in-place update. No phone installation or
app/data deletion was performed by this build. If update is rejected, preserve
app data and the diagnostic DB before deciding what to do; do not blindly
uninstall. provenance.json records the signing fingerprint and source commit;
SHA256SUMS.txt records the APK digest.

## Offline capture and field check

The field writer uses app-internal SQLite independently of PC/ADB. Storage is
bounded to 64 MiB with per-channel quotas; frames can be dropped. Offline writing
on a device with this new APK still needs verification. Records contain bounded
status/types/counts/mapping outcomes, not raw route coordinates or destination.
Do not publish the DB or private logs to GitHub.

Start guidance and check connection while parked, then drive without PC/ADB.
Have a passenger observe or check only after parking. Validate camera/bump
approach and release, curve speed and accelerator override, Naver stop to HDA,
and switching to Tmap. Take over safely if behavior is wrong and note time/display.
Later connect exactly one device and run `naver_patch.py extract-capture --profile
6.8.0.5 --output-root <fresh-private-D-folder> --json`. Preserve capture before
uninstalling or clearing data. This guide stays outside the APK.
