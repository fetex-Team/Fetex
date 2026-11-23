"""SUMO 런타임 회귀 검사: 외부 API 없이 모의 TraCI 사용."""
import json
import os
import random
from collections import Counter
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

os.environ.setdefault('MOBILITY_CONFIG', str(Path(__file__).resolve().parents[1] / 'presets/runtime_minimal.json'))
import pytest
import traci
from config_loader import DEFAULT_CONFIG, load_config
import module1_simulation.passenger_spawn_manager as spawn_module
import module1_simulation.passenger_manager as timeout_module
from module1_simulation.passenger_spawn_manager import PassengerSpawnManager
from module1_simulation.passenger_manager import PassengerTimeoutManager
import sys
ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))
if str(ROOT_DIR / "tools") not in sys.path:
    sys.path.insert(0, str(ROOT_DIR / "tools"))

from measure_wait_time import summarize
from tools.validate_runtime import schedule_status

ZONES = dict(school=['s'], company=['c'], restaurant=['r'], residential=['h'], bus_stop=['b'])


@pytest.fixture
def fake(monkeypatch):
    active, boarded, arrived = set(), {}, set()
    def add(pid, *args, **kwargs): active.add(pid)
    def remove(pid): active.discard(pid)
    person = SimpleNamespace(add=Mock(side_effect=add), appendDrivingStage=Mock(), remove=Mock(side_effect=remove),
                             getIDList=lambda: sorted(active), getVehicle=lambda pid: boarded.get(pid, ''))
    api = SimpleNamespace(person=person, simulation=SimpleNamespace(getArrivedPersonIDList=lambda: sorted(arrived)),
                          exceptions=traci.exceptions)
    monkeypatch.setattr(spawn_module, 'traci', api)
    monkeypatch.setattr(timeout_module, 'traci', api)
    return SimpleNamespace(api=api, active=active, boarded=boarded, arrived=arrived)


def manager(**overrides):
    cfg = {**DEFAULT_CONFIG, 'num_passengers': 0, 'restaurant_pop_base': 0, **overrides}
    return PassengerSpawnManager(ZONES, cfg['sim_start_hour'], cfg['sim_end_hour'], seed=42, config=cfg)


def test_config_precedence(tmp_path, monkeypatch):
    a, b = tmp_path/'a.json', tmp_path/'b.json'
    for path, n in [(a, 17), (b, 29)]:
        path.write_text(json.dumps({'num_passengers': n, 'resolve_external_data': False}))
    monkeypatch.setenv('MOBILITY_CONFIG', str(a))
    assert load_config()['num_passengers'] == 17
    assert load_config(b)['num_passengers'] == 29


@pytest.mark.parametrize('start,end,school,company', [(6,7,False,False),(7,8,True,False),(8,10,False,True),(9,11,False,True),(10,11,False,False)])
def test_schedule_intersections(start,end,school,company):
    rows = {r['kind']: r for r in schedule_status({**DEFAULT_CONFIG, 'sim_start_hour':start,'sim_end_hour':end}, ZONES)}
    assert rows['school_morning']['overlaps'] == school
    assert rows['company_morning']['overlaps'] == company


@pytest.mark.parametrize('probability,expected', [(0,0),(1,20000)])
def test_gacha_exact_attempts_and_no_saturation(fake, probability, expected):
    m = manager(sim_start_hour=10,sim_end_hour=11,num_passengers=20000,residential_taxi_probability=probability)
    for second in range(3600):
        m.maintain(second)
        m.maintain(second + .1)
    m.maintain(3600)
    assert m.attempt_counts['residential_gacha'] == 20000
    assert m.spawn_counts['residential_gacha'] == expected


def test_school_company_randomness_independent(fake):
    records = []
    for population in [0,10000]:
        m=manager(sim_start_hour=7,sim_end_hour=10,num_passengers=population)
        for second in range(10800): m.maintain(second)
        records.append([(r['depart'],r['from_edge'],r['to_edge'],r['demand_type']) for r in m.records.values()
                        if r['demand_type'] in ('school_morning','company_morning')])
    assert records[0] == records[1]


def test_spawn_failure_cleanup(fake):
    fake.api.person.appendDrivingStage.side_effect = traci.exceptions.TraCIException('bad stage')
    m=manager(sim_start_hour=10,sim_end_hour=11)
    m._draw('residential_gacha',1,1,0,['a'],['b'])
    assert m.total_spawned == 0 and m.failed_counts['residential_gacha'] == 1
    assert not fake.active


def test_late_start_does_not_replay_lunch(fake):
    m=manager(sim_start_hour=14,sim_end_hour=15)
    m.absorbed['company']=100
    m.maintain(0)
    assert not m.spawn_counts['lunch_release'] and not m.spawn_counts['lunch_return']


