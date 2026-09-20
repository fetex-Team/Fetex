# 2번 담당: SUMO 런타임 검증

이 문서는 시간대별 수요 생성, 독립 인구 추첨, 택시 전략, 승객 타임아웃을 검증하는 실행 가이드다. 정량 결과는 [실험 결과](../results/runtime_validation/RESULTS.md)와 CSV에서 재생성한다.

## 실행 환경과 명령

Python 3.12, SUMO 1.27.1을 사용한다. 전체 AI 학습 의존성을 설치하지 않아도 아래 런타임 검증이 가능하다.

```powershell
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements-runtime.txt
.venv/Scripts/python.exe -m pytest tests/test_runtime_validation.py -q
$env:MOBILITY_BACKEND = 'libsumo'
.venv/Scripts/python.exe -m fetex.runtime.measure_wait_time --config-path presets/runtime_minimal.json --output-dir results/runtime_validation/minimal --seeds 42
.venv/Scripts/python.exe tools/verify_runtime_gui.py
.venv/Scripts/python.exe tools/verify_runtime_integration.py
.venv/Scripts/python.exe tools/validate_runtime.py sweeps
.venv/Scripts/python.exe tools/validate_runtime.py experiments
.venv/Scripts/python.exe tools/validate_runtime.py report
.venv/Scripts/python.exe tools/plot_runtime_validation.py
.venv/Scripts/python.exe tools/check_runtime_results.py
```

GUI 검증은 데스크톱 세션에서 실행하며 TraCI를 사용한다. headless 실험의 기본 백엔드는 같은 SUMO 엔진/API의 libsumo다. 소켓 기반 실행은 `--backend traci`로 선택할 수 있다. GUI/libsumo 최소 시나리오의 승객별 대기시간도 비교했다.

`python -m fetex.runtime.measure_wait_time compare --config-path <설정.json> --output-dir <결과폴더> --seeds 42 43 44 45 46`은 patrol/prepositioned 비교 명령이다. `dispatch_compare greedy hungarian`도 설정을 덮어쓰지 않고 실행한다. 설정 우선순위는 명시 경로, `MOBILITY_CONFIG`, 기본 `config.json` 순이다.

실험은 실행마다 설정 JSON을 저장하고 빌드·측정을 별도 프로세스에서 수행한다. 같은 코드 해시와 설정으로 완료된 실험만 재사용한다. `results/runtime_validation/STOP` 파일을 만들면 현재 실행을 마친 후 중지한다. 재개 전 해당 파일을 제거한다. 실패 실행은 `manifest.csv`와 해당 `run.log`로 확인한다.

## 수정한 런타임 오류

- 승객이 잠깐 없으면 종료하던 조기 종료를 제거했다. 미래 호출 전의 빈 구간도 진행한다.
- SUMO 진입 대기·텔레포트 중인 택시까지 보유 대수에 포함했다. 수정 전 강남역 시험에서는 50대 설정으로 관측 대수가 88대까지 늘었다. 해당 결과는 무효 처리했다.
- 예약 보류 승객의 탑승 여부를 다시 확인한 뒤 제거한다. 임계시간 초과와 실제 제거 시각은 별도다.
- 완료·취소된 Hungarian 예약을 정리하여 과거 예약 때문에 제거가 영구 보류되는 문제를 수정했다.
- 승객 생성 성공 이후에만 생성 수를 증가시킨다. stage 추가 실패 시 고아 person을 정리하고 실패 이유를 보존한다.
- 택시 이동과 수요 유형별 난수를 분리했다. 학교도 누적 시도 방식을 사용하여 초당 1회 상한을 없앴다.
- 목적지는 출발 edge와 달라야 한다. 회사 모수의 배수에는 폴백 도로 수 대신 실제 회사 edge 수를 사용한다.
- 단순 person 소실을 정상 도착으로 간주하지 않고 실제 SUMO 도착 이벤트만 흡수한다.
- 점심 복귀 인구는 음식점에 실제 도착한 사람으로 제한한다. 회사에서 점심에 나간 인원을 차감해 복귀 시 중복 누적을 방지한다.
- 과거의 점심 이벤트를 늦게 시작한 시뮬레이션에서 소급 실행하지 않는다. 수요 생성은 `[시작, 종료)` 구간이다.
- GUI의 경로 타입과 공용 실행 함수의 출력 인자 불일치를 수정했다.

## 최소 구성과 검증 결과

