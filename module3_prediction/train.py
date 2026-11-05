"""Module 3: 단기 수요 예측 — XGBoost vs CNN-LSTM, t+1 ~ t+6 동시 예측.

흐름
1. Module 2 파이프라인(build_feature_table)으로 (H3 셀 × 시간 칸) 피처 테이블과 타깃 y_h1..y_h6 생성
2. 시간 순서 분할: 학습 → 검증(val_size) → 시험(test_size). 같은 시각의 다른 셀이 학습·시험에 섞이지 않는다.
3. XGBoost: 학습 구간에서 t+1 기준 GridSearch(TimeSeriesSplit)로 하이퍼파라미터를 고르고,
   같은 값으로 학습+검증 구간에 6개 시점을 학습(MultiOutput). 이 모델이 시뮬레이션 재배치용 demand_v2.joblib이 된다.
4. CNN-LSTM(PyTorch): 셀마다 직전 cnn_seq_len 칸의 피처를 시퀀스로 넣어 6개 시점을 한 번에 출력.
   검증 손실이 cnn_patience 번 연속 나아지지 않으면 멈추고 가장 좋았던 가중치로 되돌린다(조기 종료).
5. 시험 구간 평가 → results/module3/<freq>/ 에 지표(metrics.json), 예측값(predictions.csv.gz), 그래프 저장
6. 저장 (설정 freq와 같을 때 saved_models/):
   demand_v2.joblib    XGBoost — forecast 재배치(module4_dispatch/forecast_repositioner.py)가 읽는 형식
                       {version 2, model, feature_cols, target_cols, cells, data_until, freq, max_lag, rolling_*, metrics, ...}
   cnn_lstm_demand.pt  CNN-LSTM — 비교·보고서용 (시뮬레이션은 쓰지 않음)
   (예전 scripts/train_dispatch_model.py가 하던 demand_v2 학습을 여기로 합쳤다)

사용
    python module3_prediction/train.py --logs "data/real_demand_logs/demand_log_*.csv"
    python module3_prediction/train.py --logs "..." --freq 5min      # 5분 단위 비교 (모델은 results/module3/5min/에 저장)
    python module3_prediction/evaluate.py --compare-freq 5min 10min   # 두 단위 비교표 (같은 30분·60분 합계 기준)
    --quick: 작은 탐색 범위·짧은 학습으로 코드 동작만 확인
"""
import argparse
import json
import os
import sys
import time

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(CURRENT_DIR)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import joblib
import numpy as np
import pandas as pd

from config_loader import CFG
from module2_preprocessing.log_selection import selected_log_paths  # config_gui '학습 로그 선택' 반영
from module2_preprocessing.feature_cache import load_or_build_feature_table
from module2_preprocessing.time_series_prep import time_based_split
from module3_prediction.xgb_factory import build_xgboost_model
# torch는 CNN-LSTM 단계에서만 불러온다. 먼저 올라와 있으면 xgb_factory가 XGBoost를 단일 스레드로 돌려 튜닝이 느려진다.

SAVED_MODELS = os.path.join(PROJECT_ROOT, "saved_models")
RESULTS_DIR = os.path.join(PROJECT_ROOT, "results", "module3")
DEMAND_LOG_DIR = os.path.join(PROJECT_ROOT, "data", "sim_logs")
MIN_RECOMMENDED_ROWS = 200


# ---------------------------------------------------------------- 데이터
def load_feature_table(log_path=None, freq=None, max_lag=None, cache=True):
    """Module 2 피처 테이블. log_path 생략 시 data/sim_logs의 '학습 로그 선택' 로그.
    같은 로그·설정이면 data/features/의 전처리 저장본을 불러온다 (cache=False면 새로 만듦).
    반환: feature_df(time_bucket·h3_index·피처·타깃, attrs['freq']), feature_cols, target_cols"""
    if log_path:
        logs = [log_path]
    else:
        logs = selected_log_paths(DEMAND_LOG_DIR)
        if not logs:
            raise FileNotFoundError(f"{DEMAND_LOG_DIR}에 수요 로그가 없습니다. --logs로 로그를 지정하거나 "
                                    f"python measure_wait_time.py로 로그를 먼저 만드세요.")
        print(f"[안내] 로그 {len(logs)}개를 학습 데이터로 사용합니다.")
    feature_df = load_or_build_feature_table(logs, cache=cache, freq=freq, max_lag=max_lag)
    if len(feature_df) == 0:
        raise ValueError("lag/rolling 생성 후 남은 행이 없습니다. 로그 기간이 너무 짧습니다.")
    if len(feature_df) < MIN_RECOMMENDED_ROWS:
        print(f"[경고] 피처 테이블이 {len(feature_df)}행뿐입니다 (권장 {MIN_RECOMMENDED_ROWS}행 이상).")
    return feature_df, list(feature_df.attrs["feature_cols"]), list(feature_df.attrs["target_cols"])


