# -*- coding: utf-8 -*-
"""
[Module 2 / 실데이터 전환] NYC TLC 택시 운행 기록 → Module 2 호출 로그(pickup_datetime, latitude, longitude, ...) 변환.

Data Specification v1.0 1장 "취소·중복 처리": 실데이터 전환 시 호출 상태 필드로 집계 전에 제외하고
제외 건수·사유를 로그로 남긴다 (담당 3번). 이 스크립트가 그 단계다.

입력: TLC Yellow/Green 운행 기록 parquet 또는 csv (좌표가 있는 2016-06 이전 자료)
      https://www.nyc.gov/site/tlc/about/tlc-trip-record-data.page
      예) https://d37ci6vzurychx.cloudfront.net/trip-data/yellow_tripdata_2016-01.parquet
      ※ 2016-07 이후 자료는 위경도 대신 PULocationID(택시 존)만 있어 --zone-centroids 가 필요하다.
출력: data/real/nyc/calls_<시작일>_<종료일>.csv  (+ 같은 이름의 _exclusions.json 에 제외 건수·사유)
      컬럼: request_id, pickup_datetime, latitude, longitude, dropoff_datetime, vendor_id, payment_type,
            passenger_count, trip_distance   ← 앞 3개가 module2_preprocessing.pipeline 의 필수 컬럼

제외 규칙 (Data Spec 5장 "완전성·유효범위" + 1장 "취소·중복·테스트 호출")
  1) duplicate      : (승차시각, 하차시각, 승차좌표, 하차좌표, 요금) 완전 중복 행 — 같은 운행이 두 번 기록된 것
  2) voided         : payment_type ∈ EXCLUDE_PAYMENT (기본 {6: Voided trip}) — TLC에 '취소' 필드는 없고 무효 처리가 이에 해당
  3) missing_coords : 승차 위경도 결측 또는 (0, 0)
  4) out_of_bbox    : 승차 좌표가 대상 영역(bbox) 밖 — 지도·H3 격자와 맞추기 위함
  5) bad_time       : 승차시각 파싱 실패, 하차 ≤ 승차, 운행 24시간 초과
  6) suspect_test   : passenger_count == 0 또는 trip_distance ≤ 0 또는 fare_amount ≤ 0 — 미터기 테스트/무효 운행 의심
  규칙은 위 순서로 적용되며 한 행은 첫 번째로 걸린 사유 하나로만 집계된다.
  ※ 피크 시간대의 큰 호출 수는 이상치로 삭제하지 않는다 (Data Spec 5장 이상치 원칙) — 여기서는 행 단위 유효성만 본다.

사용:
    python scripts/prepare_nyc_tlc.py data/raw/nyc/yellow_tripdata_2016-01.parquet [더 많은 파일...]
    python scripts/prepare_nyc_tlc.py data/raw/nyc/*.parquet --bbox 40.70 40.80 -74.02 -73.93   # 맨해튼 전체
    python scripts/prepare_nyc_tlc.py ... --exclude-payment 3,4,6                              # No charge/Dispute도 제외
  bbox 생략 시 config.json의 region(예: "NYC(맨해튼)") 프리셋 bbox 사용.
  parquet 읽기에는 pyarrow 필요: pip install pyarrow
"""
import os
import sys
import json
import glob
import argparse
import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from config_loader import CFG  # noqa: E402

OUT_DIR = os.path.join(ROOT, "data", "real", "nyc")
EXCLUDE_PAYMENT_DEFAULT = {6}          # TLC payment_type: 1 Credit, 2 Cash, 3 No charge, 4 Dispute, 5 Unknown, 6 Voided trip
MAX_TRIP_HOURS = 24
BATCH_ROWS = 1_000_000

