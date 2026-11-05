"""
병렬 배차 비교용 워커. 지정된 sumo_config 폴더에서 headless로 측정하고
결과를 JSON으로 저장. 완전히 별도 프로세스(새 콘솔)라 traci 소켓 충돌 없음.

사용법: python parallel_dispatch_worker.py <config_dir_name> <algo_label> <output_json_path>
"""
import os
import sys
import json

def elevate_worker_priority():
    """병렬 워커 프로세스의 CPU 우선순위를 높여 다른 작업으로 인한 시뮬레이션 지연 방지"""
    try:
        import psutil  # 없으면 우선순위 조정만 건너뛰고 측정은 그대로 (pip install psutil)
        p = psutil.Process(os.getpid())
        if os.name == "nt":  # Windows 환경
            p.nice(psutil.ABOVE_NORMAL_PRIORITY_CLASS)
        else:  # Linux/Mac 환경
            p.nice(-5)
        print(f"[안내] 워커 프로세스(PID: {os.getpid()})의 CPU 우선순위를 상향 조정했습니다.")
    except Exception as e:
        print(f"[경고] 프로세스 우선순위 조정 실패: {e}")

# 스크립트 실행 시 즉시 적용
elevate_worker_priority()
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # 프로젝트 루트 (이 파일은 모듈의 하위 폴더에 있음)
sys.path.insert(0, ROOT)

from measure_wait_time import run_and_measure, print_result
from module4_dispatch.experiments.ab_queue import write_json_atomic


def main():
    if len(sys.argv) < 4:
        print("사용법: python parallel_dispatch_worker.py <config_dir_name> <algo_label> <output_json_path>")
        sys.exit(1)

    config_dir_name, algo_label, output_path = sys.argv[1], sys.argv[2], sys.argv[3]

    sumo_cfg_path = os.path.join(ROOT, "module1_simulation", config_dir_name, "simulation.sumocfg")
    meta_path = os.path.join(ROOT, "module1_simulation", config_dir_name, "runtime_meta.json")

    out_full = os.path.join(ROOT, output_path)
    os.makedirs(os.path.dirname(out_full), exist_ok=True)   # results/ab_results/<요청ID>/

    print(f"[워커] '{algo_label}' ({config_dir_name}) 측정 시작 (headless)...")
    try:
        result = run_and_measure(sumo_cfg_path=sumo_cfg_path, meta_path=meta_path)
    except BaseException as e:
        # 실패도 결과 파일로 알린다 — 오케스트레이터가 이 조합을 '측정 실패'로 처리하고 끝까지 기다리지 않게
        write_json_atomic(out_full, {"error": f"{type(e).__name__}: {e}", "true_avg_wait_sec_with_penalty": None})
        raise
    print_result(algo_label, result)

    # 오케스트레이터는 파일이 '생기면' 읽으므로, 다 쓴 뒤 한 번에 교체한다 (반쯤 쓰인 JSON 방지)
    write_json_atomic(out_full, result)

    print(f"\n[워커] '{algo_label}' 완료 — {output_path}에 저장했습니다.")
    print("[워커] 이 창은 자동으로 닫히지 않습니다.")


if __name__ == "__main__":
    main()