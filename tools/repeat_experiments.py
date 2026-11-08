"""
여러 날짜 x 전략 반복 실험 + 통계 검정.

하루치 실험 1번으로는 "어느 전략이 낫다"를 말하기 어렵다. 이 도구는
- 날짜마다 같은 손님 목록(수요 지문 동일)으로 전략들을 돌리고 (해시 시드 고정 → 재실행해도 같은 결과)
- 날짜를 짝지어(paired) 기준 전략(patrol) 대비 차이의 평균·95% 신뢰구간·부호 검정 p값을 계산한다.

    python tools/repeat_experiments.py --jobs 2
    python tools/repeat_experiments.py --dates 2026-09-07 2026-09-12 --strategies patrol prepositioned
    python tools/repeat_experiments.py --scenario external_away     # 외부 운행 복귀 지연 시나리오
    -> results/repeat/<이름>/runs.csv, summary.json, summary.md
    중간에 멈춰도 다시 실행하면 끝난 실행(results/repeat/<이름>/runs/*.json)은 건너뛰고 이어 한다. 처음부터: --fresh

모든 실행은 별도 meta 파일로 돌고(지도는 한 번만 생성), 원래 config.json과 학습 로그는 바뀌지 않는다.
"""
import argparse
import csv
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# 평가 기간(예측·RL 학습 기간 2026-07-13~09-06 이후, 공휴일 제외): 월~일 하루씩 7일, 9~10월에 고르게.
# 실측 날씨 기준 비 온 날 3일 포함 (9~23시 강수: 10-13 화 2.6mm, 10-18 일 27.5mm, 10-24 토 4.9mm)
DEFAULT_DATES = ["2026-09-07", "2026-10-13", "2026-10-14", "2026-09-10",   # 월 화 수 목
                 "2026-09-18", "2026-10-24", "2026-10-18"]                 # 금 토 일
SCENARIOS = {
    "base": {},
    "external_away": {"taxi_external_away": True},
    "full_demand": {"real_demand_scale": 1.0},
    # 헝가리안 비용을 직선 거리 대신 도로망 최단 이동 시간으로 (module4_dispatch/network_cost.py)
    "network_dispatch": {"dispatch_cost": "network"},
    "network_dispatch_full": {"dispatch_cost": "network", "real_demand_scale": 1.0},
}
METRICS = ["service_rate", "n_timeout_removed", "avg_wait_sec", "p90_wait_sec",
           "true_avg_wait_sec_with_penalty", "est_revenue_won"]
BETTER_HIGH = {"service_rate": True, "est_revenue_won": True}


def _worker(cfg_path, meta_path):
    os.environ["MOBILITY_CONFIG"] = cfg_path
    sys.path.insert(0, str(ROOT))
    from measure_wait_time import run_and_measure
    r = run_and_measure(meta_path=meta_path, sumo_cfg_path=str(Path(meta_path).with_name("simulation.sumocfg")))
    r.pop("waits", None)
    print("@@RESULT@@" + json.dumps(r, ensure_ascii=False), flush=True)


def _run(cfg_path, base_meta, date, strategy, workdir, log_dir):
    done = log_dir.parent / "runs" / f"{date}_{strategy}.json"
    if done.exists():                       # 이어 하기: 끝난 실행은 저장된 결과를 쓴다
        r = json.loads(done.read_text(encoding="utf-8"))
        print(f"  = {date} {strategy}: 저장된 결과 사용", flush=True)
        return r
    meta = dict(base_meta)
    meta.update({"taxi_strategy": strategy, "run_date": date})
    mpath = workdir / f"runtime_meta_{strategy}_{date}.json"
    mpath.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    log = log_dir / f"{date}_{strategy}.log"
    t0 = time.time()
    with open(log, "w", encoding="utf-8") as lf:
        p = subprocess.run([sys.executable, __file__, "--worker", cfg_path, str(mpath)], cwd=str(ROOT),
                           stdout=subprocess.PIPE, stderr=lf, text=True,
                           env={**os.environ, "PYTHONUTF8": "1", "PYTHONHASHSEED": "0"})
        lf.write(p.stdout)
    line = next((l for l in p.stdout.splitlines() if l.startswith("@@RESULT@@")), None)
    if line is None:
        raise RuntimeError(f"{date} {strategy} 실패 — {log}")
    r = json.loads(line[len("@@RESULT@@"):])
    r.update({"date": date, "strategy": strategy, "wall_min": round((time.time() - t0) / 60, 1)})
    done.parent.mkdir(parents=True, exist_ok=True)
    done.write_text(json.dumps(r, ensure_ascii=False), encoding="utf-8")
    print(f"  ✓ {date} {strategy}: 서비스율 {r['service_rate']*100:.1f}% 타임아웃 {r['n_timeout_removed']} "
          f"({r['wall_min']}분)", flush=True)
    return r


