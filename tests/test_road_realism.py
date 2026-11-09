"""
지도 현실화(신호 계획·속도 보정) 테스트.

실행: python tests/test_road_realism.py   (pytest로도 실행 가능)
- SUMO 실행 불필요. 현시 대응 규칙, 요일 종류 판정, 계획 구간 선택, 속도 배율 표를 검사한다.
"""
import datetime as dt
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from module1_simulation.signal_plans import SignalPlanController, build_logic_phases  # noqa: E402
from module1_simulation.speed_profile import day_type  # noqa: E402

# 강남역 지도 기본 신호(4단계, 주기 90초)와 같은 모양
BASE = [(22, "rrGGGGGrrrrrrrrrrrrrrrrrGGGGGg"), (3, "rryyyyyrrrrrrrrrrrrrrrrryyyyyy"), (1, "r" * 30),
        (24, "GGrrrrGGGGggrrrrrrGGGGggrrrrrr"), (3, "yyrrrryyyyggrrrrrryyyyggrrrrrr"),
        (6, "rrrrrrrrrrGGrrrrrrrrrrGGrrrrrr"), (3, "rrrrrrrrrryyrrrrrrrrrryyrrrrrr"),
        (24, "GGrrrrrrrrrrGGGGGgGrrrrrrrrrrr"), (3, "yyrrrrrrrrrryyyyyyyrrrrrrrrrrr"), (1, "r" * 30)]


def test_phases_sum_to_real_cycle_and_keep_states():
    for phases, cycle in (([50, 34, 42, 24], 150), ([47, 36, 48, 29], 160), ([54, 34, 48, 34], 170)):
        new = build_logic_phases(BASE, phases, cycle)
        assert sum(d for d, _ in new) == cycle
        assert [s for _, s in new] == [s for _, s in BASE]          # 신호 모양(어느 차로가 녹색인지)은 그대로
        assert all(d >= 5 for d, s in new if "G" in s and "y" not in s)   # 녹색 최소 5초


def test_longest_phase_goes_to_widest_green_stage():
    new = build_logic_phases(BASE, [50, 34, 42, 24], 150)
    greens = {s: d for d, s in new if "G" in s and "y" not in s}
    widest = max(greens, key=lambda s: sum(c in "Gg" for c in s))
    # 가장 긴 현시(50초) - 그 단계의 황색·전적색 = 녹색
    assert greens[widest] == max(greens.values())


def test_single_stage_approach_gets_longest_phase_then_red():
    base = [(82, "GGGG"), (3, "yyyy"), (5, "rrrr")]
    new = build_logic_phases(base, [62, 30, 68], 160)
    assert sum(d for d, _ in new) == 160
    assert new[0] == (60.0, "GGGG") and new[-1][1] == "rrrr"


def test_day_type_separates_saturday_sunday_holiday():
    assert day_type(dt.date(2026, 9, 8)) == "weekday"
    assert day_type(dt.date(2026, 9, 12)) == "saturday"
    assert day_type(dt.date(2026, 9, 13)) == "sunday"
    assert day_type(dt.date(2026, 9, 12), is_holiday=True) == "saturday"   # '주말' 플래그로 토요일을 일요일로 바꾸지 않음
    assert day_type(dt.date(2025, 10, 6)) == "sunday"                       # 추석 연휴(월)


def test_plan_segment_follows_weekday_and_time():
    data = {"intersections": {"t": {"name": "x", "weekday_plan": {"1": "4", "2": "1", "7": "3"}, "holiday_plan": [],
                                    "plans": {"1": [{"start": "05:00", "cycle": 150, "offset": 0, "phases": [75, 75]},
                                                    {"start": "16:30", "cycle": 170, "offset": 0, "phases": [85, 85]}],
                                              "4": [{"start": "00:00", "cycle": 140, "offset": 0, "phases": [70, 70]}]}}}}
    c = SignalPlanController(data, dt.date(2026, 9, 7), sim_start_hour=9)          # 월요일 -> 2번 요일 -> 1번 계획
    info = c.data["t"]
    assert c._segment(info, 9 * 3600)[1]["cycle"] == 150
    assert c._segment(info, 17 * 3600)[1]["cycle"] == 170
    assert c._segment(info, 3 * 3600)[1]["cycle"] == 140      # 월요일 첫 구간(5시) 전 = 전날(일요일, 4번 계획) 마지막 구간


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
