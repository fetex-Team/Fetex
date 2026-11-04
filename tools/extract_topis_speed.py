"""
TOPIS 「도로별 일자별 통행속도」 엑셀(월 1개, 30~45MB)에서 시뮬레이션 구역 링크만 뽑아 작은 CSV로 저장한다.

    python tools/extract_topis_speed.py                 # data/external/topis/*차량통행속도*.xlsx 전부
    python tools/extract_topis_speed.py 파일.xlsx ...
    입력 링크 목록: data/external/topis_links_gangnam.csv (tools/topis_links.py가 만든 것)
    출력: data/external/topis_speed_gangnam_<YYYYMM>.csv  (date, dow, link_id, road, from_node, to_node, hour, speed_kmh)

hour h = TOPIS의 "~(h+1)시" 열 = h시~h+1시 평균 속도(km/h). 빈 칸은 저장하지 않는다.
openpyxl만 쓰므로 노트북 기본 파이썬으로 돌아간다 (1개 파일 1~2분).
"""
import csv
import glob
import os
import re
import sys

import openpyxl

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LINKS = os.path.join(ROOT, "data", "external", "topis_links_gangnam.csv")
SRC = os.path.join(ROOT, "data", "external", "topis")


def main():
    with open(LINKS, encoding="utf-8") as f:
        links = {int(r["link_id"]) for r in csv.DictReader(f)}
    files = sys.argv[1:] or sorted(glob.glob(os.path.join(SRC, "*차량통행속도*.xlsx")))
    if not files:
        sys.exit("[중단] 통행속도 엑셀이 없습니다 (TOPIS 자료실 > 속도 정보 > 도로별 일자별 통행속도).")
    for path in files:
        ws = openpyxl.load_workbook(path, read_only=True).worksheets[0]
        rows = ws.iter_rows(values_only=True)
        head = [str(h or "") for h in next(rows)]
        col = {h: i for i, h in enumerate(head)}
        hours = [(i, int(m.group(1)) - 1) for i, h in enumerate(head) if (m := re.match(r"~(\d+)시", h))]
        out, ym = [], None
        for r in rows:
            if r[col["링크아이디"]] is None or int(r[col["링크아이디"]]) not in links:
                continue
            d = str(r[col["일자"]]); ym = ym or d[:6]
            base = [f"{d[:4]}-{d[4:6]}-{d[6:8]}", r[col["요일"]], int(r[col["링크아이디"]]),
                    r[col["도로명"]], r[col["시점명"]], r[col["종점명"]]]
            out += [base + [h, float(r[i])] for i, h in hours if r[i] not in (None, "", "-")]
        dst = os.path.join(ROOT, "data", "external", f"topis_speed_gangnam_{ym}.csv")
        with open(dst, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["date", "dow", "link_id", "road", "from_node", "to_node", "hour", "speed_kmh"])
            w.writerows(out)
        print(f"[저장] {os.path.basename(path)} -> {dst} ({len(out)}행, 링크 {len({o[2] for o in out})}개)")


if __name__ == "__main__":
    main()
