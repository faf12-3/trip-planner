"""接口层测试：导出 .ics、装备清单、分享链接（含安全边界）。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app.api as api_module  # noqa: E402
from app.memory import UserTripStore  # noqa: E402
from app.models import Attraction, Budget, DayPlan, Hotel, Meal, TripPlan, WeatherInfo  # noqa: E402
from app.share import ShareStore  # noqa: E402

H = {"X-User-Id": "u-test"}
OTHER = {"X-User-Id": "u-other"}


def make_plan() -> TripPlan:
    return TripPlan(
        city="南京",
        start_date="2026-11-01",
        end_date="2026-11-02",
        days=[
            DayPlan(
                date=f"2026-11-0{i + 1}",
                day_index=i,
                description="",
                transportation="公共交通",
                accommodation="经济型酒店",
                attractions=[Attraction(name=f"景点{i}", visit_duration=120, ticket_price=30)],
                meals=[Meal(type="lunch", name="午饭", estimated_cost=40)],
                hotel=Hotel(name="酒店", estimated_cost=260),
            )
            for i in range(2)
        ],
        weather_info=[
            WeatherInfo(date="2026-11-01", day_weather="中雨", night_weather="阴",
                        day_temp=18, night_temp=8),
        ],
        overall_suggestions="带伞",
        budget=Budget(total_transportation=60),
    )


@pytest.fixture()
def client(tmp_path, monkeypatch) -> TestClient:
    store = UserTripStore(tmp_path / "trips")
    monkeypatch.setattr(api_module, "_store", lambda: store)
    monkeypatch.setattr(api_module, "_shares", ShareStore(tmp_path / "shares"))
    monkeypatch.setattr(api_module, "_profiles", None) if False else None
    return TestClient(api_module.app)


def seed(client: TestClient) -> str:
    r = client.post("/api/trips", json={"plan": make_plan().model_dump(), "title": "南京两日"}, headers=H)
    assert r.status_code == 200, r.text
    return r.json()["trip"]["id"]


class TestStaticMapWithoutKey:
    """没配 Key 时应该是 503（服务不可用），不是 500，也不能盖掉 400 校验。

    这个次序问题是在把包拷出来、在没有 .env 的环境里跑测试时才暴露的——
    也就是部署环境的样子。
    """

    def test_valid_points_returns_503_without_key(self, client, monkeypatch):
        monkeypatch.setattr(api_module, "load_env", lambda *a, **k: {})
        r = client.post("/api/staticmap", json={"points": [{"lng": 118.8, "lat": 32.0}]})
        assert r.status_code == 503
        assert "AMAP_MAPS_API_KEY" in r.json()["detail"]

    def test_empty_points_still_400_without_key(self, client, monkeypatch):
        monkeypatch.setattr(api_module, "load_env", lambda *a, **k: {})
        r = client.post("/api/staticmap", json={"points": []})
        assert r.status_code == 400, "入参校验不该被配置缺失盖掉"


class TestExportIcs:
    def test_returns_calendar(self, client):
        trip_id = seed(client)
        r = client.get(f"/api/trips/{trip_id}/export.ics", headers=H)
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/calendar")
        assert "attachment" in r.headers["content-disposition"]
        assert r.text.startswith("BEGIN:VCALENDAR")
        assert "SUMMARY:景点0" in r.text

    def test_requires_owner(self, client):
        trip_id = seed(client)
        assert client.get(f"/api/trips/{trip_id}/export.ics", headers=OTHER).status_code == 404
        # 不带身份头会归到 anonymous，所以是 404（看不到别人的行程），不是 400
        assert client.get(f"/api/trips/{trip_id}/export.ics").status_code == 404
        # 非法身份（路径穿越）才是 400
        assert (
            client.get(
                f"/api/trips/{trip_id}/export.ics", headers={"X-User-Id": "../evil"}
            ).status_code
            == 400
        )

    def test_unknown_trip(self, client):
        assert client.get("/api/trips/nope123456/export.ics", headers=H).status_code == 404


class TestPacking:
    def test_packing_endpoint(self, client):
        trip_id = seed(client)
        r = client.get(f"/api/trips/{trip_id}/packing", headers=H)
        assert r.status_code == 200
        body = r.json()
        names = {i["name"] for g in body["groups"] for i in g["items"]}
        assert "折叠伞" in names  # 11-01 有中雨
        assert "有雨 1 天" in body["summary"]

    def test_requires_owner(self, client):
        trip_id = seed(client)
        assert client.get(f"/api/trips/{trip_id}/packing", headers=OTHER).status_code == 404

    def test_plan_response_carries_packing(self, client, monkeypatch):
        """规划响应里也带 packing（不用再打一次接口）。"""
        app_module = api_module

        async def fake_generate(request, transport="streamable-http", user="anonymous"):
            plan = make_plan()
            return {
                "plan": plan,
                "markdown": "# x",
                "elapsed_ms": 1,
                "trace": [],
                "errors": [],
                "routing": {},
                "packing": app_module.build_packing(plan),
            }

        monkeypatch.setattr(api_module, "_generate", fake_generate)
        r = client.post(
            "/api/trip/plan",
            json={"city": "南京", "start_date": "2026-11-01", "end_date": "2026-11-02", "days": 2},
            headers=H,
        )
        assert r.status_code == 200, r.text
        assert r.json()["packing"]["groups"]


class TestShare:
    def test_create_and_read(self, client):
        trip_id = seed(client)
        created = client.post(f"/api/trips/{trip_id}/share", headers=H).json()
        token = created["token"]
        assert created["read_only"] is True
        assert created["path"] == f"/?share={token}"

        # 只读接口不需要身份
        r = client.get(f"/api/share/{token}")
        assert r.status_code == 200
        body = r.json()
        assert body["read_only"] is True
        assert body["plan"]["city"] == "南京"
        assert body["title"] == "南京两日"
        assert body["packing"]["groups"]

    def test_share_payload_hides_user(self, client):
        trip_id = seed(client)
        token = client.post(f"/api/trips/{trip_id}/share", headers=H).json()["token"]
        body = client.get(f"/api/share/{token}").json()
        assert "user" not in body
        assert "u-test" not in str(body)

    def test_bad_token_404(self, client):
        assert client.get("/api/share/" + "A" * 24).status_code == 404
        assert client.get("/api/share/../etc/passwd").status_code in (404, 400)

    def test_create_requires_owner(self, client):
        trip_id = seed(client)
        assert client.post(f"/api/trips/{trip_id}/share", headers=OTHER).status_code == 404

    def test_revoke_invalidates_token(self, client):
        trip_id = seed(client)
        token = client.post(f"/api/trips/{trip_id}/share", headers=H).json()["token"]
        assert client.delete(f"/api/trips/{trip_id}/share", headers=H).json()["revoked"] == 1
        assert client.get(f"/api/share/{token}").status_code == 404

    def test_revoke_requires_owner(self, client):
        trip_id = seed(client)
        client.post(f"/api/trips/{trip_id}/share", headers=H)
        assert client.delete(f"/api/trips/{trip_id}/share", headers=OTHER).status_code == 404

    def test_share_dies_with_trip(self, client):
        trip_id = seed(client)
        token = client.post(f"/api/trips/{trip_id}/share", headers=H).json()["token"]
        client.delete(f"/api/trips/{trip_id}", headers=H)
        assert client.get(f"/api/share/{token}").status_code == 404

    def test_multiple_links_for_same_trip(self, client):
        trip_id = seed(client)
        a = client.post(f"/api/trips/{trip_id}/share", headers=H).json()["token"]
        b = client.post(f"/api/trips/{trip_id}/share", headers=H).json()["token"]
        assert a != b
        assert client.get(f"/api/share/{a}").status_code == 200
        assert client.get(f"/api/share/{b}").status_code == 200

    def test_share_token_cannot_touch_owner_data(self, client):
        """拿 token 当身份去写，**不能改到原主的行程**。

        实测发现：把 token 当 X-User-Id 传进去，服务端会把它当成一个"新用户"，
        在 data/trips/<token>/ 下另存一份——原主那份纹丝不动。
        这不是提权，但确实说明"任意字符串都能当用户名"。结论落到断言里：
        只保证"原主数据没变"，不假装请求会失败。
        """
        trip_id = seed(client)
        token = client.post(f"/api/trips/{trip_id}/share", headers=H).json()["token"]
        client.put(
            f"/api/trips/{trip_id}",
            json={"plan": make_plan().model_dump(), "title": "篡改"},
            headers={"X-User-Id": token},
        )
        mine = client.get(f"/api/trips/{trip_id}", headers=H).json()
        assert mine["title"] == "南京两日"
