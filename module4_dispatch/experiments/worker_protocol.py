"""실험 도구(repeat_experiments, fleet_sizing, check_determinism)가 공유하는 작업자 실행 규약.

부모 프로세스가 같은 스크립트를 `--worker 인자...`로 새 프로세스에서 실행하고, 작업자는 결과를
`@@RESULT@@{json}` 한 줄로 출력한다. 새 프로세스마다 SUMO·설정(MOBILITY_CONFIG)이 완전히 분리되고,
PYTHONHASHSEED를 고정해 같은 조건이면 같은 결과가 나온다 (module1_simulation/reproducibility.py).
"""
import json
import os
import subprocess
import sys

RESULT_PREFIX = "@@RESULT@@"


def emit_result(result: dict):
    """(작업자 쪽) 결과를 부모가 읽을 수 있는 한 줄로 출력."""
    print(RESULT_PREFIX + json.dumps(result, ensure_ascii=False, default=str), flush=True)


def run_worker(script, args, cwd, log_path=None, hash_seed="0", env_extra=None):
    """(부모 쪽) `python script --worker args...`를 실행하고 (결과 dict 또는 None, CompletedProcess)를 반환.
    log_path를 주면 stderr와 stdout을 그 파일에 남긴다. hash_seed=None이면 해시 시드를 고정하지 않는다."""
    env = {**os.environ, "PYTHONUTF8": "1", **(env_extra or {})}
    if hash_seed is not None:
        env["PYTHONHASHSEED"] = str(hash_seed)
    cmd = [sys.executable, str(script), "--worker", *map(str, args)]
    kw = dict(cwd=str(cwd), env=env, text=True, encoding="utf-8", errors="replace")
    if log_path:
        with open(log_path, "w", encoding="utf-8") as lf:
            proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=lf, **kw)
            lf.write(proc.stdout)
    else:
        proc = subprocess.run(cmd, capture_output=True, **kw)
    line = next((l for l in proc.stdout.splitlines() if l.startswith(RESULT_PREFIX)), None)
    return (json.loads(line[len(RESULT_PREFIX):]) if line else None), proc
