with open('module4_dispatch/forecast_repositioner.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

for i in range(40, 75):
    safe = lines[i].rstrip().encode('ascii', 'replace').decode('ascii')
    print(f'{i+1}: {safe}')
