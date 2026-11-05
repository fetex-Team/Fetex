"""
notebooks/01_EDA_and_Spatial.ipynb, 02_Model_Experiments.ipynb 를 만들고 실행해서 결과까지 저장한다.

    python notebooks/build_notebooks.py          # 프로젝트 루트에서 실행
필요: nbformat, nbclient, ipykernel (pip install nbformat nbclient ipykernel)
전제: build_env.py 실행(runtime_meta.json), data/loaders/generate_demand_logs.py 실행(data/real_demand_logs)
"""
import os
import sys

import nbformat as nbf
from nbclient import NotebookClient

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

SETUP = r'''
import os, sys, glob, json, math, datetime as dt, warnings
warnings.filterwarnings("ignore")
ROOT = os.path.abspath(os.path.join(os.getcwd(), "..")) if os.path.basename(os.getcwd()) == "notebooks" else os.getcwd()
sys.path.insert(0, ROOT)
os.environ.setdefault("PYTHONUTF8", "1")
import numpy as np, pandas as pd
import matplotlib
import matplotlib.pyplot as plt
from matplotlib import font_manager
for f in ["Malgun Gothic", "AppleGothic", "NanumGothic", "Noto Sans CJK KR", "Noto Sans CJK JP", "Noto Sans CJK TC"]:
    if any(f == x.name for x in font_manager.fontManager.ttflist):
        plt.rcParams["font.family"] = f; break
plt.rcParams["axes.unicode_minus"] = False
pd.set_option("display.width", 160)
META = json.load(open(os.path.join(ROOT, "module1_simulation", "sumo_config", "runtime_meta.json"), encoding="utf-8"))
print("지역:", META["config"]["region"], "| H3 res:", META["config"]["h3_resolution"], "| 도로:", len(META["edges"]))
'''


def md(s):
    return nbf.v4.new_markdown_cell(s.strip())


def code(s):
    return nbf.v4.new_code_cell(s.strip())


