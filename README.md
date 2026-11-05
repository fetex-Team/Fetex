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
| 디지털 트윈(실제 지역) | 강남역 OSM → SUMO, 회전·차선·신호, POI 구역 | `module1_simulation/`, `module1_simulation/real_map_fetch.py`, `module2_preprocessing/poi_extractor.py` |
| 수요 데이터 | **TIMS 2025 격자별 승차 실측 + 서울시 2025 시간대·강남구 월별 통계 + 실측 시간별 날씨, 카카오 T 몫 0.77**(가정값 모드도 유지) | `module1_simulation/demand_model.py`, `module1_simulation/weather_history.py`, `data/external/` |
| 공간 인덱싱 | H3 res 8(실측 500m 해상도와 일치) + Geohash | `module2_preprocessing/spatial_indexing.py` |
| 시계열 전처리·외부 데이터 | 10분 칸, lag/이동평균/지난주 동일 칸, 날씨·공휴일 병합, 누수 방지 | `module2_preprocessing/` |
| 수요 예측 | MultiOutput XGBoost(t+1~t+6, 시뮬레이션용 demand_v2) vs CNN-LSTM, 기준선 대비 평가 | `module3_prediction/train.py`, `notebooks/02_Model_Experiments.ipynb` |
| 동적 배차 | 헝가리안 매칭(비용 = 다익스트라 도로 이동시간) + 시간표 선배치/RL 재배치 + 할증 계산 | `module4_dispatch/`, `module4_dispatch/network_cost.py` |
| 성능 비교 | 같은 손님 목록(수요 지문)에서 7일 반복 비교 + 통계 검정(신뢰구간·부호 검정), 결과 재현성 보장 | `tools/repeat_experiments.py`, `tools/check_determinism.py`, `tools/export_unity_replay.py` |
| 시각화 | Unity 3D 재생(차선 경로 보간), 대시보드 | Unity 브랜치 `KJS-UNITY` |

## 빠른 시작 (Windows PowerShell 기준)
```powershell
python -m venv venv
venv\Scripts\activate
$env:PYTHONUTF8="1"; chcp 65001; [Console]::OutputEncoding=[System.Text.Encoding]::UTF8   # 한글 출력 깨짐 방지
pip install -r requirements.txt          # eclipse-sumo 포함 (SUMO 별도 설치 불필요)

python config_gui.py                     # 1) 설정 화면에서 값 고르고 [▶ 플레이]·학습·A/B 실행
python main.py play                      #    또는 터미널에서: 지도 생성 → SUMO GUI로 보기
python measure_wait_time.py              # 3) 화면 없이 한 번 측정 (대기시간·서비스율·추정 매출)
```

### 주요 명령
| 목적 | 명령 |
|---|---|
| 지도 생성 + SUMO GUI 시뮬레이션 | `python main.py play` (`--config 설정.json`, 기존 지도로 바로: `--no-build`) |
| 학습 전체(지도 → 로그 → 수요예측 → v2 변환 → RL) | `python main.py train` |
| A/B 비교 · Unity 재생 · 로그 리포트 | `python main.py compare '<조합 JSON>'` · `python main.py unity --stride 5` · `python main.py analyze --logs <CSV...>` |
| 전략 3개 공정 비교 + Unity 재생 파일 | `python tools/export_unity_replay.py --stride 5 --unity-dir <Unity>/Assets/StreamingAssets` |
| 택시 대수 산정 | `python tools/fleet_sizing.py --taxis 50 70 --strategy prepositioned` |
| 실측 날씨 받기(네트워크 필요) | `python scripts/fetch_weather_history.py 2026-07-01 2026-10-27` |
| 학습용 8주 호출 생성(SUMO 불필요, 약 10초) | `python scripts/generate_demand_logs.py` |
| 수요 예측 학습(demand_v2 + CNN-LSTM 비교) | `python module3_prediction/train.py --logs "data/real_demand_logs/demand_log_*.csv"` → `saved_models/demand_v2.joblib`(시뮬레이션용), `results/module3/<freq>/`(지표·그래프). 5분 단위: `--freq 5min`, 비교표: `python module3_prediction/evaluate.py --compare-freq 5min 10min` |
| RL용 카테고리 예측 | `python module3_prediction/train_category_demand.py --logs "data/real_demand_logs/demand_log_*.csv"` |
| RL 재배치 정책 학습(약 3시간) | `python module4_dispatch/rl_train.py` |
| 7일 x 3전략 반복 실험 + 통계 | `python tools/repeat_experiments.py --jobs 2` (`--scenario full_demand` / `network_dispatch` / `network_dispatch_full` / `external_away`) |
| 결과 재현성 검사 | `python tools/check_determinism.py --hours 1` |
| 차량 궤적 검사 | `python tools/verify_replay_motion.py --map results/unity_replay/map.json --replay results/unity_replay/patrol.json.gz` |
| 노트북 재생성(실행 결과 포함) | `python notebooks/build_notebooks.py` |
| 테스트 | `python tests/test_demand_model.py` · `python tests/test_weather_history.py` · `python tests/test_network_cost.py` · `python tests/test_replay_motion.py` · `python tests/test_module2.py` · `python tools/verify_region_fallback.py` |

