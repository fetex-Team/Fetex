"""
실행 재현성: 같은 설정·같은 손님 목록이면 매번 같은 결과가 나오게 한다.

파이썬은 실행할 때마다 문자열 해시 순서를 무작위로 바꾼다(PYTHONHASHSEED). 배차·택시 관리 코드가
차량 id 집합(set)을 순회하는 순서가 실행마다 달라져, 동률 처리 결과가 바뀌고
같은 조건에서도 타임아웃이 298건/324건처럼 흔들렸다.
진입점에서 ensure_fixed_hash_seed()를 부르면, 해시 시드가 고정되지 않은 경우 같은 명령을
PYTHONHASHSEED=0으로 한 번 다시 실행한다(하위 프로세스도 이 값을 물려받는다).
"""
import os
import subprocess
import sys

HASH_SEED = "0"


def fixed_env(env=None):
    """하위 프로세스용 환경변수 (해시 시드 고정 + UTF-8)."""
    e = dict(os.environ if env is None else env)
    e["PYTHONHASHSEED"] = HASH_SEED
    e.setdefault("PYTHONUTF8", "1")
    e.setdefault("PYTHONIOENCODING", "utf-8")
    return e


def ensure_fixed_hash_seed():
    if os.environ.get("PYTHONHASHSEED") == HASH_SEED:
        return
    if os.environ.get("FETEX_ALLOW_RANDOM_HASH") == "1":
        return  # 일부러 무작위 해시로 돌려 보고 싶을 때
    proc = subprocess.run([sys.executable, *sys.argv], env=fixed_env())
    sys.exit(proc.returncode)
