"""
custom_dispatchers.py 연결 패치 (프로젝트 루트 = ai_mobillit12 에서 실행).

    python apply_custom_dispatch_patch.py

- 먼저 module4_dispatch\\custom_dispatchers.py 를 넣어 두세요.
- 수정 전 원본은 _backup_custom_dispatch\\ 에 복사됩니다. 여러 번 실행해도 안전(이미 적용된 곳은 건너뜀).
- CRLF/LF 줄바꿈은 파일별로 그대로 유지합니다.
- 새 알고리즘 이름: custom_greedy, custom_routeExtension
"""
import os, shutil, sys

ROOT = os.path.dirname(os.path.abspath(__file__))
BAK = os.path.join(ROOT, "_backup_custom_dispatch")

OLD_LIST = '["greedy", "routeExtension", "hungarian"]'
NEW_LIST = '["greedy", "routeExtension", "hungarian", "custom_greedy", "custom_routeExtension"]'

EDITS = {
    "module1_simulation/build_env.py": [
        ('if _dispatch_algo not in ("greedy", "routeExtension", "hungarian"):',
         'if _dispatch_algo not in ("greedy", "routeExtension", "hungarian", "custom_greedy", "custom_routeExtension"):'),
        ('greedy/routeExtension/hungarian 중에서 선택하세요.',
         'greedy/routeExtension/hungarian/custom_greedy/custom_routeExtension 중에서 선택하세요.'),
        ('_sumo_dispatch_value = "traci" if _dispatch_algo == "hungarian" else _dispatch_algo',
         '_sumo_dispatch_value = "traci" if _dispatch_algo in ("hungarian", "custom_greedy", "custom_routeExtension") else _dispatch_algo'),
    ],
    "multi_factor_compare.py": [
        ('DISPATCH_SUMO_VALUE = {"hungarian": "traci"}',
         'DISPATCH_SUMO_VALUE = {"hungarian": "traci", "custom_greedy": "traci", "custom_routeExtension": "traci"}'),
        ('return "traci" if algo == "hungarian" else algo',
         'return "traci" if algo in DISPATCH_SUMO_VALUE else algo'),
    ],
    "parallel_dispatch_orchestrator.py": [
        ('DISPATCH_SUMO_VALUE = {"hungarian": "traci"}',
         'DISPATCH_SUMO_VALUE = {"hungarian": "traci", "custom_greedy": "traci", "custom_routeExtension": "traci"}'),
        ('return "traci" if algo == "hungarian" else algo',
         'return "traci" if algo in DISPATCH_SUMO_VALUE else algo'),
    ],
    "config_gui.py": [
        ('"배차 알고리즘 (체크)", ' + OLD_LIST, '"배차 알고리즘 (체크)", ' + NEW_LIST),
        ('"SUMO taxi dispatch algorithm", ' + OLD_LIST, '"SUMO taxi dispatch algorithm", ' + NEW_LIST),
    ],
    "measure_wait_time.py": [
        ('from module4_dispatch.hungarian_dispatcher import HungarianDispatcher',
         'from module4_dispatch.hungarian_dispatcher import HungarianDispatcher\n'
         'from module4_dispatch.custom_dispatchers import make_dispatcher'),
        ('hungarian_dispatcher = HungarianDispatcher() if dispatch_algo == "hungarian" else None',
         'hungarian_dispatcher = make_dispatcher(dispatch_algo)  # hungarian / custom_greedy / custom_routeExtension (변수명은 호환 위해 유지)'),
    ],
    "module1_simulation/run_simulation.py": [
        ('from module4_dispatch.hungarian_dispatcher import HungarianDispatcher',
         'from module4_dispatch.hungarian_dispatcher import HungarianDispatcher\n'
         'from module4_dispatch.custom_dispatchers import make_dispatcher'),
        ('HungarianDispatcher() if meta.get("taxi_dispatch_algorithm") == "hungarian" else None',
         'make_dispatcher(meta.get("taxi_dispatch_algorithm"))'),
    ],
    "module4_dispatch/rl_env.py": [
        ('from module4_dispatch.hungarian_dispatcher import HungarianDispatcher',
         'from module4_dispatch.hungarian_dispatcher import HungarianDispatcher\n'
         'from module4_dispatch.custom_dispatchers import make_dispatcher'),
        ('self.dispatcher = HungarianDispatcher() if dispatch_algo == "hungarian" else None',
         'self.dispatcher = make_dispatcher(dispatch_algo)'),
    ],
    "analyze_demand_logs.py": [
        ('(?P<algo>greedy|routeExtension|hungarian)',
         '(?P<algo>custom_greedy|custom_routeExtension|greedy|routeExtension|hungarian)'),
    ],
}


def patch_file(rel, edits):
    path = os.path.join(ROOT, *rel.split("/"))
    if not os.path.exists(path):
        # 폴더 구조가 다르면 파일명으로 한 번 더 찾아본다
        name = os.path.basename(rel)
        for dp, _, fs in os.walk(ROOT):
            if name in fs and "_backup" not in dp and "backup" not in dp.lower():
                path = os.path.join(dp, name); break
        else:
            print(f"[없음]  {rel}"); return False
    with open(path, "r", encoding="utf-8", newline="") as f:
        raw = f.read()
    crlf = "\r\n" in raw
    text = raw.replace("\r\n", "\n")
    changed, ok = 0, True
    for old, new in edits:
        if new in text and old not in text:
            continue                       # 이미 적용됨
        n = text.count(old)
        if n == 0:
            print(f"[실패]  {rel}: 못 찾음 -> {old[:60]!r}"); ok = False; continue
        text = text.replace(old, new); changed += n
    if changed:
        os.makedirs(os.path.join(BAK, os.path.dirname(rel)), exist_ok=True)
        shutil.copy2(path, os.path.join(BAK, rel))
        with open(path, "w", encoding="utf-8", newline="") as f:
            f.write(text.replace("\n", "\r\n") if crlf else text)
        print(f"[수정]  {rel}  ({changed}곳)")
    elif ok:
        print(f"[유지]  {rel}  (이미 적용됨)")
    return ok


def main():
    if not os.path.exists(os.path.join(ROOT, "module4_dispatch", "custom_dispatchers.py")):
        sys.exit("module4_dispatch\\custom_dispatchers.py 를 먼저 넣어 주세요.")
    results = [patch_file(rel, e) for rel, e in EDITS.items()]
    print("\n완료." if all(results) else "\n일부 실패 — 위 [실패]/[없음] 항목은 수동 확인이 필요합니다.")


if __name__ == "__main__":
    main()
