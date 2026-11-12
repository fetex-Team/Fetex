"""실제 SUMO 상태를 Unity 재생 JSON으로 기록한다. 배차 로직은 공용 실행기(measure_wait_time)를 사용한다.

[재생 포맷 v2 — 차량이 도로를 따라 움직이게]
v1은 10초마다 차의 "앞범퍼" 좌표만 저장하고 Unity가 그 사이를 직선으로 이었다.
그래서 교차로에서 좌·우회전하면 차가 모서리를 가로질러(포물선처럼) 도로 밖으로 나갔다
(회전 구간의 약 47%가 5m 이상 이탈, module1_simulation/unity/verify_replay_motion.py로 측정).

v2는 다음을 함께 저장한다.
- map.json `lanes`: 교차로 안쪽 연결 차선(SUMO internal lane)까지 포함한 모든 차선의 중심선
- 프레임마다 차량의 차선 번호 `l`, 차선 위 위치 `p`(m, 앞범퍼), 속도 `v`(m/s)
- 이전 프레임 이후 지나온 차선 목록 `w` (교차로 연결 차선 포함, 매 스텝 추적)
- 차량 길이 `len` (entities) — Unity가 앞범퍼가 아니라 차 중심을 도로 위에 놓도록
- `x`,`z`는 이제 차 "중심" 좌표 (v1 방식으로 재생하는 구버전 플레이어도 조금 나아짐)

Unity(FetexReplayPlayer + ReplayMotion)는 두 프레임 사이를 지나온 차선들의 실제 중심선을 따라
이동시키고, 속도로 가감속을 보간하며, 방향은 도로 접선으로 정한다.
"""
import argparse
import gzip
import os
import sys

# tools/에서 직접 실행해도 프로젝트 모듈(measure_wait_time, module1_simulation 등)을 찾도록 루트 등록
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import json
import math
import shutil
from pathlib import Path
import xml.etree.ElementTree as ET

import h3
import sumolib
import traci
import module1_simulation.vehicle_subscriptions as vehicle_subscriptions
import traci.constants as tc

from measure_wait_time import META_PATH, ROOT, RL_MODEL_PATH, run_and_measure

REPLAY_VERSION = 2
_SUBSCRIBE_VARS = (tc.VAR_LANE_ID, tc.VAR_LANEPOSITION, tc.VAR_POSITION, tc.VAR_ANGLE, tc.VAR_SPEED)


def export_map(meta_path):
    meta_path = Path(meta_path)
    meta = json.loads(meta_path.read_text(encoding='utf-8'))
    directory = meta_path.parent
    cfg = ET.parse(directory / 'simulation.sumocfg').getroot()
    net = sumolib.net.readNet(str(directory / cfg.find('input/net-file').get('value')), withInternal=True)
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
    normal_edges = [e for e in net.getEdges() if e.getFunction() != 'internal']
    all_lanes = [lane for edge in net.getEdges() for lane in edge.getLanes()]
    lanes = [{'id': lane.getID(), 'e': lane.getEdge().getID(), 'len': round(lane.getLength(), 2),
              'w': round(lane.getWidth(), 2), 'isInternal': lane.getEdge().getFunction() == 'internal',
              'xz': [round(v, 2) for x, y in lane.getShape() for v in (x - cx, y - cy)]}
             for lane in all_lanes]
    demand_source = cfg_meta.get('demand_source', 'real')
    result = {'version': REPLAY_VERSION,
              'source': 'SUMO / TIMS 실측 보정 수요' if demand_source == 'real' else 'SUMO / synthetic demand',
              'weather': weather, 'coordinateMapping': meta['coordinate_mapping'],
              'startHour': meta['sim_start_hour'], 'width': x1 - x0, 'height': y1 - y0, 'offsetX': cx, 'offsetY': cy,
              'grid': not meta['config']['use_real_map'], 'blockSize': meta['config']['grid_length'],
              # roads: 도로 메시용(교차로 내부 제외, v1과 동일). lanes: 차량 이동 경로용(교차로 내부 포함).
              'roads': [{'id': lane.getID(), 'width': lane.getWidth(),
                         'points': [point(*p) for p in lane.getShape()]}
                        for edge in normal_edges for lane in edge.getLanes()],
              'lanes': lanes,
              'junctions': [{'id': node.getID(), 'points': [point(*p) for p in node.getShape()]}
                            for node in net.getNodes() if len(node.getShape()) >= 3],
              'cells': [{'id': cell, 'center': geo_point(*h3.cell_to_latlng(cell)),
                         'points': [geo_point(*p) for p in h3.cell_to_boundary(cell)]} for cell in cells]}
    return result


