# 7714 내비 감속 브랜치 비교

## 비교 기준

- `origin/carrot-wip`: `031cb441501ac3cbda4f28cae48767a5cf2d086e`
- `origin/thftgr/navi-stream`: `4488bd591b26dad1b9d44a6f3890336a0ba25ec8`
- 확인일: 2026-07-16
- 범위: TCP 7714 WebSocket v2에서 수신한 데이터가 실제 `desiredSpeed/desiredSource`와 종방향 감속에
  연결되는 경로

위 두 SHA에 대한 비교는 역사적 사실로 유지한다. 아래의 "현재 통합 구현"은
`upstream/carrot-wip` `79a2a542020b0feb0600297f906cf0afedb6d6ff`를 기준으로 source snapshot
arbitration, category-before-speed와 Naver v1 ingress를 통합한 후의 동작을 별도로 설명한다.

두 브랜치의 공통 조상은 `b98c469bbe33a7fc9ce052f0ebe144599bdbfad9`이다. `navi-stream`의 관련
차이는 주로 `de18d530`의 secondary SDI 및 route 제어 연결에서 왔다.

## 핵심 결론

비교 대상인 두 과거 커밋은 공통으로 지원하는 도로 제한속도, primary 일반 SDI, primary 방지턱,
explicit section, 현재/다음 TBT의 최종 감속 공식과 후보 최솟값 선택 방식이 같다. 두 커밋에는
primary type 22를 현재 road category보다 먼저 판정하는 역사적 순서 결함도 공통으로 있었다.
`navi-stream`은 당시 실제 제어 범위를 다음 세 가지로 넓혔다.

1. primary `sdi.block_*`를 legacy block 상태로 변환하여 section 감속에 사용한다.
2. `sdi_secondary`를 전달하여 type 22 secondary 방지턱을 조건부 감속에 사용한다.
3. 7714 `route.polyline`과 `vehicle` 위치를 기존 route 곡률 계산기로 연결한다.

따라서 역사적 7714만 사용할 때 `carrot-wip`에서는 감속되지 않던 block/secondary bump/route가
`navi-stream`에서는 감속될 수 있었다. 현재 통합 구현은 여기에 소스별 snapshot과 sticky owner를
적용하고, 유효한 category를 speed보다 먼저 적용하며 lane-only category 변경에도 기존 bump를
재평가한다. category 누락은 유효한 0이 아니고, 명시적 0/1만 방지턱을 차단한다.

## 기능별 실제 감속 비교

| 7714 입력 | `carrot-wip` | `navi-stream` | 실제 차이 |
|---|---|---|---|
| `speed.road_limit_kph` | 적용 | 적용 | 동일. control parser는 1..200만 valid, 실제 road 후보는 `AutoRoadSpeedLimitOffset >= 0` 필요 |
| primary `sdi.type/distance_m/speed_limit_kph` | 적용 | 적용 | 지원 SDI type과 공식 동일 |
| primary type 22 방지턱 | 조건부 | 조건부 | 비교 SHA에는 같은 역사적 순서 결함이 있음. 현재 구현은 category-before-speed, missing/explicit validity와 lane-only 재평가로 수정 |
| explicit `speed.section` | 적용 | 적용 | 동일. active/not suspended/not off-route/limit > 0 gate |
| primary `sdi.section_type` | cereal/control에 없음 | 전달/저장 | `navi-stream`도 감속 판단에는 미사용 |
| primary `sdi.block_type/block_distance_m` | 미전달·미적용 | 적용 | `navi-stream`은 block type 2/3을 `xSpdType=4`, block distance로 변환하여 `section` source 생성 |
| primary `sdi.block_speed_kph` | 미전달·미적용 | 전달/저장 | `navi-stream`도 실제 제한속도에는 사용하지 않고 primary `sdi.speed_limit_kph` 사용 |
| `sdi_secondary` 전체 | 미전달·미적용 | 전달/저장 | 아래 type 22만 실제 감속 가능 |
| secondary type 22 방지턱 | 미적용 | 조건부 적용 | `navi-stream`에서 primary camera/section 분기가 없을 때만 bump 후보 생성 |
| secondary 일반 camera/section | 미적용 | 미적용 | `navi-stream`도 저장만 하고 후보를 만들지 않음 |
| current/next guidance TBT | 적용 | 적용 | 동일. off-route에서 억제, `AutoTurnControl` 설정 필요 |
| `route.polyline` | 7714 제어에는 미적용 | route 곡률 감속 적용 | `navi-stream`은 최대 256점을 기존 route 계산기에 연결 |
| `vehicle` 위치/방향 | 7714 제어 상태에 미적용 | route/GPS 상태에 반영 | `navi-stream` route 매칭 및 road name 유지에 사용; 차량속도 자체는 `carState` 사용 |
| `traffic_signal` | 종방향 미적용 | 종방향 미적용 | `navi-stream`은 UI/shared-memory 전달만 추가 |

