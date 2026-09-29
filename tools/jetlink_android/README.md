# Carrot Jetlink Android

Experimental arm64 Android USB-host inference server for Jetlink wire v2. Upstream compatibility is pinned to `f10f4705243812518e6441dfb06bf2178c408310`; licensed protocol fixtures live in `../jetlink_model/fixtures` and `reference`.

Requirements: JDK17, Android platform35/build-tools35.0.0. Set `ANDROID_HOME` or ignored `local.properties` (`sdk.dir=...`). Gradle8.11.1, Kotlin2.1.20, AGP8.9.2 and ONNX Runtime1.22.0 are pinned with dependency checksums.

```sh
bash tools/jetlink_android/gradlew -p tools/jetlink_android :core:test :runtime:test :app:lintDebug :app:assembleDebug
python tools/jetlink_android/check_apk.py tools/jetlink_android/app/build/outputs/apk/debug/app-debug.apk
```

The JVM runtime tests execute a real tiny stateful ONNX model against eight upstream golden frames and reset. They do not measure Android acceleration or device performance. Queued inference is intentionally refused pending numeric conformance (#46). CPU and NNAPI are selectable; NNAPI partitioning and sustained speed remain unverified. C3X integration is tracked in #47.

The first version requires manual import of a verified manifest/model ZIP. USB raw upload is accepted only for the selected manifest and unchanged source SHA; arbitrary model metadata is never unpickled by the app. No Internet permission, automatic USB reconnect, remote power-off or vehicle acceptance flag is provided.

See [Korean guide](../../docs/user/ko/jetlink.md) and [English guide](../../docs/user/en/jetlink.md). The optional synthetic input sequence is little-endian warped uint8 followed by packed float32, as in `tools.jetlink_model.runner`. Exported benchmark output ZIPs contain `run.json` and `outputs.bin` accepted by `tools.jetlink_model.compare`.

Debug APKs built on CI are validation artifacts. Keep a stable private signing key for field updates; never check a keystore into Git or uninstall the app to work around a signature mismatch.
