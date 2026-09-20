"""
[Module 2 / T4] 외부 데이터 결합 + 결측치 처리.

명세 Q3 "시공간 단위가 다른 외부 데이터(날씨 등)를 결합할 때 기준을 맞추는 병합 로직"
- 수요 시계열은 5분 칸, 날씨 관측은 보통 1시간 단위(기상청·Open-Meteo). 관측 간격은 자료에서 추정하므로 5분 관측(합성 external.csv)도 그대로 쓴다.
- pd.merge_asof(direction='backward'): 각 5분 칸에 "그 시각 이전 가장 최근 관측"을 붙인다.
  → 09:05~09:55 칸은 09:00 관측, 10:00 칸부터 10:00 관측. 미래 관측을 쓰지 않으므로 누수 없음.
- tolerance=max_gap_hours: 관측이 그 이상 끊겨 있으면 붙이지 않고 결측으로 남긴다.

결측치 처리 정책 (명세 R4)
1) 날씨 파일 안의 짧은 공백(≤ interpolate_limit 시간): 시간 축 선형 보간 → weather_interpolated=1
2) 긴 공백 / tolerance 밖: NaN 유지 → weather_missing=1  (모델은 이 플래그로 "모름"을 학습)
   ※ weather_missing은 "원천이 제공하는 변수"의 관측 공백만 뜻한다. 원천 자체가 어떤 변수를 아예 주지 않으면
     (예: 합성 external.csv에는 wind_speed가 없음) 그 변수는 판정에서 제외한다 — 그래야 플래그가 전 행 1로
     고정되지 않고 실제 공백을 구분한다. 제공되지 않은 변수는 NaN으로 남고 학습 시 fillna(0) 규칙을 따른다.
3) 날씨 파일 자체가 없음: config의 현재 기온으로 상수 채움 + weather_source='fallback' (학습 시 경고)
4) 유효범위: precipitation < 0 이면 ValueError (Data Spec 5장 — 잘못된 값으로 학습 진행 금지)
is_rain = precipitation > 0 (Data Spec 3장).

[강화] 2026-09-17 윤세빈 — 날씨 품질 기준·파생 피처
- 물리 범위: 기온 -40~50℃, 강수 0~200mm/h, 풍속 0~75m/s 밖이면 ValueError (센서 오류·단위 착오 차단)
- 시간축: 관측 시각은 정시(1h) 정렬, 중복 시각은 첫 관측만 사용, 역순이면 정렬
- 커버리지: 병합 후 weather_missing 비율이 max_missing_ratio(config weather_missing_max_ratio, 기본 0.05)를 넘으면
  ValueError — Data Spec 5장 "결측률 5% 미만" (학습 경로에서만 검사, 서빙은 None으로 끔)
- 파생 피처(시간별 표에서 과거 방향으로만 계산 → 누수 없음):
    precip_3h_sum   직전 3시간 강수 합 (비가 '오고 있는 중'인지, 지면이 젖었는지)
    rain_streak_h   연속 강수 시간 수 (0이면 비 안 옴)
    temp_anomaly_24h 기온 − 직전 24시간 평균 (그 시각의 '평소보다 덥다/춥다')
※ 기존 코드의 np.random 날씨 생성은 삭제 — 랜덤값은 수요와 무관해 모델에 노이즈만 추가하고 EDA 결과를 왜곡함.

공휴일(외부 달력 데이터)은 time_features.add_time_features가 붙인다 (holidays 패키지 KR).
"""
import sys
import os
import glob
import warnings
import numpy as np
import pandas as pd

from fetex.core.config import CFG
from fetex.core.paths import PROJECT_ROOT

ROOT = str(PROJECT_ROOT)
EXTERNAL_DIR = os.path.join(ROOT, "data", "external")
WEATHER_COLS = ["temperature", "precipitation", "wind_speed"]


def find_weather_file(region: str = None) -> str:
    """data/external/weather_<지역>_*.csv 중 가장 최근 파일 (없으면 None)."""
    region = region or CFG.get("region", "")
    files = sorted(glob.glob(os.path.join(EXTERNAL_DIR, f"weather_{region}_*.csv"))) or \
            sorted(glob.glob(os.path.join(EXTERNAL_DIR, "weather_*.csv")))
    return files[-1] if files else None


