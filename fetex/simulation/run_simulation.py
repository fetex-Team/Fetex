"""GUI도 headless 실험과 동일한 시뮬레이션 루프를 사용한다."""
from pathlib import Path

from fetex.core.config import CFG
from fetex.runtime.measure_wait_time import ROOT, isolated_run, print_result


def run_sumo_gui(output_dir=None):
    """현재 config.json을 스냅샷으로 빌드한 뒤 SUMO GUI를 실행한다.

    이전 실행의 runtime_meta.json을 재사용하면 전략·지도·호출 설정이 현재 GUI
    설정과 달라질 수 있으므로, GUI는 항상 독립된 실행 디렉터리를 사용한다.
    """
    result = isolated_run(
        dict(CFG), Path(output_dir or ROOT / 'results/simulation'),
        sumo_binary='sumo-gui', stream_output=True,
    )
    print_result('GUI', result)
    return result


if __name__ == '__main__':
    run_sumo_gui()
