"""局部重排的"守门"逻辑：只改选中的天，其余逐字不动。

为什么单独一个模块：这是**确定性判定**，不该掺模型调用。
模型给什么、我们接受什么，全靠这里的规则；规则越清楚，越能在
"模型不听话"的时候给出人话反馈而不是静默接受。

三条规则：
  1. 结构锁死：天数、日期序列必须与原计划一致（不许顺手增删一天）
  2. 补集不变：未选中的天，day 数据必须逐字一致
  3. 选中天有效：每天 1~6 个景点
预算不在"逐字不变"范围内——换了景点门票就变，由程序按明细重算。
"""

from __future__ import annotations

import json
from typing import Any, Iterable

MIN_ATTRACTIONS = 1
MAX_ATTRACTIONS = 6


def _dump(value: Any) -> str:
    """稳定序列化：用于逐字比较。

    sort_keys 是必须的——同一份数据字段顺序不同会被误判成"变了"。
    """
    if hasattr(value, "model_dump"):
        value = value.model_dump()
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def validate_structure(old: Any, new: Any) -> list[str]:
    """天数与日期序列必须一致。"""
    violations: list[str] = []
    old_days = list(getattr(old, "days", []) or [])
    new_days = list(getattr(new, "days", []) or [])

    if len(old_days) != len(new_days):
        violations.append(f"天数被改了：原来 {len(old_days)} 天，现在 {len(new_days)} 天")

    for idx, (a, b) in enumerate(zip(old_days, new_days)):
        if getattr(a, "date", "") != getattr(b, "date", ""):
            violations.append(
                f"第 {idx + 1} 天日期被改了：{getattr(a, 'date', '')} → {getattr(b, 'date', '')}"
            )

    if getattr(old, "city", "") != getattr(new, "city", ""):
        violations.append(f"城市被改了：{getattr(old, 'city', '')} → {getattr(new, 'city', '')}")
    return violations


def validate_unselected(old: Any, new: Any, selected: Iterable[int]) -> list[str]:
    """未选中的天必须逐字一致。"""
    picked = set(selected)
    violations: list[str] = []
    old_days = list(getattr(old, "days", []) or [])
    new_days = list(getattr(new, "days", []) or [])

    for idx in range(min(len(old_days), len(new_days))):
        if idx in picked:
            continue
        if _dump(old_days[idx]) != _dump(new_days[idx]):
            violations.append(f"第 {idx + 1} 天没让你改，却被改了")
    return violations


def validate_selected(new: Any, selected: Iterable[int]) -> list[str]:
    """选中天的景点数量必须在 1~6。"""
    violations: list[str] = []
    days = list(getattr(new, "days", []) or [])
    for idx in selected:
        if idx < 0 or idx >= len(days):
            violations.append(f"第 {idx + 1} 天不存在")
            continue
        count = len(getattr(days[idx], "attractions", []) or [])
        if count < MIN_ATTRACTIONS:
            violations.append(f"第 {idx + 1} 天一个景点都没有")
        elif count > MAX_ATTRACTIONS:
            violations.append(f"第 {idx + 1} 天排了 {count} 个景点，超过 {MAX_ATTRACTIONS} 个")
    return violations


def validate_all(old: Any, new: Any, selected: Iterable[int]) -> list[str]:
    """一次跑完三条规则，返回人话问题列表（空列表表示通过）。"""
    picked = list(selected)
    if not picked:
        return ["没有选中任何一天"]
    return (
        validate_structure(old, new)
        + validate_unselected(old, new, picked)
        + validate_selected(new, picked)
    )


def merge_local(old: Any, new: Any, selected: Iterable[int]) -> Any:
    """按"选中的天用新的、其余用旧的"合成最终计划。

    即使模型守住了局部性，也走这一步：**未选中的天直接取旧对象**，
    从构造上保证逐字不变，而不是依赖"它应该没改"。
    """
    picked = set(selected)
    merged = old.model_copy(deep=True)
    new_days = list(getattr(new, "days", []) or [])
    for idx in picked:
        if 0 <= idx < len(new_days) and idx < len(merged.days):
            merged.days[idx] = new_days[idx].model_copy(deep=True)
    return merged


def recompute_budget(plan: Any) -> Any:
    """按明细重算四项预算，合计 = 四项之和。

    为什么不沿用模型给的数字：它只改了其中一天，却可能把四个分项全重写一遍，
    结果"明细"和"总计"对不上。这里与前端的 `recomputeBudget` 用同一套口径：
      门票 = Σ ticket_price；住宿 = Σ 有名字的酒店 estimated_cost；
      餐饮 = Σ estimated_cost；交通沿用模型值（自驾油费无法从明细推导）。
    """
    days = list(getattr(plan, "days", []) or [])

    attractions = sum(
        int(getattr(a, "ticket_price", 0) or 0) for d in days for a in (getattr(d, "attractions", []) or [])
    )
    meals = sum(
        int(getattr(m, "estimated_cost", 0) or 0) for d in days for m in (getattr(d, "meals", []) or [])
    )
    hotels = 0
    for d in days:
        hotel = getattr(d, "hotel", None)
        name = (getattr(hotel, "name", "") or "").strip() if hotel else ""
        if name:
            hotels += int(getattr(hotel, "estimated_cost", 0) or 0)

    budget = getattr(plan, "budget", None)
    if budget is None:
        from app.models import Budget

        budget = Budget()
        plan.budget = budget

    budget.total_attractions = attractions
    budget.total_hotels = hotels
    budget.total_meals = meals
    # 交通保留模型/原计划的值：它不来自明细，重算没有依据
    budget.recompute_total()
    return plan


def diff_summary(old: Any, new: Any, selected: Iterable[int]) -> dict[str, Any]:
    """给用户看的"将要变成什么"，同时也给模型当反馈。

    只列选中的天：未选中的天不变，列出来只是噪音。
    """
    picked = sorted(set(selected))
    out: list[dict[str, Any]] = []
    old_days = list(getattr(old, "days", []) or [])
    new_days = list(getattr(new, "days", []) or [])

    for idx in picked:
        if idx >= len(new_days):
            continue
        before = old_days[idx] if idx < len(old_days) else None
        after = new_days[idx]
        out.append(
            {
                "day_index": idx,
                "date": getattr(after, "date", ""),
                "before": [getattr(a, "name", "") for a in (getattr(before, "attractions", []) or [])]
                if before
                else [],
                "after": [getattr(a, "name", "") for a in (getattr(after, "attractions", []) or [])],
                "hotel_before": getattr(getattr(before, "hotel", None), "name", "") if before else "",
                "hotel_after": getattr(getattr(after, "hotel", None), "name", ""),
            }
        )
    return {"days": out}
