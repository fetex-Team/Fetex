"""[Module 2] 전처리 결과(피처 테이블) 저장·재사용.

학습할 때마다 로그 수십만 건을 다시 읽어 H3 변환·집계·날씨 병합·피처 생성을 하지 않도록,
build_feature_table 결과를 data/features/에 저장해 두고 입력이 같으면 그대로 불러온다.

"입력이 같다" = 아래가 모두 같을 때 (하나라도 바뀌면 자동으로 새로 만든다)
- 로그 파일 목록과 각 파일의 크기·수정 시각
- 날씨 파일(지정했거나 자동 선택된 실측 파일)의 크기·수정 시각. 날씨 파일이 없으면 상수 대체에 쓰는 현재 기온 값
- 전처리 인자(freq, max_lag, horizons, spatial_col, all_cells, dropna 등)와 전처리 코드가 읽는 설정값
- Module 2 코드 내용 (module2_preprocessing/*.py, 휴일 판정 module1_simulation/holiday_utils.py)
- spatial_col="category"면 지도(grid.net.xml)와 구역 정의(runtime_meta.json의 zones)

사용
    from module2_preprocessing.feature_cache import load_or_build_feature_table
    feat = load_or_build_feature_table(["data/real_demand_logs/demand_log_*.csv"], freq="10min")
    feat = load_or_build_feature_table(..., cache=False)     # 저장본 무시하고 새로 만들기 (저장도 안 함)
"""
import glob
import hashlib
import json
import os
import re
from datetime import datetime

import pandas as pd

from config_loader import CFG
from module2_preprocessing.pipeline import build_feature_table

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE_DIR = os.path.join(ROOT, "data", "features")
KEEP_FILES = 12          # 오래된 저장본은 이 개수만 남기고 지움
CODE_FILES = [os.path.join(ROOT, "module1_simulation", "holiday_utils.py")]


def _resolve_logs(log_paths):
    if isinstance(log_paths, str):
        log_paths = [log_paths]
    files = []
    for p in log_paths:
        files += sorted(glob.glob(p)) if any(ch in p for ch in "*?[") else [p]
    return sorted(os.path.abspath(f) for f in files)


def _file_sig(path):
    st = os.stat(path)
    return [os.path.relpath(path, ROOT) if path.startswith(ROOT) else path, st.st_size, int(st.st_mtime)]


def _content_hash(paths):
    h = hashlib.sha1()
    for p in paths:
        if os.path.exists(p):
            with open(p, "rb") as f:
                h.update(f.read())
    return h.hexdigest()[:16]


def _code_files():
    return sorted(glob.glob(os.path.join(ROOT, "module2_preprocessing", "*.py"))) + CODE_FILES


def _config_keys_used():
    """Module 2 코드가 읽는 설정 키 (CFG["x"] / CFG.get("x")) — 이 값이 바뀌면 전처리 결과도 바뀔 수 있다."""
    keys = set()
    for p in _code_files():
        keys |= set(re.findall(r"CFG(?:\[|\.get\()\s*[\"']([A-Za-z0-9_]+)", open(p, encoding="utf-8").read()))
    return sorted(keys)


