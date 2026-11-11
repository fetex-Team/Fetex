import os, sys

# 1. PATH에 SUMO bin을 맨 앞에 넣은 상태에서 테스트
sumo_bin = r'C:\Program Files (x86)\Eclipse\Sumo\bin'
os.environ["PATH"] = sumo_bin + os.pathsep + os.environ.get("PATH", "")

print("Testing import xgboost with SUMO bin in PATH...")
try:
    import xgboost
    print("SUCCESS!")
except Exception as e:
    print("FAILED AS EXPECTED! Error:", type(e), e)
