# -*- coding: utf-8 -*-
"""
실데이터 보정 수요 모델(demand_model.RealDemandModel)로 학습용 호출 로그를 만든다. SUMO 불필요.

왜 SUMO 없이 만들 수 있나
- 이제 호출(수요)은 배차 결과와 무관한 외생 수요라서, 시뮬레이션을 돌리지 않아도
  시뮬레이션 때와 "똑같은 규칙·똑같은 시드"로 호출 목록을 만들 수 있다.
  (같은 날짜·시드라면 시뮬레이션에서 실제로 생기는 호출과 시각·도로까지 완전히 같다)
- 예전처럼 시뮬레이션을 하루치(약 16분)씩 반복할 필요가 없다. 8주치도 수십 초.

기간 기준 (데이터 명세서 v1.0의 "8주 = 56일, 시간순 60/20/20 분할"과 동일)
- 기본: sim_date 전날까지 56일 (예: sim_date 2026-09-07 → 2026-07-13 ~ 2026-09-06)
- 하루 24시간 전체를 만든다 (밤 시간이 0으로 채워져 lag 피처가 왜곡되는 문제 방지)

출력 (DemandLogRecorder와 같은 컬럼 → module2 파이프라인/train 스크립트가 그대로 읽음)
    data/real_demand_logs/demand_log_<지역>실데이터_<yymmdd>.csv  (날짜별 1개)
    data/real_demand_logs/generation_summary.json                (날짜별 총량·격자 비율 요약)

사용
    python scripts/generate_demand_logs.py                 # 8주
    python scripts/generate_demand_logs.py --days 28 --end 2026-09-06
이후
    python scripts/train_dispatch_model.py "data/real_demand_logs/demand_log_*.csv"
"""
import argparse
import csv
import datetime as dt
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from config_loader import CFG                     # noqa: E402
from demand_model import RealDemandModel          # noqa: E402
from holiday_utils import is_holiday_date         # noqa: E402
from weather_history import load_hourly_weather   # noqa: E402

META_PATH = os.path.join(ROOT, "module1_simulation", "sumo_config", "runtime_meta.json")
OUT_DIR = os.path.join(ROOT, "data", "real_demand_logs")
COLUMNS = ["person_id", "request_sec", "pickup_datetime", "edge_id", "origin_zone", "dest_category",
           "x", "y", "latitude", "longitude", "pickup_sec", "wait_sec", "taxi_id", "status"]


def edge_point(meta, edge):
    """승차 지점 위경도 = 도로 중간점 (runtime_meta.edge_latlng).
    배차·재배치 로직이 도로를 H3 칸에 배정할 때도 중간점을 쓰므로(edge_cells), 학습 데이터의 칸
    배정이 시뮬레이션 지도와 정확히 같아진다 (500m급 H3 res 8에서 도로 내 위치 차이는 무시 가능)."""
    return tuple(meta["edge_latlng"][edge])


def main(days: int, end: dt.date, seed, out_dir: str):
    with open(META_PATH, "r", encoding="utf-8") as f:
        meta = json.load(f)
    model = RealDemandModel(meta, CFG)
    edge_zone = {}
    for cat, edges in (meta.get("zones") or {}).items():
        for e in edges or []:
            edge_zone.setdefault(e, []).append(cat)
    region = re.sub(r'[\\/:*?"<>|\s]+', "", str(CFG.get("region", "")))
    os.makedirs(out_dir, exist_ok=True)
    start = end - dt.timedelta(days=days - 1)
    summary = {"generator": "demand_model.RealDemandModel", "seed": seed, "start": start.isoformat(),
               "end": end.isoformat(), "scale": model.scale, "days": []}
    total = 0
    for i in range(days):
        date = start + dt.timedelta(days=i)
        holiday = bool(is_holiday_date(date))
        hist = load_hourly_weather(date, CFG.get("region")) if CFG.get("use_historical_weather", True) else None
        calls = model.build_schedule(date, 0, 24, seed, is_holiday=holiday,
                                     hourly_precip_mm=hist["precipitation"] if hist else None)
        base = dt.datetime.combine(date, dt.time(0, 0))
        path = os.path.join(out_dir, f"demand_log_{region}실데이터{date.strftime('%y%m%d')}.csv")
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(COLUMNS)
            for k, c in enumerate(calls, 1):
                lat, lng = edge_point(meta, c.origin)
                when = base + dt.timedelta(seconds=c.t)
                w.writerow([f"pax_{k}", round(c.t, 1), when.strftime("%Y-%m-%d %H:%M:%S"), c.origin,
                            "|".join(edge_zone.get(c.origin, [])) or "none",
                            c.kind if c.kind == "external" else c.category, "", "",
                            round(lat, 7), round(lng, 7), "", "", "", "generated"])
        total += len(calls)
        summary["days"].append({"date": date.isoformat(), "weekday": date.strftime("%a"), "holiday": holiday,
                                "calls": len(calls), "weather": hist["source"] if hist else None,
                                "precip_mm": round(sum(hist["precipitation"]), 1) if hist else None,
                                "expected": round(sum(model.hourly_calls(date, holiday, hourly_precip_mm=hist["precipitation"] if hist else None)), 1)})
        print(f"[생성] {date} {'공휴일/주말' if holiday or date.weekday() >= 5 else '평일'} {len(calls):,}건 -> {os.path.basename(path)}")
    summary["total_calls"] = total
    summary["model"] = model.summary(end, 0, 24)
    with open(os.path.join(out_dir, "generation_summary.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    print(f"[완료] {days}일 {total:,}건 ({start} ~ {end}) -> {out_dir}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=56, help="생성 일수 (기본 56일 = 8주, 데이터 명세 기준)")
    ap.add_argument("--end", type=str, default=None, help="마지막 날짜 (기본: sim_date 전날)")
    ap.add_argument("--seed", type=int, default=None, help="기본: config의 passenger_seed")
    ap.add_argument("--out", type=str, default=OUT_DIR)
    a = ap.parse_args()
    end = dt.date.fromisoformat(a.end) if a.end else dt.date.fromisoformat(str(CFG.get("sim_date"))) - dt.timedelta(days=1)
    seed = a.seed if a.seed is not None else (CFG.get("passenger_seed") if CFG.get("passenger_seed") is not None else 42)
    main(a.days, end, seed, a.out)
