"""条件路由测试：晴天走直线、雨天插 adjust、生成后能查出违规。

不依赖代理、不依赖高德：天气与 POI 都用固定文本喂进去。
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.amap import FakeAmap  # noqa: E402
from app.graph import _after_weather, build_graph  # noqa: E402
from app.models import TripRequest  # noqa: E402
from app.nodes import parse_trip_plan  # noqa: E402
from app.routing import (  # noqa: E402
    bad_weather,
    classify_poi,
    needs_adjust,
    rainy_days,
    verify_plan,
)

RAIN_WEATHER = (
    '{"city":"南京","forecasts":['
    '{"date":"2026-11-01","dayweather":"中雨","nightweather":"小雨",'
    '"daytemp":"15","nighttemp":"11","daywind":"东","daypower":"3"},'
    '{"date":"2026-11-02","dayweather":"晴","nightweather":"多云",'
    '"daytemp":"20","nighttemp":"12","daywind":"北","daypower":"2"}]}'
)

SUNNY_WEATHER = (
    '{"city":"南京","forecasts":['
    '{"date":"2026-11-01","dayweather":"晴","nightweather":"多云",'
    '"daytemp":"20","nighttemp":"12","daywind":"北","daypower":"2"}]}'
)


class WeatherAmap(FakeAmap):
    """只改天气，其它走父类的假数据。"""

    def __init__(self, weather: str, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.weather = weather

    async def call(self, name: str, arguments: dict[str, Any]) -> str:
        if name == "maps_weather":
            self.calls.append((name, arguments))
            return self.weather
        return await super().call(name, arguments)


PLAN_RAINY = """{
  "city": "南京",
  "start_date": "2026-11-01",
  "end_date": "2026-11-01",
  "days": [
    {"date": "2026-11-01", "day_index": 0, "description": "",
     "attractions": [
       {"name": "玄武湖公园", "address": "", "location": {"longitude": 118.79, "latitude": 32.07},
        "visit_duration": 120, "description": "", "ticket_price": 0}
     ],
     "meals": [], "hotel": null}
  ],
  "weather_info": [],
  "overall_suggestions": "",
  "budget": {"total_attractions": 0, "total_hotels": 0, "total_meals": 0,
             "total_transportation": 0, "total": 0}
}"""

PLAN_INDOOR = PLAN_RAINY.replace("玄武湖公园", "南京博物院")


class FakeLLM(BaseChatModel):
    reply: str = PLAN_INDOOR
    prompts: list[str] = []

    @property
    def _llm_type(self) -> str:
        return "fake"

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        # 把系统提示词留存下来，用来断言"路由约束确实进了提示词"
        object.__setattr__(self, "prompts", [getattr(m, "content", "") for m in messages])
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=self.reply))])


def _request() -> TripRequest:
    return TripRequest(city="南京", start_date="2026-11-01", end_date="2026-11-01", days=1)


async def _run(tools, reply: str = PLAN_INDOOR) -> tuple[dict[str, Any], FakeLLM]:
    llm = FakeLLM(reply=reply)
    graph = build_graph()
    out = await graph.ainvoke(
        {
            "request": _request(),
            "tools": tools,
            "llm": llm,
            "attractions_raw": "",
            "weather_raw": "",
            "hotels_raw": "",
            "plan": None,
            "raw_plan": "",
            "errors": [],
            "trace": [],
        }
    )
    return out, llm


# ------------------------------------------------------------------ 决策 --
class TestWeatherJudgement:
    @pytest.mark.parametrize(
        "text,expected",
        [
            ("小雨", True),
            ("中雨", True),
            ("雷阵雨", True),
            ("雨夹雪", True),
            ("小雪", True),
            ("多云", False),
            ("晴", False),
            ("阴", False),
            ("雾", False),
            ("", False),
        ],
    )
    def test_bad_weather(self, text, expected):
        assert bad_weather(text) is expected

    def test_rainy_days_picks_only_bad(self):
        info = [
            {"date": "2026-11-01", "day_weather": "中雨", "night_weather": "小雨"},
            {"date": "2026-11-02", "day_weather": "晴", "night_weather": "多云"},
        ]
        days = rainy_days(info)
        assert [d["date"] for d in days] == ["2026-11-01"]
        assert "雨" in days[0]["reason"]

    def test_night_rain_also_counts(self):
        info = [{"date": "2026-11-01", "day_weather": "多云", "night_weather": "阵雨"}]
        assert needs_adjust(info) is True

    def test_no_rain(self):
        assert needs_adjust([{"date": "2026-11-01", "day_weather": "晴"}]) is False
        assert needs_adjust([]) is False


class TestClassify:
    @pytest.mark.parametrize(
        "name,kind",
        [
            ("南京博物院", "indoor"),
            ("苏州博物馆", "indoor"),
            ("上海科技馆", "indoor"),
            ("玄武湖公园", "outdoor"),
            ("中山陵", "outdoor"),
            ("夫子庙", "outdoor"),
            ("某某大厦", "unknown"),
            ("", "unknown"),
        ],
    )
    def test_classify(self, name, kind):
        assert classify_poi(name) == kind


class TestVerify:
    def test_outdoor_on_rainy_day_is_violation(self):
        plan = parse_trip_plan(PLAN_RAINY)
        found = verify_plan(plan, ["2026-11-01"])
        assert len(found) == 1
        assert "玄武湖公园" in found[0]["outdoor"]
        assert "室外" in found[0]["message"]

    def test_indoor_on_rainy_day_is_fine(self):
        plan = parse_trip_plan(PLAN_INDOOR)
        assert verify_plan(plan, ["2026-11-01"]) == []

    def test_sunny_day_not_checked(self):
        plan = parse_trip_plan(PLAN_RAINY)
        assert verify_plan(plan, ["2026-11-02"]) == []

    def test_unknown_kind_not_reported(self):
        plan = parse_trip_plan(PLAN_RAINY.replace("玄武湖公园", "某某大厦"))
        assert verify_plan(plan, ["2026-11-01"]) == []


# ------------------------------------------------------------------ 图 --
class TestGraphRouting:
    async def test_rain_takes_adjust_branch(self):
        tools = WeatherAmap(RAIN_WEATHER)
        out, llm = await _run(tools)

        # 图里真的走了 adjust：trace 有它，且补查了室内候选
        assert any("adjust" in t for t in out["trace"]), out["trace"]
        assert out["routing"]["needed"] is True
        assert out["routing"]["days"][0]["date"] == "2026-11-01"
        assert out["routing"]["indoor_raw_chars"] > 0

        # 室内候选查询确实发了（keywords 含"博物馆"）
        indoor_calls = [c for c in tools.calls if "博物馆" in str(c[1].get("keywords", ""))]
        assert indoor_calls, tools.calls

        # 约束进了系统提示词
        system = llm.prompts[0]
        assert "2026-11-01" in system and "室内" in system

    async def test_sunny_skips_adjust(self):
        tools = WeatherAmap(SUNNY_WEATHER)
        out, _ = await _run(tools)

        assert not any("adjust" in t for t in out["trace"]), out["trace"]
        assert not any("博物馆" in str(c[1].get("keywords", "")) for c in tools.calls)

    async def test_violation_recorded_when_model_ignores_constraint(self):
        tools = WeatherAmap(RAIN_WEATHER)
        out, _ = await _run(tools, reply=PLAN_RAINY)  # 模型硬排了室外景点

        assert out["plan"] is not None
        violations = out["routing"]["violations"]
        assert len(violations) == 1
        assert any("室外" in e for e in out["errors"])

    async def test_indoor_plan_has_no_violation(self):
        tools = WeatherAmap(RAIN_WEATHER)
        out, _ = await _run(tools, reply=PLAN_INDOOR)
        assert out["routing"]["violations"] == []


class TestRouterFunction:
    def test_router_reads_weather_raw(self):
        state = {"request": _request(), "weather_raw": RAIN_WEATHER}
        assert _after_weather(state) == "adjust"

    def test_router_without_request(self):
        assert _after_weather({}) == "hotel"

    def test_router_sunny(self):
        state = {"request": _request(), "weather_raw": SUNNY_WEATHER}
        assert _after_weather(state) == "hotel"
