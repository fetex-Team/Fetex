"""실제 도로 이동시간으로 미배정 예약과 빈 택시를 매칭한다."""
import numpy as np
from scipy.optimize import linear_sum_assignment
import traci


class HungarianDispatcher:
    def __init__(self):
        self.dispatched_reservations = set()

    def maintain(self, now_seconds=0):
        reservations = traci.person.getTaxiReservations(0)
        active = {r.id for r in reservations}
        self.dispatched_reservations.intersection_update(active)
        reservations = [r for r in reservations if r.state in (1, 2) and r.id not in self.dispatched_reservations]
        taxis = sorted(traci.vehicle.getTaxiFleet(0))
        if not taxis or not reservations:
            return
        # ponytail: 소규모 fleet용 전체 쌍 경로 조회. 대규모 실험에서는 후보 반경으로 축소한다.
        unreachable = 1e12
        cost = np.full((len(taxis), len(reservations)), unreachable)
        for i, taxi in enumerate(taxis):
            edge = traci.vehicle.getRoadID(taxi)
            if not edge or edge.startswith(':'):
                continue
            for j, reservation in enumerate(reservations):
                try:
                    route = traci.simulation.findRoute(edge, reservation.fromEdge, vType=traci.vehicle.getTypeID(taxi),
                        depart=now_seconds, departPos=traci.vehicle.getLanePosition(taxi), arrivalPos=reservation.departPos)
                    if route.edges:
                        cost[i, j] = route.travelTime
                except traci.exceptions.TraCIException:
                    continue
        taxi_idx, pax_idx = linear_sum_assignment(cost)
        for i, j in zip(taxi_idx, pax_idx):
            if cost[i, j] >= unreachable:
                continue
            reservation = reservations[j]
            try:
                traci.vehicle.dispatchTaxi(taxis[i], [reservation.id])
            except traci.exceptions.TraCIException:
                continue
            self.dispatched_reservations.add(reservation.id)
