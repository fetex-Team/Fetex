"""수요 로그 분석기: 시뮬레이션이 남긴 data/sim_logs/demand_log_*.csv 로 평일 vs 공휴일, 전략·알고리즘별 차이를 본다.

무엇을 보나
  1. 요약          : 평일/공휴일별 로그 수, 호출 수, 탑승률, 타임아웃률, 대기시간(평균/중앙/90%)
  2. 시간대 곡선   : 시간별 호출 수 (평일 vs 공휴일) + 공휴일/평일 비율
  3. 카테고리 수요 : 출발 구역(school/company/restaurant/...)별 호출 수 + 비율, 설정한 공휴일 배율과 나란히 비교
  4. 전략·알고리즘 : (전략 × 배차 알고리즘 × 평일/공휴일)별 호출/탑승률/대기시간 -> 어느 조합이 어느 날에 강한지
  5. 데이터 점검   : 평일·공휴일 한쪽이 비었는지, 표본이 너무 적은지, 같은 날짜가 겹쳤는지

사용법
  python analyze_demand_logs.py                 # 학습 로그 선택(config_gui)에서 고른 로그, 없으면 전체
  python analyze_demand_logs.py --all           # 선택 무시하고 data/sim_logs 전체
  python analyze_demand_logs.py --logs a.csv b.csv
  python analyze_demand_logs.py --no-plots      # 그래프(PNG) 생략

결과: data/analysis/<날짜_시각>/ 에 report.md + CSV들 + PNG 그래프
  (평일/공휴일 구분: 파일명의 '공휴일' 표시 우선, 없으면 로그 안의 날짜로 판정)
"""
import argparse
import glob
import os
import re
import sys
from datetime import datetime

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

from log_selection import selected_log_paths, parse_log_name, LOG_DIR  # noqa: E402
from holiday_utils import is_holiday_date  # noqa: E402

LABEL_RE = re.compile(
    r"^(?P<region>.*?)(?P<strategy>rl_prepositioned|prepositioned|patrol)_"
    r"(?P<algo>custom_greedy|custom_routeExtension|greedy|routeExtension|hungarian)(?P<hol>공휴일)?$")
DAY_KO = {"weekday": "평일", "holiday": "공휴일"}
MIN_FILES_WARN = 3  # 그룹당 로그가 이보다 적으면 '표본 부족' 표시


# ───────────────────────── 로드 ─────────────────────────
def load_log(path: str):
    d = pd.read_csv(path, encoding="utf-8-sig")
    if d.empty or "pickup_datetime" not in d.columns:
        return None
    d["pickup_datetime"] = pd.to_datetime(d["pickup_datetime"], errors="coerce")
    d = d.dropna(subset=["pickup_datetime"])
    if d.empty:
        return None
    info = parse_log_name(path)
    m = LABEL_RE.match(info["label"])
    date = d["pickup_datetime"].dt.date.min()
    tagged = "공휴일" in info["label"]
    is_hol = True if tagged else bool(is_holiday_date(date))
    d["file"] = os.path.basename(path)
    d["region"] = m.group("region") if m else "?"
    d["strategy"] = m.group("strategy") if m else "?"
    d["algo"] = m.group("algo") if m else "?"
    d["combo"] = f"{d['strategy'].iat[0]}_{d['algo'].iat[0]}" if m else "?"
    d["date"] = date
    d["day_type"] = "holiday" if is_hol else "weekday"
    d["hour"] = d["pickup_datetime"].dt.hour
    for c in ("wait_sec", "request_sec"):
        if c in d.columns:
            d[c] = pd.to_numeric(d[c], errors="coerce")
    if "origin_zone" not in d.columns:
        d["origin_zone"] = "none"
    d["origin_zone"] = d["origin_zone"].fillna("none").astype(str).replace("", "none")
    return d


def load_all(paths):
    frames, skipped = [], []
    for p in paths:
        try:
            d = load_log(p)
        except Exception as e:  # 깨진 파일 하나 때문에 전체가 죽지 않게
            skipped.append((os.path.basename(p), str(e)))
            continue
        if d is None:
            skipped.append((os.path.basename(p), "빈 로그"))
        else:
            frames.append(d)
    return (pd.concat(frames, ignore_index=True) if frames else None), skipped


