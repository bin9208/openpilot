# Galaxy Tab HUD

Native Android HUD for Galaxy Tab S9 Ultra-style wide landscape driving assistance display.

## Build

```powershell
$env:JAVA_HOME='C:\Program Files\Android\Android Studio\jbr'
.\gradlew.bat :app:assembleDebug --no-daemon
```

Debug APK:

```text
apps/galaxy_tab_hud/app/build/outputs/apk/debug/app-debug.apk
```

## Link

Run the bridge on the openpilot device:

```bash
python -m tools.galaxy_hud_bridge.bridge --host 0.0.0.0 --port 28888 --fps 30
```

During CPU/load checks, run:

```bash
python -m tools.galaxy_hud_bridge.bridge --host 0.0.0.0 --port 28888 --fps 20 --profile-interval 2
```

Hyundai/Kia CAN-FD radar parsing is enabled by default. Use
`--no-can-fd-radar` when comparing bridge CPU load without raw radar parsing.

The app uses UDP discovery first, then tries common USB tethering, hotspot, emulator, and `adb reverse` endpoints. Long-press the HUD to enter a manual `host:port`.
