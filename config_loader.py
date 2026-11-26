"""프로젝트 전체 설정의 단일 원본."""
import json
import os

ROOT = os.path.dirname(os.path.abspath(__file__))
# build_env.py --config-path 옵션이 MOBILITY_CONFIG 환경변수에 경로를 넣어주는데,
# 예전엔 여기서 이걸 읽지 않아서 --config-path가 조용히 무시되던 문제 수정.
CONFIG_PATH = os.environ.get("MOBILITY_CONFIG", os.path.join(ROOT, "config.json"))
# config.json은 개인 설정(git 제외). 없으면 팀 기본 프리셋으로 시작한다.
USER_CONFIG_PATH = os.path.join(ROOT, "config.json")
PRESET_DIR = os.path.join(ROOT, "presets")
DEFAULT_PRESET_DIR = os.path.join(PRESET_DIR, "default")   # 팀 공유 프리셋 (git 포함)
USER_PRESET_DIR = os.path.join(PRESET_DIR, "user")         # 개인 프리셋 (현재 git 포함)
DEFAULT_PRESET_PATH = os.path.join(DEFAULT_PRESET_DIR, "기본.json")


def read_user_config(path=None) -> dict:
    """설정 파일 원본(dict)을 읽는다. path 생략 시 지금 실행이 쓰는 설정(MOBILITY_CONFIG 또는 config.json).
    config.json이 아직 없으면(처음 받은 상태) 팀 기본 프리셋(presets/default/기본.json)을 쓴다.
    명시적 경로 또는 MOBILITY_CONFIG가 지정되었는데 파일이 없으면 FileNotFoundError를 발생시킨다."""
    _explicit_path = path is not None
    _env_override = os.environ.get("MOBILITY_CONFIG")
    target_path = path or _env_override or USER_CONFIG_PATH

    if not os.path.exists(target_path):
        if _explicit_path or _env_override:
            raise FileNotFoundError(
                f"지정한 설정 파일을 찾을 수 없습니다: {os.path.abspath(target_path)}\n"
                f"{'(read_user_config 인자로 지정됨)' if _explicit_path else '(MOBILITY_CONFIG 환경변수로 지정됨)'}"
            )
        if os.path.abspath(target_path) == os.path.abspath(USER_CONFIG_PATH) and os.path.exists(DEFAULT_PRESET_PATH):
            target_path = DEFAULT_PRESET_PATH
        else:
            return {}

    with open(target_path, "r", encoding="utf-8") as f:
        return json.load(f)

