"""高德 MCP 接入层。

两种传输都支持，默认 streamable-http（官方推荐）：它是纯 HTTP，不需要 Node，
也不依赖子进程——后者在受限环境里很容易被命名管道限制挡住。

    http    https://mcp.amap.com/mcp?key=<KEY>
    stdio   npx -y @amap/amap-maps-mcp-server  （env: AMAP_MAPS_API_KEY）

工具名以实际返回为准（当前官方包 12 个），不写死"16 个工具"这类数字。
"""

from __future__ import annotations

import os
from typing import Any, Optional, Protocol

HTTP_URL = "https://mcp.amap.com/mcp"
NPX_PKG = "@amap/amap-maps-mcp-server"


class AmapTools(Protocol):
    """节点只依赖这个协议，测试里换成假实现即可，不需要起服务器。"""

    async def call(self, name: str, arguments: dict[str, Any]) -> str: ...

    async def list_tools(self) -> list[str]: ...


class AmapMCP:
    """真实的 MCP 客户端（async context manager，一次会话内复用连接）。"""

    def __init__(
        self,
        key: str,
        *,
        transport: str = "streamable-http",
        timeout: float = 60.0,
    ) -> None:
        self.key = key
        self.transport = transport
        self.timeout = timeout
        self._stack = None
        self._session = None
        self._tools: Optional[list[str]] = None

    # ------------------------------------------------------------- lifecycle --
    async def __aenter__(self) -> "AmapMCP":
        from contextlib import AsyncExitStack

        from mcp import ClientSession

        self._stack = AsyncExitStack()

        if self.transport in ("streamable-http", "streamable_http", "http"):
            from mcp.client.streamable_http import streamablehttp_client

            await self._preflight()
            read, write, _ = await self._stack.enter_async_context(
                streamablehttp_client(f"{HTTP_URL}?key={self.key}")
            )
        elif self.transport == "stdio":
            from mcp.client.stdio import StdioServerParameters, stdio_client

            env = {
                **os.environ,
                "AMAP_MAPS_API_KEY": self.key,
                # npx 默认缓存在用户目录，受限环境会 EPERM；指到工作区更稳
                "npm_config_cache": os.environ.get(
                    "npm_config_cache", os.path.join(os.getcwd(), ".tmp", "npm-cache")
                ),
            }
            params = StdioServerParameters(
                command="npx.cmd" if os.name == "nt" else "npx",
                args=["-y", NPX_PKG],
                env=env,
            )
            try:
                read, write = await self._stack.enter_async_context(stdio_client(params))
            except PermissionError as exc:
                # Windows 上 asyncio 建子进程要开命名管道；沙箱/受限环境会拒绝。
                # 这不是配置错，换传输方式即可。
                raise AmapToolError(
                    "stdio 无法启动子进程（WinError 5：命名管道被限制）。"
                    "当前环境请改用默认的 --transport streamable-http。"
                ) from exc
        else:
            raise ValueError(f"未知传输: {self.transport}")

        self._session = await self._stack.enter_async_context(
            ClientSession(read, write, read_timeout_seconds=None)
        )
        await self._session.initialize()
        return self

    async def __aexit__(self, *exc_info) -> None:
        if self._stack is not None:
            await self._stack.aclose()
            self._stack = None
            self._session = None

    # ----------------------------------------------------------------- calls --
    async def _preflight(self) -> None:
        """先发一个裸 HTTP 请求确认 key 可用。

        为什么需要：key 无效时高德返回的是
        ``{"status":"0","info":"INVALID_USER_KEY","infocode":"10001"}``——
        200 状态码 + 非 MCP 报文。streamable-http 客户端拿不到 JSON-RPC 响应，
        会一直等下去（实测挂了 10 分钟没返回）。这里先探一次，把"卡住"变成"报错"。
        """
        import httpx

        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "trip-planner", "version": "0.1"},
            },
        }
        try:
            async with httpx.AsyncClient(timeout=15, trust_env=False) as client:
                resp = await client.post(
                    f"{HTTP_URL}?key={self.key}",
                    json=payload,
                    headers={"Accept": "application/json, text/event-stream"},
                )
        except httpx.HTTPError as exc:
            raise AmapToolError(f"连不上高德 MCP 端点: {type(exc).__name__}: {exc}") from exc

        body = resp.text[:400]
        _classify_preflight(resp.status_code, body)

    async def list_tools(self) -> list[str]:
        if self._tools is None:
            assert self._session is not None, "先 async with AmapMCP(...)"
            result = await self._session.list_tools()
            self._tools = [t.name for t in result.tools]
        return self._tools

    async def call(self, name: str, arguments: dict[str, Any]) -> str:
        """调用一个工具，把 content 里的文本拼起来返回。

        MCP 把"调用失败"表达成 isError=True + 文本，而不是抛异常。这里翻成异常，
        否则一句 "INVALID_USER_KEY" 会被当成正常数据喂给模型（第十章踩过这个坑）。
        """
        assert self._session is not None, "先 async with AmapMCP(...)"
        result = await self._session.call_tool(name, arguments)

        text = "\n".join(
            getattr(block, "text", "")
            for block in result.content
            if getattr(block, "text", None)
        ).strip()

        if getattr(result, "isError", False):
            raise AmapToolError(f"{name} 调用失败: {text[:300]}")
        return text


