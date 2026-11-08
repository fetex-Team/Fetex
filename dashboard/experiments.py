"""
저장된 실험 결과(results/repeat/<이름>/) 읽기.

- runs/*.json: 날짜×전략 1회 실행 결과 (repeat_experiments.py가 씀). 진행 중인 실험도 끝난 실행만큼 보인다.
- summary.json: 다 끝난 실험의 통계(짝지은 차이·95% 신뢰구간·부호 검정)와 그때 바꾼 설정(overrides).
- 이름별 설명은 CATALOG. 목록에 없는 이름(대시보드에서 새로 돌린 실험 등)은 conditions.json/summary.json으로 설명한다.
"""
from __future__ import annotations

import importlib.util
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPEAT = os.path.join(ROOT, "results", "repeat")
METRICS = ["service_rate", "n_timeout_removed", "avg_wait_sec", "p90_wait_sec", "true_avg_wait_sec_with_penalty",
           "est_revenue_won"]

# (묶음, 화면 이름, 설명). 기준 환경이 바뀌어 온 순서: 빠른 도로(~11/3) → 도로 현실화 50대 → v2(61대, 하차 9%) → v3(최종)
CATALOG = {
    "twin_v3": ("최종", "최종 평가 v3 — 택시 61대, 하차 실측 5.4%", "근거 기반 디지털 트윈 최종 설정. 7일 × 4전략"),
    "twin_v2": ("최종", "평가 v2 — 택시 61대, 하차 9% 가정", "v3 직전. 구역 안 하차 비율만 예전 가정"),
    "realistic_eval": ("도로 현실화", "도로 현실화 후 — 택시 50대", "버스차로·5030·실제 신호·TOPIS 정체 적용, 손님 위치는 건물 배수"),
    "realistic_fleet_75": ("도로 현실화", "도로 현실화 후 — 택시 75대 (평일 2일)", ""),
    "realistic_external_away": ("도로 현실화", "도로 현실화 후 — 복귀 지연 (수정 전 코드)", "하차 감지 순서 버그 수정 전 결과, 참고용"),
    "realistic_away": ("도로 현실화", "도로 현실화 후 — 복귀 지연 (일부)", "참고용"),
    "sens_fleet50": ("민감도 (v2 기준)", "택시 61 → 50대", ""),
    "sens_fleet75": ("민감도 (v2 기준)", "택시 61 → 75대", ""),
    "sens_external_away": ("민감도 (v2 기준)", "나간 택시 복귀 지연 켬", ""),
    "sens_timeout500": ("민감도 (v2 기준)", "포기 시간 600 → 500초", ""),
    "sens_dow_flat": ("민감도 (v2 기준)", "요일 배율 없앰", ""),
    "sens_internal2km": ("민감도 (v2 기준)", "구역 안 하차 기준 1 → 2km", ""),
    "sens_spatial_poi": ("민감도 (v2 기준)", "손님 위치: 생활인구 → 건물 배수", ""),
    "sens_full_demand": ("민감도 (v2 기준)", "카카오 몫 0.77 → 1.0 (모든 손님)", ""),
    "sens3_hourly_gangnam": ("민감도 (v3 기준)", "시간대: 서울 전체 → 강남역 평일", ""),
    "sens3_internal_exp": ("민감도 (v3 기준)", "구역 안 하차: 실측 → 예전 가정", ""),
    "abl_A_old": ("단계별 영향 (9/8)", "① 현실화 전", ""),
    "abl_B_bus": ("단계별 영향 (9/8)", "② + 버스차로", ""),
    "abl_C_5030": ("단계별 영향 (9/8)", "③ + 안전속도 5030", ""),
    "abl_D_signal": ("단계별 영향 (9/8)", "④ + 실제 신호", ""),
    "abl_E_topis": ("단계별 영향 (9/8)", "⑤ + TOPIS 정체", ""),
    "speed_check": ("속도 보정", "보정 전 속도 확인", ""),
    "speed_calib1": ("속도 보정", "보정 1회차", ""),
    "speed_calib2": ("속도 보정", "보정 2회차", ""),
    "base": ("예전 (빠른 도로)", "7일 × 3전략, 직선 거리 배차", "현실화 전 결과"),
    "full_demand": ("예전 (빠른 도로)", "손님 100%", ""),
    "network_dispatch": ("예전 (빠른 도로)", "도로 이동시간 배차", ""),
    "network_dispatch_full": ("예전 (빠른 도로)", "도로 이동시간 배차, 손님 100%", ""),
    "forecast_v2": ("예전 (빠른 도로)", "예측 재배치 v2", ""),
    "forecast_v2_full": ("예전 (빠른 도로)", "예측 재배치 v2, 손님 100%", ""),
    "tune_patrol": ("예전 (빠른 도로)", "재배치 기준 튜닝 — 순찰", "검증 날짜 9/8·9/17"),
    "tune_r00": ("예전 (빠른 도로)", "재배치 기준 튜닝 — 0", ""),
    "tune_r20": ("예전 (빠른 도로)", "재배치 기준 튜닝 — 0.2", ""),
    "tune_r30": ("예전 (빠른 도로)", "재배치 기준 튜닝 — 0.3", ""),
    "tune_r40": ("예전 (빠른 도로)", "재배치 기준 튜닝 — 0.4", ""),
}
# 민감도 실험의 기준 실험 (같은 평일 3일의 순찰과 비교)
SENS_BASE = {"민감도 (v2 기준)": "twin_v2", "민감도 (v3 기준)": "twin_v3"}
GROUP_ORDER = ["최종", "대시보드에서 실행", "민감도 (v3 기준)", "민감도 (v2 기준)", "도로 현실화", "단계별 영향 (9/8)",
               "속도 보정", "예전 (빠른 도로)", "기타"]

