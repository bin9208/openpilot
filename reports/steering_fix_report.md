# Steering Fix Report

## 요약
- 분석 루트: `/mnt/e/comma_backup/7.5`
- 로그: 총 329개 발견, 정상 파싱 328개 (`rlog` 164개, `qlog` 164개), 손상/lock 파일 1개
- 영상: 총 660개 발견, 첫 프레임 디코딩 성공 656개, lock/빈 파일 4개
- rlog 기준 구간: 2.71시간, latActive 유효 구간 1.91시간
- 결론: 코드 steerRatio 14.26이 liveParameters 평균 16.17와 맞지 않았고, 고속 차선유지 응답 지연은 평균 0.13s로 기존 `steerActuatorDelay=0.10`보다 컸다. 또한 이전 조향 모델에서 완료된 `LiveDelay`가 그대로 복원되어 새 delay prior와 재학습이 실제 제어 경로에 반영되지 않는 캐시 문제가 있었다.

## 분석한 로그 목록
- 전체 로그 파일 수: 329
- 정상 rlog: 164개
- 정상 qlog: 164개
- 파싱 제외/실패: `/mnt/e/comma_backup/7.5/00000b52--0df7864286--145/rlog.lock`: ValueError: unknown extension .lock
- 전체 파일별 목록은 부록 A에 포함했다.

## 분석한 영상 목록
- 카메라별 파일 수: dcamera 165개, ecamera 165개, fcamera 165개, qcamera 165개
- 정상 영상 스트림 및 첫 프레임 디코딩 성공: 656개
- 영상으로 열 수 없던 파일: `/mnt/e/comma_backup/7.5/00000b52--0df7864286--145/dcamera.hevc.lock`: /mnt/e/comma_backup/7.5/00000b52--0df7864286--145/dcamera.hevc.lock: Invalid data found when processing input
- 영상으로 열 수 없던 파일: `/mnt/e/comma_backup/7.5/00000b52--0df7864286--145/ecamera.hevc.lock`: /mnt/e/comma_backup/7.5/00000b52--0df7864286--145/ecamera.hevc.lock: Invalid data found when processing input
- 영상으로 열 수 없던 파일: `/mnt/e/comma_backup/7.5/00000b52--0df7864286--145/fcamera.hevc.lock`: /mnt/e/comma_backup/7.5/00000b52--0df7864286--145/fcamera.hevc.lock: Invalid data found when processing input
- 영상으로 열 수 없던 파일: `/mnt/e/comma_backup/7.5/00000b52--0df7864286--145/qcamera.ts.lock`: /mnt/e/comma_backup/7.5/00000b52--0df7864286--145/qcamera.ts.lock: Invalid data found when processing input
- 전체 영상 파일 목록은 부록 B에 포함했다.

## 발견한 조향 문제
- liveParameters steerRatio mismatch: 328개 로그에서 감지
- steering command delay: 136개 로그에서 감지
- model/path instability: 49개 로그에서 감지
- lane visibility drop: 34개 로그에서 감지
- curve-entry understeer: 32개 로그에서 감지
- controller angle/rate limiting: 27개 로그에서 감지
- 문제 재현 window: 68개. 40km/h 이상 차선유지 window는 1개이며 lane visibility와 MPC valid는 정상이고 차량 응답 지연이 주 패턴이었다.

## 대표 재현 구간
| 세그먼트 | 시간(s) | 속도(km/h) | 목표 | 적용 | 실제 | p95 목표오차 | p95 적용오차 | 차선/MPC | 패턴 |
|---|---:|---:|---:|---:|---:|---:|---:|---|---|
| `00000b53--893429fa7f--72` | 27.9-28.4 | 48.4 | -18.09 | -18.01 | -9.53 | 11.72 | 11.55 | 1.00/1.00 | vehicle steering response lag |
| `00000b53--893429fa7f--82` | 14.5-15.2 | 8.5 | -340.66 | -175.00 | -405.40 | 79.63 | 273.30 | 1.00/1.00 | controller angle/rate limiting |
| `00000b52--0df7864286--143` | 32.9-34.0 | 13.5 | -207.60 | -174.83 | -286.94 | 119.92 | 194.60 | 1.00/1.00 | controller angle/rate limiting |
| `00000b53--893429fa7f--82` | 15.3-16.0 | 10.0 | -243.19 | -175.00 | -273.37 | 48.36 | 148.21 | 1.00/1.00 | controller angle/rate limiting |
| `00000b52--0df7864286--140` | 41.7-42.3 | 8.8 | -200.83 | -175.00 | -101.13 | 111.69 | 90.58 | 1.00/1.00 | controller angle/rate limiting |
| `00000b52--0df7864286--140` | 41.1-41.6 | 8.5 | -149.20 | -121.29 | -62.75 | 97.83 | 83.46 | 1.00/1.00 | controller angle/rate limiting |
| `00000b52--0df7864286--143` | 7.1-7.8 | 7.7 | -200.13 | -175.00 | -115.74 | 92.83 | 61.60 | 1.00/1.00 | controller angle/rate limiting |
| `00000b52--0df7864286--143` | 24.1-24.7 | 8.4 | -22.84 | -20.73 | 27.63 | 73.11 | 69.32 | 1.00/1.00 | controller angle/rate limiting |
| `00000b52--0df7864286--143` | 14.5-15.0 | 8.0 | -222.41 | -175.00 | -176.30 | 64.05 | 4.54 | 1.00/1.00 | controller angle/rate limiting |
| `00000b52--0df7864286--113` | 10.3-10.9 | 7.4 | 63.33 | 63.07 | 0.12 | 69.83 | 69.85 | 1.00/1.00 | controller angle/rate limiting |

## 원인 가설 및 검토
1. liveParameters/차량 제원 불일치: 채택. rlog 164개 전체 정상 로그에서 live steerRatio 평균 16.17, p10 15.98, p90 16.32로 코드값 14.26과 평균 1.91 차이가 났다.
2. 조향 actuator delay 과소 보정: 채택. 40km/h 이상 로그에서 best lag 평균은 약 0.15s이며 기존 공통값은 0.10s였다. 대표 영상도 차선이 정상적으로 보이는 구간이었다.
3. 모델 경로/차선 인식 문제: 일부 구간에서 보조 원인. 49개 로그에서 path instability가 있었지만 대표 고속 문제 구간은 lane_visible=1.0, mpc_valid=1.0이었다.
4. driver override 오판/CAN/panda safety 문제: 기각. carState.canValid invalid 0회, panda fault 0회, calibration bad 0회였다.
5. controller torque/rate 제한: 부분 보류. 저속 대조향 구간에서는 제한이 보였지만 40km/h 이상 차선유지 구간의 controller clip p95 평균은 작았다.
6. 이전 조향 모델의 `LiveDelay` 캐시 재사용: 채택. 2차 스캔에서 읽힌 `liveDelay` 32,674개는 모두 `estimated`, `calPerc=100`, `validBlocks=50`이었고 `lateralDelay` 평균은 0.289s였다. 기존 코드는 fingerprint만 같으면 이 값을 계속 복원하므로 steerRatio와 actuator delay를 수정해도 새 기본값과 재학습이 시작되지 않았다.
7. 사용자 `SteerActuatorDelay` 고정값: 운영상 마스킹 가능성. `controlsd`와 `modeld`는 이 값이 0이 아니면 `liveDelay` 대신 고정값을 사용한다. Params 코드 기본값은 0이지만 `carrot_settings.json`의 설정 초기화 기본값은 30(0.30s)이므로, 실차 검증에서는 반드시 0(LiveDelay 모드)인지 확인해야 한다. 이 값은 주행 로그 메시지에 직접 기록되지 않아 기존 로그만으로 현재 장치 값을 확정할 수 없다.

## 튜닝안 비교
| 안 | steerRatio | steerActuatorDelay | 판단 |
|---|---:|---:|---|
| 보수적 | 15.4 | 0.13s | 로그 평균과 아직 차이 커서 미채택 |
| 중간 | 16.2 | 0.15s | liveParameters 평균/고속 lag와 가장 근접해 채택 |
| 공격적 | 16.35+ | 0.17s+토크 회복 상향 | 오버슈트/운전자 불쾌감 위험으로 미채택 |

## 채택한 수정
- `opendbc_repo/opendbc/car/hyundai/values.py`: `HYUNDAI_IONIQ_5_PE` CarSpecs steerRatio를 14.26에서 16.2로 변경.
- `opendbc_repo/opendbc/car/hyundai/interface.py`: angle-control 경로에서 `HYUNDAI_IONIQ_5_PE`만 `steerActuatorDelay=0.15`로 보정.
- `selfdrive/locationd/lagd.py`: 이전 route의 제어 방식(angle/torque), steerRatio 또는 steerActuatorDelay가 현재 CarParams와 다르면 기존 `LiveDelay`를 폐기하고 현재 기본값에서 재학습한다. 유효 블록이 0이거나 범위를 벗어나거나 상태가 invalid인 캐시도 명시적 예외로 폐기하되, 동일 조향 모델에서 수집된 부분/완료 학습은 유지한다.
- `selfdrive/locationd/test/test_lagd.py`: 제어 방식/비율/지연 변경, 미학습·손상 캐시, 부분 학습 유지, 완료 학습 유지, 실제 Param 삭제, 현재 기본 지연 출력을 회귀 테스트로 고정했다.

## 수정 전/후 비교
| 지표 | 수정 전 | 수정 후 | 변화 |
|---|---:|---:|---:|
| live steerRatio 대비 CarSpecs 절대 오차 | 1.908 | 0.099 | 94.8% 감소 |
| 40km/h 이상 actuator delay 절대 오차 | 0.052s | 0.018s | 66.3% 감소 |
| 로그 재시뮬레이션 controller clip p95 | 0.000deg | 0.000deg | 거의 동일 |
| 전체 active 샘플 수 | 663992 | 663992 | 동일 로그 재사용 |
| 조향 방식/비율/지연 변경 후 이전 `LiveDelay` 0.289s 복원 | 복원됨 | 폐기 후 현재 기본값 0.35s 사용 | 새 delay prior와 재학습이 첫 주행부터 적용됨 |
| 유효 블록 0인 `LiveDelay` 0.30s 복원 | 복원됨 | 폐기됨 | 오래된 fallback 고착 제거 |
| 동일 모델의 부분/완료 학습 | 복원됨 | 계속 복원됨 | 정상 warm-start 유지 |

## 실행한 검증
- WSL 전체 로그 인벤토리: 989개 관련 파일 발견.
- WSL LogReader 전체 파싱: 329개 로그 중 328개 정상, `*.lock` 1개 제외.
- WSL ffprobe/ffmpeg: 660개 중 실제 영상 656개 첫 프레임 디코딩 성공, 0바이트 `*.lock` 4개 제외.
- 대표 문제 구간 qcamera 프레임 추출: 9개 성공.
- `python3 -m py_compile opendbc_repo/opendbc/car/hyundai/values.py opendbc_repo/opendbc/car/hyundai/interface.py`: 통과.
- `python3 -m compileall -q opendbc_repo/opendbc/car/hyundai`: 통과.
- `CAR.HYUNDAI_IONIQ_5_PE.config.specs.steerRatio`: 16.2 확인.
- WSL `.venv` 의존성 동기화 후 `pytest 8.3.5`, `ruff 0.11.5`, `basedpyright 1.39.9`, SCons 4.9.1을 사용했다.
- red 검증 1: 구현 전 캐시 회귀 테스트는 비율 변경, 지연 변경, 유효 블록 0에서 `3 failed, 3 passed`였다.
- red 검증 2: 제어 방식 angle→torque 변경 사례를 추가한 직후 `1 failed, 6 passed`로 기존 누락을 재현했다.
- `python -m pytest selfdrive/locationd/test/test_lagd.py selfdrive/locationd/test/test_calibrationd.py -q`: 최종 `16 passed`.
- `python -O -m pytest -W ignore::pytest.PytestConfigWarning selfdrive/locationd/test/test_lagd.py -q`: 최적화 모드 `9 passed`; 손상 캐시 검증이 assert 제거 후에도 유지됨을 확인했다.
- `ruff check selfdrive/locationd/lagd.py selfdrive/locationd/test/test_lagd.py`: 통과.
- `basedpyright --level error selfdrive/locationd/test/test_lagd.py`: `0 errors, 0 warnings`.
- `python -m py_compile selfdrive/locationd/lagd.py selfdrive/locationd/test/test_lagd.py`: 통과.
- `git diff --check` 및 LF 줄바꿈 검사: 통과.
- `scons -j32 selfdrive/pandad/pandad_api_impl.so`: WSL 네이티브 Python 모듈 빌드 통과.
- locationd 전체 테스트 수집: 관련 테스트 13개는 통과했으나 작업 범위 밖의 기존 `selfdrive/controls/radard.py:758` 들여쓰기/혼합 줄바꿈 손상으로 `test_locationd_scenarios.py` 수집이 중단됐다.
- process replay: 공식 `CONFIGS`에 `lagd`가 없어 직접 replay 대상이 아니며, runner import도 위 `radard.py` 오류에서 중단됐다. 대신 실제 Params와 Cap'n Proto 직렬화, `retrieve_initial_lag`, `LateralLagEstimator`를 함께 사용하는 9개 캐시 시나리오로 해당 경로를 검증했다.
- `basedpyright --level error selfdrive/locationd/lagd.py`: 변경 줄이 아닌 기존 ndarray/Cap'n Proto 타입 선언 줄에서 13개 오류가 남아 있다.
- 기본 warning 레벨의 basedpyright는 Cap'n Proto/Params 타입 스텁 부재로 새 테스트에도 unknown-type 경고를 내므로 error 레벨을 품질 게이트로 사용했다.

## 리뷰 지적사항 처리
- comment-checker가 새 코드 주석을 지적했고, 해당 주석은 보고서로 충분하다고 판단해 코드에서 제거했다.
- 회귀 테스트의 Given/When/Then 주석은 테스트 의도를 고정하는 BDD 주석이라 유지했다.
- 캐시 검증에 `assert`를 쓰면 최적화 모드에서 사라진다는 리뷰는 반영해 명시적 `ValueError` 검사로 변경했고 `python -O` 테스트를 추가했다.
- 일부 리뷰 에이전트가 Windows Python 또는 WSL 시스템 Python으로 pytest/Ruff/basedpyright를 실행해 실패한 지적은 반영하지 않았다. 사용자 요구 환경인 WSL 저장소 `.venv`의 정확한 실행 파일로 다시 실행한 결과는 각각 16 passed, Ruff 통과, error-level basedpyright 0건이다.
- `SteerActuatorDelay=30`을 코드에서 자동으로 0으로 덮어쓰라는 지적은 반영하지 않았다. 이 값은 명시적 사용자 override이므로 무단 변경은 다른 차량과 의도적 수동 튜닝에 위험하다. 대신 보고서 위험성과 실차 체크리스트 첫 항목에서 0(LiveDelay 모드)을 필수 조건으로 지정했다.
- 작업 트리의 다른 수정·삭제를 정리하라는 지적은 반영하지 않았다. 사용자 변경을 되돌리지 않고 이번 세 파일만 정확한 경로로 스테이징해 커밋한다.
- `lagd.py`가 250줄을 넘는 기존 모듈이라는 지적은 확인했으나, 안전 중요 제어 코드의 광범위 분리는 이번 좁은 캐시 동작 수정의 검증 범위를 크게 확장하므로 별도 작업으로 남겼다.
- 전체 locationd/process replay 차단 원인인 `radard.py`는 이번 작업 이전부터 존재하는 별도 변경이며 사용자 작업을 되돌리지 않는 원칙에 따라 수정하지 않았다.

## 남은 위험성
- 실차 EPS 응답은 온도, 타이어, 노면, MDPS 상태에 따라 로그 평균과 다를 수 있다.
- 0.15s delay는 평균 lag에 맞춘 값이므로 일부 구간에서 조향 선행감이 늘 수 있다.
- 업데이트 후 첫 주행은 이전 `LiveDelay`가 의도적으로 초기화되어 총 지연 기본값 0.35s로 시작한다. 충분한 고속 유효 데이터가 쌓이면 새 추정값으로 전환된다.
- 장치의 `SteerActuatorDelay`가 0이 아니면 고정 지연이 우선하여 `LiveDelay` 재학습 효과가 제어 경로에서 보이지 않는다.
- 원본 로그에는 저속 대조향 구간이 포함되어 있어 주차/교차로 저속 조향과 HDA 차선유지 튜닝을 분리해서 해석해야 한다.

