# Jetlink Android와 carrotpilot 연동 설계

상태: 사용자 구조 진행 승인 후 작성한 설계 검토본. 구현/성능/실차 승인 문서가 아니다.
상위 이슈: https://github.com/bin9208/openpilot/issues/44
작성일: 2026-09-29

## 1. 목표와 대상

갤럭시 탭 S9 울트라 12GB에서 실제 큰 주행 모델을 실행하고 USB로 comma 3X의
carrotpilot에 추론 결과를 제공한다. Android는 사용자가 15 이상으로 추정했으며,
앱이 실제 API level, 기기/SoC, 메모리와 USB 연결 상태를 표시한다.

최종 결과물은 설치 가능한 APK, 재현 가능한 앱 소스/빌드, carrotpilot 연동 PR,
검증 기록과 한/영 사용 설명서다. APK 빌드와 실시간 주행 적합성을 구분한다.
사용자의 기존 미커밋 작업과 네이버 연동을 보존한다.

확인한 기준:

- carrotpilot dev: `354ee8ac71684bb56eea91159d696779d8d4ea90`.
- Jetlink: `f10f4705243812518e6441dfb06bf2178c408310`, MIT.
- zoompilot jetson-trt 조사 기준: `e5b2f9e8d0bf92c3e68b34fd4083c0624febc7e1`.
- Samsung 공식 사양: Snapdragon 8 Gen 2 for Galaxy, USB 3.2 Gen 1.
- 공식 Jetlink Android 구현은 확인되지 않았다. 공식 iOS 성능을 Android 증거로 쓰지 않는다.
- 현재 carrotpilot 기본 big-model manifest는 tinygrad PKL을 가리킨다.
  이 파일을 Android ONNX Runtime에 그대로 넣을 수 없다.

## 2. 선택한 접근과 대안

선택: Android 네이티브 앱과 Jetlink v2 USB bulk 프로토콜을 결합하고,
ONNX Runtime을 통해 실제 ONNX를 실행한다. 카메라 보정/warp, 결과 해석,
운전자 감시, planner, 제어와 CAN은 comma에 둔다. 태블릿은 추론 서버다.

대안 A: 처음부터 Qualcomm QNN HTP 전용 구현. 성능 잠재력은 있지만 확인한
ONNX Runtime QNN 문서는 HTP용 정수 양자화를 요구한다. 모델 지원 연산자,
상태 텐서, 양자화 오차와 배포 라이브러리 검증 전에는 기본값으로 삼지 않는다.

대안 B: TCP/Wi-Fi 또는 외부 Linux 서버를 Android에서 조작하는 앱.
후자는 태블릿 자체 추론이라는 목적을 충족하지 않는다. TCP는 데스크톱 테스트
전용으로 분리하고 실제 차량 연결 경로는 USB를 사용한다.

원본 출력 동등성과 프로토콜을 먼저 확립하고, NNAPI/QNN 등의 가속은
동일 모델/기기에서 실행한 결과로 승격한다. CPU 추론이 20Hz를 만족한다고
가정하지 않는다. 가속 미달이면 검증 미완료 상태로 남기고 더 느린 경로를
주행 가능으로 표시하지 않는다.

## 3. Android 앱

제안 위치: `tools/jetlink_android/`. Kotlin 앱, arm64-v8a 실기 APK,
최소 Android 9(API 28), 빌드 SDK/의존성 버전은 구현 계획에서 고정한다.
API 28은 앱 설치 범위이며 차량 사용 지원 목록이 아니다.

화면:

- 상태: 연결된 comma, 실제 기기/OS, USB 매체, 현재 모델/추론 backend,
  준비/검증/실행 상태, 끊김 원인.
- 모델: 검증된 ONNX 패키지 선택/가져오기/준비, 진행률, 용량과 SHA-256.
- 벤치마크: 실제 모델/입력으로 처리시간 p50/p95/p99/max, 50ms 초과 수,
  메모리와 thermal status. 모의 fixture 실행은 별도 표기한다.
- 로그: 사용자가 선택한 진단 내보내기. 이미지/경로/기기 식별자를 기본 수집하지 않는다.

연결 중 foreground connected-device service가 USB와 추론 수명주기를 소유한다.
회전/화면 재생성은 세션을 재시작하지 않는다. 사용자가 명시적으로 시작/중지하며,
권한 거부, USB 분리, 프로세스 종료, 과열, 메모리 부족을 오류 상태로 노출한다.
필요한 Android USB 권한/알림/foreground service 규칙을 해당 target SDK로 검증한다.
태블릿 루팅이나 시스템 USB 네트워크 설정을 전제로 하지 않는다.

추론 세션과 모델 준비는 UI 스레드 밖에서 수행한다. 세션당 추론은 직렬화하고
무제한 대기열을 만들지 않는다. 중단 요청 시 늦게 완료된 결과를 새 세션에
반환하지 않는다. 리소스는 세션 종료 시 명시적으로 닫는다.

## 4. 모델 아티팩트와 추론 계약

