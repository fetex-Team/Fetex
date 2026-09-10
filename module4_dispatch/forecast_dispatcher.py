"""완료된 호출 관측 → 30분 예측 → 인센티브 가중 목표 분포 → 빈 택시 재배치."""
from collections import Counter
from pathlib import Path
import joblib
import numpy as np
import pandas as pd
import traci
from config_loader import CFG, ROOT
from module2_preprocessing.time_series_prep import TimeSeriesPreprocessor
from module2_preprocessing.external_data_merge import merge_external_data
from module4_dispatch.surge_pricing import SurgePricingEngine


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


class ForecastDispatcher:
    def __init__(self, meta, calls, external, start, enabled=True):
        artifact = joblib.load(Path(ROOT) / 'saved_models/demand_v2.joblib')
        if artifact.get('version') != 2:
            raise ValueError('train.py로 새 예측 모델을 학습하세요.')
        self.cells = sorted(set(meta['edge_cells'].values()))
        if artifact['cells'] != self.cells:
            raise ValueError('모델과 도로망의 H3 영역이 다릅니다. 현재 맵으로 다시 학습하세요.')
        if pd.Timestamp(artifact['data_until']) > start:
            raise ValueError('시뮬레이션보다 미래 데이터로 학습·선택한 모델입니다.')
        if any(artifact[k] != CFG[k] for k in ('max_lag', 'rolling_short', 'rolling_long')):
            raise ValueError('모델과 전처리 설정이 다릅니다. 재학습하세요.')
        self.model, self.features = artifact['model'], artifact['feature_cols']
        self.meta, self.calls, self.external, self.start = meta, calls, external, start
        self.prep = TimeSeriesPreprocessor(); self.engine = SurgePricingEngine()
        self.last_tick = -1; self.enabled = enabled; self.committed = {}
        self.records, self.moves = [], []
        self.cell_edges = {cell: sorted(e for e, c in meta['edge_cells'].items() if c == cell) for cell in self.cells}

    def predict(self, now):
        origin = (self.start + pd.Timedelta(seconds=now)).floor('5min')
        history_start = origin - pd.Timedelta(minutes=5 * (max(CFG['max_lag'], CFG['rolling_long']) + 2))
        # 미래 호출이나 날씨는 접근하지 않고 완료된 구간만 집계한다.
        history = self.calls[(self.calls.pickup_datetime >= history_start) & (self.calls.pickup_datetime < origin)]
        panel = self.prep.aggregate_demands(history, cells=self.cells, start=history_start, end=origin)
        observed_external = self.external[self.external.time_bucket < origin]
        frame = self.prep.create_features(merge_external_data(panel, observed_external))
        current = frame[frame.time_bucket == origin - pd.Timedelta(minutes=5)].sort_values('h3_index')
        if len(current) != len(self.cells) or current[self.features].isna().any().any():
            raise ValueError('예측에 필요한 과거 관측 구간이 부족합니다.')
        return np.maximum(0, self.model.predict(current[self.features]))

    def maintain(self, now):
        tick = int(now // 300)
        if tick == self.last_tick:
            return
        self.last_tick = tick
        predictions = self.predict(now)
        idle = sorted(traci.vehicle.getTaxiFleet(0))
        positions = {vid: self.meta['edge_cells'].get(traci.vehicle.getRoadID(vid)) for vid in idle}
        self.committed = {vid: cell for vid, cell in self.committed.items() if vid in positions and positions[vid] != cell}
        supply = Counter(cell for cell in positions.values() if cell is not None)
        priced = self.engine.apply_surge_pricing(pd.DataFrame({'h3_index': self.cells,
            'predicted_demand': predictions.sum(axis=1), 'available_taxis': [supply[c] for c in self.cells]}))
        target = allocate_targets(priced.predicted_demand, priced.surge_multiplier, sum(supply.values()))
        desired = dict(zip(self.cells, target)); projected = supply.copy()
        for vid, cell in self.committed.items():
            if positions[vid] is not None: projected[positions[vid]] -= 1
            projected[cell] += 1
        for i, row in priced.iterrows():
            self.records.append({'time_sec': now, **row.to_dict(), 'target_taxis': int(target[i]),
                                 **{f'forecast_{k + 1}': float(predictions[i, k]) for k in range(6)}})
        if not self.enabled or predictions.sum() <= 0:
            return
        budget = int(len(idle) * CFG['reposition_fraction'])
        donors = [vid for vid in idle if vid not in self.committed and positions[vid] is not None]
        priorities = sorted(self.cells, key=lambda c: desired[c] - projected[c], reverse=True)
        for cell in priorities:
            while projected[cell] < desired[cell] and budget > 0:
                best = None
                for vid in donors:
                    if projected[positions[vid]] <= desired[positions[vid]]:
                        continue
                    current = traci.vehicle.getRoadID(vid)
                    # ponytail: 셀 대표 도로 한 곳으로 이동한다. 대규모 맵은 셀 내 후보 도로를 확장한다.
                    destination = self.cell_edges[cell][0]
                    route = traci.simulation.findRoute(current, destination, vType=traci.vehicle.getTypeID(vid), depart=now)
                    if route.edges and (best is None or route.travelTime < best[0]):
                        best = (route.travelTime, vid, destination)
                if best is None: break
                _, vid, destination = best
                donors.remove(vid)
                try:
                    traci.vehicle.changeTarget(vid, destination)
                except traci.exceptions.TraCIException:
                    continue
                self.committed[vid] = cell; projected[positions[vid]] -= 1; projected[cell] += 1; budget -= 1
                self.moves.append({'time_sec': now, 'taxi_id': vid, 'from_cell': positions[vid], 'to_cell': cell,
                                   'to_edge': destination, 'travel_time_sec': best[0]})
