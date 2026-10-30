"""Vercel 入口：把 FastAPI 的 ASGI app 暴露出去。

Vercel 的 Python runtime 会找 `api/*.py` 里的 `app`（或 `handler`）。
这里只是把 `app.api:app` 换到根路径可导入的位置，不改业务代码。

注意：Vercel 的文件系统除 /tmp 外只读，所以必须把 TRIP_DATA_DIR 指到 /tmp，
否则保存行程会失败（该项目用文件存储，没有数据库）。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# 把项目根加入 sys.path，才能 `from app.api import app`
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# 部署环境下数据目录必须可写
os.environ.setdefault("TRIP_DATA_DIR", "/tmp/trip-planner")

from app.api import app  # noqa: E402

__all__ = ["app"]
