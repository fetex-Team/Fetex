"""XGBoost 회귀 모델 생성 (PyTorch 없이 import 가능 — models.py는 torch를 항상 불러오므로 분리).

학습 스크립트(train.py, train_category_demand.py, module2_preprocessing/eda/feature_ablation.py)가
모두 이 함수로 모델을 만들어 같은 설정(config의 xgb_* 값, 시드)을 쓴다.
"""
import os
import sys

from config_loader import CFG  # noqa: E402


def build_xgboost_model(n_estimators: int = None, max_depth: int = None, learning_rate: float = None, **kwargs):
    import xgboost as xgb
    params = dict(
        n_estimators=int(n_estimators if n_estimators is not None else CFG["xgb_n_estimators"]),
        max_depth=int(max_depth if max_depth is not None else CFG["xgb_max_depth"]),
        learning_rate=float(learning_rate if learning_rate is not None else CFG["xgb_learning_rate"]),
        random_state=int(CFG.get("xgb_random_state", 42)),
    )
    # macOS에서 같은 프로세스에 torch가 올라와 있으면 OpenMP 충돌(세그폴트)이 나서 단일 스레드로
    # (CHANGES_전처리.md 2026-09-10 기록). torch를 쓰지 않는 학습은 모든 코어를 그대로 쓴다.
    if "torch" in sys.modules:
        params["n_jobs"] = 1
    params.update(kwargs)
    return xgb.XGBRegressor(**params)