def load_weather(path: str) -> pd.DataFrame:
    """시간별 날씨 CSV → DataFrame(time, temperature, precipitation, wind_speed), 시간순 정렬."""
    w = pd.read_csv(path)
    tcol = "time" if "time" in w.columns else w.columns[0]
    w = w.rename(columns={tcol: "time"})
    w["time"] = pd.to_datetime(w["time"])
    for c in WEATHER_COLS:
        if c not in w.columns:
            w[c] = np.nan
    w = _check_time_axis(w[["time"] + WEATHER_COLS], os.path.basename(path))
    _check_ranges(w, os.path.basename(path))
    return w


WEATHER_RANGES = {"temperature": (-40.0, 50.0), "precipitation": (0.0, 200.0), "wind_speed": (0.0, 75.0)}


def _check_ranges(w: pd.DataFrame, source: str = "weather") -> None:
    """유효범위 검사 (Data Spec 5장): 물리적으로 불가능한 값이 있으면 ValueError로 즉시 중단.
    결측(NaN)은 여기서 막지 않는다 — 결측은 보간·플래그 정책으로 처리."""
    for col, (lo, hi) in WEATHER_RANGES.items():
        if col not in w.columns:
            continue
        v = pd.to_numeric(w[col], errors="coerce")
        bad = v.notna() & ((v < lo) | (v > hi))
        if bad.any():
            raise ValueError(f"{source}: {col} 유효범위({lo}~{hi}) 밖 관측이 {int(bad.sum())}건 있습니다 "
                             f"(예: {v[bad].iloc[0]}). 단위·센서 오류를 확인하세요.")
    non_numeric = [c for c in WEATHER_RANGES if c in w.columns and w[c].notna().any()
                   and pd.to_numeric(w[c], errors="coerce").isna().sum() > w[c].isna().sum()]
    if non_numeric:
        raise ValueError(f"{source}: 숫자가 아닌 값이 있는 컬럼 {non_numeric}")


ALLOWED_STEPS_MIN = (5, 10, 15, 20, 30, 60)


def infer_step(times: pd.Series) -> pd.Timedelta:
    """관측 간격 추정: 시각 차이의 최빈값. 5/10/15/20/30/60분 중 하나여야 한다 (그 밖이면 ValueError)."""
    t = pd.Series(pd.to_datetime(times)).sort_values().drop_duplicates()
    if len(t) < 2:
        return pd.Timedelta(hours=1)
    step = t.diff().dropna().mode().iloc[0]
    if step.total_seconds() / 60 not in ALLOWED_STEPS_MIN:
        raise ValueError(f"날씨 관측 간격 {step}은 지원하지 않습니다 (허용: {ALLOWED_STEPS_MIN}분). 시간별 또는 5분 단위 자료여야 합니다.")
    return step


def _check_time_axis(w: pd.DataFrame, source: str = "weather") -> pd.DataFrame:
    """관측 시각 검사: 파싱 가능, 관측 간격(추정)의 경계에 정렬. 중복은 첫 관측만, 역순은 정렬.
    간격은 자료에서 추정한다 — 기상청·Open-Meteo 시간자료는 1h, 합성 external.csv는 5min.
    (예전 코드는 무조건 1h 격자로 reindex해 5분 관측의 11/12를 버렸다 → 합성 강수 신호가 사라지는 원인이었음)"""
    t = pd.to_datetime(w["time"], errors="coerce")
    if t.isna().any():
        raise ValueError(f"{source}: 파싱할 수 없는 관측 시각이 {int(t.isna().sum())}건 있습니다.")
    step = infer_step(t)
    off = (t != t.dt.floor(step))
    if off.any():
        raise ValueError(f"{source}: 관측 간격 {step} 경계에 맞지 않는 시각이 {int(off.sum())}건 있습니다 (예: {t[off].iloc[0]}).")
    w = w.copy(); w["time"] = t
    w = w.sort_values("time", kind="stable").drop_duplicates("time", keep="first").reset_index(drop=True)
    w.attrs["step"] = step
    return w


