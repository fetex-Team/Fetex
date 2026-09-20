# Fetex — 택시 수요 예측·동적 배차 Digital Twin

강남역의 특정 시간대 수요를 5분 단위로 예측하고, 빈 택시를 수요-공급 불균형 지역으로
재배치하는 SUMO 기반 프로젝트다. 입력 호출을 H3 격자로 집계하고 향후 30분(`t+1`~`t+6`)을
예측한 뒤, 할증 인센티브와 Hungarian 매칭을 실제 SUMO 시뮬레이션에 적용한다.

> 기본 데이터는 **재현 가능한 합성 호출·외부 관측**이다. 실제 카카오 호출 데이터나 실제
> 카카오 길찾기 API 결과라고 주장하지 않는다. 실제/공개 호출 CSV도
> `pickup_datetime, latitude, longitude` 스키마만 맞으면 같은 전처리 경로에 넣을 수 있다.

## 과제 요구사항 대응

| 모듈 | 구현 위치 | 확인 방법 |
|---|---|---|
| 1. Digital Twin | `fetex/simulation/`, `fetex/runtime/` | `presets/runtime_minimal.json`: 3×3 블록, 승객 5, 택시 3, 일반차 4종 각 5, AV 1, 장애물 2 |
| 2. 시공간 전처리 | `fetex/preprocessing/` | H3·Geohash, 5분 패널, 0 수요 보존, lag/rolling/요일·휴일·날씨 피처 |
| 3. 단기 예측 | `scripts/train_dispatch_model.py`, `fetex/forecasting/` | 시간 순서 분할, 43개 피처, 다중 출력 `y_h1`~`y_h6`, RMSE/MAE/MAPE/WAPE |
| 4. 동적 배차 | `fetex/dispatch/`, `fetex/runtime/` | 수급 비율, 상한 3배 할증, 정수 택시 배분, SUMO 실제 도로 이동시간 재배치 |
| 3D 표현 | `unity/`, `fetex/integrations/` | 제공 Unity Asset과 replay JSON 연결 — [가이드](unity/README.md) |

세부 설계는 [파이프라인 문서](docs/pipeline_flow.md), 실행·수치 근거는
[통합 보고서](REPORT.md), 최소 SUMO 구성은 [런타임 가이드](docs/runtime_validation.md)를 참고한다.

## 설치

Python 3.12와 SUMO 1.27 이상을 권장한다. 먼저 프로젝트 루트에서 가상환경을 만들고
핵심 패키지와 SUMO 런타임 패키지를 설치한다.

```powershell
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements.txt
.venv/Scripts/python.exe -m pip install -r requirements-runtime.txt
```

`config.json`은 기본 실행 설정이다. 재현 실행은 외부 지오코딩·현재 날씨 조회를 기본으로
끄며(`resolve_external_data=false`), 필요한 경우에만 preset 또는 별도 설정에서 켠다.

## 빠른 검증

```powershell
.venv/Scripts/python.exe tests/test_module2.py
.venv/Scripts/python.exe tests/integration/tests_logic.py
```

첫 명령은 공간·시간·외부 데이터·품질 규칙·1주 계절 피처를 포함한 85개 검사를 수행한다.
둘째 명령은 누수 차단, 0 수요 MAPE 처리, 할증 상한, 택시 중복 생성 방지, 빈 구간 진행,
forecast 인과성을 점검한다.

최소 SUMO 시뮬레이션은 다음처럼 실행한다.

```powershell
.venv/Scripts/python.exe -m fetex.runtime.measure_wait_time --config-path presets/runtime_minimal.json --output-dir results/runtime_validation/minimal
```

## Module 2–3: 데이터·예측 모델

저장소에는 2026-08-23~30의 8일 합성 샘플이 포함돼 있다. 아래 명령은 현재 저장된
`demand_v2.joblib`과 동일한 43개 피처·6개 예측시점 계약을 다시 만든다.

```powershell
.venv/Scripts/python.exe scripts/train_dispatch_model.py data/generated/calls.csv --external data/generated/external.csv
.venv/Scripts/python.exe scripts/evaluate_dispatch_model.py data/generated/calls.csv --external data/generated/external.csv --model saved_models/demand_v2.joblib
```

