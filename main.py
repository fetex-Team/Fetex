"""AI Mobility 실행 진입점 — 설정 파일을 받아 시뮬레이션·학습·비교를 순서대로 실행한다.

설정 GUI(config_gui.py)는 값만 편집하고, 실행 버튼은 화면 값을 실행용 설정 파일로 저장한 뒤 이 파일을 부른다.
터미널에서도 같은 명령으로 실행할 수 있다.

    python main.py play                         지도 생성 → SUMO GUI 시뮬레이션 (results/simulation/metrics.json)
    python main.py train                        지도 생성 → (로그 생성) → 수요예측·카테고리 모델 → demand_v2 변환 → RL
    python main.py compare '<조합 JSON>' [--model 카테고리모델.pkl]   A/B 병렬 비교 (scripts/multi_factor_compare.py)
    python main.py unity [--strategies ... --algorithm ... --stride N --unity-dir 경로]   Unity 재생 파일
    python main.py analyze --logs <로그 CSV ...>   수요 로그 리포트 (tools/analyze_demand_logs.py)
    python main.py analyzer                     수요 로그 분석기(브라우저) 서버

공통 옵션:
    --config 경로   이 설정 파일로 실행 (없으면 config.json, 그것도 없으면 presets/default/기본.json)
    --status 경로   진행 상황을 JSON으로 기록 (GUI가 1초마다 읽어 상태 줄·완료 팝업에 표시)
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import zipfile
from datetime import datetime

ROOT = os.path.dirname(os.path.abspath(__file__))
SAVED = os.path.join(ROOT, "saved_models")

for _stream in (sys.stdout, sys.stderr):
    if _stream and hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")


# ---------------------------------------------------------------- 진행 상황 기록
class Status:
    """진행 상황을 콘솔에 출력하고, --status 파일이 있으면 JSON으로도 기록한다.
    state: running → done | error. GUI는 이 파일을 읽어 상태 줄과 완료·오류 팝업을 띄운다."""

    def __init__(self, path, command):
        self.path = path
        self.data = {"command": command, "state": "running", "step": "", "text": "",
                     "started": datetime.now().isoformat(timespec="seconds"), "pid": os.getpid()}
        self._write()

    def _write(self):
        if not self.path:
            return
        os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.data, f, ensure_ascii=False, indent=1)
        os.replace(tmp, self.path)   # GUI가 반쯤 쓴 파일을 읽지 않게 한 번에 교체

    def step(self, step, text):
        print(f"\n[진행 {step}] {text}" if step else f"\n[진행] {text}", flush=True)
        self.data.update(step=step, text=text)
        self._write()

    def done(self, summary=""):
        print(f"\n[완료] {summary}" if summary else "\n[완료]", flush=True)
        self.data.update(state="done", summary=summary, finished=datetime.now().isoformat(timespec="seconds"))
        self._write()

    def error(self, message):
        print(f"\n[실패] {message}", flush=True)
        self.data.update(state="error", error=message, finished=datetime.now().isoformat(timespec="seconds"))
        self._write()


class StepFailed(Exception):
    pass


def run_script(script, args=(), env_extra=None, check=True):
    """프로젝트 스크립트를 같은 콘솔에서 실행. check=True면 실패 시 StepFailed. 반환: 성공 여부."""
    env = {**os.environ, **{str(k): str(v) for k, v in (env_extra or {}).items()}}
    rc = subprocess.run([sys.executable, os.path.join(ROOT, script), *args], cwd=ROOT, env=env).returncode
    if rc != 0 and check:
        raise StepFailed(f"{script} 실패 (종료 코드 {rc})")
    return rc == 0


def load_run_config():
    """이번 실행 설정 = 팀 기본값 + 설정 파일 (지역 좌표·실시간 날씨 조회는 하지 않음)."""
    from config_loader import DEFAULT_CONFIG, read_user_config
    return {**DEFAULT_CONFIG, **read_user_config()}


# ---------------------------------------------------------------- play
def cmd_play(status, args, extra):
    if not args.no_build:
        status.step("1/2", "지도·SUMO 설정 생성 중 (build_env.py)")
        run_script(os.path.join("module1_simulation", "build_env.py"))
    status.step("2/2", "SUMO 시뮬레이션 실행 중 (창을 닫으면 그때까지의 결과를 저장)")
    run_script(os.path.join("module1_simulation", "run_simulation.py"), extra)
    summary = ""
    try:
        with open(os.path.join(ROOT, "results", "simulation", "metrics.json"), encoding="utf-8") as f:
            r = json.load(f)
        summary = (f"승객 {r['n_total_passengers']}명 중 {r['n_measured']}명 탑승, "
                   f"평균 대기 {r['avg_wait_sec']}초 (results/simulation/metrics.json)")
    except Exception:
        pass
    status.done(summary)


# ---------------------------------------------------------------- train
def backup_saved_models(rl_total_timesteps, skip_v2=False):
    """train.py가 saved_models/를 덮어쓰기 전에 날짜·시각 폴더로 통째로 백업 (짧은 테스트 실행이면 생략)."""
    if rl_total_timesteps < 10000:
        return f"백업 생략 (rl_total_timesteps={rl_total_timesteps} < 10000, 테스트성 실행)"
    if not os.path.isdir(SAVED) or not os.listdir(SAVED):
        return "백업 생략 (기존 모델 없음)"
    dst = os.path.join(ROOT, "saved_models_backups", datetime.now().strftime("%Y%m%d_%H%M%S"))
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    # skip_v2: 새 학습 라인이 demand_v2.joblib을 쓰는 중이면 반쪽짜리 파일을 복사하지 않도록 제외
    shutil.copytree(SAVED, dst, ignore=shutil.ignore_patterns("demand_v2.joblib") if skip_v2 else None)
    return f"기존 모델을 {os.path.relpath(dst, ROOT)}에 백업"


def start_v2_line(force=False):
    """[새 학습 라인] demand_v2.joblib이 없으면(또는 force) scripts/train_dispatch_model.py를 백그라운드로 시작.
    반환: (Popen 또는 None, 'skip' | 'started' | 'no_data' | 'error:...')"""
    v2 = os.path.join(SAVED, "demand_v2.joblib")
    if os.path.exists(v2) and not force:
        return None, "skip"
    calls = os.path.join("data", "generated", "calls.csv")
    ext = os.path.join("data", "generated", "external.csv")
    if not (os.path.exists(os.path.join(ROOT, calls)) and os.path.exists(os.path.join(ROOT, ext))):
        return None, "no_data"
    try:
        if os.path.exists(v2):  # 강제 재생성: 덮어쓰기 전에 기존 파일 보존
            bak = os.path.join(ROOT, "demand_v2_backups", f"demand_v2_{datetime.now().strftime('%Y%m%d_%H%M%S')}.joblib")
            os.makedirs(os.path.dirname(bak), exist_ok=True)
            shutil.copy2(v2, bak)
            print(f"[새 학습 라인] 강제 재생성: 기존 demand_v2.joblib을 {os.path.relpath(bak, ROOT)} 에 백업했습니다.")
        os.makedirs(os.path.join(ROOT, "logs"), exist_ok=True)
        with open(os.path.join(ROOT, "logs", "train_v2.log"), "w", encoding="utf-8") as logf:
            proc = subprocess.Popen(
                [sys.executable, os.path.join(ROOT, "scripts", "train_dispatch_model.py"), calls, "--external", ext],
                cwd=ROOT, stdout=logf, stderr=subprocess.STDOUT,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
    except Exception as e:
        return None, f"error:{e}"
    return proc, "started"


def v2_cell_warning():
    """demand_v2.joblib의 H3 셀과 방금 만든 지도(runtime_meta.json)의 셀이 다르면 경고 문구, 아니면 None."""
    try:
        r = subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "convert_v2_to_category.py"), "--check-cells"],
                           cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
    except Exception:
        return None
    return r.stdout.strip() if r.returncode == 3 and r.stdout.strip() else None


def finish_v2_line(proc, state):
    """새 라인이 만든 demand_v2.joblib을 category_demand_from_v2.pkl로 변환. 반환: 결과 문구."""
    if state == "no_data":
        return "변환 생략 - demand_v2.joblib도, 학습 데이터(data/generated/calls.csv, external.csv)도 없음"
    if state.startswith("error"):
        return f"변환 생략 - 새 라인 시작 실패 ({state})"
    if state == "started":
        rc = proc.wait()
        if rc != 0 or not os.path.exists(os.path.join(SAVED, "demand_v2.joblib")):
            return f"변환 생략 - 새 학습 라인 실패 (종료코드 {rc}), logs/train_v2.log 확인"
    cell_warn = v2_cell_warning()
    if not run_script(os.path.join("scripts", "convert_v2_to_category.py"), check=False):
        return "변환 실패 (콘솔 출력 참고)"
    msg = "saved_models/category_demand_from_v2.pkl 생성"
    if state == "skip":
        msg += " (demand_v2.joblib이 이미 있어 새 학습은 SKIP)"
    if cell_warn:
        msg += f"\n   [경고] {cell_warn}"
    return msg


def backup_rl_model():
    """RL 모델 zip의 실제 종료 step이 10,000 이상일 때만 rl_model_backups/에 보존."""
    model = os.path.join(ROOT, "module4_dispatch", "rl_reposition_model.zip")
    try:
        with zipfile.ZipFile(model) as z:
            steps = int(json.loads(z.read("data").decode("utf-8"))["num_timesteps"])
    except Exception as e:
        return f"백업 생략 - 실제 종료 step 확인 불가 ({e})"
    if steps < 10000:
        return f"백업 생략 - 실제 종료 step {steps:,} < 10,000"
    dst_dir = os.path.join(ROOT, "rl_model_backups")
    os.makedirs(dst_dir, exist_ok=True)
    dst = os.path.join(dst_dir, f"rl_reposition_model_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{steps}steps.zip")
    shutil.copy2(model, dst)
    return f"실제 종료 step {steps:,} >= 10,000, rl_model_backups/{os.path.basename(dst)} 에 보존"


def cmd_train(status, args, extra):
    c = load_run_config()

    # [새 학습 라인] 기존 라인을 기다리지 않고 지금 바로 동시에 시작
    v2_proc, v2_state = start_v2_line(bool(c.get("force_regenerate_demand_v2", False)))
    print({
        "skip": "[새 학습 라인] demand_v2.joblib이 이미 있어 SKIP합니다.",
        "no_data": "[새 학습 라인] data/generated/calls.csv 또는 external.csv가 없어 시작하지 못했습니다.",
        "started": "[새 학습 라인] 백그라운드로 시작했습니다. 진행 상황은 logs/train_v2.log 에서 확인하세요.",
    }.get(v2_state, f"[새 학습 라인] 시작 실패: {v2_state}"))

    try:
        status.step("0/4", "지도·SUMO 설정 생성 중 (build_env.py)")
        run_script(os.path.join("module1_simulation", "build_env.py"))
        if v2_state == "skip":   # v2를 SKIP했으면 방금 만든 지도와 셀이 맞는지 바로 알림
            warn = v2_cell_warning()
            if warn:
                print("[경고] demand_v2.joblib 학습을 SKIP했지만 " + warn)

        if c.get("run_sim_before_training", True):
            from module2_preprocessing import log_selection
            t0 = time.time()
            n_runs = 3
            for i in range(n_runs):
                status.step("0.5/4", f"시뮬레이션으로 학습 로그 생성 중 ({i + 1}/{n_runs})")
                run_script("measure_wait_time.py")
            added = log_selection.add_new_logs_to_selection(t0)
            if added:
                print(f"[로그 선택] 방금 만든 로그 {len(added)}개를 학습 선택 목록에 추가했습니다: {', '.join(added)}")
        else:
            print("[안내] 학습 전 시뮬레이션 생략 (기존 로그로 학습)")

        status.step("1/4", "기존 모델 백업 후 수요예측 모델 학습 중 (train.py)")
        print(backup_saved_models(int(c.get("rl_total_timesteps", 20000)),
                                  skip_v2=(v2_proc is not None and v2_proc.poll() is None)))
        train_ok = run_script(os.path.join("module3_prediction", "train.py"), check=False)

        # 기존 category_demand_models.pkl은 보존하고, 이번 학습 결과는 별도 파일로 저장한다.
        category_new = os.path.join(SAVED, "category_demand_models_new.pkl")
        status.step("2/4", "카테고리 수요 모델 학습 중 (train_category_demand.py)")
        run_script(os.path.join("module3_prediction", "train_category_demand.py"), ("--out", category_new))

        # demand_v2 변환을 RL 앞에 둔다 (변환이 RL 학습보다 먼저 끝나야 함)
        status.step("3/4", "새 학습 라인(demand_v2) 완료 대기 + 카테고리 모델로 변환 중")
        v2_msg = finish_v2_line(v2_proc, v2_state)
    except BaseException:
        if v2_proc is not None and v2_proc.poll() is None:
            v2_proc.terminate()   # 중간에 멈추면 백그라운드 학습도 같이 정리
        raise

    # RL은 v2 변환본이 아니라 카테고리별로 정상 학습된 모델(train_category_demand.py 결과)을 참고한다.
    status.step("4/4", "강화학습 실행 중 (rl_train.py)")
    rl_ok = run_script(os.path.join("module4_dispatch", "rl_train.py"), check=False,
                       env_extra={"AI_CATEGORY_MODEL_PATH": category_new})
    rl_backup = backup_rl_model() if rl_ok else "백업 생략 - RL 학습이 정상 종료되지 않음"

    # 이번 라운드 RL이 참고한 카테고리 모델을 고정 이름으로 승격 → 바로 [플레이]해도 같은 수요예측을 쓴다.
    active = os.path.join(SAVED, "category_demand_models.pkl")
    if rl_ok:
        shutil.copy2(category_new, active)
        promote = "category_demand_models.pkl <- category_demand_models_new.pkl (승격 완료)"
    else:
        promote = "승격 생략 - RL 학습이 정상 종료되지 않음 (기존 category_demand_models.pkl 유지)"

    status.done(
        "생성/갱신 파일 (saved_models/):\n"
        "• category_demand_models.pkl (활성)\n"
        "• category_demand_models_new.pkl / category_demand_from_v2.pkl (이번 라운드 원본)\n"
        "• rl_reposition_model.zip (module4_dispatch/)\n\n"
        + ("" if train_ok else "[경고] train.py(Module 3 평가용 모델) 학습 실패 — 콘솔 출력 확인\n")
        + f"[승격] {promote}\n"
        f"[RL 백업] {rl_backup}\n"
        f"[demand_v2 변환] {v2_msg}")


# ---------------------------------------------------------------- 나머지 (스크립트 그대로 실행)
def _passthrough(script, text, env_extra=None):
    def run(status, args, extra):
        status.step("", text)
        run_script(script, extra, env_extra=env_extra(args) if env_extra else None)
        status.done()
    return run


COMMANDS = {
    "play": cmd_play,
    "train": cmd_train,
    "compare": _passthrough(os.path.join("scripts", "multi_factor_compare.py"), "A/B 비교 실행 중",
                            env_extra=lambda a: {"AI_CATEGORY_MODEL_PATH": a.model} if a.model else {}),
    "unity": _passthrough(os.path.join("tools", "export_unity_replay.py"), "Unity 재생 파일 생성 중"),
    "analyze": _passthrough(os.path.join("tools", "analyze_demand_logs.py"), "수요 로그 리포트 생성 중"),
    "analyzer": _passthrough(os.path.join("tools", "analyzer_server.py"), "수요 로그 분석기(브라우저) 실행 중 — 끝내려면 이 창을 닫기"),
}


def main(argv=None):
    p = argparse.ArgumentParser(description="AI Mobility 실행 진입점", allow_abbrev=False,
                                epilog="나머지 인자는 각 스크립트로 그대로 전달됩니다.")
    p.add_argument("command", choices=list(COMMANDS), nargs="?", default="play")
    p.add_argument("--config", help="실행 설정 파일 (기본: config.json → presets/default/기본.json)")
    p.add_argument("--status", help="진행 상황 JSON 파일 (GUI용)")
    p.add_argument("--model", help="compare: rl_prepositioned가 참고할 카테고리 수요 모델(.pkl)")
    p.add_argument("--no-build", action="store_true", help="play: 지도 생성을 건너뛰고 기존 지도로 실행")
    args, extra = p.parse_known_args(argv)

    # 자식 프로세스 공통 환경: 설정 파일·한글 출력·실행 재현성(reproducibility.py)
    if args.config:
        os.environ["MOBILITY_CONFIG"] = os.path.abspath(args.config)
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    os.environ.setdefault("PYTHONUTF8", "1")
    os.environ.setdefault("PYTHONHASHSEED", "0")
    if ROOT not in sys.path:
        sys.path.insert(0, ROOT)

    print(f"=== AI Mobility — {args.command} ===")
    print(f"[안내] 설정 파일: {os.environ.get('MOBILITY_CONFIG') or 'config.json (없으면 presets/default/기본.json)'}")
    status = Status(args.status, args.command)
    try:
        COMMANDS[args.command](status, args, extra)
    except StepFailed as e:
        status.error(str(e))
        return 1
    except KeyboardInterrupt:
        status.error("사용자가 중단했습니다 (Ctrl+C)")
        return 130
    except Exception as e:
        status.error(f"{type(e).__name__}: {e}")
        raise
    return 0


if __name__ == "__main__":
    sys.exit(main())
