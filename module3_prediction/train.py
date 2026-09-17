"""
Module 3: AI 수요 예측 모델 학습 엔트리포인트
"""
import os
import sys
import runpy

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(CURRENT_DIR)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

if __name__ == '__main__':
    target = os.path.join(PROJECT_ROOT, "train.py")
    runpy.run_path(target, run_name="__main__")
