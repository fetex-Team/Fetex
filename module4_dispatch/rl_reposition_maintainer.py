"""
module4_dispatch/rl_reposition_maintainer.py

학습된 RL 정책을 measure_wait_time.py 루프 안에 Random/Hungarian과 같은 방식으로 끼우기 위한 wrapper.
HungarianDispatcher가 "maintain(now)"로 매 스텝 불리는 것과 동일한 인터페이스를 맞춤.

기존 measure_wait_time.py에서:
    hungarian_dispatcher = HungarianDispatcher() if meta.get("taxi_dispatch_algorithm") == "hungarian" else None
    ...
    if hungarian_dispatcher:
        hungarian_dispatcher.maintain(now)

이걸 다음처럼 바꾸면 RL도 같은 A/B 비교 플로우에 들어감:
    reposition_maintainer = (
        RLRepositionMaintainer(MODEL_PATH, meta) if meta.get("taxi_dispatch_algorithm") == "rl_reposition" else None
    )
    ...
    if hungarian_dispatcher:
        hungarian_dispatcher.maintain(now)      # 매칭은 그대로 Hungarian이 담당
    if reposition_maintainer:
        reposition_maintainer.maintain(now)     # RL은 재배치만 별도로 담당 (둘이 같이 켜도 됨)

즉 RL을 "배차 알고리즘 자체의 대체재"가 아니라 "기존 배차 위에 얹는 재배치 보조 정책"으로 넣는 구조.
test_matching_comparison.py / multi_factor_compare.py의 taxi_dispatch_algorithm 값에
"rl_reposition"을 새 옵션으로 추가하면 기존 비교 스크립트 그대로 재사용 가능.

예측수요는 module4_dispatch/category_demand_predictor.py의 CategoryDemandPredictor를 그대로 씀
(rl_env.py 학습 때와 동일한 피처 구성 - 모델 없으면 자동으로 0 대체, 에러 안 남).
"""
import os

import numpy as np
import sys

# numpy 2.x에서 저장한 모델(rl_reposition_model.zip)을 numpy 1.x 환경에서 불러올 때
# "No module named 'numpy._core.numeric'" 오류가 나서, 옛 이름(numpy.core)으로 연결해 준다.
try:  # SHIM-V2
    import numpy._core.numeric  # numpy 2.x면 여기서 끝 (호환 처리 불필요)
except ImportError:
    import numpy.core as _np_legacy
    sys.modules["numpy._core"] = _np_legacy
    for _name in ("numeric", "multiarray", "umath", "_multiarray_umath"):
        _mod = getattr(_np_legacy, _name, None)
        if _mod is not None:
            sys.modules["numpy._core." + _name] = _mod

# NumPy 2.x는 Generator를 pickle할 때 BitGenerator 클래스 자체를 넘길 수 있다.
# NumPy 1.26의 복원 함수는 문자열 키만 받기 때문에, NumPy 2에서 저장된 PPO 모델을
# 읽으면 ``<class 'numpy.random._pcg64.PCG64'> is not a known BitGenerator``로 멈춘다.
# 모델의 정책 가중치와 무관한 RNG 상태를 현재 환경의 동명 BitGenerator로 복원하도록
# 호환 래퍼를 설치한다. NumPy 2.x에서는 원래 함수가 이미 클래스도 처리하므로 영향이 없다.
try:  # SHIM-V3
    import numpy.random._pickle as _np_random_pickle
    _original_bit_generator_ctor = _np_random_pickle.__bit_generator_ctor

    def _compatible_bit_generator_ctor(bit_generator_name="MT19937"):
        if isinstance(bit_generator_name, type):
            bit_generator_name = bit_generator_name.__name__
        return _original_bit_generator_ctor(bit_generator_name)

    _np_random_pickle.__bit_generator_ctor = _compatible_bit_generator_ctor
except Exception:
    # NumPy가 없는 환경에서는 PPO.load가 더 명확한 원래 오류를 내도록 둔다.
    pass
import traci
from gymnasium import spaces
from stable_baselines3 import PPO

from poi_extractor import CATEGORIES as ZONE_CATEGORIES
from module4_dispatch.category_demand_predictor import CategoryDemandPredictor

DECISION_INTERVAL_SEC = 60.0
CATEGORY_MODEL_PATH_DEFAULT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "saved_models", "category_demand_models.pkl"
)


