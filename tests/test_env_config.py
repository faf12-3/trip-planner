"""环境变量白名单的回归测试。

这个 bug 只有在"没有 .env 文件、只有真实环境变量"的环境里才出现（Vercel/Railway），
本地永远看不出来。所以测试要**模拟那个环境**：清空 .env 影响，只给 os.environ。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.cli import PROJECT_ENV_KEYS, load_env  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / ".env.example"


class TestEnvWhitelist:
    def test_project_keys_include_frontend_needed(self):
        """浏览器要用到的两个 key 必须在白名单里，否则地图只能降级成静态图。"""
        assert "AMAP_JS_KEY" in PROJECT_ENV_KEYS
        assert "AMAP_JS_SECURITY_CODE" in PROJECT_ENV_KEYS

    def test_project_keys_include_optional_ones(self):
        assert "UNSPLASH_ACCESS_KEY" in PROJECT_ENV_KEYS
        assert "TRIP_DATA_DIR" in PROJECT_ENV_KEYS

    def test_env_vars_are_read(self, monkeypatch):
        """只设 os.environ（不落 .env 文件）也必须读得到。"""
        monkeypatch.setenv("AMAP_JS_KEY", "k" * 32)
        monkeypatch.setenv("AMAP_JS_SECURITY_CODE", "s" * 32)
        env = load_env()
        assert env["AMAP_JS_KEY"] == "k" * 32
        assert env["AMAP_JS_SECURITY_CODE"] == "s" * 32

    def test_unknown_env_not_leaked(self, monkeypatch):
        """不该把 PATH 之类的系统变量塞进返回字典（排查时容易误读）。"""
        monkeypatch.setenv("SOME_RANDOM_SYSTEM_VAR", "x")
        assert "SOME_RANDOM_SYSTEM_VAR" not in load_env()

    @pytest.mark.skipif(not EXAMPLE.is_file(), reason="没有 .env.example")
    def test_example_keys_all_registered(self):
        """`.env.example` 里出现的每个变量名都必须登记在白名单里。

        这是防复发的那一道：以后加新变量、忘了登记，测试直接红，
        而不是等到部署之后发现"怎么没生效"。
        """
        text = EXAMPLE.read_text(encoding="utf-8")
        names = set()
        for line in text.splitlines():
            m = re.match(r"^\s*([A-Z][A-Z0-9_]+)\s*=", line)
            if m:
                names.add(m.group(1))
        assert names, "没能从 .env.example 里解析出变量名"
        missing = names - set(PROJECT_ENV_KEYS)
        assert not missing, f"这些变量写在 .env.example 里但没登记：{sorted(missing)}"
