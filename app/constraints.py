"""预算校验：模型可能超，程序必须**如实说出超了多少**，而不是假装没超。

放在这里而不是塞进提示词：提示词只能"请求"模型守预算，
能不能守住得**算**出来。这一层是确定性代码，可测。
"""

from __future__ import annotations

from typing import Any

# 超出容忍度：在这个比例内不算超（避免 1999 vs 2000 这种边界噪音）
TOLERANCE = 0.0


def check_budget(plan: Any, limit: int | None) -> dict[str, Any]:
    """返回 {limit, total, over, ok, message}。未设上限时 ok=True 且 message 为空。"""
    budget = getattr(plan, "budget", None)
    total = int(getattr(budget, "total", 0) or 0) if budget is not None else 0

    if not limit:
        return {"limit": None, "total": total, "over": 0, "ok": True, "message": ""}

    over = total - int(limit)
    ok = over <= int(limit * TOLERANCE)
    if ok:
        return {
            "limit": int(limit),
            "total": total,
            "over": 0,
            "ok": True,
            "message": f"预算 {limit} 元，合计 {total} 元，在范围内（余 {int(limit) - total} 元）",
        }

    # 给出"从哪儿减"的建议：按四项里最大的一项指路，比空泛的"请调整"有用
    items = {
        "门票": int(getattr(budget, "total_attractions", 0) or 0),
        "住宿": int(getattr(budget, "total_hotels", 0) or 0),
        "餐饮": int(getattr(budget, "total_meals", 0) or 0),
        "交通": int(getattr(budget, "total_transportation", 0) or 0),
    }
    biggest = max(items, key=items.get)
    return {
        "limit": int(limit),
        "total": total,
        "over": over,
        "ok": False,
        "message": (
            f"预算 {limit} 元，合计 {total} 元，超出 {over} 元。"
            f"四项里{biggest}占得最多（{items[biggest]} 元），可从这一项压缩。"
        ),
    }