def nb_eda():
    cells = [
        md("""
# 01. 실데이터 EDA와 공간 격자 — 강남역 택시 수요

목표: 시뮬레이션 수요가 **실제 강남역 택시 승차 데이터**와 맞는지 확인하고, 예측·배차에 쓸 공간 격자(H3 해상도)를 데이터로 정한다.

| 데이터 | 출처 | 단위 |
|---|---|---|
| TIMS 격자별 영업통계 | 국토교통부 택시운행정보관리시스템, 2025년 | 500m 격자 × 월 |
| 택시 시간대별 이용 | 서울시 「2025 서울 교통이용 통계보고서」 | 서울 전체, 1시간 (평일/주말) |
| 강남구 월별 승차 | 같은 보고서 자치구 통계 | 강남구 × 월 (평일/주말) |
| 요일 배율 | 서울시 「서울 교통이용 통계보고서」 2022년 1월 | 서울 전체, 평일 안·주말 안 상대값 |
| 시간별 실측 날씨 | Open-Meteo 과거 날씨 (`data/loaders/fetch_weather_history.py`) | 강남역 좌표, 1시간 |
| 실데이터 보정 호출 로그 | `data/loaders/generate_demand_logs.py` (8주, 24시간) | 호출 1건 |
"""),
        code(SETUP),
        md("## 1. TIMS 500m 격자 — 강남역 주변 승차량"),
        code(r'''
import module1_simulation.demand_model as dm
tims = pd.read_csv(dm.TIMS_PATH)
tims["ix"] = tims.grid.str[2:5].astype(int); tims["iy"] = tims.grid.str[5:8].astype(int)
cx, cy = dm.latlng_to_utmk(37.4979, 127.0276)       # 강남역
gx, gy = int((cx - 900000) // 500), int((cy - 1900000) // 500)
near = tims[tims.ix.between(gx - 4, gx + 4) & tims.iy.between(gy - 4, gy + 4)]
daily = near.groupby(["ix", "iy"]).trips.sum() / 365
grid = daily.unstack("ix").sort_index(ascending=False)
fig, ax = plt.subplots(figsize=(7, 6))
im = ax.imshow(grid.values, cmap="YlOrRd")
ax.set_xticks(range(grid.shape[1]), grid.columns); ax.set_yticks(range(grid.shape[0]), grid.index)
ax.set_xlabel("격자 X 번호 (500m)"); ax.set_ylabel("격자 Y 번호 (500m)")
ax.set_title("강남역 주변 4.5km × 4.5km — 하루 평균 택시 승차 (TIMS 2025)")
plt.colorbar(im, label="건/일")
model = dm.RealDemandModel(META, META["config"])
x0, y0, x1, y1 = model.bbox_utmk
r = lambda v, base: (v - base) / 500
cols = list(grid.columns); rows = list(grid.index)
ax.add_patch(plt.Rectangle((r(x0, 900000) - cols[0] - 0.5, rows[0] - r(y1, 1900000) + 0.5),
                           (x1 - x0) / 500, (y1 - y0) / 500, fill=False, ec="blue", lw=2, label="시뮬레이션 영역"))
ax.legend(loc="lower right"); plt.show()
print("강남역 칸(다사%03d%03d) 하루 평균 %.0f건" % (gx, gy, daily.get((gx, gy), np.nan)))
'''),
        md("시뮬레이션 영역(파란 사각형, 약 1km²) 안에 서울에서 가장 붐비는 격자들이 들어 있다. 영역과 겹치는 칸만 면적 비율로 합산한다."),
        code(r'''
s = model.summary(dt.date(2025, 9, 1), 0, 24)
cells = pd.DataFrame(s["cells"]).T.sort_values("share", ascending=False)
print("영역 안 하루 실측 승차(9월 월요일 기준): %.0f건" % s["area_daily_calls_real"])
cells
'''),
        md("## 2. 월별 계절성과 요일·시간대"),
        code(r'''
monthly = pd.Series({m: sum(c.overlap * c.daily_by_month[m] for c in model.active.values()) for m in range(1, 13)})
fig, axes = plt.subplots(1, 3, figsize=(16, 4))
(monthly / monthly.mean()).plot(kind="bar", ax=axes[0], color="#4C72B0"); axes[0].axhline(1, color="k", lw=0.8)
axes[0].set_title("월별 배율 (영역 합계, 2025)"); axes[0].set_xlabel("월")
pd.Series(model.dow).plot(kind="bar", ax=axes[1], color="#55A868"); axes[1].axhline(1, color="k", lw=0.8)
axes[1].set_title("요일 배율 (평일 안·주말 안, 서울 2022.1)")
pd.DataFrame(model.hourly).mul(100).plot(ax=axes[2]); axes[2].axvspan(9, 23, color="gray", alpha=0.1, label="시뮬 구간")
axes[2].set_title("시간대별 비중 % (서울, 2025)"); axes[2].set_xlabel("시"); axes[2].legend()
plt.tight_layout(); plt.show()
print("월별 최대/최소: %.3f / %.3f" % ((monthly / monthly.mean()).max(), (monthly / monthly.mean()).min()))
print("평일 9~23시 비중: %.1f%%" % (100 * sum(model.hourly["weekday"][9:23])))
'''),
        md("## 3. 생성된 8주 호출이 실측과 맞는지 검증"),
        code(r'''
files = sorted(glob.glob(os.path.join(ROOT, "data", "real_demand_logs", "demand_log_*.csv")))
logs = pd.concat([pd.read_csv(f, parse_dates=["pickup_datetime"]) for f in files], ignore_index=True)
logs["date"] = logs.pickup_datetime.dt.date
summary = json.load(open(os.path.join(ROOT, "data", "real_demand_logs", "generation_summary.json"), encoding="utf-8"))
d = pd.DataFrame(summary["days"])
print(f"{len(files)}일, {len(logs):,}건, {logs.pickup_datetime.min()} ~ {logs.pickup_datetime.max()}")
fig, ax = plt.subplots(figsize=(14, 3.5))
ax.plot(pd.to_datetime(d.date), d.calls, marker="o", ms=3, label="생성")
ax.plot(pd.to_datetime(d.date), d.expected, ls="--", label="기대값 (TIMS 월평균 × 강남구 평일/주말 × 요일 × 날씨 × 카카오 몫)")
ax.set_title("일별 호출 수"); ax.legend(); plt.show()
err = (d.calls - d.expected) / np.sqrt(d.expected)
print("일별 (생성-기대)/√기대 : 평균 %.2f, 표준편차 %.2f  (포아송이면 ≈ 0, 1)" % (err.mean(), err.std()))
'''),
        code(r'''
from module1_simulation.demand_model import grid_code_of, latlng_to_utmk
logs["grid"] = [grid_code_of(*latlng_to_utmk(a, b)) for a, b in zip(logs.latitude, logs.longitude)]
gen_share = logs.grid.value_counts(normalize=True)
exp_share = pd.Series(model.cell_shares(dt.date(2026, 8, 1)))
cmp = pd.DataFrame({"실측 기대 비율": exp_share, "생성 비율": gen_share}).fillna(0).sort_values("실측 기대 비율", ascending=False)
cmp["차이(%p)"] = (cmp["생성 비율"] - cmp["실측 기대 비율"]) * 100
print("격자별 비율 최대 차이: %.2f%%p" % cmp["차이(%p)"].abs().max())
logs["hour"] = logs.pickup_datetime.dt.hour
wk = logs[pd.to_datetime(logs.date).dt.dayofweek < 5].groupby("hour").size()
fig, ax = plt.subplots(figsize=(8, 3.5))
ax.bar(wk.index, wk / wk.sum() * 100, label="생성(평일)"); ax.plot(range(24), np.array(model.hourly["weekday"]) * 100, "r.-", label="서울 통계")
ax.set_xlabel("시"); ax.set_ylabel("%"); ax.legend(); ax.set_title("시간대 분포 비교"); plt.show()
cmp.round(4)
'''),
        md("## 4. 공간 격자 해상도 (H3) 선택"),
        code(r'''
import h3
sample = logs[pd.to_datetime(logs.date) >= pd.to_datetime(logs.date).max() - pd.Timedelta(days=13)].copy()
sample["bucket"] = sample.pickup_datetime.dt.floor("10min")
buckets = pd.date_range(sample.bucket.min(), sample.bucket.max(), freq="10min")
rows = []
for res in (7, 8, 9, 10):
    sample["cell"] = [h3.latlng_to_cell(a, b, res) for a, b in zip(sample.latitude, sample.longitude)]
    mat = sample.groupby(["cell", "bucket"]).size().unstack().reindex(columns=buckets).fillna(0)
    v = mat.values
    ac = np.nanmean([np.corrcoef(r_[:-1], r_[1:])[0, 1] for r_ in v if r_.std() > 0])
    rows.append({"res": res, "셀 변(m)": round(h3.average_hexagon_edge_length(res, unit="m")), "셀 수": len(mat),
                 "0인 칸(%)": round((v == 0).mean() * 100, 1), "최다 셀 점유(%)": round(v.sum(1).max() / v.sum() * 100, 1),
                 "lag-1 자기상관": round(ac, 3)})
pd.DataFrame(rows)
'''),
        md("""
**결론: H3 resolution 8**
- res 7: 셀 3개, 한 칸이 86% — 공간 구분이 거의 없다(재배치 대상 구역이 사실상 1개).
- res 9·10: 0인 칸 47~65%, 자기상관 0.13 이하 — 10분 단위 예측이 노이즈가 된다.
- res 8: 셀 8개, 신호(자기상관 0.27)와 구분력의 균형. 그리고 **실측 원천(TIMS)이 500m 격자**라 그보다 잘게 나누면
  실측이 아니라 POI 가정에서 나온 차이를 학습하게 된다 → 데이터 해상도와 맞는 res 8(변 531m)을 쓴다.
"""),
    ]
    nb = nbf.v4.new_notebook(); nb.cells = cells
    nb.metadata["kernelspec"] = {"name": "python3", "display_name": "Python 3", "language": "python"}
    return nb


