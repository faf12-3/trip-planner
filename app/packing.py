"""装备清单：按天气、天数、交通、住宿推一份要带的东西。

为什么用规则而不是让模型写：清单是**可穷举的**——下雨就要伞，温差大就要外套。
交给模型只会得到"因人而异"的漂亮废话（"根据个人需求携带合适物品"），用户拿不走。

每条都带 `reason`，且 reason 必须指向输入里的某个事实（第几天有雨、温差几度、几天行程）。
这让"为什么给我这条"可解释，也让它可测：改输入 → 断言某条出现或消失。
"""

from __future__ import annotations

from typing import Any

# 阈值：都是常识值，故意写在这里而不是散在 if 里，方便以后调
TEMP_RANGE_C = 8      # 昼夜温差 ≥ 8℃ 带外套
COLD_NIGHT_C = 5      # 夜间 ≤ 5℃ 加强保暖
HOT_DAY_C = 30        # 白天 ≥ 30℃ 防晒补水
LONG_TRIP_DAYS = 3    # ≥ 3 天带换洗

RAIN_WORDS = ("雨", "雪", "雷", "冰雹")


def _days(plan: Any) -> list[Any]:
    return list(getattr(plan, "days", []) or [])


def _weather(plan: Any) -> list[dict[str, Any]]:
    return [w if isinstance(w, dict) else w.model_dump() for w in (getattr(plan, "weather_info", []) or [])]


def _rainy(plan: Any) -> list[str]:
    """返回有雨/雪的日期列表（天气是数据，判断是规则，都不交给模型）。"""
    out: list[str] = []
    for w in _weather(plan):
        text = f"{w.get('day_weather', '')}{w.get('night_weather', '')}"
        if any(word in text for word in RAIN_WORDS):
            out.append(str(w.get("date", "")))
    return out


def _temps(plan: Any) -> tuple[int, int] | None:
    ws = _weather(plan)
    if not ws:
        return None
    highs = [int(w.get("day_temp", 0) or 0) for w in ws]
    lows = [int(w.get("night_temp", 0) or 0) for w in ws]
    return max(highs), min(lows)


def _transport(plan: Any) -> str:
    days = _days(plan)
    return str(getattr(days[0], "transportation", "") or "") if days else ""


def _accommodation(plan: Any) -> str:
    for day in _days(plan):
        acc = str(getattr(day, "accommodation", "") or "")
        if acc:
            return acc
    return ""


def build_packing(plan: Any) -> dict[str, Any]:
    """返回 {groups: [...], summary: str}。纯函数：同样输入必定同样输出。"""
    days = _days(plan)
    n = len(days)
    rainy = _rainy(plan)
    temps = _temps(plan)

    groups: dict[str, list[dict[str, str]]] = {}

    def add(group: str, name: str, reason: str) -> None:
        groups.setdefault(group, []).append({"name": name, "reason": reason})

    # ---- 证件与金钱：任何行程都要 ----
    add("证件与金钱", "身份证 / 护照", "入住与购票都要")
    add("证件与金钱", "少量现金", "小店与交通可能不支持扫码")

    # ---- 衣物：看温差、低温、高温、天数 ----
    if temps is not None:
        high, low = temps
        if high - low >= TEMP_RANGE_C:
            add("衣物", "薄外套", f"昼夜温差 {high - low}℃，早晚要加一件")
        if low <= COLD_NIGHT_C:
            add("衣物", "保暖内衣", f"夜间最低 {low}℃")
        if high >= HOT_DAY_C:
            add("衣物", "速干短袖", f"白天最高 {high}℃")
    if n >= LONG_TRIP_DAYS:
        add("衣物", f"换洗衣物 {n} 套", f"行程 {n} 天")

    # ---- 雨具：唯一由天气直接决定的硬规则 ----
    if rainy:
        shown = "、".join(rainy[:2]) + ("…" if len(rainy) > 2 else "")
        add("雨具", "折叠伞", f"{shown} 有雨/雪")
        add("雨具", "防水鞋套", "雨天步行不湿鞋")

    # ---- 电子 ----
    add("电子", "手机 + 充电器", "导航与支付")
    add("电子", "充电宝", f"{n} 天在外，手机撑不住" if n >= 2 else "外出补电")

    # ---- 按交通与住宿 ----
    transport = _transport(plan)
    if "自驾" in transport:
        add("交通", "驾驶证 / 行驶证", "自驾必备")
        add("交通", "车载充电器", "长时间导航耗电快")
    if "步行" in transport or "公共交通" in transport:
        add("交通", "舒适的鞋", f"以{transport}为主，步行量大")

    accommodation = _accommodation(plan)
    if "民宿" in accommodation:
        add("住宿", "洗漱用品", "民宿不一定提供一次性用品")

    # ---- 常备药品 ----
    add("药品", "常用药（感冒/肠胃/创可贴）", "出门在外应急")

    summary_bits = [f"{n} 天"]
    if rainy:
        summary_bits.append(f"有雨 {len(rainy)} 天")
    if temps is not None:
        summary_bits.append(f"{temps[0]}~{temps[1]}℃")

    return {
        "groups": [{"title": k, "items": v} for k, v in groups.items()],
        "summary": " · ".join(summary_bits),
        "rainy_days": rainy,
    }
