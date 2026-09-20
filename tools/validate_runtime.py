"""시간대/인구 스윕 및 강남역 70회 검증. 실행별 로그와 실패 상태를 보존한다."""
import argparse
import csv
import hashlib
import json
import math
import os
from pathlib import Path
import statistics
import sys
import time
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
os.environ.setdefault('MOBILITY_CONFIG',str(ROOT/'presets/runtime_minimal.json'))


def save_csv(path, rows):
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('w',newline='',encoding='utf-8') as f:
        w=csv.DictWriter(f,fieldnames=list(rows[0]) if rows else ['status']); w.writeheader(); w.writerows(rows)


def mock_sweeps(out):
    """SUMO 삽입을 성공 처리하여 시간/확률 규칙만 독립 검증한다."""
    import fetex.runtime.passenger_spawn_manager as module
    from fetex.core.config import DEFAULT_CONFIG
    from fetex.validation.runtime import schedule_status
    original=module.traci
    module.traci=SimpleNamespace(person=SimpleNamespace(add=lambda *a,**k:None,appendDrivingStage=lambda *a,**k:None,
                                    getIDList=lambda:[],remove=lambda *a:None),
                                simulation=SimpleNamespace(getArrivedPersonIDList=lambda:[]),exceptions=original.exceptions)
    zones=dict(school=['s'],company=['c'],restaurant=['r'],residential=['h'],bus_stop=['b'])
    base={**DEFAULT_CONFIG,'sim_start_hour':7,'sim_end_hour':10,'num_passengers':10000,
          'restaurant_pop_base':0,'passenger_mode':'legacy'}
    variants=[(key,value) for key,values in [('num_passengers',[0,1000,5000,10000,20000]),
              ('school_pop_base',[0,200,400,800]),('company_pop_base',[0,50,100,200]),('company_edges',[1,5,10])] for value in values]
    rows=[]
    try:
        for key,value in variants:
            cfg={**base,**({key:value} if key!='company_edges' else {})}
            z={**zones,'company':[f'c{i}' for i in range(value if key=='company_edges' else 1)]}
            samples={k:[] for k in ['residential_gacha','school_morning','company_morning']}
            attempts={k:[] for k in samples}
            for seed in range(30):
                m=module.PassengerSpawnManager(z,7,10,seed=seed,config=cfg)
                for t in range(10800): m.maintain(t)
                for kind in samples:
                    samples[kind].append(m.spawn_counts[kind]); attempts[kind].append(m.attempt_counts[kind])
            # 실제 accumulator 시도 시각의 Bernoulli 확률을 합산한다.
            for kind in samples:
                expected=variance=acc=0.0
                for t in range(10800):
                    h=7+t/3600
                    if kind=='residential_gacha':
                        rate=cfg['num_passengers']/10800; p=cfg['residential_taxi_probability']
                    else:
                        school=kind=='school_morning'; a,b=(7,8) if school else (8,10)
                        if not a<=h<b: continue
                        rate=(cfg['school_pop_base'] if school else cfg['company_pop_base']*len(z['company']))/((b-a)*3600)
                        p=(h-a)/(b-a)
                    acc+=rate; n=math.floor(acc+1e-9); acc-=n
                    expected+=n*p; variance+=n*p*(1-p)
                mean=statistics.mean(samples[kind]); tolerance=max(1e-8,5*math.sqrt(variance/30))
                rows.append(dict(parameter=key,value=value,kind=kind,seeds=30,attempts_mean=statistics.mean(attempts[kind]),
                                 expected=expected,observed_mean=mean,stddev=statistics.stdev(samples[kind]),
                                 tolerance_5se=tolerance,passed=abs(mean-expected)<=tolerance))
            print(f'[population] {key}={value}',flush=True)
        save_csv(out/'population_sweep.csv',rows)
        schedules=[]
        from fetex.simulation.build_env import build_passenger_schedule
        for a,b in [(6,7),(7,8),(8,10),(9,11),(10,11),(18,20),(21,23),(6,24)]:
            cfg={**base,'sim_start_hour':a,'sim_end_hour':b}
            m=module.PassengerSpawnManager(zones,a,b,seed=42,config=cfg)
            # 귀환 수요는 회귀 테스트의 실제 도착 주입으로 별도 검증한다.
            for t in range((b-a)*3600): m.maintain(t)
            trips=build_passenger_schedule(zones,a,b,10000,seed=42)
            for r in schedule_status(cfg,zones,m):
                schedules.append({'start_hour':a,'end_hour':b,**r,'static_total':len(trips)})
        save_csv(out/'schedule_sweep.csv',schedules)
    finally:
        module.traci=original
    if not all(r['passed'] for r in rows): raise AssertionError('인구 스윕 통계 허용 범위 초과')


