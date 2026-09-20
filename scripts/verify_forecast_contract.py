# -*- coding: utf-8 -*-
"""예측 모델·지도·온라인 호출 스트림의 배차 전 계약을 빠르게 검사한다."""
import argparse
import json
import os
import sys
from pathlib import Path

import joblib
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from config_loader import CFG  # noqa: E402
from module2_preprocessing.time_series_prep import TimeSeriesPreprocessor  # noqa: E402


def _path(value):
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def verify(meta_path=None, config_path=None):
    meta_file = Path(meta_path or ROOT / "module1_simulation" / "sumo_config" / "runtime_meta.json")
    meta = json.loads(meta_file.read_text(encoding="utf-8"))
    override = json.loads(Path(config_path).read_text(encoding="utf-8")) if config_path else {}
    cfg = {**CFG, **meta.get("config", {}), **override}
    for key in ("forecast_calls_path", "replay_calls_path"):
        if not cfg.get(key):
            raise ValueError(f"{key}를 설정하세요. forecast와 평가 대상은 같은 호출 스트림을 써야 합니다.")
    if _path(cfg["forecast_calls_path"]).resolve() != _path(cfg["replay_calls_path"]).resolve():
        raise ValueError("forecast_calls_path와 replay_calls_path가 다릅니다. 동일 수요 비교가 아닙니다.")
    for key in ("forecast_calls_path", "forecast_model_path"):
        if not _path(cfg[key]).is_file():
            raise FileNotFoundError(f"{key} 파일을 찾을 수 없습니다: {_path(cfg[key])}")

    calls = pd.read_csv(_path(cfg["forecast_calls_path"]), parse_dates=["pickup_datetime"])
    required = {"pickup_datetime", "from_edge", "to_edge"}
    if missing := required - set(calls.columns):
        raise ValueError(f"온라인 호출 스트림의 누락 컬럼: {sorted(missing)}")
    artifact = joblib.load(_path(cfg["forecast_model_path"]))
    cells = sorted(set(meta["edge_cells"].values()))
    if artifact.get("version") != 2 or artifact.get("cells") != cells:
        raise ValueError("모델 version 또는 H3 셀이 지도와 다릅니다. 데이터 생성 후 재학습하세요.")
    start = pd.Timestamp(cfg["scenario_date"]) + pd.Timedelta(hours=cfg["sim_start_hour"])
    if pd.Timestamp(artifact["data_until"]) >= start:
        raise ValueError("모델 학습 종료가 시뮬레이션 시작보다 늦습니다. 미래 누수 위험이 있습니다.")
    prep = TimeSeriesPreprocessor()
    history_start = start - pd.Timedelta(minutes=5 * prep.required_history_buckets)
    if calls["pickup_datetime"].min() > history_start:
        raise ValueError("온라인 호출 스트림이 지난주 동일 시각 피처에 필요한 이력을 덮지 못합니다.")
    print("[PASS] 모델·H3 지도·호출 스트림·누수 차단·1주 이력 계약이 일치합니다.")
    print(f"  model data_until: {artifact['data_until']}")
    print(f"  simulation start: {start}")
    print(f"  required history: {prep.required_history_buckets} buckets")
    return True


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--meta", help="runtime_meta.json 경로")
    parser.add_argument("--config-path", help="적용할 예측 preset JSON 경로")
    args = parser.parse_args()
    verify(args.meta, args.config_path)
