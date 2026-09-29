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
