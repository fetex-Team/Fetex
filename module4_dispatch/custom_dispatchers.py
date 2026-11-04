import traci
import math
from module4_dispatch.reservation_guard import ReservationGuard

class CustomGreedyDispatcher:
    def __init__(self):
        self.guard = ReservationGuard()
        self.dispatched_person_ids = set()

    # [핵심 수정 1] 메인 루프가 던지는 idle_taxis 인자를 유연하게 받도록 수정
    def maintain(self, now_seconds: float, idle_taxis=None, reservations=None, **kwargs):
        self.guard.sync(now_seconds)
        dispatched_now = []  # 이번 스텝에 배차 성공한 택시 목록

        try:
            empty_taxis = list(traci.vehicle.getTaxiFleet(0))
        except traci.exceptions.TraCIException:
            return dispatched_now

        if not empty_taxis:
            return dispatched_now

        waiting_persons = []
        try:
            t_reservations = traci.person.getTaxiReservations(1)
            for r in t_reservations:
                for pid in r.persons:
                    if pid not in self.dispatched_person_ids:
                        try:
                            pos = traci.person.getPosition(pid)
                            waiting_persons.append({'pid': pid, 'res': r, 'pos': pos})
                        except traci.exceptions.TraCIException:
                            continue
        except traci.exceptions.TraCIException:
            return dispatched_now

        if not waiting_persons:
            return dispatched_now

        for vid in empty_taxis:
            if not waiting_persons:
                break

            try:
                taxi_pos = traci.vehicle.getPosition(vid)
            except traci.exceptions.TraCIException:
                continue
            
            closest_idx = -1
            min_dist = float('inf')
            
            for i, p in enumerate(waiting_persons):
                dx = taxi_pos[0] - p['pos'][0]
                dy = taxi_pos[1] - p['pos'][1]
                dist = math.hypot(dx, dy)
                
                if dist < min_dist:
                    min_dist = dist
                    closest_idx = i
                    
            if closest_idx != -1:
                target_person = waiting_persons.pop(closest_idx)
                
                success = self.guard.dispatch(vid, target_person['res'])
                if success:
                    self.dispatched_person_ids.add(target_person['pid'])
                    dispatched_now.append(vid)  # 성공한 택시 기록

        return dispatched_now  # [핵심 수정 2] 메인 루프에게 리스트 반환


class CustomRouteExtensionDispatcher:
    def __init__(self):
        self.guard = ReservationGuard()
        self.dispatched_person_ids = set()

    # [핵심 수정 1] idle_taxis 인자 수용
    def maintain(self, now_seconds: float, idle_taxis=None, reservations=None, **kwargs):
        self.guard.sync(now_seconds)
        dispatched_now = []

        try:
            empty_taxis = list(traci.vehicle.getTaxiFleet(0))
        except traci.exceptions.TraCIException:
            return dispatched_now

        if not empty_taxis:
            return dispatched_now

        waiting_persons = []
        try:
            t_reservations = traci.person.getTaxiReservations(1)
            for r in t_reservations:
                for pid in r.persons:
                    if pid not in self.dispatched_person_ids:
                        try:
                            edge = traci.person.getRoadID(pid)
                            pos = traci.person.getPosition(pid)
                            waiting_persons.append({'pid': pid, 'res': r, 'pos': pos, 'edge': edge})
                        except traci.exceptions.TraCIException:
                            continue
        except traci.exceptions.TraCIException:
            return dispatched_now

        if not waiting_persons:
            return dispatched_now

        for vid in empty_taxis:
            if not waiting_persons:
                break

            try:
                taxi_pos = traci.vehicle.getPosition(vid)
                taxi_edge = traci.vehicle.getRoadID(vid)
            except traci.exceptions.TraCIException:
                continue
            
            candidates = []
            for i, p in enumerate(waiting_persons):
                dist = math.hypot(taxi_pos[0] - p['pos'][0], taxi_pos[1] - p['pos'][1])
                candidates.append((dist, i, p))
            
            candidates.sort(key=lambda x: x[0])
            top_candidates = candidates[:5]
            
            best_idx = -1
            min_route_time = float('inf')

            for _, orig_idx, p in top_candidates:
                if taxi_edge.startswith(":") or p['edge'].startswith(":"):
                    continue
                try:
                    route = traci.simulation.findRoute(taxi_edge, p['edge'])
                    if route and route.travelTime < min_route_time:
                        min_route_time = route.travelTime
                        best_idx = orig_idx
                except traci.exceptions.TraCIException:
                    continue
            
            if best_idx == -1:
                best_idx = top_candidates[0][1]

            if best_idx != -1:
                target_person = waiting_persons.pop(best_idx)
                
                success = self.guard.dispatch(vid, target_person['res'])
                if success:
                    self.dispatched_person_ids.add(target_person['pid'])
                    dispatched_now.append(vid)

        return dispatched_now  # [핵심 수정 2] 리스트 반환


# ----------------------------------------------------------------------------
# 팩토리 + SUMO 설정 헬퍼
# ----------------------------------------------------------------------------
CUSTOM_ALGOS = ("hungarian", "custom_greedy", "custom_routeExtension")

def make_dispatcher(algo: str, sumo_cfg_path: str = None):
    """taxi_dispatch_algorithm 값 -> 파이썬 배차기 (SUMO 내장 알고리즘이면 None).

    GUI 실행(run_simulation)·측정(measure_wait_time)·RL 학습(rl_env)이 모두 이 함수 하나로 배차기를 만든다.
    build_env는 CUSTOM_ALGOS일 때 SUMO dispatch-algorithm을 "traci"로 두므로, 여기서 배차기를 만들지 않으면
    아무도 택시를 배정하지 않는다. hungarian은 config.json의 dispatch_cost(network/euclidean)를 따르며,
    network이면 sumo_cfg_path의 도로망으로 이동시간 표를 만든다.
    """
    if algo == "custom_greedy":
        return CustomGreedyDispatcher()
    if algo == "custom_routeExtension":
        return CustomRouteExtensionDispatcher()
    if algo == "hungarian":
        from module4_dispatch.hungarian_dispatcher import build_hungarian_dispatcher
        return build_hungarian_dispatcher(sumo_cfg_path)
    return None