def provided_weather_cols(weather: pd.DataFrame) -> list:
    """원천이 실제로 제공하는 날씨 변수 (전부 NaN인 컬럼은 '미제공'으로 보고 제외)."""
    return [c for c in WEATHER_COLS if c in weather.columns and weather[c].notna().any()]


def add_weather_derived(w: pd.DataFrame, cols: list = None, step: pd.Timedelta = None) -> pd.DataFrame:
    """날씨 표에 과거 방향 파생 피처를 붙인다 (현재 시각 포함, 미래 미포함 → 5분 칸에 backward 결합해도 누수 없음).
    창 길이는 '시간' 기준이고 관측 간격(step)에 맞춰 칸 수로 환산한다 (1h 자료면 3칸, 5min 자료면 36칸)."""
    w = w.copy()
    cols = cols if cols is not None else provided_weather_cols(w)
    step = step or w.attrs.get("step") or infer_step(w["time"])
    per_hour = max(1, int(round(pd.Timedelta(hours=1) / step)))
    if "precipitation" in cols:
        p = w["precipitation"].fillna(0.0)
        w["precip_3h_sum"] = p.rolling(3 * per_hour, min_periods=1).sum()
        raining = (p > 0).astype(int)
        grp = (raining == 0).cumsum()                       # 비가 그치면 리셋
        w["rain_streak_h"] = raining.groupby(grp).cumsum() / per_hour   # 연속 강수 시간(h)
    else:
        w["precip_3h_sum"] = np.nan; w["rain_streak_h"] = np.nan
    if "temperature" in cols:
        w["temp_anomaly_24h"] = w["temperature"] - w["temperature"].rolling(24 * per_hour, min_periods=1).mean()
    else:
        w["temp_anomaly_24h"] = np.nan
    return w


def _fill_short_gaps(w: pd.DataFrame, interpolate_limit_hours: int, cols: list = None, step: pd.Timedelta = None) -> pd.DataFrame:
    """관측 자체에 빠진 시각이 있으면 관측 간격 격자로 펼친 뒤 짧은 공백(≤ interpolate_limit 시간)만 보간.
    cols: 원천이 제공하는 변수만 (미제공 변수는 보간·플래그 판정에서 제외)."""
    cols = list(cols) if cols is not None else list(WEATHER_COLS)
    step = step or w.attrs.get("step") or infer_step(w["time"])
    limit = max(1, int(round(pd.Timedelta(hours=interpolate_limit_hours) / step)))
    full = pd.date_range(w["time"].min(), w["time"].max(), freq=step)
    w = w.set_index("time").reindex(full)
    w.index.name = "time"
    was_nan = w[cols].isna().any(axis=1) if cols else pd.Series(False, index=w.index)
    if cols:
        w[cols] = w[cols].interpolate(method="linear", limit=limit, limit_area="inside")
        w["weather_interpolated"] = (was_nan & w[cols].notna().all(axis=1)).astype(int)
    else:
        w["weather_interpolated"] = 0
    out = w.reset_index()
    out.attrs["step"] = step
    return out


