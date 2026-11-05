"""A/B 비교 요청끼리 쓰는 파일 기반 대기열 (KJS 브랜치의 _join_ab_queue를 옮겨 옴).

- 요청마다 results/ab_queue/<요청ID>.json 대기표를 만든다. 요청ID = <시각>_<PID>_<난수>라
  이름순 = 도착순이고, 같은 이름이 생길 수 없다.
- 앞에서부터 limit번째 안에 들면 실행, 아니면 2초마다 다시 확인한다.
- 죽은 요청의 대기표(강제 종료·충돌)는 다음 확인 때 지운다. 판단 기준:
  1) 대기표의 PID가 없거나, 같은 PID라도 프로세스 시작 시각이 다르면(PID 재사용) 죽은 것
  2) 살아 있는 요청은 HEARTBEAT_SEC마다 대기표를 다시 쓴다. stale_sec 넘게 소식이 없으면
     멈춘 것으로 보고 지운다 (절전 등으로 잘못 지워져도 주인이 다음 박동 때 다시 만든다).
"""
import datetime
import json
import os
import threading
import time
import uuid

HEARTBEAT_SEC = 30


def process_start_time(pid):
    """프로세스 시작 시각(초). 없는 PID면 None. 살아 있지만 시작 시각을 모르면 0(비교 생략).
    psutil이 있으면 그것을, 없으면 Windows는 kernel32, 그 외는 os.kill로 확인한다."""
    try:
        pid = int(pid)
    except (ValueError, TypeError):
        return None
    try:
        import psutil
    except ImportError:
        return _start_time_windows(pid) if os.name == "nt" else _start_time_posix(pid)
    try:
        return psutil.Process(pid).create_time()
    except psutil.NoSuchProcess:
        return None
    except psutil.AccessDenied:
        return 0   # 다른 권한으로 살아 있는 프로세스


def _start_time_windows(pid):
    # Windows의 os.kill(pid, 0)은 프로세스를 죽일 수 있어 OpenProcess로 확인한다 (KJS 방식 + 시작 시각)
    import ctypes
    from ctypes import wintypes
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
    kernel32.GetProcessTimes.argtypes = (wintypes.HANDLE,) + (ctypes.POINTER(wintypes.FILETIME),) * 4
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    handle = kernel32.OpenProcess(0x1000, False, pid)   # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return 0 if ctypes.get_last_error() == 5 else None   # 접근 거부 = 살아 있음
    try:
        code = wintypes.DWORD()
        if kernel32.GetExitCodeProcess(handle, ctypes.byref(code)) and code.value != 259:   # STILL_ACTIVE
            return None
        times = [wintypes.FILETIME() for _ in range(4)]
        if not kernel32.GetProcessTimes(handle, *[ctypes.byref(t) for t in times]):
            return 0
        ft = (times[0].dwHighDateTime << 32) | times[0].dwLowDateTime   # 1601년부터 100ns 단위
        return ft / 1e7 - 11644473600.0
    finally:
        kernel32.CloseHandle(handle)


def _start_time_posix(pid):
    try:
        os.kill(pid, 0)
    except PermissionError:
        return 0
    except OSError:
        return None
    return 0


def owner_alive(info):
    """대기표/작업 폴더 주인 정보({pid, pid_start})의 프로세스가 아직 살아 있는지."""
    started = process_start_time(info.get("pid"))
    if started is None:
        return False
    want = info.get("pid_start")
    # 시작 시각이 1초 넘게 다르면 같은 번호를 다시 받은 다른 프로세스
    return not (want and started and abs(started - want) > 1.0)


def owner_info():
    return {"pid": os.getpid(), "pid_start": process_start_time(os.getpid())}


def write_json_atomic(path, data):
    """임시 파일에 다 쓴 뒤 교체 — 읽는 쪽이 반쯤 쓰인 파일을 보지 않게 한다."""
    tmp = f"{path}.{os.getpid()}.{uuid.uuid4().hex[:6]}.tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def new_request_id():
    return f"{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}_{os.getpid()}_{uuid.uuid4().hex[:6]}"


