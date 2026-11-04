"""
도로망 이동시간(module4_dispatch/network_cost.py)과 헝가리안 비용 선택 테스트.

실행: python tests/test_network_cost.py   (pytest로도 실행 가능)
- netconvert(eclipse-sumo)로 임시 도로망을 만들어 검사한다. 네트워크·SUMO 실행 불필요.

임시 도로망 (한 변 200m 정사각형, 일방통행 시계방향 고리):
    D(0,200) ←── C(200,200)
      │            ↑
      ↓            │
    A(0,0)  ──→  B(200,0)
A→B, B→C, C→D, D→A만 있다. B 근처 택시가 A 근처 승객에게 가려면 직선으로는 200m지만
일방통행이라 B→C→D→A로 600m를 돌아야 한다.
"""
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from module4_dispatch.network_cost import NetworkTravelTime, UNREACHABLE, net_file_from_sumocfg  # noqa: E402


def _netconvert():
    exe = shutil.which("netconvert")
    if exe:
        return exe
    try:
        import sumo  # eclipse-sumo 패키지
        cand = os.path.join(os.path.dirname(sumo.__file__), "bin", "netconvert")
        return cand if os.path.exists(cand) else None
    except ImportError:
        return None


def _build_loop_net(tmp):
    nodes = os.path.join(tmp, "n.nod.xml")
    edges = os.path.join(tmp, "e.edg.xml")
    with open(nodes, "w") as f:
        f.write('<nodes><node id="A" x="0" y="0" type="priority"/><node id="B" x="200" y="0" type="priority"/>'
                '<node id="C" x="200" y="200" type="priority"/><node id="D" x="0" y="200" type="priority"/></nodes>')
    with open(edges, "w") as f:
        f.write('<edges>'
                '<edge id="AB" from="A" to="B" numLanes="1" speed="10"/>'
                '<edge id="BC" from="B" to="C" numLanes="1" speed="10"/>'
                '<edge id="CD" from="C" to="D" numLanes="1" speed="10"/>'
                '<edge id="DA" from="D" to="A" numLanes="1" speed="10"/>'
                '</edges>')
    net = os.path.join(tmp, "loop.net.xml")
    subprocess.run([_netconvert(), "-n", nodes, "-e", edges, "-o", net, "--no-turnarounds", "true"],
                   check=True, capture_output=True)
    with open(os.path.join(tmp, "sim.sumocfg"), "w") as f:
        f.write('<configuration><input><net-file value="loop.net.xml"/></input></configuration>')
    return net


def test_one_way_detour_and_same_edge():
    if not _netconvert():
        print("[건너뜀] netconvert 없음")
        return
    with tempfile.TemporaryDirectory() as tmp:
        net = _build_loop_net(tmp)
        tt = NetworkTravelTime(net)
        L = tt.length[tt.index["AB"]]
        # 같은 도로 앞쪽: 그대로 직진
        assert abs(tt.time("AB", 10, "AB", 60) - 5.0) < 1e-6
        # 같은 도로 뒤쪽: 고리를 한 바퀴 돌아야 함 (약 4변)
        back = tt.time("AB", 60, "AB", 10)
        assert back > 3 * L / 10, back
        # B 근처(AB 끝)에서 A 근처(DA 끝)로: 직선은 가깝지만 BC→CD→DA를 돌아야 함
        detour = tt.time("AB", L - 1, "DA", tt.length[tt.index["DA"]] - 1)
        assert detour > 2.5 * L / 10, detour
        # 정방향 이웃은 짧다
        assert tt.time("AB", L - 1, "BC", 1) < 2.0
        # 없는 도로
        assert tt.time("AB", 0, "없음", 0) == UNREACHABLE
        assert net_file_from_sumocfg(os.path.join(tmp, "sim.sumocfg")) == net


def test_dispatcher_falls_back_without_table():
    from module4_dispatch.hungarian_dispatcher import HungarianDispatcher
    assert HungarianDispatcher().cost_mode == "euclidean"                 # 기본값은 기존 방식
    assert HungarianDispatcher("network", None).cost_mode == "euclidean"   # 표가 없으면 안전하게 대체


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"[PASS] {t.__name__}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"[FAIL] {t.__name__}: {e!r}")
    print(f"\n{len(tests) - failed}/{len(tests)} 통과")
    sys.exit(1 if failed else 0)
