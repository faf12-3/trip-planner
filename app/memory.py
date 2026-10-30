"""用户隔离与偏好记忆。

两个问题一起解决
----------------
"记忆"如果不按用户隔离，就没有意义——张三的偏好会让李四的行程变样。
所以先做**分区存储**（每个用户一个目录），再做**偏好画像**（每个用户一份 JSON）。

隔离靠什么
----------
1. 所有读写都经过 ``check_user``：只允许 ``[A-Za-z0-9_-]{3,40}``，防路径穿越；
2. 目录是 ``<root>/<user>/``，行程 id 仍走原有校验；
3. ``get``/``delete`` 带上 user，别人的 id 直接 ``TripNotFound``——不是"查不到就返回空"，是当作不存在。

记忆怎么记
----------
不做向量库：这个量级（偏好 / 档位 / 交通 / 城市）是**计数**，不是检索。
每成功规划一次就累加一次计数，取最高频的几个当"画像"，注入提示词。
缺点写在 docs\\需求澄清-... 里：它记不住"哪家酒店住过"，只记得"你常选什么档位"。
"""

from __future__ import annotations

from app.paths import data_path  # noqa: E402
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from app.store import TripStore

USER_RE = re.compile(r"^[A-Za-z0-9_-]{3,40}$")
DEFAULT_USER = "anonymous"
PROFILE_ROOT = data_path("profiles")

# 只记这几类"下次能直接用"的偏好；行程内容本身不记（那是 trips 的事）
FIELDS = ("preferences", "budget_level", "transportation", "accommodation", "city")


class InvalidUserId(ValueError):
    """user_id 不合法（防路径穿越）。"""


def check_user(user: str | None) -> str:
    """空值归到 anonymous；其余必须匹配白名单。"""
    if user is None or not str(user).strip():
        return DEFAULT_USER
    user = str(user).strip()
    if not USER_RE.match(user):
        raise InvalidUserId(f"非法的 user_id: {user!r}（只允许字母数字下划线短横，3~40 位）")
    return user


class UserTripStore(TripStore):
    """按用户分区的行程存储：``<root>/<user>/<trip_id>.json``。

    兼容旧数据：根目录下的平铺文件视为 ``anonymous`` 的行程。
    """

    def _dir(self, user: str) -> Path:
        return self.root / check_user(user)

    def _path_for(self, user: str, trip_id: str) -> Path:
        from app.store import check_id

        return self._dir(user) / f"{check_id(trip_id)}.json"

    # ------------------------------------------------------------ 写 --
    def save(
        self,
        plan: dict[str, Any],
        *,
        title: str = "",
        trip_id: str | None = None,
        markdown: str = "",
        user: str = DEFAULT_USER,
    ) -> dict[str, Any]:
        user = check_user(user)
        self._dir(user).mkdir(parents=True, exist_ok=True)

        now = datetime.now().astimezone().isoformat(timespec="seconds")
        if trip_id:
            path = self._path_for(user, trip_id)
            record = self._read(path) if path.exists() else {"id": trip_id, "created_at": now}
        else:
            from app.store import new_id

            trip_id = new_id()
            path = self._path_for(user, trip_id)
            record = {"id": trip_id, "created_at": now}

        record.update(
            {
                "id": trip_id,
                "user": user,
                "title": (title or record.get("title") or "").strip(),
                "updated_at": now,
                "plan": plan,
                "markdown": markdown,
            }
        )

        payload = json.dumps(record, ensure_ascii=False, indent=2)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(payload, encoding="utf-8")
        tmp.replace(path)
        return self._meta(record)

    # ------------------------------------------------------------ 读 --
    def list(self, limit: int = 50, *, user: str = DEFAULT_USER) -> list[dict[str, Any]]:
        user = check_user(user)
        entries: list[tuple[int, dict[str, Any]]] = []

        for directory, is_legacy in ((self._dir(user), False), (self.root, True)):
            if not directory.exists():
                continue
            for path in directory.glob("*.json"):
                if path.name.endswith(".plan.json"):
                    continue
                # 根目录只认"平铺的旧文件"，不认子目录（否则会把别人的行程算进来）
                if is_legacy and path.parent != self.root:
                    continue
                if is_legacy and user != DEFAULT_USER:
                    continue
                try:
                    record = self._read(path)
                    mtime = path.stat().st_mtime_ns
                except (OSError, json.JSONDecodeError):
                    continue
                entries.append((mtime, self._meta(record)))

        entries.sort(key=lambda pair: pair[0], reverse=True)
        return [meta for _, meta in entries[:limit]]

    def get(self, trip_id: str, *, user: str = DEFAULT_USER) -> dict[str, Any]:
        from app.store import TripNotFound

        path = self._path_for(user, trip_id)
        if not path.exists():
            # 旧数据：根目录下的平铺文件算 anonymous
            legacy = self.root / f"{check_user(trip_id) if False else trip_id}.json"
            if check_user(user) == DEFAULT_USER and legacy.exists():
                return self._read(legacy)
            raise TripNotFound(trip_id)
        return self._read(path)

    def delete(self, trip_id: str, *, user: str = DEFAULT_USER) -> bool:
        path = self._path_for(user, trip_id)
        if path.exists():
            path.unlink()
            return True
        legacy = self.root / f"{trip_id}.json"
        if check_user(user) == DEFAULT_USER and legacy.exists():
            legacy.unlink()
            return True
        return False


