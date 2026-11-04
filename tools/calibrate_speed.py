"""
시간대별 도로 속도 배율(speed_profile)을 TOPIS 실측에 맞춰 정한다.

    1) 측정 실행: 보정 전용 날짜(평가 7일과 겹치지 않음)를 1시간 단위 도로 속도 기록과 함께 돌린다
       python tools/repeat_experiments.py --name speed_calib --dates 2026-09-08 2026-09-17 2026-09-12 2026-09-13 \
           --strategies patrol --jobs 2 --set edge_speed_output=results/repeat/speed_calib/edgespeed
    2) python tools/calibrate_speed.py results/repeat/speed_calib/edgespeed
       -> data/external/speed_profile_gangnam.json (다음 실행부터 적용), results/topis_speed/calibration.md
    3) 1)을 다시 돌려 2)를 한 번 더 하면 오차가 더 줄어든다(반복 보정). 검증은 평가 7일로 tools/compare_topis_speed.py.

모형 (링크 하나, 시간대 하나)
    시뮬레이션 통과 시간 T = Σ L_e / (f · v_e) + D      v_e: 도로 제한속도, f: 배율, D: 신호·교차로 지연(측정값에서 추정)
    목표 T* = Σ L_e / v_TOPIS(요일 종류, 시)
    측정 실행에서 D = T_측정 − Σ L_e/(f_측정·v_e) 를 구하고, f_새 = Σ(L_e/v_e) / (T* − D)  (0.15~1.0로 자름)
목표 속도는 평가일이 아닌 TOPIS 전체 기간(2025-10, 2026-09)의 요일 종류×시각 평균이다. 공휴일은 일요일 쪽으로 넣는다.
실측이 없는 간선(링크에 안 잡힌 큰길 조각)은 같은 요일 종류·시각의 간선 배율 중앙값,
이면도로(residential/unclassified)는 실측 근거가 없으므로 배율 1.0(5030 제한속도 30km/h 그대로)을 쓴다.
"""
import csv
import datetime as dt
import glob
import json
import os
import re
import statistics
import sys
import xml.etree.ElementTree as ET
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from module1_simulation.speed_profile import day_type  # noqa: E402

EXT = os.path.join(ROOT, "data", "external")
NET = os.path.join(ROOT, "module1_simulation", "sumo_config", "grid.net.xml")
PROFILE = os.path.join(EXT, "speed_profile_gangnam.json")
REPORT = os.path.join(ROOT, "results", "topis_speed", "calibration.md")
F_MIN, F_MAX, MIN_SAMPLED, MIN_RESIDUAL = 0.15, 1.0, 0.6, 0.25
# 이면도로(residential 등)는 TOPIS 실측이 없다. 근거 없이 느리게 만들지 않도록 5030 제한속도(30km/h)를 그대로 둔다.
LOCAL_FACTOR = 1.0


HISTORY = os.path.join(EXT, "speed_profile_history")


def used_profile(folder):
    """측정 실행이 실제로 쓴 배율 표. 실행 결과(runs/*.json)의 speed_profile.file_version으로 찾는다.
    배율 없이 돌린 실행이면 None(배율 1.0)."""
    vers = set()
    for p in glob.glob(os.path.join(os.path.dirname(os.path.abspath(folder)), "runs", "*.json")):
        sp = json.load(open(p, encoding="utf-8")).get("speed_profile")
        vers.add(sp.get("file_version") if sp else None)
    if len(vers) > 1:
        sys.exit(f"[중단] 측정 실행들이 서로 다른 배율 표를 썼습니다: {vers}")
    ver = next(iter(vers), None)
    if ver is None:
        print("[안내] 측정 실행은 속도 배율 없이(1.0) 돌았습니다.")
        return None
    path = os.path.join(HISTORY, ver.replace(":", "") + ".json")
    if not os.path.exists(path):
        sys.exit(f"[중단] 측정에 쓴 배율 표 {ver}를 찾지 못했습니다: {path}")
    print(f"[안내] 측정 실행이 쓴 배율 표: {ver}")
    return json.load(open(path, encoding="utf-8"))


def dtype_of(date_str):
    d = dt.date.fromisoformat(date_str)
    return day_type(d)


