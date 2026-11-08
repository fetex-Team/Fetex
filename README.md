# FeTex — 강남역 택시 수요 예측 · 동적 배차 디지털 트윈

과제 12 「카카오모빌리티 택시 수요 예측 및 동적 배차」 (FeTex 6팀)

SUMO로 만든 **강남역 1km² 디지털 트윈**에 **실측 택시 승차 통계(TIMS 2025)·서울시 2025 통계·실측 날씨로 보정한 호출**을 흘려 보내고,
H3 격자별 30분 앞 수요를 XGBoost로 예측해 빈 택시를 미리 보내는 전략(시간표 선배치 / 강화학습 재배치)을
**같은 손님 목록**에서 공정하게 비교한다. 결과는 Unity(카카오 3D 에셋)에서 차선을 따라 재생한다.

```
OSM 강남역 지도 ─ build_env ─▶ SUMO 도로망 + POI 구역 + H3(res 8) 칸
TIMS 500m 격자 + 서울 2025 통계 + 실측 날씨 ─ demand_model ─▶ 하루 호출 스케줄(시드 고정, 외생 수요)
            │                                  └─▶ 학습용 8주 호출 로그 ─ Module 2 ─▶ XGBoost 30분 예측
            ▼
measure_wait_time: SUMO 실행 · 헝가리안 배차 · 전략(patrol / prepositioned / rl_prepositioned)
            ▼
export_unity_replay ─▶ map.json + 전략별 재생 파일(v2, .json.gz) ─▶ Unity FetexReplayPlayer + ReplayMotion
```

## 과제 요구사항 대응
| 요구사항 | 구현 | 위치 |
|---|---|---|
| 디지털 트윈(실제 지역) | 강남역 OSM → SUMO, 회전·차선·신호, POI 구역 | `module1_simulation/`, `real_map_fetch.py`, `poi_extractor.py` |
| 수요 데이터 | **TIMS 2025 격자별 승차 실측 + 서울시 2025 시간대·강남구 월별 통계 + 실측 시간별 날씨, 카카오 T 몫 0.77**(가정값 모드도 유지) | `demand_model.py`, `weather_history.py`, `data/external/` |
| 공간 인덱싱 | H3 res 8(실측 500m 해상도와 일치) + Geohash | `module2_preprocessing/spatial_indexing.py` |
| 시계열 전처리·외부 데이터 | 10분 칸, lag/이동평균/지난주 동일 칸, 날씨·공휴일 병합, 누수 방지 | `module2_preprocessing/` |
| 수요 예측 | MultiOutput XGBoost(t+1~t+6), 기준선 대비 평가 | `scripts/train_dispatch_model.py`, `notebooks/02_Model_Experiments.ipynb` |
| 동적 배차 | 헝가리안 매칭(비용 = 다익스트라 도로 이동시간) + 시간표 선배치/RL 재배치 + 할증 계산 | `module4_dispatch/`, `module4_dispatch/network_cost.py` |
| 성능 비교 | 같은 손님 목록(수요 지문)에서 7일 반복 비교 + 통계 검정(신뢰구간·부호 검정), 결과 재현성 보장 | `tools/repeat_experiments.py`, `tools/check_determinism.py`, `export_unity_replay.py` |
| 시각화 | Unity 3D 재생(차선 경로 보간), 대시보드 | Unity 브랜치 `KJS-UNITY` |

## 빠른 시작 (Windows PowerShell 기준)
```powershell
python -m venv venv
venv\Scripts\activate
$env:PYTHONUTF8="1"; chcp 65001; [Console]::OutputEncoding=[System.Text.Encoding]::UTF8   # 한글 출력 깨짐 방지
pip install -r requirements.txt          # eclipse-sumo 포함 (SUMO 별도 설치 불필요)

python module1_simulation/build_env.py   # 1) 지도·POI·차량 생성 (같은 범위 OSM은 저장본 재사용)
python main.py                           # 2) SUMO GUI로 보기
python config_gui.py                     #    또는 설정 화면에서 실행·A/B 비교
python measure_wait_time.py              # 3) 화면 없이 한 번 측정 (대기시간·서비스율·추정 매출)
```

### 주요 명령
| 목적 | 명령 |
|---|---|
| 전략 3개 공정 비교 + Unity 재생 파일 | `python export_unity_replay.py --stride 5 --unity-dir <Unity>/Assets/StreamingAssets` |
| 택시 대수 산정 | `python tools/fleet_sizing.py --taxis 50 70 --strategy prepositioned` |
| 실측 날씨 받기(네트워크 필요) | `python scripts/fetch_weather_history.py 2026-07-01 2026-10-27` |
| 학습용 8주 호출 생성(SUMO 불필요, 약 10초) | `python scripts/generate_demand_logs.py` |
| 예측 모델 학습 | `python scripts/train_dispatch_model.py "data/real_demand_logs/demand_log_*.csv"` |
| RL용 카테고리 예측 | `python scripts/convert_v2_to_category.py` · `python module3_prediction/train_category_demand.py --logs "data/real_demand_logs/demand_log_*.csv"` |
| RL 재배치 정책 학습(약 3시간) | `python module4_dispatch/rl_train.py` |
| 7일 x 3전략 반복 실험 + 통계 | `python tools/repeat_experiments.py --jobs 2` (`--scenario full_demand` / `network_dispatch` / `network_dispatch_full` / `external_away`) |
| 결과 재현성 검사 | `python tools/check_determinism.py --hours 1` |
| 차량 궤적 검사 | `python tools/verify_replay_motion.py --map results/unity_replay/map.json --replay results/unity_replay/patrol.json.gz` |
| 노트북 재생성(실행 결과 포함) | `python notebooks/build_notebooks.py` |
| 테스트 | `python tests/test_demand_model.py` · `python tests/test_weather_history.py` · `python tests/test_network_cost.py` · `python tests/test_replay_motion.py` · `python tests/test_module2.py` · `python tools/verify_region_fallback.py` |

