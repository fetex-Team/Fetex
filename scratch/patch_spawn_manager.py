import os

path = 'passenger_spawn_manager.py'
with open(path, 'r', encoding='utf-8') as f:
    lines = f.readlines()

# 1. __init__ 에 self.call_by_pid = {}, self._real_model = None 추가
for i, l in enumerate(lines):
    if 'self.schedule_summary = None' in l:
        if 'self._real_model = None' not in lines[i+1] and 'self._real_model = None' not in lines[i+2]:
            lines.insert(i+1, '        self.call_by_pid = {}       # pid -> Call (실데이터 호출)\n        self._real_model = None\n')
        break

# 2. _init_real_schedule 에 self._real_model = model 저장 및 weather_history 연동
for i, l in enumerate(lines):
    if 'model = RealDemandModel(meta, CFG)' in l:
        if 'self._real_model = model' not in lines[i+1]:
            lines.insert(i+1, '        self._real_model = model\n')
        break

# 3. cell_mean_time_min 메서드 추가
for i, l in enumerate(lines):
    if 'def _maintain_real' in l:
        cell_method = '''    def cell_mean_time_min(self, cell: str) -> float:
        """TIMS 셀의 평균 통행시간(분). 모르면 전체 평균."""
        m = self._real_model
        if m is None:
            return 21.0
        if cell in m.cells:
            return m.cells[cell].mean_time_min
        act = list(m.active.values())
        return sum(c.mean_time_min for c in act) / max(len(act), 1)

'''
        if 'def cell_mean_time_min' not in lines[i-1] and 'def cell_mean_time_min' not in lines[i-2]:
            lines.insert(i, cell_method)
        break

# 4. _maintain_real 에 self.call_by_pid[pid] = call 추가
for i, l in enumerate(lines):
    if 'self.total_spawned += 1' in l:
        # Check if in _maintain_real
        if i > 250 and i < 320:
            if 'self.call_by_pid[pid] = call' not in lines[i+1] and 'self.call_by_pid[pid] = call' not in lines[i+2]:
                lines.insert(i+1, '            self.call_by_pid[pid] = call\n')
            break

with open(path, 'w', encoding='utf-8') as f:
    f.writelines(lines)

print("Successfully patched passenger_spawn_manager.py!")
