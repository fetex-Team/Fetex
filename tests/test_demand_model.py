"""
실데이터 수요 모델(demand_model.py) 테스트.

실행: python tests/test_demand_model.py   (pytest로도 실행 가능)
- 네트워크·SUMO 불필요. 임시 TIMS/시간대 파일과 가짜 지도(meta)로 검사한다.
"""
import csv
import datetime as dt
import math
import os
import random
import sys
import tempfile
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
for m in ("geo_lookup", "weather_lookup"):          # config_loader가 외부 API를 부르지 않게 차단
    sys.modules.setdefault(m, types.ModuleType(m))

import demand_model as dm  # noqa: E402

GANGNAM = (37.4979, 127.0276)
# pyproj (EPSG:4326 -> EPSG:5179) 기준값
GANGNAM_UTMK = (958243.2359761745, 1944398.131422904)


def _write(path, header, rows):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


def _fixture(tmp):
    """강남역 사거리 칸 2개 + 가짜 지도(도로 4개)."""
    tims = os.path.join(tmp, "tims.csv")
    rows = []
    for month in range(1, 13):
        rows.append(["다사116089", f"2025-{month:02d}", 3000 * 30, 0, 3000 * 30 * 10000, 0])   # 하루 약 3000건, 10km
        rows.append(["다사116088", f"2025-{month:02d}", 1000 * 30, 0, 1000 * 30 * 2000, 0])    # 하루 약 1000건, 2km
    _write(tims, ["grid", "ym", "trips", "revenue", "dist_m", "time_s"], rows)
    hourly = os.path.join(tmp, "hourly.csv")
    _write(hourly, ["hour", "weekday_trips", "weekend_trips"], [[h, 1, 1 if h < 12 else 3] for h in range(24)])
    dow = os.path.join(tmp, "dow.csv")
    _write(dow, ["dow", "trips"], [[k, 100] for k in dm.DOW_KEYS])
    gangnam = os.path.join(tmp, "gangnam.csv")   # 평일 1.1배, 주말 0.8배
    _write(gangnam, ["month", "all_daily", "weekday_daily", "weekend_daily"], [[m, 100, 110, 80] for m in range(1, 13)])
    # 칸 중심 근처에 도로 2개씩
    def cell_center(code):
        x0, y0, x1, y1 = dm.grid_bounds(code)
        return dm.utmk_to_latlng((x0 + x1) / 2, (y0 + y1) / 2)
    c89, c88 = cell_center("다사116089"), cell_center("다사116088")
    edge_latlng = {"a1": c89, "a2": (c89[0] + 0.0005, c89[1]), "b1": c88, "b2": (c88[0] + 0.0005, c88[1])}
    x0, y0, x1, y1 = dm.grid_bounds("다사116088")
    lat_min, lng_min = dm.utmk_to_latlng(x0 + 1, y0 + 1)
    _, _, x1b, y1b = dm.grid_bounds("다사116089")
    lat_max, lng_max = dm.utmk_to_latlng(x1b - 1, y1b - 1)
    meta = {"edges": list(edge_latlng), "edge_latlng": {k: list(v) for k, v in edge_latlng.items()},
            "boundary_edges": ["a2", "b2"], "zones": {"subway_entrance": ["a1"]}, "department_store_edges": [],
            "config": {"lat_min": lat_min, "lat_max": lat_max, "lng_min": lng_min, "lng_max": lng_max}}
    return meta, dict(tims_path=tims, hourly_path=hourly, dow_path=dow, gangnam_path=gangnam)


def test_projection_matches_pyproj():
    x, y = dm.latlng_to_utmk(*GANGNAM)
    assert abs(x - GANGNAM_UTMK[0]) < 0.01 and abs(y - GANGNAM_UTMK[1]) < 0.01
    lat, lng = dm.utmk_to_latlng(x, y)
    assert abs(lat - GANGNAM[0]) < 1e-7 and abs(lng - GANGNAM[1]) < 1e-7


def test_grid_code():
    assert dm.grid_code_of(*GANGNAM_UTMK) == "다사116088"
    assert dm.grid_bounds("다사116089") == (958000.0, 1944500.0, 958500.0, 1945000.0)
    assert dm.grid_code_of(0, 0) is None


