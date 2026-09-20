# 전처리 & EDA 파트 (3번, 윤세빈) — 담당 내용 정리

브랜치 `feat/3-preprocessing` · 최종 갱신 2026-09-18
관련 문서: `docs/MODULE2_MANUAL.md`(사용법), `docs/module2_weather_holiday_strict.md`(날씨·휴일 기준), `REPORT.md` 2장(EDA 결과)

---

## 1. 담당 역할

Module 2 = **호출 로그를 예측 모델이 학습할 수 있는 피처 테이블로 바꾸는 파트.**
Module 1(시뮬레이션)이 만든 호출 기록을 받아, Module 3(수요 예측)이 쓰는 표를 내보낸다.

역할 기술서 4항목과 현황:

| # | 항목 | 상태 | 산출물 |
|---|---|---|---|
| 1 | `poi_extractor.py` 구역 추출 결과 점검 | 완료 | `eda/poi_zone_map.py`, `data/eda/poi_zone_map.png`, `poi_zone_summary.csv` |
| 2 | `spatial_indexing.py` H3 해상도 판단 | 완료 | `eda/h3_resolution_compare.py`, `data/eda/h3_resolution_compare.png` → EDA상 res 9, 팀 정합성으로 **res 8** 채택 |
| 3 | `time_series_prep.py` 피처 파이프라인 검증 | 완료 | 피처 43개, `tests/test_module2.py` 85개, `scripts/feature_ablation.py` |
| 4 | EDA 시각화 + REPORT 2장 | 완료 | `eda/eda_8w.py`, `data/eda/8w/01~09.png`, `notebooks/03_EDA_8weeks.ipynb`, `REPORT.md` 2.1~2.8 |

---

## 2. 전처리 파이프라인 (module2_preprocessing/)

진입점은 `pipeline.py`의 `build_feature_table()` 하나. 학습(`scripts/train_dispatch_model.py`)과 서빙(`module4_dispatch/forecast_dispatcher.py`)이 **같은 함수**를 써서 피처 정의가 어긋나지 않는다.

```
호출 로그 CSV ─① load_logs ─② H3 격자화 ─③ 5분 집계(0 채움) ─④ 날씨 병합 ─⑤ 피처·타깃 생성 ─▶ 피처 테이블
                                                    ▲
                                          날씨 CSV(data/external/)
```

| 단계 | 파일 | 하는 일 |
|---|---|---|
| ① 읽기 | `pipeline.py` `load_logs()` | `pickup_datetime`·`latitude`·`longitude`만 사용, 숫자 강제 변환, 시각을 naive KST로 통일(`to_naive_kst`), 좌표 결측 행 제거·건수 기록 |
| ② 격자화 | `spatial_indexing.py` | H3 res 8 + Geohash. 고유 좌표만 변환하는 캐시 + chunk 처리 → 10만 행 0.42초 |
| ③ 집계 | `time_series_prep.py` `aggregate_demands()` | 5분 경계로 floor, (셀 × 5분칸) 전체 격자로 reindex, 호출 없는 칸 **demand=0** |
| ④ 외부 데이터 | `external_data_merge.py` | 날씨 관측을 5분 칸에 `merge_asof(backward, tolerance 3h)` — 그 시각 **이전** 관측만 (누수 없음). ≤3h 공백 선형보간 + `weather_interpolated`, 나머지 `weather_missing` |
| ⑤ 피처 | `time_series_prep.py` `create_features()` + `time_features.py` | 셀별 `groupby` 안에서 `shift(1)` 후 계산(현재 값 미포함), 타깃 `y_h1~y_h6` = `shift(-h)` |
| 분할 | `time_series_prep.py` `time_based_split()` | 시간칸 기준 앞 80% / 뒤 20% (행 셔플 금지) |

### 피처 43개

| 묶음 | 개수 | 내용 |
|---|---|---|
| lag | 6 | 5·10·…·30분 전 수요 |
| rolling | 4 | 15분·30분·1시간 평균, 1시간 표준편차 |
| 반복 패턴 | 4 | `same_time_last_week`·`has_last_week`, `same_time_yesterday`·`has_yesterday` |
| 변화량 | 1 | `diff_1` |
| 달력 | 19 | 연·월·일·시·분·minute_of_day·요일·주말·`is_holiday`·`is_public_holiday`·`is_day_before_off`·`is_day_after_off`·`off_streak_len`·계절·time_slot·hour/dow sin·cos |
| 날씨 | 9 | 기온·강수·풍속·`is_rain`·`weather_missing`·`weather_interpolated`·`precip_3h_sum`·`rain_streak_h`·`temp_anomaly_24h` |

### 검증 규칙 (Data Spec 5장) — 위반 시 ValueError로 중단

| 검사 | 위치 |
|---|---|
| (time_bucket, h3_index) 유일, demand 결측·음수, 강수 음수, 5분 경계 정렬 | `validate_panel()` — `create_features` 입구 |
| 시간대 단일(naive 또는 단일 tz), 시각 파싱 실패 | `to_naive_kst()` |
| 기온 −40~50℃ · 강수 0~200mm · 풍속 0~75m/s | `external_data_merge._check_ranges` |
| 관측 간격 추정(5/10/15/20/30/60분)·경계 정렬·중복 시각 | `external_data_merge.infer_step / _check_time_axis` |
| 학습 경로 날씨 결측률 ≤ 5% (`config weather_missing_max_ratio`) | `merge_external_data(max_missing_ratio=)` |
| 공휴일 달력이 데이터 연도를 덮는지 | `time_features.validate_calendar()` |

