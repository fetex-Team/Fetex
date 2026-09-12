"""프로젝트 전체 설정의 단일 원본."""
import json
import os

ROOT = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.environ.get("MOBILITY_CONFIG", os.path.join(ROOT, "config.json"))

DEFAULT_CONFIG = {
    # Module 1 - simulation / map
    "grid_x": 3, "grid_y": 3, "grid_length": 200,
    "num_normal_cars": 20, "num_taxis": 20, "num_auto_cars": 1,
    "num_obstacles": 2, "num_passengers": 1000,
    "region": "강남역", "use_real_map": True,
    "sim_start_hour": 8, "sim_end_hour": 10,
    "sim_date": "2026-09-07",  # 시뮬레이션 로그의 날짜 (요일/주말 피처 계산용, 2026-09-07=월요일)
    "passenger_wait_timeout": 500,
    "passenger_seed": 42,
    "passenger_mode": "replay", "sim_date": "2026-08-31",
    "training_days": 42, "synthetic_rate": 0.4,
    "validation_size": 0.2, "forecast_horizon": 6,
    "sequence_length": 12, "reposition_fraction": 0.5,
    "taxi_strategy": "patrol",
    "depart_jitter_sec": 300.0,
    "sumo_time_to_teleport": 300,
    "taxi_remaining_edges_threshold": 2,
    "taxi_target_pick_attempts": 10,
    "taxi_primary_prob": 0.70,
    "taxi_fail_threshold": 5,
    "taxi_respawn_prefix": "taxi_respawn",
    "taxi_dispatch_algorithm": "greedy",
    "taxi_idle_algorithm": "randomCircling",
    "suppress_sumo_warnings": False,
    # Dynamic passenger population / modal split
    "school_pop_base": 400,
    "company_pop_base": 100,
    "residential_base_pop_per_edge": 200,
    "residential_schedule_scale": 0.10,
    "residential_out_hour": 7.0, "residential_out_probability": 0.15,
    "residential_evening_in_hour": 19.0, "residential_evening_in_probability": 0.15,
    "residential_late_in_hour": 22.0, "residential_late_in_probability": 0.20,
    # num_passengers 기반 독립 가챠 생성 (기존 시간대 로직과 별개, 병행 동작)
    "residential_taxi_probability": 0.10,
    "school_start_hour": 7.0, "school_end_hour": 8.0,
    "school_taxi_peak_hour": 8.0,
    "school_afternoon_start_hour": 16.0, "school_afternoon_end_hour": 17.0,
    "school_afternoon_fraction": 0.01,
    "company_start_hour": 8.0, "company_end_hour": 10.0,
    "company_taxi_peak_hour": 10.0,
    "lunch_start_hour": 12.0, "lunch_end_hour": 13.0,
    "lunch_company_release_fraction": 0.80,
    "lunch_taxi_fraction": 0.10,
    "evening_start_hour": 18.0, "evening_end_hour": 22.0,
    "evening_taxi_fraction": 0.30,
    "late_evening_start_hour": 23.0, "late_evening_end_hour": 24.0,
    "evening_residential_fraction": 25/30,
    "restaurant_stay_sec": 3600.0,
    # 음식점 방문 시민 스폰 (버스정류장/지하철입구 -> 음식점, 회사와 동일한 "건물당 인구" 방식)
    "restaurant_pop_base": 60,
    "restaurant_window1_start": 12.0, "restaurant_window1_end": 14.0,
    "restaurant_window2_start": 17.0, "restaurant_window2_end": 22.0,
    "restaurant_civilian_taxi_probability": 0.10,
    # Module 2
    # 명세 M3: 5분 단위로 t+1~t+6 예측. lag 12칸=1시간, rolling 6/12칸=30분/1시간
    "h3_resolution": 9, "freq": "5min", "max_lag": 12,
    "rolling_short": 6, "rolling_long": 12, "forecast_horizons": 6,
    # Module 3
    "xgb_n_estimators": 100, "xgb_max_depth": 6, "xgb_learning_rate": 0.1,
    "test_size": 0.2,
    "cnn_hidden_dim": 64, "cnn_num_layers": 2, "cnn_kernel_size": 3,
    "cnn_epochs": 30, "cnn_batch_size": 16, "cnn_lr": 0.001,
    # Module 4
    "base_fare": 4800, "min_multiplier": 1.0, "max_multiplier": 3.0,
    "surge_coefficient": 0.4,
    "dispatch_use_euclidean": True,
    "mock_available_taxis_min": 1, "mock_available_taxis_max": 20,
    "mock_taxi_count": 20, "mock_passenger_count": 20,
    # Synthetic training/demo data (kept explicit so it is no longer hidden)
    "demo_data_rows": 1000, "demo_data_minutes": 500,
    "demo_data_start": "2026-08-28 18:00:00",
    "xgb_random_state": 42, "train_random_state": 42,
}

