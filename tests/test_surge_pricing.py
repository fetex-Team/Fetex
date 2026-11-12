"""동적 호출비(module4_dispatch/surge_pricing.py)와 surge 전략 이동(forecast_repositioner mode="surge")."""
from collections import Counter
from types import SimpleNamespace

import numpy as np

from module4_dispatch.surge_pricing import CallFeeBoard, call_fee, imbalance, taxis_to_clear

S = {"unit": 1000, "step": 500, "max": 3000, "driver_share": 0.8, "min_incentive": 500, "horizon_sec": 300}


def test_fee_formula_examples():
    assert imbalance(6, 2) == 2.0
    assert call_fee(3, 4, S) == 0          # r = 0.6 → 택시 충분
    assert call_fee(6, 2, S) == 1000       # r = 2.0
    assert call_fee(9, 1, S) == 3000       # r = 4.5 → 상한
    assert call_fee(7, 3, S) == 500        # r = 1.75 → 750원 → 500원 단위 내림
    assert call_fee(0, 0, S) == 0


def test_taxis_to_clear_makes_fee_zero():
    for d, s in [(6, 2), (9, 1), (3.4, 0), (1, 0)]:
        need = taxis_to_clear(d, s)
        assert call_fee(d, s + need, S) == 0
        if need:
            assert call_fee(d, s + need - 1, S) > 0 or imbalance(d, s + need - 1) > 1


def test_board_quotes_fee_at_request_and_charges_only_picked():
    b = CallFeeBoard(["a", "b"], mean_fare_won=10000, settings=S)
    b.update(0, {"a": 6, "b": 1}, {"a": 2, "b": 3})
    assert b.quote("p1", "a") == 1000 and b.quote("p2", "b") == 0
    b.update(300, {"a": 0, "b": 0}, {"a": 5, "b": 5})
    assert b.quote("p1", "a") == 1000      # 이미 확정된 손님은 그대로
    sm = b.summary(["p1", "p2"])
    assert sm["call_fee_revenue_won"] == 1000 and sm["driver_incentive_won"] == 800
    assert sm["meter_revenue_won"] == 20000 and sm["total_revenue_won"] == 21000
    assert b.summary(["p2"])["call_fee_revenue_won"] == 0   # p1이 못 탔으면 호출비 없음


def test_surge_mode_moves_idle_taxis_to_fee_cell(monkeypatch):
    import module4_dispatch.forecast_repositioner as mod
    r = mod.ForecastRepositioner.__new__(mod.ForecastRepositioner)
    r.mode, r.cells = "surge", ["a", "b"]
    r.edge_cells = {"a0": "a", "b0": "b"}
    r.cell_edges = {"a": [("a0", 1)], "b": [("b0", 1)]}
    r.fee_board = CallFeeBoard(r.cells, mean_fare_won=10000, settings=S)
    r.tick_sec, r.last_tick, r.horizon, r.power = 300, -1, 3, 2 / 3
    r.bucket = SimpleNamespace(total_seconds=lambda: 600)
    r.fraction, r.min_idle_ratio, r.max_move_sec = 1.0, 0.0, 180
    r.committed, r.records, r.moves, r.stats = {}, [], [], Counter()
    import random
    r._rng = random.Random(0)
    r.travel_time = SimpleNamespace(time=lambda e1, p, e2, q: 60.0)
    # 칸 a: 다음 10분 예측 12건 → 5분 6건·빈 택시 0대 → 호출비 3,000원(상한), 칸 b: 예측 0건·빈 택시 4대 → 남는 택시
    r.predict = lambda now: np.array([[12.0] * 6, [0.0] * 6])
    taxis = {"t1": "b0", "t2": "b0", "t3": "b0", "t4": "b0"}
    moved = []
    vehicle = SimpleNamespace(getTaxiFleet=lambda flag: list(taxis), getRoadID=lambda v: taxis[v],
                              getLanePosition=lambda v: 0.0, getStops=lambda v, f: [],
                              changeTarget=lambda v, e: moved.append((v, e)))
    person = SimpleNamespace(getIDList=lambda: [])
    monkeypatch.setattr(mod, "traci", SimpleNamespace(vehicle=vehicle, person=person,
                                                     exceptions=SimpleNamespace(TraCIException=Exception)))
    r.maintain(600, idle_taxis=list(taxis))
    assert r.fee_board.fee["a"] == 3000 and r.fee_board.fee["b"] == 0
    # a의 호출비가 0원이 되려면 ceil(6)-1-0 = 5대 필요, b는 4대 모두 남음 → 4대 이동
    assert len(moved) == 4 and all(e == "a0" for _, e in moved)
    assert {rec["call_fee_won"] for rec in r.records if rec["h3_index"] == "a"} == {3000}


def test_expected_calls_matches_horizon():
    from module4_dispatch.forecast_repositioner import expected_calls
    pred = np.array([[10.0, 20.0, 30.0, 0, 0, 0]])
    assert expected_calls(pred, 600, 300)[0] == 5.0          # 10분 칸의 절반
    assert expected_calls(pred, 600, 900)[0] == 20.0         # 10 + 20의 절반
