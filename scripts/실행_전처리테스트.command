#!/bin/bash
# [Module 2] 최소 완료 조건 테스트 + 파이프라인 CLI로 피처 테이블 생성
cd "$(dirname "$0")"
for V in venv .venv ../20250907ver/venv ../../20250907ver/venv; do [ -f "$V/bin/activate" ] && { source "$V/bin/activate"; break; }; done
python -c "import holidays" 2>/dev/null || pip install -q holidays
echo "===== tests/test_module2.py ====="; python tests/test_module2.py || { read -p "테스트 실패. 엔터"; exit 1; }
echo; echo "===== 벤치마크 (10만 행) ====="; python -m module2_preprocessing.spatial_indexing --bench 100000
echo; echo "===== 파이프라인 CLI ====="; python -m module2_preprocessing.pipeline --logs "data/sim_logs/demand_log_*.csv" --out data/processed/features.csv
echo; read -p "완료. 엔터를 누르면 닫힘"
