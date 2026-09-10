import os
import sys

# [macOS 세그폴트 방지] torch와 xgboost가 각각 다른 OpenMP(libomp)를 들고 와서 GridSearch 중
# "Segmentation fault: 11"로 죽는 문제. torch/xgboost import 전에 반드시 설정해야 함.
os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
os.environ.setdefault("OMP_NUM_THREADS", "1")

# 1. 파이썬 모듈 탐색 경로(sys.path)에 최상위 프로젝트 폴더(ai_mobility_project) 등록
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(CURRENT_DIR))  # train.py는 프로젝트 루트에 위치
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

# 2. sys.path 등록 후 모듈들을 임포트
import torch
import torch.nn as nn
import torch.optim as optim
import numpy as np
import pandas as pd
import joblib
from torch.utils.data import DataLoader, TensorDataset
from sklearn.model_selection import GridSearchCV, TimeSeriesSplit

from config_loader import CFG

# 하위 모듈 불러오기
from module3_prediction.models import CNNLSTMModel, build_xgboost_model
from evaluate import calculate_metrics
from module2_preprocessing.pipeline import build_feature_table
from module2_preprocessing.time_series_prep import time_based_split
from module2_preprocessing.sim_log_recorder import latest_log_path

# 학습 타겟: Module 2 피처 테이블의 y_h1(t+1, 5분 뒤) ~ y_h6(t+6, 30분 뒤).
# 아래 단일 출력 모델은 TARGET 한 개만 사용. 다중 시점(6개) 동시 예측은 y_h1..y_h6 전체를
# y로 넘기고 CNNLSTMModel(output_dim=6) / MultiOutputRegressor(XGB)로 바꾸면 됨 (Module 3 담당).
TARGET = "y_h1"


def prepare_real_sequence_dataset(max_lag=None, log_path=None):
    """
    Module 2 파이프라인(module2_preprocessing.pipeline)으로 학습용 피처 테이블 생성.
    - data/sim_logs/demand_log_*.csv 전체를 이어 붙여 사용 (log_path를 주면 그 파일만)
    - 로그가 하나도 없으면 에러 (가짜 랜덤 데이터 폴백은 제거 — 평가 시 혼동 방지)
    반환: feature_df, feature_cols, target_cols
    """
    logs = [log_path] if log_path else [os.path.join(PROJECT_ROOT, "data", "sim_logs", "demand_log_*.csv")]
    if not log_path and latest_log_path() is None:
        raise FileNotFoundError("data/sim_logs에 호출 로그가 없습니다. 먼저 `python measure_wait_time.py`로 시뮬레이션을 돌려 로그를 만드세요.")
    feature_df = build_feature_table(logs, max_lag=max_lag)
    return feature_df, feature_df.attrs["feature_cols"], feature_df.attrs["target_cols"]