## 실차 테스트 체크리스트
1. 출발 전 `SteerActuatorDelay=0`인지 확인해 고정 지연 대신 LiveDelay를 사용한다.
2. 업데이트 첫 주행 시작 직후: `liveDelay.status=unestimated`, `validBlocks=0`, `lateralDelay` 약 0.35s인지 확인.
3. 40-80km/h 직선: 조향이 좌우로 잔진동하지 않고 차선 중앙을 유지하는지 확인.
4. 완만한 커브 진입: 기존보다 조향 시작이 늦지 않고 바깥쪽으로 밀리지 않는지 확인.
5. 커브 탈출: 조향 복귀가 과하게 빠르거나 안쪽으로 감기지 않는지 확인.
6. 운전자 손토크 개입: steeringPressed 이후 assist 복귀가 자연스럽고 운전자 의도를 방해하지 않는지 확인.
7. 20km/h 이하 큰 조향: EPS fault, steerFaultTemporary, LKAS 해제/재개가 없는지 확인.
8. 10분 이상 반복 주행: `liveDelay`, `liveParameters.steerRatio`, `angleOffsetDeg`, `stiffnessFactor`, `steerFaultTemporary`, panda faults 기록.

## 롤백 방법
- 커밋 후에는 `git revert <이번 커밋 해시>`를 사용한다.
- 수동 롤백은 `values.py`의 Ioniq 5 PE steerRatio를 14.26으로 되돌리고, `interface.py`의 Ioniq 5 PE `steerActuatorDelay=0.15` 분기를 제거한다.
- 2차 수정만 수동 롤백하려면 `lagd.py`의 steering model 일치 검사와 `valid_blocks <= 0` 분기를 제거하고 `test_lagd.py`를 함께 되돌린다.
- 코드 롤백 뒤에는 새 조향 모델에서 저장된 캐시가 재사용되지 않도록 장치에서 `python3 -c 'from openpilot.common.params import Params; Params().remove("LiveDelay")'`를 한 번 실행한다.

## 후속 수정: 레이더 인게이지, 끼어들기, 저속 MDPS 소음

### 결론
- 인게이지 불가의 직접 원인은 `fcbec5ee` 커밋이 `RadarD._cut_in_candidate`를 클래스 들여쓰기 2칸에서 1칸으로, 내부 `maybe_add_cut_in`을 4칸에서 3칸으로 바꾼 것이다. `radard.py:758`에서 `IndentationError`가 발생해 프로세스가 시작되지 않았다.
- 최근 끼어들기 패치는 차선 근거 제한을 통째로 제거하고 확인 카운터를 오래 보존했다. 실제 끼어들기를 빨리 잡았지만 대표 60초 replay에서 탐지 에피소드가 기존 1회에서 14회로 늘어 그대로 사용할 수 없었다.
- 끼어들기 오탐의 근본 원인은 미래 상대거리 계산에 상대속도 `vRel`이 아니라 선행차 절대속도 `vLead`를 쓴 것이다. 상대속도 0인 평행 차량도 1초 뒤 25m 전진한 위치의 곡선 경로로 투영됐다.
- 실제 빠른 끼어들기 누락의 직접 원인은 side 후보가 다음 프레임 중앙 트랙이 되는 순간 `cut_in_count`를 발행 없이 0으로 지운 상태 전이였다.
- 저속 소음의 가장 가능성 높은 원인은 토크 최대값 자체보다 작은 각도 명령의 빈번한 방향 반전이다. 기존 1차 rate limit는 큰 명령에서 지연을 만들면서 작은 반전은 대부분 통과시켰다.
- 최종안은 Ioniq 5 PE angle-control의 `LKAS_ANGLE_MAX_TORQUE`를 활성 시 실사용 상한 `250`으로 고정하고 속도별 토크 축소를 제거한다. DBC의 `[0|255]`는 8비트 저장 범위이며, 차량 의미상 예약된 251~255는 사용하지 않는다. 대신 작은 저속 각도 명령에만 2차 rate smoothing을 적용하고, 8도 이상 오차에서는 빠른 응답으로 전환하며 4~6m/s에서 smoothing을 해제한다.

### 레이더 오류 재현과 수정
- 재현: `.venv/bin/python -m py_compile selfdrive/controls/radard.py` 및 새 pytest 수집 모두 `radard.py:758 IndentationError`로 실패했다.
- Git 근거: `git show fcbec5ee -- selfdrive/controls/radard.py`에서 두 잘못된 공백 변경이 커밋 자체에 포함된 것을 확인했다. 로컬 checkout만의 문제가 아니다.
- 수정: 클래스 메서드와 내부 함수 들여쓰기를 복구했다.
- 회귀 방지: `selfdrive/controls/tests/test_radard_cut_in.py`가 `radard` import와 실제 후보 판정을 함께 실행한다.

### 끼어들기 로그 및 영상 대조
- 전체 164개 rlog, 9,750.78초를 다시 파싱했다. 기록된 `radarErrors` 활성 프레임과 `carState.canValid=False` 프레임은 모두 0이었다.
- 자동 후보는 옆 차선 `radarTrackId`가 중앙/선행 트랙으로 전환된 시점을 기준으로 찾았다. 원시 전환은 193건이었지만 커브, 교차로, 자차 경로 이동에 의한 분류 변화가 다수여서 그대로 정답으로 쓰지 않았다.
- 속도, 자차 lane-change 상태, `dPath`, `vLat`, 전환 시간을 적용해 13개 이벤트가 포함된 11개 세그먼트로 좁힌 뒤 fcamera 접촉시트와 로그를 직접 대조했다.
- 실제 누락 사례: `00000b53--893429fa7f--23`의 좌측 SUV는 최종 replay에서 8.45초 `track 55`로 경계 진입부터 검출됐다. `...--29`의 좌측 진입 차량은 5.74초 `track 52`로 중앙 진입 2프레임째에 검출됐다.
- `...--29`에서 초기안이 5.59초에 잡은 `track 62`는 실제 진입차가 아니라 인접 차량이었다. 영상과 side-to-center 전환 로그를 다시 맞춰 실제 차량 `track 52`를 기준으로 필터를 재설계했다.
- 11개 후보 세그먼트의 기록된 `leadsCutIn` 활성 프레임은 599개였다. 최종 로직은 두 실제 누락을 추가 검출하면서 활성 프레임을 483개로 19.4% 줄였다.
- 3초 이상 횡방향 외삽은 먼 옆 차량을 차선 안으로 투영했다. `RadarLatFactor`가 100을 넘어도 끼어들기 예측 horizon은 최대 1.0초로 제한했다.

### 끼어들기 변경
- 차선 신호가 없을 때도 탐지는 가능하지만 `dPath` 경계 1.8m 이내, 기본 in-lane overlap, 실제 중앙 접근 또는 강한 경계 진입을 함께 요구한다.
- `dRel_future`는 `dRel + vRel * horizon`으로 계산한다. 평행 차량을 절대속도만큼 먼 곡선 위로 보내던 `vLead` 투영을 제거했다.
- expanded-only 신호는 예고 카운터만 올린다. 근거리가 아니면 현재 base overlap 0.25 초과와 현재/미래 최대 0.28 초과가 함께 있어야 직접 발행한다.
- 확인은 근거리 2프레임, 원거리 3프레임 연속 조건으로 변경했다. 한 프레임이라도 후보가 아니면 `cut_in_count`를 0으로 초기화해 과거 후보가 다음 단발 신호를 즉시 확정하지 못하게 했다.
- expanded 예고 뒤 중앙 트랙으로 들어오면 2프레임 연속 중앙 상태에서 끼어들기로 발행한다. 입력 모델 lead가 비면 side/center 확인 상태를 모두 초기화한다.

### 저속 조향 로그 근거
| 지표 | 기존 로그 | 선택한 2차 smoothing 재시뮬레이션 | 변화 |
|---|---:|---:|---:|
| 저속 latActive 분석 시간 | 2,390.18s | 동일 | 동일 입력 |
| 분석 샘플 | 242,539 | 동일 | 동일 입력 |
| 목표각-적용각 MAE | 1.5386deg | 1.4941deg | 2.9% 개선 |
| 목표각-적용각 weighted p95 | 11.6904deg | 8.0560deg | 31.1% 감소 |
| 적용각 명령 가속도 p95 | 0.1714deg/tick² | 0.1348deg/tick² | 21.4% 감소 |
| 적용각 속도 방향 반전 | 3,753회 | 2,441회 | 35.0% 감소 |
| 반전 빈도 | 94.21회/분 | 61.28회/분 | 32.93회/분 감소 |

### 조향안 비교
| 안 | 작은 명령 accel/decel | 로그 결과와 판단 |
|---|---:|---|
| 보수적 소음 억제 | 0.05/0.10deg/tick² | MAE 1.4941, 가속도 p95 0.1348, 반전 2,441회로 채택 |
| 중간 | 0.08/0.16deg/tick² | MAE 1.4467, 가속도 p95 0.1619, 반전 2,720회로 미채택 |
| 공격적 응답 | 0.14/0.28deg/tick² | MAE 1.4121이나 가속도 p95 0.1989, 반전 2,833회로 소음 목표에 불리해 미채택 |

### 조향 변경 상세
- 공통 angle-control 기본값과 하드 상한은 모두 `250`, `CarControllerParams.ANGLE_MAX_TORQUE`는 기존 `200`으로 유지한다. DBC 비트 상한 255를 실사용 토크 상한으로 해석한 이전 변경은 취소했다.
- Ioniq 5 PE만 `CustomSteerMax`가 더 낮더라도 정상 활성 angle-control 최대 권한을 250으로 고정한다.
- 기존 `get_low_speed_angle_torque_factor()`와 0~6m/s의 60~100% 토크 축소를 제거했다.
- 작은 저속 목표에는 command-rate accel 0.05, decel 0.10deg/tick²를 적용한다.
- 목표 오차가 8도보다 크면 accel을 최소 0.35deg/tick²로 올려 커브 진입과 큰 복원 조향을 지연시키지 않는다.
- smoothing은 4m/s 이하에서 완전히 적용되고 4~6m/s에서 연속적으로 해제되며 6m/s부터 기존 rate 응답과 사실상 동일하다.
- 운전자가 `steeringPressed` 상태일 때 토크를 양보하고 해제 후 회복시키는 안전 로직은 유지한다. “250 고정”은 운전자 override와 비활성 상태를 제외한 정상 angle-control 권한을 뜻한다.

### Panda safety 원복 및 테스트 하네스 수정
- 1차 패치에서는 HDA1 `steer_torque_cmd_checks()` 위반 시 실제 전송을 거부하도록 바꿨으나, 기존 포크 동작과 자동 크루즈 호환성을 우선한다는 사용자 지시에 따라 최종안에서는 `tx=false` 차단을 다시 제거했다.
- Hyundai CAN-FD 조향 최대값·변화율·운전자 토크·steer-request 위반 계산은 남지만 Panda TX hook은 이를 이유로 프레임을 거부하지 않는다. 조향 제한은 controller가 담당한다.
- 생산 Panda 코드에 존재하는 `safety_tx_buffered_for_fwd`, `putui`, `memcpy/memset` 선언이 libsafety 하네스에 없어 빌드가 깨지던 문제를 보완했다.
- 생산 `safety_fwd_hook(CANPacket_t *)`와 기존 Python 테스트 ABI `safety_fwd_hook(bus, addr)` 차이는 테스트 전용 packet 래퍼로 연결했다. 생산 forwarding 코드는 바꾸지 않았다.
- Ioniq 5 PE와 같은 EV/HDA1 설정에서 실제 `0x12A` 패킷의 `LKAS_ANGLE_MAX_TORQUE=250`을 확인하고 Panda TX hook 통과를 검증했다.

### 후속 수정 파일
- `selfdrive/controls/radard.py`
- `selfdrive/controls/tests/test_radard_cut_in.py`
- `opendbc_repo/opendbc/car/hyundai/carcontroller.py`
- `opendbc_repo/opendbc/car/hyundai/tests/test_angle_control.py`
- `opendbc_repo/opendbc/safety/safety/safety_hyundai_canfd.h`
- `opendbc_repo/opendbc/safety/board/fake_stm.h`
- `opendbc_repo/opendbc/safety/board/drivers/can_common.h`
- `opendbc_repo/opendbc/safety/tests/libsafety/safety.c`
- `opendbc_repo/opendbc/safety/tests/test_hyundai_canfd.py`
- `selfdrive/test/process_replay/process_replay.py`
- `opendbc_repo/opendbc/dbc/generator/generator.py`
- `reports/steering_fix_report.md`

### 후속 검증
- radard red: 새 테스트 수집이 `IndentationError`로 실패했다.
- radard green 1차: `py_compile` 통과, `1 passed`.
- cut-in red: 약한 expanded overlap과 누적 확인 테스트 `2 failed, 2 passed`.
- cut-in red/green: `vLead` 미래 상대거리, expanded-only 발행, 원거리 overlap, 중앙 진입, 입력 공백과 stale `leadsCutIn` 상태 테스트를 각각 실패부터 재현했다.
- 집중 회귀: lead, radar cut-in, angle-control 통합 테스트 합계 `24 passed`.
- Ioniq 5 PE 대응 EV/HDA1 Panda 핵심 안전 `9 passed`는 1차 TX 차단 패치 검증 결과다. 최종 원복 뒤에는 기존 포크 정책대로 제한 초과 프레임도 통과하므로 해당 거부 테스트는 통과 조건이 아니다.
- 전체 164개 rlog를 재파싱하고, 13개 이벤트/11개 후보 세그먼트의 모든 영상을 대조한 뒤 최종 `RadarD.update()`를 11개 세그먼트에 재생했다.
- 정식 process replay: 두 실제 세그먼트에서 Ioniq 5 PE 지문 판별 후 모델 입력과 같은 수의 `radarState`를 각각 `1,201/1,201`, `1,200/1,200` 생성했다. 각 첫 시작 프레임을 제외한 valid 수는 1,200과 1,199였고 `radard` stderr는 비어 있었다.
- WSL 빌드: longitudinal MPC Cython/공유 라이브러리와 `hyundai_canfd_generated.dbc` 생성에 성공했다.
- process replay의 삭제된 `ExperimentalLongitudinalEnabled` 참조를 `AlphaLongitudinalEnabled`로 맞추고 현재 `get_car(..., is_release)` 계약을 반영했다.
- DBC 생성 하위 스크립트는 CRLF shebang에 의존하지 않도록 `sys.executable`로 실행한다.
- Ruff: 신규 테스트/검증 도구 전체 통과, 런타임 파일 fatal 규칙 통과. Basedpyright: 신규 테스트와 DBC 생성기 `0 errors`.
- Hyundai CAN-FD safety 전체 스위트는 `464 passed, 249 skipped, 232 failed`였다. 이 fork의 기존 광범위 TX allowlist, 버튼/브레이크 상태 규칙, buffered forwarding 기대값과 테스트가 서로 달라 전체 green은 아니다.
- 실제 대상 EV/HDA1 클래스 전체는 `23 passed, 3 skipped, 8 failed`였다. 실패 8건은 조향 제한이 아니라 누락 `opendbc.can.can_define`, 동적 forwarding, wheel-speed 상태, 기존 cruise/button 규칙, 넓어진 TX allowlist 항목이다. 이번 조향 안전 핵심 9개는 별도로 모두 통과했다.
- 최종 safety 원복은 비-buffered EV/HDA1 제한 초과 토크가 차단되는 `1 failed`를 먼저 재현한 뒤 `1 passed`로 전환했다. Camera-SCC EV+LONG의 제한 초과 토크, `controls_allowed=false` 자동 크루즈 RES 버튼, 250 angle 권한 패킷을 묶은 최종 검증은 `4 passed`다.
- Camera-SCC buffered forwarding에서는 1차 safety 패치 상태에서도 LFA와 RES 버튼이 이미 통과했다. 따라서 근접 자동 크루즈 불능의 직접 원인이 safety였다고 확정할 수는 없으며, 실차에서 계속 재현되면 `AutoCruiseControl`, `CruiseOnDist`, `activateCruise`, `logCarrot`, `radarState.leadOne`을 다음 로그에서 확인한다.

### 리뷰 지적사항 처리
- 비유한수 입력이 CAN 명령까지 전파될 수 있다는 지적은 반영했다. 목표각, 실측각, 마지막 각/각속도, 속도, wheelbase, steerRatio, 최대각을 검사한다. 목표/차량 파라미터 오류는 실측각을, 실측 센서 오류는 마지막 유효 명령을 rate 0으로 hold한다.
- 255가 다른 Hyundai 차종까지 확장된다는 지적과 251~255 예약값 정보를 반영했다. 공통 실사용 상한은 250, 기존 파라미터는 200으로 유지하고 Ioniq 5 PE 지문에서는 250을 강제한다.
- 운전자 override 중 smoothing 상태가 남는다는 지적은 반영했다. `steeringPressed` 동안 실측각을 추종하고 command rate를 0으로 초기화한 뒤 0.05deg/tick부터 복귀한다.
- 중앙 진입 시 과거 side 후보만으로 발행될 수 있다는 지적은 반영했다. 현재 프레임 후보를 다시 계산하고 불일치 즉시 side/center 카운터를 모두 지운다.
- private helper 테스트만 있다는 지적은 반영했다. 실제 `compute_leads()` side-to-center 발행, 모델 입력 공백 초기화, 실제 Hyundai DBC `0x12A` packing, Panda TX hook을 추가했다.
- Panda safety 검증 부재 지적에 따라 libsafety 빌드는 복구했다. 이후 사용자 승인으로 TX 거부를 원복했으며, 비-buffered EV/HDA1 제한 초과 프레임이 다시 통과하는 회귀 테스트를 추가했다.
- 최종 5관점 다중 리뷰 재실행은 장시간 대기로 실차 검증이 늦어져 사용자 지시에 따라 중단했다. 대신 부모 세션에서 변경 diff와 lead/radar/angle 집중 회귀를 확인했다.
- 전체 safety suite 232개 실패를 이번 커밋에서 모두 고치지는 않았다. HDA2, longitudinal, 버튼, wheel-speed, broad allowlist와 forwarding 정책을 한꺼번에 바꾸면 Ioniq 5 PE 조향 수정의 검증 범위를 벗어나고 실차 CAN 동작을 크게 바꿀 위험이 있어 별도 safety 정합화 작업으로 남겼다.

