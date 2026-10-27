"""실제 SUMO 상태를 Unity 재생 JSON으로 기록한다. 배차 로직은 공용 실행기를 사용한다."""
import argparse
import json
from pathlib import Path
import xml.etree.ElementTree as ET

import h3
import sumolib
import traci

from measure_wait_time import META_PATH, ROOT, RL_MODEL_PATH, run_and_measure


def export_map(meta_path):
    meta_path = Path(meta_path)
    meta = json.loads(meta_path.read_text(encoding='utf-8'))
    directory = meta_path.parent
    cfg = ET.parse(directory / 'simulation.sumocfg').getroot()
    net = sumolib.net.readNet(str(directory / cfg.find('input/net-file').get('value')))
    x0, y0, x1, y1 = net.getBoundary()
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2

    def point(x, y):
        return {'x': round(x - cx, 3), 'z': round(y - cy, 3)}

    def geo_point(lat, lng):
        config = meta['config']
        if config['use_real_map']:
            return point(*net.convertLonLat2XY(lng, lat))
        # build_env.py의 합성 bbox 매핑을 역변환한다. 실제 측량 좌표가 아니다.
        return point(x0 + (lng - config['lng_min']) / (config['lng_max'] - config['lng_min']) * (x1 - x0),
                     y0 + (lat - config['lat_min']) / (config['lat_max'] - config['lat_min']) * (y1 - y0))

    cells = sorted(set(meta['edge_cells'].values()))
    cfg_meta = meta['config']
    # 날씨는 build_env 시점의 '실시간' Open-Meteo 값이다 (sim_date와 같은 날의 날씨가 아님). 조회 실패 시 available=False.
    temp, rain, wind = (cfg_meta.get(k) for k in ('current_temperature', 'current_precipitation', 'current_wind_speed'))
    weather = {'available': temp is not None or rain is not None,
               'temperature': float(temp) if temp is not None else 0.0,
               'precipitation': float(rain) if rain is not None else 0.0,
               'windSpeed': float(wind) if wind is not None else 0.0,
               'simDate': str(cfg_meta.get('sim_date') or ''), 'live': True}
    result = {'version': 1, 'source': 'SUMO / synthetic demand', 'weather': weather, 'coordinateMapping': meta['coordinate_mapping'],
              'startHour': meta['sim_start_hour'], 'width': x1 - x0, 'height': y1 - y0, 'offsetX': cx, 'offsetY': cy,
              'grid': not meta['config']['use_real_map'], 'blockSize': meta['config']['grid_length'],
              'roads': [{'id': lane.getID(), 'width': lane.getWidth(),
                         'points': [point(*p) for p in lane.getShape()]}
                        for edge in net.getEdges() for lane in edge.getLanes()],
              'junctions': [{'id': node.getID(), 'points': [point(*p) for p in node.getShape()]}
                            for node in net.getNodes() if len(node.getShape()) >= 3],
              'cells': [{'id': cell, 'center': geo_point(*h3.cell_to_latlng(cell)),
                         'points': [geo_point(*p) for p in h3.cell_to_boundary(cell)]} for cell in cells]}
    return result