REGION_PRESETS = {
    "강남역": {"lat_min": 37.495, "lat_max": 37.505, "lng_min": 127.020, "lng_max": 127.035, "temp_min": 15.0, "temp_max": 25.0},
    "홍대입구": {"lat_min": 37.550, "lat_max": 37.560, "lng_min": 126.920, "lng_max": 126.935, "temp_min": 15.0, "temp_max": 25.0},
    "여의도": {"lat_min": 37.520, "lat_max": 37.530, "lng_min": 126.920, "lng_max": 126.935, "temp_min": 14.0, "temp_max": 24.0},
    "제주공항": {"lat_min": 33.505, "lat_max": 33.515, "lng_min": 126.485, "lng_max": 126.500, "temp_min": 18.0, "temp_max": 28.0},
    "NYC(맨해튼)": {"lat_min": 40.755, "lat_max": 40.765, "lng_min": -73.990, "lng_max": -73.975, "temp_min": 5.0, "temp_max": 20.0},
}

def load_config(config_path=None):
    path = config_path or CONFIG_PATH
    # 잘못된 설정을 다른 지역/기본값으로 조용히 대체하면 실험을 재현할 수 없다.
    with open(path, "r", encoding="utf-8") as f:
        user_cfg = json.load(f)
    if not isinstance(user_cfg, dict):
        raise ValueError("설정 파일은 JSON 객체여야 합니다.")
    merged = {**DEFAULT_CONFIG, **user_cfg}
    # 설정 import는 네트워크를 호출하지 않는다. 지도 조회는 환경 생성 때만 수행한다.
    preset = REGION_PRESETS.get(merged["region"], {})
    for key, value in preset.items():
        merged.setdefault(key, value)
    validate_config(merged)
    return merged

def _apply_region_coords(merged, region):
    coords = None
    try:
        from geo_lookup import lookup_region
        # 실제지도 모드에서도 grid_x/grid_y/grid_length 슬라이더를 그대로 활용:
        # 격자 전체 폭(grid_x*grid_length)과 높이(grid_y*grid_length) 중 큰 쪽의 절반을
        # OSM 다운로드 반경(미터)으로 사용 -> 별도 슬라이더 안 만들고 기존 3개 값 재사용
        grid_width_m = merged.get("grid_x", 3) * merged.get("grid_length", 200)
        grid_height_m = merged.get("grid_y", 3) * merged.get("grid_length", 200)
        margin_m = max(grid_width_m, grid_height_m) / 2
        result = lookup_region(region, margin_m=margin_m)
        coords = {k: result[k] for k in ("lat_min","lat_max","lng_min","lng_max")}
        print(f"[안내] '{region}' 좌표를 실시간 API(Nominatim)로 조회했습니다.")
    except Exception as e:
        print(f"[안내] 좌표 API 조회 실패({type(e).__name__}) — 프리셋으로 대체합니다.")
    if coords:
        merged.update(coords)
        temp = REGION_PRESETS.get(region, {})
        merged["temp_min"] = temp.get("temp_min", 15.0); merged["temp_max"] = temp.get("temp_max", 25.0)
    elif region in REGION_PRESETS:
        merged.update(REGION_PRESETS[region])
    else:
        print(f"[안내] '{region}' 프리셋 없음 — 홍대입구로 대체합니다.")
        merged.update(REGION_PRESETS["홍대입구"]); merged["region"] = "홍대입구"

def validate_config(cfg):
    import math
    if cfg['freq'] != '5min' or cfg['forecast_horizon'] != 6:
        raise ValueError('예측 설정은 freq=5min, forecast_horizon=6이어야 합니다.')
    for key in ('num_taxis', 'num_normal_cars', 'num_auto_cars', 'num_obstacles', 'num_passengers'):
        if not isinstance(cfg[key], int) or cfg[key] < 0:
            raise ValueError(f'{key}는 0 이상의 정수여야 합니다.')
    for key in ('grid_x', 'grid_y', 'grid_length', 'max_lag', 'rolling_short', 'rolling_long', 'training_days', 'passenger_wait_timeout', 'synthetic_rate'):
        if not math.isfinite(cfg[key]) or cfg[key] <= 0:
            raise ValueError(f'{key}는 양수여야 합니다.')
    if not 0 <= cfg['sim_start_hour'] < cfg['sim_end_hour'] <= 24:
        raise ValueError('시뮬레이션 시간은 0 <= start < end <= 24여야 합니다.')
    if not 0 <= cfg['reposition_fraction'] <= 1:
        raise ValueError('reposition_fraction은 0~1이어야 합니다.')
    if cfg['passenger_seed'] is not None and (not isinstance(cfg['passenger_seed'], int) or cfg['passenger_seed'] < 0):
        raise ValueError('passenger_seed는 0 이상의 정수 또는 null이어야 합니다.')
    if cfg['passenger_mode'] not in ('replay', 'legacy'):
        raise ValueError('passenger_mode는 replay 또는 legacy여야 합니다.')


CFG = load_config()