## 설정 GUI와 프리셋
- `python config_gui.py` — **기본** 탭(프리셋 버튼 + 자주 쓰는 값)과 **세부 설정** 탭(섹션별 전체 설정, 항목에 마우스를 올리면 설명).
- 실행 버튼(플레이·학습·A/B·Unity)은 지금 화면 값을 `results/run_configs/<종류>_<시각>.json`에 저장하고 `python main.py <명령> --config <그 파일>`을 새 콘솔 창에서 실행한다. `config.json`은 그대로.
  진행 단계는 GUI 아래 상태 줄에, 자세한 로그는 콘솔 창에 나오고, 학습이 끝나면 완료 팝업이 뜬다 (`results/run_status/`의 진행 기록을 GUI가 1초마다 읽음).
- **[적용하기]** 를 눌러야 내 설정 `config.json`이 바뀐다 (개인 파일, git 제외).
- 세부 설정의 **현재 화면을 프리셋으로 저장** → `presets/user/`(개인 프리셋, 지금은 git에 올림)에 저장되고 기본 탭 버튼(★)에 나타난다.
- 팀 기본값을 바꾸려면 `presets/default/기본.json`을 고쳐 커밋한다.

## 주요 설정
| 키 | 기본 | 의미 |
|---|---|---|
| `demand_source` | `real` | `real` = TIMS 실측 보정 외생 수요 / `synthetic` = 예전 카테고리 가정 모델 |
| `real_demand_scale` | 0.77 | 영역 실측 호출량 중 카카오 T 몫 (앱 호출 80% × 카카오 점유율 96%) |
| `use_historical_weather` | true | 그날의 실측 시간별 강수로 호출량 조정 (1mm당 +1.99%) |
| `taxi_external_away` | false | true면 밖으로 나간 택시가 늦게 돌아오는 민감도 시나리오 |
| `real_internal_trip_km` | 1.0 | 이보다 짧은 운행만 영역 안 하차(나머지는 경계 도로로 나감) |
| `h3_resolution` | 8 | 예측·재배치 격자 (근거: `docs/real_data_calibration.md` 4절) |
| `dispatch_cost` | `network` | 헝가리안 비용: 도로망 최단 이동시간(다익스트라) / `euclidean` = 예전 직선 거리 |
| `num_taxis` | 50 | 택시 대수 (카카오 몫 수요에서 서비스율 99% 이상) |
| `taxi_strategy` | `prepositioned` | `patrol` / `prepositioned` / `rl_prepositioned` |
| `taxi_dispatch_algorithm` | `hungarian` | 현재 호출 매칭 방식 |
| `sim_date`, `run_date_mode` | 2026-09-07, `alternate` | 비교 실험은 `sim_date`로 날짜 고정 |

## 결과 요약 (7일 반복, 9~23시, 택시 50대 — 날짜마다 세 전략 같은 손님 목록)
| 전략 | 평균 대기 (카카오 몫)<br>직선 거리 → **도로 이동시간** | 타임아웃/일 (손님 100%)<br>직선 거리 → **도로 이동시간** |
|---|---|---|
| **patrol** | 80.6초 → **60.1초** | 306 → **266명** |
| prepositioned | 86.5초 → 69.6초 | 306 → 315명 |
| rl_prepositioned | 91.2초 → 75.5초 | 316 → 330명 |
| **forecast** (예측 재배치, 새 전략) | — → **59.4초** | — → 294명 |

