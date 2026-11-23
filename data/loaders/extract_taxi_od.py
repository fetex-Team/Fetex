"""
한국교통연구원 「택시 기종점 통행량」 원본 CSV(약 1.4GB)에서 강남역 500m 격자 9칸 관련 행만 뽑고 요약한다.

    python data/loaders/extract_taxi_od.py <ES1004AO00101QY2404_csv.csv>
입력: 부동산 빅데이터 플랫폼(bigdata-realestate.kr) 무료 상품 「택시 기종점 통행량」 — 파일 기준월(DATA_STRD_YM) 202312, WEEK_CD=1(평일)
      격자 표기 '다사58a44b' = 1km 번호(58, 44) + 500m 반칸(a=앞, b=뒤). 같은 시군구면 도착도 격자, 다르면 읍면동 코드
출력: data/external/taxi_od/od_taxi_gangnam_2024Q4_extract.csv  (출발 또는 도착이 9칸인 행)
      data/external/taxi_od/od_gangnam_internal_share.csv       (칸별 출발 통행 중 9칸 안 도착 비율)
      data/external/taxi_od/od_gangnam_hourly.csv               (9칸 출발 통행의 TM_CD별 합)
값은 전수화 평균 통행량(CFTM_AVE_EXPDG_TRFC_QTY). 표본(프로브) 비율이 칸마다 달라 총량이 아닌 '비율'로만 쓴다.
"""
import csv
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OUT = os.path.join(ROOT, "data", "external", "taxi_od")
CELLS = ["다사57b44a", "다사57b44b", "다사57b45a", "다사58a44a", "다사58a44b", "다사58a45a",
         "다사58b44a", "다사58b44b", "다사58b45a"]   # = 500m 격자 다사115~117 x 088~090
PAT = re.compile("|".join(CELLS))
Q = "CFTM_AVE_EXPDG_TRFC_QTY"


def main():
    if any(a in ("-h", "--help") for a in sys.argv[1:]):    # 사용법만 출력
        print(__doc__)
        return
    src = sys.argv[1]
    os.makedirs(OUT, exist_ok=True)
    rows = []
    with open(src, encoding="utf-8", errors="replace", newline="") as f:
        header = next(csv.reader([f.readline()]))
        for line in f:
            if PAT.search(line):
                r = next(csv.reader([line]))
                if len(r) == len(header) and r[0] != "DATA_STRD_YM":
                    rows.append(dict(zip(header, r)))
    with open(os.path.join(OUT, "od_taxi_gangnam_2024Q4_extract.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=header)
        w.writeheader()
        w.writerows(rows)
    dep = [r for r in rows if r["DPTR_LTTC_ID"] in CELLS]
    tot, inside, hourly = {}, {}, {}
    for r in dep:
        g, v = r["DPTR_LTTC_ID"], float(r[Q])
        tot[g] = tot.get(g, 0.0) + v
        if r["ARVL_PYN_ID"] in CELLS:
            inside[g] = inside.get(g, 0.0) + v
        hourly[int(r["TM_CD"])] = hourly.get(int(r["TM_CD"]), 0.0) + v
    with open(os.path.join(OUT, "od_gangnam_internal_share.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["grid_od", "trips_per_day", "internal_trips", "internal_share"])
        for g in sorted(tot):
            w.writerow([g, round(tot[g], 6), round(inside.get(g, 0.0), 6), round(inside.get(g, 0.0) / tot[g], 6)])
    with open(os.path.join(OUT, "od_gangnam_hourly.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["tm_cd", "weekday_trips"])
        for h in sorted(hourly):
            w.writerow([h, round(hourly[h], 6)])
    print(f"{len(rows)}행 추출, 9칸 출발 {len(dep)}행, 구역 안 하차 {sum(inside.values()) / sum(tot.values()):.1%}")


if __name__ == "__main__":
    main()
