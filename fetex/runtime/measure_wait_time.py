"""재현 가능한 SUMO 실행과 승객/차량 분포 관측."""
import argparse
import csv
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import statistics
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from collections import Counter

# CLI 설정은 매니저들의 CFG import보다 먼저 선택한다.
if '--config-path' in sys.argv:
    os.environ['MOBILITY_CONFIG'] = str(Path(sys.argv[sys.argv.index('--config-path') + 1]).resolve())
if os.environ.get('MOBILITY_BACKEND') == 'libsumo':
    import libsumo as traci
    sys.modules['traci'] = traci
else:
    import traci
import sumolib
import pandas as pd
from fetex.core.config import CFG, DEFAULT_CONFIG
from fetex.core.paths import PROJECT_ROOT
from fetex.runtime.taxi_manager import TaxiFleetManager
from fetex.runtime.passenger_manager import PassengerTimeoutManager
from fetex.runtime.passenger_spawn_manager import PassengerSpawnManager
from fetex.dispatch.hungarian_dispatcher import HungarianDispatcher
from fetex.validation.runtime import schedule_status, source_fingerprint

ROOT = PROJECT_ROOT
CONFIG_PATH = ROOT / 'config.json'
SUMO_CFG = ROOT / 'fetex/simulation/sumo_config/simulation.sumocfg'
META_PATH = SUMO_CFG.with_name('runtime_meta.json')


