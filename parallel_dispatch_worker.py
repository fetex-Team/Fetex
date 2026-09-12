"""별도 SUMO 프로세스에서 한 조건을 실행하며 실패는 종료 코드로 전달한다."""
import argparse
from measure_wait_time import run_and_measure, print_result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('kind', choices=['strategy', 'algorithm'])
    parser.add_argument('label'); parser.add_argument('output_dir'); args = parser.parse_args()
    print_result(args.label, run_and_measure(output_dir=args.output_dir, **{args.kind: args.label}))
