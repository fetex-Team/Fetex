"""
FeTex 디지털 트윈 대시보드 — 실험 결과 비교 · 조건을 바꿔 새 시뮬레이션 실행 · Unity 재생 파일 내보내기.

    python dashboard/app.py                 # http://127.0.0.1:8050 (브라우저 자동으로 열림)
    python dashboard/app.py --port 8060 --no-browser

- 내 컴퓨터(127.0.0.1)에서만 접속된다.
- 바꿀 수 있는 조건은 dashboard/params.py 화이트리스트뿐이고, 기본값·근거 등급은 config와 근거 대장에서 읽는다.
- 새 실험은 module4_dispatch/experiments/repeat_experiments.py를 그대로 돌려 results/repeat/<이름>/에 저장 → 보고서 실험과 같은 형식·통계.
"""
from __future__ import annotations

import argparse
import os
import sys
import threading
import webbrowser

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from flask import Flask, jsonify, request, send_from_directory  # noqa: E402

from dashboard import experiments, params  # noqa: E402
from dashboard.jobs import Busy, JobManager, UNITY_OUT  # noqa: E402

STATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
MIN_PER_RUN = 6.0        # 하루치 1회 실행 평균 소요(분), results/repeat/twin_v3 기준


def create_app(job_manager: JobManager | None = None) -> Flask:
    app = Flask(__name__, static_folder=None)
    app.config["JSON_AS_ASCII"] = False
    jm = job_manager or JobManager()

    def bad(msg, code=400):
        return jsonify({"error": msg}), code

    @app.get("/")
    def index():
        return send_from_directory(STATIC, "index.html")

    @app.get("/static/<path:fn>")
    def static_files(fn):
        return send_from_directory(STATIC, fn)

    @app.get("/api/meta")
    def meta():
        cfg = params.load_settings()
        return jsonify({"strategies": params.STRATEGIES, "eval_dates": params.EVAL_DATES,
                        "date_min": params.DATE_MIN.isoformat(), "date_max": params.DATE_MAX.isoformat(),
                        "min_per_run": MIN_PER_RUN, "cpu": os.cpu_count(),
                        "current": {k: cfg.get(k) for k in params.PARAMS},
                        "sim_hours": [cfg.get("sim_start_hour"), cfg.get("sim_end_hour")]})

    @app.get("/api/params")
    def get_params():
        return jsonify(params.schema())

    @app.get("/api/experiments")
    def list_experiments():
        return jsonify(experiments.list_experiments())

    @app.get("/api/experiments/<name>")
    def experiment(name):
        if name != params.safe_name(name):
            return bad("실험 이름 형식이 아닙니다", 404)
        d = experiments.experiment_detail(name)
        return jsonify(d) if d else bad("없는 실험입니다", 404)

    @app.get("/api/map")
    def twin_map():
        from dashboard import twin_map as tm
        try:
            date = params.validate_dates([request.args.get("date") or "2026-10-14"])[0]
            return jsonify(tm.build(date))
        except params.InvalidRequest as e:
            return bad(str(e))
        except Exception as e:                      # 도로망 파일이 없거나 sumolib가 없을 때
            return bad(f"지도를 만들지 못했습니다: {e}", 503)

    @app.get("/api/sensitivity")
    def sensitivity():
        return jsonify(experiments.sensitivity())

    @app.get("/api/jobs")
    def jobs():
        return jsonify(jm.list())

    @app.get("/api/jobs/<job_id>")
    def job(job_id):
        j = jm.get(job_id)
        if not j:
            return bad("없는 작업입니다", 404)
        return jsonify({**j, "log_tail": jm.log_tail(job_id)})

    @app.post("/api/jobs/<job_id>/cancel")
    def cancel(job_id):
        j = jm.cancel(job_id)
        return jsonify(j) if j else bad("없는 작업입니다", 404)

    @app.post("/api/run")
    def run():
        body = request.get_json(silent=True) or {}
        try:
            dates = params.validate_dates(body.get("dates"))
            strategies = params.validate_strategies(body.get("strategies"))
            overrides = params.validate_overrides(body.get("overrides") or {})
            n_jobs = int(body.get("jobs") or 2)
            if not 1 <= n_jobs <= max(1, os.cpu_count() or 1):
                raise params.InvalidRequest(f"동시 실행 수는 1~{os.cpu_count()} 사이여야 합니다")
        except params.InvalidRequest as e:
            return bad(str(e))
        name = params.safe_name(body.get("name"))
        if name in experiments.CATALOG:
            return bad(f"'{name}'은 보고서에 쓰인 실험 이름이라 쓸 수 없습니다. 다른 이름을 주세요.")
        existing = experiments.load_runs(name)
        if existing and not body.get("resume"):
            return bad(f"'{name}' 실험이 이미 있습니다. 이어서 하려면 이어 하기를 켜거나 다른 이름을 주세요.", 409)
        try:
            j = jm.start_experiment(name, body.get("label") or "", dates, strategies, overrides, n_jobs,
                                    note=body.get("note") or "")
        except Busy as e:
            return bad(str(e), 409)
        j["eta_min"] = round(len(dates) * len(strategies) * MIN_PER_RUN / n_jobs)
        return jsonify(j), 202

    @app.post("/api/unity")
    def unity():
        body = request.get_json(silent=True) or {}
        try:
            date = params.validate_dates([body.get("date")])[0]
            strategies = params.validate_strategies(body.get("strategies"))
            overrides = params.validate_overrides(body.get("overrides") or {})
        except params.InvalidRequest as e:
            return bad(str(e))
        unity_dir = (body.get("unity_dir") or "").strip() or None
        if unity_dir and not os.path.isdir(unity_dir):
            return bad(f"Unity 폴더가 없습니다: {unity_dir}")
        name = params.safe_name(body.get("name") or f"replay_{date.replace('-', '')}")
        try:
            j = jm.start_unity_export(name, date, strategies, overrides, unity_dir)
        except Busy as e:
            return bad(str(e), 409)
        return jsonify(j), 202

    @app.get("/api/unity")
    def unity_outputs():
        out = []
        if os.path.isdir(UNITY_OUT):
            for name in sorted(os.listdir(UNITY_OUT)):
                d = os.path.join(UNITY_OUT, name)
                if os.path.isdir(d) and name != "backup":
                    files = sorted(f for f in os.listdir(d) if f.endswith((".json", ".json.gz")))
                    if files:
                        out.append({"name": name, "path": os.path.relpath(d, ROOT), "files": files})
            top = sorted(f for f in os.listdir(UNITY_OUT) if f.endswith((".json", ".json.gz")))
            if top:
                out.insert(0, {"name": "(기본 위치)", "path": os.path.relpath(UNITY_OUT, ROOT), "files": top})
        return jsonify(out)

    return app


def main():
    ap = argparse.ArgumentParser(description="FeTex 디지털 트윈 대시보드")
    ap.add_argument("--port", type=int, default=8050)
    ap.add_argument("--no-browser", action="store_true")
    a = ap.parse_args()
    app = create_app()
    url = f"http://127.0.0.1:{a.port}"
    print(f"[대시보드] {url}  (끝내려면 Ctrl+C)")
    if not a.no_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    app.run(host="127.0.0.1", port=a.port, debug=False, threaded=True)


if __name__ == "__main__":
    main()