def test_disappearance_is_not_arrival(fake):
    m=manager()
    pid=m._spawn_person(0,'b','c','company')
    m._process_arrivals(set(),0)
    fake.active.remove(pid)
    m._process_arrivals(set(),1)
    assert not m.absorbed['company']


def test_actual_arrival_is_absorbed(fake):
    m=manager()
    pid=m._spawn_person(0,'b','c','company')
    fake.active.remove(pid); fake.arrived.add(pid)
    m._process_arrivals(set(),1)
    assert m.absorbed['company'] == 1


def test_timeout_reserved_then_boarded_is_not_removed(fake):
    fake.active.add('p')
    dispatcher=SimpleNamespace(dispatched_person_ids={'p'})
    m=PassengerTimeoutManager(500,dispatcher)
    m.maintain(0); m.maintain(500)
    assert m.threshold_exceeded_at['p']==500 and not m.removed_pids
    fake.boarded['p']='taxi'; dispatcher.dispatched_person_ids.clear()
    m.maintain(501)
    assert 'p' in fake.active and not m.removed_pids and not m._pending_removal


def test_timeout_boundary_and_removal_failure(fake):
    fake.active.add('p'); m=PassengerTimeoutManager(500)
    m.maintain(0); m.maintain(499)
    assert not m.removed_pids
    fake.api.person.remove.side_effect=traci.exceptions.TraCIException('reserved')
    m.maintain(500)
    assert not m.removed_pids
    fake.api.person.remove.side_effect=lambda pid: fake.active.discard(pid)
    m.maintain(501)
    assert m.removed_at['p']==501 and m.threshold_exceeded_at['p']==500


def test_summary_partition_and_no_demand():
    rows=[dict(status=s,depart_sec=0,pickup_sec=20 if s=='picked_up' else None)
          for s in ['picked_up','timeout','pending','other_loss']]
    r=summarize(rows,500)
    assert r['n_total_passengers']==4 and r['timeout_rate']==.25 and r['avg_wait_sec']==20
    assert summarize([],500)['avg_wait_sec'] is None


def test_build_static_boundary(monkeypatch):
    from module1_simulation import build_env
    monkeypatch.setitem(build_env.CFG,'residential_schedule_scale',.1)
    assert not build_env.build_passenger_schedule(ZONES,6,7,1000,seed=42)
    trips=build_env.build_passenger_schedule(ZONES,7,8,1000,seed=42)
    assert trips and all(0 <= t < 3600 for t,_,_ in trips)


def test_future_demand_does_not_stop_early(fake):
    m=manager(sim_start_hour=6,sim_end_hour=8)
    for t in range(7200): m.maintain(t)
    assert m.spawn_counts['school_morning'] > 0
    assert all(r['depart'] >= 3600 for r in m.records.values())


def test_pending_taxi_counts_toward_fleet_cap(monkeypatch):
    import module1_simulation.taxi_manager as taxi_manager
    vehicle=SimpleNamespace(getIDList=lambda:[],getLoadedIDList=lambda:['pending'],getTypeID=lambda vid:'taxi_type')
    api=SimpleNamespace(vehicle=vehicle,exceptions=traci.exceptions)
    monkeypatch.setattr(taxi_manager,'traci',api)
    m=taxi_manager.TaxiFleetManager(1,['a'],['a','b'])
    m._spawn_new_taxi=Mock()
    m.maintain(1)
    m._spawn_new_taxi.assert_not_called()
    assert m.max_loaded_taxis==1


def test_forecast_protected_taxi_is_not_patrolled(monkeypatch):
    import module1_simulation.taxi_manager as taxi_manager
    vehicle=SimpleNamespace(getIDList=lambda:['taxi'],getLoadedIDList=lambda:['taxi'],getTypeID=lambda vid:'taxi_type')
    monkeypatch.setattr(taxi_manager,'traci',SimpleNamespace(vehicle=vehicle,exceptions=traci.exceptions))
    m=taxi_manager.TaxiFleetManager(1,['a'],['a','b'])
    m.protected_ids={'taxi'}
    m._pick_target_edge=Mock(side_effect=AssertionError('protected taxi retargeted'))
    m.maintain(1)


def test_return_pool_requires_actual_arrival(fake):
    m=manager(sim_start_hour=12,sim_end_hour=14)
    m.absorbed['company']=100
    m.maintain(0)
    assert m.spawn_counts['lunch_release']==8
    assert m.absorbed['company']==92
    m.maintain(3600)
    assert m.spawn_counts['lunch_return']==0


def test_afternoon_evening_absorption_link(fake):
    m=manager(sim_start_hour=6,sim_end_hour=24)
    m.absorbed.update(school=1000,company=200)
    for t in range(10*3600,18*3600): m.maintain(t)
    assert m.spawn_counts['school_afternoon']==10
    assert m.spawn_counts['evening']>0
    assert all(16 <= 6+r['depart']/3600 < 17 for r in m.records.values() if r['demand_type']=='school_afternoon')