# ───────────────────────── 분석 ─────────────────────────
def per_file_table(df):
    g = df.groupby("file")
    t = g.agg(date=("date", "first"), day_type=("day_type", "first"), combo=("combo", "first"),
              calls=("person_id", "size"),
              picked=("status", lambda s: (s == "picked").sum()),
              timeout=("status", lambda s: (s == "timeout").sum()),
              wait_mean=("wait_sec", lambda s: s[df.loc[s.index, "status"] == "picked"].mean()))
    t["pick_rate"] = t["picked"] / t["calls"]
    return t.reset_index()


def summarize(df, files):
    rows = []
    for dt in ("weekday", "holiday"):
        f = files[files.day_type == dt]
        sub = df[df.day_type == dt]
        w = sub.loc[sub.status == "picked", "wait_sec"].dropna()
        rows.append({
            "구분": DAY_KO[dt], "로그 수": len(f), "날짜 수": f["date"].nunique(),
            "호출 수(로그당 평균)": f["calls"].mean() if len(f) else np.nan,
            "탑승률": f["picked"].sum() / f["calls"].sum() if len(f) else np.nan,
            "타임아웃률": f["timeout"].sum() / f["calls"].sum() if len(f) else np.nan,
            "대기 평균(초)": w.mean() if len(w) else np.nan,
            "대기 중앙(초)": w.median() if len(w) else np.nan,
            "대기 90%(초)": w.quantile(0.9) if len(w) else np.nan,
        })
    return pd.DataFrame(rows)


def hourly_curve(df):
    """로그마다 '그 로그가 다룬 시간 범위' 안의 시간별 호출 수 -> 평일/공휴일별 로그당 평균."""
    parts = []
    for fn, d in df.groupby("file"):
        hrs = range(int(d.hour.min()), int(d.hour.max()) + 1)
        c = d.groupby("hour").size().reindex(hrs, fill_value=0)
        parts.append(pd.DataFrame({"file": fn, "day_type": d.day_type.iat[0], "hour": c.index, "calls": c.values}))
    long = pd.concat(parts, ignore_index=True)
    out = long.groupby(["hour", "day_type"])["calls"].agg(["mean", "std", "count"]).unstack("day_type")
    res = pd.DataFrame(index=out.index)
    for dt in ("weekday", "holiday"):
        res[f"{dt}_평균"] = out["mean"].get(dt)
        res[f"{dt}_로그수"] = out["count"].get(dt)
    res["공휴일/평일"] = res["holiday_평균"] / res["weekday_평균"].replace(0, np.nan)
    res.index.name = "시각"
    return res


def category_demand(df):
    """origin_zone은 'restaurant|bus_stop'처럼 여러 구역이 겹칠 수 있어 구역별로 펼쳐서 센다(겹친 승객은 각 구역에 1건씩)."""
    e = df.assign(zone=df["origin_zone"].str.split("|")).explode("zone")
    c = e.groupby(["file", "day_type", "zone"]).size().rename("calls").reset_index()
    out = c.groupby(["zone", "day_type"])["calls"].mean().unstack("day_type")
    for dt in ("weekday", "holiday"):
        if dt not in out:
            out[dt] = np.nan
    out["공휴일/평일"] = out["holiday"] / out["weekday"].replace(0, np.nan)
    out = out[["weekday", "holiday", "공휴일/평일"]].rename(columns={"weekday": "평일(로그당)", "holiday": "공휴일(로그당)"})
    return out.sort_values("평일(로그당)", ascending=False)


def combo_table(df):
    d = df[df.combo != "?"]
    if d.empty:
        return None
    rows = []
    for (combo, dt), sub in d.groupby(["combo", "day_type"]):
        nf = sub.file.nunique()
        w = sub.loc[sub.status == "picked", "wait_sec"].dropna()
        rows.append({"전략_알고리즘": combo, "구분": DAY_KO[dt], "로그 수": nf,
                     "호출(로그당)": len(sub) / nf, "탑승률": (sub.status == "picked").mean(),
                     "타임아웃률": (sub.status == "timeout").mean(),
                     "대기 평균(초)": w.mean() if len(w) else np.nan,
                     "대기 90%(초)": w.quantile(0.9) if len(w) else np.nan})
    return pd.DataFrame(rows).sort_values(["구분", "대기 평균(초)"])


