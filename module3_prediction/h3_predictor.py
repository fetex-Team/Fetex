"""[Module 3] H3 격자별 실시간 수요 예측기 — demand_v2.joblib으로 다음 6칸(t+1~t+6) 호출 수를 예측한다.

학습(module3_prediction/train.py)과 같은 전처리(Module 2 TimeSeriesPreprocessor·merge_external_data)로
"지금까지 관측된 호출"만 써서 피처를 만들고, 미래 호출은 보지 않는다.
Module 4의 예측 재배치(forecast_repositioner.py)가 이 예측값으로 빈 택시를 옮긴다.

사용
    p = H3DemandPredictor(cells, start_dt)        # 모델 검사: 셀·학습 기간(누수)·전처리 설정
    p.set_history(past_calls)                     # DataFrame(pickup_datetime, h3_index) — 시작 전 호출
    p.add_call(when, cell)                        # 시뮬레이션 중 실제로 나온 호출
    pred = p.predict(now_dt)                      # (셀 수, 6) — cells 순서
"""
from __future__ import annotations

import datetime as _dt
import os

import joblib
import numpy as np
import pandas as pd

from config_loader import CFG, ROOT
from module2_preprocessing.external_data_merge import find_weather_file, load_weather, merge_external_data
from module2_preprocessing.time_series_prep import TimeSeriesPreprocessor

MODEL_PATH = os.path.join(ROOT, "saved_models", "demand_v2.joblib")


class H3DemandPredictor:
    def __init__(self, cells: list, start_dt: _dt.datetime, model_path: str = MODEL_PATH):
        art = joblib.load(model_path)
        if art.get("version") != 2 or len(art.get("target_cols", [])) != 6:
            raise ValueError("module3_prediction/train.py로 만든 demand_v2(t+1~t+6) 모델이 필요합니다.")
        self.cells = list(cells)
        if art["cells"] != self.cells:
            raise ValueError("예측 모델의 H3 칸과 지금 지도의 칸이 다릅니다. 같은 지도로 다시 학습하세요.")
        self.start_dt = start_dt
        if pd.Timestamp(art["data_until"]) > pd.Timestamp(start_dt):
            raise ValueError(f"예측 모델이 시뮬레이션 날짜({start_dt.date()}) 이후 데이터로 학습됐습니다 (누수).")
        # 학습 때 전처리 설정이 지금과 달라도 피처 이름은 같아 조용히 틀린 입력이 들어가므로 여기서 막는다
        diff = {k: (art[k], CFG[k]) for k in ("max_lag", "rolling_short", "rolling_long") if k in art and art[k] != CFG[k]}
        if art.get("freq") and art["freq"] != CFG["freq"]:
            diff["freq"] = (art["freq"], CFG["freq"])
        if diff:
            raise ValueError(f"예측 모델과 지금 전처리 설정이 다릅니다 (모델, 지금): {diff}. 같은 설정으로 다시 학습하세요.")
        self.model, self.features = art["model"], art["feature_cols"]
        self.prep = TimeSeriesPreprocessor()
        self.bucket = pd.Timedelta(self.prep.freq)
        # 지난주 같은 칸(1주 전) + lag/이동평균 여유만큼 과거 칸이 있어야 피처가 학습 때와 같아진다
        self.history_buckets = self.prep.buckets_1w + max(CFG["max_lag"], self.prep.buckets_1h) + 3
        wpath = find_weather_file()
        self.weather = load_weather(wpath) if wpath and os.path.exists(wpath) else None
        self._hist = pd.DataFrame({"pickup_datetime": pd.Series(dtype="datetime64[ns]"), "h3_index": pd.Series(dtype=str)})
        self._live_t, self._live_c = [], []

    def set_history(self, calls: pd.DataFrame):
        """시뮬레이션 시작 전 호출 기록 (pickup_datetime, h3_index)."""
        self._hist = calls[["pickup_datetime", "h3_index"]].reset_index(drop=True)

    def add_call(self, when: _dt.datetime, cell: str):
        """시뮬레이션 중 실제로 나온 호출 하나."""
        self._live_t.append(when)
        self._live_c.append(cell)

    def predict(self, now: _dt.datetime) -> np.ndarray:
        """(칸 수, 6) 다음 6칸 예측. 지금 시각 이전에 끝난 칸까지만 관측으로 쓴다."""
        origin = pd.Timestamp(now).floor(self.bucket)
        last_bucket = origin - self.bucket
        first = origin - self.bucket * self.history_buckets
        calls = pd.concat([self._hist, pd.DataFrame({"pickup_datetime": self._live_t, "h3_index": self._live_c})],
                          ignore_index=True)
        calls = calls[(calls.pickup_datetime >= first) & (calls.pickup_datetime < origin)]
        panel = self.prep.aggregate_demands(calls, start=first, end=last_bucket, all_cells=self.cells)
        panel = merge_external_data(panel, weather=self.weather, time_col="time_bucket", verbose=False)
        frame = self.prep.create_features(panel, dropna=False, add_targets=False, verbose=False)
        cur = frame[frame.time_bucket == last_bucket].sort_values("h3_index")
        missing = set(self.features) - set(cur.columns)
        if missing or len(cur) != len(self.cells):
            raise ValueError(f"예측 피처를 만들 수 없습니다: {sorted(missing)}")
        # 수요 이력 피처(lag/rolling/diff)가 비면 관측 구간이 부족한 것 → 0으로 채워 예측하지 않고 멈춘다
        # (날씨 NaN은 관측 공백이라 학습과 같이 0으로 채움)
        hist_cols = [c for c in self.features if c.startswith(("lag_", "rolling_")) or c == "diff_1"]
        if cur[hist_cols].isna().any().any():
            raise ValueError("예측에 필요한 과거 호출 관측 구간이 부족합니다 (lag/rolling 결측).")
        return np.maximum(0, np.asarray(self.model.predict(cur[self.features].fillna(0.0))))
