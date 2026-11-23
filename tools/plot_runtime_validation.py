"""저장된 CSV만 읽어 보고서용 정적 PNG를 재생성한다."""
import argparse
import os
import tempfile
from pathlib import Path
import numpy as np
import pandas as pd
os.environ.setdefault('MPLCONFIGDIR', str(Path(tempfile.gettempdir()) / 'fetex-matplotlib'))
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

COLORS={'patrol':'#2463a6','prepositioned':'#c47824'}


def style(ax):
    ax.spines[['top','right']].set_visible(False)
    ax.grid(axis='y',color='#e5e7eb',linewidth=.7)
    ax.set_axisbelow(True)


def plot(out):
    plt.rcParams.update({'font.family':'DejaVu Sans','font.size':10,'axes.titleweight':'bold','figure.facecolor':'white'})
    destination=out/'figures'; destination.mkdir(exist_ok=True)
    data=pd.read_csv(out/'run_metrics.csv')
    data=data[data.completed]
    if data.empty: return
    # 시드별 점과 표준편차를 구분하여 신뢰구간으로 오해하지 않게 한다.
    subset=data[data.timeout==500]
    fig,axes=plt.subplots(1,2,figsize=(12,4.6),layout='constrained')
    for ax,metric,title,unit in zip(axes,['avg_wait_sec','timeout_rate'],['Wait among picked-up passengers','Actual timeout removal'],['Seconds','Percent of generated passengers']):
        for j,(strategy,color) in enumerate(COLORS.items()):
            xs=[]; means=[]; deviations=[]
            for i,(start,end) in enumerate([(7,10),(9,11),(6,24)]):
                values=subset[(subset.strategy==strategy)&(subset.start_hour==start)&(subset.end_hour==end)][metric].dropna()
                if metric.endswith('_rate'): values=values*100
                if values.empty: continue
                x=i+(j-.5)*.23
                xs.append(x); means.append(values.mean()); deviations.append(values.std(ddof=1) if len(values)>1 else 0)
                ax.scatter([x]*len(values),values,s=18,color=color,alpha=.45)
            ax.errorbar(xs,means,yerr=deviations,fmt='o' if j==0 else 's',color=color,capsize=4,label=strategy)
        ax.set_xticks([0,1,2],['07–10','09–11','06–24']); ax.set_xlabel('Scenario hours (synthetic demand)')
        ax.set_ylabel(unit); ax.set_title(title); ax.set_ylim(bottom=0); style(ax)
    axes[0].legend(frameon=False)
    fig.suptitle('Strategy comparison | timeout 500 s | dots: seeds, bars: ±1 sample SD',fontsize=12)
    fig.savefig(destination/'strategy_comparison.png',dpi=160); plt.close(fig)
    subset=data[(data.start_hour==9)&(data.end_hour==11)]
    fig,axes=plt.subplots(1,2,figsize=(12,4.6),layout='constrained')
    for strategy,color in COLORS.items():
        group=subset[subset.strategy==strategy].groupby('timeout')
        for ax,metric in zip(axes,['timeout_rate','avg_wait_sec']):
            values=group[metric].mean().sort_index(); sd=group[metric].std().fillna(0)
            factor=100 if metric.endswith('_rate') else 1
            ax.errorbar(values.index,values*factor,yerr=sd*factor,color=color,
                        marker='o' if strategy=='patrol' else 's',linestyle='-' if strategy=='patrol' else '--',capsize=3,label=strategy)
    for ax,title,label in zip(axes,['Timeout sensitivity','Wait among picked-up passengers'],['Actual timeout removal (%)','Mean wait (s)']):
        ax.set_title(title); ax.set_ylabel(label); ax.set_xlabel('Timeout threshold (s)')
        ax.set_xticks([200,350,500,700,900]); ax.set_ylim(bottom=0); style(ax)
    axes[0].legend(frameon=False)
    fig.suptitle('09–11 | same five seeds per threshold | bars: ±1 sample SD',fontsize=12)
    fig.savefig(destination/'timeout_sensitivity.png',dpi=160); plt.close(fig)
    # 시간×H3 행렬은 지역 분포이며 지도라고 표시하지 않는다.
    paths=[out/'runs'/f'strategy_09_11_{s}_42'/'fleet_distribution.csv' for s in COLORS]
    if all(p.exists() for p in paths):
        frames=[pd.read_csv(p) for p in paths]
        cells=sorted(set().union(*(set(f.h3_index) for f in frames)))
        panels=[f.pivot(index='h3_index',columns='time_sec',values='idle_taxis').reindex(cells).fillna(0) for f in frames]
        vmax=max(1,max(float(p.to_numpy().max()) for p in panels))
        fig,axes=plt.subplots(1,2,figsize=(12,5.3),layout='constrained')
        for ax,(strategy,_),panel in zip(axes,COLORS.items(),panels):
            im=ax.imshow(panel.to_numpy(),aspect='auto',vmin=0,vmax=vmax,cmap='Blues',interpolation='nearest')
            indices=np.linspace(0,len(panel.columns)-1,min(5,len(panel.columns))).astype(int)
            labels=[f'{9+int(panel.columns[i])//3600:02}:{(int(panel.columns[i])%3600)//60:02}' for i in indices]
            ax.set_xticks(indices,labels); ax.set_yticks(range(len(cells)),cells)
            ax.set_xlabel('Simulation time'); ax.set_title(strategy)
        axes[0].set_ylabel('H3 cell (unmapped includes internal junction edges)')
        fig.colorbar(im,ax=axes,label='Idle taxis',shrink=.8)
        fig.suptitle('Idle fleet distribution | 09–11 | seed 42 | shared color scale')
        fig.savefig(destination/'fleet_distribution.png',dpi=160); plt.close(fig)
    pop=out/'population_sweep.csv'
    if pop.exists():
        p=pd.read_csv(pop); p=p[(p.parameter=='num_passengers')&(p.kind=='residential_gacha')]
        fig,ax=plt.subplots(figsize=(7,4.4),layout='constrained')
        ax.plot(p.value,p.expected,'--',color='#4b5563',label='Expected: population × 0.1')
        ax.errorbar(p.value,p.observed_mean,yerr=p.stddev,fmt='o',color=COLORS['patrol'],capsize=4,label='Observed mean ±1 SD (30 seeds)')
        ax.set(xlabel='Independent lottery population',ylabel='Successfully generated passengers',title='Independent population sweep',ylim=(0,None))
        style(ax); ax.legend(frameon=False)
        fig.savefig(destination/'population_sweep.png',dpi=160); plt.close(fig)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__); p.add_argument('--output-dir',type=Path,default=Path('results/runtime_validation'))
    plot(p.parse_args().output_dir.resolve())
