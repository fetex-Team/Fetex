with open('config_gui.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

for i in range(1398, 1465):
    if i < len(lines):
        safe = lines[i].rstrip().encode('ascii', 'replace').decode('ascii')
        print(f'{i+1}: {safe}')