class ReplayRecorder:
    """매 시뮬레이션 스텝 호출된다. 차선 이동 기록은 매 스텝, 프레임 저장은 stride초마다."""

    def __init__(self, destination, map_data, stride=1, backup_destination=None):
        self.destination, self.map = Path(destination), map_data
        self.backup_destination = Path(backup_destination) if backup_destination else None
        self.stride = max(1, int(stride))  # N초 간격으로만 프레임 저장 (Unity가 차선 경로를 따라 보간)
        self.ids, self.entities, self.frames, self.forecasts = {}, [], [], []
        self.last_forecast = -1
        self.lane_index = {lane['id']: i for i, lane in enumerate(map_data.get('lanes', []))}
        self._subscribed = set()
        self._trail = {}          # vid -> 마지막 저장 프레임 이후 거쳐 간 차선 번호들 (첫 원소 = 그 프레임의 차선)
        self._in_last_frame = set()
        self._lengths = {}

    # ---- 매 스텝: 차선 이동만 추적 (TraCI 구독으로 스텝당 호출 1번) ----
    def _track(self, vehicle_ids):
        # 택시 매니저도 같은 차량에 구독을 걸기 때문에 공용 관리자로 변수 합집합을 유지한다 (덮어쓰기 방지)
        new_ids = [vid for vid in vehicle_ids if vid not in self._subscribed]
        vehicle_subscriptions.ensure(new_ids, _SUBSCRIBE_VARS)
        self._subscribed.update(new_ids)
        results = traci.vehicle.getAllSubscriptionResults()
        alive = set(results)
        self._subscribed &= alive
        for vid, res in results.items():
            trail = self._trail.setdefault(vid, [])
            fx, fy = res.get(tc.VAR_POSITION, (-1e9, -1e9))
            lane_id = res.get(tc.VAR_LANE_ID, '')
            if not (abs(fx) < 1e7 and abs(fy) < 1e7):
                li = -1                      # 텔레포트 중 -> 경로가 끊김 표시 (Unity는 이 구간을 직선 보간)
            elif lane_id == '':
                if trail and trail[-1] >= 0:
                    continue                 # 승하차 정차(갓길 주차) 중 -> 마지막 차선에 그대로 있는 것으로 본다
                li = -1
            else:
                li = self.lane_index.get(lane_id, -1)
            if not trail or trail[-1] != li:
                trail.append(li)
        for vid in list(self._trail):
            if vid not in alive:
                del self._trail[vid]
        return results

    def capture(self, now, requested, pickup, arrived, removed, failed, forecast, teleports,
                vehicle_ids=None, idle_taxis=None, person_ids=None, is_last=False):
        vids = vehicle_ids if vehicle_ids is not None else traci.vehicle.getIDList()
        subs = self._track(vids)
        if self.frames and not is_last and now - self.frames[-1]['t'] < self.stride - 1e-6:
            return  # stride 간격 사이의 스텝은 프레임을 저장하지 않음 (차선 이동은 위에서 이미 추적)
        idle = set(idle_taxis) if idle_taxis is not None else set(traci.vehicle.getTaxiFleet(0))
        taxis = set(traci.vehicle.getTaxiFleet(-1))
        vehicles = []
        in_frame = set()
        for vid in sorted(subs):
            res = subs[vid]
            if vid not in self.ids:
                type_id = traci.vehicle.getTypeID(vid)
                kind = 'taxi' if vid in taxis else type_id
                self.ids[vid] = len(self.entities)
                self._lengths[vid] = traci.vehicle.getLength(vid)
                self.entities.append({'id': vid, 'kind': kind, 'len': round(self._lengths[vid], 2)})
            type_id = self.entities[self.ids[vid]]['kind']
            state = 4
            if vid in taxis:
                state = 2 if traci.vehicle.getPersonNumber(vid) else 0 if vid in idle else 1
                if state == 0 and forecast and vid in getattr(forecast, 'committed', ()):
                    state = 3
            elif 'auto' in type_id.lower():
                state = 5
            elif 'obstacle' in type_id.lower():
                state = 6
            fx, fy = res[tc.VAR_POSITION]
            angle = res[tc.VAR_ANGLE]
            trail = self._trail.get(vid, [])
            li = trail[-1] if trail else -1
            if not (abs(fx) < 1e7 and abs(fy) < 1e7):
                # 텔레포트 중이거나 아직 도로에 못 올라간 차량은 SUMO가 무효 좌표(-2^30)를 준다 -> 이 프레임에서 숨김
                self._trail[vid] = [li]
                continue
            half = self._lengths[vid] / 2.0
            rad = math.radians(angle)  # SUMO: 북쪽 0도, 시계방향 -> 진행방향 벡터 (sin, cos)
            cx, cy = fx - half * math.sin(rad), fy - half * math.cos(rad)
            state_row = {'i': self.ids[vid], 'x': round(cx - self.map['offsetX'], 2),
                         'z': round(cy - self.map['offsetY'], 2), 'a': round(angle, 1), 's': state,
                         'l': li, 'p': round(res[tc.VAR_LANEPOSITION], 2), 'v': round(res[tc.VAR_SPEED], 2)}
            if vid in self._in_last_frame and len(trail) > 2:
                state_row['w'] = trail[1:-1]
            vehicles.append(state_row)
            in_frame.add(vid)
            self._trail[vid] = [li]
        self._in_last_frame = in_frame

        valid_pickup = {pid: t for pid, t in pickup.items() if pid not in removed}
        pending = set(requested) - set(valid_pickup) - set(removed) - failed
        people = []
        for pid in sorted(pending & (set(person_ids) if person_ids is not None else set(traci.person.getIDList()))):
            x, y = traci.person.getPosition(pid)
            people.append({'x': round(x - self.map['offsetX'], 2), 'z': round(y - self.map['offsetY'], 2)})
        if forecast is not None and hasattr(forecast, 'decisions'):
            # RLRepositionMaintainer: H3 셀이 아니라 POI 구역(카테고리) 단위 의사결정 스냅샷
            if forecast.decisions and len(forecast.decisions) != self.last_forecast:
                self.last_forecast = len(forecast.decisions)
                last = forecast.decisions[-1]
                self.forecasts.append({'t': last['t'], 'zones': last['zones'], 'cells': []})
        elif forecast and hasattr(forecast, 'records') and len(forecast.records) != self.last_forecast:
            self.last_forecast = len(forecast.records)
            rows = forecast.records[-len(self.map['cells']):]
            self.forecasts.append({'t': now, 'zones': [], 'cells': [
                {'demand': row['predicted_demand'], 'supply': row['available_taxis'],
                 'target': row['target_taxis'], 'surge': row['surge_multiplier'],
                 'horizon': [row[f'forecast_{i}'] for i in range(1, 7)]} for row in rows]})
        waits = [valid_pickup[pid] - requested[pid] for pid in valid_pickup if pid in requested]
        self.frames.append({'t': now, 'vehicles': vehicles, 'people': people,
                            'requested': len(requested), 'picked': len(valid_pickup), 'waiting': len(pending),
                            'arrived': len(arrived), 'timedOut': len(set(requested) & set(removed)),
                            'avgWait': round(sum(waits) / len(waits), 2) if waits else 0,
                            'moves': len(forecast.moves) if forecast and hasattr(forecast, 'moves') else 0,
                            'teleports': teleports, 'forecast': len(self.forecasts) - 1})

    def finish(self, metrics):
        payload = {'version': REPLAY_VERSION, 'strategy': metrics['strategy'], 'algorithm': metrics['algorithm'],
                   'fingerprint': metrics['demand_fingerprint'], 'duration': metrics['duration_sec'],
                   'startHour': self.map['startHour'], 'stride': self.stride, 'entities': self.entities,
                   'frames': self.frames, 'forecasts': self.forecasts,
                   'metrics': {k: v for k, v in metrics.items() if k != 'waits'}}
        # 1. backup 파일에 먼저 안전하게 저장
        target_to_write = self.backup_destination or self.destination
        target_to_write.parent.mkdir(parents=True, exist_ok=True)
        write_replay(target_to_write, payload)
        print(f'[백업 저장] {target_to_write} ({target_to_write.stat().st_size / 1e6:.1f} MB, '
              f'프레임 {len(self.frames)}개, stride {self.stride})', flush=True)

        # 2. 최신본 파일로 덮어쓰기 복사
        if self.backup_destination:
            self.destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(target_to_write, self.destination)
            print(f'[최신본 갱신] {self.destination}', flush=True)

        for warning in validate_replay(payload, self.stride, n_lanes=len(self.lane_index)):
            print('[검증 경고]', warning, flush=True)