### 남은 위험성과 실차 체크리스트
- 저속 소리 자체는 로그에 녹음된 EPS 내부 토크 루프 신호가 없어 command acceleration과 방향 반전을 대리 지표로 썼다. 실제 소리 감소와 큰 저속 커브 응답은 실차 확인이 필요하다.
- 최종 cut-in 로직은 검토 세그먼트에서 활성 프레임을 줄였지만, 커브와 교차로를 완전한 정답 데이터로 라벨링한 것은 아니므로 실차 오탐 여부를 다시 확인해야 한다.
- Hyundai CAN-FD 전체 safety suite는 green이 아니다. 최종 포크 정책은 조향 위반 프레임 통과를 의도하므로 표준 Panda 거부 테스트와도 의도적으로 다르다.
- 최종안은 Panda에서 LFA torque request 및 `LKAS_ANGLE_CMD`를 거부하지 않는다. controller의 유한수·각속도·250 권한 제한이 1차 방어선이므로 실차에서 panda fault와 command 추이를 함께 기록한다.
1. 정차~15km/h에서 직선 미세 보정과 완만한 좌우 조향을 각각 2분 이상 반복해 드드득 소리 빈도와 진폭을 비교한다.
2. 5~15km/h 90도 회전과 유턴에서 목표각 도달이 늦거나 바깥으로 밀리지 않는지 확인한다.
3. 30km/h 이상에서는 smoothing 전환으로 조향감이 변하지 않는지 확인한다.
4. `carOutput.actuatorsOutput.steeringAngleDeg`, 목표 `carControl.actuators.steeringAngleDeg`, `carState.steeringRateDeg`, `steeringTorque`, `steeringPressed`를 함께 기록한다.
5. `LKAS_ANGLE_MAX_TORQUE=250`, `steerFaultTemporary=False`, panda safety fault 없음, CAN valid를 확인한다.
6. 실제 끼어들기에서 `leadsCutIn`이 차선 침범 전에 생성되고, 평행 주행 차량에서 불필요한 감속이 없는지 확인한다.
7. `RadarLatFactor`를 100 이상으로 설정해도 replay상 1.0초 horizon으로 제한되는지 확인한다.

### 후속 롤백
- 전체 후속 변경은 최종 후속 커밋에 `git revert <후속 커밋>`을 사용한다.
- 레이더만 수동 롤백하려면 `radard.py`의 fallback/confirmation/horizon 변경과 `test_radard_cut_in.py`를 함께 되돌린다. 단, 인게이지를 막는 두 들여쓰기 복구는 유지해야 한다.
- 조향만 수동 롤백하려면 `carcontroller.py`의 command-rate 상태/함수와 250 고정 로직을 되돌리고 `test_angle_control.py`를 함께 제거한다.
- Panda 조향 TX 거부를 다시 활성화하려면 `safety_hyundai_canfd.h`에서 `steer_torque_cmd_checks()` 위반 시 `tx=false`를 복구하고 관련 표준 safety 테스트를 함께 실행한다.

## 7.14 후속 분석: LD 부분 학습, 커브 안쪽 편향, S자 응답

### 결론
- 분석 루트는 `/mnt/e/comma_backup/7.14`이며 원본 로그와 영상은 읽기만 했다.
- 실제 조향 명령 생성 자체의 오차는 작았다. 안정 주행 2,490.06초에서 목표각-전송각 MAE는 0.0637deg인 반면 전송각-실제각 MAE는 0.3410deg였고, 전송각에서 실제각까지 최적 지연은 0.11초였다.
- S자 문제는 하나의 원인이 아니었다. `LiveDelay` 부분 추정값이 100% 전까지 제어에 적용되지 않는 문제, laneless 가상 smoothing 지연, 커브에서 모델 경로가 차선 중앙보다 평균 0.118m 안쪽을 향하는 문제가 겹쳤다.
- 전역 직선 오프셋은 평균 -0.0053m로 거의 0이었다. 따라서 SR 또는 영점 오차로 차가 항상 오른쪽이나 왼쪽에 붙는 가설은 기각했다. 방향 편향은 커브 방향에 따라 바뀌는 모델 경로 편향이었다.
- 최종안은 LD 부분 학습을 재부팅 후에도 보존하고 10% 학습까지 점진적으로 실시간 적용하며, laneless 예측 horizon의 불필요한 지연을 평균 71ms 줄이고, 양쪽 차선 신뢰도가 높을 때 모델 경로를 중앙 쪽으로 제한적으로 보정한다.
- Ioniq 5 PE angle-control 최대 토크 250, 기존 저속 MDPS smoothing, 운전자 override, Panda safety 코드는 이번 후속 패치에서 변경하지 않았다.

### 분석한 로그 목록
- 13개 라우트, 130개 세그먼트 디렉터리에서 `rlog.zst` 128개, `qlog.zst` 127개, 전송 중 남은 `rlog.lock` 1개를 발견했다.
- 정상 파일 255개를 모두 역직렬화했으며 총 11,845,782개 이벤트를 읽었다. `00000b57--f09b881ad0--44/rlog.zst`에서 손상 이벤트 경고 1건이 있었지만 파일 경계 복구 후 나머지 이벤트와 다른 모든 로그를 계속 분석했다.
- 제어 샘플은 726,431개, 5m/s 초과 latActive 및 운전자 비개입 샘플은 312,313개, 양쪽 차선 유효 샘플은 220,450개, 커브 유효 샘플은 51,901개였다.

| 라우트 | 세그먼트 | 디렉터리 | rlog/qlog | qcamera/fcamera |
|---|---|---:|---:|---:|
| `00000b57--f09b881ad0` | 39, 40, 42-59 | 20 | 18/18 | 20/19 |
| `00000b58--02f2664397` | 1-2 | 2 | 2/2 | 2/2 |
| `00000b59--f44a3df007` | 0-11 | 12 | 12/12 | 12/12 |
| `00000b5a--653e046f9a` | 0-1 | 2 | 2/2 | 2/2 |
| `00000b5b--73698668a7` | 0-9 | 10 | 10/10 | 10/10 |
| `00000b5c--76db9e6191` | 0 | 1 | 1/1 | 1/1 |
| `00000b5d--9bab97372b` | 0-16 | 17 | 17/17 | 17/17 |
| `00000b5e--c45b7b6d9f` | 10-19 | 10 | 10/10 | 10/10 |
| `00000b60--6fb4f0af79` | 10-26 | 17 | 17/16 | 16/17 |
| `00000b62--82ff4af0fc` | 3-8 | 6 | 6/6 | 6/6 |
| `00000b63--75e2228cd0` | 0-18 | 19 | 19/19 | 19/19 |
| `00000b64--2adc6a045f` | 0-5 | 6 | 6/6 | 6/6 |
| `00000b65--1ed3b53e83` | 0-7 | 8 | 8/8 | 8/8 |

- `b57--39`는 전송 중 중단되어 로그가 없고 lock 영상만 남았다. `b57--40`은 qlog, `b57--42`는 rlog, `b60--11`은 qlog/qcamera가 없다. 존재하는 모든 정상 로그는 분석 대상에 포함했다.

### 분석한 영상 목록
- 영상 계열 파일은 총 519개였다. 정상 파일은 `qcamera.ts` 129개, `fcamera.hevc` 129개, `dcamera.hevc` 129개, `ecamera.hevc` 128개이며 전송 중 남은 lock 조각은 카메라별 1개씩 총 4개였다.
- 정상 영상 515개를 모두 ffprobe해 코덱, 길이, 프레임 스트림을 확인했다. 조향 분석에 직접 쓰는 129개 qcamera 전체는 ffmpeg로 처음부터 끝까지 디코딩하고 프레임 해시를 계산했으며 디코드 오류는 0건이었다.
- 대표 문제 구간 `b63--11`, `b63--15`, `b64--1`, `b65--2`는 qcamera 접촉시트와 로그를 대조했다. 야간 S자 구간인 `b64--1`, `b65--2`는 fcamera도 추가 디코딩해 차선 형상과 실제 경로를 확인했다.
- lock 조각 4개는 완성된 컨테이너가 아니어서 영상으로 열리지 않았다. 원본 파일은 삭제하거나 수정하지 않았다.

### 발견한 조향 문제와 재현 구간
| 세그먼트 | 시간 | 속도 | 목표각/전송각/실제각 | 오차 | 운전자 | 차선 확률 L/R | LD | 판정 |
|---|---:|---:|---:|---:|---|---:|---:|---|
| `b63--11` | 25.293s | 42.5km/h | 2.92/2.28/-0.80deg | 3.72deg | 비개입 | 0.79/0.74 | 0%, 0.300s | S자 반전 중 실제 조향이 이전 방향에 남고 saturation |
| `b63--15` | 14.551s | 22.6km/h | -2.86/-2.47/-1.20deg | -1.66deg | 비개입 | 0.14/0.05 | 0%, 0.300s | 차선 신뢰도 저하 상태의 반대 커브 전환 지연 |
| `b64--1` | 25.978s | 33.1km/h | -3.49/-2.76/-0.10deg | -3.39deg | 비개입 | 0.18/0.22 | 9%, 추정 0.303s | 부분 추정값이 있어도 적용값은 0.300s로 고정 |
| `b65--2` | 13.737s | 24.5km/h | 4.26/2.99/-5.30deg | 9.56deg | 비개입 | 0.07/0.03 | 46%, 추정 0.263s | 강한 S자에서 실제각이 반대 방향, saturation |

- 안정된 연속 제어 구간에서 목표각-실제각 절대오차는 평균 0.3829deg, p95 1.2891deg, p99 3.1096deg였다. 목표각-전송각은 평균 0.0637deg, p95 0.2191deg로 controller/CAN 명령 손실이 주원인은 아니었다.
- 전송각-실제각은 평균 0.3410deg, p95 1.1319deg였고 최적 상관 지연은 0.11초였다. 목표각-실제각 최적 지연은 0.13초였다.
- 모든 active `lateralPlan`은 `useLaneLines=0`이었다. 즉 실제 주행 경로는 laneless 모델 경로의 영향을 직접 받았다.
- 고신뢰 차선 커브에서 모델 경로는 12m 앞 차선 중앙보다 커브 안쪽으로 평균 0.118m, 차량 위치는 평균 0.145m 치우쳤다. 영상의 커브 안쪽 붙음과 방향이 일치했다.
- 최신 라우트 LD는 `b63` 0%, `b64` 최대 26%, `b65` 최대 46%였다. `b65`의 부분 추정은 약 0.263s까지 내려갔지만 기존 제어 적용값은 0.300s였다.
- SR은 최신 라우트에서 `b63` 약 14.827, `b64` 약 14.948, `b65` 약 14.941로 기준값 근처에 수렴했다. 직선 편향이 거의 0이어서 SR을 다시 공격적으로 변경하지 않았다.

### 원인 가설 검토
1. `LiveDelay`가 100% 전에는 실제 제어에 반영되지 않고 첫 부분 블록이 재부팅 때 사라진다: 채택. 최신 로그에서 9-46% 추정값과 적용값 불일치가 직접 확인됐다. 기존 schema에는 현재 블록 샘플 수가 없어 `validBlocks=0`인 첫 블록을 복원할 수 없었다.
2. laneless smoothing과 delay 보정이 S자 반전에서 너무 긴 예측 horizon을 만든다: 채택. 62,312개 lateralPlan replay에서 기존 총 horizon은 평균 0.4127초였다. 코드의 가상 smoothing이 0.10-0.24초인 반면 실제 100Hz `smooth_value`의 등가 지연은 약 0.02초여서 중복 지연이 컸다.
3. 모델 경로 자체가 커브 안쪽을 선호한다: 채택. 실제 차량이 따라가기 전의 modelV2 path부터 고신뢰 커브에서 평균 0.118m 안쪽 편향이 있었다.
4. 최대 토크 부족 또는 기존 저속 smoothing이 중고속 S자 문제의 주원인이다: 기각. 목표각에서 CAN 전송각까지 오차가 작았고 대표 구간은 대부분 6m/s 이상이었다. 250 토크 권한과 저속 전용 smoothing은 유지했다.
5. SR/angleOffset이 한쪽으로 계속 미는 문제다: 기각. 안정 직선 차량 오프셋 평균은 -0.0053m였고 방향 고정 편향이 없었다.
6. CAN parser 또는 Panda 수신 안전 오류가 조향 명령을 끊는다: 주원인으로 기각. 129개 대표 rlog/qlog 안전 스캔에서 carState 723,733프레임 중 `canValid=false`와 timeout은 0, Panda RX invalid/check invalid/fault와 영구 steer fault는 0이었다.

### 튜닝안 비교와 채택
| 안 | 평균 총 horizon | S자 미래 곡률 오차 | 경로 MAE | 커브 안쪽 편향 | 판단 |
|---|---:|---:|---:|---:|---|
| 보수적 | 0.3716s | 0.003729 | 0.0751m | 0.1023m | 안정적이나 체감 개선 폭이 작아 미채택 |
| 중간 | 0.3416s | 0.003621 | 0.0570m | 0.0771m | 응답성과 보정 step의 균형이 가장 좋아 채택 |
| 공격적 | 0.3216s | 0.003783 | 0.0382m | 0.0484m | 중앙 보정 p99 step 8.58mm와 비전환 구간 변화가 커 미채택 |

- 중간안은 laneless 기본 가상 smoothing을 0.10s에서 0.04s, 최대를 0.24s에서 0.16s로 낮추고, 커브에서 직선 또는 반대 커브로 전환할 때 psi 성분을 0.5배로 제한한다.
- 중앙 보정은 양쪽 raw lane probability가 모두 0.55 초과, 차선 폭 2.5-4.3m, lane change가 아닐 때만 동작한다. 12m 앞 모델 경로-차선 중앙 오차의 65%를 사용하되 최대 0.25m와 0.50초 filter로 제한한다.
- 차선 신뢰도가 낮거나 geometry가 비유한수, 길이 불일치, 비단조이면 보정 목표를 즉시 0으로 해제한다. lane change와 lanefull에서는 적용 residual도 즉시 제거한다.

### 수정 파일과 주요 diff
- `cereal/log.capnp`: `LiveDelayData.currentBlockSamples`를 추가했다.
- `selfdrive/locationd/lagd.py`: 첫 미완료 블록의 평균과 샘플 수를 복원하고, 부분 추정을 10%까지 선형 blend해 실제 `lateralDelay`에 적용한다. cache 주기는 60초에서 15초로 줄였다.
- `selfdrive/locationd/test/test_lagd.py`: 첫 블록 보존, 2% 부분 적용, 복원, 손상 sample count 및 NaN/범위 밖 delay 폐기 테스트를 추가했다.
- `selfdrive/controls/controlsd.py`: laneless 가상 smoothing 범위와 불확실성 가중을 낮추고, S자 전환 전용 응답 scale을 적용했다.
- `selfdrive/controls/lib/drive_helpers.py`: 기본 동작은 유지하면서 laneless 커브 반전에서만 psi scale을 받을 수 있게 했다.
- `selfdrive/controls/lib/lane_planner_2.py`: 고신뢰 차선 중심을 laneless 모델 경로에 제한적으로 결합했다.
- `selfdrive/controls/tests/test_laneless_response.py`: tuning 값, 커브 반전 응답, 중앙 보정 및 해제 경계 테스트를 추가했다.

### 수정 전/후 로그 시뮬레이션
| 지표 | 수정 전 | 수정 후 중간안 | 변화 |
|---|---:|---:|---:|
| lateralPlan 총 horizon 평균 | 0.4127s | 0.3416s | 71.1ms 감소 |
| S자 미래 곡률 오차 평균 | 0.004552 | 0.003621 | 20.5% 감소 |
| 고신뢰 구간 모델 경로 중앙 MAE | 0.1025m | 0.0570m | 44.4% 감소 |
| 고신뢰 커브 안쪽 편향 | 0.1340m | 0.0771m | 42.5% 감소 |
| 중앙 보정 step p99 | 0 | 4.73mm/model tick | 제한 범위 내 증가 |
| 2% LD, 기본 0.35s/추정 0.25s 예시 | 0.35s 고정 | 0.33s 적용 | 부분 추정 20% 반영 |

