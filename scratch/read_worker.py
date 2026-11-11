with open('parallel_dispatch_worker.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

for i, l in enumerate(lines):
    safe = l.rstrip().encode('ascii', 'replace').decode('ascii')
    print(f'{i+1}: {safe}')
