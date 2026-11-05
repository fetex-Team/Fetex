"""
module3_prediction/train_category_demand.py

H3 기준 train.py와 나란히 두는 "카테고리 기준" 버전.
목적: RL(module4_dispatch/rl_env.py)의 _predicted_demand() 자리에 바로 꽂을 수 있는
      카테고리별(school/residential/company/restaurant/subway_entrance/bus_stop) 수요예측 모델.

train.py와 다른 점은 spatial_col='category' 하나뿐 — TimeSeriesPreprocessor.aggregate_demands /
create_features가 원래 spatial_col을 인자로 받게 설계돼 있어서 h3_index용 로직을 그대로 재사용함.
(H3용 saved_models/xgboost_demand.pkl, cnn_lstm_demand.pt는 이 스크립트가 손대지 않음 — 완전 별도 파일)

실행: python module3_prediction/train_category_demand.py
출력: saved_models/category_demand_models.pkl
      {"models": {category: XGBRegressor}, "feature_cols": [...], "category_list": [...]}
"""
import os
import sys
import argparse

import joblib
import numpy as np
import pandas as pd
from xgboost import XGBRegressor

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from config_loader import CFG
from log_selection import selected_log_paths  # config_gui '학습 로그 선택' 반영
from module2_preprocessing.category_indexing import CategoryIndexer
from module2_preprocessing.time_series_prep import TimeSeriesPreprocessor, time_based_split

# [2026-09-21 중복 집계 수정] train.py와 같은 이유로 레거시 소스는 더 이상 합치지 않음.
#   data/sim_logs/demand_log_*.csv        : measure_wait_time.py / 병렬 워커가 남기는 새 로그 (사용)
#   data/raw/simulation_demand_log*.csv   : 예전 로그. measure_wait_time.py가 매 실행마다
#     위 새 로그와 "같은 승객"을 여기에도 동시에 남겨서, 둘을 합치면 실행분마다 두 번
#     집계됐음(날짜 부여 방식이 서로 달라 drop_duplicates로도 안 걸러짐). data/sim_logs가
#     이제 항상 채워지므로 이 소스는 뺀다.
DEMAND_LOG_DIR = os.path.join(PROJECT_ROOT, "data", "sim_logs")
DEMAND_LOG_GLOB = os.path.join(DEMAND_LOG_DIR, "demand_log_*.csv")

# 학습 타겟: train.py와 동일하게 y_h1(t+1). Module 2 create_features가 y_h1..y_hH를 만들어 줌.
TARGET = "y_h1"
OUT_PATH = os.path.join(PROJECT_ROOT, "saved_models", "category_demand_models.pkl")

CATEGORY_LIST = ["school", "residential", "company", "restaurant", "subway_entrance", "bus_stop"]


def _load_all_demand_logs():
    """train.py의 _load_all_demand_logs()와 동일한 로직 (여기서도 그대로 필요해서 복제)"""
    paths = selected_log_paths(DEMAND_LOG_DIR)
    if not paths:
        return None
    frames = []
    for path in paths:
        frame = pd.read_csv(path, parse_dates=["pickup_datetime"])
        # 서로 다른 실행 파일 사이의 날짜 공백은 '수요 0'이 아니다. 아래에서 파일별
        # 독립 시계열을 만들 수 있도록 출처를 보존한다.
        frame["source_log"] = os.path.basename(path)
        frames.append(frame)
    merged = pd.concat(frames, ignore_index=True)
    # 같은 시각·같은 좌표의 서로 다른 요청은 유효한 별도 수요다. 로그가 중복으로
    # 선택된 경우만 제거할 수 있도록 승객 식별자까지 포함한다.
    dedup_cols = [c for c in ("source_log", "person_id", "pickup_datetime", "edge_id") if c in merged.columns]
    merged = merged.drop_duplicates(subset=dedup_cols or None)
    print(f"[안내] 로그 {len(paths)}개에서 총 {len(merged)}건 로드")
    return merged


