"""共用 pytest 設定 — 把專案根目錄加入 sys.path，方便 ``from ml.predict import ...``。"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
