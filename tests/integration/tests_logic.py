"""python tests_logic.py: 핵심 회귀 검사. SUMO 서버 없이 실제 함수와 제한된 입출력 대역으로 실행."""
import json
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import patch
import numpy as np
import pandas as pd
import h3

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from fetex.core.config import CFG, DEFAULT_CONFIG, REGION_PRESETS
from data.generate import generate_data
from fetex.preprocessing.time_series_prep import TimeSeriesPreprocessor, feature_columns, target_columns, time_based_split
from fetex.preprocessing.external_data_merge import EXTERNAL_FEATURE_COLS, merge_external_data
from fetex.dispatch.surge_pricing import SurgePricingEngine
from fetex.dispatch.forecast_dispatcher import allocate_targets
from evaluate import calculate_metrics


TEST_ARTIFACTS = PROJECT_ROOT / ".test-artifacts"


def sample_meta():
    cells = [h3.latlng_to_cell(lat, 127.03, 8) for lat in (37.49, 37.51)]
    cfg = {**DEFAULT_CONFIG, **REGION_PRESETS['강남역'], 'synthetic_rate': 1.5, 'freq': '5min', 'passenger_mode': 'replay', 'passenger_seed': 42}
    return {'config': cfg, 'edge_cells': dict(zip(('a', 'b'), cells)),
            'edge_latlng': {'a': [37.49, 127.03], 'b': [37.51, 127.03]},
            'num_taxis': 0, 'boundary_edges': ['a'], 'edges': ['a', 'b'], 'zones': {},
            'taxi_strategy': 'patrol', 'taxi_dispatch_algorithm': 'greedy',
            'sim_start_hour': 0, 'sim_end_hour': 1, 'passenger_wait_timeout': 500}


def _weather_table(external):
    """합성 외부 관측(time_bucket×h3_index) → 시각별 날씨 표. forecast_dispatcher와 같은 변환."""
    from fetex.dispatch.forecast_dispatcher import _external_to_weather
    return _external_to_weather(external)


def check_preprocessing():
    # [2026-09-17 윤세빈] Module 2 현행 API(feat/3-preprocessing 계열)에 맞춰 재작성.
    #   구 API(make_supervised/chronological_split/aggregate_demands(cells=))는 develop에 없음.
    meta = sample_meta(); CFG.update(meta['config']); cells = list(meta['edge_cells'].values())
    calls = pd.DataFrame({'pickup_datetime': pd.to_datetime(['2026-08-01 00:00', '2026-08-01 00:10']), 'h3_index': [cells[0]] * 2})
    prep = TimeSeriesPreprocessor()
    assert prep.freq == '5min', f'config freq가 5min이 아닙니다: {prep.freq}'
    panel = prep.aggregate_demands(calls, all_cells=cells, start='2026-08-01', end='2026-08-01 00:15')
    assert panel.loc[panel.h3_index == cells[0], 'demand'].tolist() == [1, 0, 1, 0]
    assert panel.loc[panel.h3_index == cells[1], 'demand'].sum() == 0
    assert not panel.duplicated(['time_bucket', 'h3_index']).any()
    # 두 지역을 완전히 다른 상수로 채워 이동평균이 지역 경계를 섞지 않는지 검사한다.
    times = pd.date_range('2026-08-01', periods=200, freq='5min')
    panel = pd.DataFrame([(t, c, float(i * 10)) for t in times for i, c in enumerate(cells)], columns=['time_bucket', 'h3_index', 'demand'])
    external = panel[['time_bucket', 'h3_index']].copy()
    for col in ('temperature', 'precipitation', 'traffic_index', 'event_flag', 'is_holiday'): external[col] = 1.
    external.loc[external.time_bucket == times[0], 'temperature'] = np.nan
    # 인과 결합: 시각 t에는 t 이전 최신 관측만 붙는다 (첫 시각은 관측이 없으므로 결측 플래그)
    merged = merge_external_data(panel, weather=_weather_table(external), time_col='time_bucket', verbose=False)
    assert merged.loc[merged.time_bucket == times[0], 'temperature'].isna().all()
    assert (merged.loc[merged.time_bucket == times[0], 'weather_missing'] == 1).all()
    assert (merged.loc[merged.time_bucket >= times[12], 'weather_missing'] == 0).all()
    frame = prep.create_features(merged, verbose=False)
    names = feature_columns(frame) + [c for c in EXTERNAL_FEATURE_COLS if c in frame.columns]
    targets = target_columns(frame)
    assert len(targets) == 6 and targets == [f'y_h{h}' for h in range(1, 7)]
    assert not any(c.startswith('y_h') or c == 'demand' for c in names)
    for cell, group in frame.groupby('h3_index'):
        assert np.allclose(group['rolling_mean_6'], group.demand)       # 상수 계열: 셀 경계 섞임 없음
        assert np.allclose(group['rolling_mean_1h'], group.demand)
    train, val, test, cuts = time_based_split(frame, test_size=0.2, val_size=0.2)
    assert train.time_bucket.max() < val.time_bucket.min() <= val.time_bucket.max() < test.time_bucket.min()
    assert set(test.h3_index) == set(cells), '시간칸 기준 분할이면 모든 셀이 test에 있어야 한다'
    # 타깃은 현재값 복사가 아니라 정확히 5분 간격의 미래값이어야 한다.
    ramp = panel.copy(); ramp['demand'] = np.repeat(np.arange(len(times)), len(cells))
    ramp_frame = prep.create_features(merge_external_data(ramp, weather=_weather_table(external), time_col='time_bucket', verbose=False), verbose=False)
    ramp_frame = ramp_frame.sort_values(['time_bucket', 'h3_index']).reset_index(drop=True)
    np.testing.assert_array_equal(ramp_frame[targets].iloc[0].to_numpy(), ramp_frame.demand.iloc[0] + np.arange(1, 7))
    # 유효범위·유일성 위반은 ValueError로 중단되어야 한다 (Data Spec 5장)
    for bad in (panel.assign(demand=panel.demand.where(panel.index != 0, -1)),
                pd.concat([panel, panel.head(1)], ignore_index=True),
                panel.assign(time_bucket=panel.time_bucket.where(panel.index != 0, times[0] + pd.Timedelta(minutes=2)))):
        try:
            prep.create_features(bad, verbose=False); raise AssertionError('잘못된 패널이 통과했습니다')
        except ValueError: pass
    print('PASS: zero grid, uniqueness, causal external join, grouped rolling, six targets, temporal split, validation errors')


