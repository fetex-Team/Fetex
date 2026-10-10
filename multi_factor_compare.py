"""
멀티 팩터(taxi_strategy, taxi_dispatch_algorithm 등) 조합을 한 번에 병렬로 비교하는 오케스트레이터.

config_gui.py의 "🧪 A/B 비교하기" 다이얼로그에서 사용자가 고른 조합 목록을 JSON으로 넘기면:
1) 공용 맵을 1회만 생성 (도로망/구역은 taxi_strategy/dispatch_algorithm과 무관하므로 재사용 가능)
2) 조합 개수만큼 폴더를 복제하고, 각 폴더의 runtime_meta.json / simulation.sumocfg에
   그 조합의 값들을 패치
3) 조합 개수만큼 새 콘솔 창을 동시에 띄워 병렬 측정 (parallel_dispatch_worker.py 재사용)
4) 모든 결과 JSON이 도착할 때까지 대기 후 순위표로 비교 출력

사용법:
    python multi_factor_compare.py '[{"taxi_strategy":"patrol","taxi_dispatch_algorithm":"greedy"}, ...]'

주의: 실행 중 config.json이 combos[0] 값으로 덮어써집니다 (기존 parallel_dispatch_orchestrator.py와
동일한 제약). 팀 공용 config.json을 쓰고 있다면 실행 전에 팀에 공유하는 것을 권장합니다.
"""
import os
import sys
import json
import time
import datetime
import shutil
import subprocess
import xml.etree.ElementTree as ET

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

MODULE1 = os.path.join(ROOT, "module1_simulation")
CONFIG_PATH = os.path.join(ROOT, "config.json")

# build_env.py와 동일한 규칙: "hungarian"은 SUMO 내장 알고리즘이 아니라
# traci 콜백으로 직접 배정하는 모드이므로 SUMO 쪽엔 "traci"로 알려줘야 함
DISPATCH_SUMO_VALUE = {"hungarian": "traci", "custom_greedy": "traci", "custom_routeExtension": "traci"}
def _sumo_dispatch_value(algo: str, strategy: str = None) -> str:
    # RL은 선배치만 담당한다. 매칭 방식은 사용자가 고른 알고리즘을 보존한다.
    return "traci" if algo in DISPATCH_SUMO_VALUE else algo


def _combo_label(combo: dict) -> str:
    return "_".join(f"{k}={v}" for k, v in combo.items())


def decide_run_dates(combos: list) -> list:
    """비교 1회당 날짜를 '한 번만' 정해서 모든 조합에 똑같이 적용 (병렬 워커가 각자 랜덤으로 뽑던 버그 수정).

    - holiday_mode == "today"        -> 오늘 날짜
    - holiday_mode == weekday/holiday -> 그 종류에 맞는 날짜 1개 (같은 모드끼리는 같은 날짜)
    - holiday_mode 없음/auto          -> config.json 규칙(run_date_mode)으로 1개만 뽑아 전 조합 공유
    반환: 조합 순서대로의 datetime.date 리스트
    """
    from holiday_utils import pick_run_date, is_holiday_date, count_existing_logs
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        cfg = json.load(f)

    cache = {}
    dates = []
    for combo in combos:
        mode = combo.get("holiday_mode") or cfg.get("holiday_mode", "auto")
        if mode not in cache:
            if mode == "today":
                d = datetime.date.today()
            elif mode in ("weekday", "holiday"):
                d = pick_run_date(cfg, index=1 if mode == "holiday" else 0, mode="alternate")
                if is_holiday_date(d) != (mode == "holiday"):  # 안전장치: 종류가 안 맞으면 다시 몇 번 시도
                    for k in range(2, 30):
                        d = pick_run_date(cfg, index=(1 if mode == "holiday" else 0) + 2 * k, mode="alternate")
                        if is_holiday_date(d) == (mode == "holiday"):
                            break
            else:
                d = pick_run_date(cfg, index=count_existing_logs())
            cache[mode] = d
        dates.append(cache[mode])
    return dates


def build_shared_map(base_combo: dict):
    """combos[0] 값을 config.json에 반영해서 공용 맵을 1회 생성."""
    print(f"[오케스트레이터] 공용 맵 생성 중... (기준 조합: {base_combo})")
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    # holiday_mode(평일/공휴일 비교)는 이번 비교에만 쓰는 값이라 config.json에는 남기지 않음
    cfg.update({k: v for k, v in base_combo.items() if k != "holiday_mode"})
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)

    build_env_path = os.path.join(MODULE1, "build_env.py")
    subprocess.run([sys.executable, build_env_path, "--config-dir", "sumo_config_combo0"],
                    check=True, cwd=ROOT)


