import random
import traci
import traci.constants as tc

import module1_simulation.vehicle_subscriptions as vehicle_subscriptions
from collections import Counter

from config_loader import CFG

# 택시 매니저가 매 스텝 쓰는 값 (구독으로 스텝당 왕복 1번)
# 경로 전체(VAR_EDGES)는 순찰 택시에서 수천 개로 길어져 매 스텝 받으면 오히려 느려진다 → 필요할 때만 직접 조회
TAXI_SUBSCRIBE_VARS = (tc.LAST_STEP_PERSON_ID_LIST, tc.VAR_ROAD_ID, tc.VAR_ROUTE_INDEX)

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
        self._away_until = []   # [시나리오] 영역 밖 운행으로 빠진 택시들의 복귀 시각 (그동안 목표 대수에서 제외)
        # [KJS 이식] 막다른 길(들어가면 주 도로망으로 못 돌아오는 도로) 회피
        # 실제 계산은 TraCI 연결 후 첫 maintain()에서 1회 (_build_trap_edges)
        self.avoid_traps = bool(CFG.get("taxi_avoid_dead_ends", True))
        self._trap_edges = set()
        self._trap_built = False
        # [KJS 이식] 위치 기반 stuck 감지: {vid: (edge, 그 edge에 처음 보인 시각)}
        # sumo_time_to_teleport보다 짧게 잡아서 텔레포트(=맵 밖 소멸) 전에 선제 재경로
        self._pos_track = {}
        self.stuck_reroute_sec = float(CFG.get("taxi_stuck_reroute_sec",
                                               max(60.0, float(CFG.get("sumo_time_to_teleport", 120)) * 0.7)))
        # 배차 stop이 있는 택시 / 승객 탑승 택시에 changeTarget을 거는 기능은 SUMO taxi 예약(stop)과
        # 충돌할 수 있어 별도 스위치로 둔다 (KJS 원본은 항상 켜짐)
        self.extend_dispatch_route = bool(CFG.get("taxi_extend_dispatch_route", False))
        self.reroute_stuck_riders = bool(CFG.get("taxi_reroute_stuck_riders", False))

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

    def _build_trap_edges(self):
        """[KJS 이식] 주 도로망(all_edges)으로 되돌아올 수 없는 edge 집합을 계산한다 (매니저당 1회).

        SUMO의 idle-algorithm(randomCircling)과 _next_edge_fallback은 '아무 도로'로 보내기 때문에
        한 번 들어가면 못 나오는 막다른 도로(예: 출구 없는 골목)로 택시가 들어가 경로가 끝나 사라지는 문제가 있었다.
        계산 후 all_edges / boundary_edges / hotspot_edges / zones 후보에서도 trap edge를 뺀다
        (목적지·스폰 후보로 뽑혀 changeTarget 실패 → 소멸로 이어지는 것 방지).
        배차기(hungarian)는 SUMO 예약을 그대로 배정하므로 이 후보 목록을 쓰지 않는다.
        """
        self._trap_built = True
        self._trap_edges = set()
        if not self.avoid_traps:
            return
        try:
            main = set(self.all_edges)
            if not main:
                return
            net_edges = [e for e in traci.edge.getIDList() if not e.startswith(":")]
            preds = {}   # 다음 edge -> 그 edge로 들어올 수 있는 이전 edge들
            for edge_id in net_edges:
                try:
                    lane_count = traci.edge.getLaneNumber(edge_id)
                except traci.exceptions.TraCIException:
                    continue
                for lane_idx in range(lane_count):
                    try:
                        links = traci.lane.getLinks(f"{edge_id}_{lane_idx}")
                    except traci.exceptions.TraCIException:
                        continue
                    for link in links:
                        lane_id = link[0]
                        if not lane_id:
                            continue
                        nxt = lane_id.rsplit("_", 1)[0]
                        if nxt and not nxt.startswith(":") and nxt != edge_id:
                            preds.setdefault(nxt, set()).add(edge_id)
            # 주 도로망 edge에 도달할 수 있는 edge = 안전 (역방향 탐색). 나머지 = 막다른 길.
            safe = set(main)
            stack = list(main)
            while stack:
                cur = stack.pop()
                for p in preds.get(cur, ()):
                    if p not in safe:
                        safe.add(p)
                        stack.append(p)
            self._trap_edges = {e for e in net_edges if e not in safe}
            if not self._trap_edges:
                return
            print(f"[막다른 길] 주 도로망으로 못 돌아오는 도로 {len(self._trap_edges)}개 감지 -> 택시 진입 차단", flush=True)
            traps = self._trap_edges
            # 새 리스트로 바꿔 끼운다 (meta의 원본 리스트는 다른 모듈도 쓰므로 건드리지 않음)
            before = len(self.all_edges)
            self.all_edges = [e for e in self.all_edges if e not in traps] or self.all_edges
            self.boundary_edges = [e for e in self.boundary_edges if e not in traps] or self.boundary_edges
            self.hotspot_edges = [e for e in self.hotspot_edges if e not in traps] or self.hotspot_edges
            if self.zones:
                self.zones = {cat: [e for e in edges if e not in traps] for cat, edges in self.zones.items()}
            removed_n = before - len(self.all_edges)
            if removed_n:
                print(f"[막다른 길] 목적지 후보(all_edges)에서 {removed_n}개 제거", flush=True)
        except Exception as exc:
            if type(exc).__name__ == "FatalTraCIError":
                raise
            self._trap_edges = set()
            print(f"[막다른 길] 계산 실패, 회피 기능을 끕니다: {exc}", flush=True)

    def _redirect_from_trap(self, vid: str, current_edge: str) -> bool:
        """[KJS 이식] 경로 끝이 막다른 길인 (빈) 택시를 주 도로망 안의 목적지로 돌려놓는다."""
        for _ in range(self.target_pick_attempts):
            target = random.choice(self.all_edges)
            if target == current_edge or target.startswith(":") or target in self._trap_edges:
                continue
            try:
                route = traci.simulation.findRoute(current_edge, target, vType=self.vtype)
                if route and len(route.edges) > 0:
                    traci.vehicle.changeTarget(vid, target)
                    self.spawn_stats["trap_avoided"] += 1
                    return True
            except Exception as e:
                if type(e).__name__ == "FatalTraCIError":
                    raise
                continue
        return False

    def _update_pos_track(self, vid: str, now: float, edge: str):
        """[KJS 이식] vid의 현재 edge를 기록한다 (edge가 바뀔 때만 시각 갱신)."""
        prev = self._pos_track.get(vid)
        if prev is None or prev[0] != edge:
            self._pos_track[vid] = (edge, now)

    def _is_stuck(self, vid: str, now: float, current_edge: str) -> bool:
        """[KJS 이식] 같은 edge에 stuck_reroute_sec 이상 머물렀으면 True (텔레포트 전에 선제 재경로)."""
        if self.stuck_reroute_sec <= 0 or current_edge.startswith(":"):
            return False
        entry = self._pos_track.get(vid)
        if entry is None or entry[0] != current_edge:
            self._pos_track[vid] = (current_edge, now)
            return False
        return (now - entry[1]) >= self.stuck_reroute_sec

    @staticmethod
    def _stop_edge(stop) -> str:
        return stop.lane.rsplit("_", 1)[0]

    def _reroute_to_stop(self, vid: str, current_edge: str, stop_edge: str) -> bool:
        """현재 위치에서 stop edge로 가는 길이 있으면 changeTarget. 실패는 조용히 무시 (예약 충돌 방지)."""
        if (current_edge.startswith(":") or stop_edge.startswith(":") or stop_edge == current_edge
                or stop_edge in self._trap_edges):
            return False
        try:
            rte = traci.simulation.findRoute(current_edge, stop_edge, vType=self.vtype)
            if rte and len(rte.edges) > 0:
                traci.vehicle.changeTarget(vid, stop_edge)
                return True
        except Exception as e:
            if type(e).__name__ == "FatalTraCIError":
                raise
        return False

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
                last = self._last_info.get(vid)
                if last and last[1] and last[1] in self._trap_edges:
                    reason = "막다른 길 진입"
                elif vid in arrived:
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

    def send_away(self, vid: str, return_at: float):
        """택시를 운행에서 잠시 뺀다 (영역 밖으로 손님을 태우고 나간 시나리오). return_at이 지나면
        목표 대수가 회복되어 경계 도로에서 다시 투입된다."""
        try:
            traci.vehicle.remove(vid)
        except traci.exceptions.TraCIException:
            return
        self._our_removed.add(vid)
        self._removed_this_step.append(vid)
        self.fail_counts.pop(vid, None)
        self._away_until.append(float(return_at))
        self.spawn_stats["sent_away"] += 1

    def vanish_summary(self) -> str:
        return f"소멸 {dict(self.vanish_counts)} | 스폰 {dict(self.spawn_stats)}"

    def maintain(self, now_seconds: float = 0, current_vids: set = None):
        self._last_now = now_seconds
        try:
            current_ids = set(current_vids) if current_vids is not None else set(traci.vehicle.getIDList())
            self.fail_counts = {vid: c for vid, c in self.fail_counts.items() if vid in current_ids}
            self._type_cache = {v: ty for v, ty in self._type_cache.items() if v in current_ids}
            if self._pos_track:
                self._pos_track = {v: p for v, p in self._pos_track.items() if v in current_ids}

            if not self._trap_built:
                self._build_trap_edges()

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
            if self._away_until:
                self._away_until = [t for t in self._away_until if t > now_seconds]
            shortage = self.target_count - len(self._away_until) - (len(taxis_now) + len(pending))
            if shortage > 0:
                api = self._pending_ids()
                if api:
                    shortage -= len({v for v in api if v.startswith("taxi") and v not in self._spawned and v not in self._our_removed})
            for _ in range(min(max(0, shortage), self.max_spawn_per_step)):
                self._spawn_new_taxi()

            vehicle_subscriptions.forget(current_ids)
            vehicle_subscriptions.ensure(taxis_now, TAXI_SUBSCRIBE_VARS)
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
            if edge_id != current_edge and not edge_id.startswith(":") and edge_id not in self._trap_edges:
                nexts.append(edge_id)
        return random.choice(nexts) if nexts else None

    def _extend_dispatch_route(self, vid: str, res: dict, stops):
        """[KJS 이식] 배차 stop이 있는데 남은 경로에 stop edge가 없이 곧 끝나면 stop edge까지 경로를 연장한다.
        KJS 원본은 stops[0]으로 바꿨지만, 픽업 뒤 하차 stop이 남은 경우 경로가 꼬일 수 있어
        마지막 stop edge를 목적지로 쓴다 (SUMO는 재경로 때 남은 stop들을 경유한다).
        남은 경로가 이미 stop edge를 지나면 아무것도 하지 않는다 (매 스텝 불필요한 changeTarget 방지)."""
        try:
            route = traci.vehicle.getRoute(vid)
            route_idx = res[tc.VAR_ROUTE_INDEX] if tc.VAR_ROUTE_INDEX in res else traci.vehicle.getRouteIndex(vid)
            if len(route) - route_idx - 1 > self.remaining_edges_threshold:
                return
            stop_edge = self._stop_edge(stops[-1])
            if stop_edge in route[max(0, route_idx):]:
                return
            current_edge = res[tc.VAR_ROAD_ID] if tc.VAR_ROAD_ID in res else traci.vehicle.getRoadID(vid)
            if self._reroute_to_stop(vid, current_edge, stop_edge):
                self.spawn_stats["dispatch_route_extended"] += 1
        except traci.exceptions.TraCIException:
            pass

    def _maintain_stuck_rider(self, vid: str, now_seconds: float, res: dict):
        """[KJS 이식] 승객을 태운 택시도 같은 edge에 오래 서 있으면 하차 stop까지 경로를 다시 찾는다
        (승객을 태운 채 텔레포트·소멸 방지)."""
        try:
            current_edge = res[tc.VAR_ROAD_ID] if tc.VAR_ROAD_ID in res else traci.vehicle.getRoadID(vid)
            if current_edge.startswith(":") or not self._is_stuck(vid, now_seconds, current_edge):
                return
            stops = traci.vehicle.getStops(vid, 0)
            if stops and self._reroute_to_stop(vid, current_edge, self._stop_edge(stops[-1])):
                self.spawn_stats["passenger_stuck_rerouted"] += 1
                self._pos_track.pop(vid, None)  # 타이머 리셋
        except traci.exceptions.TraCIException:
            pass

    # [수정] 클래스 외부로 빠져나갔던 들여쓰기를 완벽 복구
    def _maintain_one(self, vid: str, now_seconds: float):
        # [성능] 탑승 승객·현재 도로·경로·경로 위치는 구독 값(직전 simulationStep 시점)을 쓴다 (vehicle_subscriptions.py).
        # 이 함수는 스텝 맨 앞에서 불리고, 같은 스텝에 이 택시의 상태를 바꾼 코드가 아직 없으므로 직접 묻는 값과 같다.
        res = vehicle_subscriptions.get(vid)
        persons = res[tc.LAST_STEP_PERSON_ID_LIST] if tc.LAST_STEP_PERSON_ID_LIST in res \
            else traci.vehicle.getPersonIDList(vid)
        if persons:
            self._last_info[vid] = ("승객 탑승", None, None)
            if self.reroute_stuck_riders:
                self._maintain_stuck_rider(vid, now_seconds, res)
            else:
                self._pos_track.pop(vid, None)   # 빈 택시가 됐을 때 예전 기록으로 stuck 오판하지 않게
            return

        try:
            stops = traci.vehicle.getStops(vid, 0)
            if stops:
                self._last_info[vid] = ("배차 stop 있음", None, None)
                self._pos_track.pop(vid, None)
                if self.extend_dispatch_route:
                    self._extend_dispatch_route(vid, res, stops)
                return
        except traci.exceptions.TraCIException:
            return

        current_edge = res[tc.VAR_ROAD_ID] if tc.VAR_ROAD_ID in res else traci.vehicle.getRoadID(vid)
        if current_edge.startswith(":"):
            return

        route = traci.vehicle.getRoute(vid)
        route_idx = res[tc.VAR_ROUTE_INDEX] if tc.VAR_ROUTE_INDEX in res else traci.vehicle.getRouteIndex(vid)
        remaining = len(route) - route_idx - 1
        self._last_info[vid] = ("빈 택시", current_edge, remaining)

        # [KJS 이식] 경로 끝이 막다른 길이고 아직 그 안에 들어가기 전이면 주 도로망 목적지로 바꾼다
        if (self._trap_edges and route and route[-1] in self._trap_edges
                and current_edge not in self._trap_edges):
            if self._redirect_from_trap(vid, current_edge):
                self._pos_track.pop(vid, None)
                return

        # [KJS 이식] 위치 기반 stuck 감지: remaining이 커도 같은 edge에 stuck_reroute_sec 이상 머물면 선제 재경로
        # → 텔레포트(sumo_time_to_teleport) 전에 미리 잡아서 맵 밖 소멸 차단.
        # RL 제어 중(rl_prepositioned)에는 이중 제어 방지를 위해 목적지를 바꾸지 않는다.
        if remaining > self.remaining_edges_threshold:
            if self.strategy != "rl_prepositioned" and self._is_stuck(vid, now_seconds, current_edge):
                new = self._pick_target_edge(now_seconds, current_edge) or self._next_edge_fallback(current_edge)
                if new and new != current_edge:
                    try:
                        rte = traci.simulation.findRoute(current_edge, new, vType=self.vtype)
                        if rte and len(rte.edges) > 0:
                            traci.vehicle.changeTarget(vid, new)
                            self.spawn_stats["stuck_rerouted"] += 1
                            self._pos_track.pop(vid, None)  # 타이머 리셋
                    except Exception as e:
                        if type(e).__name__ == "FatalTraCIError":
                            raise
            return
        self._update_pos_track(vid, now_seconds, current_edge)

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