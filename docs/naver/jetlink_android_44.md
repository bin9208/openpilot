# Jetlink Android 작업 기록 (#44)

- 이슈: https://github.com/bin9208/openpilot/issues/44
- 설계: [Jetlink Android 설계](../superpowers/specs/2026-09-29-jetlink-android-design.md)
- 대상: 사용자 지정 Galaxy Tab S9 Ultra 12GB, Android 15 이상 추정, comma 3X.
- 기준 dev: `354ee8ac71684bb56eea91159d696779d8d4ea90`.
- 작업 branch: `codex/feat-44-jetlink-android`.
- 상태: 사용자가 설계 승인. 구현 계획 검토본 작성; 앱/연동 코드, APK, 신규 기능 실기 결과 없음.
- 설계 검토: 대상, 모델 형식 차이, upstream 고정 revision, USB 프레이밍,
  모델 상태/검증 순서 및 기존 native 제어 보존을 확인했다.
- 기존 dev 기준 검사: https://github.com/bin9208/openpilot/actions/runs/36519427367
  (Jetlink 신규 구현의 검사 결과가 아니다.)
- 제품 검증은 host fixture, 실제 ONNX 출력, Android/C3X 실기, 차량 단계로 분리한다.
- 사용자 요구가 충족될 때까지 #44를 닫지 않는다.

## 구현 계획 (2026-09-29)

- [전체 계획](../superpowers/plans/2026-09-29-jetlink-android.md): 모델/앱/C3X 하위 계획 및 검증 순서.
- 구현 이슈: Android #45, 모델 패키지/동등성 #46, C3X #47.
- 계획 자체 점검: 설계 요구사항 대응, 입출력 타입, 실패 사례, 로컬 링크, 필수 섹션, placeholder 및 diff 검사.
- 계획 검토/실행 방식 선택 전이며 제품 구현에 착수하지 않았다.
- 설계 커밋 541bb289의 fast checks 성공: https://github.com/bin9208/openpilot/actions/runs/36521846616

## #46 호스트 구현

- 모델 패키지 검증/ONNX 준비, pinned wire·상태 버퍼 fixture, 연속 추론/출력 비교 도구 구현.
- 로컬 검사: host tests 39 passed, 1 skipped (Windows symlink 권한); CI 정책 4 passed.
- 실모델: CTV3 source 404a18cfd86d29637d20c697dfde245bb47c666ae016730ab674c65f4d1e1aa4.
- 원본의 declared Identity function을 제거한 최초 준비본은 엄격한 parity 실패.
  허용 오차를 바꾸지 않고 해당 함수와 그래프를 보존한 후, 합성 입력 8프레임
  원본/준비본 출력 최대 차이 0으로 통과했다. PC CPU/ORT1.22.0 결과다.
- 증거: tools/jetlink_model/evidence/2026-09-29-cinque-v3-host.json.
- Android 출력/지연/발열/USB/C3X/차량 검증은 아직 수행하지 않았다.

## #46 별도 리뷰 수정

- 별도 리뷰의 Important 3건: runtime 전 tensor byte 검사, 외부 데이터 alias 확장 합계,
  queued golden 수치 검사 누락. 각각 재현 후 크기 preflight와 명시적 golden 검사로 보완했다.
- 최종 로컬 host tests: 43 passed, 1 Windows symlink skip; CI 정책4 passed.
- queued 추론의 upstream golden 차이(max abs0.125)는 수치적으로 해결된 상태가 아니다.
  `golden_check`는 이를 unverified/exit1로 보고하고 문서에 차량 사용 미인증을 명시한다.
- Stateful tiny golden은 테스트에서 비교한다. 실제 CTV3 host parity와 queued 미검증을 구분한다.
- PR: https://github.com/bin9208/openpilot/pull/48 . #46은 Android parity/가속/실기 조건이 남아 열린 상태다.

## 구현 기록 (2026-09-29)

- #46 host 준비·실제 ONNX 연속 추론: PR #48, dev 병합 `01aaa6365b3d4c1669da217c1fccfa10a9f4a2bb`. CTV3 원본 SHA `404a18cfd86d29637d20c697dfde245bb47c666ae016730ab674c65f4d1e1aa4`, 원본 보존 package의 PC 8프레임 최대 절대 오차 0. queued golden은 불일치이므로 Android에서 명시적으로 거부.
- #45 Android 앱: PR #50, 검토 후 수정 head `0bb09b07dd30bfd171ca4d3ae86f4dd15f3da914`, dev merge `b41c4992`. core22+실제 ORT JVM2, lint/APK assemble/arm64 라이브러리·서명·16KiB 정렬 확인. 필수 Actions: https://github.com/bin9208/openpilot/actions/runs/36533856436 . 병합 후 Integration/fast/docs도 성공(36534771471/36534770741/36534770803).
- 사용자는 APK를 받아 나중에 직접 설치·검증하기로 선택. ADB 대상 기기 없음. Android 실제 추론·USB·발열/주행 검증을 완료했다고 주장하지 않는다.
- #47 C3X: 고정 v2 USB subset, 별도 owner + deadline Unix RPC, native QCOM warp 재사용, native 모델 매 프레임 유지, Off/Shadow/명시 활성 요청 및 loss latch 구현. source/loss는 modelV2와 drivingModelData의 새 append 필드. 기존 유효성/panda/모니터링 정책 유지. 활성 프로필은 동일 모델/기기/API/제공자/앱/ORT 및 실측 증거가 없으면 false.
- 로컬 C3X 관련103 tests에 owner request 경계1개를 더하여104 tests PASS. 실제 Linux selfdrived/state 검사는 Windows fcntl 제약으로 수집 불가; 필수 Linux build-release job에서 수행. 새로운 APK0.1.1은 검증 프로필 식별을 위한 handshake 기기 정보를 추가.
- 실제 C3X QCOM 빌드·warp pixels·USB 속도·30분 Shadow 및 차량 확인은 미수행. Linux 호스트 빌드와 구분하고 #44/#45/#46/#47을 실기 기준 충족 전 닫지 않는다.
- 별개 upstream mirror 실패는 #49로 분리. 기존 미러를 강제 갱신하지 않았다.

C3X 별도 리뷰에서 동시 RPC 종료 예외와 원본/실행 파일 해시 구분 부족을 확인했다. 각 실패를 테스트로 재현한 뒤 소켓 참조의 잠금 분리와 실행 artifact SHA handshake 검증으로 수정했다. 최신 로컬107 focused tests 및 Android24 tests/lint/APK가 통과했다. 활성 검증 기록은 실행 artifact까지 일치해야 하며 최초 C3X 지원은 원본 보존 CTV3로 한정한다. PR #51 최신 SHA의 필수 Actions와 실제 Linux 빌드 결과는 GitHub를 기준으로 확인한다.
