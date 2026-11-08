with open('measure_wait_time.py', 'r', encoding='utf-8') as f:
    text = f.read()

import re
# find def run_and_measure
idx = text.find('def run_and_measure')
if idx != -1:
    sub = text[idx:idx+25000]
    # find all return statements in run_and_measure
    for m in re.finditer(r'return\s*\{', sub):
        ret_idx = m.start()
        print('--- RETURN STATEMENT ---')
        safe = sub[ret_idx:ret_idx+1200].encode('ascii', 'replace').decode('ascii')
        print(safe)
