"""저장된 실행별 원시 결과와 요약의 일관성을 검증한다."""
import argparse
import csv
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from runtime_validation import source_fingerprint


def verify(out,expected=70):
    results=[]; hashes=set(); checked=0
    for path in sorted((out/'runs').glob('*/summary.json')):
        r=json.loads(path.read_text(encoding='utf-8'))
        assert r['completed_interval'],path
        assert r['source_hash']==source_fingerprint(),f'코드 버전 불일치: {path}'
        assert r['max_loaded_taxis']<=r['config']['num_taxis'],path
        assert r['n_total_passengers']==sum(r[k] for k in ['n_measured','n_timeout_removed','n_pending_at_end','n_other_loss'])
        assert r['n_other_loss']==0,path
        assert r['n_spawn_failed']==0,path
        hashes.add(r['map_hash'])
        raw=path.with_name('passenger_outcomes.csv')
        if raw.exists():
            with raw.open(encoding='utf-8') as f: rows=list(csv.DictReader(f))
            assert len(rows)==r['n_total_passengers'] and len({p['person_id'] for p in rows})==len(rows)
            waits=[float(p['pickup_sec'])-float(p['depart_sec']) for p in rows if p['status']=='picked_up']
            assert waits==r['waits'] and all(w>=0 for w in waits)
            assert sum(p['status']=='timeout' for p in rows)==r['n_timeout_removed']
            assert sum(p['status']=='pending' for p in rows)==r['n_pending_at_end']
            checked+=1
        results.append({'run':path.parent.name,'passed':True})
    assert len(results)==expected,(len(results),expected)
    assert len(hashes)==1,hashes
    with (out/'population_sweep.csv').open(encoding='utf-8') as f:
        population=list(csv.DictReader(f))
    assert len(population)==48 and all(r['passed']=='True' for r in population)
    minimum=json.loads((out/'minimal/summary.json').read_text(encoding='utf-8'))
    gui=json.loads((out/'minimal/gui/summary.json').read_text(encoding='utf-8'))
    assert minimum['n_total_passengers']==minimum['n_measured']==5
    assert minimum['waits']==gui['waits']
    payload={'runs_passed':len(results),'raw_outcomes_checked':checked,'population_checks':len(population),
             'single_map':True,'source_hash':source_fingerprint(),'gui_headless_parity':True,'runs':results}
    (out/'acceptance.json').write_text(json.dumps(payload,indent=2),encoding='utf-8')
    print(f'PASS: {len(results)} runs, {checked} raw ledgers, {len(population)} population checks, GUI parity')


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output-dir',type=Path,default=ROOT/'results/runtime_validation')
    p.add_argument('--expected',type=int,default=70)
    args=p.parse_args(); verify(args.output_dir,args.expected)
