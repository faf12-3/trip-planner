"""多轮编辑：LangGraph interrupt 停下来等确认，确认后只改选中的天。

图结构：

    START → draft（调模型 + 守门校验 + 合成）→ confirm（interrupt 等确认）→ apply → END
                        └─ 校验不过 → END（带 errors）

为什么把 interrupt 放在"生成之后、落库之前"：
  · 放在生成之前只是多一次点击，没有信息量；
  · 放在这里，用户看到的是**具体的 diff**（第 2 天要换成这些），确认才有意义；
  · 落库前停住，拒绝时原计划天然不受影响。

状态存在 InMemorySaver 里——进程重启即丢，这是要写进 README 的限制。
"""

from __future__ import annotations

import json
import operator
from typing import Annotated, Any, Optional, TypedDict

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from app.local_edit import (
    diff_summary,
    merge_local,
    recompute_budget,
    validate_all,
)
from app.models import TripPlan
from app.nodes import parse_trip_plan

MAX_ATTEMPTS = 2


class ReplanState(TypedDict, total=False):
    # 输入
    old_plan: TripPlan
    selected: list[int]
    requirement: str
    llm: Any
    trip_id: str
    user: str

    # 中间产物
    draft: Optional[TripPlan]
    preview: dict[str, Any]
    violations: list[str]
    attempts: int

    # 中断后回填
    decision: str

    # 输出
    final_plan: Optional[TripPlan]
    errors: Annotated[list[str], operator.add]


CONSTRAINTS = """## 硬约束（违反即作废）
1. 只能修改第 {days} 天；其余各天的内容**一个字都不能变**。
2. 天数、日期序列、城市都不能变。
3. 被修改的每一天必须有 1~6 个景点。
4. 输出完整计划 JSON（结构同原计划），不要只输出被改的天。
"""


def _plan_json(plan: TripPlan) -> str:
    return json.dumps(plan.model_dump(), ensure_ascii=False, indent=1)


def build_messages(state: ReplanState, feedback: str = "") -> list[tuple[str, str]]:
    """组提示词。反馈非空表示这是重试，要明确告诉模型上次错在哪。"""
    selected = list(state.get("selected") or [])
    human_days = "、".join(str(i + 1) for i in selected)
    old = state["old_plan"]

    system = (
        "你是行程编辑器。给你一份已有行程和一条修改要求，"
        "你只改指定的那几天，其余原样不动。"
    )
    body = [
        CONSTRAINTS.format(days=human_days),
        "## 原计划（完整 JSON）",
        _plan_json(old),
        f"## 修改要求\n{state.get('requirement', '').strip() or '（未提供，按常识优化）'}",
    ]
    if feedback:
        body.append("## 上一次的输出有问题，必须修正\n" + feedback)
    body.append("只输出 JSON，不要解释。")
    return [("system", system), ("human", "\n\n".join(body))]


def _call_llm(llm: Any, messages: list[tuple[str, str]]) -> str:
    """同步调用假模型（真模型走 ainvoke，见 draft 节点）。"""
    from langchain_core.messages import HumanMessage, SystemMessage

    resp = llm.invoke(
        [SystemMessage(content=messages[0][1]), HumanMessage(content=messages[1][1])]
    )
    content = getattr(resp, "content", resp)
    if isinstance(content, list):
        content = "".join(str(b) for b in content)
    return str(content)


async def draft_node(state: ReplanState) -> dict[str, Any]:
    """调模型 → 校验 → 越界带反馈重试一次 → 合成最终草稿。"""
    from langchain_core.messages import HumanMessage, SystemMessage

    llm = state["llm"]
    old = state["old_plan"]
    selected = list(state.get("selected") or [])

    feedback = ""
    last_violations: list[str] = []

    for attempt in range(MAX_ATTEMPTS):
        messages = build_messages(state, feedback)
        try:
            resp = await llm.ainvoke(
                [SystemMessage(content=messages[0][1]), HumanMessage(content=messages[1][1])]
            )
            text = getattr(resp, "content", "")
            if isinstance(text, list):
                text = "".join(str(b) for b in text)
        except Exception as exc:  # noqa: BLE001
            return {
                "draft": None,
                "errors": [f"模型调用失败: {type(exc).__name__}: {exc}"],
            }

        try:
            candidate = parse_trip_plan(str(text))
        except Exception as exc:  # noqa: BLE001
            last_violations = [f"输出不是合法计划 JSON：{type(exc).__name__}"]
            feedback = "你的输出无法解析为计划 JSON，请严格按原结构输出。"
            continue

        violations = validate_all(old, candidate, selected)
        if not violations:
            merged = merge_local(old, candidate, selected)
            recompute_budget(merged)
            return {
                "draft": merged,
                "preview": diff_summary(old, merged, selected),
                "violations": [],
                "attempts": attempt + 1,
                "errors": [],
            }

        last_violations = violations
        feedback = "；".join(violations) + "。请只改指定的天，其余原样返回。"

    return {
        "draft": None,
        "violations": last_violations,
        "attempts": MAX_ATTEMPTS,
        "errors": [f"重试 {MAX_ATTEMPTS} 次后仍未守住局部性：" + "；".join(last_violations)],
    }


def confirm_node(state: ReplanState) -> dict[str, Any]:
    """停在这里等人确认。resume 的值就是 decision。"""
    decision = interrupt(
        {
            "preview": state.get("preview", {}),
            "selected": state.get("selected", []),
            "requirement": state.get("requirement", ""),
            "attempts": state.get("attempts", 1),
        }
    )
    return {"decision": str(decision)}


def apply_node(state: ReplanState) -> dict[str, Any]:
    """确认才落地；拒绝则什么都不改。"""
    if state.get("decision") != "approve":
        return {"final_plan": None, "errors": ["用户取消了这次重排"]}
    return {"final_plan": state.get("draft")}


def _after_draft(state: ReplanState) -> str:
    """草稿有效才进入确认；失败直接结束，别让用户白点一次确认。"""
    return "confirm" if state.get("draft") is not None else "__end__"


def build_replan_graph(checkpointer: Any = None):
    graph = StateGraph(ReplanState)
    graph.add_node("draft", draft_node)
    graph.add_node("confirm", confirm_node)
    graph.add_node("apply", apply_node)

    graph.add_edge(START, "draft")
    graph.add_conditional_edges("draft", _after_draft, {"confirm": "confirm", "__end__": END})
    graph.add_edge("confirm", "apply")
    graph.add_edge("apply", END)

    return graph.compile(checkpointer=checkpointer or InMemorySaver())


# --------------------------------------------------------------- 会话管理 --
_graph = None


def get_graph():
    """进程内单例：checkpointer 得活着，两段请求才能续上同一个 thread。"""
    global _graph
    if _graph is None:
        _graph = build_replan_graph(InMemorySaver())
    return _graph


def reset_graph() -> None:
    """测试用：丢掉所有会话。"""
    global _graph
    _graph = None
