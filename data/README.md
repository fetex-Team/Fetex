# data/ — 데이터셋 설명

| 폴더 | 내용 | 생성 방법 |
|---|---|---|
| `sim_logs/demand_log_*.csv` | SUMO 시뮬레이션 호출 로그 (1행 = 승객 1명 = 호출 1건) | `python measure_wait_time.py` (또는 `실행_로그생성_학습.command`) → `module2_preprocessing/sim_log_recorder.py`가 저장 |
| `external/weather_*.csv` | 시간별 날씨 (기온·강수·풍속), Open-Meteo Archive | `python scripts/fetch_weather_history.py [시작일] [종료일]` |
| `processed/features.csv` (+`_meta.json`) | Module 2 출력: (H3 셀 × 5분) 피처 테이블, 타겟 y_h1..y_h6 | `python -m module2_preprocessing.pipeline` |
| `eda/` | EDA 결과 (H3 res 비교, POI zone 지도, 요약표) | `bash 실행_EDA.command` |

## 호출 로그 스키마 (`sim_logs`)
| 컬럼 | 설명 |
|---|---|
| person_id | SUMO person id (`dyn_pax_N`: 실시간 스폰, `passenger_N`: 정적 승객) |
| request_sec | 시뮬 경과초 기준 호출(등장) 시각 |
| pickup_datetime | `sim_date` + `sim_start_hour` + request_sec → 실제 시각 |
| edge_id / origin_zone / dest_category | 등장 도로, 그 도로의 POI 구역(복수면 `\|`), 목적지 카테고리 |
| latitude, longitude | 위경도 (SUMO geo 변환) |
| pickup_sec / wait_sec / taxi_id | 탑승 시각·대기시간·택시 (못 탔으면 빈칸) |
| status | picked / timeout / unpicked |

Module 2 파이프라인은 이 중 `pickup_datetime, latitude, longitude`만 사용한다 → 공개 데이터(뉴욕 택시 등)도 이 3컬럼으로 맞추면 그대로 입력 가능.

## 현재 보유 데이터
- `demand_log_20260910_180831_patrol.csv`: 강남역, 2026-09-07(월) 09:00~11:00, 택시 50대 patrol, 호출 2,870건 (탑승 1,235 / 타임아웃 1,620)
- 한계: 1일 × 2시간. 요일·공휴일·날씨 효과와 "지난주 동일 시간대" 피처는 다일 데이터 필요 (`docs/MODULE2_MANUAL.md` T7)