## 역사적 공통 primary bump 결함과 현재 수정

비교 대상 두 SHA는 모두 `_update_carrot_navi()`에서 새 speed sequence를 먼저 `_apply_carrot_navi_speed()`로
보내고, 이후에 `self.roadcate = navi.road_category`를 수행한다. `_apply_carrot_navi_speed()` 끝의
`_update_sdi()`는 이 때문에 현재 snapshot이 아닌 이전 road category를 사용한다.

- 이전값이 0/1이면 현재 snapshot의 primary `sdi.type=22`는 `xSpdType=-1`로 제거된다.
- 같은 snapshot의 새 road category가 8이어도 이미 끝난 판정을 자동 재실행하지 않는다.
- lane sequence만 바뀌어도 speed sequence가 같으면 재판정하지 않는다.
- present `lane_current`에 `road_category`가 누락되면 cereal 변환이 0을 넣으므로 이후 bump를 계속 막는다.
- `navi-stream`의 secondary/route 기능 추가는 이 순서를 변경하지 않았다.

반면 7713 legacy `update(json)`은 같은 JSON의 `roadcate`를 저장한 뒤 마지막에 `_update_sdi()`를 호출한다.
따라서 동일 차량에서 7713은 감속하고 7714는 감속하지 않는 현상은 코드와 일치한다.

현재 통합 구현은 이 과거 결함을 다음과 같이 수정한다.

- `CarrotNaviState.Lane.roadCategoryValid`로 key 누락/invalid와 명시적 0을 구분한다.
- fresh하고 valid한 lane category를 current speed projection보다 먼저 적용한다.
- lane sequence, category validity 또는 freshness만 바뀌어도 speed sequence와 무관하게 기존 speed를
  다시 projection한다.
- 누락/invalid category는 마지막 fresh 유효값을 보존하고 명시적 0/1만 type 22를 차단한다.
- service와 cluster의 guidance/speed/lane은 local `receivedMonoTimeNanos` 기준 공유
  `V2_ITEM_TTL_S=10.0`을 사용하며, 정확한 TTL 경계에서 stale projection을 지운다.

## block 동작 차이

`carrot-wip` parser는 primary SDI의 type/distance/speed만 보존한다. 앱이 다음처럼 block을 보내도
`block_type`, `block_distance_m`, `block_speed_kph`는 cereal/control 경로에서 사라진다.

`navi-stream`은 이를 보존하여 `_update_sdi()`에 전달한다. block type 2/3이면 다음과 같이 동작한다.

- `xSpdDist = block_distance_m`
- `xSpdType = 4`
- `xSpdLimit`은 `block_speed_kph`가 아니라 primary `speed_limit_kph * safetyFactor`
- 이후 공통 로직에서 type 4는 거리 기반 계산 결과를 다시 `xSpdLimit`으로 덮으므로 active section 동안
  사실상 즉시 section 제한속도 후보가 된다.

## secondary 방지턱 차이

`carrot-wip`의 cereal schema/control parser에는 secondary SDI 필드가 없고 `CarrotServ`가 매 speed 갱신마다
`nSdiPlus*`를 clear한다. 따라서 7714 `sdi_secondary.type == 22`는 감속하지 않는다.

`navi-stream`은 secondary 필드를 `nSdiPlus*`로 변환한다. 다만 공통 `_update_sdi()`가 `if primary ... elif
bump ...` 구조라서, 적용 가능한 primary camera 또는 section이 있으면 secondary 방지턱은 별도 후보로
계산되지 않는다. primary 분기가 없고 road category/mode gate를 통과할 때만 `bump`가 된다.

## route 감속 차이와 `route 30`

`carrot-wip`도 7714 route item을 receiver/cereal/cluster UI까지는 전달하지만, control parser와
`CarrotMan.navi_points`로 연결하지 않는다. 따라서 다른 route 소스가 없는 조건에서 7714 polyline 자체는
실제 route 감속을 만들지 않는다.

`navi-stream`은 다음을 추가한다.

- route/vehicle parsing
- session/sequence/ownership 기반 route bridge
- 7714 polyline을 기존 약 300 m 곡률/역방향 감속 계산에 입력
- `TurnSpeedControlMode`가 허용할 때 `route` 후보 생성

공통 후보 선택은 최저속도가 승리한다. `AutoCurveSpeedLowerLimit=30`인 route 후보가 방지턱 후보보다
낮으면 `desiredSource=route`, `desiredSpeed=30`이 된다. 이 현상은 `navi-stream`의 7714 route 연결 때문에
`carrot-wip` 대비 새로 관측될 수 있다.

추가 주의사항은 다음과 같다.

