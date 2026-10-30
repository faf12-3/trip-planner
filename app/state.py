"""LangGraph 的状态定义。

`errors` 和 `trace` 用了 `operator.add` 归约——多个节点都要往里追加，
不加归约会被后一个节点整个覆盖（这是 LangGraph 里最常见的坑之一）。
"""

from __future__ import annotations

import operator
from typing import Annotated, Any, Optional, TypedDict

from langchain_core.language_models import BaseChatModel

from app.amap import AmapTools
from app.models import TripPlan, TripRequest


class TripState(TypedDict, total=False):
    # 输入
    request: TripRequest

    # 注入件（不参与序列化，仅图内传递）
    tools: AmapTools
    llm: BaseChatModel
    # 记忆：该用户的历史偏好文本（可为空）。空串表示"没有画像"，不污染提示词
    memory: str

    # 中间产物：三个工具的原始返回
    attractions_raw: str
    weather_raw: str
    hotels_raw: str
    # 条件路由：下雨天补查的室内候选（原文）
    indoor_raw: str

    # 条件路由的结果，给接口和前端看：
    # {needed, days:[{date, weather, reason}], indoor:[...], violations:[...]}
    routing: dict[str, Any]

    # 输出
    plan: Optional[TripPlan]
    raw_plan: str

    # 观测
    errors: Annotated[list[str], operator.add]
    trace: Annotated[list[str], operator.add]
