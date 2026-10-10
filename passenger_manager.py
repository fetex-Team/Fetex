import traci


_RIDING_CACHE = {"t": None, "map": {}}


def riding_map(now_seconds: float, vehicle_ids=None) -> dict:
    if _RIDING_CACHE["t"] == now_seconds:
        return _RIDING_CACHE["map"]
    m = {}
    for vid in (vehicle_ids if vehicle_ids is not None else traci.vehicle.getIDList()):
        if not vid.startswith("taxi"):
            continue
        try:
            for pid in traci.vehicle.getPersonIDList(vid):
                m[pid] = vid
        except traci.exceptions.TraCIException:
            continue
    _RIDING_CACHE["t"] = now_seconds
    _RIDING_CACHE["map"] = m
    return m


class PassengerTimeoutManager:
    def __init__(self, wait_timeout_sec: float, dispatcher=None):
        self.wait_timeout_sec = wait_timeout_sec
        self.dispatcher = dispatcher
        self.depart_time = {}       # person_id -> depart 시각(초)
        self.removed_pids = set()   # 타임아웃으로 소멸 처리된 person_id
        self.removed_at = {}        # person_id -> 소멸 시각(초), 통계 집계용
        self._pending_remove = set()  # 논리적으로만 타임아웃 확정, 물리 제거는 예약 해제 후로 미룬 승객
        self._reserved_now = set()  
        self._reserved_at = -1e9    

    def _refresh_reserved(self, now_seconds: float, current_pids: set = None):
        # 10초 주기로 실행하여 엔진 통신 부하 최소화
        if now_seconds - self._reserved_at < 10:
            return
        self._reserved_at = now_seconds
        try:
            # 1. 배차 확정(2) 및 탑승 중(4)인 승객 보호 명단에 누적
            safe_passengers = {
                p 
                for state in (2, 4) 
                for r in traci.person.getTaxiReservations(state) 
                for p in r.persons
            }
            self._reserved_now.update(safe_passengers)
            
            # 2. 현재 맵에 살아있는 전체 승객 목록 불러오기
            active_persons = set(current_pids) if current_pids is not None else set(traci.person.getIDList())
            
            # 3. 예약 보호 명단 청소 (하차해서 맵에서 사라진 사람 지우기)
            self._reserved_now.intersection_update(active_persons)
            
            # 4. [신규 추가] depart_time 청소 (딴 거 타러 갔거나, 하차해서 사라진 사람의 기록 삭제)
            dead_pids = set(self.depart_time.keys()) - active_persons
            for pid in dead_pids:
                del self.depart_time[pid]
                
        except traci.exceptions.TraCIException:
            pass
 
    def _is_reserved(self, pid: str, now_seconds: float = None) -> bool:
        if now_seconds is not None:
            self._refresh_reserved(now_seconds)
        return pid in self._reserved_now

    def maintain(self, now_seconds: float, current_pids: set = None):
        """current_pids: 메인 루프가 스텝 시작에 한 번 가져온 set(traci.person.getIDList()).
        이 매니저가 remove()한 id는 current_pids에서도 빼서 뒤의 spawn/log가 같은 스텝에 이미 사라진 사람을 보지 않게 한다.
        None이면 직접 조회."""
        if current_pids is None:
            current_pids = set(traci.person.getIDList())
        # 1. 최신 SUMO 예약 상태를 현재 스텝 기준으로 갱신 (10초 주기)
        self._refresh_reserved(now_seconds, current_pids)

        for pid in list(current_pids):  # 루프 중 discard가 일어나므로 복사본 순회
            if pid not in self.depart_time:
                self.depart_time[pid] = now_seconds
                continue

            if pid in self.removed_pids:
                continue

            waited = now_seconds - self.depart_time[pid]
            if waited < self.wait_timeout_sec:
                continue

            try:
                vid = traci.person.getVehicle(pid)
            except traci.exceptions.TraCIException:
                continue
            
            if vid: # 이미 탑승 완료자는 패스
                continue

            # 2. [핵심 방어선] 현재 택시가 배차(예약)되어 오고 있는 승객인지 확인
            is_currently_reserved = (pid in self._reserved_now) or (
                self.dispatcher and hasattr(self.dispatcher, "dispatched_person_ids") and pid in self.dispatcher.dispatched_person_ids
            )

            # --- 3. 타임아웃 확정 ---
            # 예약(택시 배차) 중인 승객은 SUMO가 튕기므로 지금 물리 제거(remove/removeStages)를 하지 않는다.
            # 대신 "논리적 타임아웃"만 확정한다: removed_pids/removed_at에 기록해 통계에서는
            # wait_timeout_sec 상한으로 처리(측정 로직의 timeout 패널티)하고, 물리 제거는
            # 예약이 풀린 뒤(_flush_pending_remove)로 미룬다. dispatched_person_ids는 건드리지 않아
            # 이미 배정된 택시가 계속 안전하게 움직이도록 둔다.
            self.removed_pids.add(pid)
            self.removed_at[pid] = now_seconds

            if is_currently_reserved:
                self._pending_remove.add(pid)
                continue

            self._physical_remove(pid, current_pids)

        self._flush_pending_remove(current_pids)

    def _physical_remove(self, pid: str, current_pids: set):
        if self.dispatcher and hasattr(self.dispatcher, "dispatched_person_ids"):
            self.dispatcher.dispatched_person_ids.discard(pid)
        try:
            traci.person.removeStages(pid)
        except Exception:
            pass
        try:
            traci.person.remove(pid)
            current_pids.discard(pid)
        except Exception:
            pass

    def _flush_pending_remove(self, current_pids: set):
        """예약이 풀린(택시도 안 오고 탑승도 안 한) 논리 타임아웃 승객만 뒤늦게 물리 제거."""
        for pid in list(self._pending_remove):
            if pid not in current_pids:   # 이미 탑승/하차/소멸로 맵에서 사라짐
                self._pending_remove.discard(pid)
                continue
            try:
                if traci.person.getVehicle(pid):  # 탑승 중이면 건드리지 않음
                    continue
            except traci.exceptions.TraCIException:
                continue
            if pid in self._reserved_now or (
                self.dispatcher and hasattr(self.dispatcher, "dispatched_person_ids")
                and pid in self.dispatcher.dispatched_person_ids
            ):
                continue
            self._pending_remove.discard(pid)
            self._physical_remove(pid, current_pids)