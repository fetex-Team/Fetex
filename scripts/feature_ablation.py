# -*- coding: utf-8 -*-
"""
[Module 2] 피처 그룹별 ablation — "어떤 파생 변수가 예측 정확도를 올리는가"를 같은 데이터·같은 분할·같은 모델로 비교한다.

Data Spec 3장 피처 34개(기준) 위에 9/17 강화분(휴일 4, 날씨 파생 3, 어제 동일칸 2)을 그룹 단위로 누적 추가하며
시간순 분할(뒤 20% test)에서 6개 타깃(y_h1~y_h6)의 MAE·RMSE를 잰다. 나이브(직전 칸 값 유지)와 계절 나이브(지난주 동일칸)도 같이 둔다.

    python scripts/feature_ablation.py data/generated/calls.csv --external data/generated/external.csv
    python scripts/feature_ablation.py data/sim_logs/demand_log_*.csv            # 시뮬 로그 + data/external 날씨 파일
    옵션: --test-size 0.2  --model auto|xgb|hgb  --out data/eda/feature_ablation.md

모델: xgboost가 있으면 운영 모델과 같은 XGBRegressor(config 값), 없으면 sklearn HistGradientBoostingRegressor(같은 계열).
결과 표는 markdown으로 저장하고 REPORT 2.5절 "피처 유효성"에 그대로 붙인다.
"""
import os
import sys
import glob
import argparse
import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from config_loader import CFG  # noqa: E402
from module2_preprocessing.pipeline import build_feature_table  # noqa: E402
from module2_preprocessing.time_series_prep import time_based_split  # noqa: E402
from module2_preprocessing.time_features import TIME_FEATURE_COLS  # noqa: E402
from module2_preprocessing.external_data_merge import WEATHER_DERIVED_COLS  # noqa: E402

CAL_BASE = ["year", "month", "day", "hour", "minute", "minute_of_day", "dayofweek", "is_weekend",
            "is_holiday", "season", "time_slot_code", "hour_sin", "hour_cos", "dow_sin", "dow_cos"]
CAL_STRICT = [c for c in TIME_FEATURE_COLS if c not in CAL_BASE]          # is_public_holiday, before/after_off, off_streak_len
WEATHER_BASE = ["temperature", "precipitation", "wind_speed", "is_rain", "weather_missing", "weather_interpolated"]


def feature_groups(max_lag: int, rs: int, rl: int) -> list:
    """(그룹 이름, 이 그룹이 추가하는 피처) — 순서대로 누적."""
    return [
        ("lag 1~%d" % max_lag, [f"lag_{i}" for i in range(1, max_lag + 1)]),
        ("+ rolling/std/diff/지난주", [f"rolling_mean_{rs}", f"rolling_mean_{rl}", "rolling_mean_1h", "rolling_std_1h", "diff_1",
                                   "same_time_last_week", "has_last_week"]),
        ("+ 달력 기본 15", CAL_BASE),
        ("+ 날씨 기본 6   [= 기존 34피처]", WEATHER_BASE),
        ("+ 휴일 강화 4 (공휴일·전후일·연휴길이)", CAL_STRICT),
        ("+ 날씨 파생 3 (3h 강수합·연속강수·기온편차)", WEATHER_DERIVED_COLS),
        ("+ 어제 동일칸 2", ["same_time_yesterday", "has_yesterday"]),
    ]


def make_model(kind: str):
    if kind in ("auto", "xgb"):
        try:
            from xgboost import XGBRegressor
            from sklearn.multioutput import MultiOutputRegressor
            return "xgboost", lambda: MultiOutputRegressor(XGBRegressor(
                n_estimators=CFG.get("xgb_n_estimators", 100), max_depth=CFG.get("xgb_max_depth", 6),
                learning_rate=CFG.get("xgb_learning_rate", 0.1), random_state=42, verbosity=0))
        except ImportError:
            if kind == "xgb":
                raise
    from sklearn.ensemble import HistGradientBoostingRegressor
    from sklearn.multioutput import MultiOutputRegressor
    return "sklearn HistGradientBoosting", lambda: MultiOutputRegressor(HistGradientBoostingRegressor(
        max_iter=CFG.get("xgb_n_estimators", 100), max_depth=CFG.get("xgb_max_depth", 6),
        learning_rate=CFG.get("xgb_learning_rate", 0.1), random_state=42))


def external_to_hourly_weather_csv(external_csv: str) -> str:
    """합성 외부 관측(time_bucket×h3_index, 5분) → 시각별 날씨 표(셀 평균, 5분 간격 유지). forecast_dispatcher._external_to_weather와 같은 축약.
    (train_dispatch_model.external_to_weather_csv와 같은 역할이지만 xgboost 없이도 돌도록 여기 둔다.)"""
    ext = pd.read_csv(external_csv)
    ext["time_bucket"] = pd.to_datetime(ext["time_bucket"])
    w = ext.groupby("time_bucket", as_index=False)[["temperature", "precipitation"]].mean().rename(columns={"time_bucket": "time"})
    # 5분 관측을 그대로 둔다 — merge_external_data가 관측 간격을 추정해 각 5분 칸에 자기 관측을 붙인다
    w["wind_speed"] = np.nan
    out = os.path.join(ROOT, "data", "external", "weather_from_external_5min.csv")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    w.to_csv(out, index=False)
    print(f"[외부 관측 → 시각별 날씨] {external_csv} → {out} ({len(w)}행)")
    return out


