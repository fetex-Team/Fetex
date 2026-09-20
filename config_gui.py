"""호환 실행 경로. 새 명령은 ``python -m fetex.apps.config_gui``이다."""

import runpy


if __name__ == "__main__":
    runpy.run_module("fetex.apps.config_gui", run_name="__main__")
