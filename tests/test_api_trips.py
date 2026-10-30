"""行程持久化接口的端到端测试（TestClient，不打外网、不调模型）。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app.api as api_module  # noqa: E402
from app.memory import ProfileStore, UserTripStore  # noqa: E402

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


@pytest.fixture()
def client(tmp_path, monkeypatch) -> TestClient:
    # 把落盘目录换到临时目录，别污染项目里的 data/
    monkeypatch.setattr(api_module, "_store", lambda: UserTripStore(tmp_path / "trips"))
    monkeypatch.setattr(api_module, "_profiles", ProfileStore(tmp_path / "profiles"))
    return TestClient(api_module.app)


class TestCrud:
    def test_save_then_list(self, client: TestClient):
        resp = client.post("/api/trips", json={"plan": PLAN, "title": "南京两日"})
        assert resp.status_code == 200, resp.text
        trip = resp.json()["trip"]
        assert trip["title"] == "南京两日"

        items = client.get("/api/trips").json()["items"]
        assert len(items) == 1
        assert items[0]["id"] == trip["id"]

    def test_get_returns_full_plan(self, client: TestClient):
        trip = client.post("/api/trips", json={"plan": PLAN}).json()["trip"]
        record = client.get(f"/api/trips/{trip['id']}").json()
        assert record["plan"]["city"] == "南京"
        assert record["plan"]["days"][0]["attractions"][0]["name"] == "南京博物院"

    def test_update_overwrites(self, client: TestClient):
        trip = client.post("/api/trips", json={"plan": PLAN}).json()["trip"]
        changed = {**PLAN, "overall_suggestions": "改过的建议"}
        resp = client.put(f"/api/trips/{trip['id']}", json={"plan": changed})
        assert resp.status_code == 200
        assert client.get(f"/api/trips/{trip['id']}").json()["plan"]["overall_suggestions"] == "改过的建议"

    def test_delete(self, client: TestClient):
        trip = client.post("/api/trips", json={"plan": PLAN}).json()["trip"]
        assert client.delete(f"/api/trips/{trip['id']}").status_code == 200
        assert client.get(f"/api/trips/{trip['id']}").status_code == 404

    def test_missing_trip_is_404(self, client: TestClient):
        assert client.get("/api/trips/20260101-000000-abcdef").status_code == 404
        assert client.delete("/api/trips/20260101-000000-abcdef").status_code == 404


class TestStaticMapPayload:
    """前端会把景点名一起带过来，后端必须容忍额外字段。

    回归：`points: list[dict[str, float]]` 会把 `name` 当数字解析 → 422，
    页面上每张地图都变成"暂时取不到"（用户实测，打印出来的 PDF 里全是提示文字）。
    """

    def test_extra_keys_are_ignored(self):
        from app.api import StaticMapRequest

        req = StaticMapRequest.model_validate(
            {
                "points": [{"lng": 116.411, "lat": 39.947, "name": "孔庙和国子监博物馆"}],
                "zoom": 11,
            }
        )
        assert req.points[0].lng == 116.411
        assert not hasattr(req.points[0], "name")

    def test_plain_coordinates_still_work(self):
        from app.api import StaticMapRequest

        req = StaticMapRequest.model_validate({"points": [{"lng": 118.1, "lat": 32.0}]})
        assert req.zoom == 11
        assert len(req.points) == 1

    def test_empty_points_rejected_with_400(self, client: TestClient):
        resp = client.post("/api/staticmap", json={"points": []})
        assert resp.status_code == 400
        assert "不能为空" in resp.json()["detail"]


class TestValidation:
    def test_bad_plan_is_422(self, client: TestClient):
        resp = client.post("/api/trips", json={"plan": {"city": "南京"}})
        assert resp.status_code == 422
        assert "不合法" in resp.json()["detail"]

    def test_path_traversal_is_rejected(self, client: TestClient):
        resp = client.get("/api/trips/..%2F..%2F.env")
        assert resp.status_code in (400, 404), resp.status_code

    def test_empty_list(self, client: TestClient):
        assert client.get("/api/trips").json()["items"] == []
