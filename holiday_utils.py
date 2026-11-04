"""공휴일/실행 날짜 판정 유틸 (프로젝트 루트에 두세요).

- is_holiday_date(d): 토·일 또는 한국 공휴일이면 True (holidays 패키지 필요, 없으면 주말만 휴일 취급)
- resolve_is_holiday(sim_date, mode): holiday_mode("auto"/"weekday"/"holiday")에 따라 최종 판정
- pick_run_date(...): run_date_mode("sim_date"/"alternate"/"random")에 따라 이번 실행의 날짜 결정
"""
import datetime
import os
import random

try:
    import holidays as _holidays
except ImportError:  # pip install holidays
    _holidays = None

ROOT = os.path.dirname(os.path.abspath(__file__))
_kr_cache = {}
_warned = False


def _to_date(value):
    if value is None:
        return None
    if isinstance(value, datetime.datetime):
        return value.date()
    if isinstance(value, datetime.date):
        return value
    return datetime.datetime.strptime(str(value)[:10], "%Y-%m-%d").date()


def is_holiday_date(value) -> bool:
    """토·일 또는 한국 공휴일이면 True."""
    global _warned
    d = _to_date(value)
    if d is None:
        return False
    if d.weekday() >= 5:
        return True
    if _holidays is None:
        if not _warned:
            _warned = True
            print("[경고] holidays 패키지가 없어 주말만 휴일로 판정합니다 (pip install holidays).")
        return False
    if d.year not in _kr_cache:
        _kr_cache[d.year] = _holidays.KR(years=[d.year])
    return d in _kr_cache[d.year]


def resolve_is_holiday(sim_date, mode: str = "auto") -> bool:
    """mode: "auto"=날짜로 판정, "weekday"=무조건 평일, "holiday"=무조건 공휴일."""
    if mode == "holiday":
        return True
    if mode == "weekday":
        return False
    return is_holiday_date(sim_date)


def count_existing_logs(log_dir: str = None) -> int:
    """alternate 모드용 인덱스. 파일 개수가 아니라 파일명 끝 yymmdd로 기존 로그의 평일/공휴일 수를 세서,
    평일이 더 많으면 홀수(=공휴일 차례), 아니면 짝수(=평일 차례)를 돌려줌. 병합/덮어쓰기로 파일 수가
    안 늘어도 균형이 맞음. (구 형식 demand_log_YYYYMMDD_HHMMSS.csv는 무시)"""
    import re
    log_dir = log_dir or os.path.join(ROOT, "data", "sim_logs")
    if not os.path.isdir(log_dir):
        return 0
    n_hol = n_wk = 0
    for f in os.listdir(log_dir):
        m = re.search(r"[A-Za-z\uac00-\ud7a3](\d{6})\.csv$", f)
        if not (f.startswith("demand_log_") and m):
            continue
        try:
            d = datetime.datetime.strptime("20" + m.group(1), "%Y%m%d").date()
        except ValueError:
            continue
        if "공휴일" in f or is_holiday_date(d):
            n_hol += 1
        else:
            n_wk += 1
    return 1 if n_wk > n_hol else 0


def pick_run_date(cfg: dict, index: int = 0, rng: random.Random = None,
                  mode: str = None, past_days: int = 90) -> datetime.date:
    """이번 실행의 시뮬레이션 날짜를 정한다.

    mode (기본은 cfg["run_date_mode"]):
      "sim_date"  : cfg["sim_date"] 고정. 평가·A/B처럼 날짜를 똑같이 맞춰야 할 때.
      "alternate" : 과거 past_days일 안에서 index가 짝수면 평일, 홀수면 공휴일인 날짜를 뽑음. 학습 로그 생성용.
      "random"    : 과거 past_days일 안에서 완전 랜덤.
    rng를 안 넘기면 시드와 무관한 난수를 써서 passenger_seed가 고정이어도 날짜가 매번 바뀜.
    """
    mode = mode or cfg.get("run_date_mode", "alternate")
    if mode == "sim_date":
        return _to_date(cfg.get("sim_date", "2026-09-07"))
    rng = rng or random.Random()
    today = datetime.date.today()
    want_holiday = (index % 2 == 1) if mode == "alternate" else None
    d = today - datetime.timedelta(days=rng.randint(0, past_days))
    for _ in range(300):
        if want_holiday is None or is_holiday_date(d) == want_holiday:
            break
        d = today - datetime.timedelta(days=rng.randint(0, past_days))
    return d