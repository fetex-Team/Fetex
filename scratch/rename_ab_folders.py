"""
1. 아까 만든 임시 별칭 폴더(20261104_... 형태, info.txt만 있는 빈 껍데기) 삭제
2. ab_01793... 형태의 구버전 UUID 폴더들을 ab_ranking_생성일_시뮬날짜 형식으로 변경
"""
import os, re, shutil, datetime

ROOT = os.path.abspath(".")
AB_ROOT = os.path.join(ROOT, "ab_results")


def extract_dates_from_txt(folder_path):
    for fname in sorted(os.listdir(folder_path)):
        if fname.startswith("ab_ranking") and fname.endswith(".txt"):
            try:
                with open(os.path.join(folder_path, fname), "r", encoding="utf-8") as f:
                    content = f.read()
                m_created = re.search(r"실행 시각\s*:\s*(\d{4}-\d{2}-\d{2})\s+(\d{2}:\d{2})", content)
                m_sim = re.search(r"시뮬 날짜\s*:\s*(\d{4}-\d{2}-\d{2})", content)
                created_str = None
                sim_str = None
                if m_created:
                    date_part = m_created.group(1).replace("-", "")
                    time_part = m_created.group(2).replace(":", "")
                    created_str = f"{date_part}_{time_part}"
                if m_sim:
                    sim_str = m_sim.group(1).replace("-", "")
                return created_str, sim_str
            except Exception:
                pass
    return None, None


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


deleted = 0
renamed = 0

for name in sorted(os.listdir(AB_ROOT)):
    path = os.path.join(AB_ROOT, name)
    if not os.path.isdir(path):
        continue

    # --- 1. 임시 별칭 폴더 삭제 (yyyymmdd_hhmm_... 형태, 내부에 info.txt만 있는 것) ---
    # 패턴: 8자리숫자_4자리숫자_한글포함 (예: 20261104_1458_평일_RL1위_...)
    if re.match(r'^\d{8}_\d{4}_', name):
        contents = os.listdir(path)
        if contents == ["info.txt"] or contents == []:
            shutil.rmtree(path)
            print(f"[삭제] {name}")
            deleted += 1
        else:
            print(f"[삭제스킵-내용있음] {name} ({contents})")
        continue

    # --- 2. ab_01793... 형태 구버전 UUID 폴더 이름 변경 ---
    if re.match(r'^ab_\d{17,}', name):
        created_str, sim_str = extract_dates_from_txt(path)

        if not created_str:
            mtime = datetime.datetime.fromtimestamp(os.path.getmtime(path))
            created_str = mtime.strftime("%Y%m%d_%H%M")
            sim_str = "unknown"
            print(f"  [txt없음→mtime] {name}")

        base_new = f"ab_ranking_{created_str}_{sim_str}"
        new_name = unique_name(base_new)
        new_path = os.path.join(AB_ROOT, new_name)

        try:
            os.rename(path, new_path)
            print(f"[변경] {name}")
            print(f"    -> {new_name}")
            renamed += 1
        except PermissionError as e:
            print(f"[잠금] {name}: {e}")

print(f"\n완료: {deleted}개 임시폴더 삭제, {renamed}개 폴더 이름 변경")