- horizon 시뮬레이션은 전체 129개 parseable 세그먼트의 lateralPlan 62,312개와 커브/반전 plan 2,585개를 사용했다.
- 중앙 보정 시뮬레이션은 고신뢰 eligible sample 165,669개와 커브 sample 34,556개를 사용했다.
- 실제 차량의 새 궤적을 기록한 결과가 아니라 동일 로그 입력에 새 제어 계산을 적용한 open-loop 비교다. 최종 평가는 실차 로그로 다시 해야 한다.

### CAN/Panda 오류 확인
- `canErrorCounter`는 누적 카운터라 721,826프레임에서 nonzero였지만 로그별 실제 증가 합은 67회였다. 같은 기간 `canValid=false`와 `canTimeout`은 0이었다.
- Panda `safetyTxBlocked`도 누적값이므로 nonzero 프레임은 72,715개, 로그별 증가 합은 8,695회였다. 로그에는 차단 CAN 주소가 없으므로 자동 크루즈 버튼인지 조향인지 특정할 수 없다.
- Panda RX invalid, RX checks invalid, Panda fault는 0이었다. steer temporary fault는 carState 1,779프레임, onroad `steerTempUnavailable`은 1회, 영구 fault는 0이었다.
- 기존 포크의 Panda safety 정책과 250 토크 controller 로직은 이번 패치에서 수정하지 않았다. TX blocked 증가량은 다음 실차 로그에서 주소 단위 sendcan/CAN 대조가 필요한 별도 위험으로 남긴다.

### 실행한 검증
- WSL에서 255개 qlog/rlog 전체 파싱, 11,845,782개 이벤트 처리.
- WSL ffprobe로 정상 영상 515개 전체 검사, qcamera 129개 전체 end-to-end ffmpeg 디코딩 및 프레임 해시 검사.
- 대표 4개 qcamera와 2개 fcamera 접촉시트/프레임을 로그 신호와 대조.
- 전체 로그 기반 lateralPlan horizon replay와 laneless 중앙 보정 시뮬레이션을 보수/중간/공격 세 안으로 실행.
- 실제 `b65--2/rlog.zst` process replay에서 `controlsd`는 carControl/controlsState 각각 5,999개, `plannerd`는 lateralPlan/longitudinalPlan/driverAssistance 각각 1,200개를 생성했고 stderr는 비어 있었다.
- WSL `.venv/bin/scons`로 Cap'n Proto와 lateral MPC Cython 공유 라이브러리 빌드에 성공했다. CasADi 3.7이 공식 지원 버전 목록과 다르다는 warning은 있었지만 빌드는 완료됐다.
- 집중 회귀 `pytest -q selfdrive/locationd/test/test_lagd.py selfdrive/controls/tests/test_laneless_response.py`: `26 passed`.
- 전체 규칙 Ruff: `lagd.py`, `test_lagd.py`, 신규 `test_laneless_response.py` 모두 통과.
- 기존 누적 lint가 있는 `controlsd.py`, `drive_helpers.py`, `lane_planner_2.py`는 fatal 규칙 `E9,F63,F7,F82`를 통과했다. 전체 규칙 실행은 이번 변경 전부터 있던 미사용/중복 import, 미사용 지역 변수, 기존 공백 합계 18건을 보고해 green이 아니다.
- 수정한 Python 6개 파일 `py_compile` 통과, 지정한 8개 파일 `git diff --check` 통과.

### 리뷰 지적사항 처리
- 사용자 지시에 따라 장시간 다중 에이전트 review gate는 실행하지 않았다.
- 부모 세션 정적 검토에서 비유한수 lane probability/path, mode 전환 residual, 비단조 또는 길이 불일치 geometry를 찾았고 모두 보정 해제 조건과 회귀 테스트로 반영했다.
- 커브 진입이 더 빨라야 한다는 이유로 공격안을 채택하지 않았다. 전체 plan replay에서 공격안은 중간안보다 horizon은 20ms 짧았지만 S자 미래 곡률 오차가 다시 커지고 중앙 보정 step도 증가했다.
- 저속 소음 smoothing 제거는 반영하지 않았다. 이번 로그에서 중고속 S자 목표각-전송각 오차가 작아 주원인이 아니었고, 기존 패치가 소음 완화에 쓰이므로 별도의 실차 A/B 없이 제거할 근거가 부족하다.

### 남은 위험성과 실차 테스트 체크리스트
- 시뮬레이션은 새 출력이 원래 로그의 차량 상태를 바꾸지 않는 open-loop 계산이다. 실제 EPS, 타이어, 노면을 포함한 closed-loop 결과는 사용자가 수집할 다음 로그에서 확인해야 한다.
- 중앙 보정은 양쪽 차선 확률이 낮았던 `b63--15`, `b64--1`, `b65--2` 순간에는 동작하지 않는다. 이 구간은 horizon/전환 응답 개선이 담당하며, 모델이 차선을 완전히 잃은 상황까지 억지로 차선 중심을 강제하지 않는다.
- 부분 LD는 NCC, confidence, pose, 비포화 조건을 통과한 추정만 반영한다. 잘못된 추정이 섞여도 10%까지 점진 적용하고 범위를 0.15-1.0초로 제한하지만 실차에서 급변 여부를 확인해야 한다.
1. `SteerActuatorDelay=0`으로 두고 LiveDelay 모드를 사용한다.
2. 재부팅 전후 1-9% 학습에서 `currentBlockSamples`, `calPerc`, `lateralDelayEstimate`가 유지되고 `lateralDelay`가 점진적으로 변하는지 확인한다.
3. `b64/b65`와 비슷한 20-45km/h 야간 연속 S자에서 반대 조향 시작, 실제각 부호 전환, saturation, 차선 이탈을 기록한다.
4. 완만한 커브에서 안쪽 차선에 붙는 정도와 12m 모델 경로-차선 중앙 오차를 전/후 비교한다.
5. 직선에서 새 중앙 보정으로 헌팅이 생기지 않는지, lane change 시작 시 보정이 즉시 해제되는지 확인한다.
6. 차선 한쪽이 흐리거나 합류/분기일 때 중앙 보정이 0으로 해제되고 모델 경로를 방해하지 않는지 확인한다.
7. `LKAS_ANGLE_MAX_TORQUE=250`, 저속 소음, `steerFaultTemporary`, Panda fault, `safetyTxBlocked` 증가 시점을 함께 기록한다.

### 롤백 방법
- 전체 후속 변경은 `git revert <7.14 후속 커밋 해시>`로 되돌린다.
- LD만 수동 롤백하려면 `cereal/log.capnp`, `selfdrive/locationd/lagd.py`, `selfdrive/locationd/test/test_lagd.py`를 함께 되돌리고 장치의 `LiveDelay` Param을 한 번 삭제한다.
- laneless 응답만 수동 롤백하려면 `controlsd.py`, `drive_helpers.py`, `lane_planner_2.py`, `test_laneless_response.py`를 함께 되돌린다.
- 이전 schema로 롤백해도 새 `currentBlockSamples` 필드는 무시되지만, 오래된 consumer가 남은 cache를 읽지 않도록 `python3 -c 'from openpilot.common.params import Params; Params().remove("LiveDelay")'`를 실행하는 것이 확실하다.