모델 artifact에는 H3 셀 목록, 학습 종료 시각, 피처·타깃 목록, 전처리 창, 테스트 지표가
함께 저장된다. 배차기는 이 계약이 지도·설정과 다르면 실행을 중단한다. 기본 샘플의 시간
순서 test 결과는 RMSE **0.5095**, MAE **0.3113**, 양수 수요 칸 MAPE **61.15%**다.
0 수요 칸이 많은 카운트 자료라 MAPE만으로 비교하지 않고 WAPE와 horizon/cell/hour별 결과를
`results/prediction/dispatch_model/`에 함께 남긴다.

## Module 4: 동일 호출 스트림 기반 forecast 배차

`forecast_sample.json`은 포함된 샘플 데이터로 끝까지 실행할 수 있는 짧은 확인용 설정이다.
예측 입력과 SUMO 승객 생성에 **같은** `calls.csv`를 사용하므로 patrol/forecast 비교의 호출
지문이 달라지는 문제를 막는다.

```powershell
.venv/Scripts/python.exe scripts/verify_forecast_contract.py --config-path presets/forecast_sample.json
.venv/Scripts/python.exe -m fetex.runtime.measure_wait_time --config-path presets/forecast_smoke.json --output-dir results/forecast_smoke
```

예측기는 매 5분에 완료된 호출만 읽고, `TimeSeriesPreprocessor.required_history_buckets`
(1주+3칸)만큼 과거를 공급한다. 따라서 `same_time_yesterday`, `same_time_last_week` 피처가
서빙에서 0으로 퇴화하지 않는다. 예측된 6개 horizon의 합과 현재 빈 택시로 수급 비율을 만들고,
할증은 `min_multiplier`~`max_multiplier`(기본 1.0~3.0)로 제한한다. 이동 대상은 SUMO
`findRoute`의 실제 이동시간으로 선택한다.

금요일 저녁용 긴 실험은 새 학습·온라인 스트림을 생성한 뒤 실행한다.

```powershell
.venv/Scripts/python.exe scripts/generate_training_data.py --days 35 --scenario-date 2026-09-18
.venv/Scripts/python.exe scripts/train_dispatch_model.py data/generated/train_calls.csv --external data/generated/train_external.csv
.venv/Scripts/python.exe scripts/verify_forecast_contract.py --config-path presets/forecast_demo.json
.venv/Scripts/python.exe -m fetex.runtime.measure_wait_time --config-path presets/forecast_demo.json --output-dir results/forecast_demo
```

생성되는 `train_*`, `stream_*` 파일은 재현 가능한 대용량 산출물이라 Git에서 제외한다.
`train_*`은 평가일 전날까지로 제한하고 `stream_*`은 평가일 호출까지 포함한다. 따라서
학습 종료 시각이 시뮬레이션 시작보다 미래이면 계약 검사가 실패한다.

## Unity 3D Asset 표현

제공받은 `Kakaomobility.zip`의 Map/Object/Animation Unity package는 저작권·용량 문제로
저장소에 포함하지 않는다. `unity/Assets/Scripts/SumoReplayPlayer.cs`는
`fetex.integrations.unity_replay`의 `patrol.json`·`forecast.json`을 제공 Asset prefab으로 재생한다.
Asset import와 replay 생성 명령은 [Unity 가이드](unity/README.md)에 있다.

## 저장소 구조

```text
fetex/core/              단일 설정 원본·저장소 경로 계약
fetex/simulation/        SUMO 지도·차량·승객 Digital Twin
fetex/runtime/           SUMO 실행·택시·승객·대기시간 계측
fetex/preprocessing/     H3/Geohash·외부 결합·시계열 피처
fetex/forecasting/       예측 모델 정의·공용 평가 지표
fetex/dispatch/          Hungarian 매칭·할증·예측 기반 재배치
fetex/geospatial/        지역 검색·지도·POI 보조 기능
fetex/integrations/      Unity replay 내보내기
scripts/                 데이터 생성, 학습, 평가, 계약 검증 CLI
tests/                   단위·통합 테스트
data/, results/          입력 샘플과 재현 가능한 산출물
unity/, docs/            제공 Asset 연결과 설계·협업 문서
```

새 코드는 항상 `fetex.*` 경로를 사용한다. 루트의 `main.py`, `measure_wait_time.py`,
`train.py`, `evaluate.py`, `config_gui.py`, `export_unity_replay.py`는 기존 개인 실행 습관을
보존하는 호환 진입점이다. 역할·경계·리뷰 규칙은 [구조 가이드](docs/architecture/project_structure.md)와
[협업 가이드](CONTRIBUTING.md)에 정리했다.
