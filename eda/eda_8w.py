# -*- coding: utf-8 -*-
"""
[Module 2 / T5] 8주 합성 데이터 EDA — 시간·공간·날씨·휴일 패턴, 정상성, 자기상관 그림과 수치를 한 번에 만든다.

    python eda/eda_8w.py                                   # data/generated/calls.csv + external.csv
    python eda/eda_8w.py --calls <calls.csv> --external <external.csv> --out data/eda/8w

산출물 (out 폴더): 01_daily.png 02_hour_dow_heatmap.png 03_cell_profiles.png 04_weekday_holiday.png
                  05_weather_rain.png 06_weather_temp.png 07_rain_day.png 08_stationarity.png 09_acf.png
                  summary.md (REPORT 2장에 붙이는 수치 표)
설계: 호출 로그의 h3_index 컬럼을 그대로 쓰므로 h3 패키지 없이도 돈다. 외부 관측은 5분×셀이라 셀 평균으로 시각별 표를 만든다.
정상성: statsmodels가 있으면 adfuller(p-value), 없으면 같은 회귀를 numpy로 풀어 통계량과 MacKinnon 임계값(1%/5%/10%)으로 판정.
"""
import os, sys, argparse, warnings
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
warnings.filterwarnings("ignore")
import numpy as np, pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager

for f in ["AppleGothic", "Apple SD Gothic Neo", "NanumGothic", "Malgun Gothic", "Noto Sans CJK KR", "Noto Sans CJK JP", "Noto Sans CJK SC"]:
    if any(f == x.name for x in font_manager.fontManager.ttflist):
        plt.rcParams["font.family"] = f; break
plt.rcParams["axes.unicode_minus"] = False
INK, MUTED, BLUE, AMBER, RED = "#2b2b2b", "#8a8a8a", "#1f6f8b", "#c98a00", "#b3362b"
DOW = ["월", "화", "수", "목", "금", "토", "일"]


def _style(ax, title=None):
    for sp in ("top", "right"): ax.spines[sp].set_visible(False)
    ax.spines["left"].set_color("#cccccc"); ax.spines["bottom"].set_color("#cccccc")
    ax.tick_params(colors=MUTED, labelsize=9); ax.grid(axis="y", color="#eeeeee")
    if title: ax.set_title(title, loc="left", color=INK, fontsize=11)


# ---------- 데이터 ----------
def load(calls_path, external_path, freq="5min"):
    calls = pd.read_csv(calls_path, parse_dates=["pickup_datetime"])
    calls["time_bucket"] = calls["pickup_datetime"].dt.floor(freq)
    cells = sorted(calls["h3_index"].unique())
    buckets = pd.date_range(calls["time_bucket"].min().normalize(), calls["time_bucket"].max(), freq=freq)
    agg = (calls.groupby(["time_bucket", "h3_index"]).size().rename("demand")
           .reindex(pd.MultiIndex.from_product([buckets, cells], names=["time_bucket", "h3_index"]), fill_value=0).reset_index())
    agg["hour"] = agg["time_bucket"].dt.hour; agg["dow"] = agg["time_bucket"].dt.dayofweek; agg["date"] = agg["time_bucket"].dt.date
    ext = None
    if external_path and os.path.exists(external_path):
        e = pd.read_csv(external_path, parse_dates=["time_bucket"])
        ext = e.groupby("time_bucket")[["temperature", "precipitation"] + [c for c in ("traffic_index", "event_flag", "is_holiday") if c in e.columns]].mean()
        agg = agg.merge(ext, left_on="time_bucket", right_index=True, how="left")
    try:
        import holidays as _h
        hol = set(_h.country_holidays("KR", years=sorted(set(agg["time_bucket"].dt.year))).keys())
    except Exception:
        hol = set()
    agg["is_public_holiday"] = agg["date"].isin(hol).astype(int)
    agg["is_off"] = ((agg["dow"] >= 5) | (agg["is_public_holiday"] == 1)).astype(int)
    return calls, agg, ext, hol


