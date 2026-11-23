"""모델 세트 보관함(tools/model_sets.py): 백업에 RL 모델·학습 조건이 함께 들어가고, 이름 붙이기·A/B용 환경변수가 맞는지."""
import os

import pytest

from tools import model_sets as ms


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    saved, backups, rl = tmp_path / "saved_models", tmp_path / "saved_models_backups", tmp_path / "module4_dispatch"
    for d in (saved, rl):
        d.mkdir()
    (saved / "demand_v2.joblib").write_bytes(b"v2")
    (saved / "category_demand_models.pkl").write_bytes(b"cat")
    (rl / "rl_reposition_model.zip").write_bytes(b"rl")
    (rl / "rl_reposition_model_vecnorm.pkl").write_bytes(b"vn")
    monkeypatch.setattr(ms, "SAVED", str(saved))
    monkeypatch.setattr(ms, "BACKUPS", str(backups))
    monkeypatch.setattr(ms, "RL_DIR", str(rl))
    return tmp_path


def test_backup_includes_rl_and_meta(sandbox):
    ms.write_active_meta({"num_taxis": 61, "rl_reward_mode": "gravityVer",
                          "rl_train_start": "2026-07-13", "rl_train_end": "2026-09-06"}, rl_only=False)
    path, msg = ms.backup_current(label="택시61_gravity")
    names = set(os.listdir(path))
    assert {"demand_v2.joblib", "category_demand_models.pkl", "rl_reposition_model.zip",
            "rl_reposition_model_vecnorm.pkl", "model_meta.json"} <= names
    sets = ms.list_sets()
    assert sets[0]["id"] == ms.CURRENT
    assert sets[1]["label"] == "택시61_gravity"
    assert "택시 61" in sets[1]["title"] and "gravityVer" in sets[1]["title"] and "RL 07/13~09/06" in sets[1]["title"]


def test_label_and_env(sandbox):
    path, _ = ms.backup_current()
    ms.set_label(path, "좋은 모델")
    assert [s for s in ms.list_sets() if s["dir"] == path][0]["label"] == "좋은 모델"
    ms.set_label(path, "")
    assert [s for s in ms.list_sets() if s["dir"] == path][0]["label"] == ""
    env = ms.env_for(path)
    assert env["AI_RL_MODEL_PATH"].endswith("rl_reposition_model.zip") and path in env["AI_RL_MODEL_PATH"]
    assert path in env["AI_DEMAND_V2_PATH"] and path in env["AI_CATEGORY_MODEL_PATH"]
    assert ms.env_for(ms.SAVED) == {}


def test_rl_only_keeps_demand_trained_at(sandbox):
    first = ms.write_active_meta({"num_taxis": 50}, rl_only=False, demand_logs="실측 A")
    second = ms.write_active_meta({"num_taxis": 100}, rl_only=True)
    assert second["demand_trained_at"] == first["demand_trained_at"]
    assert second["demand_logs"] == "실측 A"
    assert "RL만 재학습" in ms.describe(second)
