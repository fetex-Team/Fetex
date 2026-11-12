"""
대시보드에서 바꿀 수 있는 조건(설정값) 목록과 검증.

- 화이트리스트에 있는 설정만 바꿀 수 있다 (임의 설정 주입 방지).
- 기본값은 실제 설정(presets/default/기본.json + config.json, config_loader 경유)에서, 근거 등급·출처는 근거 대장
  (docs/design/08_evidence_register.md)에서 읽는다 → 화면에 보이는 근거와 문서가 항상 같다.
"""
from __future__ import annotations

import datetime as dt
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REGISTER = os.path.join(ROOT, "docs", "design", "08_evidence_register.md")
ROW = re.compile(r"^\|\s*([^|]*?)\s*\|\s*`([a-z0-9_]+)`\s*\|\s*`([^`]+)`\s*\|\s*([A-G][^|]*?)\s*\|\s*(.*?)\s*\|\s*$")

STRATEGIES = {
    "patrol": "순찰",
    "prepositioned": "시간표 선배치",
    "rl_prepositioned": "RL (강화학습)",
    "forecast": "예측 재배치",
    "surge": "동적 호출비 재배치",
}
# 평가 7일 (예측·RL 학습 기간 2026-07-13~09-06 이후, 월~일 하루씩). module4_dispatch/experiments/repeat_experiments.DEFAULT_DATES와 같다.
EVAL_DATES = ["2026-09-07", "2026-10-13", "2026-10-14", "2026-09-10", "2026-09-18", "2026-10-24", "2026-10-18"]
# 실측 시간별 날씨 파일이 덮는 기간 (data/external/weather_강남역_2026-07-01_2026-10-27.csv)
DATE_MIN, DATE_MAX = dt.date(2026, 7, 1), dt.date(2026, 10, 27)

# key: (화면 이름, 묶음, 종류, 선택지 또는 (최소, 최대, 단위), 한 줄 설명)
PARAMS = {
    "num_taxis": ("택시 대수", "공급", "int", (10, 200, "대"), "구역 안에서 카카오 호출을 받는 택시 수"),
    "taxi_external_away": ("나간 택시 복귀 지연", "공급", "bool", None, "구역 밖으로 손님을 태워 간 택시가 늦게 돌아옴"),
    "external_return_factor": ("복귀 시간 배율", "공급", "float", (1.0, 4.0, "배"), "평균 운행시간 × 이 값 = 갔다 오는 시간 (복귀 지연을 켰을 때)"),
    "real_demand_scale": ("카카오 몫", "수요", "float", (0.1, 1.5, ""), "전체 택시 승차 중 카카오로 부르는 비율 (1.0 = 모든 택시 손님)"),
    "real_spatial_weights": ("손님 위치 방식", "수요", {"living_pop_250m": "생활인구 비례 (실측)", "poi": "건물 종류별 배수 (예전 가정)"}, None,
                             "같은 500m 칸 안에서 어느 도로에 손님이 나타나는지"),
    "real_internal_source": ("구역 안 하차 비율", "수요", {"taxi_od": "택시 기종점 실측 (5.4%)", "exp_distance": "거리 가정 (약 9%)"}, None,
                             "강남역에서 탄 손님 중 구역 안에서 내리는 비율"),
    "real_hourly_profile": ("시간대 비율", "수요", {"seoul_2025": "서울 전체 (2025 통계)", "gangnam_od": "강남역 평일 (기종점 자료, 밤 집중)"}, None,
                            "하루 손님을 시간대에 나누는 방식"),
    "real_dow_factors": ("요일 배율", "수요", {"seoul_2022_01": "서울 2022년 1월 통계", "flat": "요일 차이 없음"}, None,
                         "평일 안·주말 안의 요일별 차이"),
    "passenger_wait_timeout": ("손님 포기 시간", "손님", "int", (60, 1800, "초"), "배차를 이 시간 넘게 못 받으면 손님이 포기"),
    "dispatch_cost": ("배차 거리 계산", "배차", {"network": "도로 이동시간 (다익스트라)", "euclidean": "직선 거리"}, None,
                      "헝가리안 짝짓기에 쓰는 비용"),
    "map_bus_lanes": ("중앙버스차로", "도로", {"bus_only": "버스 전용 (실제)", "open": "일반 도로로 개방 (예전)"}, None, "강남대로 중앙버스차로"),
    "map_speed_limits": ("제한속도", "도로", {"seoul_5030": "안전속도 5030 (실제)", "osm_default": "SUMO 기본 (간선 100km/h)"}, None, "제한속도 없는 도로의 속도"),
    "signal_plans": ("신호", "도로", {"real": "경찰청 실제 신호 계획", "guess": "지도 추정 신호"}, None, "강남역·교보타워사거리 신호"),
    "speed_profile": ("시간대별 정체", "도로", {"topis": "TOPIS 실측 정체 반영", "free_flow": "정체 없음"}, None, "요일·시간대별 도로 속도"),
}


