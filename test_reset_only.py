"""호환 실행 경로. 실제 통합 점검은 ``tests/integration``에 있다."""

import runpy
from pathlib import Path


if __name__ == "__main__":
    runpy.run_path(str(Path(__file__).parent / "tests" / "integration" / "test_reset_only.py"), run_name="__main__")
