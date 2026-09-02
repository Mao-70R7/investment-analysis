from __future__ import annotations

import runpy
from pathlib import Path


SCRIPT_PATH = Path(__file__).with_name("运行项目数据稽核hook.py")
runpy.run_path(str(SCRIPT_PATH), run_name="__main__")
