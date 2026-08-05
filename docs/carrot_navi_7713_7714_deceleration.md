# 7713 / 7714 내비 감속 적용 비교

## 확인 기준

- 저장소: `ajouatom/openpilot`
- 기준 베이스: `upstream/carrot-wip` (`79a2a542020b0feb0600297f906cf0afedb6d6ff`)
- 구현: 소스별 snapshot arbitration과 Naver v1 ingress가 통합된 현재 코드
- 확인일: 2026-07-23
- 범위: 수신, 파싱, `CarrotServ` 상태 반영, 감속 목표 선택, 종방향 계획 반영, UI 표시

## 핵심 결론

7713과 7714는 수신 및 상태 관리 방식은 다르지만, 최종적으로는 같은 `CarrotServ._update_sdi()`와
`CarrotServ.update_navi()`를 통해 `carrotMan.desiredSpeed`와 `desiredSource`를 만든다. 따라서 같은
`xSpdType/xSpdLimit/xSpdDist`, TBT, 경로 상태에 도달한 뒤의 감속 공식과 UI source 표시는 같다.

현재 구현은 Tmap legacy, Carrot Navi v2와 Naver v1 입력을 소스별 immutable snapshot으로 분리한다.
`NavigationSourceStore`는 새 안내 활성화에 activation epoch를 부여하고, fresh한 현재 owner를
sticky하게 유지한다. 따라서 단순한 마지막 frame 순서로 안내 소유권이나 type/limit/distance가
뒤섞이지 않는다.

선택된 Tmap/Naver 소스의 안전정보가 fresh하고 제어 가능한 경우 그 정보가 권위가 있으며 HDA와 더
낮은 값을 비교하지 않는다. 선택 소스의 안전정보가 없거나 stale/unusable일 때만 같은 제어 cycle에서
유효한 HDA limit/distance로 fallback한다. 안내 owner와 안전 감속 provider는 별도 상태이므로
`naviOwner=naver_v1`, `decelProvider=hda`가 동시에 정상적으로 발행될 수 있다. HDA, model, route,
curve 후보는 외부 내비 lease로 gate하지 않는다.

7714 primary type 22는 현재 snapshot의 유효한 `lane_current.road_category`를 speed보다 먼저 적용한
뒤 평가한다. lane sequence, category validity 또는 freshness만 바뀌어도 현재 speed projection을
다시 평가한다. category key 누락은 유효한 0이 아니며 마지막 fresh 유효값을 보존하고, 명시적 0/1만
고속도로 계열로서 방지턱을 차단한다.

## 7713 → 7714 핵심 감속 parity

| 7713 | 7714 | parity 판정 |
|---|---|---|
| `nRoadLimitSpeed` | `speed.road_limit_kph` | 정상 1..200 km/h는 적용. 7713의 200 초과 legacy encoding decode와 0→30 fallback은 7714에 없음 |
| `nSdiType/SpeedLimit/Dist` | `speed.sdi.type/speed_limit_kph/distance_m` | 동일 적용 |
| `nSdiBlockType/Dist` | `speed.sdi.block_type/block_distance_m` | 동일 적용 |
| `nSdiBlockSpeed` | `speed.sdi.block_speed_kph` | 양쪽 모두 수신/저장만 하고 감속 제한속도로 사용하지 않음 |
| `nSdiPlusType/Dist`의 type 22 | `speed.sdi_secondary.type/distance_m`의 type 22 | primary 감속 분기가 없을 때 동일 적용 |
| `nSdiPlusSpeedLimit`, plus block fields | secondary speed/block fields | 양쪽 모두 감속 미적용 |
| `roadcate` | `lane_current.road_category` + `roadCategoryValid` | 양쪽 모두 유효 category를 SDI보다 먼저 적용. 7714는 lane-only 변경에도 기존 speed를 재평가하며 누락과 명시적 0을 구분 |
| 현재/다음 TBT type, distance | `guidance_current/next` type, distance | 동일 적용 |
| `nTBTNextRoadWidth` | 직접 대응 control mapping 없음 | 7714에서 누락. 다만 현재 코드는 감속 목표속도 공식에는 쓰지 않고 ATC type 전환 거리/표시에만 사용 |
| route/vrtx polyline | `route.polyline` | 동일 곡률 감속 적용. 7713 최대 4096점, 7714 최대 256점 |

일반 SDI의 지원 type과 최종 감속 공식은 같지만, 다음 차이는 7713/7714의 실제 동작을 갈라놓는다.

- 7713의 `nRoadLimitSpeed > 200` legacy encoding은 decode하지만 7714는 200 초과 road limit을 invalid로
  처리한다.
- 7713은 road limit 0 이하를 30으로 바꾸지만 7714는 invalid/no limit으로 처리한다.
- 7714는 `off_route=true`이면 SDI/TBT를 의도적으로 억제한다. 7713에는 이 안전 gate가 없다.
- 7714 route는 256점으로 잘리므로 점 간격이 매우 조밀하고 필요한 전방 구간이 256점 밖이면 7713보다
  route 곡률 정보가 짧아질 수 있다.