def _t_crit(df):
    table = {1: 12.706, 2: 4.303, 3: 3.182, 4: 2.776, 5: 2.571, 6: 2.447, 7: 2.365, 8: 2.306, 9: 2.262,
             10: 2.228, 12: 2.179, 15: 2.131, 20: 2.086, 30: 2.042}
    return table.get(df) or table[min(table, key=lambda k: abs(k - df))]


def _sign_test_p(diffs):
    """양측 부호 검정 p값 (0 차이는 제외)."""
    n_pos, n_neg = sum(d > 0 for d in diffs), sum(d < 0 for d in diffs)
    n = n_pos + n_neg
    if n == 0:
        return 1.0
    k = min(n_pos, n_neg)
    p = sum(math.comb(n, i) for i in range(k + 1)) / 2 ** n
    return min(1.0, 2 * p)


def summarize(rows, baseline):
    by = {}
    for r in rows:
        by.setdefault(r["strategy"], {})[r["date"]] = r
    dates = sorted({r["date"] for r in rows})
    out = {"dates": dates, "baseline": baseline, "strategies": {}, "same_demand_per_date": {}}
    for d in dates:
        out["same_demand_per_date"][d] = len({by[s][d]["demand_fingerprint"] for s in by if d in by[s]}) == 1
    for s, runs in by.items():
        st = {}
        for m in METRICS:
            vals = [runs[d][m] for d in dates if d in runs and runs[d].get(m) is not None]
            mean = sum(vals) / len(vals) if vals else None
            entry = {"mean": mean, "n": len(vals)}
            if s != baseline and baseline in by:
                diffs = [runs[d][m] - by[baseline][d][m] for d in dates
                         if d in runs and d in by[baseline] and runs[d].get(m) is not None]
                if len(diffs) >= 2:
                    md = sum(diffs) / len(diffs)
                    sd = math.sqrt(sum((x - md) ** 2 for x in diffs) / (len(diffs) - 1))
                    half = _t_crit(len(diffs) - 1) * sd / math.sqrt(len(diffs))
                    better = sum((x > 0) == BETTER_HIGH.get(m, False) for x in diffs if x != 0)
                    entry.update({"diff_mean": md, "diff_ci95": [md - half, md + half],
                                  "sign_test_p": _sign_test_p(diffs), "days_better": better, "days": len(diffs)})
            st[m] = entry
        out["strategies"][s] = st
    return out


