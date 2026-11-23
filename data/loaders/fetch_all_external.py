"""
외부 공공데이터 한 번에 받기 (경찰청 교차로 신호 계획 + 기상청 ASOS + 날씨 비교).

    python data/loaders/fetch_all_external.py

인증키 찾는 순서
  1) 환경변수 DATA_GO_KR_KEY
  2) secrets/data_go_kr_key.txt (비어 있지 않으면)
  3) 지금 클립보드 (공공데이터포털에서 키를 복사해 둔 경우, Windows)
  4) 화면에 붙여넣기 입력 (입력한 글자는 보이지 않음)
찾은 키는 secrets/data_go_kr_key.txt에 저장한다 (.gitignore로 커밋 제외).
"""
import getpass
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
KEY_PATH = os.path.join(ROOT, "secrets", "data_go_kr_key.txt")
KEY_RE = re.compile(r"[A-Za-z0-9%+/=]{40,}")


def clean(text):
    m = KEY_RE.search(text or "")
    return m.group(0) if m else None


def from_clipboard():
    if os.name != "nt":
        return None
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-Command", "Get-Clipboard"],
                             capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    return clean(out)


def get_key():
    key = clean(os.environ.get("DATA_GO_KR_KEY"))
    if not key and os.path.exists(KEY_PATH):
        with open(KEY_PATH, encoding="utf-8-sig", errors="ignore") as f:
            key = clean(f.read())
    if not key:
        key = from_clipboard()
        if key:
            print("[안내] 클립보드에서 인증키를 찾았습니다.")
    if not key:
        key = clean(getpass.getpass("공공데이터포털 일반 인증키를 붙여넣고 Enter (화면에 안 보여도 정상): "))
    if not key:
        sys.exit("[중단] 인증키를 찾지 못했습니다.")
    os.makedirs(os.path.dirname(KEY_PATH), exist_ok=True)
    with open(KEY_PATH, "w", encoding="utf-8") as f:
        f.write(key + "\n")
    print(f"[저장] 인증키 -> secrets/data_go_kr_key.txt ({len(key)}자, 커밋 제외)")
    return key


def run(args):
    print("\n$ python " + " ".join(args), flush=True)
    return subprocess.run([sys.executable, *args], cwd=ROOT).returncode


def main():
    os.environ["DATA_GO_KR_KEY"] = get_key()
    codes = [run(["data/loaders/fetch_signal_plans.py"]),
             run(["data/loaders/fetch_weather_asos.py", "2026-07-01", "2026-10-27"])]
    if codes[1] == 0:
        run(["module1_simulation/checks/compare_weather_sources.py"])
    print("\n[완료] 결과: data/external/signal_plans/, data/external/asos/")


if __name__ == "__main__":
    main()
