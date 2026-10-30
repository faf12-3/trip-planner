"""预算约束 + 新字段的测试。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.constraints import check_budget  # noqa: E402
from app.models import Budget, TripPlan, TripRequest  # noqa: E402
from app.prompts import planner_user_prompt  # noqa: E402


def plan_with(total: int, **parts) -> TripPlan:
    return TripPlan(
        city="南京",
        start_date="2026-11-01",
        end_date="2026-11-01",
        days=[],
        overall_suggestions="",
        budget=Budget(total=total, **parts),
    )


class TestBudgetCheck:
    def test_no_limit(self):
        r = check_budget(plan_with(5000), None)
        assert r["ok"] is True and r["message"] == "" and r["limit"] is None

    def test_within_limit(self):
        r = check_budget(plan_with(1860), 2000)
        assert r["ok"] is True
        assert r["over"] == 0
        assert "余 140 元" in r["message"]

    def test_exactly_at_limit(self):
        assert check_budget(plan_with(2000), 2000)["ok"] is True

    def test_over_limit_reports_gap(self):
        r = check_budget(plan_with(2350, total_hotels=1400), 2000)
        assert r["ok"] is False
        assert r["over"] == 350
        assert "超出 350 元" in r["message"]

    def test_over_limit_points_at_biggest_item(self):
        r = check_budget(plan_with(2600, total_attractions=200, total_hotels=1600,
                                   total_meals=500, total_transportation=300), 2000)
        assert "住宿" in r["message"]

    def test_zero_limit_means_no_limit(self):
        # 前端留空会传 null；传 0 也按"不限"处理，避免"预算 0 元"这种荒唐校验
        assert check_budget(plan_with(3000), 0)["ok"] is True

    def test_plan_without_budget(self):
        plan = TripPlan(city="南京", start_date="2026-11-01", end_date="2026-11-01",
                        days=[], overall_suggestions="")
        assert check_budget(plan, 1000)["ok"] is True  # 没有预算数据就不判超


class TestRequestFields:
    def test_defaults(self):
        r = TripRequest(city="南京", start_date="2026-11-01", end_date="2026-11-02")
        assert r.budget_total is None
        assert r.notes == ""

    def test_accepts_budget_and_notes(self):
        r = TripRequest(city="南京", start_date="2026-11-01", end_date="2026-11-02",
                        budget_total=2000, notes="  想住舒适一点的酒店  ")
        assert r.budget_total == 2000
        assert r.notes == "想住舒适一点的酒店"

    def test_negative_budget_rejected(self):
        with pytest.raises(Exception):
            TripRequest(city="南京", start_date="2026-11-01", end_date="2026-11-02",
                        budget_total=-5)

    def test_notes_trimmed_and_capped(self):
        r = TripRequest(city="南京", start_date="2026-11-01", end_date="2026-11-02",
                        notes="要" * 800)
        assert len(r.notes) <= 500


class TestPromptContent:
    def _prompt(self, **kw):
        base = dict(
            city="南京", start_date="2026-11-01", end_date="2026-11-02", days=2,
            preferences="历史文化", budget_level="舒适", transportation="公共交通",
            accommodation="舒适型酒店", attractions_raw="-", weather_raw="-",
            hotels_raw="-",
        )
        base.update(kw)
        return planner_user_prompt(**base)

    def test_budget_line_present(self):
        s = self._prompt(budget_total=2000)
        assert "- 预算上限：2000 元（四项合计不得超过）" in s

    def test_no_budget_line(self):
        s = self._prompt()
        assert "未设上限" in s

    def test_notes_line_present(self):
        s = self._prompt(notes="想住舒适一点的酒店")
        assert "- 其他要求：想住舒适一点的酒店" in s

    def test_empty_notes_shows_none(self):
        assert "- 其他要求：无" in self._prompt(notes="  ")

    def test_priority_rules_present(self):
        s = self._prompt(budget_total=1500, notes="带老人")
        assert "不得更改" in s
        assert "预算上限是硬约束" in s
        # 其他要求要优先于默认风格
        assert "优先于默认风格" in s
    def test_forbids_false_compliance_claim(self):
        s = self._prompt(budget_total=600, notes="想住舒适一点的酒店")
        assert "不许假称已满足" in s
        assert "明说" in s
