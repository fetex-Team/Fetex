# 프로젝트 구조와 실행 계약

## 공식 구조

```text
fetex/
  core/           설정, 프로젝트 루트
  simulation/     SUMO 지도·환경 생성
  runtime/        실행 루프, 택시·승객·계측
  preprocessing/  H3/Geohash, 외부 결합, 시계열 피처
  forecasting/    모델 정의, 학습·평가 진입점, 지표
  dispatch/       매칭, 서지 가격, 재배치
  geospatial/     지역 검색, 지도, POI
  integrations/   Unity replay 출력
  validation/     런타임·계약 검증
scripts/          데이터 생성·학습·평가 CLI
tests/            단위 및 통합 테스트
data/             입력·생성 데이터
saved_models/     모델 artifact
results/          재현 가능한 산출물
presets/          고정 재현 설정
```

`fetex`가 유일한 도메인 코드 패키지다. 루트의 기존 실행 파일은 호환성만 담당한다.

## 공식 명령

```powershell
# SUMO 환경과 런타임
.venv/Scripts/python.exe -m fetex.simulation.build_env
.venv/Scripts/python.exe -m fetex.runtime.measure_wait_time --config-path presets/runtime_minimal.json

# 전처리·학습·평가
.venv/Scripts/python.exe -m fetex.preprocessing.pipeline --logs data/sim_logs/*.csv --out data/processed/features.csv
.venv/Scripts/python.exe scripts/train_dispatch_model.py data/generated/calls.csv --external data/generated/external.csv
.venv/Scripts/python.exe scripts/evaluate_dispatch_model.py data/generated/calls.csv --external data/generated/external.csv --model saved_models/demand_v2.joblib

# Unity replay
.venv/Scripts/python.exe -m fetex.integrations.unity_replay --config-path presets/forecast_sample.json
```

## 리팩터링 전후 매핑

| 이전 위치 | 공식 위치 | 책임 |
|---|---|---|
| `module1_simulation/` | `fetex/simulation/` | SUMO 환경·지도 |
| `module2_preprocessing/` | `fetex/preprocessing/` | 피처 생성 |
| `module3_prediction/` | `fetex/forecasting/` | 예측 모델 |
| `module4_dispatch/` | `fetex/dispatch/` | 배차·서지·재배치 |
| 루트 실행·매니저 파일 | `fetex/runtime/`, `fetex/apps/` | 런타임·사용자 실행 |
| 루트 지도 보조 파일 | `fetex/geospatial/` | 지역·지도·POI |

데이터, Unity 스크립트, 모델, 결과, 노트북은 삭제하지 않았고 역할별 폴더에 그대로 보존한다.
Git의 rename 추적으로 파일 이력을 유지한다.
