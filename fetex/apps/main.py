from fetex.core.config import CFG
from fetex.simulation.run_simulation import run_sumo_gui

def run_pipeline():
    print("=== [12] AI Mobility — SUMO 시뮬레이션 실행 ===\n")
    print(f"[안내] 선택된 지역: {CFG.get('region', '강남역')}")
    print("▶ [Module 1] SUMO 디지털 트윈 시뮬레이션 가동")
    run_sumo_gui()

if __name__ == "__main__":
    run_pipeline()
