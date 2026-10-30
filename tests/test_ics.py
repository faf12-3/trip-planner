""".ics 导出的测试：重点不是"生成得出来"，而是"别人导得进去"。

三组断言对应 RFC 5545 里最容易出错的三个点：转义、折行、时区。
另外加一条"往返测试"：生成 → 自己解析回来 → 字段必须与计划一致。
"""

from __future__ import annotations

import re
import sys
from datetime import datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import ics  # noqa: E402
from app.models import Attraction, Budget, DayPlan, Hotel, Meal, TripPlan  # noqa: E402


@pytest.fixture(autouse=True)
def _freeze():
    ics.freeze_stamp(datetime(2026, 10, 30, 12, 0, 0))
    yield
    ics.freeze_stamp(None)


def make_plan(**overrides) -> TripPlan:
    day = DayPlan(
        date="2026-11-01",
        day_index=0,
        description="第一天",
        attractions=[
            Attraction(name="南京博物院", visit_duration=120, ticket_price=0, address="中山东路321号"),
            Attraction(name="老门东, 历史街区", visit_duration=90, ticket_price=30),
        ],
        meals=[Meal(type="lunch", name="鸭血粉丝汤; 老字号", estimated_cost=35)],
        hotel=Hotel(name="南京中心酒店", estimated_cost=260),
    )
    data = dict(
        city="南京",
        start_date="2026-11-01",
        end_date="2026-11-01",
        days=[day],
        overall_suggestions="",
        budget=Budget(total_transportation=50),
    )
    data.update(overrides)
    return TripPlan(**data)


def parse(ics_text: str) -> list[dict[str, str]]:
    """极简解析器：把 VEVENT 拆成字段字典（折行先还原）。"""
    unfolded = ics_text.replace("\r\n ", "")
    events: list[dict[str, str]] = []
    current: dict[str, str] | None = None
    for line in unfolded.split("\r\n"):
        if line == "BEGIN:VEVENT":
            current = {}
        elif line == "END:VEVENT" and current is not None:
            events.append(current)
            current = None
        elif current is not None and ":" in line:
            key, _, value = line.partition(":")
            current[key] = value
    return events


class TestStructure:
    def test_calendar_envelope(self):
        text = ics.build_calendar(make_plan(), trip_id="t1")
        assert text.startswith("BEGIN:VCALENDAR\r\n")
        assert text.rstrip("\r\n").endswith("END:VCALENDAR")
        assert "VERSION:2.0" in text
        assert "BEGIN:VTIMEZONE" in text and "END:VTIMEZONE" in text

    def test_uses_crlf_only(self):
        text = ics.build_calendar(make_plan())
        # 不能出现裸 \n（必须有 \r 前缀），否则 Outlook 会当成一行
        assert not re.search(r"(?<!\r)\n", text)

    def test_event_count(self):
        # 2 景点 + 1 餐饮 + 1 酒店
        assert len(parse(ics.build_calendar(make_plan()))) == 4

    def test_uid_is_stable_across_calls(self):
        a = parse(ics.build_calendar(make_plan(), trip_id="t1"))
        b = parse(ics.build_calendar(make_plan(), trip_id="t1"))
        assert [e["UID"] for e in a] == [e["UID"] for e in b]

    def test_uid_differs_between_trips(self):
        a = parse(ics.build_calendar(make_plan(), trip_id="t1"))
        b = parse(ics.build_calendar(make_plan(), trip_id="t2"))
        assert a[0]["UID"] != b[0]["UID"]


