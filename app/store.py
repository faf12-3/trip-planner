"""行程持久化：把行程存成 JSON 文件。

为什么用文件而不是数据库
------------------------
这个项目的量级是"几十份行程、单机自用"，SQLite 会带来一次迁移成本却换不到多少好处。
一台机器一个目录、一份 JSON、`git diff` 就能看出改了什么，反而更适合演示和排查。

目录结构
--------
    data/trips/
      <id>.json        # {meta..., plan, markdown}
      <id>.plan.json   # 冗余的纯行程，方便直接用别的工具读

id 只允许 ``[A-Za-z0-9_-]``，并且落盘前再校验一次——防路径穿越
（``../../.env`` 这种）。
"""

from __future__ import annotations

from app.paths import data_path  # noqa: E402
import json
import re
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

ID_RE = re.compile(r"^[A-Za-z0-9_-]{6,64}$")
# 默认 data/trips；部署时可用 TRIP_DATA_DIR 指到可写目录（如 /tmp/trip-planner）
DEFAULT_ROOT = data_path("trips")

# 「最新在前」用的单调序号。
# 不能用 time.time_ns()：Windows 的时钟粒度约 15ms，同一瞬间两次调用会返回同一个值，
# 排序就退化成任意顺序（这个 flaky 在测试里抖了三次才定位到）。
_last_seq = 0


def next_seq() -> int:
    """严格单调递增，跨调用不会重复。"""
    global _last_seq
    cur = time.time_ns()
    if cur <= _last_seq:
        cur = _last_seq + 1
    _last_seq = cur
    return cur


class TripNotFound(KeyError):
    """指定的行程不存在。"""


class InvalidTripId(ValueError):
    """id 不合法（防路径穿越）。"""


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def new_id(when: datetime | None = None) -> str:
    stamp = (when or datetime.now()).strftime("%Y%m%d-%H%M%S")
    return f"{stamp}-{uuid.uuid4().hex[:6]}"


def check_id(trip_id: str) -> str:
    if not isinstance(trip_id, str) or not ID_RE.match(trip_id):
        raise InvalidTripId(f"非法的行程 id: {trip_id!r}")
    return trip_id


class TripStore:
    def __init__(self, root: Path | str | None = None) -> None:
        self.root = Path(root) if root else DEFAULT_ROOT

    # ------------------------------------------------------------ 内部 --
    def _path(self, trip_id: str) -> Path:
        return self.root / f"{check_id(trip_id)}.json"

    def _meta(self, record: dict[str, Any]) -> dict[str, Any]:
        """列表里只返回元信息，避免一次拉全量行程。"""
        plan = record.get("plan") or {}
        days = plan.get("days") or []
        return {
            "id": record.get("id"),
            "title": record.get("title") or f"{plan.get('city', '')} {plan.get('start_date', '')}".strip(),
            "city": plan.get("city", ""),
            "start_date": plan.get("start_date", ""),
            "end_date": plan.get("end_date", ""),
            "days": len(days),
            "total": (plan.get("budget") or {}).get("total", 0),
            "created_at": record.get("created_at", ""),
            "updated_at": record.get("updated_at", ""),
        }

    # ------------------------------------------------------------ 接口 --
    def save(
        self,
        plan: dict[str, Any],
        *,
        title: str = "",
        trip_id: str | None = None,
        markdown: str = "",
    ) -> dict[str, Any]:
        """新建或覆盖保存；返回元信息。"""
        self.root.mkdir(parents=True, exist_ok=True)

        now = _now()
        if trip_id:
            path = self._path(trip_id)
            record = self._read(path) if path.exists() else {"id": trip_id, "created_at": now}
        else:
            trip_id = new_id()
            path = self._path(trip_id)
            record = {"id": trip_id, "created_at": now}

        record.update(
            {
                "id": trip_id,
                # 单调序号：用于"最新在前"排序。
                # 不用文件 mtime —— 两次保存可能落在同一时间片，mtime 相同；
                # 也不能拿 id 兜底 —— id 后半段是随机串，等于随机排序（抖动过两次）。
                "seq": next_seq(),
                "title": (title or record.get("title") or "").strip(),
                "updated_at": now,
                "plan": plan,
                "markdown": markdown,
            }
        )

        payload = json.dumps(record, ensure_ascii=False, indent=2)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(payload, encoding="utf-8")
        tmp.replace(path)  # 原子替换：写一半断电也不会留半份坏文件

        return self._meta(record)

    def list(self, limit: int = 50) -> list[dict[str, Any]]:
        if not self.root.exists():
            return []

        # 排序用文件 mtime（纳秒精度）而不是 updated_at（秒级）：
        # 同一秒内保存两份时，秒级时间戳会撞车，列表顺序就变成随机的。
        entries: list[tuple[int, dict[str, Any]]] = []
        for path in self.root.glob("*.json"):
            if path.name.endswith(".plan.json"):
                continue
            try:
                record = self._read(path)
                mtime = path.stat().st_mtime_ns
            except (OSError, json.JSONDecodeError, InvalidTripId):
                continue  # 坏文件跳过，不影响列表
            entries.append(((record.get("seq") or mtime), self._meta(record)))

        # 按 seq 倒序（旧数据没有 seq，退回 mtime）。不要再用 id 兜底：
        # id 后半段是随机串，mtime 相同时等于随机排序。
        entries.sort(key=lambda pair: pair[0], reverse=True)
        return [meta for _, meta in entries[:limit]]

    def get(self, trip_id: str) -> dict[str, Any]:
        path = self._path(trip_id)
        if not path.exists():
            raise TripNotFound(trip_id)
        return self._read(path)

    def delete(self, trip_id: str) -> bool:
        path = self._path(trip_id)
        if not path.exists():
            return False
        path.unlink()
        return True

    # ------------------------------------------------------------ 读取 --
    def _read(self, path: Path) -> dict[str, Any]:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise json.JSONDecodeError("不是对象", str(path), 0)
        return data