_rep = None


def _repeat_module():
    """tools/repeat_experiments.py의 통계 함수(summarize)를 그대로 쓴다 — 보고서와 같은 계산."""
    global _rep
    if _rep is None:
        spec = importlib.util.spec_from_file_location("repeat_experiments", os.path.join(ROOT, "tools", "repeat_experiments.py"))
        _rep = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(_rep)
    return _rep


def _read_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def load_runs(name: str) -> list[dict]:
    d = os.path.join(REPEAT, name, "runs")
    if not os.path.isdir(d):
        return []
    rows = []
    for fn in sorted(os.listdir(d)):
        if fn.endswith(".json"):
            r = _read_json(os.path.join(d, fn))
            if r and "service_rate" in r:
                rows.append({k: r.get(k) for k in ["date", "strategy", "service_rate", "n_timeout_removed",
                                                    "avg_wait_sec", "p90_wait_sec", "true_avg_wait_sec_with_penalty",
                                                    "est_revenue_won", "n_total_passengers", "demand_fingerprint",
                                                    "is_holiday", "wall_min"]})
    return rows


def describe(name: str) -> dict:
    cond = _read_json(os.path.join(REPEAT, name, "conditions.json")) or {}
    summ = _read_json(os.path.join(REPEAT, name, "summary.json")) or {}
    group, label, note = CATALOG.get(name, ("대시보드에서 실행" if cond else "기타", cond.get("label") or name, cond.get("note", "")))
    return {"name": name, "group": group, "label": label, "note": note,
            "overrides": cond.get("overrides", summ.get("overrides", {})),
            "complete": bool(summ), "created": cond.get("created")}


def list_experiments() -> list[dict]:
    if not os.path.isdir(REPEAT):
        return []
    out = []
    for name in sorted(os.listdir(REPEAT)):
        runs = load_runs(name)
        if not runs:
            continue
        info = describe(name)
        info.update(n_runs=len(runs), dates=sorted({r["date"] for r in runs}),
                    strategies=sorted({r["strategy"] for r in runs}))
        out.append(info)
    order = {g: i for i, g in enumerate(GROUP_ORDER)}
    cat = {n: i for i, n in enumerate(CATALOG)}
    out.sort(key=lambda e: (order.get(e["group"], 99), cat.get(e["name"], 999), -(len(e.get("created") or "")), e["name"]))
    return out


def experiment_detail(name: str) -> dict | None:
    runs = load_runs(name)
    if not runs:
        return None
    strategies = sorted({r["strategy"] for r in runs})
    baseline = "patrol" if "patrol" in strategies else strategies[0]
    stats = _repeat_module().summarize(runs, baseline)
    return {**describe(name), "runs": runs, "stats": stats}


def sensitivity() -> list[dict]:
    """민감도 실험마다 기준 실험의 같은 날짜·순찰과 비교한 서비스율·평균 대기 차이."""
    out = []
    for name, (group, label, _) in CATALOG.items():
        base = SENS_BASE.get(group)
        if not base:
            continue
        runs = [r for r in load_runs(name) if r["strategy"] == "patrol"]
        base_runs = {r["date"]: r for r in load_runs(base) if r["strategy"] == "patrol"}
        pairs = [(r, base_runs[r["date"]]) for r in runs if r["date"] in base_runs]
        if not pairs:
            continue
        n = len(pairs)
        sr = sum(a["service_rate"] for a, _ in pairs) / n
        sr0 = sum(b["service_rate"] for _, b in pairs) / n
        w = sum(a["avg_wait_sec"] for a, _ in pairs) / n
        w0 = sum(b["avg_wait_sec"] for _, b in pairs) / n
        out.append({"name": name, "group": group, "label": label, "base": base, "days": n,
                    "service_rate": sr, "base_service_rate": sr0, "diff_pp": (sr - sr0) * 100,
                    "avg_wait_sec": w, "diff_wait_sec": w - w0})
    out.sort(key=lambda e: e["diff_pp"])
    return out
