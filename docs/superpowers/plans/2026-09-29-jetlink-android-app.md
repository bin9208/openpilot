# Jetlink Android App Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Tab S9 Ultra에 설치하여 실제 ONNX를 실행하고 C3X와 통신하는 APK를 만든다 (#45).
**Architecture:** Kotlin portable core에서 wire/session/model state를 검증한다. Android 모듈은 USB/ORT/foreground service와 사용자 화면을 연결한다.
**Tech Stack:** Kotlin2.1.20, AGP8.9.2, Gradle8.11.1, JDK17, ONNX Runtime Android1.22.0.
**Spec:** [승인 설계](../specs/2026-09-29-jetlink-android-design.md).

## Global Constraints

[전체 Global Constraints](2026-09-29-jetlink-android.md#global-constraints)를 적용한다.
minSdk28/compileSdk35/targetSdk35/build-tools35.0.0, arm64-v8a 실기, package `ai.carrot.jetlink`.
Gradle wrapper와 distribution SHA-256, dependency verification을 커밋한다. APK 키는 Git 제외.
앱/코어 경로의 기준은 `tools/jetlink_android/`, Kotlin package는 `ai.carrot.jetlink`다.
M1/M2가 제공한 schema/fixture를 사용하고 별도 비호환 계약을 만들지 않는다.

## Review Focus

- A1: fragmented USB/header, 1024배수 padding, 16KiB burst 경계.
- A2: 중복 upload chunk, 저장공간 부족, 취소 후 불완전 파일 재사용.
- A3: reset/model change/reconnect 이후 이전 recurrent state가 섞이는 경우.
- A4: Activity 회전 및 USB 권한 거부/분리 중 pending inference.
- A4: thermal 상태 미지원, 알림 거부, background service 시작 제한을 실제 상태로 표시.

## File Structure

- root `settings.gradle.kts`, `build.gradle.kts`, `gradle.properties`, `gradlew`, `gradlew.bat`, `gradle/wrapper/`: 고정 빌드.
- `core/src/main/kotlin/ai/carrot/jetlink/`: Wire.kt, UsbFraming.kt, ModelPackage.kt, ModelStore.kt, ModelState.kt, JetlinkSession.kt, Stats.kt.
- `core/src/test/kotlin/ai/carrot/jetlink/`: 각 핵심 클래스의 portable JUnit tests.
- `app/src/main/java/ai/carrot/jetlink/`: UsbLink.kt, OrtEngine.kt, LinkService.kt, MainActivity.kt, DeviceHealth.kt, ReportExporter.kt.
- `app/src/main/AndroidManifest.xml`, `res/xml/device_filter.xml`, `res/values/strings.xml`, `res/values-ko/strings.xml`.
- `app/src/androidTest/java/ai/carrot/jetlink/`: OrtEngineTest.kt, LinkLifecycleTest.kt.
- `.github/workflows/jetlink.yml`, `.github/workflows/fast-checks.yml`, `.github/workflows/integration.yml`, `tools/ci/tests/test_workflow_policy.py`.

## Task A1: 빌드 가능한 wire/USB core

**Interfaces:** `Header(type:Int, sequence:Long, flags:Int, length:Long)`;
`Wire.encode(header:Header):ByteArray`, `Wire.decode(bytes:ByteArray):Header`;
`UsbFraming.feed(bytes:ByteArray):List<ByteArray>`, `UsbFraming.encodeHost(message:ByteArray):ByteArray`.
32-byte header, MAGIC=0x4B4E4C4A, VERSION=2. sequence는 unsigned32 범위를 Long으로 검사.

- [ ] WireTest/UsbFramingTest 작성: `goldenHeaderMatches`, `splitEveryByte`, `rejectVersionAndOversize`, `padded1024`, `gadget16384`. Assertions: M2 golden과 bytes 동일, 모든 분할 위치에서 동일한 메시지 하나 복원, 잘못된 version/length는 할당 전 실패.
- [ ] 최소 Gradle core 설정 후 `./gradlew -p tools/jetlink_android :core:test` 실행, 미구현 wire 테스트 실패 확인.
- [ ] raw wire/body/USB burst를 분리 구현. payload cap은 upload chunk4MiB+8 및 승인 ModelSpec의 계산 크기 중 필요한 범위로 제한하고 overflow 검사. inference/JSON에는 각 더 작은 계약 크기 적용.
- [ ] 위 명령 PASS. fast workflow에 core tests 추가, `python -m unittest discover -s tools/ci/tests -v` PASS.
- [ ] 관련 소스/빌드/fixture 참조만 commit: `feat: implement Android Jetlink wire and USB framing`.

## Task A2: 모델 보관과 서버 세션

**Interfaces:** `ModelStore.begin(sha256:String, size:Long)`, `append(offset:Long, chunk:ByteArray)`, `finish():ModelPackage`, `cancel()`;
`JetlinkSession.handle(message:ByteArray):List<ByteArray>`, `JetlinkSession.close()`.
ModelPackage는 M1 schema v1을 parse하며 graph의 runtime I/O 검사 결과까지 준비 상태에 반영한다.

- [ ] ModelStoreTest/JetlinkSessionTest 작성: `wrongHashNeverReady`, `gapAndDuplicateOffsetsRejected`, `cancelKeepsPrevious`, `pingDuringPrepare`, `shutdownRejected`. Assertions: 실패 파일은 inventory/READY 없음, 기존 모델 보존, PONG 응답, SHUTDOWN_RESP.ok=false.
- [ ] core test 명령으로 미구현 실패 확인.
- [ ] HELLO/ENGINE/UPLOAD/PROGRESS/INFER/STATE/PING/ERROR와 명시적 unsupported shutdown 구현. partial 파일은 내부 저장소에 한정하고 size/hash 검증 후 원자적으로 확정. 준비 중 진행상태/핑 처리는 계속하며 임의 코드/PKL 로드는 금지.
- [ ] core tests PASS. 최소 Android shell과 storage bridge를 연결하고 `./gradlew -p tools/jetlink_android :app:assembleDebug` 성공/APK 존재 확인.
- [ ] commit: `feat: add verified model store and Jetlink server session`.

## Task A3: 실제 ORT 및 recurrent state

**Interfaces:** `InferenceEngine.prepare(model:ModelPackage):Unit`, `run(warped:ByteArray, packed:FloatArray, reset:Boolean):FloatArray`, `close()`.
`OrtEngine`이 이를 구현한다. `ModelState.stage(...)`는 M2와 일치하는 named tensor를 만들고
`ModelState.accept(outputs:Map<String,TensorData>)`는 검증 후에만 state를 교체한다.
`TensorData(dtype:String, shape:LongArray, data:ByteBuffer)`는 연속 데이터만 보유한다. ORT wrapper의 session/tensor/result 수명은 OrtEngine이 소유하고 닫는다.

- [ ] ModelStateTest/OrtEngineTest 작성: `queuedSkip124`, `statefulReset`, `lateOldGenerationDiscarded`, `nonfiniteDoesNotAdvance`, `runtimeIoMismatch`. Assertions: M2 staging/golden outputs와 일치, reset 이후 영 상태, 이전 generation 결과 미반영, NaN/shape mismatch는 ERROR.
- [ ] core tests 및 Android `:app:connectedDebugAndroidTest` 실행. 준비된 emulator/device가 없으면 실제 런타임 검사를 실행하지 않았다고 기록하며 CI Android runner에서 수행한다.
- [ ] ORT CPU를 실제 session으로 연결하고 native/tensor/result 자원을 닫는다. 원본 metadata의 scalar/queued/stateful 계약을 지킨다. CPU/NNAPI provider를 UI/보고서에 명시하고 요청 provider 실패 시 조용히 가속 성공으로 표시하지 않는다.
- [ ] tiny ONNX 실제 실행 PASS 후 M3 실모델 reference와 Android 다중 프레임 결과 비교. JUnit 성공만으로 ORT 추론 성공을 주장하지 않는다.
- [ ] commit: `feat: run Jetlink ONNX models with validated recurrent state`.

## Task A4: USB 서비스와 사용할 수 있는 앱

**Interfaces:** `UsbLink.open(device:UsbDevice):Unit`, `read(deadlineNs:Long):ByteArray`, `write(message:ByteArray, deadlineNs:Long)`, `close()`;
`LinkService.startLink(deviceId:Int)`, `stopLink()`, immutable `UiState` 관찰.
UiState: 연결/모델/backend, OS/API/메모리/thermal, generation, 지연분포, 50ms초과 수, 최근 오류.

- [ ] LinkLifecycleTest/StatsTest 작성: `rotationKeepsOneSession`, `permissionDeniedNeverOpens`, `detachClosesPendingIo`, `oldGrantAfterDetachIgnored`, `percentilesAndDeadlineCount`. Assertions: session 1개, 권한 거부 시 open0회, detach 이후 출력0개, late grant 무시, 50ms 초과 전부 집계.
- [ ] core tests/connectedDebugAndroidTest로 실패 확인.
- [ ] Android USB host API로 pinned vendor/interface/endpoint를 탐색하고 권한 확인 뒤 open한다. connected-device foreground service, 알림·중지 동작, session generation, 제한된 read/write deadline을 연결한다. UI 스레드에서 I/O/모델 준비 금지.
- [ ] 상태/모델/벤치마크/로그 화면을 한국어 기본 기기 언어 및 영어로 구현한다. start/stop, 문서화된 HTTPS 모델 manifest/SAF 가져오기, 준비 진행률, 실제 benchmark, SAF 보고서 내보내기를 제공한다. 로그는 이미지/경로/식별자 기본 제외.
- [ ] `./gradlew -p tools/jetlink_android :core:test :app:testDebugUnitTest :app:lintDebug :app:assembleDebug` PASS. APK package/minSDK/arm64 ORT library와 SHA-256을 확인한다. API35 emulator 결과와 Tab S9 Ultra 실제 결과를 구분한다.
- [ ] reusable jetlink workflow의 app job에 unit/lint/assemble/artifact를 연결하고 integration gate needs에 jetlink 성공을 요구한다. 실패/누락/cancelled를 통과시키지 않는 workflow test PASS.
- [ ] docs/user/{ko,en}/jetlink.md, settings.md, docs_map.json에 설치·권한·연결·APK 업데이트·제한을 반영한다. mapped-doc check PASS 후 commit: `feat: ship Android Jetlink USB app and build gate`.

## 실기 완료 조건

- [ ] Tab S9 Ultra 실제 OS/USB 권한/연결, 모델 prepare, inference, 회전/서비스 중지/재연결을 기록한다.
- [ ] APK 서명/버전/hash, Actions 링크를 제공하고 업데이트 시 기존 데이터를 보존한다.
- [ ] M3 parity와 C3X end-to-end benchmark가 남으면 #45를 field pending으로 유지한다.