class AbQueueTicket:
    """with AbQueueTicket(...) as t: t.wait_turn() ... — 블록을 벗어나면(예외 포함) 대기표를 지운다."""

    def __init__(self, queue_dir, request_id, limit, stale_sec, extra=None, poll_sec=2.0, log=print):
        self.queue_dir = queue_dir
        self.request_id = request_id
        self.limit = max(1, int(limit))
        self.stale_sec = float(stale_sec)
        self.poll_sec = poll_sec
        self.log = log
        self.path = os.path.join(queue_dir, f"{request_id}.json")
        self.info = {**owner_info(), "request_id": request_id, "state": "waiting",
                     "created_at": datetime.datetime.now().isoformat(timespec="seconds"), **(extra or {})}
        self._stop = threading.Event()
        self._thread = None

    # ---------------------------------------------------------------- 대기표 쓰기/박동
    def _write(self):
        self.info["heartbeat"] = time.time()
        write_json_atomic(self.path, self.info)

    def _beat(self):
        while not self._stop.wait(HEARTBEAT_SEC):
            try:
                self._write()   # 없어졌으면(잘못 정리됨) 같은 이름으로 다시 만들어 순서를 유지
            except OSError:
                pass

    def __enter__(self):
        os.makedirs(self.queue_dir, exist_ok=True)
        self._write()
        self._thread = threading.Thread(target=self._beat, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._stop.set()
        try:
            os.remove(self.path)
        except OSError:
            pass
        return False

    def set_state(self, state, **fields):
        self.info.update(state=state, **fields)
        try:
            self._write()
        except OSError:
            pass

    # ---------------------------------------------------------------- 대기열 읽기
    def live_tickets(self):
        """살아 있는 대기표 이름 목록(도착순). 죽은 대기표는 여기서 지운다."""
        names = []
        now = time.time()
        for name in sorted(os.listdir(self.queue_dir)):
            if not name.endswith(".json"):
                continue
            path = os.path.join(self.queue_dir, name)
            if path == self.path:
                names.append(name)
                continue
            try:
                with open(path, encoding="utf-8") as f:
                    info = json.load(f)
            except (OSError, json.JSONDecodeError):
                continue   # 지금 막 쓰는 중이면 다음 주기에 다시 읽음
            beat = info.get("heartbeat") or os.path.getmtime(path)
            reason = None
            if not owner_alive(info):
                reason = "프로세스 종료"
            elif now - beat > self.stale_sec:
                reason = f"{int(now - beat)}초 동안 응답 없음"
            if reason:
                try:
                    os.remove(path)
                    self.log(f"[A/B 대기열] 끝난 요청의 대기표를 정리했습니다 ({reason}): {name}")
                except OSError:
                    pass
                continue
            names.append(name)
        return names

    def wait_turn(self):
        """앞선 요청 수가 limit보다 적어질 때까지 기다린다. 반환: 기다린 초."""
        t0 = time.time()
        last_pos = None
        while True:
            names = self.live_tickets()
            if os.path.basename(self.path) not in names:
                self._write()   # 다른 요청이 잘못 지웠으면 다시 등록
                continue
            pos = names.index(os.path.basename(self.path))   # 내 앞에 있는 요청 수
            if pos < self.limit:
                self.set_state("running", started_at=datetime.datetime.now().isoformat(timespec="seconds"))
                if last_pos is not None:
                    self.log(f"[A/B 대기열] 내 차례입니다 ({time.time() - t0:.0f}초 대기).")
                return time.time() - t0
            if pos != last_pos:
                self.log(f"[A/B 대기열] 동시에 {self.limit}개까지만 비교합니다. 앞에 {pos}개 요청이 있어 기다립니다... "
                         f"(대기표: {os.path.relpath(self.path)})")
                last_pos = pos
            time.sleep(self.poll_sec)
