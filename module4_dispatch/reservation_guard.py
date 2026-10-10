"""
예약 규칙 수문장 (ReservationGuard).

[규칙]
  1) 예약(= 택시에 승객이 묶이는 것)은 "완전히 빈 택시 1대 <-> 손님 1명" 관계에서만 허용.
  2) 동시에 유효한 예약 수는 택시 수를 절대 넘을 수 없다 (택시 1대당 예약 최대 1건).
  3) 손님을 태웠거나 픽업하러 가는 중인 택시는 새 예약을 받을 수 없다.

[동작]
  - dispatch(vid, res): 위 규칙을 전부 통과할 때만 traci.vehicle.dispatchTaxi(vid, [res.id]) 호출.
  - sync(now): 매 스텝 한 번. 빈 택시 목록 갱신 + 운행 끝난 예약 해제 + 규칙 위반(감사) 기록.
  - report(): 거부 사유별 통계 / 위반 건수 문자열.

[사용법] 배차기가 dispatchTaxi 를 직접 부르지 말고 guard.dispatch() 를 거치게 하면 됩니다.
  custom_dispatchers.py 의 두 수제 배차기는 이미 내부에서 이 guard 를 사용합니다.

[한계] SUMO 내장 알고리즘(greedy/routeExtension, dispatch-algorithm != traci)은 SUMO 가 직접
  배정하므로 막을 수 없고, audit 로 위반을 "감지"만 할 수 있습니다 (guard.sync 를 매 스텝 호출).
"""

import traci


class ReservationGuard:
    def __init__(self, release_grace_sec: float = 2.0, audit_customers: bool = True,
                 max_violation_log: int = 50):
        """
        release_grace_sec : dispatchTaxi 직후 택시 상태가 바뀌기 전 잠깐 '빈 택시'로 보일 수 있어서,
                            배차 후 이 시간(시뮬 초)이 지나야 '운행 종료'로 판단해 예약을 해제.
        audit_customers   : True면 택시별 device.taxi.currentCustomers 로 '택시 1대에 2명 이상' 위반 감지.
        """
        self.release_grace_sec = float(release_grace_sec)
        self.audit_customers = audit_customers
        self.max_violation_log = int(max_violation_log)

        self.bound = {}            # vid -> (reservation_id, [person ids], 배차 시각)  : 유효 예약 장부
        self._empty = set()        # 이번 스텝의 빈 택시
        self._fleet_size = 0       # 이번 스텝의 전체 택시 수
        self._now = 0.0
        self._synced = False

        self.accepted = 0
        self.rejected = {}         # 사유 -> 건수
        self.violations = []       # (시각, 설명) 감사 위반 기록
        self.violation_count = 0
        self.peak_active = 0

    # ---- 매 스텝 ---------------------------------------------------------
    def sync(self, now_seconds: float):
        self._now = float(now_seconds)
        try:
            self._empty = set(traci.vehicle.getTaxiFleet(0))
            all_taxis = set(traci.vehicle.getTaxiFleet(-1))
        except traci.exceptions.TraCIException:
            return
        self._fleet_size = len(all_taxis)
        self._synced = True

        # 운행 끝났거나(빈 택시로 복귀) 사라진 택시의 예약 해제
        for vid in list(self.bound):
            _, _, t0 = self.bound[vid]
            if vid not in all_taxis:
                del self.bound[vid]
            elif vid in self._empty and self._now - t0 > self.release_grace_sec:
                del self.bound[vid]

        self._audit(all_taxis)

    def _audit(self, all_taxis):
        # 불변식 1: 유효 예약 수 <= 택시 수
        if len(self.bound) > self._fleet_size:
            self._violation(f"유효 예약 {len(self.bound)}건 > 택시 {self._fleet_size}대")
        self.peak_active = max(self.peak_active, len(self.bound))
        # 불변식 2: 택시 1대에 예약/손님 2명 이상 금지 (SUMO 내장 알고리즘 감시용)
        if not self.audit_customers:
            return
        busy = all_taxis - self._empty
        for vid in busy:
            try:
                cur = traci.vehicle.getParameter(vid, "device.taxi.currentCustomers").split()
            except traci.exceptions.TraCIException:
                self.audit_customers = False      # 이 SUMO 버전은 파라미터 미지원 -> 감사 중단
                return
            if len(cur) > 1:
                self._violation(f"{vid} 에 손님 {len(cur)}명 동시 배정: {cur}")

    def _violation(self, msg):
        self.violation_count += 1
        if len(self.violations) < self.max_violation_log:
            self.violations.append((self._now, msg))

    # ---- 배차 관문 -------------------------------------------------------
    def check(self, vid, res):
        """통과하면 None, 거부면 사유 문자열."""
        if len(res.persons) != 1:
            return "손님 1명이 아닌 예약(그룹)"
        if vid not in self._empty:
            return "빈 택시 아님(픽업 중이거나 승객 탑승 중)"
        if vid in self.bound:
            return "이미 예약이 걸린 택시"
        if len(self.bound) >= self._fleet_size:
            return "유효 예약 수가 택시 수에 도달"
        return None

    def dispatch(self, vid, res) -> bool:
        if not self._synced:
            self.sync(self._now)
        reason = self.check(vid, res)
        if reason:
            self.rejected[reason] = self.rejected.get(reason, 0) + 1
            return False
        try:
            traci.vehicle.dispatchTaxi(vid, [res.id])   # 정확히 1건
        except traci.exceptions.TraCIException:
            self.rejected["dispatchTaxi 실패"] = self.rejected.get("dispatchTaxi 실패", 0) + 1
            return False
        self.bound[vid] = (res.id, list(res.persons), self._now)
        self._empty.discard(vid)       # 같은 스텝 안 중복 배차 방지
        self.accepted += 1
        self.peak_active = max(self.peak_active, len(self.bound))
        return True

    # ---- 리포트 ----------------------------------------------------------
    def report(self) -> str:
        lines = [f"[ReservationGuard] 승인 {self.accepted}건 | 동시 예약 최대 {self.peak_active}건 | "
                 f"규칙 위반 감지 {self.violation_count}건"]
        for k, v in sorted(self.rejected.items(), key=lambda x: -x[1]):
            lines.append(f"  거부 - {k}: {v}건")
        for t, m in self.violations[:10]:
            lines.append(f"  위반 t={t:.0f}s: {m}")
        return "\n".join(lines)
