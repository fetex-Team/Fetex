"""
Unity 재생 차량 위치 복원(tools/replay_motion.py = Assets/Fetex/Scripts/ReplayMotion.cs) 테스트.

실행: python tests/test_replay_motion.py   (pytest로도 실행 가능)
가짜 교차로: 남→북 직선 차선 A(0,-50→0,-5), 교차로 내부 곡선 차선 J(우회전), 서→동 차선 B(5,0→55,0).
"""
import math
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

from replay_motion import Lane, interpolate, lerp_angle  # noqa: E402


def _lanes():
    # 우회전 곡선: 중심 (5,-5), 반지름 5, (0,-5) → (5,0)  (북쪽으로 가다 동쪽으로 꺾음)
    arc = []
    for k in range(9):
        th = math.pi - k * (math.pi / 2) / 8
        arc += [5 + 5 * math.cos(th), -5 + 5 * math.sin(th)]
    arc_len = 5 * math.pi / 2
    return [
        Lane.from_json({"id": "A_0", "e": "A", "len": 45.0, "w": 3.2, "xz": [0, -50, 0, -5]}),
        Lane.from_json({"id": ":J_0_0", "e": ":J_0", "len": arc_len, "w": 3.2, "xz": arc}),
        Lane.from_json({"id": "B_0", "e": "B", "len": 50.0, "w": 3.2, "xz": [5, 0, 55, 0]}),
        Lane.from_json({"id": "A_1", "e": "A", "len": 45.0, "w": 3.2, "xz": [3.2, -50, 3.2, -5]}),
    ]


def _state(lane, pos, speed, lanes, length=4.0):
    L = lanes[lane]
    fx, fz = L.point_at(pos)
    bx, bz = L.point_at(pos - 0.5)
    ang = math.degrees(math.atan2(fx - bx, fz - bz)) % 360
    rad = math.radians(ang)
    return {"l": lane, "p": pos, "v": speed, "x": fx - length / 2 * math.sin(rad),
            "z": fz - length / 2 * math.cos(rad), "a": ang}


def _dist_to_lanes(x, z, lanes):
    best = 1e9
    for L in lanes:
        for i in range(len(L.xs) - 1):
            ax, az, bx, bz = L.xs[i], L.zs[i], L.xs[i + 1], L.zs[i + 1]
            dx, dz = bx - ax, bz - az
            t = max(0.0, min(1.0, ((x - ax) * dx + (z - az) * dz) / (dx * dx + dz * dz)))
            best = min(best, math.hypot(x - (ax + t * dx), z - (az + t * dz)))
    return best


def test_turn_follows_lanes_not_chord():
    lanes = _lanes()
    a = _state(0, 35.0, 8.0, lanes)           # 교차로 10m 전
    b = _state(2, 20.0, 8.0, lanes)           # 우회전 후 20m
    b["w"] = [1]                              # 교차로 내부 차선을 지나왔음
    worst_path, worst_linear = 0.0, 0.0
    for k in range(1, 20):
        f = k / 20
        x, z, h, used = interpolate(lanes, a, b, f, 5.0, 4.0)
        assert used
        worst_path = max(worst_path, _dist_to_lanes(x, z, lanes))
        lx, lz, _, _ = interpolate(lanes, a, b, f, 5.0, 4.0, version=1)
        worst_linear = max(worst_linear, _dist_to_lanes(lx, lz, lanes))
    assert worst_path < 0.6, worst_path            # 차선 중심선 위 (끝점 보정 오차 수준)
    assert worst_linear > 5.0, worst_linear        # 예전 직선 보간은 모서리를 가로지름


def test_endpoints_match_recorded_positions():
    lanes = _lanes()
    a = _state(0, 35.0, 8.0, lanes)
    b = dict(_state(2, 20.0, 8.0, lanes), w=[1])
    for f, s in ((0.0, a), (1.0, b)):
        x, z, h, _ = interpolate(lanes, a, b, f, 5.0, 4.0)
        assert math.hypot(x - s["x"], z - s["z"]) < 1e-6
        assert abs(((h - s["a"]) + 180) % 360 - 180) < 3.0


def test_heading_turns_smoothly():
    lanes = _lanes()
    a = _state(0, 35.0, 8.0, lanes)
    b = dict(_state(2, 20.0, 8.0, lanes), w=[1])
    hs = [interpolate(lanes, a, b, k / 40, 5.0, 4.0)[2] for k in range(41)]
    steps = [abs(((h2 - h1) + 180) % 360 - 180) for h1, h2 in zip(hs, hs[1:])]
    assert max(steps) < 12.0, max(steps)           # 한 번에 확 꺾이지 않음
    assert abs(hs[0] - 0.0) < 3 and abs(hs[-1] - 90.0) < 3


def test_lane_change_and_fallbacks():
    lanes = _lanes()
    a = _state(0, 10.0, 10.0, lanes)
    b = _state(3, 30.0, 10.0, lanes)               # 같은 도로의 옆 차선으로 이동
    x, z, _, used = interpolate(lanes, a, b, 0.5, 2.0, 4.0)
    assert used and 0.5 < x < 2.7                 # 두 차선 사이
    # 텔레포트 표시(-1)나 너무 먼 이동은 직선 보간으로 대체
    assert not interpolate(lanes, a, dict(b, w=[-1]), 0.5, 2.0, 4.0)[3]
    far = _state(2, 49.0, 1.0, lanes)
    assert not interpolate(lanes, a, dict(far, w=[1]), 0.5, 1.0, 4.0)[3]
    # 같은 차선에서 뒤로 가면(재경로) 직선 보간
    assert not interpolate(lanes, _state(0, 30.0, 0, lanes), _state(0, 10.0, 0, lanes), 0.5, 5.0, 4.0)[3]


def test_stopped_vehicle_keeps_heading():
    lanes = _lanes()
    a = _state(0, 20.0, 0.0, lanes)
    x, z, h, used = interpolate(lanes, a, dict(a), 0.5, 5.0, 4.0)
    assert used and math.hypot(x - a["x"], z - a["z"]) < 1e-6 and abs(h - a["a"]) < 1e-6


def test_lerp_angle_shortest_path():
    assert abs(lerp_angle(350, 10, 0.5) - 360) < 1e-9
    assert abs(lerp_angle(10, 350, 0.5) - 0) < 1e-9


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
