"""
module4_dispatch/rl_env.py

목적: 승객-택시 "매칭"은 GUI에서 고른 기존 방식(greedy 또는 Hungarian)이 담당하고,
      RL은 그 위에서 "유휴 택시를 어느 zone(카테고리)으로 미리 보내놓을지"만 결정한다.
"""

import os
import random
import numpy as np
import traci
import module1_simulation.vehicle_subscriptions as vehicle_subscriptions
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

import gymnasium as gym
from gymnasium import spaces

from module1_simulation.taxi_manager import TaxiFleetManager, CATEGORY_PEAK_HOURS
from module1_simulation.passenger_manager import PassengerTimeoutManager
from module1_simulation.passenger_spawn_manager import PassengerSpawnManager
from config_loader import CFG
from module1_simulation.holiday_utils import pick_run_date
from module4_dispatch.custom_dispatchers import make_dispatcher
from module4_dispatch.forecast_dispatcher import weighted_edge_order
from module2_preprocessing.poi_extractor import CATEGORIES as ZONE_CATEGORIES

N_ZONES = len(ZONE_CATEGORIES)
DECISION_INTERVAL_SEC = 60.0


def _has_no_pending_stop(taxi_id: str) -> bool:
    try:
        return len(traci.vehicle.getStops(taxi_id, 1)) == 0
    except traci.exceptions.TraCIException:
        return False


