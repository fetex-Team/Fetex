"""Module 3 공식 학습 명령의 모듈 경로 진입점."""
if __name__ == "__main__":
    import runpy
    runpy.run_module("scripts.train_dispatch_model", run_name="__main__")