## 데이터 흐름

### 7713 legacy HTTP

1. `POST /api/navi/{tmap_version}`가 JSON을 수신한다.
2. `rgdata`만 `CarrotServ.update()`로 전달한다. 중첩된 `guidance`, `sdi`, `lane` 그룹은 flat key로
   보완된다.
3. 실제 내비 감속 필드 갱신은 payload에 `nRoadLimitSpeed` key가 있을 때만 수행된다.
4. route/vrtx는 별도 `handle_route()`로 경로를 저장한다.

### 7706 legacy UDP

7706은 Naver discovery와 기존 Tmap UDP가 같은 수신 port를 공유한다. Naver discovery 모양(정확한
`type`/`source`/`schema_version` key 또는 `carrot.navigation.discover` prefix)은 크기, UTF-8, 중첩,
root object, duplicate key, non-finite number까지 strict JSON으로 검사한 뒤 requester와 port를
검증한다. 이 모양의 malformed frame은 Tmap으로 downgrade하거나 전달하지 않는다.

그 외 Tmap legacy datagram은 같은 byte-size, UTF-8, non-empty, 중첩, root-object 제한과 duplicate
key 거부를 유지하되 기존 호환 JSON decoder를 사용한다. 따라서 위치 필드의 `NaN` 같은 Python
legacy non-finite 값이 있어도 평면 root payload가 update path에 도달할 수 있다. 이 UDP 7706
호환 처리는 Naver discovery나 TCP envelope의 strict 검증을 완화하지 않는다. UDP payload는 HTTP
7713과 달리 `rgdata` wrapper를 넣지 않으며, 실제 navigation snapshot 갱신에는 평면
`nRoadLimitSpeed` key가 필요하다.

### 7714 Carrot Navi v2

1. 상시 `carrot_navi` 프로세스가 WebSocket v2 스트림을 수신한다.
2. item별 presence, sequence와 session을 검사하고 `carrotNavi` cereal service로 2 Hz heartbeat를
   발행한다.
3. `carrot_man`이 `CarrotNaviControl`로 파싱하고 로컬 수신 monotonic 시간과 item sequence를 보존한
   source snapshot을 `NavigationSourceStore`에 넣는다.
4. guidance/speed/lane은 공유 `V2_ITEM_TTL_S=10.0`을 사용한다. speed sequence가 같아도 lane
   sequence, category validity 또는 freshness가 바뀌면 projection revision을 올려 다시 적용한다.
   단순 cereal heartbeat에서는 로컬에서 차감 중인 거리를 보존한다.

## SDI wire JSON 구조

### 7713 HTTP `rgdata`

`POST http://<device>:7713/api/navi/<tmap_version>` body는 다음 형태다. 실제 SDI 갱신 gate이므로
`rgdata.nRoadLimitSpeed` key를 반드시 포함해야 한다.

```json
{
  "timestamp_ms": 1710000000000,
  "rgdata": {
    "nRoadLimitSpeed": 50,
    "nSdiType": 1,
    "nSdiSpeedLimit": 50,
    "nSdiSection": -1,
    "nSdiDist": 420,
    "nSdiBlockType": -1,
    "nSdiBlockSpeed": 0,
    "nSdiBlockDist": 0,
    "nSdiPlusType": 22,
    "nSdiPlusSpeedLimit": 0,
    "nSdiPlusDist": 93,
    "nSdiPlusBlockType": -1,
    "nSdiPlusBlockSpeed": 0,
    "nSdiPlusBlockDist": 0,
    "roadcate": 8
  }
}
```

`guidance`, `sdi`, `lane` nested object도 flatten하지만, 그 안의 key도 위와 같은 legacy 이름이어야 한다.
충돌 시 `rgdata` 최상위 값이 우선하므로 전송은 평면 구조가 가장 명확하다.

### 7714 WebSocket v2 `speed` item

네트워크에서 앱이 보내는 `value` 내부 key는 snake_case다. cereal로 변환된 뒤의 camelCase
(`sdiPresent`, `sdiType` 등)를 앱에서 직접 보내면 안 된다. WebSocket path는
`/api/navi/ws/v2/json/<session_id>/speed`이고, `session_id`, `manifest_revision`, `stream_handle`은 control
협상에서 받은 manifest 값을 사용한다.

```json
{
  "type": "item_update",
  "protocol_version": 2,
  "session_id": "0123456789abcdef",
  "manifest_revision": 1,
  "schema_version": 1,
  "kind": "json",
  "name": "speed",
  "stream_handle": 6,
  "sequence": 101,
  "source_timestamp_ms": 1710000000000,
  "sent_at_ms": 1710000000010,
  "present": true,
  "value": {
    "current_kph": 48.0,
    "road_limit_kph": 50,
    "sdi": {
      "type": 1,
      "distance_m": 420,
      "speed_limit_kph": 50,
      "section_type": -1,
      "block_type": -1,
      "block_speed_kph": 0,
      "block_distance_m": 0
    },
    "sdi_secondary": {
      "type": 22,
      "distance_m": 93,
      "speed_limit_kph": 0,
      "section_type": -1,
      "block_type": -1,
      "block_speed_kph": 0,
      "block_distance_m": 0
    },
    "section": {
      "active": false,
      "speed_limit_kph": 0,
      "average_kph": 0.0,
      "overall_average_kph": 0.0,
      "remaining_distance_m": 0.0,
      "remaining_time_sec": 0,
      "progress": 0.0,
      "suspended": false,
      "off_route": false
    }
  }
}
```

