"""시간대별 수요 생성. 유형별 난수와 성공/실패 집계를 분리한다."""
import math
import random
from collections import Counter
import traci
from config_loader import CFG


class PassengerSpawnManager:
    def __init__(self, zones, sim_start_hour, sim_end_hour=None,
                 school_pop_base=None, company_pop_base=None, seed=None, config=None):
        self.cfg = CFG if config is None else config
        self.sim_start_hour = sim_start_hour
        self.school_pop_base = int(self.cfg.get("school_pop_base", 400) if school_pop_base is None else school_pop_base)
        self.company_pop_base = int(self.cfg.get("company_pop_base", 100) if company_pop_base is None else company_pop_base)
        self.school_start_hour = float(self.cfg.get("school_start_hour", 7.0))
        self.school_end_hour = float(self.cfg.get("school_end_hour", 8.0))
        self.school_taxi_peak_hour = float(self.cfg.get("school_taxi_peak_hour", self.school_end_hour))
        self.school_afternoon_start_hour = float(self.cfg.get("school_afternoon_start_hour", 16.0))
        self.school_afternoon_end_hour = float(self.cfg.get("school_afternoon_end_hour", 17.0))
        self.school_afternoon_fraction = float(self.cfg.get("school_afternoon_fraction", 0.01))
        self.company_start_hour = float(self.cfg.get("company_start_hour", 8.0))
        self.company_end_hour = float(self.cfg.get("company_end_hour", 10.0))
        self.company_taxi_peak_hour = float(self.cfg.get("company_taxi_peak_hour", self.company_end_hour))
        self.lunch_start_hour = float(self.cfg.get("lunch_start_hour", 12.0))
        self.lunch_end_hour = float(self.cfg.get("lunch_end_hour", 13.0))
        self.lunch_company_release_fraction = float(self.cfg.get("lunch_company_release_fraction", 0.80))
        self.lunch_taxi_fraction = float(self.cfg.get("lunch_taxi_fraction", 0.10))
        self.evening_start_hour = float(self.cfg.get("evening_start_hour", 18.0))
        self.evening_end_hour = float(self.cfg.get("evening_end_hour", 22.0))
        self.evening_taxi_fraction = float(self.cfg.get("evening_taxi_fraction", 0.30))
        self.late_evening_start_hour = float(self.cfg.get("late_evening_start_hour", 23.0))
        self.late_evening_end_hour = float(self.cfg.get("late_evening_end_hour", 24.0))
        self.evening_residential_fraction = float(self.cfg.get("evening_residential_fraction", 25/30))
        self.restaurant_stay_sec = float(self.cfg.get("restaurant_stay_sec", 3600.0))
        # 음식점 방문 시민 스폰 (버스정류장/지하철입구 -> 음식점)
        self.restaurant_pop_base = float(self.cfg.get("restaurant_pop_base", 60))
        self.restaurant_window1_start = float(self.cfg.get("restaurant_window1_start", 12.0))
        self.restaurant_window1_end = float(self.cfg.get("restaurant_window1_end", 14.0))
        self.restaurant_window2_start = float(self.cfg.get("restaurant_window2_start", 17.0))
        self.restaurant_window2_end = float(self.cfg.get("restaurant_window2_end", 22.0))
        self.restaurant_civilian_taxi_probability = float(self.cfg.get("restaurant_civilian_taxi_probability", 0.10))
        self._restaurant_civilian_accumulator = 0.0
        self._restaurant_civilian_last_second = -1

        self.sim_end_hour = sim_end_hour if sim_end_hour is not None else 24
        self.duration = (self.sim_end_hour - sim_start_hour) * 3600
        if self.duration <= 0:
            raise ValueError("시뮬레이션 종료는 시작보다 늦어야 합니다.")
        self.num_passengers = int(self.cfg.get("num_passengers", 1000))
        self.residential_taxi_probability = float(self.cfg.get("residential_taxi_probability", .1))
        self.enabled = self.cfg.get("dynamic_passengers", True)
        self.zones = {k: sorted(set(v)) for k, v in zones.items()}
        self.all_edges = sorted({e for values in zones.values() for e in values})
        # 실제 구역 수와 경로 선택용 폴백을 구분한다.
        self.company_count = len(self.zones.get("company", []))
        self.restaurant_count = len(self.zones.get("restaurant", []))
        for name in ("school", "company", "restaurant", "residential"):
            setattr(self, name + "_edges", self.zones.get(name) or self.all_edges)
        self.transit_edges = sorted(set(self.zones.get("subway_entrance", []) + self.zones.get("bus_stop", []))) or self.all_edges
        self.absorbed = Counter()
        self.spawn_counts = Counter()
        self.attempt_counts = Counter()
        self.passed_counts = Counter()
        self.failed_counts = Counter()
        self.total_spawned = self.total_timeout = 0
        self.records = {}
        self.new_records = []
        self.failures = []
        self._dest_category = {}
        self._is_lunch_cycle = set()
        self._prev_active_ids = set()
        self._restaurant_pending = []
        self._counter = 0
        self._last_second = -1
        self._acc = Counter()
        self._rngs = {}
        self._seed = seed if seed is not None else random.SystemRandom().getrandbits(64)
        self._school_pool = self._evening_pool = None
        self._school_used = self._evening_used = 0
        self._lunch_done = sim_start_hour > self.lunch_start_hour
        self._return_done = sim_start_hour > self.lunch_end_hour
        self._late_done = sim_start_hour > self.late_evening_start_hour
        self._lunch_arrived = 0

    def _rng(self, kind):
        # Python hash 대신 고정 문자열 시드를 써 프로세스 간에도 재현한다.
        if kind not in self._rngs:
            self._rngs[kind] = random.Random(f"{self._seed}:{kind}")
        return self._rngs[kind]

    def _attempts(self, kind, rate):
        self._acc[kind] += max(0, rate)
        n = math.floor(self._acc[kind] + 1e-9)
        self._acc[kind] -= n
        return n

    def _spawn_person(self, now_seconds, start_edge, end_edge, dest_category=None,
                      lunch_cycle=False, kind="residential_gacha"):
        self._counter += 1
        pid = f"dyn_pax_{self._counter}"
        try:
            traci.person.add(pid, start_edge, pos=0, depart=now_seconds)
            traci.person.appendDrivingStage(pid, end_edge, lines="taxi")
        except traci.exceptions.TraCIException as exc:
            # add 성공 후 stage 실패 시 고아 person을 남기지 않는다.
            try:
                traci.person.remove(pid)
            except traci.exceptions.TraCIException:
                pass
            self.failed_counts[kind] += 1
            self.failures.append({"person_id": pid, "time_sec": now_seconds, "kind": kind, "error": str(exc)})
            return None
        self.total_spawned += 1
        self.spawn_counts[kind] += 1
        self.records[pid] = {"person_id": pid, "depart": now_seconds, "from_edge": start_edge,
                             "to_edge": end_edge, "demand_type": kind}
        self.new_records.append(self.records[pid])
        if dest_category:
            self._dest_category[pid] = dest_category
        if lunch_cycle:
            self._is_lunch_cycle.add(pid)
        return pid

    def _draw(self, kind, n, probability, now, origins, destinations, category=None, lunch=False):
        rng = self._rng(kind)
        self.attempt_counts[kind] += n
        for _ in range(n):
            if rng.random() >= probability:
                continue
            self.passed_counts[kind] += 1
            if not origins or not destinations:
                self.failed_counts[kind] += 1
                self.failures.append({"time_sec": now, "kind": kind, "error": "empty_edge_pool"})
                continue
            source = rng.choice(origins)
            choices = [e for e in destinations if e != source]
            if not choices:
                self.failed_counts[kind] += 1
                self.failures.append({"time_sec": now, "kind": kind, "error": "no_distinct_destination"})
                continue
            self._spawn_person(now, source, rng.choice(choices), category, lunch, kind)

    def _process_arrivals(self, timeout_removed_pids, now_seconds):
        current = set(traci.person.getIDList())
        # 단순 소실은 도착이 아니다. SUMO의 실제 도착 이벤트만 흡수한다.
        arrived = set(traci.simulation.getArrivedPersonIDList())
        for pid in sorted((self._prev_active_ids - current) | arrived):
            category = self._dest_category.pop(pid, None)
            lunch = pid in self._is_lunch_cycle
            self._is_lunch_cycle.discard(pid)
            if pid in timeout_removed_pids:
                self.total_timeout += 1
            elif category and pid in arrived:
                if lunch and category == "restaurant":
                    self._lunch_arrived += 1
                else:
                    self.absorbed[category] += 1
                if category == "restaurant" and not lunch:
                    self._restaurant_pending.append(now_seconds + self.restaurant_stay_sec)
        self._prev_active_ids = current

    @staticmethod
    def taxi_probability(hour, start, end, peak):
        peak = min(max(peak, start), end)
        return 0.0 if hour <= start else min(1.0, (hour - start) / max(peak - start, 1e-9))

    def maintain(self, now_seconds, timeout_removed_pids=None):
        self._process_arrivals(timeout_removed_pids or set(), now_seconds)
        second = int(now_seconds)
        if not self.enabled or second == self._last_second or not 0 <= now_seconds < self.duration:
            return
        self._last_second = second
        h = self.sim_start_hour + now_seconds / 3600
        # 모든 시간대의 한 초당 시도를 한 번만 처리한다.
        for kind, base, start, end, peak, dest, category in (
            ("school_morning", self.school_pop_base, self.school_start_hour, self.school_end_hour,
             self.school_taxi_peak_hour, self.school_edges, "school"),
            ("company_morning", self.company_pop_base * self.company_count, self.company_start_hour,
             self.company_end_hour, self.company_taxi_peak_hour, self.company_edges, "company")):
            if start <= h < end:
                n = self._attempts(kind, base / ((end - start) * 3600))
                self._draw(kind, n, self.taxi_probability(h, start, end, peak), now_seconds,
                           self.transit_edges, dest, category)
        if not self._lunch_done and h >= self.lunch_start_hour:
            self._lunch_done = True
            n = int(int(self.absorbed["company"] * self.lunch_company_release_fraction) * self.lunch_taxi_fraction)
            self.absorbed["company"] -= n
            self._draw("lunch_release", n, 1, now_seconds, self.company_edges, self.restaurant_edges, "restaurant", True)
        if not self._return_done and h >= self.lunch_end_hour:
            self._return_done = True
            # 음식점에 도착한 사람만 복귀시켜 인구를 복제하지 않는다.
            self._draw("lunch_return", self._lunch_arrived, 1, now_seconds,
                       self.restaurant_edges, self.company_edges, "company", True)
        if self.school_afternoon_start_hour <= h < self.school_afternoon_end_hour:
            if self._school_pool is None:
                self._school_pool = int(self.absorbed["school"] * self.school_afternoon_fraction)
            rate = self._school_pool / ((self.school_afternoon_end_hour - self.school_afternoon_start_hour) * 3600)
            n = min(self._school_pool - self._school_used, self._attempts("school_afternoon", rate))
            self._school_used += n
            self._draw("school_afternoon", n, 1, now_seconds, self.school_edges, self.residential_edges, "residential")
        if self.evening_start_hour <= h < self.late_evening_end_hour:
            if self._evening_pool is None:
                self._evening_pool = self.absorbed["company"]
            n, probability = 0, self.evening_taxi_fraction
            if h < self.evening_end_hour:
                rate = self._evening_pool / ((self.evening_end_hour - self.evening_start_hour) * 3600)
                n = min(self._evening_pool - self._evening_used, self._attempts("evening", rate))
            elif h >= self.late_evening_start_hour and not self._late_done:
                self._late_done = True
                n, probability = self._evening_pool - self._evening_used, 1
            self._evening_used += n
            for _ in range(n):
                home = self._rng("evening_destination").random() < self.evening_residential_fraction
                self._draw("evening", 1, probability, now_seconds, self.company_edges,
                           self.residential_edges if home else self.restaurant_edges, "residential" if home else "restaurant")
        due = [t for t in self._restaurant_pending if t <= now_seconds]
        self._restaurant_pending = [t for t in self._restaurant_pending if t > now_seconds]
        self._draw("restaurant_return", len(due), 1, now_seconds, self.restaurant_edges, self.residential_edges, "residential")
        if (self.restaurant_window1_start <= h < self.restaurant_window1_end or
                self.restaurant_window2_start <= h < self.restaurant_window2_end):
            seconds = ((self.restaurant_window1_end - self.restaurant_window1_start) +
                       (self.restaurant_window2_end - self.restaurant_window2_start)) * 3600
            n = self._attempts("restaurant_civilian", self.restaurant_pop_base * self.restaurant_count / seconds)
            self._draw("restaurant_civilian", n, self.restaurant_civilian_taxi_probability,
                       now_seconds, self.transit_edges, self.restaurant_edges, "restaurant")
        n = self._attempts("residential_gacha", self.num_passengers / self.duration)
        self._draw("residential_gacha", n, self.residential_taxi_probability, now_seconds, self.all_edges, self.all_edges)
