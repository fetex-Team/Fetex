"""
예측 기반 재배치(module4_dispatch/forecast_repositioner.py) 테스트.

실행: python tests/test_forecast_repositioner.py   (pytest로도 실행 가능)
- SUMO 불필요. 배치 공식과 "미래 호출을 보지 않는다"는 점을 검사한다.
"""
import datetime as dt
import os
import sys
import types

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from module4_dispatch.forecast_repositioner import ForecastRepositioner, target_allocation  # noqa: E402


def test_allocation_sums_and_follows_two_thirds_power():
    lam = [8.0, 1.0, 0.0, 27.0]
    t = target_allocation(lam, 20, 2 / 3)
    assert t.sum() == 20 and t[2] == 0
    w = np.power(lam, 2 / 3)
    expected = w / w.sum() * 20               # 4 : 1 : 0 : 9
    assert np.all(np.abs(t - expected) < 1.0)
    # 정비례(power=1)보다 한산한 칸에 더 많이 남긴다
    assert target_allocation(lam, 20, 1.0)[1] <= t[1]
    assert target_allocation(lam, 0, 2 / 3).sum() == 0
    assert target_allocation([0, 0], 5, 2 / 3).sum() == 0


def test_observe_never_reads_future_calls():
    r = ForecastRepositioner.__new__(ForecastRepositioner)
    r.start_dt = dt.datetime(2026, 9, 7, 9)
    r._sched = [(10.0, "a"), (600.0, "b"), (601.0, "a"), (5000.0, "b")]
    r._sched_i = 0
    seen = []                                  # 예측기(Module 3)에 넘어간 호출
    r.predictor = types.SimpleNamespace(add_call=lambda when, cell: seen.append((when, cell)))
    r._observe(600.0)
    assert [c for _, c in seen] == ["a", "b"]  # 600초까지만
    assert max(t for t, _ in seen) <= r.start_dt + dt.timedelta(seconds=600)
    r._observe(600.0)
    assert len(seen) == 2                      # 다시 불러도 중복 없음
    r._observe(10_000.0)
    assert [c for _, c in seen] == ["a", "b", "a", "b"]


def test_pick_edges_spreads_over_roads():
    r = ForecastRepositioner.__new__(ForecastRepositioner)
    import random
    r._rng = random.Random(0)
    r.cell_edges = {"c": [("e1", 50), ("e2", 30), ("e3", 20)]}
    r.edge_cells = {"e1": "c", "e2": "c", "e3": "c"}
    picks = r._pick_edges("c", 3)
    assert sorted(picks) == ["e1", "e2", "e3"]   # 같은 도로 하나로 몰지 않음
    assert len(r._pick_edges("c", 5)) == 5        # 도로 수보다 많으면 중복 허용


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"[PASS] {t.__name__}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"[FAIL] {t.__name__}: {e!r}")
    print(f"\n{len(tests) - failed}/{len(tests)} 통과")
    sys.exit(1 if failed else 0)
