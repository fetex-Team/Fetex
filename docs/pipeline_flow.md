# Python 실행 흐름 (KJS 브랜치, 2026-11 갱신)

1. `module1_simulation/build_env.py`: 강남역 OSM(같은 범위면 저장본 재사용) → netconvert 도로망 → 왕복 가능한 도로만(SCC) →
   POI 구역(학교·주거·회사·음식점·지하철 출구·버스정류장·백화점) → 택시·일반차 XML, `runtime_meta.json`(도로→H3 res 8 칸 매핑).
2. `demand_model.py` + `passenger_spawn_manager.py`: **실측 보정 호출 스케줄**을 시작 전에 시드로 생성
   (TIMS 500m 격자 하루 승차량·격자 비율·월 계절성 × 서울시 시간대·요일 비율). 배차 결과와 무관한 외생 수요라 전략이 달라도 같다.
   (`demand_source: synthetic`이면 예전 카테고리 가정 모델)
3. `scripts/generate_demand_logs.py`: 같은 규칙·시드로 SUMO 없이 학습용 8주(24시간) 호출 로그 생성 → `data/real_demand_logs/`.
4. `module2_preprocessing/`: 위경도→H3 칸, 10분 칸 집계(0 수요 보존), 날씨·달력 결합, lag/이동평균/지난주 같은 칸 피처, t+1~t+6 타깃.
5. `scripts/train_dispatch_model.py`: 시간순 분할 → MultiOutput XGBoost(`saved_models/demand_v2.joblib`).
   `scripts/convert_v2_to_category.py` → RL용 카테고리 예측, `module3_prediction/train_category_demand.py` → `category_demand_models.pkl`.
6. `measure_wait_time.py` (`run_and_measure`): SUMO 실행 → 호출 생성 → 헝가리안 배차(현재 호출) → 전략별 빈 택시 운영
   (`patrol` 순찰 / `prepositioned` 시간표 기반 선배치 / `rl_prepositioned` PPO 재배치 + 카테고리 수요 예측) →
   대기시간·타임아웃·서비스율·추정 매출·**수요 지문** 계산.
7. `export_unity_replay.py`: 6을 전략별로 실행하며 재생 파일(v2: 차선·위치·속도·지나온 차선) 기록, 수요 지문이 다르면 중단.
   → Unity `FetexReplayPlayer` + `ReplayMotion`이 차선 경로를 따라 재생.
8. 검증 도구: `tools/verify_replay_motion.py`(도로 이탈·궤적 오차), `tools/fleet_sizing.py`(택시 대수별 서비스율),
   `tests/test_demand_model.py`, `tests/test_replay_motion.py`, `tests/test_module2.py`.

`prepositioned`는 고정 시간대 규칙이며 학습 예측이 아니다. `hungarian`은 현재 호출 매칭이고 RL은 빈 택시의 선제 재배치다.
두 기능의 효과를 구분해서 평가하고, 비교는 항상 같은 수요 지문에서만 한다.
