with open('multi_factor_compare.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

idx_loop_start = -1
idx_loop_end = -1
for i, l in enumerate(lines):
    if '# 2. 실행 중인 프로세스 완료 여부 점검' in l or '# 2. ' in l and '점검' in l:
        idx_loop_start = i
    if 'for idx in just_finished:' in l:
        idx_loop_end = i
        break

print('Loop block lines:', idx_loop_start, 'to', idx_loop_end)

idx_comp_start = -1
idx_comp_end = -1
for i, l in enumerate(lines):
    if '===== [최종 비교 - 병렬 실행' in l:
        idx_comp_start = i - 1
    if 'try:' in l and 'save_ranking_text' in lines[i+1]:
        idx_comp_end = i
        break

print('Compare block lines:', idx_comp_start, 'to', idx_comp_end)
