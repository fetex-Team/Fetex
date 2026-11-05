"""
학교/회사/음식점 카테고리의 승객을 시뮬레이션 도중 실시간으로 생성/소멸시키는 매니저.

[배경] build_env.py는 이제 주택(residential)만 정적으로 미리 생성하고,
학교/회사/음식점은 이 매니저가 매 스텝 traci로 직접 만들고 없앱니다.
왜냐하면 "회사가 저녁에 몇 명을 뱉을지"가 "그날 아침 회사가 몇 명을 흡수했는지"에
달려있는데, 이건 시뮬레이션이 실제로 돌아가봐야 알 수 있는 값이기 때문입니다
(build_env.py 실행 시점=시뮬레이션 시작 전에는 알 수 없음).

[핵심 개념]
1. 카테고리별 흡수 카운터: 사람이 해당 카테고리 건물에 정상 도착(택시에서 하차)하면
   그 카테고리의 absorbed 카운터가 1 증가.
2. 흡수량 기반 재스폰: 예) 회사가 아침에 500명을 흡수했으면, 저녁 배출 시점에
   그 500이라는 숫자(의 일부, 모달 필터 적용)를 그대로 다시 뱉어냄.
3. 시간대별 택시확률 곡선: 등교/출근은 피크 시각에 가까울수록 택시 선택 확률이
   선형으로 증가하다가, 정해진 시각 이후로는 완전히 끊김(컷오프).
4. 음식점 개인별 타이머: 음식점에 도착한 사람은(점심 왕복 제외) 1시간 뒤 그 사람만
   개별적으로 재스폰되어 100% 주택으로 이동.

[도착(흡수) 판정 방법]
traci.person.getIDList()를 매 스텝 비교해서, "이전 스텝엔 있었는데 이번 스텝엔 사라진"
사람 중, passenger_manager(PassengerTimeoutManager)가 타임아웃으로 제거한 게 아니면
"정상 도착"으로 판정합니다. (타임아웃 제거는 흡수로 치지 않음 — 실제 그 건물에 못
들어간 것이므로.)

[사용법] run_simulation.py / measure_wait_time.py의 시뮬레이션 루프 안에서:
    pax_manager = PassengerTimeoutManager(...)
    spawn_manager = PassengerSpawnManager(zones=..., sim_start_hour=..., ...)
    ...
    while step < max_steps:
        traci.simulationStep()
        ...
        pax_manager.maintain(now)
        spawn_manager.maintain(now, timeout_removed_pids=pax_manager.removed_pids)
"""

import random
import traci
from config_loader import CFG
from holiday_utils import resolve_is_holiday