if __name__ == "__main__":
    os.makedirs('saved_models', exist_ok=True)

    # 1. 데이터셋 준비 및 Train/Test 분리 — 시간 기준 (비율은 config.json test_size)
    #    사용법: python train.py [로그CSV경로]  (생략 시 data/sim_logs의 모든 로그)
    log_arg = sys.argv[1] if len(sys.argv) > 1 else None
    feature_df, feature_cols, target_cols = prepare_real_sequence_dataset(log_path=log_arg)
    train_df, _val_df, test_df, cuts = time_based_split(feature_df, CFG["test_size"])
    train_df = train_df.sort_values(['time_bucket', 'h3_index'])  # TimeSeriesSplit이 시간 순서로 fold를 자르도록
    test_df = test_df.sort_values(['time_bucket', 'h3_index'])

    X_train = train_df[feature_cols].fillna(0).values
    y_train = train_df[TARGET].values
    X_test = test_df[feature_cols].fillna(0).values
    y_test = test_df[TARGET].values
    seq_len = CFG["max_lag"]

    print(f"[데이터 분리 완료] Train: {len(X_train)}개 (< {cuts['test_cut']}), Test: {len(X_test)}개 "
          f"| 셀 {feature_df['h3_index'].nunique()}개 | Features: {len(feature_cols)}개 | 타겟 {TARGET} (전체 {target_cols}) | test_size={CFG['test_size']}")

    # ------------------------------------------------------------
    # 2. XGBoost 학습, 하이퍼파라미터 튜닝(GridSearch), 평가 및 저장
    # ------------------------------------------------------------
    print("\n--- [XGBoost] Hyperparameter Tuning (GridSearch) 시작 ---")

    # config.json 값(xgb_n_estimators 등)을 중심으로 좌우 넓게 탐색
    base_n = CFG["xgb_n_estimators"]
    base_depth = CFG["xgb_max_depth"]
    base_lr = CFG["xgb_learning_rate"]

    param_grid = {
        'n_estimators': sorted(set([max(10, base_n - 50), base_n, base_n + 100])),
        'max_depth': sorted(set([max(1, base_depth - 2), base_depth, base_depth + 2])),
        'learning_rate': sorted(set([round(max(0.01, base_lr * 0.3), 3), base_lr]))
    }
    print(f"탐색 범위: {param_grid}")

    base_xgb = build_xgboost_model()
    grid_search = GridSearchCV(base_xgb, param_grid, cv=TimeSeriesSplit(n_splits=3), scoring='neg_root_mean_squared_error')
    grid_search.fit(X_train, y_train)

    best_xgb = grid_search.best_estimator_
    print(f"최적 파라미터: {grid_search.best_params_}")

    # 성능 평가 (calculate_metrics 활용)
    xgb_preds = best_xgb.predict(X_test)
    xgb_metrics = calculate_metrics(y_test, xgb_preds)
    print(f"[XGBoost Test 평가 지표] RMSE: {xgb_metrics['RMSE']:.4f} | MAE: {xgb_metrics['MAE']:.4f} | MAPE: {xgb_metrics['MAPE (%)']:.4f}%")

    joblib.dump({'model': best_xgb, 'feature_cols': feature_cols}, 'saved_models/xgboost_demand.pkl')

    # ------------------------------------------------------------
    # 3. CNN-LSTM 학습, 평가 및 저장 (전체 피처 활용 시퀀스 구조)
    # ------------------------------------------------------------
    print("\n--- [CNN-LSTM] 전체 피처 활용 시퀀스 구조 학습 시작 ---")

    num_features = X_train.shape[1]  # 피처 전체 사용

    X_train_seq = torch.tensor(X_train, dtype=torch.float32).unsqueeze(1)
    y_train_seq = torch.tensor(y_train, dtype=torch.float32).unsqueeze(1)

    X_test_seq = torch.tensor(X_test, dtype=torch.float32).unsqueeze(1)
    y_test_seq = torch.tensor(y_test, dtype=torch.float32).unsqueeze(1)

    train_loader = DataLoader(
        TensorDataset(X_train_seq, y_train_seq),
        batch_size=CFG["cnn_batch_size"],
        shuffle=True
    )

    # hidden_dim, num_layers, kernel_size는 CNNLSTMModel 내부에서 config.json 값을 자동으로 사용
    dl_model = CNNLSTMModel(input_dim=num_features)
    criterion = nn.MSELoss()
    optimizer = optim.Adam(dl_model.parameters(), lr=CFG["cnn_lr"])

    print(f"CNN-LSTM 설정: hidden_dim={CFG['cnn_hidden_dim']}, num_layers={CFG['cnn_num_layers']}, "
          f"kernel_size={CFG['cnn_kernel_size']}, epochs={CFG['cnn_epochs']}, "
          f"batch_size={CFG['cnn_batch_size']}, lr={CFG['cnn_lr']}")

    dl_model.train()
    for epoch in range(CFG["cnn_epochs"]):
        epoch_loss = 0.0
        for x_b, y_b in train_loader:
            optimizer.zero_grad()
            loss = criterion(dl_model(x_b), y_b)
            loss.backward()
            optimizer.step()
            epoch_loss += loss.item()
        if (epoch + 1) % max(1, CFG["cnn_epochs"] // 10) == 0 or epoch == 0:
            print(f"  epoch {epoch + 1}/{CFG['cnn_epochs']} - loss: {epoch_loss / len(train_loader):.4f}")

    # CNN-LSTM 평가
    dl_model.eval()
    with torch.no_grad():
        dl_preds = dl_model(X_test_seq).numpy().flatten()
    dl_metrics = calculate_metrics(y_test, dl_preds)
    print(f"[CNN-LSTM Test 평가 지표] RMSE: {dl_metrics['RMSE']:.4f} | MAE: {dl_metrics['MAE']:.4f} | MAPE: {dl_metrics['MAPE (%)']:.4f}%")

    torch.save({
        'state_dict': dl_model.state_dict(),
        'input_dim': num_features,
        'feature_cols': feature_cols
    }, 'saved_models/cnn_lstm_demand.pt')

    print("\n[완료] 학습, 튜닝, 평가 및 모델 저장 완료!")