class ProfileStore:
    """偏好画像：每个用户一份计数型 JSON。"""

    def __init__(self, root: Path | str | None = None) -> None:
        self.root = Path(root) if root else PROFILE_ROOT

    def _path(self, user: str) -> Path:
        return self.root / f"{check_user(user)}.json"

    def load(self, user: str) -> dict[str, Any]:
        try:
            data = json.loads(self._path(user).read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}

    def record(self, user: str, request: Any, plan: Any) -> dict[str, Any]:
        """一次成功规划之后累加计数。request/plan 可以是对象或 dict。"""
        profile = self.load(user)
        counts: dict[str, dict[str, int]] = profile.setdefault("counts", {})
        trips = int(profile.get("trips", 0)) + 1

        def bump(field: str, value: Any) -> None:
            value = str(value or "").strip()
            if not value:
                return
            bucket = counts.setdefault(field, {})
            bucket[value] = bucket.get(value, 0) + 1

        def pick(obj: Any, key: str) -> Any:
            return obj.get(key) if isinstance(obj, dict) else getattr(obj, key, None)

        for field in FIELDS:
            bump(field, pick(request, field))

        # 城市也记一次"实际规划的城市"（模型可能改了写法）
        days = pick(plan, "days") or []
        if days:
            bump("city", pick(plan, "city"))

        profile.update(
            {
                "user": check_user(user),
                "trips": trips,
                "counts": counts,
                "updated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            }
        )
        self.root.mkdir(parents=True, exist_ok=True)
        path = self._path(user)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(profile, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)
        return profile

    def forget_hint(self, user: str, field: str, value: str) -> bool:
        """删掉画像里的某个值（如 preferences 下的「历史文化」）。

        返回是否真的删掉了——前端据此决定提示"已删除"还是"没找到"。
        """
        profile = self.load(user)
        counts = profile.get("counts") or {}
        bucket = counts.get(field)
        if not bucket or value not in bucket:
            return False
        del bucket[value]
        if not bucket:
            counts.pop(field, None)
        profile["counts"] = counts
        profile["updated_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
        self._write(user, profile)
        return True

    def forget_all(self, user: str) -> bool:
        """整份画像清空（用户可能想"重新开始"）。"""
        path = self._path(user)
        if not path.exists():
            return False
        path.unlink()
        return True

    def _write(self, user: str, profile: dict[str, Any]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        path = self._path(user)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(profile, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)

    def hints(self, user: str, *, top: int = 2) -> dict[str, list[str]]:
        """取每个字段出现最多的几项，用于注入提示词。"""
        profile = self.load(user)
        counts: dict[str, dict[str, int]] = profile.get("counts") or {}
        out: dict[str, list[str]] = {}
        for field, bucket in counts.items():
            ranked = sorted(bucket.items(), key=lambda kv: (-kv[1], kv[0]))
            values = [name for name, n in ranked[:top] if n >= 1]
            if values:
                out[field] = values
        return out

    def summary(self, user: str) -> dict[str, Any]:
        """给前端看的画像摘要。"""
        profile = self.load(user)
        return {
            "trips": profile.get("trips", 0),
            "hints": self.hints(user),
            "updated_at": profile.get("updated_at", ""),
        }


def memory_note(hints: dict[str, list[str]]) -> str:
    """把画像拼成一句给模型看的话。空画像返回空串（不污染提示词）。"""
    if not hints:
        return ""
    parts = []
    if hints.get("preferences"):
        parts.append("偏好类型：" + "、".join(hints["preferences"]))
    if hints.get("budget_level"):
        parts.append("预算档位：" + "、".join(hints["budget_level"]))
    if hints.get("transportation"):
        parts.append("常用交通：" + "、".join(hints["transportation"]))
    if hints.get("accommodation"):
        parts.append("住宿倾向：" + "、".join(hints["accommodation"]))
    if hints.get("city"):
        parts.append("常去城市：" + "、".join(hints["city"]))
    if not parts:
        return ""
    return "该用户的历史偏好（来自过去的行程，可参考但不要盲从）：" + "；".join(parts) + "。"

