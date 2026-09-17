"""SUMO 런타임 회귀 검사: 외부 API 없이 모의 TraCI 사용."""
import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

os.environ.setdefault('MOBILITY_CONFIG', str(Path(__file__).resolve().parents[1] / 'presets/runtime_minimal.json'))
import pytest
import traci
from config_loader import DEFAULT_CONFIG, load_config
import passenger_spawn_manager as spawn_module
import passenger_manager as timeout_module
from passenger_spawn_manager import PassengerSpawnManager
from passenger_manager import PassengerTimeoutManager
from measure_wait_time import summarize
from runtime_validation import schedule_status

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
    import taxi_manager
    vehicle=SimpleNamespace(getIDList=lambda:[],getLoadedIDList=lambda:['pending'],getTypeID=lambda vid:'taxi_type')
    api=SimpleNamespace(vehicle=vehicle,exceptions=traci.exceptions)
    monkeypatch.setattr(taxi_manager,'traci',api)
    m=taxi_manager.TaxiFleetManager(1,['a'],['a','b'])
    m._spawn_new_taxi=Mock()
    m.maintain(1)
    m._spawn_new_taxi.assert_not_called()
    assert m.max_loaded_taxis==1


def test_forecast_protected_taxi_is_not_patrolled(monkeypatch):
    import taxi_manager
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


def test_forecast_excludes_reserved_and_occupied(monkeypatch):
    import numpy as np
    import pandas as pd
    import module4_dispatch.forecast_dispatcher as mod
    f=mod.ForecastDispatcher.__new__(mod.ForecastDispatcher)
    f.last_tick=-1; f.cells=['a','b']; f.records=[]; f.moves=[]; f.committed={}; f.enabled=True
    f.meta={'edge_cells':{'a0':'a','b0':'b'}}; f.cell_edges={'a':['a0'],'b':['b0']}
    f.predict=lambda now: np.array([[0]*6,[1]*6])
    f.engine=SimpleNamespace(apply_surge_pricing=lambda frame:frame.assign(surge_multiplier=1))
    changed=[]
    vehicle=SimpleNamespace(getTaxiFleet=lambda flag:['idle','reserved','occupied'],
        getPersonIDList=lambda vid:['p'] if vid=='occupied' else [],
        getStops=lambda vid,flag:['stop'] if vid=='reserved' else [],
        getRoadID=lambda vid:'a0',getTypeID=lambda vid:'taxi_type',
        changeTarget=lambda vid,edge: changed.append((vid,edge)))
    monkeypatch.setattr(mod,'traci',SimpleNamespace(vehicle=vehicle,
        simulation=SimpleNamespace(findRoute=lambda *a,**k:SimpleNamespace(edges=['a0','b0'],travelTime=10))))
    monkeypatch.setitem(mod.CFG,'reposition_fraction',1)
    f.maintain(300); f.maintain(301)
    assert changed==[('idle','b0')]
    assert len(f.records)==2 and len(f.moves)==1
