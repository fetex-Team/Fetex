"""A/B 비교 대기열(module4_dispatch/experiments/ab_queue.py): 동시 실행 수 제한과 죽은 대기표 정리."""
import json
import os
import subprocess
import sys
import time

from module4_dispatch.experiments.ab_queue import AbQueueTicket, owner_info, write_json_atomic


def _dead_pid():
    p = subprocess.Popen([sys.executable, "-c", "pass"])
    p.wait()
    return p.pid


def _ticket(tmp_path, rid, limit=1, stale=1800):
    return AbQueueTicket(str(tmp_path), rid, limit, stale, poll_sec=0.05, log=lambda *_: None)


def test_dead_process_ticket_is_removed(tmp_path):
    write_json_atomic(str(tmp_path / "00000000_dead.json"), {"pid": _dead_pid(), "heartbeat": time.time()})
    with _ticket(tmp_path, "20990101_000000_1_aaaaaa") as t:
        assert t.wait_turn() < 1.0   # 죽은 앞 요청 때문에 기다리지 않음
    assert os.listdir(tmp_path) == []   # 내 대기표도 끝나면 삭제


def test_reused_pid_is_treated_as_dead(tmp_path):
    info = {**owner_info(), "heartbeat": time.time()}
    info["pid_start"] -= 1000   # 같은 PID, 다른 시작 시각 = PID 재사용
    write_json_atomic(str(tmp_path / "00000000_reused.json"), info)
    with _ticket(tmp_path, "20990101_000000_1_bbbbbb") as t:
        assert t.live_tickets() == [os.path.basename(t.path)]


def test_silent_ticket_is_removed_after_stale_sec(tmp_path):
    write_json_atomic(str(tmp_path / "00000000_hung.json"), {**owner_info(), "heartbeat": time.time() - 100})
    with _ticket(tmp_path, "20990101_000000_1_cccccc", stale=10) as t:
        assert t.live_tickets() == [os.path.basename(t.path)]


def test_limit_counts_live_requests_ahead(tmp_path):
    write_json_atomic(str(tmp_path / "00000000_live.json"), {**owner_info(), "heartbeat": time.time()})
    with _ticket(tmp_path, "20990101_000000_1_dddddd", limit=2) as t:
        assert t.wait_turn() < 1.0               # 앞에 1개, 한도 2 → 바로 실행
        with open(t.path, encoding="utf-8") as f:
            assert json.load(f)["state"] == "running"
    with _ticket(tmp_path, "20990101_000000_1_eeeeee", limit=1) as t:
        names = t.live_tickets()
        assert names.index(os.path.basename(t.path)) == 1   # 앞에 살아 있는 1개 → 한도 1이면 대기