def _load_default_config() -> dict:
    """팀 기본값은 presets/default/기본.json 하나에만 둔다 (코드에 따로 적지 않음).
    각 항목 설명은 config_schema.TIPS (GUI에서 마우스를 올리면 보임)."""
    try:
        with open(DEFAULT_PRESET_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        raise RuntimeError(f"팀 기본 설정 {DEFAULT_PRESET_PATH}을 읽지 못했습니다: {e}. "
                           f"git에서 이 파일을 다시 받아 주세요.") from e


DEFAULT_CONFIG = _load_default_config()

REGION_PRESETS = {
    "강남역": {"lat_min": 37.495, "lat_max": 37.505, "lng_min": 127.020, "lng_max": 127.035, "temp_min": 15.0, "temp_max": 25.0},
    "홍대입구": {"lat_min": 37.550, "lat_max": 37.560, "lng_min": 126.920, "lng_max": 126.935, "temp_min": 15.0, "temp_max": 25.0},
    "여의도": {"lat_min": 37.520, "lat_max": 37.530, "lng_min": 126.920, "lng_max": 126.935, "temp_min": 14.0, "temp_max": 24.0},
    "제주공항": {"lat_min": 33.505, "lat_max": 33.515, "lng_min": 126.485, "lng_max": 126.500, "temp_min": 18.0, "temp_max": 28.0},
    "NYC(맨해튼)": {"lat_min": 40.755, "lat_max": 40.765, "lng_min": -73.990, "lng_max": -73.975, "temp_min": 5.0, "temp_max": 20.0},
}

def load_config(config_path=None):
    # config_path가 명시적으로 주어졌는지(인자로 직접 전달) 아니면 환경변수/기본값으로 결정됐는지 구분
    _explicit_path = config_path is not None
    path = config_path or CONFIG_PATH
    _env_override = os.environ.get("MOBILITY_CONFIG")

    user_cfg = {}
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                user_cfg = json.load(f)
            if _explicit_path:
                print(f"[안내] 지정 설정 파일 사용: {os.path.abspath(path)}")
            elif _env_override:
                print(f"[안내] MOBILITY_CONFIG 환경변수 설정 파일 사용: {os.path.abspath(path)}")
            else:
                print(f"[안내] 기본 설정 파일 사용: {os.path.abspath(path)}")
        except (json.JSONDecodeError, OSError) as e:
            print(f"[경고] {os.path.basename(path)}을 읽지 못해 기본값을 사용합니다: {e}")
    elif _explicit_path or _env_override:
        # 명시적으로 경로를 지정했거나 환경변수로 지정했는데 파일이 없는 경우 → 명확한 오류
        raise FileNotFoundError(
            f"지정한 설정 파일을 찾을 수 없습니다: {os.path.abspath(path)}\n"
            f"{'(config_path 인자로 지정됨)' if _explicit_path else '(MOBILITY_CONFIG 환경변수로 지정됨)'}\n"
            f"파일 경로를 확인하거나 환경변수를 제거한 뒤 다시 실행하세요."
        )
    elif os.path.abspath(path) == os.path.abspath(USER_CONFIG_PATH) and os.path.exists(DEFAULT_PRESET_PATH):
        user_cfg = read_user_config(DEFAULT_PRESET_PATH)
        print(f"[안내] config.json이 없어 팀 기본 프리셋을 사용합니다: {os.path.abspath(DEFAULT_PRESET_PATH)}")
    else:
        print("[안내] 설정 파일이 없어 코드 내 기본값을 사용합니다.")
    merged = {**DEFAULT_CONFIG, **user_cfg}
    _apply_region_coords(merged, merged.get("region", "홍대입구"))
    _apply_live_weather(merged, merged.get("region", "홍대입구"))
    return merged

def _apply_region_coords(merged, region):
    merged.pop("region_unresolved", None)
    coords = None
    try:
        from module1_simulation.geo_lookup import lookup_region
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
        # 예전에는 조용히 홍대입구로 바꿔 돌렸다 -> 다른 지역 데이터가 정상 결과처럼 섞이는 문제.
        # 설정 화면(GUI)은 계속 열 수 있게 값은 채워 두되, 표시를 남겨 build_env.py가 지도 생성을 거부한다.
        print(f"[경고] '{region}' 좌표를 찾지 못했고 프리셋도 없습니다. 지역명을 확인하거나 네트워크를 연결하세요. "
              f"(지도 생성은 중단됩니다)")
        merged.update(REGION_PRESETS["홍대입구"])
        merged["region_unresolved"] = region

def _apply_live_weather(merged, region):
    try:
        from module1_simulation.weather_lookup import get_current_weather
        lat = (merged["lat_min"] + merged["lat_max"]) / 2
        lng = (merged["lng_min"] + merged["lng_max"]) / 2
        weather = get_current_weather(lat, lng)
        if weather:
            temp = weather["temperature"]
            merged["temp_min"] = round(temp - 2.0, 1); merged["temp_max"] = round(temp + 2.0, 1)
            merged["current_temperature"] = round(temp, 1); merged["current_precipitation"] = round(weather["precipitation"], 2)
            if weather.get("wind_speed") is not None:
                merged["current_wind_speed"] = round(weather["wind_speed"], 1)
    except Exception:
        pass

CFG = load_config()

def reload_config():
    """
    config.json의 최신 설정을 다시 읽어 CFG에 반영한다.

    CFG 객체 자체는 유지하고 내부 내용만 갱신한다.
    따라서 다른 파일에서 이미 `from config_loader import CFG`
    로 가져간 참조도 그대로 최신값을 사용할 수 있다.
    """
    latest = load_config()

    CFG.clear()
    CFG.update(latest)

    return CFG

# ---------------------------------------------------------------------------
# 실행용 설정 파일: config.json은 GUI [적용하기]로만 바뀌고, 실행·비교는 이 파일을 쓴다.
# 자식 프로세스는 MOBILITY_CONFIG 환경변수로 같은 파일을 읽는다 (CONFIG_PATH 참고).
# ---------------------------------------------------------------------------
RUN_CONFIG_DIR = os.path.join(ROOT, "results", "run_configs")


def active_config_path():
    """지금 실행이 읽는 설정 파일 (MOBILITY_CONFIG가 있으면 그것, 없으면 config.json)."""
    return os.environ.get("MOBILITY_CONFIG", os.path.join(ROOT, "config.json"))


def write_run_config(cfg, label="run"):
    """cfg를 results/run_configs/<label>_<시각>.json에 저장하고 경로를 반환 (config.json은 그대로)."""
    from datetime import datetime
    os.makedirs(RUN_CONFIG_DIR, exist_ok=True)
    path = os.path.join(RUN_CONFIG_DIR, f"{label}_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    return path


def use_run_config(overrides, label="run"):
    """현재 설정 파일 + overrides로 실행용 설정을 만들고, 이 프로세스의 CFG와 이후 자식 프로세스가 그것을 쓰게 한다."""
    base = read_user_config()
    base.update(overrides)
    path = write_run_config(base, label)
    os.environ["MOBILITY_CONFIG"] = path
    CFG.clear()
    CFG.update(load_config(path))
    return path
