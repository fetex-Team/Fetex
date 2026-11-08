"""
실데이터 기반 택시 호출(수요) 모델.

왜 필요한가
- 예전 수요는 "회사 1곳당 100명, 퇴근 시 30%가 택시" 같은 가정값으로만 만들었다.
  그래서 하루 호출 수·지역 분포가 실제 강남역과 맞는지 확인할 방법이 없었다.
- 또 승객 생성이 배차 결과(도착 인원)에 따라 달라져서, 전략 A/B를 비교하면
  두 실험의 손님 목록부터 달랐다(수요 지문 불일치).

이 모듈은 공개 실데이터 두 가지로 수요를 맞춘다.
1) 국토교통부 TIMS 「택시운영현황_격자별 영업통계」(2025년, 500m 격자, 월별)
   -> 시뮬레이션 영역 안의 하루 승차 건수, 격자(500m 칸)별 비율, 월별 계절성, 평균 운행거리/요금
2) 서울시 「2025년 서울 교통이용 통계보고서」(택시 위치정보 기반, 2025.1.1~12.31)
   -> 시간대별 비율(평일/주말 따로, p.18~20), 강남구 월별 평일·주말 승차(p.81~83)
   (요일 안의 세부 차이(월~금, 토/일)는 2025년판에 없어 2022년 1월판 p.2의 비율을 쓴다)

그리고 호출 목록(스케줄)을 시뮬레이션 시작 전에 시드로 한 번에 만든다.
배차 전략이 무엇이든 같은 날짜·같은 시드면 손님 목록이 완전히 같다 -> 공정한 A/B 비교.

데이터 파일 (data/external, 자세한 출처는 data/external/README.md)
- tims/tims_500m_grid_2025_dasa.csv : grid, ym, trips, revenue, dist_m, time_s
- seoul_taxi_hourly_2025.csv         : hour, weekday_trips, weekend_trips, ...
- gangnam_taxi_pickups_2025.csv      : month, all_daily, weekday_daily, weekend_daily
- seoul_taxi_dow_2022_01.csv         : dow, trips (평일 안 / 주말 안 상대 비율만 사용)

외부 라이브러리 없이(pyproj 불필요) 동작하도록 UTM-K 좌표 변환을 직접 구현했다.
"""
from __future__ import annotations

import csv
import datetime as _dt
import math
import os
import random
from dataclasses import dataclass, field

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(ROOT, "data", "external")
TIMS_PATH = os.path.join(DATA_DIR, "tims", "tims_500m_grid_2025_dasa.csv")
HOURLY_PATH = os.path.join(DATA_DIR, "seoul_taxi_hourly_2025.csv")
GANGNAM_PATH = os.path.join(DATA_DIR, "gangnam_taxi_pickups_2025.csv")
DOW_PATH = os.path.join(DATA_DIR, "seoul_taxi_dow_2022_01.csv")

DOW_KEYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")

# 시간대별 POI 가중치 (같은 500m 칸 안에서 어느 도로에서 호출이 더 자주 나는지).
# 칸별 총량은 TIMS 실측으로 고정되고, 이 값은 칸 "안"의 배분만 바꾼다.
# (시작, 끝, 배율) 목록. 겹치면 큰 값을 쓴다. config.json의 real_poi_weights로 덮어쓸 수 있다.
DEFAULT_POI_WEIGHTS = {
    "subway_entrance": [[0, 24, 3.0]],          # 지하철 출구: 환승·귀가 승차가 가장 많음
    "bus_stop": [[0, 24, 2.0]],
    "department": [[11, 21, 2.5]],              # 백화점·쇼핑몰 영업시간
    "restaurant": [[0, 24, 1.2], [12, 14, 2.5], [18, 24, 2.5]],
    "company": [[0, 24, 1.3], [17, 21, 2.5]],   # 퇴근 시간
    "school": [[0, 24, 1.0]],
    "residential": [[0, 24, 1.0], [6, 10, 2.0]],
}


# ---------------------------------------------------------------------------
# 1. 좌표계: WGS84(위경도) <-> UTM-K(EPSG:5179) <-> 국가기준격자(500m)
# ---------------------------------------------------------------------------
_A = 6378137.0
_F = 1 / 298.257222101
_E2 = _F * (2 - _F)
_EP2 = _E2 / (1 - _E2)
_K0 = 0.9996
_LAT0 = math.radians(38.0)
_LON0 = math.radians(127.5)
_FE, _FN = 1_000_000.0, 2_000_000.0


