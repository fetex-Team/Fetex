"""모델 세트 보관함 — 학습 전 자동 백업과 KJS '모델 스냅샷'을 하나로 합친 것.

모델 세트 = 시뮬레이션이 쓰는 모델 한 벌:
    saved_models/ (demand_v2.joblib, cnn_lstm_demand.pt, category_demand_models*.pkl)
    + module4_dispatch/rl_reposition_model.zip (+ _vecnorm.pkl)
    + model_meta.json (어떤 조건으로 학습했는지: 택시 수, 보상 방식, 학습 기간 ...)

- 학습 시작 전: 지금 세트를 saved_models_backups/<시각>/에 자동 보관 (덮어쓰기 대비)
- 학습 끝: saved_models/model_meta.json에 이번 학습 조건을 기록 → 다음 백업에 함께 들어간다
- 마음에 드는 세트에는 이름(label)을 붙인다 → KJS 스냅샷처럼 A/B에서 골라 비교
- A/B·플레이에서 세트를 고르면 env_for()의 환경변수로 그 세트의 파일을 읽는다 (saved_models는 그대로)
"""
import json
import os
import shutil
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SAVED = os.path.join(ROOT, "saved_models")
BACKUPS = os.path.join(ROOT, "saved_models_backups")
RL_DIR = os.path.join(ROOT, "module4_dispatch")
RL_FILES = ("rl_reposition_model.zip", "rl_reposition_model_vecnorm.pkl")
META = "model_meta.json"
CURRENT = "current"   # list_sets()에서 지금 쓰는 세트(saved_models)를 가리키는 id

# 학습 설정 중 세트 설명에 남길 값
META_KEYS = ("region", "num_taxis", "rl_reward_mode", "rl_train_start", "rl_train_end", "rl_train_day_type",
             "rl_total_timesteps", "demand_source", "real_demand_scale", "freq", "h3_resolution")


