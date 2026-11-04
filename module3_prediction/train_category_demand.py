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
import glob
import os
import sys

import joblib

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from config_loader import CFG
from module2_preprocessing.log_selection import selected_log_paths  # config_gui '학습 로그 선택' 반영
from module2_preprocessing.time_series_prep import time_based_split
from module2_preprocessing.pipeline import build_feature_table
from module3_prediction.xgb_factory import build_xgboost_model

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


def main(log_glob: str = None, out_path: str = OUT_PATH):
    # 학습 로그: --logs 글롭, 없으면 config_gui '학습 로그 선택'(data/sim_logs)
    paths = sorted(glob.glob(log_glob)) if log_glob else selected_log_paths(DEMAND_LOG_DIR)
    if not paths:
        print("[에러] data/sim_logs/demand_log_*.csv 로그가 없습니다. "
              "먼저 `python measure_wait_time.py`를 한 번 이상 돌려주세요.")
        return

    # H3 모델(train.py·demand_v2)과 같은 공용 파이프라인을 카테고리 단위로 실행
    # (로그 검증, 시뮬 시작 시각, 칸 집계 후 날씨 병합 → 날씨 피처가 학습에 들어감)
    feature_df = build_feature_table(paths, max_lag=CFG["max_lag"], spatial_col="category",
                                     all_cells=CATEGORY_LIST)

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

    # 시간 기준 분할을 전체 테이블에 한 번만 적용 → 모든 카테고리가 같은 시험 구간을 쓴다 (train.py·demand_v2와 같은 방식)
    train_all, _val, test_all, cuts = time_based_split(feature_df, CFG["test_size"])
    print(f"[분할] 시험 구간 {cuts['test_cut']} 이후")

    models = {}
    for category in CATEGORY_LIST:
        train_df = train_all[train_all["category"] == category]
        test_df = test_all[test_all["category"] == category]
        if len(train_df) + len(test_df) < 20:
            print(f"[경고] '{category}' 샘플이 {len(train_df) + len(test_df)}건뿐이라 건너뜁니다 (최소 20건 권장).")
            continue

        # [병합 수정] y를 현재 칸 demand -> t+1 예측(y_h1)로 교체: train.py / RL의 예측 지평과 일치.
        X_train, y_train = train_df[feature_cols].fillna(0), train_df[TARGET].fillna(0)
        X_test, y_test = test_df[feature_cols].fillna(0), test_df[TARGET].fillna(0)
        model = build_xgboost_model()
        model.fit(X_train, y_train)
        rmse = ((model.predict(X_test) - y_test) ** 2).mean() ** 0.5
        print(f"[{category}] Train {len(X_train)} / Test {len(X_test)} | RMSE {rmse:.3f}")
        models[category] = model

    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    joblib.dump({"models": models, "feature_cols": feature_cols, "target": TARGET,
                 "category_list": list(models.keys())}, out_path)
    print(f"[완료] {out_path} 에 카테고리별 모델 {len(models)}개 저장")


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--logs", default=None, help="학습 로그 글롭 (생략 시 config_gui에서 고른 data/sim_logs 로그)")
    ap.add_argument("--out", default=OUT_PATH, help="저장 경로 (기본 saved_models/category_demand_models.pkl)")
    a = ap.parse_args()
    main(a.logs, a.out)
