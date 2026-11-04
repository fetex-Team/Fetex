"""GUI 시각화 실행 및 승객 대기시간 계측. 실행·측정 루프는 measure_wait_time.run_and_measure를 공유한다."""
import os
import sys
import json
import time
from pathlib import Path

# 프로젝트 루트(module1_simulation의 부모 폴더)를 sys.path에 추가 — import보다 먼저!
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import traci

from measure_wait_time import run_and_measure, print_result

SUMO_CFG = os.path.join(ROOT, "module1_simulation", "sumo_config", "simulation.sumocfg")
META_PATH = os.path.join(ROOT, "module1_simulation", "sumo_config", "runtime_meta.json")


def run_sumo_gui(sumo_binary: str = "sumo-gui", output_dir=None, gui_delay: float = 0.01,
                 sumo_cfg_path: str = None, meta_path: str = None) -> dict:
    """SUMO GUI로 시뮬레이션을 보여 주면서 측정한다.

    실행·측정 루프는 measure_wait_time.run_and_measure 하나만 쓴다 (예전에는 이 파일에 복사본이 있어
    배차 비용·실행 날짜·타임아웃 집계·예측 재배치·학습 로그 기록이 측정 경로와 달라져 있었음).
    여기서는 GUI에만 필요한 것(스텝마다 잠깐 멈춤, 창 제목에 시각 표시, 결과 저장)만 얹는다.
    """
    if 'SUMO_HOME' in os.environ:
        tools = os.path.join(os.environ['SUMO_HOME'], 'tools')
        if tools not in sys.path:
            sys.path.append(tools)

    print("SUMO Digital Twin 시뮬레이션을 시작합니다...")

    def _gui_step(now_seconds, sim_start_hour):
        if gui_delay > 0:
            time.sleep(gui_delay)
        # SUMO 창 타이틀바에 실제 시각(시:분:초) 출력
        current_hour_float = sim_start_hour + (now_seconds / 3600.0)
        h = int(current_hour_float) % 24
        m = int((current_hour_float - int(current_hour_float)) * 60)
        s = int((((current_hour_float - int(current_hour_float)) * 60) - m) * 60)
        try:
            view_ids = traci.gui.getViewIDList()
            if view_ids:
                traci.gui.setWindowCaption(
                    view_ids[0],
                    f"DT Mobility Simulation | Time: {h:02d}:{m:02d}:{s:02d} (Elapsed: {int(now_seconds)}s)"
                )
        except Exception:
            pass  # sumo(GUI 없음)로 실행하면 gui 명령이 없음

    result = run_and_measure(sumo_binary=sumo_binary, sumo_cfg_path=sumo_cfg_path or SUMO_CFG,
                             meta_path=meta_path or META_PATH, step_hook=_gui_step)
    print_result("GUI 시뮬레이션", result)

    # 결과 디렉터리가 지정되었거나 기본 경로가 있으면 저장
    out_path = Path(output_dir or os.path.join(ROOT, "results", "simulation"))
    try:
        out_path.mkdir(parents=True, exist_ok=True)
        with open(out_path / "metrics.json", "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        print(f"[안내] 시뮬레이션 결과가 {out_path / 'metrics.json'}에 저장되었습니다.")
    except Exception as e:
        print(f"[경고] 결과 저장 실패: {e}")

    return result


if __name__ == "__main__":
    run_sumo_gui()
