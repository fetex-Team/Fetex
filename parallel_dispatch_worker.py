"""
병렬 실행 워커 스크립트. 각자의 sumo_config 디렉터리 headless로 실행하고
결과를 JSON으로 저장. 프로세스 단위 격리라(각 SUMO) traci 충돌 전혀 없음.

사용법: python parallel_dispatch_worker.py <config_dir_name> <algo_label> <output_json_path>
"""
import os
import sys

# [Windows WinError 1114 방어] XGBoost GPU/OpenMP 초기화 충돌 원천 차단
os.environ["CUDA_VISIBLE_DEVICES"] = "-1"
os.environ["OMP_NUM_THREADS"] = "1"
os.environ["KMP_DUPLICATE_LIB_OK"] = "TRUE"
os.environ["OPENBLAS_NUM_THREADS"] = "1"
os.environ["MKL_NUM_THREADS"] = "1"

try:
    import xgboost  # 프로세스 최상단에서 미리 로드하여 추후 DLL Lock 방지
except Exception:
    pass
import json
import traceback
import psutil

def elevate_worker_priority():
    """병렬 실행 프로세스의 CPU 우선순위를 높여 빠른 벤치마크 완료 지원"""
    try:
        p = psutil.Process(os.getpid())
        if os.name == "nt":  # Windows 환경
            p.nice(psutil.ABOVE_NORMAL_PRIORITY_CLASS)
        else:  # Linux/Mac 환경
            p.nice(-5)
        print(f"[우선순위] 워커 프로세스(PID: {os.getpid()})의 CPU 우선순위를 상향 조정했습니다.")
    except Exception as e:
        print(f"[안내] 우선순위 조정 건너뜀: {e}")

elevate_worker_priority()
ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

from measure_wait_time import run_and_measure, print_result


def main():
    if len(sys.argv) < 4:
        print("사용법: python parallel_dispatch_worker.py <config_dir_name> <algo_label> <output_json_path>")
        sys.exit(1)

    config_dir_name, algo_label, output_path = sys.argv[1], sys.argv[2], sys.argv[3]

    sumo_cfg_path = os.path.join(ROOT, "module1_simulation", config_dir_name, "simulation.sumocfg")
    meta_path = os.path.join(ROOT, "module1_simulation", config_dir_name, "runtime_meta.json")

    print(f"[워커] '{algo_label}' ({config_dir_name}) 측정 시작 (headless)...")

    full_output_path = os.path.join(ROOT, output_path)

    try:
        result = run_and_measure(sumo_cfg_path=sumo_cfg_path, meta_path=meta_path)
        print_result(algo_label, result)

        with open(full_output_path, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)

        print(f"\n[완료] '{algo_label}' 측정값 {output_path}에 저장완료.")
        print("[종료] 이 콘솔 창은 닫으셔도 좋습니다.")

    except Exception as e:
        tb = traceback.format_exc()
        print(f"\n❌ [워커 실행 오류] '{algo_label}' 실행 중 치명적 예외 발생:\n{tb}")

        # 오케스트레이터가 무한 대기하지 않도록 실패 신호 JSON 즉시 기록
        error_result = {
            "label": algo_label,
            "failed": True,
            "error_type": type(e).__name__,
            "error_msg": str(e),
            "traceback": tb,
            "n_total_passengers": 0,
            "n_measured": 0,
            "n_unpicked": 0,
            "n_timeout_removed": 0,
            "avg_wait_sec": None,
            "true_avg_wait_sec_with_penalty": 999999.0, # 순위 최하위 처리
        }
        try:
            with open(full_output_path, "w", encoding="utf-8") as f:
                json.dump(error_result, f, ensure_ascii=False, indent=2)
            print(f"[안내] 실패 상태를 {output_path}에 기록하여 오케스트레이터에 즉시 알렸습니다.")
        except Exception as save_err:
            print(f"[경고] 실패 상태 파일 저장 실패: {save_err}")

        sys.exit(1)


if __name__ == "__main__":
    main()
