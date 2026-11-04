"""
궤적 정확도 평가용 "정답" 재생 파일 만들기: 매초(stride 1) 기록, 짧은 구간만.

    python tools/record_motion_truth.py --hours 2 --strategy patrol
    -> results/motion_eval/truth_stride1.json, results/motion_eval/map.json
그다음
    python tools/verify_replay_motion.py --map results/motion_eval/map.json \
        --truth results/motion_eval/truth_stride1.json --strides 2 5 10

현재 runtime_meta.json을 복사해 종료 시각만 줄인 임시 meta로 돌리므로 원래 설정은 바뀌지 않는다.
"""
import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tools.export_unity_replay import ReplayRecorder, export_map  # noqa: E402
from measure_wait_time import META_PATH, run_and_measure    # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=2.0)
    ap.add_argument("--out", type=Path, default=ROOT / "results" / "motion_eval")
    a = ap.parse_args()
    meta_path = Path(META_PATH)
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["sim_end_hour"] = meta["sim_start_hour"] + a.hours
    eval_meta = meta_path.with_name("runtime_meta_motion_eval.json")
    eval_meta.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    try:
        map_data = export_map(eval_meta)
        a.out.mkdir(parents=True, exist_ok=True)
        (a.out / "map.json").write_text(json.dumps(map_data, separators=(",", ":"), ensure_ascii=False), encoding="utf-8")
        rec = ReplayRecorder(a.out / "truth_stride1.json", map_data, stride=1)
        res = run_and_measure(meta_path=str(eval_meta), sumo_cfg_path=str(meta_path.with_name("simulation.sumocfg")),
                              recorder=rec)
        print({k: v for k, v in res.items() if k not in ("waits", "demand_summary")})
    finally:
        os.remove(eval_meta)


if __name__ == "__main__":
    main()
