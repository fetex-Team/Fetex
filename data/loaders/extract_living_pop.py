"""
서울 생활인구(250m) 일별 zip에서 강남역 주변 격자만 뽑는다.

    python data/loaders/extract_living_pop.py <zip 폴더> [출력 CSV]
입력: 서울 열린데이터광장 「[내국인] 서울 생활인구(250m)」(OA-22784) 일별 파일 250_LOCAL_RESD_YYYYMMDD.zip (cp949 CSV)
출력: data/external/seoul_living_pop/living_pop_250m_gangnam_<첫날>_<끝날>.csv (date, hour, adm_code, grid250, pop)

격자 코드 '다사XXXXYYYY': 다사 칸 원점(UTM-K X 900,000 / Y 1,900,000)으로부터 좌하단까지 거리(10m 단위).
강남역 시뮬레이션 영역(UTM-K X 957,685~958,689, Y 1,944,124~1,945,117)과 겹치는 칸 + 한 칸 여유를 남긴다.
"""
import csv
import glob
import io
import os
import sys
import zipfile

X_RANGE = (5725, 5875)   # 10m 단위, 250m(25) 간격
Y_RANGE = (4375, 4525)


def main():
    if any(a in ("-h", "--help") for a in sys.argv[1:]):    # 사용법만 출력
        print(__doc__)
        return
    src = sys.argv[1] if len(sys.argv) > 1 else "."
    files = sorted(glob.glob(os.path.join(src, "250_LOCAL_RESD_*.zip")))
    if not files:
        raise SystemExit(f"250_LOCAL_RESD_*.zip 파일이 없습니다: {src}")
    first, last = (os.path.basename(f)[15:23] for f in (files[0], files[-1]))
    out = sys.argv[2] if len(sys.argv) > 2 else os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "data", "external", "seoul_living_pop",
        f"living_pop_250m_gangnam_{first}_{last}.csv")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    n = 0
    with open(out, "w", newline="", encoding="utf-8") as fo:
        w = csv.writer(fo)
        w.writerow(["date", "hour", "adm_code", "grid250", "pop"])
        for p in files:
            z = zipfile.ZipFile(p)
            with z.open(z.namelist()[0]) as f:
                r = csv.reader(io.TextIOWrapper(f, encoding="cp949", errors="replace"))
                next(r)
                for row in r:
                    g = row[3]
                    if not g.startswith("다사") or len(g) != 10:
                        continue
                    x, y = int(g[2:6]), int(g[6:10])
                    if X_RANGE[0] <= x <= X_RANGE[1] and Y_RANGE[0] <= y <= Y_RANGE[1]:
                        w.writerow([row[0], row[1], row[2].strip(), g, row[4]])
                        n += 1
    print(f"{len(files)}일, {n}행 -> {out}")


if __name__ == "__main__":
    main()
