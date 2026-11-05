"""
수요 예측(XGBoost, t+1~t+6) 하이퍼파라미터를 검증 구간으로 고른다 — 설정값의 근거를 남기기 위함.

    python tools/tune_xgb.py ["data/real_demand_logs/demand_log_*.csv"]
분할 (시간 순서, 데이터 명세 60/20/20): 앞 60% 학습 / 다음 20% 검증(후보 비교) / 마지막 20% 시험(최종 1회만 보고)
출력: results/xgb_tuning/summary.md, grid.csv
"""
import csv
import itertools
import os
import sys
import time

import numpy as np
from sklearn.multioutput import MultiOutputRegressor
from xgboost import XGBRegressor

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from config_loader import CFG                                          # noqa: E402
from evaluate import calculate_metrics                                 # noqa: E402
from module2_preprocessing.pipeline import build_feature_table         # noqa: E402
from module2_preprocessing.time_series_prep import time_based_split    # noqa: E402

GRID = {"n_estimators": [100, 300, 600], "max_depth": [4, 6, 8], "learning_rate": [0.05, 0.1]}
OUT = os.path.join(ROOT, "results", "xgb_tuning")


def fit(params, X, Y):
    m = MultiOutputRegressor(XGBRegressor(**params, random_state=CFG.get("xgb_random_state", 42), n_jobs=2))
    return m.fit(X, Y)


def main():
    logs = sys.argv[1] if len(sys.argv) > 1 else "data/real_demand_logs/demand_log_*.csv"
    feat = build_feature_table(logs)
    fc, tc = feat.attrs["feature_cols"], feat.attrs["target_cols"]
    tr, va, te, cuts = time_based_split(feat, test_size=0.2, val_size=0.2)
    X = {k: d[fc].fillna(0).values for k, d in (("tr", tr), ("va", va), ("te", te))}
    Y = {k: d[tc].values for k, d in (("tr", tr), ("va", va), ("te", te))}
    rows = []
    for n, dpt, lr in itertools.product(*GRID.values()):
        p = {"n_estimators": n, "max_depth": dpt, "learning_rate": lr}
        t0 = time.time()
        pred = np.maximum(0, fit(p, X["tr"], Y["tr"]).predict(X["va"]))
        m = calculate_metrics(Y["va"].ravel(), pred.ravel())
        rows.append({**p, "val_wape": round(m["WAPE (%)"], 2), "val_rmse": round(m["RMSE"], 4), "sec": round(time.time() - t0, 1)})
        print(rows[-1], flush=True)
    best = min(rows, key=lambda r: (r["val_wape"], r["n_estimators"]))
    bp = {k: best[k] for k in GRID}
    # 최종: 학습+검증으로 다시 학습하고 시험 구간에서 한 번만 잰다
    final = fit(bp, np.vstack([X["tr"], X["va"]]), np.vstack([Y["tr"], Y["va"]]))
    tm = calculate_metrics(Y["te"].ravel(), np.maximum(0, final.predict(X["te"])).ravel())
    cur = {k: CFG[f"xgb_{k}"] for k in GRID}
    cur_row = next(r for r in rows if all(r[k] == cur[k] for k in GRID))
    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, "grid.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(sorted(rows, key=lambda r: r["val_wape"]))
    lines = ["# XGBoost 하이퍼파라미터 검증", "",
             f"- 데이터: `{logs}` (생성기 `scripts/generate_demand_logs.py`), H3 res {CFG['h3_resolution']}, {CFG['freq']} 칸, 타겟 {len(tc)}개",
             f"- 분할(시간 순서): 학습 < {cuts['val_cut']} ≤ 검증 < {cuts['test_cut']} ≤ 시험 — 행 {len(tr):,} / {len(va):,} / {len(te):,}",
             f"- 후보 {len(rows)}개 (n_estimators × max_depth × learning_rate = {GRID})", "",
             "| 구분 | n_estimators | max_depth | learning_rate | 검증 WAPE |", "|---|---:|---:|---:|---:|",
             f"| 최적 | {bp['n_estimators']} | {bp['max_depth']} | {bp['learning_rate']} | {best['val_wape']}% |",
             f"| 현재 설정 | {cur['n_estimators']} | {cur['max_depth']} | {cur['learning_rate']} | {cur_row['val_wape']}% |",
             f"| 최악 | {max(rows, key=lambda r: r['val_wape'])['n_estimators']} | {max(rows, key=lambda r: r['val_wape'])['max_depth']} | "
             f"{max(rows, key=lambda r: r['val_wape'])['learning_rate']} | {max(rows, key=lambda r: r['val_wape'])['val_wape']}% |", "",
             f"- 최적 설정을 학습+검증으로 다시 학습한 시험 구간 WAPE: **{tm['WAPE (%)']:.2f}%** (RMSE {tm['RMSE']:.3f}, MAE {tm['MAE']:.3f})",
             f"- 후보 간 검증 WAPE 차이: {min(r['val_wape'] for r in rows)}% ~ {max(r['val_wape'] for r in rows)}%", ""]
    open(os.path.join(OUT, "summary.md"), "w", encoding="utf-8").write("\n".join(lines))
    print("\n".join(lines))


if __name__ == "__main__":
    main()