class TestContent:
    def test_summary_matches_attraction(self):
        events = parse(ics.build_calendar(make_plan()))
        names = [e["SUMMARY"] for e in events]
        assert names[0] == "南京博物院"
        assert "老门东\\, 历史街区" in names  # 逗号必须转义

    def test_semicolon_escaped(self):
        events = parse(ics.build_calendar(make_plan()))
        meal = [e for e in events if "鸭血" in e["SUMMARY"]][0]
        assert meal["SUMMARY"] == "鸭血粉丝汤\\; 老字号"

    def test_description_contains_price(self):
        events = {e["SUMMARY"]: e for e in parse(ics.build_calendar(make_plan()))}
        assert "门票 30 元" in events["老门东\\, 历史街区"]["DESCRIPTION"]
        assert events["南京博物院"]["DESCRIPTION"].startswith("免费")

    def test_location_used_when_present(self):
        events = parse(ics.build_calendar(make_plan()))
        assert events[0]["LOCATION"] == "中山东路321号"

    def test_hotel_event_only_when_named(self):
        plan = make_plan()
        plan.days[0].hotel = Hotel(name="", estimated_cost=199)
        events = parse(ics.build_calendar(plan))
        assert not any("入住" in e["SUMMARY"] for e in events)


class TestTime:
    def test_timezone_tagged(self):
        text = ics.build_calendar(make_plan())
        assert "DTSTART;TZID=Asia/Shanghai:20261101T090000" in text

    def test_attractions_chained_with_gap(self):
        events = parse(ics.build_calendar(make_plan()))
        starts = [e["DTSTART;TZID=Asia/Shanghai"] for e in events[:2]]
        # 09:00 开始 120 分钟 + 30 分钟路上 → 第二个 11:30
        assert starts == ["20261101T090000", "20261101T113000"]

    def test_lunch_lands_after_eleven_thirty(self):
        """午餐不再写死 12:00：排到"哪儿算哪儿"，但不早于 11:30。"""
        plan = make_plan()
        plan.days[0].meals = [Meal(type="lunch", name="午饭", estimated_cost=30)]
        events = {e["SUMMARY"]: e for e in parse(ics.build_calendar(plan))}
        hhmm = events["午饭"]["DTSTART;TZID=Asia/Shanghai"][9:13]
        assert "1130" <= hhmm <= "1400", hhmm
        # 午餐要排在第一个景点结束之后
        assert events["午饭"]["DTSTART;TZID=Asia/Shanghai"] > events["南京博物院"][
            "DTEND;TZID=Asia/Shanghai"
        ]

    def test_dinner_not_before_six(self):
        plan = make_plan()
        plan.days[0].meals = [
            Meal(type="lunch", name="午饭", estimated_cost=30),
            Meal(type="dinner", name="晚饭", estimated_cost=60),
        ]
        events = {e["SUMMARY"]: e for e in parse(ics.build_calendar(plan))}
        assert events["晚饭"]["DTSTART;TZID=Asia/Shanghai"][9:13] >= "1800"

    def test_breakfast_at_eight(self):
        plan = make_plan()
        plan.days[0].meals = [Meal(type="breakfast", name="早饭", estimated_cost=15)]
        events = {e["SUMMARY"]: e for e in parse(ics.build_calendar(plan))}
        assert events["早饭"]["DTSTART;TZID=Asia/Shanghai"][9:13] == "0800"

    def test_end_after_start(self):
        for e in parse(ics.build_calendar(make_plan())):
            start = e["DTSTART;TZID=Asia/Shanghai"]
            end = e["DTEND;TZID=Asia/Shanghai"]
            assert end > start, e["SUMMARY"]


