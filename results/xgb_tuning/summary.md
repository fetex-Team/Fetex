# XGBoost 하이퍼파라미터 검증

- 데이터: `data/real_demand_logs/demand_log_*.csv` (생성기 `scripts/generate_demand_logs.py`), H3 res 8, 10min 칸, 타겟 6개
- 분할(시간 순서): 학습 < 2026-08-15 14:00:00 ≤ 검증 < 2026-08-26 18:30:00 ≤ 시험 — 행 38,648 / 12,888 / 12,888
- 후보 18개 (n_estimators × max_depth × learning_rate = {'n_estimators': [100, 300, 600], 'max_depth': [4, 6, 8], 'learning_rate': [0.05, 0.1]})

| 구분 | n_estimators | max_depth | learning_rate | 검증 WAPE |
|---|---:|---:|---:|---:|
| 최적 | 300 | 6 | 0.05 | 27.36% |
| 현재 설정 | 300 | 6 | 0.05 | 27.36% |
| 최악 | 600 | 8 | 0.1 | 28.22% |

- 최적 설정을 학습+검증으로 다시 학습한 시험 구간 WAPE: **27.77%** (RMSE 2.844, MAE 1.866)
- 후보 간 검증 WAPE 차이: 27.36% ~ 28.22%
- 참고: 이 표는 ldk 브랜치의 이전 파이프라인(합성 로그 data/real_demand_logs)으로 만든 결과다. jwy 파이프라인(10분 칸·타깃 6개·피처 43개)으로는 `python module3_prediction/tune_xgb.py`로 다시 돌려야 같은 근거가 된다.
