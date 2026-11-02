"""
Unity 재생기(Assets/Fetex/Scripts/ReplayMotion.cs)와 똑같은 차량 위치 복원 알고리즘의 파이썬 판.

- 검증 도구(tools/verify_replay_motion.py)와 단위 테스트(tests/test_replay_motion.py)가 이 모듈을 쓴다.
- C# 쪽을 고치면 이 파일도 같이 고쳐야 한다 (두 구현의 결과가 같다는 것을 tests에서 확인).

복원 규칙 (두 프레임 A, B 사이, 진행률 f = (t - tA) / (tB - tA))
1. 경로 = [A의 차선] + B의 `w`(그 사이 지나온 차선들) + [B의 차선]. 같은 도로(edge)의 연속 차선은
   "차선 변경" 한 구간으로 묶는다.
2. 경로 길이 D = (A 차선의 남은 길이) + (중간 차선 길이 합) + (B 차선의 진행 위치).
3. 앞범퍼 이동거리 d(f): A·B 속도를 끝점 기울기로 쓰는 3차 에르미트 보간 (기울기를 0~3D로 제한해 단조 증가 보장).
4. 차 중심 = d - 길이/2 지점의 차선 중심선 위 점. 방향 = 그 점 앞뒤 1.5m의 접선.
   기록된 프레임 위치(갓길 정차 등)와의 차이는 양 끝에서 0이 되게 선형 보정(8m 이내일 때만).
5. 차선 정보가 없거나(-1, 텔레포트) 경로가 물리적으로 불가능하면(속도 대비 너무 멂/역주행) 예전 방식(직선 보간)으로 대체.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

HEADING_PROBE_M = 1.5
TELEPORT_SLACK_M = 30.0
MAX_ENDPOINT_OFFSET_M = 8.0


@dataclass
class Lane:
    id: str
    edge: str
    length: float          # SUMO 길이 (lanePos 단위)
    width: float
    xs: list[float]
    zs: list[float]
    cum: list[float]       # 꼭짓점까지의 누적 기하 길이

    @staticmethod
    def from_json(d: dict) -> "Lane":
        xz = d["xz"]
        xs, zs = xz[0::2], xz[1::2]
        cum = [0.0]
        for i in range(1, len(xs)):
            cum.append(cum[-1] + math.hypot(xs[i] - xs[i - 1], zs[i] - zs[i - 1]))
        return Lane(d["id"], d["e"], float(d["len"]), float(d.get("w", 3.2)), xs, zs, cum)

    @property
    def geom_length(self) -> float:
        return self.cum[-1]

    def point_at(self, s: float) -> tuple[float, float]:
        """SUMO 차선 위치 s(m) -> (x, z). 범위 밖이면 양 끝으로 고정."""
        if len(self.xs) == 1:
            return self.xs[0], self.zs[0]
        g = self.geom_length
        sg = s * (g / self.length) if self.length > 1e-6 else 0.0
        sg = min(max(sg, 0.0), g)
        # 이진 탐색으로 구간 찾기
        lo, hi = 0, len(self.cum) - 1
        while hi - lo > 1:
            mid = (lo + hi) // 2
            if self.cum[mid] <= sg:
                lo = mid
            else:
                hi = mid
        seg = self.cum[hi] - self.cum[lo]
        u = (sg - self.cum[lo]) / seg if seg > 1e-9 else 0.0
        return (self.xs[lo] + (self.xs[hi] - self.xs[lo]) * u,
                self.zs[lo] + (self.zs[hi] - self.zs[lo]) * u)


@dataclass
class Segment:
    lane_from: Lane
    lane_to: Lane
    s0: float
    s1: float

    @property
    def length(self) -> float:
        return max(0.0, self.s1 - self.s0)

    def point_at(self, s: float) -> tuple[float, float]:
        a = self.lane_from.point_at(s)
        if self.lane_to is self.lane_from:
            return a
        span = self.s1 - self.s0
        t = (s - self.s0) / span if span > 1e-6 else 1.0
        t = min(max(t, 0.0), 1.0)
        t = t * t * (3 - 2 * t)  # 부드러운 차선 변경
        ratio = self.lane_to.length / self.lane_from.length if self.lane_from.length > 1e-6 else 1.0
        b = self.lane_to.point_at(s * ratio)
        return a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t


def lerp_angle(a: float, b: float, t: float) -> float:
    """Unity Mathf.LerpAngle과 같은 결과 (최단 회전 방향)."""
    delta = (b - a) % 360.0
    if delta > 180.0:
        delta -= 360.0
    return a + delta * t


def build_path(lanes: list[Lane], la: int, pa: float, via: list[int], lb: int, pb: float):
    """차선 번호 목록으로 구간 리스트를 만든다. 불가능하면 None."""
    if la < 0 or lb < 0 or any(w < 0 for w in via):
        return None
    seq = [la] + list(via) + [lb]
    dedup = [seq[0]]
    for x in seq[1:]:
        if x != dedup[-1]:
            dedup.append(x)
    groups: list[list[int]] = []
    for x in dedup:
        if groups and lanes[groups[-1][-1]].edge == lanes[x].edge:
            groups[-1].append(x)
        else:
            groups.append([x])
    segs = []
    k = len(groups)
    for i, g in enumerate(groups):
        lf, lt = lanes[g[0]], lanes[g[-1]]
        s0 = pa if i == 0 else 0.0
        s1 = pb if i == k - 1 else lf.length
        if k == 1 and s1 < s0:
            if s0 - s1 > 0.5:
                return None  # 같은 차선에서 뒤로 감 -> 텔레포트/재경로 -> 직선 대체
            s1 = s0
        segs.append(Segment(lf, lt, s0, s1))
    return segs


def path_point(segs: list[Segment], c: float) -> tuple[float, float]:
    """경로 시작점 기준 거리 c 지점. c<0이면 첫 차선을 거슬러 올라가고(0에서 고정), c>D면 끝에 고정."""
    if c <= 0:
        first = segs[0]
        return first.lane_from.point_at(max(first.s0 + c, 0.0))
    acc = 0.0
    for seg in segs:
        L = seg.length
        if c <= acc + L or seg is segs[-1]:
            return seg.point_at(seg.s0 + min(c - acc, L))
        acc += L
    last = segs[-1]
    return last.point_at(last.s1)


def hermite_distance(f: float, D: float, v0: float, v1: float, dt: float) -> float:
    if D <= 1e-6:
        return 0.0
    m0 = min(max(v0 * dt, 0.0), 3 * D)
    m1 = min(max(v1 * dt, 0.0), 3 * D)
    f2, f3 = f * f, f * f * f
    return (f3 - 2 * f2 + f) * m0 + (-2 * f3 + 3 * f2) * D + (f3 - f2) * m1


def interpolate(lanes: list[Lane], a: dict, b: dict | None, f: float, dt: float,
                length: float, version: int = 2) -> tuple[float, float, float, bool]:
    """(x, z, heading_deg, used_path) — Unity와 같은 규칙으로 한 차량의 위치를 복원한다."""
    if b is None:
        return a["x"], a["z"], a["a"], False

    def linear():
        return (a["x"] + (b["x"] - a["x"]) * f, a["z"] + (b["z"] - a["z"]) * f,
                lerp_angle(a["a"], b["a"], f), False)

    if version < 2 or "l" not in a or "l" not in b:
        return linear()
    segs = build_path(lanes, a["l"], a["p"], b.get("w", []), b["l"], b["p"])
    if segs is None:
        return linear()
    D = sum(s.length for s in segs)
    vmax = max(a.get("v", 0.0), b.get("v", 0.0), 1.0)
    if D > vmax * dt * 2.0 + TELEPORT_SLACK_M:
        return linear()
    d = hermite_distance(f, D, a.get("v", 0.0), b.get("v", 0.0), dt)
    c = d - length / 2.0
    x, z = path_point(segs, c)
    # 끝점 보정: 기록된 차 중심(갓길 정차 시 옆으로 비켜선 위치 등)과 경로상 위치의 차이를
    # 양 끝에서 0이 되도록 선형으로 나눠 더한다 -> 프레임 시점에는 기록 위치와 정확히 일치.
    ax0, az0 = path_point(segs, -length / 2.0)
    bx1, bz1 = path_point(segs, D - length / 2.0)
    oax, oaz = a["x"] - ax0, a["z"] - az0
    obx, obz = b["x"] - bx1, b["z"] - bz1
    if math.hypot(oax, oaz) > MAX_ENDPOINT_OFFSET_M:
        oax = oaz = 0.0
    if math.hypot(obx, obz) > MAX_ENDPOINT_OFFSET_M:
        obx = obz = 0.0
    x += oax * (1 - f) + obx * f
    z += oaz * (1 - f) + obz * f
    bx, bz = path_point(segs, c - HEADING_PROBE_M)
    fx, fz = path_point(segs, c + HEADING_PROBE_M)
    if math.hypot(fx - bx, fz - bz) < 0.05:
        heading = lerp_angle(a["a"], b["a"], f)
    else:
        heading = math.degrees(math.atan2(fx - bx, fz - bz)) % 360.0
    return x, z, heading, True
