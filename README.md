# 택시 수요 예측과 동적 배차 — Python / SUMO

Unity 연동 전의 계산·검증 파이프라인입니다. 완료된 5분 관측으로 다음 6구간(30분) 수요를 예측하고, 실제 SUMO 빈 택시를 재배치합니다. Unity 3D Asset·통신·재생 구현은 포함하지 않습니다.

## 설치

Python 3.12를 권장합니다. 아래 명령은 `20260910ver` 폴더에서 실행합니다.

```bash
python3.12 -m venv .venv
source .venv/bin/activate
# Windows: .venv\Scripts\activate
python -m pip install -r requirements.txt
sumo --version
netgenerate --version
```

`eclipse-sumo`는 시뮬레이터 실행 파일을 포함합니다. 가상환경의 실행 파일 경로가 PATH에 있어야 합니다. OSM 실제 지도 모드는 인터넷과 좌표 투영 지원이 필요합니다. GUI는 별도 표시 환경이 필요하며 서버에서는 `--headless`를 사용합니다.

## 기본 실행

```bash
python module1_simulation/build_env.py
python train.py
python main.py --headless
python tests_logic.py
```

환경 생성 → 학습 → 실행 순서입니다. 기존 `xgboost_demand.pkl`, `cnn_lstm_demand.pt`는 이전 단일 타깃 모델이므로 새 경로에서 사용하지 않습니다. `demand_v2.joblib`이 없거나 모델의 H3 영역·전처리 설정이 다르면 명시적으로 실패합니다. 실제값으로 예측을 대체하지 않습니다.

기본 `config.json`은 기존 강남역 실제 지도 설정을 유지합니다. 현재 작업에 포함된 옛 SUMO XML에는 새 메타 정보가 없으므로 먼저 환경을 재생성해야 합니다. 다른 맵으로 학습하면 `saved_models/demand_v2.joblib`도 다시 만들어집니다.

## 인터넷 없이 재현하는 검증 시나리오

```bash
python module1_simulation/build_env.py --config-path presets/python_validation.json --config-dir sumo_validation
python train.py --meta module1_simulation/sumo_validation/runtime_meta.json --days 21
```

이후 사용할 환경 폴더를 지정합니다.

```bash
# macOS / Linux
export MOBILITY_SIM_DIR="$PWD/module1_simulation/sumo_validation"
# Windows PowerShell
# $env:MOBILITY_SIM_DIR="$PWD/module1_simulation/sumo_validation"
python main.py --headless
python measure_wait_time.py compare
python parallel_dispatch_orchestrator.py greedy hungarian
python tests_logic.py --sumo
```

`sumo_validation`은 3×3 블록, 택시 6대, 일반 차량 4종 각 5대, 표시용 자율주행 타입 1대, 장애물 0개의 알고리즘 검증 시나리오입니다. Unity의 필수 최소 구성 제출 시나리오와 구분합니다.

## 실험 조건과 결과

- `passenger_mode=replay`: 날짜별 고정 합성 호출을 재생합니다. `synthetic_rate`가 호출 강도이며 `num_passengers`와 학교/회사 인구 설정은 사용하지 않습니다.
- `passenger_mode=legacy`: 기존 시간대별 생성기를 유지합니다. 배차 성과에 따라 다음 호출이 달라질 수 있으므로 공정한 A/B 비교와 `forecast` 전략에서는 사용하지 않습니다.
- `taxi_strategy=patrol / prepositioned / forecast`: 순찰 / 시간대 규칙 / 학습 예측 재배치입니다.
- `taxi_dispatch_algorithm=greedy / routeExtension / hungarian`: 현재 대기 호출의 배차 방식입니다. 재배치 전략과 별도 비교합니다.
- 재배치는 5분마다 실제 빈 택시만 대상으로 하며 `reposition_fraction`이 이동 대수의 상한입니다. 픽업 중·탑승 중 차량은 제외합니다.
- 매칭 실행 주기는 greedy·헝가리안 모두 1초로 맞춥니다.
- A/B는 동일한 맵·차량 초기 조건·호출 목록을 사용하며 호출 CSV의 SHA-256이 다르면 비교를 실패 처리합니다. 사용자 설정 파일을 덮어쓰지 않습니다.

`results/prediction/`에는 모델별 RMSE/MAE/MAPE/WAPE, 지역·시간대·예측 구간별 지표, 실제/예측 그래프, EDA 그래프와 분할·튜닝 기록이 저장됩니다. MAPE는 실제 수요가 양수인 표본만 계산하고 제외한 0수요 표본 수를 함께 기록합니다.

`results/simulation/`, `results/strategy_comparison/`, `results/dispatch_comparison/`에는 호출·승객별 상태, 차량 분포, 평균 대기시간·탑승률·타임아웃률·종료 미탑승 수가 저장됩니다. `forecast` 실행은 지역별 6구간 예측·요금·목표 택시 수와 실제 재배치 기록도 저장합니다. 종료 시 미탑승 대기시간은 아직 끝나지 않은 값이므로 `observed_wait_lower_bound_sec`는 관측 하한이며 완결된 평균이 아닙니다.

## CNN-LSTM 추가 검증

```bash
python -m pip install -r requirements-cnn.txt
python train.py --meta module1_simulation/sumo_validation/runtime_meta.json --days 21 --cnn
```

지역별 연속 12개 관측을 입력으로 6개 미래 타깃을 출력합니다. 정규화는 학습 자료만 사용하며 최적 epoch 선택은 검증 자료로 합니다. 온라인 SUMO 재배치는 XGBoost를 사용하고 CNN-LSTM은 비교 실험 결과로 저장합니다.

## 데이터와 한계

호출은 공개 실제 자료가 아닌 `data/generate.py`의 합성 자료입니다. 공간별 피크·요일·강수·이벤트에 따라 포아송 호출을 생성하며 같은 날짜/시드는 같은 호출을 만듭니다. 날씨·교통·이벤트·휴일도 합성 조건입니다. `external_data_merge.py`는 시간/H3 키로 외부 표를 결합하고 과거 관측만 전달하며 최초 결측은 missing 열로 표시합니다. 실제 기상청·교통 API의 과거 자료를 수집하는 기능은 포함하지 않습니다.

실험 범위와 실제 확인 결과는 `REPORT.md`를 참고하세요. 한 합성 시나리오의 결과를 실제 강남역 운영 성능이나 인센티브에 대한 실제 기사 반응으로 해석하지 않습니다.
