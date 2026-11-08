with open('config_gui.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

# 470 ~ 480 주변을 깔끔하게 재구성
idx_q = -1
for i, l in enumerate(lines):
    if 'quality_bar = ttk.Frame(bottom_frame)' in l:
        idx_q = i
        break

assert idx_q != -1

# idx_q부터 self.status 직전까지 찾기
idx_status = -1
for i in range(idx_q, idx_q + 20):
    if 'self.status = ttk.Label' in lines[i]:
        idx_status = i
        break

assert idx_status != -1

clean_block = [
    '        quality_bar = ttk.Frame(bottom_frame)\n',
    '        quality_bar.pack(fill="x", padx=12, pady=(0, 4))\n',
    '        ttk.Button(quality_bar, text="📊 실측 수요 데이터 품질 검사", command=self.open_data_quality_checker).pack(\n',
    '            side="left", fill="x", expand=True, padx=3\n',
    '        )\n',
    '        ttk.Button(quality_bar, text="📦 현재 모델 스냅샷 저장 (날짜+택시계수)", command=self.snapshot_current_models).pack(\n',
    '            side="left", fill="x", expand=True, padx=3\n',
    '        )\n',
    '\n',
]

lines = lines[:idx_q] + clean_block + lines[idx_status:]

with open('config_gui.py', 'w', encoding='utf-8') as f:
    f.writelines(lines)

print("Fixed quality_bar button syntax!")
