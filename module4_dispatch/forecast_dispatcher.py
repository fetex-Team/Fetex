"""예측 재배치 공용 도우미.

- allocate_targets: 예측 수요 × 배율 비례로 정수 택시 수를 나눈다 (최대 나머지 방식) — forecast_repositioner가 사용
- weighted_edge_order: 재배치 목표 도로 후보를 과거 호출 수 가중 무작위 순서로 — RL 재배치(rl_env·rl_reposition_maintainer)가 사용

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


def weighted_edge_order(edges, weights=None, avoid=None, rng=np.random):
    """재배치 목표 도로 후보를 '과거 호출이 많았던 도로일수록 앞에 올 확률이 높은' 무작위 순서로 돌려준다.

    - weights: {도로: 과거 호출 수}. 없으면(빈 dict) 예전처럼 균등 무작위 순서.
    - 호출 기록이 없는 도로도 가중치 1(+1 보정)이라 아주 가끔은 뽑힌다.
    - avoid: 이번 결정에서 이미 다른 택시를 보낸 도로 — 가중치를 1/20로 낮춰 한 도로로 몰리지 않게 한다.
    가중 무작위 순서는 키 = -ln(U)/w 오름차순 (가중치 비례 비복원 추출과 같음)."""
    edges = list(edges)
    if not edges:
        return edges
    if not weights:
        rng.shuffle(edges)
        return edges
    w = np.array([float(weights.get(e, 0.0)) + 1.0 for e in edges])
    if avoid:
        w = w * np.array([0.05 if e in avoid else 1.0 for e in edges])
    keys = -np.log(np.clip(rng.random(len(edges)), 1e-12, 1.0)) / w
    return [edges[i] for i in np.argsort(keys, kind="stable")]