def _read_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _write_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def write_active_meta(cfg, **extra):
    """학습이 끝난 뒤 호출: 지금 saved_models 세트가 어떤 조건으로 학습됐는지 기록.
    RL만 다시 학습한 경우 수요 예측 쪽 조건은 이전 메모를 이어받는다."""
    old = _read_json(os.path.join(SAVED, META))
    meta = {k: cfg.get(k) for k in META_KEYS}
    meta.update({"trained_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), **extra})
    if extra.get("rl_only") and old.get("demand_trained_at"):
        meta["demand_trained_at"] = old["demand_trained_at"]
        meta["demand_logs"] = old.get("demand_logs")
    elif not extra.get("rl_only"):
        meta["demand_trained_at"] = meta["trained_at"]
    os.makedirs(SAVED, exist_ok=True)
    _write_json(os.path.join(SAVED, META), meta)
    return meta


def backup_current(label=None, suffix=""):
    """지금 세트(saved_models + RL 모델)를 saved_models_backups/<시각>/에 복사. 반환: (폴더 경로, 안내 문구)"""
    has_models = os.path.isdir(SAVED) and any(f != META for f in os.listdir(SAVED))
    has_rl = os.path.exists(os.path.join(RL_DIR, RL_FILES[0]))
    if not has_models and not has_rl:
        return None, "백업 생략 (기존 모델 없음)"
    name = datetime.now().strftime("%Y%m%d_%H%M%S") + suffix
    dst = os.path.join(BACKUPS, name)
    os.makedirs(BACKUPS, exist_ok=True)
    if os.path.isdir(SAVED):
        shutil.copytree(SAVED, dst)
    else:
        os.makedirs(dst)
    for f in RL_FILES:
        src = os.path.join(RL_DIR, f)
        if os.path.exists(src):
            shutil.copy2(src, os.path.join(dst, f))
    meta = _read_json(os.path.join(dst, META))
    meta["backed_up_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if label:
        meta["label"] = label.strip()
    _write_json(os.path.join(dst, META), meta)
    return dst, f"기존 모델 세트를 {os.path.relpath(dst, ROOT)}에 백업" + (f" (이름: {label})" if label else "")


def set_label(set_dir, label):
    """보관된 세트에 이름을 붙이거나(label) 지운다(빈 문자열)."""
    path = os.path.join(set_dir, META)
    meta = _read_json(path)
    if label and label.strip():
        meta["label"] = label.strip()
    else:
        meta.pop("label", None)
    _write_json(path, meta)


def _files(set_dir, is_current):
    rl_dir = RL_DIR if is_current else set_dir
    return {
        "demand_v2": os.path.join(set_dir, "demand_v2.joblib"),
        "category": os.path.join(set_dir, "category_demand_models.pkl"),
        "rl": os.path.join(rl_dir, RL_FILES[0]),
    }


def list_sets(limit=30):
    """[{id, dir, label, meta, files, title}] 지금 세트가 맨 앞, 그다음 이름 붙은 세트, 그다음 최근 백업."""
    out = [{"id": CURRENT, "dir": SAVED, "meta": _read_json(os.path.join(SAVED, META)),
            "files": _files(SAVED, True)}]
    if os.path.isdir(BACKUPS):
        for name in sorted(os.listdir(BACKUPS), reverse=True):
            d = os.path.join(BACKUPS, name)
            if os.path.isdir(d):
                out.append({"id": name, "dir": d, "meta": _read_json(os.path.join(d, META)),
                            "files": _files(d, False)})
    cur, rest = out[0], out[1:]
    named = [s for s in rest if s["meta"].get("label")]
    unnamed = [s for s in rest if not s["meta"].get("label")]
    out = [cur] + named + unnamed[:max(0, limit - len(named))]
    for s in out:
        s["label"] = s["meta"].get("label", "")
        s["title"] = title(s)
    return out


def describe(meta):
    """'택시 61 · gravityVer · RL 07/13~09/06' 같은 한 줄 설명. 메모가 없으면 빈 문자열."""
    if not meta or not meta.get("trained_at"):
        return ""
    parts = []
    if meta.get("num_taxis") is not None:
        parts.append(f"택시 {meta['num_taxis']}")
    if meta.get("rl_reward_mode"):
        parts.append(meta["rl_reward_mode"])
    s, e = meta.get("rl_train_start"), meta.get("rl_train_end")
    if s and e:
        parts.append(f"RL {str(s)[5:].replace('-', '/')}~{str(e)[5:].replace('-', '/')}")
    if meta.get("rl_only"):
        parts.append("RL만 재학습")
    parts.append(f"학습 {str(meta['trained_at'])[5:16]}")
    return " · ".join(parts)


def title(s):
    """콤보박스에 보일 이름."""
    desc = describe(s["meta"])
    if s["id"] == CURRENT:
        head = "지금 모델 (saved_models)"
    elif s["label"]:
        head = f"★ {s['label']}"
    else:
        head = f"백업 {s['id']}"
    missing = [k for k, p in s["files"].items() if not os.path.exists(p)]
    tail = f" — {desc}" if desc else " — 학습 조건 메모 없음"
    if missing:
        tail += f" (없음: {', '.join(missing)})"
    return head + tail


def env_for(set_dir):
    """보관된 세트를 쓰도록 하는 환경변수 (measure_wait_time·h3_predictor·A/B 워커가 읽음). 지금 세트면 {}."""
    if not set_dir or os.path.abspath(set_dir) == os.path.abspath(SAVED):
        return {}
    env = {}
    files = _files(os.path.abspath(set_dir), False)
    if os.path.exists(files["demand_v2"]):
        env["AI_DEMAND_V2_PATH"] = files["demand_v2"]
    if os.path.exists(files["category"]):
        env["AI_CATEGORY_MODEL_PATH"] = files["category"]
    if os.path.exists(files["rl"]):
        env["AI_RL_MODEL_PATH"] = files["rl"]
    return env
