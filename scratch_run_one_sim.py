# -*- coding: utf-8 -*-
import os, sys
os.environ["PYTHONIOENCODING"] = "utf-8"
os.environ["PYTHONUTF8"] = "1"

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

from measure_wait_time import run_and_measure

print("[시작] 08:00~10:00 단일 시뮬레이션 실행 및 로그 생성...", flush=True)
result = run_and_measure(
    meta_path=os.path.join(ROOT, "module1_simulation", "sumo_config", "runtime_meta.json"),
    sumo_cfg_path=os.path.join(ROOT, "module1_simulation", "sumo_config", "simulation.sumocfg"),
)
print("[완료] 결과:", result, flush=True)
