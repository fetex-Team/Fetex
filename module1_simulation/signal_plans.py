"""
실제 신호 운영계획(경찰청)을 시뮬레이션 신호등에 시간대별로 적용한다.

    config "signal_plans": "real"(기본) | "guess"(지도 변환기가 만든 기본 신호, 예전 동작)
    자료: data/external/signal_plans_gangnam.json (tools/build_signal_plans.py)

적용 방법 (교차로마다, 계획 구간이 바뀔 때마다)
1. 날짜의 요일(특수일이면 특수일 계획)로 계획 번호를 고르고, 그 계획에서 현재 시각이 속한 구간(주기·옵셋·현시 시간)을 찾는다.
2. 지도 기본 신호의 단계(녹색 1개 + 뒤따르는 황색·전적색)를 경찰청 현시에 대응시킨다.
   경찰청 자료에는 현시별 방향이 없어서, **긴 현시일수록 녹색 신호를 받는 차로 연결이 많은 단계**에 순위대로 맞춘다
   (큰 도로 직진이 가장 길게 배정되는 일반 원칙). 단계의 녹색 시간 = 현시 시간 − 그 단계의 황색·전적색 시간.
   - 단계 수와 현시 수가 다르면: 단계가 1개(구역 경계에서 한 방향만 지도에 있는 경우)는 가장 긴 현시만큼 녹색, 나머지는 적색.
     그 외에는 기본 녹색 비율을 유지한 채 주기만 실제 주기로 맞춘다.
3. 옵셋: 자정 기준 경과 시간에서 옵셋을 빼고 주기로 나눈 나머지만큼 진행된 상태로 시작한다(인접 교차로 연동 유지).
"""
from __future__ import annotations

import datetime as dt
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_FILE = os.path.join(ROOT, "data", "external", "signal_plans_gangnam.json")
MIN_GREEN = 5