import time

def patch_combo(idx, combo, run_date=None):
    base_dir = os.path.join(MODULE1, "sumo_config_combo0")
    dst_dir = base_dir if idx == 0 else os.path.join(MODULE1, f"sumo_config_combo{idx}")

    if idx != 0:
        if os.path.exists(dst_dir):
            for attempt in range(5):
                try:
                    shutil.rmtree(dst_dir)
                    break
                except PermissionError:
                    print(f"[경고] {dst_dir} 삭제 재시도 중... (이전 SUMO 프로세스가 아직 안 죽었을 수 있음)")
                    time.sleep(2)
            else:
                raise RuntimeError(f"{dst_dir}를 삭제할 수 없습니다 — 남아있는 sumo.exe/python.exe 프로세스를 수동으로 종료해주세요.")
        shutil.copytree(base_dir, dst_dir)

    meta_path = os.path.join(dst_dir, "runtime_meta.json")
    with open(meta_path, "r", encoding="utf-8") as f:
        meta = json.load(f)
    meta.update(combo)
    if run_date is not None:
        # measure_wait_time.py가 이 값이 있으면 날짜를 새로 뽑지 않고 그대로 사용
        meta["run_date"] = run_date.isoformat() if hasattr(run_date, "isoformat") else str(run_date)
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)

    if "taxi_dispatch_algorithm" in combo:
        sumocfg_path = os.path.join(dst_dir, "simulation.sumocfg")
        tree = ET.parse(sumocfg_path)
        root = tree.getroot()
        for elem in root.iter("device.taxi.dispatch-algorithm"):
            elem.set("value", _sumo_dispatch_value(
                combo["taxi_dispatch_algorithm"],
                combo.get("taxi_strategy", meta.get("taxi_strategy"))
            ))
        tree.write(sumocfg_path)

    return dst_dir


def launch_workers(combos: list) -> list:
    """조합마다 parallel_dispatch_worker.py를 새 콘솔에서 동시에 실행."""
    worker_path = os.path.join(ROOT, "parallel_dispatch_worker.py")
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"

    result_paths = []
    print(f"[오케스트레이터] 조합 {len(combos)}개를 새 창에서 동시 측정 시작...")
    for idx, combo in enumerate(combos):
        label = _combo_label(combo)
        result_path = f"_result_combo{idx}.json"
        full_result_path = os.path.join(ROOT, result_path)
        if os.path.exists(full_result_path):
            os.remove(full_result_path)
        result_paths.append((label, result_path))

        config_dir_name = f"sumo_config_combo{idx}"
        if os.name == "nt":
            inner_cmd = subprocess.list2cmdline([sys.executable, worker_path, config_dir_name, label, result_path])
            full_cmd = f'cmd /k call {inner_cmd}'
            subprocess.Popen(
                full_cmd,
                cwd=ROOT, env=env, creationflags=subprocess.CREATE_NEW_CONSOLE
            )
        else:
            subprocess.Popen(
                [sys.executable, worker_path, config_dir_name, label, result_path],
                cwd=ROOT, env=env
            )
    return result_paths


def wait_for_results(result_paths: list):
    print("[오케스트레이터] 모든 결과가 도착할 때까지 대기 중...")
    while True:
        done_flags = [os.path.exists(os.path.join(ROOT, p)) for _, p in result_paths]
        status = " / ".join(
            f"{label}:{'O' if d else 'X'}" for (label, _), d in zip(result_paths, done_flags)
        )
        print(f"  {status}", end="\r")
        if all(done_flags):
            print()
            break
        time.sleep(2)


def _day_type(iso: str, is_holiday=None) -> str:
    if is_holiday is None:
        try:
            from holiday_utils import is_holiday_date
            is_holiday = is_holiday_date(datetime.date.fromisoformat(iso))
        except Exception:
            return ""
    return "공휴일/주말" if is_holiday else "평일"


