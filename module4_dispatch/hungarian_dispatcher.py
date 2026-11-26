"""
SUMO taxi device의 dispatch-algorithm="traci" 모드에서 실제 배정을 수행하는
헝가리안(최적 할당, scipy linear_sum_assignment) 기반 실시간 배차 매니저.

[왜 필요한가]
기존 dynamic_matching.py의 헝가리안 로직은 mock (id, x, y) 데이터로만 동작해서
시뮬레이션과 연결돼 있지 않았음. 이 매니저는 traci로 "실제" 대기 중인 예약(승객)과
"실제" 빈 택시 좌표를 가져와 같은 헝가리안 알고리즘으로 배정한다 — SUMO 안에서 진짜로 돌아감.

[동작 원리]
SUMO device.taxi.dispatch-algorithm이 "traci"면, 승객 예약이 생겨도 SUMO가
자동으로 택시를 배정하지 않고 traci.vehicle.dispatchTaxi()가 불려주기를 기다린다.
이 매니저가 매 스텝:
  1) traci.person.getTaxiReservations()로 아직 배정 안 된 예약 목록을 가져오고
  2) traci.vehicle.getTaxiFleet(0)으로 완전히 빈(empty) 택시 목록을 가져와
  3) 택시-예약 간 비용행렬을 만들어 linear_sum_assignment로 최적 조합을 구하고
     - cost_mode="network"(권장): 실제 도로망 최단 이동 시간(초) — module4_dispatch/network_cost.py
     - cost_mode="euclidean"(예전): 택시 좌표와 승객 도로 시작점 사이 직선 거리(m)
  4) traci.vehicle.dispatchTaxi(vehID, [reservationID])로 실제 배정한다.

[사용법] 직접 만들지 말고 module4_dispatch.custom_dispatchers.make_dispatcher(algo, sumo_cfg)를 쓴다
(dispatch_cost에 맞는 비용 방식을 고름). 시뮬레이션 루프 안에서:
    dispatcher = make_dispatcher(meta.get("taxi_dispatch_algorithm"), sumo_cfg_path)
    ...
    while step < max_steps:
        traci.simulationStep()
        now = traci.simulation.getTime()
        ...
        if dispatcher:
            dispatcher.maintain(now)
"""

import traci
import numpy as np
from scipy.optimize import linear_sum_assignment

from module4_dispatch.network_cost import UNREACHABLE