- mode 2의 조건 `-500 < xDistToTurn < 500`은 유효 TBT가 없을 때의 `xDistToTurn == 0`도 통과시킨다.
- route parser/bridge는 현재 `navigation_status.off_route`로 route를 억제하지 않는다. SDI와 TBT는
  off-route에서 억제되지만, route item이 남아 있으면 route 후보는 계속 생길 수 있다.

## 연결과 호환성 차이

두 브랜치 모두 7714 protocol v2, stream별 strictly increasing sequence와 session/manifest 검사를 한다.
`navi-stream`은 envelope 검사를 더 엄격하게 했다.

- requirements query에 `catalog_revision == 1` 필수
- JSON item에 `sent_at_ms` 필수
- `value` key 필수, present item은 stream에 맞는 object/array 형태 필수
- absent item은 `value: null`과 비어 있지 않은 `reason` 필수

따라서 `carrot-wip`에서 동작하던 구형 client가 이 필드를 누락하면 `navi-stream`에서는 7714 연결/JSON이
거절되어 어떤 감속도 적용되지 않을 수 있다. 호환 envelope를 보내면 공통 기능의 감속 공식 차이는 없다.

## 상태 유지와 clear 차이

- `carrot-wip`의 control 활성 판정은 speed/current/next presence만 본다.
- `navi-stream`은 여기에 `guidance_active`와 route presence를 포함하여 route-only 상태도 active로 유지한다.
- `navi-stream`은 disconnect에서 speed/TBT 외에도 7714 route ownership, vehicle freshness, 남은
  거리/시간과 traffic UI 상태를 정리한다.
- 두 브랜치 모두 receiver가 advertised stale timeout만으로 control item을 자동 expire하지는 않는다.
  연결과 item presence가 유지되면 2 Hz cereal heartbeat가 계속되고, SDI/TBT 거리는 차량 이동량만큼
  로컬 차감된다.

위 항목은 비교 SHA의 역사적 차이다. 현재 통합 구현에서는 TCP EOF/reset/timeout을 즉시 terminal
clear로 취급하지 않는다. 해당 연결에서 확인된 exact source/session의 transport loss만 기록하고,
명시적 `stopped`/`arrived`가 없으면 Naver 2초, legacy Tmap 4초, Carrot Navi v2 10초 owner lease가
끝날 때까지 snapshot과 owner를 유지한다. terminal session은 tombstone으로 남아 더 큰 sequence로도
재활성화할 수 없으며 새 session ID가 필요하다.

## 현재 통합 구현의 소스 선택과 HDA

Tmap legacy, Carrot Navi v2와 Naver v1은 서로 분리된 frozen snapshot으로 저장된다. 새 안내
활성화에서만 activation epoch를 올리고, 선택된 fresh owner는 다른 source의 단순 후속 frame 때문에
바뀌지 않는다. terminal/stale owner가 사라지면 같은 select cycle에 다음 fresh owner 또는 no-owner
상태로 원자적으로 전환한다.

선택된 Tmap/Naver 안전정보가 fresh하고 제어 가능하면 HDA와 최솟값을 비교하지 않고 그 앱 정보를
사용한다. 안전정보가 absent/stale/unusable일 때만 limit과 distance가 모두 양수인 HDA로 같은 cycle에
fallback한다. HDA는 APN을 만들지 않는다. 안내 owner가 있고 safety만 HDA로 fallback하면 APN은
유지될 수 있지만 HDA-only 상태에서는 APN이 표시되지 않는다. 수정된 Tmap APK는 필요하지 않다.

## 현재 provider/freshness 진단

| 필드 | 의미 |
|---|---|
| `naviOwner` | 안내 owner |
| `naviSessionId`, `naviSequence` | owner snapshot identity/order |
| `naviOwnerAgeMs`, `naviSafetyAgeMs` | local monotonic age; 없으면 `-1` |
| `naviLifecycle` | `idle`, `guiding`, `stopped`, `arrived` |
| `naviControlAllowed`, `naviSafetyRejection` | owner 안전정보의 제어 가능 여부와 bounded rejection |
| `decelProvider`, `decelReason` | 그 cycle에 실제 선택한 navigation/HDA 안전 후보 |

`naviOwner`와 `decelProvider`는 의도적으로 독립적이다. `desiredSource`는 이 안전 후보뿐 아니라 road,
route, model 등 전체 속도 후보 중 최종 winner이며 기존 consumer 호환 token을 유지한다. cluster와
Web은 두 source를 별도 표시하고, 새 field가 없는 옛 log에는 기존 generic fallback을 사용한다.

Naver에 대한 위 설명은 유효한 `naver.navigation.v1` frame이 C3에 도착한 경우의 정책이다. 특정
Naver 6.8.0.5 APK에서 실제 안내·카메라·방지턱 frame 수신이 검증되었다는 acceptance 주장은 아니다.