# ---------- 1. 일별 ----------
def fig_daily(agg, out):
    d = agg.groupby("date")["demand"].sum()
    fig, ax = plt.subplots(figsize=(11, 3.2))
    colors = [AMBER if pd.Timestamp(x).dayofweek >= 5 else BLUE for x in d.index]
    ax.bar(range(len(d)), d.values, color=colors, width=.8)
    ax.set_xticks(range(0, len(d), 7)); ax.set_xticklabels([pd.Timestamp(x).strftime("%m/%d") for x in d.index[::7]])
    _style(ax, f"일별 호출 수 — {len(d)}일, 평균 {d.mean():.0f}건/일 (주황=주말)")
    fig.tight_layout(); fig.savefig(os.path.join(out, "01_daily.png"), dpi=130); plt.close(fig)
    return d


# ---------- 2. 시간대 × 요일 ----------
def fig_hour_dow(agg, out):
    days = agg.groupby("dow")["date"].nunique()
    tab = agg.groupby(["dow", "hour"])["demand"].sum().unstack("hour").div(days, axis=0)
    fig, ax = plt.subplots(figsize=(11, 4.2))
    im = ax.imshow(tab.values, aspect="auto", cmap="Blues")
    ax.set_yticks(range(7)); ax.set_yticklabels([f"{DOW[i]} ({days[i]}일)" for i in tab.index])
    ax.set_xticks(range(24)); ax.set_xticklabels(range(24), fontsize=8)
    for sp in ax.spines.values(): sp.set_visible(False)
    ax.tick_params(length=0, colors=MUTED)
    ax.set_title("시간대 × 요일 호출 수 (요일별 하루 평균, 전체 셀 합)", loc="left", color=INK)
    cb = fig.colorbar(im, ax=ax, fraction=0.02, pad=0.01); cb.outline.set_visible(False)
    fig.tight_layout(); fig.savefig(os.path.join(out, "02_hour_dow_heatmap.png"), dpi=130); plt.close(fig)
    return tab


# ---------- 3. 셀별 시간 프로필 ----------
def fig_cells(agg, out):
    ndays = agg["date"].nunique()
    prof = agg.groupby(["h3_index", "hour"])["demand"].sum().unstack("hour") / ndays
    tot = agg.groupby("h3_index")["demand"].sum().sort_values(ascending=False)
    fig, axes = plt.subplots(1, 2, figsize=(12, 3.6), gridspec_kw={"width_ratios": [1, 2]})
    axes[0].barh(range(len(tot)), tot.values[::-1], color=BLUE)
    axes[0].set_yticks(range(len(tot))); axes[0].set_yticklabels([c[6:10] for c in tot.index[::-1]], fontsize=8)
    _style(axes[0], f"셀별 총 호출 (최다 셀 {tot.iloc[0] / tot.sum() * 100:.0f}%)")
    for c in prof.index:
        axes[1].plot(prof.columns, prof.loc[c], lw=1.6, label=c[6:10])
    axes[1].legend(fontsize=7, ncol=4, frameon=False); axes[1].set_xticks(range(0, 24, 2))
    _style(axes[1], "셀별 시간대 프로필 (하루 평균 호출/시간) — 피크 시각이 셀마다 다름")
    fig.tight_layout(); fig.savefig(os.path.join(out, "03_cell_profiles.png"), dpi=130); plt.close(fig)
    return tot, prof


# ---------- 4. 평일/주말/공휴일 ----------
def fig_weekday(agg, out, hol):
    g = agg.copy()
    g["group"] = np.where(g["is_public_holiday"] == 1, "공휴일", np.where(g["dow"] >= 5, "주말", "평일"))
    prof = g.groupby(["group", "hour"])["demand"].sum() / g.groupby(["group", "hour"])["date"].nunique()
    prof = prof.unstack("group")
    fig, ax = plt.subplots(figsize=(11, 3.4))
    for col, color in (("평일", BLUE), ("주말", AMBER), ("공휴일", RED)):
        if col in prof: ax.plot(prof.index, prof[col], lw=2, color=color, label=col)
    ax.legend(frameon=False); ax.set_xticks(range(0, 24, 2))
    per_day = g.groupby(["group", "date"])["demand"].sum().groupby("group").mean()
    _style(ax, "시간대별 하루 평균 호출 — " + ", ".join(f"{k} {v:.0f}건/일" for k, v in per_day.items()))
    fig.tight_layout(); fig.savefig(os.path.join(out, "04_weekday_holiday.png"), dpi=130); plt.close(fig)
    return per_day, sorted(d for d in hol if agg["date"].min() <= d <= agg["date"].max())


