"""prefs 的测试：自然语言意图 → 酒店搜索词。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.prefs import derive_hotel_keyword, hotel_keyword_reason  # noqa: E402


class TestDerive:
    @pytest.mark.parametrize(
        "notes,expected",
        [
            ("想住舒适一点的酒店", "舒适型酒店"),
            ("住宿好一点，干净安静", "舒适型酒店"),
            ("有没有品质一点的", "舒适型酒店"),
            ("要豪华的", "豪华型酒店"),
            ("想住高端酒店", "豪华型酒店"),
            ("尽量省钱", "经济型酒店"),
            ("预算有限，实惠为主", "经济型酒店"),
            ("想住民宿体验一下", "民宿"),
        ],
    )
    def test_intent_mapping(self, notes, expected):
        assert derive_hotel_keyword("经济型酒店", notes) == expected

    def test_no_notes_keeps_form_value(self):
        assert derive_hotel_keyword("舒适型酒店", "") == "舒适型酒店"

    def test_unrecognized_notes_keeps_form_value(self):
        assert derive_hotel_keyword("经济型酒店", "带老人，少走路，每天别排太满") == "经济型酒店"

    def test_high_end_wins_over_comfort(self):
        # 两句话里都有意图时，"豪华"优先于"舒适"
        assert derive_hotel_keyword("经济型酒店", "要豪华的，住得舒适") == "豪华型酒店"

    def test_english_case_insensitive(self):
        assert derive_hotel_keyword("经济型酒店", "想住 AIRBNB") == "民宿"

    def test_notes_with_spaces(self):
        assert derive_hotel_keyword("经济型酒店", "   想住舒适一点的酒店   ") == "舒适型酒店"


class TestReason:
    def test_reason_when_changed(self):
        r = hotel_keyword_reason("经济型酒店", "想住舒适一点的酒店")
        assert "经济型酒店 → 舒适型酒店" in r

    def test_reason_when_unchanged(self):
        r = hotel_keyword_reason("经济型酒店", "带老人")
        assert "沿用表单" in r

    def test_reason_accepts_explicit_keyword(self):
        r = hotel_keyword_reason("经济型酒店", "", "豪华型酒店")
        assert "豪华型酒店" in r
