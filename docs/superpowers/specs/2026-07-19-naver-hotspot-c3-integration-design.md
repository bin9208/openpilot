# Naver Map Hotspot-to-C3 Integration Design

## Goal

Make the modified Naver Map application discover and reconnect to a C3 that is a client of the phone's Android hotspot, without a fixed IP address, a companion bridge application, or ADB forwarding. Deploy the pending Naver next-TBT and safety mappings to the C3 while preserving Tmap compatibility and fail-neutral control behavior.

The completed stationary validation must demonstrate this path:

`Naver Map hook -> canonical frame -> hotspot discovery -> TCP 7712 -> carrot_man -> carrotMan/navRoute -> terminal neutralization`

Physical acceleration, braking, steering, and automatic lane-change validation are deliberately deferred to a low-speed closed-course test after stationary validation succeeds.

## Current State

- C3 commit `b368801a` accepts typed Naver frames, arbitrates them with Tmap, publishes provider validity and control authority, and neutralizes stale or terminal navigation.
- C3 listens for typed discovery on UDP 7706, sends responses to UDP 7705, and accepts newline-delimited canonical frames on TCP 7712.
- The installed simulation APK uses a loopback-only transport and therefore required ADB reverse and an SSH tunnel during the previous test.
- The working tree contains uncommitted parser and mapping changes for next TBT, fixed cameras, mobile cameras, section cameras, and bumps.
- Production Naver hooks do not yet have evidence-backed accessors that turn the application's internal objects into canonical frames. Unknown internal objects are currently ignored.

## Considered Connection Approaches

### Selected: typed broadcast discovery plus last-success retry

The APK broadcasts a typed discovery request on the active hotspot/Wi-Fi IPv4 interfaces. It connects to the source address of a valid C3 response and remembers that endpoint for the lifetime of the app process. A reconnect first tries the last successful endpoint and falls back to discovery.

This handles changing DHCP addresses, avoids user configuration, and reuses the C3's existing discovery contract.

### Rejected: fixed or manually entered C3 address

A fixed address is unreliable across phone models, hotspot restarts, and DHCP leases. A manual setting also adds an unnecessary UI and creates a failure mode during driving.

### Rejected: subnet scanning

Scanning a hotspot subnet is slower, noisier, and less trustworthy than a typed response. It also behaves poorly on non-/24 networks and is unnecessary because the C3 already supports discovery.

## Architecture

### APK transport

`PersistentNavigationTransport` owns endpoint discovery, connection reuse, and reconnection:

1. If a previous endpoint succeeded in the current app process, attempt TCP 7712 there with a short timeout.
2. If that fails, enumerate active, non-loopback IPv4 interfaces and collect their directed broadcast addresses.
3. Send the schema-v1 `carrot.navigation.discover` request to every collected broadcast address and to `255.255.255.255`, deduplicating destinations.
4. Bind the discovery socket to UDP 7705 and accept only bounded responses that have the expected type, schema, response source port, and advertised TCP port 7712.
5. Use the UDP packet's actual source address as the C3 address. Do not trust an address embedded in the JSON response.
6. Connect TCP 7712, keep the socket alive, and send one newline-delimited canonical JSON frame at a time.
7. On a write or connect failure, close the socket, discard a failed cached endpoint, and rediscover with the existing 500 ms to 10 s jittered exponential backoff.

The selected endpoint is kept only in memory. An app restart performs fresh discovery, which is safer when the hotspot subnet changes.

### Simulation and production builds

The simulation build will generate deterministic synthetic navigation frames but use the same persistent discovery transport as production. This makes the stationary test representative of the in-car network path and removes the need for ADB reverse or a PC tunnel.

The production build uses the same transport without synthetic playback. `DirectNavigationTransport` remains available only to isolated tests and is not selected by distributable builds.

### Naver object adapter

A bounded adapter aggregates the six existing hook channels: status/goal, current TBT, next TBT, safety, route, and lane/fork/ramp. Accessors are added only after their exact classes and values are observed on the attached 6.8.0.5 application.

The adapter follows these rules:

- It is restricted to the profiled package version and main process.
- It uses a small allowlist of observed no-argument accessors and simple fields; it does not perform open-ended object traversal.
- Partial or unknown data never creates a control-authorized frame.
- A guiding frame is emitted only when the required status, current maneuver, distance, remaining route data, and position are valid.
- Route termination emits an `arrived` or `stopped` frame, clears the route, and lets the C3 neutralize immediately.
- Invalid values, unsupported enum values, and stale snapshots are dropped rather than guessed.

### Canonical Naver metadata

The Naver extension carries:

- next maneuver and distance;
- safety kind and distance;
- safety speed limit in km/h when Naver provides it;
- destination coordinate;
- route coordinates when changed.

