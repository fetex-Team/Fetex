# develop 통합 검수 — 브랜치별 반영/미반영 내역

작성일: 2026-09-12
목적: main / ldk / myth / feat/3-preprocessing / jwy 다섯 브랜치의 내용 중
develop에 무엇이 들어갔고, 무엇이 왜 빠졌는지 전부 기록한다.
"만든 내용은 어떤 것도 누락되면 안 된다"는 원칙에 따라, 빠진 것은 전부 이유와 함께 남긴다.

검토 방법
- 브랜치마다 `git diff --name-only <브랜치> develop` 으로 파일 차이를 뽑고,
- develop에 없는 파일/내용은 원본 diff를 열어 한 건씩 채택/미채택을 판정했다.
- 판정 기준: 과제 요구사항 부합 > 최신 세대 코드 > 동작 완성도.

## 1. origin/myth — 전부 반영 (develop의 기본 뼈대)

과제 요구사항(5분 단위 t+1~t+6 예측, 시간 기준 분할, 누수 방지)에 가장 잘 맞아
develop의 기준 브랜치로 삼았다. 파일 대부분이 그대로 develop에 있다.

빠진 것:
- train.py의 평가 연결 부분: myth의 train.py는 학습 끝에 evaluate.save_evaluation을
  호출해 예측 CSV·그림까지 저장했다. develop의 train.py는 feat/3 계열(시뮬레이션 로그 학습)로
  바뀌면서 이 호출이 빠졌다. 다만 save_evaluation 함수 자체는 evaluate.py에 그대로 살아 있어
  필요하면 한 줄로 다시 연결할 수 있다.

## 2. origin/feat/3-preprocessing — 전처리·학습 파이프라인 반영

반영된 것:
- module2_preprocessing 전체(pipeline, time_series_prep, external_data_merge,
  time_features, spatial_indexing 등) — 시뮬레이션 로그로 피처 테이블을 만드는 흐름.
- train.py — 로그 기반 학습 + 시간 기준 분할 + GridSearch(TimeSeriesSplit).
- 지난주 동일 시간대, 실측 날씨 결합, 결측 보간 — 과제 문서가 직접 요구하는 항목들.

빠진 것 (이유와 함께):
- README.md / REPORT.md 이 브랜치 버전: develop은 myth 버전 문서를 유지. 내용이 서로
  다른 세대라 합치지 않았고, 최종 문서는 제출 전에 새로 정리할 예정.
- config.json 이 브랜치 버전: develop은 myth의 config.json(검증 로직 포함)을 유지.
- measure_wait_time.py / run_simulation.py의 로그 기록 장치(sim_log_recorder 연결):
  develop의 시뮬레이션은 이미 calls.csv(같은 컬럼 구성)를 남기므로 기록 장치를 붙일 필요가
  없어 뺐다. sim_log_recorder.py 파일 자체는 develop에 있다.

## 3. origin/ldk — 전부 반영

geo_lookup.py(지역 검색 2단계 캐시), 검증 문서(docs/module1_map_verification.md,
docs/weather_coefficient.md), 검증 도구(tools/ 3종) 모두 develop에 있다. 빠진 것 없음.

## 4. main — 프로토타입, 최신 세대로 대체

main은 초기 업로드 모음이라 대부분 myth/feat/3에서 다시 작성됐다.

빠진 것 (이유와 함께):
- parallel_dispatch_orchestrator.py / parallel_dispatch_worker.py의 main 버전(수백 줄):
  myth가 안내문만 남기고 정리했고 develop도 그걸 따랐다. 여러 배차 전략을 같은 조건에서
  비교하는 원래 목적은 measure_wait_time.py compare + results/dispatch_comparison이 대신한다.
  원본 코드는 main 브랜치에 그대로 남아 있어 필요하면 꺼낼 수 있다.

## 5. origin/jwy — 쓸 만한 것만 이식, 나머지는 구세대라 미채택

jwy는 develop과 공통 조상이 없는 옛 세대 코드(10분 간격, 랜덤 데모 데이터 학습,
config 검증 제거)라 파일 그대로는 못 쓴다. 가치 있는 부분만 골라 이식했다.

이식한 것:
- CNN 입력 표준화(StandardScaler) + 모델 파일에 scaler 저장 → develop train.py에 반영.
  피처 단위가 제각각(수요 수, 기온, sin/cos)이라 표준화 없이는 학습이 흔들리고,
  평가·추론 때 같은 기준을 적용하려면 scaler를 모델과 함께 저장해야 한다.
- 저장된 XGBoost/CNN-LSTM 성능 비교·추천 도구(evaluate_saved_models) →
  scripts/compare_saved_models.py로 이식. 데이터 준비를 develop 파이프라인
  (시뮬레이션 로그 + 시간 기준 분할)으로 바꾸고, 지표는 develop evaluate.calculate_metrics 사용.

빠진 것 (이유와 함께):
- train.py / config.json / config_loader.py / main.py 이 브랜치 버전: myth 이전 세대
  (10분 간격, 랜덤 데모 데이터, 검증 로직 제거)라 과제 요구사항(5분 단위) 기준으로 미채택.
- macOS 전용 환경변수 설정 제거: develop은 유지(맥에서 GridSearch 중 죽는 문제 방지용).
- saved_models 산출물 2개: 랜덤 데모 데이터로 학습된 파일이라 미채택. 학습은 develop
  파이프라인으로 다시 돌리면 된다.
- CNN 학습 설정값(epochs 10, batch 32, lr 0.0005): develop은 config.json 값을 쓴다.
  참고용으로 여기만 기록해 둔다.

## 결론

다섯 브랜치에서 새로 만들어진 내용 중 develop에 없는 것은 위에 적힌 항목이 전부이며,
각각 대체물이 있거나(2·4번), 구세대라 뺐거나(5번), 원 브랜치에 보존돼 있다.
내용 자체가 사라진 것은 없다.