class AmapToolError(RuntimeError):
    """高德 MCP 工具返回 isError=True。"""


class AmapKeyError(RuntimeError):
    """高德 MCP 端点拒绝鉴权（key 无效/过期/类型不对）。"""


def _classify_preflight(status_code: int, body: str) -> None:
    """判断预检响应，不合格就抛错。

    抽成纯函数是为了能直接测：这一段决定"卡住"还是"报错"，值得被测试钉住。
    """
    if "INVALID_USER_KEY" in body or '"10001"' in body:
        raise AmapKeyError(
            "高德 key 无效（INVALID_USER_KEY）。请确认："
            "① 在 https://console.amap.com/ 创建的是「Web服务」类型的 Key；"
            "② 没有多余空格；③ 应用已启用 Web 服务 API。"
        )
    if status_code >= 400:
        raise AmapToolError(f"高德 MCP 端点返回 HTTP {status_code}: {body[:200]}")


class FakeAmap:
    """测试用：按工具名返回固定文本，形状贴近高德真实返回。"""

    def __init__(self, *, fail_tool: Optional[str] = None) -> None:
        self.fail_tool = fail_tool
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def list_tools(self) -> list[str]:
        return [
            "maps_text_search",
            "maps_around_search",
            "maps_search_detail",
            "maps_weather",
            "maps_geo",
            "maps_regeocode",
            "maps_ip_location",
            "maps_distance",
            "maps_bicycling",
            "maps_direction_walking",
            "maps_direction_driving",
            "maps_direction_transit_integrated",
        ]

    async def call(self, name: str, arguments: dict[str, Any]) -> str:
        self.calls.append((name, arguments))
        if self.fail_tool == name:
            raise AmapToolError(f"{name} 调用失败: INVALID_USER_KEY")

        if name == "maps_text_search":
            keywords = str(arguments.get("keywords", ""))
            city = str(arguments.get("city", "")) or "北京"
            # 形状要贴近真实：真实返回是 {"status":"1","count":"n","pois":[...]}，
            # 不是裸数组。第一版返回裸数组，导致新加的载荷校验在测试里误报。
            if any(k in keywords for k in ("酒店", "住宿", "宾馆")):
                pois = (
                    f'[{{"name":"{city}站前快捷酒店","address":"{city}站前路 8 号",'
                    '"location":"116.395,39.898","type":"住宿服务"},'
                    f'{{"name":"{city}中心商务酒店","address":"{city}中心广场东侧",'
                    '"location":"116.411,39.914","type":"住宿服务"}]'
                )
            elif city != "北京":
                pois = (
                    f'[{{"name":"{city}博物馆","address":"{city}人民大道 201 号",'
                    '"location":"121.473,31.230","type":"风景名胜"},'
                    f'{{"name":"{city}老城隍庙","address":"{city}方浜中路 249 号",'
                    '"location":"121.492,31.227","type":"风景名胜"},'
                    f'{{"name":"{city}滨江公园","address":"{city}滨江大道",'
                    '"location":"121.503,31.245","type":"风景名胜"}]'
                )
            else:
                pois = (
                    '[{"name":"故宫博物院","address":"东城区景山前街4号",'
                    '"location":"116.397,39.916","type":"风景名胜"},'
                    '{"name":"天坛公园","address":"东城区天坛路",'
                    '"location":"116.416,39.882","type":"风景名胜"},'
                    '{"name":"颐和园","address":"海淀区新建宫门路19号",'
                    '"location":"116.273,39.999","type":"风景名胜"}]'
                )
            count = pois.count('"name"')
            return f'{{"status":"1","count":"{count}","pois":{pois}}}'
        if name == "maps_weather":
            return (
                '{"city":"北京","forecasts":['
                '{"date":"2026-10-28","dayweather":"晴","nightweather":"多云",'
                '"daytemp":"18","nighttemp":"9","daywind":"北","daypower":"3"},'
                '{"date":"2026-10-29","dayweather":"多云","nightweather":"阴",'
                '"daytemp":"16","nighttemp":"8","daywind":"东","daypower":"2"}]}'
            )
        if name == "maps_around_search":
            return '[{"name":"如家酒店(前门店)","address":"前门西大街","location":"116.395,39.898"}]'
        return "{}"
