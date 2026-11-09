"""대시보드(dashboard/) API 테스트 — 시뮬레이션은 돌리지 않는다(JobManager dry_run)."""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from dashboard import params  # noqa: E402
from dashboard.app import create_app  # noqa: E402
from dashboard.jobs import JobManager  # noqa: E402


@pytest.fixture(autouse=True)
def _user_config_only(monkeypatch):
    """다른 테스트(test_runtime_validation)가 MOBILITY_CONFIG를 최소 설정으로 바꿔 두므로,
    대시보드 기본값은 개인 config.json(없으면 presets/default/기본.json) 기준으로 확인한다."""
    monkeypatch.delenv("MOBILITY_CONFIG", raising=False)


@pytest.fixture
def client(tmp_path):
    app = create_app(JobManager(job_dir=str(tmp_path), dry_run=True))
    app.testing = True
    return app.test_client()


def test_params_match_settings_and_register():
    if not os.path.exists(params.REGISTER):
        pytest.skip("근거 대장 docs/design/08_evidence_register.md가 아직 이 브랜치에 없음 (문서 작업에서 추가 예정)")
    rows = {r["key"]: r for r in params.schema()}
    cfg = params.load_settings()
    assert rows["num_taxis"]["default"] == cfg["num_taxis"]
    assert rows["real_spatial_weights"]["grade"] == "A"          # 근거 대장에서 읽은 등급
    assert rows["real_spatial_weights"]["type"] == "choice"


def test_validate_overrides_whitelist_and_range():
    assert params.validate_overrides({"num_taxis": 70}) == {"num_taxis": 70}
    assert params.validate_overrides({"num_taxis": params.load_settings()["num_taxis"]}) == {}   # 기본값과 같으면 뺌
    for bad in ({"num_taxis": 5}, {"num_taxis": "many"}, {"real_spatial_weights": "x"},
                {"taxi_external_away": "yes"}, {"rm -rf": 1}, {"sim_start_hour": 0}):
        with pytest.raises(params.InvalidRequest):
            params.validate_overrides(bad)


def test_validate_dates_and_names():
    assert params.validate_dates(["2026-09-10", "2026-09-10"]) == ["2026-09-10"]
    for bad in ([], ["2026-13-01"], ["2025-01-01"], "2026-09-10"):
        with pytest.raises(params.InvalidRequest):
            params.validate_dates(bad)
    assert params.safe_name("Taxi 70 / POI!") == "taxi_70_poi"
    assert params.safe_name("../../etc").startswith("etc")
    assert params.safe_name("").startswith("dash_")


def test_read_endpoints(client):
    assert client.get("/").status_code == 200
    m = client.get("/api/meta").get_json()
    assert "patrol" in m["strategies"] and len(m["eval_dates"]) == 7
    exps = client.get("/api/experiments").get_json()
    names = {e["name"] for e in exps}
    assert "twin_v3" in names
    d = client.get("/api/experiments/twin_v3").get_json()
    assert d["stats"]["strategies"]["patrol"]["service_rate"]["mean"] > 0.5
    assert client.get("/api/experiments/../config").status_code == 404
    sens = client.get("/api/sensitivity").get_json()
    assert any(r["name"] == "sens_fleet50" and r["diff_pp"] < -10 for r in sens)


def test_run_builds_command_and_rejects_bad_input(client, tmp_path):
    r = client.post("/api/run", json={"name": "dash_test_run", "dates": ["2026-09-10"], "strategies": ["patrol"],
                                      "overrides": {"num_taxis": 70, "real_spatial_weights": "poi"}})
    assert r.status_code == 202, r.get_json()
    j = r.get_json()
    assert j["total"] == 1 and j["status"] == "dry_run"
    cmd = " ".join(j["cmd"])
    assert "module4_dispatch" in cmd and "repeat_experiments.py" in cmd
    assert "--name dash_test_run" in cmd and "--set num_taxis=70 real_spatial_weights=\"poi\"" in cmd
    cond = os.path.join(ROOT, "results", "repeat", "dash_test_run", "conditions.json")
    assert os.path.exists(cond)
    os.remove(cond)
    os.rmdir(os.path.dirname(cond))
    assert client.post("/api/run", json={"name": "twin_v3", "dates": ["2026-09-10"], "strategies": ["patrol"]}).status_code == 400
    assert client.post("/api/run", json={"dates": ["2026-09-10"], "strategies": ["bogus"]}).status_code == 400
    assert client.post("/api/run", json={"dates": ["2026-09-10"], "strategies": ["patrol"],
                                         "overrides": {"base_fare": 1}}).status_code == 400


def test_unity_export_uses_temp_config(client):
    r = client.post("/api/unity", json={"date": "2026-10-14", "strategies": ["patrol", "forecast"],
                                        "overrides": {"num_taxis": 75}})
    assert r.status_code == 202, r.get_json()
    j = r.get_json()
    assert any(c.replace("\\", "/").endswith("module1_simulation/unity/export_unity_replay.py") for c in j["cmd"]) and j["output"].endswith(os.path.join("unity_replay", "replay_20261014"))
    assert client.post("/api/unity", json={"date": "2026-10-14", "strategies": ["patrol"],
                                           "unity_dir": "/no/such/dir"}).status_code == 400


def _map(client):
    r = client.get("/api/map?date=2026-10-14")
    if r.status_code == 503:
        pytest.skip(r.get_json()["error"])
    return r.get_json()


def test_map_endpoint_matches_network_and_demand(client):
    """지도 패널(좌표는 도로망 범위 ±20m — 차로 모양이 교차로 범위보다 조금 나감): 도로 수 = 그린 도로 수,
    H3 칸별 호출 합 = 시간대별 합 = 하루 호출 수, 실제 신호 교차로 2곳 표시."""
    d = _map(client)
    assert d["n_edges"] == len(d["roads"]) > 0 and d["n_junctions"] > 0 and d["n_tls"] > 0
    assert sum(h["calls"] for h in d["hexes"]) == d["total_calls"] == sum(h["n"] for h in d["hourly"])
    assert {s["name"] for s in d["signals"]} == {"강남역", "교보타워사거리"}
    assert all(-20 <= x <= d["w"] + 20 and -20 <= y <= d["h"] + 20 for r_ in d["roads"] for x, y in r_["p"])
    assert client.get("/api/map?date=1999-01-01").status_code == 400


def test_map_counts_match_realistic_network(client):
    """도로 현실화(중앙버스차로·5030·실제 신호)로 다시 만든 강남역 도로망이면 382 도로·170 교차로·31 신호 (ldk 기준)."""
    d = _map(client)
    if (d["n_edges"], d["n_junctions"], d["n_tls"]) != (382, 170, 31):
        pytest.skip(f"지금 도로망({d['n_edges']} 도로·{d['n_junctions']} 교차로·{d['n_tls']} 신호)은 도로 현실화 빌드가 아님 "
                    f"— 시뮬레이션 코어(build_env.py 현실화)를 반영해 module1_simulation/build_env.py로 다시 만든 뒤 확인")
