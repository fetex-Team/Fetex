"""
[Module 2 / 실데이터 전환] scripts/prepare_nyc_tlc.py 검사 — TLC 형식의 작은 합성 표로 제외 규칙·건수 기록·파이프라인 통과를 확인한다.

실행: python tests/test_nyc_tlc.py
- 실제 TLC 파일 없이 동작 (csv 경로). h3/pygeohash/holidays 없는 환경은 tests/test_module2.py와 같은 대체 구현 사용.
- 실제 parquet 검사는 맥에서: python scripts/prepare_nyc_tlc.py data/raw/nyc/yellow_tripdata_2016-01.parquet
"""
import os, sys, json, types, warnings, tempfile
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT); sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
warnings.filterwarnings("ignore")
import test_module2  # noqa: F401  (h3/pygeohash/holidays 대체 구현 + 네트워크 차단 셋업 재사용)
from test_module2 import check, RESULTS, MOCKED

import numpy as np
import pandas as pd
from config_loader import CFG, REGION_PRESETS
from scripts.prepare_nyc_tlc import convert, apply_exclusions, standardize, _resolve_columns, EXCLUSION_ORDER

NYC = REGION_PRESETS["NYC(맨해튼)"]
BBOX = {k: NYC[k] for k in ("lat_min", "lat_max", "lng_min", "lng_max")}
LAT, LNG = 40.760, -73.983   # bbox 안 (타임스퀘어 부근)


def _tlc_frame():
    """2016-01 yellow 형식. 정상 8일치 + 제외 대상 각 유형."""
    rng = np.random.default_rng(7)
    t0 = pd.Timestamp("2016-01-04 00:00")                       # 월요일
    n_ok = 8 * 24 * 6                                           # 8일 × 시간당 6건
    pick = t0 + pd.to_timedelta(rng.integers(0, 8 * 24 * 3600, n_ok), unit="s")
    ok = pd.DataFrame({
        "VendorID": rng.choice([1, 2], n_ok),
        "tpep_pickup_datetime": pick,
        "tpep_dropoff_datetime": pick + pd.to_timedelta(rng.integers(120, 3600, n_ok), unit="s"),
        "passenger_count": rng.integers(1, 5, n_ok),
        "trip_distance": rng.uniform(0.3, 8, n_ok).round(2),
        "pickup_longitude": LNG + rng.uniform(-0.005, 0.005, n_ok),
        "pickup_latitude": LAT + rng.uniform(-0.004, 0.004, n_ok),
        "dropoff_longitude": LNG + rng.uniform(-0.05, 0.05, n_ok),
        "dropoff_latitude": LAT + rng.uniform(-0.05, 0.05, n_ok),
        "payment_type": rng.choice([1, 2], n_ok),
        "fare_amount": rng.uniform(4, 40, n_ok).round(2),
    })
    base = ok.iloc[0].to_dict()
    counter = [0]
    def row(**kw):
        # 각 제외 대상 행은 승차·하차 시각을 서로 다르게 해 '중복' 규칙에 먼저 걸리지 않게 한다
        counter[0] += 1
        shift = pd.Timedelta(minutes=counter[0])
        r = dict(base); r["tpep_pickup_datetime"] = base["tpep_pickup_datetime"] + shift
        r["tpep_dropoff_datetime"] = base["tpep_dropoff_datetime"] + shift; r.update(kw); return r
    bad = pd.DataFrame([
        ok.iloc[5].to_dict(), ok.iloc[5].to_dict(),                                 # duplicate ×2 (한 건만 중복으로 집계)
        row(payment_type=6),                                                        # voided
        row(pickup_latitude=0.0, pickup_longitude=0.0),                             # missing_coords (0,0)
        row(pickup_latitude=np.nan),                                                # missing_coords NaN
        row(pickup_latitude=40.70, pickup_longitude=-74.01),                        # out_of_bbox
        row(tpep_dropoff_datetime=base["tpep_pickup_datetime"] - pd.Timedelta(minutes=30)),  # bad_time (하차 < 승차)
        row(tpep_dropoff_datetime=base["tpep_pickup_datetime"] + pd.Timedelta(hours=30)),    # bad_time (24h 초과)
        row(passenger_count=0),                                                     # suspect_test
        row(trip_distance=0.0),                                                     # suspect_test
        row(fare_amount=-5.0),                                                      # suspect_test
    ])
    return pd.concat([ok, bad], ignore_index=True), n_ok


