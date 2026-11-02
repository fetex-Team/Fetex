"""
Unity 재생 파일의 차량 궤적 품질 검사 (도로 이탈률 + 실제 궤적 대비 오차).

1) 도로 이탈률 (모든 재생 파일, v1/v2 공통)
   두 프레임 사이를 Unity와 같은 방식으로 복원한 점들이 도로 위(차선 폭 안 또는 교차로 면 안)에 있는지 본다.
   - v1 파일: 직선 보간 (예전 Unity 방식)
   - v2 파일: 차선 경로 보간 (ReplayMotion.cs와 같은 tools/replay_motion.py 알고리즘)

2) 실제 궤적 대비 오차 (--truth: stride 1로 기록한 v2 파일)
   매초 기록을 N초 간격으로 솎아낸 뒤(지나온 차선 목록은 다시 합침) 사이 시점을 복원해서,
   실제로 기록된 그 시점의 차 중심 위치와의 거리(m)를 잰다. --strides 2 5 10 처럼 여러 간격을 비교.

사용
    python tools/verify_replay_motion.py --map results/unity_replay/map.json --replay results/unity_replay/patrol.json
    python tools/verify_replay_motion.py --map results/unity_replay/map.json --truth results/motion_eval/truth_stride1.json --strides 1 2 5 10
    (예전 파일 비교) --map 옛_map.json --replay 옛_patrol.json --legacy-linear

결과는 표로 출력하고, --out으로 JSON 저장.
"""
import argparse
import json
import math
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from replay_motion import Lane, interpolate  # noqa: E402

OFFROAD_TOL_M = 1.0   # 차선 가장자리에서 이만큼까지는 허용 (차 폭 절반 수준)


