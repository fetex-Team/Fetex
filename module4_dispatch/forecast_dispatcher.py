"""예측 재배치 공용 도우미.

- allocate_targets: 예측 수요 × 배율 비례로 정수 택시 수를 나눈다 (최대 나머지 방식) — forecast_repositioner가 사용
- _external_to_weather: 합성 외부 관측(time_bucket×h3_index) → 시각별 날씨 표 — scripts/train_dispatch_model.py가 사용

예전 ForecastDispatcher 클래스는 forecast_repositioner.ForecastRepositioner로 대체되어 제거했다
(5분 고정이라 10분 설정에서 동작하지 않았고, 이력 창이 짧아 same_time_* 피처가 항상 0이었음).
"""
import numpy as np
import pandas as pd
from module2_preprocessing.external_data_merge import WEATHER_COLS


def allocate_targets(demand, multipliers, fleet_size):
    """한정된 빈 택시 수를 초과하지 않도록 최대 나머지 방식으로 정수 배분한다."""
    weights = np.asarray(demand, dtype=float) * np.asarray(multipliers, dtype=float)
    if fleet_size < 0 or not np.isfinite(weights).all() or (weights < 0).any():
        raise ValueError('목표 배분 입력을 확인하세요.')
    if weights.sum() == 0:
        return np.zeros(len(weights), dtype=int)
    quotas = weights / weights.sum() * fleet_size
    targets = np.floor(quotas).astype(int)
    order = np.argsort(-(quotas - targets), kind='stable')
    targets[order[:fleet_size - targets.sum()]] += 1
    return targets


def _external_to_weather(external):
    """합성 외부 관측(time_bucket×h3_index)을 시간별 날씨 표(time 기준)로 변환한다.

    Module 2의 merge_external_data는 '시각 → 그 이전 가장 최근 관측'(merge_asof backward)
    방식이라, 셀 구분 없는 시간별 표만 있으면 된다. 셀별 값은 시각 평균으로 축약한다.
    """
    if external is None or len(external) == 0:
        return None
    if 'time_bucket' not in external or 'temperature' not in external:
        return None
    cols = [c for c in ('temperature', 'precipitation', 'wind_speed') if c in external]
    w = (external.groupby('time_bucket', as_index=False)[cols]
         .mean().rename(columns={'time_bucket': 'time'}))
    w['time'] = pd.to_datetime(w['time'])
    for col in WEATHER_COLS:
        if col not in w:
            w[col] = np.nan
    return w[['time'] + WEATHER_COLS].sort_values('time').reset_index(drop=True)
