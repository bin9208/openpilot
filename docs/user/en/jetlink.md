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

The C3X needs a branch containing the Jetlink integration (#47); the app alone is insufficient. While offroad, set Carrot Web `System → Jetlink External Model → Jetlink mode` to 1 (Shadow). The default is 0 and manager restart/reboot resets it to 0. Once that integration prepares the Jetlink vendor gadget (1209:0001), use a USB 3 data cable, choose **Status → Connect comma USB**, and grant USB permission. A foreground service shows an ongoing notification.

After denied permission or cable removal, reconnect manually. Use **Stop** in the app or notification to stop the service. It does not automatically reconnect after a reboot/process death. Thermal status SEVERE or above stops work; cool the device before retrying. Memory/model errors appear on the status page. An engine reporting `ready` does not authorize driving.

Pending device checks: install/rotation, import, CPU and NNAPI output comparison, USB denial/detach/reconnect, 30-minute Shadow/thermal testing, full C3X 50ms latency and control disengagement on loss. Field criteria in #44/#45/#46/#47 remain open until evidence is available.

## C3X selection and validation boundary

Shadow keeps native control and never waits for external results before publishing native output. Native inference continues every frame during external activation to preserve fallback history. A separate 50ms local communication deadline isolates a stalled USB-owner process. The full budget runs from C3X warp through external response/parsing to immediately before publication, unlike the standalone app benchmark.

Mode 2 requests activation. No passed device-validation profile is supplied, so it retains the native model. Profiles bind source and executed artifact SHA-256, tablet model, Android API, requested backend, app/ORT version, warp contract, numerical comparison and at least 30 minutes of full latency/loss evidence. App 0.1.1 transmits the required identity in its handshake; changing version/backend invalidates an older match. Do not turn benchmark success into an activation flag.

The first active timeout/detach/wrong-frame/NaN discards external output and latches loss. It enters the external-model-loss no-entry/soft-disable path; AlwaysLateral-only steering cannot bypass loss. Native success/reconnection does not clear it. Stop, disengage cruise and lateral control, acknowledge through 0 or a ready 1, then make a new request through 2. Old frames are never relabelled fresh.

Gadget recovery is deferred offroad. Toggle 0 → 1 and reconnect the app while parked; stuck kernel I/O may require a parked device restart. `JetlinkStatus` describes preparation/errors; `modelV2.jetlink` and `drivingModelData.jetlink` logs carry source/loss/session/timing. C3X QCOM device builds, physical USB speed, camera pixel comparison and drive acceptance remain separate validation work.

Initial C3X support requires CTV3 with its original bytes preserved. Preparation rejects an executed artifact with a different hash or a missing handshake hash. An acceptance record cannot be reused for a differently converted file with the same source name/shapes.
