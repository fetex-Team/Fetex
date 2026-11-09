"""
수요 예측(XGBoost, t+1~t+6) 하이퍼파라미터를 검증 구간으로 고른다 — 설정값의 근거를 남기기 위함.

    python module3_prediction/tune_xgb.py                                   # train.py와 같은 학습 로그 선택 목록
    python module3_prediction/tune_xgb.py "data/real_demand_logs/demand_log_*.csv"
피처 테이블은 train.py와 같은 module2_preprocessing.feature_cache.load_or_build_feature_table
(config freq·forecast_horizons·피처 목록 그대로, 현재 10분 칸 · 타깃 6개 · 피처 43개)을 쓴다.
분할 (시간 순서, 데이터 명세 60/20/20): 앞 60% 학습 / 다음 20% 검증(후보 비교) / 마지막 20% 시험(최종 1회만 보고)
출력: results/xgb_tuning/summary.md, grid.csv
"""
import argparse
import csv
import glob
import itertools
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

GRID = {"n_estimators": [100, 300, 600], "max_depth": [4, 6, 8], "learning_rate": [0.05, 0.1]}
OUT = os.path.join(ROOT, "results", "xgb_tuning")


def fit(params, X, Y):
    # train.py와 같은 모델 생성기(xgb_factory: config 시드·스레드 규칙) + 타깃 6개(t+1~t+6) 동시 예측
    from sklearn.multioutput import MultiOutputRegressor
    from module3_prediction.xgb_factory import build_xgboost_model
    return MultiOutputRegressor(build_xgboost_model(**params)).fit(X, Y)


def main():
    ap = argparse.ArgumentParser(description="XGBoost 수요 예측 하이퍼파라미터 검증 (60/20/20 시간 순서 분할)")
    ap.add_argument("logs", nargs="?", default=None,
                    help="수요 로그 CSV glob (기본: train.py와 같은 '학습 로그 선택' 목록, data/sim_logs)")
    ap.add_argument("--no-cache", action="store_true", help="data/features/ 저장본을 쓰지 않고 피처 테이블을 다시 만든다")
    a = ap.parse_args()

    # 무거운 모듈은 --help 이후에 불러온다 (config_loader는 import 때 지역 좌표 조회를 한다)
    import numpy as np
    from config_loader import CFG
    from module2_preprocessing.feature_cache import load_or_build_feature_table
    from module2_preprocessing.time_series_prep import time_based_split
    from module3_prediction.evaluate import calculate_metrics

    if a.logs:
        log_paths, logs = sorted(glob.glob(a.logs)), a.logs
    else:
        from module2_preprocessing.log_selection import selected_log_paths
        log_paths = selected_log_paths()
        logs = f"학습 로그 선택 {len(log_paths)}개"
    if not log_paths:
        raise SystemExit(f"수요 로그가 없습니다: {a.logs or 'data/sim_logs (학습 로그 선택)'}")
    # train.py와 같은 피처 테이블 (config freq·forecast_horizons, 같은 저장본 캐시)
    feat = load_or_build_feature_table(log_paths, cache=not a.no_cache)
    fc, tc = list(feat.attrs["feature_cols"]), list(feat.attrs["target_cols"])
    tr, va, te, cuts = time_based_split(feat, test_size=0.2, val_size=0.2)
    X = {k: d[fc].fillna(0).values for k, d in (("tr", tr), ("va", va), ("te", te))}
    Y = {k: d[tc].fillna(0).values.astype(float) for k, d in (("tr", tr), ("va", va), ("te", te))}
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
    # 현재 설정이 후보 격자 밖이면 검증 WAPE를 '-'로 표시
    cur_row = next((r for r in rows if all(r[k] == cur[k] for k in GRID)), {"val_wape": "-"})
    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, "grid.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(sorted(rows, key=lambda r: r["val_wape"]))
    lines = ["# XGBoost 하이퍼파라미터 검증", "",
             f"- 데이터: `{logs}` H3 res {CFG['h3_resolution']}, {feat.attrs.get('freq', CFG['freq'])} 칸, 피처 {len(fc)}개, 타겟 {len(tc)}개",
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