## 주요 설정 (`config.json`)
| 키 | 기본 | 의미 |
|---|---|---|
| `demand_source` | `real` | `real` = TIMS 실측 보정 외생 수요 / `synthetic` = 예전 카테고리 가정 모델 |
| `real_demand_scale` | 0.77 | 영역 실측 호출량 중 카카오 T 몫 (앱 호출 80% × 카카오 점유율 96%) |
| `use_historical_weather` | true | 그날의 실측 시간별 강수로 호출량 조정 (1mm당 +1.99%) |
| `taxi_external_away` | false | true면 밖으로 나간 택시가 늦게 돌아오는 민감도 시나리오 |
| `real_internal_trip_km` | 1.0 | 이보다 짧은 운행만 영역 안 하차(나머지는 경계 도로로 나감) |
| `h3_resolution` | 8 | 예측·재배치 격자 (근거: `docs/real_data_calibration.md` 4절) |
| `dispatch_cost` | `network` | 헝가리안 비용: 도로망 최단 이동시간(다익스트라) / `euclidean` = 예전 직선 거리 |
| `num_taxis` | 61 | 택시 대수 (국토부 통행량·영업통계로 추정, `tools/estimate_taxi_supply.py`) |
| `taxi_strategy` | `prepositioned` | `patrol` / `prepositioned` / `rl_prepositioned` |
| `taxi_dispatch_algorithm` | `hungarian` | 현재 호출 매칭 방식 |
| `sim_date`, `run_date_mode` | 2026-09-07, `alternate` | 비교 실험은 `sim_date`로 날짜 고정 |

## 결과 요약 — 근거 기반 디지털 트윈 (7일, 택시 61대, 같은 손님 목록)

**결론: 배차 방법 간 차이는 없고, 결과는 택시 수가 좌우한다.** 전체 표는 [`docs/RESULTS_SUMMARY.md`](docs/RESULTS_SUMMARY.md).

| 전략 | 서비스율 | 평균 대기 |
|---|---:|---:|
| 순찰 `patrol` | 95.7% | 153초 |
| 예측 재배치 `forecast` | 95.8% | 154초 |
| 시간표 선배치 `prepositioned` | 95.5% | 166초 |
| RL `rl_prepositioned` | 95.7% | 180초 |

- 도로 속도 실측 대비 2.1배 → 0.93배 (버스차로·5030·경찰청 신호·TOPIS 정체)
- 근거 없던 값 교체: 손님 위치(생활인구 250m), 택시 61대, 포기 시간 600초, 구역 안 하차 5.4%. 설정 42개 근거 대장
- 민감도: 택시 61→50대 −21%p, 나간 택시 늦게 복귀 −22%p, 손님 쪽 가정 ±2.3%p

자세한 수치·한계는 [`REPORT.md`](REPORT.md).

## 문서
- [`docs/RESULTS_SUMMARY.md`](docs/RESULTS_SUMMARY.md) — 결과 요약표 (항목·결과·근거·원자료)
- [`REPORT.md`](REPORT.md) — 통합 보고서
- [`docs/design/`](docs/design/README.md) — 설계 결정 기록 (무엇을 왜 바꿨는지, 근거 수치)
- [`docs/real_data_calibration.md`](docs/real_data_calibration.md) — 실측 수요 보정 방법·검증
- [`docs/replay_motion.md`](docs/replay_motion.md) — Unity 차량 회전 궤적 문제와 수정
- [`docs/module1_map_verification.md`](docs/module1_map_verification.md) — 지도·외부데이터 검증 (1번)
- [`docs/weather_coefficient.md`](docs/weather_coefficient.md) — 날씨-수요 실측 계수
- [`docs/pipeline_flow.md`](docs/pipeline_flow.md) — 실행 흐름
- [`data/external/README.md`](data/external/README.md) — 외부 데이터 출처
- [`SETUP_GUIDE.md`](SETUP_GUIDE.md) — 설치 안내

## 폴더 구조 (주요)
```
config.json / config_loader.py / config_gui.py     설정
demand_model.py / passenger_spawn_manager.py       수요(실측 보정 스케줄 / 가정 모델)
measure_wait_time.py / export_unity_replay.py      실행·측정 / Unity 내보내기
module1_simulation/  module2_preprocessing/  module3_prediction/  module4_dispatch/
scripts/   tools/   tests/   notebooks/   eda/   docs/
data/external/        실측 데이터 (TIMS, 서울시 2025 통계, 실측 날씨)
data/real_demand_logs/ 학습용 8주 호출 (scripts/generate_demand_logs.py로 재생성 가능)
saved_models/         demand_v2.joblib, category_demand_*.pkl, (기존) xgboost/cnn
results/              repeat/(반복 실험), unity_replay/, fleet_sizing/, motion_eval/, ab_results/
```

## 역할
FeTex 6팀. 역할 분담과 담당 범위는 팀 문서를 따른다 (지도·외부데이터·실측 수요 보정·재생 궤적: 1번 이동규).
