"""
예측 기반 빈 택시 재배치 (taxi_strategy = "forecast") + 동적 호출비 갱신 (surge_pricing.CallFeeBoard).

mode:
- "forecast": 아래 1)~3) (예측 비율 배치)
- "surge"   : 1) 예측 → 칸별 호출비 → 호출비(기사 인센티브)가 높은 칸으로 빈 택시 이동 (기사가 인센티브를 보고 움직임)
- "observe" : 1) 예측 → 칸별 호출비만 갱신, 택시는 움직이지 않음 (순찰·시간표·RL 전략에서 요금만 계산)

수요 예측 모델(saved_models/demand_v2.joblib, H3 res 8 칸별 10분 x 6칸 앞)을 배차에 직접 쓰는 전략.
빈 택시는 평소에는 순찰(patrol)과 똑같이 움직이고, 5분마다 아래를 한 번 한다.

1) 예측: 지금까지 관측된 호출(지난 7일 + 오늘)만으로 피처를 만들어 칸별 다음 30분 호출 수 λ_c를 예측한다.
   λ_c = 진행 중인 시간 칸 t(=floor(지금))의 다음 칸부터 forecast_horizon_buckets칸(t+1..t+3, 10분 칸이면 30분) 합.
   진행 중인 칸 t 자체는 학습 타깃에 없으므로(행 t의 타깃은 t+1..t+6) λ에 들어가지 않는다.
   예측 자체는 Module 3의 module3_prediction/h3_predictor.H3DemandPredictor가 하고, 여기서는 관측 호출을 넘겨준다.
   - 지난 7일: 학습 로그와 같은 생성기(demand_model, data/loaders/generate_demand_logs.py와 같은 시드)로 만든 과거 호출
   - 오늘: 시작 시각 이전 호출 + 시뮬레이션에서 지금까지 실제로 나온 호출. 미래 호출은 보지 않는다.
   - 날씨: 실측 시간별 날씨(학습과 같은 merge_external_data). 학습·서빙 피처가 같다.
2) 목표 배치: 빈 택시 N대를 칸별 λ_c^α 비율로 나눈다 (α = forecast_alloc_power, 기본 2/3).
   손님이 무작위로 나타날 때 가장 가까운 빈 택시까지의 평균 거리는 그 칸의 택시 밀도의 −1/2제곱에 비례하므로,
   Σ λ_c · n_c^(−1/2)을 최소화하는 배치가 n_c ∝ λ_c^(2/3)이다 (수요에 정비례로 몰면 한산한 칸이 비어 손해).
3) 이동: 목표보다 많은 칸(남는 칸)의 빈 택시를 모자란 칸으로 보낸다.
   - 도착 도로: 그 칸에서 과거 호출이 많았던 도로를 호출 비율대로 골라 여러 도로로 흩어 보낸다.
   - 누구를 어디로: 도로망 이동시간(다익스트라, network_cost.py) 비용의 헝가리안 매칭.
   - 제한: 이동시간 forecast_max_move_sec(기본 180초) 이하만, 한 번에 빈 택시의 reposition_fraction(기본 30%)까지.
   이동 중인 택시도 빈 택시라 그 사이 호출이 오면 그대로 배차된다(헝가리안 배차가 우선).

설정(config.json): forecast_tick_sec(300), forecast_horizon_buckets(3 = 30분), forecast_alloc_power(0.6667),
forecast_max_move_sec(180), reposition_fraction(0.3)
"""
from __future__ import annotations

import datetime as _dt
import math
import random
from collections import Counter, defaultdict

import numpy as np
import pandas as pd
import traci
from scipy.optimize import linear_sum_assignment

from config_loader import CFG
from module3_prediction.h3_predictor import MODEL_PATH, H3DemandPredictor
from module4_dispatch.forecast_dispatcher import allocate_targets
from module4_dispatch.network_cost import UNREACHABLE
from module4_dispatch.surge_pricing import taxis_to_clear


def expected_calls(pred, bucket_sec: float, horizon_sec: float) -> np.ndarray:
    """다음 horizon_sec 동안의 칸별 예상 호출 수. pred 열 k = t+(k+1)칸 예측, 칸 길이 bucket_sec.
    예: 10분 칸, 5분 → t+1 예측의 절반."""
    out = np.zeros(pred.shape[0])
    left = float(horizon_sec)
    for k in range(pred.shape[1]):
        if left <= 0:
            break
        part = min(left, bucket_sec) / bucket_sec
        out += pred[:, k] * part
        left -= bucket_sec
    return out