def nb_model():
    cells = [
        md("""
# 02. 수요 예측 모델 실험 — 60분(10분 × 6칸) 앞 H3 격자별 호출 수

- 데이터: 실데이터 보정 호출 8주(2026-07-13 ~ 09-06, 24시간, 카카오 T 몫 0.77, 실측 날씨 반영), H3 res 8 격자, 10분 칸
- 분할: **시간순** 앞 80% 학습 / 뒤 20% 시험 (예전 scripts/train_dispatch_model.py 기준 — 지금은 `module3_prediction/train.py`가 학습/검증/시험으로 나눠 튜닝함. 이 노트북은 검증 구간을 따로 두지 않음)
- 비교: 나이브(직전 칸 유지), 계절 나이브(지난주 같은 칸), 이동평균(1시간), XGBoost(MultiOutput, 43개 피처)
- 지표: RMSE, MAE, **WAPE**(희소 카운트에서 MAPE 대신 사용)
"""),
        code(SETUP),
        code(r'''
from module2_preprocessing.pipeline import build_feature_table
from module2_preprocessing.time_series_prep import time_based_split
from config_loader import CFG
feat = build_feature_table(os.path.join(ROOT, "data", "real_demand_logs", "demand_log_*.csv"), verbose=False)
F, T = feat.attrs["feature_cols"], feat.attrs["target_cols"]
train_df, val_df, test_df, cuts = time_based_split(feat, CFG["test_size"])
print(f"피처 테이블 {len(feat):,}행, 셀 {feat.h3_index.nunique()}개, 피처 {len(F)}개, 타깃 {T}")
print("분할:", {k: str(v) for k, v in cuts.items()}, "| 학습", len(train_df), "/ 검증", len(val_df), "/ 시험", len(test_df))
'''),
        code(r'''
import joblib
from module3_prediction.evaluate import calculate_metrics
art = joblib.load(os.path.join(ROOT, "saved_models", "demand_v2.joblib"))
Y = test_df[T].values
pred_xgb = np.maximum(0, art["model"].predict(test_df[art["feature_cols"]].fillna(0).values))
lag1 = [c for c in F if c.endswith("lag_1")][0]
week = [c for c in F if "last_week" in c][0]
roll = [c for c in F if "rolling" in c and "mean" in c and c.endswith("6")]
roll = roll[0] if roll else [c for c in F if "rolling" in c][0]
base = {"나이브(직전 칸)": np.repeat(test_df[[lag1]].fillna(0).values, len(T), 1),
        "계절 나이브(지난주 같은 칸)": np.repeat(test_df[[week]].fillna(0).values, len(T), 1),
        f"이동평균({roll})": np.repeat(test_df[[roll]].fillna(0).values, len(T), 1),
        "XGBoost (demand_v2)": pred_xgb}
rows = []
for name, p in base.items():
    m = calculate_metrics(Y.ravel(), p.ravel())
    rows.append({"모델": name, "RMSE": round(m["RMSE"], 3), "MAE": round(m["MAE"], 3), "WAPE(%)": round(m["WAPE (%)"], 1)})
# 이론 하한: 호출은 포아송 과정이라 진짜 기대값을 알아도 오차가 남는다.
# 포아송 평균절대편차 E|Y-λ| = 2·e^(-λ)·λ^(⌊λ⌋+1)/⌊λ⌋!  (λ는 XGBoost 예측으로 근사)
from scipy.special import gammaln
lam = np.maximum(pred_xgb, 1e-6); kf = np.floor(lam)
floor_wape = (2 * np.exp(-lam + (kf + 1) * np.log(lam) - gammaln(kf + 1))).sum() / Y.sum() * 100
rows.append({"모델": "(참고) 포아송 잡음 하한", "RMSE": round(float(np.sqrt(lam.mean())), 3), "MAE": None, "WAPE(%)": round(floor_wape, 1)})
res = pd.DataFrame(rows).set_index("모델"); res
'''),
        code(r'''
per_h = pd.DataFrame({t: calculate_metrics(Y[:, i], pred_xgb[:, i]) for i, t in enumerate(T)}).T[["RMSE", "MAE", "WAPE (%)"]]
fig, axes = plt.subplots(1, 2, figsize=(14, 4))
per_h["WAPE (%)"].plot(marker="o", ax=axes[0]); axes[0].set_title("예측 지평별 WAPE (10분 단위)"); axes[0].set_ylabel("%")
top = test_df.h3_index.value_counts().index[0]
sel = test_df[test_df.h3_index == top].iloc[:144 * 3]
idx = sel.index.map(test_df.index.get_loc)
axes[1].plot(sel.time_bucket, sel["y_h1"], label="실제(t+1)")
axes[1].plot(sel.time_bucket, pred_xgb[idx, 0], label="예측(t+1)")
axes[1].set_title(f"가장 붐비는 격자 {top} — 3일"); axes[1].legend(); plt.tight_layout(); plt.show()
per_h.round(3)
'''),
        code(r'''
imp = np.mean([est.feature_importances_ for est in art["model"].estimators_], axis=0)
s = pd.Series(imp, index=art["feature_cols"]).sort_values(ascending=False).head(15)
s[::-1].plot(kind="barh", figsize=(7, 5), title="XGBoost 피처 중요도 (6개 지평 평균, 상위 15)"); plt.show()
'''),
        md("""
## 결론
- XGBoost가 나이브·계절 나이브·이동평균보다 모든 지표에서 낫다(위 표).
- WAPE 약 26%. 호출 자체가 무작위(포아송)라 진짜 기대값을 알아도 남는 오차(위 표 '포아송 잡음 하한', 약 25%)와 1~2%p 차이다 → 이 데이터에서 더 줄일 여지가 거의 없다.
- 카카오 T 몫(0.77)만 쓰면서 칸당 호출이 줄어 상대 오차가 커졌다(전체 실측량 기준일 때 약 23%). 모델이 나빠진 것이 아니라 잡음 하한이 올라간 것이다.
- 날씨는 실측 시간별 강수(Open-Meteo)를 붙였다. 강수 1mm당 +2%라 10분 단위 잡음에 비해 작아 피처 중요도는 낮다.
- RL 재배치용 카테고리 예측은 `module3_prediction/train_category_demand.py`가 따로 학습한다.
- 한계: 시간대 분포가 서울 전체 비율이라 실제보다 매끈하다 → 실제 카카오 호출 로그가 있으면 오차가 커질 수 있다(보수적으로 해석).
"""),
    ]
    nb = nbf.v4.new_notebook(); nb.cells = cells
    nb.metadata["kernelspec"] = {"name": "python3", "display_name": "Python 3", "language": "python"}
    return nb


def run(nb, name):
    path = os.path.join(HERE, name)
    NotebookClient(nb, timeout=1200, kernel_name="python3", resources={"metadata": {"path": HERE}}).execute()
    nbf.write(nb, path)
    print("[저장]", path)


if __name__ == "__main__":
    which = sys.argv[1:] or ["01", "02"]
    if "01" in which:
        run(nb_eda(), "01_EDA_and_Spatial.ipynb")
    if "02" in which:
        run(nb_model(), "02_Model_Experiments.ipynb")