def write_csv(path, rows, fields):
    """빈 데이터도 헤더를 남겨 후속 모듈에서 동일하게 읽는다."""
    with Path(path).open('w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(rows)


def summarize(outcomes, timeout):
    """생성 성공만 분모로 사용하고 종료 검열과 실제 타임아웃을 분리한다."""
    counts = {k: sum(r['status'] == k for r in outcomes) for k in ('picked_up', 'timeout', 'pending', 'other_loss')}
    waits = [r['pickup_sec'] - r['depart_sec'] for r in outcomes if r['status'] == 'picked_up']
    n = len(outcomes)
    ordered = sorted(waits)
    p90 = ordered[max(0, __import__('math').ceil(.9 * len(ordered)) - 1)] if ordered else None
    adjusted = waits + [timeout] * counts['timeout']
    return {'n_total_passengers': n, 'n_measured': counts['picked_up'], 'n_unpicked': n - counts['picked_up'],
            'n_timeout_removed': counts['timeout'], 'n_pending_at_end': counts['pending'],
            'n_other_loss': counts['other_loss'], 'avg_wait_sec': statistics.mean(waits) if waits else None,
            'median_wait_sec': statistics.median(waits) if waits else None, 'p90_wait_sec': p90,
            'max_wait_sec': max(waits) if waits else None, 'min_wait_sec': min(waits) if waits else None,
            'pickup_rate': counts['picked_up'] / n if n else None,
            'timeout_rate': counts['timeout'] / n if n else None,
            'pending_rate': counts['pending'] / n if n else None,
            'true_avg_wait_sec_with_penalty': statistics.mean(adjusted) if adjusted else None,
            'waits': waits}


def _project_path(value):
    """설정 파일 안의 상대 경로를 저장소 기준으로 해석한다."""
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


class ReplayPassengerSource:
    """동일한 호출 스트림을 예측 입력과 SUMO 실제 승객으로 함께 사용한다.

    ``forecast_calls_path``만 읽고 별도 난수 승객을 생성하면 예측과 평가 대상 수요가
    달라진다. replay source는 각 호출 시각에 실제 SUMO person을 추가하므로 patrol과
    forecast가 완전히 같은 호출 지문에서 비교된다.
    """
    def __init__(self, path, start, duration, valid_edges):
        df = pd.read_csv(path, parse_dates=["pickup_datetime"])
        required = {"pickup_datetime", "from_edge", "to_edge"}
        missing = required - set(df.columns)
        if missing:
            raise ValueError(f"replay_calls_path에 필요한 컬럼이 없습니다: {sorted(missing)}")
        df = df.copy()
        df["pickup_datetime"] = pd.to_datetime(df["pickup_datetime"], errors="raise")
        df["depart"] = (df["pickup_datetime"] - pd.Timestamp(start)).dt.total_seconds()
        df = df[(df["depart"] >= 0) & (df["depart"] < duration)].sort_values(
            ["depart", "pickup_datetime"], kind="stable"
        )
        allowed = set(valid_edges)
        invalid = df[~df["from_edge"].isin(allowed) | ~df["to_edge"].isin(allowed)]
        if not invalid.empty:
            raise ValueError(f"replay_calls_path에 현재 지도에 없는 edge가 {len(invalid)}건 있습니다.")
        ids = df["request_id"] if "request_id" in df else pd.Series(range(len(df)), index=df.index)
        df["person_id"] = [f"replay_{value}" for value in ids.astype(str)]
        if df["person_id"].duplicated().any():
            raise ValueError("replay_calls_path의 request_id가 중복됩니다.")
        self.rows = df.to_dict("records")
        self.index = 0
        self.new_records = []
        self.records = {}
        self.spawn_counts = Counter()
        self.attempt_counts = Counter()
        self.passed_counts = Counter()
        self.failed_counts = Counter()
        self.failures = []

    def maintain(self, now_seconds, _timeout_removed_pids=None):
        self.new_records = []
        while self.index < len(self.rows) and self.rows[self.index]["depart"] < now_seconds + 1:
            row = self.rows[self.index]
            self.index += 1
            kind = "replay"
            self.attempt_counts[kind] += 1
            self.passed_counts[kind] += 1
            record = {"person_id": row["person_id"], "depart": float(row["depart"]),
                      "from_edge": row["from_edge"], "to_edge": row["to_edge"],
                      "demand_type": row.get("demand_type", kind)}
            try:
                traci.person.add(record["person_id"], record["from_edge"], pos=0, depart=now_seconds)
                traci.person.appendDrivingStage(record["person_id"], record["to_edge"], lines="taxi")
            except traci.exceptions.TraCIException as exc:
                self.failed_counts[kind] += 1
                self.failures.append({"person_id": record["person_id"], "time_sec": now_seconds,
                                      "kind": kind, "error": str(exc)})
                try:
                    traci.person.remove(record["person_id"])
                except traci.exceptions.TraCIException:
                    pass
                continue
            self.records[record["person_id"]] = record
            self.new_records.append(record)
            self.spawn_counts[kind] += 1


def run_and_measure(sumo_binary='sumo', max_steps=100000, sumo_cfg_path=None, meta_path=None,
                    output_dir=None, strategy=None, algorithm=None, observer=None, forecast=None,
                    gui_delay=None):
    """GUI/headless 공용 루프. 1초 생성 틱과 [시작, 종료) 구간을 사용한다."""
    meta_path = Path(meta_path or META_PATH)
    code_hash = source_fingerprint()
    meta = json.loads(meta_path.read_text(encoding='utf-8'))
    cfg = {**DEFAULT_CONFIG, **meta.get('config', {})}
    # 독립 프로세스 실행 외에도 매니저 설정은 메타와 일치시킨다.
    CFG.clear()
    CFG.update(cfg)
    strategy = strategy or meta['taxi_strategy']
    algorithm = algorithm or meta.get('taxi_dispatch_algorithm', 'greedy')
    meta.update(taxi_strategy=strategy, taxi_dispatch_algorithm=algorithm)
    out = Path(output_dir or ROOT / 'results/simulation')
    out.mkdir(parents=True, exist_ok=True)
    sumocfg = Path(sumo_cfg_path or meta_path.with_name('simulation.sumocfg'))
    start_hour, end_hour = meta['sim_start_hour'], meta['sim_end_hour']
    raw_duration = (end_hour - start_hour) * 3600
    duration = int(round(raw_duration))
    if duration <= 0 or abs(raw_duration - duration) > 1e-6:
        raise ValueError('시뮬레이션 구간은 양의 정수 초여야 합니다.')
    start = dt.datetime.fromisoformat(cfg.get('scenario_date', '2026-09-18')) + dt.timedelta(hours=start_hour)
    timeout = meta.get('passenger_wait_timeout', 500)
    dispatcher = HungarianDispatcher() if algorithm in ('hungarian', 'rl_reposition') else None
    manager = TaxiFleetManager(meta['num_taxis'], meta['boundary_edges'], meta['edges'], strategy=strategy,
                               hotspot_edges=meta.get('hotspot_edges'), zones=meta['zones'], sim_start_hour=start_hour)
    pax = PassengerTimeoutManager(timeout, dispatcher)
    replay_path = cfg.get('replay_calls_path')
    spawn = (ReplayPassengerSource(_project_path(replay_path), start, duration, meta['edges'])
             if replay_path else PassengerSpawnManager(
                 meta['zones'], start_hour, end_hour, meta.get('school_pop_base'),
                 meta.get('company_pop_base'), cfg.get('passenger_seed'), config=cfg))
    if strategy == 'forecast' and forecast is None:
        # 모델/이력 미준비를 순찰 실행으로 위장하지 않는다.
        if not cfg.get('forecast_calls_path'):
            raise ValueError('forecast에는 forecast_calls_path와 호환 예측 모델이 필요합니다.')
        import pandas as pd
        from fetex.dispatch.forecast_dispatcher import ForecastDispatcher
        calls = pd.read_csv(_project_path(cfg['forecast_calls_path']), parse_dates=['pickup_datetime'])
        external = pd.read_csv(_project_path(cfg['forecast_external_path'])) if cfg.get('forecast_external_path') else None
        forecast = ForecastDispatcher(meta, calls, external, pd.Timestamp(start))
    if algorithm == 'rl_reposition':
        raise ValueError('이 검증 실행기는 RL 학습/평가를 지원하지 않습니다. hungarian을 사용하세요.')
    static = [] if replay_path else meta.get('static_calls')
    if static is None:
        static = []
        for person in ET.parse(sumocfg.with_name('entities.rou.xml')).getroot().findall('person'):
            ride = person.find('ride')
            static.append({'person_id': person.get('id'), 'depart': float(person.get('depart')),
                           'from_edge': ride.get('from'), 'to_edge': ride.get('to'), 'demand_type': 'residential_schedule'})
    requested, pickup, arrived, last_active = {}, {}, set(), set()
    records = {}
    static = sorted(static, key=lambda r: r['depart'])
    static_index = 0
    fleet_rows, replay_rows = [], []
    begin = time.monotonic()
    command = [sumolib.checkBinary(sumo_binary), '-c', str(sumocfg), '--step-length', '1',
               '--seed', str(cfg.get('passenger_seed') or 0), '--no-step-log', 'true',
               '--duration-log.disable', 'true', '--error-log', str(out / 'sumo_errors.log')]
    command += ['--device.taxi.dispatch-algorithm', 'traci' if algorithm == 'hungarian' else algorithm]
    # GUI 실행은 기존 전용 실행기처럼 창을 자동 종료하지 않는다. 각 스텝에 짧은
    # 지연을 주어 사람이 차량·승객·재배치 동작을 볼 수 있게 한다.
    if sumo_binary == 'sumo-gui' and gui_delay is None:
        gui_delay = 0.01
    traci.start(command)
    version = traci.getVersion()
    now = 0
    teleports = 0
    try:
        while now < duration and now < max_steps:
            # 0초부터 생성하고 마지막 초에도 한 번만 추첨한다.
            spawn.maintain(now, pax.removed_pids)
            new = spawn.new_records
            while static_index < len(static) and static[static_index]['depart'] < min(now + 1, duration):
                new.append(static[static_index])
                static_index += 1
            for r in new:
                pid = r['person_id']
                records[pid] = r
                requested[pid] = r['depart']
                pax.depart_time[pid] = r['depart']
            spawn.new_records = []
            traci.simulationStep()
            now = traci.simulation.getTime()
            if sumo_binary == 'sumo-gui':
                current_hour = start_hour + now / 3600.0
                hours = int(current_hour) % 24
                minutes = int((current_hour - int(current_hour)) * 60)
                seconds = int((((current_hour - int(current_hour)) * 60) - minutes) * 60)
                try:
                    view_ids = traci.gui.getIDList()
                    if view_ids:
                        traci.gui.setWindowCaption(
                            view_ids[0],
                            f'DT Mobility Simulation | Time: {hours:02d}:{minutes:02d}:{seconds:02d}',
                        )
                except Exception:
                    # GUI 표시는 계측 결과에 영향을 주지 않아야 한다.
                    pass
                if gui_delay and gui_delay > 0:
                    time.sleep(gui_delay)
            arrived.update(traci.simulation.getArrivedPersonIDList())
            teleports += traci.simulation.getStartingTeleportNumber()
            last_active = set(traci.person.getIDList())
            for pid in sorted(last_active):
                if pid not in records:
                    continue
                if pid not in pickup and traci.person.getVehicle(pid):
                    pickup[pid] = now
            if dispatcher:
                dispatcher.refresh_reservations()
            pax.maintain(now)
            if dispatcher:
                dispatcher.maintain(now)
            if forecast and now < duration:
                forecast.maintain(now)
                manager.protected_ids = set(forecast.committed)
            if now < duration:
                manager.maintain(now)
            if now % 300 == 0 or now == duration or now == 1:
                cells = sorted(set(meta.get('edge_cells', {}).values()) | {'unmapped'})
                snapshot = {cell: dict(time_sec=now, h3_index=cell, total_taxis=0, idle_taxis=0,
                                       occupied_taxis=0, waiting_passengers=0) for cell in cells}
                idle = set(traci.vehicle.getTaxiFleet(0))
                for vid in sorted(traci.vehicle.getIDList()):
                    edge = traci.vehicle.getRoadID(vid)
                    vtype = traci.vehicle.getTypeID(vid)
                    x, y = traci.vehicle.getPosition(vid)
                    replay_rows.append(dict(time_sec=now, object_id=vid, object_type=vtype, edge=edge,
                                            x=x, y=y, speed=traci.vehicle.getSpeed(vid)))
                    if vtype != 'taxi_type':
                        continue
                    cell = meta.get('edge_cells', {}).get(edge, 'unmapped')
                    snapshot[cell]['total_taxis'] += 1
                    snapshot[cell]['idle_taxis'] += int(vid in idle)
                    snapshot[cell]['occupied_taxis'] += bool(traci.vehicle.getPersonIDList(vid))
                for pid in sorted(last_active - set(pickup) - pax.removed_pids):
                    edge = traci.person.getRoadID(pid)
                    snapshot[meta.get('edge_cells', {}).get(edge, 'unmapped')]['waiting_passengers'] += 1
                fleet_rows.extend(snapshot.values())
            if observer:
                observer.capture(now, requested, pickup, arrived, pax.removed_pids, set(), forecast, teleports)
            if now % 3600 == 0:
                print(f'[진행] {now:.0f}/{duration:.0f}s 생성={len(records)} 탑승={len(pickup)}', flush=True)
    finally:
        traci.close()
    calls, outcomes = [], []
    for pid, r in records.items():
        lat, lng = meta.get('edge_latlng', {}).get(r['from_edge'], [None, None])
        dlat, dlng = meta.get('edge_latlng', {}).get(r['to_edge'], [None, None])
        calls.append(dict(person_id=pid, pickup_datetime=(start + dt.timedelta(seconds=r['depart'])).isoformat(),
                          latitude=lat, longitude=lng, destination_latitude=dlat, destination_longitude=dlng,
                          from_edge=r['from_edge'], to_edge=r['to_edge'], demand_type=r['demand_type'],
                          coordinate_mapping=meta.get('coordinate_mapping')))
        status = 'picked_up' if pid in pickup else 'timeout' if pid in pax.removed_pids else 'pending' if pid in last_active else 'other_loss'
        outcomes.append(dict(person_id=pid, demand_type=r['demand_type'], depart_sec=r['depart'], pickup_sec=pickup.get(pid),
                             removed_sec=pax.removed_at.get(pid), threshold_exceeded_sec=pax.threshold_exceeded_at.get(pid),
                             reserved_pending=pid in pax._pending_removal, status=status))
    result = summarize(outcomes, timeout)
    result.update(strategy=strategy, algorithm=algorithm, config=cfg, seed=cfg.get('passenger_seed'),
                  duration_sec=now, completed_interval=now == duration, sumo_version=version,
                  source_hash=code_hash, max_loaded_taxis=manager.max_loaded_taxis,
                  wall_time_sec=time.monotonic() - begin, backend=os.environ.get('MOBILITY_BACKEND', 'traci'),
                  n_spawn_failed=sum(spawn.failed_counts.values()), n_reserved_pending=len(pax._pending_removal),
                  n_threshold_exceeded=len(pax.threshold_exceeded_at), spawn_counts=dict(spawn.spawn_counts),
                  attempts=dict(spawn.attempt_counts), probability_passed=dict(spawn.passed_counts),
                  spawn_failures=dict(spawn.failed_counts), teleports=teleports,
                  map_hash=hashlib.sha256(sumocfg.with_name('grid.net.xml').read_bytes()).hexdigest(),
                  demand_fingerprint=hashlib.sha256(json.dumps(calls, sort_keys=True).encode()).hexdigest(),
                  schedules=schedule_status(cfg, meta['zones'], spawn),
                  evening=summarize([r for r in outcomes if 18 <= start_hour + r['depart_sec'] / 3600 < 24], timeout))
    assert result['n_total_passengers'] == sum(result[k] for k in ('n_measured', 'n_timeout_removed', 'n_pending_at_end', 'n_other_loss'))
    write_csv(out / 'calls.csv', calls, ['person_id','pickup_datetime','latitude','longitude','destination_latitude','destination_longitude','from_edge','to_edge','demand_type','coordinate_mapping'])
    write_csv(out / 'passenger_outcomes.csv', outcomes, ['person_id','demand_type','depart_sec','pickup_sec','removed_sec','threshold_exceeded_sec','reserved_pending','status'])
    write_csv(out / 'fleet_distribution.csv', fleet_rows, ['time_sec','h3_index','total_taxis','idle_taxis','occupied_taxis','waiting_passengers'])
    write_csv(out / 'object_states.csv', replay_rows, ['time_sec','object_id','object_type','edge','x','y','speed'])
    (out / 'spawn_failures.json').write_text(json.dumps(spawn.failures, ensure_ascii=False, indent=2), encoding='utf-8')
    (out / 'summary.json').write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
    if forecast:
        write_csv(out / 'forecast.csv', forecast.records, list(forecast.records[0]) if forecast.records else ['time_sec'])
        write_csv(out / 'reposition_moves.csv', forecast.moves, list(forecast.moves[0]) if forecast.moves else ['time_sec'])
    if observer:
        observer.finish(result)
    return result


def print_result(label, result):
    print(f"[{label}] 생성={result['n_total_passengers']} 탑승={result['n_measured']} "
          f"타임아웃={result['n_timeout_removed']} 평균대기={result['avg_wait_sec']}")


def isolated_run(config, destination, sumo_binary='sumo', stream_output=False):
    """빌드와 측정은 별도 프로세스에서 같은 설정 스냅샷을 읽는다."""
    destination = Path(destination).resolve()
    destination.mkdir(parents=True, exist_ok=True)
    path = destination / 'config.json'
    path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding='utf-8')
    env = {**os.environ, 'MOBILITY_CONFIG': str(path), 'PYTHONIOENCODING': 'utf-8'}
    commands = ([sys.executable, '-m', 'fetex.simulation.build_env', '--config-path', str(path), '--config-dir', str(destination / 'sumo')],
                [sys.executable, '-m', 'fetex.runtime.measure_wait_time', '_worker', '--config-path', str(path), '--output-dir', str(destination), '--sumo-binary', sumo_binary])
    if stream_output:
        for command in commands:
            subprocess.run(command, env=env, cwd=ROOT, check=True)
    else:
        with (destination / 'run.log').open('w', encoding='utf-8') as log:
            for command in commands:
                subprocess.run(command, env=env, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, check=True)
    return json.loads((destination / 'summary.json').read_text(encoding='utf-8'))


