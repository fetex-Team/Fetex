"""
배차를 못 받고 너무 오래 대기한 승객을 시뮬레이션에서 소멸(제거)시키는 매니저.

왜 필요한가:
- build_env.py는 스케줄대로 승객을 '생성'만 함.
- taxi_manager.TaxiFleetManager는 택시 '대수'만 유지함 (손님 없는 택시 재타겟팅).
- 즉, 어느 쪽도 "너무 오래 기다린 승객을 치우는" 로직을 갖고 있지 않았음.
- 그 결과 배차 못 받은 승객이 sim_end_hour까지 계속 길가에 쌓여서
  n_unpicked 수가 부풀려지고, 실제로는 "영원히 대기 중"인 상태가 됨.

동작:
- 매 스텝 traci.person.getIDList()를 보고, 아직 택시에 타지 않은(person.getVehicle == "")
  사람 중 depart 이후 wait_timeout(초)을 넘긴 사람을 traci.person.remove()로 제거.
- 제거된 사람은 removed_pids에 기록되어 measure_wait_time.py 등에서
  "타임아웃으로 소멸(집계에서 unpicked로 카운트)" 구분에 사용 가능.

[수정] 이미 Hungarian이 택시를 배정한(예약이 걸린) 승객을 타임아웃으로 강제 제거하면
SUMO 내부의 택시 예약/스케줄 상태가 깨져서 "Connection closed by SUMO" 크래시로
이어지는 경우가 있음이 확인됨. 그래서 dispatcher(HungarianDispatcher)의
dispatched_person_ids를 넘겨주면, 그 목록에 있는 승객만 "물리적 제거를 한 스텝
미루고" 다음 스텝에 다시 확인 - 배정이 취소/만료돼서 목록에서 빠지면 그때 안전하게
제거함. 아직 배정 안 된 대다수 승객은 원래 로직 그대로 즉시 제거되므로,
"타임아웃 소멸" 지표 자체는 거의 왜곡되지 않음 (제거가 살짝 늦어질 뿐).
"""

import traci


class PassengerTimeoutManager:
    def __init__(self, wait_timeout_sec: float, dispatcher=None):
        """
        dispatcher: module4_dispatch.hungarian_dispatcher.HungarianDispatcher 인스턴스(선택).
                    넘겨주면 그 dispatcher.dispatched_person_ids를 참조해서
                    이미 예약된 승객의 강제 제거를 한 스텝 미룸. 안 넘기면
                    (Random/Greedy 등 Hungarian을 안 쓰는 조합) 기존과 동일하게 즉시 제거.
        """
        self.wait_timeout_sec = wait_timeout_sec
        self.dispatcher = dispatcher
        self.depart_time = {}       # person_id -> depart 시각(초)
        self.removed_pids = set()   # 타임아웃으로 소멸 처리된 person_id
        self.threshold_exceeded_at = {}
        self.removed_at = {}        # person_id -> 소멸(타임아웃 확정) 시각(초), 대기시간 재구성용
        self._pending_removal = set()  # 예약 걸려있어서 제거를 미루고 있는 person_id

    def _is_reserved(self, pid: str) -> bool:
        if self.dispatcher is None:
            return False
        return pid in self.dispatcher.dispatched_person_ids

    def maintain(self, now_seconds: float):
        # 탑승 확인을 예약/제거 판단보다 먼저 수행한다.
        active = set(traci.person.getIDList())
        self._pending_removal.intersection_update(active)
        for pid in sorted(active):
            self.depart_time.setdefault(pid, now_seconds)
            if pid in self.removed_pids:
                continue
            try:
                if traci.person.getVehicle(pid):
                    self._pending_removal.discard(pid)
                    continue
                if now_seconds - self.depart_time[pid] < self.wait_timeout_sec:
                    continue
                self.threshold_exceeded_at.setdefault(pid, now_seconds)
                if self._is_reserved(pid):
                    self._pending_removal.add(pid)
                    continue
                traci.person.remove(pid)
            except traci.exceptions.TraCIException:
                continue  # 실제 제거 실패를 타임아웃 성공으로 집계하지 않는다.
            self._pending_removal.discard(pid)
            self.removed_pids.add(pid)
            self.removed_at[pid] = now_seconds
