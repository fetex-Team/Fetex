"""
디지털 트윈 지도 데이터 (대시보드 개요의 지도 패널).

- 도로: SUMO 도로망(module1_simulation/sumo_config/grid.net.xml)의 차로 중심선 → 화면 좌표(m, 북쪽 위)
- 칸: H3 res 8 육각형 경계 + 평가 날짜 하루(9~23시) 호출 수 — 시뮬레이션과 같은 수요 생성기·시드로 만든 값
- 실제 신호 계획이 들어간 교차로(경찰청): 강남역, 교보타워사거리
결과는 results/dashboard_cache/map_<날짜>.json에 저장해 두고 도로망·설정이 바뀌면 다시 만든다.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NET = os.path.join(ROOT, "module1_simulation", "sumo_config", "grid.net.xml")
META = os.path.join(ROOT, "module1_simulation", "sumo_config", "runtime_meta.json")
SIGNALS = os.path.join(ROOT, "data", "external", "signal_plans_gangnam.json")
CACHE = os.path.join(ROOT, "results", "dashboard_cache")
ARTERIAL = {"primary", "primary_link", "secondary", "secondary_link", "tertiary", "tertiary_link", "trunk", "trunk_link"}


def _key(date: str) -> str:
    h = hashlib.sha1(b"v2")
    for p in (NET, META, os.path.join(ROOT, "config.json")):
        if os.path.exists(p):
            h.update(f"{p}:{os.path.getmtime(p)}".encode())
    h.update(date.encode())
    return h.hexdigest()[:12]


def build(date: str = "2026-10-14") -> dict:
    os.makedirs(CACHE, exist_ok=True)
    key = _key(date)
    path = os.path.join(CACHE, f"map_{date}.json")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            cached = json.load(f)
        if cached.get("key") == key:
            return cached
    import sumolib
    import h3
    net = sumolib.net.readNet(NET)
    (x0, y0), (x1, y1) = net.getBBoxXY()
    roads = []
    for e in net.getEdges():
        if e.getFunction() == "internal":
            continue
        kind = (e.getType() or "").split(".")[-1]
        shape = [(round(x - x0, 1), round(y1 - y, 1)) for x, y in e.getShape()]
        roads.append({"id": e.getID(), "a": int(kind in ARTERIAL), "n": e.getLaneNumber(), "p": shape})

    def to_xy(lat, lon):
        x, y = net.convertLonLat2XY(lon, lat)
        return round(x - x0, 1), round(y1 - y, 1)

    with open(META, encoding="utf-8") as f:
        meta = json.load(f)
    calls, total, hourly = {}, 0, {}
    h0 = meta.get("sim_start_hour", 9)
    try:
        import sys
        sys.path.insert(0, ROOT)
        from dashboard.params import load_settings
        from demand_model import RealDemandModel
        model = RealDemandModel(meta, load_settings())
        sched = model.build_schedule(dt.date.fromisoformat(date), meta.get("sim_start_hour", 9),
                                     meta.get("sim_end_hour", 23), meta.get("passenger_seed", 42))
        for c in sched:
            lat, lon = meta["edge_latlng"][c.origin]
            hx = h3.latlng_to_cell(lat, lon, 8)
            calls[hx] = calls.get(hx, 0) + 1
            hh = int(h0 + c.t // 3600)
            hourly[hh] = hourly.get(hh, 0) + 1
            total += 1
    except Exception as exc:          # 수요를 못 만들면 지도만 보여 준다
        calls, hourly = {}, {}
        print(f"[대시보드 지도] 수요 계산 건너뜀: {exc}")
    hexes = [{"id": hx, "calls": n, "b": [to_xy(la, lo) for la, lo in h3.cell_to_boundary(hx)]}
             for hx, n in sorted(calls.items(), key=lambda kv: -kv[1])]
    signals = []
    try:                               # 경찰청 교차로 번호 ↔ 위치 표 (tools/build_signal_plans.INTERSECTIONS)
        import importlib.util
        spec = importlib.util.spec_from_file_location("bsp", os.path.join(ROOT, "tools", "build_signal_plans.py"))
        bsp = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(bsp)
        for it in bsp.INTERSECTIONS:
            x, y = to_xy(it["lat"], it["lon"])
            signals.append({"name": it["name"], "x": x, "y": y})
    except Exception as exc:
        print(f"[대시보드 지도] 신호 위치 건너뜀: {exc}")
    out = {"key": key, "date": date, "w": round(x1 - x0, 1), "h": round(y1 - y0, 1), "roads": roads,
           "hexes": hexes, "total_calls": total,
           "hourly": [{"h": h, "n": hourly[h]} for h in sorted(hourly)], "signals": signals,
           "n_edges": len(roads), "n_junctions": len([j for j in net.getNodes() if j.getType() != "internal"]),
           "n_tls": len(net.getTrafficLights())}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
    return out