class HungarianDispatcher:
    def __init__(self, cost_mode: str = "euclidean", travel_time=None, dist_weight: float = 0.0):
        # travel_time: network_cost.NetworkTravelTime (cost_mode="network"일 때 필요). 없으면 직선 거리로 대체.
        self.cost_mode = cost_mode if (cost_mode != "network" or travel_time is not None) else "euclidean"
        if cost_mode == "network" and travel_time is None:
            print("[경고] 도로망 이동시간 표가 없어 헝가리안 비용을 직선 거리로 대체합니다.")
        self.travel_time = travel_time
        self.dist_weight = float(dist_weight)  # 거리 가중치 (예: 0.02, 0.03, 0.04, 0.05)
        self.cost_stats = {"assignments": 0, "unreachable_pairs_chosen": 0}
        # 이미 배정을 "시도"한 예약 id를 기록해서 같은 예약에 매 스텝 중복 dispatchTaxi 호출하는 것 방지.
        # (SUMO가 배정 후에도 한동안 getTaxiReservations에 남겨둘 수 있어 안전장치로 둠)
        self.dispatched_reservations = set()
        # 이미 택시가 배정된 승객의 person_id 집합. passenger_manager.py가 타임아웃 처리할 때,
        # 이미 예약이 걸린 승객을 traci.person.remove()로 강제 제거하면 SUMO 내부의 택시
        # 예약/스케줄 상태가 깨져서 (Connection closed by SUMO) 크래시로 이어질 수 있음 --
        # 그래서 이 집합에 들어있는 승객만 "물리적 제거는 미루고 논리적으로만 소멸 처리"하도록
        # passenger_manager.py 쪽에서 참조함. 아직 배정 안 된 승객(여기 없는 pid)은 그대로
        # 안전하게 물리적으로 제거해도 됨 -- 대기시간 지표를 불필요하게 왜곡시키지 않기 위함.
        self.dispatched_person_ids = set()
        # dispatchTaxi 실패(연속) 시 그 예약을 포기하고 다음 스텝에 다시 시도할 수 있게
        # 별도로 걸러내진 않음 — SUMO가 상태를 관리하므로 실패해도 다음 스텝에 재시도됨.

    def _get_pending_reservations(self, reservations=None):
        """아직 배정 시도하지 않은 예약만 반환. reservations를 주면 TraCI 조회를 생략."""
        if reservations is None:
            try:
                reservations = traci.person.getTaxiReservations(0)
            except traci.exceptions.TraCIException:
                return []
        return [r for r in reservations if r.id not in self.dispatched_reservations]

    def _get_idle_taxis(self):
        """현재 손님도 없고 픽업 중도 아닌 완전히 빈 택시 목록. (0 = TAXI_EMPTY)"""
        try:
            return list(traci.vehicle.getTaxiFleet(0))
        except traci.exceptions.TraCIException:
            return []

    def _edge_start_position(self, edge_id: str):
        """edge의 시작 좌표를 그 예약(승객)의 근사 위치로 사용."""
        try:
            shape = traci.lane.getShape(edge_id + "_0")
            return shape[0]
        except (traci.exceptions.TraCIException, IndexError):
            return (0.0, 0.0)

    def _euclidean_cost(self, idle_taxis, reservations):
        taxi_positions = []
        for vid in idle_taxis:
            try:
                taxi_positions.append(traci.vehicle.getPosition(vid))
            except traci.exceptions.TraCIException:
                taxi_positions.append((0.0, 0.0))
        pax_positions = [self._edge_start_position(r.fromEdge) for r in reservations]
        cost = np.zeros((len(idle_taxis), len(reservations)))
        for i, tp in enumerate(taxi_positions):
            for j, pp in enumerate(pax_positions):
                cost[i, j] = np.hypot(tp[0] - pp[0], tp[1] - pp[1])
        return cost

    def _taxi_location(self, vid):
        """(도로, 도로 위 위치). 교차로 내부(':'로 시작)에 있으면 경로상 다음 일반 도로의 시작으로 본다."""
        edge = traci.vehicle.getRoadID(vid)
        if edge and not edge.startswith(":"):
            return edge, traci.vehicle.getLanePosition(vid)
        route, idx = traci.vehicle.getRoute(vid), traci.vehicle.getRouteIndex(vid)
        if 0 <= idx + 1 < len(route):
            return route[idx + 1], 0.0
        return (route[idx] if 0 <= idx < len(route) else ""), 0.0

    def _network_cost(self, idle_taxis, reservations):
        """도로망 최단 이동 시간(초). 갈 수 없으면 UNREACHABLE + 직선 거리(그래도 가장 가까운 쪽이 뽑히게)."""
        tt = self.travel_time
        locs = []
        for vid in idle_taxis:
            try:
                locs.append(self._taxi_location(vid))
            except traci.exceptions.TraCIException:
                locs.append(("", 0.0))
        cost = np.zeros((len(idle_taxis), len(reservations)))
        euclid = None
        for j, r in enumerate(reservations):
            pb = r.departPos if r.departPos is not None and r.departPos >= 0 else 0.0
            for i, (edge, pa) in enumerate(locs):
                cost[i, j] = tt.time(edge, pa, r.fromEdge, pb)
        if (cost >= UNREACHABLE).any():
            euclid = self._euclidean_cost(idle_taxis, reservations)
            cost = np.where(cost >= UNREACHABLE, UNREACHABLE + euclid, cost)

        # [신규] 택시별 이동 거리(공차 주행 거리, m) 가중치 반영: Cost = Time(s) + dist_weight * Dist(m)
        if self.dist_weight != 0.0:
            if euclid is None:
                euclid = self._euclidean_cost(idle_taxis, reservations)
            cost = cost + (self.dist_weight * euclid)

        return cost

    def maintain(self, now_seconds: float = 0, idle_taxis=None, reservations=None):
        """idle_taxis / reservations: 메인 루프가 스텝당 한 번 가져온 값 (None이면 직접 조회).
        반환: 이번 스텝에 dispatchTaxi 성공한 택시 id 리스트 (메인 루프가 idle 목록에서 빼는 데 사용)."""
        dispatched_now = []
        try:
            reservations = self._get_pending_reservations(reservations)
            idle_taxis = list(idle_taxis) if idle_taxis is not None else self._get_idle_taxis()

            # 후보가 아예 없을 때 왜 안 잡히는지 확인하기 위한 임시 프린트
            # if not reservations or not idle_taxis:
            #     return

            if not reservations or not idle_taxis:
                return dispatched_now

            cost = (self._network_cost(idle_taxis, reservations) if self.cost_mode == "network"
                    else self._euclidean_cost(idle_taxis, reservations))

            taxi_idx, pax_idx = linear_sum_assignment(cost)

            for t_i, p_i in zip(taxi_idx, pax_idx):
                vid = idle_taxis[t_i]
                res = reservations[p_i]
                try:
                    traci.vehicle.dispatchTaxi(vid, [res.id])
                    self.cost_stats["assignments"] += 1
                    if self.cost_mode == "network" and cost[t_i, p_i] >= UNREACHABLE:
                        self.cost_stats["unreachable_pairs_chosen"] += 1
                    self.dispatched_reservations.add(res.id)
                    dispatched_now.append(vid)
                    self.dispatched_person_ids.update(res.persons)  # 이 승객들은 이제 예약이 걸린 상태
                    # 👈 배정 성공할 때마다 콘솔에 찍히는 디버그 로그 추가
                    #print(f"[Hungarian] {vid} -> 예약 {res.id} 배정 (누적 {len(self.dispatched_reservations)}건)")
                except traci.exceptions.TraCIException:
                    continue
        except traci.exceptions.TraCIException:
            pass
        return dispatched_now


