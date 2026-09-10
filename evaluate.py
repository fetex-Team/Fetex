import os
import sys
import joblib
import torch
import numpy as np
import pandas as pd
from sklearn.metrics import mean_squared_error, mean_absolute_error
from sklearn.model_selection import train_test_split

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from config_loader import CFG
from module3_prediction.models import CNNLSTMModel


def calculate_metrics(y_true: np.ndarray, y_pred: np.ndarray) -> dict:
    """
    RMSE, MAE, MAPE 성능 평가 지표 계산 (MAPE 0 나누기 방지 처리)
    """
    rmse = np.sqrt(mean_squared_error(y_true, y_pred))
    mae = mean_absolute_error(y_true, y_pred)
    
    # 0으로 나누는 상황 방지를 위한 처리 (Small epsilon)
    epsilon = 1e-5
    mape = np.mean(np.abs((y_true - y_pred) / np.maximum(np.abs(y_true), epsilon))) * 100

    return {
        "RMSE": round(float(rmse), 4),
        "MAE": round(float(mae), 4),
        "MAPE (%)": round(float(mape), 4)
    }


def evaluate_saved_models():
    """저장된 XGBoost와 CNN-LSTM 모델을 로드하여 Test 셋에서 성능을 비교 평가"""
    xgb_path = os.path.join(PROJECT_ROOT, 'saved_models/xgboost_demand.pkl')
    dl_path = os.path.join(PROJECT_ROOT, 'saved_models/cnn_lstm_demand.pt')

    if not os.path.exists(xgb_path) or not os.path.exists(dl_path):
        print("[오류] 저장된 모델 파일이 없습니다. 먼저 python train.py를 실행하세요.")
        return

    # 1. 평가용 데이터셋 생성 (train.py와 동일한 파이프라인)
    from train import prepare_real_sequence_dataset
    X_mat, y_vec, feature_cols, _ = prepare_real_sequence_dataset()
    _, X_test, _, y_test = train_test_split(
        X_mat, y_vec, test_size=CFG["test_size"], random_state=CFG.get("train_random_state", 42), shuffle=False
    )

    # 2. XGBoost 평가
    xgb_data = joblib.load(xgb_path)
    xgb_model = xgb_data['model']
    xgb_preds = xgb_model.predict(X_test)
    xgb_metrics = calculate_metrics(y_test, xgb_preds)

    # 3. CNN-LSTM 평가
    dl_data = torch.load(dl_path, weights_only=False)
    dl_model = CNNLSTMModel(input_dim=dl_data['input_dim'])
    dl_model.load_state_dict(dl_data['state_dict'])
    dl_model.eval()

    scaler = dl_data.get('scaler', None)
    if scaler is not None:
        X_test_scaled = scaler.transform(X_test)
    else:
        X_test_scaled = X_test

    X_test_seq = torch.tensor(X_test_scaled, dtype=torch.float32).unsqueeze(1)
    with torch.no_grad():
        dl_preds = dl_model(X_test_seq).numpy().flatten()
    dl_metrics = calculate_metrics(y_test, dl_preds)

    # 4. 성능 비교표 출력
    print("\n" + "=" * 62)
    print(f"{'[AI 수요 예측 모델 성능 비교 결과]':^56}")
    print("=" * 62)
    print(f"{'평가 지표 (Metric)':<20} | {'XGBoost (머신러닝)':<18} | {'CNN-LSTM (딥러닝)':<18}")
    print("-" * 62)
    print(f"{'RMSE (낮을수록 우수)':<20} | {xgb_metrics['RMSE']:<18} | {dl_metrics['RMSE']:<18}")
    print(f"{'MAE  (낮을수록 우수)':<20} | {xgb_metrics['MAE']:<18} | {dl_metrics['MAE']:<18}")
    print(f"{'MAPE (오차율 %)':<20} | {str(xgb_metrics['MAPE (%)']) + '%':<18} | {str(dl_metrics['MAPE (%)']) + '%':<18}")
    print("=" * 62)

    # 5. 판정 결과
    if xgb_metrics['RMSE'] <= dl_metrics['RMSE']:
        winner = "XGBoost (머신러닝)"
        reason = "소규모 시계열 정형 데이터에서 과적합 없이 안정적인 성능을 보였습니다."
    else:
        winner = "CNN-LSTM (딥러닝)"
        reason = "시계열 패턴과 비선형적 관계를 효과적으로 학습했습니다."

    print(f"[추천 모델] {winner}")
    print(f"[분석 사유] {reason}")
    print("=" * 62 + "\n")


if __name__ == "__main__":
    evaluate_saved_models()