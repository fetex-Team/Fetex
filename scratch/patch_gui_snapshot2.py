path = 'config_gui.py'
with open(path, 'r', encoding='utf-8') as f:
    lines = f.readlines()

for i, l in enumerate(lines):
    if 'open_data_quality_checker' in l:
        # find the end of this pack statement
        for j in range(i, i+5):
            if ')' in lines[j]:
                indent = '        '
                new_btn = [
                    f'{indent}ttk.Button(quality_bar, text="📦 현재 모델 스냅샷 저장 (날짜+택시계수)", command=self.snapshot_current_models).pack(\n',
                    f'{indent}    side="left", fill="x", expand=True, padx=3\n',
                    f'{indent})\n'
                ]
                lines = lines[:j+1] + new_btn + lines[j+1:]
                break
        break

with open(path, 'w', encoding='utf-8') as f:
    f.writelines(lines)
print('Successfully inserted button!')
