"""
시뮬레이션 도로 속도와 TOPIS 실측 통행속도(서울시 택시 GPS 기반)를 같은 도로·같은 시간대로 비교한다.

    python tools/compare_topis_speed.py [edgeData 폴더=results/repeat/speed_check/edgespeed]
    입력: data/external/topis_links_gangnam.csv          (tools/topis_links.py, coverage>=0.5만 사용)
          data/external/topis_speed_gangnam_YYYYMM.csv   (tools/extract_topis_speed.py)
          <폴더>/edgespeed_<날짜>_<전략>_hHH.xml         (config edge_speed_output, 1시간 단위 SUMO edgeData)
    출력: results/topis_speed/compare.csv, summary.md, by_hour.png

비교 규칙
- 시뮬레이션 링크 속도 = 링크에 연결된 엣지 길이 합 / 엣지별 (길이/평균속도) 합. 그 시간에 차가 지나간 엣지가
  링크 길이의 60% 미만이면 그 칸은 비교하지 않는다 (표본 부족).
- 실측: 같은 날짜가 있으면 그날 값, 없으면(2026년 10월은 11/10 공개) 같은 달 전년도(2025-10) 같은 요일 평균을 대리값으로 쓴다.
  어느 쪽을 썼는지 compare.csv의 topis_source 열에 남긴다.
"""
import csv
import datetime as dt
import glob
import math
import os
import re
import sys
import xml.etree.ElementTree as ET
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXT = os.path.join(ROOT, "data", "external")
OUT = os.path.join(ROOT, "results", "topis_speed")
NET = os.path.join(ROOT, "module1_simulation", "sumo_config", "grid.net.xml")
MIN_COVER, MIN_SAMPLED = 0.5, 0.6
DOW = "월화수목금토일"


def load_links():
    with open(os.path.join(EXT, "topis_links_gangnam.csv"), encoding="utf-8") as f:
        return {int(r["link_id"]): r for r in csv.DictReader(f) if float(r["coverage"]) >= MIN_COVER and r["edges"]}


def load_topis(links):
    exact, by_dow = {}, defaultdict(list)          # (date, link, hour) -> km/h ; (yyyymm, dow, link, hour) -> [km/h]
    for p in glob.glob(os.path.join(EXT, "topis_speed_gangnam_*.csv")):
        with open(p, encoding="utf-8") as f:
            for r in csv.DictReader(f):
                lid = int(r["link_id"])
                if lid in links:
                    k = (r["date"], lid, int(r["hour"])); v = float(r["speed_kmh"])
                    exact[k] = v
                    by_dow[(r["date"][:7], r["dow"], lid, int(r["hour"]))].append(v)
    return exact, {k: sum(v) / len(v) for k, v in by_dow.items()}


def topis_for(date, lid, hour, exact, by_dow):
    if (date, lid, hour) in exact:
        return exact[(date, lid, hour)], "same_day"
    d = dt.date.fromisoformat(date)
    proxy = f"{d.year - 1}-{d.month:02d}"
    v = by_dow.get((proxy, DOW[d.weekday()], lid, hour))
    return (v, f"proxy_{proxy}_{DOW[d.weekday()]}") if v is not None else (None, None)


def edge_lengths():
    import sumolib
    net = sumolib.net.readNet(NET)
    return {e.getID(): e.getLength() for e in net.getEdges()}