def write_replay(path, payload):
    """재생 파일 저장. 확장자가 .gz면 gzip 압축 (JSON 48MB -> 약 8MB, Unity가 그대로 풀어서 읽음)."""
    path = Path(path)
    text = json.dumps(payload, separators=(',', ':'), allow_nan=False, ensure_ascii=False)
    if path.suffix == '.gz':
        with gzip.open(path, 'wt', encoding='utf-8', compresslevel=6) as f:
            f.write(text)
    else:
        path.write_text(text, encoding='utf-8')


def read_replay(path):
    """.json / .json.gz 모두 읽기."""
    path = Path(path)
    if path.suffix == '.gz':
        with gzip.open(path, 'rt', encoding='utf-8') as f:
            return json.load(f)
    return json.loads(path.read_text(encoding='utf-8'))


def validate_replay(data, stride=1, n_lanes=None):
    """구조 오류(인덱스/시간/상태값/차선 번호)는 assert로 막고, 집계 수치 불일치는 경고 목록으로 돌려준다."""
    assert data['version'] in (1, 2) and len(data['frames']) > 1
    assert all(a['t'] < b['t'] for a, b in zip(data['frames'], data['frames'][1:]))
    # 마지막 스텝은 항상 기록하므로 duration과 마지막 프레임 시각이 같아야 한다 (v1은 stride 이내 허용)
    assert -1e-6 <= data['duration'] - data['frames'][-1]['t'] < stride + 1e-6
    assert len({e['id'] for e in data['entities']}) == len(data['entities'])
    warnings = []
    for frame in data['frames']:
        ids = [v['i'] for v in frame['vehicles']]
        assert len(ids) == len(set(ids)) and all(0 <= i < len(data['entities']) for i in ids)
        assert all(0 <= v['s'] <= 6 for v in frame['vehicles'])
        assert -1 <= frame['forecast'] < len(data['forecasts'])
        if n_lanes is not None and data['version'] >= 2:
            for v in frame['vehicles']:
                assert -1 <= v['l'] < n_lanes, f"t={frame['t']}: 차선 번호 범위 밖 {v['l']}"
                assert all(-1 <= w < n_lanes for w in v.get('w', ()))
        if frame['picked'] > frame['requested'] or frame['waiting'] > frame['requested']:
            warnings.append(f"t={frame['t']}: picked/waiting이 requested보다 큼")
            break
    last, n_measured = data['frames'][-1]['picked'], data['metrics']['n_measured']
    if last != n_measured:
        warnings.append(f'마지막 프레임 picked={last} / metrics.n_measured={n_measured} 불일치')
    return warnings


