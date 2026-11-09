"""
근거 대장(docs/design/08_evidence_register.md)과 실제 설정이 어긋나지 않는지 검사.

- 대장 표의 `설정 | 값` 칸을 읽어 presets/default/기본.json 위에 근거기반_현실화.json 프리셋을 덮어쓴 값과 같은지 비교
  (팀 기본값은 예전 동작을 유지하고, 근거 기반 값은 프리셋으로 켠다)
- 결과에 영향을 주는 핵심 설정이 대장에서 빠지지 않았는지 확인
설정을 바꾸면 대장도 함께 고쳐야 이 테스트가 통과한다 (근거 없는 값이 조용히 들어오는 것을 막음).
"""
import json
import os
import re
import sys
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
for m in ("geo_lookup", "weather_lookup"):          # config_loader가 외부 API를 부르지 않게 차단
    sys.modules.setdefault(m, types.ModuleType(m))

REGISTER = os.path.join(ROOT, "docs", "design", "08_evidence_register.md")
ROW = re.compile(r"^\|[^|]*\|\s*`([a-z0-9_]+)`\s*\|\s*`([^`]+)`\s*\|\s*([A-G][^|]*)\|")

REQUIRED = {
    "use_real_map", "map_bus_lanes", "map_speed_limits", "signal_plans", "speed_profile",
    "demand_source", "real_demand_scale", "real_dow_factors", "real_spatial_weights", "real_internal_source", "real_hourly_profile",
    "weather_precip_coef_per_mm", "use_historical_weather",
    "num_taxis", "passenger_wait_timeout", "taxi_external_away", "external_replacement_ratio", "external_return_factor",
    "taxi_dispatch_algorithm", "dispatch_cost", "forecast_min_idle_ratio", "forecast_alloc_power",
    "h3_resolution", "xgb_n_estimators", "xgb_max_depth", "xgb_learning_rate",
    "sim_start_hour", "sim_end_hour", "base_fare",
}


def _register():
    rows = {}
    with open(REGISTER, encoding="utf-8") as f:
        for line in f:
            m = ROW.match(line.strip())
            if m:
                rows[m.group(1)] = (json.loads(m.group(2)), m.group(3).strip())
    return rows


PRESET_DIR = os.path.join(ROOT, "presets", "default")


def _settings():
    # 팀 기본값(기본.json) 위에 근거 기반 현실화 프리셋을 덮어쓴 설정 = 근거 대장이 설명하는 설정
    cfg = {}
    for name in ("기본.json", "근거기반_현실화.json"):
        with open(os.path.join(PRESET_DIR, name), encoding="utf-8") as f:
            cfg.update(json.load(f))
    return cfg


def test_register_covers_required_settings():
    missing = REQUIRED - set(_register())
    assert not missing, f"근거 대장에 없는 설정: {sorted(missing)}"


def test_register_values_match_settings():
    cfg = _settings()
    wrong = {k: (v, cfg.get(k)) for k, (v, _) in _register().items() if cfg.get(k) != v}
    assert not wrong, f"대장 값과 실제 설정이 다름 (대장, 실제): {wrong}"


def test_every_row_has_grade():
    for k, (_, grade) in _register().items():
        assert grade[0] in "ABCDEFG", (k, grade)
