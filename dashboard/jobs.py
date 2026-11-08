"""
대시보드에서 시작한 작업(시뮬레이션 실험, Unity 재생 파일 내보내기)을 백그라운드 프로세스로 돌리고 상태를 관리한다.

- 실험: tools/repeat_experiments.py를 그대로 호출 → 결과는 results/repeat/<이름>/ (보고서 실험과 같은 형식·같은 통계)
- Unity: export_unity_replay.py를 임시 설정 파일(MOBILITY_CONFIG)로 호출 → results/unity_replay/<이름>/
- 시뮬레이션은 CPU를 많이 써서 한 번에 한 작업만 돌린다(나머지는 거절). 작업 기록은 results/dashboard_jobs/에 남는다.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import signal
import subprocess
import sys
import tempfile
import threading

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
JOB_DIR = os.path.join(ROOT, "results", "dashboard_jobs")
REPEAT = os.path.join(ROOT, "results", "repeat")
UNITY_OUT = os.path.join(ROOT, "results", "unity_replay")


class Busy(RuntimeError):
    pass


class JobManager:
    def __init__(self, job_dir: str = JOB_DIR, python: str = sys.executable, dry_run: bool = False):
        self.job_dir, self.python, self.dry_run = job_dir, python, dry_run
        os.makedirs(job_dir, exist_ok=True)
        self._lock = threading.Lock()
        self._procs: dict[str, subprocess.Popen] = {}

    # ---------- 기록 ----------
    def _path(self, job_id):
        return os.path.join(self.job_dir, f"{job_id}.json")

    def _save(self, job):
        tmp = self._path(job["id"]) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(job, f, ensure_ascii=False, indent=1)
        os.replace(tmp, self._path(job["id"]))

    def get(self, job_id):
        if not job_id.replace("_", "").isalnum():
            return None
        try:
            with open(self._path(job_id), encoding="utf-8") as f:
                job = json.load(f)
        except (OSError, ValueError):
            return None
        return self._refresh(job)

    def list(self):
        jobs = []
        for fn in sorted(os.listdir(self.job_dir), reverse=True):
            if fn.endswith(".json") and fn[:8].isdigit():
                j = self.get(fn[:-5])
                if j:
                    jobs.append(j)
        return jobs

    def running(self):
        return next((j for j in self.list() if j["status"] == "running"), None)

    def _refresh(self, job):
        """진행률(끝난 실행 수)과, 대시보드가 다시 켜졌을 때 끊긴 작업 표시."""
        if job["kind"] == "experiment":
            d = os.path.join(REPEAT, job["name"], "runs")
            done = len([f for f in os.listdir(d) if f.endswith(".json")]) if os.path.isdir(d) else 0
            job["done"] = min(done, job["total"])
        if job["status"] == "running" and job["id"] not in self._procs and not self.dry_run:
            if not _pid_alive(job.get("pid")):
                job["status"] = "interrupted"
                self._save(job)
        return job

    def log_tail(self, job_id, n=60):
        job = self.get(job_id)
        if not job or not os.path.exists(job["log"]):
            return ""
        with open(job["log"], encoding="utf-8", errors="replace") as f:
            lines = [l for l in f.read().splitlines() if not l.startswith("Warning:")]
        return "\n".join(lines[-n:])

    # ---------- 시작 ----------
    def _start(self, kind, name, cmd, total, env_extra=None, meta=None):
        with self._lock:
            if self.running():
                raise Busy("이미 실행 중인 작업이 있습니다. 끝나거나 멈춘 뒤 다시 시도하세요.")
            job_id = dt.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            log = os.path.join(self.job_dir, f"{job_id}.log")
            job = {"id": job_id, "kind": kind, "name": name, "cmd": cmd, "total": total, "done": 0,
                   "status": "running", "started": dt.datetime.now().isoformat(timespec="seconds"),
                   "finished": None, "returncode": None, "log": log, "pid": None, **(meta or {})}
            if self.dry_run:
                job["status"] = "dry_run"
                self._save(job)
                return job
            lf = open(log, "w", encoding="utf-8")
            env = {**os.environ, "PYTHONUTF8": "1", "PYTHONHASHSEED": "0", **(env_extra or {})}
            group = ({"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt"
                     else {"start_new_session": True})      # 멈출 때 SUMO 자식 프로세스까지 함께 끄려고
            p = subprocess.Popen(cmd, cwd=ROOT, stdout=lf, stderr=subprocess.STDOUT, env=env, **group)
            job["pid"] = p.pid
            self._procs[job_id] = p
            self._save(job)
            threading.Thread(target=self._wait, args=(job_id, p, lf), daemon=True).start()
            return job

    def _wait(self, job_id, p, lf):
        rc = p.wait()
        lf.close()
        with self._lock:
            self._procs.pop(job_id, None)
            job = json.load(open(self._path(job_id), encoding="utf-8"))
            if job["status"] == "running":
                job["status"] = "done" if rc == 0 else "failed"
            job.update(returncode=rc, finished=dt.datetime.now().isoformat(timespec="seconds"))
            self._save(job)

    def cancel(self, job_id):
        job = self.get(job_id)
        if not job or job["status"] != "running":
            return job
        _kill_tree(job["pid"], self._procs.get(job_id))
        job["status"] = "cancelled"
        job["finished"] = dt.datetime.now().isoformat(timespec="seconds")
        self._save(job)
        return job

    def start_experiment(self, name, label, dates, strategies, overrides, jobs=2, note=""):
        out = os.path.join(REPEAT, name)
        os.makedirs(out, exist_ok=True)
        with open(os.path.join(out, "conditions.json"), "w", encoding="utf-8") as f:
            json.dump({"label": label or name, "note": note, "dates": dates, "strategies": strategies,
                       "overrides": overrides, "created": dt.datetime.now().isoformat(timespec="seconds"),
                       "source": "dashboard"}, f, ensure_ascii=False, indent=1)
        cmd = [self.python, os.path.join("tools", "repeat_experiments.py"), "--name", name, "--jobs", str(jobs),
               "--dates", *dates, "--strategies", *strategies]
        if overrides:
            cmd += ["--set", *[f"{k}={json.dumps(v)}" for k, v in overrides.items()]]
        return self._start("experiment", name, cmd, len(dates) * len(strategies),
                           meta={"label": label or name, "dates": dates, "strategies": strategies, "overrides": overrides})

    def start_unity_export(self, name, date, strategies, overrides, unity_dir=None):
        from dashboard.params import load_settings
        cfg = load_settings()
        cfg.update(overrides)
        cfg.update({"sim_date": date, "run_date_mode": "sim_date", "write_demand_logs": False})
        cfg_dir = os.path.join(self.job_dir, "configs")
        os.makedirs(cfg_dir, exist_ok=True)
        fd, cfg_path = tempfile.mkstemp(prefix="unity_", suffix=".json", dir=cfg_dir)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False)
        out = os.path.join(UNITY_OUT, name)
        cmd = [self.python, "export_unity_replay.py", "--output", out, "--strategies", *strategies]
        if unity_dir:
            cmd += ["--unity-dir", unity_dir]
        return self._start("unity", name, cmd, len(strategies), env_extra={"MOBILITY_CONFIG": cfg_path},
                           meta={"label": f"Unity 재생 {date}", "dates": [date], "strategies": strategies,
                                 "overrides": overrides, "output": out})


def _pid_alive(pid):
    """프로세스가 살아 있나. Windows에서 os.kill(pid, 0)은 프로세스를 종료시켜 버리므로 psutil을 쓴다."""
    if not pid:
        return False
    try:
        import psutil
        return psutil.pid_exists(pid)
    except ImportError:
        if os.name == "nt":
            return True          # 확인할 수 없으면 살아 있다고 본다 (끊김 표시만 늦어짐)
        try:
            os.kill(pid, 0)
        except OSError:
            return False
        return True


def _kill_tree(pid, proc=None):
    """작업 프로세스와 그 자식(SUMO 등)을 모두 끈다."""
    if not pid:
        return
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True)
        else:
            os.killpg(pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError, OSError):
        if proc:
            proc.terminate()
