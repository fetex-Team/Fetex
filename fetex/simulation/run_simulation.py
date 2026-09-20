"""GUI도 headless 실험과 동일한 시뮬레이션 루프를 사용한다."""
from pathlib import Path

from fetex.runtime.measure_wait_time import ROOT, print_result, run_and_measure


def run_sumo_gui():
    result = run_and_measure(sumo_binary='sumo-gui', output_dir=Path(ROOT) / 'results/simulation')
    print_result('GUI', result)
    return result


if __name__ == '__main__':
    run_sumo_gui()
