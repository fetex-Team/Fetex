# Module 2 (시공간 데이터 처리 / 전처리·EDA) 작업 매뉴얼

담당: 윤세빈 (역할 3번 — 전처리/EDA)
기준 문서: 과제 명세 "28_DT_카카오모빌리티" Module 2 기능 요구 + 최종 결과물 + 동료평가·인터뷰 질문
작업 브랜치: `feature/preprocessing`

> 이 문서는 "무엇을 왜 하는지 → 어디까지 하면 완료인지 → 어떻게 확인하는지"를 태스크별로 고정한다.
> 각 태스크의 **최소 완료 조건(DoD)** 을 전부 만족하면 그 태스크는 닫는다. 조건에 없는 건 하지 않는다.

---

## 0. 명세가 Module 2에 요구하는 것 (근거)

| # | 명세 원문 (요약) | 대응 태스크 |
|---|---|---|
| R1 | 위도/경도를 **Geohash 또는 H3**로 격자 매핑 | T1 |
| R2 | 타임스탬프를 **연·월·일·시·분·요일**로 분해 + **공휴일 여부** 추가 | T2 |
| R3 | **지난 1시간 평균, 지난주 동일 시간대, 이동평균** 등 시계열 피처 | T3 |
| R4 | 기상·교통량·이벤트 등 **외부 데이터 결합** + **결측치 처리** | T4 |
| R5 | Module 3 입력: t까지의 데이터로 **t+1~t+6 (5분 단위)** 예측 → 입력/타겟 시퀀스 정의 | T3 |
| R6 | 최종 결과물: `module2_preprocessing/` 코드, `notebooks/` EDA 노트북, `data/` 샘플·로드 스크립트 | T5, T6 |
| R7 | REPORT: 시공간 데이터 특성 분석(EDA) 및 시각화 — 시간대/요일/지역별 히트맵 | T5 |
| Q1 | (평가) 공간 인덱싱 선택 이유, **대용량 처리 효율화** 방식 | T1 |
| Q2 | (평가) 파생 변수 생성과 **유효성 검증** 방법 | T3, T5 |
| Q3 | (평가) 시공간 단위가 다른 외부 데이터 **병합 로직** | T4 |
| Q4 | (인터뷰) 대규모 데이터 성능·메모리 이슈 해결 | T1, T6 |
| Q5 | (인터뷰) 시계열 **정상성(Stationarity)** 확인·처리 | T5 |

---

## T0. 입력 데이터 준비 (전제)

**목적**: Module 2의 입력은 "호출 로그 CSV"다. 어디서 오든 같은 스키마여야 한다.

**입력 스키마** (`data/sim_logs/demand_log_*.csv`, 1행 = 호출 1건)

| 컬럼 | 설명 |
|---|---|
| `pickup_datetime` | 호출 시각 (YYYY-MM-DD HH:MM:SS) |
| `latitude`, `longitude` | 호출 위치 |
| (선택) `origin_zone`, `dest_category`, `wait_sec`, `status` | 시뮬 로그일 때만 존재. Module 2는 없어도 동작해야 함 |

**데이터 소스 2종**
- (A) SUMO 시뮬 로그: `python module1_simulation/build_env.py` → `python measure_wait_time.py`가 `data/sim_logs/`에 생성. 파이프라인 검증·배차(M4) 연동용.
- (B) 공개 데이터(명세 제약 7 허용): 뉴욕 옐로택시 등. 다일·다요일·날씨 상관 EDA용. **팀 결정 필요** — 결정 전까지 코드는 (A)(B) 모두 같은 스키마로 받도록 만든다.

**최소 완료 조건**
- [ ] `data/sim_logs/`에 로그 CSV 1개 이상 존재 (현재: `demand_log_20260910_180831_patrol.csv`, 2,870건)
- [ ] `data/README.md`에 데이터셋 설명(소스·기간·컬럼·생성 방법) 존재
- [ ] 여러 로그 파일을 이어 붙여도(다일) 파이프라인이 그대로 동작

---

## T1. 공간 인덱싱 (R1, Q1, Q4)

**해야 할 것**
1. H3 resolution 결정 근거 문서화 (완료: `data/eda/h3_resolution_summary.csv`, res 9)
2. Geohash 병행 출력 (명세가 "Geohash 또는 H3"라 둘 다 제공, 비교 가능하게)
3. 대용량 효율화: 행 단위 `df.apply` → **중복 좌표 캐시 + 리스트 컴프리헨션** (같은 좌표 반복 계산 제거)
4. 벤치마크: 10만 행 기준 변환 시간 측정 함수 제공 → 인터뷰 Q1/Q4 답변 숫자