def main():
    folder = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "results", "repeat", "speed_check", "edgespeed")
    links = load_links()
    exact, by_dow = load_topis(links)
    elen = edge_lengths()
    rows = []
    for path in sorted(glob.glob(os.path.join(folder, "edgespeed_*_h??.xml"))):
        m = re.search(r"edgespeed_(\d{4}-\d{2}-\d{2})_(.+)_h(\d\d)\.xml$", path)
        if not m:
            continue
        date, strat, h0 = m.group(1), m.group(2), int(m.group(3))
        try:
            root = ET.parse(path).getroot()
        except ET.ParseError:
            print(f"[건너뜀] 끝나지 않은 파일: {os.path.basename(path)}")
            continue
        for iv in root.iter("interval"):
            hour = h0 + int(float(iv.get("begin")) // 3600)
            sp = {e.get("id"): float(e.get("speed")) for e in iv.iter("edge") if e.get("speed") and float(e.get("speed")) > 0}
            for lid, L in links.items():
                es = L["edges"].split(";")
                tot = sum(elen[e] for e in es)
                got = [e for e in es if e in sp]
                cov = sum(elen[e] for e in got) / tot if tot else 0
                if cov < MIN_SAMPLED:
                    continue
                sim = sum(elen[e] for e in got) / sum(elen[e] / sp[e] for e in got) * 3.6
                real, src = topis_for(date, lid, hour, exact, by_dow)
                if real is None:
                    continue
                rows.append({"date": date, "strategy": strat, "hour": hour, "link_id": lid, "road": L["road"],
                             "from_node": L["from_node"], "to_node": L["to_node"], "sim_kmh": round(sim, 1),
                             "topis_kmh": round(real, 1), "ratio": round(sim / real, 3), "topis_source": src,
                             "sampled_share": round(cov, 2)})
    if not rows:
        sys.exit("[중단] 비교할 칸이 없습니다 (edgeData 파일·TOPIS CSV 확인).")
    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, "compare.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)

    def agg(key):
        g = defaultdict(list)
        for r in rows:
            g[key(r)].append(r)
        return {k: (sum(r["sim_kmh"] for r in v) / len(v), sum(r["topis_kmh"] for r in v) / len(v), len(v)) for k, v in sorted(g.items())}

    lr = [math.log(r["ratio"]) for r in rows]
    gm = math.exp(sum(lr) / len(lr))
    mape = sum(abs(r["sim_kmh"] - r["topis_kmh"]) / r["topis_kmh"] for r in rows) / len(rows) * 100
    same = sum(r["topis_source"] == "same_day" for r in rows)
    lines = ["# 시뮬레이션 도로 속도 vs TOPIS 실측", "",
             f"- 비교 칸(날짜×시간×링크): {len(rows)}개 (같은 날 실측 {same}, 전년 같은 달·요일 대리값 {len(rows) - same})",
             f"- 시뮬레이션/실측 속도 비(기하평균): **{gm:.2f}배**, 평균 절대 오차율 {mape:.0f}%", "",
             "## 도로별", "", "| 도로 | 시뮬 km/h | 실측 km/h | 비 | 칸 수 |", "|---|---|---|---|---|"]
    for k, (s, t, n) in agg(lambda r: r["road"]).items():
        lines.append(f"| {k} | {s:.1f} | {t:.1f} | {s / t:.2f} | {n} |")
    lines += ["", "## 시간대별 (전체 링크 평균)", "", "| 시 | 시뮬 km/h | 실측 km/h | 비 |", "|---|---|---|---|"]
    hours = agg(lambda r: r["hour"])
    for k, (s, t, n) in hours.items():
        lines.append(f"| {k} | {s:.1f} | {t:.1f} | {s / t:.2f} |")
    lines += ["", "## 날짜별", "", "| 날짜 | 시뮬 km/h | 실측 km/h | 비 | 실측 출처 |", "|---|---|---|---|---|"]
    srcs = {r["date"]: r["topis_source"] for r in rows}
    for k, (s, t, n) in agg(lambda r: r["date"]).items():
        lines.append(f"| {k} | {s:.1f} | {t:.1f} | {s / t:.2f} | {srcs[k]} |")
    with open(os.path.join(OUT, "summary.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print("\n".join(lines))
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from matplotlib import font_manager
        have = {f.name for f in font_manager.fontManager.ttflist}
        plt.rcParams["font.family"] = [f for f in ("Malgun Gothic", "NanumGothic", "Noto Sans CJK KR") if f in have] + ["DejaVu Sans"]
        hs = list(hours)
        fig, ax = plt.subplots(figsize=(7, 3.6))
        ax.plot(hs, [hours[h][1] for h in hs], "o-", label="TOPIS 실측")
        ax.plot(hs, [hours[h][0] for h in hs], "s-", label="시뮬레이션")
        ax.set_xlabel("시"); ax.set_ylabel("평균 속도 (km/h)"); ax.set_ylim(0, None); ax.grid(alpha=.3); ax.legend()
        ax.set_title("강남역 주변 간선도로 속도: 시뮬레이션 vs 실측")
        fig.tight_layout(); fig.savefig(os.path.join(OUT, "by_hour.png"), dpi=150)
    except Exception as e:  # noqa: BLE001
        print(f"[그림 생략] {e}")


if __name__ == "__main__":
    main()
