# AI Mobility — 택시 수요 예측 · 동적 배차 디지털 트윈

SUMO로 만든 실제 도로망(OSM, 기본 강남역) 위에서 택시 수요를 만들고, **수요 예측 → 택시 선배치 → 배차**까지 돌려
승객 평균 대기시간을 비교·개선하는 프로젝트입니다. (PBL 12번, 카카오모빌리티 DT)

```
Module1 시뮬레이션 ──▶ Module2 전처리 ──▶ Module3 수요 예측 ──▶ Module4 배차·재배치
 (SUMO 디지털 트윈)    (호출 로그→시계열)    (XGBoost / CNN-LSTM)   (Hungarian · Greedy · RL)
        ▲                                                              │
        └────────────── A/B 비교 · 수요 로그 분석기 ◀───────────────────┘
```

## 1. 폴더 구조

| 경로 | 역할 |
|---|---|
| `config.json`, `config_loader.py`, `config_gui.py` | 설정의 단일 원본 / GUI (지역, 택시 수, 전략, 배차 알고리즘, 평일·공휴일, A/B 비교) |
| `main.py` | SUMO GUI로 시뮬레이션 실행 |
| `measure_wait_time.py` | headless 측정 실행기 (대기시간 계산, 수요 로그 저장). A/B의 공용 실행기 |
| `multi_factor_compare.py`, `parallel_dispatch_*.py` | 조합별 병렬 A/B 비교 (`ab_results/`에 순위 저장) |
| `module1_simulation/` | `build_env.py`(도로망·승객·택시 생성), `run_simulation.py`(GUI 루프) |
| `module2_preprocessing/` | H3 공간 인덱싱, 시간·날씨 피처, 5분 시계열, `sim_log_recorder.py`(수요 로그 기록) |
| `module3_prediction/` | 수요 예측 모델(`models.py`), 카테고리별 학습 |
| `module4_dispatch/` | 배차(`hungarian_dispatcher.py` 등), RL 환경·학습·추론, 서지 프라이싱 |
| `taxi_manager.py` | 택시 대수 유지, 순찰·선배치 재경로 |
| `passenger_spawn_manager.py`, `passenger_manager.py` | 시간대별 승객 생성·소멸, 타임아웃 처리 |
| `step_snapshot.py` | 스텝당 한 번 가져오는 TraCI 조회 헬퍼 |
| `analyzer_server.py`, `demand_analyzer.html` | 수요 로그 분석기 (출발 지도, 순위, 시간대별) |
| `export_unity_replay.py` | SUMO 상태를 Unity 재생용 JSON으로 저장 |
| `data/sim_logs/` | 시뮬레이션이 남기는 호출 로그 `demand_log_*.csv` (학습·분석 입력) |
| `saved_models/`, `module4_dispatch/rl_reposition_model.zip` | 학습된 예측 모델 / RL 정책 |
| `scripts/`, `eda/`, `tools/`, `tests/`, `notebooks/`, `presets/` | 학습 데이터 생성, EDA, 검증 도구, 테스트, 노트북, GUI 설정 프리셋 |

## 2. 설치

- Python 3.12, **SUMO** 설치 후 환경변수 `SUMO_HOME` 설정 (TraCI/sumolib 사용)
- 패키지: `pip install -r requirements.txt` (자세한 순서는 `SETUP_GUIDE.md` 참고)
- 인터넷 연결: 지역 좌표(Nominatim), OSM 도로망, 현재 날씨(Open-Meteo)를 조회합니다. 실패하면 프리셋으로 대체됩니다.

## 3. 실행 방법

```bash
python config_gui.py                 # 1) 설정 (지역·택시 수·전략·배차 알고리즘·날짜)
python main.py                       # 2) SUMO GUI로 한 번 돌려보기
python measure_wait_time.py          # 3) headless 측정 (수요 로그 저장)
```

**A/B 비교**: `config_gui.py`의 "🧪 A/B 비교하기"에서 조합을 고르면 `multi_factor_compare.py`가 조합마다 새 창을 띄워 병렬로 돌립니다.
직접 실행하려면 조합을 JSON으로 넘깁니다.