class TaxiRepositionEnv(gym.Env):
    metadata = {"render_modes": []}

    def __init__(
        self,
        sumo_cfg_path: str,
        meta: dict,
        demand_predictor=None,
        max_decisions: int = 360,
        sumo_binary: str = "sumo",
        incentive_weight: float = 0.0,
        target_hotspot_ratio: float = 0.5,
        randomize_passenger_seed: bool = False,
        training_seed_base: int = 1000,
        worker_index: int = 0,
    ):
        super().__init__()

        self.sumo_cfg_path = sumo_cfg_path
        self.meta = meta
        self.zones = meta["zones"]
        self.demand_predictor = demand_predictor
        self.max_decisions = max_decisions
        self.sumo_binary = sumo_binary
        self.incentive_weight = incentive_weight
        self.target_hotspot_ratio = target_hotspot_ratio
        self.randomize_passenger_seed = randomize_passenger_seed
        self.training_seed_base = training_seed_base + worker_index * 100_000
        # [KJS] 보상 항목별 진단 로그 (10분마다 콘솔 + training_logs/reward_breakdown.csv)
        self.worker_index = worker_index
        self._log_file_path = os.path.join(PROJECT_ROOT, "module4_dispatch", "training_logs", "reward_breakdown.csv")
        self._interval_stats = {
            "pickups": 0.0, "r_pickup": 0.0,
            "timeouts": 0.0, "r_timeout": 0.0,
            "waiting_sum": 0.0, "r_wait": 0.0,
            "match_sum": 0.0, "r_match": 0.0,
            "dist_m": 0.0, "r_dist": 0.0,
            "total_reward": 0.0, "steps": 0.0,
        }

        self.observation_space = spaces.Box(
            low=0, high=np.inf, shape=(N_ZONES * 3 + 1,), dtype=np.float32,
        )
        self.action_space = spaces.MultiDiscrete(np.full(N_ZONES, 4, dtype=np.int64))
        self._edge_to_zone = {}

        for cat, edges in self.zones.items():
            for edge in edges:
                self._edge_to_zone[edge] = cat

        self.dispatcher = None
        self.fleet_manager = None
        self.pax_manager = None
        self.spawn_manager = None
        self.signal_ctrl = None      # 평가(measure_wait_time.py)와 같은 도로 현실화: 실제 신호 계획
        self.speed_ctrl = None       # 〃 시간대별 실측 속도 보정 (TOPIS)
        self._decision_count = 0
        self._sumo_started = False
        self._depart_time = {}
        self._unreachable_edges = set()
        self._last_reposition_distance_m = 0.0
        self._episode_index = 0
        self._seen_request_ids = set()
        self.committed = {}          # [KJS] 재배치 명령을 받고 아직 이동 중인 택시 {taxi_id: 목표 edge}
        self._last_riding = set()    # [KJS] 직전 결정 시점의 탑승 승객 (매 스텝 중복 조회 방지)
        # 새 호출을 카테고리 수요로 셀 때의 기준 = 학습과 같은 '승차 위치 -> 가장 가까운 POI 구역'
        self._edge_weights = {}       # 재배치 목표 도로 가중치 {도로: 과거 호출 수} (reset에서 채움)
        self._picked_targets = set()  # 이번 결정에서 이미 택시를 보낸 도로
        self._request_category = {}
        if demand_predictor is not None and hasattr(demand_predictor, "record_new_requests"):
            from module3_prediction.category_predictor import edge_request_categories
            self._request_category = edge_request_categories(os.path.dirname(os.path.abspath(sumo_cfg_path)))

    def _pick_episode_date(self):
        """학습 에피소드 날짜. rl_train_start~rl_train_end가 있으면 그 기간 안에서만 뽑는다
        (평가 날짜(sim_date 이후)와 겹치지 않게 시간 순서로 분리 — 수요예측 학습 기간과 같은 8주).
        [KJS] rl_train_day_type: "weekday"(기본)=평일만, "holiday"=공휴일·주말만, "mixed"=둘 다.
        평일/공휴일이 섞이면 에피소드 보상 분산의 대부분이 '그날이 무슨 날이냐'로 결정돼 정책 개선이 안 보인다."""
        import datetime as _dt
        from module1_simulation.holiday_utils import is_holiday_date
        rng = random.Random(self.training_seed_base + self._episode_index)
        day_type = str(CFG.get("rl_train_day_type", "weekday")).lower()
        start, end = CFG.get("rl_train_start"), CFG.get("rl_train_end")
        if start and end:
            d0, d1 = _dt.date.fromisoformat(str(start)), _dt.date.fromisoformat(str(end))
            days = [d0 + _dt.timedelta(days=i) for i in range((d1 - d0).days + 1)]
            if day_type in ("weekday", "holiday"):
                want = day_type == "holiday"
                days = [d for d in days if bool(is_holiday_date(d)) == want] or days
            return rng.choice(days)
        if day_type == "weekday":
            return pick_run_date(CFG, index=2 * self._episode_index, rng=rng, mode="alternate")
        if day_type == "holiday":
            return pick_run_date(CFG, index=2 * self._episode_index + 1, rng=rng, mode="alternate")
        return pick_run_date(CFG, index=self._episode_index, rng=rng)

    def reset(self, seed=None, options=None):
        super().reset(seed=seed)
        self._close_sumo()

        vehicle_subscriptions.reset()   # 새 SUMO 실행: 구독 기록 초기화
        traci.start([
            self.sumo_binary, "-c", self.sumo_cfg_path,
            "--no-step-log", "true", "--no-warnings", "true",
        ])

        self._sumo_started = True
        self._decision_count = 0
        self._depart_time = {}
        self._last_reposition_distance_m = 0.0
        self._seen_request_ids.clear()
        self.committed = {}
        self._episode_index += 1
        for key in self._interval_stats:
            self._interval_stats[key] = 0.0
        passenger_seed = self.meta.get("passenger_seed")
        if self.randomize_passenger_seed:
            passenger_seed = self.training_seed_base + self._episode_index

        episode_date = self._pick_episode_date()

        dispatch_algo = self.meta.get("taxi_dispatch_algorithm", "greedy")
        self.dispatcher = make_dispatcher(dispatch_algo, self.sumo_cfg_path)  # hungarian/custom_* (SUMO 내장이면 None)
        
        sim_start_hour = self.meta.get("sim_start_hour", 0)
        
        self.fleet_manager = TaxiFleetManager(
            target_count=self.meta["num_taxis"],
            boundary_edges=self.meta["boundary_edges"],
            all_edges=self.meta["edges"],
            strategy="rl_prepositioned",
            hotspot_edges=self.meta.get("hotspot_edges"),
            zones=self.meta.get("zones"),
            sim_start_hour=sim_start_hour,
        )
        
        self.pax_manager = PassengerTimeoutManager(
            wait_timeout_sec=self.meta.get("passenger_wait_timeout", 900),
            dispatcher=self.dispatcher,
        )
        
        self.spawn_manager = PassengerSpawnManager(
            zones=self.meta.get("zones", {}),
            department_edges=self.meta.get("department_store_edges", []),
            sim_start_hour=sim_start_hour,
            sim_end_hour=self.meta.get("sim_end_hour"),
            school_pop_base=self.meta.get("school_pop_base", 400),
            company_pop_base=self.meta.get("company_pop_base", 100),
            seed=passenger_seed,
            sim_date=episode_date.isoformat(),
            meta=self.meta,
        )

        # [디지털 트윈 일치] 학습 환경도 평가 환경과 같은 도로 현실화를 쓴다 (예전엔 신호·정체 보정 없이 학습 → 평가와 달랐음)
        from module1_simulation.signal_plans import SignalPlanController
        from module1_simulation.speed_profile import SpeedProfileController
        from module4_dispatch.network_cost import TimeVaryingTravelTime, net_file_from_sumocfg
        is_hol = bool(getattr(self.spawn_manager, "is_holiday", False))
        net_file = net_file_from_sumocfg(self.sumo_cfg_path)
        self.signal_ctrl = SignalPlanController.from_config(CFG, episode_date, sim_start_hour, is_hol)
        shared_tt = None
        if self.dispatcher is not None and getattr(self.dispatcher, "travel_time", None) is not None \
                and CFG.get("speed_profile", "topis") == "topis":
            shared_tt = TimeVaryingTravelTime(net_file)
            self.dispatcher.travel_time = shared_tt
        self.speed_ctrl = SpeedProfileController.from_config(CFG, net_file, episode_date, sim_start_hour, is_hol,
                                                             travel_time=shared_tt)

        if self.demand_predictor is not None and hasattr(self.demand_predictor, "reset"):
            self.demand_predictor.reset(episode_date)
        # 시작 전 호출(지난 8일 + 오늘 시작 전)로 예측기의 lag·어제·지난주 피처를 학습 때처럼 채운다 (실측 수요 모드)
        self._edge_weights = {}
        if (hasattr(self.demand_predictor, "set_past_requests")
                and getattr(self.spawn_manager, "_real_model", None) is not None and self._request_category):
            from collections import Counter
            from module3_prediction.category_predictor import build_past_requests
            past = build_past_requests(self.spawn_manager._real_model, episode_date, sim_start_hour,
                                       passenger_seed, self._request_category,
                                       is_holiday_today=is_hol)   # 실제 승객 생성과 같은 공휴일 판정
            self.demand_predictor.set_past_requests(past, sim_start_hour * 3600.0)
            # 재배치 목표 도로 가중치 = 그 도로의 과거 호출 수 (rl_weighted_targets)
            if CFG.get("rl_weighted_targets", True):
                self._edge_weights = Counter(r[2] for r in past)

        self._advance_sim(DECISION_INTERVAL_SEC)
        self._last_riding = self._riding_passenger_ids()
        obs = self._get_obs()
        return obs, {}

    def step(self, action):
        prev_in_taxi = self._last_riding
        prev_timeout_count = len(self.pax_manager.removed_pids)

        self._apply_action(action)
        self._advance_sim(DECISION_INTERVAL_SEC)

        waiting, idle = self._zone_counts()
        n_waiting = sum(waiting.values())
        riding_now = self._riding_passenger_ids()
        newly_picked_up = len(riding_now - prev_in_taxi)
        self._last_riding = riding_now
        new_timeouts = len(self.pax_manager.removed_pids) - prev_timeout_count

        reward_mode = CFG.get("rl_reward_mode", "default")
        if reward_mode == "gravityVer":
            # ── [gravityVer] 균형 최적화 보상 (스케일 정규화: -3.0 ~ +3.0) ──
            match_score = self._distribution_match_score(idle, self._predicted_demand()) if self.incentive_weight > 0 else self._prepositioned_distribution_score(idle)
            r_match = 1.5 * match_score
            r_pickup = min(0.15 * newly_picked_up, 2.5)
            r_wait = -min(0.01 * n_waiting, 1.5)
            r_timeout = -min(0.3 * max(new_timeouts, 0), 3.0)
            r_dist = -0.00005 * self._last_reposition_distance_m
        else:
            # ── [default] 기존 보상 방식 ──
            r_pickup = 3.0 * newly_picked_up
            r_wait = -0.10 * n_waiting
            r_timeout = -8.0 * max(new_timeouts, 0)
            r_dist = -0.0002 * self._last_reposition_distance_m
            prepositioned_score = self._prepositioned_distribution_score(idle)
            r_match = 0.5 * prepositioned_score
            if self.incentive_weight > 0:
                match_score = self._distribution_match_score(idle, self._predicted_demand())
                r_match += self.incentive_weight * match_score
            else:
                match_score = prepositioned_score

        reward = r_match + r_pickup + r_wait + r_timeout + r_dist

        # ── 600초(10분 = 10스텝) 누적 통계 및 진단 로깅 ──
        s = self._interval_stats
        s["pickups"] += newly_picked_up
        s["r_pickup"] += r_pickup
        s["timeouts"] += max(new_timeouts, 0)
        s["r_timeout"] += r_timeout
        s["waiting_sum"] += n_waiting
        s["r_wait"] += r_wait
        s["match_sum"] += match_score
        s["r_match"] += r_match
        s["dist_m"] += self._last_reposition_distance_m
        s["r_dist"] += r_dist
        s["total_reward"] += reward
        s["steps"] += 1.0

        if int(s["steps"]) >= 10:  # 매 600초(10스텝)
            sim_sec = traci.simulation.getTime()
            sim_start_sec = self.meta.get("sim_start_hour", 0) * 3600.0
            cur_sec = sim_start_sec + sim_sec
            h = int(cur_sec // 3600)
            m = int((cur_sec % 3600) // 60)
            prev_m = (m - 10) % 60
            prev_h = h if m >= 10 else (h - 1) % 24
            time_str = f"{prev_h:02d}:{prev_m:02d}~{h:02d}:{m:02d}"

            avg_wait = s["waiting_sum"] / max(s["steps"], 1.0)
            avg_match = (s["match_sum"] / max(s["steps"], 1.0)) * 100.0
            dist_km = s["dist_m"] / 1000.0

            if self.worker_index == 0:
                print(f"[진단 {time_str}] 탑승:{int(s['pickups'])}건({s['r_pickup']:+.2f}) | "
                      f"대기:{avg_wait:.1f}명({s['r_wait']:+.2f}) | "
                      f"타임아웃:{int(s['timeouts'])}건({s['r_timeout']:+.2f}) | "
                      f"핫존:{avg_match:.0f}%({s['r_match']:+.2f}) | "
                      f"공차:{dist_km:.1f}km({s['r_dist']:+.2f}) | "
                      f"구간합:{s['total_reward']:+.2f}점")

                try:
                    os.makedirs(os.path.dirname(self._log_file_path), exist_ok=True)
                    write_header = not os.path.exists(self._log_file_path)
                    with open(self._log_file_path, "a", encoding="utf-8") as _lf:
                        if write_header:
                            _lf.write("episode,decision_count,time_str,pickups,r_pickup,waiting_avg,r_wait,timeouts,r_timeout,match_pct,r_match,dist_km,r_dist,total_reward\n")
                        _lf.write(f"{self._episode_index},{self._decision_count},{time_str},{int(s['pickups'])},{s['r_pickup']:.3f},{avg_wait:.1f},{s['r_wait']:.3f},{int(s['timeouts'])},{s['r_timeout']:.3f},{avg_match:.1f},{s['r_match']:.3f},{dist_km:.2f},{s['r_dist']:.3f},{s['total_reward']:.3f}\n")
                except Exception:
                    pass

            for k in s:
                s[k] = 0.0

        obs = self._get_obs(waiting, idle)   # 위에서 센 결과 재사용 (TraCI 재조회 없음)
        self._decision_count += 1
        terminated = traci.simulation.getMinExpectedNumber() <= 0
        truncated = self._decision_count >= self.max_decisions

        if terminated or truncated:
            self._close_sumo()

        return obs, reward, terminated, truncated, {
            "picked_up": newly_picked_up,
            "timeouts": max(new_timeouts, 0),
            "waiting": n_waiting,
            "reposition_distance_m": self._last_reposition_distance_m,
        }

    def close(self):
        self._close_sumo()

    def _close_sumo(self):
        if self._sumo_started:
            try:
                traci.close()
            except Exception:
                pass
            self._sumo_started = False

        try:
            import psutil
            for p in psutil.process_iter(["pid", "name", "ppid"]):
                if p.info["name"] and p.info["name"].lower().startswith("sumo") \
                        and p.info["ppid"] == os.getpid():
                    try:
                        p.kill()
                    except Exception:
                        pass
        except ImportError:
            pass

    def _advance_sim(self, duration_sec: float):
        THROTTLE_SEC = 10.0
        end_time = traci.simulation.getTime() + duration_sec

        while (
            traci.simulation.getTime() < end_time
            and traci.simulation.getMinExpectedNumber() > 0
        ):
            traci.simulationStep()
            now = traci.simulation.getTime()
            if self.signal_ctrl:
                self.signal_ctrl.maintain(now)
            if self.speed_ctrl:
                self.speed_ctrl.maintain(now)
            current_pids = set(traci.person.getIDList())

            if int(now) % THROTTLE_SEC == 0:
                self.fleet_manager.maintain(now, current_vids=set(traci.vehicle.getIDList()))
                self.pax_manager.maintain(now, current_pids=current_pids)
            
            self.spawn_manager.maintain(now, timeout_removed_pids=self.pax_manager.removed_pids, current_pids=current_pids)

            # [KJS] spawn_manager가 이번 틱에 넣은 승객까지 포함해 신규 요청을 수집
            self._record_new_requests(now, current_pids)

            if self.dispatcher is not None:
                self.dispatcher.maintain(now)

            for pid in current_pids:
                if pid not in self._depart_time:
                    self._depart_time[pid] = now

    def _record_new_requests(self, now: float, current_pids: set):
        """[KJS] 수요예측에는 현재 대기 인원이 아니라 새로 등장한 승객(호출)만 전달한다.
        카테고리는 학습(train_category_demand.py)과 같은 기준 — 승차 도로 위치의 가장 가까운 POI 구역."""
        if self.demand_predictor is None or not hasattr(self.demand_predictor, "record_new_requests"):
            return
        counts = {category: 0 for category in ZONE_CATEGORIES}
        for pid in current_pids - self._seen_request_ids:
            try:
                category = self._request_category.get(traci.person.getRoadID(pid))
            except traci.exceptions.TraCIException:
                category = None
            if category in counts:
                counts[category] += 1
        self._seen_request_ids.update(current_pids)
        sim_start_sec = self.meta.get("sim_start_hour", 0) * 3600.0
        self.demand_predictor.record_new_requests(counts, sim_start_sec + now)

    def _prune_committed(self, idle_taxis):
        """재배치 중 표시 정리: 더 이상 빈 택시가 아니거나(승객 배정/탑승) 목표 도로에 닿았으면 제거."""
        if not self.committed:
            return
        idle = set(idle_taxis)
        for taxi_id, target_edge in list(self.committed.items()):
            if taxi_id not in idle:
                del self.committed[taxi_id]
                continue
            try:
                if traci.vehicle.getRoadID(taxi_id) == target_edge:
                    del self.committed[taxi_id]
            except traci.exceptions.TraCIException:
                del self.committed[taxi_id]

    def _eligible_idle_taxis(self, idle_taxis):
        """재배치 후보: 이동 중(committed)이거나 예약된 택시는 제외."""
        eligible = []
        for taxi_id in idle_taxis:
            if taxi_id in self.committed:
                continue
            if not _has_no_pending_stop(taxi_id):
                continue
            try:
                edge = traci.vehicle.getRoadID(taxi_id)
            except traci.exceptions.TraCIException:
                continue
            if edge and not edge.startswith(":"):
                eligible.append((taxi_id, edge, self._edge_to_zone.get(edge)))
        return eligible

    def _apply_action(self, action):
        self._last_reposition_distance_m = 0.0
        self._picked_targets = set()
        weights = np.asarray(action, dtype=np.int64).reshape(-1)
        if weights.size != N_ZONES or not np.any(weights):
            return

        all_idle = list(traci.vehicle.getTaxiFleet(0))
        if not all_idle:
            return
        self._prune_committed(all_idle)
        eligible_taxis = self._eligible_idle_taxis(all_idle)
        if not eligible_taxis:
            return

        current_idle = {c: 0 for c in ZONE_CATEGORIES}
        for _, _, source in eligible_taxis:
            if source:
                current_idle[source] += 1

        total_idle = len(eligible_taxis)
        raw_targets = weights / weights.sum() * total_idle
        target_counts = np.floor(raw_targets).astype(int)
        for index in np.argsort(raw_targets - target_counts)[::-1][:total_idle - target_counts.sum()]:
            target_counts[index] += 1

        # 목표 대수를 넘긴 구역의 택시만 이동 후보로 삼는다.
        movable = []
        np.random.shuffle(eligible_taxis)
        for taxi_id, edge, source in eligible_taxis:
            if source is not None:
                source_index = ZONE_CATEGORIES.index(source)
                if current_idle[source] <= target_counts[source_index]:
                    continue
                current_idle[source] -= 1
            movable.append((taxi_id, edge))

        for target_index, target_category in enumerate(ZONE_CATEGORIES):
            deficit = max(0, target_counts[target_index] - current_idle[target_category])
            target_edges = self.zones.get(target_category) or []
            if not target_edges:
                continue

            while deficit > 0 and movable:
                taxi_id, current_edge = movable.pop()
                if self._move_taxi_to_zone(taxi_id, current_edge, target_edges):
                    current_idle[target_category] += 1
                    deficit -= 1

    def _move_taxi_to_zone(self, taxi_id: str, current_edge: str, target_edges: list) -> bool:
        # 과거 호출이 많았던 도로일수록 먼저 시도 (기록이 없으면 예전처럼 균등 무작위)
        candidates = weighted_edge_order(target_edges, self._edge_weights, self._picked_targets)

        for target_edge in candidates:
            if target_edge == current_edge or (current_edge, target_edge) in self._unreachable_edges:
                continue
            try:
                # [최적화 4] RL 에이전트 경로 지정 시에도 택시 물리법칙(vType) 강제하여 충돌 차단
                route = traci.simulation.findRoute(current_edge, target_edge, vType="taxi_type")
                if route is None or not route.edges:
                    continue
                traci.vehicle.changeTarget(taxi_id, target_edge)
                self.committed[taxi_id] = target_edge
                self._picked_targets.add(target_edge)
                self._last_reposition_distance_m += max(float(route.length), 0.0)
                return True
            except traci.exceptions.TraCIException:
                self._unreachable_edges.add((current_edge, target_edge))
        return False

    def _prepositioned_distribution_score(self, idle: dict) -> float:
        total_idle = sum(idle.values())
        if total_idle <= 0:
            return 0.0

        now_seconds = traci.simulation.getTime()
        now_hour = (self.meta.get("sim_start_hour", 0) + (now_seconds / 3600.0)) % 24

        scored = []
        for category in ZONE_CATEGORIES:
            peak_hours = CATEGORY_PEAK_HOURS.get(category, [])
            if not peak_hours:
                min_diff = 24.0
            else:
                min_diff = min(abs((now_hour - peak + 12) % 24 - 12) for peak in peak_hours)
            score = 3.0 - min_diff
            scored.append((category, score))

        scored.sort(key=lambda item: item[1], reverse=True)
        if not scored:
            return 0.0

        primary_category = scored[0][0]
        primary_idle = idle.get(primary_category, 0)
        actual_primary_ratio = primary_idle / total_idle
        target_primary_ratio = float(self.target_hotspot_ratio)
        primary_score = max(0.0, 1.0 - abs(target_primary_ratio - actual_primary_ratio))

        return primary_score

    def _distribution_match_score(self, idle: dict, predicted: dict) -> float:
        total_idle = sum(idle.values())
        if total_idle <= 0:
            return 0.0
        ordered = sorted(ZONE_CATEGORIES, key=lambda c: predicted.get(c, 0.0), reverse=True)
        hotspot_categories = set(ordered[:max(1, len(ordered) // 2)])
        hotspot_idle = sum(idle.get(c, 0) for c in hotspot_categories)
        actual_hotspot_ratio = hotspot_idle / total_idle
        return max(0.0, 1.0 - abs(self.target_hotspot_ratio - actual_hotspot_ratio))

    @staticmethod
    def _riding_passenger_ids() -> set:
        riding = set()
        for pid in traci.person.getIDList():
            try:
                if traci.person.getVehicle(pid):
                    riding.add(pid)
            except traci.exceptions.TraCIException:
                continue
        return riding

    def _zone_counts(self):
        waiting = {category: 0 for category in ZONE_CATEGORIES}
        idle = {category: 0 for category in ZONE_CATEGORIES}
        for pid in traci.person.getIDList():
            try:
                if traci.person.getVehicle(pid):
                    continue
                edge = traci.person.getRoadID(pid)
            except traci.exceptions.TraCIException:
                continue
            category = self._edge_to_zone.get(edge)
            if category:
                waiting[category] += 1
        for vid in traci.vehicle.getTaxiFleet(0):
            try:
                edge = traci.vehicle.getRoadID(vid)
            except traci.exceptions.TraCIException:
                continue
            category = self._edge_to_zone.get(edge)
            if category:
                idle[category] += 1
        return waiting, idle

    def _predicted_demand(self):
        if self.demand_predictor is None:
            return {category: 0.0 for category in ZONE_CATEGORIES}
        sim_start_sec = self.meta.get("sim_start_hour", 0) * 3600.0
        return self.demand_predictor.predict_by_zone(sim_start_sec + traci.simulation.getTime())

    def _get_obs(self, waiting=None, idle=None):
        if waiting is None or idle is None:
            waiting, idle = self._zone_counts()
        predicted = self._predicted_demand()
        now = traci.simulation.getTime()
        sim_start = self.meta.get("sim_start_hour", 0) * 3600.0
        sim_end = self.meta.get("sim_end_hour", 24) * 3600.0
        sim_len = max(sim_end - sim_start, 1.0)
        time_frac = min(max(now / sim_len, 0.0), 1.0)
        vec = []
        for category in ZONE_CATEGORIES:
            vec += [float(waiting[category]), float(idle[category]), float(predicted.get(category, 0.0))]
        vec.append(time_frac)
        return np.clip(np.array(vec, dtype=np.float32), 0.0, np.inf)