# ---------- 5·6. 날씨 (시간대·셀 통제 후 비율) ----------
def fig_weather(agg, out):
    if "precipitation" not in agg: return None
    g = agg.copy()
    base = g.groupby(["h3_index", "hour"])["demand"].transform("mean")
    g["ratio"] = g["demand"] / base.replace(0, np.nan)
    rain = g.groupby("precipitation")["ratio"].agg(["mean", "count"])
    fig, ax = plt.subplots(figsize=(6.5, 3.4))
    ax.bar([str(x) for x in rain.index], rain["mean"], color=[MUTED, BLUE, RED][:len(rain)], width=.55)
    ax.axhline(1, color="#999", lw=1, ls="--")
    for i, (m, n) in enumerate(zip(rain["mean"], rain["count"])): ax.text(i, m + .02, f"{m:.2f}\n(n={n:,})", ha="center", fontsize=9, color=INK)
    _style(ax, "강수량(mm)별 호출 비율 — 같은 셀·같은 시간대 평균 = 1 기준")
    fig.tight_layout(); fig.savefig(os.path.join(out, "05_weather_rain.png"), dpi=130); plt.close(fig)
    g["temp_bin"] = pd.cut(g["temperature"], bins=[-99, 20, 22, 24, 26, 28, 99], labels=["<20", "20-22", "22-24", "24-26", "26-28", ">28"])
    temp = g.groupby("temp_bin", observed=True)["ratio"].agg(["mean", "count"])
    fig, ax = plt.subplots(figsize=(6.5, 3.4))
    ax.bar(temp.index.astype(str), temp["mean"], color=BLUE, width=.6); ax.axhline(1, color="#999", lw=1, ls="--")
    _style(ax, "기온(℃) 구간별 호출 비율 — 시간대 통제 후")
    fig.tight_layout(); fig.savefig(os.path.join(out, "06_weather_temp.png"), dpi=130); plt.close(fig)
    # 하루 시계열 + 강수 오버레이, 강수 자기상관
    w = agg.groupby("time_bucket")[["precipitation", "temperature"]].first()
    ac1, ac12 = w["precipitation"].autocorr(1), w["precipitation"].autocorr(12)
    rainy_days = g.groupby("date")["precipitation"].mean().sort_values(ascending=False)
    day = rainy_days.index[0]
    s = g[g["date"] == day].groupby("time_bucket")[["demand", "precipitation"]].agg({"demand": "sum", "precipitation": "first"})
    fig, ax = plt.subplots(figsize=(11, 3.4)); ax2 = ax.twinx()
    ax.plot(s.index, s["demand"].rolling(6, min_periods=1).mean(), color=BLUE, lw=1.8, label="호출(30분 이동평균)")
    ax2.bar(s.index, s["precipitation"], width=pd.Timedelta(minutes=5), color=RED, alpha=.35, label="강수(mm)")
    ax2.set_ylabel("강수 mm", color=MUTED); ax2.tick_params(colors=MUTED)
    _style(ax, f"{day} 호출과 강수 — 강수 자기상관 5분 {ac1:.2f}, 1시간 {ac12:.2f} (실측 강남역 1시간 자기상관 0.77)")
    for sp in ("top",): ax2.spines[sp].set_visible(False)
    fig.tight_layout(); fig.savefig(os.path.join(out, "07_rain_day.png"), dpi=130); plt.close(fig)
    return rain, temp, ac1, ac12


# ---------- 8. 정상성 ----------
MACKINNON = {"1%": -3.43, "5%": -2.86, "10%": -2.57}   # 상수항 포함, n 큼