def _meridian(phi: float) -> float:
    e2, e4, e6 = _E2, _E2 ** 2, _E2 ** 3
    return _A * ((1 - e2 / 4 - 3 * e4 / 64 - 5 * e6 / 256) * phi
                 - (3 * e2 / 8 + 3 * e4 / 32 + 45 * e6 / 1024) * math.sin(2 * phi)
                 + (15 * e4 / 256 + 45 * e6 / 1024) * math.sin(4 * phi)
                 - (35 * e6 / 3072) * math.sin(6 * phi))


_M0 = _meridian(_LAT0)


def latlng_to_utmk(lat: float, lng: float) -> tuple[float, float]:
    """위경도 -> UTM-K(EPSG:5179) 미터 좌표. pyproj와 cm 단위로 일치(테스트로 확인)."""
    phi, lam = math.radians(lat), math.radians(lng)
    sin_p, cos_p, tan_p = math.sin(phi), math.cos(phi), math.tan(phi)
    n = _A / math.sqrt(1 - _E2 * sin_p ** 2)
    t = tan_p ** 2
    c = _EP2 * cos_p ** 2
    a = (lam - _LON0) * cos_p
    x = _FE + _K0 * n * (a + (1 - t + c) * a ** 3 / 6
                         + (5 - 18 * t + t ** 2 + 72 * c - 58 * _EP2) * a ** 5 / 120)
    y = _FN + _K0 * (_meridian(phi) - _M0 + n * tan_p * (
        a ** 2 / 2 + (5 - t + 9 * c + 4 * c ** 2) * a ** 4 / 24
        + (61 - 58 * t + t ** 2 + 600 * c - 330 * _EP2) * a ** 6 / 720))
    return x, y


def utmk_to_latlng(x: float, y: float) -> tuple[float, float]:
    """UTM-K -> 위경도 (정방향 식을 뉴턴법으로 역산, 1mm 이내로 수렴)."""
    lat, lng = 38.0 + (y - _FN) / 111_000.0, 127.5 + (x - _FE) / 88_000.0
    for _ in range(20):
        fx, fy = latlng_to_utmk(lat, lng)
        dx, dy = x - fx, y - fy
        if abs(dx) < 1e-4 and abs(dy) < 1e-4:
            break
        h = 1e-6
        x_lat, y_lat = latlng_to_utmk(lat + h, lng)
        x_lng, y_lng = latlng_to_utmk(lat, lng + h)
        j11, j12 = (x_lat - fx) / h, (x_lng - fx) / h
        j21, j22 = (y_lat - fy) / h, (y_lng - fy) / h
        det = j11 * j22 - j12 * j21
        lat += (j22 * dx - j12 * dy) / det
        lng += (-j21 * dx + j11 * dy) / det
    return lat, lng


# 국가기준격자 100km 문자 칸의 좌하단 UTM-K 좌표. TIMS 파일은 '다사'(서울 대부분)만 담았다.
_GRID_SQUARES = {"다사": (900_000.0, 1_900_000.0)}
CELL_M = 500.0


def grid_code_of(x: float, y: float) -> str | None:
    """UTM-K 좌표가 속한 500m 국가기준격자 코드(예: '다사116089'). 범위 밖이면 None."""
    for prefix, (x0, y0) in _GRID_SQUARES.items():
        ix, iy = int((x - x0) // CELL_M), int((y - y0) // CELL_M)
        if 0 <= ix < 200 and 0 <= iy < 200:
            return f"{prefix}{ix:03d}{iy:03d}"
    return None


def grid_bounds(code: str) -> tuple[float, float, float, float]:
    """500m 격자 코드 -> (x0, y0, x1, y1) UTM-K 범위."""
    x0, y0 = _GRID_SQUARES[code[:2]]
    ix, iy = int(code[2:5]), int(code[5:8])
    return x0 + ix * CELL_M, y0 + iy * CELL_M, x0 + (ix + 1) * CELL_M, y0 + (iy + 1) * CELL_M


# ---------------------------------------------------------------------------
# 2. 실데이터 로딩
# ---------------------------------------------------------------------------
def _read_csv(path: str) -> list[dict]:
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"실데이터 파일이 없습니다: {path}\n"
            "data/external/README.md의 안내대로 파일을 넣거나, config.json의 "
            "demand_source를 'synthetic'으로 바꾸세요.")
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def load_hourly_profile(path: str = HOURLY_PATH) -> dict[str, list[float]]:
    rows = sorted(_read_csv(path), key=lambda r: int(r["hour"]))
    if len(rows) != 24:
        raise ValueError(f"{path}: 0~23시 24행이 필요합니다 (현재 {len(rows)}행)")
    prof = {}
    for kind in ("weekday", "weekend"):
        vals = [float(r[f"{kind}_trips"]) for r in rows]
        total = sum(vals)
        prof[kind] = [v / total for v in vals]
    return prof