def configured_multipliers():
    """config.json의 holiday_multipliers. (config_loader를 import하면 지역 좌표 실시간 조회가 돌아서 직접 읽음)"""
    import json
    try:
        with open(os.path.join(ROOT, "config.json"), "r", encoding="utf-8") as f:
            return dict(json.load(f).get("holiday_multipliers") or {})
    except Exception:
        return {}


def checks(files, df):
    msgs = []
    nw, nh = (files.day_type == "weekday").sum(), (files.day_type == "holiday").sum()
    if nw == 0 or nh == 0:
        msgs.append(f"평일 로그 {nw}개, 공휴일 로그 {nh}개 — 한쪽이 비어 있어 평일/공휴일 비교는 못 합니다. "
                    f"'학습만 실행'(번갈아 생성)이나 A/B의 공휴일 체크로 부족한 쪽을 더 만드세요.")
    else:
        if min(nw, nh) < MIN_FILES_WARN:
            msgs.append(f"표본 부족: 평일 {nw}개 / 공휴일 {nh}개 (각 {MIN_FILES_WARN}개 이상 권장). 차이가 우연일 수 있습니다.")
        if max(nw, nh) > 2 * min(nw, nh):
            msgs.append(f"평일 {nw}개 / 공휴일 {nh}개로 2배 넘게 쏠려 있습니다. 로그당 평균으로 비교하니 큰 문제는 아니지만 신뢰도는 낮습니다.")
    dup = files.groupby(["date", "combo"]).size()
    dup = dup[dup > 1]
    if len(dup):
        msgs.append("같은 날짜·같은 조합 로그가 여러 개(구형식 '?'는 전략 정보 없는 예전 로그): "
                    + ", ".join(f"{d}/{'구형식' if c == '?' else c}({n}개)" for (d, c), n in dup.items())
                    + " → 합쳐서 학습하면 그날 수요가 부풀려집니다.")
    same_day = files.groupby("date")["combo"].nunique()
    same_day = same_day[same_day > 1]
    if len(same_day):
        msgs.append("같은 날짜에 서로 다른 조합 로그가 섞여 있습니다(날짜 기준 수요 합산에 쓰면 2배로 부풀어요): "
                    + ", ".join(str(d) for d in same_day.index))
    hc = df.groupby(["file", "hour"]).size().groupby("hour").mean()
    if len(hc) >= 3 and hc.iloc[0] > 3 * hc.iloc[1:].mean():
        msgs.append(f"{int(hc.index[0])}시에 호출이 몰려 있습니다(로그당 평균 {hc.iloc[0]:,.0f}건, 나머지 시각 평균 {hc.iloc[1:].mean():,.0f}건). "
                    f"시뮬 시작 시점에 미리 만들어 둔 승객이 한꺼번에 등장하기 때문일 수 있어요. 시간대 곡선 해석 시 첫 시각은 따로 보세요.")
    spans = df.groupby("file").hour.agg(["min", "max"])
    if spans["min"].nunique() > 1 or spans["max"].nunique() > 1:
        msgs.append("로그마다 시뮬 시간대가 다릅니다. 시간대 곡선은 각 로그가 다룬 시각만으로 평균냈습니다 "
                    f"(예: {int(spans['min'].min())}~{int(spans['max'].max())}시 중 일부만 있는 시각은 표본이 적음).")
    return msgs


# ───────────────────────── 출력 ─────────────────────────
def md_table(df, index=True, floatfmt="{:,.2f}"):
    d = df.reset_index() if index else df.copy()
    cols = [str(c) for c in d.columns]
    lines = ["| " + " | ".join(cols) + " |", "|" + "|".join(["---"] * len(cols)) + "|"]
    for _, r in d.iterrows():
        cells = []
        for v in r.tolist():
            if v is None or (isinstance(v, float) and np.isnan(v)):
                cells.append("-")
            elif isinstance(v, (float, np.floating)):
                cells.append(floatfmt.format(v))
            else:
                cells.append(str(v))
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def setup_font():
    import matplotlib
    matplotlib.use("Agg")
    from matplotlib import font_manager, pyplot as plt
    have = {f.name for f in font_manager.fontManager.ttflist}
    for name in ("Malgun Gothic", "AppleGothic", "NanumGothic", "Noto Sans CJK KR", "Noto Sans KR"):
        if name in have:
            plt.rcParams["font.family"] = name
            break
    plt.rcParams["axes.unicode_minus"] = False
    return plt


