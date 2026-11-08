with open('measure_wait_time.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

for i in range(79, 460):
    l = lines[i]
    if any(k in l for k in ['open(', '.to_csv', 'return', 'save', 'replay', 'log', 'export']):
        safe = l.strip().encode('ascii', 'replace').decode('ascii')
        print(f'{i+1}: {safe}')