def save_ranking_text(ranked, results, labels_total, combos=None):
    """최종 순위를 ab_results/ab_ranking_<시각>.txt 로 저장하고 경로를 반환."""
    out_dir = os.path.join(ROOT, "ab_results")
    os.makedirs(out_dir, exist_ok=True)
    now = datetime.datetime.now()
    path = os.path.join(out_dir, f"ab_ranking_{now.strftime('%Y%m%d_%H%M%S')}.txt")

    lines = []
    lines.append("=" * 64)
    lines.append(" A/B 비교 최종 순위")
    lines.append("=" * 64)
    lines.append(f" 실행 시각 : {now.strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f" 비교 조합 : {labels_total}개 (순위 산정 {len(ranked)}개)")
    dates = sorted({(r.get("run_date"), r.get("is_holiday")) for r in results.values() if r.get("run_date")})
    if dates:
        lines.append(" 시뮬 날짜 : " + ", ".join(f"{d} ({_day_type(d, h)})" for d, h in dates))
        if len(dates) > 1:
            lines.append("   ※ 조합마다 날짜가 다릅니다 - 승패가 날짜 차이 때문일 수 있습니다.")
    lines.append(" 기준 지표 : 평균 대기시간(타임아웃 페널티 포함, 낮을수록 좋음)")
    lines.append("-" * 64)

    best_v = ranked[0][0]
    for rank, (v, k) in enumerate(ranked, 1):
        r = results[k]
        total = r.get("n_total_passengers") or 0
        to = r.get("n_timeout_removed") or 0
        rate = f"{to / total * 100:.1f}%" if total else "-"
        diff = "" if rank == 1 else f" (1위 대비 +{(v - best_v) / best_v * 100:.1f}%)" if best_v else ""
        lines.append(f"{rank}위  {k}")
        lines.append(f"    평균 대기(페널티 포함) : {v}초 (약 {v / 60:.1f}분){' ← 최적' if rank == 1 else diff}")
        if r.get("avg_wait_sec") is not None:
            lines.append(f"    평균 대기(탑승자만)    : {r['avg_wait_sec']}초")
        lines.append(f"    타임아웃 소멸          : {to}명 / 전체 {total}명 ({rate})")
        lines.append(f"    실행 날짜              : {r.get('run_date', '-')}")
        lines.append(f"    수요 지문              : {r.get('demand_fingerprint', '-')}")
        lines.append("")

    fps = {r.get("demand_fingerprint") for r in results.values() if r.get("demand_fingerprint")}
    if len(fps) > 1:
        lines.append("※ 조합별 수요 지문이 서로 다릅니다. 같은 날짜여도 승객 수요(점심/저녁 재스폰 포함)가")
        lines.append("  완전히 같지는 않아서, 작은 차이(수 %)는 우연일 수 있습니다. 반복 실행으로 확인하세요.")
    unmeasured = [k for k, r in results.items() if r.get("true_avg_wait_sec_with_penalty") is None]
    if unmeasured:
        lines.append("※ 측정 실패(탑승자 없음)로 순위에서 제외: " + ", ".join(unmeasured))
    lines.append("=" * 64)

    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return path


def compare(result_paths: list):
    from measure_wait_time import print_result

    metrics = {}
    results = {}
    for label, path in result_paths:
        with open(os.path.join(ROOT, path), "r", encoding="utf-8") as f:
            result = json.load(f)
        print_result(label, result)
        results[label] = result
        metrics[label] = result.get("true_avg_wait_sec_with_penalty")

    print(f"\n===== [최종 비교 - 병렬 실행, 전체 {len(result_paths)}개 조합] =====")
    ranked = sorted(((v, k) for k, v in metrics.items() if v is not None))
    if not ranked:
        print(" - 모든 조합에서 측정 실패 (탑승자 없음). 시간대/승객 설정을 확인하세요.")
        return

    best_v = ranked[0][0]
    for rank, (v, k) in enumerate(ranked, 1):
        diff_pct = (v - best_v) / best_v * 100 if best_v else 0
        marker = " ← 최적" if rank == 1 else f" (+{diff_pct:.1f}%)"
        print(f" {rank}위: {k} — 평균 대기(타임아웃 포함) {v}초{marker}")

    try:
        saved = save_ranking_text(ranked, results, len(result_paths))
        print(f"\n[오케스트레이터] 최종 순위를 텍스트로 저장했습니다: {saved}")
    except Exception as e:
        print(f"\n[경고] 순위 텍스트 저장 실패: {e}")


def main():
    if len(sys.argv) < 2:
        print("사용법: python multi_factor_compare.py '<json_combos>'")
        sys.exit(1)

    combos = json.loads(sys.argv[1])
    if not combos:
        print("비교할 조합이 없습니다.")
        sys.exit(1)

    run_dates = decide_run_dates(combos)
    print("[오케스트레이터] 이번 비교의 시뮬 날짜: " + ", ".join(sorted({d.isoformat() for d in run_dates})))

    build_shared_map(combos[0])
    for idx, combo in enumerate(combos):
        patch_combo(idx, combo, run_dates[idx])

    result_paths = launch_workers(combos)
    wait_for_results(result_paths)
    compare(result_paths)

    print("\n[오케스트레이터] 완료. 자동 종료하지 않습니다.")
    input("엔터를 누르면 종료합니다...")


if __name__ == "__main__":
    main()
