"""학습에 쓸 수요 로그 선택 (data/sim_logs/selected_logs.json).

- 선택 파일이 없으면 -> demand_log_*.csv 전체 사용 (기존 동작 그대로)
- 선택 파일이 있으면 -> 거기에 적힌 파일만 사용 (이후 새로 생긴 로그는 자동 포함되지 않음)
- config_gui.py의 '학습 로그 선택' 창이 이 파일을 만들고, 학습 스크립트들은 selected_log_paths()로 읽음.
"""
import glob
import json
import os
import re
from datetime import datetime

ROOT = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = os.path.join(ROOT, "data", "sim_logs")
SELECTION_PATH = os.path.join(LOG_DIR, "selected_logs.json")

# demand_log_{구역}{전략}_{알고리즘}{yymmdd}.csv  — 글자 바로 뒤의 6자리 숫자가 날짜
_NAME_RE = re.compile(r"^demand_log_(?P<label>.+?)(?P<ymd>\d{6})\.csv$")


def parse_log_name(filename: str) -> dict:
    """파일명에서 라벨/날짜 추출. 예전 형식(demand_log_YYYYMMDD_HHMMSS.csv)은 date=None."""
    m = _NAME_RE.match(os.path.basename(filename))
    if not m or not re.search(r"[A-Za-z\uac00-\ud7a3]$", m.group("label")):
        return {"label": os.path.basename(filename)[len("demand_log_"):-len(".csv")], "date": None}
    try:
        d = datetime.strptime("20" + m.group("ymd"), "%Y%m%d").date()
    except ValueError:
        d = None
    return {"label": m.group("label"), "date": d}


def _count_rows(path: str) -> int:
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            return max(sum(1 for _ in f) - 1, 0)
    except OSError:
        return 0


def list_logs(log_dir: str = None) -> list:
    """[{filename, label, date, is_holiday, rows}] 날짜순."""
    log_dir = log_dir or LOG_DIR
    try:
        from holiday_utils import is_holiday_date
    except Exception:  # holiday_utils 못 읽어도 목록은 보여줌
        is_holiday_date = None
    out = []
    for p in sorted(glob.glob(os.path.join(log_dir, "demand_log_*.csv"))):
        info = parse_log_name(p)
        out.append({
            "filename": os.path.basename(p),
            "label": info["label"],
            "date": info["date"],
            # 파일명에 '공휴일' 표식이 있으면 그걸 우선(실행 때 실제로 적용된 판정), 없으면 날짜로 판정
            "is_holiday": True if "공휴일" in info["label"] else (
                bool(is_holiday_date(info["date"])) if (is_holiday_date and info["date"]) else None),
            "rows": _count_rows(p),
        })
    out.sort(key=lambda r: (r["date"] is None, r["date"], r["filename"]))
    return out


def load_selection():
    """선택된 파일명 리스트. 선택 파일이 없거나 깨졌으면 None(= 전체 사용)."""
    try:
        with open(SELECTION_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        sel = data.get("selected")
        return list(sel) if isinstance(sel, list) else None
    except (OSError, ValueError):
        return None


def save_selection(filenames) -> None:
    os.makedirs(os.path.dirname(SELECTION_PATH), exist_ok=True)
    with open(SELECTION_PATH, "w", encoding="utf-8") as f:
        json.dump({"selected": sorted(filenames), "saved_at": datetime.now().isoformat(timespec="seconds")},
                  f, ensure_ascii=False, indent=2)


def clear_selection() -> None:
    """선택 해제 -> 다시 전체 사용."""
    try:
        os.remove(SELECTION_PATH)
    except OSError:
        pass


def add_new_logs_to_selection(since_ts: float) -> list:
    """since_ts(epoch) 이후 만들어지거나 갱신된 로그를, 선택 목록이 있을 때만 거기에 추가.
    (학습 팝업에서 '시뮬 먼저 돌려 로그 생성'을 켰을 때 방금 만든 로그가 빠지지 않게)"""
    sel = load_selection()
    if sel is None:
        return []
    added = []
    for p in glob.glob(os.path.join(LOG_DIR, "demand_log_*.csv")):
        fn = os.path.basename(p)
        if fn not in sel and os.path.getmtime(p) >= since_ts:
            added.append(fn)
    if added:
        save_selection(list(sel) + added)
    return added


def same_date_groups(filenames) -> dict:
    """{date: [파일명...]} — 같은 날짜가 2개 이상 선택된 경우만. (그날 수요가 겹쳐 합쳐짐)"""
    by_date = {}
    for fn in filenames:
        d = parse_log_name(fn)["date"]
        if d is not None:
            by_date.setdefault(d, []).append(os.path.basename(fn))
    return {d: v for d, v in by_date.items() if len(v) > 1}


def selected_log_paths(log_dir: str = None) -> list:
    """학습에 쓸 로그 전체 경로(정렬). 선택 파일이 있으면 그 목록만."""
    log_dir = log_dir or LOG_DIR
    all_paths = sorted(glob.glob(os.path.join(log_dir, "demand_log_*.csv")))
    sel = load_selection()
    if sel is None:
        paths = all_paths
    else:
        keep = set(sel)
        paths = [p for p in all_paths if os.path.basename(p) in keep]
        print(f"[로그 선택] 선택한 {len(paths)}개 사용 (전체 {len(all_paths)}개 중)")
        if not paths:
            print("[경고] 선택된 로그가 현재 data/sim_logs에 하나도 없습니다. "
                  "config_gui의 '학습 로그 선택'에서 다시 고르세요.")
    for d, names in sorted(same_date_groups(paths).items()):
        print(f"[경고] 같은 날짜({d}) 로그가 {len(names)}개 선택돼 그날 수요가 겹쳐 집계됩니다: {', '.join(names)}")
    return paths