def to_markdown(summary):
    names = {"service_rate": "서비스율", "n_timeout_removed": "타임아웃(명)", "avg_wait_sec": "평균 대기(초)",
             "p90_wait_sec": "90% 대기(초)", "true_avg_wait_sec_with_penalty": "대기(타임아웃 포함, 초)",
             "est_revenue_won": "추정 매출(원)"}
    lines = [f"날짜 {len(summary['dates'])}일: {', '.join(summary['dates'])}  (기준: {summary['baseline']})", "",
             "| 전략 | " + " | ".join(names[m] for m in METRICS) + " |",
             "|---|" + "---:|" * len(METRICS)]
    for s, st in summary["strategies"].items():
        cells = []
        for m in METRICS:
            e = st[m]
            if e["mean"] is None:
                cells.append("-")
                continue
            v = f"{e['mean']*100:.1f}%" if m == "service_rate" else f"{e['mean']:,.0f}" if m == "est_revenue_won" else f"{e['mean']:.1f}"
            if "diff_mean" in e:
                lo, hi = e["diff_ci95"]
                k = 100 if m == "service_rate" else 1
                unit = "%p" if m == "service_rate" else ""
                v += f" ({e['diff_mean']*k:+.1f}{unit}, 95% CI {lo*k:+.1f}~{hi*k:+.1f}, {e['days_better']}/{e['days']}일 우세, p={e['sign_test_p']:.3f})"
            cells.append(v)
        lines.append(f"| {s} | " + " | ".join(cells) + " |")
    lines += ["", "괄호: 기준 전략 대비 날짜별 차이의 평균, 95% 신뢰구간(t분포), 더 나았던 날 수, 부호 검정 p값."]
    return "\n".join(lines)


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "--worker":
        return _worker(sys.argv[2], sys.argv[3])
    ap = argparse.ArgumentParser()
    ap.add_argument("--dates", nargs="+", default=DEFAULT_DATES)
    ap.add_argument("--strategies", nargs="+", default=["patrol", "prepositioned", "rl_prepositioned"])
    ap.add_argument("--baseline", default="patrol")
    ap.add_argument("--scenario", default="base", choices=sorted(SCENARIOS))
    ap.add_argument("--jobs", type=int, default=2)
    ap.add_argument("--name", default=None)
    ap.add_argument("--fresh", action="store_true", help="저장된 실행 결과를 지우고 처음부터")
    ap.add_argument("--set", nargs="*", default=[], metavar="KEY=VALUE",
                    help="시나리오 위에 덮어쓸 설정 (예: forecast_min_idle_ratio=0.3). 값은 JSON으로 해석")
    a = ap.parse_args()

    name = a.name or a.scenario
    out = ROOT / "results" / "repeat" / name
    log_dir = out / "logs"
    if a.fresh:
        shutil.rmtree(out / "runs", ignore_errors=True)
    log_dir.mkdir(parents=True, exist_ok=True)
    cfg = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
    extra = {}
    for kv in a.set:
        k, _, v = kv.partition("=")
        try:
            extra[k] = json.loads(v)
        except json.JSONDecodeError:
            extra[k] = v
    cfg.update({"write_demand_logs": False, "run_date_mode": "sim_date", **SCENARIOS[a.scenario], **extra})
    fd, cfg_path = tempfile.mkstemp(prefix="repeat_cfg_", suffix=".json")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False)
    workdir = ROOT / "module1_simulation" / f"sumo_config_repeat_{name}"
    try:
        subprocess.run([sys.executable, str(ROOT / "module1_simulation" / "build_env.py"),
                        "--config-path", cfg_path, "--config-dir", workdir.name],
                       check=True, cwd=str(ROOT), stdout=subprocess.DEVNULL,
                       env={**os.environ, "PYTHONUTF8": "1", "PYTHONHASHSEED": "0"})
        base_meta = json.loads((workdir / "runtime_meta.json").read_text(encoding="utf-8"))
        jobs = [(d, s) for d in a.dates for s in a.strategies]
        print(f"[반복 실험] 시나리오 {a.scenario}: {len(a.dates)}일 x {len(a.strategies)}전략 = {len(jobs)}회, 병렬 {a.jobs}")
        with ThreadPoolExecutor(max_workers=a.jobs) as ex:
            rows = list(ex.map(lambda j: _run(cfg_path, base_meta, j[0], j[1], workdir, log_dir), jobs))
    finally:
        os.remove(cfg_path)
        shutil.rmtree(workdir, ignore_errors=True)

    cols = ["date", "strategy", "demand_fingerprint", "is_holiday", "n_total_passengers", "n_measured", *METRICS,
            "wall_min"]
    with open(out / "runs.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(sorted(rows, key=lambda r: (r["date"], r["strategy"])))
    summary = summarize(rows, a.baseline)
    summary.update({"scenario": a.scenario, "overrides": {**SCENARIOS[a.scenario], **extra}})
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    md = to_markdown(summary)
    (out / "summary.md").write_text(md + "\n", encoding="utf-8")
    bad = [d for d, ok in summary["same_demand_per_date"].items() if not ok]
    print("\n" + md)
    print(f"\n[공정 비교] 날짜별 수요 지문 {'모두 같음' if not bad else '다른 날짜 있음: ' + ', '.join(bad)}")
    print(f"[저장] {out}")


if __name__ == "__main__":
    sys.exit(main() or 0)
