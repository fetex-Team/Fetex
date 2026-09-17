#!/bin/bash
# data/sim_logs의 최신 로그로 train.py만 실행 (인자로 로그 경로 지정 가능)
cd "$(dirname "$0")"
for V in venv .venv ../20250907ver/venv ../../20250907ver/venv; do [ -f "$V/bin/activate" ] && { source "$V/bin/activate"; break; }; done
export KMP_DUPLICATE_LIB_OK=TRUE OMP_NUM_THREADS=1
python train.py "$@"
echo; read -p "완료. 엔터를 누르면 닫힘"