방지턱 허용 판단의 `road_category`는 별도
`/api/navi/ws/v2/json/<session_id>/lane_current` item의 `value.road_category`로 보내야 한다. 같은 envelope
형태에서 `name`, `stream_handle`, `sequence`, `value`를 해당 stream에 맞게 바꾼다.

`road_category`는 현재 주행 링크의 도로 등급 코드다. 이 저장소에는 0~9 전체 enum 표가 없으며 제어
코드가 확정적으로 해석하는 의미는 `0/1 = 고속도로 계열`, `2 이상 = 그 밖의 도로`뿐이다. 따라서 제어
관점에서는 다음처럼 취급해야 한다.

| 값 | 현재 코드의 의미 | type 22 방지턱 |
|---:|---|---|
| 0 | 고속도로 | 차단 |
| 1 | 도시고속도로/자동차전용도로 계열 | 차단 |
| 2 이상 | 일반도로 계열로 취급 | 허용 |
| 누락/invalid | `roadCategoryValid=false`; 유효한 0으로 덮지 않고 마지막 fresh 유효값 유지 | 마지막 유효값에 따름 |

TMAP 데이터 정의의 `roadcate` 표는 0 고속국도, 1 도시고속화도로, 2 국도, 3 국가지원지방도,
4 지방도, 5 주요도로1, 6 주요도로2, 7 주요도로3, 8 기타도로1, 9 이면도로, 10 페리항로,
11 단지내도로, 12 이면도로2(세도로)다. 필드명과 0/1 의미가 코드 주석과 정확히 일치하므로 이 값의
원천으로 볼 수 있다. 다만 7714 protocol 문서 자체에는 enum이 선언되어 있지 않으며 openpilot 제어는
세부 등급을 구분하지 않고 `0/1` 대 `2 이상`만 사용한다. 수신 전에 임의의 일반도로 기본값으로
방지턱을 허용하지 않으며, key presence와 숫자 validity가 확인된 category만 새 유효값으로 저장한다.

```json
{
  "type": "item_update",
  "protocol_version": 2,
  "session_id": "0123456789abcdef",
  "manifest_revision": 1,
  "schema_version": 1,
  "kind": "json",
  "name": "lane_current",
  "stream_handle": 4,
  "sequence": 33,
  "source_timestamp_ms": 1710000000000,
  "sent_at_ms": 1710000000010,
  "present": true,
  "value": {
    "road_category": 8
  }
}
```

SDI를 지울 때는 더 큰 sequence로 `present: false`, `value: null`, 비어 있지 않은 `reason`을 보낸다.

```json
{
  "type": "item_update",
  "protocol_version": 2,
  "session_id": "0123456789abcdef",
  "manifest_revision": 1,
  "schema_version": 1,
  "kind": "json",
  "name": "speed",
  "stream_handle": 6,
  "sequence": 102,
  "source_timestamp_ms": 1710000001000,
  "sent_at_ms": 1710000001010,
  "present": false,
  "value": null,
  "reason": "source_absent"
}
```

## 감속 기능별 비교

