"""프로젝트 전체 설정의 단일 원본 (Single Source of Truth)."""
import json
import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
for _sub in ("module1_simulation", "module2_preprocessing", "module3_prediction", "module4_dispatch", "tools", "scripts"):
    _sub_path = os.path.join(ROOT, _sub)
    if os.path.exists(_sub_path) and _sub_path not in sys.path:
        sys.path.insert(0, _sub_path)
CONFIG_PATH = os.path.join(ROOT, "config.json")

DEFAULT_CONFIG = {
    # Module 1 - simulation / map
    "grid_x": 3, "grid_y": 3, "grid_length": 200,
    "num_normal_cars": 20, "num_taxis": 20, "num_auto_cars": 1,
    "num_obstacles": 2, "num_passengers": 1000,
    "region": "강남역", "use_real_map": True,
    "sim_start_hour": 8, "sim_end_hour": 10,
    "passenger_wait_timeout": 500,
    "passenger_seed": None,
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
    "h3_resolution": 8, "freq": "5min", "max_lag": 6,
    "rolling_short": 3, "rolling_long": 6,
    # Module 3
    "xgb_n_estimators": 100, "xgb_max_depth": 6, "xgb_learning_rate": 0.1,
    "test_size": 0.2,
    "cnn_hidden_dim": 64, "cnn_num_layers": 2, "cnn_kernel_size": 3,
    "cnn_epochs": 30, "cnn_batch_size": 128, "cnn_lr": 0.001,
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


def load_config(config_path=None, live_api=False, verbose=False):
    """
    config.json에서 설정을 안전하고 신속하게 불러옵니다.
    - live_api=False(기본값): 외부 네트워크 API(지오코딩, 실시간날씨) 호출 없이 로컬 프리셋과 JSON 값만 읽어 0초 만에 완결 (독립 실행 및 재현성 보장).
    - live_api=True: 실시간 Nominatim/Open-Meteo API를 명시적으로 조회하여 좌표/기온을 갱신.
    """
    path = config_path or CONFIG_PATH
    user_cfg = {}
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                user_cfg = json.load(f)
            if verbose:
                print(f"[안내] {os.path.basename(path)}에서 설정값을 불러왔습니다.")
        except (json.JSONDecodeError, OSError) as e:
            if verbose:
                print(f"[경고] config.json을 읽지 못해 기본값을 사용합니다: {e}")
    else:
        if verbose:
            print("[안내] config.json이 없어 기본값을 사용합니다.")

    merged = {**DEFAULT_CONFIG, **user_cfg}
    region = merged.get("region", "강남역")

    if live_api:
        _apply_region_coords(merged, region, verbose=verbose)
        _apply_live_weather(merged, region)
    else:
        # 오프라인/테스트 모드: 좌표/날씨 키가 비어있으면 안전하게 로컬 프리셋으로 채움 (무부작용)
        if "lat_min" not in merged or "lng_min" not in merged:
            preset = REGION_PRESETS.get(region, REGION_PRESETS.get("강남역", {}))
            for k, v in preset.items():
                if k not in merged:
                    merged[k] = v

    return merged


def get_config(overrides=None, **kwargs):
    """
    단위 테스트 및 모듈별 격리 실행을 위해 특정 파라미터만 오버라이드한 설정 딕셔너리를 반환합니다.
    예: cfg = get_config(h3_resolution=9, max_lag=3)
    """
    cfg = load_config(live_api=False, verbose=False).copy()
    if overrides and isinstance(overrides, dict):
        cfg.update(overrides)
    if kwargs:
        cfg.update(kwargs)
    return cfg


def get_module_config(module_name: str) -> dict:
    """특정 모듈에 연관된 설정값만 추출하여 반환 (모듈 독립 테스트 지원)"""
    cfg = load_config(live_api=False, verbose=False)
    prefix_map = {
        "module1": ["grid_", "num_", "sim_", "passenger_", "taxi_", "school_", "company_", "residential_", "lunch_", "evening_", "restaurant_"],
        "module2": ["h3_", "freq", "max_lag", "rolling_", "weather_"],
        "module3": ["xgb_", "cnn_", "test_size", "train_random_state"],
        "module4": ["base_fare", "min_multiplier", "max_multiplier", "surge_", "dispatch_", "mock_"],
    }
    prefixes = prefix_map.get(module_name.lower(), [])
    return {k: v for k, v in cfg.items() if any(k.startswith(p) or k == p for p in prefixes)}


def _apply_region_coords(merged, region, verbose=False):
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
        coords = {k: result[k] for k in ("lat_min", "lat_max", "lng_min", "lng_max")}
        if verbose:
            print(f"[안내] '{region}' 좌표를 실시간 API(Nominatim)로 조회했습니다.")
    except Exception as e:
        if verbose:
            print(f"[안내] 좌표 API 조회 실패({type(e).__name__}) - 프리셋으로 대체합니다.")
    if coords:
        merged.update(coords)
        temp = REGION_PRESETS.get(region, {})
        merged["temp_min"] = temp.get("temp_min", 15.0)
        merged["temp_max"] = temp.get("temp_max", 25.0)
    elif region in REGION_PRESETS:
        merged.update(REGION_PRESETS[region])
    else:
        if verbose:
            print(f"[안내] '{region}' 프리셋 없음 - 강남역으로 대체합니다.")
        merged.update(REGION_PRESETS["강남역"])
        merged["region"] = "강남역"


def _apply_live_weather(merged, region):
    try:
        from weather_lookup import get_current_weather
        lat = (merged["lat_min"] + merged["lat_max"]) / 2
        lng = (merged["lng_min"] + merged["lng_max"]) / 2
        weather = get_current_weather(lat, lng)
        if weather:
            temp = weather["temperature"]
            merged["temp_min"] = round(temp - 2.0, 1)
            merged["temp_max"] = round(temp + 2.0, 1)
            merged["current_temperature"] = round(temp, 1)
            merged["current_precipitation"] = round(weather["precipitation"], 2)
    except Exception:
        pass


# 전역 설정 싱글톤 (사이드이펙트 없이 조용하고 빠르게 로드)
CFG = load_config(live_api=False, verbose=False)