def main():
    folder = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "results", "repeat", "speed_calib", "edgespeed")
    import sumolib
    net = sumolib.net.readNet(NET)
    vlim = {e.getID(): e.getSpeed() for e in net.getEdges() if e.getFunction() != "internal"}
    elen = {e.getID(): e.getLength() for e in net.getEdges() if e.getFunction() != "internal"}
    with open(os.path.join(EXT, "topis_links_gangnam.csv"), encoding="utf-8") as f:
        links = {r["link_id"]: r["edges"].split(";") for r in csv.DictReader(f) if float(r["coverage"]) >= 0.5 and r["edges"]}
    links = {k: [e for e in v if e in vlim] for k, v in links.items()}
    links = {k: v for k, v in links.items() if v}

    # 목표: 요일 종류 x 시 x 링크 평균 속도
    tgt = defaultdict(list)
    for p in glob.glob(os.path.join(EXT, "topis_speed_gangnam_*.csv")):
        with open(p, encoding="utf-8") as f:
            for r in csv.DictReader(f):
                if r["link_id"] in links:
                    tgt[(dtype_of(r["date"]), int(r["hour"]), r["link_id"])].append(float(r["speed_kmh"]) / 3.6)
    tgt = {k: sum(v) / len(v) for k, v in tgt.items()}

    old = used_profile(folder)

    def f_old(dtype, hour, e):
        if not old:
            return 1.0
        h = old["factors"].get(dtype, {}).get(str(hour))
        return 1.0 if h is None else h.get("edges", {}).get(e, h["default"])

    # 측정: 요일 종류 x 시 x 링크 -> [통과 시간]
    meas = defaultdict(list)
    files = sorted(glob.glob(os.path.join(folder, "edgespeed_*_h??.xml")))
    for path in files:
        m = re.search(r"edgespeed_(\d{4}-\d{2}-\d{2})_.+_h(\d\d)\.xml$", path)
        try:
            root = ET.parse(path).getroot()
        except ET.ParseError:
            print(f"[건너뜀] 끝나지 않은 파일: {os.path.basename(path)}")
            continue
        dtype, h0 = dtype_of(m.group(1)), int(m.group(2))
        for iv in root.iter("interval"):
            hour = h0 + int(float(iv.get("begin")) // 3600)
            sp = {e.get("id"): float(e.get("speed")) for e in iv.iter("edge") if e.get("speed") and float(e.get("speed")) > 0}
            for lid, es in links.items():
                L = sum(elen[e] for e in es)
                got = [e for e in es if e in sp]
                Ls = sum(elen[e] for e in got)
                if Ls < MIN_SAMPLED * L:
                    continue
                T = sum(elen[e] / sp[e] for e in got) * L / Ls
                Tf = sum(elen[e] / (f_old(dtype, hour, e) * vlim[e]) for e in got) * L / Ls
                meas[(dtype, hour, lid)].append((T, Tf))
    if not meas:
        sys.exit(f"[중단] 측정 파일이 없습니다: {folder}")

    factors, rows = defaultdict(dict), []
    for (dtype, hour, lid), v in sorted(meas.items()):
        if (dtype, hour, lid) not in tgt:
            continue
        es = links[lid]
        T = sum(t for t, _ in v) / len(v)
        Tf = sum(tf for _, tf in v) / len(v)
        D = max(0.0, T - Tf)
        L = sum(elen[e] for e in es)
        Tstar = L / tgt[(dtype, hour, lid)]
        free = sum(elen[e] / vlim[e] for e in es)
        f = free / max(Tstar - D, MIN_RESIDUAL * Tstar)
        f = min(F_MAX, max(F_MIN, f))
        for e in es:
            factors[(dtype, hour)][e] = round(f, 3)
        rows.append((dtype, hour, lid, L / T * 3.6, tgt[(dtype, hour, lid)] * 3.6, D, f))

    # 측정이 없는 시각: 같은 요일 종류의 가장 가까운 측정 시각 배율 x (목표 속도 비)
    prof = {"version": dt.datetime.now().strftime("%Y-%m-%dT%H:%M"),
            "method": "T = ΣL/(f·v) + D, D는 측정 실행에서 추정 (tools/calibrate_speed.py)",
            "measured_runs": [os.path.basename(p) for p in files],
            "target": "TOPIS 도로별 일자별 통행속도 2025-10, 2026-09 요일 종류x시 평균", "factors": {}}
    for dtype in ("weekday", "saturday", "sunday"):
        have = sorted(h for (d, h) in factors if d == dtype)
        if not have:
            continue
        prof["factors"][dtype] = {}
        for hour in range(24):
            if hour in have:
                fe = factors[(dtype, hour)]
            else:
                hn = min(have, key=lambda x: abs(x - hour))
                ratio = {lid: tgt.get((dtype, hour, lid), 0) / tgt[(dtype, hn, lid)]
                         for lid in links if (dtype, hn, lid) in tgt}
                fe = {}
                for lid, es in links.items():
                    for e in es:
                        if e in factors[(dtype, hn)] and ratio.get(lid):
                            fe[e] = round(min(F_MAX, max(F_MIN, factors[(dtype, hn)][e] * ratio[lid])), 3)
            prof["factors"][dtype][str(hour)] = {"default": round(statistics.median(fe.values()), 3) if fe else 1.0,
                                                 "default_local": LOCAL_FACTOR, "edges": fe}
    prof["previous_version"] = old.get("version") if old else None
    os.makedirs(HISTORY, exist_ok=True)
    for path in (PROFILE, os.path.join(HISTORY, prof["version"].replace(":", "") + ".json")):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(prof, f, ensure_ascii=False, indent=1)

    os.makedirs(os.path.dirname(REPORT), exist_ok=True)
    err = [abs(s - t) / t for *_, s, t, _, _ in [(r[0], r[1], r[2], r[3], r[4], r[5], r[6]) for r in rows]]
    lines = ["# 속도 보정 결과", "", f"- 측정 실행 파일 {len(files)}개, 보정한 칸(요일 종류x시x링크) {len(rows)}개",
             f"- 측정 시점 시뮬레이션 vs 목표 평균 절대 오차율: {sum(err) / len(err) * 100:.0f}% (이번 배율 적용 전 상태)",
             "", "| 요일 종류 | 시 | 링크 수 | 측정 km/h | 목표 km/h | 신호·교차로 지연(초, 평균) | 새 배율(중앙값) |", "|---|---|---|---|---|---|---|"]
    g = defaultdict(list)
    for r in rows:
        g[(r[0], r[1])].append(r)
    for (dtype, hour), rs in sorted(g.items()):
        lines.append(f"| {dtype} | {hour} | {len(rs)} | {sum(r[3] for r in rs) / len(rs):.1f} | "
                     f"{sum(r[4] for r in rs) / len(rs):.1f} | {sum(r[5] for r in rs) / len(rs):.0f} | "
                     f"{statistics.median(r[6] for r in rs):.2f} |")
    with open(REPORT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print("\n".join(lines[:4]))
    print(f"[저장] {PROFILE}\n[저장] {REPORT}")


if __name__ == "__main__":
    main()