def make_plots(out_dir, hourly, cat, combo):
    plt = setup_font()
    made = []
    colors = {"weekday": "#2563eb", "holiday": "#dc2626"}
    fig, ax = plt.subplots(2, 1, figsize=(9, 7), sharex=True)
    for dt in ("weekday", "holiday"):
        if hourly[f"{dt}_평균"].notna().any():
            ax[0].plot(hourly.index, hourly[f"{dt}_평균"], marker="o", color=colors[dt], label=DAY_KO[dt])
    ax[0].set_ylabel("시간당 호출 수 (로그당 평균)")
    ax[0].legend()
    ax[0].set_title("시간대별 수요: 평일 vs 공휴일")
    ratio = hourly["공휴일/평일"]
    ax[1].bar(hourly.index, ratio.fillna(0), color="#7c3aed")
    ax[1].axhline(1, color="gray", ls="--")
    ax[1].set_ylabel("공휴일 / 평일")
    ax[1].set_xlabel("시각")
    fig.tight_layout()
    p = os.path.join(out_dir, "hourly_demand.png")
    fig.savefig(p, dpi=130)
    plt.close(fig)
    made.append(p)

    top = cat.head(10)
    if len(top):
        fig, ax = plt.subplots(figsize=(9, 5))
        x = np.arange(len(top))
        ax.bar(x - 0.2, top["평일(로그당)"].fillna(0), 0.4, label="평일", color=colors["weekday"])
        ax.bar(x + 0.2, top["공휴일(로그당)"].fillna(0), 0.4, label="공휴일", color=colors["holiday"])
        ax.set_xticks(x)
        ax.set_xticklabels(top.index, rotation=30, ha="right")
        ax.set_ylabel("호출 수 (로그당 평균)")
        ax.set_title("출발 구역별 수요")
        ax.legend()
        fig.tight_layout()
        p = os.path.join(out_dir, "category_demand.png")
        fig.savefig(p, dpi=130)
        plt.close(fig)
        made.append(p)

    if combo is not None and len(combo):
        piv = combo.pivot_table(index="전략_알고리즘", columns="구분", values="대기 평균(초)")
        fig, ax = plt.subplots(figsize=(9, 5))
        x = np.arange(len(piv))
        for i, kor in enumerate(("평일", "공휴일")):
            if kor in piv:
                ax.bar(x + (i - 0.5) * 0.4, piv[kor].fillna(0), 0.4, label=kor,
                       color=colors["weekday" if kor == "평일" else "holiday"])
        ax.set_xticks(x)
        ax.set_xticklabels(piv.index, rotation=20, ha="right")
        ax.set_ylabel("평균 대기시간(초)")
        ax.set_title("전략·알고리즘별 평균 대기시간")
        ax.legend()
        fig.tight_layout()
        p = os.path.join(out_dir, "wait_by_combo.png")
        fig.savefig(p, dpi=130)
        plt.close(fig)
        made.append(p)
    return made


