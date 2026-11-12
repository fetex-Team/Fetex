"""[Module 4] 동적 호출비(Surge) — 미터 요금은 그대로 두고, 칸별 수급에 따라 호출비만 더한다.

한국 택시 미터 요금(기본·거리·시간)은 지자체가 정하는 규제 요금이라 바꾸지 않는다.
카카오 T 블루처럼 미터 요금 위에 붙는 '호출비'를 H3 칸별·5분마다 수급으로 정한다.

계산 (칸 c, 5분마다 — forecast_repositioner가 부른다):
    D_c = 지금 그 칸에서 기다리는 손님 수 + 다음 5분(갱신 주기) 예측 호출 수(Module 3 demand_v2: 10분 칸 t+1 예측의 절반)
    S_c = 지금 그 칸의 빈 택시 수 (그 칸으로 이동 중인 택시 포함)
    r_c = D_c / (S_c + 1)                       # +1: 0으로 나누지 않고, 작은 칸에서 값이 튀지 않게
    호출비_c = 단위(1,000원) x (r_c - 1)          # r <= 1(택시 충분)이면 0원
             → 단위(500원)로 내림, 0 ~ 상한(3,000원)

손님 요금 = 미터 요금(그 칸의 TIMS 실측 평균 요금) + 호출한 시점의 그 칸 호출비
기사 인센티브 = 호출비 x 기사 몫(80%), 나머지는 플랫폼 몫
탑승한 손님만 낸다 (시간 초과로 못 탄 손님은 0원).

설정(config.json): surge_enabled, surge_fee_unit_won(1000), surge_fee_step_won(500), surge_fee_max_won(3000),
surge_driver_share(0.8), surge_min_incentive_won(500, surge 전략의 이동 기준), surge_horizon_sec(300)
"""
from __future__ import annotations

import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config_loader import CFG


def fee_settings(cfg=None) -> dict:
    c = CFG if cfg is None else cfg
    return {
        "unit": float(c.get("surge_fee_unit_won", 1000)),
        "step": float(c.get("surge_fee_step_won", 500)),
        "max": float(c.get("surge_fee_max_won", 3000)),
        "driver_share": float(c.get("surge_driver_share", 0.8)),
        "min_incentive": float(c.get("surge_min_incentive_won", 500)),
        "horizon_sec": float(c.get("surge_horizon_sec", 300)),
    }


def imbalance(demand: float, idle: float) -> float:
    """수급 비율 r = D / (S + 1)."""
    return max(float(demand), 0.0) / (max(float(idle), 0.0) + 1.0)


def call_fee(demand: float, idle: float, settings: dict | None = None) -> int:
    """칸 하나의 호출비(원). r <= 1이면 0원, 넘는 만큼 단위 x (r-1), step 단위로 내림, 상한 max."""
    s = settings or fee_settings()
    raw = s["unit"] * (imbalance(demand, idle) - 1.0)
    if raw <= 0:
        return 0
    stepped = math.floor(raw / s["step"]) * s["step"] if s["step"] > 0 else raw
    return int(min(stepped, s["max"]))


def taxis_to_clear(demand: float, idle: float) -> int:
    """호출비가 0원이 되려면(r <= 1) 더 필요한 빈 택시 수: S + 1 >= D → ceil(D) - 1 - S."""
    return max(0, math.ceil(max(float(demand), 0.0) - 1e-9) - 1 - int(idle))


class CallFeeBoard:
    """칸별 호출비 게시판: 5분마다 update()로 갱신, 손님이 호출할 때 quote()로 그 칸 호출비를 확정한다."""

    def __init__(self, cells, mean_fare_won: float | None = None, settings: dict | None = None):
        self.cells = list(cells)
        self.settings = settings or fee_settings()
        self.mean_fare_won = mean_fare_won
        self.fee = {c: 0 for c in self.cells}
        self.demand = {c: 0.0 for c in self.cells}
        self.records = []           # (시각, 칸, D, S, r, 호출비) — 결과 지도·Unity용
        self.fee_of_pid = {}        # 손님 -> 호출 시점 호출비
        self.cell_of_pid = {}

    def update(self, now: float, demand_by_cell: dict, idle_by_cell: dict) -> dict:
        for c in self.cells:
            d, s = float(demand_by_cell.get(c, 0.0)), int(idle_by_cell.get(c, 0))
            self.demand[c] = d
            self.fee[c] = call_fee(d, s, self.settings)
            self.records.append({"time_sec": now, "h3_index": c, "demand": round(d, 3), "idle_taxis": s,
                                 "imbalance": round(imbalance(d, s), 3), "call_fee_won": self.fee[c]})
        return dict(self.fee)

    def quote(self, pid: str, cell: str | None) -> int:
        """손님이 호출한 순간의 호출비를 확정해 기록 (같은 손님은 한 번만)."""
        if pid in self.fee_of_pid:
            return self.fee_of_pid[pid]
        fee = int(self.fee.get(cell, 0)) if cell is not None else 0
        self.fee_of_pid[pid] = fee
        self.cell_of_pid[pid] = cell
        return fee

    def multiplier(self, cell: str) -> float:
        """미터 요금 대비 배수 (예전 surge_multiplier 칸 호환: 1 + 호출비 / 평균 미터 요금)."""
        if not self.mean_fare_won:
            return 1.0
        return round(1.0 + self.fee.get(cell, 0) / self.mean_fare_won, 3)

    def summary(self, picked_pids) -> dict:
        """탑승한 손님 기준 호출비 집계."""
        picked = [p for p in picked_pids if p in self.fee_of_pid]
        fees = [self.fee_of_pid[p] for p in picked]
        n_quoted = len(self.fee_of_pid)
        total = int(sum(fees))
        ticks = len(self.records) // max(len(self.cells), 1)
        surged_cell_ticks = sum(1 for r in self.records if r["call_fee_won"] > 0)
        meter = (self.mean_fare_won or 0) * len(picked)
        return {
            "n_calls_quoted": n_quoted,
            "n_paid": len(picked),
            "n_paid_with_fee": sum(1 for f in fees if f > 0),
            "share_paid_with_fee": round(sum(1 for f in fees if f > 0) / len(picked), 4) if picked else None,
            "mean_fee_won": round(total / len(picked), 1) if picked else None,
            "max_fee_won": max(fees) if fees else 0,
            "call_fee_revenue_won": total,
            "driver_incentive_won": int(round(total * self.settings["driver_share"])),
            "platform_share_won": total - int(round(total * self.settings["driver_share"])),
            "meter_revenue_won": int(round(meter)) if self.mean_fare_won else None,
            "total_revenue_won": int(round(meter + total)) if self.mean_fare_won else None,
            "ticks": ticks,
            "share_surged_cell_ticks": round(surged_cell_ticks / len(self.records), 4) if self.records else None,
            "settings": {k: self.settings[k] for k in ("unit", "step", "max", "driver_share", "min_incentive",
                                                        "horizon_sec") if k in self.settings},
        }
