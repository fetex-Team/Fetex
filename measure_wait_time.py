"""GUI/headless 공용 실행 및 동일 호출 A/B 비교. 종료 시 미탑승 승객도 집계한다."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import pandas as pd
import traci
from config_loader import CFG, ROOT
from data.generate import generate_data
from taxi_manager import TaxiFleetManager
from passenger_manager import PassengerTimeoutManager
from passenger_spawn_manager import PassengerSpawnManager, ReplayPassengerManager
from module4_dispatch.hungarian_dispatcher import HungarianDispatcher

ROOT = Path(ROOT)
META_PATH = Path(os.environ.get('MOBILITY_SIM_DIR', ROOT / 'module1_simulation/sumo_config')) / 'runtime_meta.json'
SUMO_CFG = META_PATH.with_name('simulation.sumocfg')


def run_and_measure(sumo_binary='sumo', max_steps=None, sumo_cfg_path=None, meta_path=None,
                    strategy=None, algorithm=None, output_dir=None, recorder=None):
    meta = json.loads(Path(meta_path or META_PATH).read_text())
    if 'config' not in meta:
        raise ValueError('build_env.py로 환경을 다시 생성하세요.')
    # 한 번 생성한 환경의 설정 스냅샷으로만 실행한다.
    CFG.clear(); CFG.update(meta['config'])
    strategy = strategy or meta['taxi_strategy']; algorithm = algorithm or meta['taxi_dispatch_algorithm']
    if strategy not in ('patrol', 'prepositioned', 'forecast') or algorithm not in ('greedy', 'routeExtension', 'hungarian'):
        raise ValueError('지원하지 않는 전략/배차 알고리즘입니다.')
    start = pd.Timestamp(CFG['sim_date']) + pd.Timedelta(hours=meta['sim_start_hour'])
    end = pd.Timestamp(CFG['sim_date']) + pd.Timedelta(hours=meta['sim_end_hour'])
    duration = int((end - start).total_seconds())
    if duration <= 0: raise ValueError('종료 시각은 시작보다 뒤여야 합니다.')
    limit = duration if max_steps is None else min(duration, max_steps)
    if limit <= 0: raise ValueError('max_steps는 양수여야 합니다.')
    mode = CFG['passenger_mode']
    forecast = None
    if mode == 'replay':
        calls, external = generate_data(meta, start.normalize() - pd.Timedelta(days=1), 2, seed=CFG['passenger_seed'] if CFG['passenger_seed'] is not None else 42)
        spawn = ReplayPassengerManager(calls, start, end)
        fingerprint = hashlib.sha256(spawn.calls.to_csv(index=False).encode()).hexdigest()
        if strategy == 'forecast':
            from module4_dispatch.forecast_dispatcher import ForecastDispatcher
            forecast = ForecastDispatcher(meta, calls, external, start)
    elif mode == 'legacy':
        if strategy == 'forecast': raise ValueError('forecast 전략은 고정 호출 replay 모드에서 실행하세요.')
        spawn = PassengerSpawnManager(meta['zones'], meta['sim_start_hour'], meta['sim_end_hour'], seed=CFG['passenger_seed'])
        fingerprint = None
    else: raise ValueError('passenger_mode는 replay 또는 legacy여야 합니다.')
    manager = TaxiFleetManager(meta['num_taxis'], meta['boundary_edges'], meta['edges'], strategy=strategy,
                               zones=meta['zones'], sim_start_hour=meta['sim_start_hour'])
    timeout = PassengerTimeoutManager(meta['passenger_wait_timeout'])
    dispatcher = HungarianDispatcher() if algorithm == 'hungarian' else None
    requested, pickup, arrived = {}, {}, set()
    records = []; now = 0; teleports = 0; fleet_counts = []
    command = [sumo_binary, '-c', str(sumo_cfg_path or SUMO_CFG), '--seed', str(CFG['passenger_seed'] if CFG['passenger_seed'] is not None else 42),
               '--device.taxi.dispatch-algorithm', 'traci' if dispatcher else algorithm, '--device.taxi.dispatch-period', '1', '--no-step-log', 'true']
    if sumo_binary.endswith('sumo-gui'): command += ['--start', '--quit-on-end']
    traci.start(command)
    try:
        while now < limit:
            traci.simulationStep(); now = traci.simulation.getTime()
            # 먼저 현재 탑승/도착 상태를 읽고, 그 다음 타임아웃과 새 호출을 처리한다.
            active = set(traci.person.getIDList())
            if mode == 'legacy':
                for pid in active: requested.setdefault(pid, now)
            for pid in active:
                if pid not in pickup and traci.person.getVehicle(pid): pickup[pid] = now
            arrived.update(pid for pid in traci.simulation.getArrivedPersonIDList() if pid in pickup)
            timeout.maintain(now)
            # 종료 경계에서는 새 요청을 삽입하지 않는다.
            if now < limit:
                previous_index = spawn.index if mode == 'replay' else 0
                spawn.maintain(now, timeout_removed_pids=timeout.removed_pids)
                if mode == 'replay':
                    for index in range(previous_index, spawn.index):
                        row = spawn.rows[index]
                        requested[row.request_id] = row.depart_sec
                        timeout.depart_time[row.request_id] = row.depart_sec
                manager.maintain(now)
                if dispatcher: dispatcher.maintain(now)
                if forecast: forecast.maintain(now)
            teleports += traci.simulation.getStartingTeleportNumber()
            if recorder is not None:
                # 시각화 기록은 상태를 읽기만 하며 배차 판단에는 관여하지 않는다.
                recorder.capture(now, requested, pickup, arrived, timeout.removed_pids,
                                 spawn.failed_ids if mode == 'replay' else set(), forecast, teleports)
            if int(now) % 300 == 0 or now == 1:
                counts = {}
                fleet_counts.append(len(traci.vehicle.getTaxiFleet(-1)))
                for vid in traci.vehicle.getTaxiFleet(0):
                    cell = meta['edge_cells'].get(traci.vehicle.getRoadID(vid), 'internal_or_unknown')
                    counts[cell] = counts.get(cell, 0) + 1
                records.extend({'time_sec': now, 'h3_index': cell, 'idle_taxis': counts.get(cell, 0)}
                               for cell in sorted(set(meta['edge_cells'].values()) | set(counts)))
    finally:
        traci.close()
    waits = [pickup[pid] - when for pid, when in requested.items() if pid in pickup]
    failed = spawn.failed_ids if mode == 'replay' else set()
    # 미탑승 요청은 최소 대기시간만 아는 우측 검열 자료다. 성공자 평균과 별도 보고한다.
    timeout_ids = set(requested) & timeout.removed_pids
    pending = set(requested) - set(pickup) - timeout_ids - failed
    observed_waits = waits + [meta['passenger_wait_timeout']] * len(timeout_ids)
    observed_waits += [max(0, now - requested[pid]) for pid in pending | failed]
    total = len(requested)
    result = {'strategy': strategy, 'algorithm': algorithm, 'source': 'synthetic' if mode == 'replay' else 'legacy_rules',
        'demand_fingerprint': fingerprint, 'duration_sec': now, 'completed_interval': now >= duration,
        'n_total_passengers': total, 'n_measured': len(waits), 'n_arrived': len(arrived),
        'n_unpicked': total - len(waits), 'n_timeout_removed': len(timeout_ids),
        'n_pending_at_end': len(pending), 'n_spawn_failed': len(failed),
        'pickup_rate': len(waits) / total if total else None,
        'timeout_rate': len(timeout_ids) / total if total else None,
        'avg_wait_sec': sum(waits) / len(waits) if waits else None,
        'observed_wait_lower_bound_sec': sum(observed_waits) / total if total else None,
        'max_wait_sec': max(waits) if waits else None, 'min_wait_sec': min(waits) if waits else None,
        'reposition_count': len(forecast.moves) if forecast else 0, 'teleport_count': teleports,
        'min_sampled_fleet': min(fleet_counts) if fleet_counts else None,
        'max_sampled_fleet': max(fleet_counts) if fleet_counts else None, 'dispatch_period_sec': 1}
    if output_dir:
        output = Path(output_dir); output.mkdir(parents=True, exist_ok=True)
        (output / 'metrics.json').write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
        (output / 'config.json').write_text(json.dumps({**meta['config'], 'taxi_strategy': strategy, 'taxi_dispatch_algorithm': algorithm}, ensure_ascii=False, indent=2, allow_nan=False))
        pd.DataFrame(records).to_csv(output / 'vehicle_distribution.csv', index=False)
        if mode == 'replay': spawn.calls.to_csv(output / 'calls.csv', index=False)
        pd.DataFrame([{'request_id': pid, 'request_sec': when, 'pickup_sec': pickup.get(pid),
                       'status': 'picked_up' if pid in pickup else 'timeout' if pid in timeout_ids else 'spawn_failed' if pid in failed else 'pending'}
                      for pid, when in requested.items()]).to_csv(output / 'passengers.csv', index=False)
        if forecast:
            pd.DataFrame(forecast.records).to_csv(output / 'forecast_regions.csv', index=False)
            pd.DataFrame(forecast.moves, columns=['time_sec', 'taxi_id', 'from_cell', 'to_cell', 'to_edge', 'travel_time_sec']).to_csv(output / 'repositions.csv', index=False)
    if recorder is not None:
        recorder.finish(result)
    return result


def print_result(label, result):
    print(f'[{label}] ' + json.dumps(result, ensure_ascii=False, indent=2))


def rebuild_env():
    subprocess.run([sys.executable, str(ROOT / 'module1_simulation/build_env.py')], cwd=ROOT, check=True)


def compare_runs(kind='strategy', a='patrol', b='forecast', parallel=False):
    meta = json.loads(META_PATH.read_text())
    if meta['config']['passenger_mode'] != 'replay': raise ValueError('A/B 비교는 고정 호출 replay 모드가 필요합니다.')
    choices = ('patrol', 'prepositioned', 'forecast') if kind == 'strategy' else ('greedy', 'routeExtension', 'hungarian')
    if kind not in ('strategy', 'algorithm') or a not in choices or b not in choices:
        raise ValueError('지원하는 비교 조건을 선택하세요.')
    if a == b: raise ValueError('서로 다른 두 조건을 선택하세요.')
    output = ROOT / 'results' / ('dispatch_comparison' if kind == 'algorithm' else 'strategy_comparison')
    results = {}
    # 원본 설정/맵을 변경하지 않고 같은 snapshot과 호출 목록을 사용한다.
    processes = []
    for label in (a, b):
        dest = output / label
        if parallel:
            processes.append((label, subprocess.Popen([sys.executable, str(ROOT / 'parallel_dispatch_worker.py'), kind, label, str(dest)], cwd=ROOT)))
        else:
            results[label] = run_and_measure(output_dir=dest, **{kind: label})
    for label, process in processes:
        if process.wait() != 0: raise RuntimeError(f'{label} 워커 실행 실패. 해당 로그를 확인하세요.')
        results[label] = json.loads((output / label / 'metrics.json').read_text())
    if results[a]['demand_fingerprint'] != results[b]['demand_fingerprint']:
        raise RuntimeError('A/B 호출 목록이 달라 비교할 수 없습니다.')
    if not all(r['completed_interval'] for r in results.values()): raise RuntimeError('설정한 실험 구간이 완료되지 않았습니다.')
    output.mkdir(parents=True, exist_ok=True)
    (output / 'comparison.json').write_text(json.dumps(results, ensure_ascii=False, indent=2, allow_nan=False))
    for label, result in results.items(): print_result(label, result)
    return results


if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('mode', nargs='?', default='current')
    parser.add_argument('a', nargs='?'); parser.add_argument('b', nargs='?')
    args = parser.parse_args()
    if args.mode == 'compare': compare_runs(a=args.a or 'patrol', b=args.b or 'forecast')
    elif args.mode == 'dispatch_compare': compare_runs('algorithm', args.a or 'greedy', args.b or 'hungarian')
    else:
        strategy = args.mode if args.mode in ('patrol', 'prepositioned', 'forecast') else None
        print_result(args.mode, run_and_measure(strategy=strategy, output_dir=ROOT / 'results/simulation'))
