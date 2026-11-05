"""Module 3 평가 — 지표 계산, 기준 모델 비교, 시간대·셀별 분석, 그래프 저장.

train.py가 학습 직후 evaluate_predictions()를 불러 results/module3/<freq>/ 에 결과를 남긴다.
    metrics.json            모델·기준 모델별 지표 (전체, 시점별 t+1~t+6, 30분·60분 합계, 시간대별, 셀별)
    predictions.csv.gz      시험 구간 실제값·예측값
    01_timeseries_top_cell.png   호출이 가장 많은 셀(강남역 일대)의 실제 vs 예측 (t+1)
    02_horizon_error.png         예측 시점별 WAPE
    03_hour_error.png            시간대별 WAPE
    04_cell_error.png            셀별 WAPE (호출 많은 순 상위 15개)

사용
    python module3_prediction/evaluate.py                       # 저장된 모델을 다시 평가 (train.py와 같은 분할)
    python module3_prediction/evaluate.py --logs "data/real_demand_logs/demand_log_*.csv"
    python module3_prediction/evaluate.py --compare-freq 5min 10min   # 단위별 결과 비교 (30분·60분 합계 기준)
"""
import json
import os
import sys

import numpy as np
import pandas as pd
from sklearn.metrics import mean_absolute_error, mean_squared_error

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)
RESULTS_DIR = os.path.join(PROJECT_ROOT, "results", "module3")
WINDOWS_MIN = (30, 60)   # 단위가 달라도 비교할 수 있게 '앞으로 30분·60분 동안의 합계'로도 평가


def fmt_pct(v, digits: int = 2) -> str:
    """None(계산 불가)이면 '-', 아니면 'xx.xx%'."""
    return "-" if v is None else f"{v:.{digits}f}%"


def calculate_metrics(y_true: np.ndarray, y_pred: np.ndarray) -> dict:
    """
    RMSE, MAE, MAPE, WAPE 성능 평가 지표 계산.

    수요 데이터는 0인 칸이 많아서 MAPE를 그대로 계산하면 0으로 나눠 수백만 %가 나온다.
    - MAPE: 실제값이 0인 칸은 제외하고 계산 (전부 0이면 None)
    - WAPE: 오차 절대값 합 / 실제값 절대값 합 (0이 많아도 안정적, 합이 0이면 None) — 주 지표
    """
    y_true = np.asarray(y_true, dtype=float).ravel()
    y_pred = np.asarray(y_pred, dtype=float).ravel()
    rmse = np.sqrt(mean_squared_error(y_true, y_pred))
    mae = mean_absolute_error(y_true, y_pred)
    err = np.abs(y_true - y_pred)
    nonzero = np.abs(y_true) > 0
    mape = float(np.mean(err[nonzero] / np.abs(y_true[nonzero])) * 100) if nonzero.any() else None
    denom = float(np.sum(np.abs(y_true)))
    wape = float(np.sum(err) / denom * 100) if denom > 0 else None
    return {
        "RMSE": round(float(rmse), 4),
        "MAE": round(float(mae), 4),
        "MAPE (%)": round(mape, 4) if mape is not None else None,
        "WAPE (%)": round(wape, 4) if wape is not None else None,
    }


def _wape(y, p):
    s = float(np.abs(y).sum())
    return None if s == 0 else round(float(np.abs(y - p).sum()) / s * 100, 3)


def baseline_predictions(test_df, n_targets):
    """기준 모델: 나이브(직전 칸 그대로), 계절 나이브(지난주 같은 칸). 피처가 없으면 생략."""
    out = {}
    if "lag_1" in test_df:
        out["나이브(직전 칸)"] = np.repeat(test_df[["lag_1"]].fillna(0).values, n_targets, 1)
    if "same_time_last_week" in test_df:
        out["계절 나이브(지난주)"] = np.repeat(test_df[["same_time_last_week"]].fillna(0).values, n_targets, 1)
    return out


def _window_buckets(freq, minutes, n_targets):
    step = pd.Timedelta(freq).total_seconds() / 60
    nb = minutes / step
    return int(nb) if nb == int(nb) and 1 <= nb <= n_targets else None


