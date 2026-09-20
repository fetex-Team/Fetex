"""호환용 Module 3 학습 진입점.

이전 구현은 현재 시점 수요를 목표로 학습하고 1칸짜리 CNN-LSTM 시퀀스를 만들었다.
공식 학습 경로는 t+1~t+6 다중 타깃, 시간 순서 분할, Module 4 배차 계약을 갖춘
``scripts.train_dispatch_model``이다. 기존 ``python train.py`` 사용자는 그대로
새 경로를 실행할 수 있다.
"""
if __name__ == "__main__":
    import runpy
    runpy.run_module("scripts.train_dispatch_model", run_name="__main__")
