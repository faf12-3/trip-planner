"""多轮编辑（interrupt）的测试：真中断、真续跑、越界重试、拒绝不改。

不需要网络：模型用假对象，计划用构造函数造。
对应验收标准 1、2、3、4、5、6、7。
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langgraph.types import Command

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.models import Attraction, Budget, DayPlan, Hotel, Meal, TripPlan  # noqa: E402
from app.replan import build_replan_graph, reset_graph  # noqa: E402


def make_plan() -> TripPlan:
    return TripPlan(
        city="南京",
        start_date="2026-11-01",
        end_date="2026-11-03",
        days=[
            DayPlan(
                date=f"2026-11-0{i + 1}",
                day_index=i,
                description=f"第{i + 1}天",
                attractions=[Attraction(name=f"D{i}A", visit_duration=60, ticket_price=10)],
                meals=[Meal(type="lunch", name="饭", estimated_cost=30)],
                hotel=Hotel(name="酒店", estimated_cost=200),
            )
            for i in range(3)
        ],
        overall_suggestions="",
        budget=Budget(total_transportation=50),
    )


def plan_with(day2_names: list[str], *, day0_name: str = "D0A") -> str:
    """造一份"模型输出"：第 2 天换成 point，其余尽量保持原样。"""
    plan = make_plan()
    plan.days[0].attractions = [Attraction(name=day0_name, visit_duration=60, ticket_price=10)]
    plan.days[1].attractions = [
        Attraction(name=n, visit_duration=60, ticket_price=0) for n in day2_names
    ]
    import json

    return json.dumps(plan.model_dump(), ensure_ascii=False)


class ScriptLLM(BaseChatModel):
    """按顺序吐预设输出的假模型，并记录被调了几次。"""

    replies: list[str] = []
    calls: int = 0

    @property
    def _llm_type(self) -> str:
        return "script"

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        idx = min(self.calls, len(self.replies) - 1)
        object.__setattr__(self, "calls", self.calls + 1)
        return ChatResult(
            generations=[ChatGeneration(message=AIMessage(content=self.replies[idx]))]
        )


async def _start(replies: list[str], *, selected=(1,)) -> tuple[Any, dict, dict]:
    reset_graph()
    graph = build_replan_graph()
    llm = ScriptLLM(replies=replies)
    cfg = {"configurable": {"thread_id": "t-test"}}
    out = await graph.ainvoke(
        {
            "old_plan": make_plan(),
            "selected": list(selected),
            "requirement": "改成室内",
            "llm": llm,
            "trip_id": "trip-1",
            "user": "u1",
            "errors": [],
        },
        cfg,
    )
    return graph, out, cfg


# ------------------------------------------------------------------ 中断 --
class TestInterrupt:
    async def test_stops_with_preview(self):
        graph, out, cfg = await _start([plan_with(["博物馆"])])

        assert out.get("__interrupt__"), "应当停在 interrupt"
        payload = out["__interrupt__"][0].value
        assert payload["selected"] == [1]
        assert payload["preview"]["days"][0]["after"] == ["博物馆"]

        snap = await graph.aget_state(cfg)
        assert "confirm" in snap.next

    async def test_resume_approve_produces_plan(self):
        graph, out, cfg = await _start([plan_with(["博物馆"])])
        final = await graph.ainvoke(Command(resume="approve"), cfg)

        plan = final.get("final_plan")
        assert plan is not None
        assert [a.name for a in plan.days[1].attractions] == ["博物馆"]
        # 其余天逐字不变
        assert [a.name for a in plan.days[0].attractions] == ["D0A"]
        assert [a.name for a in plan.days[2].attractions] == ["D2A"]

    async def test_resume_reject_changes_nothing(self):
        graph, out, cfg = await _start([plan_with(["博物馆"])])
        final = await graph.ainvoke(Command(resume="reject"), cfg)

        assert final.get("final_plan") is None
        assert any("取消" in e for e in final.get("errors", []))

    async def test_preview_before_after(self):
        graph, out, cfg = await _start([plan_with(["博物院", "美术馆"])])
        row = out["__interrupt__"][0].value["preview"]["days"][0]
        assert row["before"] == ["D1A"]
        assert row["after"] == ["博物院", "美术馆"]


# -------------------------------------------------------------- 守门重试 --
class TestGuard:
    async def test_retry_once_then_success(self):
        # 第一次偷偷改了第 1 天；第二次守规矩
        bad = plan_with(["博物馆"], day0_name="偷偷改的")
        good = plan_with(["博物馆"])
        graph, out, cfg = await _start([bad, good])

        assert out.get("__interrupt__"), "重试成功后应当停在确认"
        final = await graph.ainvoke(Command(resume="approve"), cfg)
        assert final["final_plan"].days[0].attractions[0].name == "D0A"

    async def test_two_failures_end_with_error(self):
        bad = plan_with(["博物馆"], day0_name="乱改A")
        bad2 = plan_with(["博物馆"], day0_name="乱改B")
        graph, out, cfg = await _start([bad, bad2])

        assert not out.get("__interrupt__"), "两次都越界不该进入确认"
        assert out.get("draft") is None
        assert any("局部性" in e for e in out.get("errors", []))

    async def test_structure_change_rejected(self):
        plan = make_plan()
        plan.days[1].date = "2026-12-31"
        plan.days[1].attractions = [Attraction(name="博物馆", visit_duration=60)]
        import json

        text = json.dumps(plan.model_dump(), ensure_ascii=False)
        graph, out, cfg = await _start([text, text])
        assert not out.get("__interrupt__")
        assert out.get("draft") is None

    async def test_empty_day_rejected(self):
        text = plan_with([])
        graph, out, cfg = await _start([text, text])
        assert not out.get("__interrupt__")
        assert any("景点" in e for e in out.get("errors", []))


# ---------------------------------------------------------------- 预算 --
class TestBudgetInReplan:
    async def test_budget_recomputed_after_approve(self):
        graph, out, cfg = await _start([plan_with(["博物馆"])])
        final = await graph.ainvoke(Command(resume="approve"), cfg)
        budget = final["final_plan"].budget
        assert budget.total == (
            budget.total_attractions + budget.total_hotels + budget.total_meals + budget.total_transportation
        )
        # 第 2 天门票变 0（原 10），合计应比原来少 10
        assert budget.total_attractions == 20

    async def test_transportation_kept(self):
        graph, out, cfg = await _start([plan_with(["博物馆"])])
        final = await graph.ainvoke(Command(resume="approve"), cfg)
        assert final["final_plan"].budget.total_transportation == 50


# ------------------------------------------------------------- 多天选择 --
class TestMultipleDays:
    async def test_two_selected_days(self):
        plan = make_plan()
        plan.days[1].attractions = [Attraction(name="新1", visit_duration=60)]
        plan.days[2].attractions = [Attraction(name="新2", visit_duration=60)]
        import json

        text = json.dumps(plan.model_dump(), ensure_ascii=False)
        graph, out, cfg = await _start([text], selected=(1, 2))

        payload = out["__interrupt__"][0].value
        assert [d["day_index"] for d in payload["preview"]["days"]] == [1, 2]
        final = await graph.ainvoke(Command(resume="approve"), cfg)
        assert [a.name for a in final["final_plan"].days[0].attractions] == ["D0A"]

    async def test_no_selection_rejected(self):
        graph, out, cfg = await _start([plan_with(["博物馆"])], selected=())
        assert any("没有选中" in e for e in out.get("errors", []))
