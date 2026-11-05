"""
멀티 팩터(taxi_strategy, taxi_dispatch_algorithm 등) 조합을 한 번에 병렬로 비교하는 오케스트레이터.

config_gui.py의 "🧪 A/B 비교하기" 다이얼로그에서 사용자가 고른 조합 목록을 JSON으로 넘기면:
1) 공용 맵을 1회만 생성 (도로망/구역은 taxi_strategy/dispatch_algorithm과 무관하므로 재사용 가능)
2) 조합 개수만큼 폴더를 복제하고, 각 폴더의 runtime_meta.json / simulation.sumocfg에
   그 조합의 값들을 패치
3) 조합마다 새 콘솔 창을 띄워 병렬 측정 (parallel_dispatch_worker.py, 한 비교당 동시 ab_max_parallel_workers개)
4) 모든 결과 JSON이 도착할 때까지 대기 후 순위표로 비교 출력

여러 비교를 동시에 눌러도 안전하게 (KJS 브랜치 개선 이식):
- 대기열: results/ab_queue/<요청ID>.json. 동시에 ab_max_parallel_compares개까지만 실행, 나머지는 대기 (ab_queue.py)
- 요청마다 고유 폴더: module1_simulation/sumo_config_ab_<요청ID>_combo<i>/ (끝나면 삭제),
  결과 JSON은 results/ab_results/<요청ID>/combo<i>.json, 순위표는 results/ab_results/ab_ranking_<요청ID>.txt
- Ctrl+C 한 번은 무시(실수 방지), 5초 안에 두 번 누르면 워커를 끄고 작업 폴더·대기표를 정리한 뒤 중단

사용법:
    python module4_dispatch/experiments/multi_factor_compare.py '[{"taxi_strategy":"patrol","taxi_dispatch_algorithm":"greedy"}, ...]'

config.json은 바뀌지 않는다. combos[0] 값은 results/run_configs/ab_<시각>.json에 저장되고,
build_env와 워커는 MOBILITY_CONFIG로 그 파일을 읽는다. 이 파일은 대기열에 들어가기 전에 만들므로
기다리는 동안 config.json이 바뀌어도 이 비교는 누른 시점의 설정으로 돈다.
"""
import os
import sys
import json
import time
import datetime
import shutil
import signal
import subprocess
import xml.etree.ElementTree as ET

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # 프로젝트 루트 (이 파일은 모듈의 하위 폴더에 있음)
sys.path.insert(0, ROOT)

MODULE1 = os.path.join(ROOT, "module1_simulation")
AB_QUEUE_DIR = os.path.join(ROOT, "results", "ab_queue")       # 요청별 대기표
AB_RESULTS_DIR = os.path.join(ROOT, "results", "ab_results")   # 순위표 + 요청별 결과 JSON 폴더
WORK_DIR_PREFIX = "sumo_config_ab_"                            # module1_simulation/ 아래 요청별 작업 폴더
OWNER_FILE = "ab_owner.json"                                   # 작업 폴더 주인 PID (죽었으면 다음 실행이 정리)

from module4_dispatch.experiments.ab_queue import (AbQueueTicket, new_request_id, owner_alive, owner_info,
                                                   write_json_atomic)

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
    from module1_simulation.holiday_utils import pick_run_date, is_holiday_date, count_existing_logs
    from config_loader import active_config_path
    with open(active_config_path(), "r", encoding="utf-8") as f:
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


def build_shared_map(base_combo: dict, config_dir_name: str, guard=None):
    """combos[0] 값으로 공용 맵을 1회 생성. 설정은 main()이 만든 실행용 설정 파일(MOBILITY_CONFIG)을 쓴다."""
    print(f"[오케스트레이터] 공용 맵 생성 중... (기준 조합: {base_combo})")
    build_env_path = os.path.join(MODULE1, "build_env.py")
    # 새 프로세스 그룹: 콘솔 Ctrl+C가 build_env를 바로 죽이지 않게 (중단 여부는 오케스트레이터가 판단)
    proc = subprocess.Popen([sys.executable, build_env_path, "--config-dir", config_dir_name],
                            cwd=ROOT, **_detached_kwargs())
    if guard:
        guard.children.append(proc)
    if proc.wait() != 0:
        raise RuntimeError(f"build_env.py 실패 (종료 코드 {proc.returncode})")