def build_hungarian_dispatcher(sumo_cfg_path=None, dist_weight: float = 0.0):
    """config.json의 dispatch_cost("network" / "euclidean")에 맞춰 헝가리안 배차기를 만든다.
    network이면 sumocfg의 도로망으로 이동시간 표를 만든다(같은 도로망이면 프로세스 안에서 재사용).
    dist_weight > 0 이면 이동시간에 (거리(m) * dist_weight) 가중치를 추가한다."""
    from config_loader import CFG
    from module4_dispatch.network_cost import NetworkTravelTime, net_file_from_sumocfg
    mode = str(CFG.get("dispatch_cost", "euclidean")).lower()
    travel_time = None
    if mode == "network":
        net_file = net_file_from_sumocfg(sumo_cfg_path) if sumo_cfg_path else None
        if net_file and __import__("os").path.exists(net_file):
            travel_time = NetworkTravelTime.cached(net_file)
    return HungarianDispatcher(mode, travel_time, dist_weight=dist_weight)


class AdaptiveHungarianDispatcher(HungarianDispatcher):
    """
    실시간 수요/공급비(대기 승객 수 / 빈 택시 수)에 따라 거리 가중치(dist_weight)를 동적으로 조절하는 헝가리안 배차기.
    - 공급 여유 (R <= 1.0): dist_weight = 0.06 (공차 주행 최소화 & 회전율 극대화)
    - 일반 도심 (R ~ 3.5): dist_weight = 0.04 (안정적 밸런스)
    - 초과밀 피크 (R >= 6.0): dist_weight = 0.02 (택시 갇힘 방지 및 광역 수송)
    """
    def __init__(self, cost_mode: str = "euclidean", travel_time=None, min_w: float = 0.02, max_w: float = 0.06):
        super().__init__(cost_mode, travel_time, dist_weight=max_w)
        self.min_w = float(min_w)
        self.max_w = float(max_w)

    def maintain(self, now_seconds: float = 0, idle_taxis=None, reservations=None):
        pending = self._get_pending_reservations(reservations)
        idles = list(idle_taxis) if idle_taxis is not None else self._get_idle_taxis()

        n_pax = len(pending)
        n_taxi = len(idles)

        if n_pax > 0 and n_taxi > 0:
            # 수요/공급비 R (현재 대기 승객 수 / 가용 빈 택시 수)
            r = n_pax / max(n_taxi, 1)
            # R <= 1.0 이면 0.06, R >= 6.0 이면 0.02 사이를 부드럽게 선형 보간
            if r <= 1.0:
                self.dist_weight = self.max_w
            elif r >= 6.0:
                self.dist_weight = self.min_w
            else:
                ratio = (r - 1.0) / (6.0 - 1.0)
                self.dist_weight = round(self.max_w - ratio * (self.max_w - self.min_w), 4)

        return super().maintain(now_seconds, idle_taxis=idles, reservations=pending)


