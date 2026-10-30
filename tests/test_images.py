"""景点配图的离线测试：一个字都不发出去。

验四件事：
1. 没配 key 时是彻底的 no-op（配图不能拖累主流程）
2. 缓存命中就不查网络
3. 同一景点只查一次，但**每个实例都要回填**（第一版只填了第一天）
4. 限流/网络失败不让行程失败
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import images  # noqa: E402
from app.nodes import parse_trip_plan  # noqa: E402

PLAN_JSON = """{
  "city": "北京",
  "start_date": "2026-11-01",
  "end_date": "2026-11-02",
  "days": [
    {
      "date": "2026-11-01", "day_index": 0, "description": "",
      "attractions": [
        {"name": "故宫博物院", "visit_duration": 180, "ticket_price": 60},
        {"name": "天坛公园", "visit_duration": 120, "ticket_price": 15}
      ],
      "meals": []
    },
    {
      "date": "2026-11-02", "day_index": 1, "description": "",
      "attractions": [
        {"name": "故宫博物院", "visit_duration": 60, "ticket_price": 60}
      ],
      "meals": []
    }
  ],
  "weather_info": [],
  "overall_suggestions": "",
  "budget": {"total_attractions": 0, "total_hotels": 0, "total_meals": 0,
             "total_transportation": 0, "total": 0}
}"""


@pytest.fixture(autouse=True)
def offline(tmp_path, monkeypatch):
    """没有网络：把真正发请求的那层换掉，并给每个测试独立缓存文件。"""
    monkeypatch.setattr(images, "CACHE_PATH", tmp_path / "unsplash.json")
    calls: list[str] = []

    async def stub(client, key, cache_key, query, cache):
        calls.append(cache_key)
        return cache_key, None  # 一律"查不到"，回填逻辑只走缓存

    monkeypatch.setattr(images, "_search_one", stub)
    return calls


class TestNoKey:
    async def test_empty_key_is_noop(self, offline):
        plan = parse_trip_plan(PLAN_JSON)
        assert await images.enrich_plan_images(plan, "") == 0
        assert offline == []  # 一个查询都没发
        assert all(a.image_url is None for d in plan.days for a in d.attractions)


class TestCacheAndBackfill:
    async def test_cached_attraction_fills_every_instance(self, offline):
        """故宫在第 1、2 天都出现：查一次，但两天都要有图。"""
        images._save_cache({"北京|故宫博物院": "https://example.com/gugong.jpg"})

        plan = parse_trip_plan(PLAN_JSON)
        filled = await images.enrich_plan_images(plan, "fake-key")

        assert filled == 2
        assert plan.days[0].attractions[0].image_url == "https://example.com/gugong.jpg"
        assert plan.days[1].attractions[0].image_url == "https://example.com/gugong.jpg"
        # 没缓存的那条不填
        assert plan.days[0].attractions[1].image_url is None
        # 故宫只查一次（缓存命中所以其实不查，天坛查一次）
        assert offline.count("北京|故宫博物院") == 0
        assert offline.count("北京|天坛公园") == 1

    async def test_cache_key_is_city_scoped(self, offline):
        """同样叫"故宫"，北京和沈阳的照片不该串。"""
        images._save_cache({"北京|故宫博物院": "https://example.com/bj.jpg"})
        plan = parse_trip_plan(PLAN_JSON)
        plan.city = "沈阳"
        filled = await images.enrich_plan_images(plan, "fake-key")
        assert filled == 0
        assert plan.days[0].attractions[0].image_url is None


class TestFailureTolerance:
    async def test_rate_limit_does_not_raise(self, offline, monkeypatch):
        """403 限流时函数应正常返回，行程不受影响。"""

        async def boom(client, key, cache_key, query, cache):
            raise RuntimeError("RATE_LIMITED")

        monkeypatch.setattr(images, "_search_one", boom)
        plan = parse_trip_plan(PLAN_JSON)
        assert await images.enrich_plan_images(plan, "fake-key") == 0
        assert plan.days[0].attractions[0].image_url is None

    async def test_existing_image_is_not_overwritten(self, offline):
        plan = parse_trip_plan(PLAN_JSON)
        plan.days[0].attractions[0].image_url = "https://example.com/keep.jpg"
        images._save_cache({"北京|故宫博物院": "https://example.com/other.jpg"})

        await images.enrich_plan_images(plan, "fake-key")

        # 第 1 天已有的保持原样；第 2 天那条仍会被填
        assert plan.days[0].attractions[0].image_url == "https://example.com/keep.jpg"
        assert plan.days[1].attractions[0].image_url == "https://example.com/other.jpg"
class TestTwoSizes:
    """列表用 small、灯箱用 large；旧缓存只有一档时不能把已有数据废掉。"""

    async def test_new_cache_carries_both_sizes(self, offline):
        images._save_cache(
            {"北京|故宫博物院": {"small": "https://x/s.jpg", "large": "https://x/l.jpg"}}
        )
        plan = parse_trip_plan(PLAN_JSON)
        await images.enrich_plan_images(plan, "fake-key")

        a = plan.days[0].attractions[0]
        assert a.image_url == "https://x/s.jpg"
        assert a.image_url_large == "https://x/l.jpg"

    async def test_legacy_string_cache_falls_back_to_same_url(self, offline):
        images._save_cache({"北京|故宫博物院": "https://x/only.jpg"})
        plan = parse_trip_plan(PLAN_JSON)
        await images.enrich_plan_images(plan, "fake-key")

        a = plan.days[0].attractions[0]
        assert a.image_url == "https://x/only.jpg"
        assert a.image_url_large == "https://x/only.jpg"

    def test_norm_handles_all_shapes(self):
        assert images.norm("u") == {"small": "u", "large": "u"}
        assert images.norm({"small": "s"}) == {"small": "s", "large": "s"}
        assert images.norm({"small": "s", "large": "l"}) == {"small": "s", "large": "l"}
        assert images.norm({}) is None
        assert images.norm(None) is None
        assert images.norm(123) is None
def test_upgrade_url_rewrites_width():
    """旧缓存只有 w=400，换大图靠改参数，不用重打 Unsplash 配额。"""
    small = "https://images.unsplash.com/photo-1?ixid=abc&w=400&q=80"
    large = images.upgrade_url(small)
    assert "w=1600" in large
    assert "w=400" not in large
    assert large.split("?")[0] == small.split("?")[0]  # 同一张图
    assert large.endswith("&q=80")  # 其它参数保留


def test_upgrade_url_leaves_unknown_shape_alone():
    assert images.upgrade_url("https://x/y.jpg") == "https://x/y.jpg"
    assert images.upgrade_url("") == ""