def test_volume_shares_and_determinism():
    with tempfile.TemporaryDirectory() as tmp:
        meta, paths = _fixture(tmp)
        model = dm.RealDemandModel(meta, {"real_demand_scale": 1.0}, **paths)
        date = dt.date(2025, 9, 1)          # 월요일, 평일 시간대는 균등(1/24)
        expected = model.area_daily_calls(date)
        assert 4290 < expected < 4401       # 두 칸 하루 약 4000건 x 평일 배율 1.1
        random.seed(1)
        s1 = model.build_schedule(date, 9, 23, seed=7)
        random.random(); random.shuffle(list(range(100)))   # 전역 난수를 건드려도
        s2 = model.build_schedule(date, 9, 23, seed=7)
        assert s1 == s2, "같은 날짜·시드면 손님 목록이 완전히 같아야 함"
        assert s1 != model.build_schedule(date, 9, 23, seed=8)
        n = len(s1)
        exp_window = expected * 14 / 24
        assert abs(n - exp_window) < 4 * math.sqrt(exp_window), (n, exp_window)
        share89 = sum(c.cell == "다사116089" for c in s1) / n
        assert abs(share89 - 0.75) < 0.03, share89
        assert all(0 <= c.t < 14 * 3600 for c in s1) and all(a.t <= b.t for a, b in zip(s1, s1[1:]))
        # 평균거리 2km 칸은 영역 안 하차 비율이 더 높아야 함 (1 - e^(-1/2) ≈ 39%)
        assert abs(model.internal_share("다사116088") - (1 - math.exp(-0.5))) < 1e-9
        internal88 = [c.kind == "internal" for c in s1 if c.cell == "다사116088"]
        assert abs(sum(internal88) / len(internal88) - 0.393) < 0.06
        assert all(c.dest in meta["boundary_edges"] for c in s1 if c.kind == "external")
        assert all(c.origin != c.dest for c in s1)


def test_weekend_profile_and_scale():
    with tempfile.TemporaryDirectory() as tmp:
        meta, paths = _fixture(tmp)
        model = dm.RealDemandModel(meta, {"real_demand_scale": 0.5}, **paths)
        sat = dt.date(2025, 9, 6)
        rates = model.hourly_calls(sat)
        assert abs(rates[15] / rates[3] - 3.0) < 1e-9        # 주말 프로파일 사용
        assert abs(sum(rates) - model.area_daily_calls(sat) * 0.5) < 1e-6
        assert abs(model.area_daily_calls(sat) / model.area_daily_calls(dt.date(2025, 9, 3)) - 0.8 / 1.1) < 1e-9
        # 평일 공휴일은 일요일 배율 + 주말 프로파일
        assert model.hourly_calls(dt.date(2025, 9, 3), is_holiday=True)[15] > model.hourly_calls(dt.date(2025, 9, 3))[15]


def test_hourly_weather_factor():
    with tempfile.TemporaryDirectory() as tmp:
        meta, paths = _fixture(tmp)
        model = dm.RealDemandModel(meta, {"weather_precip_coef_per_mm": 0.02, "weather_precip_cap_mm": 10}, **paths)
        d = dt.date(2025, 9, 1)
        precip = [0.0] * 24
        precip[10], precip[11] = 5.0, 50.0                    # 50mm는 상한 10mm로 자름
        dry, wet = model.hourly_calls(d), model.hourly_calls(d, hourly_precip_mm=precip)
        assert abs(wet[10] / dry[10] - 1.10) < 1e-9 and abs(wet[11] / dry[11] - 1.20) < 1e-9
        assert wet[9] == dry[9]
        s_wet = model.build_schedule(d, 0, 24, seed=1, hourly_precip_mm=precip)
        assert s_wet == model.build_schedule(d, 0, 24, seed=1, hourly_precip_mm=precip)


def test_poi_weight_concentrates_within_cell():
    with tempfile.TemporaryDirectory() as tmp:
        meta, paths = _fixture(tmp)
        model = dm.RealDemandModel(meta, {}, **paths)
        calls = [c for c in model.build_schedule(dt.date(2025, 9, 1), 0, 24, seed=3) if c.cell == "다사116089"]
        a1 = sum(c.origin == "a1" for c in calls) / len(calls)
        assert abs(a1 - 0.75) < 0.04, a1                      # 지하철 출구 가중치 3 : 1


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