def metrics(y, p) -> dict:
    y, p = np.asarray(y, float), np.asarray(p, float)
    err = np.abs(y - p)
    pos = y > 0
    return {"MAE": float(err.mean()), "RMSE": float(np.sqrt(((y - p) ** 2).mean())),
            "WAPE": float(err.sum() / y.sum() * 100) if y.sum() else float("nan"),
            "under_pos": float((p[pos] < y[pos]).mean() * 100) if pos.any() else float("nan")}   # 양수 구간 과소예측률


def run(logs, external=None, test_size=0.2, model_kind="auto", out=None, weather_path=None):
    if external:
        weather_path = external_to_hourly_weather_csv(external)
    feat = build_feature_table(logs, weather_path=weather_path, verbose=True)
    targets = feat.attrs["target_cols"]
    train, _, test, cuts = time_based_split(feat, test_size)
    Y_tr, Y_te = train[targets].values, test[targets].values
    print(f"[분할] train {len(train):,}행 (< {cuts['test_cut']}), test {len(test):,}행, 타깃 6개, 0타깃 비율 {(Y_te == 0).mean() * 100:.1f}%")
    model_name, factory = make_model(model_kind)
    print(f"[모델] {model_name}")

    rows = []
    # 0 예측: 희소 카운트에서 MAE의 하한선(중앙값 예측). 모델은 RMSE(평균)를 줄이므로 MAE는 이보다 클 수 있다 → RMSE·WAPE로 판단
    zeros = np.zeros_like(Y_te, dtype=float)
    rows.append(("전부 0 예측 (희소 데이터 MAE 하한)", 1, metrics(Y_te, zeros), metrics(Y_te[:, 0], zeros[:, 0])))
    # 나이브: 모든 시점 = 직전 칸 값(lag_1) / 계절 나이브: 지난주 동일칸(t+h의 지난주 값 ≈ 여기선 현재 칸의 지난주 값으로 근사)
    naive = np.repeat(test[["lag_1"]].fillna(0).values, 6, axis=1)
    rows.append(("나이브 (다음 30분 = 직전 5분 값)", 1, metrics(Y_te, naive), metrics(Y_te[:, 0], naive[:, 0])))
    if test["has_last_week"].mean() > 0.5:
        snaive = np.repeat(test[["same_time_last_week"]].values, 6, axis=1)
        rows.append(("계절 나이브 (지난주 동일칸)", 1, metrics(Y_te, snaive), metrics(Y_te[:, 0], snaive[:, 0])))

    cols = []
    for name, add in feature_groups(CFG["max_lag"], CFG["rolling_short"], CFG["rolling_long"]):
        add = [c for c in add if c in feat.columns]
        cols = cols + [c for c in add if c not in cols]
        m = factory(); m.fit(train[cols].fillna(0).values, Y_tr)
        pred = np.maximum(0, m.predict(test[cols].fillna(0).values))
        rows.append((name, len(cols), metrics(Y_te, pred), metrics(Y_te[:, 0], pred[:, 0])))
        print(f"  {name:<40} {len(cols):>3}개  MAE {rows[-1][2]['MAE']:.4f}  RMSE {rows[-1][2]['RMSE']:.4f}  WAPE {rows[-1][2]['WAPE']:.1f}%")

    base_mae = next(r[2]["MAE"] for r in rows if "기존 34" in r[0])
    lines = [f"## 피처 ablation — {os.path.basename(str(logs))}",
             f"기간 {feat.time_bucket.min()} ~ {feat.time_bucket.max()}, train {len(train):,} / test {len(test):,}행 (시간순, 뒤 {int(test_size * 100)}%), "
             f"셀 {feat.h3_index.nunique()}개, 모델 {model_name}, 타깃 y_h1~y_h6 (지표는 6개 평균, h1은 5분 뒤만)", "",
             "| 피처 그룹 (누적) | 피처 수 | MAE | RMSE | WAPE | 과소예측률(양수) | h1 MAE | 기존 34 대비 MAE |",
             "|---|---:|---:|---:|---:|---:|---:|---:|"]
    for name, n, m6, m1 in rows:
        delta = "" if n == 1 else f"{(m6['MAE'] - base_mae) / base_mae * 100:+.1f}%"
        lines.append(f"| {name} | {n} | {m6['MAE']:.4f} | {m6['RMSE']:.4f} | {m6['WAPE']:.1f}% | {m6['under_pos']:.1f}% | {m1['MAE']:.4f} | {delta} |")
    lines += ["", "- WAPE = Σ|오차| / Σ실제 (전체 규모 대비 오차), 과소예측률 = 실제>0인 칸에서 예측<실제 비율 (배차 부족으로 직결)",
              "- 누수 방지: 모든 피처는 shift(1) 이후·격자별 계산, 날씨는 backward 결합. 분할은 time_bucket 기준."]
    md = "\n".join(lines)
    if out:
        os.makedirs(os.path.dirname(out), exist_ok=True)
        with open(out, "w", encoding="utf-8") as f:
            f.write(md + "\n")
        print(f"[저장] {out}")
    return md, rows


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("logs", nargs="+")
    ap.add_argument("--external", default=None, help="합성 외부 관측 CSV (time_bucket×h3_index) → 시간별 날씨로 변환해 결합")
    ap.add_argument("--weather", default=None, help="시간별 날씨 CSV 직접 지정")
    ap.add_argument("--test-size", type=float, default=0.2)
    ap.add_argument("--model", default="auto", choices=["auto", "xgb", "hgb"])
    ap.add_argument("--out", default=os.path.join(ROOT, "data", "eda", "feature_ablation.md"))
    a = ap.parse_args()
    logs = []
    for p in a.logs:
        logs += sorted(glob.glob(p)) if any(ch in p for ch in "*?[") else [p]
    md, _ = run(logs, a.external, a.test_size, a.model, a.out, a.weather)
    print("\n" + md)
