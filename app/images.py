"""景点配图：用 Unsplash 给行程里的景点补一张图。

只用一个 Key
------------
Unsplash 的 **Access Key** 用于服务端公开搜索（`client_id` 参数）。
**Secret Key 用不到**——它只在 OAuth 授权流程里用（替用户登录拿 token），
本项目不做用户授权，所以绝不把 Secret 写进代码或配置。

为什么要缓存
------------
Demo 应用配额是 **50 次/小时**。一次 3 天行程有 8~12 个景点，不缓存的话跑几次
就到顶了。这里按「城市 + 景点名」做磁盘缓存，第二次同城同景点直接命中。

失败不阻断
----------
Unsplash 在境外、需要代理，还可能 403（限流）。任何失败都只让 `image_url`
保持为 null——行程本身照常返回。
"""

from __future__ import annotations

from app.paths import data_path  # noqa: E402
import asyncio
import json
import os
import re
from pathlib import Path
from typing import Any

from app.models import TripPlan

API = "https://api.unsplash.com/search/photos"
CACHE_PATH = data_path(".cache", "unsplash.json")
DEFAULT_LIMIT = 12


def _load_cache() -> dict[str, Any]:
    try:
        return json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _save_cache(cache: dict[str, Any]) -> None:
    try:
        CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = CACHE_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(CACHE_PATH)
    except OSError:
        pass  # 缓存写不了不算错误


def _proxy() -> str | None:
    return os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY")


LARGE_WIDTH = 1600


def upgrade_url(url: str) -> str:
    """把 Unsplash 的缩略图地址改成大图地址。

    为什么能这么干：Unsplash 的图走 imgix，`w` 就是渲染宽度参数，
    把 `w=400` 换成 `w=1600` 得到同一张图的高清版，**不用再请求一次搜索接口**。
    这让"历史缓存里只有小图"不用重建——已有条目直接就有大图了。
    URL 里没有 w 参数就原样返回，不猜。
    """
    if not url or "w=" not in url:
        return url
    return re.sub(r"([?&])w=\d+", lambda m: f"{m.group(1)}w={LARGE_WIDTH}", url, count=1)


def norm(entry: Any) -> dict[str, str] | None:
    """把缓存条目统一成 {small, large}。

    旧缓存是纯字符串（只有 small）：大图由 upgrade_url 推导；
    推不出来才退回同一张——宁可糊一点，也不要让历史缓存失效。
    """
    if isinstance(entry, str):
        return {"small": entry, "large": upgrade_url(entry)}
    if isinstance(entry, dict) and entry.get("small"):
        return {
            "small": entry["small"],
            "large": entry.get("large") or upgrade_url(entry["small"]),
        }
    return None


async def _search_one(
    client: Any, key: str, cache_key: str, query: str, cache: dict[str, str]
) -> tuple[str, str | None]:
    """查一个景点；返回 (cache_key, url 或 None)。"""
    if cache_key in cache:
        return cache_key, norm(cache[cache_key])

    try:
        resp = await client.get(
            API,
            params={"query": query, "per_page": 1, "orientation": "landscape"},
            headers={"Authorization": f"Client-ID {key}"},
        )
    except Exception:  # noqa: BLE001 - 网络问题不该影响行程
        return cache_key, None

    if resp.status_code == 403:
        # 限流：不要继续打，本轮全部放弃
        raise RuntimeError("RATE_LIMITED")
    if resp.status_code != 200:
        return cache_key, None

    try:
        results = resp.json().get("results") or []
        if not results:
            return cache_key, None
        urls = results[0].get("urls") or {}
        small = urls.get("small")
        large = urls.get("regular") or small
        if not small:
            return cache_key, None
        return cache_key, {"small": small, "large": large}
    except Exception:  # noqa: BLE001
        return cache_key, None

    return cache_key, url


async def enrich_plan_images(
    plan: TripPlan,
    access_key: str,
    *,
    limit: int = DEFAULT_LIMIT,
    concurrency: int = 3,
    stats: dict[str, Any] | None = None,
) -> int:
    """给行程里的景点补图，返回补了几个「景点实例」。

    ``stats`` 用来回传非致命情况（目前只有限流），调用方可以据此写一条 trace，
    让"这次为什么没图"可见——否则排障时只能靠猜。

    注意两个列表的区别（第一版踩过）：
      * ``pairs``   —— 每个景点实例都记，回填时用；同一景点在两天的行程里
                       出现两次，两天都要有图。
      * ``queries`` —— 按「城市|景点名」去重，查询时用；同一景点只查一次，
                       省 Unsplash 的配额。
    只用去重后的列表回填，会出现"第二天同一景点没图"。
    """
    if not access_key:
        return 0

    pairs: list[tuple[str, Any]] = []
    queries: dict[str, str] = {}

    for day in plan.days:
        for attraction in day.attractions:
            if attraction.image_url:
                continue
            cache_key = f"{plan.city}|{attraction.name}"
            pairs.append((cache_key, attraction))
            if cache_key not in queries and len(queries) < limit:
                queries[cache_key] = f"{attraction.name} {plan.city}"

    if not queries:
        return 0

    cache = _load_cache()
    # 缓存里已经有的，直接跳过查询层——省一次函数调用，也让"命中缓存就不发请求"
    # 这件事在代码里是显式的，而不是藏在 _search_one 里面。
    missing = {ck: q for ck, q in queries.items() if ck not in cache}
    semaphore = asyncio.Semaphore(concurrency)

    import httpx

    async with httpx.AsyncClient(timeout=12, proxy=_proxy(), trust_env=bool(_proxy())) as client:

        async def guarded(cache_key: str, query: str):
            async with semaphore:
                return await _search_one(client, access_key, cache_key, query, cache)

        results: list[Any] = []
        if missing:
            try:
                results = list(
                    await asyncio.gather(
                        *(guarded(ck, q) for ck, q in missing.items()),
                        return_exceptions=True,
                    )
                )
            except Exception:  # noqa: BLE001
                results = []

        rate_limited = False
        for item in results:
            if isinstance(item, BaseException):
                if isinstance(item, RuntimeError) and "RATE_LIMITED" in str(item):
                    rate_limited = True
                continue
            cache_key, url = item
            if url:
                cache[cache_key] = url

        # 限流不是错误，但要让人看得见"这次为什么没图"
        if rate_limited and stats is not None:
            stats["rate_limited"] = True

    # 回填到每一个景点实例（同一景点出现多次都要填）
    filled = 0
    for cache_key, attraction in pairs:
        item = norm(cache.get(cache_key))
        if item:
            attraction.image_url = item["small"]
            attraction.image_url_large = item["large"]
            filled += 1

    _save_cache(cache)
    return filled

