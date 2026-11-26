import csv

with open('results/ab_results/ab_experiment_summary.csv', 'r', encoding='utf-8-sig') as f:
    reader = csv.DictReader(f)
    rows = list(reader)

d04_experiments = {}
for r in rows:
    req = r['요청ID']
    if req not in d04_experiments:
        d04_experiments[req] = []
    d04_experiments[req].append(r)

print('=== d04가 포함된 실제 완료 실험 세트 요약 ===\n')
count = 0
for req, items in d04_experiments.items():
    has_d04 = any('d04' in i['알고리즘'] for i in items)
    if has_d04:
        count += 1
        t0 = items[0]
        print(f"[{count}] 요청 ID: {req} | 시각: {t0['실행시각']} | 날짜: {t0['시뮬날짜']} | 승객수: {t0['전체승객(명)']}명")
        for i in items:
            algo_short = i['알고리즘'].replace('taxi_strategy=rl_prepositioned_', '').replace('taxi_dispatch_algorithm=', '').replace('_holiday_mode=auto', '')
            cname = i['콤보'] if i['콤보'] else 'combo?'
            print(f"     {i['순위']:>4} | {cname:>7} | {algo_short:<25} | 체감대기: {i['체감대기시간(초)']:>6}초 | 소멸: {i['소멸승객(명)']:>4}명")
        print('-'*75)