`presets/runtime_minimal.json`은 확률 추첨 대신 고정 승객 5명을 0·60·120·180·240초에 배치한다. 동적 생성은 끈다.

| 항목 | 생성 검증 |
|---|---:|
| 블록 | 3×3 (교차로 16개) |
| 승객 | 5명 |
| 택시 | 3대 |
| 일반 차량 | 4종 각 5대 |
| 자율주행 표시 차량 | 1대 |
| 장애물 | 2개 |

실제 headless와 GUI에서 모두 5명 탑승, 타임아웃 0명이었다. 승객별 대기시간은 69, 7, 58, 48, 64초로 두 백엔드가 같았으며 평균은 49.2초였다. 구성 근거는 `minimal/composition.json`, 승객별 결과는 `minimal/passenger_outcomes.csv`에 있다.

![최소 구성 SUMO 실제 화면](../results/runtime_validation/minimal/gui/sumo_60s.png)

이 화면은 SUMO 2D 교통 런타임 증거다. 제공 Unity Asset과의 표현 연결은 `unity/README.md`, `fetex.integrations.unity_replay`, `unity/Assets/Scripts/SumoReplayPlayer.cs`에서 별도로 제공한다. 차량 4종은 SUMO의 sedan/hatchback/wagon/van 외형이다.

## 실험 설계와 지표

학교 모수는 학교 전체, 회사 모수는 회사 edge당 인구다. 실제 건물 수와 같다고 해석하지 않는다. `additional_population`은 새 설정이 아니라 `num_passengers` 독립 추첨을 뜻한다. `legacy`에서는 이 값이 주택 정적 스케줄에도 반영되므로 두 수요를 따로 집계한다.

인구 스윕은 한 변수를 바꾸고 30개 시드로 반복한다. 유형별 이론 평균은 실제 추첨 시각의 확률 합, 분산은 `Σp(1-p)`다. 평균 오차가 5표준오차 이내인지 검사한다. 이 기준은 구현 회귀 검사용이며 통계적 우월성 검정이 아니다. 확률 0·1, 초당 복수 추첨, 같은 초 중복 호출은 별도 단위 테스트로 정확히 검사한다.

정량 실험은 강남역 저장 지도·구역, 택시 50대, 독립 추첨 10000, 학교 400, 회사 100, Hungarian 배차를 사용한다. 시간표 전략 두 개, 시드 42~46, 07~10·09~11·06~24시로 30회 실행한다. 타임아웃은 09~11시에서 200·350·500·700·900초로 비교하며 500초 10회를 재사용해 40회를 추가한다.

06~24시 실행은 18시 이전 실제 흡수량을 누적한다. 18~24시에 발생한 호출의 별도 결과는 `evening_metrics.csv`다. 시나리오 날짜 2026-09-18은 합성 금요일이며 생성기에는 요일 효과가 없다. 실측 금요일 급증을 재현했다고 주장하지 않는다.

- **평균·중앙값·P90 대기시간:** 탑승 시각 − 호출 시각, 탑승자만 포함. P90은 nearest-rank 방식이다.
- **타임아웃률:** 실제 타임아웃 제거 / 생성 성공. 예약 때문에 남은 승객은 제거로 세지 않는다.
- **종료 미탑승률:** 종료 시 대기 중 / 생성 성공. 관측 종료로 검열된 승객을 별도 표시한다.
- **승객 보존:** 생성 성공 = 탑승 + 실제 타임아웃 제거 + 종료 미탑승 + 기타 소실.
- **기존 페널티 평균:** 탑승 대기시간과 타임아웃 임계값을 합친 보조 지표. 종료 미탑승자는 제외된다. 임계값 간 순위 결정에 사용하지 않는다.
- **차량 대수:** loaded 차량 기준 상한을 매 스텝 확인한다. 도로 관측 대수는 진입 대기·텔레포트 때문에 그보다 작을 수 있다.
- **반복 집계:** 시드별 결과의 산술평균과 표본 표준편차. 전략 차이는 같은 시드끼리 계산한다. 표준편차를 신뢰구간으로 표기하지 않는다.

흡수 기반 후속 수요는 전략이나 타임아웃에 따라 달라진다. 같은 시드라고 전체 하루 호출 목록이 동일하다고 가정하지 않는다. 타임아웃 500초와 전략의 기본값을 실험 결과만으로 자동 변경하지 않는다.

## 로그와 타 담당 연결

