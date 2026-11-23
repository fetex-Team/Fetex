"""
경찰청 교차로 신호 운영계획을 시뮬레이션 신호등(SUMO TLS)에 연결할 수 있는 작은 파일로 정리한다.

    python data/loaders/build_signal_plans.py
    입력: data/external/signal_plans/getPlanCROPInfo.json, getPlanCRWDInfo.json, getPlanCRHDInfo.json
          (data/loaders/fetch_signal_plans.py로 받은 서울 전체. 없으면 같은 폴더의 gangnam_subset.json)
          module1_simulation/sumo_config/grid.net.xml
    출력: data/external/signal_plans_gangnam.json (저장소에 포함, 시뮬레이션은 이 파일만 읽음)

경찰청 자료에는 교차로 좌표가 없어서, 아래 INTERSECTIONS 표로 "경찰청 교차로 번호 ↔ 지도 위 위치"를 직접 잇는다.
위치 근거는 OpenStreetMap에 이름이 적힌 지점이다. 지도에서 그 위치 30m 안에 있는 신호등을 연결한다.
- 강남역(2978): 강남대로×테헤란로·서초대로 교차로. OSM 신호 지점(node 11034387027, 37.49799/127.02770)
- 교보타워사거리(1424): 강남대로×봉은사로·사평대로. OSM 신호 지점 "교보타워사거리"(node 2281095539)
시뮬레이션 구역(1km²) 안의 나머지 신호 교차로는 경찰청 자료에서 같은 이름을 찾지 못했다
(진흥아파트사거리 없음, 삼호아파트앞(2528)은 요일계획이 모두 0번이라 운영계획 없음). 그 신호는 지도 기본값을 쓴다.

요일 번호(PLAN_DY): 1=일 2=월 … 7=토 (강남역: 일=4번 계획, 월=1번, 화~금=2번, 토=3번 — 일요일 계획이 가장 짧은 주기라 일치)
"""
import json
import math
import os
import sys

import sumolib

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SRC = os.path.join(ROOT, "data", "external", "signal_plans")
NET = os.path.join(ROOT, "module1_simulation", "sumo_config", "grid.net.xml")
OUT = os.path.join(ROOT, "data", "external", "signal_plans_gangnam.json")
INTERSECTIONS = [
    {"int_no": "2978", "name": "강남역", "lon": 127.02770, "lat": 37.49799, "osm_ref": "node/11034387027 강남대로×테헤란로 신호 지점"},
    {"int_no": "1424", "name": "교보타워사거리", "lon": 127.02443, "lat": 37.50445, "osm_ref": "node/2281095539 교보타워사거리"},
]
RADIUS_M = 60.0


def load_api():
    full = {op: os.path.join(SRC, f"{op}.json") for op in ("getPlanCROPInfo", "getPlanCRWDInfo", "getPlanCRHDInfo")}
    if all(os.path.exists(p) for p in full.values()):
        return {op: json.load(open(p, encoding="utf-8")) for op, p in full.items()}
    sub = os.path.join(SRC, "gangnam_subset.json")
    if os.path.exists(sub):
        return json.load(open(sub, encoding="utf-8"))
    sys.exit("[중단] 경찰청 신호 자료가 없습니다 (python data/loaders/fetch_signal_plans.py 먼저).")


def nearest_tls(net, lon, lat):
    x, y = net.convertLonLat2XY(lon, lat)
    best = None
    for t in net.getTrafficLights():
        pts = [c[0].getEdge().getToNode().getCoord() for c in t.getConnections()]
        d = min(math.hypot(px - x, py - y) for px, py in pts)
        if best is None or d < best[0]:
            best = (d, t.getID())
    return best


def main():
    if any(a in ("-h", "--help") for a in sys.argv[1:]):    # 사용법만 출력
        print(__doc__)
        return
    api = load_api()
    net = sumolib.net.readNet(NET)
    out = {"source": "경찰청 교차로계획정보서비스 (공공데이터포털 15056569)",
           "day_numbering": "1=일 2=월 3=화 4=수 5=목 6=금 7=토", "intersections": {}}
    for it in INTERSECTIONS:
        no = it["int_no"]
        d, tls = nearest_tls(net, it["lon"], it["lat"])
        if d > RADIUS_M:
            print(f"[건너뜀] {it['name']}: {RADIUS_M:.0f}m 안에 신호등 없음 (가장 가까운 {tls} {d:.0f}m)")
            continue
        plans = {}
        for r in api["getPlanCROPInfo"]:
            if str(r["INT_NO"]) != no or int(r["INT_OPER_CYCLE_VAL"] or 0) <= 0:
                continue
            ph = [int(r[f"A_RING_{i}_PHASE_VAL"] or 0) for i in range(1, 9)]
            ph = [p for p in ph if p > 0]
            plans.setdefault(str(int(r["INT_PLAN_NO"])), []).append({
                "start": f"{int(r['OPER_PLAN_HH']):02d}:{int(r['OPER_PLAN_MI']):02d}",
                "cycle": int(r["INT_OPER_CYCLE_VAL"]), "offset": int(r["INT_OPER_OFFSET_VAL"] or 0), "phases": ph})
        for v in plans.values():
            v.sort(key=lambda p: p["start"])
        week = {str(int(r["PLAN_DY"])): str(int(r["INT_PLAN_NO"])) for r in api["getPlanCRWDInfo"] if str(r["INT_NO"]) == no}
        holi = [{"month": int(r["HOLY_PLAN_MM"]), "day": int(r["HOLY_PLAN_DD"]), "plan": str(int(r["INT_PLAN_NO"]))}
                for r in api["getPlanCRHDInfo"]
                if str(r["INT_NO"]) == no and int(r["HOLY_PLAN_MM"] or 0) > 0 and int(r["INT_PLAN_NO"] or 0) > 0]
        bad = [p for v in plans.values() for p in v if sum(p["phases"]) != p["cycle"]]
        out["intersections"][tls] = {"int_no": no, "name": it["name"], "osm_ref": it["osm_ref"],
                                     "match_distance_m": round(d, 1), "weekday_plan": week,
                                     "holiday_plan": holi, "plans": plans}
        print(f"[연결] {it['name']}({no}) -> 신호등 {tls[:40]} ({d:.0f}m): 계획 {len(plans)}개, "
              f"요일 {week}, 특수일 {len(holi)}건, 현시합≠주기 {len(bad)}건")
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"[저장] {OUT}")


if __name__ == "__main__":
    main()
