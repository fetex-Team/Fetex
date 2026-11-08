with open('config_gui.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

for j in range(465, 485):
    safe = lines[j].rstrip().encode('ascii', 'replace').decode('ascii')
    print(f'{j+1}: {safe}')