**최소 완료 조건**
- [ ] `SpatialIndexer.process_dataframe()`이 `h3_index`, `geohash` 두 컬럼을 생성
- [ ] 같은 입력에 대해 개선 전(`apply`)/후 결과가 동일 (테스트)
- [ ] `python -m module2_preprocessing.spatial_indexing --bench 100000` 실행 시 처리 시간 출력 (초)
- [ ] H3 res 선택 근거가 코드 주석 + `docs/`에 남아 있음

**확인 방법**: `python tests/test_module2.py` (test_spatial_*) 통과

---

## T2. 시간 피처 (R2)

**해야 할 것**
- `time_features.py` 신규: `add_time_features(df, ts_col)` →
  `year, month, day, hour, minute, dayofweek, is_weekend, is_holiday, season, time_slot, minute_of_day, hour_sin, hour_cos, dow_sin, dow_cos`
- `is_holiday`: `holidays` 라이브러리 KR 달력 (없으면 주말만 True로 폴백하되 경고 출력)
- `time_slot`: 새벽/출근피크/낮/점심/퇴근피크/저녁/심야 — 시뮬 규칙의 시간창(`config` school/company/lunch/evening 시각)과 일치시켜 해석 가능하게

**최소 완료 조건**
- [ ] 위 컬럼 전부 생성, 타입 정수/실수 (문자열 카테고리는 `time_slot`만, 모델 입력 시 원-핫 또는 코드화)
- [ ] 2026-09-07(월)은 `dayofweek=0, is_weekend=0`, 2026-10-03(개천절)은 `is_holiday=1` (테스트)
- [ ] `hour_sin²+hour_cos²≈1` (순환 인코딩 검증)

---

## T3. 시계열 피처 + 예측 타겟 (R3, R5, Q2)

**해야 할 것**
1. 집계 단위 `freq=5min` (명세 M3: 5분 단위). config 기본값 변경 → **팀 공지 필요**
2. (셀 × 5분칸) 전체 격자로 0 채우기 (완료)
3. 피처
   - `lag_1..lag_k` (k = `max_lag`, 기본 12 = 1시간)
   - `rolling_mean_1h` (직전 12칸 평균 — "지난 1시간 평균"), `rolling_mean_30m`
   - `same_time_last_week` (7일 전 같은 칸) + `has_last_week` 플래그 (데이터가 1주 미만이면 0)
   - `diff_1` (직전 칸 대비 증감)
   - 모두 **현재 칸 값을 쓰지 않음** (shift(1) 후 계산) → 누수 없음
4. 타겟: `y_h1..y_h6` = t+1~t+6 칸 수요 (셀 내부 shift(-h)). 미래 6칸이 없는 마지막 행은 제거
5. train/val/test 시간 순서 분할 (`time_based_split`, 완료) — val 추가

**최소 완료 조건**
- [ ] 수작업 계산과 `lag_1`, `rolling_mean_1h`, `y_h1..y_h6` 값 일치 (테스트, 셀 2개 이상)
- [ ] 셀 경계에서 다른 셀 값이 섞이지 않음 (테스트)
- [ ] 피처 컬럼에 현재/미래 정보 없음: `demand`와 완전 동일한 피처 없음, `y_h*`는 피처 목록에서 제외 (테스트)
- [ ] 1주 미만 데이터에서도 에러 없이 동작 (`same_time_last_week` = NaN→0, 플래그 0)

---

## T4. 외부 데이터 결합 + 결측치 처리 (R4, Q3)

**해야 할 것**
1. 날씨 실데이터: `scripts/fetch_weather_history.py` — Open-Meteo Archive API(무료, 키 불필요)로 지역 중심 좌표의 **시간별** 기온·강수·풍속 수집 → `data/external/weather_<지역>_<시작>_<끝>.csv`
2. 병합 로직 (`external_data_merge.py` 재작성)
   - 날씨는 **1시간 단위**, 수요는 **5분 단위** → `pd.merge_asof(direction='backward')`로 "그 시각 이전 가장 최근 관측" 붙임 (미래 정보 누수 없음)
   - 결측: 관측 공백 ≤3시간은 선형 보간, 그 이상은 NaN 유지 + `weather_missing=1` 플래그; 파일 자체가 없으면 `config`의 현재 기온으로 상수 채움 + `weather_source='fallback'`
   - 파생: `is_rain` (강수 ≥0.1mm)
3. 공휴일은 T2의 `is_holiday`로 결합 (외부 달력 데이터)
4. 기존 `np.random` 날씨 생성 코드 **삭제** (평가 시 치명적)

**최소 완료 조건**
- [ ] 랜덤값 생성 코드 없음 (grep `np.random` 결과 0)
- [ ] 시간별 날씨 3개 관측(09:00, 10:00, 11:00)을 5분칸에 붙였을 때 09:05~09:55는 09:00 값, 10:00부터 10:00 값 (테스트)
- [ ] 관측 1개를 지운 뒤 보간값·플래그가 예상대로 (테스트)
- [ ] 날씨 파일 없이도 파이프라인 정상 종료 + 경고 메시지

