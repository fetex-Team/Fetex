"""
평균 승객 대기시간 측정 + A/B 비교 스크립트.

측정 방법:
- traci로 시뮬레이션을 스텝마다 진행하면서, 각 person(승객)이
  "등장(depart) 시각"과 "택시에 탑승한 시각"을 기록.
- 탑승 시각은 traci.person.getVehicle(person_id)가 빈 문자열("")에서
  택시 id로 바뀌는 순간으로 판정 (그 전까지는 길가에서 대기 중).
- 대기시간 = 탑승 시각 - depart 시각.
- 시뮬레이션 종료(모든 차량/사람 소진) 후 평균/최대 대기시간을 출력.

사용법:
    python measure_wait_time.py patrol         # config.json의 sim 설정 그대로, 택시 전략만 patrol로 강제
    python measure_wait_time.py prepositioned  # 택시 전략만 prepositioned로 강제
    python measure_wait_time.py compare        # 두 전략 각각 build_env.py부터 다시 돌려서 순차 비교
    python measure_wait_time.py dispatch_compare [algo_a] [algo_b]  # 배차 알고리즘 A vs B 비교

주의: build_env.py가 만든 entities.rou.xml에 이미 반영된 전략을 그대로 재생하려면
      인자 없이 실행하면 됩니다 (config.json의 taxi_strategy 값 사용).

[헝가리안 배차 연동]
config.json의 taxi_dispatch_algorithm이 "hungarian"이면, build_env.py가 SUMO 쪽엔
"traci"로 알려주고 실제 선택값은 runtime_meta.json에 남겨둠. 이 스크립트는 그 값을 읽어
HungarianDispatcher를 매 스텝 maintain()해서 실제 배차를 수행함 (SUMO 내장 greedy 대신).

[학습용 수요 로그 (신규)]
run_and_measure()가 호출될 때마다(단독 실행 / parallel_dispatch_worker.py /
multi_factor_compare.py의 병렬 워커 전부 포함, run_simulation.py의 GUI 실행은 미포함),
새로 등장하는 승객의 "등장 시각 + 위경도"를 data/raw/simulation_demand_log.csv에
append합니다. (Module 3 학습은 data/sim_logs/demand_log_*.csv 또는 --logs로 지정한 로그를 쓰고,
이 레거시 CSV는 읽지 않습니다.)

날짜는 실행마다 과거 90일 내에서 랜덤 배정되어, 여러 번 돌릴수록 dayofweek/is_weekend
같은 캘린더 피처에 실제 분산이 생기도록 했습니다.
"""

import os
import sys
import json
import time
import datetime
import random
import csv
import re
import hashlib
import traci
import module1_simulation.vehicle_subscriptions as vehicle_subscriptions
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from module1_simulation.taxi_manager import TaxiFleetManager
from module1_simulation.passenger_manager import PassengerTimeoutManager, riding_map
from module1_simulation.passenger_spawn_manager import PassengerSpawnManager
from config_loader import CFG, use_run_config
from module1_simulation.holiday_utils import pick_run_date, is_holiday_date, count_existing_logs
from module4_dispatch.custom_dispatchers import make_dispatcher
from module1_simulation.step_snapshot import fetch_idle_taxis
from module4_dispatch.rl_reposition_maintainer import RLRepositionMaintainer  # 추가
from module2_preprocessing.sim_log_recorder import DemandLogRecorder  # [Module 2] 호출 로그 기록 (feat3, 병합)
from module1_simulation.external_trip_model import ExternalTripAwayModel

ROOT = os.path.dirname(os.path.abspath(__file__))
SUMO_CFG = os.path.join(ROOT, "module1_simulation", "sumo_config", "simulation.sumocfg")
META_PATH = os.path.join(ROOT, "module1_simulation", "sumo_config", "runtime_meta.json")
DEMAND_LOG_PATH = os.path.join(ROOT, "data", "raw", "simulation_demand_log.csv")
# RL 재배치 정책 파일. AI_RL_MODEL_PATH 환경변수로 다른 zip(예: 재학습한 모델)을 골라 쓸 수 있다.
RL_MODEL_PATH = os.environ.get(
    "AI_RL_MODEL_PATH",
    os.path.join(ROOT, "module4_dispatch", "rl_reposition_model.zip"),
)
# RLRepositionMaintainer가 RL 정책과 "실행 시점"에 짝지어 쓸 수요예측 파일.
# 기본값은 기존 카테고리 모델이며, AI_CATEGORY_MODEL_PATH 환경변수로 다른 pkl
# (예: saved_models_backups/<시각>/category_demand_models.pkl)을 골라 바로 조합해 쓸 수 있다.
# rl_reposition_model.zip 자체는 그대로, 여기서 붙이는 수요예측만 갈아끼우는 방식(재학습 불필요).
CATEGORY_MODEL_PATH = os.environ.get(
    "AI_CATEGORY_MODEL_PATH",
    os.path.join(ROOT, "saved_models", "category_demand_models.pkl"),
)



# ---------------------------------------------------------------------------
# 실행·측정 루프 (run_and_measure)와 그 단계별 도우미
# GUI 플레이(run_simulation), A/B 비교, 반복 실험, Unity 내보내기가 모두 이 루프를 쓴다.
# ---------------------------------------------------------------------------