def main():
    ap = argparse.ArgumentParser(description="수요 로그 분석기 (평일/공휴일, 전략·알고리즘 비교)")
    ap.add_argument("--logs", nargs="+", help="분석할 로그 CSV들 (생략하면 학습 로그 선택 또는 전체)")
    ap.add_argument("--all", action="store_true", help="학습 로그 선택을 무시하고 data/sim_logs 전체 사용")
    ap.add_argument("--out", help="결과 폴더 (기본: data/analysis/<날짜_시각>)")
    ap.add_argument("--no-plots", action="store_true", help="PNG 그래프 생략")
    a = ap.parse_args()

    if a.logs:
        paths = [p for pat in a.logs for p in (sorted(glob.glob(pat)) or [pat])]
    elif a.all:
        paths = sorted(glob.glob(os.path.join(LOG_DIR, "demand_log_*.csv")))
    else:
        paths = selected_log_paths()
    paths = [p for p in paths if os.path.exists(p)]
    if not paths:
        print("[에러] 분석할 로그가 없습니다. data/sim_logs에 demand_log_*.csv가 있는지 확인하세요.")
        return 1

    df, skipped = load_all(paths)
    if df is None:
        print("[에러] 읽을 수 있는 로그가 없습니다:", skipped)
        return 1
    files = per_file_table(df)
    print(f"[분석] 로그 {len(files)}개, 호출 {len(df):,}건 "
          f"(평일 {(files.day_type == 'weekday').sum()} · 공휴일 {(files.day_type == 'holiday').sum()})")

    summary = summarize(df, files)
    hourly = hourly_curve(df)
    cat = category_demand(df)
    combo = combo_table(df)
    mult = configured_multipliers()
    cat_cmp = cat.copy()
    cat_cmp["설정 배율"] = [mult.get(z, np.nan) for z in cat_cmp.index]
    notes = checks(files, df)

    out_dir = a.out or os.path.join(ROOT, "data", "analysis", datetime.now().strftime("%Y%m%d_%H%M%S"))
    os.makedirs(out_dir, exist_ok=True)
    files.to_csv(os.path.join(out_dir, "per_file.csv"), index=False, encoding="utf-8-sig")
    summary.to_csv(os.path.join(out_dir, "summary.csv"), index=False, encoding="utf-8-sig")
    hourly.to_csv(os.path.join(out_dir, "hourly.csv"), encoding="utf-8-sig")
    cat_cmp.to_csv(os.path.join(out_dir, "category.csv"), encoding="utf-8-sig")
    if combo is not None:
        combo.to_csv(os.path.join(out_dir, "combo.csv"), index=False, encoding="utf-8-sig")

    plots = []
    if not a.no_plots:
        try:
            plots = make_plots(out_dir, hourly, cat, combo)
        except Exception as e:
            print(f"[경고] 그래프 생성 실패(표/CSV는 저장됨): {e}")

    lines = [f"# 수요 로그 분석 ({datetime.now():%Y-%m-%d %H:%M})", "",
             f"- 로그 {len(files)}개 / 호출 {len(df):,}건 / 구역: {', '.join(sorted(set(df.region) - {'?'})) or '-'}"]
    if skipped:
        lines.append(f"- 읽지 못해 건너뜀: {', '.join(n for n, _ in skipped)}")
    if notes:
        lines += ["", "## ⚠ 데이터 점검"] + [f"- {m}" for m in notes]
    lines += ["", "## 1. 요약 (평일 vs 공휴일)", md_table(summary, index=False, floatfmt="{:,.2f}"),
              "", "## 2. 시간대별 수요", md_table(hourly[["weekday_평균", "holiday_평균", "공휴일/평일"]]),
              "", "## 3. 출발 구역별 수요 (설정한 공휴일 배율과 비교)", md_table(cat_cmp),
              "", "> 설정 배율은 *동적 스폰 확률*에 곱한 값이라, 구역별 실제 호출 수 비율과 1:1로 같지는 않습니다(정적 승객·구역 중복 등). 방향(증가/감소)이 맞는지 보는 용도입니다."]
    if combo is not None:
        lines += ["", "## 4. 전략·알고리즘별 성능", md_table(combo, index=False)]
    else:
        lines += ["", "## 4. 전략·알고리즘별 성능", "파일명에서 전략/알고리즘을 읽을 수 있는 로그(새 형식)가 없어 생략했습니다."]
    if plots:
        lines += ["", "## 그래프"] + [f"![{os.path.basename(p)}]({os.path.basename(p)})" for p in plots]
    with open(os.path.join(out_dir, "report.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    print("\n" + md_table(summary, index=False))
    for m in notes:
        print("[점검]", m)
    print(f"\n[저장] {out_dir}")
    print("       report.md / summary.csv / hourly.csv / category.csv / combo.csv / per_file.csv"
          + (" / *.png" if plots else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