def evaluate_predictions(test_df, target_cols, model_preds, freq, report_dir, info=None, plots=True):
    """시험 구간 예측을 평가해 report_dir에 metrics.json·predictions.csv.gz·그래프를 저장. 반환: metrics dict"""
    os.makedirs(report_dir, exist_ok=True)
    Y = test_df[target_cols].fillna(0).values.astype(float)
    n_t = len(target_cols)
    preds = {**model_preds, **baseline_predictions(test_df, n_t)}
    step_min = int(pd.Timedelta(freq).total_seconds() // 60)
    hours = pd.to_datetime(test_df["time_bucket"]).dt.hour.values
    cells = test_df["h3_index"].values

    metrics = {"info": info or {}, "freq": freq, "target_cols": target_cols, "models": {}}
    for name, P in preds.items():
        m = {"overall": calculate_metrics(Y, P),
             "per_horizon": {f"t+{k + 1} ({(k + 1) * step_min}분)": calculate_metrics(Y[:, k], P[:, k]) for k in range(n_t)},
             "windows": {}}
        for w in WINDOWS_MIN:
            nb = _window_buckets(freq, w, n_t)
            if nb:
                m["windows"][f"{w}분 합계"] = calculate_metrics(Y[:, :nb].sum(1), P[:, :nb].sum(1))
        m["per_hour_wape"] = {int(h): _wape(Y[hours == h], P[hours == h]) for h in sorted(set(hours))}
        demand_by_cell = pd.Series(Y[:, 0], index=cells).groupby(level=0).sum().sort_values(ascending=False)
        m["per_cell_wape"] = {c: _wape(Y[cells == c], P[cells == c]) for c in demand_by_cell.index}
        metrics["models"][name] = m

    with open(os.path.join(report_dir, "metrics.json"), "w", encoding="utf-8") as f:
        json.dump(metrics, f, ensure_ascii=False, indent=1, default=str)
    pred_df = test_df[["time_bucket", "h3_index"]].copy()
    for k, c in enumerate(target_cols):
        pred_df[f"{c}_true"] = Y[:, k]
        for name, P in model_preds.items():
            pred_df[f"{c}_{name}"] = np.round(P[:, k], 4)
    pred_df.to_csv(os.path.join(report_dir, "predictions.csv.gz"), index=False, compression="gzip")

    print_summary(metrics)
    if plots:
        plot_report(test_df, Y, preds, list(model_preds), metrics, freq, report_dir)
    return metrics


def print_summary(metrics):
    models = metrics["models"]
    print("\n" + "=" * 78)
    print(f"[시험 구간 성능] {metrics['freq']} 칸, 타깃 {metrics['target_cols'][0]}~{metrics['target_cols'][-1]}  (WAPE가 주 지표)")
    print("-" * 78)
    wins = sorted({w for m in models.values() for w in m["windows"]})
    head = f"{'모델':<22}{'RMSE':>8}{'MAE':>8}{'WAPE':>10}{'MAPE(0제외)':>13}" + "".join(f"{w + ' WAPE':>14}" for w in wins)
    print(head)
    for name, m in models.items():
        o = m["overall"]
        row = f"{name:<22}{o['RMSE']:>8.3f}{o['MAE']:>8.3f}{fmt_pct(o['WAPE (%)']):>10}{fmt_pct(o['MAPE (%)']):>13}"
        row += "".join(f"{fmt_pct(m['windows'].get(w, {}).get('WAPE (%)')):>14}" for w in wins)
        print(row)
    print("=" * 78)


def _setup_matplotlib():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib import font_manager
    installed = {f.name for f in font_manager.fontManager.ttflist}
    korean = [n for n in ("Malgun Gothic", "AppleGothic", "NanumGothic") if n in installed]  # 없는 글꼴은 경고가 쏟아지므로 거름
    plt.rcParams["font.family"] = korean[:1] + ["DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False
    return plt


def plot_report(test_df, Y, preds, model_names, metrics, freq, report_dir):
    plt = _setup_matplotlib()
    t = pd.to_datetime(test_df["time_bucket"]).values
    cells = test_df["h3_index"].values
    colors = {"XGBoost": "#2563eb", "CNN-LSTM": "#dc2626"}

    # 1) 호출이 가장 많은 셀: 실제 vs 예측 (t+1), 시험 구간 마지막 2일
    top = pd.Series(Y[:, 0], index=cells).groupby(level=0).sum().idxmax()
    mask = cells == top
    tt = t[mask]
    keep = tt >= (tt.max() - np.timedelta64(2, "D"))
    fig, ax = plt.subplots(figsize=(13, 4.5))
    ax.plot(tt[keep], Y[mask, 0][keep], color="#111827", lw=1.4, label="실제")
    for name in model_names:
        ax.plot(tt[keep], preds[name][mask, 0][keep], color=colors.get(name), lw=1.1, alpha=0.85, label=f"{name} 예측")
    ax.set_title(f"호출이 가장 많은 셀 {top} — 실제 vs 예측 (t+1, {freq} 칸, 시험 구간 마지막 2일)")
    ax.set_ylabel("호출 수"); ax.legend(); ax.grid(alpha=0.3)
    fig.tight_layout(); fig.savefig(os.path.join(report_dir, "01_timeseries_top_cell.png"), dpi=130); plt.close(fig)

    # 2) 예측 시점별 WAPE
    fig, ax = plt.subplots(figsize=(8, 4.5))
    for name, m in metrics["models"].items():
        labels = list(m["per_horizon"])
        ax.plot(range(1, len(labels) + 1), [v["WAPE (%)"] for v in m["per_horizon"].values()], marker="o",
                label=name, color=colors.get(name), ls="-" if name in model_names else "--")
    ax.set_xticks(range(1, len(labels) + 1)); ax.set_xticklabels([l.split(" ")[1].strip("()") for l in labels])
    ax.set_xlabel("몇 분 뒤 예측"); ax.set_ylabel("WAPE (%)"); ax.set_title("예측 시점별 오차 (낮을수록 좋음)")
    ax.legend(); ax.grid(alpha=0.3)
    fig.tight_layout(); fig.savefig(os.path.join(report_dir, "02_horizon_error.png"), dpi=130); plt.close(fig)

    # 3) 시간대별 WAPE + 시간대별 평균 호출(막대)
    hours = pd.to_datetime(test_df["time_bucket"]).dt.hour.values
    fig, ax = plt.subplots(figsize=(11, 4.5))
    ax2 = ax.twinx()
    hs = sorted(set(hours))
    ax2.bar(hs, [Y[hours == h, 0].mean() for h in hs], color="#e5e7eb", label="평균 호출(t+1)")
    for name in model_names:
        w = metrics["models"][name]["per_hour_wape"]
        ax.plot(hs, [w[h] for h in hs], marker="o", color=colors.get(name), label=name)
    ax.set_zorder(ax2.get_zorder() + 1); ax.patch.set_visible(False)
    ax.set_xticks(hs); ax.set_xlabel("시각"); ax.set_ylabel("WAPE (%)"); ax2.set_ylabel("칸당 평균 호출")
    ax.set_title("시간대별 오차 — 호출이 적은 새벽은 상대 오차가 커진다"); ax.legend(loc="upper left"); ax.grid(alpha=0.3)
    fig.tight_layout(); fig.savefig(os.path.join(report_dir, "03_hour_error.png"), dpi=130); plt.close(fig)

    # 4) 셀별 WAPE (호출 많은 순 상위 15개)
    order = list(next(iter(metrics["models"].values()))["per_cell_wape"])[:15]
    fig, ax = plt.subplots(figsize=(11, 4.8))
    width = 0.8 / max(1, len(model_names))
    cap = 150   # 호출이 거의 없는 셀은 WAPE가 수천 %까지 튀어 나머지가 안 보이므로 축을 자르고 값을 적는다
    for i, name in enumerate(model_names):
        w = metrics["models"][name]["per_cell_wape"]
        vals = [w[c] or 0 for c in order]
        xs = np.arange(len(order)) + i * width
        ax.bar(xs, np.minimum(vals, cap), width, label=name, color=colors.get(name))
        for x, v in zip(xs, vals):
            if v > cap:
                ax.text(x, cap * 0.97, f"{v:.0f}", ha="center", va="top", fontsize=7, rotation=90, color="white")
    share = pd.Series(Y[:, 0], index=cells).groupby(level=0).sum()
    share = (share / share.sum() * 100)
    ax.set_ylim(0, cap)
    ax.set_xticks(np.arange(len(order)) + width * (len(model_names) - 1) / 2)
    ax.set_xticklabels([f"{c[-7:]}\n{share[c]:.1f}%" for c in order], fontsize=8)
    ax.set_xlabel("H3 셀 끝 7자리 / 전체 호출 중 비중 (왼쪽일수록 호출 많음)"); ax.set_ylabel(f"WAPE (%, {cap}%에서 자름)")
    ax.set_title("셀별 오차 — 호출 많은 셀 상위 15개 (호출이 적은 셀은 상대 오차가 커짐)"); ax.legend(); ax.grid(alpha=0.3, axis="y")
    fig.tight_layout(); fig.savefig(os.path.join(report_dir, "04_cell_error.png"), dpi=130); plt.close(fig)
    print(f"[그래프] {os.path.relpath(report_dir, PROJECT_ROOT)}/01~04_*.png")


# ---------------------------------------------------------------- 저장된 모델 다시 평가
def evaluate_saved_models(log_path=None, model_dir=None):
    """저장된 XGBoost·CNN-LSTM을 train.py와 같은 분할의 시험 구간에서 다시 평가."""
    import joblib
    import torch
    from module3_prediction.models import CNNLSTMModel
    from module3_prediction.train import SAVED_MODELS, build_sequences, load_feature_table, split_by_time

    model_dir = model_dir or SAVED_MODELS
    xgb_art = joblib.load(os.path.join(model_dir, "demand_v2.joblib"))
    dl_art = torch.load(os.path.join(model_dir, "cnn_lstm_demand.pt"), weights_only=False)
    freq = xgb_art.get("freq")
    if not freq or "seq_len" not in dl_art:
        print("[오류] 예전 형식의 모델입니다. python module3_prediction/train.py로 다시 학습하세요.")
        return None
    feature_df, feature_cols, target_cols = load_feature_table(log_path, freq=freq)
    if feature_cols != xgb_art["feature_cols"] or feature_cols != dl_art["feature_cols"]:
        print(f"[오류] 저장된 모델의 피처({len(xgb_art['feature_cols'])}개)가 지금 전처리 피처({len(feature_cols)}개)와 다릅니다. "
              f"python module3_prediction/train.py로 다시 학습하세요.")
        return None
    train_df, val_df, test_df, _ = split_by_time(feature_df)
    X = test_df[feature_cols].fillna(0).values
    pred_xgb = np.maximum(0, xgb_art["model"].predict(X))

    all_df = pd.concat([train_df, val_df, test_df])
    seq = build_sequences(all_df, dl_art["scaler"].transform(all_df[feature_cols].fillna(0).values).astype(np.float32),
                          dl_art.get("seq_len", 1), freq)[len(train_df) + len(val_df):]
    cnn = CNNLSTMModel(input_dim=dl_art["input_dim"], output_dim=dl_art["output_dim"])
    cnn.load_state_dict(dl_art["state_dict"]); cnn.eval()
    with torch.no_grad():
        pred_cnn = np.maximum(0, cnn(torch.tensor(seq)).numpy())
    report_dir = os.path.join(RESULTS_DIR, freq, "re_evaluate")
    return evaluate_predictions(test_df, target_cols, {"XGBoost": pred_xgb, "CNN-LSTM": pred_cnn}, freq, report_dir,
                                {"model_dir": model_dir, "logs": log_path})


def compare_freqs(freqs):
    """results/module3/<freq>/metrics.json들을 '앞으로 30분·60분 합계' 기준으로 비교 (단위가 달라도 같은 기준)."""
    rows = []
    for fq in freqs:
        path = os.path.join(RESULTS_DIR, fq, "metrics.json")
        if not os.path.exists(path):
            print(f"[안내] {os.path.relpath(path, PROJECT_ROOT)} 없음 — python module3_prediction/train.py --freq {fq} 먼저 실행")
            continue
        m = json.load(open(path, encoding="utf-8"))
        for name, mm in m["models"].items():
            rows.append({"단위": fq, "모델": name, "칸별 WAPE": mm["overall"]["WAPE (%)"],
                         **{f"{w} WAPE": v["WAPE (%)"] for w, v in mm["windows"].items()}})
    if not rows:
        return None
    df = pd.DataFrame(rows)
    print("\n[단위 비교] 칸이 작을수록 칸당 호출이 적어 '칸별 WAPE'는 커지므로, 같은 시간 합계(30분·60분)로 비교하세요.")
    print(df.to_string(index=False))
    out = os.path.join(RESULTS_DIR, "freq_comparison.csv")
    df.to_csv(out, index=False, encoding="utf-8-sig")
    print(f"[저장] {os.path.relpath(out, PROJECT_ROOT)}")
    return df


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Module 3 평가 (저장된 모델 재평가 / 단위 비교)")
    ap.add_argument("--logs", default=None, help="평가 로그 글롭 (train.py --logs와 같게)")
    ap.add_argument("--model-dir", default=None, help="모델 폴더 (기본 saved_models/)")
    ap.add_argument("--compare-freq", nargs="+", metavar="FREQ", help="예: --compare-freq 5min 10min")
    a = ap.parse_args()
    if a.compare_freq:
        compare_freqs(a.compare_freq)
    else:
        evaluate_saved_models(a.logs, a.model_dir)
