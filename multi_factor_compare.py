"""
멀티 팩터(taxi_strategy, taxi_dispatch_algorithm 등) 조합을 한 번에 병렬로 비교하는 오케스트레이터.

config_gui.py의 "🧪 A/B 비교하기" 다이얼로그에서 사용자가 고른 조합 목록을 JSON으로 넘기면:
1) 공용 맵을 1회만 생성 (도로망/구역은 taxi_strategy/dispatch_algorithm과 무관하므로 재사용 가능)
2) 조합 개수만큼 폴더를 복제하고, 각 폴더의 runtime_meta.json / simulation.sumocfg에
   그 조합의 값들을 패치
3) 조합 개수만큼 새 콘솔 창을 최대 4개씩만 띄워 병렬 측정 (parallel_dispatch_worker.py 재사용)
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
import uuid
import signal
import tempfile
import subprocess
import xml.etree.ElementTree as ET

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

MODULE1 = os.path.join(ROOT, "module1_simulation")
CONFIG_PATH = os.path.join(ROOT, "config.json")
AB_QUEUE_DIR = os.path.join(ROOT, ".ab_compare_queue")

# build_env.py와 동일한 규칙: "hungarian"은 SUMO 내장 알고리즘이 아니라
# traci 콜백으로 직접 배정하는 모드이므로 SUMO 쪽엔 "traci"로 알려줘야 함
DISPATCH_SUMO_VALUE = {"hungarian": "traci", "custom_greedy": "traci", "custom_routeExtension": "traci"}
def _sumo_dispatch_value(algo: str, strategy: str = None) -> str:
    # RL은 선배치만 담당한다. 매칭 방식은 사용자가 고른 알고리즘을 보존한다.
    return "traci" if algo in DISPATCH_SUMO_VALUE else algo


def _ignore_console_interrupt(signum, frame):
    """A/B 전용 콘솔에 잘못 전달된 Ctrl+C/Ctrl+Break는 비교를 중단하지 않는다."""
    print("\n[보호] Ctrl+C/Break 신호를 무시했습니다. A/B 비교와 대기열은 계속 실행됩니다.")


def _install_interrupt_guard():
    signal.signal(signal.SIGINT, _ignore_console_interrupt)
    if hasattr(signal, "SIGBREAK"):
        signal.signal(signal.SIGBREAK, _ignore_console_interrupt)


def _save_config_atomically(config):
    """중단 신호가 와도 기존 config.json을 비우지 않는 교체 저장."""
    fd, temp_path = tempfile.mkstemp(prefix=".config_ab_", suffix=".json", dir=ROOT, text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(config, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp_path, CONFIG_PATH)
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)


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


def build_shared_map(base_combo: dict, config_dir_name: str):
    """combos[0] 값을 config.json에 반영해서 공용 맵을 1회 생성."""
    print(f"[오케스트레이터] 공용 맵 생성 중... (기준 조합: {base_combo})")
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    # holiday_mode(평일/공휴일 비교)는 이번 비교에만 쓰는 값이라 config.json에는 남기지 않음
    cfg.update({k: v for k, v in base_combo.items() if k != "holiday_mode"})
    _save_config_atomically(cfg)

    build_env_path = os.path.join(MODULE1, "build_env.py")
    subprocess.run([sys.executable, build_env_path, "--config-dir", config_dir_name],
                    check=True, cwd=ROOT)


import time

def patch_combo(idx, combo, run_date=None, config_dir_names=None):
    """이번 A/B 요청만의 고유 폴더에 조합을 준비한다.

    이전 실행의 sumo_config_combo* 폴더를 지우지 않는다. 실행 중인 SUMO가
    파일을 잠그는 문제와, 다른 A/B 요청의 결과가 섞이는 문제를 함께 막는다.
    """
    if not config_dir_names:
        config_dir_names = [f"sumo_config_combo{i}" for i in range(idx + 1)]
    base_dir = os.path.join(MODULE1, config_dir_names[0])
    dst_dir = os.path.join(MODULE1, config_dir_names[idx])

    if idx != 0:
        if os.path.exists(dst_dir):
            raise RuntimeError(f"고유 A/B 작업 폴더가 이미 존재합니다: {dst_dir}")
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



MAX_PARALLEL_SLOTS = 4  # 스타크래프트 건물 슬롯: 최대 동시 실행 프로세스 수


def _pid_is_alive(pid):
    """종료된 A/B 실행이 남긴 대기표는 다음 실행이 자동으로 치운다."""
    try:
        pid = int(pid)
    except (ValueError, TypeError):
        return False

    # Windows의 os.kill(pid, 0)은 일부 Python/Windows 조합에서 WinError 87을
    # SystemError로 바꿔 내보낸다. OpenProcess로 확인해야 안정적이다.
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
        kernel32.GetExitCodeProcess.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
        kernel32.CloseHandle.restype = wintypes.BOOL
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            # 접근 거부라면 존재는 하지만 다른 권한으로 실행 중인 프로세스다.
            return ctypes.get_last_error() == 5
        try:
            exit_code = wintypes.DWORD()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                return True
            return exit_code.value == STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)

    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _join_ab_queue(combos, config_snapshot=None):
    """A/B 비교 '한 판' 자체를 전역 대기열에 넣는다.

    한 판 안에서는 run_queue_and_wait가 4개 슬롯을 관리한다. 이 바깥 대기열은
    사용자가 A/B 버튼을 다시 눌러도 두 번째 오케스트레이터가 같은 임시 파일과
    SUMO 슬롯을 동시에 쓰지 않게 한다.
    """
    os.makedirs(AB_QUEUE_DIR, exist_ok=True)
    ticket_name = f"{time.time_ns():020d}_{os.getpid()}_{uuid.uuid4().hex}.json"
    ticket_path = os.path.join(AB_QUEUE_DIR, ticket_name)
    ticket = {
        "pid": os.getpid(),
        "created_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "combo_count": len(combos),
        "config_snapshot": config_snapshot,
    }
    # x 모드는 같은 이름의 대기표를 절대 덮어쓰지 않게 한다.
    with open(ticket_path, "x", encoding="utf-8") as f:
        json.dump(ticket, f, ensure_ascii=False, indent=2)

    announced_wait = False
    try:
        while True:
            tickets = []
            for name in os.listdir(AB_QUEUE_DIR):
                if not name.endswith(".json"):
                    continue
                path = os.path.join(AB_QUEUE_DIR, name)
                try:
                    with open(path, encoding="utf-8") as f:
                        info = json.load(f)
                    if not _pid_is_alive(info.get("pid")):
                        os.remove(path)
                        print(f"[A/B 대기열] 종료된 실행의 대기표를 정리했습니다: {name}")
                        continue
                    tickets.append((name, path, info))
                except (OSError, json.JSONDecodeError):
                    # 파일을 쓰는 짧은 순간에는 다음 확인 주기에 다시 읽는다.
                    continue

            tickets.sort(key=lambda item: item[0])
            own_index = next((i for i, item in enumerate(tickets) if item[1] == ticket_path), None)
            if own_index == 0:
                print("[A/B 대기열] 내 차례입니다. 최대 4개 슬롯으로 비교를 시작합니다.")
                return ticket_path, ticket
            if own_index is None:
                raise RuntimeError("A/B 대기표를 찾을 수 없습니다. 다시 실행해주세요.")
            if not announced_wait:
                print(f"[A/B 대기열] 이미 실행 중인 비교가 있습니다. 이번 비교는 대기열 {own_index}번째입니다. "
                      "앞선 비교가 끝나면 자동으로 시작합니다.")
                announced_wait = True
            time.sleep(2)
    except Exception:
        try:
            os.remove(ticket_path)
        except OSError:
            pass
        raise


def _leave_ab_queue(ticket_path):
    try:
        os.remove(ticket_path)
        print("[A/B 대기열] 이번 비교를 완료하여 다음 대기 작업에 자리를 넘겼습니다.")
    except OSError:
        pass


def run_queue_and_wait(combos: list, config_dir_names: list, result_prefix: str,
                       max_slots: int = MAX_PARALLEL_SLOTS,
                       session_dir: str = None) -> list:
    """조합 목록을 최대 max_slots(기본 4개)까지만 동시에 실행하고,
    하나가 완료될 때마다 대기열의 다음 조합을 즉시 투입하는 생산 큐 오케스트레이터.
    session_dir: ROOT 기준 상대경로 (ab_results/<session_id>). 지정 시 결과 JSON을 그 안에 저장."""
    worker_path = os.path.join(ROOT, "parallel_dispatch_worker.py")
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    category_model = os.environ.get("AI_CATEGORY_MODEL_PATH")
    if category_model:
        env["AI_CATEGORY_MODEL_PATH"] = category_model

    total = len(combos)
    result_paths = []
    items = []
    for idx, combo in enumerate(combos):
        label = _combo_label(combo)
        if session_dir:
            # ab_results/<session_id>/combo{idx}.json — ROOT 기준 상대경로
            result_path = os.path.join(session_dir, f"combo{idx}.json").replace("\\", "/")
        else:
            result_path = f"_result_{result_prefix}_combo{idx}.json"
        result_paths.append((label, result_path))
        items.append((idx, combo, label, result_path))

    pending_queue = list(items)  # 대기열
    running = {}  # {idx: (label, result_path, proc, start_time)}
    finished = set()

    print(f"\n[오케스트레이터] 총 {total}개 조합 비교 시작 (최대 동시 실행 배럭 슬롯: {max_slots}개)")

    while len(finished) < total:
        # 1. 빈 슬롯이 있고 대기열에 작업이 남아있으면 투입
        while len(running) < max_slots and pending_queue:
            idx, combo, label, result_path = pending_queue.pop(0)
            config_dir_name = config_dir_names[idx]

            full_res = os.path.join(ROOT, result_path)
            if os.path.exists(full_res):
                try:
                    os.remove(full_res)
                except Exception:
                    pass

            if os.name == "nt":
                inner_cmd = subprocess.list2cmdline([sys.executable, worker_path, config_dir_name, label, result_path])
                full_cmd = f'cmd /k call {inner_cmd}'
                proc = subprocess.Popen(
                    full_cmd,
                    cwd=ROOT, env=env, creationflags=subprocess.CREATE_NEW_CONSOLE
                )
            else:
                proc = subprocess.Popen(
                    [sys.executable, worker_path, config_dir_name, label, result_path],
                    cwd=ROOT, env=env
                )

            running[idx] = (label, result_path, proc, time.time())
            print(f"  ▶ [슬롯 투입] 조합 #{idx+1}/{total} '{label}' 시작! (현재 가동 중: {len(running)}/{max_slots})")

        # 2. 실행 중인 프로세스 완료 여부 확인
        just_finished = []
        for idx, (label, result_path, proc, st) in list(running.items()):
            full_res = os.path.join(ROOT, result_path)
            if os.path.exists(full_res):
                elapsed = time.time() - st
                print(f"  ✓ [완료] 조합 #{idx+1}/{total} '{label}' 완료! ({elapsed:.1f}초 소요)")
                just_finished.append(idx)
                finished.add(idx)
            elif proc.poll() is not None:
                time.sleep(1)
                if os.path.exists(full_res):
                    elapsed = time.time() - st
                    print(f"  ✓ [완료] 조합 #{idx+1}/{total} '{label}' 완료! ({elapsed:.1f}초 소요)")
                    just_finished.append(idx)
                    finished.add(idx)

        for idx in just_finished:
            del running[idx]
            if pending_queue:
                print(f"  ➔ 빈 슬롯 확보! 대기 중인 다음 조합 즉시 투입...")

        time.sleep(1.5)

    print(f"\n[오케스트레이터] 모든 {total}개 조합 시뮬레이션 완료!\n")
    return result_paths

def launch_workers(combos: list, session_dir: str = None) -> list:
    """조합마다 parallel_dispatch_worker.py를 새 콘솔에서 동시에 실행.
    session_dir: ROOT 기준 상대경로 (ab_results/<session_id>). 지정 시 결과 JSON을 그 안에 저장."""
    worker_path = os.path.join(ROOT, "parallel_dispatch_worker.py")
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"

    result_paths = []
    print(f"[오케스트레이터] 조합 {len(combos)}개를 새 창에서 동시 측정 시작...")
    for idx, combo in enumerate(combos):
        label = _combo_label(combo)
        if session_dir:
            result_path = os.path.join(session_dir, f"combo{idx}.json").replace("\\", "/")
        else:
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


def save_ranking_text(ranked, results, labels_total, combos=None, session_dir: str = None):
    """최종 순위를 ab_results/<session_id>/ab_ranking_<시각>.txt 로 저장하고 경로를 반환.
    session_dir: ROOT 기준 상대경로. None 이면 ab_results/ 바로 아래에 저장 (구버전 호환)."""
    if session_dir:
        out_dir = os.path.join(ROOT, session_dir)
    else:
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


def compare(result_paths: list, session_dir: str = None):
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
        saved = save_ranking_text(ranked, results, len(result_paths), session_dir=session_dir)
        print(f"\n[오케스트레이터] 최종 순위를 텍스트로 저장했습니다: {saved}")
    except Exception as e:
        print(f"\n[경고] 순위 텍스트 저장 실패: {e}")


def main():
    _install_interrupt_guard()
    if len(sys.argv) < 2:
        print("사용법: python multi_factor_compare.py '<json_combos>'")
        sys.exit(1)

    combos = json.loads(sys.argv[1])
    if not combos:
        print("비교할 조합이 없습니다.")
        sys.exit(1)

    # GUI는 클릭 시점의 설정 스냅샷도 함께 넘긴다. 대기 중 다른 A/B 요청이
    # config.json을 바꿔도, 내 차례에는 이 요청을 만들었던 설정으로 맵을 만든다.
    config_snapshot = json.loads(sys.argv[2]) if len(sys.argv) >= 3 else None
    ticket_path, ticket = _join_ab_queue(combos, config_snapshot)
    try:
        # 대기표 이름은 시간/PID/UUID라서, 매 요청마다 충돌 없는 작업 공간이 된다.
        session_id = os.path.splitext(os.path.basename(ticket_path))[0]
        config_dir_names = [f"sumo_config_ab_{session_id}_combo{idx}" for idx in range(len(combos))]
        result_prefix = f"ab_{session_id}"
        print(f"[A/B 작업 공간] 이번 비교 전용 폴더를 사용합니다: {config_dir_names[0]}")

        if ticket.get("config_snapshot") is not None:
            _save_config_atomically(ticket["config_snapshot"])
            print("[A/B 대기열] 버튼을 누른 시점의 설정을 복원했습니다.")

        run_dates = decide_run_dates(combos)
        print("[오케스트레이터] 이번 비교의 시뮬 날짜: " + ", ".join(sorted({d.isoformat() for d in run_dates})))

        build_shared_map(combos[0], config_dir_names[0])
        for idx, combo in enumerate(combos):
            patch_combo(idx, combo, run_dates[idx], config_dir_names)

        # 최대 4개 슬롯 생산 큐 방식으로 순차 투입 및 완료 대기
        # ab_results/<session_id>/ 하위 폴더를 미리 생성 — 모든 JSON/txt 여기에 모임
        session_dir = os.path.join("ab_results", session_id)
        os.makedirs(os.path.join(ROOT, session_dir), exist_ok=True)
        print(f"[A/B 결과 폴더] ab_results/{session_id}/")

        result_paths = run_queue_and_wait(
            combos, config_dir_names, result_prefix,
            max_slots=MAX_PARALLEL_SLOTS, session_dir=session_dir
        )
        compare(result_paths, session_dir=session_dir)
    finally:
        _leave_ab_queue(ticket_path)

    print("\n[오케스트레이터] 완료. 자동 종료하지 않습니다.")
    input("엔터를 누르면 종료합니다...")


if __name__ == "__main__":
    main()
