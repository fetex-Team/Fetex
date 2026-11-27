"""택시 55대 재학습·비교 실험 (보상 방식 default / gravityVer, 9만 스텝).

순서 (각 단계는 따로 실행해도 되고, 중간에 멈췄다가 다시 실행하면 끝난 부분은 건너뛴다):
    python taxi55_experiment/taxi55.py train      # 1) RL 학습 2번 (default → gravityVer), 각 약 5~6시간
    python taxi55_experiment/taxi55.py pick       # 2) 학습 중간 저장본 중 가장 좋은 것 고르기 (검증용 3일)
    python taxi55_experiment/taxi55.py compare    # 3) 고정 7일 비교 (순찰·선배치·예측 재배치·RL 3종)
    python taxi55_experiment/taxi55.py report     # 4) 결과 표 한 장으로 합치기 → taxi55_experiment/결과_7일비교.md

옵션: train --only default|gravityVer, --jobs N (동시에 돌릴 시뮬레이션 수, 기본 4)

모델 세트는 saved_models_backups/ 아래에 둔다 (설정 GUI의 A/B 창에서도 골라 쓸 수 있음):
    taxi55_default, taxi55_gravityVer          학습이 끝난 최종 모델
    taxi55_default_best, taxi55_gravityVer_best 중간 저장본 중 검증 성적이 가장 좋은 모델
    kjs_best1111                               KJS 최고 모델 (origin/KJS에서 가져옴)
"""
import argparse
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import zipfile
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PY = sys.executable
MODES = ("default", "gravityVer")
BACKUPS = os.path.join(ROOT, "saved_models_backups")
RL_DIR = os.path.join(ROOT, "module4_dispatch")
CKPT_SRC = os.path.join(RL_DIR, "checkpoints")
CKPT_DST = os.path.join(HERE, "checkpoints")

# 최종 비교용 고정 7일 (팀 README·twin_v3와 같은 날짜)
TEST_DATES = ["2026-09-07", "2026-09-10", "2026-09-18", "2026-10-13", "2026-10-14", "2026-10-18", "2026-10-24"]
# 저장본 고르기용 검증 3일: RL 학습 기간(~09-06) 이후, 최종 7일과 겹치지 않는 평일, 공휴일·추석 제외
VAL_DATES = ["2026-09-15", "2026-09-30", "2026-10-21"]
# 고를 저장본 (가장 가까운 스텝의 저장본을 씀) + 학습이 끝난 최종 모델
PICK_STEPS = [30000, 50000, 70000, 90000]
# 고르는 기준: 타임아웃 벌점을 포함한 평균 대기(낮을수록 좋음). 놓친 손님과 대기를 한 숫자로 본다
PICK_METRIC = "true_avg_wait_sec_with_penalty"


def cfg_path(mode):
    return os.path.join(HERE, f"config_{mode}.json")


def run_env(mode="default", extra=None):
    """가상환경을 켠 것과 같은 환경 (SUMO 실행 파일 경로 포함) + 실험 설정 파일."""
    scripts = os.path.dirname(PY)
    env = dict(os.environ)
    env["PATH"] = scripts + os.pathsep + env.get("PATH", "")
    env["VIRTUAL_ENV"] = os.path.dirname(scripts)
    try:
        import sumo
        env.setdefault("SUMO_HOME", sumo.SUMO_HOME)
    except ImportError:
        pass
    env.update({"MOBILITY_CONFIG": cfg_path(mode), "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8",
                "PYTHONHASHSEED": "0"})
    for k in ("AI_RL_MODEL_PATH", "AI_CATEGORY_MODEL_PATH", "AI_DEMAND_V2_PATH"):
        env.pop(k, None)
    env.update(extra or {})
    return env


def say(msg):
    print(f"\n[taxi55 {datetime.now():%H:%M}] {msg}", flush=True)


def model_steps(zip_path):
    try:
        with zipfile.ZipFile(zip_path) as z:
            return int(json.loads(z.read("data").decode("utf-8"))["num_timesteps"])
    except Exception:
        return None


