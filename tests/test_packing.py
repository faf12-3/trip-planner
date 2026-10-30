"""装备清单的测试：规则引擎的价值全在"改输入 → 输出对得上"。

测法固定：造一份计划 → 断言某一条出现/消失、且 reason 里带着对应事实。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.models import Attraction, Budget, DayPlan, Hotel, Meal, TripPlan, WeatherInfo  # noqa: E402
from app.packing import build_packing  # noqa: E402


def make_plan(
    *,
    days: int = 2,
    weather: list[dict] | None = None,
    transportation: str = "公共交通",
    accommodation: str = "经济型酒店",
) -> TripPlan:
    return TripPlan(
        city="南京",
        start_date="2026-11-01",
        end_date="2026-11-03",
        days=[
            DayPlan(
                date=f"2026-11-0{i + 1}",
                day_index=i,
                description="",
                transportation=transportation,
                accommodation=accommodation,
                attractions=[Attraction(name=f"A{i}", visit_duration=60)],
                meals=[Meal(type="lunch", name="饭", estimated_cost=30)],
                hotel=Hotel(name="酒店", estimated_cost=200),
            )
            for i in range(days)
        ],
        weather_info=[WeatherInfo(**w) for w in (weather or [])],
        overall_suggestions="",
        budget=Budget(total_transportation=50),
    )


def names(pack: dict) -> set[str]:
    return {item["name"] for group in pack["groups"] for item in group["items"]}


def reason_of(pack: dict, name: str) -> str:
    for group in pack["groups"]:
        for item in group["items"]:
            if item["name"] == name:
                return item["reason"]
    return ""


class TestRain:
    def test_rain_adds_umbrella_with_reason(self):
        pack = build_packing(make_plan(weather=[
            {"date": "2026-11-01", "day_weather": "中雨", "night_weather": "阴",
             "day_temp": 18, "night_temp": 12},
        ]))
        assert "折叠伞" in names(pack)
        assert "2026-11-01" in reason_of(pack, "折叠伞")
        assert pack["rainy_days"] == ["2026-11-01"]

    def test_sunny_has_no_umbrella(self):
        pack = build_packing(make_plan(weather=[
            {"date": "2026-11-01", "day_weather": "晴", "night_weather": "多云",
             "day_temp": 22, "night_temp": 15},
        ]))
        assert "折叠伞" not in names(pack)
        assert pack["rainy_days"] == []

    def test_night_rain_counts(self):
        pack = build_packing(make_plan(weather=[
            {"date": "2026-11-01", "day_weather": "多云", "night_weather": "小雨",
             "day_temp": 20, "night_temp": 14},
        ]))
        assert "折叠伞" in names(pack)

    def test_snow_counts_as_rain(self):
        pack = build_packing(make_plan(weather=[
            {"date": "2026-11-01", "day_weather": "小雪", "night_weather": "阴",
             "day_temp": 2, "night_temp": -4},
        ]))
        assert "折叠伞" in names(pack)


class TestTemperature:
    def test_big_range_adds_jacket(self):
        pack = build_packing(make_plan(weather=[
            {"date": "2026-11-01", "day_weather": "晴", "night_weather": "晴",
             "day_temp": 24, "night_temp": 10},  # 温差 14
        ]))
        assert "薄外套" in names(pack)
        assert "14" in reason_of(pack, "薄外套")

    def test_small_range_no_jacket(self):
        pack = build_packing(make_plan(weather=[
            {"date": "2026-11-01", "day_weather": "晴", "night_weather": "晴",
             "day_temp": 24, "night_temp": 20},
        ]))
        assert "薄外套" not in names(pack)

    def test_cold_night_adds_thermal(self):
        pack = build_packing(make_plan(weather=[
            {"date": "2026-11-01", "day_weather": "晴", "night_weather": "晴",
             "day_temp": 6, "night_temp": 3},
        ]))
        assert "保暖内衣" in names(pack)

    def test_hot_day_adds_quickdry(self):
        pack = build_packing(make_plan(weather=[
            {"date": "2026-11-01", "day_weather": "晴", "night_weather": "晴",
             "day_temp": 34, "night_temp": 26},
        ]))
        assert "速干短袖" in names(pack)


class TestDays:
    def test_short_trip_no_change_clothes(self):
        assert "换洗衣物 2 套" not in names(build_packing(make_plan(days=2)))

    def test_three_days_adds_change_clothes(self):
        pack = build_packing(make_plan(days=3))
        assert "换洗衣物 3 套" in names(pack)
        assert "3 天" in reason_of(pack, "换洗衣物 3 套")


class TestContext:
    def test_self_driving_adds_license(self):
        pack = build_packing(make_plan(transportation="自驾"))
        assert {"驾驶证 / 行驶证", "车载充电器"} <= names(pack)

    def test_walking_adds_shoes(self):
        pack = build_packing(make_plan(transportation="步行"))
        assert "舒适的鞋" in names(pack)

    def test_homestay_adds_toiletries(self):
        pack = build_packing(make_plan(accommodation="民宿"))
        assert "洗漱用品" in names(pack)

    def test_hotel_no_toiletries(self):
        assert "洗漱用品" not in names(build_packing(make_plan(accommodation="经济型酒店")))


class TestShapeAndStability:
    def test_always_has_essentials(self):
        pack = build_packing(make_plan())
        assert {"身份证 / 护照", "手机 + 充电器", "常用药（感冒/肠胃/创可贴）"} <= names(pack)

    def test_groups_have_titles_and_items(self):
        pack = build_packing(make_plan(days=3))
        assert pack["groups"]
        for g in pack["groups"]:
            assert g["title"] and g["items"]
            for item in g["items"]:
                assert item["name"] and item["reason"]

    def test_deterministic(self):
        plan = make_plan(days=3, weather=[
            {"date": "2026-11-01", "day_weather": "小雨", "night_weather": "阴",
             "day_temp": 20, "night_temp": 8},
        ])
        assert build_packing(plan) == build_packing(plan)

    def test_summary_mentions_days_and_rain(self):
        pack = build_packing(make_plan(days=3, weather=[
            {"date": "2026-11-01", "day_weather": "中雨", "night_weather": "阴",
             "day_temp": 20, "night_temp": 12},
        ]))
        assert "3 天" in pack["summary"]
        assert "有雨 1 天" in pack["summary"]

    def test_no_weather_still_works(self):
        pack = build_packing(make_plan(weather=[]))
        assert "折叠伞" not in names(pack)
        assert "身份证 / 护照" in names(pack)