- **헝가리안 매칭 비용을 직선 거리 → 다익스트라 도로 이동시간으로 바꿔 세 전략 모두 평균 대기 10~20초 단축** (`"dispatch_cost": "network"`, 기본값)
- 가장 좋은 조합: **순찰 + 헝가리안(도로 이동시간)**. 미리 옮겨 두는 전략(선배치·RL)은 7일 모두 평균 대기가 더 길다
- 수요 예측(H3 res 8, 30분 앞) WAPE **26.4%** — 포아송 잡음 하한 24.8%, 나이브 36.2%
- Unity 재생 회전 구간 도로 이탈 **56.0% → 0.6%**, 재생 파일 48MB → 8MB(gzip)

자세한 수치·한계는 [`REPORT.md`](REPORT.md).

## 문서
- [`REPORT.md`](REPORT.md) — 통합 보고서
- [`docs/design/`](docs/design/README.md) — 설계 결정 기록 (무엇을 왜 바꿨는지, 근거 수치)
- [`docs/real_data_calibration.md`](docs/real_data_calibration.md) — 실측 수요 보정 방법·검증
- [`docs/replay_motion.md`](docs/replay_motion.md) — Unity 차량 회전 궤적 문제와 수정
- [`docs/module1_map_verification.md`](docs/module1_map_verification.md) — 지도·외부데이터 검증 (1번)
- [`docs/weather_coefficient.md`](docs/weather_coefficient.md) — 날씨-수요 실측 계수
- [`docs/pipeline_flow.md`](docs/pipeline_flow.md) — 실행 흐름
- [`data/external/README.md`](data/external/README.md) — 외부 데이터 출처
- [`docs/setup.md`](docs/setup.md) — 설치 안내

## 폴더 구조 (주요)
```
main.py                                 실행 진입점 (play / train / compare / unity / analyze — 실행 순서는 여기)
config_gui.py                           설정 GUI (값 편집·프리셋·저장, 실행은 main.py에 맡기고 진행 상황만 표시)
config_loader.py                        설정 로더 (DEFAULT_CONFIG = presets/default/기본.json을 읽은 값)
presets/default/                        팀 공유 프리셋 (기본.json = 팀 기본 설정, 시나리오 4개) — git 포함
presets/user/                           개인 프리셋 — git 포함(당분간)
config.json                             내 설정 — git 제외. 없으면 기본.json으로 시작
measure_wait_time.py                    공용 실행·측정 루프 (여러 도구가 import)
module1_simulation/    디지털 트윈: build_env·run_simulation, 택시/승객 매니저, 수요 모델(demand_model),
                       지도·날씨·공휴일(geo_lookup, real_map_fetch, weather_*, holiday_utils), 재현성·TraCI 구독 도우미
module2_preprocessing/ 전처리: H3 인덱싱, 시계열 피처(43개), 외부 데이터 병합, POI 추출, 학습 로그 선택
module3_prediction/    예측 모델: 학습 train.py(XGBoost vs CNN-LSTM → demand_v2)·train_category_demand.py, 평가 evaluate.py,
                       실시간 예측 h3_predictor.py(H3 격자, forecast 재배치용)·category_predictor.py(장소 종류별, RL용)
module4_dispatch/      배차: 헝가리안·커스텀 배차, 예측 재배치(forecast — Module 3 예측기 사용), RL 선배치, 할증
scripts/               데이터 생성·학습·A/B 비교(multi_factor_compare + parallel_dispatch_worker)
tools/                 실험·검증·Unity 내보내기·수요 로그 분석기·지역 검색 위젯
tests/   notebooks/   eda/   docs/
data/external/         실측 데이터 (TIMS, 서울시 2025 통계, 실측 날씨)
data/real_demand_logs/ 학습용 8주 호출 (scripts/generate_demand_logs.py로 재생성 가능)
saved_models/          demand_v2.joblib, category_demand_*.pkl, xgboost/cnn (Module 3)
results/               repeat/(반복 실험), unity_replay/, fleet_sizing/, motion_eval/, ab_results/, runtime_validation/
```

## 역할
FeTex 6팀. 역할 분담과 담당 범위는 팀 문서를 따른다 (지도·외부데이터·실측 수요 보정·재생 궤적: 1번 이동규).
