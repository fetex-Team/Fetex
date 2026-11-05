"""예측 재배치 공용 도우미.

- allocate_targets: 예측 수요 × 배율 비례로 정수 택시 수를 나눈다 (최대 나머지 방식) — forecast_repositioner가 사용

예전 ForecastDispatcher 클래스는 forecast_repositioner.ForecastRepositioner로 대체되어 제거했다
(5분 고정이라 10분 설정에서 동작하지 않았고, 이력 창이 짧아 same_time_* 피처가 항상 0이었음).
"""
import numpy as np


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