| 기능 | 7713 입력 | 7714 입력 | 실제 적용 조건과 결과 | 적용 source/UI label |
|---|---|---|---|---|
| 고정/일반 카메라 | `nSdiType` 0,1,2,3,4,8,75,76 + speed/dist | primary `sdi`의 같은 type + speed/dist | `AutoNaviSpeedCtrlMode > 0`, speed > 0. 안전계수와 감속률 적용 | `cam` |
| 이동식 카메라 | type 7 | type 7 | mode 3에서만 적용. mode 1/2에서는 `_update_sdi()`가 limit/dist를 0으로 지움 | `cam` |
| 구간단속(block) | `nSdiBlockType` 2/3, block distance | primary SDI block type 2/3, block distance | type을 4로 바꾸고 block distance 사용. 단, block speed는 사용하지 않고 primary SDI speed에 안전계수를 적용 | `section` |
| 7714 전용 section object | 없음 | `section.active`, speed limit, remaining distance | present + active + not suspended + section off-route 아님 + 전체 off-route 아님 + limit > 0일 때 type 4로 변환 | `section` |
| 방지턱 | primary/plus type 22 | primary/secondary type 22 | mode >= 2이고 fresh category가 존재하며 0/1이 아닐 때 적용. 단, Naver v1 mapper가 `SafetyCode.isSpeedBump()`로 검증한 type 22는 category가 없을 때도 적용한다. 모든 source에서 명시적 0/1은 차단한다. 7714는 같은 snapshot category를 speed보다 먼저 적용하고 lane-only 변경에도 재평가. payload speed는 무시하고 `AutoNaviSpeedBumpSpeed` 사용 | `bump` |
| 도로 제한속도 | `nRoadLimitSpeed` | `road_limit_kph` | `AutoRoadSpeedLimitOffset >= 0`, active >= 2, road limit valid일 때 limit+offset | `road` |
| 현재 TBT | `nTBTTurnType/nTBTDist` | `guidance_current.turn_type/distance_m` | 지원 turn type이 `xTurnInfo`로 변환되고 `AutoTurnControl`이 2 또는 3일 때 속도 목표 계산 | `atc` |
| 다음 TBT | `nTBTTurnTypeNext/nTBTDistNext` | `guidance_next` | 현재 거리 + 다음 거리를 사용하고 같은 ATC 설정 적용 | `atc2` |
| route 곡률 | `route`/`vrtx`, 최대 4096점 | route polyline, 최대 256점 | 동일 경로 곡률 계산. `TurnSpeedControlMode=2`는 TBT ±500 m, mode 3/4는 항상. 기본값 mode 1에서는 route 감속 미적용 | `route` |
| 신호등 | `sinf/ssinf` | `traffic_signal` | `TrafficLight` shared-memory param과 cluster/UI 표시에만 전달. `desiredSpeed`나 longitudinal stop target에는 직접 연결되지 않음 | 감속 source 없음 |

공통 감속 속도는 목표 지점의 안전속도와 안전시간을 기준으로
`sqrt(v_target^2 + 2 * decel_rate * decel_distance)` 형태로 계산한다. 모든 후보 중 최솟값을
`desiredSpeed`로 선택하고, planner가 cruise 속도와 다시 `min()`하여 실제 종방향 목표에 반영한다.

## 수신되지만 감속 제어에는 쓰이지 않는 값

### 양쪽 공통 legacy 상태에서 미사용

- `nSdiSection` / 7714 `sdi.section_type`: 저장만 하고 판단에 사용하지 않는다.
- `nSdiBlockSpeed`: 저장은 되지만 block/section 진입 시 `xSpdLimit`에 사용하지 않는다.
- `nSdiPlusSpeedLimit`, `nSdiPlusBlockType`, `nSdiPlusBlockSpeed`, `nSdiPlusBlockDist`: 저장만 한다.
- secondary SDI는 type 22 방지턱의 거리만 감속에 사용할 수 있다. 적용 가능한 primary
  카메라/section이 있으면 primary 분기가 먼저 선택되므로 secondary 방지턱도 별도 후보로 계산되지
  않는다. secondary 카메라나 secondary section은 제어에 적용되지 않는다.
- 지원 목록 밖의 primary SDI type은 설명이나 화면 표시는 가능해도 `_update_sdi()` 감속 대상이 아니다.
- type 22의 payload speed limit은 primary/secondary 모두 무시한다.
- `roadcate` 0/1에서는 방지턱 감속을 의도적으로 막는다.

### 7714에서 화면/상태용이지만 제어에 미사용

- speed `current_kph`
- section `average_kph`, `overall_average_kph`, `remaining_time_sec`, `progress`
- guidance `time_sec`, `road_name`, `mid_direction`, point 좌표
- navigation status의 `mode`, `route_present`; `guidance_active`는 control 활성 판정에는 포함되지만
  그 자체로 속도를 만들지 않는다.
- lane 대부분은 제어에 쓰이지 않고 `road_category`만 방지턱 허용 판단에 사용한다.
- traffic signal은 위와 같이 UI/shared-memory 전달만 한다.

7714의 explicit section object가 active이면 같은 speed item의 primary SDI보다 먼저 선택된다. 둘을
각각 감속 후보로 계산해 더 낮은 값을 고르는 구조가 아니다. 이후 공통 `_update_sdi()`에서도 primary
section/camera 분기가 secondary 방지턱보다 먼저다.

## 7713과 7714의 중요한 동작 차이

### Primary type 22 category-before-speed 동작

7713 `update(json)`은 같은 packet의 `roadcate`를 저장한 뒤 `_update_sdi()`를 호출한다. 현재 7714
`_update_carrot_navi()`도 같은 의미를 보장한다.

1. `roadCategoryValid=true`인 fresh lane category를 먼저 적용한다.
2. 그 뒤 current speed snapshot을 projection한다.
3. lane sequence, category validity 또는 freshness가 변하면 speed sequence가 같아도 projection을
   다시 실행한다.
4. lane category key가 누락되거나 invalid이면 유효한 0으로 만들지 않고 마지막 fresh 유효값을
   보존한다.
5. 10초 TTL 경계에서 stale speed/category projection을 지우고 `projection_revision`을 올린다.

