"""data/sim_logs/demand_log_*.csv 데이터 품질 자동 검사기.

원본 CSV는 절대 수정/삭제하지 않는다.
검사 결과는 data/quality/ 아래에 CSV + TXT로 저장한다.

검사 항목:
- 필수 컬럼/빈 파일/깨진 CSV
- timestamp 파싱 실패
- 파일명 날짜와 실제 timestamp 날짜 불일치
- 중복 timestamp / 완전 중복 행
- 시간 간격(gap) 이상
- 좌표 결측/범위 이상
- 행 수/실제 시뮬레이션 구간
- 날짜별 로그 중복(같은 날짜에 여러 로그)
- 이벤트가 특정 시간대에만 몰리는지 참고 지표
"""

from __future__ import annotations

import argparse
import csv
import glob
import json
import os
import re
from datetime import datetime

import pandas as pd

ROOT = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = os.path.join(ROOT, "data", "sim_logs")
OUT_DIR = os.path.join(ROOT, "data", "quality")

DATE_RE = re.compile(r"(\d{6})\.csv$")
REQUIRED = {"pickup_datetime", "latitude", "longitude"}


def _filename_date(path):
    m = DATE_RE.search(os.path.basename(path))
    if not m:
        return None
    try:
        return datetime.strptime("20" + m.group(1), "%Y%m%d").date()
    except ValueError:
        return None


def _status(errors, warnings):
    if errors:
        return "제외 권장"
    if warnings:
        return "의심"
    return "정상"


def inspect_file(path):
    name = os.path.basename(path)
    errors, warnings = [], []
    result = {
        "filename": name,
        "filename_date": str(_filename_date(path) or ""),
        "rows": 0,
        "valid_timestamp_rows": 0,
        "unique_timestamps": 0,
        "start": "",
        "end": "",
        "duration_min": 0.0,
        "median_gap_sec": 0.0,
        "max_gap_sec": 0.0,
        "large_gap_count": 0,
        "duplicate_timestamp_rows": 0,
        "duplicate_full_rows": 0,
        "invalid_timestamp_rows": 0,
        "invalid_coordinate_rows": 0,
        "zero_coordinate_rows": 0,
        "status": "",
        "reason": "",
    }

    try:
        df = pd.read_csv(path)
    except Exception as e:
        result["status"] = "제외 권장"
        result["reason"] = f"CSV 읽기 실패: {e}"
        return result

    result["rows"] = int(len(df))
    missing = REQUIRED - set(df.columns)
    if missing:
        result["status"] = "제외 권장"
        result["reason"] = "필수 컬럼 없음: " + ", ".join(sorted(missing))
        return result
    if df.empty:
        result["status"] = "제외 권장"
        result["reason"] = "빈 CSV"
        return result

    ts = pd.to_datetime(df["pickup_datetime"], errors="coerce")
    invalid_ts = int(ts.isna().sum())
    result["invalid_timestamp_rows"] = invalid_ts
    result["valid_timestamp_rows"] = int(ts.notna().sum())
    if invalid_ts:
        errors.append(f"timestamp 파싱 실패 {invalid_ts}행")

    valid = ts.dropna().sort_values()
    if not valid.empty:
        result["start"] = valid.iloc[0].isoformat()
        result["end"] = valid.iloc[-1].isoformat()
        result["duration_min"] = round((valid.iloc[-1] - valid.iloc[0]).total_seconds() / 60.0, 2)
        unique = valid.drop_duplicates()
        result["unique_timestamps"] = int(unique.nunique())
        dup_ts = int(valid.duplicated(keep=False).sum())
        result["duplicate_timestamp_rows"] = dup_ts
        # 같은 pickup_datetime에 여러 승객이 존재하는 것은 정상적인 수요 데이터다.
        # 따라서 중복 timestamp 자체는 경고/제외 사유로 취급하지 않는다.

        diffs = unique.sort_values().diff().dropna().dt.total_seconds()
        if not diffs.empty:
            median_gap = float(diffs.median())
            max_gap = float(diffs.max())
            result["median_gap_sec"] = round(median_gap, 2)
            result["max_gap_sec"] = round(max_gap, 2)
            # 시뮬레이션 로그는 이벤트가 없어도 timestamp가 건너뛸 수 있으므로
            # 매우 긴 공백만 경고한다. 단, 2배 기준만으로 너무 민감해지지 않도록 10분 이상을 요구.
            if median_gap > 0:
                threshold = max(600.0, median_gap * 6.0)
                large = diffs[diffs > threshold]
                result["large_gap_count"] = int(len(large))
                if len(large):
                    warnings.append(
                        f"긴 시간 공백 {len(large)}회 (최대 {max_gap/60:.1f}분, 기준 {threshold/60:.1f}분)"
                    )

        fn_date = _filename_date(path)
        actual_dates = set(valid.dt.date)
        if fn_date and (actual_dates != {fn_date}):
            errors.append(
                "파일명 날짜와 실제 timestamp 날짜 불일치 "
                f"(파일명={fn_date}, 실제={','.join(map(str, sorted(actual_dates)))})"
            )

        if result["duration_min"] < 30:
            warnings.append(f"실제 기록 구간이 {result['duration_min']:.1f}분으로 매우 짧음")

    lat = pd.to_numeric(df["latitude"], errors="coerce")
    lon = pd.to_numeric(df["longitude"], errors="coerce")
    bad_coord = lat.isna() | lon.isna() | (lat < -90) | (lat > 90) | (lon < -180) | (lon > 180)
    result["invalid_coordinate_rows"] = int(bad_coord.sum())
    if bad_coord.any():
        errors.append(f"좌표 결측/범위 오류 {int(bad_coord.sum())}행")

    zero_coord = (lat == 0) & (lon == 0)
    result["zero_coordinate_rows"] = int(zero_coord.sum())
    if zero_coord.any():
        warnings.append(f"(0,0) 좌표 {int(zero_coord.sum())}행")

    full_dups = int(df.duplicated().sum())
    result["duplicate_full_rows"] = full_dups
    if full_dups:
        warnings.append(f"완전 중복 행 {full_dups}행")

    result["status"] = _status(errors, warnings)
    result["reason"] = "; ".join(errors + warnings)
    return result