def test_convert():
    df, n_ok = _tlc_frame()
    tmp = tempfile.mkdtemp()
    src = os.path.join(tmp, "yellow_tripdata_2016-01.csv"); df.to_csv(src, index=False)
    out_dir = os.path.join(tmp, "out")
    totals = convert([src], BBOX, out_dir=out_dir, verbose=False)
    ex = totals["by_reason"]
    check("NYC 컬럼 매핑(tpep_*, pickup_latitude → 표준명)", {"pickup_datetime", "latitude", "longitude", "payment_type"} <= set(_resolve_columns(df.columns)))
    check("NYC 중복 2건 제외(원본 1건은 유지)", ex["duplicate"] == 2, str(ex))
    check("NYC voided(payment_type 6) 1건 제외", ex["voided"] == 1, str(ex))
    check("NYC 좌표 결측/(0,0) 2건 제외", ex["missing_coords"] == 2, str(ex))
    check("NYC bbox 밖 1건 제외", ex["out_of_bbox"] == 1, str(ex))
    check("NYC 시각 오류 2건 제외", ex["bad_time"] == 2, str(ex))
    check("NYC 테스트/무효 의심 3건 제외", ex["suspect_test"] == 3, str(ex))
    check("NYC 유지 건수 == 정상 건수", totals["kept_rows"] == n_ok, f"{totals['kept_rows']} vs {n_ok}")
    check("NYC 입력 == 유지 + 제외", totals["input_rows"] == totals["kept_rows"] + sum(ex.values()))
    out = pd.read_csv(totals["output"])
    check("NYC 출력에 파이프라인 필수 3컬럼 + request_id", {"request_id", "pickup_datetime", "latitude", "longitude"} <= set(out.columns))
    check("NYC request_id 유일", out["request_id"].is_unique)
    check("NYC 출력 좌표 전부 bbox 안", out.latitude.between(BBOX["lat_min"], BBOX["lat_max"]).all() and out.longitude.between(BBOX["lng_min"], BBOX["lng_max"]).all())
    js = json.load(open(os.path.join(out_dir, os.path.basename(totals["output"]).replace(".csv", "_exclusions.json")), encoding="utf-8"))
    check("NYC 제외 건수·사유·기간·일별 건수 JSON 기록", js["by_reason"] == ex and js["days"] == 8 and len(js["daily_counts"]) == 8)
    check("NYC 원본 파일 변경 없음", len(pd.read_csv(src)) == len(df))
    return totals["output"]


def test_pipeline_on_nyc(calls_csv):
    from module2_preprocessing.pipeline import build_feature_table
    from module2_preprocessing.time_features import add_time_features
    saved = CFG.copy()
    try:
        CFG.update({"region": "NYC(맨해튼)", **NYC, "timezone": "America/New_York", "holiday_country": "US", "freq": "5min"})
        feat = build_feature_table([calls_csv], weather_path="/nonexistent.csv", verbose=False)
        check("NYC 로그 → 피처 테이블 생성(5min, 34피처)", len(feat) > 0 and len(feat.attrs["feature_cols"]) == 34, str(len(feat.attrs["feature_cols"])))
        check("NYC 타깃 6개·0 이상", feat.attrs["target_cols"] == [f"y_h{h}" for h in range(1, 7)] and (feat[feat.attrs["target_cols"]] >= 0).all().all())
        check("NYC 8일치 → 2주차 행에 지난주 피처 존재", (feat["has_last_week"] == 1).any())
        check("NYC 날씨 없음 → fallback + weather_missing=1 (학습 전 fetch_weather_history 필요)", (feat["weather_source"] == "fallback").all() and (feat["weather_missing"] == 1).all())
        if "holidays" not in "".join(MOCKED):   # 실제 holidays 패키지가 있을 때만 미국 달력 검증
            f = add_time_features(pd.DataFrame({"time_bucket": pd.to_datetime(["2016-01-18 09:00", "2016-01-19 09:00"])}))
            check("NYC 공휴일: 2016-01-18(MLK Day)=1, 01-19=0", f.loc[0, "is_holiday"] == 1 and f.loc[1, "is_holiday"] == 0)
        # tz-aware(UTC) 시각도 America/New_York 현지 naive로 통일되는지
        from module2_preprocessing.time_series_prep import to_naive_kst
        utc = pd.Series(pd.to_datetime(["2016-01-04 05:00"]).tz_localize("UTC"))
        check("NYC timezone: UTC 05:00 → 현지 00:00", to_naive_kst(utc).iloc[0] == pd.Timestamp("2016-01-04 00:00"))
    finally:
        CFG.clear(); CFG.update(saved)


if __name__ == "__main__":
    RESULTS.clear()
    if MOCKED:
        print(f"[안내] 대체 구현 사용: {', '.join(MOCKED)}\n")
    try:
        out = test_convert()
        test_pipeline_on_nyc(out)
    except Exception as e:
        import traceback; traceback.print_exc()
        check("NYC 테스트 실행", False, f"{type(e).__name__}: {e}")
    n_ok = sum(1 for _, ok, _ in RESULTS if ok)
    print(f"\n===== {n_ok}/{len(RESULTS)} PASS =====")
    sys.exit(0 if n_ok == len(RESULTS) else 1)