def _sec(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 3600 + int(m) * 60


def _stages(phases):
    """[(duration, state)] -> [[녹색 index, 전환 index...], ...]  (녹색 = G/g가 있는 단계)"""
    stages, cur = [], None
    for i, (_, st) in enumerate(phases):
        green = any(c in "Gg" for c in st) and "y" not in st
        if green:
            cur = [i]
            stages.append(cur)
        elif cur is not None:
            cur.append(i)
        elif stages:
            stages[-1].append(i)
        else:                                   # 앞쪽 전적색: 마지막 단계에 붙인다
            stages.append([i])
    if stages and not any(c in "Gg" for c in phases[stages[0][0]][1]):
        lead = stages.pop(0)
        if stages:
            stages[-1].extend(lead)
    return stages


def build_logic_phases(base_phases, plan_phases, cycle):
    """기본 신호 [(dur, state)] + 경찰청 현시 [초] -> 새 [(dur, state)] (합 = cycle)."""
    st = _stages(base_phases)
    if not st:
        return list(base_phases)
    trans = [sum(base_phases[i][0] for i in s[1:]) for s in st]
    width = [sum(c in "Gg" for c in base_phases[s[0]][1]) for s in st]
    green = [0.0] * len(st)
    out_extra = 0.0
    if len(st) == len(plan_phases):
        order_s = sorted(range(len(st)), key=lambda k: (-width[k], k))
        order_p = sorted(range(len(plan_phases)), key=lambda k: (-plan_phases[k], k))
        for ks, kp in zip(order_s, order_p):
            green[ks] = max(MIN_GREEN, plan_phases[kp] - trans[ks])
    elif len(st) == 1:
        green[0] = max(MIN_GREEN, max(plan_phases) - trans[0])
        out_extra = max(0.0, cycle - green[0] - trans[0])
    else:
        g0 = [base_phases[s[0]][0] for s in st]
        free = max(MIN_GREEN * len(st), cycle - sum(trans))
        green = [max(MIN_GREEN, free * g / sum(g0)) for g in g0]
    new = []
    for k, s in enumerate(st):
        new.append((float(round(green[k])), base_phases[s[0]][1]))
        new += [(float(base_phases[i][0]), base_phases[i][1]) for i in s[1:]]
    if out_extra > 0:
        new.append((float(round(out_extra)), "r" * len(base_phases[0][1])))
    diff = cycle - sum(d for d, _ in new)               # 반올림 오차는 가장 긴 녹색에서 맞춘다
    if abs(diff) >= 1:
        k = max(range(len(new)), key=lambda i: new[i][0] if any(c in "Gg" for c in new[i][1]) else -1)
        new[k] = (max(float(MIN_GREEN), new[k][0] + diff), new[k][1])
    return new


class SignalPlanController:
    def __init__(self, data: dict, run_date: dt.date, sim_start_hour: int, is_holiday: bool = False):
        self.data = data["intersections"]
        self.run_date = run_date
        self.t0 = sim_start_hour * 3600
        self.is_holiday = is_holiday
        self.base = {}           # tls -> [(dur, state)] 기본 프로그램
        self.active = {}         # tls -> (plan_no, start)
        self.next_check = 0.0
        self.log = []

    @classmethod
    def from_config(cls, cfg, run_date, sim_start_hour, is_holiday=False):
        if cfg.get("signal_plans", "real") != "real":
            return None
        path = cfg.get("signal_plans_file") or DEFAULT_FILE
        if not os.path.exists(path):
            print(f"[경고] 신호 계획 파일이 없어 지도 기본 신호를 씁니다: {path} (tools/build_signal_plans.py)")
            return None
        with open(path, encoding="utf-8") as f:
            return cls(json.load(f), run_date, sim_start_hour, is_holiday)

    def _plan_for(self, info, day: dt.date):
        for h in info.get("holiday_plan", []):
            if h["month"] == day.month and h["day"] == day.day and h["plan"] in info["plans"]:
                return h["plan"]
        # 공휴일(평일·토요일에 걸친 경우)은 일요일 계획. is_holiday는 '주말 또는 공휴일'이라 토요일엔 쓰지 않는다.
        from module1_simulation.speed_profile import day_type
        dy = 1 if day_type(day, self.is_holiday and day == self.run_date) == "sunday" else (day.isoweekday() % 7) + 1  # 1=일 … 7=토
        return info["weekday_plan"].get(str(dy))

    def _segment(self, info, abs_sec: float):
        """자정 기준 abs_sec에 해당하는 (계획 번호, 구간). 그날 첫 구간 전이면 전날 계획의 마지막 구간."""
        day = self.run_date + dt.timedelta(seconds=int(abs_sec) // 86400 * 86400)
        sec = abs_sec % 86400
        pn = self._plan_for(info, day)
        segs = [s for s in info["plans"].get(str(pn), []) if _sec(s["start"]) <= sec]
        if segs:
            return pn, segs[-1]
        prev = self._plan_for(info, day - dt.timedelta(days=1))
        segs = info["plans"].get(str(prev), [])
        return (prev, segs[-1]) if segs else (None, None)

    def maintain(self, now: float):
        if now < self.next_check:
            return
        import traci
        self.next_check = now + 60.0                 # 계획 구간은 분 단위로 바뀐다
        abs_sec = self.t0 + now
        for tls, info in self.data.items():
            if tls not in self.base:
                try:
                    logic = traci.trafficlight.getAllProgramLogics(tls)[0]
                except traci.exceptions.TraCIException:
                    continue
                self.base[tls] = [(p.duration, p.state) for p in logic.phases]
            pn, seg = self._segment(info, abs_sec)
            if seg is None or self.active.get(tls) == (pn, seg["start"]):
                continue
            phases = build_logic_phases(self.base[tls], seg["phases"], seg["cycle"])
            logic = traci.trafficlight.Logic("real", 0, 0, [traci.trafficlight.Phase(d, s) for d, s in phases])
            traci.trafficlight.setProgramLogic(tls, logic)
            traci.trafficlight.setProgram(tls, "real")
            pos = (abs_sec - seg["offset"]) % seg["cycle"]     # 옵셋 반영: 주기 안 현재 위치
            for i, (d, _) in enumerate(phases):
                if pos < d:
                    traci.trafficlight.setPhase(tls, i)
                    traci.trafficlight.setPhaseDuration(tls, d - pos)
                    break
                pos -= d
            self.active[tls] = (pn, seg["start"])
            self.log.append({"t": now, "tls": tls, "name": info["name"], "plan": pn, "start": seg["start"],
                             "cycle": seg["cycle"], "phases": [d for d, _ in phases]})

    def summary(self):
        return {"intersections": {v["name"]: k for k, v in self.data.items()}, "switches": len(self.log),
                "applied": self.log[:4]}
