"""四个节点 + 一个解析工具。

图是线性的：attraction → weather → hotel → planner。
之所以不做并行：MCP 会话是单连接，并行调用收益有限，而串行让 trace 更好读。
（真实耗时主要在一次 LLM 规划调用上，三个工具调用各不到 1 秒。）
"""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from app.amap import AmapToolError
from app.models import Budget, TripPlan, WeatherInfo
from app.prefs import derive_hotel_keyword, hotel_keyword_reason  # noqa: E402
from app.prompts import planner_system, planner_user_prompt
from app.state import TripState


# --------------------------------------------------------------- 工具节点 --
def _check_payload(kind: str, text: str) -> str:
    """校验工具返回的载荷，不合格时返回原因（空字符串表示合格）。

    为什么需要：高德偶尔会返回"空壳"——比如 `{"city":"","forecasts":[]}`（30 字符）。
    这种不是 isError，所以不会抛异常，会被当成正常数据喂给模型，模型就自己编天气。
    实测出现过一次，所以在这里挡住，宁可让天气为空并记一条错误。
    """
    if not text or not text.strip():
        return "返回为空"
    if kind == "weather":
        if '"forecasts"' not in text:
            return f"没有 forecasts 字段（前 80 字符: {text[:80]!r}）"
        if '"date"' not in text:
            return "forecasts 里没有日期"
    elif kind == "poi":
        if '"pois"' not in text:
            return f"没有 pois 字段（前 80 字符: {text[:80]!r}）"
    return ""


async def _call_tool(
    tools: Any,
    name: str,
    arguments: dict[str, Any],
    kind: str,
    *,
    attempts: int = 2,
) -> tuple[str, str]:
    """调一个工具并校验载荷；空壳就重试一次。

    高德 MCP 在连续调用时会偶发返回空壳（`{"city":"","forecasts":[]}` 之类），
    单独调同一个工具又是正常的——属于服务端抖动/限流。重试一次能救回大部分。
    返回 (载荷, 错误说明)；两者必有其一为空。
    """
    last = ""
    for attempt in range(attempts):
        try:
            raw = await tools.call(name, arguments)
        except AmapToolError as exc:
            last = f"调用失败: {exc}"
        else:
            problem = _check_payload(kind, raw)
            if not problem:
                return raw, ""
            last = f"数据异常: {problem}"

        if attempt + 1 < attempts:
            await asyncio.sleep(0.5)

    return "", last


async def attraction_node(state: TripState) -> dict[str, Any]:
    """景点：keywords 直接取用户偏好，city 取目的地。"""
    req = state["request"]
    raw, err = await _call_tool(
        state["tools"],
        "maps_text_search",
        {"keywords": f"{req.preferences}景点", "city": req.city},
        "poi",
    )
    if err:
        return {"attractions_raw": "", "errors": [f"景点搜索失败: {err}"]}
    return {"attractions_raw": raw}


async def weather_node(state: TripState) -> dict[str, Any]:
    req = state["request"]
    raw, err = await _call_tool(
        state["tools"], "maps_weather", {"city": req.city}, "weather"
    )
    if err:
        return {"weather_raw": "", "errors": [f"天气查询失败: {err}"]}
    return {"weather_raw": raw}


async def adjust_node(state: TripState) -> dict[str, Any]:
    """条件路由命中时执行：为坏天气补查室内候选。

    只查候选、不替模型改行程——把候选和"必须用室内"的约束交给 planner。
    生成之后再用 verify_plan 检查一次，违规就写进 routing.violations，
    前端会提示用户。
    """
    from app.routing import INDOOR_SEARCH_KEYWORDS, rainy_days

    req = state["request"]
    weather = _weather_from_raw(state.get("weather_raw", ""), req.start_date, req.days)
    days = rainy_days(weather)
    date_set = {d["date"] for d in days}

    raw, err = await _call_tool(
        state["tools"],
        "maps_text_search",
        {"keywords": INDOOR_SEARCH_KEYWORDS, "city": req.city},
        "poi",
    )

    routing = {
        "needed": True,
        "days": days,
        "indoor_raw_chars": len(raw or ""),
        "violations": [],
    }
    update: dict[str, Any] = {"routing": routing}
    if err:
        # 查不到室内候选不算整体失败：退化为"只提示不改"，
        # 让 planner 用原有景点，但把风险告诉用户。
        update["errors"] = [f"室内候选查询失败: {err}"]
        update["indoor_raw"] = ""
        routing["indoor_query_failed"] = True
        return update

    update["indoor_raw"] = raw
    return update


