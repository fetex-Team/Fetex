"""GUI 시각화 실행 및 승객 대기시간 계측을 통합 수행하는 시뮬레이션 모듈."""
import os
import sys
import json
import time
import datetime
import random
import csv
from pathlib import Path

# 프로젝트 루트(module1_simulation의 부모 폴더)를 sys.path에 추가 — import보다 먼저!
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
MODULE1_DIR = os.path.dirname(os.path.abspath(__file__))
if MODULE1_DIR not in sys.path:
    sys.path.insert(0, MODULE1_DIR)

import traci

from module1_simulation.taxi_manager import TaxiFleetManager
from module1_simulation.passenger_manager import PassengerTimeoutManager
from module1_simulation.passenger_spawn_manager import PassengerSpawnManager
from config_loader import CFG
from module1_simulation.holiday_utils import pick_run_date
from module4_dispatch.custom_dispatchers import make_dispatcher
from module1_simulation.step_snapshot import fetch_idle_taxis
from module4_dispatch.rl_reposition_maintainer import RLRepositionMaintainer

SUMO_CFG = os.path.join(ROOT, "module1_simulation", "sumo_config", "simulation.sumocfg")
META_PATH = os.path.join(ROOT, "module1_simulation", "sumo_config", "runtime_meta.json")
DEMAND_LOG_PATH = os.path.join(ROOT, "data", "raw", "simulation_demand_log.csv")


def print_result(label: str, result: dict):
    print(f"\n===== [{label}] 결과 =====")
    print(f" - 전체 승객 수: {result['n_total_passengers']}")
    print(f" - 탑승 성공: {result['n_measured']}명 / 끝까지 못 탄 승객: {result['n_unpicked']}명 "
          f"(그중 대기시간 초과로 소멸: {result.get('n_timeout_removed', 0)}명)")
    if result.get("avg_wait_sec") is not None:
        print(f" - 평균 대기시간(탑승자만): {result['avg_wait_sec']}초 (약 {result['avg_wait_sec']/60:.1f}분)")
        print(f" - 최소/최대 대기시간: {result['min_wait_sec']}초 / {result['max_wait_sec']}초")
    if result.get("true_avg_wait_sec_with_penalty") is not None:
        v = result["true_avg_wait_sec_with_penalty"]
        print(f" - 평균 대기시간(타임아웃 페널티 포함, 체감치): {v}초 (약 {v/60:.1f}분)")
    else:
        print(" - 탑승한 승객이 없어 대기시간을 계산할 수 없습니다.")


