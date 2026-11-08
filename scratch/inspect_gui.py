with open('config_gui.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

for i, l in enumerate(lines):
    if 'def open_ab_compare_dialog' in l:
        print('open_ab_compare_dialog starts at:', i+1)
    if 'bf = ttk.Frame' in l:
        print('bf starts at:', i+1)
        for j in range(i, min(len(lines), i+15)):
            safe = lines[j].strip().encode('ascii', 'replace').decode('ascii')
            print(f'  {j+1}: {safe}')
