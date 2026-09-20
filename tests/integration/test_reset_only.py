# test_reset_only.py (프로젝트 루트에 임시로 만들어서 실행)
import json, os, sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from fetex.dispatch.rl_env import TaxiRepositionEnv

META_PATH = os.path.join("fetex", "simulation", "sumo_config", "runtime_meta.json")
with open(META_PATH, encoding="utf-8") as f:
    meta = json.load(f)

env = TaxiRepositionEnv(
    sumo_cfg_path=os.path.join("fetex", "simulation", "sumo_config", "simulation.sumocfg"),
    meta=meta,
    demand_predictor=None,
    max_decisions=5,
    sumo_binary="sumo",
)
print("reset 시작...")
obs, _ = env.reset()
print("reset 성공:", obs.shape)
env.close()
