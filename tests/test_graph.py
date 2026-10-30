"""图级测试：不需要高德 key，也不需要真实 LLM。

用 FakeAmap 提供工具数据、用假的 chat model 提供规划 JSON，
验证的是「图能不能跑通、契约能不能校验、预算会不会自相矛盾」——
这三件事和模型聪不聪明无关，属于必须稳的部分。
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

from app.amap import (  # noqa: E402
    AmapKeyError,
    AmapToolError,
    FakeAmap,
    _classify_preflight,
)
from app.graph import build_graph  # noqa: E402
from app.models import TripPlan, TripRequest  # noqa: E402
from app.nodes import parse_trip_plan  # noqa: E402

PLAN_JSON = """{
  "city": "北京",
  "start_date": "2026-11-01",
  "end_date": "2026-11-02",
  "days": [
    {
      "date": "2026-11-01", "day_index": 0,
      "description": "故宫 + 天坛",
      "transportation": "公共交通", "accommodation": "经济型酒店",
      "hotel": {"name": "如家酒店(前门店)", "estimated_cost": 320},
      "attractions": [
        {"name": "故宫博物院", "address": "东城区景山前街4号",
         "visit_duration": 180, "ticket_price": 60,
         "location": {"longitude": 116.397, "latitude": 39.916}},
        {"name": "天坛公园", "visit_duration": 120, "ticket_price": 15,
         "location": {"longitude": 116.416, "latitude": 39.882}}
      ],
      "meals": [
        {"type": "lunch", "name": "全聚德(前门店)", "estimated_cost": 120}
      ]
    },
    {
      "date": "2026-11-02", "day_index": 1,
      "description": "胡同漫步",
      "transportation": "公共交通", "accommodation": "经济型酒店",
      "attractions": [
        {"name": "南锣鼓巷", "visit_duration": 120, "ticket_price": 0}
      ],
      "meals": [{"type": "dinner", "name": "小吃", "estimated_cost": 80}]
    }
  ],
  "weather_info": [
    {"date": "2026-11-01", "day_weather": "晴", "night_weather": "多云",
     "day_temp": "18°C", "night_temp": "9°C"}
  ],
  "overall_suggestions": "早晚温差大，带外套。",
  "budget": {
    "total_attractions": 75, "total_hotels": 320,
    "total_meals": 200, "total_transportation": 50, "total": 999
  }
}"""


class FakeLLM(BaseChatModel):
    """吐固定 JSON 的假模型；用来把「图」和「模型」解耦。"""

    reply: str = PLAN_JSON
    calls: int = 0

    @property
    def _llm_type(self) -> str:
        return "fake"

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        object.__setattr__(self, "calls", self.calls + 1)
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=self.reply))])


def _request(days: int = 2) -> TripRequest:
    return TripRequest(
        city="北京",
        start_date="2026-11-01",
        end_date="2026-11-02",
        days=days,
    )


async def _run(tools, reply: str = PLAN_JSON) -> dict[str, Any]:
    graph = build_graph()
    return await graph.ainvoke(
        {
            "request": _request(),
            "tools": tools,
            "llm": FakeLLM(reply=reply),
            "attractions_raw": "",
            "weather_raw": "",
            "hotels_raw": "",
            "plan": None,
            "raw_plan": "",
            "errors": [],
            "trace": [],
        }
    )


# --------------------------------------------------------------- 图能跑通 --
class TestGraphHappyPath:
    async def test_graph_produces_valid_plan(self):
        tools = FakeAmap()
        final = await _run(tools)

        assert final["errors"] == []
        plan = final["plan"]
        assert isinstance(plan, TripPlan)
        assert plan.city == "北京"
        assert len(plan.days) == 2

    async def test_three_tools_are_called_in_order(self):
        tools = FakeAmap()
        await _run(tools)
        assert [name for name, _ in tools.calls] == [
            "maps_text_search",
            "maps_weather",
            "maps_text_search",
        ]

    async def test_tool_arguments_follow_the_request(self):
        tools = FakeAmap()
        await _run(tools)
        _, first_args = tools.calls[0]
        assert first_args["city"] == "北京"
        assert "历史文化" in first_args["keywords"]

    async def test_trace_records_every_node(self):
        final = await _run(FakeAmap())
# 每个节点都要留痕；但一个节点可能留两条（如 hotel 自己写"搜索词被调整" + 包装器写字符数），
        # 所以断言"每个节点都出现"，不锁死总条数。
        assert len(final["trace"]) >= 4
        joined = "\n".join(final["trace"])
        for name in ("attraction", "weather", "hotel", "planner"):
            assert name in joined, f"trace 里缺 {name}：{final['trace']}"
        assert final["trace"][0].startswith("attraction")


# ------------------------------------------------------------------ 预算 --
class TestBudget:
    async def test_total_is_recomputed_from_parts(self):
        """模型给的 total=999 是错的（分项之和 645），必须以分项为准。"""
        final = await _run(FakeAmap())
        budget = final["plan"].budget
        assert budget.total == 75 + 320 + 200 + 50
        assert budget.total != 999


# -------------------------------------------------------------- 容错路径 --
class TestFailurePaths:
    async def test_one_tool_failure_is_recorded_and_graph_continues(self):
        tools = FakeAmap(fail_tool="maps_weather")
        final = await _run(tools)

        assert any("天气查询失败" in e for e in final["errors"])
        # 天气挂了不影响规划：景点和酒店还在
        assert final["plan"] is not None

    async def test_unparsable_output_keeps_raw_text(self):
        final = await _run(FakeAmap(), reply="抱歉，我无法生成行程。")
        assert final["plan"] is None
        assert any("解析失败" in e for e in final["errors"])
        assert "抱歉" in final["raw_plan"]


class TestNormalisation:
    async def test_day_index_is_renumbered_from_zero(self):
        """模型给 1、2，出图后必须是 0、1——序号不交给模型决定。"""
        shifted = PLAN_JSON.replace('"day_index": 0', '"day_index": 1').replace(
            '"day_index": 1', '"day_index": 2'
        )
        final = await _run(FakeAmap(), reply=shifted)
        assert [d.day_index for d in final["plan"].days] == [0, 1]

    async def test_fake_amap_branches_on_keywords(self):
        """关键词含"酒店"时返回住宿，否则返回景点——否则测试会掩盖字段混用。"""
        hotels = await FakeAmap().call(
            "maps_text_search", {"keywords": "经济型酒店", "city": "北京"}
        )
        attractions = await FakeAmap().call(
            "maps_text_search", {"keywords": "历史文化景点", "city": "北京"}
        )
        assert "酒店" in hotels
        assert "故宫" in attractions


# ------------------------------------------------------------- 解析与校验 --
class TestParsing:
    def test_strips_markdown_fence(self):
        wrapped = "```json\n" + PLAN_JSON + "\n```"
        plan = parse_trip_plan(wrapped)
        assert plan.city == "北京"

    def test_tolerates_surrounding_prose(self):
        noisy = "好的，以下是行程：\n" + PLAN_JSON + "\n希望对你有帮助。"
        assert parse_trip_plan(noisy).city == "北京"

    def test_rejects_output_without_json(self):
        with pytest.raises(ValueError):
            parse_trip_plan("今天天气不错")

    def test_temperature_string_is_parsed(self):
        plan = parse_trip_plan(PLAN_JSON)
        assert plan.weather_info[0].day_temp == 18
        assert plan.weather_info[0].night_temp == 9

    def test_schema_violation_is_rejected(self):
        bad = PLAN_JSON.replace('"ticket_price": 60', '"ticket_price": -5')
        with pytest.raises(Exception):
            parse_trip_plan(bad)


class TestAmapErrors:
    async def test_is_error_becomes_exception(self):
        """MCP 把失败表达成 isError=True 的文本；必须翻成异常，否则会被当数据喂给模型。"""
        tools = FakeAmap(fail_tool="maps_text_search")
        with pytest.raises(AmapToolError):
            await tools.call("maps_text_search", {"keywords": "x", "city": "北京"})


class TestPayloadValidation:
    """高德会返回"空壳"载荷（不是 isError），必须在节点里挡住。"""

    def test_short_weather_shell_is_rejected(self):
        from app.nodes import _check_payload

        assert _check_payload("weather", '{"city":"","forecasts":[]}')
        assert _check_payload("weather", "") == "返回为空"

    def test_real_weather_passes(self):
        from app.nodes import _check_payload

        good = '{"city":"北京市","forecasts":[{"date":"2026-10-27","dayweather":"晴"}]}'
        assert _check_payload("weather", good) == ""

    def test_poi_without_pois_is_rejected(self):
        from app.nodes import _check_payload

        assert _check_payload("poi", '{"suggestion":{}}')

    def test_real_poi_passes(self):
        from app.nodes import _check_payload

        assert _check_payload("poi", '[{"pois":[{"name":"故宫"}]}]') == ""

    async def test_bad_weather_is_recorded_not_passed_on(self):
        """空壳天气不应进入 state 的 weather_raw。"""

        class ShellAmap(FakeAmap):
            async def call(self, name, arguments):
                if name == "maps_weather":
                    return '{"city":"","forecasts":[]}'
                return await super().call(name, arguments)

        final = await _run(ShellAmap())
        joined = " | ".join(final["errors"])
        assert "数据异常" in joined  # 重试后仍失败，记一条错误
        assert final.get("weather_raw", "") == ""
        assert final["plan"] is not None  # 其余步骤照常
        # 关键：天气没拿到时不能把模型编的那条留着
        assert final["plan"].weather_info == []


class TestRetry:
    """服务端偶发返回空壳时，重试一次应该救回来。"""

    async def test_shell_then_good_weather_succeeds(self):
        class FlakyAmap(FakeAmap):
            def __init__(self):
                super().__init__()
                self.n = 0

            async def call(self, name, arguments):
                if name == "maps_weather":
                    self.n += 1
                    if self.n == 1:
                        return '{"city":"","forecasts":[]}'  # 第一次给空壳
                return await super().call(name, arguments)

        final = await _run(FlakyAmap())
        assert final["errors"] == []          # 重试成功，没有错误
        assert final["weather_raw"]           # 天气拿到了
        assert final["plan"].weather_info     # 且没有被清空

    async def test_always_shell_reports_error_after_retries(self):
        class ShellAmap2(FakeAmap):
            def __init__(self):
                super().__init__()
                self.n = 0

            async def call(self, name, arguments):
                if name == "maps_weather":
                    self.n += 1
                    return '{"city":"","forecasts":[]}'
                return await super().call(name, arguments)

        tools = ShellAmap2()
        final = await _run(tools)
        assert tools.n == 2                    # 试了两次
        assert any("天气查询失败" in e for e in final["errors"])


class TestWeatherFromRaw:
    """天气必须由程序从工具原文重建，不能靠模型抄。"""

    def test_matching_dates_are_picked(self):
        from app.nodes import _weather_from_raw

        raw = (
            '{"city":"南京市","forecasts":['
            '{"date":"2026-12-20","dayweather":"晴","nightweather":"多云",'
            '"daytemp":"10","nighttemp":"2","daywind":"北","daypower":"1-3"},'
            '{"date":"2026-12-21","dayweather":"阴","nightweather":"雨",'
            '"daytemp":"9","nighttemp":"3","daywind":"东","daypower":"1-3"}]}'
        )
        got = _weather_from_raw(raw, "2026-12-20", 2)
        assert [g["date"] for g in got] == ["2026-12-20", "2026-12-21"]
        assert got[0]["day_temp"] == "10"

    def test_no_fallback_when_dates_do_not_match(self):
        """预报里没有行程日期时返回空，绝不拿别的日期顶替。

        第一版是"匹配不上就用原文前 N 天"，实测出过 11-01 的行程配 10-28 的天气：
        日期本身是真的，但张冠李戴——用户会以为那天下雨，条件路由也会拿错日期比对。
        没有预报是"不知道"，不是"随便给一个"。
        """
        from app.nodes import _weather_from_raw

        raw = '{"forecasts":[{"date":"2026-10-27","dayweather":"晴","daytemp":"16","nighttemp":"1"}]}'
        assert _weather_from_raw(raw, "2026-12-20", 3) == []

    def test_partial_match_returns_only_matching_days(self):
        from app.nodes import _weather_from_raw

        raw = (
            '{"forecasts":['
            '{"date":"2026-10-27","dayweather":"晴","daytemp":"16","nighttemp":"8"},'
            '{"date":"2026-12-21","dayweather":"小雨","daytemp":"9","nighttemp":"3"}]}'
        )
        got = _weather_from_raw(raw, "2026-12-20", 2)
        assert [g["date"] for g in got] == ["2026-12-21"]
        assert got[0]["day_weather"] == "小雨"

    def test_broken_payload_returns_empty(self):
        from app.nodes import _weather_from_raw

        assert _weather_from_raw("not json", "2026-12-20", 2) == []
        assert _weather_from_raw("", "2026-12-20", 2) == []


class TestEmptyNestedModels:
    """模型常用 `{}` 表示"这天不需要"；不能让必填字段把它判成非法。"""

    def test_empty_hotel_dict_becomes_none(self):
        plan = parse_trip_plan(PLAN_JSON)
        assert plan.days[0].hotel is not None  # 原来有值
        variant = PLAN_JSON.replace(
            '"hotel": {"name": "如家酒店(前门店)", "estimated_cost": 320}', '"hotel": {}'
        )
        assert parse_trip_plan(variant).days[0].hotel is None

    def test_hotel_without_name_becomes_none(self):
        variant = PLAN_JSON.replace(
            '"hotel": {"name": "如家酒店(前门店)", "estimated_cost": 320}',
            '"hotel": {"estimated_cost": 320}',
        )
        assert parse_trip_plan(variant).days[0].hotel is None

    def test_empty_location_dict_becomes_none(self):
        variant = PLAN_JSON.replace(
            '"location": {"longitude": 116.397, "latitude": 39.916}', '"location": {}'
        )
        plan = parse_trip_plan(variant)
        assert plan.days[0].attractions[0].location is None

    def test_real_location_still_parsed(self):
        plan = parse_trip_plan(PLAN_JSON)
        assert plan.days[0].attractions[0].location is not None
        assert plan.days[0].attractions[0].location.longitude == 116.397


class TestMarkdownRendering:
    """渲染层的小坑：模型用 "无" 表示当天不住宿。"""

    def test_placeholder_hotel_name_is_skipped(self):
        from app.cli import to_markdown
        from app.models import Hotel

        plan = parse_trip_plan(PLAN_JSON)
        plan.days[1].hotel = Hotel(name="无", estimated_cost=0)
        assert "住宿：无" not in to_markdown(plan)

    def test_normal_hotel_is_rendered(self):
        from app.cli import to_markdown

        assert "住宿：如家酒店(前门店)（320 元/晚）" in to_markdown(parse_trip_plan(PLAN_JSON))


class TestPreflight:
    """预检决定「卡住」还是「报错」：key 无效必须在毫秒级失败。"""

    def test_invalid_key_raises_key_error(self):
        with pytest.raises(AmapKeyError):
            _classify_preflight(
                200, '{"status":"0","info":"INVALID_USER_KEY","infocode":"10001"}'
            )

    def test_infocode_10001_also_counts(self):
        with pytest.raises(AmapKeyError):
            _classify_preflight(200, '{"infocode":"10001"}')

    def test_http_error_raises_tool_error(self):
        with pytest.raises(AmapToolError):
            _classify_preflight(503, "service unavailable")

    def test_healthy_response_passes(self):
        _classify_preflight(200, '{"jsonrpc":"2.0","id":1,"result":{}}')