def check_metrics():
    # Data Spec 4장: MAPE는 실제값이 양수인 구간에서만. 전부 0이면 None (0 나누기 금지).
    m = calculate_metrics(np.zeros(3), np.ones(3))
    assert m['MAPE (%)'] is None, f"MAPE가 None이 아닙니다: {m['MAPE (%)']} (evaluate.py가 Data Spec 4장 기준이 아님)"
    print('PASS: zero-demand MAPE is None')


def check_replay():
    meta = sample_meta()
    calls_a, weather_a = generate_data(meta, '2026-08-01', 2)
    calls_b, weather_b = generate_data(meta, '2026-08-02', 1)
    pd.testing.assert_frame_equal(calls_a[calls_a.pickup_datetime >= '2026-08-02'].reset_index(drop=True), calls_b)
    pd.testing.assert_frame_equal(weather_a[weather_a.time_bucket >= '2026-08-02'].reset_index(drop=True), weather_b)
    print('PASS: identical daily requests and external observations regardless of generation window')


def check_pricing():
    engine = SurgePricingEngine()
    assert engine.get_multiplier(engine.calculate_imbalance(100, 0)) == CFG['max_multiplier']
    assert engine.get_multiplier(engine.calculate_imbalance(0, 0)) == CFG['min_multiplier']
    for demand in ([0, 0], [3, 7], [100, 1]):
        targets = allocate_targets(demand, [1, 2], 3)
        assert (targets >= 0).all() and targets.sum() == (3 if sum(demand) else 0)
    try: engine.calculate_imbalance(-1, 2)
    except ValueError: pass
    else: raise AssertionError('negative demand accepted')
    print('PASS: bounded surge, zero supply, conserved integer fleet budget')


def check_pickup_protection():
    import fetex.runtime.taxi_manager as taxi_manager
    vehicle = SimpleNamespace(getIDList=lambda: ['pickup'], getLoadedIDList=lambda: ['pickup'],
                              getTaxiFleet=lambda mode: [] if mode == 0 else ['pickup'],
                              getTypeID=lambda vid: 'taxi_type', getPersonIDList=lambda vid: ['passenger'])
    fake = SimpleNamespace(vehicle=vehicle, simulation=SimpleNamespace(getPendingVehicles=lambda: []), exceptions=SimpleNamespace(TraCIException=RuntimeError))
    with patch.object(taxi_manager, 'traci', fake):
        manager = taxi_manager.TaxiFleetManager(1, ['a'], ['a', 'b'])
        manager._pick_target_edge = lambda *args: (_ for _ in ()).throw(AssertionError('pickup taxi was repositioned'))
        manager.maintain(10)
    vehicle.getIDList = lambda: []
    vehicle.getLoadedIDList = lambda: []
    vehicle.getTaxiFleet = lambda mode: []
    fake.simulation.getPendingVehicles = lambda: ['pending_taxi']
    with patch.object(taxi_manager, 'traci', fake):
        manager = taxi_manager.TaxiFleetManager(1, ['a'], ['a', 'b'])
        manager._spawn_new_taxi = lambda: (_ for _ in ()).throw(AssertionError('pending taxi duplicated'))
        manager.maintain(10)
    print('PASS: assigned pickup route is untouched; pending taxi is not duplicated')