def split_by_time(feature_df, test_size=None, val_size=None):
    """시간 순서 분할 (학습 → 검증 → 시험). 각 구간은 시간순 정렬."""
    test_size = CFG["test_size"] if test_size is None else test_size
    val_size = CFG.get("val_size", 0.1) if val_size is None else val_size
    train_df, val_df, test_df, cuts = time_based_split(feature_df, test_size, val_size=val_size)
    order = ["time_bucket", "h3_index"]
    return train_df.sort_values(order), val_df.sort_values(order), test_df.sort_values(order), cuts


def prepare_train_test(max_lag=None, log_path=None):
    """(예전 호출부 호환) 학습+검증 / 시험 행렬. 반환: X_train, X_test, Y_train, Y_test, feature_cols, cuts"""
    feature_df, feature_cols, target_cols = load_feature_table(log_path, max_lag=max_lag)
    train_df, val_df, test_df, cuts = split_by_time(feature_df)
    fit_df = pd.concat([train_df, val_df])
    return (fit_df[feature_cols].fillna(0).values, test_df[feature_cols].fillna(0).values,
            fit_df[target_cols].fillna(0).values, test_df[target_cols].fillna(0).values, feature_cols, cuts)


def build_sequences(feature_df, values, seq_len, freq):
    """각 행마다 '같은 셀의 직전 seq_len 칸' 피처를 쌓은 (N, seq_len, F) 배열 (행 순서는 feature_df와 같음).
    시간이 끊긴 곳(로그 날짜 사이 공백)은 넘지 않고, 앞쪽 이력이 모자라면 가장 오래된 칸을 반복해 채운다.
    시험 행의 시퀀스는 학습·검증 구간의 직전 칸을 쓰므로, 분할 전 전체 테이블로 만든다."""
    n = len(feature_df)
    if seq_len <= 1:
        return values.reshape(n, 1, -1)
    cell = feature_df["h3_index"].to_numpy()
    t = pd.to_datetime(feature_df["time_bucket"]).to_numpy()
    order = np.lexsort((t, cell))                      # 셀 → 시간 순
    c_s, t_s = cell[order], t[order]
    new_seg = np.ones(n, dtype=bool)
    new_seg[1:] = (c_s[1:] != c_s[:-1]) | ((t_s[1:] - t_s[:-1]) != pd.Timedelta(freq).to_timedelta64())
    seg_start = np.maximum.accumulate(np.where(new_seg, np.arange(n), 0))
    idx = np.arange(n)[:, None] + np.arange(-seq_len + 1, 1)[None, :]
    idx = np.maximum(idx, seg_start[:, None])          # 구간 시작 전은 시작 칸으로 채움
    seq_sorted = values[order][idx]
    out = np.empty_like(seq_sorted)
    out[order] = seq_sorted
    return out