Safety speed is not inferred from the road speed limit. If the application does not expose a trustworthy camera speed, the canonical value is zero and camera-speed control remains neutral.

### C3 mapping

The C3 parser validates the optional Naver extension only for `source=naver`. Accepted values map as follows:

| Naver value | Carrot control fields |
| --- | --- |
| fixed camera | `nSdiType=1`, distance and observed camera speed |
| mobile camera | `nSdiType=7`, distance and observed camera speed |
| section camera | `nSdiType=2`, `nSdiSection=1`, distance and observed section speed |
| bump | `nSdiPlusType=22`, bump distance, no invented speed |
| next maneuver | `nTBTTurnTypeNext`, `nTBTDistNext` |

Existing Tmap and legacy Tmap payloads retain their original parsing and arbitration. Unsupported or missing Naver metadata leaves all related control fields neutral.

## Data Flow and Lifecycle

1. The phone enables its hotspot and the C3 joins it as a client.
2. A Naver navigation hook initializes the payload runtime.
3. The simulation or evidence-backed production adapter offers a canonical snapshot.
4. The APK tries the cached C3 endpoint, then broadcasts discovery if necessary.
5. The C3 replies from UDP 7706; the APK connects to that packet's source address on TCP 7712.
6. The C3 validates the frame, arbitrates the provider lease, maps TBT and safety fields, and publishes `carrotMan` and route data.
7. Guiding frames are refreshed within the one-second TTL.
8. A terminal frame or loss of refresh clears provider authority, route data, TBT, safety, and desired control state.

## Failure Handling and Safety

- Discovery responses larger than the fixed receive buffer or with the wrong type, schema, ports, or address class are ignored.
- The APK never scans the subnet and never connects to an endpoint named only by untrusted response JSON.
- The C3 retains its frame size, route length, timestamp, sequence, TTL, enum, coordinate, and provider checks.
- A network change or socket failure cannot preserve control authority beyond the existing lease/TTL.
- Tmap/Naver provider switching remains serialized by `NavigationMux`.
- No signing keys, APKs, DEX files, raw diagnostic captures, coordinates, or route logs are committed.

## Testing

### Automated

- Python protocol tests for valid and invalid Naver metadata, including safety speed.
- C3 arbitration tests for next TBT, each safety type, missing metadata, terminal frames, stale frames, and unchanged Tmap behavior.
- Java transport tests for directed/global broadcast deduplication, packet-source endpoint selection, invalid response rejection, cached endpoint retry, rediscovery, reconnect backoff, and terminal delivery.
- Java adapter tests built from redacted fixtures captured from the exact application version.
- APK patch tests verifying all hook anchors occur exactly once and the selected payload mode uses persistent discovery.

### Stationary integration

1. Deploy the scoped C3 mapping commit and restart the C3 runtime while offroad.
2. Install the same-signer simulation APK.
3. On the current shared Wi-Fi, enter Naver Safe Driving and verify direct discovery with no ADB reverse, SSH forwarding, or PC listener.
4. Observe provider activation, current/next TBT, each safety mapping, route delivery, and terminal fail-neutral on the C3.
5. Install the production APK, begin a real Naver route while parked, and verify that evidence-backed frames arrive without synthetic playback.
6. Repeat the same checks with the phone as hotspot host and the C3 as hotspot client.

### Vehicle validation gate

Only after the stationary production test passes:

- replay or shadow-observe a real route without control engagement;
- test camera and bump deceleration at low speed in a controlled area;
- validate curve-speed behavior;
- validate lane-change requests with real lane, blind-spot, and driver-confirmation gates;
- proceed to public-road testing only after fail-neutral and abort behavior are confirmed.

## Deployment and Rollback

- Keep C3 changes in one scoped control-side commit and exclude `tools/naver_map_patch/**` and all generated APK artifacts from that commit.
- Push only the scoped control change to the configured C3 branch, then update the C3 with a fast-forward-only pull that preserves unrelated device files.
- Build and sign the APK with the preserved Naver-specific key, verify the signer and package splits, and install it with `install-multiple`.
- Preserve the previous signed APK set for rollback. A rollback reinstalls the previous APK set and reverts the scoped C3 commit; fail-neutral behavior remains the default throughout.

## Acceptance Criteria

- The C3 and phone connect with the phone as hotspot host and no fixed C3 IP.
- No ADB reverse, PC relay, companion bridge application, or manual IP entry is required.
- Reconnection succeeds after a C3 TCP restart and after a hotspot DHCP address change.
- Synthetic and real parked-route tests both activate `provider=naver` and then return to neutral on terminal or stale input.
- Current and next TBT, fixed/mobile/section cameras, and bumps match the canonical input.
- Missing or malformed metadata, lost connectivity, unsupported objects, and provider switching never leave residual navigation control authority.
