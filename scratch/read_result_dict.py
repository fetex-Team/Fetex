with open('measure_wait_time.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

for i in range(414, 460):
    safe = lines[i].rstrip().encode('ascii', 'replace').decode('ascii')
    print(f'{i+1}: {safe}')