| 입력 | 결과 |
|---|---|
| 같은 snapshot의 category 8 + primary type 22/dist 93 | 즉시 `xSpdType=22`, bump 설정속도, distance 93 |
| 기존 category 0 뒤 lane-only category 8 | 기존 type 22 speed를 즉시 재평가하여 허용 |
| category key 누락/invalid | 마지막 fresh 유효 category 유지; 명시적 0으로 간주하지 않음 |
| 명시적 category 0 또는 1 | 고속도로 계열 gate로 type 22 차단 |
| Naver v1 category 없음 + mapper가 검증한 type 22 | category를 합성하지 않고 방지턱 허용 |
| Tmap/Carrot Navi category 없음 | `road_category_missing`으로 차단하고 HDA 후보로 fallback |
| category/speed 수신 후 정확히 10초 | stale projection clear |

과거 `origin/carrot-wip`과 `origin/thftgr/navi-stream`의 비교 대상 커밋에는 speed를 먼저 판정하고
category를 나중에 저장하는 순서 결함이 있었다. 위 표는 그 역사적 결함이 아니라 현재 통합 구현의
동작이다.

Naver 예외는 production mapper의 exact safety-code 판정에만 적용한다. 숫자 type 22만 임의로 만든
다른 source에는 확장하지 않으며, Naver snapshot에 category 0/1이 명시되면 예외보다 highway gate가
우선한다. 실제 production Naver safety 이벤트의 기기 수용 여부는 Task 15 실주행 검증 전까지 완료로
간주하지 않는다.

### 도로 제한속도

- 7713은 현재 저장값과 다른 제한속도가 6회 연속 들어와야 값을 바꾼다. 새 후보값 자체의 일치 여부는
  추적하지 않으므로 서로 다른 값들이 연속으로 들어와도 counter는 증가한다.
- 7713은 200 초과 값을 `(value - 20) / 10`으로 legacy decode하고, 120은 115로 보정하며,
  0 이하는 30으로 바꾼다.
- 7714는 1..200만 valid로 인정하여 즉시 적용한다. 120 보정이나 200 초과 legacy decode는 없다.

### off-route와 clear

- 7714는 전체 off-route에서 primary/secondary SDI, section, current/next TBT를 억제한다. 도로
  제한속도는 유효하면 남긴다.
- 7713에는 off-route gate가 없다. 앱이 clear 값이나 새 값을 보내거나 active timer가 끝날 때까지
  기존 값이 유지된다.
- TCP EOF, reset 또는 timeout은 `record_transport_loss(source, exact_session_id, now)`로 해당 연결에서
  실제 확인된 source/session의 transport loss만 기록한다. active snapshot이나 owner를 즉시 지우지
  않는다.
- 명시적 `stopped`/`arrived` envelope는 즉시 terminal 상태가 되며 `(source, session_id)` tombstone을
  남긴다. 같은 session은 더 큰 sequence로도 재활성화할 수 없고 새 안내에는 새 session ID가 필요하다.
- terminal envelope가 없으면 Naver는 2초, legacy Tmap은 4초, Carrot Navi v2는 10초 owner lease
  경계에서 논리적으로 release된다.
- lease expiry는 terminal tombstone이 아니다. 같은 nonterminal session이 나중에 재개할 수는 있지만
  기존 activation epoch를 유지하므로 현재 fresh owner를 빼앗지 않는다.

### 순서와 freshness

- 7713 rgdata는 양수 timestamp가 있을 때 하나의 전역 `last timestamp`보다 큰지만 검사한다. timestamp가
  없거나 0이면 검사하지 않으며, client 재접속 때 counter를 reset하지 않는다.
- 7714는 새 협상마다 session을 만들고 item stream별 strictly increasing sequence를 검사한다.
- 7714 guidance current/next, speed와 lane은 `carrot_navi_control.V2_ITEM_TTL_S=10.0`을 service와
  cluster가 함께 사용한다. local `receivedMonoTimeNanos`만 freshness 기준이며 phone wall-clock은
  제어 TTL에 사용하지 않는다.
- vehicle/crossroad, status, route, traffic 같은 다른 cluster 표시 item의 TTL은 별도이며 이 10초
  guidance/speed/lane 계약과 혼동하면 안 된다.

### route

- 7713 route는 `NavDestination`도 마지막 점으로 갱신한다.
- 7714 route bridge는 기존 route consumer만 갱신하고 `NavDestination`은 쓰지 않는다.
- 두 경로 모두 실제 route 감속은 공통 곡률 계산을 사용하므로 설정이 허용하고 route candidate가
  최솟값일 때만 `desiredSource=route`가 된다.

### 동시 수신 충돌

- Tmap legacy, Carrot Navi v2와 Naver v1은 각각 완전한 frozen `NavigationSnapshot`으로 저장된다.
- `NavigationSourceStore`의 `RLock` 아래에서 accept/select를 수행하므로 source 간
  type/limit/distance가 섞이지 않는다.
- inactive→guiding 또는 새 session 전환에서만 activation epoch를 올린다. 더 새로 활성화된 안내가
  owner가 될 수 있지만, fresh owner가 정해진 뒤 다른 source의 단순 후속 frame은 owner를 흔들지 않는다.
