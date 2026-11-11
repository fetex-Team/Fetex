import os

path = 'multi_factor_compare.py'
with open(path, 'r', encoding='utf-8') as f:
    content = f.read()

# 1. run_queue_and_wait 루프 수정
old_loop_target = '''        # 2. 실행 중인 프로세스 완료 여부 점검
        just_finished = []
        for idx, (label, result_path, proc, st) in list(running.items()):
            full_res = os.path.join(ROOT, result_path)
            if os.path.exists(full_res):
                elapsed = time.time() - st
                print(f"  ✔ [완료] 조합 #{idx+1}/{total} '{label}' 완료! ({elapsed:.1f}초 소요)")
                just_finished.append(idx)
                finished.add(idx)
            elif proc.poll() is not None:
                time.sleep(1)
                if os.path.exists(full_res):
                    elapsed = time.time() - st
                    print(f"  ✔ [완료] 조합 #{idx+1}/{total} '{label}' 완료! ({elapsed:.1f}초 소요)")
                    just_finished.append(idx)
                    finished.add(idx)'''

new_loop_code = '''        # 2. 실행 중인 프로세스 완료 및 오류 실패 여부 점검
        just_finished = []
        for idx, (label, result_path, proc, st) in list(running.items()):
            full_res = os.path.join(ROOT, result_path)
            if os.path.exists(full_res):
                elapsed = time.time() - st
                try:
                    with open(full_res, "r", encoding="utf-8") as _rf:
                        _data = json.load(_rf)
                except Exception:
                    _data = {}

                if _data.get("failed"):
                    err_msg = _data.get("error_msg", "알 수 없는 오류")
                    print(f"  ❌ [실패 감지] 조합 #{idx+1}/{total} '{label}' 오류로 중단! ({elapsed:.1f}초 경과 | 사유: {err_msg})")
                else:
                    print(f"  ✔ [완료] 조합 #{idx+1}/{total} '{label}' 완료! ({elapsed:.1f}초 소요)")

                just_finished.append(idx)
                finished.add(idx)

            elif proc.poll() is not None:
                time.sleep(1)
                elapsed = time.time() - st
                if os.path.exists(full_res):
                    try:
                        with open(full_res, "r", encoding="utf-8") as _rf:
                            _data = json.load(_rf)
                    except Exception:
                        _data = {}
                    if _data.get("failed"):
                        err_msg = _data.get("error_msg", "알 수 없는 오류")
                        print(f"  ❌ [실패 감지] 조합 #{idx+1}/{total} '{label}' 오류로 중단! ({elapsed:.1f}초 경과 | 사유: {err_msg})")
                    else:
                        print(f"  ✔ [완료] 조합 #{idx+1}/{total} '{label}' 완료! ({elapsed:.1f}초 소요)")
                else:
                    # 결과 파일도 남기지 못하고 비정상 종료된 경우
                    print(f"  ❌ [비정상 종료] 조합 #{idx+1}/{total} '{label}' 프로세스가 에러로 강제 종료되었습니다! (exit code: {proc.returncode})")
                    err_obj = {
                        "label": label,
                        "failed": True,
                        "error_msg": f"프로세스 강제 종료 (exit={proc.returncode})",
                        "true_avg_wait_sec_with_penalty": 999999.0
                    }
                    try:
                        with open(full_res, "w", encoding="utf-8") as _wf:
                            json.dump(err_obj, _wf, ensure_ascii=False, indent=2)
                    except Exception:
                        pass
                just_finished.append(idx)
                finished.add(idx)'''

if "실패 감지" not in content:
    assert old_loop_target in content, "old_loop_target not found in multi_factor_compare.py"
    content = content.replace(old_loop_target, new_loop_code, 1)
    print("Updated run_queue_and_wait with fail detection!")
else:
    print("Fail detection already in run_queue_and_wait")

# 2. compare 함수에서 실패한 조합 분리 및 순위 표시
old_compare_target = '''    print(f"\\n===== [최종 비교 - 병렬 실행, 전체 {len(result_paths)}개 조합] =====")
    ranked = sorted(((v, k) for k, v in metrics.items() if v is not None))
    if not ranked:
        print(" - 유효 측정값이 있는 조합이 (아직) 없습니다. 개별/워커 출력을 확인하세요.")
        return

    best_v = ranked[0][0]
    for rank, (v, k) in enumerate(ranked, 1):
        diff_pct = (v - best_v) / best_v * 100 if best_v else 0
        marker = " ← 최적" if rank == 1 else f" (+{diff_pct:.1f}%)"
        print(f" {rank}위: {k} — 평균 대기(타임아웃 포함) {v}초{marker}")'''

new_compare_code = '''    print(f"\\n===== [최종 비교 - 병렬 실행, 전체 {len(result_paths)}개 조합] =====")
    failed_items = [k for k, r in results.items() if r.get("failed")]
    ranked = sorted(((v, k) for k, v in metrics.items() if v is not None and k not in failed_items))

    if ranked:
        best_v = ranked[0][0]
        for rank, (v, k) in enumerate(ranked, 1):
            diff_pct = (v - best_v) / best_v * 100 if best_v else 0
            marker = " ← 최적" if rank == 1 else f" (+{diff_pct:.1f}%)"
            print(f" {rank}위: {k} — 평균 대기(타임아웃 포함) {v}초{marker}")
    else:
        print(" - 정상 완료된 유효 조합이 없습니다.")

    if failed_items:
        print("\\n [실패한 조합 목록]")
        for fk in failed_items:
            fr = results[fk]
            print(f"  ❌ {fk}: {fr.get('error_msg', '알 수 없는 오류')}")'''

if "failed_items = [k for k, r in results.items()" not in content:
    assert old_compare_target in content, "old_compare_target not found in multi_factor_compare.py"
    content = content.replace(old_compare_target, new_compare_code, 1)
    print("Updated compare function with fail handling!")
else:
    print("Fail handling already in compare function")

with open(path, 'w', encoding='utf-8') as f:
    f.write(content)

print("Finished patching multi_factor_compare.py successfully!")
