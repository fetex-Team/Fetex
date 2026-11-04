"""
실측 날씨 조회(weather_history.py) 테스트.

실행: python tests/test_weather_history.py   (pytest로도 실행 가능)
- 네트워크 불필요. 임시 폴더에 날씨 CSV를 만들어 검사한다.
"""
import csv
import datetime as dt
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import module1_simulation.weather_history as wh  # noqa: E402


def _write(path, day, hours, precip=0.0):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["time", "temperature", "precipitation", "wind_speed"])
        for h in hours:
            w.writerow([f"{day}T{h:02d}:00", 20 + h * 0.1, precip if h == 18 else 0.0, 3.0])


def test_reads_24_hours_and_skips_synthetic():
    with tempfile.TemporaryDirectory() as tmp:
        _write(os.path.join(tmp, "weather_강남역_2026-09-01_2026-09-02.csv"), "2026-09-01", range(24), precip=4.5)
        _write(os.path.join(tmp, "weather_from_external.csv"), "2026-09-01", range(24), precip=99.0)
        wh._CACHE.clear()
        r = wh.load_hourly_weather("2026-09-01", region="강남역", directory=tmp)
        assert r["source"].startswith("weather_강남역") and r["hours"] == 24
        assert r["precipitation"][18] == 4.5 and sum(r["precipitation"]) == 4.5
        assert wh.load_hourly_weather(dt.date(2026, 9, 1), directory=tmp)["precipitation"][18] == 4.5
        assert wh.load_hourly_weather("2026-09-05", directory=tmp) is None      # 파일에 없는 날짜


def test_incomplete_day_returns_none():
    with tempfile.TemporaryDirectory() as tmp:
        _write(os.path.join(tmp, "weather_강남역_x.csv"), "2026-09-01", range(10))
        wh._CACHE.clear()
        assert wh.load_hourly_weather("2026-09-01", directory=tmp) is None      # 20시간 미만
        assert wh.load_hourly_weather("2026-09-01", directory=tmp, min_hours=10)["hours"] == 10


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"[PASS] {t.__name__}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"[FAIL] {t.__name__}: {e!r}")
    print(f"\n{len(tests) - failed}/{len(tests)} 통과")
    sys.exit(1 if failed else 0)