def scenarios(base):
    """500초 기준 결과를 재사용하여 중복 없는 70개 조합을 만든다."""
    for a,b in [(7,10),(9,11),(6,24)]:
        for seed in range(42,47):
            for strategy in ['patrol','prepositioned']:
                yield f'strategy_{a:02}_{b:02}_{strategy}_{seed}',{**base,'sim_start_hour':a,'sim_end_hour':b,'passenger_seed':seed,'taxi_strategy':strategy,'passenger_wait_timeout':500}
    for timeout in [200,350,700,900]:
        for seed in range(42,47):
            for strategy in ['patrol','prepositioned']:
                yield f'timeout_{timeout}_{strategy}_{seed}',{**base,'sim_start_hour':9,'sim_end_hour':11,'passenger_seed':seed,'taxi_strategy':strategy,'passenger_wait_timeout':timeout}


def experiments(out, limit=None):
    from fetex.runtime.measure_wait_time import isolated_run
    from fetex.validation.runtime import source_fingerprint
    base=json.loads((ROOT/'config.json').read_text(encoding='utf-8'))
    # 저장된 지도와 구역을 복제하여 온라인 지도 변경 영향을 배제한다.
    base.update(resolve_external_data=False,shared_map_dir=str(ROOT/'fetex/simulation/sumo_config'),
                passenger_mode='legacy',dynamic_passengers=True,scenario_date='2026-09-18',
                num_taxis=50,num_passengers=10000,school_pop_base=400,company_pop_base=100,
                taxi_dispatch_algorithm='hungarian',use_real_map=True)
    manifest=[]
    for i,(name,cfg) in enumerate(scenarios(base)):
        if (out / 'STOP').exists():
            print('[중지] 현재 실행까지 저장했습니다.', flush=True)
            break
        if limit is not None and i>=limit: break
        directory=out/'runs'/name
        summary=directory/'summary.json'
        existing=json.loads(summary.read_text(encoding='utf-8')) if summary.exists() else None
        try:
            # 같은 코드와 설정으로 완료된 결과만 재사용한다.
            if existing and existing.get('source_hash') == source_fingerprint() and existing.get('completed_interval') and all(existing['config'].get(k)==v for k,v in cfg.items()):
                result=existing
            else:
                print(f'[{i+1}/70] {name}',flush=True)
                result=isolated_run(cfg,directory)
            manifest.append(dict(run=name,status='complete' if result['completed_interval'] else 'incomplete',error=''))
        except Exception as exc:
            manifest.append(dict(run=name,status='failed',error=str(exc)))
            print(f'[실패] {name}: {exc}',flush=True)
        save_csv(out/'manifest.csv',manifest)
    report(out)


