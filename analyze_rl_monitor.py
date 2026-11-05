"""RL 학습 중 Monitor가 남긴 에피소드 보상을 읽어, 학습이 실제로 개선됐는지 요약한다.

사용법 (프로젝트 루트에서):
    python analyze_rl_monitor.py                      # 가장 최근 학습 폴더
    python analyze_rl_monitor.py <training_logs 하위 폴더 경로>
"""
import glob
import os
import sys

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.abspath(__file__))
LOG_ROOT = os.path.join(ROOT, "module4_dispatch", "training_logs")
CHUNK = 8  # 몇 에피소드씩 묶어서 평균낼지


def main():
    if len(sys.argv) > 1:
        folder = sys.argv[1]
    else:
        subs = sorted(d for d in glob.glob(os.path.join(LOG_ROOT, "*")) if os.path.isdir(d))
        if not subs:
            print(f"[오류] {LOG_ROOT} 아래에 학습 폴더가 없습니다.")
            return
        folder = subs[-1]

    files = sorted(glob.glob(os.path.join(folder, "ppo_worker_*.monitor.csv")))
    if not files:
        print(f"[오류] {folder} 에 ppo_worker_*.monitor.csv 가 없습니다.")
        return

    frames = []
    for f in files:
        df = pd.read_csv(f, skiprows=1)  # 첫 줄은 '#{json}' 헤더
        df["worker"] = os.path.basename(f).split(".")[0]
        frames.append(df)
    eps = pd.concat(frames).sort_values("t").reset_index(drop=True)
    r = eps["r"].to_numpy(dtype=float)
    n = len(r)

    print(f"폴더: {folder}")
    print(f"워커 {len(files)}개, 에피소드 {n}개, 에피소드 길이 평균 {eps['l'].mean():.0f}스텝")
    print(f"전체 보상 평균 {r.mean():.2f}, 표준편차 {r.std():.2f}, 최소 {r.min():.2f}, 최대 {r.max():.2f}\n")

    print(f"[{CHUNK}에피소드씩 묶은 평균 보상 (시간순)]")
    for i in range(0, n, CHUNK):
        part = r[i:i + CHUNK]
        print(f"  {i + 1:>3}~{i + len(part):<3} 평균 {part.mean():8.2f}  (n={len(part)})")

    if n >= 6:
        k = max(n // 3, 2)
        first, last = r[:k], r[-k:]
        diff = last.mean() - first.mean()
        sem = np.sqrt(first.var(ddof=1) / len(first) + last.var(ddof=1) / len(last))
        z = diff / sem if sem > 0 else float("nan")
        print(f"\n[앞 {k}개 vs 뒤 {k}개]")
        print(f"  앞 평균 {first.mean():.2f} → 뒤 평균 {last.mean():.2f}  (차이 {diff:+.2f}, z={z:+.2f})")
        if abs(z) < 2:
            print("  → 차이가 노이즈 범위(|z|<2)입니다. 개선이라고 말하기 어렵습니다.")
        elif diff > 0:
            print("  → 보상이 의미 있게 올랐습니다.")
        else:
            print("  → 보상이 의미 있게 내려갔습니다. 환경/보상 설계를 점검하세요.")
    else:
        print("\n에피소드가 6개 미만이라 앞뒤 비교를 생략합니다.")


if __name__ == "__main__":
    main()