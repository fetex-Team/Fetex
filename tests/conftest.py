"""pytest가 tests/ 안의 파일을 실행할 때 프로젝트 루트를 import 경로에 넣는다."""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
