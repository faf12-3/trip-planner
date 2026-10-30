"""分享链接的测试：token 安全性、只读语义、撤销即失效。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.share import InvalidToken, ShareStore, check_token  # noqa: E402


@pytest.fixture()
def store(tmp_path) -> ShareStore:
    return ShareStore(tmp_path / "shares")


class TestTokenSafety:
    def test_token_is_long_and_urlsafe(self, store):
        rec = store.create(user="alice", trip_id="t1")
        token = rec["token"]
        assert 16 <= len(token) <= 64
        assert check_token(token) == token

    def test_two_tokens_differ(self, store):
        a = store.create(user="alice", trip_id="t1")["token"]
        b = store.create(user="alice", trip_id="t1")["token"]
        assert a != b

    def test_token_does_not_leak_trip_or_user(self, store):
        rec = store.create(user="alice", trip_id="20261030-120000-abcdef")
        assert "alice" not in rec["token"]
        assert "20261030" not in rec["token"]

    @pytest.mark.parametrize("bad", ["../etc/passwd", "a" * 15, "有中文", "", "a b c", "../../x"])
    def test_bad_tokens_rejected(self, bad):
        with pytest.raises(InvalidToken):
            check_token(bad)

    def test_traversal_returns_none(self, store):
        assert store.resolve("../../etc/passwd") is None


class TestResolve:
    def test_roundtrip(self, store):
        rec = store.create(user="alice", trip_id="t1", title="南京三日")
        got = store.resolve(rec["token"])
        assert got is not None
        assert got["trip_id"] == "t1"
        assert got["user"] == "alice"
        assert got["title"] == "南京三日"

    def test_unknown_token(self, store):
        assert store.resolve("A" * 24) is None

    def test_corrupted_file(self, store):
        rec = store.create(user="alice", trip_id="t1")
        path = store.root / f"{rec['token']}.json"
        path.write_text("{not json", encoding="utf-8")
        assert store.resolve(rec["token"]) is None


class TestRevoke:
    def test_revoke_removes_all_tokens_of_trip(self, store):
        a = store.create(user="alice", trip_id="t1")["token"]
        b = store.create(user="alice", trip_id="t1")["token"]
        other = store.create(user="alice", trip_id="t2")["token"]

        assert store.revoke_trip(user="alice", trip_id="t1") == 2
        assert store.resolve(a) is None
        assert store.resolve(b) is None
        assert store.resolve(other) is not None

    def test_revoke_does_not_touch_other_users(self, store):
        mine = store.create(user="alice", trip_id="t1")["token"]
        theirs = store.create(user="bob", trip_id="t1")["token"]

        assert store.revoke_trip(user="alice", trip_id="t1") == 1
        assert store.resolve(mine) is None
        assert store.resolve(theirs) is not None

    def test_revoke_when_nothing(self, store):
        assert store.revoke_trip(user="alice", trip_id="nope") == 0


class TestList:
    def test_lists_only_own(self, store):
        store.create(user="alice", trip_id="t1")
        store.create(user="bob", trip_id="t1")
        assert len(store.list_for(user="alice", trip_id="t1")) == 1

    def test_sorted_newest_first(self, store):
        first = store.create(user="alice", trip_id="t1")
        second = store.create(user="alice", trip_id="t1")
        items = store.list_for(user="alice", trip_id="t1")
        assert len(items) == 2
        assert {items[0]["token"], items[1]["token"]} == {first["token"], second["token"]}