# TLC 컬럼명은 연도·서비스(yellow/green)마다 조금씩 다르다 → 표준 이름으로 매핑
COLUMN_ALIASES = {
    "pickup_datetime":  ["tpep_pickup_datetime", "lpep_pickup_datetime", "pickup_datetime", "Trip_Pickup_DateTime"],
    "dropoff_datetime": ["tpep_dropoff_datetime", "lpep_dropoff_datetime", "dropoff_datetime", "Trip_Dropoff_DateTime"],
    "latitude":         ["pickup_latitude", "Pickup_latitude", "Start_Lat"],
    "longitude":        ["pickup_longitude", "Pickup_longitude", "Start_Lon"],
    "dropoff_latitude": ["dropoff_latitude", "Dropoff_latitude", "End_Lat"],
    "dropoff_longitude": ["dropoff_longitude", "Dropoff_longitude", "End_Lon"],
    "pu_location_id":   ["PULocationID", "pulocationid"],
    "vendor_id":        ["VendorID", "vendor_id", "vendor_name"],
    "payment_type":     ["payment_type", "Payment_Type", "Payment_type"],
    "passenger_count":  ["passenger_count", "Passenger_Count"],
    "trip_distance":    ["trip_distance", "Trip_Distance"],
    "fare_amount":      ["fare_amount", "Fare_Amt", "Fare_amount"],
}
EXCLUSION_ORDER = ["duplicate", "voided", "missing_coords", "out_of_bbox", "bad_time", "suspect_test"]


def _resolve_columns(available) -> dict:
    """파일에 실제로 있는 컬럼명 → 표준 이름 매핑."""
    lower = {c.lower(): c for c in available}
    found = {}
    for std, cands in COLUMN_ALIASES.items():
        for c in cands:
            if c.lower() in lower:
                found[std] = lower[c.lower()]
                break
    return found


def _iter_frames(path: str, columns: list):
    """parquet/csv 를 배치 단위로 읽는다 (한 달 1,100만 행도 메모리 상한 유지)."""
    if path.lower().endswith(".parquet"):
        import pyarrow.parquet as pq
        pf = pq.ParquetFile(path)
        for batch in pf.iter_batches(batch_size=BATCH_ROWS, columns=columns):
            yield batch.to_pandas()
    else:
        for chunk in pd.read_csv(path, usecols=columns, chunksize=BATCH_ROWS):
            yield chunk


def _load_zone_centroids(path: str) -> pd.DataFrame:
    z = pd.read_csv(path)
    need = {"LocationID", "latitude", "longitude"}
    if not need <= set(z.columns):
        raise ValueError(f"{path}: {sorted(need)} 컬럼이 필요합니다 (택시 존 shapefile 중심점)")
    return z[["LocationID", "latitude", "longitude"]].drop_duplicates("LocationID")


def standardize(df: pd.DataFrame, colmap: dict, zone_centroids: pd.DataFrame = None) -> pd.DataFrame:
    """표준 컬럼명으로 바꾸고 없는 컬럼은 NaN으로 채운다."""
    out = pd.DataFrame(index=df.index)
    for std, src in colmap.items():
        out[std] = df[src].values
    for std in COLUMN_ALIASES:
        if std not in out.columns:
            out[std] = np.nan
    if out["latitude"].isna().all() and zone_centroids is not None and out["pu_location_id"].notna().any():
        # 2016-07 이후 자료: 택시 존 ID → 존 중심점 좌표 (존 단위 해상도임을 request_id 접두어에 남긴다)
        m = out[["pu_location_id"]].merge(zone_centroids, left_on="pu_location_id", right_on="LocationID", how="left")
        out["latitude"] = m["latitude"].values; out["longitude"] = m["longitude"].values
        out["coord_source"] = "zone_centroid"
    else:
        out["coord_source"] = "gps"
    return out


