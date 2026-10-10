import random
import traci

from collections import Counter

from config_loader import CFG

CATEGORY_PEAK_HOURS = {
    "company": [CFG.get("lunch_start_hour", 12.0), CFG.get("evening_start_hour", 18.0), CFG.get("late_evening_start_hour", 23.0)],
    "school": [CFG.get("school_afternoon_start_hour", 16.0)],
    "residential": [CFG.get("school_start_hour", 7.0), CFG.get("evening_start_hour", 18.0), CFG.get("late_evening_start_hour", 23.0)],
    "restaurant": [CFG.get("lunch_start_hour", 12.0), CFG.get("evening_start_hour", 18.0)],
    "subway_entrance": [CFG.get("school_start_hour", 7.0), CFG.get("company_start_hour", 8.0), CFG.get("evening_start_hour", 18.0)],
    "bus_stop": [CFG.get("school_start_hour", 7.0), CFG.get("company_start_hour", 8.0), CFG.get("evening_start_hour", 18.0)],
}

class TaxiFleetManager:
    def __init__(self, target_count: int, boundary_edges: list, all_edges: list,
                 vtype: str = "taxi_type", strategy: str = "patrol",
                 hotspot_edges: list = None, zones: dict = None,
                 sim_start_hour: float = 0, remaining_edges_threshold: int = None, **kwargs):
        self.target_count = target_count
        self.boundary_edges = boundary_edges or all_edges
        self.all_edges = all_edges
        self.vtype = vtype
        self.strategy = strategy
        self.hotspot_edges = hotspot_edges if hotspot_edges else all_edges
        self.zones = zones or {}
        self.sim_start_hour = sim_start_hour
        self.remaining_edges_threshold = (CFG.get("taxi_remaining_edges_threshold", 2)
                                          if remaining_edges_threshold is None else remaining_edges_threshold)
        self.primary_prob = float(CFG.get("taxi_primary_prob", 0.70))
        self.fail_threshold = int(CFG.get("taxi_fail_threshold", 5))
        self.target_pick_attempts = int(CFG.get("taxi_target_pick_attempts", 10))
        self.respawn_prefix = str(CFG.get("taxi_respawn_prefix", "taxi_respawn"))

        self.fail_counts = {}
        self._respawn_counter = 0

        self.max_spawn_per_step = int(CFG.get("taxi_max_spawn_per_step", 5))
        self.spawn_pending_grace_sec = float(CFG.get("taxi_spawn_pending_grace_sec", 30))
        self.min_spawn_lane_len = float(CFG.get("taxi_min_spawn_lane_len", 15.0))
        self.rl_keepalive = bool(CFG.get("taxi_rl_keepalive", True))
        self._spawned = {}
        self._our_removed = set()
        self._type_cache = {}
        self._lane_len_cache = {}
        self._known_taxis = set()
        self._last_info = {}
        self.vanish_counts = Counter()
        self.spawn_stats = Counter()
        self._vanish_printed = 0
        self._last_summary = 0.0
        self._last_now = 0.0
        self._removed_this_step = []
        self.warmup_sec = float(CFG.get("taxi_warmup_sec", 10))
        self.lock_initial = bool(CFG.get("taxi_lock_initial_count", False))
        self._init_reported = False
        self._pending_cache = (None, None)

    def _current_hour(self, now_seconds: float) -> float:
        return self.sim_start_hour + (now_seconds / 3600.0)

    def _category_busyness_ranking(self, now_seconds: float) -> list:
        now_hour = self._current_hour(now_seconds) % 24
        scored = []
        for category, peak_hours in CATEGORY_PEAK_HOURS.items():
            min_diff = min(abs((now_hour - p + 12) % 24 - 12) for p in peak_hours)
            score = 3.0 - min_diff
            scored.append((category, score))
        scored.sort(key=lambda x: x[1], reverse=True)
        return [cat for cat, _ in scored]

    def _current_hotspot_pools(self, now_seconds: float):
        if not self.zones:
            return self.hotspot_edges, self.hotspot_edges
        ranking = self._category_busyness_ranking(now_seconds)
        primary_pool, secondary_pool = [], []
        idx = 0
        while idx < len(ranking) and not primary_pool:
            primary_pool = self.zones.get(ranking[idx], [])
            idx += 1
        while idx < len(ranking) and not secondary_pool:
            secondary_pool = self.zones.get(ranking[idx], [])
            idx += 1
        if not primary_pool:
            primary_pool = self.hotspot_edges or self.all_edges
        if not secondary_pool:
            secondary_pool = primary_pool
        return primary_pool, secondary_pool

    def _pick_target_edge(self, now_seconds: float, current_edge: str) -> str:
        if self.strategy == "prepositioned":
            primary_pool, secondary_pool = self._current_hotspot_pools(now_seconds)
            pool = primary_pool if random.random() < self.primary_prob else secondary_pool
        else:
            pool = self.all_edges

        for _ in range(self.target_pick_attempts):
            candidate = random.choice(pool)
            if candidate == current_edge or candidate.startswith(":"):
                continue
            try:
                route = traci.simulation.findRoute(current_edge, candidate, vType=self.vtype)
                if route and len(route.edges) > 0:
                    return candidate
            except Exception:
                continue
        return None

    def _respawn_taxi(self, old_vid: str):
        try:
            traci.vehicle.remove(old_vid)
        except traci.exceptions.TraCIException:
            pass
        self._our_removed.add(old_vid)
        self._removed_this_step.append(old_vid)
        self.fail_counts.pop(old_vid, None)
        self.spawn_stats["respawn_isolated"] += 1
        if self.spawn_stats["respawn_isolated"] <= 10:
            print(f"[택시 재생성] t={self._last_now:.0f}s id={old_vid} (목적지 {self.fail_threshold}회 연속 실패 -> 제거 후 재스폰)", flush=True)
        self._spawn_new_taxi()

    def _edge_ok_for_spawn(self, edge_id: str) -> bool:
        if edge_id in self._lane_len_cache:
            return self._lane_len_cache[edge_id]
        try:
            ok = traci.lane.getLength(edge_id + "_0") >= self.min_spawn_lane_len
        except traci.exceptions.TraCIException:
            ok = True
        self._lane_len_cache[edge_id] = ok
        return ok

    def _spawn_new_taxi(self):
        now = self._last_now
        candidates = list(self.boundary_edges)
        random.shuffle(candidates)
        tried = 0
        for start_edge in candidates:
            if tried >= 2:  # [최적화 1] 헛발질 재시도를 5회에서 2회로 줄여 랙 방지
                break
            if start_edge.startswith(":") or not self._edge_ok_for_spawn(start_edge):
                continue
            tried += 1
            others = [e for e in self.all_edges if e != start_edge and not e.startswith(":")]
            if not others:
                continue
            end_edge = random.choice(others)
            try:
                route = traci.simulation.findRoute(start_edge, end_edge, vType=self.vtype)
                if route and len(route.edges) > 1:
                    self._respawn_counter += 1
                    new_vid = f"{self.respawn_prefix}_{self._respawn_counter}"
                    route_id = f"route_{new_vid}"
                    traci.route.add(route_id, list(route.edges))
                    traci.vehicle.add(new_vid, route_id, typeID=self.vtype, departPos="random_free")
                    self._spawned[new_vid] = now
                    self.spawn_stats["spawned"] += 1
                    return new_vid
            except Exception as e:
                if type(e).__name__ == "FatalTraCIError":
                    raise
        self.spawn_stats["spawn_route_fail"] += 1
        return None

    def _is_taxi(self, vid: str) -> bool:
        t = self._type_cache.get(vid)
        if t is None:
            try:
                t = traci.vehicle.getTypeID(vid)
            except traci.exceptions.TraCIException:
                return False
            self._type_cache[vid] = t
        return t == self.vtype

    def _update_spawned(self, now: float, current_ids: set) -> set:
        if not self._spawned:
            return set()
        for vid in [v for v in self._spawned if v in current_ids]:
            del self._spawned[vid]
        if not self._spawned:
            return set()
        api_pending = self._pending_ids()
        pending = set()
        for vid, t0 in list(self._spawned.items()):
            waited = now - t0
            still = (vid in api_pending) if api_pending is not None else (waited < self.spawn_pending_grace_sec)
            if not still:
                del self._spawned[vid]
                self.spawn_stats["lost_after_add"] += 1
            elif waited >= self.spawn_pending_grace_sec:
                try:
                    traci.vehicle.remove(vid)
                except traci.exceptions.TraCIException:
                    pass
                self._our_removed.add(vid)
                del self._spawned[vid]
                self.spawn_stats["stuck_pending_dropped"] += 1
            else:
                pending.add(vid)
        return pending

    def _pending_ids(self):
        now = self._last_now
        if self._pending_cache[0] == now:
            return self._pending_cache[1]
        try:
            ids = set(traci.simulation.getPendingVehicles())
        except Exception:
            ids = None
        self._pending_cache = (now, ids)
        return ids

    @staticmethod
    def _safe_ids(func_name: str) -> set:
        try:
            return set(getattr(traci.simulation, func_name)())
        except Exception:
            return set()

    def _track_vanished(self, now: float, taxis_now: set):
        vanished = self._known_taxis - taxis_now
        ours = vanished & self._our_removed
        vanished -= ours
        self._our_removed -= ours
        if len(self._our_removed) > 2000:
            self._our_removed.clear()
        if vanished:
            arrived = self._safe_ids("getArrivedIDList")
            colliding = self._safe_ids("getCollidingVehiclesIDList")
            for vid in sorted(vanished):
                if vid in arrived:
                    reason = "경로 끝 도착"
                elif vid in colliding:
                    reason = "충돌"
                else:
                    reason = "불명(텔레포트/제거/관측 누락)"
                self.vanish_counts[reason] += 1
                if self._vanish_printed < 20:
                    self._vanish_printed += 1
                    info = self._last_info.get(vid)
                    print(f"[택시 소멸] t={now:.0f}s id={vid} 사유={reason} 직전상태={info}", flush=True)
        for vid in vanished | ours:
            self._last_info.pop(vid, None)
        self._known_taxis = set(taxis_now)
        if self.vanish_counts and now - self._last_summary >= 600:
            self._last_summary = now
            print(f"[택시 진단] t={now:.0f}s 소멸 누계 {dict(self.vanish_counts)} | 스폰 {dict(self.spawn_stats)} | 살아있음 {len(taxis_now)}/{self.target_count}", flush=True)

    def vanish_summary(self) -> str:
        return f"소멸 {dict(self.vanish_counts)} | 스폰 {dict(self.spawn_stats)}"

    def maintain(self, now_seconds: float = 0, current_vids: set = None):
        self._last_now = now_seconds
        try:
            current_ids = set(current_vids) if current_vids is not None else set(traci.vehicle.getIDList())
            self.fail_counts = {vid: c for vid, c in self.fail_counts.items() if vid in current_ids}
            self._type_cache = {v: ty for v, ty in self._type_cache.items() if v in current_ids}

            taxis_now = {vid for vid in current_ids if self._is_taxi(vid)}
            self._track_vanished(now_seconds, taxis_now)

            if not self._init_reported and now_seconds >= self.warmup_sec and taxis_now:
                self._init_reported = True
                n_spawned = sum(1 for v in taxis_now if v.startswith(self.respawn_prefix))
                print(f"[택시 초기 대수] t={now_seconds:.0f}s 설정 {self.target_count} / 실제 {len(taxis_now)} "
                      f"(처음 배치 {len(taxis_now) - n_spawned} + 매니저 스폰 {n_spawned})"
                      + (" -> 실제 대수로 목표 고정" if self.lock_initial else ""), flush=True)
                if self.lock_initial:
                    self.target_count = len(taxis_now)

            pending = self._update_spawned(now_seconds, current_ids)
            shortage = self.target_count - (len(taxis_now) + len(pending))
            if shortage > 0:
                api = self._pending_ids()
                if api:
                    shortage -= len({v for v in api if v.startswith("taxi") and v not in self._spawned and v not in self._our_removed})
            for _ in range(min(max(0, shortage), self.max_spawn_per_step)):
                self._spawn_new_taxi()

            for vid in taxis_now:
                try:
                    self._maintain_one(vid, now_seconds)
                except traci.exceptions.TraCIException:
                    continue
        except traci.exceptions.TraCIException:
            pass
        finally:
            if self._removed_this_step:
                if current_vids is not None:
                    current_vids.difference_update(self._removed_this_step)
                self._removed_this_step = []

    def _next_edge_fallback(self, current_edge: str):
        try:
            links = traci.lane.getLinks(current_edge + "_0")
        except traci.exceptions.TraCIException:
            return None
        nexts = []
        for link in links:
            lane_id = link[0]
            if not lane_id or lane_id.startswith(":"):
                continue
            edge_id = lane_id.rsplit("_", 1)[0]
            if edge_id != current_edge and not edge_id.startswith(":"):
                nexts.append(edge_id)
        return random.choice(nexts) if nexts else None

    # [수정] 클래스 외부로 빠져나갔던 들여쓰기를 완벽 복구
    def _maintain_one(self, vid: str, now_seconds: float):
        if traci.vehicle.getPersonIDList(vid):
            self._last_info[vid] = ("승객 탑승", None, None)
            return

        try:
            if traci.vehicle.getStops(vid, 0):
                self._last_info[vid] = ("배차 stop 있음", None, None)
                return
        except traci.exceptions.TraCIException:
            return

        current_edge = traci.vehicle.getRoadID(vid)
        if current_edge.startswith(":"):
            return

        route = traci.vehicle.getRoute(vid)
        route_idx = traci.vehicle.getRouteIndex(vid)
        remaining = len(route) - route_idx - 1
        self._last_info[vid] = ("빈 택시", current_edge, remaining)

        if remaining > self.remaining_edges_threshold:
            return

        # [최적화 2] RL이 제어 중일 땐 매니저가 새 목적지를 주지 않고(이중 제어 방지), 오직 1칸 생존 연장만 허용
        if self.strategy == "rl_prepositioned":
            if not (self.rl_keepalive and remaining <= 0):
                return
            new_target = self._next_edge_fallback(current_edge)
            if new_target:
                self.spawn_stats["fallback_next_edge"] += 1
        else:
            new_target = self._pick_target_edge(now_seconds, current_edge)
            if not new_target or new_target == current_edge:
                new_target = self._next_edge_fallback(current_edge)
                if new_target:
                    self.spawn_stats["fallback_next_edge"] += 1

        if not new_target or new_target == current_edge:
            self.fail_counts[vid] = self.fail_counts.get(vid, 0) + 1
            if self.fail_counts[vid] >= self.fail_threshold:
                self._respawn_taxi(vid)
            return

        try:
            if traci.vehicle.isStopped(vid):
                traci.vehicle.resume(vid)
        except traci.exceptions.TraCIException:
            pass

        # [최적화 3] 도달 가능한 도로인지 확인 후 명령을 내려 에러 도배(unreachable) 완벽 차단
        try:
            route_check = traci.simulation.findRoute(current_edge, new_target, vType=self.vtype)
            if route_check and len(route_check.edges) > 0:
                traci.vehicle.changeTarget(vid, new_target)
                self.fail_counts[vid] = 0
            else:
                self.fail_counts[vid] = self.fail_counts.get(vid, 0) + 1
        except Exception:
            self.fail_counts[vid] = self.fail_counts.get(vid, 0) + 1

        if self.fail_counts.get(vid, 0) >= self.fail_threshold:
            self._respawn_taxi(vid)