def test_missing_explicit_config_fails(monkeypatch,tmp_path):
    monkeypatch.setenv('MOBILITY_CONFIG',str(tmp_path/'missing.json'))
    with pytest.raises(FileNotFoundError): load_config()


def test_experiment_matrix_has_70_unique_cases():
    from tools.validate_runtime import scenarios
    rows=list(scenarios({}))
    assert len(rows)==70 and len({n for n,_ in rows})==70
    assert sum(c['passenger_wait_timeout']==500 for _,c in rows)==30


def test_forecast_target_allocation():
    """예측 기반 재배치의 정수 택시 배분 검증: 빈 택시 수와 비례 배분 일치 확인."""
    from module4_dispatch.forecast_dispatcher import allocate_targets
    import numpy as np
    demand = np.array([10.0, 20.0, 30.0])
    multipliers = np.ones(3)
    targets = allocate_targets(demand, multipliers, fleet_size=6)
    assert targets.sum() == 6
    assert targets[0] <= targets[1] <= targets[2]


def test_forecast_repositioner_filters_idle(monkeypatch):
    """ForecastRepositioner는 탑승(occupied) 및 예약(reserved) 택시를 제외하고 오직 idle 택시만 대상으로 한다."""
    from module4_dispatch.forecast_repositioner import ForecastRepositioner
    # mock traci vehicle fleet
    mock_vehicle = SimpleNamespace(
        getTaxiFleet=lambda flag: ["taxi_idle_1", "taxi_idle_2"] if flag == 0 else ["taxi_idle_1", "taxi_idle_2", "taxi_occupied", "taxi_reserved"],
        getRoadID=lambda vid: "edge_0",
        getRoute=lambda vid: ["edge_0", "edge_1"],
        getRouteIndex=lambda vid: 0,
        getLanePosition=lambda vid: 10.0,
    )
    import module4_dispatch.forecast_repositioner as frep_mod
    monkeypatch.setattr(frep_mod, "traci", SimpleNamespace(vehicle=mock_vehicle, exceptions=traci.exceptions))
    rep = ForecastRepositioner.__new__(ForecastRepositioner)
    rep.tick_sec = 300
    rep.last_tick = -1
    rep.bucket = SimpleNamespace(total_seconds=lambda: 0)
    rep.horizon = 3
    rep.predict = lambda now: np.ones((2, 6))
    rep.stats = Counter()
    rep.edge_cells = {"edge_0": "cell_0", "edge_1": "cell_1"}
    rep.cell_edges = {"cell_0": [("edge_0", 1)], "cell_1": [("edge_1", 1)]}
    rep.fraction = 0.5
    rep.min_idle_ratio = 0.0
    rep.power = 0.6667
    rep.travel_time = None
    rep.max_move_sec = 180
    rep._rng = random.Random(42)

    # maintain 호출 시 idle_taxis가 주어지지 않으면 getTaxiFleet(0)으로만 조회
    # (내부 loc 딕셔너리에 idle 택시 2대만 들어가는지 확인)
    rep.maintain(300)
    assert rep.stats["ticks"] == 1


def test_read_user_config_explicit_missing_fails(tmp_path, monkeypatch):
    """명시적으로 지정한 설정 파일이나 MOBILITY_CONFIG가 존재하지 않으면 FileNotFoundError 발생 확인."""
    from config_loader import read_user_config
    missing = str(tmp_path / "does_not_exist.json")
    with pytest.raises(FileNotFoundError):
        read_user_config(missing)

    monkeypatch.setenv("MOBILITY_CONFIG", missing)
    with pytest.raises(FileNotFoundError):
        read_user_config()


def test_ab_run_config_isolation(tmp_path, monkeypatch):
    """A/B 실행용 use_run_config가 원본 config.json을 건드리지 않고 run_configs에 분리 저장되는지 확인."""
    from config_loader import use_run_config, active_config_path
    overrides = {"num_taxis": 99, "taxi_strategy": "prepositioned"}
    run_path = use_run_config(overrides, label="test_ab")
    try:
        assert os.path.exists(run_path)
        assert active_config_path() == run_path
        with open(run_path, "r", encoding="utf-8") as f:
            saved = json.load(f)
        assert saved["num_taxis"] == 99
        assert saved["taxi_strategy"] == "prepositioned"
    finally:
        if os.path.exists(run_path):
            os.remove(run_path)