def apply_exclusions(df: pd.DataFrame, bbox: dict, exclude_payment: set) -> tuple:
    """제외 규칙 적용. 반환: (남은 행, 사유별 건수 dict). 한 행은 첫 번째로 걸린 사유 하나로만 집계."""
    reason = pd.Series(pd.NA, index=df.index, dtype="object")

    def mark(mask, name):
        mask = mask & reason.isna()
        reason[mask] = name

    pickup = pd.to_datetime(df["pickup_datetime"], errors="coerce")
    dropoff = pd.to_datetime(df["dropoff_datetime"], errors="coerce")
    lat = pd.to_numeric(df["latitude"], errors="coerce"); lng = pd.to_numeric(df["longitude"], errors="coerce")

    # 1) 완전 중복 (기록 중복 — 같은 운행이 두 번 실린 것)
    key_cols = [c for c in ("pickup_datetime", "dropoff_datetime", "latitude", "longitude",
                            "dropoff_latitude", "dropoff_longitude", "fare_amount") if c in df.columns]
    mark(df.duplicated(subset=key_cols, keep="first"), "duplicate")
    # 2) 무효 처리된 운행 (취소에 해당)
    pay = pd.to_numeric(df["payment_type"], errors="coerce")
    mark(pay.isin(list(exclude_payment)), "voided")
    # 3) 좌표 결측 / (0,0)
    mark(lat.isna() | lng.isna() | ((lat.abs() < 1e-6) & (lng.abs() < 1e-6)), "missing_coords")
    # 4) 대상 영역 밖
    mark(~((lat >= bbox["lat_min"]) & (lat <= bbox["lat_max"]) & (lng >= bbox["lng_min"]) & (lng <= bbox["lng_max"])), "out_of_bbox")
    # 5) 시각 오류
    dur_h = (dropoff - pickup).dt.total_seconds() / 3600
    mark(pickup.isna() | (dropoff.notna() & (dur_h <= 0)) | (dur_h > MAX_TRIP_HOURS), "bad_time")
    # 6) 테스트/무효 운행 의심
    pc = pd.to_numeric(df["passenger_count"], errors="coerce")
    dist = pd.to_numeric(df["trip_distance"], errors="coerce")
    fare = pd.to_numeric(df["fare_amount"], errors="coerce")
    mark((pc.notna() & (pc <= 0)) | (dist.notna() & (dist <= 0)) | (fare.notna() & (fare <= 0)), "suspect_test")

    counts = {r: int((reason == r).sum()) for r in EXCLUSION_ORDER}
    keep = df[reason.isna()].copy()
    keep["pickup_datetime"] = pickup[reason.isna()]
    keep["dropoff_datetime"] = dropoff[reason.isna()]
    keep["latitude"] = lat[reason.isna()]; keep["longitude"] = lng[reason.isna()]
    return keep, counts


def convert(paths, bbox: dict, exclude_payment: set = None, zone_centroids_path: str = None,
            out_dir: str = OUT_DIR, verbose: bool = True) -> dict:
    exclude_payment = set(exclude_payment) if exclude_payment is not None else set(EXCLUDE_PAYMENT_DEFAULT)
    files = []
    for p in paths:
        files += sorted(glob.glob(p)) if any(ch in p for ch in "*?[") else [p]
    if not files:
        raise FileNotFoundError(f"입력 파일 없음: {paths}")
    zones = _load_zone_centroids(zone_centroids_path) if zone_centroids_path else None

    kept_parts, totals = [], {"input_rows": 0, "kept_rows": 0, "by_reason": {r: 0 for r in EXCLUSION_ORDER}, "by_file": {}}
    for f in files:
        # 파일의 컬럼 목록만 먼저 읽어 매핑
        if f.lower().endswith(".parquet"):
            import pyarrow.parquet as pq
            available = pq.ParquetFile(f).schema.names
        else:
            available = list(pd.read_csv(f, nrows=0).columns)
        colmap = _resolve_columns(available)
        if "pickup_datetime" not in colmap:
            raise ValueError(f"{f}: 승차 시각 컬럼을 찾지 못했습니다 (컬럼: {available[:10]}...)")
        if "latitude" not in colmap and "pu_location_id" not in colmap:
            raise ValueError(f"{f}: 승차 위경도도 PULocationID도 없습니다.")
        if "latitude" not in colmap and zones is None:
            raise ValueError(f"{f}: 위경도가 없는 자료(2016-07 이후)입니다. --zone-centroids <택시존 중심점 csv> 를 지정하세요.")

        n_in, n_keep, counts = 0, 0, {r: 0 for r in EXCLUSION_ORDER}
        for chunk in _iter_frames(f, list(colmap.values())):
            std = standardize(chunk, colmap, zones)
            keep, c = apply_exclusions(std, bbox, exclude_payment)
            n_in += len(std); n_keep += len(keep)
            for r in EXCLUSION_ORDER:
                counts[r] += c[r]
            kept_parts.append(keep)
        totals["by_file"][os.path.basename(f)] = {"input_rows": n_in, "kept_rows": n_keep, "excluded": counts}
        totals["input_rows"] += n_in; totals["kept_rows"] += n_keep
        for r in EXCLUSION_ORDER:
            totals["by_reason"][r] += counts[r]
        if verbose:
            print(f"[변환] {os.path.basename(f)}: {n_in:,}행 → {n_keep:,}행 유지 | 제외 " +
                  ", ".join(f"{r} {counts[r]:,}" for r in EXCLUSION_ORDER if counts[r]))

    calls = pd.concat(kept_parts, ignore_index=True) if kept_parts else pd.DataFrame()
    if calls.empty:
        raise ValueError("제외 후 남은 행이 없습니다. bbox·제외 규칙을 확인하세요.")
    # 파일 경계를 넘는 중복(같은 운행이 두 달 파일에 모두 실린 경우)까지 한 번 더 제거
    before = len(calls)
    calls = calls.drop_duplicates(subset=[c for c in ("pickup_datetime", "dropoff_datetime", "latitude", "longitude", "fare_amount") if c in calls.columns])
    cross_dup = before - len(calls)
    totals["by_reason"]["duplicate"] += cross_dup; totals["kept_rows"] = len(calls)

    calls = calls.sort_values("pickup_datetime").reset_index(drop=True)
    calls["request_id"] = [f"nyc_{t:%Y%m%d}_{i}" for i, t in enumerate(calls["pickup_datetime"])]
    cols = ["request_id", "pickup_datetime", "latitude", "longitude", "dropoff_datetime", "vendor_id",
            "payment_type", "passenger_count", "trip_distance", "coord_source"]
    calls = calls[[c for c in cols if c in calls.columns]]

    start, end = calls["pickup_datetime"].min(), calls["pickup_datetime"].max()
    os.makedirs(out_dir, exist_ok=True)
    stem = f"calls_{start:%Y-%m-%d}_{end:%Y-%m-%d}"
    out_csv = os.path.join(out_dir, stem + ".csv")
    calls.to_csv(out_csv, index=False)
    totals.update({
        "source": "NYC TLC Trip Record Data (nyc.gov) — 실제 운행 기록, 카카오 데이터 아님",
        "files": [os.path.basename(f) for f in files], "bbox": bbox, "exclude_payment": sorted(exclude_payment),
        "period": [str(start), str(end)], "days": int((end.normalize() - start.normalize()).days) + 1,
        "coord_source": sorted(calls["coord_source"].unique().tolist()) if "coord_source" in calls else ["gps"],
        "daily_counts": {str(k.date()): int(v) for k, v in calls.groupby(calls["pickup_datetime"].dt.normalize()).size().items()},
        "output": out_csv,
    })
    with open(os.path.join(out_dir, stem + "_exclusions.json"), "w", encoding="utf-8") as fh:
        json.dump(totals, fh, ensure_ascii=False, indent=2)
    if verbose:
        ex = totals["by_reason"]; n_ex = sum(ex.values())
        print(f"[결과] 입력 {totals['input_rows']:,}행 → 유지 {totals['kept_rows']:,}행 ({totals['kept_rows'] / max(1, totals['input_rows']) * 100:.1f}%), "
              f"제외 {n_ex:,}행: " + ", ".join(f"{r} {ex[r]:,}" for r in EXCLUSION_ORDER))
        print(f"[기간] {start} ~ {end} ({totals['days']}일), 하루 평균 {totals['kept_rows'] / max(1, totals['days']):,.0f}건")
        print(f"[저장] {out_csv} (+ _exclusions.json)")
        print("[다음] python -m module2_preprocessing.pipeline --logs " + out_csv + " --out data/processed/features_nyc.csv")
    return totals