def adf(x: np.ndarray, maxlag: int = None):
    """ADF 검정. statsmodels 있으면 p-value 포함, 없으면 numpy OLS로 통계량만 (임계값 비교)."""
    x = np.asarray(x, float)
    try:
        from statsmodels.tsa.stattools import adfuller
        stat, p, used, *_ = adfuller(x, autolag="AIC")
        return stat, p, used
    except ImportError:
        pass
    n = len(x); maxlag = maxlag or int(12 * (n / 100) ** 0.25)
    dx = np.diff(x)
    y = dx[maxlag:]; X = [np.ones(len(y)), x[maxlag:-1]]
    for k in range(1, maxlag + 1): X.append(dx[maxlag - k:-k])
    X = np.column_stack(X)
    beta, res, *_ = np.linalg.lstsq(X, y, rcond=None)
    resid = y - X @ beta; s2 = (resid @ resid) / (len(y) - X.shape[1])
    se = np.sqrt(s2 * np.linalg.inv(X.T @ X)[1, 1])
    return beta[1] / se, None, maxlag


def fig_stationarity(agg, out):
    s = agg.groupby("time_bucket")["demand"].sum().astype(float)
    rows = []
    for name, x in (("원계열", s), ("1차 차분", s.diff().dropna())):
        stat, p, lag = adf(x.values)
        concl = ("정상" if (p is not None and p < 0.05) or (p is None and stat < MACKINNON["1%"]) else "비정상")
        rows.append({"계열": name, "ADF 통계량": round(float(stat), 3), "p-value": (round(p, 4) if p is not None else "—(statsmodels 없음)"),
                     "1% 임계값": MACKINNON["1%"], "사용 lag": lag, "결론": concl})
    fig, axes = plt.subplots(2, 1, figsize=(11, 4.6), sharex=True)
    week = s[s.index < s.index.min() + pd.Timedelta(days=7)]
    axes[0].plot(week.index, week.values, color=BLUE, lw=1); _style(axes[0], f"원계열 (첫 주) — ADF {rows[0]['ADF 통계량']} → {rows[0]['결론']}")
    axes[1].plot(week.index, week.diff().values, color=AMBER, lw=1); _style(axes[1], f"1차 차분 — ADF {rows[1]['ADF 통계량']} → {rows[1]['결론']}")
    fig.tight_layout(); fig.savefig(os.path.join(out, "08_stationarity.png"), dpi=130); plt.close(fig)
    return pd.DataFrame(rows).set_index("계열")


# ---------- 9. 자기상관 ----------
def fig_acf(agg, out, max_lag=48):
    acfs = []
    for _, g in agg.sort_values("time_bucket").groupby("h3_index"):
        x = g["demand"].to_numpy(float); x = x - x.mean(); den = (x * x).sum()
        acfs.append([1.0] + [(x[:-k] * x[k:]).sum() / den for k in range(1, max_lag + 1)])
    acf = np.mean(acfs, axis=0); n = agg["time_bucket"].nunique(); ci = 1.96 / np.sqrt(n)
    sig = int(np.argmax(np.abs(acf[1:]) < ci)) if (np.abs(acf[1:]) < ci).any() else max_lag
    # 하루(288칸)·일주일(2016칸) 계절 lag
    day_lag, week_lag = 288, 2016
    seasonal = {}
    for name, L in (("1일", day_lag), ("1주", week_lag)):
        vals = []
        for _, g in agg.sort_values("time_bucket").groupby("h3_index"):
            x = g["demand"].to_numpy(float); x = x - x.mean(); den = (x * x).sum()
            if len(x) > L + 2: vals.append((x[:-L] * x[L:]).sum() / den)
        seasonal[name] = float(np.mean(vals)) if vals else np.nan
    fig, ax = plt.subplots(figsize=(11, 3.2))
    ax.bar(range(max_lag + 1), acf, color=BLUE, width=.6)
    ax.axhline(ci, color=RED, lw=1, ls="--"); ax.axhline(-ci, color=RED, lw=1, ls="--")
    _style(ax, f"셀 평균 ACF (lag=5분칸, 0~{max_lag}) — 유의 lag {sig}개 (±{ci:.3f}) · 계절 lag 1일 {seasonal['1일']:.2f}, 1주 {seasonal['1주']:.2f}")
    ax.set_xlabel("lag (5분 칸)", color=MUTED)
    fig.tight_layout(); fig.savefig(os.path.join(out, "09_acf.png"), dpi=130); plt.close(fig)
    return acf, sig, ci, seasonal