async def hotel_node(state: TripState) -> dict[str, Any]:
    req = state["request"]
    # 「其他要求」里如果说"想住舒适一点"，搜索词要跟着改——否则只会在经济型里挑，
    # 提示词写得再狠也变不出舒适型酒店。映射是确定性的（见 app/prefs.py）。
    keyword = derive_hotel_keyword(req.accommodation, getattr(req, "notes", ""))
    raw, err = await _call_tool(
        state["tools"],
        "maps_text_search",
        {"keywords": keyword, "city": req.city},
        "poi",
    )
    if err:
        return {"hotels_raw": "", "errors": [f"酒店搜索失败: {err}"]}
    return {
        "hotels_raw": raw,
        "trace": [hotel_keyword_reason(req.accommodation, getattr(req, "notes", ""), keyword)],
    }


# ------------------------------------------------------------------ 规划 --
def _weather_from_raw(raw: str, start_date: str, days: int) -> list[dict[str, Any]]:
    """把高德天气原文直接转成 weather_info，不经过模型。

    为什么不交给模型：天气是**数据**不是创作。实测 gemini-flash 有几次明明拿到了
    893 字符的 forecasts，却在输出里把 weather_info 写成空数组。让程序解析一遍比
    "在提示词里再强调一次"可靠得多。
    """
    if not raw.strip():
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []

    forecasts = data.get("forecasts") if isinstance(data, dict) else None
    if not isinstance(forecasts, list):
        return []

    def to_entry(f: dict[str, Any]) -> dict[str, Any]:
        return {
            "date": str(f.get("date", "")),
            "day_weather": str(f.get("dayweather", "")),
            "night_weather": str(f.get("nightweather", "")),
            "day_temp": str(f.get("daytemp", "0")),
            "night_temp": str(f.get("nighttemp", "0")),
            "wind_direction": str(f.get("daywind", "")),
            "wind_power": str(f.get("daypower", "")),
        }

    # 优先挑行程日期当天；匹配不上就用原文的前 N 天（保留真实日期，不改写成行程日期）
    wanted = set()
    try:
        from datetime import date, timedelta

        base = date.fromisoformat(start_date)
        wanted = {(base + timedelta(days=i)).isoformat() for i in range(days)}
    except ValueError:
        pass

    matched = [to_entry(f) for f in forecasts if str(f.get("date")) in wanted]
    if matched:
        return matched[:days]

    # 预报里没有行程日期（高德只给未来 4 天，行程在更远的将来）时返回空，
    # **不要**回退成"原文前 N 天"。第一版就是这么干的，结果 11-01 的行程
    # 配上了 10-28 的天气——日期是真的，但张冠李戴，比没有更糟：
    # 用户会以为那天下雨，条件路由也会拿错日期去比对。
    return []


