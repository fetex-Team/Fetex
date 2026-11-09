"""
시간대별 도로 속도 보정 — 시뮬레이션 도로 속도를 TOPIS 실측(서울시 택시 GPS 통행속도)에 맞춘다.

    config "speed_profile": "topis"(기본) | "free_flow"(제한속도 그대로, 예전 동작)
    자료: data/external/speed_profile_gangnam.json (tools/calibrate_speed.py)

왜 필요한가
- 시뮬레이션에는 일반 차량이 수십 대뿐이라 도로가 늘 비어 있다. 수만 대를 넣으면 실행이 수십 배 느려지므로,
  대신 "그 시간대 실제로 그 도로를 지나는 속도"가 나오도록 도로 최고속도에 배율을 곱한다(정체를 속도로 표현).
- 배율은 요일 종류(평일/토/일·공휴일) × 시각(0~23시) × 도로별로 정한다. TOPIS 링크와 연결된 간선 도로는
  그 링크의 배율을, 링크에 안 잡힌 간선 조각은 같은 시간대 간선 배율의 중앙값을 쓴다.
  이면도로는 실측이 없어 배율 1.0(5030 제한속도 30km/h 그대로)이 기본이다(default_local).

적용
- 정시마다 traci.edge.setMaxSpeed(도로, 제한속도 × 배율).
- 배차·재배치 비용(TimeVaryingTravelTime)도 같은 배율 표로 바꿔, 배차가 보는 이동 시간과 실제 이동이 일치하게 한다.
"""
from __future__ import annotations

import datetime as dt
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_FILE = os.path.join(ROOT, "data", "external", "speed_profile_gangnam.json")
LOCAL_TYPES = {"residential", "unclassified", "living_street", "service"}


def day_type(day: dt.date, is_holiday: bool = False) -> str:
    """weekday / saturday / sunday(일요일·공휴일). is_holiday는 프로젝트의 '주말 또는 공휴일' 판정이라
    토요일 구분에는 쓰지 않고, 평일인데 is_holiday면(holiday_mode로 강제한 경우 포함) 공휴일로 본다."""
    from module1_simulation.holiday_utils import is_public_holiday
    if day.weekday() == 6 or is_public_holiday(day) or (is_holiday and day.weekday() < 5):
        return "sunday"
    return "saturday" if day.weekday() == 5 else "weekday"


class SpeedProfileController:
    def __init__(self, profile: dict, net_file: str, run_date: dt.date, sim_start_hour: int, is_holiday: bool,
                 travel_time=None):
        import sumolib
        net = sumolib.net.readNet(net_file)
        self.base = {e.getID(): e.getSpeed() for e in net.getEdges() if e.getFunction() != "internal"}
        self.local = {e.getID() for e in net.getEdges() if e.getFunction() != "internal"
                      and (e.getType() or "").split(".")[-1] in LOCAL_TYPES}
        self.profile = profile
        self.run_date, self.t0, self.is_holiday = run_date, sim_start_hour * 3600, is_holiday
        self.travel_time = travel_time
        self.hour_key = None
        self.applied = []

    @classmethod
    def from_config(cls, cfg, net_file, run_date, sim_start_hour, is_holiday=False, travel_time=None):
        if cfg.get("speed_profile", "topis") != "topis":
            return None
        path = cfg.get("speed_profile_file") or DEFAULT_FILE
        if not os.path.exists(path):
            print(f"[경고] 속도 보정 파일이 없어 제한속도 그대로 씁니다: {path} (tools/calibrate_speed.py)")
            return None
        with open(path, encoding="utf-8") as f:
            return cls(json.load(f), net_file, run_date, sim_start_hour, is_holiday, travel_time)

    def scale_for(self, dtype: str, hour: int) -> dict:
        h = self.profile["factors"].get(dtype, {}).get(str(hour))
        if h is None:
            return {e: 1.0 for e in self.base}
        edges = h.get("edges", {})
        loc = h.get("default_local", h["default"])
        return {e: edges.get(e, loc if e in self.local else h["default"]) for e in self.base}

    def maintain(self, now: float):
        abs_sec = self.t0 + now
        day = self.run_date + dt.timedelta(days=int(abs_sec // 86400))
        key = (day_type(day, self.is_holiday and day == self.run_date), int(abs_sec % 86400 // 3600))
        if key == self.hour_key:
            return
        import traci
        scale = self.scale_for(*key)
        for e, v in self.base.items():
            traci.edge.setMaxSpeed(e, v * scale[e])
        if self.travel_time is not None and hasattr(self.travel_time, "set_scale"):
            self.travel_time.set_scale(key, scale)
        self.hour_key = key
        vals = sorted(scale.values())
        self.applied.append({"t": now, "day_type": key[0], "hour": key[1], "median_factor": round(vals[len(vals) // 2], 3)})

    def summary(self):
        return {"file_version": self.profile.get("version"), "hours": self.applied}
