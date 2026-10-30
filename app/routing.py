"""条件路由：下雨天改室内。

为什么单独一个模块：路由**决策**必须是确定性的、可单测的。
把"今天算不算坏天气""这个景点是不是室外""生成结果有没有违规"做成纯函数，
LangGraph 里只负责按决策分叉——这样代理断了也能把这块验完。

分工：
  routing.py   决策（纯函数）
  nodes.py     adjust 节点：按决策补查室内候选
  graph.py     条件边：weather → adjust 或 hotel
"""

from __future__ import annotations

import re
from typing import Any, Iterable

# 高德天气文本形如「小雨」「中雨」「雷阵雨」「雨夹雪」「多云」「阴」「霾」。
# 判据只认"会让人不想在室外待着"的那几类：雨、雪、雷、冰雹、沙尘、强风。
BAD_WEATHER_WORDS = ("雨", "雪", "雷", "冰雹", "沙尘", "暴", "台风", "大风")
# 「阴」「多云」「晴」「雾」「霾」不算坏天气：能出门，只是体验差一点。
# 注意「雨」类里"小雨"也算——室外长时间活动一样受影响。

INDOOR_WORDS = (
    "博物馆",
    "博物院",
    "陈列馆",
    "天文馆",
    "美术馆",
    "科技馆",
    "展览馆",
    "纪念馆",
    "图书馆",
    "艺术馆",
    "大剧院",
    "剧院",
    "水族馆",
    "海洋馆",
    "文化馆",
    "画廊",
    "书店",
)

OUTDOOR_WORDS = (
    "公园",
    "广场",
    "山",
    "湖",
    "街",
    "古镇",
    "村",
    "湿地",
    "动物园",
    "植物园",
    "长城",
    "塔",
    "寺",
    "庙",
    "陵",
    "园",
    "滩",
    "岛",
    "谷",
    "洞",
    "泉",
    "瀑布",
    "草原",
    "沙漠",
    "栈道",
    "步道",
    "观景",
)

INDOOR_SEARCH_KEYWORDS = "博物馆 美术馆 科技馆"


def bad_weather(text: str) -> bool:
    """这段天气文案算不算"坏天气"（需要改室内）。"""
    if not text:
        return False
    return any(word in text for word in BAD_WEATHER_WORDS)


def classify_poi(name: str) -> str:
    """按名字判断景点属性。

    先看室内词再看室外词：像"南京博物院艺术馆"这种名字里同时有"馆"和别的，
    室内优先更安全；而"博物馆群公园"这类歧义名字，室内优先也不会错得离谱。
    判断不出来返回 unknown——**不猜**，猜错会把晴天也改掉。
    """
    if not name:
        return "unknown"
    if any(word in name for word in INDOOR_WORDS):
        return "indoor"
    if any(word in name for word in OUTDOOR_WORDS):
        return "outdoor"
    return "unknown"


def rainy_days(weather_info: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """从 weather_info 里挑出坏天气的日子。"""
    out: list[dict[str, Any]] = []
    for item in weather_info or []:
        day = str(item.get("day_weather") or "")
        night = str(item.get("night_weather") or "")
        hit = [w for w in (day, night) if bad_weather(w)]
        if hit:
            out.append(
                {
                    "date": str(item.get("date") or ""),
                    "weather": day or night,
                    "night_weather": night,
                    "reason": f"{'/'.join(hit)}，室外活动受影响",
                }
            )
    return out


def needs_adjust(weather_info: Iterable[dict[str, Any]]) -> bool:
    return bool(rainy_days(weather_info))


def verify_plan(plan: Any, bad_dates: Iterable[str]) -> list[dict[str, Any]]:
    """检查生成结果：坏天气那天是否还排了确定的室外景点。

    只在**能确定**是室外时报警（unknown 不报）——误报比漏报更烦人，
    用户会开始无视提示。
    """
    dates = {str(d) for d in (bad_dates or []) if d}
    if not dates or plan is None:
        return []

    violations: list[dict[str, Any]] = []
    for index, day in enumerate(getattr(plan, "days", []) or []):
        date = str(getattr(day, "date", "") or "")
        if date not in dates:
            continue
        outdoor = [
            getattr(a, "name", "") or ""
            for a in (getattr(day, "attractions", []) or [])
            if classify_poi(getattr(a, "name", "") or "") == "outdoor"
        ]
        if outdoor:
            violations.append(
                {
                    "day_index": index,
                    "date": date,
                    "outdoor": outdoor[:5],
                    "message": f"第 {index + 1} 天有雨，但仍排了室外景点：{'、'.join(outdoor[:3])}",
                }
            )
    return violations


DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def normalize_dates(items: Iterable[tuple[str, str]]) -> list[tuple[str, str]]:
    """把 (起始日, 天数) 展开成日期列表——图里做条件判断时用。"""
    from datetime import date, timedelta

    out: list[tuple[str, str]] = []
    for start, _ in items:
        if not DATE_RE.match(start or ""):
            continue
        base = date.fromisoformat(start)
        out.append((start, base.isoformat()))
    return out