def _create_controllers(meta: dict, sumo_cfg: str):
    """택시 매니저·배차기·타임아웃 매니저·RL 선배치를 만든다.
    반환: (manager, dispatcher, pax_manager, rl_maintainer)"""
    manager = TaxiFleetManager(
        target_count=meta["num_taxis"],
        boundary_edges=meta["boundary_edges"],
        all_edges=meta["edges"],
        strategy=meta["taxi_strategy"],
        hotspot_edges=meta.get("hotspot_edges"),
        zones=meta.get("zones"),
        sim_start_hour=meta.get("sim_start_hour", 0),
    )

    # 배차 알고리즘은 GUI/런타임 설정을 그대로 따른다. RL은 선배치 정책일 뿐
    # 매칭 알고리즘을 바꾸지 않는다.
    dispatch_algo = meta.get("taxi_dispatch_algorithm")
    dispatcher = make_dispatcher(dispatch_algo, sumo_cfg)  # hungarian/custom_* (SUMO 내장이면 None)

    # 배차 못 받고 너무 오래 대기한 승객을 소멸시키는 매니저 (없으면 끝까지 길가에 쌓임)
    # dispatcher를 넘겨주면, 이미 Hungarian이 예약을 배정한 승객의 강제제거를 한 스텝
    # 미뤄서 SUMO 내부 상태 충돌("Connection closed by SUMO")을 방지함 (passenger_manager.py 참고)
    pax_manager = PassengerTimeoutManager(
        wait_timeout_sec=meta.get("passenger_wait_timeout", 900),
        dispatcher=dispatcher,
    )

    # taxi_strategy가 rl_prepositioned일 때만 RL 재배치 매니저를 추가로 켠다.
    # 모델이 없으면 재배치 없이, 사용자가 고른 매칭 알고리즘만 그대로 실행한다.
    rl_maintainer = None
    if meta.get("taxi_strategy") == "rl_prepositioned":
        if os.path.exists(RL_MODEL_PATH):
            rl_maintainer = RLRepositionMaintainer(RL_MODEL_PATH, meta, category_model_path=CATEGORY_MODEL_PATH,
                                                   config_dir=os.path.dirname(os.path.abspath(sumo_cfg)))
            print(f"[안내] RL 재배치 정책을 불러왔습니다: {RL_MODEL_PATH}")
            print(f"[안내] 수요예측은 이걸 씁니다: {CATEGORY_MODEL_PATH}")
        else:
            print("=" * 70)
            print(f"[경고] rl_prepositioned 전략을 골랐지만 RL 모델 파일이 없습니다: {RL_MODEL_PATH}")
            print(f"[경고] RL 재배치 없이 '{dispatch_algo}' 매칭만 수행합니다 (결과는 RL 전략이 아님). "
                  f"먼저 module4_dispatch/rl_train.py를 실행하거나 AI_RL_MODEL_PATH 환경변수로 모델 경로를 지정하세요.")
            print("=" * 70)
    return manager, dispatcher, pax_manager, rl_maintainer


def _resolve_run_date(meta: dict):
    """이번 실행의 날짜와 공휴일 모드를 정한다. 반환: (run_date, holiday_mode)

    스폰 매니저 생성 *전에* 정한다. 시드와 무관한 난수(rng)를 써서 passenger_seed가 고정이어도
    날짜가 매번 바뀐다 (예전엔 random.seed 직후 randint라 항상 같은 날짜였음).
    run_date_mode: "sim_date"(평가·A/B, 날짜 고정) / "alternate"(평일·공휴일 번갈아) / "random"
    """
    run_date = pick_run_date(CFG, index=count_existing_logs())
    # A/B 비교의 '평일/공휴일' 체크(runtime_meta.holiday_mode)가 있으면 config.json 값보다 우선.
    # 강제 모드인데 뽑힌 날짜가 그 유형과 다르면, 로그 파일명 날짜와 실제 판정이 어긋나지 않게 맞는 날짜로 다시 뽑음.
    holiday_mode = meta.get("holiday_mode") or CFG.get("holiday_mode", "auto")
    _fixed = meta.get("run_date")  # A/B 오케스트레이터가 비교 1회당 1개로 정해 내려준 날짜
    if _fixed:
        # [A/B 날짜 고정] 워커가 날짜를 다시 뽑지 않고 오케스트레이터가 정한 날짜를 그대로 사용
        run_date = datetime.date.fromisoformat(str(_fixed))
        if holiday_mode == "today":
            holiday_mode = "auto"
        elif holiday_mode in ("weekday", "holiday") and is_holiday_date(run_date) != (holiday_mode == "holiday"):
            print(f"[경고] 고정 날짜 {run_date}가 '{holiday_mode}' 설정과 다른 종류입니다. 그래도 고정 날짜를 유지합니다.")
        print(f"[안내] A/B 고정 날짜 사용: {run_date}")
    else:
        if holiday_mode == "today":
            run_date = datetime.date.today()
            holiday_mode = "auto"
            print(f"[안내] 공휴일 미체크 -> 오늘({run_date})은 {'공휴일/주말' if is_holiday_date(run_date) else '평일'}로 판정해 실행합니다.")
        if holiday_mode in ("weekday", "holiday") and is_holiday_date(run_date) != (holiday_mode == "holiday"):
            run_date = pick_run_date(CFG, index=1 if holiday_mode == "holiday" else 0, mode="alternate")
    return run_date, holiday_mode


def _create_road_realism(run_date, sim_start_hour, spawn_manager, dispatcher, sumo_cfg):
    """[현실화] 실제 신호 운영계획(경찰청)과 시간대별 실측 속도 보정(TOPIS) 컨트롤러를 만든다.
    module1_simulation/signal_plans.py, speed_profile.py. 설정이 꺼져 있으면(signal_plans!="real",
    speed_profile!="topis") 각각 None. speed_profile="topis"이고 배차기가 도로망 이동 시간 표를 쓰면
    시간대별 표(TimeVaryingTravelTime)로 바꿔 끼운다 — 예측 재배치도 이 표를 같이 보도록
    _create_forecast_repositioner보다 먼저 호출해야 한다.
    반환: (signal_ctrl, speed_ctrl)"""
    from module1_simulation.signal_plans import SignalPlanController
    from module1_simulation.speed_profile import SpeedProfileController
    from module4_dispatch.network_cost import TimeVaryingTravelTime, net_file_from_sumocfg
    is_hol = bool(getattr(spawn_manager, "is_holiday", False))
    net_file = net_file_from_sumocfg(sumo_cfg)
    signal_ctrl = SignalPlanController.from_config(CFG, run_date, sim_start_hour, is_hol)
    shared_tt = None
    if dispatcher is not None and getattr(dispatcher, "travel_time", None) is not None \
            and CFG.get("speed_profile", "topis") == "topis":
        shared_tt = TimeVaryingTravelTime(net_file)
        dispatcher.travel_time = shared_tt          # 배차와 재배치가 같은 시간대별 이동 시간 표를 본다
    speed_ctrl = SpeedProfileController.from_config(CFG, net_file, run_date, sim_start_hour, is_hol,
                                                    travel_time=shared_tt)
    if signal_ctrl:
        print(f"[안내] 실제 신호 계획 적용: {', '.join(v['name'] for v in signal_ctrl.data.values())}")
    if speed_ctrl:
        print("[안내] 시간대별 실측 속도 보정 적용 (TOPIS)")
    return signal_ctrl, speed_ctrl


