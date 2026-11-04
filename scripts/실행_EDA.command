#!/bin/bash
# [Module 2] EDA 일괄 실행: H3 비교 → POI 지도 → EDA 노트북 실행(결과 포함 저장). 시뮬 재실행 없음.
cd "$(dirname "$0")"
for V in venv .venv ../20250907ver/venv ../../20250907ver/venv; do [ -f "$V/bin/activate" ] && { source "$V/bin/activate"; break; }; done
export KMP_DUPLICATE_LIB_OK=TRUE OMP_NUM_THREADS=1
python -c "import holidays, statsmodels, nbconvert, ipykernel" 2>/dev/null || pip install -q holidays statsmodels jupyter nbconvert ipykernel
python eda/h3_resolution_compare.py || exit 1
python eda/poi_zone_map.py || exit 1
echo; echo "===== EDA 노트북 실행 (notebooks/01_EDA_and_Spatial.ipynb) ====="
jupyter nbconvert --to notebook --execute --inplace --ExecutePreprocessor.timeout=600 notebooks/01_EDA_and_Spatial.ipynb \
  && echo "노트북 실행 완료 (결과가 노트북 안에 저장됨)" || echo "노트북 실행 실패 — 위 에러 확인"
open data/eda/poi_zone_map.html 2>/dev/null
echo; read -p "완료. 엔터를 누르면 닫힘"
