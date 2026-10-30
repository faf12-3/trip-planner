"""把「其他要求」映射成酒店搜索词。

为什么需要这一层：项目坚持"工具参数由代码决定，不交给模型猜"。
但用户说"想住舒适一点的酒店"时，如果搜索词还是表单里的"经济型酒店"，
模型只能在几家经济型里挑——**提示词再怎么写也变不出舒适型酒店**。

所以加一个确定性的映射：从自然语言里识别档位意图，只影响"搜什么"，不影响"搜不搜"。
规则是白名单式的，可穷举测试；识别不出来就沿用表单值（不猜）。
"""

from __future__ import annotations

# 关键词 → 目标档位。顺序有意义：先判"高端"，再判"舒适"，最后判"省钱"。
INTENT_RULES: list[tuple[tuple[str, ...], str]] = [
    (("豪华", "高端", "五星", "轻奢", "奢侈"), "豪华型酒店"),
    (("舒适", "中端", "好一点", "品质", "干净", "安静", "有窗"), "舒适型酒店"),
    (("省钱", "便宜", "经济", "穷游", "实惠"), "经济型酒店"),
    (("民宿", "客栈", "青旅", "airbnb"), "民宿"),
]


def derive_hotel_keyword(accommodation: str, notes: str = "") -> str:
    """返回酒店搜索关键词。notes 里没有明确意图时原样返回表单值。"""
    text = (notes or "").strip().lower()
    if not text:
        return accommodation
    for words, target in INTENT_RULES:
        if any(w in text for w in words):
            return target
    return accommodation


def hotel_keyword_reason(accommodation: str, notes: str = "", keyword: str = "") -> str:
    """给人看的一句话：说明搜索词为什么变成这样（写进 trace，便于排查）。"""
    kw = keyword or derive_hotel_keyword(accommodation, notes)
    if kw == accommodation:
        return f"酒店搜索沿用表单：{kw}"
    return f"酒店搜索由「其他要求」调整：{accommodation} → {kw}"
