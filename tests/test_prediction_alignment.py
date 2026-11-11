"""예측 시점 정렬 검사 — 분할 경계 purge, 계절 나이브(지난주 t+k), 같은 시각 평균 기준 모델."""
import numpy as np
import pandas as pd

from module2_preprocessing.time_series_prep import time_based_split
from module3_prediction.evaluate import NAIVE_NAME, SLOT_NAME, SNAIVE_NAME, baseline_predictions

FREQ = "10min"


def _table(n_buckets=100, cells=("a", "b")):
    t = pd.date_range("2026-07-01", periods=n_buckets, freq=FREQ)
    rows = [{"time_bucket": tt, "h3_index": c, "v": i} for i, tt in enumerate(t) for c in cells]
    return pd.DataFrame(rows)


def test_purge_drops_rows_before_each_cut():
    df = _table(100)
    tr0, va0, te0, cut0 = time_based_split(df, 0.2, 0.1)
    tr, va, te, cut = time_based_split(df, 0.2, 0.1, purge=6)
    assert cut == cut0 and len(te) == len(te0)            # 경계·시험 구간은 그대로
    step = pd.Timedelta(FREQ)
    assert tr.time_bucket.max() == cut["val_cut"] - step * 7   # val_cut 직전 6칸 제외
    assert va.time_bucket.max() == cut["test_cut"] - step * 7  # test_cut 직전 6칸 제외
    assert va.time_bucket.min() == cut["val_cut"]
    assert len(tr0) - len(tr) == 6 * 2 and len(va0) - len(va) == 6 * 2
    # purge=0이면 예전과 완전히 같음
    tr1, va1, te1, _ = time_based_split(df, 0.2, 0.1, purge=0)
    assert tr1.equals(tr0) and va1.equals(va0) and te1.equals(te0)


def test_purge_without_val():
    df = _table(50)
    tr, va, te, cut = time_based_split(df, 0.2, purge=3)
    assert va.empty
    assert tr.time_bucket.max() == cut["test_cut"] - pd.Timedelta(FREQ) * 4


def test_seasonal_naive_uses_last_week_of_target_bucket():
    # same_time_last_week를 행마다 다른 값(시각 순번)으로 두고, 시점 k가 (t+k) 행의 값을 가져오는지 본다
    df = _table(20, cells=("a", "b"))
    df["same_time_last_week"] = df["v"] * 10 + (df["h3_index"] == "b")
    df["lag_1"] = 0.0
    out = baseline_predictions(df, 3, freq=FREQ)
    P = out[SNAIVE_NAME]
    for i in (0, 5, 16):
        for cell in ("a", "b"):
            r = df.index[(df.v == i) & (df.h3_index == cell)][0]
            for k in (1, 2, 3):
                j = i + k if i + k < 20 else i        # 시험 구간 끝을 넘으면 행 t 값으로 대신
                assert P[r, k - 1] == j * 10 + (cell == "b")
    assert NAIVE_NAME in out


def test_slot_mean_baseline():
    t_train = pd.date_range("2026-07-06", periods=3, freq="7D")   # 월요일 3주, 같은 시각
    train = pd.DataFrame({"time_bucket": t_train, "h3_index": "a", "is_weekend": 0, "minute_of_day": 0,
                          "y_h1": [1.0, 2.0, 3.0]})
    test = pd.DataFrame({"time_bucket": [pd.Timestamp("2026-07-27"), pd.Timestamp("2026-07-27 00:10")],
                         "h3_index": ["a", "a"], "is_weekend": [0, 0], "minute_of_day": [0, 10], "y_h1": [0.0, 0.0]})
    out = baseline_predictions(test, 1, freq=FREQ, train_df=train, target_cols=["y_h1"])
    P = out[SLOT_NAME]
    assert P[0, 0] == 2.0           # 같은 셀·평일·00:00 평균
    assert P[1, 0] == 2.0           # 그 시각이 학습에 없으면 셀 전체 평균으로 대신
    assert SLOT_NAME not in baseline_predictions(test, 1, freq=FREQ)   # train_df 없으면 생략