def test_taxi_manager_respawn_and_stuck_reroute(monkeypatch):
    """TaxiFleetManager가 목표 대수 부족 시 재생성하고, stuck 차량을 감지하여 재경로하는지 검증."""
    import module1_simulation.taxi_manager as tm
    spawned = []
    retargeted = []

    mock_vehicle = SimpleNamespace(
        getIDList=lambda: ["taxi_0"],
        getLoadedIDList=lambda: ["taxi_0"],
        getTypeID=lambda vid: "taxi_type",
        getRoadID=lambda vid: "edge_stuck",
        getRoute=lambda vid: ["edge_stuck", "edge_next"],
        getRouteIndex=lambda vid: 0,
        getPersonIDList=lambda vid: [],
        getStops=lambda vid, flag: [],
        changeTarget=lambda vid, edge: retargeted.append((vid, edge)),
    )
    mock_sim = SimpleNamespace(
        findRoute=lambda cur, target, vType=None: SimpleNamespace(edges=[cur, target]),
    )
    api = SimpleNamespace(vehicle=mock_vehicle, simulation=mock_sim, exceptions=traci.exceptions)
    monkeypatch.setattr(tm, "traci", api)

    # 목표 3대, 현재 1대 -> shortage = 2대 재생성 시도
    fleet = tm.TaxiFleetManager(target_count=3, boundary_edges=["edge_0"], all_edges=["edge_stuck", "edge_next", "edge_target"])
    fleet._spawn_new_taxi = Mock(side_effect=lambda: spawned.append("taxi_new"))
    fleet.stuck_reroute_sec = 60.0
    fleet.avoid_traps = False

    # 1. 초기 유지 (t=0)
    fleet.maintain(0.0)
    assert len(spawned) == 2  # 부족분 2대 생성 호출

    # 2. stuck 시뮬레이션: 같은 도로에 70초 머무름 -> stuck reroute 발생
    fleet._maintain_one("taxi_0", 0.0)
    fleet._maintain_one("taxi_0", 70.0)
    # _is_stuck이 True가 되면서 changeTarget 호출됨
    assert len(retargeted) >= 1
    assert retargeted[0][0] == "taxi_0"


def test_taxi_manager_extend_dispatch_and_stuck_rider(monkeypatch):
    """배차 중 경로 연장(_extend_dispatch_route) 및 승객 탑승 중 stuck 재경로(_maintain_stuck_rider) 동작 검증."""
    import module1_simulation.taxi_manager as tm
    retargeted = []

    stop_mock = SimpleNamespace(lane="edge_stop_0")
    mock_vehicle = SimpleNamespace(
        getRoadID=lambda vid: "edge_cur",
        getRoute=lambda vid: ["edge_cur"],
        getRouteIndex=lambda vid: 0,
        changeTarget=lambda vid, edge: retargeted.append((vid, edge)),
    )
    mock_sim = SimpleNamespace(
        findRoute=lambda cur, stop, vType=None: SimpleNamespace(edges=[cur, stop]),
    )
    monkeypatch.setattr(tm, "traci", SimpleNamespace(vehicle=mock_vehicle, simulation=mock_sim, exceptions=traci.exceptions))

    fleet = tm.TaxiFleetManager(target_count=1, boundary_edges=["edge_cur"], all_edges=["edge_cur", "edge_stop"])
    fleet.remaining_edges_threshold = 2
    fleet.extend_dispatch_route = True

    # 1. _extend_dispatch_route: 배차 stop edge가 경로 끝에 없을 때 연장
    res = {}
    fleet._extend_dispatch_route("taxi_dispatch", res, stops=[stop_mock])
    assert ("taxi_dispatch", "edge_stop") in retargeted
    assert fleet.spawn_stats["dispatch_route_extended"] == 1

    # 2. _maintain_stuck_rider: 승객 탑승 중 stuck 시 stop edge로 재경로
    retargeted.clear()
    fleet.reroute_stuck_riders = True
    fleet._pos_track["taxi_rider"] = ("edge_cur", 0.0)
    mock_vehicle.getStops = lambda vid, flag: [stop_mock]
    fleet._maintain_stuck_rider("taxi_rider", now_seconds=300.0, res={})
    assert ("taxi_rider", "edge_stop") in retargeted
    assert fleet.spawn_stats["passenger_stuck_rerouted"] == 1


def test_rl_maintainer_vecnorm_detection():
    """RLRepositionMaintainer가 모델의 VecNormalize 학습 여부를 자동 감지하는지 검증."""
    from module4_dispatch.rl_reposition_maintainer import RLRepositionMaintainer
    # Main 모델 경로 확인
    main_model_path = os.path.join(ROOT_DIR, "module4_dispatch", "rl_reposition_model.zip")
    if os.path.exists(main_model_path):
        meta = {"zones": {c: [f"e_{c}"] for c in ["school", "residential", "company", "restaurant", "subway_entrance", "bus_stop"]}}
        maintainer = RLRepositionMaintainer(main_model_path, meta)
        # Main 모델은 51,200 steps로 정규화 없이 학습됨 -> _obs_mean이 None이어야 함
        assert maintainer._obs_mean is None