def load_dow_factors(path: str = DOW_PATH) -> dict[str, float]:
    """요일 배율을 '평일끼리'(월~금 평균 = 1), '주말끼리'(토·일 평균 = 1)로 정규화해 돌려준다.
    평일·주말 사이의 큰 차이는 강남구 2025 실측(load_gangnam_ratios)이 정하고, 이 값은 그 안의 세부 차이만 준다."""
    rows = {r["dow"]: float(r["trips"]) for r in _read_csv(path)}
    missing = [k for k in DOW_KEYS if k not in rows]
    if missing:
        raise ValueError(f"{path}: 요일 {missing} 행이 없습니다")
    wd = sum(rows[k] for k in DOW_KEYS[:5]) / 5
    we = sum(rows[k] for k in DOW_KEYS[5:]) / 2
    return {k: rows[k] / (wd if i < 5 else we) for i, k in enumerate(DOW_KEYS)}


def load_gangnam_ratios(path: str = GANGNAM_PATH) -> dict[int, tuple[float, float]]:
    """월 -> (평일 배율, 주말 배율). 강남구 2025 일평균 승차(평일/주말) ÷ 전체 일평균."""
    out = {}
    for r in _read_csv(path):
        a = float(r["all_daily"])
        out[int(r["month"])] = (float(r["weekday_daily"]) / a, float(r["weekend_daily"]) / a)
    if len(out) != 12:
        raise ValueError(f"{path}: 1~12월 12행이 필요합니다 (현재 {len(out)}행)")
    return out


@dataclass
class CellStat:
    code: str
    overlap: float                      # 시뮬레이션 영역과 겹치는 면적 비율 (0~1)
    daily_by_month: dict[int, float]    # 월 -> 하루 평균 승차 건수 (칸 전체)
    mean_dist_km: float
    mean_fare_won: float
    mean_time_min: float
    edges: list[str] = field(default_factory=list)

    @property
    def area_daily(self) -> float:
        """영역 안 하루 평균 승차 (연평균)."""
        return self.overlap * sum(self.daily_by_month.values()) / max(len(self.daily_by_month), 1)


def load_tims_cells(path: str, codes: set[str]) -> dict[str, dict]:
    """TIMS 격자별 월 통계 중 필요한 칸만 읽는다 (파일 전체 26만 행 중 일부)."""
    out: dict[str, dict] = {}
    for r in _read_csv(path):
        code = r["grid"]
        if code not in codes:
            continue
        ym = r["ym"].replace("-", "")
        year, month = int(ym[:4]), int(ym[4:6])
        days = (_dt.date(year + (month == 12), month % 12 + 1, 1) - _dt.date(year, month, 1)).days
        d = out.setdefault(code, {"daily": {}, "trips": 0.0, "dist": 0.0, "rev": 0.0, "time": 0.0})
        trips = float(r["trips"])
        d["daily"][month] = trips / days
        d["trips"] += trips
        d["dist"] += float(r["dist_m"])
        d["rev"] += float(r["revenue"])
        d["time"] += float(r["time_s"])
    return out


# ---------------------------------------------------------------------------
# 3. 호출 스케줄
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Call:
    t: float            # 시뮬레이션 경과초 (sim_start_hour 기준)
    origin: str         # 승차 도로(edge)
    origin_frac: float  # 도로 위 승차 위치 (0~1, 길이 비율)
    dest: str           # 하차 도로
    kind: str           # 'internal'(영역 안 하차) / 'external'(영역 밖으로 나감 -> 경계 도로 하차)
    cell: str           # 승차 500m 격자 코드
    category: str       # 승차 도로의 대표 POI (subway_entrance/company/.../none)


