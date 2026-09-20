# -*- coding: utf-8 -*-
"""저장된 30분 수요 예측 모델을 시간 순서 test 구간에서 재평가한다.

모델을 다시 학습하지 않고 저장된 artifact의 피처·셀·전처리 계약을 확인한 뒤,
전체/예측시점/시간대/H3 셀별 RMSE·MAE·MAPE·WAPE를 JSON과 CSV로 남긴다.
"""
import argparse
import json
import os
import sys
from pathlib import Path

import joblib
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from config_loader import CFG  # noqa: E402
from evaluate import calculate_metrics  # noqa: E402
from module2_preprocessing.pipeline import build_feature_table  # noqa: E402
from module2_preprocessing.time_series_prep import time_based_split  # noqa: E402


def _default_logs():
    for name in ("train_calls.csv", "calls.csv"):
        candidate = ROOT / "data" / "generated" / name
        if candidate.is_file():
            return [str(candidate)]
    return [str(ROOT / "data" / "sim_logs" / "demand_log_*.csv")]


def _paths(value):
    return [str(Path(v)) for v in value] if value else _default_logs()


def evaluate(logs=None, external=None, model_path=None, output_dir=None):
    model_file = Path(model_path or ROOT / "saved_models" / "demand_v2.joblib")
    if not model_file.is_file():
        raise FileNotFoundError(f"모델을 찾을 수 없습니다: {model_file}")
    artifact = joblib.load(model_file)
    if artifact.get("version") != 2:
        raise ValueError("지원하지 않는 모델 artifact입니다. scripts/train_dispatch_model.py로 다시 학습하세요.")

    weather = None
    if external:
        from module4_dispatch.forecast_dispatcher import _external_to_weather
        weather = _external_to_weather(pd.read_csv(external, parse_dates=["time_bucket"]))
        if weather is None:
            raise ValueError("외부 관측에는 time_bucket, temperature 컬럼이 필요합니다.")
    feat = build_feature_table(_paths(logs), weather=weather)
    _train, _val, test, cuts = time_based_split(feat, CFG["test_size"], CFG.get("validation_size", 0.0))
    features = list(artifact.get("feature_cols", []))
    targets = list(artifact.get("target_cols", []))
    missing = sorted(set(features + targets) - set(test.columns))
    if missing:
        raise ValueError(f"모델·전처리 계약이 다릅니다. 누락 컬럼: {missing}")
    if sorted(test["h3_index"].unique()) != list(artifact.get("cells", [])):
        raise ValueError("평가 데이터의 H3 셀이 모델 학습 셀과 다릅니다.")

    actual = test[targets].to_numpy(dtype=float)
    predicted = np.maximum(0, np.asarray(artifact["model"].predict(test[features].fillna(0.0))))
    if predicted.shape != actual.shape:
        raise ValueError(f"예측 출력 형태가 다릅니다: {predicted.shape}, 기대값 {actual.shape}")
    persistence = np.repeat(test[["lag_1"]].fillna(0).to_numpy(dtype=float), len(targets), axis=1)

    overall = calculate_metrics(actual, predicted)
    naive = calculate_metrics(actual, persistence)
    by_horizon = []
    for index, target in enumerate(targets):
        by_horizon.append({"horizon": target, **calculate_metrics(actual[:, index], predicted[:, index])})
    by_cell = []
    for cell, group in test.assign(_row=np.arange(len(test))).groupby("h3_index", sort=True):
        rows = group["_row"].to_numpy()
        by_cell.append({"h3_index": cell, **calculate_metrics(actual[rows], predicted[rows])})
    by_hour = []
    for hour, group in test.assign(_row=np.arange(len(test))).groupby(test["time_bucket"].dt.hour, sort=True):
        rows = group["_row"].to_numpy()
        by_hour.append({"hour": int(hour), **calculate_metrics(actual[rows], predicted[rows])})

    destination = Path(output_dir or ROOT / "results" / "prediction" / "dispatch_model")
    destination.mkdir(parents=True, exist_ok=True)
    payload = {
        "source_logs": _paths(logs), "external": external, "model_path": str(model_file),
        "model_data_until": artifact.get("data_until"),
        "splits": {key: str(value) for key, value in cuts.items()},
        "rows": int(len(test)), "cells": list(artifact["cells"]), "targets": targets,
        "model": overall, "persistence_lag_1": naive,
        "by_horizon": by_horizon, "by_h3_index": by_cell, "by_hour": by_hour,
    }
    (destination / "metrics.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    pd.DataFrame(by_horizon).to_csv(destination / "metrics_by_horizon.csv", index=False)
    pd.DataFrame(by_cell).to_csv(destination / "metrics_by_h3_index.csv", index=False)
    pd.DataFrame(by_hour).to_csv(destination / "metrics_by_hour.csv", index=False)
    preview = test[["time_bucket", "h3_index"]].reset_index(drop=True).copy()
    for i, target in enumerate(targets):
        preview[f"actual_{target}"] = actual[:, i]
        preview[f"prediction_{target}"] = predicted[:, i]
    preview.to_csv(destination / "predictions.csv", index=False)
    print(f"[평가] RMSE {overall['RMSE']:.4f} | MAE {overall['MAE']:.4f} | "
          f"MAPE {overall['MAPE (%)']} | WAPE {overall['WAPE (%)']}")
    print(f"[저장] {destination}")
    return payload


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("logs", nargs="*", help="호출 로그 CSV (미지정 시 data/generated 우선)")
    parser.add_argument("--external", help="외부 관측 CSV")
    parser.add_argument("--model", help="demand_v2.joblib 경로")
    parser.add_argument("--output-dir", help="결과 폴더")
    args = parser.parse_args()
    evaluate(args.logs or None, args.external, args.model, args.output_dir)
