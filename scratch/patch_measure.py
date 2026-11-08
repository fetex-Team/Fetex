import os

path = 'measure_wait_time.py'
with open(path, 'r', encoding='utf-8') as f:
    lines = f.readlines()

updated = False
for i, l in enumerate(lines):
    if 'RL_MODEL_PATH = os.path.join(ROOT, "module4_dispatch", "rl_reposition_model.zip")' in l:
        lines[i] = 'RL_MODEL_PATH = os.environ.get("AI_RL_MODEL_PATH", os.path.join(ROOT, "module4_dispatch", "rl_reposition_model.zip"))  # 스냅샷 환경변수 우선\n'
        updated = True
        break

if updated:
    with open(path, 'w', encoding='utf-8') as f:
        f.writelines(lines)
    print('Updated measure_wait_time.py successfully!')
else:
    print('Target line not found!')