def _assign_logged_categories(df: pd.DataFrame) -> pd.DataFrame:
    """수요 예측용 category는 승객의 목적지 카테고리(dest_category)로 통일한다.

    origin_zone은 출발 edge의 POI 중첩 정보라서 한 승객이 여러 카테고리에 걸리거나
    출발지와 목적지 의미가 섞일 수 있으므로 학습 분류에 사용하지 않는다.
    구형 로그에는 dest_category가 없으므로 해당 행은 안전하게 제외한다.
    """
    df = df.copy()
    raw = df.get("dest_category", pd.Series("", index=df.index)).fillna("").astype(str).str.strip()
    valid = raw.isin(CATEGORY_LIST)
    df["category"] = raw.where(valid)

    # 진단용: 기존 origin_zone은 참고만 하고 학습 분류에는 절대 사용하지 않는다.
    origin = df.get("origin_zone", pd.Series("", index=df.index)).fillna("").astype(str)
    print(f"[안내] dest_category 유효 {int(valid.sum()):,}건, "
          f"미기록/지원외 {int((~valid).sum()):,}건, 최종 사용 {int(valid.sum()):,}건")
    if int(valid.sum()) == 0:
        print("[경고] 현재 선택된 로그에는 dest_category가 없습니다. "
              "dest_category가 기록된 새 시뮬레이션 로그를 생성한 뒤 다시 학습하세요.")
    print("[안내] origin_zone은 사용하지 않습니다. 목적지 카테고리 기준으로 학습 데이터의 의미를 통일합니다.")
    return df.loc[valid].copy()

def _build_episode_features(df: pd.DataFrame, prep: TimeSeriesPreprocessor) -> pd.DataFrame:
    """각 로그 파일을 독립 에피소드로 전처리한다.

    이전 구현처럼 여러 실행일을 합쳐 최소~최대 날짜 전체를 reindex하면, 실행하지
    않은 날이 거대한 0수요 구간으로 바뀐다. 파일 안에서는 실제 0수요 시간칸을
    보존하되, 파일과 파일 사이에는 lag/rolling이 이어지지 않게 한다.
    """
    parts = []
    for source_log, episode in df.groupby("source_log", sort=False):
        agg = prep.aggregate_demands(
            episode, timestamp_col="pickup_datetime", spatial_col="category",
            all_cells=CATEGORY_LIST,
        )
        features = prep.create_features(agg, spatial_col="category", verbose=False)
        if features.empty:
            print(f"[경고] {source_log}: lag/rolling 뒤 남은 행이 없어 건너뜁니다.")
            continue
        features["source_log"] = source_log
        parts.append(features)

    if not parts:
        return pd.DataFrame()
    out = pd.concat(parts, ignore_index=True)
    out.attrs["feature_cols"] = list(parts[0].attrs["feature_cols"])
    out.attrs["target_cols"] = list(parts[0].attrs["target_cols"])
    print(f"[안내] 로그 {len(parts)}개를 독립 에피소드로 전처리: "
          f"{len(out):,}행, 실제 시간칸 {out['time_bucket'].nunique():,}개")
    return out


def _evaluate(name: str, y_true, y_pred) -> dict:
    y_true = np.asarray(y_true, dtype=float)
    y_pred = np.maximum(0.0, np.asarray(y_pred, dtype=float))
    err = y_true - y_pred
    return {
        "rmse": float(np.sqrt(np.mean(err ** 2))),
        "mae": float(np.mean(np.abs(err))),
        "wape": float(np.abs(err).sum() / max(float(y_true.sum()), 1e-9)),
    }


def _print_metrics(category: str, name: str, metrics: dict):
    print(f"[{category} / {name}] RMSE {metrics['rmse']:.4f} | "
          f"MAE {metrics['mae']:.4f} | WAPE {metrics['wape']:.2%}")


