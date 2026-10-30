"""用户隔离与偏好记忆的测试。

两个能力都是"给用户用"才需要的：一个人用不存在隔离问题，也没有"记住他"的价值。
所以这两组测试是成对的——记忆必须按用户分开，否则张三的偏好会改李四的行程。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app.api as api_module  # noqa: E402
from app.memory import (  # noqa: E402
    DEFAULT_USER,
    InvalidUserId,
    ProfileStore,
    UserTripStore,
    check_user,
    memory_note,
)
from app.prompts import planner_system  # noqa: E402
from app.store import TripNotFound  # noqa: E402

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
    "overall_suggestions": "",
    "budget": {
        "total_attractions": 0,
        "total_hotels": 0,
        "total_meals": 0,
        "total_transportation": 60,
        "total": 60,
    },
}


class Req:
    """模拟 TripRequest（只需要那几个字段）。"""

    city = "南京"
    preferences = "历史文化"
    budget_level = "中等"
    transportation = "公共交通"
    accommodation = "经济型酒店"


@pytest.fixture()
def store(tmp_path) -> UserTripStore:
    return UserTripStore(tmp_path / "trips")


@pytest.fixture()
def profiles(tmp_path) -> ProfileStore:
    return ProfileStore(tmp_path / "profiles")


# ------------------------------------------------------------------ 隔离 --
class TestUserIsolation:
    def test_lists_are_separate(self, store: UserTripStore):
        store.save(PLAN, user="alice")
        store.save(PLAN, user="alice")
        store.save(PLAN, user="bob")

        assert len(store.list(user="alice")) == 2
        assert len(store.list(user="bob")) == 1
        assert store.list(user="carol") == []

    def test_cannot_read_other_users_trip(self, store: UserTripStore):
        meta = store.save(PLAN, user="alice")
        with pytest.raises(TripNotFound):
            store.get(meta["id"], user="bob")

    def test_cannot_delete_other_users_trip(self, store: UserTripStore):
        meta = store.save(PLAN, user="alice")
        assert store.delete(meta["id"], user="bob") is False
        assert store.get(meta["id"], user="alice")["id"] == meta["id"]

    def test_same_trip_id_lives_in_separate_dirs(self, store: UserTripStore):
        a = store.save(PLAN, user="alice")
        (store.root / "bob").mkdir(parents=True, exist_ok=True)
        # 直接往 bob 目录写同一 id，验证两人互不影响
        import json

        payload = json.loads((store.root / "alice" / f"{a['id']}.json").read_text(encoding="utf-8"))
        payload["title"] = "bob 的版本"
        (store.root / "bob" / f"{a['id']}.json").write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8"
        )
        assert store.get(a["id"], user="alice")["title"] != "bob 的版本"
        assert store.get(a["id"], user="bob")["title"] == "bob 的版本"

    @pytest.mark.parametrize("bad", ["../evil", "a/b", "x" * 200, "有中文", "ab"])
    def test_bad_user_id_rejected(self, bad: str):
        with pytest.raises(InvalidUserId):
            check_user(bad)

    def test_empty_user_becomes_anonymous(self):
        assert check_user(None) == DEFAULT_USER
        assert check_user("  ") == DEFAULT_USER

    def test_traversal_cannot_escape(self, store: UserTripStore):
        with pytest.raises(InvalidUserId):
            store.list(user="../../etc")

    def test_legacy_flat_file_belongs_to_anonymous(self, store: UserTripStore):
        store.root.mkdir(parents=True, exist_ok=True)
        import json

        legacy = {"id": "20260101-000000-abcdef", "plan": PLAN, "title": "旧数据"}
        (store.root / "20260101-000000-abcdef.json").write_text(
            json.dumps(legacy, ensure_ascii=False), encoding="utf-8"
        )
        assert len(store.list(user="anonymous")) == 1
        assert store.list(user="alice") == []


# ------------------------------------------------------------------ 记忆 --
class TestProfileMemory:
    def test_counts_accumulate(self, profiles: ProfileStore):
        profiles.record("alice", Req(), PLAN)
        profiles.record("alice", Req(), PLAN)
        data = profiles.load("alice")
        assert data["trips"] == 2
        assert data["counts"]["preferences"]["历史文化"] == 2

    def test_users_do_not_share_profile(self, profiles: ProfileStore):
        profiles.record("alice", Req(), PLAN)
        assert profiles.hints("bob") == {}
        assert profiles.summary("bob")["trips"] == 0

    def test_hints_return_most_frequent(self, profiles: ProfileStore):
        class Req2(Req):
            preferences = "自然风光"

        profiles.record("alice", Req(), PLAN)
        profiles.record("alice", Req(), PLAN)
        profiles.record("alice", Req2(), PLAN)
        hints = profiles.hints("alice")
        assert hints["preferences"][0] == "历史文化"
        assert "自然风光" in hints["preferences"]

    def test_empty_profile_has_no_hints(self, profiles: ProfileStore):
        assert profiles.hints("nobody") == {}

    def test_memory_note_is_empty_without_hints(self):
        assert memory_note({}) == ""

    def test_memory_note_mentions_preferences(self):
        note = memory_note({"preferences": ["历史文化"], "budget_level": ["中等"]})
        assert "历史文化" in note and "中等" in note
        assert "历史偏好" in note

    def test_prompt_includes_memory_only_when_given(self):
        without = planner_system(2, "2026-12-22")
        with_mem = planner_system(2, "2026-12-22", "该用户的历史偏好：历史文化。")
        assert "历史偏好" not in without
        assert "历史文化" in with_mem
        # 记忆是附加段，不能挤掉原有规则
        assert "JSON" in with_mem


# -------------------------------------------------------------------- API --
@pytest.fixture()
def client(tmp_path, monkeypatch) -> TestClient:
    monkeypatch.setattr(api_module, "_store", lambda: UserTripStore(tmp_path / "trips"))
    monkeypatch.setattr(api_module, "_profiles", ProfileStore(tmp_path / "profiles"))
    return TestClient(api_module.app)


class TestApiIsolation:
    def test_each_user_sees_only_own_trips(self, client: TestClient):
        client.post("/api/trips", json={"plan": PLAN}, headers={"X-User-Id": "alice"})
        client.post("/api/trips", json={"plan": PLAN}, headers={"X-User-Id": "bob"})

        alice = client.get("/api/trips", headers={"X-User-Id": "alice"}).json()["items"]
        bob = client.get("/api/trips", headers={"X-User-Id": "bob"}).json()["items"]
        assert len(alice) == 1 and len(bob) == 1
        assert alice[0]["id"] != bob[0]["id"]

    def test_other_users_trip_is_404(self, client: TestClient):
        trip = client.post(
            "/api/trips", json={"plan": PLAN}, headers={"X-User-Id": "alice"}
        ).json()["trip"]
        assert client.get(f"/api/trips/{trip['id']}", headers={"X-User-Id": "bob"}).status_code == 404
        assert client.get(f"/api/trips/{trip['id']}", headers={"X-User-Id": "alice"}).status_code == 200

    def test_invalid_user_header_is_400(self, client: TestClient):
        resp = client.get("/api/trips", headers={"X-User-Id": "../evil"})
        assert resp.status_code == 400

    def test_missing_header_falls_back_to_anonymous(self, client: TestClient):
        client.post("/api/trips", json={"plan": PLAN})
        items = client.get("/api/trips").json()["items"]
        assert len(items) == 1

    def test_profile_endpoint(self, client: TestClient):
        resp = client.get("/api/profile", headers={"X-User-Id": "alice"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["user"] == "alice"
        assert body["trips"] == 0
        assert body["hints"] == {}