class ReplayRecorder:
    def __init__(self, destination, map_data, stride=1, backup_destination=None):
        self.destination, self.map = Path(destination), map_data
        self.backup_destination = Path(backup_destination) if backup_destination else None
        self.stride = max(1, int(stride))  # N초 간격으로만 기록 (Unity가 프레임 사이를 보간)
        self.ids, self.entities, self.frames, self.forecasts = {}, [], [], []
        self.last_forecast = -1

    def capture(self, now, requested, pickup, arrived, removed, failed, forecast, teleports,
                vehicle_ids=None, idle_taxis=None, person_ids=None):
        if self.frames and now - self.frames[-1]['t'] < self.stride - 1e-6:
            return  # stride 간격 사이의 스텝은 건너뜀 (첫 프레임은 항상 기록)
        idle = set(idle_taxis) if idle_taxis is not None else set(traci.vehicle.getTaxiFleet(0))
        taxis = set(traci.vehicle.getTaxiFleet(-1))
        vehicles = []
        for vid in sorted(vehicle_ids if vehicle_ids is not None else traci.vehicle.getIDList()):
            if vid not in self.ids:
                kind = 'taxi' if vid in taxis else traci.vehicle.getTypeID(vid)
                self.ids[vid] = len(self.entities)
                self.entities.append({'id': vid, 'kind': kind})
            state = 4
            if vid in taxis:
                state = 2 if traci.vehicle.getPersonNumber(vid) else 0 if vid in idle else 1
                if state == 0 and forecast and vid in forecast.committed:
                    state = 3
            elif 'auto' in traci.vehicle.getTypeID(vid).lower():
                state = 5
            elif 'obstacle' in traci.vehicle.getTypeID(vid).lower():
                state = 6
            x, y = traci.vehicle.getPosition(vid)
            vehicles.append({'i': self.ids[vid], 'x': round(x - self.map['offsetX'], 2),
                             'z': round(y - self.map['offsetY'], 2),
                             'a': round(traci.vehicle.getAngle(vid), 1), 's': state})
        valid_pickup = {pid: t for pid, t in pickup.items() if pid not in removed}
        pending = set(requested) - set(valid_pickup) - set(removed) - failed
        people = []
        for pid in sorted(pending & (set(person_ids) if person_ids is not None else set(traci.person.getIDList()))):
            x, y = traci.person.getPosition(pid)
            people.append({'x': round(x - self.map['offsetX'], 3), 'z': round(y - self.map['offsetY'], 3)})
        if forecast is not None and hasattr(forecast, 'decisions'):
            # RLRepositionMaintainer: H3 셀이 아니라 POI 구역(카테고리) 단위 의사결정 스냅샷
            if forecast.decisions and len(forecast.decisions) != self.last_forecast:
                self.last_forecast = len(forecast.decisions)
                last = forecast.decisions[-1]
                self.forecasts.append({'t': last['t'], 'zones': last['zones'], 'cells': []})
        elif forecast and len(forecast.records) != self.last_forecast:
            self.last_forecast = len(forecast.records)
            rows = forecast.records[-len(self.map['cells']):]
            self.forecasts.append({'t': now, 'zones': [], 'cells': [
                {'demand': row['predicted_demand'], 'supply': row['available_taxis'],
                 'target': row['target_taxis'], 'surge': row['surge_multiplier'],
                 'horizon': [row[f'forecast_{i}'] for i in range(1, 7)]} for row in rows]})
        waits = [valid_pickup[pid] - requested[pid] for pid in valid_pickup]
        self.frames.append({'t': now, 'vehicles': vehicles, 'people': people,
                            'requested': len(requested), 'picked': len(valid_pickup), 'waiting': len(pending),
                            'arrived': len(arrived), 'timedOut': len(set(requested) & set(removed)),
                            'avgWait': sum(waits) / len(waits) if waits else 0,
                            'moves': len(forecast.moves) if forecast else 0,
                            'teleports': teleports, 'forecast': len(self.forecasts) - 1})

    def finish(self, metrics):
        payload = {'version': 1, 'strategy': metrics['strategy'], 'algorithm': metrics['algorithm'],
                   'fingerprint': metrics['demand_fingerprint'], 'duration': metrics['duration_sec'],
                   'startHour': self.map['startHour'], 'stride': self.stride, 'entities': self.entities, 'frames': self.frames,
                   'forecasts': self.forecasts, 'metrics': metrics}
        # 1. backup 파일에 먼저 안전하게 저장
        target_to_write = self.backup_destination or self.destination
        target_to_write.parent.mkdir(parents=True, exist_ok=True)
        target_to_write.write_text(json.dumps(payload, separators=(',', ':'), allow_nan=False), encoding='utf-8')
        print(f'[백업 저장] {target_to_write} ({target_to_write.stat().st_size / 1e6:.1f} MB, 프레임 {len(self.frames)}개, stride {self.stride})', flush=True)

        # 2. 최신본 파일로 덮어쓰기 복사
        if self.backup_destination:
            self.destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(target_to_write, self.destination)
            print(f'[최신본 갱신] {self.destination}', flush=True)

        for warning in validate_replay(payload, self.stride):
            print('[검증 경고]', warning, flush=True)


def validate_replay(data, stride=1):
    """구조 오류(인덱스/시간/상태값)는 assert로 막고, 집계 수치 불일치는 경고 목록으로 돌려준다.
    (타임아웃으로 소멸 처리된 뒤 뒤늦게 탑승한 승객이 picked와 timedOut에 둘 다 잡히는 게 정상이라 합계 단언은 쓰지 않는다.)"""
    assert data['version'] == 1 and len(data['frames']) > 1
    assert all(a['t'] < b['t'] for a, b in zip(data['frames'], data['frames'][1:]))
    # stride>1이면 마지막 스텝이 기록 간격에 안 걸릴 수 있어 duration과 stride 이내 차이를 허용
    assert -1e-6 <= data['duration'] - data['frames'][-1]['t'] < stride + 1e-6
    assert len({e['id'] for e in data['entities']}) == len(data['entities'])
    warnings = []
    for frame in data['frames']:
        ids = [v['i'] for v in frame['vehicles']]
        assert len(ids) == len(set(ids)) and all(0 <= i < len(data['entities']) for i in ids)
        assert all(0 <= v['s'] <= 6 for v in frame['vehicles'])
        assert -1 <= frame['forecast'] < len(data['forecasts'])
        if frame['picked'] > frame['requested'] or frame['waiting'] > frame['requested']:
            warnings.append(f"t={frame['t']}: picked/waiting이 requested보다 큼")
            break
    last, n_measured = data['frames'][-1]['picked'], data['metrics']['n_measured']
    if last != n_measured:
        warnings.append(f'마지막 프레임 picked={last} / metrics.n_measured={n_measured} '
                        '(소멸 후 뒤늦게 탑승한 승객이 picked에는 포함되고 n_measured에서는 빠져서 다를 수 있음)')
    return warnings


