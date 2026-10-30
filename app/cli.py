"""命令行入口（不写前端，这就是"界面"）。

    python -m app.cli --city 北京 --days 3 --start 2026-11-01 --end 2026-11-03

真实运行需要 .env 里的 AMAP_MAPS_API_KEY 与 LLM_* 配置；
加 --fake-amap 可以用假数据跑通全图（不需要高德 key，但仍需要 LLM 才能出计划）。
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

from app.amap import AmapKeyError, AmapMCP, FakeAmap
from app.graph import build_graph
from app.models import TripPlan, TripRequest


# 项目认识的环境变量全集。新增变量时**必须**登记在这里，
# 否则部署到只有环境变量（没有 .env 文件）的平台就读不到。
# 测试会拿 .env.example 与它比对，漏登记会红。
PROJECT_ENV_KEYS: frozenset[str] = frozenset({
    "LLM_API_KEY",
    "LLM_BASE_URL",
    "LLM_MODEL_ID",
    "AMAP_MAPS_API_KEY",
    "AMAP_JS_KEY",
    "AMAP_JS_SECURITY_CODE",
    "UNSPLASH_ACCESS_KEY",
    "TAVILY_API_KEY",
    "TRIP_DATA_DIR",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "NO_PROXY",
})


def load_env() -> dict[str, str]:
    """读工作区 .env（不引额外依赖）。已存在的真实环境变量优先。"""
    candidates = [
        Path(__file__).resolve().parents[2] / ".env",  # D:\PRACTICE\.env
        Path(__file__).resolve().parents[1] / ".env",  # trip_planner/.env
    ]
    env: dict[str, str] = {}
    for path in candidates:
        if not path.is_file():
            continue
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            env.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    # 真实环境变量优先于 .env 文件（部署平台只有环境变量，没有文件）。
    # 这里按"项目认识的全部变量"取值，而不是只认写死的那几个——
    # 之前漏了 AMAP_JS_KEY / AMAP_JS_SECURITY_CODE / UNSPLASH_ACCESS_KEY，
    # 本地读 .env 看不出问题，一部署地图就降级成静态图。
    env.update({k: os.environ[k] for k in PROJECT_ENV_KEYS if k in os.environ})
    return env


def build_llm(env: dict[str, str]):
    from langchain_openai import ChatOpenAI

    api_key = env.get("LLM_API_KEY", "")
    base_url = env.get("LLM_BASE_URL", "")
    model = env.get("LLM_MODEL_ID", "gpt-4o-mini")
    if not api_key:
        raise SystemExit("缺少 LLM_API_KEY（在项目根目录 .env 或环境变量里配置）")

    kwargs: dict = {"model": model, "api_key": api_key, "temperature": 0.2}
    if base_url:
        kwargs["base_url"] = base_url

    proxy = env.get("HTTPS_PROXY") or env.get("HTTP_PROXY")
    if proxy:
        # 显式给 httpx 客户端，而不是依赖 openai_proxy：
        # 后者在 uvicorn 里实测会连不上（同一个 key、同一份配置，CLI 能过、API 500）。
        # trust_env=False 避免再叠加一层系统代理。
        import httpx

        kwargs["http_client"] = httpx.Client(proxy=proxy, timeout=180, trust_env=False)
        kwargs["http_async_client"] = httpx.AsyncClient(
            proxy=proxy, timeout=180, trust_env=False
        )
    return ChatOpenAI(**kwargs)


def to_markdown(plan: TripPlan) -> str:
    lines = [
        f"# {plan.city} {plan.start_date} ~ {plan.end_date}",
        "",
        f"{plan.overall_suggestions}".strip(),
        "",
    ]
    if plan.budget:
        b = plan.budget
        lines += [
            "## 预算估算",
            f"- 门票 {b.total_attractions} 元 · 住宿 {b.total_hotels} 元 · "
            f"餐饮 {b.total_meals} 元 · 交通 {b.total_transportation} 元",
            f"- **合计 {b.total} 元**",
            "",
        ]
    for day in plan.days:
        lines.append(f"## 第 {day.day_index + 1} 天 · {day.date}")
        if day.description:
            lines.append(f"{day.description}")
        # 模型有时用 "无" 表示当天不需要住宿，别把它当酒店名打出来
        hotel_name = (day.hotel.name or "").strip() if day.hotel else ""
        if hotel_name and hotel_name not in {"无", "None", "null", "-"}:
            lines.append(
                f"- 住宿：{hotel_name}（{day.hotel.estimated_cost} 元/晚）"
            )
        for a in day.attractions:
            price = f"{a.ticket_price} 元" if a.ticket_price else "免费"
            loc = ""
            if a.location:
                loc = f" `{a.location.longitude},{a.location.latitude}`"
            lines.append(f"- 景点：{a.name}（{a.visit_duration} 分钟，{price}）{loc}")
        for m in day.meals:
            lines.append(f"- {m.type}：{m.name}（{m.estimated_cost} 元）")
        lines.append("")
    if plan.weather_info:
        lines.append("## 天气")
        for w in plan.weather_info:
            lines.append(
                f"- {w.date}：{w.day_weather} / {w.night_weather}，"
                f"{w.day_temp}~{w.night_temp}°C"
            )
    return "\n".join(lines)


async def run(args: argparse.Namespace) -> int:
    env = load_env()
    request = TripRequest(
        city=args.city,
        start_date=args.start,
        end_date=args.end,
        days=args.days,
        preferences=args.preferences,
        budget_level=args.budget,
        transportation=args.transportation,
        accommodation=args.accommodation,
    )

    llm = build_llm(env)

    if args.fake_amap:
        print("[env] 使用假高德数据（--fake-amap）")
        tools = FakeAmap()
        update = await _invoke(build_graph(), request, tools, llm, env)
    else:
        key = env.get("AMAP_MAPS_API_KEY", "")
        if not key:
            raise SystemExit(
                "缺少 AMAP_MAPS_API_KEY。请到 https://console.amap.com/ 创建"
                "「Web 服务」类型的 Key，写进项目根目录 .env 或环境变量"
            )
        print(f"[env] 高德 MCP 传输方式：{args.transport}")
        # 同 API：整个请求共用一个会话，进入/退出必须在同一个 task
        async with AmapMCP(key, transport=args.transport) as tools:
            try:
                names = await tools.list_tools()
            except AmapKeyError as exc:
                raise SystemExit(f"高德 Key 无效: {exc}") from exc
            print(f"[env] 高德 MCP 已连接，{len(names)} 个工具")
            update = await _invoke(build_graph(), request, tools, llm, env)

    plan = update.get("plan")
    print()
    for note in update.get("trace", []):
        print(f"  · {note}")
    for err in update.get("errors", []):
        print(f"  ! {err}")

    if plan is None:
        print("\n没有生成计划。模型原始输出：\n")
        print(update.get("raw_plan", "(空)")[:2000])
        return 1

    print()
    print(to_markdown(plan))
    if args.out:
        Path(args.out).write_text(to_markdown(plan), encoding="utf-8")
        print(f"\n已写入 {args.out}")
    return 0


async def _invoke(graph, request, tools, llm, env) -> dict:
    return await graph.ainvoke(
        {
            "request": request,
            "tools": tools,
            "llm": llm,
            "attractions_raw": "",
            "weather_raw": "",
            "hotels_raw": "",
            "plan": None,
            "raw_plan": "",
            "errors": [],
            "trace": [],
        }
    )


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="智能旅行助手（LangGraph 版，无前端）")
    p.add_argument("--city", default="北京")
    p.add_argument("--start", default="2026-11-01")
    p.add_argument("--end", default="2026-11-03")
    p.add_argument("--days", type=int, default=3)
    p.add_argument("--preferences", default="历史文化")
    p.add_argument("--budget", default="中等")
    p.add_argument("--transportation", default="公共交通")
    p.add_argument("--accommodation", default="经济型酒店")
    p.add_argument(
        "--transport",
        default="streamable-http",
        choices=["streamable-http", "stdio"],
        help="高德 MCP 传输方式",
    )
    p.add_argument("--fake-amap", action="store_true", help="用假高德数据跑通图")
    p.add_argument("--out", default="", help="把 Markdown 结果写到文件")
    args = p.parse_args(argv)

    try:
        return asyncio.run(run(args))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
