"""
[시나리오] 영역 밖으로 손님을 태우고 나간 택시의 복귀 지연.

기본 시뮬레이션은 손님을 영역 경계에 내려 준 택시가 바로 다시 빈 차로 돌아다닌다고 본다
(= 나간 택시만큼 다른 빈 택시가 즉시 들어온다는 가정). 실제로는
- 강남구 하루 택시 하차 85,218건 / 승차 92,858건 (서울시 2025 교통이용 통계보고서 p.80)
  -> 나간 택시의 약 91.8%만 '다른 곳에서 손님을 내려 준 빈 택시'로 채워지고,
- 나머지 8.2%는 그 택시가 손님을 목적지까지 태워 주고(TIMS 평균 운행 21분) 돌아올 때까지 빈자리로 남는다.

taxi_external_away=true면, 영역 밖으로 가는 손님(Call.kind == 'external')을 내려 준 택시 중
(1 - 보충 비율) 확률로 그 택시를 '운행 시간 x 왕복 배수 - 영역 안에서 이미 탄 시간'만큼 운행에서 뺀다.
기본은 꺼져 있다(민감도 시나리오). 같은 시드면 같은 택시가 같은 시간 빠진다(전용 난수).
"""
import random

import traci

DEFAULT_REPLACEMENT = 85218 / 92858   # 강남구 2025 하차/승차


class ExternalTripAwayModel:
    def __init__(self, manager, spawn_manager, replacement_ratio=DEFAULT_REPLACEMENT, return_factor=2.0,
                 seed=None):
        self.manager = manager
        self.spawn = spawn_manager
        self.replacement_ratio = float(replacement_ratio)
        self.return_factor = float(return_factor)
        self.rng = random.Random(f"fetex-away|{seed}")
        self.n_external = self.n_replaced = self.n_away = self.n_busy = 0
        self.total_away_sec = 0.0
        self.removed_this_step = set()     # 메인 루프가 이번 스텝 차량 목록에서 뺀다

    @classmethod
    def from_config(cls, cfg, manager, spawn_manager, seed=None):
        if not cfg.get("taxi_external_away", False):
            return None
        if getattr(spawn_manager, "demand_source", "") != "real":
            print("[안내] taxi_external_away는 실데이터 수요(demand_source=real)에서만 동작합니다 — 건너뜀.")
            return None
        return cls(manager, spawn_manager,
                   replacement_ratio=cfg.get("external_replacement_ratio", DEFAULT_REPLACEMENT),
                   return_factor=cfg.get("external_return_factor", 2.0), seed=seed)

    def on_dropoff(self, now, pid, vid, picked_at=None):
        call = getattr(self.spawn, "call_by_pid", {}).get(pid)
        if call is None or call.kind != "external":
            return
        self.n_external += 1
        if self.rng.random() < self.replacement_ratio:
            self.n_replaced += 1
            return
        try:
            state = traci.vehicle.getParameter(vid, "device.taxi.state")
        except traci.exceptions.TraCIException:
            return
        if state not in ("0", ""):
            self.n_busy += 1          # 이미 다음 배차를 받은 택시는 빼지 않는다 (하차 감지가 배차보다 먼저라 드묾)
            return
        ride_sec = max(0.0, now - picked_at) if picked_at is not None else 0.0
        mean_min = self.spawn.cell_mean_time_min(call.cell)
        away = max(60.0, self.return_factor * mean_min * 60.0 - ride_sec)
        self.manager.send_away(vid, now + away)
        self.removed_this_step.add(vid)
        self.n_away += 1
        self.total_away_sec += away

    def summary(self):
        return {"replacement_ratio": round(self.replacement_ratio, 3), "return_factor": self.return_factor,
                "external_dropoffs": self.n_external, "replaced": self.n_replaced, "sent_away": self.n_away,
                "skipped_busy": self.n_busy,
                "avg_away_min": round(self.total_away_sec / self.n_away / 60, 1) if self.n_away else 0.0}