class RLRepositionMaintainer:
    def __init__(self, model_path: str, meta: dict, category_model_path: str = None):
        # SB3 모델에는 Gymnasium action_space의 난수 상태도 저장된다. NumPy 2에서
        # 저장된 이 상태는 NumPy 1.26에서 역직렬화가 호환되지 않는다. 공간의 정의는
        # 이 프로젝트에서 고정(구역당 waiting/idle/predicted + time, 구역별 0~3 action)
        # 이므로, 실행에 필요 없는 저장 RNG 대신 현재 정의로 안전하게 재구성한다.
        n_zones = len(ZONE_CATEGORIES)
        observation_space = spaces.Box(
            low=0.0, high=np.inf, shape=(n_zones * 3 + 1,), dtype=np.float32,
        )
        action_space = spaces.MultiDiscrete(np.full(n_zones, 4, dtype=np.int64))
        self.model = PPO.load(
            model_path,
            custom_objects={
                "observation_space": observation_space,
                "action_space": action_space,
            },
        )
        self.zones = meta["zones"]
        self._last_decision_time = -DECISION_INTERVAL_SEC
        self._unreachable_edges = set()
        # --- Unity 재생 기록용 공개 상태 (ReplayRecorder.capture가 forecast 인자로 받아 읽는다) ---
        self.committed = {}   # 재배치 명령을 받고 아직 이동 중인 택시: {taxi_id: 목표 edge}
        self.moves = []       # 성공한 재배치 명령 누적 목록 [{t, taxi, from, to, zone}]
        self.decisions = []   # 의사결정(1분 주기)마다 구역별 대기/유휴/예측/목표 스냅샷
        self._now = 0.0

        category_model_path = category_model_path or CATEGORY_MODEL_PATH_DEFAULT
        self.demand_predictor = None
        if os.path.exists(category_model_path):
            try:
                self.demand_predictor = CategoryDemandPredictor(category_model_path)
            except Exception as exc:
                # XGBoost DLL/OpenMP 초기화가 Windows 상태에 따라 실패해도 Unity 재생
                # 전체를 중단하지 않는다. RL 정책은 waiting/idle/time 관측값만으로도
                # 실행할 수 있고, 예측 수요 슬롯은 0으로 채워진다.
                print(
                    f"[경고] 수요예측 모델을 불러오지 못해 예측수요 없이 RL 재배치를 계속합니다: {exc}"
                )
        # 모델이 없거나 불러오기 실패 시 None으로 두고 예측수요=0으로 대체한다.

        self._edge_to_zone = {}
        for cat, edges in self.zones.items():
            for e in edges:
                self._edge_to_zone[e] = cat

    def set_run_date(self, run_date):
        """이번 실행의 날짜를 예측 모델에 알려준다 (요일·공휴일 피처, 수요 이력 초기화)."""
        if self.demand_predictor is not None:
            self.demand_predictor.reset(run_date)

    def _zone_counts(self, current_pids=None, idle_taxis=None):
        waiting = {c: 0 for c in ZONE_CATEGORIES}
        idle = {c: 0 for c in ZONE_CATEGORIES}

        for pid in (current_pids if current_pids is not None else traci.person.getIDList()):
            try:
                if traci.person.getVehicle(pid):
                    continue
                edge = traci.person.getRoadID(pid)
            except traci.exceptions.TraCIException:
                continue
            cat = self._edge_to_zone.get(edge)
            if cat:
                waiting[cat] += 1

        for vid in (idle_taxis if idle_taxis is not None else traci.vehicle.getTaxiFleet(0)):
            try:
                edge = traci.vehicle.getRoadID(vid)
            except traci.exceptions.TraCIException:
                continue
            cat = self._edge_to_zone.get(edge)
            if cat:
                idle[cat] += 1

        return waiting, idle

    def _build_obs(self, now, sim_start_hour, sim_end_hour, current_pids=None, idle_taxis=None):
        waiting, idle = self._zone_counts(current_pids, idle_taxis)

        if self.demand_predictor is not None:
            self.demand_predictor.update(waiting)  # rl_env.py의 _get_obs()와 동일한 순서
            # now는 시뮬 시작을 0으로 하는 경과초 -> 예측 모델이 쓰는 '시계 시각'으로 바꿔서 전달
            predicted = self.demand_predictor.predict_by_zone(sim_start_hour * 3600.0 + now)
        else:
            predicted = {c: 0.0 for c in ZONE_CATEGORIES}

        # now는 0부터 시작하는 경과초이므로 시뮬 길이로만 나눈다 (예전엔 시작 시각을 또 빼서 앞 9시간 동안 0이었음)
        sim_len = max((sim_end_hour - sim_start_hour) * 3600.0, 1.0)
        time_frac = min(max(now / sim_len, 0.0), 1.0)

        vec = []
        for c in ZONE_CATEGORIES:
            vec += [float(waiting[c]), float(idle[c]), float(predicted.get(c, 0.0))]
        vec.append(time_frac)
        return np.array(vec, dtype=np.float32)

    def maintain(self, now: float, sim_start_hour: float = 0, sim_end_hour: float = 24,
                 current_pids: set = None, idle_taxis: list = None):
        """current_pids / idle_taxis: 메인 루프의 스텝 스냅샷 (None이면 의사결정 시점에 직접 조회).
        idle_taxis에는 같은 스텝에 Hungarian이 방금 배정한 택시가 이미 빠져 있어야 한다."""
        self._now = now
        self._prune_committed(idle_taxis)  # 매 스텝: 도착했거나 승객을 받은 택시는 '재배치 중'에서 뺀다
        if now - self._last_decision_time < DECISION_INTERVAL_SEC:
            return
        self._last_decision_time = now

        obs = self._build_obs(now, sim_start_hour, sim_end_hour, current_pids, idle_taxis)
        action, _ = self.model.predict(obs, deterministic=True)
        weights = np.asarray(action, dtype=np.int64).reshape(-1)
        decision = None
        if weights.size == len(ZONE_CATEGORIES):
            per_zone = np.asarray(obs[:-1], dtype=np.float64).reshape(len(ZONE_CATEGORIES), 3)
            decision = {'t': float(now), 'zones': [
                {'name': c, 'waiting': float(per_zone[k][0]), 'idle': float(per_zone[k][1]),
                 'predicted': float(per_zone[k][2]), 'weight': int(weights[k]), 'target': 0}
                for k, c in enumerate(ZONE_CATEGORIES)]}
            self.decisions.append(decision)
        if weights.size != len(ZONE_CATEGORIES):
            raise ValueError(
                "현재 RL 모델은 구형 단일-택시 행동 공간으로 학습되었습니다. "
                "새 rl_env.py로 rl_train.py를 다시 실행해 모델을 재학습하세요."
            )
        if not np.any(weights):
            return

        eligible_taxis = self._eligible_idle_taxis(idle_taxis)
        if not eligible_taxis:
            return

        current_idle = {c: 0 for c in ZONE_CATEGORIES}
        for _, edge, source in eligible_taxis:
            if source:
                current_idle[source] += 1

        total_idle = len(eligible_taxis)
        raw_targets = weights / weights.sum() * total_idle
        target_counts = np.floor(raw_targets).astype(int)
        for index in np.argsort(raw_targets - target_counts)[::-1][:total_idle - target_counts.sum()]:
            target_counts[index] += 1
        if decision is not None:
            for zone_info, count in zip(decision['zones'], target_counts):
                zone_info['target'] = int(count)

        # 목표 대수를 넘긴 구역의 택시만 이동 후보로 삼는다. 이 때문에 이미
        # 적정 위치에 있는 택시가 매분 다시 목적지를 받는 일이 없다.
        movable = []
        np.random.shuffle(eligible_taxis)
        for taxi_id, edge, source in eligible_taxis:
            if source is not None:
                source_index = ZONE_CATEGORIES.index(source)
                if current_idle[source] <= target_counts[source_index]:
                    continue
                current_idle[source] -= 1
            movable.append((taxi_id, edge))

        for target_index, category in enumerate(ZONE_CATEGORIES):
            target_edges = self.zones.get(category) or []
            deficit = max(0, target_counts[target_index] - current_idle[category])
            while deficit > 0 and movable and target_edges:
                taxi_id, current_edge = movable.pop()
                if self._move_taxi(taxi_id, current_edge, target_edges, category):
                    current_idle[category] += 1
                    deficit -= 1

    @staticmethod
    def _has_no_pending_stop(taxi_id: str) -> bool:
        try:
            return len(traci.vehicle.getStops(taxi_id, 1)) == 0
        except traci.exceptions.TraCIException:
            return False

    def _eligible_idle_taxis(self, idle_taxis=None):
        """승객 배차가 이미 예약된 택시는 제외한 재배치 후보를 반환한다."""
        eligible = []
        for taxi_id in (idle_taxis if idle_taxis is not None else traci.vehicle.getTaxiFleet(0)):
            if not self._has_no_pending_stop(taxi_id):
                continue
            try:
                edge = traci.vehicle.getRoadID(taxi_id)
            except traci.exceptions.TraCIException:
                continue
            if edge and not edge.startswith(":"):
                eligible.append((taxi_id, edge, self._edge_to_zone.get(edge)))
        return eligible

    def _prune_committed(self, idle_taxis=None):
        """재배치 중 표시를 정리한다: 더 이상 빈 택시가 아니거나(승객 배정/탑승) 목표 도로에 닿았으면 제거."""
        if not self.committed:
            return
        idle = set(idle_taxis) if idle_taxis is not None else set(traci.vehicle.getTaxiFleet(0))
        for taxi_id, target_edge in list(self.committed.items()):
            if taxi_id not in idle:
                del self.committed[taxi_id]
                continue
            try:
                if traci.vehicle.getRoadID(taxi_id) == target_edge:
                    del self.committed[taxi_id]
            except traci.exceptions.TraCIException:
                del self.committed[taxi_id]

    def _move_taxi(self, taxi_id: str, current_edge: str, target_edges: list, zone: str = None) -> bool:
        """도달 가능한 도로만 선택해 SUMO 재배치 충돌을 피한다."""
        candidates = list(target_edges)
        np.random.shuffle(candidates)
        for target_edge in candidates:
            if target_edge == current_edge or (current_edge, target_edge) in self._unreachable_edges:
                continue
            try:
                route = traci.simulation.findRoute(current_edge, target_edge)
                if route is None or not route.edges:
                    continue
                traci.vehicle.changeTarget(taxi_id, target_edge)
                self.committed[taxi_id] = target_edge
                self.moves.append({'t': float(self._now), 'taxi': taxi_id, 'from': current_edge,
                                   'to': target_edge, 'zone': zone})
                return True
            except traci.exceptions.TraCIException:
                self._unreachable_edges.add((current_edge, target_edge))
        return False
