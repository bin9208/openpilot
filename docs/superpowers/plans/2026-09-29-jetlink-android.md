# Jetlink Android Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Galaxy Tab S9 Ultra 12GB에서 큰 주행 모델을 실행하고 C3X carrotpilot과 USB로 연동한다.
**Architecture:** 모델 패키지/수치 계약, Android 서버, comma 어댑터를 별도 산출물로 만든다. 공통 fixture부터 구현하고 실제 제어 활성은 출력/지연/소실 전이 검증 후 승격한다.
**Tech Stack:** Python 3.12, Kotlin 2.1.20, ONNX Runtime 1.22.0, Android USB Host, 기존 tinygrad/cereal/pytest 및 GitHub Actions.
**Spec:** [사용자 승인 설계](../specs/2026-09-29-jetlink-android-design.md). 2026-09-29 사용자의 'ㄱㄱ'로 서면 설계 승인. 이 계획의 검토/실행 방식 선택은 별도 단계다.

## Global Constraints

- Galaxy Tab S9 Ultra 12GB, comma 3X; Android 15 이상은 사용자 추정이므로 실제 API level 확인.
- 최소 Android 9(API 28), 실기 arm64-v8a, 기본 OFF, USB 3, 태블릿 루팅 불필요.
- Jetlink upstream `f10f4705243812518e6441dfb06bf2178c408310`, wire version 2/32-byte little-endian.
- 원본 모델/출력/상태를 보존하고 PKL을 ONNX로 취급하지 않는다.
- 20Hz/50ms는 전체 프레임 예산. CPU 추론 성공/평균 지연만으로 주행 적합성을 주장하지 않는다.
- actual model, fixture, emulator, 기기, 차량 증거를 분리한다. 최소 30분 지속 성능 측정.
- 모델·USB·제어 경고 임계값을 테스트 통과 목적으로 완화하지 않는다.
- 원본/변환 모델은 검증된 NAS 모델 경로; 원본 모델, 캡처, 키를 Git에 넣지 않는다.
- 모델 활성 전환은 정차+cruise 해제+lateral 해제. 재연결은 READY이며 운전 중 자동 재활성 금지.
- 사용자 동작/설정 변경과 한/영 문서·docs_map·focused tests를 같은 PR에 반영한다.
- 기존 미커밋 작업 보존. 최신 dev에서 이슈별 branch, PR 대상 dev, 직접 dev/main push 금지.
- required checks: fast checks / integration gate / check mapped user docs. 누락/실패를 우회하지 않는다.

## Review Focus

1. 재연결 직후 이전 추론 완료 → Android A3 및 C3X C2에서 session generation 불일치 거부.
2. 형식은 정상이나 다른 모델의 output slices → Model M1 및 C3X C1에서 해시/metadata 대조 실패.
3. 화면 회전/알림 거부/OS 중지 → Android A4에서 중복 세션 방지 및 명시적 중단 검증.
4. USB 부분 전송/정확한 1024-byte 배수/16KiB burst → Android A1과 C3X C1에서 golden bytes 검증.
5. lateral-only/정차/모델 소실 직후 native 성공 → C3X C3에서 loss 경고가 native 성공으로 지워지지 않음.

## 산출물과 의존 순서

| 순서 | 계획 | 이슈 | 독립 검증 산출물 |
|---|---|---|---|
| 1 | [모델/계약](2026-09-29-jetlink-model-package.md) | #46 | 패키지 검사·준비 도구, pinned fixture, 실제 비교 보고서 |
| 2 | [Android 앱](2026-09-29-jetlink-android-app.md) | #45 | 설치 가능한 APK, protocol/실제 ONNX/USB 서버 |
| 3 | [C3X 연결](2026-09-29-jetlink-c3x.md) | #47 | Off/Shadow/Active 연결, warp·해제·복귀 및 replay |
| 4 | 아래 통합 검증 | #44 | 대상 기기 성능/설치 안내/사용자 차량 결과 |

