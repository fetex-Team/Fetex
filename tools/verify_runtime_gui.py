"""최소 구성의 실제 SUMO GUI 화면과 객체 수를 검증한다."""
import json
import os
from pathlib import Path
import sys
import xml.etree.ElementTree as ET
from collections import Counter

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
os.environ['MOBILITY_CONFIG']=str(ROOT/'presets/runtime_minimal.json')
os.environ.pop('MOBILITY_BACKEND',None)
import traci
from fetex.runtime.measure_wait_time import run_and_measure


class Capture:
    def __init__(self,out): self.out=out; self.images=[]
    def capture(self,now,*args):
        if now in (60,180,300):
            path=self.out/f'sumo_{int(now)}s.png'
            traci.gui.screenshot('View #0',str(path),width=1280,height=960)
            self.images.append(path.name)
    def finish(self,result):
        (self.out/'gui_validation.json').write_text(json.dumps({'images':self.images,'completed':result['completed_interval'],
            'passengers':result['n_total_passengers'],'picked_up':result['n_measured']},indent=2),encoding='utf-8')


def main():
    directory=ROOT/'results/runtime_validation/minimal'
    entities=ET.parse(directory/'sumo/entities.rou.xml').getroot()
    counts=Counter(v.get('type') for v in entities.findall('trip'))
    expected={'taxi_type':3,'normal_type_0':5,'normal_type_1':5,'normal_type_2':5,'normal_type_3':5,'auto_type':1,'obstacle_type':2}
    assert counts==expected,counts
    assert len(entities.findall('person'))==5
    net=ET.parse(directory/'sumo/grid.net.xml').getroot()
    assert len([j for j in net.findall('junction') if not j.get('id').startswith(':')])==16
    out=directory/'gui'; out.mkdir(exist_ok=True)
    (directory/'composition.json').write_text(json.dumps(dict(vehicles=counts,passengers=5,blocks=[3,3]),indent=2))
    result=run_and_measure('sumo-gui',meta_path=directory/'sumo/runtime_meta.json',output_dir=out,observer=Capture(out))
    assert result['n_total_passengers']==result['n_measured']==5 and result['completed_interval']


if __name__=='__main__': main()
