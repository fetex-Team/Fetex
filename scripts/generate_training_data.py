# -*- coding: utf-8 -*-
"""재현 가능한 학습·온라인 관측 스트림을 한 번에 만든다.

같은 합성 생성기를 쓰더라도 학습 구간과 평가일을 섞으면 미래 누수가 된다. 이
스크립트는 평가일 전날까지만 ``train_*``에 저장하고, 평가일 호출을 포함한
``stream_*``은 온라인 배차가 과거 시점까지만 읽도록 제공한다.

사용 예시
----------
python scripts/generate_training_data.py --days 35 --scenario-date 2026-09-18
python scripts/train_dispatch_model.py data/generated/train_calls.csv \
    --external data/generated/train_external.csv
"""
import argparse
import json
import os
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from data.generate import generate_data  # noqa: E402


def _meta_path(value: str | None) -> Path:
    return Path(value or os.environ.get(
        "MOBILITY_SIM_DIR", ROOT / "module1_simulation" / "sumo_config"
    )) / "runtime_meta.json"


def _scenario_date(meta: dict, explicit: str | None) -> pd.Timestamp:
    raw = explicit or meta.get("config", {}).get("scenario_date") or meta.get("config", {}).get("sim_date")
    if not raw:
        raise ValueError("scenario_date가 없습니다. --scenario-date YYYY-MM-DD를 지정하세요.")
    return pd.Timestamp(raw).normalize()


def main(days: int, scenario_date: str | None = None, meta_dir: str | None = None,
         output: str | None = None, seed: int | None = None) -> dict:
    if days < 8:
        raise ValueError("지난주 동일 시각 피처를 위해 --days는 최소 8이어야 합니다.")
    meta_path = _meta_path(meta_dir)
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    if "config" not in meta:
        raise ValueError(f"{meta_path}: build_env.py가 만든 runtime_meta.json이 아닙니다.")

    evaluation_day = _scenario_date(meta, scenario_date)
    start = evaluation_day - pd.Timedelta(days=days)
    random_seed = int(seed if seed is not None else meta["config"].get("passenger_seed") or 42)

    # 학습은 평가일 직전까지, stream은 평가일 전체를 포함한다. 날짜별 독립 RNG라
    # 같은 날짜의 기록은 두 파일에서 완전히 일치한다.
    stream_calls, stream_external = generate_data(meta, start, days + 1, seed=random_seed)
    train_calls = stream_calls[stream_calls["pickup_datetime"] < evaluation_day].copy()
    train_external = stream_external[stream_external["time_bucket"] < evaluation_day].copy()
    if train_calls.empty:
        raise ValueError("학습 호출이 0건입니다. 지도·synthetic_rate 설정을 확인하세요.")

    destination = Path(output or ROOT / "data" / "generated")
    destination.mkdir(parents=True, exist_ok=True)
    train_calls.to_csv(destination / "train_calls.csv", index=False)
    train_external.to_csv(destination / "train_external.csv", index=False)
    stream_calls.to_csv(destination / "stream_calls.csv", index=False)
    stream_external.to_csv(destination / "stream_external.csv", index=False)
    manifest = {
        "source": "synthetic",
        "seed": random_seed,
        "meta_path": str(meta_path),
        "scenario_date": str(evaluation_day.date()),
        "training_start": str(start.date()),
        "training_end": str((evaluation_day - pd.Timedelta(days=1)).date()),
        "training_calls": int(len(train_calls)),
        "stream_calls": int(len(stream_calls)),
        "cells": sorted(set(meta["edge_cells"].values())),
    }
    (destination / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        f"[저장] {destination}\n"
        f"  학습: {len(train_calls):,} 호출 ({manifest['training_start']} ~ {manifest['training_end']})\n"
        f"  온라인 스트림: {len(stream_calls):,} 호출 (평가일 {manifest['scenario_date']} 포함)"
    )
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=35, help="평가일 이전 학습 일수 (최소 8, 기본 35)")
    parser.add_argument("--scenario-date", help="평가 시뮬레이션 날짜 YYYY-MM-DD")
    parser.add_argument("--meta-dir", help="runtime_meta.json이 들어 있는 SUMO 디렉터리")
    parser.add_argument("--output", help="출력 디렉터리 (기본 data/generated)")
    parser.add_argument("--seed", type=int, help="합성 데이터 시드")
    args = parser.parse_args()
    main(args.days, args.scenario_date, args.meta_dir, args.output, args.seed)
