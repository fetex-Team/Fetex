"""
Adaptive Hungarian v3 (완성형)

기존 v1/v2는 "순간 R(대기 승객/빈 택시)"로 dist_weight만 바꿨다.
v3는 아래 3가지를 추가한다.

1) R을 EMA로 평활화 -> 가중치가 스텝마다 튀지 않음
2) 대기시간 aging 보너스 -> 택시가 부족할 때(승객 > 택시) 오래 기다린 승객이 먼저 뽑힘
   (linear_sum_assignment는 택시 수만큼만 승객을 고르므로, 안 보정하면 가까운 승객만 계속 뽑혀
    먼 승객이 타임아웃으로 소멸함. 승객 <= 택시일 땐 모든 승객이 배정되므로 효과 없음)
3) 가중치/배정 통계 수집 -> A/B 결과 분석용 (summary())

사용: module4_dispatch/adaptive_hungarian3.py 로 저장 후 custom_dispatchers.make_dispatcher에서 호출.
"""

import traci

from module4_dispatch.hungarian_dispatcher import HungarianDispatcher


class AdaptiveHungarianDispatcherV3(HungarianDispatcher):
    def __init__(
        self,
        cost_mode: str = "euclidean",
        travel_time=None,
        base_w: float = 0.04,    # 한산/휴일 (d04가 휴일 1위)
        min_w: float = 0.02,     # 초과밀 (d02/d04가 혼잡 평일 상위)
        r_lo: float = 1.0,       # R <= r_lo -> base_w
        r_hi: float = 4.0,       # R >= r_hi -> min_w
        ema_alpha: float = 0.05,  # 작을수록 느리게 반응 (1초 스텝이면 약 20초)
        aging_w: float = 0.3,    # 대기 1초당 비용 감소량 (비용 단위: 초 + dist_weight*m)
        aging_cap_s: float = 300.0,  # aging에 반영하는 최대 대기시간(초)
    ):
        super().__init__(cost_mode, travel_time, dist_weight=base_w)
        self.base_w = float(base_w)
        self.min_w = float(min_w)
        self.r_lo = float(r_lo)
        self.r_hi = float(r_hi)
        self.ema_alpha = float(ema_alpha)
        self.aging_w = float(aging_w)
        self.aging_cap_s = float(aging_cap_s)

        self._r_ema = 0.0
        self._now = 0.0
        self._depth = 0
        self._first_seen = {}  # reservation id -> 처음 본 시각 (reservationTime 없을 때 대체)
        self._stats = {"calls": 0, "w_sum": 0.0, "w_min": float("inf"), "w_max": 0.0, "aged_calls": 0}

    # ------------------------------------------------------------------
    # 가중치 결정
    # ------------------------------------------------------------------
    def _weight_from_r(self, r: float) -> float:
        if r <= self.r_lo:
            return self.base_w
        if r >= self.r_hi:
            return self.min_w
        ratio = (r - self.r_lo) / (self.r_hi - self.r_lo)
        return round(self.base_w - ratio * (self.base_w - self.min_w), 4)

    def _update_weight(self, n_pax: int, n_taxi: int):
        if n_pax <= 0:
            r = 0.0
        else:
            r = min(n_pax / max(n_taxi, 1), self.r_hi * 2.0)  # 빈 택시 0일 때 폭주 방지
        self._r_ema += self.ema_alpha * (r - self._r_ema)
        self.dist_weight = self._weight_from_r(self._r_ema)

        st = self._stats
        st["calls"] += 1
        st["w_sum"] += self.dist_weight
        st["w_min"] = min(st["w_min"], self.dist_weight)
        st["w_max"] = max(st["w_max"], self.dist_weight)

    # ------------------------------------------------------------------
    # aging (대기시간 보정)
    # ------------------------------------------------------------------
    def _wait_seconds(self, res) -> float:
        t0 = getattr(res, "reservationTime", None)
        if t0 is None or t0 < 0 or t0 > self._now:
            t0 = self._first_seen.setdefault(res.id, self._now)
        return max(0.0, self._now - float(t0))

    def _apply_aging(self, cost, reservations):
        if self.aging_w <= 0.0:
            return cost
        # 승객 <= 택시면 모든 승객이 배정되므로 열 상수 이동은 결과에 영향 없음 -> 생략
        if cost.shape[1] <= cost.shape[0]:
            return cost
        for j, res in enumerate(reservations):
            wait = min(self._wait_seconds(res), self.aging_cap_s)
            cost[:, j] -= self.aging_w * wait
        self._stats["aged_calls"] += 1
        return cost

    def _with_aging(self, fn, idle_taxis, reservations):
        # _network_cost 내부에서 _euclidean_cost를 다시 부르므로, 최상위 호출에서만 aging 적용
        self._depth += 1
        try:
            cost = fn(idle_taxis, reservations)
        finally:
            self._depth -= 1
        if self._depth == 0:
            cost = self._apply_aging(cost, reservations)
        return cost

    def _network_cost(self, idle_taxis, reservations):
        return self._with_aging(super()._network_cost, idle_taxis, reservations)

    def _euclidean_cost(self, idle_taxis, reservations):
        return self._with_aging(super()._euclidean_cost, idle_taxis, reservations)

    # ------------------------------------------------------------------
    def maintain(self, now_seconds: float = 0, idle_taxis=None, reservations=None):
        self._now = float(now_seconds)
        pending = self._get_pending_reservations(reservations)
        idles = list(idle_taxis) if idle_taxis is not None else self._get_idle_taxis()

        self._update_weight(len(pending), len(idles))

        dispatched = super().maintain(now_seconds, idle_taxis=idles, reservations=pending)

        # 배정 끝난 예약은 first_seen에서 정리 (메모리 누수 방지)
        for rid in self.dispatched_reservations:
            self._first_seen.pop(rid, None)
        return dispatched

    def summary(self) -> dict:
        st = self._stats
        n = max(st["calls"], 1)
        return {
            "calls": st["calls"],
            "avg_dist_weight": round(st["w_sum"] / n, 4),
            "min_dist_weight": None if st["w_min"] == float("inf") else st["w_min"],
            "max_dist_weight": st["w_max"],
            "aging_applied_calls": st["aged_calls"],
            "assignments": self.cost_stats["assignments"],
        }


def build_adaptive_hungarian3_dispatcher(sumo_cfg_path=None, **kwargs):
    from config_loader import CFG
    from module4_dispatch.network_cost import NetworkTravelTime, net_file_from_sumocfg
    import os

    mode = str(CFG.get("dispatch_cost", "euclidean")).lower()
    travel_time = None
    if mode == "network":
        net_file = net_file_from_sumocfg(sumo_cfg_path) if sumo_cfg_path else None
        if net_file and os.path.exists(net_file):
            travel_time = NetworkTravelTime.cached(net_file)
    return AdaptiveHungarianDispatcherV3(mode, travel_time, **kwargs)
