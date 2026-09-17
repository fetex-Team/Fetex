# 실데이터 전환 — NYC TLC 택시 운행 기록 (Module 2 / 3번 전처리)

작성: 윤세빈, 2026-09-17. 범위는 **3번 파트(전처리 입력 교체)** 까지다. 지도·시뮬레이션(1·2번)과 모델 재학습(4번)은 별도.

## 왜 NYC TLC인가
- 과제 제약(#7)상 실제 카카오 호출 데이터는 쓸 수 없다. NYC TLC Trip Record Data는 공개 자료이고, 2016-06 이전 자료에는 **승차 위경도·시각·결제 상태**가 있어 Module 2 입력 스키마(pickup_datetime, latitude, longitude)로 바로 변환된다.
- Data Specification v1.0 1장이 실데이터 전환 시 3번 몫으로 둔 "취소·중복·테스트 호출을 호출 상태 필드로 집계 전에 제외하고 제외 건수·사유를 로그로 남긴다"를 `scripts/prepare_nyc_tlc.py`가 수행한다.
- 한계: 운행(완료된 승차) 기록이지 '호출' 기록이 아니다 → 취소된 호출은 애초에 없고, `payment_type=6(Voided trip)`을 취소 상당으로 본다. 라이선스는 NYC Open Data(출처 표시).

## 절차 (맥에서)
1. 데이터 내려받기 — 좌표가 있는 2015-01 ~ 2016-06 중 원하는 달. 8주 기준이면 두 달.
   ```
   mkdir -p data/raw/nyc
   curl -o data/raw/nyc/yellow_tripdata_2016-01.parquet https://d37ci6vzurychx.cloudfront.net/trip-data/yellow_tripdata_2016-01.parquet
   curl -o data/raw/nyc/yellow_tripdata_2016-02.parquet https://d37ci6vzurychx.cloudfront.net/trip-data/yellow_tripdata_2016-02.parquet
   pip install pyarrow
   ```
   (`data/raw/`는 .gitignore — 원본은 저장소에 올리지 않는다.)
2. config.json 설정 (팀 규칙: 값 바꾸기 전 톡방 공유)
   ```json
   "region": "NYC(맨해튼)",
   "timezone": "America/New_York",
   "holiday_country": "US"
   ```
   - `timezone`: 호출 로그·날씨 모두 이 현지 시각(naive) 기준으로 통일된다. TLC 시각은 뉴욕 현지 시각이다.
   - `holiday_country`: `is_holiday`에 쓰는 달력. 생략 시 KR.
   - `NYC(맨해튼)` 프리셋 bbox는 타임스퀘어 부근 약 1km²다. 지도(1번)와 같은 영역이어야 하므로, 넓히려면 1번과 함께 정한다.
3. 변환 (제외 규칙 적용 + 건수 기록)
   ```
   python scripts/prepare_nyc_tlc.py data/raw/nyc/yellow_tripdata_2016-0[12].parquet
   # 영역을 직접 줄 때: --bbox 40.70 40.80 -74.02 -73.93   (맨해튼 대부분)
   # No charge(3)/Dispute(4)도 빼려면: --exclude-payment 3,4,6
   ```
   출력: `data/real/nyc/calls_<시작>_<종료>.csv` + `_exclusions.json`(입력/유지/사유별 제외 건수, 파일별, 일별 건수, bbox, 기간).
4. 날씨 (Open-Meteo Archive, 무료·키 불필요, 2016년 제공)
   ```
   python scripts/fetch_weather_history.py 2016-01-01 2016-02-29
   ```
   → `data/external/weather_NYC(맨해튼)_2016-01-01_2016-02-29.csv`. 시각은 config timezone 기준.
5. 피처 테이블
   ```
   python -m module2_preprocessing.pipeline --logs data/real/nyc/calls_2016-01-01_2016-02-29.csv --out data/processed/features_nyc.csv
   ```
   유효범위·유일성 검사(`validate_panel`)가 자동으로 돈다. 실패하면 ValueError 메시지대로 원인부터 잡는다.
6. 검사
   ```
   python tests/test_nyc_tlc.py      # 변환 규칙·건수 기록·파이프라인 통과 (TLC 파일 없이 동작)
   python tests/test_module2.py
   ```

## 제외 규칙과 Data Spec 대응
| 사유 | 규칙 | Data Spec |
|---|---|---|
| duplicate | (승차·하차 시각, 승차·하차 좌표, 요금) 완전 중복. 파일 경계도 한 번 더 검사 | 1장 중복 호출, 5장 유일성 |
| voided | payment_type ∈ {6} (옵션으로 3, 4 추가) | 1장 취소 호출(상태 필드) |
| missing_coords | 위경도 결측 또는 (0,0) | 5장 완전성 |
| out_of_bbox | 대상 영역 밖 | 5장 공간 일관성(1번과 같은 bbox) |
| bad_time | 승차 파싱 실패, 하차 ≤ 승차, 24h 초과 | 5장 유효범위 |
| suspect_test | 승객 0명, 거리 ≤ 0, 요금 ≤ 0 | 1장 테스트 호출 |

한 행은 첫 번째로 걸린 사유 하나로만 집계된다. 피크 시간대의 큰 호출 수는 이상치로 지우지 않는다(5장 이상치 원칙).

## 이 전환으로 다른 파트에서 바뀌어야 하는 것 (참고)
- 1번: SUMO 지도를 NYC bbox로 생성해야 `forecast_dispatcher`의 셀 목록 검사가 통과한다. 날씨는 위 4단계로 대체 가능.
- 2번: 시뮬레이션 호출을 실데이터 재생(replay)으로 바꿀지 결정. `data/generate.py`의 합성 생성은 그대로 두고 로그만 교체하는 방식이 가장 적다.
- 4번: `scripts/train_dispatch_model.py`를 `data/real/nyc/calls_*.csv`로 재학습. 5분×격자 희소도가 합성보다 낮을 수 있으니(맨해튼은 수요가 훨씬 많음) 지표 해석 주의. 이벤트·교통지수 피처는 TLC에 없다.
- 5번(문서): Data Spec 2장 출처 표(택시 호출 = SUMO 합성 → NYC TLC 실측 추가), 4장 규모·분포 수치, 9장 라이선스(NYC Open Data) 갱신.
