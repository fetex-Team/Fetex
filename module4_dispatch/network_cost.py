"""
도로망 기준 이동 시간 — 헝가리안 배차의 비용으로 쓴다.

왜 필요한가
- 기존 비용은 택시와 승객 도로 시작점 사이의 **직선 거리**였다. 강남역 일대는 일방통행·유턴 제한·좌회전 금지가 많아서
  직선으로 가까운 택시가 실제로는 크게 돌아가야 하는 경우가 많다.
  (무작위 표본 300건: 직선 최근접 택시가 도로 최단시간 택시와 다른 경우 40%, 그때 평균 +17초 — 신호 대기 제외)
- 그래서 실제 도로망(차선 연결·회전 허용, vClass=taxi)에서의 최단 이동 시간으로 비용을 만든다.

방법
- 시작할 때 한 번: 일반 도로(e) 382개 각각에서 Dijkstra로 "e의 끝 → 다른 도로 b의 시작" 최단 시간 D[e][b]를 계산(수십 ms).
  간선 비용 = 교차로 내부 연결 차선(via lane) 통과 시간 + 다음 도로 전체 통과 시간, 속도는 제한속도(자유 흐름).
- 배차 때: 택시(도로 a, 위치 pa) → 승객(도로 b, 위치 pb) 시간 =
    같은 도로이고 pb ≥ pa:  (pb − pa) / v_a
    그 외:                 (len_a − pa) / v_a + D[a][b] + pb / v_b
- 갈 수 없는 조합은 UNREACHABLE(1e6초)로 두어 다른 택시가 우선 배정되게 한다.

한계
- 신호 대기·정체는 넣지 않았다(자유 흐름 시간). 상대 비교(어느 택시가 더 빨리 가나)에는 충분하고,
  정체 반영이 필요하면 traci.edge.getTraveltime으로 주기적으로 갱신하는 확장이 가능하다.
"""
from __future__ import annotations

import heapq
import os
import re

import numpy as np

UNREACHABLE = 1e6
_CACHE: dict[tuple[str, float, str], "NetworkTravelTime"] = {}


def net_file_from_sumocfg(sumocfg_path: str) -> str | None:
    """simulation.sumocfg의 net-file 경로(상대경로면 cfg 폴더 기준)."""
    try:
        with open(sumocfg_path, "r", encoding="utf-8") as f:
            text = f.read()
    except OSError:
        return None
    m = re.search(r'<net-file\s+value="([^"]+)"', text)
    if not m:
        return None
    path = m.group(1)
    return path if os.path.isabs(path) else os.path.join(os.path.dirname(os.path.abspath(sumocfg_path)), path)


class NetworkTravelTime:
    def __init__(self, net_file: str, vclass: str = "taxi"):
        import sumolib
        net = sumolib.net.readNet(net_file, withInternal=True)
        edges = [e for e in net.getEdges() if e.getFunction() != "internal"]
        self.index = {e.getID(): i for i, e in enumerate(edges)}
        self.length = np.array([e.getLength() for e in edges])
        self.speed = np.array([max(e.getSpeed(), 0.1) for e in edges])
        n = len(edges)
        succ = [[] for _ in range(n)]          # (다음 도로 index, 교차로 내부 통과 시간)
        for i, e in enumerate(edges):
            for to, conns in e.getAllowedOutgoing(vclass).items():
                j = self.index.get(to.getID())
                if j is None:
                    continue
                via = 0.0
                via_id = conns[0].getViaLaneID() if conns else ""
                if via_id:
                    lane = net.getLane(via_id)
                    via = lane.getLength() / max(lane.getSpeed(), 0.1)
                succ[i].append((j, via))
        self.D = np.full((n, n), UNREACHABLE)
        full = self.length / self.speed
        for s in range(n):                      # D[s][b] = s의 끝에서 b의 시작까지
            dist = self.D[s]
            heap = [(via, j) for j, via in succ[s]]
            for via, j in heap:
                dist[j] = min(dist[j], via)
            heapq.heapify(heap)
            while heap:
                d, u = heapq.heappop(heap)
                if d > dist[u]:
                    continue
                du = d + full[u]
                for v, via in succ[u]:
                    nd = du + via
                    if nd < dist[v]:
                        dist[v] = nd
                        heapq.heappush(heap, (nd, v))
        self.n_edges = n

    @classmethod
    def cached(cls, net_file: str, vclass: str = "taxi") -> "NetworkTravelTime":
        key = (os.path.abspath(net_file), os.path.getmtime(net_file), vclass)
        if key not in _CACHE:
            _CACHE[key] = cls(net_file, vclass)
        return _CACHE[key]

    def time(self, from_edge: str, from_pos: float, to_edge: str, to_pos: float) -> float:
        a, b = self.index.get(from_edge), self.index.get(to_edge)
        if a is None or b is None:
            return UNREACHABLE
        pa = min(max(from_pos, 0.0), self.length[a])
        pb = min(max(to_pos, 0.0), self.length[b])
        if a == b and pb >= pa:
            return (pb - pa) / self.speed[a]
        mid = self.D[a, b]
        if mid >= UNREACHABLE:
            return UNREACHABLE
        return (self.length[a] - pa) / self.speed[a] + mid + pb / self.speed[b]
