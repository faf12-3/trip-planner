"""数据目录的唯一来源。

默认 `<项目>/data`，可用环境变量 `TRIP_DATA_DIR` 覆盖。
为什么要覆盖：Vercel 这类平台的文件系统除 /tmp 外只读，代码目录写不进去。
"""

from __future__ import annotations

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def data_root() -> Path:
    """返回数据根目录（不保证存在，调用方自己 mkdir）。"""
    override = os.environ.get("TRIP_DATA_DIR", "").strip()
    return Path(override) if override else PROJECT_ROOT / "data"


def data_path(*parts: str) -> Path:
    return data_root().joinpath(*parts)
