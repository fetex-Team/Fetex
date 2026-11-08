"""대시보드(dashboard/) API 테스트 — 시뮬레이션은 돌리지 않는다(JobManager dry_run)."""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from dashboard import params  # noqa: E402
from dashboard.app import create_app  # noqa: E402
from dashboard.jobs import JobManager  # noqa: E402


@pytest.fixture
def client(tmp_path):
    app = create_app(JobManager(job_dir=str(tmp_path), dry_run=True))
    app.testing = True
    return app.test_client()


def test_params_match_settings_and_register():
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
    assert "export_unity_replay.py" in j["cmd"] and j["output"].endswith(os.path.join("unity_replay", "replay_20261014"))
    assert client.post("/api/unity", json={"date": "2026-10-14", "strategies": ["patrol"],
                                           "unity_dir": "/no/such/dir"}).status_code == 400


def test_map_endpoint_matches_network_and_demand(client):
    """지도 패널(좌표는 도로망 범위 ±20m — 차로 모양이 교차로 범위보다 조금 나감): SUMO 도로망 수치(382 도로·170 교차로·31 신호)와 H3 칸별 호출 합 = 하루 호출 수."""
    r = client.get("/api/map?date=2026-10-14")
    if r.status_code == 503:
        pytest.skip(r.get_json()["error"])
    d = r.get_json()
    assert d["n_edges"] == len(d["roads"]) == 382
    assert d["n_junctions"] == 170 and d["n_tls"] == 31
    assert sum(h["calls"] for h in d["hexes"]) == d["total_calls"] == sum(h["n"] for h in d["hourly"])
    assert {s["name"] for s in d["signals"]} == {"강남역", "교보타워사거리"}
    assert all(-20 <= x <= d["w"] + 20 and -20 <= y <= d["h"] + 20 for r_ in d["roads"] for x, y in r_["p"])
    assert client.get("/api/map?date=1999-01-01").status_code == 400
