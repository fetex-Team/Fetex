# 통합 분석 보고서 (작성 중)

## 2. 시공간 데이터 특성 분석 (EDA) 및 전처리 — Module 2 (담당 윤세빈)

> 그림·수치는 `notebooks/03_EDA_8weeks.ipynb`(실행 결과 포함)와 `data/eda/8w/`, ablation은 `data/eda/feature_ablation_8w_synthetic.md`.
> 재실행: `python eda/eda_8w.py` (8주 calls.csv·external.csv를 `data/generated_8w/`(저장소 밖 보관)에 두거나 `python scripts/generate_training_data.py --days 56`으로 재생성), `python scripts/feature_ablation.py data/generated/calls.csv --external data/generated/external.csv`.
> 1일치 예비 분석(2026-09-10)은 `notebooks/01_EDA_and_Spatial.ipynb`에 남겨 두었고, 아래는 8주 데이터로 확정한 결과다.

### 2.1 데이터
- 강남역 SUMO 디지털 트윈 합성 호출 8주(2026-07-06 월 ~ 08-30 일), **36,786건**, 날짜별 고정 시드 42 → 완전 재현. 실제 카카오 데이터 아님(과제 제약 #7 허용).
- 관측 단위 H3 res 8 셀 8개 × 5분칸 16,125개 = **129,000행**. 수요 0인 칸 80.1%, 칸당 평균 0.285건, 최대 11건 — 희소 카운트 데이터.
- 일별 호출 평균 657건(최소 487, 최대 953). 요일별 하루 평균: 월 646, 화 662, 수 672, 목 666, **금 899**, 토 527, 일 527.

### 2.2 시간 패턴 (`02_hour_dow_heatmap.png`, `04_weekday_holiday.png`)
- 피크 8시(등교·출근)와 18~19시(퇴근·저녁). **금요일 18~20시**가 가장 진한 칸 — "강남역 금요일 저녁" 시나리오가 데이터에 재현됨(이벤트 셀 ×3).
- 금요일은 다른 평일보다 +35%, 주말은 평일 대비 −26%(아침 피크 소멸, 저녁만 남음).
- 기간 내 평일 공휴일 없음(8/15가 토요일) → `is_public_holiday`·연휴 피처는 이 데이터에서 주말과 구분되지 않음. 생성기가 대체공휴일(8/17)을 달력으로 넣어야 분리 가능.

### 2.3 공간 격자 — H3 resolution 8 (팀 기준) (`03_cell_profiles.png`)
- 셀 8개의 총량은 11.7~13.8%로 고르지만 **피크 시각이 셀마다 다르다**: 4개 셀 8시, 3개 셀 18~19시, 1개 셀 12시(음식점). 격자별로 따로 학습해야 하는 이유이고, 모든 시계열 피처를 셀별 groupby 안에서 계산하는 이유.
- 해상도 판단(1일치 EDA, `data/eda/h3_resolution_compare.png`): res 8은 셀 7개에 최다 셀 35%(too coarse), res 10은 0인 칸 37%·lag-1 r 0.17(too fine), **res 9가 EDA상 적정**. 그러나 시뮬레이션 도로–격자 매핑과 저장 모델의 셀 목록이 같은 해상도여야 해서 develop 통합 시 **팀 기준 res 8로 통일**했다(`config.json h3_resolution`). 해상도 변경 시 모델 재학습 필요.
- 대용량 처리: 좌표 고유값 캐시 + chunk. 10만 행(중복 없는 최악 조건) H3+Geohash 변환 0.42초, 행 단위 apply 대비 2.3배.

### 2.4 POI zone 검증
- `eda/poi_zone_map.py`: 학교 5·주택 42·회사 66·음식점 218 구역을 지도에 겹쳐 확인(`data/eda/poi_zone_map.png`). 매핑 반경은 결과에 영향 없음(40~200m 동일). 주택 POI가 큰길로 매핑되는 문제 → Module 1 공유.

### 2.5 정상성·자기상관 (`08_stationarity.png`, `09_acf.png`)
**정상성(ADF, 전체 셀 합 5분 계열)**: 원계열 통계량 −18.6, 1차 차분 −24.9 (1% 임계값 −3.43) → 둘 다 단위근 없음.
1일치(09~11시)에서 원계열이 비정상(p=0.73)이었던 것은 2시간 창 안의 출근 상승 추세 때문이고, 8주 전체로는 수요가 일 주기 주변에서 정상적으로 흔들린다. 의미: 차분이 필수는 아니지만 일·주 주기가 강하므로 계절 피처(`same_time_yesterday`, `same_time_last_week`)와 단기 추세(`diff_1`)를 함께 쓴다. ARIMA 계열과 비교한다면 d=0 + 계절 항(SARIMA).

**자기상관(ACF, 셀 평균)**: lag1 0.38, lag6(30분) 0.36, lag12(1시간) 0.31, 유의 lag **40개**(±0.015). 계절 lag 1일 전 0.31, 1주 전 0.34.
1일치의 "판정 불가"가 8주에서는 "40칸까지 유의"로 확정 — lag 6개 + rolling 1h(12칸)는 유의 구간 안이고 그 이상은 rolling·계절 피처가 대신한다.

### 2.6 외부 데이터(날씨·달력) 병합과 품질 기준
- 날씨 결합: 관측 표(시간별 또는 5분) → 5분 칸에 `merge_asof(backward, tolerance 3h)` — 그 시각 이전 최신 관측만 사용(누수 없음). ≤3h 공백 선형보간 + `weather_interpolated`, 그 밖 `weather_missing` 플래그(원천이 제공하는 변수만 판정), 파일 없으면 상수 fallback + 경고.
- 품질 기준(Data Spec 5장, 위반 시 ValueError로 중단): 기온 −40~50℃·강수 0~200mm·풍속 0~75m/s, 관측 간격 경계 정렬, 학습 경로 결측률 ≤ 5%(`weather_missing_max_ratio`), 공휴일 달력이 데이터 연도를 덮을 것, (time_bucket, h3_index) 유일, demand ≥ 0, 5분 경계 정렬, tz 단일.
- 파생 피처: 날씨 `precip_3h_sum`·`rain_streak_h`·`temp_anomaly_24h`(과거 방향), 달력 `is_public_holiday`·`is_day_before_off`·`is_day_after_off`·`off_streak_len`, 수요 `same_time_yesterday`. 모델 입력 피처 34 → **43개**. 검증: `tests/test_module2.py` 85개(실제 `holidays` 달력·대체공휴일 포함 검사).

### 2.7 날씨·휴일이 수요에 미치는 영향 (`05_weather_rain.png`, `06_weather_temp.png`, `07_rain_day.png`)
같은 셀·같은 시간대 평균을 1로 둔 호출 비율:

| 강수 | 0 mm | 1.2 mm | 5.5 mm |
|---|---:|---:|---:|
| 호출 비율 | 0.92 | 0.98 | **1.27** |

- 비가 많이 올수록 호출이 는다(생성기 배수 1+0.08×mm와 일치). 기온은 구간별 0.96~1.04로 효과 없음(생성기가 기온을 수요에 넣지 않음).
- **한계**: 합성 강수의 자기상관이 5분 0.002, 1시간 −0.003(실측 강남역 1시간 0.77). 지금 비가 오는지가 5분 뒤 비와 무관해, 과거만 쓰는 모델은 미래 강수 효과를 학습할 수 없다. 이것이 2.8의 날씨 피처 기여 0의 원인이며 피처 설계가 아니라 생성기 문제다. 제안(Module 1·외부데이터): 강수를 시간 단위 에피소드로 생성하거나 실측 시간별 날씨 8주를 주입하고, 계수는 실측 추정치(강수 1mm당 +1.99%, 기온 1℃당 +2.73%, `docs/weather_coefficient.md`)를 사용. 공휴일은 `holidays` 달력으로 넣고 주말과 다른 배수.

### 2.8 피처 유효성 — ablation (같은 데이터·같은 분할·같은 모델)
시간순 뒤 20% test(25,768행), 타깃 y_h1~y_h6, 그래디언트 부스팅(맥 xgboost / 샌드박스 sklearn HGB):

| 피처 그룹 (누적) | 피처 수 | MAE | RMSE | WAPE | 기존 34 대비 MAE |
|---|---:|---:|---:|---:|---:|
| 전부 0 예측 (희소 MAE 하한) | 1 | 0.2816 | 0.7454 | 100.0% | |
| 나이브 (직전 5분 값 유지) | 1 | 0.3558 | 0.7662 | 126.4% | |
| lag 1~6 | 6 | 0.3443 | 0.5874 | 122.3% | +7.8% |
| + rolling/std/diff/지난주 | 13 | 0.3340 | 0.5758 | 118.6% | +4.5% |
| + 달력 기본 15 | 28 | 0.3195 | 0.5468 | 113.5% | +0.0% |
| + 날씨 기본 6 [= 기존 34] | 34 | 0.3195 | 0.5468 | 113.5% | — |
| + 휴일 강화 4 | 38 | 0.3193 | 0.5464 | 113.4% | −0.1% |
| + 날씨 파생 3 | 41 | 0.3195 | 0.5465 | 113.5% | −0.0% |
| + 어제 동일칸 2 [= 43] | 43 | 0.3186 | 0.5460 | 113.1% | −0.3% |

- 나이브 대비 **RMSE −29%**. 타깃의 80%가 0이라 MAE는 "전부 0"이 가장 낮으므로 RMSE·WAPE로 비교한다(모델은 평균을 맞춤).
- 기여 순서: 달력 > rolling/지난주 > lag > 어제 동일칸(작지만 일관). 휴일 강화·날씨 파생은 2.2·2.7의 데이터 한계로 0 — 생성기 개선 후 재실행하면 이 두 행이 움직여야 정상.
- 서빙 주의: `forecast_dispatcher`의 이력 창(75분)이 `same_time_*` 피처보다 짧아 서빙에서 항상 0이 됨 → `TimeSeriesPreprocessor.required_history_buckets`(1주+3칸)로 맞출 것.

## 3. 단기 수요 예측 모델링 및 성능 평가 — Module 3 (담당 전우영)

> 관련 코드: `train.py`, `evaluate.py`, `module3_prediction/models.py`, `module3_prediction/train.py`, `module3_prediction/evaluate.py`.  
> 가중치 저장소: `saved_models/xgboost_demand.pkl`, `saved_models/cnn_lstm_demand.pt`.  
> 모델 평가 실행: `python evaluate.py` (또는 `python module3_prediction/evaluate.py`).

### 3.1 모델 설계 및 타깃 정의
- **예측 목적**: H3 육각 격자(Resolution 8) 및 5분 단위 시계열 피처를 기반으로, 현재 시점 $t$에서 **향후 30분($t+1 \sim t+6$, 5분 간격 6개 시점)**의 미래 셀별 택시 호출 수를 동시 예측(Multi-horizon Forecasting).
- **타깃 변수 ($y_{h1} \sim y_{h6}$)**: 
  - $y_{h1}$ ($t+5$분), $y_{h2}$ ($t+10$분), $y_{h3}$ ($t+15$분), $y_{h4}$ ($t+20$분), $y_{h5}$ ($t+25$분), $y_{h6}$ ($t+30$분).
- **비교 모델 2종**:
  1. **XGBoost (Gradient Boosting Decision Tree)**: 비선형 시계열 래그/롤링 피처와 날씨·달력 피처 간의 상호작용을 학습. `GridSearchCV`를 통해 최적 하이퍼파라미터(`n_estimators`, `max_depth`, `learning_rate`) 튜닝.
  2. **CNN-LSTM (Deep Neural Network)**: 1D-CNN으로 단기 국소 특징을 추출하고 LSTM으로 장기 시간 의존성을 학습하는 복합 딥러닝 아키텍처. 텐서 차원 정합성 보정 및 `StandardScaler` 기반 입력 정규화 적용.
- **데이터 분할 원칙**: 시계열 특성상 미래 데이터가 과거로 누수(Data Leakage)되는 것을 방지하기 위해 셔플 없이 시간 순서 기준 Train 80% / Test 20% 분할.

### 3.2 정량적 성능 평가 결과 (Test Dataset)

45,210건의 실제 시뮬레이션 주행 호출 로그(시계열 샘플 165,690행: Train 132,552건, Test 33,138건) 및 기상청 실제 날씨 데이터를 결합한 34개 피처 기반 테스트 세트 평가 결과:

- **XGBoost 최적 하이퍼파라미터 (GridSearchCV)**: `n_estimators=200`, `max_depth=8`, `learning_rate=0.1`
- **CNN-LSTM 학습 조건**: 30 Epochs, Batch Size 128, Loss 수렴(6.7500 → 0.6144)

| 평가지표 (Metric) | XGBoost (머신러닝) | CNN-LSTM (딥러닝) | 우수 모델 및 성능 비교 |
| :--- | :---: | :---: | :--- |
| **RMSE (평균 제곱근 오차)** | **0.3651** | 0.7060 | **XGBoost 우세** (오차 48.3% 감소) |
| **MAE (평균 절대 오차)** | **0.0350** | 0.0851 | **XGBoost 우세** (오차 58.9% 감소) |
| **MAPE (백분율 오차)** | 159,822.5% | 604,892.9% | 희소 카운트 데이터 ($\epsilon=1e-5$ 적용) |

### 3.3 결과 분석 및 고찰
1. **대규모 데이터 확장(4.5만 건) 하에서의 트리 모델 안정성**:
   - 16.5만 개 공간-시계열 샘플로 학습 데이터를 대폭 확장한 환경에서도 **XGBoost는 극도의 기저 수요 희소성을 과적합 없이 안정적으로 추정(MAE 0.0350, RMSE 0.3651)**하여 CNN-LSTM 대비 월등한 예측 정밀도를 유지했습니다.
   - CNN-LSTM 역시 에포크가 진행됨에 따라 Loss가 6.75에서 0.61로 크게 감소하며 시계열 패턴을 성공적으로 학습했으나, 0이 다수를 차지하는 비대칭 희소 데이터셋 특성상 잔차 최소화에는 트리 기반 모델(XGBoost)이 더욱 유리함을 실증했습니다.
2. **평가지표 선정 근거 (동료 평가/인터뷰 대비)**:
   - 0 분모 문제를 방지하기 위해 Epsilon을 적용한 MAPE를 산출하였으며, 비정상 스파이크 및 극단치 오차를 엄격하게 검증하기 위해 **RMSE와 MAE를 최종 모델 선정을 위한 핵심 지표로 채택**했습니다.
3. **최종 모델 확정 및 후속 연동**:
   - 예측 오차와 추론 속도 면에서 가장 뛰어난 **XGBoost 모델을 최종 서빙 모델로 확정**하여 `saved_models/xgboost_demand.pkl`로 저장 완료.
   - 본 예측치는 배차 모듈(`forecast_dispatcher.py`) 및 강화학습 환경(`rl_env.py`)의 `demand_predictor`로 주입되어, 빈 택시를 승객 발생 예상 지역으로 사전 이동(Prepositioned)시키는 핵심 정보로 작동합니다.