def load_settings() -> dict:
    """팀 기본값(config_loader.DEFAULT_CONFIG = presets/default/기본.json) + 개인 설정(config.json).
    config.json이 없으면 read_user_config()가 팀 기본 프리셋으로 대신한다.
    config_loader는 import할 때 지역 좌표 조회를 한 번 하므로, 대시보드 시작을 늦추지 않게 함수 안에서 import한다."""
    import config_loader
    cfg = dict(config_loader.DEFAULT_CONFIG)
    cfg.update(config_loader.read_user_config())
    return cfg


def load_register() -> dict:
    """근거 대장 표: key -> {grade, evidence}."""
    out = {}
    if not os.path.exists(REGISTER):
        return out
    with open(REGISTER, encoding="utf-8") as f:
        for line in f:
            m = ROW.match(line.strip())
            if m:
                out[m.group(2)] = {"item": m.group(1), "grade": m.group(4).strip(), "evidence": m.group(5)}
    return out


def schema() -> list[dict]:
    cfg, reg = load_settings(), load_register()
    rows = []
    for key, (label, group, kind, rng, desc) in PARAMS.items():
        r = {"key": key, "label": label, "group": group, "desc": desc, "default": cfg.get(key),
             "grade": reg.get(key, {}).get("grade", ""), "evidence": reg.get(key, {}).get("evidence", "")}
        if isinstance(kind, dict):
            r.update(type="choice", choices=[{"value": k, "label": v} for k, v in kind.items()])
        else:
            r["type"] = kind
            if rng:
                r.update(min=rng[0], max=rng[1], unit=rng[2])
        rows.append(r)
    return rows


class InvalidRequest(ValueError):
    pass


def validate_overrides(overrides: dict) -> dict:
    """화이트리스트·범위 검사 후 정규화한 값을 돌려준다. 기본값과 같은 항목은 뺀다."""
    if not isinstance(overrides, dict):
        raise InvalidRequest("조건은 {설정: 값} 형태여야 합니다.")
    cfg = load_settings()
    clean = {}
    for k, v in overrides.items():
        if k not in PARAMS:
            raise InvalidRequest(f"바꿀 수 없는 설정입니다: {k}")
        _, _, kind, rng, _ = PARAMS[k]
        if isinstance(kind, dict):
            if v not in kind:
                raise InvalidRequest(f"{k}: 허용 값 {list(kind)} 중 하나여야 합니다 (받은 값 {v!r})")
        elif kind == "bool":
            if not isinstance(v, bool):
                raise InvalidRequest(f"{k}: true/false여야 합니다")
        else:
            try:
                v = int(v) if kind == "int" else float(v)
            except (TypeError, ValueError):
                raise InvalidRequest(f"{k}: 숫자여야 합니다 (받은 값 {v!r})")
            if isinstance(v, float) and (v != v):
                raise InvalidRequest(f"{k}: 숫자여야 합니다")
            if not (rng[0] <= v <= rng[1]):
                raise InvalidRequest(f"{k}: {rng[0]}~{rng[1]} 범위여야 합니다 (받은 값 {v})")
        if cfg.get(k) != v:
            clean[k] = v
    return clean


def validate_dates(dates) -> list[str]:
    if not isinstance(dates, list) or not dates:
        raise InvalidRequest("날짜를 하나 이상 고르세요.")
    out = []
    for d in dates:
        try:
            day = dt.date.fromisoformat(str(d))
        except ValueError:
            raise InvalidRequest(f"날짜 형식이 아닙니다: {d!r} (예: 2026-09-10)")
        if not (DATE_MIN <= day <= DATE_MAX):
            raise InvalidRequest(f"{d}: 실측 날씨가 있는 {DATE_MIN}~{DATE_MAX} 안이어야 합니다")
        if day.isoformat() not in out:
            out.append(day.isoformat())
    return out


def validate_strategies(strategies) -> list[str]:
    if not isinstance(strategies, list) or not strategies:
        raise InvalidRequest("전략을 하나 이상 고르세요.")
    bad = [s for s in strategies if s not in STRATEGIES]
    if bad:
        raise InvalidRequest(f"알 수 없는 전략: {bad}")
    return list(dict.fromkeys(strategies))


def safe_name(name: str | None) -> str:
    """실험 이름 → results/repeat/<이름> 폴더명. 영문 소문자·숫자·_만, 비면 시각으로."""
    name = (name or "").strip().lower()
    name = re.sub(r"[^a-z0-9_]+", "_", name).strip("_")[:48]
    return name or "dash_" + dt.datetime.now().strftime("%Y%m%d_%H%M%S")
