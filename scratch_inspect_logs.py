# -*- coding: utf-8 -*-
import glob, os, pandas as pd

logs = sorted(glob.glob('data/sim_logs/*.csv'), key=os.path.getmtime, reverse=True)
for log in logs[:2]:
    print('========================================')
    print('파일명:', os.path.basename(log))
    df = pd.read_csv(log)
    print('총 행 수:', len(df))
    nan_count = df['dest_category'].isna().sum()
    print('dest_category 결측치 수:', nan_count, f'({nan_count / len(df) * 100:.2f}%)')
    print('\ndest_category 카테고리별 빈도:')
    print(df['dest_category'].value_counts(dropna=False))
    print('\nCSV 상위 15행 (origin_zone vs dest_category):')
    print(df[['person_id', 'origin_zone', 'dest_category', 'status']].head(15).to_string())
    print()