PKL을 확장자만 바꿔 사용하거나, shape만 맞는 더미 모델로 주행 출력을 대체하지 않는다.
기존 big-model PKL 선택과 저장소는 그대로 두고 Android용 ONNX 준비 도구와
별도 패키지 manifest를 만든다. 모델 배포는 프로젝트 정책의 NAS 모델 경로를
사용하며, 준비된 파일의 해시 확인 전에는 새 NAS 파일을 게시하지 않는다.
사용자 모델 가져오기 역시 동일한 검증 과정을 거친다.

패키지는 source checkpoint, 원본 SHA-256, 변환 ONNX SHA-256/크기,
준비 도구/런타임 버전, 입력·출력 이름/형식/shape, output_slices,
frame_skip, 상태 초기화 규칙을 포함한다. 모델 경로 traversal, 외부 데이터의
허용 디렉터리 이탈, 과대 크기/정수 overflow, 불완전 다운로드를 거부한다.
재사용 캐시 키에는 모델 해시, backend, runtime, 준비 버전, 기기 정보를 포함한다.

첫 실모델 목표는 현재 큰 모델 계열인 Cinque Terre V3의 출처가 확인된 ONNX다.
원본 export를 확보하고 메타데이터/해시를 기록하기 전에는 해당 모델 지원을
표시하지 않는다. 준비된 metadata와 실제 ONNX session I/O를 대조한다.

Jetlink의 queued graph와 stateful graph를 별개로 처리한다:

- queued: 이미지/feature/desire 이력과 frame_skip 1/2/4 샘플링을 upstream과 대조.
- stateful: `state_*`와 `next_state_*`의 대응, dtype/shape, reset을 검증하고
  모델의 recurrent state를 태블릿에 유지한다.
- 재접속, 모델 변경, 명시적 RESET_QUEUES에서 전체 상태 초기화.
- 결과 길이를 18452로 무조건 고정하지 않고 승인된 모델 metadata의 output_slices와
  comma parser가 요구하는 의미/크기를 동시에 검증한다.
- 원본 fp32 기준과 다중 프레임 비교 후 가속 후보를 평가한다. 양자화는 별도 모델
  해시/검증 결과로 다루며, 허용 오차를 결과에 맞춰 완화하지 않는다.

## 5. USB와 프로토콜

Android는 USB host, comma는 Jetlink vendor gadget이다. 대상 interface와
bulk IN/OUT endpoint를 확인한 뒤 Android UsbManager 권한을 받아 연다.
USB 3 케이블과 각 장치의 전원 확보가 필요하며 실제 협상 속도를 측정한다.
같은 comma USB-C 포트의 Chestnut/eGPU와 동시 사용하지 않는다.

Jetlink v2의 32-byte little-endian header, message/flag/status 번호와 payload를
그대로 따른다. Android 변형 프로토콜을 새로 만들어 upstream 호환을 가장하지 않는다.
HELLO, ENGINE 준비/업로드, PROGRESS, INFER, STATE, PING/PONG, ERROR를 구현하고,
지원하지 않는 SHUTDOWN에는 명시적으로 실패 응답한다. 태블릿 전원을 끄지 않는다.

USB는 메시지 단위 read가 보장되지 않는다. partial read/write, host PADDED byte,
comma 16KiB burst 정렬, 여러 메시지 경계와 연결 중단을 upstream wire fixture로 검증한다.
모델 업로드 offset, 총 크기와 SHA-256을 검증한 뒤 원자적으로 확정한다.
magic/version/seq/frame_id/length/shape/finite output 검증 실패는 세션을 실패 처리한다.

## 6. carrotpilot 경계와 전환

`openpilot/selfdrive/modeld/jetlink/`에 transport/client, 모델 어댑터,
상태 기계와 검증을 분리한다. 재사용한 MIT 소스의 저작권/라이선스를 보존한다.
zoompilot 전체 포크나 sunnypilot modeld_v2를 무분별하게 가져오지 않는다.

C3X GPU에서 실제 카메라 크기와 calibration을 사용해 warp한다. 기존
`compile_modeld.py`와 `local_gpu_warp.py`의 입출력 계약을 대조하고, 별도 warp
아티팩트가 필요하면 빌드·원본 비교 검사를 추가한다. Tinygrad PKL을 ONNX인 것처럼
전송하지 않는다. 모델 다운로드/준비는 realtime frame loop에서 실행하지 않는다.

모드는 Off(기본), 검증(Shadow), 활성 연결로 구분한다. 초기 산출물은 Off/Shadow를
먼저 검증하되, 최종 목표인 활성 연결 구현과 승격 검증을 생략하지 않는다.
Shadow에서 native 모델이 제어를 담당하고 외부 결과는 비교/진단만 수행한다.

상태는 OFF → CONNECTING → PREPARING → READY → ACTIVE, 실패 시 LOST이다.
READY는 handshake/모델 준비 완료이지 차량 사용 검증 완료가 아니다.
외부 모델 활성 전환은 정차, cruise 해제, lateral 제어 해제 상태에서만 허용한다.
기존 항상 조향 기능이 켜져 있다는 이유로 전환 조건을 우회하지 않는다.

