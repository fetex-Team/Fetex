import os

# 1. multi_factor_compare.py 에 OMP 환경변수 추가
path1 = 'multi_factor_compare.py'
with open(path1, 'r', encoding='utf-8') as f:
    text1 = f.read()

target1 = 'env["PYTHONHASHSEED"] = "0"'
replacement1 = '''env["PYTHONHASHSEED"] = "0"
    # Windows 서브프로세스 동시 실행 시 XGBoost/OpenMP DLL 초기화 충돌(WinError 1114) 방지
    env["OMP_NUM_THREADS"] = "1"
    env["KMP_DUPLICATE_LIB_OK"] = "TRUE"
    env["OPENBLAS_NUM_THREADS"] = "1"
    env["MKL_NUM_THREADS"] = "1"'''

if 'OMP_NUM_THREADS' not in text1:
    assert target1 in text1, "target1 not found"
    text1 = text1.replace(target1, replacement1, 1)
    with open(path1, 'w', encoding='utf-8') as f:
        f.write(text1)
    print("Updated multi_factor_compare.py with OMP environment variables!")
else:
    print("OMP_NUM_THREADS already in multi_factor_compare.py")

# 2. forecast_repositioner.py 에 DLL 로드 재시도 보호막 추가
path2 = os.path.join('module4_dispatch', 'forecast_repositioner.py')
with open(path2, 'r', encoding='utf-8') as f:
    text2 = f.read()

target2 = '        art = joblib.load(model_path)'
replacement2 = '''        # Windows 다중 서브프로세스 실행 시 OpenMP DLL Loader Lock(WinError 1114) 완화를 위한 재시도 루프
        art = None
        last_err = None
        import time
        for retry in range(4):
            try:
                art = joblib.load(model_path)
                break
            except Exception as e:
                last_err = e
                time.sleep(0.3 * (retry + 1))
        if art is None:
            raise RuntimeError(f"demand_v2 모델 로드 실패: {last_err}")'''

if 'for retry in range(4):' not in text2:
    assert target2 in text2, "target2 not found"
    text2 = text2.replace(target2, replacement2, 1)
    with open(path2, 'w', encoding='utf-8') as f:
        f.write(text2)
    print("Updated forecast_repositioner.py with retry mechanism!")
else:
    print("Retry mechanism already in forecast_repositioner.py")
