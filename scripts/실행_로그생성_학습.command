#!/bin/bash
# 더블클릭 → 시뮬레이션(headless) 실행 → 호출 로그 CSV 저장 → 그 로그로 train.py 학습
cd "$(dirname "$0")"
for V in venv .venv ../20250907ver/venv ../../20250907ver/venv; do [ -f "$V/bin/activate" ] && { source "$V/bin/activate"; break; }; done
python -c "import h3, traci, sumolib, torch, xgboost" 2>/dev/null || { echo "venv를 못 찾았거나 패키지 부족. requirements.txt 설치 필요"; read -p "엔터를 누르면 닫힘"; exit 1; }
export KMP_DUPLICATE_LIB_OK=TRUE OMP_NUM_THREADS=1   # macOS torch+xgboost OpenMP 충돌(세그폴트) 방지
export SUMO_HOME="$(python -c 'import sumo, os; print(os.path.dirname(sumo.__file__))' 2>/dev/null || echo "$SUMO_HOME")"
echo "python: $(which python)"

echo; echo "===== [1/3] build_env (도로망/승객 생성) ====="
python module1_simulation/build_env.py || { read -p "build_env 실패. 엔터"; exit 1; }
echo; echo "===== [2/3] 시뮬레이션 실행 + 호출 로그 저장 ====="
python measure_wait_time.py || { read -p "시뮬레이션 실패. 엔터"; exit 1; }
ls -la data/sim_logs/
echo; echo "===== [3/3] 학습 (최신 로그 사용) ====="
python train.py
echo; read -p "완료. 엔터를 누르면 닫힘"
