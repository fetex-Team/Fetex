with open('config_gui.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

for i, l in enumerate(lines):
    if 'def open_ab_compare_dialog' in l:
        for j in range(i, min(len(lines), i+120)):
            safe = lines[j].rstrip().encode('ascii', 'replace').decode('ascii')
            print(f'{j+1}: {safe}')
        break
