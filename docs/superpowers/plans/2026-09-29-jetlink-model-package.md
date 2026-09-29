# Jetlink Model Package Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Android/host가 동일하게 검사할 ONNX 패키지와 실제 모델 출력 비교 도구를 만든다 (#46).
**Architecture:** 고정 upstream metadata/wire fixture를 공통 계약으로 두고 host에서 모델을 준비한다. 원본과 변환 파일의 정체성을 분리한다.
**Tech Stack:** Python 3.12, upstream pinned onnx/numpy/protobuf, ONNX Runtime CPU 1.22.0, pytest.
**Spec:** [승인 설계](../specs/2026-09-29-jetlink-android-design.md).

## Global Constraints

[전체 계획의 Global Constraints](2026-09-29-jetlink-android.md#global-constraints)를 모두 적용한다.
추가: source와 ONNX external data 합계 최대 4GiB, manifest 최대 64KiB, 원본과 변환 SHA-256 별도 저장.
원본/실제 캡처는 Git 제외, deterministic tiny ONNX만 fixture로 커밋한다.
Python 도구 의존성은 upstream `JetlinkKit/Scripts/fixture-pins.txt`를 확인해 requirements.lock에 고정한다.
앱 baseline ORT와 host ORT는 1.22.0으로 맞추며 원본 IR/opset 미지원은 명시적으로 실패한다.

## Review Focus

- M1: 정상 JSON에 잘못된 모델 해시/겹친 slices가 포함되는 경우 거부.
- M1: external_data 경로의 ../, 절대경로, symlink 이탈 거부.
- M1: 정수 overflow/shape 곱 overflow/4GiB 초과를 할당 전에 거부.
- M2: reset 후 첫 프레임과 frame_skip 1/2/4의 상태 일치.
- M3: 한 프레임은 맞지만 누적 상태가 다른 모델을 다중 프레임 검사에서 실패 처리.

## File Structure

- `tools/jetlink_model/manifest.schema.json`, `manifest.py`: 앱/host 공통 package v1 계약.
- `tools/jetlink_model/prepare.py`: ONNX metadata 추출/준비/원자적 package 확정.
- `tools/jetlink_model/compare.py`: reference/기기 출력 비교와 보고서.
- `tools/jetlink_model/export_fixtures.py`, `requirements.lock`: pinned reference와 재현 도구.
- `tools/jetlink_model/tests/`, `tools/jetlink_model/fixtures/`: 합성 검사와 공통 fixture.
- `tools/jetlink_model/README.md`, `THIRD_PARTY_NOTICES.md`: 사용법/라이선스/출처.

## Task M1: 검증 가능한 모델 패키지

**Files:** 위 schema/manifest.py/prepare.py 및 `tests/test_manifest.py`, `tests/test_prepare.py` 생성.
**Interfaces:**
- `validate_manifest(value: dict, root: Path) -> ModelPackage`.
- `prepare(source: Path, destination: Path, frame_skip: int) -> ModelPackage`.
- ModelPackage JSON 필드: `schema_version=1`, `source{checkpoint,sha256,bytes}`,
  `artifact{path,sha256,bytes}`, `runtime{name,version,backend}`, `prepare_version`,
  `frame_skip`, `inputs`, `outputs`, `output_slices`, `state_pairs`.
- inputs/outputs는 `{name:{dtype,shape}}`, slices는 `{name:[start,stop]}`.
  모든 dimension은 양의 고정 정수. metadata 의미와 ONNX 실제 I/O가 일치해야 한다.
- `source.checkpoint`는 원본 metadata에서 추출, 없으면 실모델 지원 거부.
  supported frame_skip은 1/2/4; queued/stateful 규칙은 pinned upstream ModelSpec 기준.

- [ ] 테스트 작성: `test_valid_package_roundtrip`, `test_wrong_hash_rejected`, `test_external_data_escape`, `test_overlapping_output_slices`, `test_oversize_before_allocation`.
  Assertions: parsed.schema_version == 1; 변조 hash/../ 경로/겹친 slices/4GiB+1 입력은 ValueError; 원본 파일은 바뀌지 않는다.
- [ ] `python -m pytest -c /dev/null --confcutdir=tools/jetlink_model/tests tools/jetlink_model/tests/test_manifest.py tools/jetlink_model/tests/test_prepare.py -q`로 미구현 실패 확인.
- [ ] prepare에서 upstream graph 변환 규칙을 필요한 범위로 이식하고 MIT 표기를 보존한다. 원본 metadata와 ORT session I/O를 검사하고 임시 디렉터리 완성 후 확정한다. artifact.path는 package 내부 상대경로만 허용한다.
- [ ] 동일 명령 PASS 및 `python -m tools.jetlink_model.prepare --help` exit 0 확인.
- [ ] 관련 파일만 add/commit: `feat: add validated Jetlink ONNX package contract`.

## Task M2: Python/Android가 공유하는 고정 fixture

**Files:** `export_fixtures.py`, `fixtures/`, `tests/test_reference_fixtures.py`, `requirements.lock`, notices 생성; `.github/workflows/fast-checks.yml`, `tools/ci/tests/test_workflow_policy.py` 수정.
**Interfaces:** `export_fixtures(destination: Path) -> dict[str, str]`는 파일별 SHA-256 manifest 반환.
`fixtures/index.json`은 upstream revision, generator/dependency 버전, 각 파일 해시와 종류(wire/staging/model)를 기록한다.

- [ ] 테스트 작성: `test_upstream_header_and_usb_bytes`, `test_reset_state_and_skip`, `test_fixture_hashes`. Assertions: 생성 bytes == 고정 upstream bytes; skip 1/2/4 tensor == upstream expected; reset 이후 기존 state 없음.
- [ ] `python -m pytest -c /dev/null --confcutdir=tools/jetlink_model/tests tools/jetlink_model/tests/test_reference_fixtures.py -q`로 fixture/구현 부재 실패 확인.
- [ ] pinned upstream Python generator의 wire/staging/tiny model 데이터를 필요한 범위로 가져오고 출처/라이선스를 보존한다. 작은 실제 queued/stateful ONNX와 golden 연속 출력을 포함한다.
- [ ] 위 명령 PASS. fast에 네트워크 없이 golden 비교를 추가하고 `python -m unittest discover -s tools/ci/tests -v` PASS. ORT 기반 모델 생성/실행은 integration에 연결한다.
- [ ] 명시한 파일만 commit: `test: pin Jetlink protocol and recurrent model fixtures`.

## Task M3: 실모델 reference와 가속 판정

**Files:** `compare.py`, `tests/test_compare.py`, README, `.github/workflows/jetlink.yml` 생성/수정.
**Interfaces:** `compare(reference: Path, candidate: Path, atol: float = 1e-5, rtol: float = 1e-4) -> ComparisonReport`.
보고서는 모델 원본/변환 해시, 입력 corpus 해시, frame 수, slice별 max abs/relative error,
NaN/shape/frame 오류, pass/fail을 JSON으로 저장한다. 위 오차 기준은 수치 검사 기준이며 차량 승인 기준이 아니다.

- [ ] `test_identical_sequence_passes`, `test_recurrent_drift_fails`, `test_nonfinite_fails`, `test_frame_or_hash_mismatch_fails` 작성. Assertions: 동일은 pass; 누적오차/NaN/다른 frame/hash는 fail.
- [ ] `python -m pytest -c /dev/null --confcutdir=tools/jetlink_model/tests tools/jetlink_model/tests/test_compare.py -q`로 실패 확인.
- [ ] 실제 Cinque V3 원본 ONNX의 출처/checkpoint/hash/배포 조건을 확인해 준비한다. 파일이 없으면 fake로 대체하지 말고 해당 acceptance를 pending으로 기록하되 독립 도구 구현은 계속한다.
- [ ] `python -m tools.jetlink_model.compare --reference <reference-dir> --candidate <candidate-dir> --output <report.json>` 실행. 합성 fixture는 CI, 실제 연속 모델 입력/출력은 비공개 artifact로 보관한다. PASS/FAIL에 맞게 exit code 반환.
- [ ] 앱 A3 CPU 결과를 reference와 비교한다. NNAPI/QNN 후보는 별도 runtime/해시/연산 coverage와 같은 수치 기준으로 평가하고 기준 미달·CPU fallback은 가속 성공으로 보고하지 않는다. QNN 준비는 SDK 배포 조건과 실제 필요한 연산을 확인한 뒤 수행한다.
- [ ] pytest PASS, 실모델 보고서와 미검증 사항을 이슈에 기록한 뒤 commit: `feat: compare Jetlink reference and Android model outputs`.

## CI 및 완료

- [ ] reusable `jetlink.yml`의 model job에서 host tests 및 tiny ONNX 실제 추론을 수행하고 필요한 runtime을 고정한다. parent integration gate는 이 job을 필수로 요구한다.
- [ ] 원본/변환 모델 패키지·실제 태블릿 결과·가속 지원 여부가 확인되기 전 #46을 닫지 않는다.
- [ ] 검증된 모델 게시 시 NAS의 별도 Jetlink 디렉터리를 쓰고 기존 PKL manifest는 바꾸지 않는다.