def main(calls_path, external_path, out):
    os.makedirs(out, exist_ok=True)
    calls, agg, ext, hol = load(calls_path, external_path)
    daily = fig_daily(agg, out); tab = fig_hour_dow(agg, out); tot, prof = fig_cells(agg, out)
    per_day, hol_in = fig_weekday(agg, out, hol); wx = fig_weather(agg, out)
    st = fig_stationarity(agg, out); acf, sig, ci, seasonal = fig_acf(agg, out)

    peak_hour = tab.mean(axis=0).idxmax(); peak_dow = tab.sum(axis=1).idxmax()
    L = [f"# 8주 합성 데이터 EDA 요약 ({calls['pickup_datetime'].min():%Y-%m-%d} ~ {calls['pickup_datetime'].max():%Y-%m-%d})", "",
         f"- 호출 {len(calls):,}건, {agg['date'].nunique()}일, 셀 {agg['h3_index'].nunique()}개, 5분칸 {agg['time_bucket'].nunique():,}개 → (셀×칸) {len(agg):,}행",
         f"- 수요 0인 칸 {(agg['demand'] == 0).mean() * 100:.1f}%, 칸당 평균 {agg['demand'].mean():.3f}건, 최대 {agg['demand'].max()}건",
         f"- 일별 호출 평균 {daily.mean():.0f}건 (최소 {daily.min()} / 최대 {daily.max()}), 요일별 하루 평균: " + ", ".join(f"{DOW[i]} {v:.0f}" for i, v in tab.sum(axis=1).items()),
         f"- 피크 시간대 {peak_hour}시, 최다 요일 {DOW[peak_dow]}. 평일/주말/공휴일 하루 평균: " + ", ".join(f"{k} {v:.0f}건" for k, v in per_day.items()) + (f" (공휴일: {', '.join(map(str, hol_in))})" if hol_in else " (기간 내 평일 공휴일 없음)"),
         f"- 셀 집중도: 최다 셀 {tot.iloc[0] / tot.sum() * 100:.1f}%, 최소 셀 {tot.iloc[-1] / tot.sum() * 100:.1f}% — 셀별 피크 시각: " + ", ".join(f"{c[6:10]} {int(prof.loc[c].idxmax())}시" for c in prof.index), ""]
    if wx:
        rain, temp, ac1, ac12 = wx
        L += ["## 날씨", "| 강수(mm) | 호출 비율(시간대·셀 통제) | 칸 수 |", "|---|---:|---:|"]
        L += [f"| {p} | {m:.3f} | {int(n):,} |" for p, (m, n) in rain.iterrows()]
        L += ["", "| 기온 구간 | 호출 비율 | 칸 수 |", "|---|---:|---:|"] + [f"| {b} | {m:.3f} | {int(n):,} |" for b, (m, n) in temp.iterrows()]
        L += ["", f"- 강수 자기상관: 5분 {ac1:.3f}, 1시간 {ac12:.3f} (실측 강남역 1시간 0.77) → 합성 강수는 지속성이 없어 미래 강수를 과거로 예측할 수 없음", ""]
    L += ["## 정상성 (ADF)", st.to_markdown() if hasattr(st, "to_markdown") else st.to_string(), "",
          "## 자기상관", f"- 셀 평균 ACF 유의 lag {sig}개 (±{ci:.3f}), lag1 {acf[1]:.3f}, lag6 {acf[6]:.3f}, lag12 {acf[12]:.3f}",
          f"- 계절 lag: 1일 전 {seasonal['1일']:.3f}, 1주 전 {seasonal['1주']:.3f}", ""]
    md = "\n".join(L)
    with open(os.path.join(out, "summary.md"), "w", encoding="utf-8") as f: f.write(md + "\n")
    print(md); print(f"[저장] {out}/01~09 png + summary.md")
    return md


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    # 기본 입력: data/generated_8w (8주, 저장소 밖 보관) 가 있으면 그것, 없으면 data/generated
    gen = "generated_8w" if os.path.exists(os.path.join(ROOT, "data", "generated_8w", "calls.csv")) else "generated"
    ap.add_argument("--calls", default=os.path.join(ROOT, "data", gen, "calls.csv"))
    ap.add_argument("--external", default=os.path.join(ROOT, "data", gen, "external.csv"))
    ap.add_argument("--out", default=os.path.join(ROOT, "data", "eda", "8w"))
    a = ap.parse_args()
    main(a.calls, a.external, a.out)
