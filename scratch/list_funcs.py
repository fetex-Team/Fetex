with open('measure_wait_time.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

for i, l in enumerate(lines):
    if l.startswith('def '):
        print(f'{i+1}: {l.strip()}')