def bbox_from_config() -> dict:
    return {k: float(CFG[k]) for k in ("lat_min", "lat_max", "lng_min", "lng_max")}


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("inputs", nargs="+", help="TLC parquet/csv (와일드카드 가능)")
    ap.add_argument("--bbox", nargs=4, type=float, metavar=("LAT_MIN", "LAT_MAX", "LNG_MIN", "LNG_MAX"),
                    help="대상 영역. 생략 시 config.json region 프리셋")
    ap.add_argument("--exclude-payment", default=",".join(map(str, sorted(EXCLUDE_PAYMENT_DEFAULT))),
                    help="제외할 payment_type 코드(쉼표 구분). 기본 6(Voided trip)")
    ap.add_argument("--zone-centroids", default=None, help="PULocationID 자료용 택시 존 중심점 csv (LocationID, latitude, longitude)")
    ap.add_argument("--out-dir", default=OUT_DIR)
    a = ap.parse_args()
    bbox = dict(zip(("lat_min", "lat_max", "lng_min", "lng_max"), a.bbox)) if a.bbox else bbox_from_config()
    if bbox["lat_min"] > 0 and bbox["lng_min"] > 0:
        print(f"[경고] bbox가 동반구({bbox})입니다. config.json region을 'NYC(맨해튼)'으로 바꾸거나 --bbox 를 지정하세요.")
    excl = {int(x) for x in a.exclude_payment.split(",") if x.strip()}
    convert(a.inputs, bbox, excl, a.zone_centroids, a.out_dir)