ACTIVE에서 링크 소실/오류/유효 시간 초과 시 외부 출력을 즉시 무효화하고 기존
selfdrived 경고/해제 경로와 연결한다. soft-disable과 native 복귀는 같은 제어 주기의
상태 전이를 테스트한다. 이전 결과를 새 프레임으로 재발행하지 않는다.
재연결은 READY로 돌아가며 운전 중 자동 재활성하지 않는다.

50ms는 end-to-end 프레임 예산이다. 태블릿 자체 추론 시간만 보고 통과시키지 않는다.
기존 model/pose validity, panda 제한, 운전자 감시를 완화하지 않는다.
구현 전 상태 전이 표에는 native 준비 실패, engaged/lateral-only/standstill,
단일 deadline miss와 지속 timeout 각각의 메시지 유효성 및 해제 동작을 명시한다.

## 7. 검증과 승격

1. 양방향 프로토콜/USB framing 및 queued/stateful fixture를 upstream Python과 비교.
2. Android 단위 테스트, lint, APK assemble, APK manifest/arm64 native library 확인.
3. 실제 ONNX를 host reference와 Android에서 동일 입력/상태로 연속 실행하여 비교.
4. carrotpilot focused tests와 replay: 순서 오류/끊김/늦은 결과/NaN/잘못된 모델,
   native 회귀, 상태 전환 및 control 이벤트 순서 확인.
5. C3X + Tab S9 Ultra USB 3 오프로드 연결 및 카메라 입력 Shadow 실행.
6. 최소 30분 지속 측정: 모델/해시/OS/backend/전원/냉각 조건, 메모리,
   thermal status, 전체 지연 분포와 모든 50ms 초과·유실 기록. 평균값만으로 통과 금지.
7. 최신 PR 필수 검사와 실제 빌드 성공 후 사용자 대상 차량 검증. 이슈는 검증 전 닫지 않는다.

실기/장시간 테스트는 현재 수행되지 않았다. 실패한 기기/모델 조합은 지원 목록에
넣지 않으며, 테스트 실패를 숨기는 자동 CPU 전환/임계값 완화는 허용하지 않는다.

## 8. 개발 단위와 문서

상위 #44 아래 구현 계획에서 Android 앱/추론, comma 연결/상태 전환,
모델 준비·가속 검증을 독립 이슈로 구분한다. 각 이슈는 최신 dev 기준 branch와
명시적 의존관계를 사용하고 dev 대상 PR을 만든다. dev/main 직접 push는 하지 않는다.
앱 CI를 기존 fast/integration gate에 연결하고 required check 이름을 보존한다.

사용자 설정/동작이 추가되는 변경은 `docs/user/ko/jetlink.md`와
`docs/user/en/jetlink.md`, 기존 settings 안내 및 `docs/user/docs_map.json`을 함께 갱신한다.
위치는 공개 기능 문서만 사용하고 개인 기기 로그/연락처/키는 포함하지 않는다.
`python tools/docs/check_user_docs.py --base origin/dev`를 실행한다.
현재 문서는 설계만 기록하므로 아직 사용자 가이드를 생성하지 않는다.

## 9. 확인된 한계와 후속 결정

- Android 정확한 버전은 실행 시 수집한다. 15로 단정하지 않는다.
- CPU는 수치 기준/진단용 출발점이다. Tab S9 Ultra의 실시간 충족 여부는 미측정.
- QNN 양자화/가속 라이브러리와 NNAPI 연산 coverage는 별도 검증 대상이며 지원을 가정하지 않는다.
- 원본 Cinque V3 ONNX/변환 아티팩트의 검증 없이는 실모델 완료 조건을 충족하지 않는다.
- 모델 활성 전환/실기 승격은 위 검증의 통과 기록이 필요하다.

## 근거

- https://zoompilot.ai/jetlink/
- https://github.com/zoompilot/jetlink/tree/f10f4705243812518e6441dfb06bf2178c408310
- https://github.com/zoompilot/jetlink/blob/f10f4705243812518e6441dfb06bf2178c408310/docs/conformance.md
- https://github.com/zoompilot/jetlink/blob/f10f4705243812518e6441dfb06bf2178c408310/docs/transport.md
- https://github.com/zoompilot/zoompilot/tree/e5b2f9e8d0bf92c3e68b34fd4083c0624febc7e1/openpilot/sunnypilot/accelerators/jetlink
- https://developer.android.com/develop/connectivity/usb/host
- https://onnxruntime.ai/docs/tutorials/mobile/deploy-android.html
- https://onnxruntime.ai/docs/execution-providers/QNN-ExecutionProvider.html
- https://www.samsung.com/ph/tablets/galaxy-tab-s/galaxy-tab-s9-ultra-5g-graphite-256gb-sm-x916bzaaxtc/