def merge_external_data(df: pd.DataFrame, weather: pd.DataFrame = None, weather_path: str = None,
                        time_col: str = None, max_gap_hours: int = 3, interpolate_limit_hours: int = 3,
                        verbose: bool = True, max_missing_ratio: float = None) -> pd.DataFrame:
    """
    df(5분 칸 수요 테이블 또는 호출 로그)에 날씨 컬럼을 붙인다.
    time_col: 기준 시각 컬럼 (없으면 time_bucket → pickup_datetime → datetime 순으로 탐색)
    max_missing_ratio: 병합 후 weather_missing 비율 상한. 넘으면 ValueError (Data Spec 5장 결측률 5% 미만).
                       None이면 검사 안 함(서빙 경로). 학습 경로(pipeline)는 config weather_missing_max_ratio(기본 0.05).
    """
    df = df.copy()
    if time_col is None:
        time_col = next((c for c in ("time_bucket", "pickup_datetime", "datetime") if c in df.columns), None)
    if time_col is None:
        raise ValueError("시각 컬럼(time_bucket/pickup_datetime/datetime)이 없습니다")
    df[time_col] = pd.to_datetime(df[time_col])

    if weather is None:
        weather_path = weather_path or find_weather_file()
        if weather_path and os.path.exists(weather_path):
            weather = load_weather(weather_path)
            if verbose:
                print(f"[외부 데이터] 날씨 파일 사용: {os.path.basename(weather_path)} ({len(weather)}시간, "
                      f"{weather['time'].min()} ~ {weather['time'].max()})")

    if weather is None or weather.empty:
        # 3) 폴백: 상수 채움. 학습에 쓰면 날씨 피처는 정보량 0 — 반드시 경고.
        warnings.warn("날씨 파일이 없어 config 현재 기온으로 상수 채움 (weather_source='fallback'). "
                      "scripts/fetch_weather_history.py 로 실데이터를 받으세요.")
        df["temperature"] = float(CFG.get("current_temperature", (CFG.get("temp_min", 15) + CFG.get("temp_max", 25)) / 2))
        df["precipitation"] = float(CFG.get("current_precipitation", 0.0))
        df["wind_speed"] = np.nan
        df["weather_interpolated"] = 0
        df["weather_missing"] = 1
        df["weather_source"] = "fallback"
    else:
        weather = weather.copy()
        for c in WEATHER_COLS:
            if c not in weather.columns:
                weather[c] = np.nan
        weather = _check_time_axis(weather, "weather")
        _check_ranges(weather, "weather")
        provided = provided_weather_cols(weather)
        not_provided = [c for c in WEATHER_COLS if c not in provided]
        step = weather.attrs.get("step")
        w = _fill_short_gaps(weather, interpolate_limit_hours, cols=provided, step=step)
        w = add_weather_derived(w, cols=provided, step=step)
        df["_row_order"] = np.arange(len(df))
        merged = pd.merge_asof(df.sort_values(time_col), w.sort_values("time"),
                               left_on=time_col, right_on="time", direction="backward",
                               tolerance=pd.Timedelta(hours=max_gap_hours))
        df = merged.sort_values("_row_order").drop(columns=["time", "_row_order"]).reset_index(drop=True)
        # 결측 플래그는 '제공되는 변수'의 공백만 본다 (미제공 변수 때문에 전 행 1로 고정되는 것을 막음)
        df["weather_missing"] = (df[provided].isna().any(axis=1) if provided else pd.Series(True, index=df.index)).astype(int)
        df["weather_interpolated"] = df["weather_interpolated"].fillna(0).astype(int)
        df["weather_source"] = "observed"
        miss_ratio = float(df["weather_missing"].mean())
        if verbose:
            extra = f", 원천 미제공 변수 {not_provided}(NaN 유지)" if not_provided else ""
            print(f"[외부 데이터] 병합 완료: 결측 {miss_ratio * 100:.1f}% (tolerance {max_gap_hours}h), 보간 {df['weather_interpolated'].sum()}행{extra}")
        if max_missing_ratio is not None and miss_ratio > max_missing_ratio:
            span = f"{df[time_col].min()} ~ {df[time_col].max()}"
            wspan = f"{weather['time'].min()} ~ {weather['time'].max()}"
            raise ValueError(f"날씨 결측률 {miss_ratio * 100:.1f}%가 허용치 {max_missing_ratio * 100:.0f}%를 넘습니다. "
                             f"수요 기간 {span} 을 날씨 기간 {wspan} 이 덮지 못합니다 — 날씨 파일을 다시 받거나 기간을 재선정하세요.")

    for c in WEATHER_DERIVED_COLS:
        if c not in df.columns:
            df[c] = np.nan
    df["is_rain"] = (df["precipitation"].fillna(0) > 0).astype(int)
    return df


WEATHER_DERIVED_COLS = ["precip_3h_sum", "rain_streak_h", "temp_anomaly_24h"]
EXTERNAL_FEATURE_COLS = ["temperature", "precipitation", "wind_speed", "is_rain", "weather_missing", "weather_interpolated"] + WEATHER_DERIVED_COLS