def cache_key(log_paths, weather_path=None, **kwargs):
    """저장본 이름에 쓰는 키와, 그 키를 만든 입력 요약. 반환: (key, inputs)"""
    from module2_preprocessing.external_data_merge import find_weather_file
    files = _resolve_logs(log_paths)
    if not files:
        raise FileNotFoundError(f"로그 파일 없음: {log_paths}")
    weather = weather_path or find_weather_file()
    has_weather = bool(weather and os.path.exists(weather))
    live = {"current_temperature", "current_precipitation", "temp_min", "temp_max", "current_wind_speed"}
    # 실측 날씨 파일이 있으면 실시간 날씨 값은 결과에 영향이 없으므로 키에서 뺀다 (매 실행마다 달라져 재사용이 안 되는 것 방지)
    cfg = {k: CFG.get(k) for k in _config_keys_used() if not (has_weather and k in live) and k not in ("lat_min", "lat_max", "lng_min", "lng_max")}
    args = {k: (sorted(v) if isinstance(v, (list, tuple, set)) else v) for k, v in sorted(kwargs.items())}
    inputs = {
        "logs": [_file_sig(f) for f in files],
        "weather": _file_sig(weather) if has_weather else None,
        "args": args,
        "config": cfg,
        "code": _content_hash(_code_files()),
    }
    if kwargs.get("spatial_col") == "category":
        cfg_dir = os.path.join(ROOT, "module1_simulation", "sumo_config")
        try:
            zones = json.load(open(os.path.join(cfg_dir, "runtime_meta.json"), encoding="utf-8")).get("zones")
        except Exception:
            zones = None
        inputs["map"] = {"net": _content_hash([os.path.join(cfg_dir, "grid.net.xml")]),
                         "zones": hashlib.sha1(json.dumps(zones, sort_keys=True).encode()).hexdigest()[:16]}
    key = hashlib.sha1(json.dumps(inputs, sort_keys=True, default=str).encode()).hexdigest()[:12]
    return key, inputs


def _prune():
    files = sorted(glob.glob(os.path.join(CACHE_DIR, "*.pkl.gz")), key=os.path.getmtime, reverse=True)
    for old in files[KEEP_FILES:]:
        for p in (old, old[:-len(".pkl.gz")] + ".json"):
            try:
                os.remove(p)
            except OSError:
                pass


def load_or_build_feature_table(log_paths, cache=True, verbose=True, **kwargs) -> pd.DataFrame:
    """build_feature_table과 같은 인자·결과. 입력이 같으면 data/features/의 저장본을 불러온다."""
    if not cache:
        return build_feature_table(log_paths, verbose=verbose, **kwargs)
    spatial = kwargs.get("spatial_col", "h3_index")
    freq = kwargs.get("freq") or CFG["freq"]
    weather_path = kwargs.pop("weather_path", None)
    key, inputs = cache_key(log_paths, weather_path=weather_path, **{**kwargs, "freq": freq})
    path = os.path.join(CACHE_DIR, f"{spatial}_{freq}_{key}.pkl.gz")
    if os.path.exists(path):
        try:
            feat = pd.read_pickle(path)
            os.utime(path)   # 최근에 쓴 저장본은 정리 대상에서 뒤로
            if verbose:
                print(f"[전처리 저장본 사용] {os.path.relpath(path, ROOT)} — 로그 {len(inputs['logs'])}개, "
                      f"{len(feat):,}행 × 피처 {len(feat.attrs['feature_cols'])}개 ({feat.attrs.get('freq')}) "
                      f"(새로 만들려면 --no-cache)")
            return feat
        except Exception as e:
            print(f"[경고] 전처리 저장본을 읽지 못해 새로 만듭니다: {e}")
    feat = build_feature_table(log_paths, weather_path=weather_path, verbose=verbose, **kwargs)
    os.makedirs(CACHE_DIR, exist_ok=True)
    tmp = path + ".tmp"
    feat.to_pickle(tmp, compression="gzip")   # pickle은 attrs(feature_cols·target_cols·freq)도 함께 저장
    os.replace(tmp, path)
    with open(path[:-len(".pkl.gz")] + ".json", "w", encoding="utf-8") as f:
        json.dump({"created": datetime.now().isoformat(timespec="seconds"), "rows": len(feat),
                   "feature_cols": feat.attrs.get("feature_cols"), "target_cols": feat.attrs.get("target_cols"),
                   "inputs": inputs}, f, ensure_ascii=False, indent=1, default=str)
    if verbose:
        print(f"[전처리 저장] {os.path.relpath(path, ROOT)} — 다음에 같은 로그·설정이면 바로 불러옵니다.")
    _prune()
    return feat
