"""저장된 환경과 모델로 실제 배차 시뮬레이션을 실행한다."""
import argparse
from pathlib import Path
from measure_wait_time import run_and_measure, print_result, ROOT


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--headless', action='store_true')
    parser.add_argument('--strategy', choices=['patrol', 'prepositioned', 'forecast'])
    args = parser.parse_args()
    result = run_and_measure(sumo_binary='sumo' if args.headless else 'sumo-gui', strategy=args.strategy,
                             output_dir=ROOT / 'results/simulation')
    print_result('simulation', result)
