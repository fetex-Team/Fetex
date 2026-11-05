# -*- coding: utf-8 -*-
"""
[EDA 재현 전용] 예전 합성 호출/외부 데이터(data/generated/calls.csv, external.csv) 생성.

REPORT 부록 A(Module 2 EDA, eda/eda_8w.py)와 scripts/feature_ablation.py가 이 데이터로 만들어져 재현용으로만 남겨 둔다.
**모델 학습에는 쓰지 않는다.** 학습 데이터는 실측 보정 로그(scripts/generate_demand_logs.py →
data/real_demand_logs/)나 시뮬레이션 로그(data/sim_logs/)이고, 학습은 module3_prediction/train.py.

생성 범위: sim_date 전날까지 days일치 (기본 8일 — 지난주 동일 시간대 피처가 실제 값을 갖도록).
날짜별 독립 시드라 시뮬레이션(replay)과 겹치는 날짜의 호출은 완전히 같다.

사용
    python scripts/generate_training_data.py           # 기본 8일치
    python scripts/generate_training_data.py --days 14
이후 (EDA·ablation 재현)
    python eda/eda_8w.py
    python scripts/feature_ablation.py data/generated/calls.csv --external data/generated/external.csv
"""
import argparse
import json
import os
import sys
from pathlib import Path

import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from data.generate import generate_data, save_data  # noqa: E402

META_PATH = Path(os.environ.get('MOBILITY_SIM_DIR',
                 os.path.join(ROOT, 'module1_simulation', 'sumo_config'))) / 'runtime_meta.json'


def main(days: int):
    meta = json.loads(META_PATH.read_text(encoding='utf-8'))
    if 'config' not in meta:
        raise ValueError('build_env.py로 시뮬레이션 환경을 먼저 생성하세요.')
    cfg = meta['config']
    sim_date = pd.Timestamp(cfg['sim_date'])
    start = sim_date - pd.Timedelta(days=days)
    seed = cfg['passenger_seed'] if cfg.get('passenger_seed') is not None else 42
    calls, external = generate_data(meta, start, days, seed=seed)
    out = Path(ROOT) / 'data' / 'generated'
    save_data(calls, external, out)
    end = sim_date - pd.Timedelta(days=1)
    print(f"[저장] {out}  calls.csv {len(calls):,}행, external.csv {len(external):,}행 "
          f"({start.date()} ~ {end.date()}, 시뮬레이션일 {sim_date.date()} 이전까지)")


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--days', type=int, default=8)
    main(ap.parse_args().days)