| 파일 | 의미 |
|---|---|
| `calls.csv` | 호출 ID, ISO 시각, 출발·도착 edge/위경도, 수요 유형, 좌표 출처 |
| `passenger_outcomes.csv` | 호출·탑승·실제 제거·임계초과 시각, 예약 보류, 최종 상태 |
| `fleet_distribution.csv` | 5분마다 H3별 전체·빈·탑승 중 택시, 대기 승객; 최초/종료 스냅샷 포함 |
| `object_states.csv` | 동일 스냅샷의 객체 ID·종류·edge·SUMO x/y·속도 |
| `summary.json` | 설정, 엔진/코드/지도 버전, 수요 지문, 지표, 0명 사유 |
| `spawn_failures.json` | 생성 실패 이유 |

합성 격자는 bbox 선형 매핑, 실제 지도는 SUMO 투영 좌표를 사용한다. 내부 교차로처럼 H3 매핑이 없는 차량은 `unmapped`로 보존한다. 5분 시각은 시뮬레이션 시작 기준이다. 과거 날짜를 무작위로 붙이거나 공용 학습 로그에 자동 추가하지 않는다.

전처리 담당은 `pickup_datetime`, `latitude`, `longitude`를 사용할 수 있다. 이 필드의 pickup은 기존 학습 입력 이름에 맞춘 **호출 시각**이며 실제 탑승 시각은 outcomes의 `pickup_sec`다. 3D 담당은 객체 ID와 x/y(미터), 시뮬레이션 초를 받아 좌표계를 맞춘다. 5분 스냅샷은 분포 확인용이며 부드러운 Animation 재생용 고주파 궤적이 아니다.

예측 배차는 `ForecastDispatcher`의 5분 갱신·H3별 향후 6칸 인터페이스를 사용한다. `strategy='forecast'` 실행에는 같은 `forecast_calls_path`와 `replay_calls_path`, 호환 `saved_models/demand_v2.joblib`, H3 영역, 전처리 설정 및 `reposition_fraction`이 필요하다. 포함 모델은 43개 피처·6개 타깃의 시간 순서 분할 XGBoost이며, 온라인 이력은 지난주 피처까지 계산되는 1주+3칸을 읽는다. `scripts/verify_forecast_contract.py`가 누수·지도·호출 스트림 계약을 검사하고 미준비 상태에서는 명시적으로 실패한다. 배차 효과의 우위는 `patrol`과 동일 호출 fingerprint를 반복 비교해 검증해야 하며, 기본 모델 정확도는 `results/prediction/dispatch_model/`에 기록한다.

공개 저장소에는 최종 집계 CSV·검증 요약 JSON·그래프와 최소 구성의 호출/승객 결과·대표 GUI 화면을 남긴다. 70회 실행별 설정·요약·차량 분포, 격자망/재현성 실험 상세 출력, 반복 복제 지도와 대용량 로그는 `.gitignore`로 제외하고 로컬에 보관한다. 새로 복제한 저장소에서 전체 결과 검증이나 보고서·차량 분포 그래프 재생성을 하려면 위 실행 명령으로 실험 원시 파일을 먼저 생성해야 한다. 기존 프로젝트 전체 EDA/모델 평가/3D Asset 보고서는 각 담당이 별도로 완성해야 한다.

## 추가 통합 검증

회귀 테스트 25개와 실제 SUMO 통합 검증 4개를 통과했다. 독립 인구 1000/20000의 실제 격자망 생성량은 110/2039명이며, 두 실행 모두 학교 201명·회사 459명으로 같았다. 06시 시작의 빈 구간 이후 학교 수요가 정상 발생했다. 강남역 동일 시드 반복 실행에서는 승객별 대기시간·호출 지문·타임아웃 집계가 일치했다. 자세한 기록은 `integration_checks.json`에 있다.

전처리 기존 검사도 42개 항목을 통과했다. 테스트의 Unix 고정 임시 경로 대신 플랫폼 임시 디렉터리를 인자로 전달했으며 원본 테스트 파일은 변경하지 않았다. Geohash·공휴일은 테스트 자체의 선택 의존성 대체 구현을 사용했다.

최종 실제 실험은 70/70회 정상 종료했다. `tools/check_runtime_results.py`로 70개 원시 기록·요약, 동일 코드·지도, 택시 대수 상한, 생성 실패·기타 소실 0건, 인구 통계 검사 48개 및 GUI/headless 일치를 확인했다. 검증 결과는 `acceptance.json`에 저장되어 있다.