```bash
python multi_factor_compare.py '[{"taxi_strategy":"prepositioned","taxi_dispatch_algorithm":"hungarian"},
                                 {"taxi_strategy":"rl_prepositioned","taxi_dispatch_algorithm":"hungarian"}]'
```

**학습**

```bash
python train.py                                          # 수요 예측 (XGBoost, CNN-LSTM) -> saved_models/
python module3_prediction/train_category_demand.py       # 카테고리별 수요 모델 (RL 입력용)
python module4_dispatch/rl_train.py                      # RL 선배치 정책 (PPO) -> rl_reposition_model.zip
```

**분석기**

```bash
python analyzer_server.py        # 브라우저가 열리고 data/sim_logs 로그를 체크박스로 골라 분석
```

**Unity 재생 JSON**: `python export_unity_replay.py --output results/unity_replay`

## 4. 택시 전략 × 배차 알고리즘

두 축은 **독립**이라 어떤 조합이든 가능합니다.

| 택시 전략 (`taxi_strategy`) | 설명 |
|---|---|
| `patrol` | 빈 택시가 무작위 목적지를 순찰 |
| `prepositioned` | 시간대별 혼잡 구역(학교·회사·음식점·교통 등)으로 규칙 기반 선배치 |
| `rl_prepositioned` | 학습된 RL(PPO) 정책이 구역별로 유휴 택시를 배분 (1분 주기) |

| 배차 알고리즘 (`taxi_dispatch_algorithm`) | 설명 |
|---|---|
| `greedy`, `routeExtension` | SUMO 내장 (`routeExtension`은 한 택시에 승객을 여러 명 엮을 수 있음) |
| `hungarian` | 파이썬이 `traci` 모드로 직접 배정 (scipy 최적 할당) |
| `custom_greedy` 등 수제 배차기 | 빈 택시에만 1:1로 배정하도록 직접 구현한 그리디 계열 |

## 5. 평가 지표와 로그 규칙

- **평균 대기(페널티 포함)** = (탑승자 대기시간 합 + 타임아웃 건수 × 페널티) ÷ (탑승 + 타임아웃), 페널티 기본 500초
- 분석기의 **"페널티 초과 탑승은 타임아웃으로 계산"** 옵션을 켜면, 배차는 받았지만 500초를 넘겨 탄 승객도 타임아웃으로 세서 모든 알고리즘에 같은 상한이 적용됩니다.
- 로그 파일명: `demand_log_{지역}{전략}_{알고리즘}[공휴일]{yymmdd}.csv`
  같은 이름이면 시간대별로 병합하고 기존본은 `data/sim_logs/backup/`에 `.bak`으로 남깁니다.
- 공정한 비교를 위해 **같은 날짜의 로그끼리** 비교하세요. A/B는 한 번 정한 날짜를 모든 조합에 똑같이 적용합니다.
  (평일/공휴일 모드는 최근 90일 중 해당 종류의 날짜를 무작위로 하나 뽑습니다.)

### 현재 결과 (2026-10-09 기준, 택시 50대, 공휴일·주말 1일, 조합당 1회 실행, 호출 24,446건)

| 조합 (전략 + 알고리즘) | 평균 대기 | 타임아웃률 |
|---|---|---|
| rl_prepositioned + hungarian | 147.5초 | 6.8% |
| prepositioned + hungarian | 166.5초 | 10.1% |
| rl_prepositioned + custom_greedy | 178.0초 | 12.3% |
| prepositioned + custom_greedy | 181.7초 | 13.9% |

하루치·1회 결과라 순위가 확정은 아닙니다. 평일 로그와 반복 실행으로 보강할 예정입니다.

## 6. 성능 구조 (TraCI 조회 통합)

승객이 늘수록 느려지던 원인이 스텝마다 여러 모듈이 같은 전체 조회를 반복하는 것이었습니다.
메인 루프 맨 위에서 한 번만 조회해 하위 모듈에 내려줍니다.