- terminal/stale owner가 사라지면 같은 select cycle에 다음 fresh owner 또는 no-owner 상태로
  원자적으로 전환한다.
- owner와 safety provider는 분리되어 있으므로 안내 owner가 유지되는 동안에도 safety absent/stale
  조건에서 `decelProvider=hda`를 사용할 수 있다.

## UI 표시 확인

### 안내 owner, 안전 provider와 APN

현재 `carrotMan` 진단은 안내 소유권과 안전 감속 선택을 다음 필드로 분리한다.

| 필드 | 의미 |
|---|---|
| `naviOwner` | 현재 guiding owner: `tmap_legacy`, `carrot_navi_v2`, `naver_v1` 또는 빈 값 |
| `naviSessionId`, `naviSequence` | 선택 owner snapshot의 session과 sequence |
| `naviOwnerAgeMs` | local monotonic receipt 기준 owner age; 없으면 `-1` |
| `naviSafetyAgeMs` | 선택 owner의 안전 item age; 없으면 `-1` |
| `naviLifecycle` | `idle`, `guiding`, `stopped`, `arrived` lifecycle |
| `naviControlAllowed` | 선택 owner 안전정보의 제어 허용 여부 |
| `naviSafetyRejection` | `safety_absent`, `safety_stale`, `off_route` 등 bounded rejection token |
| `decelProvider`, `decelReason` | 그 cycle에 실제 선택한 navigation/HDA 안전 후보와 이유 |

`naviOwner`는 guidance를, `decelProvider`는 safety candidate를 설명한다. 따라서 Naver guidance와 HDA
fallback이 동시에 표시되는 것은 모순이 아니다. `desiredSource`는 이 안전 후보뿐 아니라 ATC, road,
route, model 등 모든 속도 후보 중 최종 winner이므로 `decelProvider`와 다를 수 있다. 기존
`desiredSource` token은 호환성을 위해 유지한다.

APN/`activeCarrot`은 guiding owner에서 시작한다. fresh owner는 기본 active 2이고 선택된
camera/section/bump에 따라 3/4/5가 될 수 있다. HDA는 APN을 켜거나 올리지 않는다. 따라서 HDA-only는
APN이 아니지만, 안내 owner가 있고 안전정보만 HDA로 fallback한 cycle에는 APN이 유지될 수 있다.
옛 log에 새 필드가 없으면 cluster/Web은 기존 generic source 표시로 fallback한다.

### 실제 감속 source 표시

`CarrotServ`가 후보 중 최솟값을 고른 뒤 다음 중 하나를 `desiredSource`로 발행한다.

`atc`, `atc2`, `cam`, `hda`, `bump`, `section`, `police`, `waze`, `road`, `vturn`, `route`,
`model`, `gas`

on-road UI, mici UI, cluster live UI는 모두 다음 조건에서 실제 source 문자열과 목표속도를 주황색으로
표시한다.

- `0 < desiredSpeed < 200`
- `desiredSpeed < 운전자 설정 cruise speed`
- label은 8자로 자른 `desiredSource`

따라서 route 데이터가 존재하는 것만으로 `route`가 표시되는 것은 아니다. route 후보가 설정상
활성이고 다른 모든 후보보다 낮아 실제 winner가 되어야 한다. 방지턱도 같은 방식으로 `bump`가 winner일
때만 표시된다. `longitudinalPlan.cruiseTarget`의 eco 표시 조건이 먼저 참이면 `eco`가 우선 표시된다.
가속페달 override가 허용되는 source는 최종 source가 `gas`로 바뀔 수 있다. `cam`, `section`, `police`는
가속페달 override reset 대상이지만 bump, route, road, atc, waze 등은 그렇지 않다.

후보 속도가 완전히 같으면 list 순서상 `atc`, `atc2`, SDI 계열, `road`, `vturn`, `route`, `model`
순서로 먼저 등장한 source가 label이 된다.

### 방지턱에서 `route 30`이 표시되는 경우

`desiredSource`는 현재 들어온 이벤트 type을 그대로 표시하는 값이 아니라, 실제로 생성된 후보 중 가장
낮은 속도를 만든 후보의 이름이다. 따라서 `route 30`은 정상 생성된 bump 후보보다 route가 낮거나,
명시적 category 0/1, mode, off-route, stale/invalid safety 같은 gate로 bump 후보가 생성되지 않은
경우에 가능하다. 과거 category 처리 순서 결함은 현재 구현에서 수정되었으므로 누락 category나
lane-only 변경 자체를 순서 결함으로 진단하면 안 된다.

- 방지턱 후보는 `AutoNaviSpeedBumpSpeed`를 도착 목표로 하여 거리 기반 감속 속도를 계산한다. 목표속도가
  30 km/h여도 방지턱에 도달하기 전 계산값은 보통 30보다 조금 크다. 기본 목표는 35 km/h다.
