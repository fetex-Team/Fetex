"""
경찰청 교차로계획정보서비스(공공데이터포털 15056569)에서 서울 교차로 신호 계획을 받아 저장한다.

    1) 공공데이터포털 마이페이지의 일반 인증키를 아래 중 한 곳에 둔다 (저장소에 올라가지 않음)
       - 환경변수 DATA_GO_KR_KEY
       - 파일 secrets/data_go_kr_key.txt  (한 줄, Encoding/Decoding 어느 쪽이든 됨)
    2) python tools/fetch_signal_plans.py                  # 서울(L01) 전체 4종 계획
       python tools/fetch_signal_plans.py --name 강남       # 교차로명 검색만
    -> data/external/signal_plans/<기능>.json, summary.txt

받는 정보 (경찰청 기술문서 기준)
- getPlanCROPInfo  교차로 운영계획: 계획번호별 시작 시/분, 운영주기, 옵셋, A·B링 1~8현시 시간  ← 신호 시간의 핵심
- getPlanCRWDInfo  요일계획: 요일별 사용하는 계획번호
- getPlanCRHDInfo  특수일계획: 특수일(명절 등) 계획번호
- getPlanCRRSInfo  예약계획: 예약 기간·시간·제어코드
"""
import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASE = "https://apis.data.go.kr/1320000/PlanCrossRoadInfoService"
OPS = {"getPlanCROPInfo": "운영계획", "getPlanCRWDInfo": "요일계획",
       "getPlanCRHDInfo": "특수일계획", "getPlanCRRSInfo": "예약계획"}
OUT_DIR = os.path.join(ROOT, "data", "external", "signal_plans")


def load_key():
    key = os.environ.get("DATA_GO_KR_KEY")
    path = os.path.join(ROOT, "secrets", "data_go_kr_key.txt")
    if not key and os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            key = f.read().strip()
    if not key:
        sys.exit("[중단] 인증키가 없습니다. 환경변수 DATA_GO_KR_KEY 또는 secrets/data_go_kr_key.txt에 넣어 주세요.")
    return urllib.parse.unquote(key)   # Encoding 키를 넣어도 Decoding 키로 바꿔 쓴다


def call(op, key, page, rows, region, name):
    q = {"ServiceKey": key, "pageNo": page, "numOfRows": rows, "type": "json", "srchCTId": region}
    if name:
        q["srchCRNm"] = name
    url = f"{BASE}/{op}?{urllib.parse.urlencode(q)}"
    req = urllib.request.Request(url, headers={"User-Agent": "fetex-signal/1.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        text = r.read().decode("utf-8")
    if page == 1:   # 응답 구조 확인용 원문 (인증키는 들어 있지 않음)
        os.makedirs(OUT_DIR, exist_ok=True)
        with open(os.path.join(OUT_DIR, f"raw_{op}_p1.txt"), "w", encoding="utf-8") as f:
            f.write(text[:20000])
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        hint = ""
        if "SERVICE_KEY_IS_NOT_REGISTERED" in text or "SERVICE KEY IS NOT REGISTERED" in text.upper():
            hint = " -> 인증키가 이 서비스에 아직 등록되지 않음. 승인 직후엔 1~2시간 뒤 다시 실행"
        elif "LIMITED_NUMBER_OF_SERVICE_REQUESTS" in text:
            hint = " -> 하루 호출 한도 초과"
        raise RuntimeError(f"JSON이 아닌 응답{hint}: {text[:300]}")


def items_of(payload):
    """공공데이터포털 응답에서 (항목 목록, 전체 건수, 결과코드, 메시지)를 꺼낸다 (구조 차이에 관대하게).
    이 서비스는 type=json일 때 [헤더(totalCount 등), 항목, 항목, ...] 배열을 돌려준다 (한 페이지 최대 100건)."""
    if isinstance(payload, list):
        head = payload[0] if payload and isinstance(payload[0], dict) and "totalCount" in payload[0] else None
        items = payload[1:] if head else payload
        total = int(head["totalCount"]) if head else None
        code = head.get("resultCode") if head else None
        return items, total, code, head.get("resultMsg") if head else None
    resp = payload.get("response") or payload
    body = resp.get("body") or {}
    header = resp.get("header") or {}
    code, msg = header.get("resultCode", body.get("resultCode")), header.get("resultMsg", body.get("resultMsg"))
    items = body.get("items", [])
    if isinstance(items, dict):
        items = items.get("item", [])
    if isinstance(items, dict):
        items = [items]
    total = int(body.get("totalCount", len(items)) or 0)
    return items or [], total, code, msg


def fetch(op, key, region, name, rows, max_pages):
    out, page, total = [], 1, None
    while True:
        items, total, code, msg = items_of(call(op, key, page, rows, region, name))
        if code not in (None, "00", "0", 0):
            raise RuntimeError(f"{op} 결과코드 {code}: {msg}")
        out.extend(items)
        done = (len(items) < rows) if total is None else (len(out) >= total)
        if page % 50 == 0:
            print(f"    {op} {len(out)}/{total if total is not None else '?'}", flush=True)
        if done or not items or page >= max_pages:
            break
        page += 1
        time.sleep(0.05)
    return out, (total if total is not None else len(out))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--region", default="L01", help="지역코드 (L01 = 서울)")
    ap.add_argument("--name", default=None, help="교차로명 검색어 (예: 강남, 역삼)")
    ap.add_argument("--rows", type=int, default=100, help="이 서비스는 한 페이지 최대 100건")
    ap.add_argument("--max-pages", type=int, default=1000)
    a = ap.parse_args()
    key = load_key()
    os.makedirs(OUT_DIR, exist_ok=True)
    lines = []
    for op, label in OPS.items():
        try:
            items, total = fetch(op, key, a.region, a.name, a.rows, a.max_pages)
        except Exception as e:  # noqa: BLE001  어떤 오류든 원인을 보여 주고 다음 기능으로
            import traceback
            traceback.print_exc()
            if isinstance(e, urllib.error.HTTPError):
                e = RuntimeError(f"HTTP {e.code}: {e.read().decode('utf-8', 'replace')[:300]}")
            print(f"[실패] {label}({op}): {e}")
            lines.append(f"{op}\t실패\t{e}")
            continue
        suffix = f"_{a.name}" if a.name else ""
        path = os.path.join(OUT_DIR, f"{op}{suffix}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(items, f, ensure_ascii=False, indent=1)
        names = sorted({str(i.get("INT_NM", "")) for i in items})
        print(f"[저장] {label}({op}): {len(items)}/{total}건, 교차로 {len(names)}곳 -> {path}")
        lines.append(f"{op}\t{len(items)}/{total}\t교차로 {len(names)}곳")
        gangnam = [n for n in names if any(k in n for k in ("강남", "역삼", "논현", "서초", "교보", "우성", "뱅뱅", "국기원"))]
        if gangnam:
            print(f"        강남역 주변으로 보이는 교차로: {', '.join(gangnam[:30])}{' ...' if len(gangnam) > 30 else ''}")
            lines.append("\t강남역 주변 후보: " + ", ".join(gangnam))
    with open(os.path.join(OUT_DIR, "summary.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


if __name__ == "__main__":
    main()
