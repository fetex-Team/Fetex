# 전처리/EDA 파트 변경 사항 (윤세빈, 2026-09-10)

기준: `20250907ver` 복사본. 원본 폴더는 수정하지 않음.

## 1. 시뮬레이션 호출 로그 저장 (신규)
- `module2_preprocessing/sim_log_recorder.py` 추가
- `module1_simulation/run_simulation.py`, `measure_wait_time.py`에 연결
- 시뮬레이션이 끝나면(GUI를 중간에 닫아도) `data/sim_logs/demand_log_*.csv` 생성
- 1행 = 승객 1명: 등장 시각, 실제 시각(pickup_datetime), edge, 출발 구역(origin_zone), 목적지 카테고리, 위경도, 탑승 시각/대기시간/택시, status(picked/timeout/unpicked)
- `config.json`에 `sim_date` 추가 (로그 날짜 → 요일/주말 피처용, 기본 2026-09-07 월요일)

## 2. time_series_prep.py 버그 수정
| 문제 | 기존 | 수정 |
|---|---|---|
| 수요 0인 시간칸 | 행이 없어서 lag_1이 "직전에 호출 있던 칸"을 가리킴 | (셀 × 시간칸) 전체 격자로 채우고 demand=0 |
| rolling | shift만 셀별, rolling은 전체 이어서 계산 → 이전 셀 값 섞임 | 셀 내부에서만 rolling |
| dropna | 모든 컬럼 기준 | lag/rolling 컬럼 기준 |
| 피처 | - | `minute_of_day` 추가 |

- `time_based_split()` 추가: 마지막 test_size 비율의 **시간칸**을 test로 분할

## 3. train.py
- `data/sim_logs`의 최신 로그를 자동 사용 (`python train.py 경로.csv`로 지정 가능), 없으면 기존 가짜 데이터로 폴백 + 경고
- train/test 분할: 행 기준 → 시간 기준 (기존엔 "마지막 셀들"이 test였음)
- GridSearch CV: KFold → TimeSeriesSplit

## 사용 순서
1. `python measure_wait_time.py` (또는 `python main.py`) → 로그 CSV 생성
2. `python train.py` → 로그로 학습

## 남은 이슈
- 시뮬레이션 9~11시(2시간) / 10분 = 12칸 → lag 6·rolling 6 결측 제거 후 셀당 6행, test는 시간칸 1~2개뿐. 시뮬레이션 시간을 늘리거나 freq를 5분으로 줄이는 것 검토 필요
- `external_data_merge.py`의 기온/강수는 여전히 랜덤 → 모델에 노이즈만 추가됨
- `main.py`는 아직 가짜 데이터 사용 (파이프라인 데모용)
- 기존 `saved_models/`는 이전 피처 기준이라 재학습 필요

## 2026-09-10 실행 결과 (첫 실제 로그)
- `data/sim_logs/demand_log_20260910_180831_patrol.csv` — 강남역, 09~11시, 택시 50대, patrol
- 호출 2,870건 / 탑승 1,235 / 타임아웃 1,620(56%) / 평균 대기 302초
- 10분별 호출: 09:00~09:50 300→474건 상승(출근), 10:00 이후 ~85건 (company_end_hour=10 효과)
- origin_zone 겹침 심함: 한 edge가 company|restaurant|subway|bus_stop 3~4개 카테고리 동시 소속 (restaurant edge 157/399, 매핑 반경 80m)
- H3 res 8 → 셀 7개, 학습 데이터 42행. res 9 이상 필요
- train.py 세그폴트(macOS torch+xgboost OpenMP 충돌) → KMP_DUPLICATE_LIB_OK / OMP_NUM_THREADS=1 / xgb n_jobs=1로 해결
- evaluate.py MAPE가 수요 0 칸에서 폭발(359,284%) → 0 제외 또는 sMAPE 필요 (평가 담당 공유)
- 실행: `python module1_simulation/build_env.py && python measure_wait_time.py`(시뮬) → `python scripts/train_dispatch_model.py`(학습)