def run_sumo_gui(sumo_binary: str = "sumo-gui", output_dir=None, gui_delay: float = 0.01) -> dict:
    if 'SUMO_HOME' in os.environ:
        tools = os.path.join(os.environ['SUMO_HOME'], 'tools')
        if tools not in sys.path:
            sys.path.append(tools)

    sumo_cfg = SUMO_CFG
    meta_path = META_PATH

    print("SUMO Digital Twin 시뮬레이션을 시작합니다...")

    # build_env.py가 저장해둔 edges/boundary_edges/전략 정보 로드 (택시 대수 유지에 필요)
    with open(meta_path, "r", encoding="utf-8") as f:
        meta = json.load(f)

    sim_start_hour = meta.get("sim_start_hour", 0)

    manager = TaxiFleetManager(
        target_count=meta["num_taxis"],
        boundary_edges=meta["boundary_edges"],
        all_edges=meta["edges"],
        strategy=meta["taxi_strategy"],
        hotspot_edges=meta.get("hotspot_edges"),
        zones=meta.get("zones"),
        sim_start_hour=sim_start_hour,
        remaining_edges_threshold=meta.get("taxi_remaining_edges_threshold"),
    )

    # 파이썬 배차기(hungarian/custom_*). SUMO 내장 알고리즘이면 None
    hungarian_dispatcher = make_dispatcher(meta.get("taxi_dispatch_algorithm"), sumo_cfg)

    # 배차 못 받고 너무 오래 대기한 승객을 소멸시키는 매니저 (없으면 끝까지 길가에 쌓임)
    pax_manager = PassengerTimeoutManager(
        wait_timeout_sec=meta.get("passenger_wait_timeout", 900),
        dispatcher=hungarian_dispatcher,
    )
    # RL은 택시의 선배치만 담당한다. 매칭은 위의 GUI 설정(greedy/hungarian)을 그대로 쓴다.
    rl_maintainer = None
    if meta.get("taxi_strategy") == "rl_prepositioned":
        model_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                  "module4_dispatch", "rl_reposition_model.zip")
        category_model_path = os.environ.get(
            "AI_CATEGORY_MODEL_PATH",
            os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         "saved_models", "category_demand_models.pkl"),
        )
        if os.path.exists(model_path):
            rl_maintainer = RLRepositionMaintainer(model_path, meta, category_model_path=category_model_path)
            print(f"[안내] RL 선배치 정책을 불러왔습니다: {model_path}")
        else:
            print(f"[경고] RL 모델이 없어 선배치 없이 '{meta.get('taxi_dispatch_algorithm')}' 매칭만 실행합니다.")

    sim_end_hour = meta.get("sim_end_hour")
    if sim_end_hour is None:
        # runtime_meta.json에 sim_end_hour가 없는 경우(구버전 build_env.py로 생성됐거나
        # 저장 누락) 실행 중인 설정(MOBILITY_CONFIG 반영)에서 폴백. 이게 없으면 max_steps=9999까지
        # 그냥 다 돌아버려서 설정한 시간대가 무시되는 문제가 있었음.
        sim_end_hour = CFG.get("sim_end_hour")
        if sim_end_hour is not None:
            print(f"[안내] runtime_meta.json에 sim_end_hour가 없어 설정에서 읽어왔습니다 ({sim_end_hour}시).")

    # 학교/회사/음식점 승객을 실시간으로 생성·소멸(흡수→재스폰)시키는 매니저
    spawn_manager = PassengerSpawnManager(
        zones=meta.get("zones", {}),
        department_edges=meta.get("department_store_edges", []),
        sim_start_hour=sim_start_hour,
        sim_end_hour=sim_end_hour,
        school_pop_base=meta.get("school_pop_base", 400),
        company_pop_base=meta.get("company_pop_base", 100),
        seed=meta.get("passenger_seed"),
        sim_date=pick_run_date(CFG, mode="sim_date").isoformat(),  # [공휴일] 설정창 sim_date로 평일/공휴일 판정
    )

    sim_end_seconds = (sim_end_hour - sim_start_hour) * 3600 if sim_end_hour is not None else None

    # sim_end_seconds도 못 구했으면(둘 다 없음) 무한정 도는 걸 막기 위한 최후 안전장치로
    # 하루(24시) 분량으로 상한을 둠 (기존 max_steps=9999 하드코딩이 실질적 상한 역할을 하던 문제 방지)
    if sim_end_seconds is None:
        print("[경고] sim_end_hour를 어디서도 찾지 못해 안전상 24시간 분량으로 자동 종료합니다.")
        sim_end_seconds = 24 * 3600

    depart_time = {}     # person_id -> depart 시각(초)
    pickup_time = {}     # person_id -> 탑승 확인된 시각(초)
    in_taxi = set()      # 이미 탑승 처리된 person_id (중복 계산 방지)
    seen_persons = set()
    demand_log_rows = []
    run_date = datetime.date.today() - datetime.timedelta(days=random.randint(0, 90))

    try:
        traci.start([sumo_binary, "-c", sumo_cfg])

        step = 0
        max_steps = int(sim_end_seconds) + 100  # 종료시각보다 넉넉히 큰 안전 상한 (실제 종료는 아래 sim_end_seconds 체크가 담당)

        while step < max_steps:
            traci.simulationStep()
            now_seconds = traci.simulation.getTime()
            # [성능] 맵 전체 상태 조회는 스텝당 딱 한 번. 하위 매니저는 이 값을 재사용한다.
            current_pids = set(traci.person.getIDList())
            current_vids = set(traci.vehicle.getIDList())
            idle_taxis = fetch_idle_taxis() if hungarian_dispatcher else None  # RL 단독이면 RL이 의사결정 시점(1분마다)에 직접 조회
            manager.maintain(now_seconds, current_vids=current_vids)  # 도착 임박한 택시에 새 목적지를 얹어 소멸을 막음 (대수 유지)
            pax_manager.maintain(now_seconds, current_pids=current_pids)
            if hungarian_dispatcher:
                dispatched_now = hungarian_dispatcher.maintain(now_seconds, idle_taxis=idle_taxis)
                if dispatched_now:  # 방금 배정된 택시는 RL 재배치 후보에서 제외 (stop 충돌 방지)
                    _busy = set(dispatched_now)
                    idle_taxis = [v for v in idle_taxis if v not in _busy]
            if rl_maintainer:
                rl_maintainer.maintain(now_seconds, sim_start_hour=sim_start_hour, sim_end_hour=sim_end_hour,
                                       current_pids=current_pids, idle_taxis=idle_taxis)
            spawn_manager.maintain(now_seconds, timeout_removed_pids=pax_manager.removed_pids, current_pids=current_pids)

            # 이번 스텝에 새로 등장한 person 기록 및 탑승 판정
            for pid in current_pids:
                if pid not in seen_persons:
                    seen_persons.add(pid)
                    depart_time[pid] = now_seconds

                    try:
                        x, y = traci.person.getPosition(pid)
                        lon, lat = traci.simulation.convertGeo(x, y)
                        current_hour = sim_start_hour + (now_seconds / 3600.0)
                        pickup_dt = datetime.datetime.combine(
                            run_date, datetime.time(0, 0)
                        ) + datetime.timedelta(hours=current_hour)
                        demand_log_rows.append((pickup_dt.isoformat(), lat, lon))
                    except Exception:
                        pass

                if pid not in in_taxi:
                    try:
                        vid = traci.person.getVehicle(pid)
                    except traci.exceptions.TraCIException:
                        vid = ""  # 이번 스텝에 타임아웃 매니저가 제거한 승객
                    if vid:
                        pickup_time[pid] = now_seconds
                        in_taxi.add(pid)

            if step % 100 == 0:
                sc = spawn_manager.spawn_counts
                print(
                    f"[DEBUG] time={now_seconds:.0f}s | "
                    f"vehicles={len(current_vids)} | "
                    f"persons={len(current_pids)}"
                    f" | 누적 생성={spawn_manager.total_spawned} "
                    f"(학교아침={sc['school_morning']} 회사아침={sc['company_morning']} "
                    f"점심출={sc['lunch_release']} 점심복귀={sc['lunch_return']} "
                    f"학교하교={sc['school_afternoon']} 퇴근={sc['evening']} "
                    f"주거가챠={sc['residential_gacha']} 음식점시민={sc['restaurant_civilian']}) "
                    f"| 누적 타임아웃={spawn_manager.total_timeout}"
                )

            if gui_delay > 0:
                time.sleep(gui_delay)

            # SUMO 창 타이틀바에 실제 시각(시:분:초) 출력
            current_hour_float = sim_start_hour + (now_seconds / 3600.0)
            h = int(current_hour_float) % 24
            m = int((current_hour_float - int(current_hour_float)) * 60)
            s = int((((current_hour_float - int(current_hour_float)) * 60) - m) * 60)
            time_str = f"{h:02d}:{m:02d}:{s:02d}"

            try:
                view_ids = traci.gui.getViewIDList()
                if view_ids:
                    traci.gui.setWindowCaption(
                        view_ids[0],
                        f"DT Mobility Simulation | Time: {time_str} (Elapsed: {int(now_seconds)}s)"
                    )
            except Exception:
                pass

            # 설정한 시간대(sim_end_hour)에 도달하면 승객이 남아있어도 강제 종료.
            if sim_end_seconds is not None and now_seconds >= sim_end_seconds:
                print(f" -> [안내] 설정한 시간대({sim_end_hour}시)에 도달하여 시뮬레이션을 종료합니다. (Step: {step})")
                break

            step += 1

        traci.close()
        print("시뮬레이션이 정상적으로 종료되었습니다.")

    except Exception as e:
        print(f"시뮬레이션 실행 중 오류 발생: {e}")
        try:
            traci.close()
        except Exception:
            pass

    # 대기시간 지표 계산
    waits = []
    for pid, dtime in depart_time.items():
        if pid in pickup_time:
            waits.append(pickup_time[pid] - dtime)

    n_total = len(depart_time)
    n_measured = len(waits)
    n_timeout_removed = len(pax_manager.removed_pids)
    n_unpicked = n_total - n_measured

    # [수정] 타임아웃된 승객에게 최대 대기시간 페널티를 부여하여 왜곡 방지
    max_penalty_sec = meta.get("passenger_wait_timeout", 500)
    adjusted_waits = list(waits)
    for _ in range(n_timeout_removed):
        adjusted_waits.append(max_penalty_sec)

    if demand_log_rows:
        try:
            os.makedirs(os.path.dirname(DEMAND_LOG_PATH), exist_ok=True)
            file_exists = os.path.exists(DEMAND_LOG_PATH)
            with open(DEMAND_LOG_PATH, "a", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                if not file_exists:
                    writer.writerow(["pickup_datetime", "latitude", "longitude"])
                writer.writerows(demand_log_rows)
            print(f"[안내] 학습용 수요 로그 {len(demand_log_rows)}건을 {DEMAND_LOG_PATH}에 남겼습니다.")
        except Exception as e:
            print(f"[경고] 수요 로그 저장 중 오류: {e}")

    result = {
        "n_total_passengers": n_total,
        "n_measured": n_measured,
        "n_unpicked": n_unpicked,
        "n_timeout_removed": n_timeout_removed,
        "avg_wait_sec": round(sum(waits) / len(waits), 1) if waits else None,
        "true_avg_wait_sec_with_penalty": round(sum(adjusted_waits) / len(adjusted_waits), 1) if adjusted_waits else None,
        "max_wait_sec": round(max(waits), 1) if waits else None,
        "min_wait_sec": round(min(waits), 1) if waits else None,
        "waits": waits,
    }

    print_result("GUI 시뮬레이션", result)

    # 결과 디렉터리가 지정되었거나 기본 경로가 있으면 저장
    target_output_dir = output_dir or os.path.join(ROOT, "results", "simulation")
    if target_output_dir:
        try:
            out_path = Path(target_output_dir)
            out_path.mkdir(parents=True, exist_ok=True)
            with open(out_path / "metrics.json", "w", encoding="utf-8") as f:
                json.dump(result, f, ensure_ascii=False, indent=2)
            print(f"[안내] 시뮬레이션 결과가 {out_path / 'metrics.json'}에 저장되었습니다.")
        except Exception as e:
            print(f"[경고] 결과 저장 실패: {e}")

    return result


if __name__ == "__main__":
    run_sumo_gui()