설정(`config.json`): `freq 5min`, `h3_resolution 8`, `max_lag 6`, `rolling_short 3`, `rolling_long 6`, `forecast_horizons 6`, `weather_missing_max_ratio 0.05`, `timezone Asia/Seoul`, `holiday_country KR`.

---

## 3. EDA 결과 요약 (8주 합성 데이터, 상세는 REPORT.md 2장)

- 데이터: 2026-07-06~08-30, 호출 36,786건, 셀 8개 × 5분칸 16,125개 = 129,000행. 수요 0인 칸 80.1% (희소 카운트).
- 시간 패턴: 피크 8시·18~19시, 금요일 +35%, 주말 −26%. 셀별 피크 시각이 다름(8시 4개·18~19시 3개·12시 1개) → 셀별 groupby 계산의 근거.
- 정상성: ADF 원계열 −18.6, 차분 −24.9 (둘 다 정상). ACF 유의 lag 40개, lag1 0.38, 1일 전 0.31, 1주 전 0.34 → lag 6 + rolling 1h + 계절 피처 조합의 근거.
- 날씨: 강수 5.5mm일 때 호출 +27%(셀·시간대 통제). 기온 효과 없음.
- ablation(시간순 뒤 20% test): 나이브 RMSE 0.766 → 43피처 0.546 (**−29%**). 기여 순서 달력 > rolling/지난주 > lag > 어제 동일칸.
- 한계 발견: 합성 강수가 5분마다 독립 난수(자기상관 0.002, 실측 0.77)라 날씨 피처 기여 0 — 생성기 문제, 피처 설계 문제 아님. 공휴일도 8/15(토) 하나뿐이라 주말과 구분 불가.

---

## 4. 실행 방법

```bash
python tests/test_module2.py                                   # 검증 85개 (h3/holidays 없으면 mock으로 자동 전환)
python -m module2_preprocessing.pipeline --logs data/generated/calls.csv --out data/processed/features.csv
python scripts/feature_ablation.py data/generated/calls.csv --external data/generated/external.csv   # → data/eda/feature_ablation.md
python eda/eda_8w.py                                            # → data/eda/8w/*.png, summary.md
python eda/h3_resolution_compare.py ; python eda/poi_zone_map.py
```

8주 데이터는 `data/generated_8w/`(gitignore)에 두거나 `python scripts/generate_training_data.py --days 56`으로 재생성.

---

## 5. 다른 파트에 전달한 사항

| 대상 | 내용 |
|---|---|
| 2번·4번 (forecast_dispatcher) | 서빙 이력 창이 15칸(75분)이라 `same_time_last_week`·`same_time_yesterday`가 항상 0 → `self.history_buckets = self.prep.required_history_buckets`(1주+3칸)로 수정 권장 |
| 2번·1번 (생성기·외부데이터) | 강수를 시간 단위 에피소드로 생성하거나 실측 시간별 날씨 주입 + 실측 계수(강수 1mm +1.99%, 기온 1℃ +2.73%, `docs/weather_coefficient.md`). 공휴일은 `holidays` 달력으로, 배수는 주말과 다르게 |
| 1번 (train) | `build_feature_table`이 날씨 결측률 5% 초과 시 중단 → sim_logs로 학습 시 그 기간을 덮는 날씨 파일 필요. 5분 관측을 그대로 쓰므로 재학습 필요 |
| 4번 (evaluate) | MAPE가 수요 0 칸에서 폭발 → 0 제외 또는 sMAPE. develop의 evaluate.py 회귀 복구 필요 |
| 5번 (Data Spec) | 3장 피처 34 → 43, `weather_missing` 문구, 5장 날씨 결측 "5% 초과 시 ValueError" |
| 1·2번 (POI) | 주택 POI가 큰길 edge로 매핑됨(주택 전용 edge 4/28), 음식점 edge가 zone 절반 → POI 수 가중 스폰 제안 |

---

## 6. 변경 이력

| 날짜 | 내용 |
|---|---|
| 09-10 | 시뮬 로그 저장(`sim_log_recorder.py`), 수요 0 칸 채움·셀별 rolling 버그 수정, 시간 기준 분할, H3 해상도 비교(res 9 판단), POI zone 검증, 명세 기준 보강(5분·merge_asof·공휴일·타깃 6개·pipeline·테스트 42개·EDA 노트북) |
| 09-13 | develop 통합. H3 res 8로 팀 통일 |
| 09-17 | Data Spec v1.0 반영(유효범위·유일성 검사, freq 5min 복구). NYC 실데이터 전환 시도 후 되돌림(과제 제약 #7: 합성 데이터 사용). 맥 전용 `실행_*.command` 제거. 날씨·휴일 기준 강화 + 파생 피처 9개(34→43), ablation. 8주 EDA 확정·REPORT 2장 재작성 |
| 09-18 | 휴일 테스트를 달력 명시 방식으로 수정(실제 `holidays` 패키지의 대체공휴일 반영), 85/85 PASS (맥 실환경 확인) |
