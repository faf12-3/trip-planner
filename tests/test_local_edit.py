"""局部重排守门逻辑的测试（纯函数，不需要模型/网络）。

这些用例对应需求澄清里的验收标准 1、4、5、6。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.local_edit import (  # noqa: E402
    diff_summary,
    merge_local,
    recompute_budget,
    validate_all,
    validate_selected,
    validate_structure,
    validate_unselected,
)
from app.models import Attraction, Budget, DayPlan, Hotel, Meal, TripPlan  # noqa: E402


def make_plan(days: int = 3, *, attractions: list[list[str]] | None = None) -> TripPlan:
    names = attractions or [["A1", "A2"], ["B1"], ["C1", "C2", "C3"]][:days]
    return TripPlan(
        city="南京",
        start_date="2026-11-01",
        end_date=f"2026-11-{days:02d}",
        days=[
            DayPlan(
                date=f"2026-11-{i + 1:02d}",
                day_index=i,
                description=f"第{i + 1}天",
                attractions=[
                    Attraction(name=n, visit_duration=60, ticket_price=10 * (k + 1))
                    for k, n in enumerate(names[i])
                ]
                if i < len(names)
                else [],
                meals=[Meal(type="lunch", name=f"饭{i}", estimated_cost=30)],
                hotel=Hotel(name=f"酒店{i}", estimated_cost=200),
            )
            for i in range(days)
        ],
        overall_suggestions="",
        budget=Budget(total_transportation=50),
    )


# ------------------------------------------------------------------ 结构 --
class TestStructure:
    def test_same_structure_passes(self):
        old = make_plan()
        assert validate_structure(old, old.model_copy(deep=True)) == []

    def test_day_count_changed(self):
        old = make_plan(3)
        new = make_plan(2)
        problems = validate_structure(old, new)
        assert any("天数被改了" in p for p in problems), problems

    def test_date_sequence_changed(self):
        old = make_plan(3)
        new = old.model_copy(deep=True)
        new.days[1].date = "2026-12-05"
        problems = validate_structure(old, new)
        assert any("日期被改了" in p for p in problems), problems

    def test_city_changed(self):
        old = make_plan(3)
        new = old.model_copy(deep=True)
        new.city = "苏州"
        assert any("城市被改了" in p for p in validate_structure(old, new))


# ---------------------------------------------------------------- 补集 --
class TestUnselected:
    def test_unselected_untouched_passes(self):
        old = make_plan(3)
        new = old.model_copy(deep=True)
        new.days[1].attractions[0].name = "B1改"
        assert validate_unselected(old, new, [1]) == []

    def test_unselected_changed_is_caught(self):
        old = make_plan(3)
        new = old.model_copy(deep=True)
        new.days[0].attractions[0].name = "偷偷改了"
        problems = validate_unselected(old, new, [1])
        assert problems == ["第 1 天没让你改，却被改了"], problems

    def test_two_unselected_changed(self):
        old = make_plan(3)
        new = old.model_copy(deep=True)
        new.days[0].description = "x"
        new.days[2].description = "y"
        problems = validate_unselected(old, new, [1])
        assert len(problems) == 2

    def test_field_order_does_not_matter(self):
        """序列化要稳定：字段顺序不同不能被判成"变了"。"""
        old = make_plan(3)
        new = old.model_copy(deep=True)
        new.days[0] = DayPlan(**old.days[0].model_dump())
        assert validate_unselected(old, new, [1]) == []


# ------------------------------------------------------------ 选中天有效 --
class TestSelectedValid:
    def test_one_to_six_ok(self):
        plan = make_plan(3, attractions=[["A1"], ["B1", "B2"], ["C1", "C2", "C3", "C4", "C5", "C6"]])
        assert validate_selected(plan, [0, 1, 2]) == []

    def test_empty_day_rejected(self):
        plan = make_plan(3, attractions=[["A1"], [], ["C1"]])
        problems = validate_selected(plan, [1])
        assert any("一个景点都没有" in p for p in problems), problems

    def test_seven_rejected(self):
        plan = make_plan(1, attractions=[["1", "2", "3", "4", "5", "6", "7"]])
        problems = validate_selected(plan, [0])
        assert any("超过 6 个" in p for p in problems), problems

    def test_out_of_range_day(self):
        plan = make_plan(2)
        assert any("不存在" in p for p in validate_selected(plan, [5]))


# ---------------------------------------------------------------- 合并 --
class TestMerge:
    def test_unselected_taken_from_old_verbatim(self):
        old = make_plan(3)
        new = old.model_copy(deep=True)
        new.days[0].attractions[0].name = "模型偷偷改了"
        new.days[1].attractions[0].name = "这是允许的"

        merged = merge_local(old, new, [1])
        assert merged.days[0].attractions[0].name == "A1"  # 未选中 → 旧值
        assert merged.days[1].attractions[0].name == "这是允许的"
        # 且是深拷贝：改 merged 不会影响 old
        merged.days[0].attractions[0].name = "再改"
        assert old.days[0].attractions[0].name == "A1"

    def test_merge_output_passes_validation(self):
        old = make_plan(3)
        new = old.model_copy(deep=True)
        new.days[0].description = "乱改的"
        new.days[1].attractions.append(Attraction(name="B2", visit_duration=30))
        merged = merge_local(old, new, [1])
        assert validate_all(old, merged, [1]) == []


# ---------------------------------------------------------------- 预算 --
class TestBudget:
    def test_recompute_from_details(self):
        plan = make_plan(2)
        plan.budget = Budget(total_attractions=999, total_hotels=999, total_meals=999,
                             total_transportation=50, total=999)
        recompute_budget(plan)
        # 门票 10+20 + 10 = 40；住宿 200+200；餐饮 30+30；交通沿用 50
        assert plan.budget.total_attractions == 40
        assert plan.budget.total_hotels == 400
        assert plan.budget.total_meals == 60
        assert plan.budget.total_transportation == 50
        assert plan.budget.total == 40 + 400 + 60 + 50

    def test_hotel_without_name_not_counted(self):
        plan = make_plan(1)
        plan.days[0].hotel = Hotel(name="", estimated_cost=500)
        recompute_budget(plan)
        assert plan.budget.total_hotels == 0

    def test_budget_created_when_missing(self):
        plan = make_plan(1)
        plan.budget = None
        recompute_budget(plan)
        assert plan.budget is not None
        assert plan.budget.total == plan.budget.total_attractions + plan.budget.total_hotels + \
            plan.budget.total_meals + plan.budget.total_transportation


# ---------------------------------------------------------------- 汇总 --
class TestValidateAll:
    def test_selected_required(self):
        old = make_plan(2)
        assert validate_all(old, old, []) == ["没有选中任何一天"]

    def test_clean_edit_passes(self):
        old = make_plan(3)
        new = old.model_copy(deep=True)
        new.days[1].attractions = [Attraction(name="博物馆", visit_duration=90, ticket_price=0)]
        assert validate_all(old, new, [1]) == []

    def test_multiple_problems_collected(self):
        old = make_plan(3)
        new = old.model_copy(deep=True)
        new.days[0].description = "乱改"          # 补集被改
        new.days[1].attractions = []              # 选中天变空
        problems = validate_all(old, new, [1])
        assert len(problems) == 2, problems


class TestDiffSummary:
    def test_lists_only_selected_days(self):
        old = make_plan(3)
        new = old.model_copy(deep=True)
        new.days[1].attractions = [Attraction(name="新景点")]
        summary = diff_summary(old, new, [1])
        assert len(summary["days"]) == 1
        row = summary["days"][0]
        assert row["day_index"] == 1
        assert row["before"] == ["B1"]
        assert row["after"] == ["新景点"]
        assert row["hotel_before"] == "酒店1"

    def test_sorted_and_deduplicated(self):
        old = make_plan(3)
        summary = diff_summary(old, old, [2, 0, 2])
        assert [d["day_index"] for d in summary["days"]] == [0, 2]
