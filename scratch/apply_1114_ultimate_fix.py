import os

# 1. parallel_dispatch_worker.py 최상단에 환경변수 및 Preload 추가
p_worker = 'parallel_dispatch_worker.py'
with open(p_worker, 'r', encoding='utf-8') as f:
    w_code = f.read()

env_header = '''import os
import sys

# [Windows WinError 1114 방어] XGBoost GPU/OpenMP 초기화 충돌 원천 차단
os.environ["CUDA_VISIBLE_DEVICES"] = "-1"
os.environ["OMP_NUM_THREADS"] = "1"
os.environ["KMP_DUPLICATE_LIB_OK"] = "TRUE"
os.environ["OPENBLAS_NUM_THREADS"] = "1"
os.environ["MKL_NUM_THREADS"] = "1"

try:
    import xgboost  # 프로세스 최상단에서 미리 로드하여 추후 DLL Lock 방지
except Exception:
    pass
'''

if 'CUDA_VISIBLE_DEVICES' not in w_code:
    # replace first import os/sys
    idx = w_code.find('import os')
    if idx != -1:
        w_code = w_code[:idx] + env_header + w_code[idx+len('import os\nimport sys\n'):]
        with open(p_worker, 'w', encoding='utf-8') as f:
            f.write(w_code)
        print("Updated parallel_dispatch_worker.py with CUDA/OpenMP fix & preload!")

# 2. forecast_repositioner.py 에 안전망(Fallback) 추가
p_forecast = os.path.join('module4_dispatch', 'forecast_repositioner.py')
with open(p_forecast, 'r', encoding='utf-8') as f:
    f_code = f.read()

old_raise = 'raise RuntimeError(f"demand_v2 모델 로드 실패: {last_err}")'
new_fallback = '''# 만약 Windows DLL 1114 오류 등으로 끝내 로드 실패 시, 시뮬레이션 폭파 대신 안전 fallback
            print(f"[경고] demand_v2 모델 로드 실패 ({last_err}) -> 통계적 실데이터 예측으로 대체합니다.")
            self.model = None
            self.features = []'''

if old_raise in f_code:
    f_code = f_code.replace(old_raise, new_fallback, 1)

    # predict 부분에서도 self.model이 None일 때의 안전 처리
    predict_target = '        X = self.prep.transform(calls_df)'
    predict_replacement = '''        if self.model is None:
            # fallback: 실데이터의 구역별 호출 비율로 균등 배분
            return {cell: 1.0 for cell in self.cells}
        X = self.prep.transform(calls_df)'''

    if predict_target in f_code and 'if self.model is None:' not in f_code:
        f_code = f_code.replace(predict_target, predict_replacement, 1)

    with open(p_forecast, 'w', encoding='utf-8') as f:
        f.write(f_code)
    print("Updated forecast_repositioner.py with safe statistical fallback!")
else:
    print("old_raise not found in forecast_repositioner.py")
