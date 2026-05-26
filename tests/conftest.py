"""pytest 根 conftest — 统一 src/ 路径配置。"""
from __future__ import annotations

import os
import sys

_SRC_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src"))
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)
