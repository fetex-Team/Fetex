"""
실행 재현성 검사: 같은 설정으로 시뮬레이션을 두 번(각각 새 프로세스) 돌려 결과가 똑같은지 확인한다.

    python tools/check_determinism.py --hours 1 --strategy prepositioned
    python tools/check_determinism.py --hours 1 --random-hash     # 고정 전(예전) 상태 비교용

현재 runtime_meta.json을 복사해 종료 시각만 줄인 임시 meta로 돌리므로 원래 설정은 바뀌지 않는다.
"""
import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
KEYS = ["demand_fingerprint", "n_total_passengers", "n_measured", "n_timeout_removed", "avg_wait_sec",
        "p90_wait_sec", "true_avg_wait_sec_with_penalty"]


def _worker(meta_path, strategy):
    sys.path.insert(0, str(ROOT))
    from config_loader import CFG
    CFG["write_demand_logs"] = False
    CFG["run_date_mode"] = "sim_date"   # 날짜까지 고정해야 같은 조건
    from measure_wait_time import run_and_measure
    meta = json.loads(Path(meta_path).read_text(encoding="utf-8"))
    meta["taxi_strategy"] = strategy
    Path(meta_path).write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    r = run_and_measure(meta_path=meta_path, sumo_cfg_path=str(Path(meta_path).with_name("simulation.sumocfg")))
    print("@@RESULT@@" + json.dumps({k: r.get(k) for k in KEYS}, ensure_ascii=False), flush=True)


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "--worker":
        return _worker(sys.argv[2], sys.argv[3])
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=1.0)
    ap.add_argument("--strategy", default="prepositioned", choices=["patrol", "prepositioned", "rl_prepositioned"])
    ap.add_argument("--random-hash", action="store_true", help="해시 시드를 고정하지 않고 돌려 비교")
    a = ap.parse_args()
    src = ROOT / "module1_simulation" / "sumo_config" / "runtime_meta.json"
    meta = json.loads(src.read_text(encoding="utf-8"))
    meta["sim_end_hour"] = meta["sim_start_hour"] + a.hours
    results = []
    for i in range(2):
        tmp = src.with_name(f"runtime_meta_det{i}.json")
        tmp.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
        env = {**os.environ, "PYTHONUTF8": "1"}
        if a.random_hash:
            env["PYTHONHASHSEED"] = str(1000 + i)
        else:
            env["PYTHONHASHSEED"] = "0"
        try:
            out = subprocess.run([sys.executable, __file__, "--worker", str(tmp), a.strategy], cwd=str(ROOT),
                                 env=env, capture_output=True, text=True)
        finally:
            tmp.unlink(missing_ok=True)
        line = next((l for l in out.stdout.splitlines() if l.startswith("@@RESULT@@")), None)
        if not line:
            print(out.stdout[-2000:], out.stderr[-2000:])
            raise SystemExit("실행 실패")
        results.append(json.loads(line[len("@@RESULT@@"):]))
        print(f"[실행 {i + 1}] {results[-1]}")
    same = results[0] == results[1]
    print(f"\n[재현성] {'같음 ✓' if same else '다름 ✗'} (해시 시드 {'무작위' if a.random_hash else '고정'})")
    return 0 if same else 1


if __name__ == "__main__":
    sys.exit(main() or 0)
