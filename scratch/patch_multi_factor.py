path = 'multi_factor_compare.py'
with open(path, 'r', encoding='utf-8') as f:
    lines = f.readlines()

updated = False
for i, l in enumerate(lines):
    if 'category_model = os.environ.get("AI_CATEGORY_MODEL_PATH")' in l:
        insertion = [
            '    rl_model = os.environ.get("AI_RL_MODEL_PATH")\n',
            '    if rl_model:\n',
            '        env["AI_RL_MODEL_PATH"] = rl_model\n',
        ]
        lines = lines[:i+3] + insertion + lines[i+3:]
        updated = True
        break

if updated:
    with open(path, 'w', encoding='utf-8') as f:
        f.writelines(lines)
    print('Updated multi_factor_compare.py successfully!')
else:
    print('Target line not found!')