async def planner_node(state: TripState) -> dict[str, Any]:
    """唯一的 LLM 调用点：排日程 + 估预算，输出 TripPlan。"""
    req = state["request"]
    llm = state["llm"]

    user = planner_user_prompt(
        city=req.city,
        start_date=req.start_date,
        end_date=req.end_date,
        days=req.days,
        preferences=req.preferences,
        budget_level=req.budget_level,
        transportation=req.transportation,
        accommodation=req.accommodation,
        attractions_raw=state.get("attractions_raw", ""),
        weather_raw=state.get("weather_raw", ""),
        hotels_raw=state.get("hotels_raw", ""),
        budget_total=getattr(req, "budget_total", None),
        notes=getattr(req, "notes", "") or "",
    )
    system = planner_system(req.days, req.start_date, state.get("memory", ""))
    from app.prompts import routing_note

    routing = dict(state.get("routing") or {})
    note = routing_note(routing)
    if note:
        system = system + "\n" + note

    # 高德只给今天起 4 天预报。行程更长时，别让模型把没预报的日子"补"出来：
    # weather_info 反正会被程序重建覆盖，但 it 会在 description 里写"第 5 天晴朗"。
    forecast = _weather_from_raw(state.get("weather_raw", ""), req.start_date, req.days)
    if forecast and len(forecast) < req.days:
        system += (
            f"\n## 天气数据范围\n只有前 {len(forecast)} 天有天气预报"
            f"（{forecast[0]['date']} ~ {forecast[-1]['date']}）。\n"
            f"其余 {req.days - len(forecast)} 天**没有任何天气数据**："
            "不要编造天气，也不要在当天描述里写晴雨，weather_info 里只保留给出的那几天。\n"
        )

    resp = None
    last_error = ""
    # 代理/网关偶发断连（实测 httpcore.RemoteProtocolError: Server disconnected），
    # 重试一次通常就过去了；仍然失败就把原因如实带回上层，不要抛到 ASGI 里变 500。
    for attempt in range(2):
        try:
            resp = await llm.ainvoke(
                [SystemMessage(content=system), HumanMessage(content=user)]
            )
            break
        except Exception as exc:  # noqa: BLE001 - 任何模型侧异常都按"调用失败"处理
            last_error = f"{type(exc).__name__}: {exc}"
            if attempt == 0:
                await asyncio.sleep(2)

    if resp is None:
        return {
            "plan": None,
            "errors": [f"模型调用失败（已重试）: {last_error[:300]}"],
            "raw_plan": "",
        }

    text = _as_text(resp.content)

    try:
        plan = parse_trip_plan(text)
    except Exception as exc:  # noqa: BLE001 - 解析失败要把原文带出去，便于排查
        return {
            "plan": None,
            "errors": [f"规划输出解析失败: {type(exc).__name__}: {exc}"],
            "raw_plan": text,
        }

    # 预算合计以分项为准：模型给的 total 经常和分项对不上
    if plan.budget is None:
        plan.budget = Budget()
    plan.budget.recompute_total()

    # day_index 以列表顺序为准：模型经常从 1 开始、或干脆乱序。
    # 这个字段是给人看的序号，不该交给模型决定。
    for idx, day in enumerate(plan.days):
        day.day_index = idx

    # 天气一律用工具原文重建：模型偶尔把 weather_info 写成空数组，
    # 而这份数据是查到的、不是编的，程序解析更可靠。
    weather_raw = state.get("weather_raw") or ""
    rebuilt = _weather_from_raw(weather_raw, plan.start_date or req.start_date, req.days)
    if rebuilt:
        plan.weather_info = [WeatherInfo(**w) for w in rebuilt]
    elif not weather_raw.strip():
        plan.weather_info = []

    # 条件路由的确定性校验：坏天气那天还排着"确定是室外"的景点就记一笔。
    # 不自动改行程——改行程需要重新调模型，代价大且不可控；
    # 先把问题如实暴露给用户，比偷偷改了更好。
    if routing.get("needed"):
        from app.routing import verify_plan

        violations = verify_plan(plan, [d["date"] for d in routing.get("days") or []])
        routing["violations"] = violations
        update: dict[str, Any] = {"plan": plan, "raw_plan": text, "routing": routing}
        if violations:
            update["errors"] = [v["message"] for v in violations]
        return update

    return {"plan": plan, "raw_plan": text}


# ------------------------------------------------------------------ 工具 --
def _as_text(content: Any) -> str:
    """LangChain 的 content 可能是 str，也可能是 content block 列表。"""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and block.get("type") == "text":
                parts.append(str(block.get("text", "")))
        return "".join(parts)
    return str(content)


_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


def parse_trip_plan(text: str) -> TripPlan:
    """从模型输出里抠出 JSON 并校验。

    模型常见的三种走样：包了 ```json 围栏、前后带解释文字、末尾多逗号。
    前两种这里处理，最后一种交给一次宽松修复。
    """
    cleaned = _FENCE_RE.sub("", text).strip()

    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError(f"输出里找不到 JSON 对象（前 120 字符: {text[:120]!r}）")
    body = cleaned[start : end + 1]

    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        data = json.loads(re.sub(r",\s*([}\]])", r"\1", body))

    return TripPlan.model_validate(data)
