"""호환 실행 경로. 새 명령은 ``python -m fetex.apps.main``이다."""

from fetex.apps.main import run_pipeline


if __name__ == "__main__":
    run_pipeline()