def main(out_path: str = None):
    out_path = out_path or OUT_PATH
    df = _load_all_demand_logs()
    if df is None:
        print("[에러] data/sim_logs/demand_log_*.csv 로그가 없습니다. "
              "먼저 `python measure_wait_time.py`를 한 번 이상 돌려주세요.")
        return

    df = _assign_logged_categories(df)

    prep = TimeSeriesPreprocessor(max_lag=CFG["max_lag"])
    feature_df = _build_episode_features(df, prep)

    if feature_df.empty:
        print("[에러] lag/rolling 생성 후 데이터가 하나도 안 남았습니다. 로그를 더 쌓아주세요.")
        return

    # [병합 수정] 예전 create_features는 타겟 컬럼을 만들지 않아서 "demand 빼고 전부 피처"가
    # 통했지만, 지금 버전은 y_h1..y_hH(미래 수요)를 함께 만든다. 그대로 두면 미래값이 입력
    # 피처로 들어가 타겟 누수가 생기므로 train.py와 동일하게 attrs를 그대로 사용한다.
    feature_cols = list(feature_df.attrs["feature_cols"])
    target_cols = list(feature_df.attrs["target_cols"])
    if TARGET not in target_cols:
        print(f"[에러] TARGET={TARGET!r}이 피처 테이블의 target_cols({target_cols})에 없습니다.")
        return

    # 모든 카테고리에 동일한 실제 시점 경계를 적용한다. 특정 카테고리의 행 개수만
    # 기준으로 자르면 서로 다른 날짜를 평가하게 되어 비교가 불가능해진다.
    train_df, _, test_df, cuts = time_based_split(feature_df, CFG["test_size"])
    print(f"[시간 분할] Train < {cuts['test_cut']}, Test >= {cuts['test_cut']} "
          f"(train {len(train_df):,} / test {len(test_df):,})")

    models, metrics_by_category = {}, {}
    for category in CATEGORY_LIST:
        cat_train = train_df[train_df["category"] == category]
        cat_test = test_df[test_df["category"] == category]
        if len(cat_train) < 20 or cat_test.empty:
            print(f"[경고] '{category}' train={len(cat_train)}, test={len(cat_test)}라 건너뜁니다.")
            continue

        X_train, y_train = cat_train[feature_cols].fillna(0), cat_train[TARGET].fillna(0)
        X_test, y_test = cat_test[feature_cols].fillna(0), cat_test[TARGET].fillna(0)
        model = XGBRegressor(
            n_estimators=int(CFG["xgb_n_estimators"]),
            max_depth=int(CFG["xgb_max_depth"]),
            learning_rate=float(CFG["xgb_learning_rate"]),
            random_state=int(CFG["xgb_random_state"]),
        )
        model.fit(X_train, y_train)
        category_metrics = {
            "lag_1": _evaluate("lag_1", y_test, X_test["lag_1"]),
            "slot_mean": _evaluate(
                "slot_mean", y_test,
                X_test["minute_of_day"].map(cat_train.groupby("minute_of_day")[TARGET].mean()).fillna(y_train.mean()),
            ),
            "xgb": _evaluate("xgb", y_test, model.predict(X_test)),
        }
        for name, metric in category_metrics.items():
            _print_metrics(category, name, metric)
        if category_metrics["xgb"]["mae"] >= min(category_metrics["lag_1"]["mae"], category_metrics["slot_mean"]["mae"]):
            print(f"[경고] '{category}' XGBoost가 MAE 기준 베이스라인을 이기지 못했습니다. PPO 재학습 전 검토하세요.")
        models[category] = model
        metrics_by_category[category] = category_metrics

    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    joblib.dump({"models": models, "feature_cols": feature_cols, "target": TARGET,
                 "category_list": list(models.keys()), "metrics": metrics_by_category,
                 "test_cut": str(cuts["test_cut"])}, out_path)
    print(f"[완료] {out_path} 에 카테고리별 모델 {len(models)}개 저장")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="카테고리별 수요예측 모델 학습")
    parser.add_argument(
        "--out", default=OUT_PATH,
        help="저장할 모델 파일 경로 (기본: saved_models/category_demand_models.pkl)",
    )
    args = parser.parse_args()
    main(args.out)
