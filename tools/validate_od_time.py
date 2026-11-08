"""
택시 기종점 통행시간(한국교통연구원, 기준월 202309)으로 디지털 트윈의 배차 이동시간 표를 검증한다.

    python tools/validate_od_time.py
입력: data/external/taxi_od/od_time_gangnam_2023Q3_extract.csv (강남역 9칸 관련 행, WEEK_CD 9 = 전체 요일)
비교: 같은 500m 칸 쌍의 실제 택시 대표 통행시간 vs 시뮬레이션 도로망 최단 이동시간(평일 9~23시 TOPIS 속도 배율 평균, 칸 안 도로 쌍의 중앙값)
출력: results/topis_speed/od_time_validation.csv
"""
import json, numpy as np, pandas as pd, sys, os
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, ROOT); os.chdir(ROOT)
import demand_model as dm
from module4_dispatch.network_cost import NetworkTravelTime, net_file_from_sumocfg
from module1_simulation.speed_profile import SpeedProfileController
meta=json.load(open('module1_simulation/sumo_config/runtime_meta.json'))
net=net_file_from_sumocfg('module1_simulation/sumo_config/simulation.sumocfg')
prof=json.load(open('data/external/speed_profile_gangnam.json'))
import datetime as dt
spc=SpeedProfileController(prof, net, dt.date(2026,10,14), 9, False)
cell_edges={}
for e,(la,lo) in meta['edge_latlng'].items():
    c=dm.grid_code_of(*dm.latlng_to_utmk(la,lo))
    if c: cell_edges.setdefault(dm.od_grid_code(c),[]).append(e)
t=pd.read_csv('data/external/taxi_od/od_time_gangnam_2023Q3_extract.csv')
cells=set(cell_edges)
pairs=t[t.DPTR_LTTC_ID.isin(cells)&t.ARVL_PYN_ID.isin(cells)&(t.WEEK_CD==9)]
rows=[]
tables={}
for h in range(9,23):
    sc=spc.scale_for('weekday',h); tables[h]=NetworkTravelTime(net,'taxi',speed_scale=sc)
for _,r in pairs.iterrows():
    A,B=cell_edges[r.DPTR_LTTC_ID],cell_edges[r.ARVL_PYN_ID]
    vals=[]
    for h,T in tables.items():
        ts=[T.time(a,T.length[T.index[a]]/2,b,T.length[T.index[b]]/2) for a in A for b in B if a in T.index and b in T.index]
        ts=[x for x in ts if x<1e8]
        if ts: vals.append(np.median(ts))
    rows.append((r.DPTR_LTTC_ID,r.ARVL_PYN_ID,r.REPRSNT_TRFC_TM,np.mean(vals)/60))
d=pd.DataFrame(rows,columns=['o','d','real_min','sim_net_min'])
d.to_csv('results/topis_speed/od_time_validation.csv',index=False)
print(len(d), 'real mean',d.real_min.mean().round(2),'sim mean',d.sim_net_min.mean().round(2),'ratio',(d.sim_net_min/d.real_min).median().round(2))
print('MAPE',(abs(d.sim_net_min-d.real_min)/d.real_min).mean().round(3),'corr',np.corrcoef(d.real_min,d.sim_net_min)[0,1].round(2))