def write_meta(dst, mode, label, **extra):
    c = json.load(open(cfg_path(mode), encoding="utf-8"))
    meta = {k: c.get(k) for k in ("region", "num_taxis", "rl_reward_mode", "rl_train_start", "rl_train_end",
                                  "rl_train_day_type", "rl_total_timesteps", "demand_source",
                                  "real_demand_scale", "freq", "h3_resolution")}
    meta.update({"label": label, "trained_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), **extra})
    with open(os.path.join(dst, "model_meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)


# ------------------------------------------------------------------ 1) 학습
def train_one(mode):
    final_dir = os.path.join(BACKUPS, f"taxi55_{mode}")
    if os.path.exists(os.path.join(final_dir, "rl_reposition_model.zip")):
        say(f"{mode}: 이미 학습됨 ({final_dir}) — 건너뜀. 다시 하려면 그 폴더를 지우세요.")
        return
    # 앞 학습의 중간 저장본이 섞이지 않게 비워 둔다 (지우지 않고 옮김)
    if os.path.isdir(CKPT_SRC) and os.listdir(CKPT_SRC):
        old = os.path.join(CKPT_DST, f"_이전_{datetime.now():%Y%m%d_%H%M%S}")
        os.makedirs(CKPT_DST, exist_ok=True)
        shutil.move(CKPT_SRC, old)
        say(f"이전 중간 저장본을 {old}로 옮겼습니다")

    say(f"{mode}: RL 학습 시작 (택시 55대, 9만 스텝, 약 5~6시간)")
    started = datetime.now().timestamp()
    rc = subprocess.call([PY, os.path.join(ROOT, "main.py"), "train", "--rl-only"], cwd=ROOT, env=run_env(mode))
    model = os.path.join(RL_DIR, "rl_reposition_model.zip")
    steps = model_steps(model)
    if rc != 0 or not os.path.exists(model) or os.path.getmtime(model) < started or (steps or 0) < 89000:
        raise SystemExit(f"[중단] {mode} 학습이 정상적으로 끝나지 않았습니다 (종료 코드 {rc}, 모델 스텝 {steps}). "
                         f"위 콘솔 출력을 확인하세요.")

    # 이번 학습 결과를 모델 세트로 보관
    os.makedirs(final_dir, exist_ok=True)
    saved = os.path.join(ROOT, "saved_models")
    for f in ("demand_v2.joblib", "category_demand_models.pkl", "cnn_lstm_demand.pt"):
        if os.path.exists(os.path.join(saved, f)):
            shutil.copy2(os.path.join(saved, f), os.path.join(final_dir, f))
    for f in ("rl_reposition_model.zip", "rl_reposition_model_vecnorm.pkl"):
        if os.path.exists(os.path.join(RL_DIR, f)):
            shutil.copy2(os.path.join(RL_DIR, f), os.path.join(final_dir, f))
    write_meta(final_dir, mode, f"택시55_{mode}_최종", rl_steps=steps)
    # 중간 저장본과 학습 기록도 함께 보관
    dst = os.path.join(CKPT_DST, mode)
    if os.path.isdir(dst):
        shutil.rmtree(dst)
    if os.path.isdir(CKPT_SRC):
        shutil.move(CKPT_SRC, dst)
    logs = sorted(glob.glob(os.path.join(RL_DIR, "training_logs", "2*")))
    if logs:
        shutil.copytree(logs[-1], os.path.join(HERE, "training_logs", mode), dirs_exist_ok=True)
    say(f"{mode}: 학습 완료 ({steps:,} 스텝) → {final_dir}")


def cmd_train(a):
    for mode in ([a.only] if a.only else MODES):
        train_one(mode)


# ------------------------------------------------------------------ 시뮬레이션 실행 도우미
def repeat(name, dates, strategies, jobs, mode="default", env_extra=None):
    """repeat_experiments.py 실행. 결과: results/repeat/<name>/ (중간에 멈춰도 다시 실행하면 이어 함)"""
    cmd = [PY, os.path.join(ROOT, "module4_dispatch", "experiments", "repeat_experiments.py"),
           "--dates", *dates, "--strategies", *strategies, "--name", name, "--jobs", str(jobs)]
    rc = subprocess.call(cmd, cwd=ROOT, env=run_env(mode, env_extra))
    if rc != 0:
        raise SystemExit(f"[중단] {name} 실행 실패 (종료 코드 {rc}) — results/repeat/{name}/logs/ 확인")
    return json.load(open(os.path.join(ROOT, "results", "repeat", name, "summary.json"), encoding="utf-8"))


def set_env(set_dir, rl_zip=None):
    """모델 세트 폴더 → 환경변수. demand_v2는 모든 RL 비교에서 지금 것을 같이 쓴다(공정 비교)."""
    env = {"AI_RL_MODEL_PATH": rl_zip or os.path.join(set_dir, "rl_reposition_model.zip")}
    cat = os.path.join(set_dir, "category_demand_models.pkl")
    if os.path.exists(cat):
        env["AI_CATEGORY_MODEL_PATH"] = cat
    return env


# ------------------------------------------------------------------ 2) 저장본 고르기
def candidates(mode):
    ck = os.path.join(CKPT_DST, mode)
    found = {}
    for p in glob.glob(os.path.join(ck, "rl_reposition_model_*_steps.zip")):
        m = re.search(r"_(\d+)_steps\.zip$", p)
        if m:
            found[int(m.group(1))] = p
    out = []
    for target in PICK_STEPS:
        if found:
            s = min(found, key=lambda k: abs(k - target))
            if s not in [c[0] for c in out]:
                out.append((s, found[s]))
    final = os.path.join(BACKUPS, f"taxi55_{mode}", "rl_reposition_model.zip")
    if os.path.exists(final):
        out.append((model_steps(final) or 0, final))   # 최종 모델 (vecnorm은 같은 폴더의 _vecnorm.pkl)
    return out


def cmd_pick(a):
    lines = [f"# 저장본 고르기 결과", "", f"검증 날짜: {', '.join(VAL_DATES)} (최종 7일과 겹치지 않음)",
             f"기준: 대기(타임아웃 포함, 초) — 낮을수록 좋음", ""]
    for mode in MODES:
        set_dir = os.path.join(BACKUPS, f"taxi55_{mode}")
        if not os.path.isdir(set_dir):
            say(f"{mode}: 학습된 모델이 없어 건너뜀 (먼저 train)")
            continue
        results = []
        for steps, z in candidates(mode):
            tag = "final" if os.path.dirname(z) == set_dir else str(steps)
            say(f"{mode}: {tag} ({steps:,} 스텝) 검증 3일 실행")
            s = repeat(f"taxi55_pick_{mode}_{tag}", VAL_DATES, ["rl_prepositioned"], a.jobs, mode,
                       set_env(set_dir, z))
            st = s["strategies"]["rl_prepositioned"]
            results.append((st[PICK_METRIC]["mean"], st["service_rate"]["mean"], steps, tag, z))
        if not results:
            continue
        results.sort()
        best = results[0]
        lines += [f"## {mode}", "", "| 저장본 | 스텝 | 대기(타임아웃 포함, 초) | 서비스율 |", "|---|---:|---:|---:|"]
        for r in sorted(results, key=lambda r: r[2]):
            mark = " ← 선택" if r is best else ""
            lines.append(f"| {r[3]}{mark} | {r[2]:,} | {r[0]:.1f} | {r[1]*100:.1f}% |")
        lines.append("")
        # 가장 좋은 저장본을 모델 세트로
        best_dir = os.path.join(BACKUPS, f"taxi55_{mode}_best")
        os.makedirs(best_dir, exist_ok=True)
        shutil.copy2(best[4], os.path.join(best_dir, "rl_reposition_model.zip"))
        vn = best[4][:-4] + "_vecnorm.pkl"
        m = re.search(r"^(.*)_(\d+)_steps\.zip$", best[4])
        if m and not os.path.exists(vn):
            vn = f"{m.group(1)}_vecnormalize_{m.group(2)}_steps.pkl"
        if os.path.exists(vn):
            shutil.copy2(vn, os.path.join(best_dir, "rl_reposition_model_vecnorm.pkl"))
        else:
            say(f"[경고] {mode} 선택 저장본의 정규화 파일이 없습니다: {vn}")
        for f in ("demand_v2.joblib", "category_demand_models.pkl"):
            if os.path.exists(os.path.join(set_dir, f)):
                shutil.copy2(os.path.join(set_dir, f), os.path.join(best_dir, f))
        write_meta(best_dir, mode, f"택시55_{mode}_best({best[3]})", rl_steps=best[2],
                   picked_on=VAL_DATES, picked_by=PICK_METRIC)
        say(f"{mode}: 가장 좋은 저장본 = {best[3]} ({best[2]:,} 스텝) → {best_dir}")
    out = os.path.join(HERE, "결과_저장본고르기.md")
    open(out, "w", encoding="utf-8").write("\n".join(lines) + "\n")
    say(f"저장: {out}")


# ------------------------------------------------------------------ 3) 7일 비교
def rl_sets():
    """비교할 RL 모델 세트: (표에 쓸 이름, 폴더). best가 없으면 최종 모델로."""
    out = [("rl_KJS_best1111", os.path.join(BACKUPS, "kjs_best1111"))]
    for mode in MODES:
        best = os.path.join(BACKUPS, f"taxi55_{mode}_best")
        final = os.path.join(BACKUPS, f"taxi55_{mode}")
        if os.path.exists(os.path.join(best, "rl_reposition_model.zip")):
            out.append((f"rl_new_{mode}", best))
        elif os.path.exists(os.path.join(final, "rl_reposition_model.zip")):
            say(f"[안내] {mode}: 고른 저장본이 없어 최종 모델로 비교합니다 (pick을 먼저 하면 best로 비교)")
            out.append((f"rl_new_{mode}", final))
    return out


def cmd_compare(a):
    say("7일 비교: 순찰·시간표 선배치·예측 재배치")
    repeat("taxi55_7d_base", TEST_DATES, ["patrol", "prepositioned", "forecast"], a.jobs)
    for label, d in rl_sets():
        say(f"7일 비교: {label} ({os.path.relpath(d, ROOT)})")
        repeat(f"taxi55_7d_{label}", TEST_DATES, ["rl_prepositioned"], a.jobs, env_extra=set_env(d))
    cmd_report(a)


# ------------------------------------------------------------------ 4) 결과 합치기
def cmd_report(a):
    sys.path.insert(0, os.path.join(ROOT, "module4_dispatch", "experiments"))
    sys.path.insert(0, ROOT)
    from repeat_experiments import summarize, to_markdown
    rows = []
    base = os.path.join(ROOT, "results", "repeat", "taxi55_7d_base", "runs")
    for p in glob.glob(os.path.join(base, "*.json")):
        rows.append(json.load(open(p, encoding="utf-8")))
    for label, _ in rl_sets():
        for p in glob.glob(os.path.join(ROOT, "results", "repeat", f"taxi55_7d_{label}", "runs", "*.json")):
            r = json.load(open(p, encoding="utf-8"))
            r["strategy"] = label
            rows.append(r)
    if not rows:
        raise SystemExit("[중단] 7일 비교 결과가 아직 없습니다 (먼저 compare)")
    s = summarize(rows, "patrol")
    md = ["# 택시 55대 7일 비교", "",
          "- 같은 날짜에는 모든 전략이 같은 손님 목록을 씀 (수요 지문 "
          + ("모두 같음" if all(s["same_demand_per_date"].values()) else "다른 날짜 있음 — 확인 필요") + ")",
          "- rl_KJS_best1111: KJS 최고 모델 (택시 61대로 학습) / rl_new_default·rl_new_gravityVer: 이번에 55대로 학습",
          "", to_markdown(s)]
    out = os.path.join(HERE, "결과_7일비교.md")
    open(out, "w", encoding="utf-8").write("\n".join(md) + "\n")
    print("\n".join(md))
    say(f"저장: {out}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["train", "pick", "compare", "report", "all"])
    ap.add_argument("--only", choices=MODES, help="train: 한 가지 보상 방식만")
    ap.add_argument("--jobs", type=int, default=4, help="동시에 돌릴 시뮬레이션 수 (기본 4)")
    a = ap.parse_args()
    if a.command in ("train", "all"):
        cmd_train(a)
    if a.command in ("pick", "all"):
        cmd_pick(a)
    if a.command in ("compare", "all"):
        cmd_compare(a)
    if a.command == "report":
        cmd_report(a)


if __name__ == "__main__":
    main()
