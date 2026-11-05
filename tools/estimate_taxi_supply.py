"""
강남역 구역에 평소 택시가 몇 대 있는지 실데이터로 추정한다 (시뮬레이션 택시 대수의 근거).

    python tools/estimate_taxi_supply.py
    입력: data/external/tims/nodelink/taxi_volume_gangnam_seocho_2025.csv
            (공공데이터포털 「국토교통부_전국 노드링크별 평균택시통행량_20251231」 중 강남·서초구, 표준링크별 하루 평균 택시 대수)
          data/external/tims/ops/ops_2025_per_taxi_day.csv
            (공공데이터포털 「국토교통부_택시운행정보관리시스템_택시운영현황_영업통계」 2025, 지자체·월별 1대당 1일 평균)
          data/external/topis/서울시 표준링크 매핑정보_*.xlsx, data/external/topis_links_gangnam.csv, data/external/topis_speed_gangnam_*.csv
    출력: results/taxi_supply/summary.md, links.csv

방법 (리틀의 법칙: 평균 대수 = 도착률 × 머무는 시간)
1. TOPIS 링크(구역 안 큰길 구간)마다 그 링크에 속한 표준링크들의 하루 택시 통행량 평균 V(대/일)
2. 구역 안 길이 L ÷ TOPIS 9~23시 평균 속도 v = 지나는 시간 T
3. 그 구간 위 평균 택시 수 = V × T / 86,400  → 모든 구간 합 = 24시간 평균
4. 낮(9~23시) 보정: 서울 택시 이용 중 9~23시 비중(서울시 2025, 평일 66.7%) ÷ (14/24)
5. 빈 차 비율: 서울 2025년 9~10월 공차시간 ÷ 운행시간
한계: 표준노드링크에 없는 골목은 빠져 있어 최소치, 지나가기만 하는 택시도 포함.
"""
import csv
import glob
import os
import statistics
from collections import defaultdict

import openpyxl

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXT = os.path.join(ROOT, "data", "external")
OUT = os.path.join(ROOT, "results", "taxi_supply")
DAY_SHARE_9_23 = 0.667       # 서울시 2025 교통이용 통계보고서, 평일 택시 이용 중 9~23시 비중


def main():
    vol = {r["link_id"]: float(r["avg_daily_taxis"])
           for r in csv.DictReader(open(os.path.join(EXT, "tims", "nodelink", "taxi_volume_gangnam_seocho_2025.csv"), encoding="utf-8"))}
    std = defaultdict(list)
    wb = openpyxl.load_workbook(sorted(glob.glob(os.path.join(EXT, "topis", "*표준링크 매핑*.xlsx")))[-1], read_only=True)
    for r in wb["data"].iter_rows(min_row=2, values_only=True):
        if r[0]:
            std[str(r[0])].append(str(r[1]))
    spd = defaultdict(list)
    for p in glob.glob(os.path.join(EXT, "topis_speed_gangnam_*.csv")):
        for r in csv.DictReader(open(p, encoding="utf-8")):
            if 9 <= int(r["hour"]) < 23:
                spd[r["link_id"]].append(float(r["speed_kmh"]))
    ops = [r for r in csv.DictReader(open(os.path.join(EXT, "tims", "ops", "ops_2025_per_taxi_day.csv"), encoding="utf-8"))
           if r["지자체명"] == "서울특별시" and r["연월"] in ("25-Sep", "25-Oct")]
    empty_share = sum(float(r["공차시간(초)"]) for r in ops) / sum(float(r["운행시간(초)"]) for r in ops)
    trips = statistics.mean(float(r["영엽횟수(회)"]) for r in ops)

    rows, total = [], 0.0
    for L in csv.DictReader(open(os.path.join(EXT, "topis_links_gangnam.csv"), encoding="utf-8")):
        vs = [vol[s] for s in std.get(L["link_id"], []) if s in vol]
        if not vs or not spd[L["link_id"]] or "고속도로" in L["road"]:
            continue
        V, v = statistics.mean(vs), statistics.mean(spd[L["link_id"]])
        n = V * float(L["in_net_m"]) / (v / 3.6) / 86400
        total += n
        rows.append([L["link_id"], L["road"], L["from_node"], L["to_node"], round(V), L["in_net_m"], round(v, 1), round(n, 2)])
    day = total * DAY_SHARE_9_23 / (14 / 24)
    empty = day * empty_share
    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, "links.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["link_id", "road", "from", "to", "taxis_per_day", "in_area_m", "speed_kmh_9_23", "avg_taxis_on_link"])
        w.writerows(rows)
    lines = ["# 강남역 구역 택시 공급 추정", "",
             f"- 큰길 {len(rows)}구간 위 평균 택시 수(24시간): **{total:.0f}대**",
             f"- 낮(9~23시) 기준: **{day:.0f}대** (9~23시 비중 {DAY_SHARE_9_23:.1%} ÷ 14/24)",
             f"- 빈 차 비율(서울 2025년 9~10월, 공차시간/운행시간): {empty_share:.1%} → 빈 택시 **약 {empty:.0f}대**",
             f"- 참고: 서울 택시 1대당 하루 영업 {trips:.0f}회",
             "- 한계: 골목(표준노드링크에 없는 도로)은 빠져 최소치. 지나가기만 하는 택시 포함.", ""]
    open(os.path.join(OUT, "summary.md"), "w", encoding="utf-8").write("\n".join(lines))
    print("\n".join(lines))


if __name__ == "__main__":
    main()