- route 후보는 `max(route_speed * MapTurnSpeedFactor, AutoCurveSpeedLowerLimit)`이다. 기본
  `AutoCurveSpeedLowerLimit`는 30 km/h라서 경로 곡률 계산값이 낮으면 정확히 30에 고정된다.
- UI에 발행하는 `desiredSpeed`는 `int()`로 소수점을 버린다. 내부적으로 route가 30.0, bump가 30.x이면
  둘 다 화면에는 30처럼 보일 수 있지만 route가 엄밀히 더 낮아 source는 `route`가 된다.
- 속도가 완전히 같은 경우에는 후보 list에서 먼저 나오는 SDI/bump가 이긴다. 따라서 `route 30`은 내부
  route 값이 실제로 더 낮았거나, 방지턱이 category/mode/freshness 등의 조건을 통과하지 못했다는 뜻이다.
- route 곡률은 현재 위치부터 약 300 m 경로를 사용하고 뒤에서 앞으로 감속속도를 전파한다. 전방의 실제
  급커브뿐 아니라 polyline의 꺾임/노이즈도 route 값을 30까지 낮출 수 있으며, 방지턱 type 자체를 route로
  변환하는 로직은 없다.
- `TurnSpeedControlMode == 2`의 route 활성 조건은 현재 `-500 < xDistToTurn < 500`뿐이다. 유효한 TBT가
  없을 때의 `xDistToTurn == 0`도 이 조건을 통과하므로 route가 의도와 달리 활성화될 수 있다.
  mode 3/4에서는 route 후보가 항상 활성화된다.

현장에서 `carrotMan.xSpdType == 22`이고 `activeCarrot == 5`이면 방지턱 자체는 정상 인식된 상태에서
route가 더 낮아 이긴 것이다. `xSpdType == -1`이면 raw SDI가 UI에 보여도 방지턱 제어 후보는 없으며,
`naviOwner`, `naviSafetyRejection`, `naviSafetyAgeMs`, 명시적 `lane_current.road_category`, mode와
off-route를 함께 확인해야 한다.

### 현장 설정 사례: bump 22 km/h, route/curve 하한 18 km/h

확인한 현장 Params의 관련 값은 다음과 같다.

| Param | 값 | 실제 영향 |
|---|---:|---|
| `AutoNaviSpeedCtrlMode` | 2 | 일반 카메라와 type 22 방지턱 활성. 이동식 type 7은 비활성 |
| `AutoNaviSpeedBumpSpeed` | 22 | payload의 bump speed 대신 최종 방지턱 목표 22 km/h 사용 |
| `AutoNaviSpeedBumpTime` | 3 | 22 km/h × 3초 = 약 18.33m를 safe distance로 사용 |
| `AutoNaviSpeedDecelRate` | 120 | 계산에서는 1.20 m/s² 사용 |
| `AutoNaviCountDownMode` | 2 | 방지턱 거리 카운트다운 포함 |
| `AutoNaviSpeedSafetyFactor` | 107 | 일반 camera/section에는 107% 적용하지만 bump에는 미적용 |
| `AutoNaviSpeedCtrlEnd` | 10 | 일반 camera에 사용하며 bump에는 미적용 |
| `TurnSpeedControlMode` | 2 | vision `vturn`과 조건부 route 후보 활성 |
| `AutoCurveSpeedLowerLimit` | 18 | vturn/route/model 후보 하한 18 km/h |
| `MapTurnSpeedFactor` | 102 | route 계산속도에 1.02 곱함 |
| `AutoCurveSpeedFactor` | 90 | vision 곡률 입력에 0.90 곱해 vturn 감속을 다소 완화 |
| `AutoTurnControl` | 1 | TBT `atc/atc2` 속도 후보는 비활성 |
| `ModelTurnSpeedFactor` | 0 | model turn speed가 200으로 유지되어 현재 후보 조건 `< 200`을 통과하지 않음 |
| `AutoRoadSpeedLimitOffset` | -1 | `road` 제한속도 후보 비활성 |

이 설정의 순수 방지턱 후보는 다음 공식이다.

여기서 `bump 후보`는 방지턱 자체의 제한속도가 아니라, 현재 남은 거리에서 planner에 넘길 **현재 허용
목표속도**다. `AutoNaviSpeedBumpSpeed=22`는 방지턱 도착 목표이고, 코드는 감속률과 거리를 역산하여
멀리서는 22보다 높은 속도를 허용한 뒤 접근할수록 22로 낮춘다. 최종 후보가 운전자 설정속도보다 높으면
아직 cruise를 제한하지 않으며, 그보다 낮아지는 지점부터 실제 감속 요구가 생긴다.

`safeDist = (22 / 3.6) * 3 = 18.33m`

`distance > 18.33m`이면
`bumpKph = 3.6 * sqrt((22 / 3.6)^2 + 2 * 1.2 * (distance - 18.33))`, 그 이하는 22
km/h다.

