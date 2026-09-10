"""같은 맵·고정 호출을 공유하는 배차 알고리즘 병렬 비교."""
import argparse
from measure_wait_time import compare_runs


if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('a'); parser.add_argument('b'); args = parser.parse_args()
    compare_runs('algorithm', args.a, args.b, parallel=True)
