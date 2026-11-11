with open('multi_factor_compare.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

for i in range(340, 365):
    safe = lines[i].rstrip().encode('ascii', 'replace').decode('ascii')
    print(f'{i+1}: {safe}')
