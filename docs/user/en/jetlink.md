# Experimental Android Jetlink app

The target is a Galaxy Tab S9 Ultra with 12GB RAM (Android 15 or later) and comma 3X. The APK targets arm64 Android 9+, but compatibility and measurements on the target device require separate testing. This is a model/link validation stage, not approval for vehicle use.

## Install and import

1. Check the supplied APK SHA-256, then open it on the tablet. Your file app may need permission to install apps.
2. Open **Carrot Jetlink → Model → Import model package ZIP**. Weights are not bundled in the APK. The app verifies the archive before storing it privately; import needs temporary storage as well as model space.
3. The ZIP must contain `manifest.json`, `model.onnx`, then optional `benchmark.frames.bin`, in that order. Paths, sizes, SHA-256, tensor shapes and state mappings are checked. Use the stateful CTV3 package preserving the original model bytes. Queued models are refused while numerical conformance is unverified.
4. CPU is the initial baseline. NNAPI requests an execution provider; it does not prove full NPU execution. Preparation may fail or some operators may run on CPU.

Do not uninstall or clear app data during updates. Install an APK with the same signing key over the existing app to preserve models. CI debug APKs may have a different temporary signature each run; do not mix them with the locally distributed APK for updates.

## Parked benchmark

Choose **Benchmark → Run 100-frame benchmark** and keep the screen open. After preparation and warmup, the app measures actual ONNX Runtime inference using repeated synthetic package inputs or black images with fixed context. It shows mean/p50/p95/p99/maximum and the number of frames over 50ms. Percentiles cover the last 2,000 samples; mean, maximum and misses cover the full run.

**Save numerical comparison ZIP (after benchmark)** exports the actual outputs for the first input sequence. Package inputs are limited to 100 frames; longer sequences are rejected at import. Extract it on a PC and use `tools.jetlink_model.compare` against a PC recording of identical inputs. **Diagnostics → Save diagnostic report** exports device/model/requested provider/performance/thermal information. Measurements retain the model/provider selected when the run began, even if later selections change. Health readings are sampled at export. Diagnostics contain no camera images or location.

These timings exclude C3X camera processing and the complete USB round trip. An APK build or synthetic-input result does not establish tablet performance, 30-minute thermal stability or vehicle acceptance.

## USB and stopping

C3X integration is separate work (#47). Installing this app alone cannot connect an unchanged carrotpilot installation. Once that integration prepares the Jetlink vendor gadget (1209:0001), use a USB 3 data cable, choose **Status → Connect comma USB**, and grant USB permission. A foreground service shows an ongoing notification.

After denied permission or cable removal, reconnect manually. Use **Stop** in the app or notification to stop the service. It does not automatically reconnect after a reboot/process death. Thermal status SEVERE or above stops work; cool the device before retrying. Memory/model errors appear on the status page. An engine reporting `ready` does not authorize driving.

Pending device checks: install/rotation, import, CPU and NNAPI output comparison, USB denial/detach/reconnect, 30-minute Shadow/thermal testing, full C3X 50ms latency and control disengagement on loss. Field criteria in #44/#45/#46/#47 remain open until evidence is available.