def patch_combo(idx, combo, run_date=None, config_dir_names=None):
    """이번 요청 전용 폴더(config_dir_names[idx])에 조합 값을 넣는다. 다른 요청의 폴더는 건드리지 않는다."""
    config_dir_names = config_dir_names or [f"sumo_config_combo{i}" for i in range(idx + 1)]
    base_dir = os.path.join(MODULE1, config_dir_names[0])
    dst_dir = os.path.join(MODULE1, config_dir_names[idx])

    if idx != 0:
        if os.path.exists(dst_dir):
            raise RuntimeError(f"이번 요청 전용 폴더가 이미 있습니다 (요청ID 충돌?): {dst_dir}")
        shutil.copytree(base_dir, dst_dir)

    meta_path = os.path.join(dst_dir, "runtime_meta.json")
    with open(meta_path, "r", encoding="utf-8") as f:
        meta = json.load(f)
    meta.update(combo)
    if run_date is not None:
        # measure_wait_time.py가 이 값이 있으면 날짜를 새로 뽑지 않고 그대로 사용
        meta["run_date"] = run_date.isoformat() if hasattr(run_date, "isoformat") else str(run_date)
    write_json_atomic(meta_path, meta)

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


def _detached_kwargs():
    """콘솔 Ctrl+C가 자식에게 직접 가지 않게 하는 Popen 옵션 (Windows: 새 프로세스 그룹, 그 외: 새 세션)."""
    if os.name == "nt":
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    return {"start_new_session": True}


def _kill_tree(proc):
    """워커 창(cmd /k)과 그 안의 python·SUMO까지 함께 종료."""
    if proc.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except OSError:
            proc.kill()


def _start_worker(worker_path, config_dir_name, label, result_path, env):
    args = [sys.executable, worker_path, config_dir_name, label, result_path]
    if os.name == "nt":
        # 새 콘솔이라 원래 창의 Ctrl+C가 워커에 가지 않는다. 창은 결과를 보도록 열어 둔다(cmd /k).
        return subprocess.Popen(f"cmd /k call {subprocess.list2cmdline(args)}",
                                cwd=ROOT, env=env, creationflags=subprocess.CREATE_NEW_CONSOLE)
    return subprocess.Popen(args, cwd=ROOT, env=env, start_new_session=True)


def run_queue_and_wait(combos: list, config_dir_names: list, session_dir: str,
                       max_slots: int = 4, guard=None) -> list:
    """조합을 최대 max_slots개까지만 동시에 실행하고, 하나 끝날 때마다 다음 조합을 투입 (KJS 방식).
    결과는 session_dir(ROOT 기준)/combo<i>.json. 반환: [(라벨, 결과 경로)]"""
    worker_path = os.path.join(ROOT, "module4_dispatch", "experiments", "parallel_dispatch_worker.py")
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    env["PYTHONHASHSEED"] = "0"  # 실행 재현성: 같은 조건이면 같은 결과 (reproducibility.py)

    total = len(combos)
    result_paths = [(_combo_label(c), os.path.join(session_dir, f"combo{i}.json").replace("\\", "/"))
                    for i, c in enumerate(combos)]
    pending = list(range(total))
    running = {}   # idx -> (proc, 시작 시각, 창이 닫힌 걸 처음 본 시각)
    finished = set()
    max_slots = max(1, int(max_slots))
    print(f"[오케스트레이터] 조합 {total}개 측정 시작 (이 비교에서 동시에 최대 {max_slots}개 창)")

    while len(finished) < total:
        while len(running) < max_slots and pending:
            idx = pending.pop(0)
            label, result_path = result_paths[idx]
            proc = _start_worker(worker_path, config_dir_names[idx], label, result_path, env)
            if guard:
                guard.children.append(proc)
            running[idx] = (proc, time.time(), None)
            print(f"  ▶ 조합 #{idx + 1}/{total} '{label}' 시작 (가동 {len(running)}/{max_slots})")

        for idx, (proc, t0, closed_at) in list(running.items()):
            label, result_path = result_paths[idx]
            if os.path.exists(os.path.join(ROOT, result_path)):
                print(f"  ✓ 조합 #{idx + 1}/{total} '{label}' 완료 ({time.time() - t0:.0f}초)")
            elif proc.poll() is not None:
                # 워커 창이 결과 없이 닫힘(사용자가 닫음/충돌). 파일 기록이 늦을 수 있어 3초 더 기다린다.
                if closed_at is None:
                    running[idx] = (proc, t0, time.time())
                    continue
                if time.time() - closed_at < 3:
                    continue
                print(f"  ✗ 조합 #{idx + 1}/{total} '{label}' 결과 없이 창이 닫혔습니다 — 측정 실패로 처리")
                write_json_atomic(os.path.join(ROOT, result_path),
                                  {"error": "워커 창이 결과 없이 종료됨", "true_avg_wait_sec_with_penalty": None})
            else:
                continue
            finished.add(idx)
            del running[idx]
        time.sleep(1.5)

    print(f"[오케스트레이터] 조합 {total}개 측정 완료")
    return result_paths