if __name__ == '__main__':
    import shutil
    from datetime import datetime

    parser = argparse.ArgumentParser()
    parser.add_argument('--meta', type=Path, default=META_PATH)
    parser.add_argument('--output', type=Path, default=Path(ROOT) / 'results/unity_replay')
    parser.add_argument('--stride', type=int, default=10, help='N초 간격으로 기록 (기본 10, 1이면 매 스텝)')
    parser.add_argument('--strategies', nargs='+', default=['patrol', 'rl_prepositioned'],
                        choices=['patrol', 'prepositioned', 'rl_prepositioned'],
                        help='실행할 전략 목록 (기본: patrol rl_prepositioned)')
    parser.add_argument('--algorithm', type=str, default='hungarian',
                        help='배차 알고리즘 (기본: hungarian)')
    parser.add_argument('--only', choices=['patrol', 'prepositioned', 'rl_prepositioned'],
                        help='(하위호환) 단일 전략만 실행')
    parser.add_argument('--unity-dir', type=Path, default=None,
                        help='Unity Assets/StreamingAssets 디렉터리 경로 (지정 시 완료 후 자동 복사)')
    args = parser.parse_args()

    strategies_to_run = [args.only] if args.only else args.strategies

    # 모든 전략이 같은 날짜·같은 승객으로 돌아야 공정 비교다.
    from config_loader import CFG
    CFG['run_date_mode'] = 'sim_date'
    print(f"[안내] 선택된 전략 {strategies_to_run} 모두 sim_date={CFG.get('sim_date')} 로 고정해서 실행합니다.")
    if 'rl_prepositioned' in strategies_to_run and not Path(RL_MODEL_PATH).exists():
        raise SystemExit(f'RL 모델이 없습니다: {RL_MODEL_PATH}\n없으면 rl_prepositioned이 patrol과 똑같이 나옵니다. rl_train.py를 먼저 실행하세요.')

    map_data = export_map(args.meta)
    args.output.mkdir(parents=True, exist_ok=True)

    # ── 백업 폴더 준비 및 날씨/날짜 태그 생성 ──────────────────
    _ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    _w = map_data.get('weather', {})
    _sim_date = _w.get('simDate') or CFG.get('sim_date') or 'unknown'
    _rain = _w.get('precipitation', 0.0)
    _tag = f"{_ts}_{_sim_date}_rain{_rain}"

    _backup_dir = args.output / 'backup'
    _backup_dir.mkdir(parents=True, exist_ok=True)

    # map.json: 백업에 먼저 저장 후 최신본으로 복사
    _map_backup = _backup_dir / f'{_tag}_map.json'
    _map_backup.write_text(json.dumps(map_data, separators=(',', ':'), allow_nan=False), encoding='utf-8')
    shutil.copy2(_map_backup, args.output / 'map.json')
    print(f'[백업 저장] map.json -> backup/{_map_backup.name}', flush=True)
    print(f'[최신본 갱신] {args.output / "map.json"}', flush=True)

    generated_files = [args.output / 'map.json']
    results = []
    for strategy in strategies_to_run:
        backup_file = _backup_dir / f'{_tag}_{strategy}.json'
        target_file = args.output / f'{strategy}.json'
        recorder = ReplayRecorder(target_file, map_data, stride=args.stride, backup_destination=backup_file)
        result = run_and_measure(meta_path=args.meta, sumo_cfg_path=args.meta.with_name('simulation.sumocfg'),
                                 strategy=strategy, algorithm=args.algorithm, recorder=recorder)
        results.append(result)
        generated_files.append(target_file)
        print(f"[{strategy}] 완료: {result}", flush=True)

    fingerprints = {r['demand_fingerprint'] for r in results}
    if len(strategies_to_run) > 1:
        assert len(fingerprints) == 1, f'전략들의 승객 수요가 서로 다릅니다: {fingerprints}'
    assert all(r['completed_interval'] for r in results)

    # ── Unity StreamingAssets 자동 복사 ──────────────────
    if args.unity_dir:
        unity_dir = Path(args.unity_dir)
        if unity_dir.exists():
            print(f'[유니티 복사] {unity_dir} 경로로 생성된 파일 복사 시작...', flush=True)
            for gf in generated_files:
                if gf.exists():
                    try:
                        shutil.copy2(gf, unity_dir / gf.name)
                        print(f'  ✓ {gf.name} -> {unity_dir / gf.name}', flush=True)
                    except Exception as e:
                        print(f'  ✗ {gf.name} 복사 실패: {e}', flush=True)
        else:
            print(f'[유니티 복사 건너뜀] 대상 경로가 존재하지 않습니다: {unity_dir}', flush=True)
