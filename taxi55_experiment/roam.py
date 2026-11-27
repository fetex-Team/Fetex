"""'RL + 빈 택시 돌아다니기' 재학습 실험 (택시 55대, gravityVer).

가설: RL이 순찰보다 느렸던 건 RL이 안 옮긴 빈 택시가 제자리 근처에 머물렀기 때문이다.
      빈 택시가 돌아다니는 환경에서 다시 학습하면 RL이 순찰이 놓치는 부분을 메우도록 배울 수 있다.
바꾼 규칙 (config_gravityVer_roam.json, 학습·평가 둘 다 적용):
    rl_idle_roam=true  RL이 옮기지 않은(또는 목적지에 도착한) 빈 택시는 순찰처럼 지역 전체를 돌아다닌다

순서:
    1) 새 규칙으로 gravityVer 3만 스텝 학습 → saved_models_backups/taxi55_gravityVer_roam
    2) 7일 비교: 새로 학습한 모델 + 새 규칙
    3) 결과표 → taxi55_experiment/결과_돌아다니기.md
       (순찰·기존 RL·'기존 모델+돌아다니기' 결과는 이미 있는 것 재사용)

실행:  python taxi55_experiment/roam.py
"""
import glob
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import taxi55 as t

MODE = "gravityVer_roam"
SET_DIR = os.path.join(t.BACKUPS, "taxi55_gravityVer_roam")
OLD_BEST = os.path.join(t.BACKUPS, "taxi55_gravityVer_best")


def train():
    if os.path.exists(os.path.join(SET_DIR, "rl_reposition_model.zip")):
        t.say(f"이미 학습됨 ({SET_DIR}) — 건너뜀")
        return
    if os.path.isdir(t.CKPT_SRC) and os.listdir(t.CKPT_SRC):
        old = os.path.join(t.CKPT_DST, f"_이전_{datetime.now():%Y%m%d_%H%M%S}")
        os.makedirs(t.CKPT_DST, exist_ok=True)
        shutil.move(t.CKPT_SRC, old)
    t.say("gravityVer + 빈 택시 돌아다니기(rl_idle_roam): RL 3만 스텝 학습 시작 (약 40분)")
    started = datetime.now().timestamp()
    rc = subprocess.call([t.PY, os.path.join(t.ROOT, "main.py"), "train", "--rl-only"], cwd=t.ROOT,
                         env=t.run_env(MODE))
    model = os.path.join(t.RL_DIR, "rl_reposition_model.zip")
    steps = t.model_steps(model)
    if rc != 0 or not os.path.exists(model) or os.path.getmtime(model) < started or (steps or 0) < 29000:
        raise SystemExit(f"[중단] 학습이 정상적으로 끝나지 않았습니다 (종료 코드 {rc}, 모델 스텝 {steps})")
    os.makedirs(SET_DIR, exist_ok=True)
    saved = os.path.join(t.ROOT, "saved_models")
    for f in ("demand_v2.joblib", "category_demand_models.pkl", "cnn_lstm_demand.pt"):
        if os.path.exists(os.path.join(saved, f)):
            shutil.copy2(os.path.join(saved, f), os.path.join(SET_DIR, f))
    for f in ("rl_reposition_model.zip", "rl_reposition_model_vecnorm.pkl"):
        shutil.copy2(os.path.join(t.RL_DIR, f), os.path.join(SET_DIR, f))
    t.write_meta(SET_DIR, MODE, "택시55_gravityVer_돌아다니기(3만)", rl_steps=steps, rl_idle_roam=True)
    if os.path.isdir(t.CKPT_SRC):
        dst = os.path.join(t.CKPT_DST, MODE)
        shutil.rmtree(dst, ignore_errors=True)
        shutil.move(t.CKPT_SRC, dst)
    logs = sorted(glob.glob(os.path.join(t.RL_DIR, "training_logs", "2*")))
    if logs:
        shutil.copytree(logs[-1], os.path.join(t.HERE, "training_logs", MODE), dirs_exist_ok=True)
    t.say(f"학습 완료 ({steps:,} 스텝) → {SET_DIR}")


RUNS = [  # (표 이름, 결과 폴더, 모델 세트, 평가 때 쓸 설정)
    ("rl_roam_new", "taxi55_7d_rl_roam_new", SET_DIR, MODE),
]
OLD = [("rl_new_gravityVer", "taxi55_7d_rl_new_gravityVer"), ("rl_roam_oldmodel", "taxi55_7d_rl_roam_oldmodel")]


def compare(jobs):
    for label, name, d, mode in RUNS:
        t.say(f"7일 비교: {label}")
        t.repeat(name, t.TEST_DATES, ["rl_prepositioned"], jobs, mode, t.set_env(d))


def report():
    sys.path.insert(0, os.path.join(t.ROOT, "module4_dispatch", "experiments"))
    sys.path.insert(0, t.ROOT)
    from repeat_experiments import summarize, to_markdown
    res = os.path.join(t.ROOT, "results", "repeat")
    rows = [json.load(open(p, encoding="utf-8")) for p in glob.glob(os.path.join(res, "taxi55_7d_base", "runs", "*.json"))]
    for label, name in OLD + [(r[0], r[1]) for r in RUNS]:
        for p in glob.glob(os.path.join(res, name, "runs", "*.json")):
            r = json.load(open(p, encoding="utf-8"))
            r["strategy"] = label
            rows.append(r)
    s = summarize(rows, "patrol")
    by_day = {}
    for r in rows:
        by_day.setdefault(r["run_date"], {})[r["strategy"]] = r["avg_wait_sec"]
    cols = ["patrol", "forecast", "rl_new_gravityVer", "rl_roam_oldmodel", "rl_roam_new"]
    day_md = ["| 날짜 | " + " | ".join(cols) + " |", "|---|" + "---:|" * len(cols)]
    for d in sorted(by_day):
        day_md.append(f"| {d} | " + " | ".join(f"{by_day[d].get(c, float('nan')):.0f}" for c in cols) + " |")
    md = ["# RL + 빈 택시 돌아다니기 (택시 55대, gravityVer)", "",
          "- rl_new_gravityVer: 기존 방식(RL이 안 옮긴 빈 택시는 제자리 근처), 3만 스텝 저장본",
          "- rl_roam_oldmodel: 위 모델을 재학습 없이 '빈 택시 돌아다니기(rl_idle_roam)'만 켜고 실행",
          "- rl_roam_new: '빈 택시 돌아다니기'를 켠 환경에서 3만 스텝 새로 학습",
          "", to_markdown(s), "", "## 날짜별 평균 대기(초)", "", *day_md]
    out = os.path.join(t.HERE, "결과_돌아다니기.md")
    open(out, "w", encoding="utf-8").write("\n".join(md) + "\n")
    print("\n".join(md))
    t.say(f"저장: {out}")


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("command", nargs="?", default="all", choices=["train", "compare", "report", "all"])
    ap.add_argument("--jobs", type=int, default=4)
    a = ap.parse_args()
    if a.command in ("train", "all"):
        train()
    if a.command in ("compare", "all"):
        compare(a.jobs)
    if a.command in ("report", "all"):
        report()
