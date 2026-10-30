"""分享链接：把一份行程以只读方式发给别人。

设计上的三条底线（每条都有对应测试）：
  1. **token 不可猜测**：`secrets.token_urlsafe(18)` 给 24 个 url-safe 字符（约 144 bit），
     不是行程 id，也不含用户名——否则别人能顺着改 URL 翻别人的行程。
  2. **只读**：分享 token 只能读，任何写接口都不接受它；拿到 token 也改不了东西。
  3. **可撤销**：撤销即删记录，之后 404，不需要"过期时间"这种兜底。

token 存成文件 `data/shares/<token>.json`，与行程分开：
行程删了分享自然失效（解析时找不到行程就 404），但分享记录不会因为删行程而丢权限信息。
"""

from __future__ import annotations

from app.paths import data_path  # noqa: E402
import json
import re
import secrets
from datetime import datetime
from pathlib import Path
from typing import Any

TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{16,64}$")
DEFAULT_ROOT = data_path("shares")


class InvalidToken(ValueError):
    """token 形状不合法（防路径穿越）。"""


def check_token(token: str) -> str:
    token = (token or "").strip()
    if not TOKEN_RE.match(token):
        raise InvalidToken(token)
    return token


class ShareStore:
    def __init__(self, root: Path | str | None = None) -> None:
        self.root = Path(root) if root else DEFAULT_ROOT

    def _path(self, token: str) -> Path:
        return self.root / f"{check_token(token)}.json"

    # ---------------------------------------------------------------- 写 --
    def create(self, *, user: str, trip_id: str, title: str = "") -> dict[str, Any]:
        """生成一个新 token。同一份行程可以有多个链接（方便分别发给不同的人）。"""
        token = secrets.token_urlsafe(18)
        record = {
            "token": token,
            "user": user,
            "trip_id": trip_id,
            "title": title,
            "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        }
        self.root.mkdir(parents=True, exist_ok=True)
        path = self._path(token)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)
        return record

    def revoke_trip(self, *, user: str, trip_id: str) -> int:
        """撤销这份行程的全部链接。只删自己的。"""
        if not self.root.exists():
            return 0
        removed = 0
        for path in self.root.glob("*.json"):
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if record.get("user") == user and record.get("trip_id") == trip_id:
                path.unlink(missing_ok=True)
                removed += 1
        return removed

    # ---------------------------------------------------------------- 读 --
    def resolve(self, token: str) -> dict[str, Any] | None:
        """按 token 找记录。形状不对或文件不存在都返回 None（不抛，免得多一个错误分支）。"""
        try:
            path = self._path(token)
        except InvalidToken:
            return None
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

    def list_for(self, *, user: str, trip_id: str) -> list[dict[str, Any]]:
        if not self.root.exists():
            return []
        out = []
        for path in self.root.glob("*.json"):
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if record.get("user") == user and record.get("trip_id") == trip_id:
                out.append(record)
        return sorted(out, key=lambda r: r.get("created_at", ""), reverse=True)
