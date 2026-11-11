"""
ab_ranking_YYYYMMDD_HHMM_unknown 폴더들을 combo*.json의 run_date로 정확한 날짜로 수정합니다.
"""
import os, re, json

ROOT = os.path.abspath(".")
AB_ROOT = os.path.join(ROOT, "ab_results")


def get_run_date_from_combos(folder_path):
    """combo*.json에서 run_date를 추출해 YYYYMMDD 형식으로 반환."""
    for fname in sorted(os.listdir(folder_path)):
        if fname.startswith("combo") and fname.endswith(".json"):
            try:
                with open(os.path.join(folder_path, fname), "r", encoding="utf-8") as f:
                    d = json.load(f)
                run_date = d.get("run_date")
                if run_date:
                    return str(run_date).replace("-", "")
            except Exception:
                pass
    return None


def unique_name(base_name):
    path = os.path.join(AB_ROOT, base_name)
    if not os.path.exists(path):
        return base_name
    n = 2
    while True:
        candidate = f"{base_name}({n})"
        if not os.path.exists(os.path.join(AB_ROOT, candidate)):
            return candidate
        n += 1


fixed = 0
for name in sorted(os.listdir(AB_ROOT)):
    if not name.endswith("_unknown") and not re.search(r'_unknown\(\d+\)$', name):
        continue
    path = os.path.join(AB_ROOT, name)
    if not os.path.isdir(path):
        continue

    run_date = get_run_date_from_combos(path)
    if not run_date:
        print(f"[스킵-날짜없음] {name}")
        continue

    # ab_ranking_20261104_1120_unknown  ->  ab_ranking_20261104_1120_20261011
    new_name = re.sub(r'_unknown(\(\d+\))?$', f"_{run_date}", name)
    new_name = unique_name(new_name)
    new_path = os.path.join(AB_ROOT, new_name)

    try:
        os.rename(path, new_path)
        print(f"[수정] {name}")
        print(f"    -> {new_name}")
        fixed += 1
    except PermissionError as e:
        print(f"[잠금] {name}: {e}")

print(f"\n완료: {fixed}개 폴더 날짜 수정")