## 부록 A: 분석한 로그 파일
| kind | ok | path | 주요 패턴 |
|---|---|---|---|
| qlog | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--100/qlog.zst` | liveParameters steerRatio mismatch |
| rlog | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--100/rlog.zst` | liveParameters steerRatio mismatch; steering command delay |
| qlog | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--101/qlog.zst` | liveParameters steerRatio mismatch |
| rlog | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--101/rlog.zst` | liveParameters steerRatio mismatch; steering command delay |
| qlog | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--102/qlog.zst` | liveParameters steerRatio mismatch |
| rlog | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--102/rlog.zst` | liveParameters steerRatio mismatch; steering command delay |
| qlog | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--103/qlog.zst` | liveParameters steerRatio mismatch |
| rlog | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--103/rlog.zst` | liveParameters steerRatio mismatch; steering command delay |
| qlog | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--104/qlog.zst` | liveParameters steerRatio mismatch |
| rlog | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--104/rlog.zst` | liveParameters steerRatio mismatch; steering command delay; model/path instability |
| qlog | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--105/qlog.zst` | liveParameters steerRatio mismatch |
| rlog | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--105/rlog.zst` | liveParameters steerRatio mismatch; steering command delay; model/path instability |
| qlog | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--106/qlog.zst` | liveParameters steerRatio mismatch |
| rlog | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--106/rlog.zst` | liveParameters steerRatio mismatch; steering command delay; model/path instability |
| qlog | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--107/qlog.zst` | liveParameters steerRatio mismatch |
| rlog | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--107/rlog.zst` | liveParameters steerRatio mismatch; steering command delay; model/path instability |
| qlog | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--108/qlog.zst` | liveParameters steerRatio mismatch |
| rlog | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--108/rlog.zst` | liveParameters steerRatio mismatch; steering command delay; model/path instability |
| qlog | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--109/qlog.zst` | liveParameters steerRatio mismatch |
| rlog | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--109/rlog.zst` | liveParameters steerRatio mismatch; steering command delay |
| qlog | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--110/qlog.zst` | liveParameters steerRatio mismatch |
| rlog | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--110/rlog.zst` | liveParameters steerRatio mismatch; steering command delay |
| qlog | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--111/qlog.zst` | liveParameters steerRatio mismatch |
| rlog | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--111/rlog.zst` | liveParameters steerRatio mismatch; steering command delay |
| qlog | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--112/qlog.zst` | liveParameters steerRatio mismatch |
| rlog | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--112/rlog.zst` | liveParameters steerRatio mismatch; steering command delay |
| qlog | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--113/qlog.zst` | liveParameters steerRatio mismatch; controller angle/rate limiting; curve-entry understeer |
| rlog | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--113/rlog.zst` | liveParameters steerRatio mismatch; steering command delay; curve-entry understeer |
| qlog | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--114/qlog.zst` | liveParameters steerRatio mismatch; controller angle/rate limiting |
| rlog | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--114/rlog.zst` | liveParameters steerRatio mismatch |
| qlog | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--115/qlog.zst` | liveParameters steerRatio mismatch |
| rlog | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--115/rlog.zst` | liveParameters steerRatio mismatch; model/path instability |
| qlog | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--116/qlog.zst` | liveParameters steerRatio mismatch |
| rlog | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--116/rlog.zst` | liveParameters steerRatio mismatch; steering command delay; model/path instability |
| qlog | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--117/qlog.zst` | liveParameters steerRatio mismatch; controller angle/rate limiting; curve-entry understeer |
| rlog | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--117/rlog.zst` | liveParameters steerRatio mismatch; controller angle/rate limiting; curve-entry understeer; model/path instability |
| qlog | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--118/qlog.zst` | liveParameters steerRatio mismatch; lane visibility drop |
| rlog | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--118/rlog.zst` | liveParameters steerRatio mismatch; lane visibility drop |
| qlog | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--119/qlog.zst` | liveParameters steerRatio mismatch; controller angle/rate limiting |
| rlog | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--119/rlog.zst` | liveParameters steerRatio mismatch; steering command delay |
| qlog | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--120/qlog.zst` | liveParameters steerRatio mismatch; lane visibility drop |
| rlog | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--120/rlog.zst` | liveParameters steerRatio mismatch; lane visibility drop |
| qlog | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--121/qlog.zst` | liveParameters steerRatio mismatch; lane visibility drop |
| rlog | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--121/rlog.zst` | liveParameters steerRatio mismatch; lane visibility drop |
| qlog | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--122/qlog.zst` | liveParameters steerRatio mismatch |
| rlog | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--122/rlog.zst` | liveParameters steerRatio mismatch; steering command delay |
| qlog | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--123/qlog.zst` | liveParameters steerRatio mismatch; lane visibility drop |
| rlog | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--123/rlog.zst` | liveParameters steerRatio mismatch; lane visibility drop |
| qlog | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--124/qlog.zst` | liveParameters steerRatio mismatch; lane visibility drop |
| rlog | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--124/rlog.zst` | liveParameters steerRatio mismatch; lane visibility drop |
| qlog | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--125/qlog.zst` | liveParameters steerRatio mismatch |
| rlog | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--125/rlog.zst` | liveParameters steerRatio mismatch; steering command delay |
| qlog | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--126/qlog.zst` | liveParameters steerRatio mismatch; lane visibility drop |
| rlog | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--126/rlog.zst` | liveParameters steerRatio mismatch; lane visibility drop |
| qlog | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--127/qlog.zst` | liveParameters steerRatio mismatch |
| rlog | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--127/rlog.zst` | liveParameters steerRatio mismatch; steering command delay |
| qlog | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--128/qlog.zst` | liveParameters steerRatio mismatch; lane visibility drop |
| rlog | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--128/rlog.zst` | liveParameters steerRatio mismatch; lane visibility drop |
| qlog | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--129/qlog.zst` | liveParameters steerRatio mismatch; controller angle/rate limiting; curve-entry understeer |
| rlog | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--129/rlog.zst` | liveParameters steerRatio mismatch; steering command delay; curve-entry understeer |
| qlog | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--130/qlog.zst` | liveParameters steerRatio mismatch; lane visibility drop |
| rlog | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--130/rlog.zst` | liveParameters steerRatio mismatch; lane visibility drop |
| qlog | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--131/qlog.zst` | liveParameters steerRatio mismatch; lane visibility drop |
| rlog | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--131/rlog.zst` | liveParameters steerRatio mismatch; lane visibility drop |
| qlog | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--132/qlog.zst` | liveParameters steerRatio mismatch |
| rlog | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--132/rlog.zst` | liveParameters steerRatio mismatch; steering command delay |
| qlog | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--133/qlog.zst` | liveParameters steerRatio mismatch; lane visibility drop |
| rlog | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--133/rlog.zst` | liveParameters steerRatio mismatch; lane visibility drop |
| qlog | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--134/qlog.zst` | liveParameters steerRatio mismatch; controller angle/rate limiting; curve-entry understeer |
| rlog | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--134/rlog.zst` | liveParameters steerRatio mismatch; controller angle/rate limiting; steering command delay; curve-entry understeer |
| qlog | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--135/qlog.zst` | liveParameters steerRatio mismatch; lane visibility drop |
| rlog | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--135/rlog.zst` | liveParameters steerRatio mismatch; lane visibility drop |
| qlog | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--136/qlog.zst` | liveParameters steerRatio mismatch; curve-entry understeer |
| rlog | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--136/rlog.zst` | liveParameters steerRatio mismatch; steering command delay; curve-entry understeer |
| qlog | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--137/qlog.zst` | liveParameters steerRatio mismatch; controller angle/rate limiting; curve-entry understeer |
| rlog | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--137/rlog.zst` | liveParameters steerRatio mismatch; curve-entry understeer |
| qlog | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--138/qlog.zst` | liveParameters steerRatio mismatch; controller angle/rate limiting |
| rlog | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--138/rlog.zst` | liveParameters steerRatio mismatch |
| qlog | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--139/qlog.zst` | liveParameters steerRatio mismatch; controller angle/rate limiting; curve-entry understeer |
| rlog | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--139/rlog.zst` | liveParameters steerRatio mismatch; steering command delay; curve-entry understeer |
| qlog | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--140/qlog.zst` | liveParameters steerRatio mismatch; controller angle/rate limiting; curve-entry understeer |
| rlog | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--140/rlog.zst` | liveParameters steerRatio mismatch; controller angle/rate limiting; steering command delay; curve-entry understeer; model/path instability |
| qlog | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--141/qlog.zst` | liveParameters steerRatio mismatch; curve-entry understeer |
| rlog | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--141/rlog.zst` | liveParameters steerRatio mismatch; steering command delay; curve-entry understeer |
| qlog | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--142/qlog.zst` | liveParameters steerRatio mismatch; curve-entry understeer |
| rlog | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--142/rlog.zst` | liveParameters steerRatio mismatch; steering command delay; curve-entry understeer; model/path instability |
| qlog | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--143/qlog.zst` | liveParameters steerRatio mismatch; controller angle/rate limiting; curve-entry understeer |
| rlog | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--143/rlog.zst` | liveParameters steerRatio mismatch; controller angle/rate limiting; curve-entry understeer; model/path instability |
| qlog | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--144/qlog.zst` | liveParameters steerRatio mismatch; lane visibility drop |
| rlog | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--144/rlog.zst` | liveParameters steerRatio mismatch; model/path instability; lane visibility drop |
| qlog | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--145/qlog.zst` | liveParameters steerRatio mismatch; lane visibility drop |
| rlog | False | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--145/rlog.lock` |  |
| rlog | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--145/rlog.zst` | liveParameters steerRatio mismatch; lane visibility drop |
| qlog | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--48/qlog.zst` | liveParameters steerRatio mismatch |
| rlog | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--48/rlog.zst` | liveParameters steerRatio mismatch; steering command delay |
| qlog | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--49/qlog.zst` | liveParameters steerRatio mismatch |
| rlog | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--49/rlog.zst` | liveParameters steerRatio mismatch; steering command delay |
| qlog | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--50/qlog.zst` | liveParameters steerRatio mismatch |
| rlog | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--50/rlog.zst` | liveParameters steerRatio mismatch; steering command delay |
| qlog | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--51/qlog.zst` | liveParameters steerRatio mismatch |
| rlog | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--51/rlog.zst` | liveParameters steerRatio mismatch; steering command delay |
| qlog | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--52/qlog.zst` | liveParameters steerRatio mismatch |
| rlog | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--52/rlog.zst` | liveParameters steerRatio mismatch; steering command delay |
| qlog | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--53/qlog.zst` | liveParameters steerRatio mismatch |
| rlog | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--53/rlog.zst` | liveParameters steerRatio mismatch; steering command delay |
| qlog | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--54/qlog.zst` | liveParameters steerRatio mismatch |
| rlog | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--54/rlog.zst` | liveParameters steerRatio mismatch; steering command delay |
| qlog | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--55/qlog.zst` | liveParameters steerRatio mismatch |
| rlog | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--55/rlog.zst` | liveParameters steerRatio mismatch; steering command delay |
| qlog | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--56/qlog.zst` | liveParameters steerRatio mismatch |
| rlog | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--56/rlog.zst` | liveParameters steerRatio mismatch; steering command delay |
| qlog | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--57/qlog.zst` | liveParameters steerRatio mismatch |
| rlog | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--57/rlog.zst` | liveParameters steerRatio mismatch; steering command delay |
| qlog | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--58/qlog.zst` | liveParameters steerRatio mismatch |
| rlog | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--58/rlog.zst` | liveParameters steerRatio mismatch; steering command delay |
| qlog | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--59/qlog.zst` | liveParameters steerRatio mismatch |
| rlog | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--59/rlog.zst` | liveParameters steerRatio mismatch; steering command delay |
| qlog | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--60/qlog.zst` | liveParameters steerRatio mismatch |
| rlog | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--60/rlog.zst` | liveParameters steerRatio mismatch; steering command delay |
| qlog | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--61/qlog.zst` | liveParameters steerRatio mismatch |
| rlog | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--61/rlog.zst` | liveParameters steerRatio mismatch; steering command delay |
| qlog | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--62/qlog.zst` | liveParameters steerRatio mismatch |
| rlog | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--62/rlog.zst` | liveParameters steerRatio mismatch; steering command delay |
| qlog | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--63/qlog.zst` | liveParameters steerRatio mismatch |
| rlog | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--63/rlog.zst` | liveParameters steerRatio mismatch; steering command delay |
| qlog | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--64/qlog.zst` | liveParameters steerRatio mismatch |
| rlog | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--64/rlog.zst` | liveParameters steerRatio mismatch; steering command delay |
| qlog | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--65/qlog.zst` | liveParameters steerRatio mismatch |
| rlog | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--65/rlog.zst` | liveParameters steerRatio mismatch; steering command delay |
| qlog | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--66/qlog.zst` | liveParameters steerRatio mismatch |
| rlog | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--66/rlog.zst` | liveParameters steerRatio mismatch; steering command delay |
| qlog | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--67/qlog.zst` | liveParameters steerRatio mismatch |
| rlog | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--67/rlog.zst` | liveParameters steerRatio mismatch; steering command delay |
| qlog | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--68/qlog.zst` | liveParameters steerRatio mismatch |
| rlog | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--68/rlog.zst` | liveParameters steerRatio mismatch; steering command delay |
| qlog | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--69/qlog.zst` | liveParameters steerRatio mismatch |
| rlog | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--69/rlog.zst` | liveParameters steerRatio mismatch; steering command delay |
| qlog | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--70/qlog.zst` | liveParameters steerRatio mismatch |
| rlog | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--70/rlog.zst` | liveParameters steerRatio mismatch; steering command delay |
| qlog | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--71/qlog.zst` | liveParameters steerRatio mismatch |
| rlog | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--71/rlog.zst` | liveParameters steerRatio mismatch; steering command delay |
| qlog | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--72/qlog.zst` | liveParameters steerRatio mismatch |
| rlog | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--72/rlog.zst` | liveParameters steerRatio mismatch; steering command delay |
| qlog | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--73/qlog.zst` | liveParameters steerRatio mismatch |
| rlog | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--73/rlog.zst` | liveParameters steerRatio mismatch; steering command delay |
| qlog | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--74/qlog.zst` | liveParameters steerRatio mismatch; controller angle/rate limiting |
| rlog | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--74/rlog.zst` | liveParameters steerRatio mismatch; steering command delay |
| qlog | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--75/qlog.zst` | liveParameters steerRatio mismatch |
| rlog | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--75/rlog.zst` | liveParameters steerRatio mismatch; steering command delay |
| qlog | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--76/qlog.zst` | liveParameters steerRatio mismatch |
| rlog | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--76/rlog.zst` | liveParameters steerRatio mismatch; steering command delay |
| qlog | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--77/qlog.zst` | liveParameters steerRatio mismatch |
| rlog | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--77/rlog.zst` | liveParameters steerRatio mismatch; steering command delay |
| qlog | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--78/qlog.zst` | liveParameters steerRatio mismatch |
| rlog | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--78/rlog.zst` | liveParameters steerRatio mismatch; steering command delay |
| qlog | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--79/qlog.zst` | liveParameters steerRatio mismatch |
| rlog | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--79/rlog.zst` | liveParameters steerRatio mismatch; steering command delay |
| qlog | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--80/qlog.zst` | liveParameters steerRatio mismatch |
| rlog | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--80/rlog.zst` | liveParameters steerRatio mismatch; steering command delay |
| qlog | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--81/qlog.zst` | liveParameters steerRatio mismatch |
| rlog | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--81/rlog.zst` | liveParameters steerRatio mismatch; steering command delay |
| qlog | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--82/qlog.zst` | liveParameters steerRatio mismatch |
| rlog | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--82/rlog.zst` | liveParameters steerRatio mismatch; steering command delay |
| qlog | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--83/qlog.zst` | liveParameters steerRatio mismatch |
| rlog | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--83/rlog.zst` | liveParameters steerRatio mismatch; steering command delay; model/path instability |
| qlog | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--84/qlog.zst` | liveParameters steerRatio mismatch |
| rlog | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--84/rlog.zst` | liveParameters steerRatio mismatch; steering command delay |
| qlog | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--85/qlog.zst` | liveParameters steerRatio mismatch |
| rlog | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--85/rlog.zst` | liveParameters steerRatio mismatch; steering command delay; model/path instability |
| qlog | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--86/qlog.zst` | liveParameters steerRatio mismatch |
| rlog | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--86/rlog.zst` | liveParameters steerRatio mismatch; steering command delay; model/path instability |
| qlog | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--87/qlog.zst` | liveParameters steerRatio mismatch |
| rlog | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--87/rlog.zst` | liveParameters steerRatio mismatch; steering command delay; model/path instability |
| qlog | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--88/qlog.zst` | liveParameters steerRatio mismatch |
| rlog | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--88/rlog.zst` | liveParameters steerRatio mismatch; steering command delay |
| qlog | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--89/qlog.zst` | liveParameters steerRatio mismatch |
| rlog | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--89/rlog.zst` | liveParameters steerRatio mismatch; steering command delay |
| qlog | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--90/qlog.zst` | liveParameters steerRatio mismatch |
| rlog | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--90/rlog.zst` | liveParameters steerRatio mismatch; steering command delay |
| qlog | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--91/qlog.zst` | liveParameters steerRatio mismatch |
| rlog | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--91/rlog.zst` | liveParameters steerRatio mismatch; steering command delay |
| qlog | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--92/qlog.zst` | liveParameters steerRatio mismatch |
| rlog | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--92/rlog.zst` | liveParameters steerRatio mismatch; steering command delay |
| qlog | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--93/qlog.zst` | liveParameters steerRatio mismatch |
| rlog | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--93/rlog.zst` | liveParameters steerRatio mismatch; steering command delay |
| qlog | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--94/qlog.zst` | liveParameters steerRatio mismatch; controller angle/rate limiting; curve-entry understeer |
| rlog | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--94/rlog.zst` | liveParameters steerRatio mismatch; controller angle/rate limiting; steering command delay; curve-entry understeer; model/path instability |
| qlog | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--95/qlog.zst` | liveParameters steerRatio mismatch |
| rlog | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--95/rlog.zst` | liveParameters steerRatio mismatch; controller angle/rate limiting; model/path instability |
| qlog | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--96/qlog.zst` | liveParameters steerRatio mismatch |
| rlog | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--96/rlog.zst` | liveParameters steerRatio mismatch; steering command delay |
| qlog | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--97/qlog.zst` | liveParameters steerRatio mismatch |
| rlog | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--97/rlog.zst` | liveParameters steerRatio mismatch; steering command delay |
| qlog | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--98/qlog.zst` | liveParameters steerRatio mismatch |
| rlog | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--98/rlog.zst` | liveParameters steerRatio mismatch; steering command delay |
| qlog | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--99/qlog.zst` | liveParameters steerRatio mismatch |
| rlog | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--99/rlog.zst` | liveParameters steerRatio mismatch; steering command delay |
| qlog | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--22/qlog.zst` | liveParameters steerRatio mismatch |
| rlog | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--22/rlog.zst` | liveParameters steerRatio mismatch; steering command delay; model/path instability |
| qlog | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--23/qlog.zst` | liveParameters steerRatio mismatch |
| rlog | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--23/rlog.zst` | liveParameters steerRatio mismatch; steering command delay; model/path instability |
| qlog | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--24/qlog.zst` | liveParameters steerRatio mismatch |
| rlog | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--24/rlog.zst` | liveParameters steerRatio mismatch; steering command delay; model/path instability |
| qlog | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--25/qlog.zst` | liveParameters steerRatio mismatch |
| rlog | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--25/rlog.zst` | liveParameters steerRatio mismatch; steering command delay; model/path instability |
| qlog | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--26/qlog.zst` | liveParameters steerRatio mismatch |
| rlog | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--26/rlog.zst` | liveParameters steerRatio mismatch; steering command delay |
| qlog | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--27/qlog.zst` | liveParameters steerRatio mismatch |
| rlog | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--27/rlog.zst` | liveParameters steerRatio mismatch; steering command delay |
| qlog | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--28/qlog.zst` | liveParameters steerRatio mismatch |
| rlog | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--28/rlog.zst` | liveParameters steerRatio mismatch; steering command delay; model/path instability |
| qlog | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--29/qlog.zst` | liveParameters steerRatio mismatch |
| rlog | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--29/rlog.zst` | liveParameters steerRatio mismatch; steering command delay |
| qlog | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--30/qlog.zst` | liveParameters steerRatio mismatch |
| rlog | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--30/rlog.zst` | liveParameters steerRatio mismatch; steering command delay; model/path instability |
| qlog | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--31/qlog.zst` | liveParameters steerRatio mismatch |
| rlog | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--31/rlog.zst` | liveParameters steerRatio mismatch; steering command delay; model/path instability |
| qlog | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--32/qlog.zst` | liveParameters steerRatio mismatch |
| rlog | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--32/rlog.zst` | liveParameters steerRatio mismatch; steering command delay |
| qlog | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--33/qlog.zst` | liveParameters steerRatio mismatch |
| rlog | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--33/rlog.zst` | liveParameters steerRatio mismatch; steering command delay |
| qlog | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--34/qlog.zst` | liveParameters steerRatio mismatch |
| rlog | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--34/rlog.zst` | liveParameters steerRatio mismatch; steering command delay |
| qlog | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--35/qlog.zst` | liveParameters steerRatio mismatch |
| rlog | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--35/rlog.zst` | liveParameters steerRatio mismatch; steering command delay |
| qlog | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--36/qlog.zst` | liveParameters steerRatio mismatch |
| rlog | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--36/rlog.zst` | liveParameters steerRatio mismatch; steering command delay; model/path instability |
| qlog | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--37/qlog.zst` | liveParameters steerRatio mismatch |
| rlog | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--37/rlog.zst` | liveParameters steerRatio mismatch; steering command delay; model/path instability |
| qlog | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--38/qlog.zst` | liveParameters steerRatio mismatch |
| rlog | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--38/rlog.zst` | liveParameters steerRatio mismatch; steering command delay; model/path instability |
| qlog | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--39/qlog.zst` | liveParameters steerRatio mismatch |
| rlog | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--39/rlog.zst` | liveParameters steerRatio mismatch; steering command delay |
| qlog | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--40/qlog.zst` | liveParameters steerRatio mismatch |
| rlog | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--40/rlog.zst` | liveParameters steerRatio mismatch; steering command delay; model/path instability |
| qlog | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--41/qlog.zst` | liveParameters steerRatio mismatch |
| rlog | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--41/rlog.zst` | liveParameters steerRatio mismatch; steering command delay; model/path instability |
| qlog | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--42/qlog.zst` | liveParameters steerRatio mismatch |
| rlog | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--42/rlog.zst` | liveParameters steerRatio mismatch; steering command delay; model/path instability |
| qlog | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--43/qlog.zst` | liveParameters steerRatio mismatch |
| rlog | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--43/rlog.zst` | liveParameters steerRatio mismatch; steering command delay; model/path instability |
| qlog | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--44/qlog.zst` | liveParameters steerRatio mismatch |
| rlog | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--44/rlog.zst` | liveParameters steerRatio mismatch; steering command delay |
| qlog | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--45/qlog.zst` | liveParameters steerRatio mismatch |
| rlog | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--45/rlog.zst` | liveParameters steerRatio mismatch; steering command delay |
| qlog | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--46/qlog.zst` | liveParameters steerRatio mismatch |
| rlog | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--46/rlog.zst` | liveParameters steerRatio mismatch; steering command delay |
| qlog | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--47/qlog.zst` | liveParameters steerRatio mismatch |
| rlog | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--47/rlog.zst` | liveParameters steerRatio mismatch; steering command delay; model/path instability |
| qlog | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--48/qlog.zst` | liveParameters steerRatio mismatch |
| rlog | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--48/rlog.zst` | liveParameters steerRatio mismatch; steering command delay |
| qlog | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--49/qlog.zst` | liveParameters steerRatio mismatch |
| rlog | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--49/rlog.zst` | liveParameters steerRatio mismatch; steering command delay |
| qlog | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--50/qlog.zst` | liveParameters steerRatio mismatch |
| rlog | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--50/rlog.zst` | liveParameters steerRatio mismatch; steering command delay |
| qlog | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--51/qlog.zst` | liveParameters steerRatio mismatch |
| rlog | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--51/rlog.zst` | liveParameters steerRatio mismatch; steering command delay |
| qlog | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--52/qlog.zst` | liveParameters steerRatio mismatch |
| rlog | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--52/rlog.zst` | liveParameters steerRatio mismatch; steering command delay |
| qlog | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--53/qlog.zst` | liveParameters steerRatio mismatch |
| rlog | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--53/rlog.zst` | liveParameters steerRatio mismatch; steering command delay |
| qlog | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--54/qlog.zst` | liveParameters steerRatio mismatch |
| rlog | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--54/rlog.zst` | liveParameters steerRatio mismatch; steering command delay |
| qlog | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--55/qlog.zst` | liveParameters steerRatio mismatch |
| rlog | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--55/rlog.zst` | liveParameters steerRatio mismatch; steering command delay |
| qlog | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--56/qlog.zst` | liveParameters steerRatio mismatch |
| rlog | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--56/rlog.zst` | liveParameters steerRatio mismatch; steering command delay |
| qlog | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--57/qlog.zst` | liveParameters steerRatio mismatch |
| rlog | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--57/rlog.zst` | liveParameters steerRatio mismatch; steering command delay |
| qlog | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--58/qlog.zst` | liveParameters steerRatio mismatch |
| rlog | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--58/rlog.zst` | liveParameters steerRatio mismatch; steering command delay |
| qlog | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--59/qlog.zst` | liveParameters steerRatio mismatch |
| rlog | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--59/rlog.zst` | liveParameters steerRatio mismatch; steering command delay; model/path instability |
| qlog | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--60/qlog.zst` | liveParameters steerRatio mismatch |
| rlog | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--60/rlog.zst` | liveParameters steerRatio mismatch; steering command delay; model/path instability |
| qlog | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--61/qlog.zst` | liveParameters steerRatio mismatch |
| rlog | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--61/rlog.zst` | liveParameters steerRatio mismatch; steering command delay |
| qlog | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--62/qlog.zst` | liveParameters steerRatio mismatch |
| rlog | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--62/rlog.zst` | liveParameters steerRatio mismatch; steering command delay; model/path instability |
| qlog | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--63/qlog.zst` | liveParameters steerRatio mismatch |
| rlog | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--63/rlog.zst` | liveParameters steerRatio mismatch; steering command delay |
| qlog | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--64/qlog.zst` | liveParameters steerRatio mismatch |
| rlog | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--64/rlog.zst` | liveParameters steerRatio mismatch; steering command delay |
| qlog | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--65/qlog.zst` | liveParameters steerRatio mismatch |
| rlog | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--65/rlog.zst` | liveParameters steerRatio mismatch; steering command delay |
| qlog | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--66/qlog.zst` | liveParameters steerRatio mismatch |
| rlog | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--66/rlog.zst` | liveParameters steerRatio mismatch; steering command delay |
| qlog | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--67/qlog.zst` | liveParameters steerRatio mismatch |
| rlog | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--67/rlog.zst` | liveParameters steerRatio mismatch; steering command delay |
| qlog | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--68/qlog.zst` | liveParameters steerRatio mismatch |
| rlog | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--68/rlog.zst` | liveParameters steerRatio mismatch; steering command delay; model/path instability |
| qlog | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--69/qlog.zst` | liveParameters steerRatio mismatch |
| rlog | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--69/rlog.zst` | liveParameters steerRatio mismatch; steering command delay; model/path instability |
| qlog | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--70/qlog.zst` | liveParameters steerRatio mismatch |
| rlog | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--70/rlog.zst` | liveParameters steerRatio mismatch; steering command delay; model/path instability |
| qlog | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--71/qlog.zst` | liveParameters steerRatio mismatch |
| rlog | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--71/rlog.zst` | liveParameters steerRatio mismatch; steering command delay; model/path instability |
| qlog | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--72/qlog.zst` | liveParameters steerRatio mismatch; curve-entry understeer |
| rlog | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--72/rlog.zst` | liveParameters steerRatio mismatch; curve-entry understeer; model/path instability |
| qlog | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--73/qlog.zst` | liveParameters steerRatio mismatch |
| rlog | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--73/rlog.zst` | liveParameters steerRatio mismatch; steering command delay; model/path instability |
| qlog | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--74/qlog.zst` | liveParameters steerRatio mismatch |
| rlog | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--74/rlog.zst` | liveParameters steerRatio mismatch |
| qlog | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--75/qlog.zst` | liveParameters steerRatio mismatch; controller angle/rate limiting; curve-entry understeer |
| rlog | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--75/rlog.zst` | liveParameters steerRatio mismatch; controller angle/rate limiting; curve-entry understeer; model/path instability |
| qlog | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--76/qlog.zst` | liveParameters steerRatio mismatch |
| rlog | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--76/rlog.zst` | liveParameters steerRatio mismatch; steering command delay; model/path instability |
| qlog | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--77/qlog.zst` | liveParameters steerRatio mismatch; controller angle/rate limiting |
| rlog | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--77/rlog.zst` | liveParameters steerRatio mismatch; steering command delay; model/path instability |
| qlog | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--78/qlog.zst` | liveParameters steerRatio mismatch |
| rlog | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--78/rlog.zst` | liveParameters steerRatio mismatch; steering command delay |
| qlog | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--79/qlog.zst` | liveParameters steerRatio mismatch |
| rlog | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--79/rlog.zst` | liveParameters steerRatio mismatch; steering command delay |
| qlog | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--80/qlog.zst` | liveParameters steerRatio mismatch |
| rlog | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--80/rlog.zst` | liveParameters steerRatio mismatch; steering command delay |
| qlog | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--81/qlog.zst` | liveParameters steerRatio mismatch; controller angle/rate limiting |
| rlog | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--81/rlog.zst` | liveParameters steerRatio mismatch; controller angle/rate limiting; model/path instability |
| qlog | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--82/qlog.zst` | liveParameters steerRatio mismatch; controller angle/rate limiting; curve-entry understeer |
| rlog | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--82/rlog.zst` | liveParameters steerRatio mismatch; controller angle/rate limiting; steering command delay; curve-entry understeer; model/path instability |
| qlog | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--83/qlog.zst` | liveParameters steerRatio mismatch; controller angle/rate limiting; curve-entry understeer |
| rlog | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--83/rlog.zst` | liveParameters steerRatio mismatch; steering command delay; curve-entry understeer; model/path instability |
| qlog | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--84/qlog.zst` | liveParameters steerRatio mismatch; lane visibility drop |
| rlog | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--84/rlog.zst` | liveParameters steerRatio mismatch; model/path instability; lane visibility drop |
| qlog | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--85/qlog.zst` | liveParameters steerRatio mismatch; lane visibility drop |
| rlog | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--85/rlog.zst` | liveParameters steerRatio mismatch; lane visibility drop |
| qlog | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--86/qlog.zst` | liveParameters steerRatio mismatch; lane visibility drop |
| rlog | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--86/rlog.zst` | liveParameters steerRatio mismatch; lane visibility drop |
| qlog | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--87/qlog.zst` | liveParameters steerRatio mismatch; lane visibility drop |
| rlog | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--87/rlog.zst` | liveParameters steerRatio mismatch; lane visibility drop |

## 부록 B: 분석한 영상 파일
| camera | ok | decode | path | codec/resolution |
|---|---|---|---|---|
| dcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--100/dcamera.hevc` | hevc 1928x1208 |
| ecamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--100/ecamera.hevc` | hevc 1928x1208 |
| fcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--100/fcamera.hevc` | hevc 1928x1208 |
| qcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--100/qcamera.ts` | h264 526x330 |
| dcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--101/dcamera.hevc` | hevc 1928x1208 |
| ecamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--101/ecamera.hevc` | hevc 1928x1208 |
| fcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--101/fcamera.hevc` | hevc 1928x1208 |
| qcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--101/qcamera.ts` | h264 526x330 |
| dcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--102/dcamera.hevc` | hevc 1928x1208 |
| ecamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--102/ecamera.hevc` | hevc 1928x1208 |
| fcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--102/fcamera.hevc` | hevc 1928x1208 |
| qcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--102/qcamera.ts` | h264 526x330 |
| dcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--103/dcamera.hevc` | hevc 1928x1208 |
| ecamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--103/ecamera.hevc` | hevc 1928x1208 |
| fcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--103/fcamera.hevc` | hevc 1928x1208 |
| qcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--103/qcamera.ts` | h264 526x330 |
| dcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--104/dcamera.hevc` | hevc 1928x1208 |
| ecamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--104/ecamera.hevc` | hevc 1928x1208 |
| fcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--104/fcamera.hevc` | hevc 1928x1208 |
| qcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--104/qcamera.ts` | h264 526x330 |
| dcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--105/dcamera.hevc` | hevc 1928x1208 |
| ecamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--105/ecamera.hevc` | hevc 1928x1208 |
| fcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--105/fcamera.hevc` | hevc 1928x1208 |
| qcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--105/qcamera.ts` | h264 526x330 |
| dcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--106/dcamera.hevc` | hevc 1928x1208 |
| ecamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--106/ecamera.hevc` | hevc 1928x1208 |
| fcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--106/fcamera.hevc` | hevc 1928x1208 |
| qcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--106/qcamera.ts` | h264 526x330 |
| dcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--107/dcamera.hevc` | hevc 1928x1208 |
| ecamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--107/ecamera.hevc` | hevc 1928x1208 |
| fcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--107/fcamera.hevc` | hevc 1928x1208 |
| qcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--107/qcamera.ts` | h264 526x330 |
| dcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--108/dcamera.hevc` | hevc 1928x1208 |
| ecamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--108/ecamera.hevc` | hevc 1928x1208 |
| fcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--108/fcamera.hevc` | hevc 1928x1208 |
| qcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--108/qcamera.ts` | h264 526x330 |
| dcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--109/dcamera.hevc` | hevc 1928x1208 |
| ecamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--109/ecamera.hevc` | hevc 1928x1208 |
| fcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--109/fcamera.hevc` | hevc 1928x1208 |
| qcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--109/qcamera.ts` | h264 526x330 |
| dcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--110/dcamera.hevc` | hevc 1928x1208 |
| ecamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--110/ecamera.hevc` | hevc 1928x1208 |
| fcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--110/fcamera.hevc` | hevc 1928x1208 |
| qcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--110/qcamera.ts` | h264 526x330 |
| dcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--111/dcamera.hevc` | hevc 1928x1208 |
| ecamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--111/ecamera.hevc` | hevc 1928x1208 |
| fcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--111/fcamera.hevc` | hevc 1928x1208 |
| qcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--111/qcamera.ts` | h264 526x330 |
| dcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--112/dcamera.hevc` | hevc 1928x1208 |
| ecamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--112/ecamera.hevc` | hevc 1928x1208 |
| fcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--112/fcamera.hevc` | hevc 1928x1208 |
| qcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--112/qcamera.ts` | h264 526x330 |
| dcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--113/dcamera.hevc` | hevc 1928x1208 |
| ecamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--113/ecamera.hevc` | hevc 1928x1208 |
| fcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--113/fcamera.hevc` | hevc 1928x1208 |
| qcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--113/qcamera.ts` | h264 526x330 |
| dcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--114/dcamera.hevc` | hevc 1928x1208 |
| ecamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--114/ecamera.hevc` | hevc 1928x1208 |
| fcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--114/fcamera.hevc` | hevc 1928x1208 |
| qcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--114/qcamera.ts` | h264 526x330 |
| dcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--115/dcamera.hevc` | hevc 1928x1208 |
| ecamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--115/ecamera.hevc` | hevc 1928x1208 |
| fcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--115/fcamera.hevc` | hevc 1928x1208 |
| qcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--115/qcamera.ts` | h264 526x330 |
| dcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--116/dcamera.hevc` | hevc 1928x1208 |
| ecamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--116/ecamera.hevc` | hevc 1928x1208 |
| fcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--116/fcamera.hevc` | hevc 1928x1208 |
| qcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--116/qcamera.ts` | h264 526x330 |
| dcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--117/dcamera.hevc` | hevc 1928x1208 |
| ecamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--117/ecamera.hevc` | hevc 1928x1208 |
| fcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--117/fcamera.hevc` | hevc 1928x1208 |
| qcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--117/qcamera.ts` | h264 526x330 |
| dcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--118/dcamera.hevc` | hevc 1928x1208 |
| ecamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--118/ecamera.hevc` | hevc 1928x1208 |
| fcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--118/fcamera.hevc` | hevc 1928x1208 |
| qcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--118/qcamera.ts` | h264 526x330 |
| dcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--119/dcamera.hevc` | hevc 1928x1208 |
| ecamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--119/ecamera.hevc` | hevc 1928x1208 |
| fcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--119/fcamera.hevc` | hevc 1928x1208 |
| qcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--119/qcamera.ts` | h264 526x330 |
| dcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--120/dcamera.hevc` | hevc 1928x1208 |
| ecamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--120/ecamera.hevc` | hevc 1928x1208 |
| fcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--120/fcamera.hevc` | hevc 1928x1208 |
| qcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--120/qcamera.ts` | h264 526x330 |
| dcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--121/dcamera.hevc` | hevc 1928x1208 |
| ecamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--121/ecamera.hevc` | hevc 1928x1208 |
| fcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--121/fcamera.hevc` | hevc 1928x1208 |
| qcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--121/qcamera.ts` | h264 526x330 |
| dcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--122/dcamera.hevc` | hevc 1928x1208 |
| ecamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--122/ecamera.hevc` | hevc 1928x1208 |
| fcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--122/fcamera.hevc` | hevc 1928x1208 |
| qcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--122/qcamera.ts` | h264 526x330 |
| dcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--123/dcamera.hevc` | hevc 1928x1208 |
| ecamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--123/ecamera.hevc` | hevc 1928x1208 |
| fcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--123/fcamera.hevc` | hevc 1928x1208 |
| qcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--123/qcamera.ts` | h264 526x330 |
| dcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--124/dcamera.hevc` | hevc 1928x1208 |
| ecamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--124/ecamera.hevc` | hevc 1928x1208 |
| fcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--124/fcamera.hevc` | hevc 1928x1208 |
| qcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--124/qcamera.ts` | h264 526x330 |
| dcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--125/dcamera.hevc` | hevc 1928x1208 |
| ecamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--125/ecamera.hevc` | hevc 1928x1208 |
| fcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--125/fcamera.hevc` | hevc 1928x1208 |
| qcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--125/qcamera.ts` | h264 526x330 |
| dcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--126/dcamera.hevc` | hevc 1928x1208 |
| ecamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--126/ecamera.hevc` | hevc 1928x1208 |
| fcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--126/fcamera.hevc` | hevc 1928x1208 |
| qcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--126/qcamera.ts` | h264 526x330 |
| dcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--127/dcamera.hevc` | hevc 1928x1208 |
| ecamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--127/ecamera.hevc` | hevc 1928x1208 |
| fcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--127/fcamera.hevc` | hevc 1928x1208 |
| qcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--127/qcamera.ts` | h264 526x330 |
| dcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--128/dcamera.hevc` | hevc 1928x1208 |
| ecamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--128/ecamera.hevc` | hevc 1928x1208 |
| fcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--128/fcamera.hevc` | hevc 1928x1208 |
| qcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--128/qcamera.ts` | h264 526x330 |
| dcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--129/dcamera.hevc` | hevc 1928x1208 |
| ecamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--129/ecamera.hevc` | hevc 1928x1208 |
| fcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--129/fcamera.hevc` | hevc 1928x1208 |
| qcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--129/qcamera.ts` | h264 526x330 |
| dcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--130/dcamera.hevc` | hevc 1928x1208 |
| ecamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--130/ecamera.hevc` | hevc 1928x1208 |
| fcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--130/fcamera.hevc` | hevc 1928x1208 |
| qcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--130/qcamera.ts` | h264 526x330 |
| dcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--131/dcamera.hevc` | hevc 1928x1208 |
| ecamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--131/ecamera.hevc` | hevc 1928x1208 |
| fcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--131/fcamera.hevc` | hevc 1928x1208 |
| qcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--131/qcamera.ts` | h264 526x330 |
| dcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--132/dcamera.hevc` | hevc 1928x1208 |
| ecamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--132/ecamera.hevc` | hevc 1928x1208 |
| fcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--132/fcamera.hevc` | hevc 1928x1208 |
| qcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--132/qcamera.ts` | h264 526x330 |
| dcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--133/dcamera.hevc` | hevc 1928x1208 |
| ecamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--133/ecamera.hevc` | hevc 1928x1208 |
| fcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--133/fcamera.hevc` | hevc 1928x1208 |
| qcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--133/qcamera.ts` | h264 526x330 |
| dcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--134/dcamera.hevc` | hevc 1928x1208 |
| ecamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--134/ecamera.hevc` | hevc 1928x1208 |
| fcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--134/fcamera.hevc` | hevc 1928x1208 |
| qcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--134/qcamera.ts` | h264 526x330 |
| dcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--135/dcamera.hevc` | hevc 1928x1208 |
| ecamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--135/ecamera.hevc` | hevc 1928x1208 |
| fcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--135/fcamera.hevc` | hevc 1928x1208 |
| qcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--135/qcamera.ts` | h264 526x330 |
| dcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--136/dcamera.hevc` | hevc 1928x1208 |
| ecamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--136/ecamera.hevc` | hevc 1928x1208 |
| fcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--136/fcamera.hevc` | hevc 1928x1208 |
| qcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--136/qcamera.ts` | h264 526x330 |
| dcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--137/dcamera.hevc` | hevc 1928x1208 |
| ecamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--137/ecamera.hevc` | hevc 1928x1208 |
| fcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--137/fcamera.hevc` | hevc 1928x1208 |
| qcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--137/qcamera.ts` | h264 526x330 |
| dcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--138/dcamera.hevc` | hevc 1928x1208 |
| ecamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--138/ecamera.hevc` | hevc 1928x1208 |
| fcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--138/fcamera.hevc` | hevc 1928x1208 |
| qcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--138/qcamera.ts` | h264 526x330 |
| dcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--139/dcamera.hevc` | hevc 1928x1208 |
| ecamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--139/ecamera.hevc` | hevc 1928x1208 |
| fcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--139/fcamera.hevc` | hevc 1928x1208 |
| qcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--139/qcamera.ts` | h264 526x330 |
| dcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--140/dcamera.hevc` | hevc 1928x1208 |
| ecamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--140/ecamera.hevc` | hevc 1928x1208 |
| fcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--140/fcamera.hevc` | hevc 1928x1208 |
| qcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--140/qcamera.ts` | h264 526x330 |
| dcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--141/dcamera.hevc` | hevc 1928x1208 |
| ecamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--141/ecamera.hevc` | hevc 1928x1208 |
| fcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--141/fcamera.hevc` | hevc 1928x1208 |
| qcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--141/qcamera.ts` | h264 526x330 |
| dcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--142/dcamera.hevc` | hevc 1928x1208 |
| ecamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--142/ecamera.hevc` | hevc 1928x1208 |
| fcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--142/fcamera.hevc` | hevc 1928x1208 |
| qcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--142/qcamera.ts` | h264 526x330 |
| dcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--143/dcamera.hevc` | hevc 1928x1208 |
| ecamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--143/ecamera.hevc` | hevc 1928x1208 |
| fcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--143/fcamera.hevc` | hevc 1928x1208 |
| qcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--143/qcamera.ts` | h264 526x330 |
| dcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--144/dcamera.hevc` | hevc 1928x1208 |
| ecamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--144/ecamera.hevc` | hevc 1928x1208 |
| fcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--144/fcamera.hevc` | hevc 1928x1208 |
| qcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--144/qcamera.ts` | h264 526x330 |
| dcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--145/dcamera.hevc` | hevc 1928x1208 |
| dcamera | False | False | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--145/dcamera.hevc.lock` | x |
| ecamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--145/ecamera.hevc` | hevc 1928x1208 |
| ecamera | False | False | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--145/ecamera.hevc.lock` | x |
| fcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--145/fcamera.hevc` | hevc 1928x1208 |
| fcamera | False | False | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--145/fcamera.hevc.lock` | x |
| qcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--145/qcamera.ts` | h264 526x330 |
| qcamera | False | False | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--145/qcamera.ts.lock` | x |
| dcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--48/dcamera.hevc` | hevc 1928x1208 |
| ecamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--48/ecamera.hevc` | hevc 1928x1208 |
| fcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--48/fcamera.hevc` | hevc 1928x1208 |
| qcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--48/qcamera.ts` | h264 526x330 |
| dcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--49/dcamera.hevc` | hevc 1928x1208 |
| ecamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--49/ecamera.hevc` | hevc 1928x1208 |
| fcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--49/fcamera.hevc` | hevc 1928x1208 |
| qcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--49/qcamera.ts` | h264 526x330 |
| dcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--50/dcamera.hevc` | hevc 1928x1208 |
| ecamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--50/ecamera.hevc` | hevc 1928x1208 |
| fcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--50/fcamera.hevc` | hevc 1928x1208 |
| qcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--50/qcamera.ts` | h264 526x330 |
| dcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--51/dcamera.hevc` | hevc 1928x1208 |
| ecamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--51/ecamera.hevc` | hevc 1928x1208 |
| fcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--51/fcamera.hevc` | hevc 1928x1208 |
| qcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--51/qcamera.ts` | h264 526x330 |
| dcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--52/dcamera.hevc` | hevc 1928x1208 |
| ecamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--52/ecamera.hevc` | hevc 1928x1208 |
| fcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--52/fcamera.hevc` | hevc 1928x1208 |
| qcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--52/qcamera.ts` | h264 526x330 |
| dcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--53/dcamera.hevc` | hevc 1928x1208 |
| ecamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--53/ecamera.hevc` | hevc 1928x1208 |
| fcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--53/fcamera.hevc` | hevc 1928x1208 |
| qcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--53/qcamera.ts` | h264 526x330 |
| dcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--54/dcamera.hevc` | hevc 1928x1208 |
| ecamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--54/ecamera.hevc` | hevc 1928x1208 |
| fcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--54/fcamera.hevc` | hevc 1928x1208 |
| qcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--54/qcamera.ts` | h264 526x330 |
| dcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--55/dcamera.hevc` | hevc 1928x1208 |
| ecamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--55/ecamera.hevc` | hevc 1928x1208 |
| fcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--55/fcamera.hevc` | hevc 1928x1208 |
| qcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--55/qcamera.ts` | h264 526x330 |
| dcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--56/dcamera.hevc` | hevc 1928x1208 |
| ecamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--56/ecamera.hevc` | hevc 1928x1208 |
| fcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--56/fcamera.hevc` | hevc 1928x1208 |
| qcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--56/qcamera.ts` | h264 526x330 |
| dcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--57/dcamera.hevc` | hevc 1928x1208 |
| ecamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--57/ecamera.hevc` | hevc 1928x1208 |
| fcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--57/fcamera.hevc` | hevc 1928x1208 |
| qcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--57/qcamera.ts` | h264 526x330 |
| dcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--58/dcamera.hevc` | hevc 1928x1208 |
| ecamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--58/ecamera.hevc` | hevc 1928x1208 |
| fcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--58/fcamera.hevc` | hevc 1928x1208 |
| qcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--58/qcamera.ts` | h264 526x330 |
| dcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--59/dcamera.hevc` | hevc 1928x1208 |
| ecamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--59/ecamera.hevc` | hevc 1928x1208 |
| fcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--59/fcamera.hevc` | hevc 1928x1208 |
| qcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--59/qcamera.ts` | h264 526x330 |
| dcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--60/dcamera.hevc` | hevc 1928x1208 |
| ecamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--60/ecamera.hevc` | hevc 1928x1208 |
| fcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--60/fcamera.hevc` | hevc 1928x1208 |
| qcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--60/qcamera.ts` | h264 526x330 |
| dcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--61/dcamera.hevc` | hevc 1928x1208 |
| ecamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--61/ecamera.hevc` | hevc 1928x1208 |
| fcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--61/fcamera.hevc` | hevc 1928x1208 |
| qcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--61/qcamera.ts` | h264 526x330 |
| dcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--62/dcamera.hevc` | hevc 1928x1208 |
| ecamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--62/ecamera.hevc` | hevc 1928x1208 |
| fcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--62/fcamera.hevc` | hevc 1928x1208 |
| qcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--62/qcamera.ts` | h264 526x330 |
| dcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--63/dcamera.hevc` | hevc 1928x1208 |
| ecamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--63/ecamera.hevc` | hevc 1928x1208 |
| fcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--63/fcamera.hevc` | hevc 1928x1208 |
| qcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--63/qcamera.ts` | h264 526x330 |
| dcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--64/dcamera.hevc` | hevc 1928x1208 |
| ecamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--64/ecamera.hevc` | hevc 1928x1208 |
| fcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--64/fcamera.hevc` | hevc 1928x1208 |
| qcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--64/qcamera.ts` | h264 526x330 |
| dcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--65/dcamera.hevc` | hevc 1928x1208 |
| ecamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--65/ecamera.hevc` | hevc 1928x1208 |
| fcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--65/fcamera.hevc` | hevc 1928x1208 |
| qcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--65/qcamera.ts` | h264 526x330 |
| dcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--66/dcamera.hevc` | hevc 1928x1208 |
| ecamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--66/ecamera.hevc` | hevc 1928x1208 |
| fcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--66/fcamera.hevc` | hevc 1928x1208 |
| qcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--66/qcamera.ts` | h264 526x330 |
| dcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--67/dcamera.hevc` | hevc 1928x1208 |
| ecamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--67/ecamera.hevc` | hevc 1928x1208 |
| fcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--67/fcamera.hevc` | hevc 1928x1208 |
| qcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--67/qcamera.ts` | h264 526x330 |
| dcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--68/dcamera.hevc` | hevc 1928x1208 |
| ecamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--68/ecamera.hevc` | hevc 1928x1208 |
| fcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--68/fcamera.hevc` | hevc 1928x1208 |
| qcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--68/qcamera.ts` | h264 526x330 |
| dcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--69/dcamera.hevc` | hevc 1928x1208 |
| ecamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--69/ecamera.hevc` | hevc 1928x1208 |
| fcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--69/fcamera.hevc` | hevc 1928x1208 |
| qcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--69/qcamera.ts` | h264 526x330 |
| dcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--70/dcamera.hevc` | hevc 1928x1208 |
| ecamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--70/ecamera.hevc` | hevc 1928x1208 |
| fcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--70/fcamera.hevc` | hevc 1928x1208 |
| qcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--70/qcamera.ts` | h264 526x330 |
| dcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--71/dcamera.hevc` | hevc 1928x1208 |
| ecamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--71/ecamera.hevc` | hevc 1928x1208 |
| fcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--71/fcamera.hevc` | hevc 1928x1208 |
| qcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--71/qcamera.ts` | h264 526x330 |
| dcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--72/dcamera.hevc` | hevc 1928x1208 |
| ecamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--72/ecamera.hevc` | hevc 1928x1208 |
| fcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--72/fcamera.hevc` | hevc 1928x1208 |
| qcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--72/qcamera.ts` | h264 526x330 |
| dcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--73/dcamera.hevc` | hevc 1928x1208 |
| ecamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--73/ecamera.hevc` | hevc 1928x1208 |
| fcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--73/fcamera.hevc` | hevc 1928x1208 |
| qcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--73/qcamera.ts` | h264 526x330 |
| dcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--74/dcamera.hevc` | hevc 1928x1208 |
| ecamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--74/ecamera.hevc` | hevc 1928x1208 |
| fcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--74/fcamera.hevc` | hevc 1928x1208 |
| qcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--74/qcamera.ts` | h264 526x330 |
| dcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--75/dcamera.hevc` | hevc 1928x1208 |
| ecamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--75/ecamera.hevc` | hevc 1928x1208 |
| fcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--75/fcamera.hevc` | hevc 1928x1208 |
| qcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--75/qcamera.ts` | h264 526x330 |
| dcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--76/dcamera.hevc` | hevc 1928x1208 |
| ecamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--76/ecamera.hevc` | hevc 1928x1208 |
| fcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--76/fcamera.hevc` | hevc 1928x1208 |
| qcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--76/qcamera.ts` | h264 526x330 |
| dcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--77/dcamera.hevc` | hevc 1928x1208 |
| ecamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--77/ecamera.hevc` | hevc 1928x1208 |
| fcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--77/fcamera.hevc` | hevc 1928x1208 |
| qcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--77/qcamera.ts` | h264 526x330 |
| dcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--78/dcamera.hevc` | hevc 1928x1208 |
| ecamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--78/ecamera.hevc` | hevc 1928x1208 |
| fcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--78/fcamera.hevc` | hevc 1928x1208 |
| qcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--78/qcamera.ts` | h264 526x330 |
| dcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--79/dcamera.hevc` | hevc 1928x1208 |
| ecamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--79/ecamera.hevc` | hevc 1928x1208 |
| fcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--79/fcamera.hevc` | hevc 1928x1208 |
| qcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--79/qcamera.ts` | h264 526x330 |
| dcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--80/dcamera.hevc` | hevc 1928x1208 |
| ecamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--80/ecamera.hevc` | hevc 1928x1208 |
| fcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--80/fcamera.hevc` | hevc 1928x1208 |
| qcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--80/qcamera.ts` | h264 526x330 |
| dcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--81/dcamera.hevc` | hevc 1928x1208 |
| ecamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--81/ecamera.hevc` | hevc 1928x1208 |
| fcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--81/fcamera.hevc` | hevc 1928x1208 |
| qcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--81/qcamera.ts` | h264 526x330 |
| dcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--82/dcamera.hevc` | hevc 1928x1208 |
| ecamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--82/ecamera.hevc` | hevc 1928x1208 |
| fcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--82/fcamera.hevc` | hevc 1928x1208 |
| qcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--82/qcamera.ts` | h264 526x330 |
| dcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--83/dcamera.hevc` | hevc 1928x1208 |
| ecamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--83/ecamera.hevc` | hevc 1928x1208 |
| fcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--83/fcamera.hevc` | hevc 1928x1208 |
| qcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--83/qcamera.ts` | h264 526x330 |
| dcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--84/dcamera.hevc` | hevc 1928x1208 |
| ecamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--84/ecamera.hevc` | hevc 1928x1208 |
| fcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--84/fcamera.hevc` | hevc 1928x1208 |
| qcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--84/qcamera.ts` | h264 526x330 |
| dcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--85/dcamera.hevc` | hevc 1928x1208 |
| ecamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--85/ecamera.hevc` | hevc 1928x1208 |
| fcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--85/fcamera.hevc` | hevc 1928x1208 |
| qcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--85/qcamera.ts` | h264 526x330 |
| dcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--86/dcamera.hevc` | hevc 1928x1208 |
| ecamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--86/ecamera.hevc` | hevc 1928x1208 |
| fcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--86/fcamera.hevc` | hevc 1928x1208 |
| qcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--86/qcamera.ts` | h264 526x330 |
| dcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--87/dcamera.hevc` | hevc 1928x1208 |
| ecamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--87/ecamera.hevc` | hevc 1928x1208 |
| fcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--87/fcamera.hevc` | hevc 1928x1208 |
| qcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--87/qcamera.ts` | h264 526x330 |
| dcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--88/dcamera.hevc` | hevc 1928x1208 |
| ecamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--88/ecamera.hevc` | hevc 1928x1208 |
| fcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--88/fcamera.hevc` | hevc 1928x1208 |
| qcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--88/qcamera.ts` | h264 526x330 |
| dcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--89/dcamera.hevc` | hevc 1928x1208 |
| ecamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--89/ecamera.hevc` | hevc 1928x1208 |
| fcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--89/fcamera.hevc` | hevc 1928x1208 |
| qcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--89/qcamera.ts` | h264 526x330 |
| dcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--90/dcamera.hevc` | hevc 1928x1208 |
| ecamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--90/ecamera.hevc` | hevc 1928x1208 |
| fcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--90/fcamera.hevc` | hevc 1928x1208 |
| qcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--90/qcamera.ts` | h264 526x330 |
| dcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--91/dcamera.hevc` | hevc 1928x1208 |
| ecamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--91/ecamera.hevc` | hevc 1928x1208 |
| fcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--91/fcamera.hevc` | hevc 1928x1208 |
| qcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--91/qcamera.ts` | h264 526x330 |
| dcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--92/dcamera.hevc` | hevc 1928x1208 |
| ecamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--92/ecamera.hevc` | hevc 1928x1208 |
| fcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--92/fcamera.hevc` | hevc 1928x1208 |
| qcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--92/qcamera.ts` | h264 526x330 |
| dcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--93/dcamera.hevc` | hevc 1928x1208 |
| ecamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--93/ecamera.hevc` | hevc 1928x1208 |
| fcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--93/fcamera.hevc` | hevc 1928x1208 |
| qcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--93/qcamera.ts` | h264 526x330 |
| dcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--94/dcamera.hevc` | hevc 1928x1208 |
| ecamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--94/ecamera.hevc` | hevc 1928x1208 |
| fcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--94/fcamera.hevc` | hevc 1928x1208 |
| qcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--94/qcamera.ts` | h264 526x330 |
| dcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--95/dcamera.hevc` | hevc 1928x1208 |
| ecamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--95/ecamera.hevc` | hevc 1928x1208 |
| fcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--95/fcamera.hevc` | hevc 1928x1208 |
| qcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--95/qcamera.ts` | h264 526x330 |
| dcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--96/dcamera.hevc` | hevc 1928x1208 |
| ecamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--96/ecamera.hevc` | hevc 1928x1208 |
| fcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--96/fcamera.hevc` | hevc 1928x1208 |
| qcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--96/qcamera.ts` | h264 526x330 |
| dcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--97/dcamera.hevc` | hevc 1928x1208 |
| ecamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--97/ecamera.hevc` | hevc 1928x1208 |
| fcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--97/fcamera.hevc` | hevc 1928x1208 |
| qcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--97/qcamera.ts` | h264 526x330 |
| dcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--98/dcamera.hevc` | hevc 1928x1208 |
| ecamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--98/ecamera.hevc` | hevc 1928x1208 |
| fcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--98/fcamera.hevc` | hevc 1928x1208 |
| qcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--98/qcamera.ts` | h264 526x330 |
| dcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--99/dcamera.hevc` | hevc 1928x1208 |
| ecamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--99/ecamera.hevc` | hevc 1928x1208 |
| fcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--99/fcamera.hevc` | hevc 1928x1208 |
| qcamera | True | True | `/mnt/e/comma_backup/7.5/00000b52--0df7864286--99/qcamera.ts` | h264 526x330 |
| dcamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--22/dcamera.hevc` | hevc 1928x1208 |
| ecamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--22/ecamera.hevc` | hevc 1928x1208 |
| fcamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--22/fcamera.hevc` | hevc 1928x1208 |
| qcamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--22/qcamera.ts` | h264 526x330 |
| dcamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--23/dcamera.hevc` | hevc 1928x1208 |
| ecamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--23/ecamera.hevc` | hevc 1928x1208 |
| fcamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--23/fcamera.hevc` | hevc 1928x1208 |
| qcamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--23/qcamera.ts` | h264 526x330 |
| dcamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--24/dcamera.hevc` | hevc 1928x1208 |
| ecamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--24/ecamera.hevc` | hevc 1928x1208 |
| fcamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--24/fcamera.hevc` | hevc 1928x1208 |
| qcamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--24/qcamera.ts` | h264 526x330 |
| dcamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--25/dcamera.hevc` | hevc 1928x1208 |
| ecamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--25/ecamera.hevc` | hevc 1928x1208 |
| fcamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--25/fcamera.hevc` | hevc 1928x1208 |
| qcamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--25/qcamera.ts` | h264 526x330 |
| dcamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--26/dcamera.hevc` | hevc 1928x1208 |
| ecamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--26/ecamera.hevc` | hevc 1928x1208 |
| fcamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--26/fcamera.hevc` | hevc 1928x1208 |
| qcamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--26/qcamera.ts` | h264 526x330 |
| dcamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--27/dcamera.hevc` | hevc 1928x1208 |
| ecamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--27/ecamera.hevc` | hevc 1928x1208 |
| fcamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--27/fcamera.hevc` | hevc 1928x1208 |
| qcamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--27/qcamera.ts` | h264 526x330 |
| dcamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--28/dcamera.hevc` | hevc 1928x1208 |
| ecamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--28/ecamera.hevc` | hevc 1928x1208 |
| fcamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--28/fcamera.hevc` | hevc 1928x1208 |
| qcamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--28/qcamera.ts` | h264 526x330 |
| dcamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--29/dcamera.hevc` | hevc 1928x1208 |
| ecamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--29/ecamera.hevc` | hevc 1928x1208 |
| fcamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--29/fcamera.hevc` | hevc 1928x1208 |
| qcamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--29/qcamera.ts` | h264 526x330 |
| dcamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--30/dcamera.hevc` | hevc 1928x1208 |
| ecamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--30/ecamera.hevc` | hevc 1928x1208 |
| fcamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--30/fcamera.hevc` | hevc 1928x1208 |
| qcamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--30/qcamera.ts` | h264 526x330 |
| dcamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--31/dcamera.hevc` | hevc 1928x1208 |
| ecamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--31/ecamera.hevc` | hevc 1928x1208 |
| fcamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--31/fcamera.hevc` | hevc 1928x1208 |
| qcamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--31/qcamera.ts` | h264 526x330 |
| dcamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--32/dcamera.hevc` | hevc 1928x1208 |
| ecamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--32/ecamera.hevc` | hevc 1928x1208 |
| fcamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--32/fcamera.hevc` | hevc 1928x1208 |
| qcamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--32/qcamera.ts` | h264 526x330 |
| dcamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--33/dcamera.hevc` | hevc 1928x1208 |
| ecamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--33/ecamera.hevc` | hevc 1928x1208 |
| fcamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--33/fcamera.hevc` | hevc 1928x1208 |
| qcamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--33/qcamera.ts` | h264 526x330 |
| dcamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--34/dcamera.hevc` | hevc 1928x1208 |
| ecamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--34/ecamera.hevc` | hevc 1928x1208 |
| fcamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--34/fcamera.hevc` | hevc 1928x1208 |
| qcamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--34/qcamera.ts` | h264 526x330 |
| dcamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--35/dcamera.hevc` | hevc 1928x1208 |
| ecamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--35/ecamera.hevc` | hevc 1928x1208 |
| fcamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--35/fcamera.hevc` | hevc 1928x1208 |
| qcamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--35/qcamera.ts` | h264 526x330 |
| dcamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--36/dcamera.hevc` | hevc 1928x1208 |
| ecamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--36/ecamera.hevc` | hevc 1928x1208 |
| fcamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--36/fcamera.hevc` | hevc 1928x1208 |
| qcamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--36/qcamera.ts` | h264 526x330 |
| dcamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--37/dcamera.hevc` | hevc 1928x1208 |
| ecamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--37/ecamera.hevc` | hevc 1928x1208 |
| fcamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--37/fcamera.hevc` | hevc 1928x1208 |
| qcamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--37/qcamera.ts` | h264 526x330 |
| dcamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--38/dcamera.hevc` | hevc 1928x1208 |
| ecamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--38/ecamera.hevc` | hevc 1928x1208 |
| fcamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--38/fcamera.hevc` | hevc 1928x1208 |
| qcamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--38/qcamera.ts` | h264 526x330 |
| dcamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--39/dcamera.hevc` | hevc 1928x1208 |
| ecamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--39/ecamera.hevc` | hevc 1928x1208 |
| fcamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--39/fcamera.hevc` | hevc 1928x1208 |
| qcamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--39/qcamera.ts` | h264 526x330 |
| dcamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--40/dcamera.hevc` | hevc 1928x1208 |
| ecamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--40/ecamera.hevc` | hevc 1928x1208 |
| fcamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--40/fcamera.hevc` | hevc 1928x1208 |
| qcamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--40/qcamera.ts` | h264 526x330 |
| dcamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--41/dcamera.hevc` | hevc 1928x1208 |
| ecamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--41/ecamera.hevc` | hevc 1928x1208 |
| fcamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--41/fcamera.hevc` | hevc 1928x1208 |
| qcamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--41/qcamera.ts` | h264 526x330 |
| dcamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--42/dcamera.hevc` | hevc 1928x1208 |
| ecamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--42/ecamera.hevc` | hevc 1928x1208 |
| fcamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--42/fcamera.hevc` | hevc 1928x1208 |
| qcamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--42/qcamera.ts` | h264 526x330 |
| dcamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--43/dcamera.hevc` | hevc 1928x1208 |
| ecamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--43/ecamera.hevc` | hevc 1928x1208 |
| fcamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--43/fcamera.hevc` | hevc 1928x1208 |
| qcamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--43/qcamera.ts` | h264 526x330 |
| dcamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--44/dcamera.hevc` | hevc 1928x1208 |
| ecamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--44/ecamera.hevc` | hevc 1928x1208 |
| fcamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--44/fcamera.hevc` | hevc 1928x1208 |
| qcamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--44/qcamera.ts` | h264 526x330 |
| dcamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--45/dcamera.hevc` | hevc 1928x1208 |
| ecamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--45/ecamera.hevc` | hevc 1928x1208 |
| fcamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--45/fcamera.hevc` | hevc 1928x1208 |
| qcamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--45/qcamera.ts` | h264 526x330 |
| dcamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--46/dcamera.hevc` | hevc 1928x1208 |
| ecamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--46/ecamera.hevc` | hevc 1928x1208 |
| fcamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--46/fcamera.hevc` | hevc 1928x1208 |
| qcamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--46/qcamera.ts` | h264 526x330 |
| dcamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--47/dcamera.hevc` | hevc 1928x1208 |
| ecamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--47/ecamera.hevc` | hevc 1928x1208 |
| fcamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--47/fcamera.hevc` | hevc 1928x1208 |
| qcamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--47/qcamera.ts` | h264 526x330 |
| dcamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--48/dcamera.hevc` | hevc 1928x1208 |
| ecamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--48/ecamera.hevc` | hevc 1928x1208 |
| fcamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--48/fcamera.hevc` | hevc 1928x1208 |
| qcamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--48/qcamera.ts` | h264 526x330 |
| dcamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--49/dcamera.hevc` | hevc 1928x1208 |
| ecamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--49/ecamera.hevc` | hevc 1928x1208 |
| fcamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--49/fcamera.hevc` | hevc 1928x1208 |
| qcamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--49/qcamera.ts` | h264 526x330 |
| dcamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--50/dcamera.hevc` | hevc 1928x1208 |
| ecamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--50/ecamera.hevc` | hevc 1928x1208 |
| fcamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--50/fcamera.hevc` | hevc 1928x1208 |
| qcamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--50/qcamera.ts` | h264 526x330 |
| dcamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--51/dcamera.hevc` | hevc 1928x1208 |
| ecamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--51/ecamera.hevc` | hevc 1928x1208 |
| fcamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--51/fcamera.hevc` | hevc 1928x1208 |
| qcamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--51/qcamera.ts` | h264 526x330 |
| dcamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--52/dcamera.hevc` | hevc 1928x1208 |
| ecamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--52/ecamera.hevc` | hevc 1928x1208 |
| fcamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--52/fcamera.hevc` | hevc 1928x1208 |
| qcamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--52/qcamera.ts` | h264 526x330 |
| dcamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--53/dcamera.hevc` | hevc 1928x1208 |
| ecamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--53/ecamera.hevc` | hevc 1928x1208 |
| fcamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--53/fcamera.hevc` | hevc 1928x1208 |
| qcamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--53/qcamera.ts` | h264 526x330 |
| dcamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--54/dcamera.hevc` | hevc 1928x1208 |
| ecamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--54/ecamera.hevc` | hevc 1928x1208 |
| fcamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--54/fcamera.hevc` | hevc 1928x1208 |
| qcamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--54/qcamera.ts` | h264 526x330 |
| dcamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--55/dcamera.hevc` | hevc 1928x1208 |
| ecamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--55/ecamera.hevc` | hevc 1928x1208 |
| fcamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--55/fcamera.hevc` | hevc 1928x1208 |
| qcamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--55/qcamera.ts` | h264 526x330 |
| dcamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--56/dcamera.hevc` | hevc 1928x1208 |
| ecamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--56/ecamera.hevc` | hevc 1928x1208 |
| fcamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--56/fcamera.hevc` | hevc 1928x1208 |
| qcamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--56/qcamera.ts` | h264 526x330 |
| dcamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--57/dcamera.hevc` | hevc 1928x1208 |
| ecamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--57/ecamera.hevc` | hevc 1928x1208 |
| fcamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--57/fcamera.hevc` | hevc 1928x1208 |
| qcamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--57/qcamera.ts` | h264 526x330 |
| dcamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--58/dcamera.hevc` | hevc 1928x1208 |
| ecamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--58/ecamera.hevc` | hevc 1928x1208 |
| fcamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--58/fcamera.hevc` | hevc 1928x1208 |
| qcamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--58/qcamera.ts` | h264 526x330 |
| dcamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--59/dcamera.hevc` | hevc 1928x1208 |
| ecamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--59/ecamera.hevc` | hevc 1928x1208 |
| fcamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--59/fcamera.hevc` | hevc 1928x1208 |
| qcamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--59/qcamera.ts` | h264 526x330 |
| dcamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--60/dcamera.hevc` | hevc 1928x1208 |
| ecamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--60/ecamera.hevc` | hevc 1928x1208 |
| fcamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--60/fcamera.hevc` | hevc 1928x1208 |
| qcamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--60/qcamera.ts` | h264 526x330 |
| dcamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--61/dcamera.hevc` | hevc 1928x1208 |
| ecamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--61/ecamera.hevc` | hevc 1928x1208 |
| fcamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--61/fcamera.hevc` | hevc 1928x1208 |
| qcamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--61/qcamera.ts` | h264 526x330 |
| dcamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--62/dcamera.hevc` | hevc 1928x1208 |
| ecamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--62/ecamera.hevc` | hevc 1928x1208 |
| fcamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--62/fcamera.hevc` | hevc 1928x1208 |
| qcamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--62/qcamera.ts` | h264 526x330 |
| dcamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--63/dcamera.hevc` | hevc 1928x1208 |
| ecamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--63/ecamera.hevc` | hevc 1928x1208 |
| fcamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--63/fcamera.hevc` | hevc 1928x1208 |
| qcamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--63/qcamera.ts` | h264 526x330 |
| dcamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--64/dcamera.hevc` | hevc 1928x1208 |
| ecamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--64/ecamera.hevc` | hevc 1928x1208 |
| fcamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--64/fcamera.hevc` | hevc 1928x1208 |
| qcamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--64/qcamera.ts` | h264 526x330 |
| dcamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--65/dcamera.hevc` | hevc 1928x1208 |
| ecamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--65/ecamera.hevc` | hevc 1928x1208 |
| fcamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--65/fcamera.hevc` | hevc 1928x1208 |
| qcamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--65/qcamera.ts` | h264 526x330 |
| dcamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--66/dcamera.hevc` | hevc 1928x1208 |
| ecamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--66/ecamera.hevc` | hevc 1928x1208 |
| fcamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--66/fcamera.hevc` | hevc 1928x1208 |
| qcamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--66/qcamera.ts` | h264 526x330 |
| dcamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--67/dcamera.hevc` | hevc 1928x1208 |
| ecamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--67/ecamera.hevc` | hevc 1928x1208 |
| fcamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--67/fcamera.hevc` | hevc 1928x1208 |
| qcamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--67/qcamera.ts` | h264 526x330 |
| dcamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--68/dcamera.hevc` | hevc 1928x1208 |
| ecamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--68/ecamera.hevc` | hevc 1928x1208 |
| fcamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--68/fcamera.hevc` | hevc 1928x1208 |
| qcamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--68/qcamera.ts` | h264 526x330 |
| dcamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--69/dcamera.hevc` | hevc 1928x1208 |
| ecamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--69/ecamera.hevc` | hevc 1928x1208 |
| fcamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--69/fcamera.hevc` | hevc 1928x1208 |
| qcamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--69/qcamera.ts` | h264 526x330 |
| dcamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--70/dcamera.hevc` | hevc 1928x1208 |
| ecamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--70/ecamera.hevc` | hevc 1928x1208 |
| fcamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--70/fcamera.hevc` | hevc 1928x1208 |
| qcamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--70/qcamera.ts` | h264 526x330 |
| dcamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--71/dcamera.hevc` | hevc 1928x1208 |
| ecamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--71/ecamera.hevc` | hevc 1928x1208 |
| fcamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--71/fcamera.hevc` | hevc 1928x1208 |
| qcamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--71/qcamera.ts` | h264 526x330 |
| dcamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--72/dcamera.hevc` | hevc 1928x1208 |
| ecamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--72/ecamera.hevc` | hevc 1928x1208 |
| fcamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--72/fcamera.hevc` | hevc 1928x1208 |
| qcamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--72/qcamera.ts` | h264 526x330 |
| dcamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--73/dcamera.hevc` | hevc 1928x1208 |
| ecamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--73/ecamera.hevc` | hevc 1928x1208 |
| fcamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--73/fcamera.hevc` | hevc 1928x1208 |
| qcamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--73/qcamera.ts` | h264 526x330 |
| dcamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--74/dcamera.hevc` | hevc 1928x1208 |
| ecamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--74/ecamera.hevc` | hevc 1928x1208 |
| fcamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--74/fcamera.hevc` | hevc 1928x1208 |
| qcamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--74/qcamera.ts` | h264 526x330 |
| dcamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--75/dcamera.hevc` | hevc 1928x1208 |
| ecamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--75/ecamera.hevc` | hevc 1928x1208 |
| fcamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--75/fcamera.hevc` | hevc 1928x1208 |
| qcamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--75/qcamera.ts` | h264 526x330 |
| dcamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--76/dcamera.hevc` | hevc 1928x1208 |
| ecamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--76/ecamera.hevc` | hevc 1928x1208 |
| fcamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--76/fcamera.hevc` | hevc 1928x1208 |
| qcamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--76/qcamera.ts` | h264 526x330 |
| dcamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--77/dcamera.hevc` | hevc 1928x1208 |
| ecamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--77/ecamera.hevc` | hevc 1928x1208 |
| fcamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--77/fcamera.hevc` | hevc 1928x1208 |
| qcamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--77/qcamera.ts` | h264 526x330 |
| dcamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--78/dcamera.hevc` | hevc 1928x1208 |
| ecamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--78/ecamera.hevc` | hevc 1928x1208 |
| fcamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--78/fcamera.hevc` | hevc 1928x1208 |
| qcamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--78/qcamera.ts` | h264 526x330 |
| dcamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--79/dcamera.hevc` | hevc 1928x1208 |
| ecamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--79/ecamera.hevc` | hevc 1928x1208 |
| fcamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--79/fcamera.hevc` | hevc 1928x1208 |
| qcamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--79/qcamera.ts` | h264 526x330 |
| dcamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--80/dcamera.hevc` | hevc 1928x1208 |
| ecamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--80/ecamera.hevc` | hevc 1928x1208 |
| fcamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--80/fcamera.hevc` | hevc 1928x1208 |
| qcamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--80/qcamera.ts` | h264 526x330 |
| dcamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--81/dcamera.hevc` | hevc 1928x1208 |
| ecamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--81/ecamera.hevc` | hevc 1928x1208 |
| fcamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--81/fcamera.hevc` | hevc 1928x1208 |
| qcamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--81/qcamera.ts` | h264 526x330 |
| dcamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--82/dcamera.hevc` | hevc 1928x1208 |
| ecamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--82/ecamera.hevc` | hevc 1928x1208 |
| fcamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--82/fcamera.hevc` | hevc 1928x1208 |
| qcamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--82/qcamera.ts` | h264 526x330 |
| dcamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--83/dcamera.hevc` | hevc 1928x1208 |
| ecamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--83/ecamera.hevc` | hevc 1928x1208 |
| fcamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--83/fcamera.hevc` | hevc 1928x1208 |
| qcamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--83/qcamera.ts` | h264 526x330 |
| dcamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--84/dcamera.hevc` | hevc 1928x1208 |
| ecamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--84/ecamera.hevc` | hevc 1928x1208 |
| fcamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--84/fcamera.hevc` | hevc 1928x1208 |
| qcamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--84/qcamera.ts` | h264 526x330 |
| dcamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--85/dcamera.hevc` | hevc 1928x1208 |
| ecamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--85/ecamera.hevc` | hevc 1928x1208 |
| fcamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--85/fcamera.hevc` | hevc 1928x1208 |
| qcamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--85/qcamera.ts` | h264 526x330 |
| dcamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--86/dcamera.hevc` | hevc 1928x1208 |
| ecamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--86/ecamera.hevc` | hevc 1928x1208 |
| fcamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--86/fcamera.hevc` | hevc 1928x1208 |
| qcamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--86/qcamera.ts` | h264 526x330 |
| dcamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--87/dcamera.hevc` | hevc 1928x1208 |
| ecamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--87/ecamera.hevc` | hevc 1928x1208 |
| fcamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--87/fcamera.hevc` | hevc 1928x1208 |
| qcamera | True | True | `/mnt/e/comma_backup/7.5/00000b53--893429fa7f--87/qcamera.ts` | h264 526x330 |
