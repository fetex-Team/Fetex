"""GUI도 headless 실험과 동일한 시뮬레이션 루프를 사용한다."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from measure_wait_time import run_and_measure, print_result, ROOT


def run_sumo_gui():
    result = run_and_measure(sumo_binary='sumo-gui', output_dir=ROOT / 'results/simulation')
    print_result('GUI', result)
    return result


if __name__ == '__main__':
    run_sumo_gui()