# ---------------------------------------------------------------- XGBoost
def train_xgboost(X_train, Y_train, X_fit=None, Y_fit=None, quick=False):
    """X_train에서 t+1 기준 GridSearch(시간 순 교차검증)로 하이퍼파라미터를 고른 뒤,
    같은 값으로 X_fit(기본: X_train)에 6개 시점 모델을 학습. 반환: (model, best_params)"""
    from sklearn.model_selection import GridSearchCV, TimeSeriesSplit
    from sklearn.multioutput import MultiOutputRegressor
    base_n, base_d, base_lr = CFG["xgb_n_estimators"], CFG["xgb_max_depth"], CFG["xgb_learning_rate"]
    if quick:
        grid = {"n_estimators": [max(10, base_n // 10)], "max_depth": [3], "learning_rate": [base_lr]}
    else:
        grid = {"n_estimators": sorted({max(10, base_n - 100), base_n, base_n + 100}),
                "max_depth": sorted({max(2, base_d - 2), base_d, base_d + 2}),
                "learning_rate": sorted({round(max(0.01, base_lr * 0.5), 3), base_lr})}
    print(f"[XGBoost] 탐색 범위(t+1 기준, TimeSeriesSplit 3겹): {grid}")
    t0 = time.time()
    gs = GridSearchCV(build_xgboost_model(), grid, cv=TimeSeriesSplit(n_splits=3),
                      scoring="neg_root_mean_squared_error")
    gs.fit(X_train, Y_train[:, 0])
    best = gs.best_params_
    print(f"[XGBoost] 최적 파라미터: {best} (교차검증 RMSE {-gs.best_score_:.4f}, {time.time() - t0:.0f}초)")
    model = MultiOutputRegressor(build_xgboost_model(**best))
    model.fit(X_train if X_fit is None else X_fit, Y_train if Y_fit is None else Y_fit)
    return model, best


# ---------------------------------------------------------------- CNN-LSTM
def train_cnn_lstm(X_seq_train, Y_train, X_seq_val, Y_val, epochs=None, patience=None):
    """CNN-LSTM 학습 + 검증 손실 기준 조기 종료. 반환: (model, history, best_epoch)"""
    import torch
    import torch.nn as nn
    from torch.utils.data import DataLoader, TensorDataset
    from module3_prediction.models import CNNLSTMModel
    torch.manual_seed(int(CFG.get("train_random_state", 42)))

    epochs = CFG["cnn_epochs"] if epochs is None else epochs
    patience = int(CFG.get("cnn_patience", 5)) if patience is None else patience
    to_t = lambda a: torch.tensor(a, dtype=torch.float32)
    loader = DataLoader(TensorDataset(to_t(X_seq_train), to_t(Y_train)), batch_size=CFG["cnn_batch_size"], shuffle=True)
    has_val = len(X_seq_val) > 0
    xv, yv = (to_t(X_seq_val), to_t(Y_val)) if has_val else (None, None)

    model = CNNLSTMModel(input_dim=X_seq_train.shape[2], output_dim=Y_train.shape[1])
    opt = torch.optim.Adam(model.parameters(), lr=CFG["cnn_lr"])
    loss_fn = nn.MSELoss()
    print(f"[CNN-LSTM] 입력 (시퀀스 {X_seq_train.shape[1]}칸 × 피처 {X_seq_train.shape[2]}) → 출력 {Y_train.shape[1]}개 시점 | "
          f"hidden={CFG['cnn_hidden_dim']}, layers={CFG['cnn_num_layers']}, kernel={CFG['cnn_kernel_size']}, "
          f"batch={CFG['cnn_batch_size']}, lr={CFG['cnn_lr']}, 최대 {epochs} epoch, 조기 종료 {patience}회"
          + ("" if has_val else " (검증 구간 없음 → 조기 종료 안 함)"))

    history, best_loss, best_state, best_epoch, wait = [], float("inf"), None, 0, 0
    for epoch in range(1, epochs + 1):
        model.train()
        total = 0.0
        for xb, yb in loader:
            opt.zero_grad()
            loss = loss_fn(model(xb), yb)
            loss.backward()
            opt.step()
            total += loss.item() * len(xb)
        train_loss = total / len(loader.dataset)
        val_loss = None
        if has_val:
            model.eval()
            with torch.no_grad():
                val_loss = loss_fn(model(xv), yv).item()
        history.append({"epoch": epoch, "train_loss": round(train_loss, 5),
                        "val_loss": None if val_loss is None else round(val_loss, 5)})
        print(f"  epoch {epoch:3d}  train {train_loss:.4f}" + ("" if val_loss is None else f"  val {val_loss:.4f}"))
        score = val_loss if has_val else train_loss
        if score < best_loss - 1e-6:
            best_loss, best_epoch, wait = score, epoch, 0
            best_state = {k: v.detach().clone() for k, v in model.state_dict().items()}
        else:
            wait += 1
            if has_val and wait >= patience:
                print(f"  → 검증 손실이 {patience}번 연속 나아지지 않아 멈춤 (가장 좋았던 epoch {best_epoch})")
                break
    model.load_state_dict(best_state)
    model.eval()
    return model, history, best_epoch


def predict_cnn(model, X_seq):
    import torch
    with torch.no_grad():
        return model(torch.tensor(X_seq, dtype=torch.float32)).numpy()


# ---------------------------------------------------------------- 전체 실행
def run(log_path=None, freq=None, out_dir=None, report_dir=None, quick=False, cache=True):
    from sklearn.preprocessing import StandardScaler
    from module3_prediction.evaluate import evaluate_predictions

    freq = freq or CFG["freq"]
    feature_df, feature_cols, target_cols = load_feature_table(log_path, freq=freq, cache=cache)
    train_df, val_df, test_df, cuts = split_by_time(feature_df)
    print(f"[분할] 학습 {len(train_df):,}행 / 검증 {len(val_df):,}행 (검증 시작 {cuts['val_cut']}) / "
          f"시험 {len(test_df):,}행 (시험 시작 {cuts['test_cut']}) | 셀 {feature_df.h3_index.nunique()}개 · "
          f"피처 {len(feature_cols)}개 · 타깃 {target_cols} ({freq} 칸)")
    X = lambda d: d[feature_cols].fillna(0).values
    Y = lambda d: d[target_cols].fillna(0).values.astype(float)

    # 1) XGBoost — 학습 구간으로 튜닝, 학습+검증 구간으로 최종 학습 (시험 구간은 두 모델 모두 보지 않음)
    fit_df = pd.concat([train_df, val_df])
    xgb_model, xgb_params = train_xgboost(X(train_df), Y(train_df), X(fit_df), Y(fit_df), quick=quick)
    pred_xgb = np.maximum(0, xgb_model.predict(X(test_df)))

    # 2) CNN-LSTM — 학습 구간 기준 표준화, 셀별 직전 seq_len 칸 시퀀스
    seq_len = 2 if quick else int(CFG.get("cnn_seq_len", 6))
    scaler = StandardScaler().fit(X(train_df))
    all_df = pd.concat([train_df, val_df, test_df])
    seq_all = build_sequences(all_df, scaler.transform(X(all_df)).astype(np.float32), seq_len, freq)
    n_tr, n_va = len(train_df), len(val_df)
    cnn, history, best_epoch = train_cnn_lstm(seq_all[:n_tr], Y(train_df), seq_all[n_tr:n_tr + n_va], Y(val_df),
                                              epochs=3 if quick else None)
    pred_cnn = np.maximum(0, predict_cnn(cnn, seq_all[n_tr + n_va:]))

    # 3) 평가·그래프
    report_dir = report_dir or os.path.join(RESULTS_DIR, freq)
    info = {"freq": freq, "logs": log_path or "data/sim_logs (학습 로그 선택)", "cuts": {k: str(v) for k, v in cuts.items()},
            "rows": {"train": n_tr, "val": n_va, "test": len(test_df)}, "n_features": len(feature_cols),
            "xgb_params": xgb_params, "cnn": {"seq_len": seq_len, "best_epoch": best_epoch, "history": history},
            "quick": quick}
    metrics = evaluate_predictions(test_df, target_cols, {"XGBoost": pred_xgb, "CNN-LSTM": pred_cnn},
                                   freq, report_dir, info)

    # 4) 모델 저장 — 설정의 freq와 같을 때만 saved_models/에 (다른 단위 비교 실험은 결과 폴더에)
    out_dir = out_dir or (SAVED_MODELS if freq == CFG["freq"] and not quick else os.path.join(report_dir, "models"))
    os.makedirs(out_dir, exist_ok=True)
    meta = {"feature_cols": feature_cols, "target_cols": target_cols, "freq": freq, "data_until": str(feature_df.time_bucket.max())}
    xm = metrics["models"]["XGBoost"]
    joblib.dump({"version": 2, "model": xgb_model, **meta,
                 "cells": sorted(feature_df["h3_index"].unique()),
                 "max_lag": CFG["max_lag"], "rolling_short": CFG["rolling_short"], "rolling_long": CFG["rolling_long"],
                 "params": xgb_params, "metrics": {"overall": xm["overall"], "per_horizon": xm["per_horizon"]},
                 "source_logs": [log_path] if log_path else selected_log_paths(DEMAND_LOG_DIR)},
                os.path.join(out_dir, "demand_v2.joblib"))
    import torch
    torch.save({"state_dict": cnn.state_dict(), "input_dim": len(feature_cols), "output_dim": len(target_cols),
                "seq_len": seq_len, "scaler": scaler, **meta}, os.path.join(out_dir, "cnn_lstm_demand.pt"))
    print(f"\n[완료] 모델 저장: {os.path.relpath(out_dir, PROJECT_ROOT)} | 결과·그래프: {os.path.relpath(report_dir, PROJECT_ROOT)}")
    return metrics


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Module 3: XGBoost vs CNN-LSTM (t+1~t+6 동시 예측)")
    ap.add_argument("--logs", default=None, help="학습 로그 글롭 (예: \"data/real_demand_logs/demand_log_*.csv\"). 생략 시 data/sim_logs")
    ap.add_argument("--freq", default=None, help="집계 칸 (예: 5min, 10min). 생략 시 설정값")
    ap.add_argument("--out-dir", default=None, help="모델 저장 폴더 (기본: 설정 freq면 saved_models/, 아니면 결과 폴더)")
    ap.add_argument("--report-dir", default=None, help="지표·그래프 폴더 (기본: results/module3/<freq>/)")
    ap.add_argument("--quick", action="store_true", help="작은 탐색·짧은 학습으로 동작만 확인 (모델은 saved_models에 저장 안 함)")
    ap.add_argument("--no-cache", action="store_true", help="전처리 저장본(data/features/)을 쓰지 않고 새로 전처리")
    a = ap.parse_args()
    run(a.logs, a.freq, a.out_dir, a.report_dir, a.quick, cache=not a.no_cache)