def _day_type(iso: str, is_holiday=None) -> str:
    if is_holiday is None:
        try:
            from module1_simulation.holiday_utils import is_holiday_date
            is_holiday = is_holiday_date(datetime.date.fromisoformat(iso))
        except Exception:
            return ""
    return "공휴일/주말" if is_holiday else "평일"


def save_ranking_text(ranked, results, labels_total, combos=None, request_id=None):
    """최종 순위를 results/ab_results/ab_ranking_<요청ID>.txt 로 저장하고 경로를 반환.
    요청ID가 시각으로 시작해서 예전 ab_ranking_<시각>.txt와 같은 순서로 정렬되고, 동시 실행끼리 겹치지 않는다."""
    os.makedirs(AB_RESULTS_DIR, exist_ok=True)
    now = datetime.datetime.now()
    path = os.path.join(AB_RESULTS_DIR, f"ab_ranking_{request_id or now.strftime('%Y%m%d_%H%M%S')}.txt")

    lines = []
    lines.append("=" * 64)
    lines.append(" A/B 비교 최종 순위")
    lines.append("=" * 64)
    lines.append(f" 실행 시각 : {now.strftime('%Y-%m-%d %H:%M:%S')}")
    if request_id:
        lines.append(f" 요청 ID   : {request_id} (조합별 결과 JSON: results/ab_results/{request_id}/)")
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


def compare(result_paths: list, request_id=None):
    from measure_wait_time import print_result

    metrics = {}
    results = {}
    for label, path in result_paths:
        with open(os.path.join(ROOT, path), "r", encoding="utf-8") as f:
            result = json.load(f)
        if result.get("error"):
            print(f"\n===== [{label}] 측정 실패: {result['error']} =====")
        else:
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
        saved = save_ranking_text(ranked, results, len(result_paths), request_id=request_id)
        print(f"\n[오케스트레이터] 최종 순위를 텍스트로 저장했습니다: {saved}")
    except Exception as e:
        print(f"\n[경고] 순위 텍스트 저장 실패: {e}")


class InterruptGuard:
    """Ctrl+C/Break 한 번은 무시(다른 창에 누른 키가 잘못 와도 비교가 깨지지 않게), window초 안에 두 번이면 중단.
    중단하면 main()의 finally가 워커 창 종료 → 작업 폴더 삭제 → 대기표 삭제를 한다."""

    def __init__(self, window=5.0):
        self.window = window
        self.last = 0.0
        self.children = []   # 중단·실패 시 함께 끌 자식 프로세스
        self._old = {}

    def _handler(self, signum, frame):
        now = time.time()
        if now - self.last <= self.window:
            raise KeyboardInterrupt
        self.last = now
        print(f"\n[보호] Ctrl+C를 받았지만 A/B 비교는 계속합니다. 정말 중단하려면 {self.window:.0f}초 안에 한 번 더 누르세요 "
              "(워커 창을 닫고 작업 폴더·대기표를 정리합니다).", flush=True)

    def install(self):
        for name in ("SIGINT", "SIGBREAK"):
            if hasattr(signal, name):
                sig = getattr(signal, name)
                self._old[sig] = signal.signal(sig, self._handler)

    def uninstall(self):
        for sig, old in self._old.items():
            signal.signal(sig, old)
        self._old.clear()

    def kill_children(self):
        for proc in self.children:
            try:
                _kill_tree(proc)
            except Exception:
                pass


def _remove_dir(path, tries=5):
    """SUMO가 막 끝난 직후엔 파일이 잠겨 있을 수 있어 몇 번 재시도."""
    for i in range(tries):
        shutil.rmtree(path, ignore_errors=True)
        if not os.path.exists(path):
            return True
        if i + 1 < tries:
            time.sleep(2)
    return False


def sweep_stale_work_dirs():
    """주인(오케스트레이터)이 죽은 채 남은 module1_simulation/sumo_config_ab_* 폴더를 정리."""
    for name in os.listdir(MODULE1):
        path = os.path.join(MODULE1, name)
        if not (name.startswith(WORK_DIR_PREFIX) and os.path.isdir(path)):
            continue
        owner_path = os.path.join(path, OWNER_FILE)
        try:
            with open(owner_path, encoding="utf-8") as f:
                alive = owner_alive(json.load(f))
        except (OSError, json.JSONDecodeError):
            # 주인 표시가 아직 없음: 막 만들어지는 중일 수 있으니 1시간 넘은 것만 정리
            alive = time.time() - os.path.getmtime(path) < 3600
        if not alive and _remove_dir(path, tries=1):
            print(f"[A/B 정리] 끝난 비교가 남긴 작업 폴더를 지웠습니다: module1_simulation/{name}")


