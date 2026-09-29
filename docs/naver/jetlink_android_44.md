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
