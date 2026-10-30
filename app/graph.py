"""图装配：START → attraction → weather → hotel → planner → END。"""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from app.nodes import (
    adjust_node,
    attraction_node,
    hotel_node,
    planner_node,
    weather_node,
)
from app.state import TripState


def build_graph(*, with_trace: bool = True) -> CompiledStateGraph:
    """编译好的图。

    with_trace=True 时给每个节点包一层，把进出状态摘要记进 trace——
    和 hello_agents 里 EndToEndAgent 的做法一致：出问题时能说清是哪一步。
    """
    graph = StateGraph(TripState)

    graph.add_node("attraction", _traced("attraction", attraction_node, with_trace))
    graph.add_node("weather", _traced("weather", weather_node, with_trace))
    graph.add_node("adjust", _traced("adjust", adjust_node, with_trace))
    graph.add_node("hotel", _traced("hotel", hotel_node, with_trace))
    graph.add_node("planner", _traced("planner", planner_node, with_trace))

    graph.add_edge(START, "attraction")
    graph.add_edge("attraction", "weather")
    # 条件路由：下雨就走 adjust 补查室内候选，晴天直接去酒店
    graph.add_conditional_edges(
        "weather",
        _after_weather,
        {"adjust": "adjust", "hotel": "hotel"},
    )
    graph.add_edge("adjust", "hotel")
    graph.add_edge("hotel", "planner")
    graph.add_edge("planner", END)

    return graph.compile()


def _traced(name: str, fn, enabled: bool):
    if not enabled:
        return fn

    async def wrapper(state: TripState) -> dict:
        update = await fn(state)
        note = f"{name}: ok"
        if isinstance(update, dict):
            # 把请求城市带进 trace：排查"明明查南京却返回北京数据"这类问题时，
            # 光看字符数看不出参数是否正确。
            city = ""
            req = state.get("request")
            if req is not None:
                city = getattr(req, "city", "") or ""
            suffix = f" [{city}]" if city else ""

            if update.get("errors"):
                note = f"{name}: {len(update['errors'])} error(s){suffix}"
            elif name != "planner":
                # 键名是复数（attractions_raw / hotels_raw / weather_raw），
                # 不能用 f"{name}_raw"——第一版这里一直是 0 chars，看着像没取到数据
                raw = next(
                    (
                        v
                        for k, v in update.items()
                        if k.endswith("_raw") and isinstance(v, str)
                    ),
                    "",
                )
                note = f"{name}: {len(raw)} chars"
            elif update.get("plan") is not None:
                plan = update["plan"]
                note = f"planner: {len(plan.days)} day(s), budget={plan.budget.total if plan.budget else 0}"
            # 追加而不是覆盖：节点自己写的 trace（例如"酒店搜索词被调整"）不能被吞掉
            update = {**update, "trace": [*(update.get("trace") or []), note]}
        return update

    return wrapper


def _after_weather(state: TripState) -> str:
    """天气查完之后分叉：有坏天气 -> adjust，否则 -> hotel。

    判断放在这里而不是节点里：LangGraph 的条件边就是"决策点"，
    把它写成独立函数，图结构一眼能看出分支。
    """
    req = state.get("request")
    if req is None:
        return "hotel"

    from app.nodes import _weather_from_raw
    from app.routing import needs_adjust

    weather = _weather_from_raw(state.get("weather_raw", "") or "", req.start_date, req.days)
    return "adjust" if needs_adjust(weather) else "hotel"
