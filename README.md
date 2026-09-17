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
   python train.py   ```

## SUMO 런타임 검증 (2번 담당)

Python 3.12에서 프로젝트 전용 환경을 준비합니다.

```powershell
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements-runtime.txt
.venv/Scripts/python.exe -m pytest tests/test_runtime_validation.py -q
$env:MOBILITY_BACKEND = 'libsumo'
.venv/Scripts/python.exe measure_wait_time.py --config-path presets/runtime_minimal.json --output-dir results/runtime_validation/minimal --seeds 42
.venv/Scripts/python.exe tools/verify_runtime_gui.py
.venv/Scripts/python.exe tools/validate_runtime.py sweeps
.venv/Scripts/python.exe tools/validate_runtime.py experiments
.venv/Scripts/python.exe tools/validate_runtime.py report
.venv/Scripts/python.exe tools/plot_runtime_validation.py
```

최소 구성은 확정 승객 5명이며, 나머지 실험은 시간대별 합성 수요를 사용합니다. 강남역 지도는 저장된 OSM 변환 도로망을 재사용합니다. 실험의 합성 날짜는 2026-09-18이며 실제 카카오 호출 데이터가 아닙니다. 비교 실행은 원본 `config.json`과 공용 학습 CSV를 변경하지 않습니다.

- [상세 실행 가이드·지표 정의·모듈 연결](docs/runtime_validation.md)
- [런타임 보고서](REPORT.md)
- [실제 정량 결과](results/runtime_validation/RESULTS.md)

GUI 화면은 SUMO 2D 런타임 확인용입니다. 제공된 3D Asset 적용 및 모델 학습·평가는 별도 작업입니다.