def build_adaptive_hungarian_dispatcher(sumo_cfg_path=None, min_w: float = 0.02, max_w: float = 0.06):
    from config_loader import CFG
    from module4_dispatch.network_cost import NetworkTravelTime, net_file_from_sumocfg
    mode = str(CFG.get("dispatch_cost", "euclidean")).lower()
    travel_time = None
    if mode == "network":
        net_file = net_file_from_sumocfg(sumo_cfg_path) if sumo_cfg_path else None
        if net_file and __import__("os").path.exists(net_file):
            travel_time = NetworkTravelTime.cached(net_file)
    return AdaptiveHungarianDispatcher(mode, travel_time, min_w=min_w, max_w=max_w)


class AdaptiveHungarianDispatcherV2(HungarianDispatcher):
    """
    [V2] 사용자 제안 수식 기반 수요 연동 헝가리안 배차기.
    - 기본(한산/주말/4,500명 이하): dist_weight = 0.04 고정 (주말 1위 d04 성능 100% 보장)
    - 수요 증가(6,000명 부근): dist_weight = 0.03~0.035로 점진적 감소
    - 초과밀(10,000명 이상): dist_weight = 0.02 도달 (초과밀 1위 d02 성능 확보)
    """
    def __init__(self, cost_mode: str = "euclidean", travel_time=None, base_w: float = 0.04, min_w: float = 0.02):
        super().__init__(cost_mode, travel_time, dist_weight=base_w)
        self.base_w = float(base_w)  # 0.04
        self.min_w = float(min_w)    # 0.02

    def maintain(self, now_seconds: float = 0, idle_taxis=None, reservations=None):
        pending = self._get_pending_reservations(reservations)
        idles = list(idle_taxis) if idle_taxis is not None else self._get_idle_taxis()

        n_pax = len(pending)
        n_taxi = len(idles)

        if n_pax > 0 and n_taxi > 0:
            # 수요/공급비 R (현재 대기 승객 수 / 가용 빈 택시 수)
            # R <= 1.0 (평시/주말 3500~4500명): 0.04 유지
            # R >= 4.0 (과밀 10,000명 이상): 0.02 도달
            r = n_pax / max(n_taxi, 1)
            if r <= 1.0:
                self.dist_weight = self.base_w  # 0.04 고정
            elif r >= 4.0:
                self.dist_weight = self.min_w   # 0.02 고정
            else:
                # 1.0 < r < 4.0 구간: 0.04 -> 0.02 선형 보간 (r=2.5 부근 6000명대에서 약 0.03)
                ratio = (r - 1.0) / (4.0 - 1.0)
                self.dist_weight = round(self.base_w - ratio * (self.base_w - self.min_w), 4)
        else:
            self.dist_weight = self.base_w

        return super().maintain(now_seconds, idle_taxis=idles, reservations=pending)


def build_adaptive_hungarian2_dispatcher(sumo_cfg_path=None, base_w: float = 0.04, min_w: float = 0.02):
    from config_loader import CFG
    from module4_dispatch.network_cost import NetworkTravelTime, net_file_from_sumocfg
    mode = str(CFG.get("dispatch_cost", "euclidean")).lower()
    travel_time = None
    if mode == "network":
        net_file = net_file_from_sumocfg(sumo_cfg_path) if sumo_cfg_path else None
        if net_file and __import__("os").path.exists(net_file):
            travel_time = NetworkTravelTime.cached(net_file)
    return AdaptiveHungarianDispatcherV2(mode, travel_time, base_w=base_w, min_w=min_w)