def main():
    from datetime import datetime

    parser = argparse.ArgumentParser(description='SUMO 실행 결과를 Unity 재생 파일(map.json + 전략별 json)로 내보낸다.')
    parser.add_argument('--meta', type=Path, default=META_PATH)
    parser.add_argument('--output', type=Path, default=Path(ROOT) / 'results/unity_replay')
    parser.add_argument('--stride', type=int, default=5,
                        help='N초 간격으로 프레임 저장 (기본 5). 차선 경로는 매 스텝 추적하므로 간격이 커도 도로를 벗어나지 않는다.')
    parser.add_argument('--strategies', nargs='+', default=['patrol', 'prepositioned', 'rl_prepositioned'],
                        choices=['patrol', 'prepositioned', 'rl_prepositioned', 'forecast', 'surge'],
                        help='실행할 전략 목록 (기본: patrol prepositioned rl_prepositioned)')
    parser.add_argument('--algorithm', type=str, default='hungarian', help='배차 알고리즘 (기본: hungarian)')
    parser.add_argument('--only', choices=['patrol', 'prepositioned', 'rl_prepositioned', 'forecast', 'surge'],
                        help='(하위호환) 단일 전략만 실행')
    parser.add_argument('--no-gzip', action='store_true',
                        help='재생 파일을 압축하지 않고 .json으로 저장 (기본: .json.gz, Unity가 둘 다 읽음)')
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

    args.output.mkdir(parents=True, exist_ok=True)
    _ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    _backup_dir = args.output / 'backup'
    _backup_dir.mkdir(parents=True, exist_ok=True)

    generated_files, results = [], []
    ext = '.json' if args.no_gzip else '.json.gz'
    for stale in (args.output / f'{s}{".json.gz" if args.no_gzip else ".json"}' for s in strategies_to_run):
        if stale.exists():
            stale.unlink()   # 같은 전략의 다른 형식 옛 파일이 남아 Unity가 그걸 읽지 않게
    map_data = None
    for strategy in strategies_to_run:
        # run_and_measure가 전략마다 build_env를 다시 돌리므로, 지도는 첫 실행 직후의 meta로 만든다.
        recorder_holder = {}

        def make_recorder():
            nonlocal map_data
            if map_data is None:
                map_data = export_map(args.meta)
            _w = map_data.get('weather', {})
            tag = f"{_ts}_{_w.get('simDate') or CFG.get('sim_date') or 'unknown'}_rain{_w.get('precipitation', 0.0)}"
            recorder_holder['tag'] = tag
            return ReplayRecorder(args.output / f'{strategy}{ext}', map_data, stride=args.stride,
                                  backup_destination=_backup_dir / f'{tag}_{strategy}{ext}')

        result = run_and_measure(meta_path=args.meta, sumo_cfg_path=args.meta.with_name('simulation.sumocfg'),
                                 strategy=strategy, algorithm=args.algorithm, recorder_factory=make_recorder)
        results.append(result)
        generated_files.append(args.output / f'{strategy}{ext}')
        print(f"[{strategy}] 완료: { {k: v for k, v in result.items() if k not in ('waits', 'demand_summary')} }",
              flush=True)

    # map.json: 백업에 먼저 저장 후 최신본으로 복사
    _map_backup = _backup_dir / f"{recorder_holder.get('tag', _ts)}_map.json"
    _map_backup.write_text(json.dumps(map_data, separators=(',', ':'), allow_nan=False, ensure_ascii=False),
                           encoding='utf-8')
    shutil.copy2(_map_backup, args.output / 'map.json')
    generated_files.insert(0, args.output / 'map.json')
    print(f'[최신본 갱신] {args.output / "map.json"} (차선 {len(map_data["lanes"])}개, 교차로 내부 포함)', flush=True)

    fingerprints = {r['demand_fingerprint'] for r in results}
    if len(strategies_to_run) > 1 and len(fingerprints) != 1:
        raise SystemExit(f'[중단] 전략들의 승객 수요가 서로 다릅니다: {fingerprints} — 공정 비교가 아닙니다.')
    if not all(r['completed_interval'] for r in results):
        raise SystemExit('[중단] 시뮬레이션이 설정 시간까지 끝나지 않은 전략이 있습니다.')
    print(f'[공정 비교 확인] 모든 전략의 수요 지문이 같습니다: {fingerprints.pop()}', flush=True)

    if args.unity_dir:
        unity_dir = Path(args.unity_dir)
        if unity_dir.exists():
            print(f'[유니티 복사] {unity_dir} 경로로 생성된 파일 복사 시작...', flush=True)
            for gf in generated_files:
                if gf.exists():
                    try:
                        shutil.copy2(gf, unity_dir / gf.name)
                        print(f'  ✓ {gf.name} -> {unity_dir / gf.name}', flush=True)
                        if gf.name.endswith('.json.gz') and (unity_dir / gf.name[:-3]).exists():
                            (unity_dir / gf.name[:-3]).unlink()   # 압축 전 옛 파일 제거 (Unity는 .gz를 우선 읽음)
                            print(f'    - 옛 파일 {gf.name[:-3]} 삭제', flush=True)
                    except Exception as e:
                        print(f'  ✗ {gf.name} 복사 실패: {e}', flush=True)
        else:
            print(f'[유니티 복사 건너뜀] 대상 경로가 존재하지 않습니다: {unity_dir}', flush=True)


if __name__ == '__main__':
    from module1_simulation.reproducibility import ensure_fixed_hash_seed
    ensure_fixed_hash_seed()
    main()