def _hour_weight(spec: list, hour: float) -> float:
    w = 0.0
    for start, end, mult in spec:
        if start <= hour < end:
            w = max(w, float(mult))
    return w or 1.0


class RealDemandModel:
    """TIMS + 서울시 시간대 통계로 보정한 호출 생성기.

    meta: build_env.py가 만든 runtime_meta.json 내용 (edges, zones, edge_latlng, boundary_edges, config)
    cfg : config.json 설정 (real_demand_scale, real_internal_trip_km, real_poi_weights ...)
    """

    def __init__(self, meta: dict, cfg: dict | None = None, tims_path: str = TIMS_PATH,
                 hourly_path: str = HOURLY_PATH, dow_path: str = DOW_PATH, gangnam_path: str = GANGNAM_PATH):
        cfg = dict(cfg or {})
        mcfg = meta.get("config", {})
        self.scale = float(cfg.get("real_demand_scale", 1.0))
        self.internal_km = float(cfg.get("real_internal_trip_km", 1.0))
        self.poi_weights = {**DEFAULT_POI_WEIGHTS, **(cfg.get("real_poi_weights") or {})}
        self.hourly = load_hourly_profile(hourly_path)
        self.dow = load_dow_factors(dow_path)
        self.weekpart = load_gangnam_ratios(gangnam_path)
        # 시간별 강수량 1mm당 호출 증가율 (docs/weather_coefficient.md 실측 추정, 상한 cap mm)
        self.precip_coef = float(cfg.get("weather_precip_coef_per_mm", 0.0199))
        self.precip_cap = float(cfg.get("weather_precip_cap_mm", 30.0))

        self.edges = list(meta["edges"])
        self.edge_latlng = meta["edge_latlng"]
        self.boundary = [e for e in meta.get("boundary_edges", []) if e in set(self.edges)] or self.edges
        self.edge_cats: dict[str, list[str]] = {}
        for cat, edges in (meta.get("zones") or {}).items():
            for e in edges or []:
                self.edge_cats.setdefault(e, []).append(cat)
        for e in meta.get("department_store_edges") or []:
            self.edge_cats.setdefault(e, []).append("department")

        # 시뮬레이션 영역(bbox)을 UTM-K로 바꿔 격자와 겹치는 면적을 계산
        lat_min = cfg.get("lat_min", mcfg.get("lat_min"))
        lat_max = cfg.get("lat_max", mcfg.get("lat_max"))
        lng_min = cfg.get("lng_min", mcfg.get("lng_min"))
        lng_max = cfg.get("lng_max", mcfg.get("lng_max"))
        if None in (lat_min, lat_max, lng_min, lng_max):
            raise ValueError("시뮬레이션 영역(lat/lng min/max)을 알 수 없습니다. build_env.py를 먼저 실행하세요.")
        xs, ys = zip(*(latlng_to_utmk(la, ln) for la in (lat_min, lat_max) for ln in (lng_min, lng_max)))
        self.bbox_utmk = (min(xs), min(ys), max(xs), max(ys))

        edge_cell = {}
        for e in self.edges:
            lat, lng = self.edge_latlng[e]
            code = grid_code_of(*latlng_to_utmk(lat, lng))
            if code:
                edge_cell[e] = code
        if not edge_cell:
            raise ValueError("이 지역은 TIMS 파일 범위('다사' 격자, 서울 대부분) 밖입니다. "
                             "demand_source를 'synthetic'으로 바꾸세요.")
        self.edge_cell = edge_cell

        codes = set(edge_cell.values()) | set(self._overlapping_codes())
        raw = load_tims_cells(tims_path, codes)
        x0, y0, x1, y1 = self.bbox_utmk
        self.cells: dict[str, CellStat] = {}
        for code, d in raw.items():
            cx0, cy0, cx1, cy1 = grid_bounds(code)
            ov = max(0.0, min(x1, cx1) - max(x0, cx0)) * max(0.0, min(y1, cy1) - max(y0, cy0)) / CELL_M ** 2
            trips = max(d["trips"], 1.0)
            self.cells[code] = CellStat(code, ov, d["daily"], d["dist"] / trips / 1000,
                                        d["rev"] / trips, d["time"] / trips / 60)
        for e, code in edge_cell.items():
            if code in self.cells:
                self.cells[code].edges.append(e)
        # 영역과 겹치지만 도로가 하나도 배정되지 않은 칸의 수요는 버리지 않고 보고만 한다
        self.dropped_cells = {c: s.area_daily for c, s in self.cells.items() if s.overlap > 0 and not s.edges}
        self.active = {c: s for c, s in self.cells.items() if s.edges and s.overlap > 0}
        if not self.active:
            raise ValueError("TIMS 격자와 도로가 하나도 연결되지 않았습니다.")

    def _overlapping_codes(self):
        x0, y0, x1, y1 = self.bbox_utmk
        for prefix, (gx, gy) in _GRID_SQUARES.items():
            for ix in range(int((x0 - gx) // CELL_M), int((x1 - gx) // CELL_M) + 1):
                for iy in range(int((y0 - gy) // CELL_M), int((y1 - gy) // CELL_M) + 1):
                    if 0 <= ix < 200 and 0 <= iy < 200:
                        yield f"{prefix}{ix:03d}{iy:03d}"

    # ----- 총량 -----
    def area_daily_calls(self, date: _dt.date, is_holiday: bool | None = None) -> float:
        """해당 날짜의 영역 안 하루 호출 수 (24시간, real_demand_scale 적용 전).

        월 평균(TIMS 2025, 그 달의 계절성)
          x 평일/주말 배율(강남구 2025 같은 달 실측: 예 9월 평일 1.046, 주말 0.873)
          x 평일 안·주말 안 요일 차이(서울시 2022.1).
        평일 공휴일은 주말 배율 + 일요일 차이. 도로가 없는 칸의 수요는 제외한다(dropped_cells로 보고).
        """
        month = date.month
        base = 0.0
        for s in self.active.values():
            daily = s.daily_by_month.get(month)
            if daily is None:
                daily = sum(s.daily_by_month.values()) / len(s.daily_by_month)
            base += s.overlap * daily
        holiday_weekday = bool(is_holiday) and date.weekday() < 5
        weekend = holiday_weekday or date.weekday() >= 5
        wd_ratio, we_ratio = self.weekpart[month]
        key = "sun" if holiday_weekday else DOW_KEYS[date.weekday()]
        return base * (we_ratio if weekend else wd_ratio) * self.dow[key]

    def weather_factors(self, hourly_precip_mm) -> list[float]:
        """시간별 강수량(mm, 24개) -> 시간별 호출 배율 1 + coef x min(강수, cap)."""
        if not hourly_precip_mm:
            return [1.0] * 24
        return [1.0 + self.precip_coef * min(max(float(p or 0.0), 0.0), self.precip_cap) for p in hourly_precip_mm]

    def hourly_calls(self, date: _dt.date, is_holiday: bool = False, weather_factor: float = 1.0,
                     hourly_precip_mm=None) -> list[float]:
        """0~23시 각 시간의 기대 호출 수.
        scale x (실측 날씨가 있으면 시간별 강수 배율, 없으면 기존 '날씨적용' 단일 배율 weather_factor)."""
        weekend = is_holiday or date.weekday() >= 5
        prof = self.hourly["weekend" if weekend else "weekday"]
        total = self.area_daily_calls(date, is_holiday) * self.scale
        hourly_f = self.weather_factors(hourly_precip_mm) if hourly_precip_mm else [weather_factor] * 24
        return [total * p * f for p, f in zip(prof, hourly_f)]

    def cell_shares(self, date: _dt.date) -> dict[str, float]:
        w = {c: s.overlap * s.daily_by_month.get(date.month, s.area_daily / max(s.overlap, 1e-9))
             for c, s in self.active.items()}
        tot = sum(w.values())
        return {c: v / tot for c, v in w.items()}

    def internal_share(self, code: str) -> float:
        """영역 안에서 끝나는 손님 비율. 운행거리가 평균 L km인 지수분포라고 보고
        P(거리 < real_internal_trip_km) = 1 - exp(-d/L). (강남역 칸 평균 L≈10.5km -> 약 9%)"""
        mean_km = max(self.cells[code].mean_dist_km, 0.1)
        return 1 - math.exp(-self.internal_km / mean_km)

    def edge_weight(self, edge: str, hour: float) -> float:
        cats = self.edge_cats.get(edge)
        if not cats:
            return 1.0
        return max(_hour_weight(self.poi_weights.get(c, [[0, 24, 1.0]]), hour) for c in cats)

    def main_category(self, edge: str) -> str:
        cats = self.edge_cats.get(edge) or ["none"]
        return max(cats, key=lambda c: (_hour_weight(self.poi_weights.get(c, [[0, 24, 1.0]]), 12), c))

    # ----- 스케줄 -----
    def build_schedule(self, date: _dt.date, start_hour: float, end_hour: float, seed,
                       is_holiday: bool = False, weather_factor: float = 1.0, hourly_precip_mm=None) -> list[Call]:
        """[start_hour, end_hour) 구간의 호출 목록. 같은 (date, seed, 날씨)면 항상 같은 결과."""
        rng = random.Random(f"fetex-demand|{seed}|{date.isoformat()}")
        rates = self.hourly_calls(date, is_holiday, weather_factor, hourly_precip_mm)
        shares = self.cell_shares(date)
        cells = sorted(shares)
        cell_w = [shares[c] for c in cells]
        calls: list[Call] = []
        dest_cache: dict[int, list[float]] = {}
        h = math.floor(start_hour)
        while h < end_hour:
            lo, hi = max(h, start_hour), min(h + 1, end_hour)
            rate_per_sec = rates[int(h) % 24] / 3600.0
            if rate_per_sec > 0:
                # 시간 h 안에서 균일한 포아송 과정 (지수분포 간격)
                t = lo * 3600.0
                weights_cache = {}
                while True:
                    t += rng.expovariate(rate_per_sec)
                    if t >= hi * 3600.0:
                        break
                    hour = t / 3600.0
                    cell = rng.choices(cells, cell_w)[0]
                    pool = self.active[cell].edges
                    key = (cell, int(hour))
                    if key not in weights_cache:
                        weights_cache[key] = [self.edge_weight(e, hour % 24) for e in pool]
                    origin = rng.choices(pool, weights_cache[key])[0]
                    frac = rng.uniform(0.1, 0.9)
                    if rng.random() < self.internal_share(cell):
                        kind = "internal"
                        dest = origin
                        hk = int(hour) % 24
                        if hk not in dest_cache:
                            dest_cache[hk] = [self.edge_weight(e, hk) for e in self.edges]
                        for _ in range(10):
                            dest = rng.choices(self.edges, dest_cache[hk])[0]
                            if dest != origin:
                                break
                    else:
                        kind = "external"
                        dest = rng.choice(self.boundary)
                        if dest == origin and len(self.boundary) > 1:
                            dest = rng.choice([b for b in self.boundary if b != origin])
                    if dest == origin:
                        continue
                    calls.append(Call(round(t - start_hour * 3600.0, 3), origin, round(frac, 4), dest,
                                      kind, cell, self.main_category(origin)))
            h += 1
        return calls

    # ----- 요금(매출) 추정 -----
    def mean_fare(self, cell: str | None = None) -> float:
        if cell and cell in self.cells:
            return self.cells[cell].mean_fare_won
        tot = sum(s.area_daily for s in self.active.values())
        return sum(s.mean_fare_won * s.area_daily for s in self.active.values()) / max(tot, 1e-9)

    def summary(self, date: _dt.date, start_hour: float, end_hour: float, is_holiday: bool = False) -> dict:
        rates = self.hourly_calls(date, is_holiday)
        in_window = sum(rates[h] * (min(h + 1, end_hour) - max(h, start_hour))
                        for h in range(24) if h + 1 > start_hour and h < end_hour)
        return {
            "date": date.isoformat(), "is_holiday": bool(is_holiday),
            "area_daily_calls_real": round(self.area_daily_calls(date, is_holiday), 1),
            "scale": self.scale,
            "expected_calls_in_window": round(in_window, 1),
            "cells": {c: {"overlap": round(s.overlap, 3), "edges": len(s.edges),
                          "share": round(v, 4), "internal_share": round(self.internal_share(c), 3),
                          "mean_dist_km": round(s.mean_dist_km, 2), "mean_fare_won": round(s.mean_fare_won)}
                      for c, s in self.active.items() for v in [self.cell_shares(date)[c]]},
            "dropped_cells_daily": {c: round(v, 1) for c, v in self.dropped_cells.items()},
            "mean_fare_won": round(self.mean_fare()),
        }