def target_allocation(predicted, n_idle: int, power: float) -> np.ndarray:
    """칸별 예측 호출 수 → 빈 택시 목표 대수 (λ^power 비율, 정수 배분, 합 = n_idle)."""
    w = np.power(np.maximum(np.asarray(predicted, dtype=float), 0.0), power)
    if n_idle <= 0 or w.sum() <= 0:
        return np.zeros(len(w), dtype=int)
    return allocate_targets(w, np.ones(len(w)), n_idle)


class ForecastRepositioner:
    def __init__(self, meta: dict, run_date: _dt.date, is_holiday: bool, sim_start_hour: float,
                 demand_model, schedule, seed, travel_time=None, model_path: str = MODEL_PATH,
                 mode: str = "forecast", fee_board=None):
        self.mode = mode
        self.fee_board = fee_board   # surge_pricing.CallFeeBoard (없으면 호출비 계산 안 함)
        self.edge_cells = meta["edge_cells"]
        self.cells = sorted(set(self.edge_cells.values()))
        self.start_dt = _dt.datetime.combine(run_date, _dt.time(0)) + _dt.timedelta(hours=sim_start_hour)
        # 예측기(Module 3): 모델 검사(셀·누수·전처리 설정)를 먼저 해서, 맞지 않으면 과거 호출을 만들기 전에 멈춘다
        self.predictor = H3DemandPredictor(self.cells, self.start_dt, model_path)
        self.bucket = self.predictor.bucket
        self.travel_time = travel_time
        self.tick_sec = float(CFG.get("forecast_tick_sec", 300))
        self.horizon = int(CFG.get("forecast_horizon_buckets", 3))
        self.power = float(CFG.get("forecast_alloc_power", 2 / 3))
        self.max_move_sec = float(CFG.get("forecast_max_move_sec", 180))
        self.fraction = float(CFG.get("reposition_fraction", 0.3))
        # 안전장치: 빈 택시가 전체의 이 비율보다 적으면(택시 부족) 재배치하지 않는다.
        # 택시가 모자랄 때 옮기면 그 사이 근처 호출을 놓쳐 오히려 손해(손님 100% 7일 실험에서 확인).
        self.min_idle_ratio = float(CFG.get("forecast_min_idle_ratio", 0.0))
        self._rng = random.Random(f"fetex-forecast|{seed}|{run_date.isoformat()}")

        # ---- 관측 호출 기록: (시각, 칸) + 칸별 호출이 많았던 도로 ----
        from module1_simulation.holiday_utils import is_holiday_date
        from module1_simulation.weather_history import load_hourly_weather
        times, cells, hist_edges = [], [], Counter()

        def add(base, calls, count_edges):
            for c in calls:
                cell = self.edge_cells.get(c.origin)
                if cell is None:
                    continue
                times.append(base + _dt.timedelta(seconds=float(c.t)))
                cells.append(cell)
                if count_edges:
                    hist_edges[c.origin] += 1

        def weather(d):
            w = load_hourly_weather(d, CFG.get("region")) if CFG.get("use_historical_weather", True) else None
            return w["precipitation"] if w else None

        hist_days = 8   # 지난주 같은 칸 피처(1주 전) + 이동평균 여유
        for k in range(hist_days, 0, -1):
            d = run_date - _dt.timedelta(days=k)
            add(_dt.datetime.combine(d, _dt.time(0)),
                demand_model.build_schedule(d, 0, 24, seed, is_holiday=bool(is_holiday_date(d)),
                                            hourly_precip_mm=weather(d)), True)
        add(_dt.datetime.combine(run_date, _dt.time(0)),
            demand_model.build_schedule(run_date, 0, sim_start_hour, seed, is_holiday=is_holiday,
                                        hourly_precip_mm=weather(run_date)), False)
        self.predictor.set_history(pd.DataFrame({"pickup_datetime": times, "h3_index": cells}))
        # 시뮬레이션 호출(외생 스케줄)은 시각순 — 지금까지 나온 것만 꺼내 쓴다
        self._sched = sorted(((float(c.t), self.edge_cells.get(c.origin)) for c in schedule), key=lambda x: x[0])
        self._sched_i = 0
        # 칸별 도착 도로 후보: 지난 8일 호출이 많았던 도로 (호출 수 = 가중치)
        self.cell_edges = defaultdict(list)
        for e, n in sorted(hist_edges.items()):
            if not e.startswith(":"):
                self.cell_edges[self.edge_cells[e]].append((e, n))

        self.last_tick = -1
        self.committed: dict[str, str] = {}   # 이동 중인 택시 -> 목표 칸 (Unity 표시용)
        self.records, self.moves = [], []
        self.stats = Counter()

    # ---- 1) 예측 ----
    def _observe(self, now: float):
        """시뮬레이션 호출 중 지금 시각까지 나온 것만 예측기에 넘긴다 (미래 호출은 보지 않음)."""
        while self._sched_i < len(self._sched) and self._sched[self._sched_i][0] <= now:
            t, cell = self._sched[self._sched_i]
            if cell is not None:
                self.predictor.add_call(self.start_dt + _dt.timedelta(seconds=t), cell)
            self._sched_i += 1

    def predict(self, now: float) -> np.ndarray:
        """(칸 수, 6) 예측 (Module 3 예측기) — 열 k = 진행 중인 칸 t의 k+1칸 뒤 (t+1..t+6)."""
        self._observe(now)
        return self.predictor.predict(self.start_dt + _dt.timedelta(seconds=now))

    # ---- 2), 3) 배치·이동 ----
    def _location(self, vid):
        edge = traci.vehicle.getRoadID(vid)
        if edge and not edge.startswith(":"):
            return edge, traci.vehicle.getLanePosition(vid)
        route, idx = traci.vehicle.getRoute(vid), traci.vehicle.getRouteIndex(vid)
        return (route[idx + 1] if 0 <= idx + 1 < len(route) else ""), 0.0

    def _pick_edges(self, cell: str, k: int) -> list[str]:
        cands = self.cell_edges.get(cell) or [(e, 1) for e, c in sorted(self.edge_cells.items()) if c == cell]
        edges, weights = [e for e, _ in cands], [w for _, w in cands]
        out = []
        for _ in range(k):   # 호출 비율대로 고르되 같은 도로 중복은 피함
            pool = [(e, w) for e, w in zip(edges, weights) if e not in out] or list(zip(edges, weights))
            out.append(self._rng.choices([e for e, _ in pool], weights=[w for _, w in pool])[0])
        return out

    def maintain(self, now: float, idle_taxis=None):
        tick = int(now // self.tick_sec)
        if tick == self.last_tick or now < self.bucket.total_seconds():
            return
        self.last_tick = tick
        try:
            pred = self.predict(now)
        except ValueError as e:
            self.stats["predict_failed"] += 1
            if self.stats["predict_failed"] == 1:
                print(f"[예측 재배치] 예측 실패, 이번 칸은 건너뜀: {e}", flush=True)
            return
        lam = pred[:, :self.horizon].sum(axis=1)   # t+1..t+horizon (진행 중인 칸 다음부터) 예측 호출 합
        idle = sorted(idle_taxis if idle_taxis is not None else traci.vehicle.getTaxiFleet(0))
        loc = {}
        for vid in idle:
            try:
                loc[vid] = self._location(vid)
            except traci.exceptions.TraCIException:
                continue
        try:
            fleet = len(traci.vehicle.getTaxiFleet(-1))
        except traci.exceptions.TraCIException:
            fleet = 0
        self.stats["idle_ratio_sum"] += (len(loc) / fleet) if fleet else 0.0
        self.stats["ticks"] += 1
        cell_of = {vid: self.edge_cells.get(e) for vid, (e, _) in loc.items()}
        self.committed = {v: c for v, c in self.committed.items() if v in loc and cell_of.get(v) != c}
        # 이동 중인 택시는 도착할 칸에 있는 것으로 센다
        supply = Counter(self.committed.get(v, cell_of[v]) for v in loc if cell_of[v] is not None
                         or v in self.committed)
        n = sum(supply.values())

        # 동적 호출비: D = 지금 기다리는 손님 + 다음 갱신까지(surge_horizon_sec, 기본 5분) 들어올 예측 호출,
        # S = 빈 택시. 같은 시간 구간끼리 비교해야 "지금 빈 택시로 곧 올 손님을 감당할 수 있나"가 된다.
        fees, demand = {}, {}
        if self.fee_board is not None:
            waiting = self._waiting_by_cell()
            soon = expected_calls(pred, self.bucket.total_seconds(), self.fee_board.settings["horizon_sec"])
            demand = {c: float(soon[i]) + waiting.get(c, 0) for i, c in enumerate(self.cells)}
            fees = self.fee_board.update(now, demand, supply)

        if self.mode == "surge":
            target = None
        else:
            target = target_allocation(lam, n, self.power)
        desired = dict(zip(self.cells, target)) if target is not None else {}
        for i, c in enumerate(self.cells):
            self.records.append({"time_sec": now, "h3_index": c, "predicted_demand": float(lam[i]),
                                 "available_taxis": int(supply[c]),
                                 "target_taxis": int(desired.get(c, supply[c])),
                                 "surge_multiplier": self.fee_board.multiplier(c) if self.fee_board else 1.0,
                                 "call_fee_won": int(fees.get(c, 0)),
                                 **{f"forecast_{k + 1}": float(pred[i, k]) for k in range(6)}})
        if self.mode == "observe":
            return
        if self.mode == "surge":
            # 기사 인센티브(호출비 x 기사 몫)가 기준 이상인 칸만 택시를 부른다. 필요한 대수 = 호출비가 0원이 되는 대수
            share, min_inc = self.fee_board.settings["driver_share"], self.fee_board.settings["min_incentive"]
            deficit = {c: taxis_to_clear(demand[c], supply[c]) for c in self.cells
                       if fees.get(c, 0) * share >= min_inc}
            deficit = {c: k for c, k in deficit.items() if k > 0}
            # 호출비 0원인 칸에서, 그 칸 수요를 채우고 남는 택시만 보낸다
            surplus = {c: supply[c] - max(math.ceil(demand[c] - 1e-9) - 1, 0) for c in self.cells
                       if fees.get(c, 0) == 0}
            surplus = {c: k for c, k in surplus.items() if k > 0}
        else:
            surplus = {c: supply[c] - desired[c] for c in self.cells if supply[c] > desired[c]}
            deficit = {c: desired[c] - supply[c] for c in self.cells if desired[c] > supply[c]}
        budget = int(len(loc) * self.fraction)
        if fleet and len(loc) < self.min_idle_ratio * fleet:
            self.stats["skipped_low_supply"] += 1
            return
        self._move(now, loc, cell_of, surplus, deficit, budget)

    def _waiting_by_cell(self) -> Counter:
        """지금 차에 안 탄 채 기다리는 손님 수 (칸별)."""
        out = Counter()
        try:
            for pid in traci.person.getIDList():
                if traci.person.getVehicle(pid):
                    continue
                cell = self.edge_cells.get(traci.person.getRoadID(pid))
                if cell is not None:
                    out[cell] += 1
        except traci.exceptions.TraCIException:
            pass
        return out

    def _move(self, now, loc, cell_of, surplus, deficit, budget):
        """남는 칸의 빈 택시를 모자란 칸으로 (도로 이동시간 헝가리안 매칭, max_move_sec 이내, budget 대까지)."""
        if not surplus or not deficit or budget <= 0 or self.travel_time is None:
            return
        movers = [v for v in loc if v not in self.committed and cell_of[v] in surplus]
        # 남는 칸에서는 남는 대수만큼만 후보로 (그 칸 손님 근처에 가장 오래 남을 택시는 그대로 둠)
        by_cell = defaultdict(list)
        for v in movers:
            by_cell[cell_of[v]].append(v)
        movers = [v for c, vs in sorted(by_cell.items()) for v in sorted(vs)[:surplus[c]]]
        slots = [(c, e) for c in sorted(deficit) for e in self._pick_edges(c, deficit[c])]
        if not movers or not slots:
            return
        tt = self.travel_time
        cost = np.array([[tt.time(loc[v][0], loc[v][1], e, 0.0) for (_, e) in slots] for v in movers])
        cost = np.where(cost > self.max_move_sec, UNREACHABLE, cost)
        rows, cols = linear_sum_assignment(cost)
        order = sorted(zip(rows, cols), key=lambda rc: cost[rc[0], rc[1]])
        for r, c in order:
            if budget <= 0 or cost[r, c] >= UNREACHABLE:
                continue
            vid, (cell, edge) = movers[r], slots[c]
            try:
                if traci.vehicle.getStops(vid, 0):     # 이 사이 배차됐으면 건드리지 않음
                    continue
                traci.vehicle.changeTarget(vid, edge)
            except traci.exceptions.TraCIException:
                self.stats["move_failed"] += 1
                continue
            self.committed[vid] = cell
            budget -= 1
            self.stats["moves"] += 1
            self.moves.append({"time_sec": now, "taxi_id": vid, "from_cell": cell_of[vid], "to_cell": cell,
                               "to_edge": edge, "travel_time_sec": float(cost[r, c])})

    def summary(self) -> dict:
        return {"ticks": len(self.records) // max(len(self.cells), 1), "moves": int(self.stats["moves"]),
                "move_failed": int(self.stats["move_failed"]), "predict_failed": int(self.stats["predict_failed"]),
                "skipped_low_supply": int(self.stats["skipped_low_supply"]),
                "mean_idle_ratio": round(self.stats["idle_ratio_sum"] / max(self.stats["ticks"], 1), 3),
                "mode": self.mode, "min_idle_ratio": self.min_idle_ratio, "alloc_power": self.power, "horizon_buckets": self.horizon, "max_move_sec": self.max_move_sec}
