"""城市检索的测试：中文、拼音、首字母、无结果、空查询。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app.api as api_module  # noqa: E402
from app.cities import all_cities, grouped, search  # noqa: E402


class TestSearch:
    def test_Chinese_prefix_ranks_first(self):
        names = [c["name"] for c in search("南")]
        assert names[0].startswith("南"), names
        assert "南京" in names and "南昌" in names

    def test_exact_name_wins(self):
        assert search("南京")[0]["name"] == "南京"

    def test_pinyin_prefix(self):
        assert search("nanjing")[0]["name"] == "南京"

    def test_initials(self):
        assert "南京" in [c["name"] for c in search("nj")]

    def test_substring_still_found(self):
        assert "南京" in [c["name"] for c in search("京")]

    def test_no_match_returns_empty(self):
        assert search("不存在的城市名") == []

    def test_empty_query_returns_hotlist(self):
        items = search("")
        assert len(items) == 8
        assert items[0]["name"] == "北京"

    def test_limit_is_clamped(self):
        assert len(search("", limit=3)) == 3
        # 上限是 400（清单 344 个）：传更大的值也只能拿到实际数量
        assert len(search("", limit=999)) <= 400
        assert len(search("", limit=999)) == len(all_cities())

    def test_case_insensitive(self):
        assert search("NJ")[0]["name"] == "南京"
        assert search("BeiJing")[0]["name"] == "北京"

    def test_dataset_has_no_duplicates(self):
        names = [c["name"] for c in all_cities()]
        assert len(names) == len(set(names))
        assert len(names) >= 300, f"城市数量偏少：{len(names)}"


@pytest.fixture()
def client() -> TestClient:
    return TestClient(api_module.app)


class TestApi:
    def test_default_list(self, client: TestClient):
        body = client.get("/api/cities").json()
        assert len(body["items"]) == 8
        assert body["items"][0]["name"] == "北京"

    def test_query(self, client: TestClient):
        body = client.get("/api/cities", params={"q": "苏州"}).json()
        assert body["items"][0]["name"] == "苏州"

    def test_query_with_limit(self, client: TestClient):
        body = client.get("/api/cities", params={"q": "南", "limit": 2}).json()
        assert len(body["items"]) == 2
class TestNoise:
    """短查询不做子串匹配：否则 nj 会命中 天津（tia-nj-in），下拉全是噪音。"""

    def test_two_letter_query_is_prefix_only(self):
        names = [c["name"] for c in search("nj")]
        assert "南京" in names
        assert "天津" not in names, names
        assert "镇江" not in names, names

    def test_long_query_still_fuzzy(self):
        # 长查询允许全拼子串，"jing" 能搜到 北京（bei-jing）
        assert "北京" in [c["name"] for c in search("jing")]

    def test_unknown_initials_return_empty(self):
        assert search("zg") == []
class TestShowAll:
    """空输入应当能看到全部城市，而不是只有 8 个热门。"""

    def test_empty_query_can_return_all(self):
        total = len(all_cities())
        assert len(search("", limit=400)) == total, "空查询应能一次取完"
        assert total >= 300

    def test_limit_upper_bound_is_400(self):
        # 传入更大的值会被钳到 400，但清单本身只有 344，所以取到全部
        assert len(search("", limit=9999)) == len(all_cities())

    def test_api_accepts_large_limit(self, client: TestClient):
        body = client.get("/api/cities", params={"limit": 400}).json()
        assert len(body["items"]) >= 300

    def test_filter_still_caps(self, client: TestClient):
        body = client.get("/api/cities", params={"q": "南", "limit": 5}).json()
        assert len(body["items"]) <= 5
class TestProvinceGroups:
    """按省份折叠要用到 province，且热门城市必须落回真实省份。"""

    def test_every_city_has_province(self):
        missing = [c["name"] for c in all_cities() if not c.get("province")]
        assert missing == [], f"缺省份：{missing[:5]}"

    def test_province_count_reasonable(self):
        provinces = {c["province"] for c in all_cities()}
        assert len(provinces) >= 25, f"分组偏少：{len(provinces)}"

    def test_guangzhou_belongs_to_guangdong(self):
        cities = {c["name"]: c["province"] for c in all_cities()}
        assert cities["广州"] == "广东"
        assert cities["南京"] == "江苏"

    def test_grouped_keeps_order_and_counts(self):
        groups = grouped()
        assert groups[0]["province"] == "直辖市"
        assert groups[0]["count"] == len(groups[0]["cities"])
        assert sum(g["count"] for g in groups) == len(all_cities())

    def test_grouped_respects_query(self):
        groups = grouped("南", limit=10)
        flat = [c["name"] for g in groups for c in g["cities"]]
        assert "南京" in flat
        assert all("南" in n or n in ("济南",) for n in flat), flat