"""行程持久化的测试：只用临时目录，不碰真实 data/。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.store import InvalidTripId, TripNotFound, TripStore, check_id  # noqa: E402

PLAN = {
    "city": "南京",
    "start_date": "2026-12-22",
    "end_date": "2026-12-23",
    "days": [
        {
            "date": "2026-12-22",
            "day_index": 0,
            "description": "首日",
            "transportation": "公共交通",
            "attractions": [{"name": "南京博物院", "visit_duration": 180, "ticket_price": 0}],
            "meals": [],
        }
    ],
    "weather_info": [],
    "overall_suggestions": "提前预约",
    "budget": {
        "total_attractions": 0,
        "total_hotels": 0,
        "total_meals": 0,
        "total_transportation": 60,
        "total": 60,
    },
}


@pytest.fixture()
def store(tmp_path) -> TripStore:
    return TripStore(tmp_path / "trips")


class TestSaveAndLoad:
    def test_new_save_returns_meta(self, store: TripStore):
        meta = store.save(PLAN)
        assert meta["city"] == "南京"
        assert meta["days"] == 1
        assert meta["total"] == 60
        assert meta["id"]

    def test_roundtrip_keeps_chinese(self, store: TripStore):
        meta = store.save(PLAN)
        record = store.get(meta["id"])
        assert record["plan"]["city"] == "南京"
        assert record["plan"]["days"][0]["attractions"][0]["name"] == "南京博物院"

    def test_second_save_creates_new_record(self, store: TripStore):
        a = store.save(PLAN)
        b = store.save(PLAN)
        assert a["id"] != b["id"]
        assert len(store.list()) == 2

    def test_update_in_place_keeps_id_and_created_at(self, store: TripStore):
        meta = store.save(PLAN, title="我的南京行")
        changed = json.loads(json.dumps(PLAN))
        changed["days"][0]["attractions"][0]["ticket_price"] = 88

        again = store.save(changed, trip_id=meta["id"])
        assert again["id"] == meta["id"]
        assert again["title"] == "我的南京行"
        record = store.get(meta["id"])
        assert record["plan"]["days"][0]["attractions"][0]["ticket_price"] == 88
        assert record["created_at"] == store.get(meta["id"])["created_at"]

    def test_title_defaults_to_city_and_date(self, store: TripStore):
        meta = store.save(PLAN)
        assert "南京" in meta["title"]


class TestListAndDelete:
    def test_list_is_newest_first(self, store: TripStore):
        first = store.save(PLAN, title="第一份")
        second = store.save(PLAN, title="第二份")
        titles = [item["title"] for item in store.list()]
        assert titles[0] == "第二份", titles
        assert second["id"] in {item["id"] for item in store.list()}
        assert first["id"] in {item["id"] for item in store.list()}

    def test_delete_removes_file(self, store: TripStore):
        meta = store.save(PLAN)
        assert store.delete(meta["id"]) is True
        assert store.list() == []
        with pytest.raises(TripNotFound):
            store.get(meta["id"])

    def test_delete_missing_returns_false(self, store: TripStore):
        assert store.delete("20260101-000000-abcdef") is False

    def test_missing_trip_raises(self, store: TripStore):
        with pytest.raises(TripNotFound):
            store.get("20260101-000000-abcdef")


class TestSafety:
    @pytest.mark.parametrize("bad", ["../.env", "..", "a/b", "x" * 200, "", "traversal_..", "有中文"])
    def test_invalid_ids_rejected(self, bad: str):
        with pytest.raises(InvalidTripId):
            check_id(bad)

    def test_traversal_cannot_read_outside_root(self, store: TripStore):
        with pytest.raises(InvalidTripId):
            store.get("../../.env")

    def test_corrupt_file_does_not_break_list(self, store: TripStore):
        store.save(PLAN)
        (store.root / "broken-file-abc.json").write_text("{not json", encoding="utf-8")
        items = store.list()
        assert len(items) == 1, "坏文件应被跳过而不是让列表崩掉"

    def test_empty_store_lists_nothing(self, store: TripStore):
        assert store.list() == []

    def test_unicode_title_survives_disk(self, store: TripStore):
        meta = store.save(PLAN, title="周末·南京两日")
        raw = (store.root / f"{meta['id']}.json").read_text(encoding="utf-8")
        assert "周末·南京两日" in raw
