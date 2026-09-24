# Naver diagnostic capture

This package builds a diagnostic-only payload for the reviewed Naver Map
artifact. It does not map navigation observations into an openpilot control
message, discover a remote receiver, replay traffic, or deploy an APK.

## Fixed diagnostic contract

The payload observes exactly six channels:

- `status`: the first explicit `Status` parameter at
  `NaviStatusBroadcaster.g(Status)` method entry
- `tbt_current`: explicit parameter 0 of the phone/core
  `TbtData(TbtItem,TbtItem)` constructor, after `Object.<init>`
- `tbt_next`: explicit parameter 1 of the same constructor, before the
  nullable second item is stored
- `safety`: the `SafeControlItem` return register
- `route`: the first explicit `CurrentRoute` parameter at method entry
- `lane`: the initialized `NaviLaneItem` register in
  `NaviStore.M0(GuidanceSession)`, immediately after its constructor

All anchors are exact-one patches. A missing or duplicate anchor fails the
patch without writing a partial artifact. The payload DEX entry is fixed to
`classes43.dex`.

For the current field-acceptance v4 build, `route` and `safety` do not enqueue
the raw Naver objects. They first pass through the same production mapper used
by the public candidate and persist only an exact bounded mapping outcome:
channel, result, root descriptor, input/output counts, revision, item presence,
distance validity, and frame eligibility. Arbitrary fields, coordinates,
route geometry, object strings, and exception text are rejected by the host
decoder. The other channels retain their bounded structural diagnostics.

The hook caller only enqueues into a capacity-16 queue with `offer`; it never
blocks the navigation thread. A full queue increments a drop counter. A
background thread may connect only to `127.0.0.1:7712`, with a 100 ms socket
timeout, and only in process `com.nhn.android.nmap`.

## Privacy boundary

The sampler invokes only zero-argument accessors in the profile-owned,
per-channel allowlists. It reports class/method descriptors, accessor names,
enum names, collection element descriptors, string lengths, and numeric
buckets. It does not call arbitrary
`toString()` methods or export exact coordinates, place identifiers, full road
names, route geometry, identity fields, or exception messages.

Bounds are fixed at depth 4, 16 accessors, 4 collection elements, 160 sampled
string characters, and a 4096-character Java-side record cap.

## Offline capture

The primary diagnostic record is a bounded SQLite database in Naver Map's
private app-internal files directory. It continues recording without an ADB
connection, a PC connection, or `adb reverse`. The app-specific external files
directory contains only a frozen extraction snapshot, never the live SQLite
writer. Disconnect ADB before driving; do not manipulate GPS. GPS spoofing and
simulation are unsupported.

After the drive, reconnect exactly one authorized device and run:

```powershell
python tools/naver_map_patch/naver_patch.py extract-capture `
  --profile 6.8.0.5 `
  --output-root C:\tmp\naver-map-capture-6805-v3-drive `
  --json
```

The extractor accepts no serial, package, version, device-path, or overwrite
option. It gates on exactly one ADB transport row in state `device`, verifies
package `com.nhn.android.nmap`, version `6.8.0.5`, and
`versionCode=60800007`, then uses only this fixed sequence:

```text
adb devices -l
adb -s <gated-device> shell dumpsys package com.nhn.android.nmap
adb -s <gated-device> exec-out content read --uri \
  content://com.nhn.android.nmap.fileprovider/navi_trace/naver-diagnostic/capture-v3-export.sqlite3
```

The diagnostic payload grants `com.android.shell` read access only to that one
v3 export URI. The first approved FileProvider read synchronously freezes new
capture acceptance, drains the mapper and local sink, waits for any in-flight
SQLite batch, marks the run clean, checkpoints WAL, closes the private
canonical database, copies the active primary or recovery database to a
non-granted temporary external file, fsyncs it, and atomically replaces the
single export file before the provider returns its descriptor. General app
shutdown never publishes a snapshot. Legacy v1 exports are neither modified
nor granted. The code never walks the app directory and never grants write,
prefix, persistable, or execute access.

Extraction does not use force-stop, `adb pull`, or chmod, and never clears,
uninstalls, or deletes the canonical device database. Each invocation creates a
new `capture-<UUID>` directory and never overwrites or reuses host evidence.
The host stores the downloaded export as `capture-v3.sqlite3`. Keep raw SQLite files below
`C:\tmp`; the shareable bundle is atomically published only after copied-file
hashing, SQLite integrity, exact schema and quota checks, frame validation,
and privacy scanning pass. Device-side database deletion is a separate future
explicit action, and only after validation.

Extraction intentionally leaves the diagnostic runtime quiesced for the
current app process. Before beginning a later capture, fully terminate Naver
Map and launch it again, then verify that its process ID changed. Merely
foregrounding the same process does not restart capture. The fresh process must
observe at least one navigation hook before extraction so runtime
initialization can issue the exact FileProvider URI grant. An update-install,
data clear, uninstall, or ADB reverse is neither needed nor permitted.

The bundle contains only:

- `hashes.json`
- `summary.json`
- `manifest.json`
- `accessor_shapes.json`

Historical discovery evidence covers all six real channels. Production route
and safety acceptance nevertheless remains pending until real field v4
mapping outcomes from Naver Map 6.8.0.5 are captured and reviewed. A successful
stationary hook or static DEX chain does not prove a real route, camera, or bump
while driving.

The prior v2 database and export are retained as immutable discovery evidence.
The v3 names deliberately prevent a hook/allowlist update from appending frames
to a database carrying the older payload build ID.

## Optional live TCP mirror

`capture_cli.py` listens only on loopback TCP port 7712. The raw capture and
sanitized bundle must be different paths and both must be outside the
repository:

```text
python tools/naver_map_patch/capture_cli.py \
  --raw D:/private/naver/raw.jsonl \
  --bundle D:/private/naver/shareable
```

The receiver and its UDP discovery service both bind to `127.0.0.1`: TCP
7712 and UDP 7706 respectively. Discovery replies always advertise
`server=127.0.0.1`, so an ADB-connected device must explicitly forward the
diagnostic TCP connection, for example:

```text
adb reverse tcp:7712 tcp:7712
```

No external receiver address is accepted or advertised.

The raw JSONL is local evidence and must never be copied into Git. The
shareable directory contains only:

- `capture.sanitized.jsonl`
- `summary.json`
- `manifest.json`

The receiver serves only the exact typed Naver discovery request on loopback
UDP 7706, replying to requester port 7705 with `127.0.0.1` and TCP 7712. It
rejects duplicate JSON keys and validates the complete diagnostic schema and
privacy-scans the sanitized record before any raw JSONL write. The summary
reports per-channel counts, six-hook completeness, parser rejections, source
drop count, and global sequence/monotonic ordering.

This document does not claim complete six-channel real-drive evidence or
real-drive route/safety acceptance. Control integration and public packaging
are separate gates from diagnostic capture.
