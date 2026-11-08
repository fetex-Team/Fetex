"""
과거 시간별 날씨(실측) 조회.

scripts/fetch_weather_history.py가 Open-Meteo Archive에서 받아 둔
data/external/weather_<지역>_<시작>_<종료>.csv (time, temperature, precipitation, wind_speed)를 읽어
특정 날짜의 0~23시 값을 돌려준다. 파일이 없거나 그날 자료가 부족하면 None
(그때는 기존 '날씨적용' 방식 — 실행 시점 실시간 날씨 — 으로 대체된다).
"""
from __future__ import annotations

import csv
import datetime as _dt
import glob
import os

ROOT = os.path.dirname(os.path.abspath(__file__))
EXTERNAL_DIR = os.path.join(ROOT, "data", "external")
_CACHE: dict[str, dict] = {}


def _load_file(path: str) -> dict:
    if path in _CACHE:
        return _CACHE[path]
    by_hour = {}
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
            try:
                t = _dt.datetime.fromisoformat(r["time"].strip())
            except (KeyError, ValueError):
                continue
            def num(k):
                v = (r.get(k) or "").strip()
                return float(v) if v not in ("", "None", "nan") else None
            by_hour[(t.date(), t.hour)] = (num("precipitation"), num("temperature"), num("wind_speed"))
    _CACHE[path] = by_hour
    return by_hour


def weather_files(region: str | None = None, directory: str = EXTERNAL_DIR) -> list[str]:
    """실측 날씨 파일 목록 (합성 자료 weather_from_external.csv는 제외)."""
    pat = f"weather_{region}_*.csv" if region else "weather_*.csv"
    return sorted(p for p in glob.glob(os.path.join(directory, pat))
                  if os.path.basename(p) != "weather_from_external.csv")


def load_hourly_weather(date, region: str | None = None, directory: str = EXTERNAL_DIR,
                        min_hours: int = 20) -> dict | None:
    """date(YYYY-MM-DD 또는 date)의 시간별 실측 날씨. {'precipitation': [24], 'temperature': [24],
    'wind_speed': [24], 'source': 파일명} 또는 None. 빠진 시간은 강수 0으로 둔다."""
    d = _dt.date.fromisoformat(str(date)) if not isinstance(date, _dt.date) else date
    for path in reversed(weather_files(region, directory) or weather_files(None, directory)):
        data = _load_file(path)
        rows = [data.get((d, h)) for h in range(24)]
        have = sum(r is not None and r[0] is not None for r in rows)
        if have >= min_hours:
            return {"precipitation": [(r[0] if r and r[0] is not None else 0.0) for r in rows],
                    "temperature": [(r[1] if r else None) for r in rows],
                    "wind_speed": [(r[2] if r else None) for r in rows],
                    "source": os.path.basename(path), "hours": have}
    return None
