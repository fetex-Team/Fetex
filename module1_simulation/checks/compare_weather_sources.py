"""
시뮬레이션 날씨(Open-Meteo)와 기상청 ASOS 서울(108) 실측을 비교한다.

    python module1_simulation/checks/compare_weather_sources.py
    (data/external/weather_강남역_*.csv 와 data/external/asos/asos108_*.csv 가 있어야 함)

출력: 시간별·일별 강수 상관, 비 온 시간 일치율, 시간당 강수 차이, 기온 차이, 평가 7일 9~23시 강수 비교.
수요 모델은 시간별 강수만 쓰므로(1mm당 +1.99%) 강수 일치도가 핵심이다.
"""
import csv
import glob
import math
import os
import sys
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
EVAL_DATES = ["2026-09-07", "2026-10-13", "2026-10-14", "2026-09-10", "2026-09-18", "2026-10-24", "2026-10-18"]


def load(path):
    d = {}
    with open(path, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            def num(k):
                v = (r.get(k) or "").strip()
                return float(v) if v not in ("", "None", "nan") else None
            d[r["time"][:13]] = (num("precipitation"), num("temperature"))
    return d


def corr(x, y):
    n = len(x)
    mx, my = sum(x) / n, sum(y) / n
    sx = math.sqrt(sum((a - mx) ** 2 for a in x)); sy = math.sqrt(sum((b - my) ** 2 for b in y))
    return sum((a - mx) * (b - my) for a, b in zip(x, y)) / (sx * sy) if sx and sy else float("nan")


def main():
    om = sorted(p for p in glob.glob(os.path.join(ROOT, "data", "external", "weather_*.csv"))
                if "from_external" not in p)
    asos = sorted(glob.glob(os.path.join(ROOT, "data", "external", "asos", "asos108_*.csv")))
    if not om or not asos:
        sys.exit("[중단] Open-Meteo 파일과 ASOS 파일이 모두 있어야 합니다 (data/loaders/fetch_weather_asos.py 먼저 실행).")
    a, b = load(om[-1]), load(asos[-1])
    keys = sorted(k for k in a if k in b and a[k][0] is not None and b[k][0] is not None)
    pa, pb = [a[k][0] for k in keys], [b[k][0] for k in keys]
    da, db = defaultdict(float), defaultdict(float)
    for k, x, y in zip(keys, pa, pb):
        da[k[:10]] += x; db[k[:10]] += y
    days = sorted(da)
    wet_a = [x >= 0.1 for x in pa]; wet_b = [y >= 0.1 for y in pb]
    both = sum(p and q for p, q in zip(wet_a, wet_b)); either = sum(p or q for p, q in zip(wet_a, wet_b))
    ta = [(a[k][1], b[k][1]) for k in keys if a[k][1] is not None and b[k][1] is not None]
    print(f"비교 시간 {len(keys)}개 ({keys[0]} ~ {keys[-1]})  Open-Meteo: {os.path.basename(om[-1])}  ASOS: {os.path.basename(asos[-1])}")
    print(f"시간별 강수 상관 {corr(pa, pb):.3f} | 일별 강수 상관 {corr([da[d] for d in days], [db[d] for d in days]):.3f}")
    print(f"총 강수 Open-Meteo {sum(pa):.1f}mm vs ASOS {sum(pb):.1f}mm")
    print(f"비 온 시간(≥0.1mm) 일치: 둘 다 {both} / 어느 한쪽 {either} ({both / max(either, 1) * 100:.0f}%)")
    print(f"시간당 강수 평균 절대 차이 {sum(abs(x - y) for x, y in zip(pa, pb)) / len(pa):.2f}mm")
    if ta:
        print(f"기온 평균 차이 {sum(x - y for x, y in ta) / len(ta):+.2f}°C, 평균 절대 차이 {sum(abs(x - y) for x, y in ta) / len(ta):.2f}°C")
    print("\n평가 7일 9~23시 강수 (Open-Meteo / ASOS, mm) 와 수요 배율 차이")
    for d in EVAL_DATES:
        hs = [f"{d}T{h:02d}" for h in range(9, 23)]
        x = sum(a.get(h, (0, 0))[0] or 0 for h in hs); y = sum(b.get(h, (0, 0))[0] or 0 for h in hs)
        print(f"  {d}: {x:5.1f} / {y:5.1f}  (구간 평균 호출 배율 차이 {(x - y) * 0.0199 / 14 * 100:+.2f}%)")


if __name__ == "__main__":
    main()
