"""호환 import 경로. 예측 지표 구현은 :mod:`fetex.forecasting.metrics`에 있다."""

from fetex.forecasting.metrics import *  # noqa: F401,F403


if __name__ == "__main__":
    import runpy
    runpy.run_module("fetex.forecasting.metrics", run_name="__main__")