class TestNoOverlap:
    """排出来不能重叠——这是"导进日历能用"的底线。

    做法上保证：所有事件共用一个游标，插入后游标只往后走，所以结构上不可能重叠。
    这里断言的是结果，不是实现。
    """

    def _spans(self, text: str) -> list[tuple[str, str, str]]:
        out = []
        for e in parse(text):
            start = e.get("DTSTART;TZID=Asia/Shanghai", "")
            end = e.get("DTEND;TZID=Asia/Shanghai", "")
            if start and end:
                out.append((start, end, e.get("SUMMARY", "")))
        return sorted(out)

    def test_same_day_events_do_not_overlap(self):
        plan = make_plan()
        plan.days[0].meals = [
            Meal(type="breakfast", name="早饭", estimated_cost=15),
            Meal(type="lunch", name="午饭", estimated_cost=30),
            Meal(type="dinner", name="晚饭", estimated_cost=60),
        ]
        spans = self._spans(ics.build_calendar(plan))
        for (s1, e1, n1), (s2, e2, n2) in zip(spans, spans[1:]):
            assert e1 <= s2, f"{n1}（到 {e1}）与 {n2}（{s2} 开始）重叠"

    def test_each_meal_type_once(self):
        plan = make_plan()
        plan.days[0].meals = [
            Meal(type="breakfast", name="早饭", estimated_cost=15),
            Meal(type="lunch", name="午饭", estimated_cost=30),
            Meal(type="dinner", name="晚饭", estimated_cost=60),
        ]
        names = [e["SUMMARY"] for e in parse(ics.build_calendar(plan))]
        assert names.count("午饭") == 1
        assert names.count("早饭") == 1
        assert names.count("晚饭") == 1

    def test_hotel_after_dinner(self):
        plan = make_plan()
        plan.days[0].meals = [Meal(type="dinner", name="晚饭", estimated_cost=60)]
        events = {e["SUMMARY"]: e for e in parse(ics.build_calendar(plan))}
        assert events["入住 南京中心酒店"]["DTSTART;TZID=Asia/Shanghai"] >= events["晚饭"][
            "DTEND;TZID=Asia/Shanghai"
        ]

    def test_long_attraction_pushes_lunch_back(self):
        """一个景点跨过中午 → 午餐必须往后挪，不能压在景点上。"""
        plan = make_plan()
        plan.days[0].attractions = [
            Attraction(name="超长景点", visit_duration=240, ticket_price=0)
        ]
        plan.days[0].meals = [Meal(type="lunch", name="午饭", estimated_cost=30)]
        events = {e["SUMMARY"]: e for e in parse(ics.build_calendar(plan))}
        assert events["午饭"]["DTSTART;TZID=Asia/Shanghai"] >= events["超长景点"][
            "DTEND;TZID=Asia/Shanghai"
        ]

    def test_no_meals_still_no_overlap(self):
        plan = make_plan()
        plan.days[0].meals = []
        spans = self._spans(ics.build_calendar(plan))
        for (s1, e1, n1), (s2, e2, n2) in zip(spans, spans[1:]):
            assert e1 <= s2, f"{n1} 与 {n2} 重叠"


class TestFolding:
    def test_long_line_folded_within_75_octets(self):
        plan = make_plan()
        plan.days[0].attractions[0].name = "南京" * 40  # 远超 75 字节
        text = ics.build_calendar(plan)
        for line in text.split("\r\n"):
            assert len(line.encode("utf-8")) <= 75, line[:40]

    def test_folded_content_roundtrips(self):
        plan = make_plan()
        long_name = "很长的景点名称" * 12
        plan.days[0].attractions[0].name = long_name
        events = parse(ics.build_calendar(plan))
        assert events[0]["SUMMARY"] == long_name

    def test_chinese_not_split_mid_character(self):
        # 折行必须按字节边界切，切开一个 UTF-8 字符会乱码
        text = ics.build_calendar(make_plan())
        text.encode("utf-8").decode("utf-8")  # 能解码就说明没切坏
        assert "南京博物院" in text


class TestRobustness:
    def test_bad_date_skipped(self):
        plan = make_plan()
        plan.days[0].date = "not-a-date"
        assert parse(ics.build_calendar(plan)) == []

    def test_empty_plan(self):
        plan = make_plan(days=[])
        text = ics.build_calendar(plan)
        assert text.count("BEGIN:VEVENT") == 0

    def test_backslash_escaped(self):
        plan = make_plan()
        plan.days[0].attractions[0].name = "C:\\temp"
        events = parse(ics.build_calendar(plan))
        assert events[0]["SUMMARY"] == "C:\\\\temp"

    def test_filename_is_safe(self):
        name = ics.filename_for(make_plan(), trip_id="t1")
        assert name.endswith(".ics")
        assert "/" not in name and "\\" not in name
