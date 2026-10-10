"""수요 로그 분석기(demand_analyzer.html)를 띄우는 아주 작은 로컬 서버.

왜 필요한가: 브라우저는 보안상 HTML 파일이 컴퓨터의 폴더를 마음대로 뒤질 수 없어서
'파일 선택'을 매번 눌러야 했음. 이 서버가 data/sim_logs의 로그 목록과 도로망(grid.net.xml)을
브라우저에 넘겨주면, 분석기에서 체크박스로 고르기만 하면 됨.

사용: python analyzer_server.py          (브라우저가 자동으로 열림, 끝낼 때는 이 창에서 Ctrl+C)
      python analyzer_server.py --no-browser --port 8765
내 컴퓨터(127.0.0.1)에서만 접속되고, data/sim_logs의 demand_log_*.csv와 grid.net.xml만 내보냄.
"""
import argparse
import json
import os
import re
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlparse

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
from log_selection import LOG_DIR, parse_log_name  # noqa: E402

HTML = os.path.join(ROOT, "demand_analyzer.html")
NET_CANDIDATES = [
    os.path.join(ROOT, "module1_simulation", "sumo_config", "grid.net.xml"),
    os.path.join(ROOT, "module1_simulation", "sumo_config_combo0", "grid.net.xml"),
]
NAME_RE = re.compile(r"^demand_log_[^/\\]+\.csv$")


def list_logs():
    out = []
    if os.path.isdir(LOG_DIR):
        for fn in os.listdir(LOG_DIR):
            if not NAME_RE.match(fn):
                continue
            p = os.path.join(LOG_DIR, fn)
            if not os.path.isfile(p):
                continue
            d = parse_log_name(fn)["date"]
            out.append({"name": fn, "size": os.path.getsize(p), "mtime": os.path.getmtime(p),
                        "date": d.isoformat() if d else None})
    # 최신 날짜(또는 수정시각) 먼저
    out.sort(key=lambda r: (r["date"] or "", r["mtime"], r["name"]), reverse=True)
    return out


def find_net():
    for p in NET_CANDIDATES:
        if os.path.isfile(p):
            return p
    return None


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body: bytes, ctype="text/plain; charset=utf-8"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _file(self, path, ctype):
        try:
            with open(path, "rb") as f:
                self._send(200, f.read(), ctype)
        except OSError:
            self._send(404, "not found".encode())

    def do_GET(self):
        path = unquote(urlparse(self.path).path)
        if path in ("/", "/index.html", "/demand_analyzer.html"):
            return self._file(HTML, "text/html; charset=utf-8")
        if path == "/api/logs":
            return self._send(200, json.dumps(list_logs(), ensure_ascii=False).encode("utf-8"),
                              "application/json; charset=utf-8")
        if path.startswith("/logs/"):
            name = path[len("/logs/"):]
            if NAME_RE.match(name):  # 경로 이동(../) 차단: 파일명 형식만 허용
                return self._file(os.path.join(LOG_DIR, name), "text/csv; charset=utf-8")
            return self._send(400, "bad name".encode())
        if path == "/api/net":
            p = find_net()
            return self._file(p, "application/xml; charset=utf-8") if p else self._send(404, "no net".encode())
        self._send(404, "not found".encode())

    def log_message(self, *a):  # 콘솔 조용히
        pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-browser", action="store_true")
    a = ap.parse_args()
    if not os.path.isfile(HTML):
        print(f"[에러] {HTML} 가 없습니다. demand_analyzer.html을 프로젝트 폴더에 두세요.")
        return 1
    srv = None
    for port in range(a.port, a.port + 20):
        try:
            srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
            break
        except OSError:
            continue
    if srv is None:
        print("[에러] 사용 가능한 포트를 못 찾았습니다.")
        return 1
    url = f"http://127.0.0.1:{srv.server_address[1]}/"
    print(f"[분석기] {url}  (로그 {len(list_logs())}개, 도로망 {'있음' if find_net() else '없음 - 지도 배경 없이 점만 표시'})")
    print("         끝내려면 이 창에서 Ctrl+C 를 누르거나 창을 닫으세요.")
    if not a.no_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