def report(out):
    """원시 요약으로부터 시드별/집계 표와 검증 보고서를 다시 생성한다."""
    rows=[]
    metrics=['avg_wait_sec','p90_wait_sec','pickup_rate','timeout_rate','pending_rate','n_total_passengers','n_reserved_pending','n_other_loss','teleports','wall_time_sec']
    for path in sorted((out/'runs').glob('*/summary.json')):
        r=json.loads(path.read_text(encoding='utf-8')); cfg=r['config']
        rows.append(dict(run=path.parent.name,start_hour=cfg['sim_start_hour'],end_hour=cfg['sim_end_hour'],
                         strategy=r['strategy'],seed=r['seed'],timeout=cfg['passenger_wait_timeout'],
                         completed=r['completed_interval'],**{k:r[k] for k in metrics}))
    save_csv(out/'run_metrics.csv',rows)
    groups={}
    for r in rows:
        if r['completed']:
            key=(r['start_hour'],r['end_hour'],r['strategy'],r['timeout'])
            groups.setdefault(key,[]).append(r)
    aggregates=[]
    for (a,b,strategy,timeout),items in sorted(groups.items()):
        result=dict(start_hour=a,end_hour=b,strategy=strategy,timeout=timeout,n=len(items))
        for k in metrics:
            vals=[r[k] for r in items if r[k] is not None]
            result[k+'_mean']=statistics.mean(vals) if vals else None
            result[k+'_std']=statistics.stdev(vals) if len(vals)>1 else 0 if vals else None
        aggregates.append(result)
    save_csv(out/'aggregates.csv',aggregates)
    paired=[]
    for r in rows:
        if r['strategy']!='patrol': continue
        other=next((b for b in rows if (b['start_hour'],b['end_hour'],b['timeout'],b['seed'],b['strategy']) ==
                    (r['start_hour'],r['end_hour'],r['timeout'],r['seed'],'prepositioned')),None)
        if other:
            paired.append(dict(start_hour=r['start_hour'],end_hour=r['end_hour'],timeout=r['timeout'],seed=r['seed'],
                          **{k+'_pre_minus_patrol':other[k]-r[k] if other[k] is not None and r[k] is not None else None for k in metrics[:5]}))
    save_csv(out/'paired_differences.csv',paired)
    changes=[]
    for r in rows:
        if (r['start_hour'],r['end_hour'])!=(9,11): continue
        baseline=next((b for b in rows if (b['start_hour'],b['end_hour'],b['strategy'],b['seed'],b['timeout'])==(9,11,r['strategy'],r['seed'],500)),None)
        if baseline:
            changes.append(dict(strategy=r['strategy'],seed=r['seed'],timeout=r['timeout'],
                       timeout_change_pp=100*(r['timeout_rate']-baseline['timeout_rate']) if r['timeout_rate'] is not None else None,
                       wait_change_sec=r['avg_wait_sec']-baseline['avg_wait_sec'] if r['avg_wait_sec'] is not None and baseline['avg_wait_sec'] is not None else None))
    save_csv(out/'timeout_sensitivity.csv',changes)
    evening=[]
    for path in sorted((out/'runs').glob('strategy_06_24_*/summary.json')):
        r=json.loads(path.read_text(encoding='utf-8'))
        evening.append(dict(strategy=r['strategy'],seed=r['seed'],**{k:r['evening'][k] for k in metrics[:6]}))
    save_csv(out/'evening_metrics.csv',evening)
    category_rows=[]
    for path in sorted((out/'runs').glob('*/summary.json')):
        r=json.loads(path.read_text(encoding='utf-8'))
        for kind,count in r['spawn_counts'].items():
            category_rows.append(dict(run=path.parent.name,strategy=r['strategy'],seed=r['seed'],kind=kind,generated=count))
    save_csv(out/'category_counts.csv',category_rows)
    lines=['# SUMO 런타임 실험 결과','',f'완료 실행: {sum(r["completed"] for r in rows)}/70. 실패/누락 실행은 평균에서 제외한다.',
           '', '설정: 강남역 고정 OSM 지도, 택시 50대, 학교 400(전체 모수), 회사 100(edge당 모수), 독립 가챠 10000, Hungarian 배차. 시드 42~46.',
           '', '금요일 날짜는 2026-09-18 합성 조건이며 요일 효과나 실측 급증을 재현한 결과가 아니다. prepositioned는 시간표 기반 휴리스틱이다.',
           '', '| 구간 | 전략 | 타임아웃(s) | 실행 수 | 탑승자 평균 대기(s) | 타임아웃률 | 종료 미탑승률 |',
           '|---|---|---:|---:|---:|---:|---:|']
    for r in aggregates:
        def fmt(k,pct=False):
            v=r[k+'_mean']; return 'N/A' if v is None else f'{v*100:.2f}%' if pct else f'{v:.2f}'
        lines.append(f'| {r["start_hour"]}~{r["end_hour"]} | {r["strategy"]} | {r["timeout"]} | {r["n"]} | {fmt("avg_wait_sec")} | {fmt("timeout_rate",True)} | {fmt("pending_rate",True)} |')
    lines += ['', '평균 대기는 탑승자만의 지표다. 타임아웃률·종료 미탑승률과 함께 해석한다. 임계값 초과 후 예약 보류는 실제 제거와 구분한다.',
              '', '흡수량 기반 후속 수요는 택시 전략에 따라 달라지므로 같은 시드라도 하루 전체 호출 목록이 동일하다고 가정하지 않는다.',
              '', '원시 결과: `runs/*/summary.json`, `calls.csv`, `passenger_outcomes.csv`, `fleet_distribution.csv`, `object_states.csv`. 집계: `aggregates.csv`, `paired_differences.csv`, `timeout_sensitivity.csv`, `evening_metrics.csv`.']
    if evening:
        lines += ['', '## 18~24시 호출 코호트', '', '| 전략 | 실행 수 | 탑승자 평균 대기(s) | 타임아웃률 |', '|---|---:|---:|---:|']
        for strategy in ('patrol','prepositioned'):
            items=[r for r in evening if r['strategy']==strategy]
            if items:
                lines.append(f"| {strategy} | {len(items)} | {statistics.mean(r['avg_wait_sec'] for r in items):.2f} | {100*statistics.mean(r['timeout_rate'] for r in items):.2f}% |")
    if rows:
        lines += ['', '## 해석의 제한', '',
                  f"SUMO 텔레포트 횟수는 실행별 {min(r['teleports'] for r in rows)}~{max(r['teleports'] for r in rows)}회다. 이는 차량의 정체/경로 복구 이벤트이며 실제 도로 주행과 다르다.",
                  f"종료 시 임계시간을 초과한 예약 보류 승객은 실행별 {min(r['n_reserved_pending'] for r in rows)}~{max(r['n_reserved_pending'] for r in rows)}명이다. 실제 제거율이 모든 장기 대기 비율을 뜻하지 않는다.",
                  '지역별 생성량은 `category_counts.csv`, 시드별 평균 차이는 `paired_differences.csv`, 500초 대비 변화는 `timeout_sensitivity.csv`를 참조한다.']
    (out/'RESULTS.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('mode',choices=['sweeps','experiments','report','all'])
    p.add_argument('--output-dir',type=Path,default=ROOT/'results/runtime_validation')
    p.add_argument('--limit',type=int)
    p.add_argument('--backend',choices=['traci','libsumo'],default='libsumo')
    args=p.parse_args(); out=args.output_dir.resolve(); out.mkdir(parents=True,exist_ok=True)
    os.environ['MOBILITY_BACKEND']=args.backend
    if args.mode in ('sweeps','all'): mock_sweeps(out)
    if args.mode in ('experiments','all'): experiments(out,args.limit)
    if args.mode=='report': report(out)


if __name__=='__main__': main()
