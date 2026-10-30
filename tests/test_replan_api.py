"""接口层测试：pending → confirm 两段式、409 并发保护、拒绝不改库。

模型用假对象替换：`app.api.build_llm` 打补丁即可，不用网络。
对应验收标准 2、3、7、8。
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app.api as api_module  # noqa: E402
from app.models import Attraction, Budget, DayPlan, Hotel, Meal, TripPlan  # noqa: E402
from app.replan import reset_graph  # noqa: E402
from app.memory import UserTripStore  # noqa: E402


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


def model_reply(day2: list[str], *, day0: str = "D0A") -> str:
    import json

    plan = make_plan()
    plan.days[0].attractions = [Attraction(name=day0, visit_duration=60, ticket_price=10)]
    plan.days[1].attractions = [Attraction(name=n, visit_duration=60) for n in day2]
    return json.dumps(plan.model_dump(), ensure_ascii=False)


class ScriptLLM(BaseChatModel):
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


@pytest.fixture()
def client(tmp_path, monkeypatch) -> TestClient:
    store = UserTripStore(tmp_path / "trips")
    monkeypatch.setattr(api_module, "_store", lambda: store)
    monkeypatch.setattr(api_module, "_replan_sessions", {})
    reset_graph()
    return TestClient(api_module.app)


def _seed(client: TestClient) -> str:
    plan = make_plan().model_dump()
    r = client.post(
        "/api/trips",
        json={"plan": plan, "title": "南京三日"},
        headers={"X-User-Id": "u-test"},
    )
    assert r.status_code == 200, r.text
    return r.json()["trip"]["id"]


def _patch_llm(monkeypatch, replies: list[str]) -> ScriptLLM:
    llm = ScriptLLM(replies=replies)
    monkeypatch.setattr(api_module, "build_llm", lambda env: llm)
    return llm


class TestReplanApi:
    def test_pending_then_apply(self, client, monkeypatch):
        trip_id = _seed(client)
        _patch_llm(monkeypatch, [model_reply(["博物馆"])])
        h = {"X-User-Id": "u-test"}

        r1 = client.post(
            f"/api/trips/{trip_id}/replan",
            json={"selected_days": [1], "requirement": "换成室内"},
            headers=h,
        )
        assert r1.status_code == 200, r1.text
        body = r1.json()
        assert body["status"] == "pending"
        assert body["preview"]["days"][0]["after"] == ["博物馆"]
        assert body["attempts"] == 1, "一次就守住局部性时应为 1"

        r2 = client.post(
            f"/api/trips/{trip_id}/replan/confirm", json={"approve": True}, headers=h
        )
        assert r2.status_code == 200, r2.text
        applied = r2.json()
        assert applied["status"] == "applied"
        assert [a["name"] for a in applied["plan"]["days"][1]["attractions"]] == ["博物馆"]
        # 落库了
        got = client.get(f"/api/trips/{trip_id}", headers=h).json()
        assert [a["name"] for a in got["plan"]["days"][1]["attractions"]] == ["博物馆"]

    def test_reject_keeps_original(self, client, monkeypatch):
        trip_id = _seed(client)
        _patch_llm(monkeypatch, [model_reply(["博物馆"])])
        h = {"X-User-Id": "u-test"}

        client.post(
            f"/api/trips/{trip_id}/replan",
            json={"selected_days": [1], "requirement": "换成室内"},
            headers=h,
        )
        r = client.post(
            f"/api/trips/{trip_id}/replan/confirm", json={"approve": False}, headers=h
        )
        assert r.json()["status"] == "rejected"
        got = client.get(f"/api/trips/{trip_id}", headers=h).json()
        assert [a["name"] for a in got["plan"]["days"][1]["attractions"]] == ["D1A"]

    def test_second_request_gets_409(self, client, monkeypatch):
        trip_id = _seed(client)
        _patch_llm(monkeypatch, [model_reply(["博物馆"])])
        h = {"X-User-Id": "u-test"}

        first = client.post(
            f"/api/trips/{trip_id}/replan",
            json={"selected_days": [1], "requirement": "x"},
            headers=h,
        )
        assert first.status_code == 200
        second = client.post(
            f"/api/trips/{trip_id}/replan",
            json={"selected_days": [2], "requirement": "y"},
            headers=h,
        )
        assert second.status_code == 409, second.text
        assert "未完成" in second.json()["detail"]

        # 收尾，避免影响其他用例
        client.post(f"/api/trips/{trip_id}/replan/confirm", json={"approve": False}, headers=h)

    def test_confirm_without_session_is_404(self, client):
        trip_id = _seed(client)
        r = client.post(
            f"/api/trips/{trip_id}/replan/confirm",
            json={"approve": True},
            headers={"X-User-Id": "u-test"},
        )
        assert r.status_code == 404

    def test_unknown_trip_is_404(self, client, monkeypatch):
        _patch_llm(monkeypatch, [model_reply(["博物馆"])])
        r = client.post(
            "/api/trips/does-not-exist-123/replan",
            json={"selected_days": [0], "requirement": "x"},
            headers={"X-User-Id": "u-test"},
        )
        assert r.status_code == 404

    def test_day_index_out_of_range_is_400(self, client, monkeypatch):
        trip_id = _seed(client)
        _patch_llm(monkeypatch, [model_reply(["博物馆"])])
        r = client.post(
            f"/api/trips/{trip_id}/replan",
            json={"selected_days": [9], "requirement": "x"},
            headers={"X-User-Id": "u-test"},
        )
        assert r.status_code == 400
        assert "越界" in r.json()["detail"]

    def test_empty_selection_rejected_by_schema(self, client):
        trip_id = _seed(client)
        r = client.post(
            f"/api/trips/{trip_id}/replan",
            json={"selected_days": [], "requirement": "x"},
            headers={"X-User-Id": "u-test"},
        )
        assert r.status_code == 422  # pydantic min_length=1

    def test_guard_failure_is_422(self, client, monkeypatch):
        trip_id = _seed(client)
        bad = model_reply(["博物馆"], day0="乱改")
        _patch_llm(monkeypatch, [bad, bad])
        r = client.post(
            f"/api/trips/{trip_id}/replan",
            json={"selected_days": [1], "requirement": "x"},
            headers={"X-User-Id": "u-test"},
        )
        assert r.status_code == 422, r.text
        assert "局部性" in r.json()["detail"]
        # 原计划没被动
        got = client.get(f"/api/trips/{trip_id}", headers={"X-User-Id": "u-test"}).json()
        assert [a["name"] for a in got["plan"]["days"][0]["attractions"]] == ["D0A"]

    def test_cross_user_cannot_replan(self, client, monkeypatch):
        trip_id = _seed(client)
        _patch_llm(monkeypatch, [model_reply(["博物馆"])])
        r = client.post(
            f"/api/trips/{trip_id}/replan",
            json={"selected_days": [0], "requirement": "x"},
            headers={"X-User-Id": "someone-else"},
        )
        assert r.status_code == 404