```python
current_pids = set(traci.person.getIDList())     # 승객
current_vids = set(traci.vehicle.getIDList())    # 차량
idle_taxis   = fetch_idle_taxis()                # 빈 택시 (배차기/RL/기록기가 사용)
manager.maintain(now, current_vids=current_vids)
pax_manager.maintain(now, current_pids=current_pids)
spawn_manager.maintain(now, timeout_removed_pids=..., current_pids=current_pids)
```

- 모든 새 인자는 기본값 `None`입니다. 안 넘기면 예전처럼 직접 조회하므로 호출부를 빠뜨려도 에러가 나지 않습니다.
- 매니저가 지운 승객/택시는 공유 스냅샷에서도 빼고, 새로 만든 승객은 바로 추가해서 같은 스텝의 로그 기록이 어긋나지 않게 합니다.

## 7. 택시 대수 유지 (`taxi_manager.py`)

살아있는 택시 + 삽입 대기 중인 택시를 합쳐 `num_taxis`보다 모자라면 지도 가장자리(`boundary_edges`)에서 새로 채웁니다.
10분(시뮬 1000초)마다 누계 진단을 한 줄 출력합니다.

```
[택시 초기 대수] t=10s 설정 50 / 실제 48 (처음 배치 48 + 매니저 스폰 0)
[택시 진단] t=1000s 소멸 누계 {...} | 스폰 {...} | 살아있음 50/50
```

| 설정 키 (`config.json`, 선택) | 기본값 | 설명 |
|---|---|---|
| `taxi_max_spawn_per_step` | 5 | 한 스텝에 새로 채우는 최대 대수 |
| `taxi_spawn_pending_grace_sec` | 30 | 이 시간 넘게 삽입이 안 되면 포기하고 다른 edge로 재스폰 |
| `taxi_min_spawn_lane_len` | 15 | 이보다 짧은 edge에는 스폰하지 않음 (택시 길이 8m) |
| `taxi_rl_keepalive` | true | `rl_prepositioned`에서도 경로 끝에 닿는 택시만 연장 |
| `taxi_lock_initial_count` | false | 시작 10초 시점의 실제 대수를 목표로 고정 |
| `taxi_diag_interval_sec` | 1000 | 누계 진단 출력 간격(시뮬 초) |

## 8. 자주 겪는 문제

| 증상 | 원인과 대처 |
|---|---|
| 진행 로그의 `택시 N대`가 설정보다 많음 | 예전에는 전체 차량 수(장애물 포함)를 센 값이었음. 지금은 ID가 `taxi`로 시작하는 차량만 셈 |
| `Vehicle 'taxi_N' is not known` | 이미 사라진 차량을 조회한 SUMO 메시지. 치명적이지 않지만 자주 나오면 스냅샷 목록이 낡은 것이니 알려주세요 |
| `could not assign stop ... (taxi:dispatch)` | 승객 탑승/하차 위치를 못 잡은 경우. **같은 lane이 반복되면** 그 edge의 stop 위치 문제일 수 있음 |
| 로그 CSV 저장 실패 | 엑셀 등이 같은 CSV를 열고 있으면 Windows에서 쓰기가 거부됨. 실행 중에는 `data/sim_logs`의 파일을 열어두지 마세요 |
| RL 모델 관련 에러 | 행동 공간이 바뀌면 이전 모델을 못 씀. `rl_train.py`로 다시 학습 |

## 9. 테스트

```bash
python tests/test_module2.py     # 전처리(공간 인덱싱·시계열·외부 데이터) 검증
python tests_logic.py            # 핵심 로직 회귀 검사 (SUMO 불필요)
python tests_logic.py --sumo     # 실제 SUMO 결정성 검사 (오래 걸림)
```

## 10. 향후 계획

- 평일 로그 추가와 반복 실행으로 비교 신뢰도 확보
- RL 학습 완료 후 재평가
- Unity 연동 (`export_unity_replay.py`의 재생 JSON 활용)
- 자체 배차 알고리즘(FETEX) 개발
