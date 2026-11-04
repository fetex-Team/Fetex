"""
기상청 지상(종관, ASOS) 시간자료(공공데이터포털)에서 서울(108) 시간별 관측을 받아 저장한다.

    인증키: 환경변수 DATA_GO_KR_KEY 또는 secrets/data_go_kr_key.txt (tools/fetch_signal_plans.py와 같은 키)
    python tools/fetch_weather_asos.py 2026-07-01 2026-10-27
    -> data/external/asos/asos108_<시작>_<끝>.csv  (time, temperature, precipitation, wind_speed[km/h])

용도: 시뮬레이션 날씨(Open-Meteo, 재분석 격자값)가 기상청 실측과 얼마나 맞는지 검증한다
(tools/compare_weather_sources.py). 시뮬레이션이 읽는 data/external/weather_*.csv와 폴더를 분리해,
받기만 해서는 시뮬레이션 결과가 바뀌지 않게 했다.
풍속은 Open-Meteo와 단위를 맞추려고 m/s -> km/h(×3.6)로 바꿔 저장한다. 강수 없음(빈 값)은 0.
"""
import csv
import datetime as dt
import json
import os
import sys
import time
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
URL = "https://apis.data.go.kr/1360000/AsosHourlyInfoService/getWthrDataList"
OUT_DIR = os.path.join(ROOT, "data", "external", "asos")


def load_key():
    key = os.environ.get("DATA_GO_KR_KEY")
    path = os.path.join(ROOT, "secrets", "data_go_kr_key.txt")
    if not key and os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            key = f.read().strip()
    if not key:
        sys.exit("[중단] 인증키가 없습니다. 환경변수 DATA_GO_KR_KEY 또는 secrets/data_go_kr_key.txt에 넣어 주세요.")
    return urllib.parse.unquote(key)


def num(v, default=None):
    try:
        return float(v) if v not in (None, "", " ") else default
    except ValueError:
        return default


def fetch_chunk(key, stn, d0, d1):
    rows, page = [], 1
    while True:
        q = {"serviceKey": key, "pageNo": page, "numOfRows": 999, "dataType": "JSON", "dataCd": "ASOS",
             "dateCd": "HR", "startDt": d0.strftime("%Y%m%d"), "startHh": "00",
             "endDt": d1.strftime("%Y%m%d"), "endHh": "23", "stnIds": stn}
        req = urllib.request.Request(f"{URL}?{urllib.parse.urlencode(q)}", headers={"User-Agent": "fetex-asos/1.0"})
        with urllib.request.urlopen(req, timeout=30) as r:
            text = r.read().decode("utf-8")
        try:
            body = json.loads(text)["response"]
        except (json.JSONDecodeError, KeyError):
            raise RuntimeError(f"응답 오류(인증키·기간 확인): {text[:300]}")
        if body["header"]["resultCode"] != "00":
            raise RuntimeError(f"{body['header']['resultCode']}: {body['header']['resultMsg']}")
        items = body["body"]["items"]["item"]
        rows.extend(items if isinstance(items, list) else [items])
        if len(rows) >= int(body["body"]["totalCount"]):
            return rows
        page += 1
        time.sleep(0.2)


def main():
    if len(sys.argv) < 3:
        sys.exit("사용: python tools/fetch_weather_asos.py 시작일 종료일 [지점번호=108]")
    d0, d1 = dt.date.fromisoformat(sys.argv[1]), dt.date.fromisoformat(sys.argv[2])
    stn = sys.argv[3] if len(sys.argv) > 3 else "108"
    key = load_key()
    out = []
    cur = d0
    while cur <= d1:                      # 한 번에 30일씩
        end = min(cur + dt.timedelta(days=29), d1)
        items = fetch_chunk(key, stn, cur, end)
        out.extend(items)
        print(f"[받음] {cur} ~ {end}: {len(items)}시간")
        cur = end + dt.timedelta(days=1)
    os.makedirs(OUT_DIR, exist_ok=True)
    path = os.path.join(OUT_DIR, f"asos{stn}_{d0}_{d1}.csv")
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["time", "temperature", "precipitation", "wind_speed"])
        for it in sorted(out, key=lambda x: x["tm"]):
            ws = num(it.get("ws"))
            w.writerow([it["tm"].replace(" ", "T"), num(it.get("ta"), ""), num(it.get("rn"), 0.0),
                        round(ws * 3.6, 2) if ws is not None else ""])
    print(f"[저장] {path} ({len(out)}시간, 지점 {stn})")


if __name__ == "__main__":
    main()