class PassengerSpawnManager:
    _weather_logged = False  # 날씨 보정 로그를 프로세스당 1번만 찍기 위한 플래그
    _holiday_logged_for = None  # 공휴일 로그를 같은 날짜에 대해 1번만 찍기 위한 플래그
    _HOLIDAY_DEFAULTS = {"school": 0.0, "company": 0.25, "lunch": 1.0,
                         "evening": 1.0, "restaurant": 1.5, "general": 1.3, "department": 2.0}

    def __init__(self, zones: dict, sim_start_hour: float, sim_end_hour: float = None,
                 school_pop_base: int = None, company_pop_base: int = None,
                 seed: int = None, sim_date=None, department_edges: list = None,
                 holiday_mode: str = None):
        if seed is not None:
            random.seed(seed)

        self.sim_start_hour = sim_start_hour
        # [날씨 보정] GUI "날씨적용" 체크 시 오늘 날씨로 모든 택시 선택 확률에 가산값을 더함.
        self.weather_bonus = self._compute_weather_bonus()
        # [공휴일 보정] sim_date(없으면 평일 취급)와 holiday_mode로 평일/공휴일을 판정하고,
        # 공휴일이면 카테고리별 배율을 택시확률에 곱함. 평일이면 모든 배율이 1.0이라 기존과 동일.
        self.sim_date = sim_date
        # holiday_mode를 직접 넘기면(A/B의 평일/공휴일 비교) config.json 값보다 우선.
        _hmode = holiday_mode or CFG.get("holiday_mode", "auto")
        self.is_holiday = resolve_is_holiday(sim_date, _hmode) if (
            sim_date is not None or _hmode != "auto") else False
        user_mult = CFG.get("holiday_multipliers") or {}
        self.holiday_mult = {k: float(user_mult.get(k, v)) for k, v in self._HOLIDAY_DEFAULTS.items()}
        if PassengerSpawnManager._holiday_logged_for != (str(sim_date), self.is_holiday):
            PassengerSpawnManager._holiday_logged_for = (str(sim_date), self.is_holiday)
            print(f"[공휴일 보정] 날짜 {sim_date} -> {'공휴일/주말' if self.is_holiday else '평일'}"
                  f"{' | 배율 ' + str(self.holiday_mult) if self.is_holiday else ''}")
        self.school_pop_base = int(CFG.get("school_pop_base", 400) if school_pop_base is None else school_pop_base)
        self.company_pop_base = int(CFG.get("company_pop_base", 100) if company_pop_base is None else company_pop_base)
        self.school_start_hour = float(CFG.get("school_start_hour", 7.0))
        self.school_end_hour = float(CFG.get("school_end_hour", 8.0))
        self.school_taxi_peak_hour = float(CFG.get("school_taxi_peak_hour", self.school_end_hour))
        self.school_afternoon_start_hour = float(CFG.get("school_afternoon_start_hour", 16.0))
        self.school_afternoon_end_hour = float(CFG.get("school_afternoon_end_hour", 17.0))
        self.school_afternoon_fraction = float(CFG.get("school_afternoon_fraction", 0.01))
        self.company_start_hour = float(CFG.get("company_start_hour", 8.0))
        self.company_end_hour = float(CFG.get("company_end_hour", 10.0))
        self.company_taxi_peak_hour = float(CFG.get("company_taxi_peak_hour", self.company_end_hour))
        self.lunch_start_hour = float(CFG.get("lunch_start_hour", 12.0))
        self.lunch_end_hour = float(CFG.get("lunch_end_hour", 13.0))
        self.lunch_company_release_fraction = float(CFG.get("lunch_company_release_fraction", 0.80))
        self.lunch_taxi_fraction = float(CFG.get("lunch_taxi_fraction", 0.10))
        self.evening_start_hour = float(CFG.get("evening_start_hour", 18.0))
        self.evening_end_hour = float(CFG.get("evening_end_hour", 22.0))
        self.evening_taxi_fraction = float(CFG.get("evening_taxi_fraction", 0.30))
        self.late_evening_start_hour = float(CFG.get("late_evening_start_hour", 23.0))
        self.late_evening_end_hour = float(CFG.get("late_evening_end_hour", 24.0))
        self.evening_residential_fraction = float(CFG.get("evening_residential_fraction", 25/30))
        self.restaurant_stay_sec = float(CFG.get("restaurant_stay_sec", 3600.0))
        # 음식점 방문 시민 스폰 (버스정류장/지하철입구 -> 음식점)
        self.restaurant_pop_base = float(CFG.get("restaurant_pop_base", 60))
        self.restaurant_window1_start = float(CFG.get("restaurant_window1_start", 12.0))
        self.restaurant_window1_end = float(CFG.get("restaurant_window1_end", 14.0))
        self.restaurant_window2_start = float(CFG.get("restaurant_window2_start", 17.0))
        self.restaurant_window2_end = float(CFG.get("restaurant_window2_end", 22.0))
        self.restaurant_civilian_taxi_probability = float(CFG.get("restaurant_civilian_taxi_probability", 0.10))
        self._restaurant_civilian_accumulator = 0.0
        self._restaurant_civilian_last_second = -1

        # 백화점/쇼핑몰 (스폰 전용 후보 edge. zones/카테고리에는 없음 -> RL 관측/행동 크기 영향 없음)
        self.department_edges = list(department_edges or [])
        self.department_enabled = bool(CFG.get("department_enabled", True)) and len(self.department_edges) > 0
        self.department_pop_base = float(CFG.get("department_pop_base", 40))
        self.department_window_start = float(CFG.get("department_window_start", 11.0))
        self.department_window_end = float(CFG.get("department_window_end", 21.0))
        self.department_taxi_probability = float(CFG.get("department_taxi_probability", 0.10))
        self.department_stay_sec = float(CFG.get("department_stay_sec", 5400.0))
        self._department_accumulator = 0.0
        self._department_last_second = -1
        self._department_pending = []  # 체류 후 귀가 트리거 시각 목록

        # -------------------------------------------------------------
        # 기본 승객 수(num_passengers) 기반 독립 가챠 생성
        # 기존 시간대별(school/company/residential 스케줄) 로직과는 완전히 별개로,
        # sim 구간을 num_passengers로 나눈 간격마다 1틱씩 체크하며
        # residential_taxi_probability 확률로 "택시 승객"만 생성한다.
        # 확률에 당첨되지 않으면 그 틱은 그냥 소멸(생성 자체를 안 함).
        # -------------------------------------------------------------
        self.num_passengers = int(CFG.get("num_passengers", 1000))
        self.residential_taxi_probability = float(CFG.get("residential_taxi_probability", 0.10))
        self.sim_end_hour = sim_end_hour
        if self.sim_end_hour is not None and self.num_passengers > 0:
            duration_sec = max(1.0, (self.sim_end_hour - self.sim_start_hour) * 3600.0)
            
            # [변경] 기존 초당 비율에 10초 주기를 반영하여 10배로 늘림
            self._gacha_rate_per_sec = (self.num_passengers / duration_sec) * 10.0
        else:
            self._gacha_rate_per_sec = None
        self._gacha_accumulator = 0.0
        self._gacha_last_second = -1  # 같은 초에 중복 누적 방지
        self._company_gacha_accumulator = 0.0
        self._company_gacha_last_second = -1

        self.transit_edges = zones.get("subway_entrance", []) + zones.get("bus_stop", [])
        self.school_edges = zones.get("school", [])
        self.company_edges = zones.get("company", [])
        self.restaurant_edges = zones.get("restaurant", [])
        self.residential_edges = zones.get("residential", [])

        all_edges = [e for edges in zones.values() for e in edges if edges]
        self.all_edges = all_edges
        self.transit_edges = self.transit_edges or all_edges
        self.school_edges = self.school_edges or all_edges
        self.company_edges = self.company_edges or all_edges
        self.restaurant_edges = self.restaurant_edges or all_edges
        self.residential_edges = self.residential_edges or all_edges

        # 카테고리별 흡수(도착) 카운터
        self.absorbed = {"school": 0, "company": 0, "restaurant": 0}

        # 도착 판정을 위한 이전 스텝 활성 승객 집합
        self._prev_active_ids = set()
        # 이 매니저가 만든 승객의 목적지 카테고리 기록 (도착 시 어느 카운터를 올릴지 판단용)
        self._dest_category = {}
        # 점심 왕복으로 생성된 승객은 restaurant 개인 타이머 대상에서 제외
        self._is_lunch_cycle = set()

        self._counter = 0
        self.total_timeout = 0
        self.total_spawned = 0
        # 카테고리별 "생성(spawn)" 카운터 -- 콘솔 디버그로 어디서 몇 명 나오는지 바로 보려고 추가
        self.spawn_counts = {
            "school_morning": 0, "company_morning": 0,
            "lunch_release": 0, "lunch_return": 0,
            "school_afternoon": 0, "evening": 0,
            "residential_gacha": 0, "restaurant_civilian": 0,
            "department": 0, "department_return": 0,
        }
        # 각 이벤트가 "한 번만" 실행되도록 하는 트리거 플래그/스냅샷
        self._lunch_12_done = False
        self._lunch_13_done = False
        self._lunch_release_taxi_count = 0
        self._evening_pool_snapshot = None  # 18시 시점 company 흡수량 스냅샷
        self._evening_spawned_count = 0
        self._evening_dump_done = False
        self._school_afternoon_pool_snapshot = None
        self._school_afternoon_spawned_count = 0

        # 음식점 개인별 재스폰 대기열: [(트리거시각, 출발edge), ...]
        self._restaurant_pending = []
        self._current_pids = None  # maintain() 실행 중에만 유효한 스냅샷 참조

    # ---------- 내부 유틸 ----------
    def _current_hour(self, now_seconds: float) -> float:
        return self.sim_start_hour + (now_seconds / 3600.0)

    def _new_pid(self) -> str:
        self._counter += 1
        return f"dyn_pax_{self._counter}"

    def _spawn_person(self, now_seconds: float, start_edge: str, end_edge: str,
                       dest_category: str = None, lunch_cycle: bool = False):
        pid = self._new_pid()
        try:
            traci.person.add(pid, start_edge, pos=0, depart=now_seconds)
            traci.person.appendDrivingStage(pid, end_edge, lines="taxi")
        except traci.exceptions.TraCIException:
            return None
        self.total_spawned += 1
        if self._current_pids is not None:
            self._current_pids.add(pid)  # 이번 스텝 스냅샷에도 반영
        if dest_category:
            self._dest_category[pid] = dest_category
        if lunch_cycle:
            self._is_lunch_cycle.add(pid)
        return pid

    def _pick_edge(self, pool: list, exclude: str = None) -> str:
        candidate = random.choice(pool)
        if candidate == exclude and len(pool) > 1:
            candidate = random.choice(pool)
        return candidate

    # ---------- 도착(흡수) 감지 ----------
    def _process_arrivals(self, timeout_removed_pids: set, now_seconds: float, current_pids: set):
        current_ids = set(current_pids)  # 복사: 이후 스폰으로 current_pids가 커져도 _prev_active_ids는 스폰 전 상태 유지
        disappeared = self._prev_active_ids - current_ids
        self._prev_active_ids = current_ids

        for pid in disappeared:
            if pid not in self._dest_category:
                continue  # 이 매니저가 만든 게 아닌 사람(주택발 정적 승객 등)은 흡수 집계 안 함
            category = self._dest_category.pop(pid)
            was_lunch = pid in self._is_lunch_cycle
            self._is_lunch_cycle.discard(pid)

            if pid in timeout_removed_pids:
                self.total_timeout += 1
                continue  # 타임아웃으로 못 태운 것 — 흡수(도착)로 치지 않음

            # 정상 도착 처리
            self.absorbed[category] = self.absorbed.get(category, 0) + 1

            # 음식점 도착이고 점심 왕복이 아니면 -> 1시간 뒤 개인 재스폰 예약
            if category == "restaurant" and not was_lunch:
                self._restaurant_pending.append((now_seconds + self.restaurant_stay_sec, None))

            # 백화점 도착 -> 체류 후 주거로 귀가 예약
            if category == "department":
                self._department_pending.append(now_seconds + self.department_stay_sec)

    # ---------- 아침 (7~10시): 학교 등교(7~8시, 9시 컷오프) + 회사 출근(8~10시) ----------
    def _maintain_morning(self, now_seconds: float, now_hour: float):
        # 학교 등교: 7~8시, 8시에 가까울수록 택시 확률 선형 증가, 9시 이후 완전 중단
        if self.school_start_hour <= now_hour < self.school_end_hour:
            rate = self.school_pop_base / 3600.0  # 초당 시도 횟수(윈도우 1시간 기준)
            if random.random() < rate:
                peak = min(max(self.school_taxi_peak_hour, self.school_start_hour), self.school_end_hour)
                taxi_prob = 0.0 if now_hour <= self.school_start_hour else min(1.0, (now_hour - self.school_start_hour) / max(peak - self.school_start_hour, 1e-9))
                if random.random() < self._tp(taxi_prob, "school"):
                    start_e = self._pick_edge(self.transit_edges)
                    end_e = self._pick_edge(self.school_edges)
                    self._spawn_person(now_seconds, start_e, end_e, dest_category="school")
                    self.spawn_counts["school_morning"] += 1
        # (9시 이후는 자연히 이 if문 범위 밖이라 스폰 없음 — 별도 컷오프 처리 불필요)

        # 회사 출근: 8~10시, 10시에 가까울수록 택시 확률 선형 증가
        if self.company_start_hour <= now_hour < self.company_end_hour:
            # company_pop_base는 "회사 한 곳당 인구"이므로, 실제 회사 개수(company_edges)를 곱해서
            # 지역 전체 총 모수로 환산 (회사가 많은 지역일수록 시도 횟수도 비례해서 늘어남)
            company_total_pop = self.company_pop_base * max(len(self.company_edges), 1)
            rate = company_total_pop / max((self.company_end_hour - self.company_start_hour) * 3600.0, 1.0)  # 윈도우 2시간
            # rate가 1을 넘으면(회사 많음+pop_base 큼) 초당 1회 상한에 막혀 포화되므로
            # residential 가챠와 동일하게 accumulator로 초당 여러 번 시도 가능하게 처리
            cur_second = int(now_seconds)
            if cur_second != self._company_gacha_last_second:
                self._company_gacha_last_second = cur_second
                self._company_gacha_accumulator += rate
            attempts = int(self._company_gacha_accumulator)
            self._company_gacha_accumulator -= attempts
            for _ in range(attempts):
                peak = min(max(self.company_taxi_peak_hour, self.company_start_hour), self.company_end_hour)
                taxi_prob = 0.0 if now_hour <= self.company_start_hour else min(1.0, (now_hour - self.company_start_hour) / max(peak - self.company_start_hour, 1e-9))
                if random.random() < self._tp(taxi_prob, "company"):
                    start_e = self._pick_edge(self.transit_edges)
                    end_e = self._pick_edge(self.company_edges)
                    self._spawn_person(now_seconds, start_e, end_e, dest_category="company")
                    self.spawn_counts["company_morning"] += 1

    # ---------- 점심 (12~13시): 회사 80% 흡수 -> 음식점(10%택시) -> 13시 복귀(10%택시) ----------
    def _maintain_lunch(self, now_seconds: float, now_hour: float):
        if not self._lunch_12_done and now_hour >= self.lunch_start_hour:
            self._lunch_12_done = True
            release_total = int(self.absorbed.get("company", 0) * self.lunch_company_release_fraction)
            self._lunch_release_taxi_count = int(release_total * self._tp(self.lunch_taxi_fraction, "lunch"))
            for _ in range(self._lunch_release_taxi_count):
                start_e = self._pick_edge(self.company_edges)
                end_e = self._pick_edge(self.restaurant_edges)
                self._spawn_person(now_seconds, start_e, end_e,
                                    dest_category="restaurant", lunch_cycle=True)
                self.spawn_counts["lunch_release"] += 1

        if not self._lunch_13_done and now_hour >= self.lunch_end_hour:
            self._lunch_13_done = True
            for _ in range(self._lunch_release_taxi_count):
                start_e = self._pick_edge(self.restaurant_edges)
                end_e = self._pick_edge(self.company_edges)
                # 13시 복귀는 다시 company로 도착하므로 흡수 카운터가 자연 누적됨(=재흡수)
                self._spawn_person(now_seconds, start_e, end_e,
                                    dest_category="company", lunch_cycle=True)
                self.spawn_counts["lunch_return"] += 1

    # ---------- 학교 하교 (16~17시만, 컷오프) ----------
    def _maintain_school_afternoon(self, now_seconds: float, now_hour: float):
        if self._school_afternoon_pool_snapshot is None and now_hour >= self.school_afternoon_start_hour:
            self._school_afternoon_pool_snapshot = int(self.absorbed.get("school", 0) * self.school_afternoon_fraction)

        if self._school_afternoon_pool_snapshot and self.school_afternoon_start_hour <= now_hour < self.school_afternoon_end_hour:
            remaining = self._school_afternoon_pool_snapshot - self._school_afternoon_spawned_count
            if remaining > 0:
                rate = self._school_afternoon_pool_snapshot / 3600.0
                if random.random() < rate:
                    start_e = self._pick_edge(self.school_edges)
                    end_e = self._pick_edge(self.residential_edges)
                    self._spawn_person(now_seconds, start_e, end_e, dest_category="residential")
                    self._school_afternoon_spawned_count += 1
                    self.spawn_counts["school_afternoon"] += 1
        # 17시 이후는 범위 밖이라 자연 중단 (하드 컷오프)

    # ---------- 회사 퇴근 (18~22시 30% 필터 / 23~24시 100%) ----------
    def _maintain_evening(self, now_seconds: float, now_hour: float):
        if self._evening_pool_snapshot is None and now_hour >= self.evening_start_hour:
            self._evening_pool_snapshot = self.absorbed.get("company", 0)

        if self._evening_pool_snapshot is None:
            return

        if self.evening_start_hour <= now_hour < self.evening_end_hour:
            rate = self._evening_pool_snapshot / max((self.evening_end_hour - self.evening_start_hour) * 3600.0, 1.0)
            if random.random() < rate:
                if random.random() < self._tp(self.evening_taxi_fraction, "evening"):  # 버스35%+지하철35% 제외 -> 30%만 택시
                    dest_pool = self.residential_edges if random.random() < self.evening_residential_fraction else self.restaurant_edges
                    start_e = self._pick_edge(self.company_edges)
                    end_e = self._pick_edge(dest_pool)
                    dest_cat = "residential" if dest_pool is self.residential_edges else "restaurant"
                    self._spawn_person(now_seconds, start_e, end_e, dest_category=dest_cat)
                    self.spawn_counts["evening"] += 1
                self._evening_spawned_count += 1  # 필터 탈락도 "시도"로 카운트(모수 소진)

        elif self.late_evening_start_hour <= now_hour < self.late_evening_end_hour and not self._evening_dump_done:
            self._evening_dump_done = True
            remaining = max(0, self._evening_pool_snapshot - self._evening_spawned_count)
            for _ in range(remaining):
                dest_pool = self.residential_edges if random.random() < self.evening_residential_fraction else self.restaurant_edges
                start_e = self._pick_edge(self.company_edges)
                end_e = self._pick_edge(dest_pool)
                dest_cat = "residential" if dest_pool is self.residential_edges else "restaurant"
                self._spawn_person(now_seconds, start_e, end_e, dest_category=dest_cat)
                self.spawn_counts["evening"] += 1

    # ---------- 음식점 개인별 1시간 타이머 ----------
    def _maintain_restaurant_timers(self, now_seconds: float):
        still_pending = []
        for trigger_time, _ in self._restaurant_pending:
            if now_seconds >= trigger_time:
                start_e = self._pick_edge(self.restaurant_edges)
                end_e = self._pick_edge(self.residential_edges)
                self._spawn_person(now_seconds, start_e, end_e, dest_category="residential")
            else:
                still_pending.append((trigger_time, None))
        self._restaurant_pending = still_pending

    # ---------- 음식점 방문 시민 (버스정류장/지하철입구 -> 음식점, 12~14시 & 17~22시) ----------
    def _maintain_restaurant_civilian(self, now_seconds: float, now_hour: float):
        in_window1 = self.restaurant_window1_start <= now_hour < self.restaurant_window1_end
        in_window2 = self.restaurant_window2_start <= now_hour < self.restaurant_window2_end
        if not (in_window1 or in_window2):
            return

        # restaurant_pop_base는 "음식점 한 곳당 인구" -> 실제 음식점 개수(restaurant_edges)를 곱해서
        # 지역 전체 총 모수로 환산 (회사와 동일한 방식)
        total_pop = self.restaurant_pop_base * max(len(self.restaurant_edges), 1)
        window_duration_sec = (
            (self.restaurant_window1_end - self.restaurant_window1_start) +
            (self.restaurant_window2_end - self.restaurant_window2_start)
        ) * 3600.0
        rate = total_pop / max(window_duration_sec, 1.0)

        cur_second = int(now_seconds)
        if cur_second != self._restaurant_civilian_last_second:
            self._restaurant_civilian_last_second = cur_second
            self._restaurant_civilian_accumulator += rate
        attempts = int(self._restaurant_civilian_accumulator)
        self._restaurant_civilian_accumulator -= attempts

        for _ in range(attempts):
            if random.random() < self._tp(self.restaurant_civilian_taxi_probability, "restaurant"):
                start_e = self._pick_edge(self.transit_edges)
                end_e = self._pick_edge(self.restaurant_edges)
                self._spawn_person(now_seconds, start_e, end_e, dest_category="restaurant")
                self.spawn_counts["restaurant_civilian"] += 1

    # ---------- 백화점 방문 시민 (버스정류장/지하철입구 -> 백화점, 체류 후 주거 귀가) ----------
    def _maintain_department(self, now_seconds: float, now_hour: float):
        if not self.department_enabled:
            return
        # 체류가 끝난 사람은 시간대와 무관하게 주거로 귀가
        still = []
        for trigger_time in self._department_pending:
            if now_seconds >= trigger_time:
                start_e = self._pick_edge(self.department_edges)
                end_e = self._pick_edge(self.residential_edges)
                if self._spawn_person(now_seconds, start_e, end_e, dest_category="residential"):
                    self.spawn_counts["department_return"] += 1
            else:
                still.append(trigger_time)
        self._department_pending = still

        if not (self.department_window_start <= now_hour < self.department_window_end):
            return
        total_pop = self.department_pop_base * len(self.department_edges)
        rate = total_pop / max((self.department_window_end - self.department_window_start) * 3600.0, 1.0)
        cur_second = int(now_seconds)
        if cur_second != self._department_last_second:
            self._department_last_second = cur_second
            self._department_accumulator += rate
        attempts = int(self._department_accumulator)
        self._department_accumulator -= attempts
        for _ in range(attempts):
            if random.random() < self._tp(self.department_taxi_probability, "department"):
                start_e = self._pick_edge(self.transit_edges)
                end_e = self._pick_edge(self.department_edges)
                if self._spawn_person(now_seconds, start_e, end_e, dest_category="department"):
                    self.spawn_counts["department"] += 1

    # ---------- 날씨 보정 ----------
    @staticmethod
    def _compute_weather_bonus() -> float:
        """weather_bonus_enabled가 켜져 있을 때 택시확률에 더할 가산값을 반환 (꺼져 있으면 0.0).

        비(강수량>=임계) 가산 + 쌀쌀함(기온<임계) 가산 + 풍속 2단계(강풍/태풍, 둘 중 높은 단계만
        적용) 가산은 해당되는 것끼리 합산, 아무것도 해당 안 되면 맑음 가산.
        날씨 조회에 실패해서 current_temperature/precipitation 값이 없으면 보정하지 않음.
        풍속(current_wind_speed)만 없으면 풍속 판정만 건너뜀.
        """
        if not CFG.get("weather_bonus_enabled", False):
            return 0.0
        temp = CFG.get("current_temperature")
        precip = CFG.get("current_precipitation")
        if temp is None or precip is None:
            print("[경고] 날씨적용이 켜져 있지만 현재 날씨 조회 값이 없어 보정을 건너뜁니다.")
            return 0.0
        rainy = float(precip) >= float(CFG.get("weather_rain_threshold_mm", 0.1))
        cool = float(temp) < float(CFG.get("weather_cool_threshold_c", 10.0))
        bonus = 0.0
        if rainy:
            bonus += float(CFG.get("weather_rain_taxi_bonus", 0.10))
        if cool:
            bonus += float(CFG.get("weather_cool_taxi_bonus", 0.10))
        wind = CFG.get("current_wind_speed")
        wind_level = None  # None / "strong" / "typhoon" — 풍속 조회 실패 시 판정 자체를 건너뜀
        if wind is not None:
            wind = float(wind)
            if wind >= float(CFG.get("weather_typhoon_wind_threshold_kmh", 61.2)):
                wind_level = "typhoon"
            elif wind >= float(CFG.get("weather_strong_wind_threshold_kmh", 50.4)):
                wind_level = "strong"
        if wind_level == "typhoon":
            bonus += float(CFG.get("weather_typhoon_taxi_bonus", 0.0))
        elif wind_level == "strong":
            bonus += float(CFG.get("weather_strong_wind_taxi_bonus", 0.0))
        if not rainy and not cool and wind_level is None:
            bonus += float(CFG.get("weather_sunny_taxi_bonus", 0.0))
        # rl_env는 에피소드(reset)마다 매니저를 새로 만들므로, 프로세스당 1번만 출력
        if not PassengerSpawnManager._weather_logged:
            PassengerSpawnManager._weather_logged = True
            wind_label = {"typhoon": " (태풍급)", "strong": " (강풍급)"}.get(wind_level, "")
            print(f"[날씨 보정] 기온 {temp}°C / 강수 {precip}mm / 풍속 {wind if wind is not None else '-'}km/h"
                  f"{wind_label} -> 택시확률 {bonus:+.3f}")
        return bonus

    def _tp(self, p: float, category: str = None) -> float:
        """기본 택시확률 p에 날씨 가산값을 더한 뒤, 공휴일이면 카테고리 배율을 곱해 0~1로 자른 값.

        배율을 가산 뒤에 곱하므로 배율 0(예: 공휴일 등교)이면 날씨 가산이 있어도 확실히 0이 됨.
        평일이거나 category가 None이면 배율은 1.0 (기존 동작과 동일).
        """
        mult = self.holiday_mult.get(category, 1.0) if (self.is_holiday and category) else 1.0
        return min(1.0, max(0.0, (p + self.weather_bonus) * mult))

    # ---------- 기본 승객 수(num_passengers) 독립 가챠 ----------
    def _maintain_residential_gacha(self, now_seconds: float):
        if self._gacha_rate_per_sec is None:
            return
        
        cur_second = int(now_seconds)
        
        # [변경] 정확히 10초에 한 번씩만 실행되도록 조건 추가
        if cur_second % 10 != 0:
            return
            
        if cur_second == self._gacha_last_second:
            return  
        self._gacha_last_second = cur_second

        self._gacha_accumulator += self._gacha_rate_per_sec
        attempts = int(self._gacha_accumulator)
        self._gacha_accumulator -= attempts

        for _ in range(attempts):
            if random.random() < self._tp(self.residential_taxi_probability, "general"):
                start_e = self._pick_edge(self.all_edges)
                end_e = self._pick_edge(self.all_edges, exclude=start_e)
                self._spawn_person(now_seconds, start_e, end_e, dest_category=None)
                self.spawn_counts["residential_gacha"] += 1
    # ---------- 매 스텝 호출 ----------
    def maintain(self, now_seconds: float, timeout_removed_pids: set = None, current_pids: set = None):
        """current_pids: 메인 루프가 스텝 시작에 한 번 가져온 person ID set (pax_manager가 지운 id는 이미 빠져 있음).
        이 매니저가 새로 만든 사람은 _spawn_person()이 current_pids에 직접 추가해서,
        뒤에 오는 log_recorder가 같은 스텝에 새 승객을 보게 한다. None이면 직접 조회."""
        timeout_removed_pids = timeout_removed_pids or set()
        if current_pids is None:
            current_pids = set(traci.person.getIDList())
        self._current_pids = current_pids
        now_hour = self._current_hour(now_seconds) % 24

        self._process_arrivals(timeout_removed_pids, now_seconds, current_pids)
        self._maintain_morning(now_seconds, now_hour)
        self._maintain_lunch(now_seconds, now_hour)
        self._maintain_school_afternoon(now_seconds, now_hour)
        self._maintain_evening(now_seconds, now_hour)
        self._maintain_restaurant_timers(now_seconds)
        self._maintain_restaurant_civilian(now_seconds, now_hour)
        self._maintain_department(now_seconds, now_hour)
        self._maintain_residential_gacha(now_seconds)
        self._current_pids = None