def _edge_speed_args(run_date, strategy, sim_start_hour):
    """[검증] edge_speed_output=폴더: 1시간 단위 도로별 평균 속도(SUMO edgeData)를 저장하는 traci.start 추가 인자.
    module1_simulation/checks/compare_topis_speed.py가 TOPIS 실측과 비교한다. 설정이 없으면 빈 목록."""
    if not CFG.get("edge_speed_output"):
        return []
    ed_dir = os.path.abspath(CFG["edge_speed_output"])
    os.makedirs(ed_dir, exist_ok=True)
    ed_name = f"edgespeed_{run_date.isoformat()}_{strategy}_h{sim_start_hour:02d}"
    ed_add = os.path.join(ed_dir, ed_name + ".add.xml")
    with open(ed_add, "w", encoding="utf-8") as f:
        f.write(f'<additional><edgeData id="hourly" period="3600" excludeEmpty="true" '
                f'file="{ed_name}.xml"/></additional>\n')
    return ["--additional-files", ed_add]


def _create_forecast_repositioner(meta, run_date, spawn_manager, dispatcher, sim_start_hour, sumo_cfg):
    """수요 예측(demand_v2)을 5분마다 쓰는 재배치기 + 동적 호출비 게시판을 만든다.
    - taxi_strategy="forecast": 예측 비율로 빈 택시 재배치 (+ 호출비 계산)
    - taxi_strategy="surge"   : 호출비(기사 인센티브)가 높은 칸으로 빈 택시 이동
    - 그 밖의 전략            : surge_enabled면 택시는 그대로 두고 호출비만 계산 (요금 비교용)
    반환: (재배치기 또는 None, CallFeeBoard 또는 None)
    module4_dispatch/forecast_repositioner.py, module4_dispatch/surge_pricing.py"""
    strategy = meta.get("taxi_strategy")
    pricing = bool(CFG.get("surge_enabled", True))
    if strategy not in ("forecast", "surge") and not pricing:
        return None, None
    if spawn_manager._real_model is None:
        if strategy in ("forecast", "surge"):
            raise SystemExit(f"{strategy} 전략은 실데이터 수요 모드(demand_source=real)에서만 쓸 수 있습니다.")
        print("[안내] 동적 호출비는 실데이터 수요 모드에서만 계산합니다 (이번 실행은 생략).")
        return None, None
    from module4_dispatch.forecast_repositioner import ForecastRepositioner
    from module4_dispatch.network_cost import NetworkTravelTime, net_file_from_sumocfg
    from module4_dispatch.surge_pricing import CallFeeBoard
    mode = strategy if strategy in ("forecast", "surge") else "observe"
    _tt = getattr(dispatcher, "travel_time", None) or NetworkTravelTime.cached(net_file_from_sumocfg(sumo_cfg))
    cells = sorted(set(meta["edge_cells"].values()))
    board = CallFeeBoard(cells, mean_fare_won=getattr(spawn_manager, "mean_fare_won", None))         if (pricing or mode == "surge") else None
    try:
        repositioner = ForecastRepositioner(
            meta, run_date, bool(spawn_manager.is_holiday), sim_start_hour, spawn_manager._real_model,
            spawn_manager.schedule, meta.get("passenger_seed") if meta.get("passenger_seed") is not None else 42,
            travel_time=_tt, mode=mode, fee_board=board)
    except (ValueError, FileNotFoundError, SystemExit) as e:
        if mode != "observe":
            raise
        print(f"[경고] 수요 예측 모델을 쓸 수 없어 동적 호출비를 계산하지 않습니다: {e}")
        return None, None
    if mode == "forecast":
        print(f"[안내] 예측 기반 재배치: {repositioner.tick_sec:.0f}초마다 칸별 "
              f"{repositioner.horizon * 10}분 수요 예측 -> 빈 택시 λ^{repositioner.power:.2f} 비율 배치")
    elif mode == "surge":
        print(f"[안내] 동적 호출비 재배치: {repositioner.tick_sec:.0f}초마다 칸별 호출비 -> "
              f"기사 인센티브 {board.settings['min_incentive']:.0f}원 이상인 칸으로 빈 택시 이동")
    if board is not None:
        st = board.settings
        print(f"[안내] 동적 호출비: {st['unit']:.0f}원 x (수요/(빈 택시+1) - 1), {st['step']:.0f}원 단위, "
              f"최대 {st['max']:.0f}원, 기사 몫 {st['driver_share']:.0%}")
    return repositioner, board


