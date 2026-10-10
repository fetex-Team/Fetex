"""
module4_dispatch/rl_env.py

목적: 승객-택시 "매칭"은 GUI에서 고른 기존 방식(greedy 또는 Hungarian)이 담당하고,
      RL은 그 위에서 "유휴 택시를 어느 zone(카테고리)으로 미리 보내놓을지"만 결정한다.
"""

import os
import random
import sys
import numpy as np
import traci

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import gymnasium as gym
from gymnasium import spaces

from taxi_manager import TaxiFleetManager, CATEGORY_PEAK_HOURS
from passenger_manager import PassengerTimeoutManager
from passenger_spawn_manager import PassengerSpawnManager
from config_loader import CFG
from holiday_utils import pick_run_date
from module4_dispatch.hungarian_dispatcher import HungarianDispatcher
from poi_extractor import CATEGORIES as ZONE_CATEGORIES

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
        self._decision_count = 0
        self._sumo_started = False
        self._depart_time = {}
        self._unreachable_edges = set()
        self._last_reposition_distance_m = 0.0
        self._episode_index = 0

    def reset(self, seed=None, options=None):
        super().reset(seed=seed)
        self._close_sumo()

        traci.start([
            self.sumo_binary, "-c", self.sumo_cfg_path,
            "--no-step-log", "true", "--no-warnings", "true",
        ])

        self._sumo_started = True
        self._decision_count = 0
        self._depart_time = {}
        self._last_reposition_distance_m = 0.0
        self._episode_index += 1
        passenger_seed = self.meta.get("passenger_seed")
        if self.randomize_passenger_seed:
            passenger_seed = self.training_seed_base + self._episode_index

        episode_date = pick_run_date(CFG, index=self._episode_index,
                                     rng=random.Random(self.training_seed_base + self._episode_index))

        dispatch_algo = self.meta.get("taxi_dispatch_algorithm", "greedy")
        self.dispatcher = HungarianDispatcher() if dispatch_algo == "hungarian" else None
        
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
        )

        if self.demand_predictor is not None and hasattr(self.demand_predictor, "reset"):
            self.demand_predictor.reset(episode_date)

        self._advance_sim(DECISION_INTERVAL_SEC)
        obs = self._get_obs()
        return obs, {}

    def step(self, action):
        prev_waiting, _ = self._zone_counts()
        prev_in_taxi = self._riding_passenger_ids()
        prev_timeout_count = len(self.pax_manager.removed_pids)

        self._apply_action(action)
        self._advance_sim(DECISION_INTERVAL_SEC)

        waiting, idle = self._zone_counts()
        n_waiting = sum(waiting.values())
        newly_picked_up = len(self._riding_passenger_ids() - prev_in_taxi)
        new_timeouts = len(self.pax_manager.removed_pids) - prev_timeout_count

        reward = 3.0 * newly_picked_up
        reward -= 0.10 * n_waiting
        reward -= 8.0 * max(new_timeouts, 0)
        reward -= 0.0002 * self._last_reposition_distance_m

        prepositioned_score = self._prepositioned_distribution_score(idle)
        reward += 0.5 * prepositioned_score

        if self.incentive_weight > 0:
            reward += self.incentive_weight * self._distribution_match_score(
                idle, self._predicted_demand()
            )

        obs = self._get_obs()
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
            current_pids = set(traci.person.getIDList())

            if int(now) % THROTTLE_SEC == 0:
                self.fleet_manager.maintain(now, current_vids=set(traci.vehicle.getIDList()))
                self.pax_manager.maintain(now, current_pids=current_pids)
            
            self.spawn_manager.maintain(now, timeout_removed_pids=self.pax_manager.removed_pids, current_pids=current_pids)

            if self.dispatcher is not None:
                self.dispatcher.maintain(now)

            for pid in current_pids:
                if pid not in self._depart_time:
                    self._depart_time[pid] = now

    def _apply_action(self, action):
        self._last_reposition_distance_m = 0.0
        weights = np.asarray(action, dtype=np.int64).reshape(-1)
        if weights.size != N_ZONES or not np.any(weights):
            return

        idle_taxis = list(traci.vehicle.getTaxiFleet(0))
        if not idle_taxis:
            return

        idle_taxis = [t for t in idle_taxis if _has_no_pending_stop(t)]
        if not idle_taxis:
            return
            
        np.random.shuffle(idle_taxis)
        _, current_idle = self._zone_counts()
        total_idle = len(idle_taxis)
        raw_targets = weights / weights.sum() * total_idle
        target_counts = np.floor(raw_targets).astype(int)
        for index in np.argsort(raw_targets - target_counts)[::-1][:total_idle - target_counts.sum()]:
            target_counts[index] += 1

        movable = []
        for taxi_id in idle_taxis:
            try:
                current_edge = traci.vehicle.getRoadID(taxi_id)
            except traci.exceptions.TraCIException:
                continue
            if not current_edge or current_edge.startswith(":"):
                continue

            source = self._edge_to_zone.get(current_edge)
            if source is not None:
                source_index = ZONE_CATEGORIES.index(source)
                if current_idle[source] <= target_counts[source_index]:
                    continue
                current_idle[source] -= 1
            movable.append((taxi_id, current_edge))

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
        candidates = list(target_edges)
        np.random.shuffle(candidates)

        for target_edge in candidates:
            if target_edge == current_edge or (current_edge, target_edge) in self._unreachable_edges:
                continue
            try:
                # [최적화 4] RL 에이전트 경로 지정 시에도 택시 물리법칙(vType) 강제하여 충돌 차단
                route = traci.simulation.findRoute(current_edge, target_edge, vType="taxi_type")
                if route is None or not route.edges:
                    continue
                traci.vehicle.changeTarget(taxi_id, target_edge)
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

    def _get_obs(self):
        waiting, idle = self._zone_counts()
        if self.demand_predictor is not None and hasattr(self.demand_predictor, "update"):
            self.demand_predictor.update(waiting)
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