## 2026-09-10 H3 resolution 결정: 8 → 9
- 비교 스크립트: `eda/h3_resolution_compare.py` (실행: `python eda/h3_resolution_compare.py`), 결과: `data/eda/h3_resolution_summary.csv`, `h3_resolution_compare.png`
- res 7: 셀 3개, 최다 셀 86% → 공간 예측 불가 / res 8: 셀 7개, 최다 셀 35%, 학습 42행 → too coarse
- res 10: 셀 70개, 0인 칸 37%, 중앙값 1건/10분, lag-1 r=0.17 → too fine(노이즈)
- res 9: 셀 22개(변 201m), 0인 칸 20%, 학습 132행, 출근 핫스팟 5~6셀 식별됨 → 채택
- 변경 파일: config.json, config_loader.py 기본값, spatial_indexing.py 주석
- ※ 공유 config 변경이므로 팀 공지 필요. 최종 로그(시뮬 시간 확정 후) 나오면 표만 다시 뽑아 확인

## 2026-09-10 POI zone 검증 결과
- 스크립트: `eda/poi_zone_map.py` (실행: `python eda/poi_zone_map.py`) → `data/eda/poi_zone_map.html`(Leaflet, 카테고리 레이어 토글), `poi_zone_map.png`, `poi_zone_summary.csv`
- OSM POI → edge: 학교 5→4, 주택 70→28, 회사 123→51, 음식점 525→157, 지하철 17→16, 버스 46→28
- 매핑 반경 40/80/120/200m 모두 edge 수 동일 → 반경은 원인 아님, 80m 유지. 80m 밖 누락 POI는 주택 5개뿐
- 겹침: zone edge 190/404 중 2개 이상 카테고리 63개, 4개 카테고리 5개(강남대로). 원인은 큰길 하나에 POI가 몰리는 것 + zone 내 edge 균등 랜덤 선택
- 주택: 블록 안쪽 골목이 도로망에 없어 주택 POI가 큰길로 매핑됨 (주택 전용 edge 4/28). 주거 출발 승객이 강남대로에서 스폰되는 문제
- 음식점 전용 edge 102개로 zone 절반 차지 → restaurant_civilian 스폰이 사실상 전역
- 제안(팀 결정 필요, 1·2번 공유 코드): get_zones가 {edge: POI수} 반환 + spawn 시 POI수 가중 선택 / 주택 골목 도로 보존 여부 확인

## 2026-09-10 (2차) 명세 기준 Module 2 보강 — docs/MODULE2_MANUAL.md 참고
- config: freq 10min→**5min**, max_lag 6→12(1h), rolling 6/12, forecast_horizons=6 (명세 M3: t+1~t+6 5분) ※ 공유 config 변경
- spatial_indexing.py: 중복 좌표 캐시(np.unique)+chunk 처리, `--bench N` 벤치마크, Geohash 병행, res 9 근거 주석
- time_features.py (신규): 연·월·일·시·분·요일·주말·**공휴일(holidays KR)**·계절·time_slot·순환 인코딩
- time_series_prep.py: rolling_mean_1h/rolling_std_1h, **same_time_last_week**(+has_last_week), diff_1, **y_h1..y_h6 타겟**, val 분할
- external_data_merge.py (재작성): np.random 삭제, 시간별 날씨 → 5분 칸 **merge_asof(backward)**, 보간/결측 플래그, fallback
- scripts/fetch_weather_history.py (신규): Open-Meteo Archive 시간별 날씨 수집
- pipeline.py (신규): 단일 진입점 + CLI, 다일 로그 glob 지원 / train.py가 pipeline 사용 (가짜 데이터 폴백 제거)
- tests/test_module2.py (신규): 최소 완료 조건 42개 테스트 (h3 없는 환경은 mock)
- eda/eda_utils.py + notebooks/01_EDA_and_Spatial.ipynb (신규): 개요/시간/공간/POI/ADF/ACF/ablation/외부상관 8섹션
- README Module 2 섹션, data/README.md, REPORT.md EDA 초안, requirements(holidays/statsmodels/jupyter)
- 실행: `python tests/test_module2.py`(테스트), `python -m module2_preprocessing.spatial_indexing --bench 100000`(벤치), `python -m module2_preprocessing.pipeline`(CLI), EDA는 위 두 스크립트 + `jupyter nbconvert --execute notebooks/01_EDA_and_Spatial.ipynb`