def compare_strategies(config=None, output_dir=None, seeds=(42, 43, 44, 45, 46)):
    cfg = dict(config or CFG)
    results = {}
    for seed in seeds:
        for strategy in ('patrol', 'prepositioned'):
            results[f'{seed}/{strategy}'] = isolated_run({**cfg, 'passenger_seed': seed, 'taxi_strategy': strategy},
                Path(output_dir or ROOT / 'results/runtime_validation/compare') / str(seed) / strategy)
    return results


def compare_dispatch_algorithms(algo_a='greedy', algo_b='hungarian', output_dir=None, seeds=(42,)):
    """기존 배차 비교 명령도 원본 설정을 변경하지 않고 실행한다."""
    return {f'{seed}/{algo}': isolated_run({**CFG, 'passenger_seed': seed, 'taxi_dispatch_algorithm': algo},
            Path(output_dir or ROOT / 'results/dispatch_comparison') / str(seed) / algo)
            for seed in seeds for algo in (algo_a, algo_b)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', nargs='?', default='current', choices=['current','_worker','compare','dispatch_compare','patrol','prepositioned'])
    parser.add_argument('algorithms', nargs='*', choices=['greedy','hungarian','routeExtension'])
    parser.add_argument('--config-path')
    parser.add_argument('--output-dir', type=Path, default=ROOT / 'results/simulation')
    parser.add_argument('--seeds', type=int, nargs='+', default=[42,43,44,45,46])
    parser.add_argument('--sumo-binary', default='sumo')
    parser.add_argument('--gui-delay', type=float, default=None,
                        help='SUMO GUI의 스텝 간 표시 지연(초). GUI 기본값은 0.01초입니다.')
    args = parser.parse_args()
    if args.mode == 'compare':
        compare_strategies(CFG, args.output_dir, args.seeds)
    elif args.mode == 'dispatch_compare':
        compare_dispatch_algorithms(*(args.algorithms or ['greedy','hungarian']), output_dir=args.output_dir, seeds=args.seeds)
    elif args.mode == '_worker':
        print_result('worker', run_and_measure(sumo_binary=args.sumo_binary, meta_path=args.output_dir / 'sumo/runtime_meta.json', output_dir=args.output_dir, gui_delay=args.gui_delay))
    elif args.mode in ('patrol', 'prepositioned') or args.config_path:
        cfg = {**CFG, 'passenger_seed': args.seeds[0]}
        if args.mode in ('patrol','prepositioned'):
            cfg['taxi_strategy'] = args.mode
        print_result(args.mode, isolated_run(cfg, args.output_dir, args.sumo_binary))
    else:
        print_result('current', run_and_measure(sumo_binary=args.sumo_binary, output_dir=args.output_dir, gui_delay=args.gui_delay))


if __name__ == '__main__':
    main()