def scan(paths):
    results = [inspect_file(p) for p in sorted(paths)]
    by_date = {}
    for r in results:
        d = r["filename_date"]
        if d:
            by_date.setdefault(d, []).append(r["filename"])
    duplicate_dates = {d: names for d, names in by_date.items() if len(names) > 1}
    return results, duplicate_dates


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--logs", nargs="*", help="검사할 CSV. 생략하면 data/sim_logs 전체")
    ap.add_argument("--out-dir", default=OUT_DIR)
    args = ap.parse_args()

    paths = args.logs or sorted(glob.glob(os.path.join(LOG_DIR, "demand_log_*.csv")))
    paths = [os.path.abspath(p) for p in paths if os.path.isfile(p)]
    if not paths:
        print("[에러] 검사할 demand_log_*.csv가 없습니다.")
        return 2

    results, duplicate_dates = scan(paths)
    os.makedirs(args.out_dir, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    csv_path = os.path.join(args.out_dir, f"demand_data_quality_{stamp}.csv")
    txt_path = os.path.join(args.out_dir, f"demand_data_quality_{stamp}.txt")
    json_path = os.path.join(args.out_dir, f"demand_data_quality_{stamp}.json")

    pd.DataFrame(results).to_csv(csv_path, index=False, encoding="utf-8-sig")
    summary = {
        "checked_files": len(results),
        "normal": sum(r["status"] == "정상" for r in results),
        "suspicious": sum(r["status"] == "의심" for r in results),
        "exclude_recommended": sum(r["status"] == "제외 권장" for r in results),
        "duplicate_dates": duplicate_dates,
        "results": results,
    }
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    with open(txt_path, "w", encoding="utf-8") as f:
        f.write("=== Demand Log 데이터 품질 검사 ===\n")
        f.write(f"검사 시각: {datetime.now():%Y-%m-%d %H:%M:%S}\n")
        f.write(f"검사 파일: {len(results)}개\n")
        f.write(f"정상: {summary['normal']}개 | 의심: {summary['suspicious']}개 | 제외 권장: {summary['exclude_recommended']}개\n\n")
        if duplicate_dates:
            f.write("[날짜 중복] 같은 날짜에 여러 로그가 존재합니다. 비교/학습에서는 같은 날짜를 여러 전략이 공유하지 않도록 주의하세요.\n")
            for d, names in sorted(duplicate_dates.items()):
                f.write(f"  {d}: {len(names)}개\n")
        f.write("\n[파일별 결과]\n")
        for r in results:
            f.write(
                f"[{r['status']}] {r['filename']} | rows={r['rows']:,} | "
                f"{r['start']} ~ {r['end']} | {r['duration_min']:.1f}min | {r['reason']}\n"
            )

    print(f"[데이터 검사 완료] {len(results)}개 로그")
    print(f"정상={summary['normal']} | 의심={summary['suspicious']} | 제외 권장={summary['exclude_recommended']}")
    print(f"결과 CSV: {csv_path}")
    print(f"상세 TXT: {txt_path}")
    print(f"상세 JSON: {json_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