| 남은 방지턱 거리 | 내부 bump 후보 | publish `int()` 값 |
|---:|---:|---:|
| 150m | 67.67 | 67 |
| 100m | 54.99 | 54 |
| 93m | 52.98 | 52 |
| 60m | 42.19 | 42 |
| 50m | 38.33 | 38 |
| 40m | 34.03 | 34 |
| 30m | 29.10 | 29 |
| 20m | 23.15 | 23 |
| 18.33m 이하 | 22.00 | 22 |

따라서 route 후보가 30 km/h로 유지된다면 약 31.7m 바깥에서는 route 30이 bump 후보보다 낮아
`desiredSource=route`가 되고, 약 31.7m 안쪽부터 bump 후보가 30보다 낮아져 `bump`가 이긴다. route가
하한 18까지 내려가면 bump 최저목표 22보다 항상 낮아서 방지턱 전체 구간에서 route가 이길 수 있다.

추가 조건과 주의점:

- 7714 primary 또는 secondary SDI type 22가 있어야 한다. secondary type 22는 적용 가능한 primary
  camera/section이 있으면 무시된다.
- 명시적인 fresh `road_category`가 0/1이면 방지턱을 차단한다. key 누락/invalid는 유효한 0으로
  덮지 않고 마지막 fresh 유효값을 유지한다. 같은 snapshot의 category는 speed보다 먼저 적용되고
  lane-only 변경도 즉시 기존 speed를 재평가한다.
- `navigation_status.off_route=true`이면 7714 SDI가 억제된다.
- `HapticFeedbackWhenSpeedCamera=1`은 현재 checkout에서 Param 등록/설정 UI 외에 읽는 코드가 없어
  방지턱 감속이나 실제 햅틱에 영향을 주지 않는다.
- bump는 가속페달 override 보호 source 목록에 없으므로 감속 중 가속페달을 새로 밟으면 최종 source가
  `gas`로 바뀌고 방지턱 목표보다 높은 속도가 허용될 수 있다.

### 실제 적용과 무관할 수 있는 별도 표시

- 일반 on-road HUD의 별도 `ROUTE` badge는 `carrotMan.navPathVertexCount`를 읽지만 해당 capnp field가
  존재하지 않고 publisher는 `naviPaths` text만 보낸다. 따라서 이 badge 경로는 현재 항상 0으로
  떨어져 표시되지 않는다. 실제 감속 winner의 `route` label은 별도 로직이므로 표시 가능하다.
- speed limit box는 방지턱(type 22)을 제외한 모든 `xSpdType`을 세분하지 않고 `CAM`으로 표시한다.
  section도 이 box에서는 CAM이다. winner source 표시에서는 `section`으로 구분된다.
- 7714 전용 cluster 내비 패널은 raw SDI를 `SDI/SDI 2`, raw section을 `SECTION`, route presence를
  `ROUTE`로 표시한다. 7713은 이 raw `carrotNavi` 패널의 입력이 아니다. 이 패널 표시는 실제
  `desiredSpeed` winner 여부와 무관하다.
- cluster의 raw SDI/section 표시는 전체 off-route를 함께 확인하지 않고, section은 `sectionActive`만
  확인하여 suspended/section off-route도 반영하지 않는다. 따라서 control parser가 감속을 억제해도
  패널에는 SDI 또는 SECTION이 보일 수 있다.

## APK와 지원 범위

이 C3 source arbitration은 수정된 Tmap APK를 요구하지 않으며 기존 Tmap 입력 계약을 보존한다. Naver는
`naver.navigation.v1` envelope가 TCP 7712로 실제 수신될 때만 `naver_v1` source가 된다. 이 문서의
Naver projection 설명은 C3 정책과 synthetic envelope 검증 범위이며, 특정 Naver 6.8.0.5 APK에서 실제
안내·카메라·방지턱 frame이 도착했다는 실차 acceptance 주장은 아니다.

## 검증 근거

- `test_navigation_sources.py`는 sticky owner, exact-session transport loss, lease boundary, terminal
  tombstone, owner/provider 분리와 concurrent accept/select 계약을 다룬다.
- `test_carrot_navi.py`, `test_carrot_navi_control.py`, `test_carrot_navi_serv.py`는
  `roadCategoryValid`, same-frame category-before-speed, lane-only 재평가, missing/explicit category
  구분과 10초 expiry 계약을 다룬다.
- `test_cluster_navi.py`는 service와 cluster가 같은 `V2_ITEM_TTL_S == 10.0`을 사용하는지 단언한다.
- `test_carrot_navi_speed_selection.py`는 selected app authority, same-cycle HDA fallback, APN과
  navigation-owner/deceleration-provider 분리를 다룬다.
- `test_cluster_live.py`와 Web navigation-provider/wire tests는 새 진단 field와 old-log fallback을
  다룬다.
- 이 문서 수정 과정에서는 프로세스 spawn이 불가능하여 위 suite를 다시 실행하지 못했다. 테스트 결과는
  실행 담당자의 실제 출력으로만 보고해야 하며, 문서의 계약 설명을 통과 증거로 간주하면 안 된다.
