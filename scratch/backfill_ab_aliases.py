"""
기존 ab_results/<session_id>/ 폴더에 소급해서 사람이 읽기 쉬운 별칭 폴더를 만듭니다.
"""
import os, json, datetime, re

ROOT = os.path.abspath(".")
AB_ROOT = os.path.join(ROOT, "ab_results")

STRATEGY_MAP = {
    "rl_prepositioned": "RL",
    "rl": "RL",
    "prepositioned": "고정배치",
    "forecast": "수요예측",
    "patrol": "순찰",
}


def clean_strategy(s: str) -> str:
    if s is None:
        return "unknown"
    for k, v in STRATEGY_MAP.items():
        if k in s:
            return v
    return s[:8]


def process_folder(folder_name):
    folder_path = os.path.join(AB_ROOT, folder_name)
    if not os.path.isdir(folder_path):
        return None

    # combo*.json 수집 (strategy 없는 경우 combo 인덱스로 대체)
    all_combos = []
    for fname in sorted(os.listdir(folder_path)):
        if fname.startswith("combo") and fname.endswith(".json"):
            try:
                with open(os.path.join(folder_path, fname), "r", encoding="utf-8") as f:
                    d = json.load(f)
                if not d.get("failed"):
                    all_combos.append(d)
            except Exception:
                pass

    if not all_combos:
        return None

    ranked = sorted(
        ((c.get("true_avg_wait_sec_with_penalty"), c) for c in all_combos
         if c.get("true_avg_wait_sec_with_penalty") is not None)
    )
    if not ranked:
        return None

    best_wait, best_combo = ranked[0]
    if best_wait is None:
        return None

    run_date = best_combo.get("run_date", "")
    is_holiday = best_combo.get("is_holiday", False)
    n_pax = best_combo.get("n_total_passengers", 0)
    day_type = "공휴일" if is_holiday else "평일"

    # 전략명: strategy 필드 or ab_ranking txt 파싱 or combo 순서 기반 추정
    strat_raw = best_combo.get("strategy", "")
    if not strat_raw:
        # Try to find from ab_ranking txt
        for fname in os.listdir(folder_path):
            if fname.startswith("ab_ranking") and fname.endswith(".txt"):
                try:
                    with open(os.path.join(folder_path, fname), "r", encoding="utf-8") as f:
                        content = f.read()
                    # Find first "1위" block
                    m = re.search(r'1위\s+(\S+)', content)
                    if m:
                        strat_raw = m.group(1)
                        break
                except Exception:
                    pass
    if not strat_raw:
        # Guess from index (combo0=patrol, combo1=prepositioned, combo2=rl, combo3=forecast historically)
        strat_raw = "unknown"

    strat_name = clean_strategy(strat_raw)

    mtime = os.path.getmtime(folder_path)
    dt = datetime.datetime.fromtimestamp(mtime)
    now_str = dt.strftime("%Y%m%d_%H%M")

    alias_name = f"{now_str}_{day_type}_{strat_name}1위_{best_wait:.0f}초_pax{n_pax}"
    for ch in r'\/:*?"<>|':
        alias_name = alias_name.replace(ch, "")

    # info.txt
    info_lines = [
        "=== A/B 비교 결과 요약 ===",
        f"실행 시각  : {dt.strftime('%Y-%m-%d %H:%M:%S')}",
        f"시뮬 날짜  : {run_date} ({day_type})",
        f"총 승객 수 : {n_pax}명",
        "",
        "[순위]",
    ]
    for rank, (v, c) in enumerate(ranked, 1):
        sk = clean_strategy(c.get("strategy", ""))
        marker = " ← 최적" if rank == 1 else (f" (+{(v - best_wait) / best_wait * 100:.1f}%)" if best_wait else "")
        info_lines.append(f"  {rank}위: {sk}  —  평균 대기 {v:.0f}초{marker}")
    info_lines += [
        "",
        "[상세 데이터 폴더]",
        f"  ab_results/{folder_name}/",
    ]
    info_text = "\n".join(info_lines) + "\n"

    # 기존 폴더 안에도 info.txt 저장
    with open(os.path.join(folder_path, "info.txt"), "w", encoding="utf-8") as f:
        f.write(info_text)

    return alias_name, info_text


processed = 0
created = 0

for name in sorted(os.listdir(AB_ROOT)):
    path = os.path.join(AB_ROOT, name)
    # 기존 UUID 형식 폴더만 대상 (숫자 17자리로 시작)
    if not os.path.isdir(path):
        continue
    if not (len(name) > 10 and name[0:17].isdigit()):
        continue

    result = process_folder(name)
    processed += 1
    if result is None:
        print(f"  [스킵-데이터없음] {name}")
        continue

    alias_name, info_text = result
    alias_path = os.path.join(AB_ROOT, alias_name)

    if os.path.exists(alias_path):
        print(f"  [이미존재] {alias_name}")
    else:
        os.makedirs(alias_path, exist_ok=True)
        with open(os.path.join(alias_path, "info.txt"), "w", encoding="utf-8") as f:
            f.write(info_text)
        print(f"  [생성] {alias_name}")
        created += 1

print(f"\n완료: 총 {processed}개 폴더 처리, {created}개 별칭 폴더 생성")
