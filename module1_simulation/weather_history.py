"""
과거 시간별 날씨(실측) 조회.

scripts/fetch_weather_history.py가 Open-Meteo Archive에서 받아 둔
data/external/weather_<지역>_<시작>_<종료>.csv (time, temperature, precipitation, wind_speed)를 읽어
특정 날짜의 0~23시 값을 돌려준다. 파일이 없거나 그날 자료가 부족하면 None
(그때는 기존 '날씨적용' 방식 — 실행 시점 실시간 날씨 — 으로 대체된다).
"""
from __future__ import annotations

import datetime as _dt
import os

from module2_preprocessing.external_data_merge import EXTERNAL_DIR, load_weather, weather_files

_CACHE: dict[str, dict] = {}


def _load_file(path: str) -> dict:
    """파일 → {(날짜, 시): (강수, 기온, 풍속)}. 읽기·범위 검사는 학습과 같은 load_weather를 쓴다."""
    if path in _CACHE:
        return _CACHE[path]
    w = load_weather(path)

    def num(v):
        return None if v != v else float(v)  # NaN → None
    by_hour = {(t.date(), t.hour): (num(p), num(tc), num(ws))
               for t, p, tc, ws in zip(w["time"], w["precipitation"], w["temperature"], w["wind_speed"])}
    _CACHE[path] = by_hour
    return by_hour


def load_hourly_weather(date, region: str | None = None, directory: str = EXTERNAL_DIR,
                        min_hours: int = 20) -> dict | None:
    """date(YYYY-MM-DD 또는 date)의 시간별 실측 날씨. {'precipitation': [24], 'temperature': [24],
    'wind_speed': [24], 'source': 파일명} 또는 None. 빠진 시간은 강수 0으로 둔다.
    파일은 학습·예측과 같은 우선순위(종료일 늦은 순)로 보고, 그날을 min_hours 이상 덮는 첫 파일을 쓴다."""
    d = _dt.date.fromisoformat(str(date)) if not isinstance(date, _dt.date) else date
    for path in weather_files(region, directory):
        data = _load_file(path)
        rows = [data.get((d, h)) for h in range(24)]
        have = sum(r is not None and r[0] is not None for r in rows)
        if have >= min_hours:
            return {"precipitation": [(r[0] if r and r[0] is not None else 0.0) for r in rows],
                    "temperature": [(r[1] if r else None) for r in rows],
                    "wind_speed": [(r[2] if r else None) for r in rows],
                    "source": os.path.basename(path), "hours": have}
    return None
