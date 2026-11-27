"""발표 자료용 추가 기록: 택시 분포 지도 + 시간대별 대기.

7일 비교와 같은 설정으로 순찰 / 기존 RL / 고친 RL(재학습)을 다시 돌리면서
  - 1분마다 빈 택시(손님 없음) 위치
  - 손님마다 호출 시각·위치, 탑승 시각, 포기 여부
를 기록한다. 모델은 이미 학습된 것을 그대로 쓴다 (재학습 없음).

결과: results/trace/<이름>/<날짜>.json
실행: python taxi55_experiment/trace_runs.py [--dates ...] [--jobs 4]
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "module4_dispatch" / "experiments"))
OUT = ROOT / "results" / "trace"

# (이름, 설정 모드, 전략, 모델 세트)
RUNS = [
    ("patrol", "gravityVer", "patrol", None),
    ("rl_old", "gravityVer", "rl_prepositioned", "taxi55_gravityVer_best"),
    ("rl_roam_new", "gravityVer_roam", "rl_prepositioned", "taxi55_gravityVer_roam"),
]


class TraceRecorder:
    """measure_wait_time.run_and_measure가 매 스텝 capture()를 부른다."""

    def __init__(self, every=60):
        self.every, self.last = every, -1e9
        self.idle = []          # [t, x, y]
        self.req_pos = {}       # pid -> [x, y]

    def capture(self, now, requested, pickup, arrived, removed, failed, forecast, teleports,
                vehicle_ids=None, idle_taxis=None, person_ids=None, is_last=False):
        import traci
        for pid in requested:
            if pid not in self.req_pos:
                try:
                    x, y = traci.person.getPosition(pid)
                    self.req_pos[pid] = [round(x, 1), round(y, 1)]
                except traci.exceptions.TraCIException:
                    self.req_pos[pid] = None
        if now - self.last < self.every:
            return
        self.last = now
        self.requested, self.pickup, self.removed = requested, pickup, removed
        idle = idle_taxis if idle_taxis is not None else traci.vehicle.getTaxiFleet(0)
        for vid in idle:
            try:
                x, y = traci.vehicle.getPosition(vid)
            except traci.exceptions.TraCIException:
                continue
            if abs(x) < 1e7 and abs(y) < 1e7:
                self.idle.append([round(now), round(x, 1), round(y, 1)])

    def finish(self, metrics):
        self.metrics = metrics


def worker(cfg_path, meta_path, out_path):
    os.environ["MOBILITY_CONFIG"] = cfg_path
    sys.path.insert(0, str(ROOT))
    from measure_wait_time import run_and_measure
    rec = TraceRecorder()
    r = run_and_measure(meta_path=meta_path, sumo_cfg_path=str(Path(meta_path).with_name("simulation.sumocfg")),
                        recorder=rec)
    meta = json.loads(Path(meta_path).read_text(encoding="utf-8"))
    pax = []
    for pid, t0 in rec.requested.items():
        pax.append({"t": t0, "pickup": rec.pickup.get(pid) if pid not in rec.removed else None,
                    "timeout": pid in rec.removed, "pos": rec.req_pos.get(pid)})
    r.pop("waits", None)
    Path(out_path).write_text(json.dumps({"metrics": r, "sim_start_hour": meta.get("sim_start_hour", 9),
                                          "idle": rec.idle, "passengers": pax}, ensure_ascii=False),
                              encoding="utf-8")
    from worker_protocol import emit_result
    emit_result({"ok": True, "avg_wait_sec": r.get("avg_wait_sec")})


def main():
    import taxi55 as t
    from worker_protocol import run_worker
    ap = argparse.ArgumentParser()
    ap.add_argument("--dates", nargs="+", default=t.TEST_DATES)
    ap.add_argument("--jobs", type=int, default=4)
    a = ap.parse_args()

    workdirs = {}
    try:
        for mode in sorted({r[1] for r in RUNS}):
            cfg = json.load(open(t.cfg_path(mode), encoding="utf-8"))
            cfg.update({"write_demand_logs": False, "run_date_mode": "sim_date"})
            fd, cpath = tempfile.mkstemp(prefix="trace_cfg_", suffix=".json")
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(cfg, f, ensure_ascii=False)
            wd = ROOT / "module1_simulation" / f"sumo_config_trace_{mode}"
            subprocess.run([t.PY, str(ROOT / "module1_simulation" / "build_env.py"),
                            "--config-path", cpath, "--config-dir", wd.name], check=True, cwd=str(ROOT),
                           stdout=subprocess.DEVNULL, env=t.run_env(mode))
            workdirs[mode] = (cpath, wd, json.loads((wd / "runtime_meta.json").read_text(encoding="utf-8")))
        # 지도 그리기용 도로망 보관
        OUT.mkdir(parents=True, exist_ok=True)
        net = next(workdirs["gravityVer"][1].glob("*.net.xml"))
        shutil.copy2(net, OUT / "network.net.xml")

        jobs = [(r, d) for r in RUNS for d in a.dates]

        def run(job):
            (name, mode, strategy, set_name), date = job
            out = OUT / name / f"{date}.json"
            if out.exists():
                return f"= {name} {date} (저장된 결과)"
            out.parent.mkdir(parents=True, exist_ok=True)
            cpath, wd, base = workdirs[mode]
            meta = dict(base, taxi_strategy=strategy, run_date=date)
            mpath = wd / f"runtime_meta_{name}_{date}.json"
            mpath.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
            env = t.run_env(mode, t.set_env(os.path.join(t.BACKUPS, set_name)) if set_name else None)
            t0 = time.time()
            res, _ = run_worker(__file__, [cpath, mpath, out], ROOT, log_path=OUT / name / f"{date}.log",
                                env_extra={k: v for k, v in env.items() if k.startswith(("AI_", "MOBILITY", "PATH", "SUMO", "VIRTUAL"))})
            if res is None:
                raise RuntimeError(f"{name} {date} 실패 — {OUT / name / (date + '.log')}")
            return f"✓ {name} {date}: 평균 대기 {res['avg_wait_sec']}초 ({(time.time() - t0) / 60:.1f}분)"

        print(f"[기록 실행] {len(jobs)}회, 병렬 {a.jobs}", flush=True)
        with ThreadPoolExecutor(max_workers=a.jobs) as ex:
            for msg in ex.map(run, jobs):
                print("  " + msg, flush=True)
    finally:
        for cpath, wd, _ in workdirs.values():
            os.remove(cpath)
            shutil.rmtree(wd, ignore_errors=True)
    print(f"[완료] {OUT}", flush=True)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--worker":
        worker(*sys.argv[2:5])
    else:
        main()
