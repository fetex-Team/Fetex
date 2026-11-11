with open('multi_factor_compare.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

new_loop_lines = [
    '        # 2. 실행 중인 프로세스 완료 및 오류 실패 여부 점검\n',
    '        just_finished = []\n',
    '        for idx, (label, result_path, proc, st) in list(running.items()):\n',
    '            full_res = os.path.join(ROOT, result_path)\n',
    '            if os.path.exists(full_res):\n',
    '                elapsed = time.time() - st\n',
    '                try:\n',
    '                    with open(full_res, "r", encoding="utf-8") as _rf:\n',
    '                        _data = json.load(_rf)\n',
    '                except Exception:\n',
    '                    _data = {}\n',
    '\n',
    '                if _data.get("failed"):\n',
    '                    err_msg = _data.get("error_msg", "알 수 없는 오류")\n',
    '                    print(f"  ❌ [실패 감지] 조합 #{idx+1}/{total} \'{label}\' 오류로 중단! ({elapsed:.1f}초 경과 | 사유: {err_msg})")\n',
    '                else:\n',
    '                    print(f"  ✔ [완료] 조합 #{idx+1}/{total} \'{label}\' 완료! ({elapsed:.1f}초 소요)")\n',
    '\n',
    '                just_finished.append(idx)\n',
    '                finished.add(idx)\n',
    '\n',
    '            elif proc.poll() is not None:\n',
    '                time.sleep(1)\n',
    '                elapsed = time.time() - st\n',
    '                if os.path.exists(full_res):\n',
    '                    try:\n',
    '                        with open(full_res, "r", encoding="utf-8") as _rf:\n',
    '                            _data = json.load(_rf)\n',
    '                    except Exception:\n',
    '                        _data = {}\n',
    '                    if _data.get("failed"):\n',
    '                        err_msg = _data.get("error_msg", "알 수 없는 오류")\n',
    '                        print(f"  ❌ [실패 감지] 조합 #{idx+1}/{total} \'{label}\' 오류로 중단! ({elapsed:.1f}초 경과 | 사유: {err_msg})")\n',
    '                    else:\n',
    '                        print(f"  ✔ [완료] 조합 #{idx+1}/{total} \'{label}\' 완료! ({elapsed:.1f}초 소요)")\n',
    '                else:\n',
    '                    print(f"  ❌ [비정상 종료] 조합 #{idx+1}/{total} \'{label}\' 결과 파일 없이 프로세스 강제 종료됨! (exit code: {proc.returncode})")\n',
    '                    err_obj = {\n',
    '                        "label": label,\n',
    '                        "failed": True,\n',
    '                        "error_msg": f"프로세스 강제 종료 (exit={proc.returncode})",\n',
    '                        "true_avg_wait_sec_with_penalty": 999999.0,\n',
    '                    }\n',
    '                    try:\n',
    '                        with open(full_res, "w", encoding="utf-8") as _wf:\n',
    '                            json.dump(err_obj, _wf, ensure_ascii=False, indent=2)\n',
    '                    except Exception:\n',
    '                        pass\n',
    '                just_finished.append(idx)\n',
    '                finished.add(idx)\n',
]

# 교체: 345 (0-indexed)부터 362 (0-indexed)
lines = lines[:345] + new_loop_lines + lines[362:]

# compare 함수 교체
new_compare_lines = [
    '    print(f"\\n===== [최종 비교 - 병렬 실행, 전체 {len(result_paths)}개 조합] =====")\n',
    '    failed_items = [k for k, r in results.items() if r.get("failed")]\n',
    '    ranked = sorted(((v, k) for k, v in metrics.items() if v is not None and k not in failed_items))\n',
    '\n',
    '    if ranked:\n',
    '        best_v = ranked[0][0]\n',
    '        for rank, (v, k) in enumerate(ranked, 1):\n',
    '            diff_pct = (v - best_v) / best_v * 100 if best_v else 0\n',
    '            marker = " ← 최적" if rank == 1 else f" (+{diff_pct:.1f}%)"\n',
    '            print(f" {rank}위: {k} — 평균 대기(타임아웃 포함) {v}초{marker}")\n',
    '    else:\n',
    '        print(" - 정상 완료된 유효 조합이 없습니다.")\n',
    '\n',
    '    if failed_items:\n',
    '        print("\\n [실패한 조합 목록]")\n',
    '        for fk in failed_items:\n',
    '            fr = results[fk]\n',
    '            print(f"  ❌ {fk}: {fr.get(\'error_msg\', \'알 수 없는 오류\')}")\n',
]

# find "===== [최종 비교" line index
idx_comp = -1
for i, l in enumerate(lines):
    if '===== [최종 비교' in l or '===== [' in l and '최종' in l:
        idx_comp = i
        break

assert idx_comp != -1

# find end of compare print loop (before try: saved = save_ranking_text)
idx_comp_end = -1
for i in range(idx_comp, idx_comp + 25):
    if 'save_ranking_text' in lines[i]:
        idx_comp_end = i - 1
        break

assert idx_comp_end != -1

lines = lines[:idx_comp] + new_compare_lines + lines[idx_comp_end:]

with open('multi_factor_compare.py', 'w', encoding='utf-8') as f:
    f.writelines(lines)

print("Applied fail detection and fail reporting to multi_factor_compare.py!")
