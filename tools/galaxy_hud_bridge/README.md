# Galaxy Tab HUD bridge

Streams the existing openpilot/carrot live runtime payload to a Galaxy Tab HUD app over a low-latency TCP JSON-lines link.

```bash
cd /data/openpilot
python -m tools.galaxy_hud_bridge.bridge --host 0.0.0.0 --port 28888 --fps 30
```

For load testing:

```bash
python -m tools.galaxy_hud_bridge.bridge --host 0.0.0.0 --port 28888 --fps 20 --profile-interval 2
```

Hyundai/Kia CAN-FD radar parsing is enabled by default and adds a compact
`services.canFdRadar` payload for the Android renderer. Use
`--no-can-fd-radar` to compare CPU load without raw CAN radar parsing.

The bridge also answers UDP discovery on port `28889`, so the Android app can find the openpilot device on USB tethering, hotspot, or `adb reverse`.

USB-C development fallback:

```bash
adb reverse tcp:28888 tcp:28888
```

Then leave the app on its default `127.0.0.1:28888` endpoint or long-press the HUD to set a host manually.