def check_empty_interval():
    import fetex.runtime.measure_wait_time as measure
    meta = sample_meta(); meta['static_calls'] = []
    meta['config'].update(dynamic_passengers=False, num_passengers=0)
    clock = [0]
    def step(): clock[0] += 1
    fake = SimpleNamespace(start=lambda args: None, close=lambda: None, getVersion=lambda: ('test',), simulationStep=step,
        simulation=SimpleNamespace(getTime=lambda: clock[0], getArrivedPersonIDList=lambda: [], getStartingTeleportNumber=lambda: 0),
        person=SimpleNamespace(getIDList=lambda: []),
        vehicle=SimpleNamespace(getTaxiFleet=lambda mode: [], getIDList=lambda: []))
    class Manager:
        def __init__(self, *args, **kwargs):
            self.removed_pids = set(); self.depart_time = {}; self.max_loaded_taxis = 0
            self._pending_removal = set(); self.threshold_exceeded_at = {}; self.removed_at = {}
            self.new_records = []; self.spawn_counts = {}; self.attempt_counts = {}
            self.passed_counts = {}; self.failed_counts = {}; self.failures = []
        def maintain(self, *args, **kwargs): pass
    directory = TEST_ARTIFACTS / 'empty_interval'
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / 'meta.json'; path.write_text(json.dumps(meta))
    (directory / 'grid.net.xml').write_text('<net/>')
    with patch.object(measure, 'traci', fake), patch.object(measure, 'TaxiFleetManager', Manager), \
         patch.object(measure, 'PassengerTimeoutManager', Manager), \
         patch.object(measure, 'PassengerSpawnManager', Manager):
        result = measure.run_and_measure(meta_path=path, max_steps=60)
    assert result['duration_sec'] == 60 and not result['completed_interval']
    assert result['n_total_passengers'] == 0 and result['avg_wait_sec'] is None
    print('PASS: empty demand does not end simulation at 16 seconds; partial run is marked incomplete')


def check_forecast_causality():
    from fetex.dispatch import forecast_dispatcher as module
    meta = sample_meta(); CFG.update(meta['config'])
    calls, external = generate_data(meta, '2026-08-30', 2)
    prep = TimeSeriesPreprocessor()
    panel = prep.aggregate_demands(calls, all_cells=sorted(set(meta['edge_cells'].values())), start='2026-08-30', end='2026-09-01')
    frame = prep.create_features(merge_external_data(panel, weather=_weather_table(external), time_col='time_bucket', verbose=False), verbose=False)
    features = feature_columns(frame) + [c for c in EXTERNAL_FEATURE_COLS if c in frame.columns]
    class Model:
        def predict(self, frame):
            return np.repeat(frame[['lag_1']].to_numpy(), 6, axis=1)
    artifact = {'version': 2, 'cells': sorted(meta['edge_cells'].values()), 'data_until': '2026-08-30',
                'model': Model(), 'feature_cols': features, 'target_cols': [f'y_h{h}' for h in range(1, 7)],
                **{k: CFG[k] for k in ('max_lag', 'rolling_short', 'rolling_long')}}
    start = pd.Timestamp('2026-08-31 17:00')
    with patch.object(module.joblib, 'load', return_value=artifact):
        dispatcher = module.ForecastDispatcher(meta, calls, external, start)
        before = dispatcher.predict(0)
        dispatcher.calls = pd.concat([calls, calls[calls.pickup_datetime >= start]] * 2, ignore_index=True)
        # 과거는 그대로, 미래 호출만 복제한 자료로 교체한다.
        dispatcher.calls = pd.concat([calls[calls.pickup_datetime < start], dispatcher.calls[dispatcher.calls.pickup_datetime >= start]], ignore_index=True)
        dispatcher.external = external.copy()
        dispatcher.external.loc[external.time_bucket >= start, 'temperature'] = 9999
        np.testing.assert_array_equal(before, dispatcher.predict(0))
    print('PASS: future calls and future weather cannot change the current forecast')


def check_sumo():
    import fetex.runtime.measure_wait_time as measure
    # 같은 입력의 반복 실행은 동일해야 하며, 예측 재배치의 성능 우위는 가정하지 않는다.
    a = measure.run_and_measure(strategy='patrol')
    b = measure.run_and_measure(strategy='patrol')
    assert a == b, '동일 조건 반복 실행의 결과가 달라졌습니다.'
    for algorithm in ('greedy', 'hungarian'):
        result = measure.run_and_measure(strategy='forecast', algorithm=algorithm)
        assert result['demand_fingerprint'] == a['demand_fingerprint']
        assert result['completed_interval'] and result['n_spawn_failed'] == 0
        assert result['n_total_passengers'] == result['n_measured'] + result['n_unpicked']
        assert result['n_unpicked'] == result['n_timeout_removed'] + result['n_pending_at_end'] + result['n_spawn_failed']
        assert result['max_sampled_fleet'] <= CFG['num_taxis']
    print('PASS: real SUMO deterministic replay, both dispatchers, complete duration, passenger conservation, fleet cap')


if __name__ == '__main__':
    saved = CFG.copy()
    try:
        check_preprocessing(); check_metrics(); check_replay(); check_pricing(); check_pickup_protection(); check_empty_interval(); check_forecast_causality()
    finally:
        CFG.clear(); CFG.update(saved)

    import sys
    if '--sumo' in sys.argv:
        check_sumo()