class PassengerTracker:
    """승객별 등장·탑승·하차 시각을 기록한다 (대기시간 계산과 Unity 기록기의 입력)."""

    def __init__(self, run_date, sim_start_hour):
        self.run_date, self.sim_start_hour = run_date, sim_start_hour
        self.depart_time = {}      # person_id -> depart 시각(초)
        self.pickup_time = {}      # person_id -> 탑승 확인된 시각(초)
        self.arrived_pids = set()  # 목적지까지 도착 완료(정상 하차)한 person_id
        self.in_taxi = set()       # 이미 탑승 처리된 person_id (중복 계산 방지)
        self.onboard = {}          # 지금 택시에 타고 있는 person_id -> taxi id (하차 감지용, 최대 택시 대수만큼만 유지)
        self.seen_persons = set()
        self.demand_log_rows = []  # (pickup_datetime_iso, latitude, longitude) — 구 data/raw/simulation_demand_log.csv

    def observe(self, now, current_pids, riding):
        """이번 스텝에 새로 등장한 승객과 새로 탄 승객을 기록. riding = {탑승 중 승객: 택시}."""
        for pid in current_pids:
            if pid not in self.seen_persons:
                self.seen_persons.add(pid)
                self.depart_time[pid] = now

                # 학습 데이터용: 등장 시점의 위치를 위경도로 변환해 기록
                try:
                    x, y = traci.person.getPosition(pid)
                    lon, lat = traci.simulation.convertGeo(x, y)
                    current_hour = self.sim_start_hour + (now / 3600.0)
                    pickup_dt = datetime.datetime.combine(
                        self.run_date, datetime.time(0, 0)
                    ) + datetime.timedelta(hours=current_hour)
                    self.demand_log_rows.append((pickup_dt.isoformat(), lat, lon))
                except Exception:
                    pass

            if pid not in self.in_taxi:
                vid = riding.get(pid, "")
                if vid:  # 빈 문자열이 아니면 = 어떤 택시에 탑승함
                    self.pickup_time[pid] = now
                    self.in_taxi.add(pid)
                    self.onboard[pid] = vid

    def detect_arrivals(self, now, current_pids, removed_pids, away_model=None):
        """탑승했던 사람이 이번 스텝에 더 이상 안 보이면(=목적지 도착해서 SUMO가 자동 제거함) "정상 도착"으로
        기록. 타임아웃으로 소멸한 사람과 구분하기 위함 (recorder.capture()의 arrived 인자 및 최종 검증용).
        택시 대수만큼만 확인한다."""
        for pid in [p for p in self.onboard if p not in current_pids]:
            vid = self.onboard.pop(pid)
            if pid in removed_pids:
                continue
            self.arrived_pids.add(pid)
            if away_model is not None:
                away_model.on_dropoff(now, pid, vid, self.pickup_time.get(pid))


