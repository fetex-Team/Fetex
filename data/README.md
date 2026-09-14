# data/ — 데이터셋 설명

이 폴더에는 성격이 다른 두 계열의 데이터가 공존한다.

| 폴더 | 계열 | 내용 | 생성 방법 |
|---|---|---|---|
| `generated/` | 합성 | 규격화된 합성 호출·외부 관측 (21일치) | `python -m data.generate` |
| `sim_logs/` | 시뮬레이션 | SUMO 호출 로그 (1행 = 승객 1명 = 호출 1건) | 시뮬레이션 실행 시 자동 저장 |
| `external/` | 실측 | 시간별 날씨 (기온·강수·풍속) | `python scripts/fetch_weather_history.py [시작일] [종료일]` |
| `processed/` | 파생 | Module 2 출력: (H3 셀 × 5분) 피처 테이블, 타겟 y_h1..y_h6 | `python -m module2_preprocessing.pipeline` |
| `eda/` | 파생 | EDA 결과 (H3 res 비교, POI zone 지도, 요약표) | `bash 실행_EDA.command` |

Module 2 파이프라인의 필수 입력 컬럼은 `pickup_datetime, latitude, longitude` 세 개다.
`generated/calls.csv`, `sim_logs/demand_log_*.csv`, 시뮬레이션 결과 `results/*/calls.csv`,
그리고 공개 데이터(뉴욕 택시 등)까지 이 3컬럼만 맞추면 같은 파이프라인에 입력할 수 있다.

## 합성 데이터 규격 (`generated/`)

`generate.py`는 `build_env.py`에서 만든 도로/H3 매핑을 입력으로 호출과 외부 관측을 생성한다. 실제 카카오모빌리티·기상청·교통량 기록이 아니다.

- 호출: `request_id, pickup_datetime, latitude, longitude, h3_index, from_edge, to_edge`
- 외부 관측: `time_bucket, h3_index, temperature, precipitation, traffic_index, event_flag, is_holiday`
- 시간은 시나리오 지역의 naive local time이며 5분 단위. 다른 타임존 자료는 입력 전 통일해야 한다.
- GPS는 도로 대표점이다. 실제 OSM은 투영 좌표를 역변환하고 합성 격자는 지역 bbox로 선형 매핑한다. 도로 하나를 H3 셀 하나로 취급하는 근사이다.
- `synthetic_rate`: 셀당 호출 강도. 날짜/공간별 피크 및 요일/강수/이벤트 가중치를 가진 포아송 과정이다.
- `is_holiday`: 합성 시나리오의 8월 15일 플래그이며 공식 전체 공휴일 달력이 아니다.
- 날짜별 독립 난수 발생기를 사용하며 seed 0도 유효하다. 생성 기간을 바꿔도 같은 날짜의 호출은 같다.
- 학습 생성 자료는 `generated/calls.csv`, `generated/external.csv`로 저장하며 대용량 파일은 Git에서 제외한다.

실제 자료를 넣을 때는 GPS 유효성·타임존·중복 호출 ID를 먼저 검사하고, 전체 시간×셀 범위를 지정해 0수요를 보존한 뒤 외부 자료를 시간/H3로 결합한다. 미래값으로 결측치를 역방향 보간하지 않는다.

## 호출 로그 스키마 (`sim_logs/`)

| 컬럼 | 설명 |
|---|---|
| person_id | SUMO person id (`dyn_pax_N`: 실시간 스폰, `passenger_N`: 정적 승객) |
| request_sec | 시뮬 경과초 기준 호출(등장) 시각 |
| pickup_datetime | `sim_date` + `sim_start_hour` + request_sec → 실제 시각 |
| edge_id / origin_zone / dest_category | 등장 도로, 그 도로의 POI 구역(복수면 `\|`), 목적지 카테고리 |
| latitude, longitude | 위경도 (SUMO geo 변환) |
| pickup_sec / wait_sec / taxi_id | 탑승 시각·대기시간·택시 (못 탔으면 빈칸) |
| status | picked / timeout / unpicked |

## 한계

- 시뮬레이션 로그는 확보한 날짜 수만큼만 요일·공휴일·날씨 효과를 반영한다.
  "지난주 동일 시간대" 피처는 7일 이상의 연속 데이터가 필요하다.
- 실측 외부 데이터의 출처·기간·결합 방식은 `docs/weather_coefficient.md` 참고.
