import numpy as np
from sklearn.metrics import mean_absolute_error, mean_squared_error

def calculate_metrics(y_true: np.ndarray, y_pred: np.ndarray) -> dict:
    """수요 예측 지표를 계산한다.

    수요 패널은 0 수요 칸이 많다. 0을 작은 수로 나누는 방식의 MAPE는 숫자를
    폭발시켜 모델 비교를 왜곡하므로, MAPE는 실제 수요가 양수인 칸에서만 계산한다.
    전체 수요 오차는 WAPE도 함께 제공한다.
    """
    actual = np.asarray(y_true, dtype=float).reshape(-1)
    predicted = np.asarray(y_pred, dtype=float).reshape(-1)
    if actual.shape != predicted.shape:
        raise ValueError("y_true와 y_pred의 형태가 다릅니다.")
    if not len(actual):
        raise ValueError("비어 있는 배열에는 지표를 계산할 수 없습니다.")
    if not (np.isfinite(actual).all() and np.isfinite(predicted).all()):
        raise ValueError("지표 입력에는 NaN 또는 무한대가 있으면 안 됩니다.")

    error = np.abs(actual - predicted)
    positive = np.abs(actual) > 0
    denominator = np.abs(actual).sum()
    mape = float((error[positive] / np.abs(actual[positive])).mean() * 100) if positive.any() else None
    wape = float(error.sum() / denominator * 100) if denominator else None
    return {
        "RMSE": round(float(np.sqrt(mean_squared_error(actual, predicted))), 4),
        "MAE": round(float(mean_absolute_error(actual, predicted)), 4),
        "MAPE (%)": round(mape, 4) if mape is not None else None,
        "MAPE_valid_count": int(positive.sum()),
        "zero_target_count": int((~positive).sum()),
        "WAPE (%)": round(wape, 4) if wape is not None else None,
    }

if __name__ == "__main__":
    y_real = np.array([10, 15, 20, 25, 30])
    y_hat = np.array([11, 14, 22, 24, 28])

    metrics = calculate_metrics(y_real, y_hat)
    print("--- 평가 지표 테스트 ---")
    print(metrics)