def _print_progress(now, sim_start_hour, sim_end_seconds, log_recorder, tracker, pax_manager,
                    current_vids, current_pids, wall0, wall_prev):
    """진행 표시: 시뮬레이션 10분마다 한 줄 (suppress_sumo_warnings=true면 이것만 보임). 반환: 이번 실제 시각"""
    _hh = int(sim_start_hour + now / 3600); _mm = int((now % 3600) // 60)
    _w = time.time()
    print(f"[진행] 시뮬 {_hh:02d}:{_mm:02d} ({now/sim_end_seconds*100:.0f}%) | "
          f"호출 누적 {len(log_recorder.records)}건 | 탑승 {len(tracker.pickup_time)}명 | "
          f"타임아웃 {len(pax_manager.removed_pids)}명 | 택시 {len(current_vids)}대 | "
          f"[진단] 살아있는 승객 {len(current_pids)}명 · 제거 보류 {len(pax_manager._reserved_now)}명 | "
          f"실시간 +{_w - wall_prev:.0f}초 (누적 {(_w - wall0)/60:.1f}분)", flush=True)
    return _w


def _save_demand_logs(tracker, log_recorder, meta, spawn_manager, run_date):
    """학습용 수요 로그 저장 (시뮬레이션 1회당 한 번).
    write_demand_logs=false: 도구(택시 대수 산정 등)가 학습 로그 폴더를 오염시키지 않게 저장 생략"""
    _write_logs = bool(CFG.get("write_demand_logs", True))
    if tracker.demand_log_rows and _write_logs:
        os.makedirs(os.path.dirname(DEMAND_LOG_PATH), exist_ok=True)
        file_exists = os.path.exists(DEMAND_LOG_PATH)
        with open(DEMAND_LOG_PATH, "a", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            if not file_exists:
                writer.writerow(["pickup_datetime", "latitude", "longitude"])
            writer.writerows(tracker.demand_log_rows)
        print(f"[안내] 학습용 수요 로그 {len(tracker.demand_log_rows)}건을 {DEMAND_LOG_PATH}에 남겼습니다.")

    if log_recorder.records and _write_logs:
        # 파일명: demand_log_{구역}{전략}_{알고리즘}[공휴일]{yymmdd}.csv  (예: demand_log_강남역patrol_hungarian공휴일261003.csv)
        # demand_log_ 접두어는 train.py·pipeline.py의 demand_log_*.csv 글롭이 읽도록 유지.
        _region = re.sub(r'[\\/:*?"<>|\s]+', "", str(CFG.get("region", "")))
        _strategy = meta.get("taxi_strategy") or "unknown"
        _algo = meta.get("taxi_dispatch_algorithm") or "greedy"
        _dtag = "공휴일" if getattr(spawn_manager, "is_holiday", False) else ""  # 공휴일로 돌린 로그는 이름에 표시
        log_name = f"demand_log_{_region}{_strategy}_{_algo}{_dtag}{run_date.strftime('%y%m%d')}.csv"
        saved_path = log_recorder.save(filename=log_name)
        print(f"[안내] Module 2 호출 로그 {len(log_recorder.records)}건을 {saved_path}에 저장했습니다 "
              f"(train.py가 읽는 실제 로그입니다).")


def _summarize(tracker, pax_manager, meta, spawn_manager, run_date, now, sim_end_seconds,
               strategy, algorithm, dispatcher, away_model, forecast_repositioner,
               signal_ctrl=None, speed_ctrl=None, fee_board=None):
    """대기시간 지표와 실행 정보를 결과 dict로 정리한다.

    규칙: 차에 못 탄 채 wait_timeout이 지나면 소멸(확정). 탑승한 사람은 소멸 기한이 사라진다.
    소멸이 확정된 승객은 예약 때문에 물리 제거가 늦어져 나중에 태워지더라도 소멸로만 세고,
    대기시간 표본(waits)에서는 뺀다 (이중 집계 방지)."""
    depart_time, pickup_time = tracker.depart_time, tracker.pickup_time
    max_penalty_sec = meta.get("passenger_wait_timeout", 500)
    timeout_pids = pax_manager.removed_pids & set(depart_time)
    waits = []
    for pid, dtime in depart_time.items():
        if pid in pickup_time and pid not in timeout_pids:
            waits.append(pickup_time[pid] - dtime)

    n_total = len(depart_time)
    n_measured = len(waits)
    n_unpicked = n_total - n_measured  # 끝까지 못 탄 승객 (소멸 확정 승객 포함)
    n_timeout_removed = len(timeout_pids)

    # 타임아웃된 승객에게 최대 대기시간 페널티를 부여하여 왜곡 방지 (제때 탄 승객 + 페널티)
    adjusted_waits = list(waits)
    for _ in range(n_timeout_removed):
        adjusted_waits.append(max_penalty_sec)
    overlap = timeout_pids & set(pickup_time)
    if overlap:
        print(f"[진단] 소멸 확정 후 뒤늦게 태워진 승객 {len(overlap)}명 (소멸로만 집계, 대기시간 표본에서 제외)")

    return {
        "strategy": strategy,
        "algorithm": algorithm,
        "duration_sec": now,  # 마지막으로 관측된 시뮬레이션 시각(초) = 실제 돈 구간 길이
        "completed_interval": sim_end_seconds is not None and now >= sim_end_seconds,
        # 같은 승객 수요(등장 시각+좌표)로 실행됐는지 확인하는 지문. patrol vs forecast처럼
        # 서로 다른 배차 방식을 "같은 손님들"로 공정 비교했는지 검증할 때 씀
        # (spawn_manager의 seed가 config.json의 passenger_seed로 고정돼 있어야 두 실행의
        # 지문이 일치함 - 안 고정돼 있으면 매번 달라지는 게 정상이니 비교 목적이 아니면 무시해도 됨)
        "demand_fingerprint": hashlib.sha256(
            ",".join(f"{pid}:{t:.1f}" for pid, t in sorted(depart_time.items())).encode()
        ).hexdigest()[:16],
        "run_date": run_date.isoformat(),
        "is_holiday": bool(getattr(spawn_manager, "is_holiday", is_holiday_date(run_date))),
        "n_total_passengers": n_total,
        "n_measured": n_measured,
        "n_unpicked": n_unpicked,
        "n_timeout_removed": n_timeout_removed,
        "avg_wait_sec": round(sum(waits) / len(waits), 1) if waits else None,
        "true_avg_wait_sec_with_penalty": round(sum(adjusted_waits) / len(adjusted_waits), 1) if adjusted_waits else None,
        "max_wait_sec": round(max(waits), 1) if waits else None,
        "min_wait_sec": round(min(waits), 1) if waits else None,
        "p90_wait_sec": round(sorted(waits)[int(0.9 * (len(waits) - 1))], 1) if waits else None,
        # 서비스율 = 제때 태운 손님 / 전체 호출 (운영 KPI)
        "service_rate": round(n_measured / n_total, 4) if n_total else None,
        "demand_source": getattr(spawn_manager, "demand_source", "synthetic"),
        "n_scheduled_calls": len(getattr(spawn_manager, "schedule", []) or []) or None,
        "spawn_failures": getattr(spawn_manager, "spawn_failures", 0),
        "demand_summary": getattr(spawn_manager, "schedule_summary", None),
        # 추정 매출 = 태운 손님 수 x TIMS 실측 평균 요금(이 영역 격자, 2025) — 실데이터 모드에서만
        "est_revenue_won": (round(n_measured * spawn_manager.mean_fare_won)
                            if getattr(spawn_manager, "mean_fare_won", None) else None),
        "external_away": away_model.summary() if away_model is not None else None,
        "forecast_reposition": forecast_repositioner.summary() if forecast_repositioner is not None else None,
        # 동적 호출비: 미터 요금(TIMS 평균) + 호출 시점 칸별 호출비, 탑승한 손님만 (module4_dispatch/surge_pricing.py)
        "surge_pricing": (fee_board.summary([p for p in pickup_time if p not in timeout_pids])
                          if fee_board is not None else None),
        "signal_plans": signal_ctrl.summary() if signal_ctrl else None,
        "speed_profile": speed_ctrl.summary() if speed_ctrl else None,
        "dispatch_cost": getattr(dispatcher, "cost_mode", None),  # 헝가리안만 비용 방식이 있음
        "dispatch_cost_stats": (dict(dispatcher.cost_stats) if hasattr(dispatcher, "cost_stats") else None),
        "waits": waits,
    }


# max_steps 기본값을 24시간 전체 스텝(86400초 이상)을 충분히 커버할 수 있도록 확장
def run_and_measure(sumo_binary: str = "sumo", max_steps: int = 100000,
                     sumo_cfg_path: str = None, meta_path: str = None,
                     strategy: str = None, algorithm: str = None, recorder=None,
                     recorder_factory=None, step_hook=None) -> dict:
    """
    시뮬레이션을 headless(sumo, GUI 없음)로 끝까지 돌리면서 승객별 대기시간을 측정.
    택시는 taxi_manager.TaxiFleetManager가 항상 target_count(=num_taxis)만큼 유지함
    (도착해서 사라진 택시는 즉시 길 끝에서 재스폰).
    반환: {"avg_wait_sec", "n_measured", "n_unpicked", "demand_fingerprint", ..., "waits": [...]} (_summarize 참고)

    strategy/algorithm: 지정하면 실행용 설정에 반영하고 build_env.py를 다시 돌린 뒤 시작함
        (export_unity_replay.py처럼 "이 조합으로 새로 지도까지 다시 만들어서 측정해줘"가 필요한
        호출부용. 안 넘기면 "지금 설정/meta 그대로" 바로 측정함).
    recorder: module4_dispatch나 외부(export_unity_replay.py의 ReplayRecorder 등)에서 만든
        기록기. recorder.capture(now, requested, pickup, arrived, removed, failed, forecast,
        teleports)를 매 스텝 호출하고, 끝나면 recorder.finish(result)를 호출함. 안 넘기면
        (기본값) 기록 없이 예전과 완전히 동일하게 동작 - 성능/결과에 영향 없음.
    step_hook: 매 스텝 끝에 step_hook(now, sim_start_hour)를 호출 (GUI 실행의 화면 지연·창 제목 표시용,
        module1_simulation/run_simulation.py). 안 넘기면 아무 일도 하지 않음.

    흐름: 설정·환경 준비 → 컨트롤러 생성(_create_controllers) → 실행 날짜(_resolve_run_date) → 승객 생성기·
    예측 재배치 준비 → 스텝 루프(배차·재배치·승객 생성·기록) → 로그 저장(_save_demand_logs) → 결과(_summarize)
    """
    if strategy is not None:
        set_strategy_in_config(strategy)
    if algorithm is not None:
        set_dispatch_algorithm_in_config(algorithm)
    if strategy is not None or algorithm is not None:
        rebuild_env()
    if recorder is None and recorder_factory is not None:
        # 지도(map.json)를 방금 다시 만든 환경 기준으로 만들도록 기록기를 재빌드 이후에 생성
        recorder = recorder_factory()

    _meta_path = meta_path or META_PATH
    _sumo_cfg = sumo_cfg_path or SUMO_CFG
    with open(_meta_path, "r", encoding="utf-8") as f:
        meta = json.load(f)

    manager, dispatcher, pax_manager, rl_maintainer = _create_controllers(meta, _sumo_cfg)

    sim_end_hour = meta.get("sim_end_hour")
    sim_start_hour = meta.get("sim_start_hour", 0)
    if sim_end_hour is None:
        sim_end_hour = CFG.get("sim_end_hour")  # 실행 중인 설정(MOBILITY_CONFIG 반영)에서 폴백
        if sim_end_hour is not None:
            print(f"[안내] runtime_meta.json에 sim_end_hour가 없어 설정에서 읽어왔습니다 ({sim_end_hour}시).")

    run_date, holiday_mode = _resolve_run_date(meta)
    # RL 재배치가 쓰는 수요예측 모델에도 이번 실행 날짜를 알려줌 (요일·공휴일 피처)
    # — 과거 호출 기록은 승객 매니저(실측 수요 모델)를 만든 뒤 아래에서 넣는다
    if rl_maintainer is not None and hasattr(rl_maintainer, "set_run_date"):
        rl_maintainer.set_run_date(run_date)
    print(f"[안내] 이번 실행 날짜: {run_date} ({'공휴일/주말' if is_holiday_date(run_date) else '평일'}, "
          f"run_date_mode={CFG.get('run_date_mode', 'alternate')})")

    # 학교/회사/음식점(가정 모델) 또는 실측 보정 스케줄로 승객을 생성하는 매니저
    spawn_manager = PassengerSpawnManager(
        zones=meta.get("zones", {}),
        department_edges=meta.get("department_store_edges", []),
        sim_start_hour=sim_start_hour,
        sim_end_hour=sim_end_hour,
        school_pop_base=meta.get("school_pop_base", 400),
        company_pop_base=meta.get("company_pop_base", 100),
        seed=meta.get("passenger_seed"),
        sim_date=run_date.isoformat(),
        holiday_mode=holiday_mode,
        meta=meta,
    )

    if rl_maintainer is not None and getattr(spawn_manager, "_real_model", None) is not None:
        rl_maintainer.set_run_date(run_date, demand_model=spawn_manager._real_model,
                                   sim_start_hour=sim_start_hour, seed=meta.get("passenger_seed"),
                                   is_holiday=bool(getattr(spawn_manager, "is_holiday", is_holiday_date(run_date))))

    # [시나리오] 영역 밖으로 나간 택시의 복귀 지연 (taxi_external_away=true일 때만)
    away_model = ExternalTripAwayModel.from_config(CFG, manager, spawn_manager, seed=meta.get("passenger_seed"))
    # [현실화] 신호·속도 보정 — 예측 재배치가 같은 시간대별 이동 시간 표를 쓰도록 재배치기보다 먼저 만든다
    signal_ctrl, speed_ctrl = _create_road_realism(run_date, sim_start_hour, spawn_manager, dispatcher, _sumo_cfg)
    forecast_repositioner, fee_board = _create_forecast_repositioner(meta, run_date, spawn_manager, dispatcher,
                                                                     sim_start_hour, _sumo_cfg)

    sim_end_seconds = (sim_end_hour - sim_start_hour) * 3600 if sim_end_hour is not None else None
    if sim_end_seconds is None:
        print("[경고] sim_end_hour를 어디서도 찾지 못해 안전상 24시간 분량으로 자동 종료합니다.")
        sim_end_seconds = 24 * 3600
    max_steps = max(max_steps, int(sim_end_seconds) + 100)

    # [Module 2] train.py(pipeline.py 경유)가 읽는 data/sim_logs/demand_log_*.csv 기록기 — 실질적인 학습 로그.
    # [2026-09-22 날짜 충돌 수정] sim_date를 고정값으로 두면 실행마다 같은 달력 날짜로 기록돼서
    # aggregate_demands()의 groupby가 서로 다른 실행의 수요를 같은 시간칸으로 합쳐버림 -> run_date를 넘겨
    # 실행마다 서로 다른 달력 날짜를 쓰게 함 (시간칸 충돌 방지 + dayofweek/is_weekend 분산 확보).
    log_recorder = DemandLogRecorder(
        sim_start_hour=sim_start_hour, zones=meta.get("zones", {}), sim_date=run_date.isoformat(),
        spawn_manager=spawn_manager,
    )
    tracker = PassengerTracker(run_date, sim_start_hour)
    teleport_count = 0

    vehicle_subscriptions.reset()   # 새 SUMO 실행: 구독 기록 초기화
    traci.start([sumo_binary, "-c", _sumo_cfg]
                + _edge_speed_args(run_date, meta.get("taxi_strategy") or "unknown", sim_start_hour))

    step = 0
    now = 0.0                 # 첫 스텝 전에 연결이 끊겨도 결과 정리가 가능하도록
    _wall0 = time.time()      # [진단] 실제 경과 시간 측정용
    _wall_prev = _wall0
    try:
        while step < max_steps:
            traci.simulationStep()
            now = traci.simulation.getTime()
            if signal_ctrl:
                signal_ctrl.maintain(now)
            if speed_ctrl:
                speed_ctrl.maintain(now)
            # [성능] 맵 전체 상태 조회는 스텝당 딱 한 번. 하위 매니저/기록기는 이 값을 재사용한다.
            current_pids = set(traci.person.getIDList())
            current_vids = set(traci.vehicle.getIDList())
            # [하차 감지] 배차보다 먼저: 이번 스텝에 내린 택시가 같은 스텝에 다음 배차를 받기 전에
            # 복귀 지연 시나리오(taxi_external_away)가 영역 밖으로 나간 택시를 뺄 수 있게 한다.
            # (예전에는 배차 뒤에 감지해서, 택시가 부족한 날엔 거의 모든 택시가 '이미 배차됨'으로 빠지지 않았다)
            tracker.detect_arrivals(now, current_pids, pax_manager.removed_pids, away_model)
            if away_model is not None and away_model.removed_this_step:
                current_vids -= away_model.removed_this_step
                away_model.removed_this_step = set()
            idle_taxis = fetch_idle_taxis() if (dispatcher or recorder is not None) else None  # RL 단독이면 RL이 1분마다 직접 조회
            manager.maintain(now, current_vids=current_vids)  # 도착 임박한 택시에 새 목적지를 얹어 소멸을 막음 (대수 유지)
            pax_manager.maintain(now, current_pids=current_pids)  # 대기시간 초과 승객 소멸 처리
            if dispatcher:
                dispatched_now = dispatcher.maintain(now, idle_taxis=idle_taxis)  # 파이썬 배차 (hungarian/custom_*)
                if dispatched_now:  # 방금 배정된 택시는 RL 재배치 후보/기록기의 idle 목록에서 제외
                    _busy = set(dispatched_now)
                    idle_taxis = [v for v in idle_taxis if v not in _busy]
            if forecast_repositioner is not None:
                forecast_repositioner.maintain(now, idle_taxis=idle_taxis)
            if rl_maintainer:
                rl_maintainer.maintain(now, sim_start_hour=sim_start_hour, sim_end_hour=sim_end_hour,
                                       current_pids=current_pids, idle_taxis=idle_taxis)

            spawn_manager.maintain(now, timeout_removed_pids=pax_manager.removed_pids, current_pids=current_pids)  # 승객 생성·소멸
            log_recorder.step(now, timeout_removed_pids=pax_manager.removed_pids, current_pids=current_pids)  # [Module 2] 호출 로그 기록

            if int(now) % 600 == 0 and now > 0:
                _wall_prev = _print_progress(now, sim_start_hour, sim_end_seconds, log_recorder, tracker, pax_manager,
                                             current_vids, current_pids, _wall0, _wall_prev)

            # 승객 등장·탑승(riding_map: 택시 대수만큼만 TraCI 호출). 하차 감지는 배차보다 먼저 한다 (루프 위쪽 [하차 감지])
            _n_seen = len(tracker.seen_persons)
            tracker.observe(now, current_pids, riding_map(now, current_vids))
            if fee_board is not None and len(tracker.seen_persons) != _n_seen:
                # 새로 호출한 손님: 그 순간 출발 칸의 호출비를 확정 (동적 호출비)
                for pid in current_pids:
                    if pid not in fee_board.fee_of_pid:
                        try:
                            fee_board.quote(pid, meta["edge_cells"].get(traci.person.getRoadID(pid)))
                        except traci.exceptions.TraCIException:
                            fee_board.quote(pid, None)

            if recorder is not None:
                teleport_count += traci.simulation.getStartingTeleportNumber()
                recorder.capture(
                    now, tracker.depart_time, tracker.pickup_time, tracker.arrived_pids,
                    pax_manager.removed_pids, set(), rl_maintainer or forecast_repositioner, teleport_count,
                    vehicle_ids=current_vids, idle_taxis=idle_taxis, person_ids=current_pids,
                    is_last=sim_end_seconds is not None and now >= sim_end_seconds,
                )

            if step_hook is not None:
                step_hook(now, sim_start_hour)

            # 설정한 시간대(sim_end_hour)에 도달하면 승객이 남아있어도 강제 종료.
            # 승객이 잠깐 0명이어도 중간에 끝내지 않는다 (조기 종료 제거).
            if sim_end_seconds is not None and now >= sim_end_seconds:
                break
            step += 1
    except traci.exceptions.FatalTraCIError as e:
        # SUMO 프로세스 자체가 죽어서 연결이 끊긴 경우(GUI 창을 닫았거나, 이미 예약된 승객을 강제 제거하다
        # SUMO 내부 상태가 깨지는 등). 지저분한 스택트레이스로 죽는 대신 지금까지 쌓인 결과(호출 로그,
        # 대기시간 등)를 그대로 살려서 아래 결과 계산/저장으로 넘어간다.
        print(f"[경고] 시뮬레이션이 중간에 끊겼습니다 (SUMO 연결 끊김, step={step}, "
              f"마지막 정상 시각={now:.0f}초): {e}")
        print("[경고] 지금까지 쌓인 부분 결과로 계속 진행합니다 (완전한 결과가 아님에 주의).")
    finally:
        try:
            traci.close()
        except Exception:
            pass

    result = _summarize(tracker, pax_manager, meta, spawn_manager, run_date, now, sim_end_seconds,
                        strategy, algorithm, dispatcher, away_model, forecast_repositioner,
                        signal_ctrl=signal_ctrl, speed_ctrl=speed_ctrl, fee_board=fee_board)
    _save_demand_logs(tracker, log_recorder, meta, spawn_manager, run_date)
    if recorder is not None:
        recorder.finish(result)
    return result


def set_strategy_in_config(strategy: str):
    """이번 실행의 taxi_strategy를 바꾼다. config.json은 그대로 두고 실행용 설정 파일(results/run_configs/)에
    반영해 이후 rebuild_env()의 build_env.py와 이 프로세스가 같은 값을 읽게 한다."""
    use_run_config({"taxi_strategy": strategy}, "strategy")


def rebuild_env():
    """build_env.py를 다시 실행해서 현재 설정(MOBILITY_CONFIG 또는 config.json) 기준으로 도로망/승객/택시를 재생성"""
    import subprocess
    build_env_path = os.path.join(ROOT, "module1_simulation", "build_env.py")
    subprocess.run([sys.executable, build_env_path], check=True, cwd=ROOT)


def print_result(label: str, result: dict):
    print(f"\n===== [{label}] 결과 =====")
    print(f" - 전체 승객 수: {result['n_total_passengers']}")
    print(f" - 탑승 성공: {result['n_measured']}명 / 끝까지 못 탄 승객: {result['n_unpicked']}명 "
          f"(그중 대기시간 초과로 소멸: {result.get('n_timeout_removed', 0)}명)")
    if result["avg_wait_sec"] is not None:
        print(f" - 평균 대기시간(탑승자만): {result['avg_wait_sec']}초 (약 {result['avg_wait_sec']/60:.1f}분)")
        print(f" - 최소/최대 대기시간: {result['min_wait_sec']}초 / {result['max_wait_sec']}초")
    if result.get("true_avg_wait_sec_with_penalty") is not None:
        v = result["true_avg_wait_sec_with_penalty"]
        print(f" - 평균 대기시간(타임아웃 페널티 포함, 체감치): {v}초 (약 {v/60:.1f}분)")
    else:
        print(" - 탑승한 승객이 없어 대기시간을 계산할 수 없습니다.")


def _compare_two(kind: str, options, apply_option):
    """options 두 개를 순차 실행해서 평균 대기시간 비교. apply_option(값)이 이번 실행 설정을 바꾼다
    (config.json은 그대로, 실행용 설정 파일 사용). kind는 출력용 이름("전략"/"알고리즘")."""
    results = {}
    for opt in options:
        print(f"\n########## {kind} '{opt}' 시뮬레이션 준비 중 ##########")
        apply_option(opt)
        rebuild_env()
        result = run_and_measure()
        results[opt] = result
        print_result(opt, result)

    name_a, name_b = options
    print(f"\n===== [최종 비교] {name_a} vs {name_b} =====")
    # 타임아웃 페널티 포함 지표로 비교 -- 배차 실패(타임아웃)가 많은 쪽이 픽업만 기준으로는
    # 오히려 좋게 보이는 착시를 막기 위함 (실제로 greedy 56.3% 개선 → 타임아웃 포함 14.5%였던 사례)
    a_metric = results[name_a].get("true_avg_wait_sec_with_penalty")
    b_metric = results[name_b].get("true_avg_wait_sec_with_penalty")
    if a_metric is not None and b_metric is not None:
        diff = a_metric - b_metric
        pct = (diff / a_metric * 100) if a_metric else 0
        better = name_b if diff > 0 else name_a
        print(f" - {name_a} 평균 대기(타임아웃 포함): {a_metric}초")
        print(f" - {name_b} 평균 대기(타임아웃 포함): {b_metric}초")
        print(f" - 차이: {abs(diff):.1f}초 ({abs(pct):.1f}%) - '{better}' {kind}이 더 나음")
    else:
        print(" - 둘 중 하나 이상에서 측정 실패 (탑승자 없음). 시간대/승객 설정을 확인하세요.")
    return results


def compare_strategies():
    """patrol vs prepositioned 두 전략을 순차 실행해서 비교"""
    return _compare_two("전략", ("patrol", "prepositioned"), set_strategy_in_config)


def set_dispatch_algorithm_in_config(algo: str):
    """이번 실행의 taxi_dispatch_algorithm을 바꾼다 (config.json은 그대로, set_strategy_in_config 참고)."""
    use_run_config({"taxi_dispatch_algorithm": algo}, "dispatch")


def compare_dispatch_algorithms(algo_a: str = "greedy", algo_b: str = "hungarian"):
    """두 배차 알고리즘(A vs B)을 순차 실행해서 평균 대기시간 비교"""
    return _compare_two("알고리즘", (algo_a, algo_b), set_dispatch_algorithm_in_config)


if __name__ == "__main__":
    from module1_simulation.reproducibility import ensure_fixed_hash_seed
    ensure_fixed_hash_seed()
    mode = sys.argv[1] if len(sys.argv) > 1 else "current"

    if mode == "compare":
        compare_strategies()
    elif mode in ("patrol", "prepositioned"):
        set_strategy_in_config(mode)
        rebuild_env()
        result = run_and_measure()
        print_result(mode, result)
    elif mode == "dispatch_compare":
        algo_a = sys.argv[2] if len(sys.argv) > 2 else "greedy"
        algo_b = sys.argv[3] if len(sys.argv) > 3 else "hungarian"
        compare_dispatch_algorithms(algo_a, algo_b)
    else:
        # config.json에 이미 설정된 전략 그대로, 재생성 없이 현재 rou.xml/sumocfg로 측정만
        result = run_and_measure()
        print_result("current config", result)