def load(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def road_area(map_data):
    """도로 면 = (차선 중심선 + 폭/2 + 허용오차) 띠의 합집합 + 교차로 다각형."""
    from shapely.geometry import LineString, Polygon
    from shapely.ops import unary_union
    shapes = []
    if map_data.get("lanes"):
        for ln in map_data["lanes"]:
            xz = ln["xz"]
            pts = list(zip(xz[0::2], xz[1::2]))
            if len(pts) >= 2:
                shapes.append(LineString(pts).buffer(ln.get("w", 3.2) / 2 + OFFROAD_TOL_M, cap_style=2))
    else:
        for rd in map_data["roads"]:
            pts = [(p["x"], p["z"]) for p in rd["points"]]
            if len(pts) >= 2:
                shapes.append(LineString(pts).buffer((rd.get("width") or 3.2) / 2 + OFFROAD_TOL_M, cap_style=2))
    for j in map_data.get("junctions", []):
        pts = [(p["x"], p["z"]) for p in j["points"]]
        if len(pts) >= 3:
            poly = Polygon(pts)
            if not poly.is_valid:
                poly = poly.buffer(0)
            shapes.append(poly.buffer(OFFROAD_TOL_M))
    return unary_union(shapes)


def sample_positions(map_data, replay, samples=(0.25, 0.5, 0.75), legacy_linear=False, moving_only=True):
    lanes = [Lane.from_json(d) for d in map_data.get("lanes", [])]
    lengths = [e.get("len", 0.0) for e in replay["entities"]]
    version = 1 if legacy_linear else replay.get("version", 1)
    xs, zs, turning, path_flags = [], [], [], []
    frames = replay["frames"]
    for fa, fb in zip(frames, frames[1:]):
        dt = fb["t"] - fa["t"]
        lookup = {v["i"]: v for v in fb["vehicles"]}
        for va in fa["vehicles"]:
            vb = lookup.get(va["i"])
            if vb is None:
                continue
            if moving_only and math.hypot(vb["x"] - va["x"], vb["z"] - va["z"]) < 1.0:
                continue
            turn = abs(((vb["a"] - va["a"]) + 180) % 360 - 180) >= 60
            for f in samples:
                x, z, _h, used = interpolate(lanes, va, vb, f, dt, lengths[va["i"]], version)
                xs.append(x); zs.append(z); turning.append(turn); path_flags.append(used)
    return np.array(xs), np.array(zs), np.array(turning), np.array(path_flags)


def offroad_stats(area, xs, zs, mask=None):
    import shapely
    if mask is not None:
        xs, zs = xs[mask], zs[mask]
    if len(xs) == 0:
        return {"n": 0}
    inside = shapely.contains_xy(area, xs, zs)
    out_idx = np.where(~inside)[0]
    dist = np.zeros(len(xs))
    if len(out_idx):
        pts = shapely.points(xs[out_idx], zs[out_idx])
        dist[out_idx] = shapely.distance(area, pts)
    return {"n": int(len(xs)), "offroad_pct": round(100 * float((~inside).mean()), 2),
            "offroad_over_3m_pct": round(100 * float((dist > 3).mean()), 2),
            "p90_offroad_m": round(float(np.percentile(dist, 90)), 2),
            "max_offroad_m": round(float(dist.max()), 2)}


def subsample(truth, stride):
    """stride 1 기록을 N초 간격으로 솎아낸다. 건너뛴 프레임의 차선들은 다음 프레임의 w에 합친다."""
    frames = truth["frames"]
    maps = [{v["i"]: v for v in fr["vehicles"]} for fr in frames]
    keep = [i for i, fr in enumerate(frames) if i == 0 or i == len(frames) - 1 or round(fr["t"]) % stride == 0]
    out, prev_keep = [], None
    for idx in keep:
        fr = frames[idx]
        if prev_keep is None:
            out.append({"t": fr["t"], "vehicles": [dict(v) for v in fr["vehicles"]]})
            prev_keep = idx
            continue
        merged = []
        for v in fr["vehicles"]:
            trail, ok = [], True
            for j in range(prev_keep + 1, idx + 1):
                vj = maps[j].get(v["i"])
                if vj is None:
                    ok = False
                    break
                trail.extend(vj.get("w", []))
                trail.append(vj["l"])
            nv = dict(v)
            if ok:
                start = maps[prev_keep][v["i"]]["l"] if v["i"] in maps[prev_keep] else None
                seq = [start] + trail if start is not None else trail
                dedup = [seq[0]] if seq else []
                for x in seq[1:]:
                    if x != dedup[-1]:
                        dedup.append(x)
                nv["w"] = dedup[1:-1] if len(dedup) > 2 else []
            else:
                nv.pop("w", None)
            merged.append(nv)
        out.append({"t": fr["t"], "vehicles": merged})
        prev_keep = idx
    return out


def truth_error(map_data, truth, stride, legacy_linear=False, area=None):
    lanes = [Lane.from_json(d) for d in map_data["lanes"]]
    pxs, pzs = [], []
    lengths = [e.get("len", 0.0) for e in truth["entities"]]
    sub = subsample(truth, stride)
    by_t = {round(fr["t"]): {v["i"]: v for v in fr["vehicles"]} for fr in truth["frames"]}
    errs, head_errs = [], []
    for fa, fb in zip(sub, sub[1:]):
        dt = fb["t"] - fa["t"]
        if dt <= 1:
            continue
        lookup = {v["i"]: v for v in fb["vehicles"]}
        for k in range(1, int(round(dt))):
            t = fa["t"] + k
            actual = by_t.get(round(t), {})
            f = k / dt
            for va in fa["vehicles"]:
                vb, va_true = lookup.get(va["i"]), actual.get(va["i"])
                if vb is None or va_true is None:
                    continue
                x, z, h, _ = interpolate(lanes, va, vb, f, dt, lengths[va["i"]], 1 if legacy_linear else 2)
                errs.append(math.hypot(x - va_true["x"], z - va_true["z"]))
                if math.hypot(vb["x"] - va["x"], vb["z"] - va["z"]) >= 1.0:
                    pxs.append(x); pzs.append(z)
                head_errs.append(abs(((h - va_true["a"]) + 180) % 360 - 180))
    if not errs:
        return {"stride": stride, "n": 0}
    e, he = np.array(errs), np.array(head_errs)
    off = offroad_stats(area, np.array(pxs), np.array(pzs)) if area is not None else {}
    return {"stride": stride, "n": int(len(e)), "offroad_pct": off.get("offroad_pct"),
            "offroad_over_3m_pct": off.get("offroad_over_3m_pct"), "mean_err_m": round(float(e.mean()), 2),
            "p90_err_m": round(float(np.percentile(e, 90)), 2), "max_err_m": round(float(e.max()), 2),
            "mean_heading_err_deg": round(float(he.mean()), 1), "p90_heading_err_deg": round(float(np.percentile(he, 90)), 1)}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--map", required=True)
    ap.add_argument("--replay", nargs="*", default=[])
    ap.add_argument("--truth", default=None, help="stride 1로 기록한 v2 재생 파일")
    ap.add_argument("--strides", nargs="*", type=int, default=[2, 5, 10])
    ap.add_argument("--legacy-linear", action="store_true", help="v2 파일도 예전 직선 보간으로 평가 (비교용)")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()

    map_data = load(a.map)
    report = {"offroad": {}, "truth": []}
    if a.replay:
        area = road_area(map_data)
        for path in a.replay:
            rep = load(path)
            for legacy in ([False, True] if rep.get("version", 1) >= 2 and not a.legacy_linear else [a.legacy_linear]):
                xs, zs, turning, used = sample_positions(map_data, rep, legacy_linear=legacy)
                label = f"{os.path.basename(path)} ({'직선 보간(v1 방식)' if legacy or rep.get('version', 1) < 2 else '차선 경로 보간(v2)'})"
                stats = {"all": offroad_stats(area, xs, zs), "turns": offroad_stats(area, xs, zs, turning),
                         "path_used_pct": round(100 * float(used.mean()), 1) if len(used) else 0.0}
                report["offroad"][label] = stats
                print(f"\n[도로 이탈] {label}")
                print(f"  전체 이동 구간: {stats['all']}")
                print(f"  회전 구간(60도 이상): {stats['turns']}")
                print(f"  차선 경로 보간 사용 비율: {stats['path_used_pct']}%")
    if a.truth:
        truth = load(a.truth)
        area_t = road_area(map_data)
        print("\n[실제 궤적 대비 오차] (stride 1 기록을 솎아낸 뒤 사이 시점 복원)")
        for st in a.strides:
            for legacy in (False, True):
                r = truth_error(map_data, truth, st, legacy_linear=legacy, area=area_t)
                r["method"] = "직선 보간(v1)" if legacy else "차선 경로 보간(v2)"
                report["truth"].append(r)
                print(f"  {r}")
    if a.out:
        os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
        with open(a.out, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        print(f"\n[저장] {a.out}")


if __name__ == "__main__":
    main()