def _setting(key, default, env_key):
    """새 설정은 CFG.get(key, default). 같은 이름의 환경변수(env_key)가 있으면 그것이 우선 (테스트·임시 조정용)."""
    from config_loader import CFG
    v = os.environ.get(env_key)
    if v in (None, ""):
        v = CFG.get(key, default)
    if isinstance(default, bool):
        return v if isinstance(v, bool) else str(v).strip().lower() in ("1", "true", "yes", "y")
    return type(default)(v)


def main():
    if len(sys.argv) < 2:
        print("사용법: python multi_factor_compare.py '<json_combos>'")
        sys.exit(1)

    combos = json.loads(sys.argv[1])
    if not combos:
        print("비교할 조합이 없습니다.")
        sys.exit(1)

    from config_loader import use_run_config
    # 대기열에 들어가기 '전'에 이번 비교의 설정 파일을 만든다 → 기다리는 동안 config.json이 바뀌어도 영향 없음.
    # holiday_mode(평일/공휴일 비교)는 조합별로 runtime_meta에 따로 넣으므로 공용 설정에는 넣지 않음
    cfg_path = use_run_config({k: v for k, v in combos[0].items() if k != "holiday_mode"}, "ab")
    print(f"[오케스트레이터] 이번 비교 설정: {os.path.relpath(cfg_path, ROOT)} (config.json은 변경 안 함)")

    max_compares = _setting("ab_max_parallel_compares", 4, "AB_MAX_PARALLEL_COMPARES")
    max_workers = _setting("ab_max_parallel_workers", 4, "AB_MAX_PARALLEL_WORKERS")
    stale_sec = _setting("ab_queue_stale_sec", 1800, "AB_QUEUE_STALE_SEC")
    keep_dirs = _setting("ab_keep_work_dirs", False, "AB_KEEP_WORK_DIRS")

    request_id = new_request_id()
    config_dir_names = [f"{WORK_DIR_PREFIX}{request_id}_combo{i}" for i in range(len(combos))]
    session_dir = os.path.join("results", "ab_results", request_id)
    print(f"[오케스트레이터] 요청 ID: {request_id}")

    guard = InterruptGuard()
    guard.install()
    ok = False
    try:
        sweep_stale_work_dirs()
        with AbQueueTicket(AB_QUEUE_DIR, request_id, max_compares, stale_sec,
                           extra={"combo_count": len(combos), "config": os.path.relpath(cfg_path, ROOT)}) as ticket:
            ticket.wait_turn()

            base_dir = os.path.join(MODULE1, config_dir_names[0])
            os.makedirs(base_dir)
            write_json_atomic(os.path.join(base_dir, OWNER_FILE), {**owner_info(), "request_id": request_id})
            os.makedirs(os.path.join(ROOT, session_dir), exist_ok=True)
            print(f"[A/B 작업 공간] module1_simulation/{WORK_DIR_PREFIX}{request_id}_combo* "
                  f"/ 결과: {session_dir.replace(os.sep, '/')}/")

            run_dates = decide_run_dates(combos)
            print("[오케스트레이터] 이번 비교의 시뮬 날짜: " + ", ".join(sorted({d.isoformat() for d in run_dates})))

            build_shared_map(combos[0], config_dir_names[0], guard)
            for idx, combo in enumerate(combos):
                patch_combo(idx, combo, run_dates[idx], config_dir_names)

            ticket.set_state("measuring")
            result_paths = run_queue_and_wait(combos, config_dir_names, session_dir, max_workers, guard)
            compare(result_paths, request_id)
            ok = True
    except KeyboardInterrupt:
        print("\n[중단] 사용자가 A/B 비교를 중단했습니다. 워커를 끄고 정리합니다...")
    finally:
        if not ok:
            guard.kill_children()   # 실패·중단: 남은 워커 창/SUMO 종료 (정상 완료면 창은 결과 확인용으로 둠)
        if not keep_dirs:
            left = [d for d in config_dir_names
                    if os.path.exists(os.path.join(MODULE1, d)) and not _remove_dir(os.path.join(MODULE1, d))]
            if left:
                print(f"[경고] 작업 폴더 {len(left)}개를 지우지 못했습니다. 다음 A/B 비교 때 다시 정리합니다.")
        guard.uninstall()

    if not ok:
        sys.exit(130)
    print("\n[오케스트레이터] 완료. 자동 종료하지 않습니다.")
    try:
        input("엔터를 누르면 종료합니다...")
    except (EOFError, KeyboardInterrupt):
        pass


if __name__ == "__main__":
    main()
