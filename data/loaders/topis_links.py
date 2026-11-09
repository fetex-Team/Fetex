"""
TOPIS 서비스링크를 시뮬레이션 도로망(SUMO)에 연결한다.

    python data/loaders/topis_links.py
    입력: data/external/topis/서울시 서비스링크 보간점 정보_*.xlsx  (링크별 좌표, GRS80 중부원점 = EPSG:5181)
          data/external/topis/서울시 서비스링크 정보_*.xlsx        (도로명, 시작·종료 노드명, 길이)
          module1_simulation/sumo_config/grid.net.xml
    출력: data/external/topis_links_gangnam.csv   (작은 파일, 저장소에 포함)
          link_id, road, from_node, to_node, length_m, in_net_m, edges(;구분), matched_m, coverage

연결 방법
- 링크 좌표 선을 SUMO 좌표로 바꾼 뒤, 승용차가 다닐 수 있는 SUMO 엣지 중
  (1) 엣지 위 점의 80% 이상이 링크 선에서 20m 이내이고 (2) 진행 방향이 같은(내적 > 0.7) 엣지를 그 링크의 구간으로 본다.
- 대상 구역 = region.osm.xml의 <bounds> (지도를 받아 온 1km² 범위). 구역 밖 50m 미만만 걸치는 링크는 뺀다.
- coverage = 연결된 엣지 길이 합 / 링크 중 대상 구역 안 길이. 0.5 미만이면 비교에서 뺀다.
"""
import csv
import glob
import math
import os
import sys
from collections import defaultdict

import sumolib
from pyproj import Transformer

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TOPIS = os.path.join(ROOT, "data", "external", "topis")
NET = os.path.join(ROOT, "module1_simulation", "sumo_config", "grid.net.xml")
OSM = os.path.join(ROOT, "module1_simulation", "sumo_config", "region.osm.xml")   # <bounds> = 시뮬레이션 대상 구역
OUT = os.path.join(ROOT, "data", "external", "topis_links_gangnam.csv")
NEAR_M, DIR_COS, MIN_COVER = 20.0, 0.7, 0.5


def latest(pattern):
    files = sorted(glob.glob(os.path.join(TOPIS, pattern)))
    if not files:
        sys.exit(f"[중단] {pattern} 파일이 없습니다 (TOPIS 자료실 > 기타 정보 > 기반 정보).")
    return files[-1]


def seg_dist(p, a, b):
    ax, ay = a; bx, by = b; px, py = p
    dx, dy = bx - ax, by - ay
    L2 = dx * dx + dy * dy
    t = 0.0 if L2 == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / L2))
    qx, qy = ax + t * dx, ay + t * dy
    return math.hypot(px - qx, py - qy), (dx, dy)


def nearest(p, line):
    best = (1e18, (0.0, 0.0))
    for a, b in zip(line, line[1:]):
        d = seg_dist(p, a, b)
        if d[0] < best[0]:
            best = d
    return best


def unit(v):
    n = math.hypot(*v)
    return (v[0] / n, v[1] / n) if n else (0.0, 0.0)


def densify(shape, step=10.0):
    pts = []
    for a, b in zip(shape, shape[1:]):
        n = max(1, int(math.hypot(b[0] - a[0], b[1] - a[1]) // step))
        pts += [(a[0] + (b[0] - a[0]) * i / n, a[1] + (b[1] - a[1]) * i / n, unit((b[0] - a[0], b[1] - a[1]))) for i in range(n)]
    return pts


def main():
    if any(a in ("-h", "--help") for a in sys.argv[1:]):    # 사용법만 출력
        print(__doc__)
        return
    import openpyxl    # TOPIS 엑셀 읽기용 (requirements.txt), --help는 없이도 동작
    net = sumolib.net.readNet(NET)
    import xml.etree.ElementTree as ET
    for _, el in ET.iterparse(OSM, events=("start",)):
        if el.tag == "bounds":
            bd = {k: float(v) for k, v in el.attrib.items()}
            break
    xmin, ymin = net.convertLonLat2XY(bd["minlon"], bd["minlat"])
    xmax, ymax = net.convertLonLat2XY(bd["maxlon"], bd["maxlat"])
    to_tm = Transformer.from_crs(5181, 4326, always_xy=True)

    pts = defaultdict(list)
    for r in openpyxl.load_workbook(latest("*보간점*.xlsx"), read_only=True)["data"].iter_rows(min_row=2, values_only=True):
        if r[0] is not None:
            pts[int(r[0])].append((int(r[1]), float(r[2]), float(r[3])))
    info = {}
    for r in openpyxl.load_workbook(latest("*서비스링크 정보*.xlsx"), read_only=True)["data"].iter_rows(min_row=2, values_only=True):
        if r[0] is not None:
            info[int(r[0])] = r[1:5]

    def to_xy(x, y):
        lon, lat = to_tm.transform(x, y)
        return net.convertLonLat2XY(lon, lat)

    inside = lambda p: xmin <= p[0] <= xmax and ymin <= p[1] <= ymax
    edges = [e for e in net.getEdges() if e.getFunction() != "internal" and e.allows("passenger")]
    rows = []
    for lid, vs in pts.items():
        line = [to_xy(x, y) for _, x, y in sorted(vs)]
        dens = densify(line)
        in_net = sum(10.0 for p in dens if inside(p))
        if in_net < 50:
            continue
        matched, ms = [], 0.0
        for e in edges:
            ep = densify(e.getShape())
            if not ep:
                continue
            ok = 0
            for x, y, u in ep:
                d, segv = nearest((x, y), line)
                su = unit(segv)
                if d <= NEAR_M and u[0] * su[0] + u[1] * su[1] > DIR_COS:
                    ok += 1
            if ok >= 0.8 * len(ep):
                matched.append(e.getID()); ms += e.getLength()
        road, a, b, length = info.get(lid, ("", "", "", ""))
        cov = min(1.0, ms / in_net) if in_net else 0.0
        rows.append([lid, road, a, b, length, round(in_net), ";".join(sorted(matched)), round(ms), round(cov, 2)])
    rows.sort()
    with open(OUT, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["link_id", "road", "from_node", "to_node", "length_m", "in_net_m", "edges", "matched_m", "coverage"])
        w.writerows(rows)
    ok = [r for r in rows if r[-1] >= MIN_COVER]
    print(f"[저장] {OUT}: 도로망 안 링크 {len(rows)}개, 연결 충분(coverage≥{MIN_COVER}) {len(ok)}개")
    for r in rows:
        print(f"  {r[0]} {r[1]:<6} {r[2]}→{r[3]}  망내 {r[5]}m  엣지 {len(r[6].split(';')) if r[6] else 0}개  coverage {r[8]}")


if __name__ == "__main__":
    main()
