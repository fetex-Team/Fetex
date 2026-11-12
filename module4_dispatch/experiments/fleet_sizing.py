"""
택시 대수 산정: 실측 수요(TIMS 보정)를 몇 대로 감당할 수 있는지 시뮬레이션으로 확인한다.

같은 날짜·같은 손님 목록(수요 지문 동일)으로 택시 대수만 바꿔 여러 번 돌리고
서비스율(제때 태운 비율)·평균/90% 대기시간·타임아웃을 표로 낸다.
각 실행은 별도 설정 폴더(sumo_config_fleet<N>)에서 돌아서 서로 간섭하지 않고, 원래 config.json도 그대로다.

    python module4_dispatch/experiments/fleet_sizing.py --taxis 50 70 90 --strategy prepositioned --date 2026-09-07 --jobs 2
    -> results/fleet_sizing/fleet_<전략>_<날짜>.json / .csv
"""
import argparse
import csv
import json
import os
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
from worker_protocol import emit_result, run_worker  # noqa: E402  (같은 폴더)


def worker(config_path: str, config_dir: str):
    """(하위 프로세스) 지정 설정으로 환경을 만들고 한 번 측정한 결과를 JSON 한 줄로 출력."""
    os.environ["MOBILITY_CONFIG"] = config_path
    sys.path.insert(0, str(ROOT))
    subprocess.run([sys.executable, str(ROOT / "module1_simulation" / "build_env.py"),
                    "--config-path", config_path, "--config-dir", config_dir],
                   check=True, cwd=str(ROOT), stdout=subprocess.DEVNULL)
    from measure_wait_time import run_and_measure
    d = ROOT / "module1_simulation" / config_dir
    res = run_and_measure(meta_path=str(d / "runtime_meta.json"), sumo_cfg_path=str(d / "simulation.sumocfg"))
    res.pop("waits", None)
    emit_result(res)


def run_one(base_cfg: dict, n: int, strategy: str, date: str, log_dir: Path):
    cfg = dict(base_cfg)
    cfg.update({"num_taxis": n, "taxi_strategy": strategy, "sim_date": date, "run_date_mode": "sim_date",
                "write_demand_logs": False})
    fd, path = tempfile.mkstemp(prefix=f"fleet{n}_", suffix=".json")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False)
    log = log_dir / f"fleet_{strategy}_{n}.log"
    t0 = time.time()
    try:
        res, _ = run_worker(__file__, [path, f"sumo_config_fleet{n}"], ROOT, log_path=log)  # 해시 시드 고정 = 재현성
        if res is None:
            raise RuntimeError(f"택시 {n}대 실행 실패 — 로그 확인: {log}")
        res["num_taxis"] = n
        res["wall_min"] = round((time.time() - t0) / 60, 1)
        return res
    finally:
        os.remove(path)


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "--worker":
        worker(sys.argv[2], sys.argv[3])
        return
    ap = argparse.ArgumentParser()
    ap.add_argument("--taxis", nargs="+", type=int, default=[50, 70, 90])
    ap.add_argument("--strategy", default="prepositioned", choices=["patrol", "prepositioned", "rl_prepositioned", "forecast", "surge"])
    ap.add_argument("--date", default=None, help="기본: config의 sim_date")
    ap.add_argument("--jobs", type=int, default=2)
    a = ap.parse_args()
    from config_loader import read_user_config
    base = read_user_config()  # config.json (없으면 팀 기본 프리셋)
    date = a.date or base.get("sim_date")
    out = ROOT / "results" / "fleet_sizing"
    out.mkdir(parents=True, exist_ok=True)
    with ThreadPoolExecutor(max_workers=a.jobs) as ex:
        results = list(ex.map(lambda n: run_one(base, n, a.strategy, date, out), a.taxis))
    results.sort(key=lambda r: r["num_taxis"])
    fps = {r["demand_fingerprint"] for r in results}
    cols = ["num_taxis", "n_total_passengers", "n_measured", "n_timeout_removed", "service_rate",
            "avg_wait_sec", "p90_wait_sec", "true_avg_wait_sec_with_penalty", "est_revenue_won", "wall_min"]
    stem = out / f"fleet_{a.strategy}_{date}"
    with open(f"{stem}.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(results)
    Path(f"{stem}.json").write_text(json.dumps({"strategy": a.strategy, "date": date, "same_demand": len(fps) == 1,
                                                "fingerprints": sorted(fps), "results": results},
                                               ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[택시 대수 산정] 전략={a.strategy}, 날짜={date}, 같은 손님 목록={'예' if len(fps) == 1 else '아니오'}")
    for r in results:
        print(f"  {r['num_taxis']:>4}대 | 호출 {r['n_total_passengers']} | 서비스율 {r['service_rate']*100:.1f}% | "
              f"평균대기 {r['avg_wait_sec']}s | p90 {r['p90_wait_sec']}s | 타임아웃 {r['n_timeout_removed']}")
    print(f"[저장] {stem}.csv / .json")


if __name__ == "__main__":
    main()
