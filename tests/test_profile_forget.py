"""记忆可纠正的测试：能删单项、能清空、删完不影响别人。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app.api as api_module  # noqa: E402
from app.memory import ProfileStore, UserTripStore  # noqa: E402


class Req:
    city = "南京"
    preferences = "历史文化"
    budget_level = "中等"
    transportation = "公共交通"
    accommodation = "经济型酒店"


PLAN = {"city": "南京", "days": [{"date": "2026-12-22"}]}


class TestForget:
    def test_forget_single_hint(self, tmp_path):
        store = ProfileStore(tmp_path / "profiles")
        store.record("alice", Req(), PLAN)
        assert "历史文化" in store.hints("alice")["preferences"]

        assert store.forget_hint("alice", "preferences", "历史文化") is True
        assert "preferences" not in store.hints("alice"), store.hints("alice")

    def test_forget_missing_returns_false(self, tmp_path):
        store = ProfileStore(tmp_path / "profiles")
        store.record("alice", Req(), PLAN)
        assert store.forget_hint("alice", "preferences", "自然风光") is False
        assert store.forget_hint("alice", "不存在的字段", "x") is False

    def test_forget_one_of_two_keeps_other(self, tmp_path):
        store = ProfileStore(tmp_path / "profiles")

        class Req2(Req):
            budget_level = "穷游"

        store.record("alice", Req(), PLAN)
        store.record("alice", Req2(), PLAN)
        store.forget_hint("alice", "budget_level", "穷游")
        assert store.hints("alice")["budget_level"] == ["中等"]

    def test_forget_all(self, tmp_path):
        store = ProfileStore(tmp_path / "profiles")
        store.record("alice", Req(), PLAN)
        assert store.forget_all("alice") is True
        assert store.summary("alice")["trips"] == 0
        assert store.forget_all("alice") is False  # 第二次没得删

    def test_forget_does_not_touch_other_users(self, tmp_path):
        store = ProfileStore(tmp_path / "profiles")
        store.record("alice", Req(), PLAN)
        store.record("bob", Req(), PLAN)
        store.forget_all("alice")
        assert store.hints("bob")["preferences"] == ["历史文化"]


@pytest.fixture()
def client(tmp_path, monkeypatch) -> TestClient:
    monkeypatch.setattr(api_module, "_store", lambda: UserTripStore(tmp_path / "trips"))
    monkeypatch.setattr(api_module, "_profiles", ProfileStore(tmp_path / "profiles"))
    api_module._profiles.record("alice", Req(), PLAN)
    return TestClient(api_module.app)


class TestApiForget:
    def test_delete_hint(self, client: TestClient):
        r = client.delete(
            "/api/profile/hints",
            params={"field": "preferences", "value": "历史文化"},
            headers={"X-User-Id": "alice"},
        )
        assert r.status_code == 200, r.text
        hints = r.json()["profile"]["hints"]
        # 只删了这一项：其它字段（城市/交通等）应当还在
        assert "preferences" not in hints, hints
        assert hints.get("city") == ["南京"], hints

    def test_delete_missing_hint_is_404(self, client: TestClient):
        r = client.delete(
            "/api/profile/hints",
            params={"field": "preferences", "value": "自然风光"},
            headers={"X-User-Id": "alice"},
        )
        assert r.status_code == 404

    def test_delete_all(self, client: TestClient):
        r = client.delete("/api/profile", headers={"X-User-Id": "alice"})
        assert r.status_code == 200
        assert r.json()["removed"] is True
        assert client.get("/api/profile", headers={"X-User-Id": "alice"}).json()["trips"] == 0

    def test_bad_user_rejected(self, client: TestClient):
        r = client.delete("/api/profile", headers={"X-User-Id": "../evil"})
        assert r.status_code == 400
