"""실제 격자망 대표 사례 및 동일 시드 반복 재현 검사."""
import json
import os
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
os.environ.setdefault('MOBILITY_CONFIG',str(ROOT/'presets/runtime_minimal.json'))
os.environ.setdefault('MOBILITY_BACKEND','libsumo')
from fetex.runtime.measure_wait_time import isolated_run
from fetex.core.config import DEFAULT_CONFIG


def main():
    out=ROOT/'results/runtime_validation'
    base={**DEFAULT_CONFIG,'region':'강남역','use_real_map':False,'resolve_external_data':False,
          'grid_x':3,'grid_y':3,'grid_length':200,'num_taxis':3,'passenger_mode':'legacy',
          'passenger_seed':42,'taxi_dispatch_algorithm':'hungarian','scenario_date':'2026-09-18'}
    checks=[]
    for name,override in [('empty_before_school',{'sim_start_hour':6,'sim_end_hour':8,'num_passengers':0}),
                           ('population_1000',{'sim_start_hour':7,'sim_end_hour':10,'num_passengers':1000}),
                           ('population_20000',{'sim_start_hour':7,'sim_end_hour':10,'num_passengers':20000})]:
        r=isolated_run({**base,**override},out/'grid'/name)
        assert r['completed_interval'] and r['max_loaded_taxis']<=3 and r['n_other_loss']==0
        if name=='empty_before_school': assert r['spawn_counts']['school_morning']>0
        if name.startswith('population'):
            assert r['attempts']['residential_gacha']==override['num_passengers']
        checks.append(dict(case=name,passed=True,total=r['n_total_passengers'],generated=r['spawn_counts']))
    original=out/'runs/strategy_09_11_patrol_42/summary.json'
    if original.exists():
        a=json.loads(original.read_text(encoding='utf-8'))
        b=isolated_run(a['config'],out/'repeatability')
        keys=['waits','n_total_passengers','n_timeout_removed','n_pending_at_end','n_other_loss',
              'spawn_counts','attempts','demand_fingerprint','map_hash','max_loaded_taxis']
        assert all(a[k]==b[k] for k in keys)
        checks.append(dict(case='gangnam_same_seed_repeat',passed=True))
    (out/'integration_checks.json').write_text(json.dumps(checks,ensure_ascii=False,indent=2),encoding='utf-8')
    print(checks)


if __name__=='__main__': main()