M1/M2 계약·fixture를 먼저 dev에 통합하고 A1-A4를 구현한다. M3의 실모델
reference 검증은 A3와 연결해 완료한다. C1-C3은 dev에 반영된 계약을 사용한다.
각 계획의 일부만 완료되면 완료 task와 남은 acceptance를 이슈에 구분해서 기록한다.
app/패키지의 정적 단위 테스트는 실제 기기 연결 없이 진행할 수 있다.

## 공통 실행 및 CI 규칙

- [ ] 해당 이슈 시작 시 최신 origin/dev와 required check를 읽고 지정 branch를 생성한다.
- [ ] 각 task의 실패 테스트 → 최소 구현 → 통과 검사를 실행하고 파일을 명시해 커밋한다.
- [ ] CI 의존성이 생기는 첫 task에 fast/integration 호출을 함께 추가한다. Android는 reusable workflow로 APK를 실제 빌드한다.
- [ ] fast는 빠른 protocol/schema/model-state 단위 검사, integration은 Android lint/assemble 및 기존 build/navigation/APK 검사를 모두 포함한다.
- [ ] 매 push의 fast 결과와 PR 최신 SHA의 필수 검사, 빌드 아티팩트/해시를 기록한다.
- [ ] `python tools/docs/check_user_docs.py --base origin/dev`, `git diff --check`를 실행한다.
- [ ] reviewer 지적을 해결한 후에만 병합한다. post-merge check를 별도로 확인한다.

## 통합 검증/사용자 전달

- [ ] 승인된 원본/변환 ONNX 해시와 APK 서명·해시·기기/OS를 고정하고 C3X를 offroad 상태에서 연결한다.
- [ ] 실제 USB 속도, 권한, 모델 준비, 정상 추론을 확인한다. Android runtime 결과는 M3 reference와 비교한다.
- [ ] Shadow 30분을 실행한다. 50ms 초과/누락/NaN/재접속을 전부 기록하고 end-to-end p50/p95/p99/max와 발열/메모리를 내보낸다.
- [ ] 케이블 분리, 앱 종료, 과열/timeout 주입을 offroad/replay에서 검증한다. 제어 활성 상태 시퀀스는 C3 테스트와 대조한다.
- [ ] ACTIVE 승격 전 parity 통과, 전체 50ms 예산 내 연속 30분, loss/recovery 테스트 성공을 모두 요구한다. 조건 미달은 지원 불가/검증 대기로 남긴다.
- [ ] APK 업데이트는 기존 데이터 보존 설치. 앱 식별자/서명 불일치는 알리고 임의 삭제하지 않는다.
- [ ] 사용자에게 설치 APK·소스/PR·Actions·한/영 연결법·회수 방법을 제공한다. 실제 차량 검증은 사용자 기록 후 완료 처리한다.

## 실행 방식

권장: 이 세션에서 직접 구현(Native). 계약/모델/상태 전이 의존성이 강하므로
중간 맥락을 유지하며 순서대로 구현하고 마지막에 별도 리뷰를 받는다.
대안: task별 구현·리뷰 서브에이전트. 더 세분된 독립 리뷰를 제공하지만 실행 비용이 증가한다.
현재 계획은 self-review를 완료한 검토본이며 실행 방식은 사용자가 선택한다.

## 도구 버전 근거

- AGP 8.9.2 / Gradle 8.11.1 / JDK17 / build-tools35.0.0 / compileSdk35 / targetSdk35.
- https://developer.android.com/build/releases/agp-8-9-0-release-notes
- https://kotlinlang.org/docs/whatsnew2120.html
- `com.microsoft.onnxruntime:onnxruntime-android:1.22.0` POM을 Maven Central에서 확인.
- 모델 호환성 때문에 runtime 갱신이 필요하면 패키지 manifest/fixture/reference를 같은 변경에서 재검증한다.
