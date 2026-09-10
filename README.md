# Autonomous Taxi Fleet Dynamic Matching & Demand Forecasting

이 프로젝트는 SUMO 기반 디지털 트윈 환경에서 H3 공간 인덱싱과 시계열 AI 모델(XGBoost / CNN-LSTM)을 연동하여 수요를 예측하고, Surge Pricing 및 헝가리안 알고리즘 기반 최적 동적 배차를 검증하는 통합 파이프라인입니다.

## 모듈 구성
- **Module 1 (`module1_simulation`)**: SUMO Grid 환경 설정 및 Digital Twin 가동
- **Module 2 (`module2_preprocessing`)**: GPS H3 매핑, 5분 리샘플링, Lag/Calendar 피처 및 외부 환경 데이터(날씨/휴일) 결합
- **Module 3 (`models.py`, `train.py`, `evaluate.py`)**: XGBoost(GridSearch 튜닝) & CNN-LSTM 모델 학습 및 RMSE/MAE/MAPE 평가
- **Module 4 (`module4_dispatch`)**: 수급 불균형 기반 Surge Pricing 및 헝가리안 최적 배차

## 실행 방법
1. **모델 학습 및 저장 (Train/Test 분리 및 하이퍼파라미터 튜닝)**
   ```bash
   python train.py

## Module 2 — 시공간 데이터 처리 (전처리/EDA) 실행 순서

```bash
source ../20250907ver/venv/bin/activate          # 또는 venv/
pip install -r requirements.txt                   # holidays, statsmodels, jupyter 포함
python measure_wait_time.py                       # ① 시뮬 → data/sim_logs/demand_log_*.csv (호출 로그)
python scripts/fetch_weather_history.py           # ② 시간별 날씨 → data/external/weather_*.csv (선택, 없으면 fallback)
python tests/test_module2.py                      # ③ 최소 완료 조건 테스트 (42개)
python -m module2_preprocessing.pipeline --logs "data/sim_logs/*.csv" --out data/processed/features.csv   # ④ 피처 테이블
bash 실행_EDA.command                              # ⑤ H3/POI 그림 + notebooks/01_EDA_and_Spatial.ipynb 실행
python train.py                                   # ⑥ Module 3 학습 (④를 내부에서 호출)
```

- 파이프라인: 로그 → H3(res 9)/Geohash → (셀×5분) 집계·0채움 → 날씨 asof 병합·결측 플래그 → lag/rolling/지난주/시간·공휴일 피처 → 타겟 y_h1..y_h6
- 설계 근거·최소 완료 조건: `docs/MODULE2_MANUAL.md` / 변경 이력: `CHANGES_전처리.md` / 데이터 설명: `data/README.md`