---

## T5. EDA 노트북 (R6, R7, Q2, Q5)

**해야 할 것** — `notebooks/01_EDA_and_Spatial.ipynb` (실행 완료 상태로 커밋)
1. 데이터 개요: 건수, 기간, 셀 수, 결측
2. 시간 패턴: 5분/시간대별 수요 곡선, (다일 데이터면) 시간대×요일 히트맵
3. 공간 패턴: 셀별 총수요 상위 표, 셀×시간 히트맵 (`eda/h3_resolution_compare.py` 재사용), H3 res 비교표
4. POI zone 검증 요약 (`eda/poi_zone_map.py` 결과)
5. **정상성**: 전체 수요 시계열 ADF 검정 p-value + 1차 차분 후 재검정 → 결론 한 줄
6. **자기상관**: ACF 그래프 (lag 몇 개까지 유의한지 → `max_lag` 근거)
7. **피처 유효성 검증** (Q2): 피처 그룹 ablation — {lag만, +rolling, +calendar, +weather}로 XGBoost 학습, test RMSE/MAE 표 → "어떤 피처가 얼마나 기여했나"
8. 외부 변수 상관: 기온·강수·요일 vs 수요 (데이터가 1일이면 "현재 데이터로는 판단 불가, 다일 데이터 필요" 명시)

**최소 완료 조건**
- [ ] 노트북이 `jupyter nbconvert --execute`로 에러 없이 끝까지 실행됨
- [ ] 위 8개 섹션 각각 그림 또는 표 1개 이상 + 결론 문장 1개 이상
- [ ] ADF p-value, ACF 유의 lag 수, ablation 표가 숫자로 존재
- [ ] 데이터 한계(1일치)에서 나온 결론은 "예비"로 표시

---

## T6. 파이프라인 통합 · 테스트 · 문서 (R6, Q4)

**해야 할 것**
1. `module2_preprocessing/pipeline.py`: 단일 진입점
   `build_feature_table(log_paths, freq, horizons, weather_path) -> DataFrame`
   CLI: `python -m module2_preprocessing.pipeline --logs data/sim_logs/*.csv --out data/processed/features.csv`
2. `train.py`가 `pipeline`을 호출 (전처리 코드 중복 제거)
3. `tests/test_module2.py`: T1~T4 최소 완료 조건을 그대로 테스트로 (외부 패키지 없는 환경에서도 돌게 mock 지원)
4. 문서: `README.md` Module 2 실행법 섹션, `data/README.md`, `REPORT.md` EDA 섹션 초안(노트북 결과 요약)

**최소 완료 조건**
- [ ] `python tests/test_module2.py` 전부 PASS
- [ ] CLI 한 줄로 로그 → 피처 CSV 생성, 출력 요약(행 수·셀 수·기간·피처 목록) 표시
- [ ] `train.py`가 pipeline 출력으로 학습 (기존과 동일 결과 재현)
- [ ] README에 "Module 2 실행 순서" 3줄 이상

---

## T7. 데이터 확대 (팀 결정 필요 — Module 2 단독으로 못 닫음)

현재 로그: 1일 × 2시간 → 5분칸 24개. `lag_12` + `y_h6` 제거 후 셀당 6행. R3의 "지난주 동일 시간대", R7의 요일별 히트맵, R4의 날씨 상관은 **최소 2주치**가 있어야 의미가 있다.

선택지
- (a) 시뮬 다일 실행: `sim_date`를 바꿔가며 7~22시 × 14일 → 실행 시간 큼(1일 15시간분 ≈ 1시간+), 요일·날씨 효과는 시뮬 규칙에 없으므로 여전히 0
- (b) 공개 데이터(NYC TLC 옐로택시 + NOAA/Open-Meteo 날씨) 1~3개월: 명세 허용, 실제 주간·날씨 패턴 존재, 파이프라인은 입력 CSV만 교체

**최소 완료 조건**: 팀 회의에서 (a)/(b) 결정 → `data/README.md`에 기록 → 해당 데이터로 T5 노트북 재실행

---

## 실행 순서 (맥)

```bash
cd ~/Desktop/20263567/CodeSay20263600/FeTex
source ../20250907ver/venv/bin/activate
pip install holidays statsmodels jupyter        # 최초 1회
python tests/test_module2.py                    # T1~T4 검증
python scripts/fetch_weather_history.py         # T4 날씨 수집 (네트워크)
python -m module2_preprocessing.pipeline --logs "data/sim_logs/*.csv" --out data/processed/features.csv
python eda/h3_resolution_compare.py && python eda/poi_zone_map.py   # T5 H3/POI 그림
jupyter nbconvert --to notebook --execute --inplace notebooks/01_EDA_and_Spatial.ipynb   # T5 노트북 실행
python train.py                                 # M3 연동 확인
```
