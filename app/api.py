"""HTTP 接口层：把 CLI 的能力包成一个 API，给前端用。

设计取舍
--------
* **每次请求新建一个 MCP 会话**。开一次 streamable-http 会话约 1 秒，换来的是
  "服务可以随起随停、不会因为某个连接卡死而拖垮整个进程"。等高德调用量上来了
  再考虑用进程级单例 + 重连。
* **图实例也是每次新建**。`build_graph()` 只做装配，很便宜；共享反而要处理
  State 串味的问题。
* 接口只回 `TripPlan`，不回原始工具文本——前端不需要看 3000 字符的 POI JSON。
  原始文本和 trace 走 `/api/trip/plan/debug`（本地调试用）。
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, ConfigDict, Field

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.amap import AmapKeyError, AmapMCP, AmapToolError  # noqa: E402
from app.cli import build_llm, load_env, to_markdown  # noqa: E402
from app.constraints import check_budget  # noqa: E402
from app.graph import build_graph  # noqa: E402
from app.images import enrich_plan_images  # noqa: E402
from app.ics import build_calendar, filename_for  # noqa: E402
from app.models import TripPlan, TripRequest  # noqa: E402
from app.packing import build_packing  # noqa: E402
from app.share import InvalidToken as InvalidShareToken  # noqa: E402
from app.share import ShareStore  # noqa: E402
from app.memory import (  # noqa: E402
    DEFAULT_USER,
    InvalidUserId,
    ProfileStore,
    UserTripStore,
    check_user,
    memory_note,
)
from app.store import InvalidTripId, TripNotFound, TripStore  # noqa: E402

app = FastAPI(title="智能旅行助手 API", version="0.1.0")

# 前端 dev server 是 5173，生产是静态文件；这里放开 CORS 省事
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class PlanResponse(BaseModel):
    plan: TripPlan
    markdown: str = Field(description="同一份结果的 Markdown 渲染，便于复制")
    elapsed_ms: int
    trace: list[str] = Field(default_factory=list)
    # 工具部分失败但行程可用时的说明。前端据此显示「数据提示」卡片。
    errors: list[str] = Field(default_factory=list)
    # 记忆：本次用到了哪些历史偏好（前端可以显示「它记住了什么」）
    memory: dict[str, Any] = Field(default_factory=dict)
    # 条件路由：下雨天是否改室内、改了哪几天、模型有没有照做
    routing: dict[str, Any] = Field(default_factory=dict)
    # 装备清单：按天气/天数/交通/住宿推出来的（确定性规则，不是模型写的）
    packing: dict[str, Any] = Field(default_factory=dict)
    # 预算校验：设了上限时，由程序算出"超没超、超多少"，不靠模型自觉
    budget_check: dict[str, Any] = Field(default_factory=dict)


class DebugResponse(PlanResponse):
    attractions_raw: str = ""
    weather_raw: str = ""
    hotels_raw: str = ""
    raw_plan: str = ""


def _run_kwargs(transport: str = "streamable-http") -> tuple[dict[str, str], Any]:
    env = load_env()
    key = env.get("AMAP_MAPS_API_KEY", "")
    if not key:
        raise HTTPException(
            status_code=500,
            detail="服务端缺少 AMAP_MAPS_API_KEY，请检查 D:\\PRACTICE\\.env",
        )
    return env, key


async def _generate(
    request: TripRequest, transport: str = "streamable-http", user: str = DEFAULT_USER
) -> dict[str, Any]:
    import time

    started = time.perf_counter()
    env, key = _run_kwargs(transport)

    # 记忆：拿到该用户的画像，拼成一句话注入提示词。没有画像就是空串，不污染提示词。
    hints = _profiles.hints(user)
    note = memory_note(hints)

    try:
        llm = build_llm(env)
    except SystemExit as exc:  # build_llm 用 SystemExit 报配置缺失
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    try:
        # 整个请求共用一个 MCP 会话，用 async with 保证**进入和退出在同一个 task**。
        # 注意：不能用"懒加载"——在节点 task 里进入、在请求 task 里退出会 500：
        #   RuntimeError: Attempted to exit cancel scope in a different task
        # 对照实验（三种模式）见 docs\设计判断-智能旅行助手.md。
        async with AmapMCP(key, transport=transport) as tools:
            graph = build_graph()
            final = await graph.ainvoke(
                {
                    "request": request,
                    "tools": tools,
                    "llm": llm,
                    "memory": note,
                    "attractions_raw": "",
                    "weather_raw": "",
                    "hotels_raw": "",
                    "plan": None,
                    "raw_plan": "",
                    "errors": [],
                    "trace": [],
                }
            )
    except AmapKeyError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except AmapToolError as exc:
        raise HTTPException(status_code=502, detail=f"高德 MCP 出错: {exc}") from exc

    plan = final.get("plan")
    if plan is None:
        errors = "；".join(final.get("errors", [])) or "未知原因"
        # 模型连接失败用 503（可重试），其余用 502；前端据此给不同提示
        status = 503 if "模型调用失败" in errors else 502
        raise HTTPException(status_code=status, detail=f"未能生成行程：{errors}")

    # 补图是锦上添花：没有 UNSPLASH_ACCESS_KEY 就跳过；
    # 限流/网络失败也只让 image_url 保持 null，绝不影响行程本身。
    unsplash = env.get("UNSPLASH_ACCESS_KEY", "")
    if unsplash:
        image_stats: dict[str, Any] = {}
        try:
            filled = await enrich_plan_images(plan, unsplash, stats=image_stats)
            if image_stats.get("rate_limited"):
                final.setdefault("trace", []).append("images: 限流，本轮跳过")
            elif filled:
                final.setdefault("trace", []).append(f"images: {filled} 张")
        except Exception as exc:  # noqa: BLE001
            final.setdefault("trace", []).append(f"images: 跳过（{type(exc).__name__}）")

    final["elapsed_ms"] = int((time.perf_counter() - started) * 1000)
    final["markdown"] = to_markdown(plan)
    # 装备清单是纯规则，放在这里算：规划完就能看到，不必再打一次接口
    final["packing"] = build_packing(plan)
    # 预算是硬约束：模型说"没超"不算数，这里按四项合计重新算一遍
    final["budget_check"] = check_budget(plan, request.budget_total)
    return final


@app.get("/api/health")
async def health() -> dict[str, Any]:
    env = load_env()
    return {
        "ok": True,
        "amap_key_configured": bool(env.get("AMAP_MAPS_API_KEY")),
        "amap_js_configured": bool(env.get("AMAP_JS_KEY") and env.get("AMAP_JS_SECURITY_CODE")),
        "llm_model": env.get("LLM_MODEL_ID", ""),
        "python": sys.version.split()[0],
    }


# ------------------------------------------------------------------ 地图 --
# 两类 Key 不要混：
#   AMAP_MAPS_API_KEY     「Web服务」——后端调 REST（MCP、静态地图）
#   AMAP_JS_KEY + 安全密钥  「Web端(JS API)」——浏览器里画交互地图
# 面向前端时只发 JS 那两个；Web服务 key 永不外泄。
@app.get("/api/config/map")
async def map_config() -> dict[str, Any]:
    env = load_env()
    key = env.get("AMAP_JS_KEY", "")
    code = env.get("AMAP_JS_SECURITY_CODE", "")
    return {"configured": bool(key and code), "key": key, "security_code": code}


_STATIC_CACHE: dict[str, tuple[bytes, str]] = {}
_STATIC_CACHE_MAX = 40


class StaticMapPoint(BaseModel):
    """一个点位。

    这里必须容忍额外字段：前端顺手把景点名一起传过来，而 dict[str, float] 会把
    `name` 当数字解析 —— 实测直接 422，页面上每一张地图都变成"暂时取不到"。
    """

    model_config = ConfigDict(extra="ignore")

    lng: float
    lat: float


class StaticMapRequest(BaseModel):
    points: list[StaticMapPoint] = Field(
        default_factory=list, description="[{lng, lat}, ...]，至少一个；其它字段会被忽略"
    )
    zoom: int = Field(default=11, ge=3, le=17)


@app.post("/api/staticmap")
async def static_map(req: StaticMapRequest) -> Any:
    """代理高德静态地图，供「导出图片」用。

    为什么不让浏览器直连高德：① 静态地图也要用「Web服务」Key，不能下发到前端；
    ② 直连会被 CORS 和跨域 canvas 污染卡住，导出必然失败。
    这里由服务端取图后同源返回，前端 `<img>` 一嵌就能进 html2canvas。
    """
    from fastapi.responses import Response

    # 先校验入参，再看配置：请求本身不合法时应该 400，
    # 不该因为"服务端没配 key"变成 500——部署环境（Vercel）没 key 时就会撞上这个次序问题。
    if not req.points:
        raise HTTPException(status_code=400, detail="points 不能为空")

    env = load_env()
    key = env.get("AMAP_MAPS_API_KEY", "")
    if not key:
        raise HTTPException(status_code=503, detail="服务端未配置 AMAP_MAPS_API_KEY（静态地图不可用）")

    lngs = [p.lng for p in req.points]
    lats = [p.lat for p in req.points]
    center = f"{sum(lngs) / len(lngs):.6f},{sum(lats) / len(lats):.6f}"
    # 最多 10 个标记，高德静态地图有数量限制
    markers = "|".join(
        f"mid,0x2f6bff,{i + 1}:{p.lng:.6f},{p.lat:.6f}"
        for i, p in enumerate(req.points[:10])
    )

    params = {
        "location": center,
        "zoom": req.zoom,
        "size": "700*360",
        "scale": 2,
        "markers": markers,
        "key": key,
    }

    # 同一组点位反复取图（切天、重绘、打印前批量取）没必要每次都打高德，
    # 内存里留几十张就够了——高德静态地图是按次计费的。
    cache_key = json.dumps(
        [[round(p.lng, 4), round(p.lat, 4)] for p in req.points[:10]] + [req.zoom],
        sort_keys=True,
    )
    hit = _STATIC_CACHE.get(cache_key)
    if hit is not None:
        content, ctype = hit
        return Response(
            content=content, media_type=ctype, headers={"X-Cache": "HIT", "Cache-Control": "private, max-age=600"}
        )

    try:
        async with httpx.AsyncClient(timeout=20, trust_env=False) as client:
            resp = await client.get("https://restapi.amap.com/v3/staticmap", params=params)
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"取静态地图失败: {exc}") from exc

    ctype = resp.headers.get("content-type", "")
    if resp.status_code != 200 or not ctype.startswith("image/"):
        # 高德出错时返回 JSON，把原因带出去便于排查
        raise HTTPException(
            status_code=502,
            detail=f"高德返回非图片（HTTP {resp.status_code}）：{resp.text[:200]}",
        )

    _STATIC_CACHE[cache_key] = (resp.content, ctype)
    while len(_STATIC_CACHE) > _STATIC_CACHE_MAX:
        _STATIC_CACHE.pop(next(iter(_STATIC_CACHE)))
    return Response(
        content=resp.content, media_type=ctype, headers={"X-Cache": "MISS", "Cache-Control": "private, max-age=600"}
    )


@app.post("/api/trip/plan", response_model=PlanResponse)
async def plan_trip(
    request: TripRequest, x_user_id: str | None = Header(default=None)
) -> PlanResponse:
    user = _user_or_400(x_user_id)
    final = await _generate(request, user=user)
    # 成功之后才记画像：失败的需求不该被记住
    try:
        _profiles.record(user, request, final["plan"])
    except Exception:  # noqa: BLE001 - 记不住画像不影响本次结果
        pass
    return PlanResponse(
        plan=final["plan"],
        markdown=final["markdown"],
        elapsed_ms=final["elapsed_ms"],
        trace=final.get("trace", []),
        errors=final.get("errors", []),
        memory={
            "user": user,
            "used": bool(final.get("memory_note")),
            "hints": final.get("hints", {}),
        },
        routing=final.get("routing", {}) or {},
        packing=final.get("packing", {}) or {},
        budget_check=final.get("budget_check", {}) or {},
    )


@app.post("/api/trip/plan/debug", response_model=DebugResponse)
async def plan_trip_debug(
    request: TripRequest, x_user_id: str | None = Header(default=None)
) -> DebugResponse:
    """本地排查用：把工具原文和模型原始输出也带出来。"""
    user = _user_or_400(x_user_id)
    final = await _generate(request, user=user)
    return DebugResponse(
        plan=final["plan"],
        markdown=final["markdown"],
        elapsed_ms=final["elapsed_ms"],
        trace=final.get("trace", []),
        errors=final.get("errors", []),
        attractions_raw=final.get("attractions_raw", ""),
        weather_raw=final.get("weather_raw", ""),
        hotels_raw=final.get("hotels_raw", ""),
        raw_plan=final.get("raw_plan", ""),
    )


# ------------------------------------------------------------ 行程持久化 --
# 存成 JSON 文件（见 app/store.py）。前端刷新/重开还能把行程捞回来。
class SaveTripRequest(BaseModel):
    plan: dict[str, Any] = Field(description="完整行程，服务端会按 TripPlan 校验")
    title: str = Field(default="", description="行程标题，留空则用「城市 + 日期」")
    markdown: str = Field(default="", description="可选的 Markdown 版本")


def _store() -> UserTripStore:
    """按用户分区的行程存储（``data/trips/<user>/``）。"""
    return UserTripStore()


_profiles = ProfileStore()


def _user_or_400(x_user_id: str | None) -> str:
    """从请求头取用户；不合法直接 400（防路径穿越）。未提供则归到 anonymous。"""
    try:
        return check_user(x_user_id)
    except InvalidUserId as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


class ReplanRequest(BaseModel):
    """多轮编辑：选中哪几天 + 一句新要求。"""

    selected_days: list[int] = Field(
        ..., min_length=1, description="要重排的天，从 0 开始的下标"
    )
    requirement: str = Field(default="", description="例如：第 2 天下雨，全换成室内")


class ReplanConfirm(BaseModel):
    approve: bool = Field(..., description="true 落地，false 放弃")


# 未完成的编辑会话：(user, trip_id) -> thread_id。
# 同一行程只允许一个——两个会话同时改一份计划，最后写入的会覆盖前面，没法解释。
_replan_sessions: dict[tuple[str, str], str] = {}

# 分享链接存储：与行程分开，一个 token 对应一条记录
_shares = ShareStore()


@app.post("/api/trips")
async def save_trip(
    req: SaveTripRequest, x_user_id: str | None = Header(default=None)
) -> dict[str, Any]:
    """新建一份行程记录（存到该用户自己的目录）。"""
    user = _user_or_400(x_user_id)
    try:
        # 用模型校验一遍：前端编辑后可能传来缺字段的结构，早点报错好定位
        validated = TripPlan.model_validate(req.plan)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=422, detail=f"行程结构不合法: {exc}") from exc

    meta = _store().save(
        validated.model_dump(),
        title=req.title,
        markdown=req.markdown,
        user=user,
    )
    return {"ok": True, "trip": meta}


@app.put("/api/trips/{trip_id}")
async def update_trip(
    trip_id: str, req: SaveTripRequest, x_user_id: str | None = Header(default=None)
) -> dict[str, Any]:
    """覆盖保存已有行程（id 不存在时也允许写入，便于前端重试）。"""
    user = _user_or_400(x_user_id)
    try:
        validated = TripPlan.model_validate(req.plan)
        meta = _store().save(
            validated.model_dump(),
            title=req.title,
            trip_id=trip_id,
            markdown=req.markdown,
            user=user,
        )
    except InvalidTripId as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=422, detail=f"行程结构不合法: {exc}") from exc
    return {"ok": True, "trip": meta}


@app.get("/api/trips")
async def list_trips(
    limit: int = 50, x_user_id: str | None = Header(default=None)
) -> dict[str, Any]:
    """只返回该用户的行程——这是"多用户隔离"最直接的一处体现。"""
    user = _user_or_400(x_user_id)
    return {"items": _store().list(limit=max(1, min(limit, 200)), user=user)}


@app.get("/api/trips/{trip_id}")
async def get_trip(
    trip_id: str, x_user_id: str | None = Header(default=None)
) -> dict[str, Any]:
    user = _user_or_400(x_user_id)
    try:
        record = _store().get(trip_id, user=user)
    except InvalidTripId as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except TripNotFound as exc:
        raise HTTPException(status_code=404, detail=f"没有这份行程: {trip_id}") from exc
    return record


@app.post("/api/trips/{trip_id}/replan")
async def start_replan(
    trip_id: str,
    req: ReplanRequest,
    x_user_id: str | None = Header(default=None),
) -> dict[str, Any]:
    """发起一次局部重排。返回 status=pending 表示**停在等待确认**，不是失败。"""
    import uuid

    from app.replan import get_graph

    user = _user_or_400(x_user_id)
    store = _store()
    try:
        record = store.get(trip_id, user=user)
    except (TripNotFound, InvalidTripId) as exc:
        raise HTTPException(status_code=404, detail="行程不存在") from exc

    if (user, trip_id) in _replan_sessions:
        raise HTTPException(
            status_code=409,
            detail="这份行程还有一个未完成的编辑，请先确认或取消它",
        )

    old_plan = TripPlan(**record["plan"])
    day_count = len(old_plan.days)
    bad = [d for d in req.selected_days if d < 0 or d >= day_count]
    if bad:
        raise HTTPException(
            status_code=400, detail=f"天数下标越界：{bad}（这份行程共 {day_count} 天）"
        )

    try:
        llm = build_llm(load_env())
    except SystemExit as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    thread_id = f"{user}:{trip_id}:{uuid.uuid4().hex[:8]}"
    config = {"configurable": {"thread_id": thread_id}}

    try:
        out = await get_graph().ainvoke(
            {
                "old_plan": old_plan,
                "selected": sorted(set(req.selected_days)),
                "requirement": req.requirement.strip(),
                "llm": llm,
                "trip_id": trip_id,
                "user": user,
                "errors": [],
            },
            config,
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=502, detail=f"重排失败: {type(exc).__name__}: {exc}"
        ) from exc

    interrupts = out.get("__interrupt__") or []
    if interrupts:
        payload = interrupts[0].value or {}
        _replan_sessions[(user, trip_id)] = thread_id
        return {
            "status": "pending",
            "thread_id": thread_id,
            "selected": payload.get("selected", []),
            "preview": payload.get("preview", {}),
            # 模型第几次才守住局部性：1 = 一次就对；2 = 越界被拦后重试成功
            "attempts": payload.get("attempts", 1),
        }

    errors = out.get("errors") or ["模型没有产出可用结果"]
    raise HTTPException(status_code=422, detail="；".join(errors))


@app.post("/api/trips/{trip_id}/replan/confirm")
async def confirm_replan(
    trip_id: str,
    req: ReplanConfirm,
    x_user_id: str | None = Header(default=None),
) -> dict[str, Any]:
    """确认或放弃。确认后只写回这一份行程，标题保留。"""
    from langgraph.types import Command

    from app.replan import get_graph

    user = _user_or_400(x_user_id)
    thread_id = _replan_sessions.get((user, trip_id))
    if not thread_id:
        raise HTTPException(status_code=404, detail="没有等待确认的编辑会话")

    config = {"configurable": {"thread_id": thread_id}}
    try:
        out = await get_graph().ainvoke(
            Command(resume="approve" if req.approve else "reject"), config
        )
    finally:
        _replan_sessions.pop((user, trip_id), None)

    if not req.approve:
        return {"status": "rejected", "errors": out.get("errors", [])}

    plan = out.get("final_plan")
    if plan is None:
        raise HTTPException(status_code=502, detail="确认后没有拿到新计划")

    store = _store()
    try:
        record = store.get(trip_id, user=user)
        title = record.get("title", "")
    except (TripNotFound, InvalidTripId):
        title = ""

    meta = store.save(
        plan.model_dump(), title=title, trip_id=trip_id,
        markdown=to_markdown(plan), user=user,
    )
    return {
        "status": "applied",
        "plan": plan.model_dump(),
        "markdown": to_markdown(plan),
        "trip": meta,
    }


def _owned_trip(trip_id: str, user: str) -> dict[str, Any]:
    """取本人行程；不存在或不属于自己都按 404（不暴露"存在但不是你的"）。"""
    try:
        return _store().get(trip_id, user=user)
    except (TripNotFound, InvalidTripId) as exc:
        raise HTTPException(status_code=404, detail="行程不存在") from exc


@app.get("/api/trips/{trip_id}/export.ics")
async def export_ics(
    trip_id: str, x_user_id: str | None = Header(default=None)
) -> Response:
    """导出日历文件（RFC 5545）。"""
    user = _user_or_400(x_user_id)
    record = _owned_trip(trip_id, user)
    plan = TripPlan(**record["plan"])
    text = build_calendar(plan, trip_id=trip_id, title=record.get("title", ""))
    name = filename_for(plan, trip_id)
    # 中文文件名不能直接塞进 header：HTTP 头按 latin-1 编码，塞进去会 500。
    # 给一个 ASCII 兜底名 + RFC 5987 的 filename*，现代浏览器优先用后者。
    from urllib.parse import quote

    ascii_name = name.encode("ascii", "ignore").decode().lstrip("-") or "trip.ics"
    disposition = (
        f'attachment; filename="{ascii_name}"; '
        f"filename*=UTF-8''{quote(name)}"
    )
    return Response(
        content=text,
        media_type="text/calendar; charset=utf-8",
        headers={"Content-Disposition": disposition},
    )


@app.get("/api/trips/{trip_id}/packing")
async def trip_packing(
    trip_id: str, x_user_id: str | None = Header(default=None)
) -> dict[str, Any]:
    """已保存行程的装备清单（规划时的响应里已经带过一次，这里便于重新拉）。"""
    user = _user_or_400(x_user_id)
    record = _owned_trip(trip_id, user)
    return build_packing(TripPlan(**record["plan"]))


@app.post("/api/trips/{trip_id}/share")
async def create_share(
    trip_id: str, x_user_id: str | None = Header(default=None)
) -> dict[str, Any]:
    """生成只读链接。同一份行程可以有多个链接，方便分别发给不同的人。"""
    user = _user_or_400(x_user_id)
    record = _owned_trip(trip_id, user)
    share = _shares.create(user=user, trip_id=trip_id, title=record.get("title", ""))
    return {
        "token": share["token"],
        "path": f"/?share={share['token']}",
        "read_only": True,
        "created_at": share["created_at"],
    }


@app.delete("/api/trips/{trip_id}/share")
async def revoke_share(
    trip_id: str, x_user_id: str | None = Header(default=None)
) -> dict[str, Any]:
    user = _user_or_400(x_user_id)
    _owned_trip(trip_id, user)  # 必须先确认是自己的，否则 404
    return {"revoked": _shares.revoke_trip(user=user, trip_id=trip_id)}


@app.get("/api/share/{token}")
async def get_share(token: str) -> dict[str, Any]:
    """只读查看。**故意不读 X-User-Id**：拿到链接的人就能看，这正是分享的含义。"""
    record = _shares.resolve(token)
    if record is None:
        raise HTTPException(status_code=404, detail="链接无效或已撤销")

    trip_id = record.get("trip_id", "")
    try:
        trip = _store().get(trip_id, user=record.get("user", ""))
    except (TripNotFound, InvalidTripId) as exc:
        # 行程被删了，链接自然失效——比"显示一个空壳"诚实
        raise HTTPException(status_code=404, detail="行程已被删除，链接失效") from exc

    plan = TripPlan(**trip["plan"])
    return {
        "read_only": True,
        "title": trip.get("title", ""),
        "plan": plan.model_dump(),
        "markdown": to_markdown(plan),
        "packing": build_packing(plan),
        # 故意不返回 user：分享链接不该暴露是谁的行程
    }


@app.delete("/api/trips/{trip_id}")
async def delete_trip(
    trip_id: str, x_user_id: str | None = Header(default=None)
) -> dict[str, Any]:
    user = _user_or_400(x_user_id)
    try:
        ok = _store().delete(trip_id, user=user)
    except InvalidTripId as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not ok:
        raise HTTPException(status_code=404, detail=f"没有这份行程: {trip_id}")
    return {"ok": True}


@app.delete("/api/profile/hints")
async def forget_hint(
    field: str,
    value: str,
    x_user_id: str | None = Header(default=None),
) -> dict[str, Any]:
    """删掉画像里的某一项。前端 chip 上点 × 调它。"""
    user = _user_or_400(x_user_id)
    ok = _profiles.forget_hint(user, field, value)
    if not ok:
        raise HTTPException(status_code=404, detail=f"画像里没有这一项：{field}={value}")
    return {"ok": True, "profile": {"user": user, **_profiles.summary(user)}}


@app.delete("/api/profile")
async def forget_all(x_user_id: str | None = Header(default=None)) -> dict[str, Any]:
    """整份画像清空（"重新开始"）。"""
    user = _user_or_400(x_user_id)
    removed = _profiles.forget_all(user)
    return {"ok": True, "removed": removed, "profile": {"user": user, **_profiles.summary(user)}}


@app.get("/api/cities")
async def list_cities(q: str = "", limit: int = 8) -> dict[str, Any]:
    """城市检索。前端一次拉全量做本地过滤也行，这里仍支持 q/limit 便于直接调。"""
    from app.cities import search

    return {"items": search(q, limit=limit)}


@app.get("/api/profile")
async def get_profile(x_user_id: str | None = Header(default=None)) -> dict[str, Any]:
    """该用户的偏好画像：前端用来显示「它记住了什么」。"""
    user = _user_or_400(x_user_id)
    return {"user": user, **_profiles.summary(user)}


def main() -> None:
    import uvicorn

    port = int(os.environ.get("TRIP_API_PORT", "8000"))
    # 前端 dev server 在 5173，与后端不同源，所以必须有 CORS
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="info")


# ---------------------------------------------------------------- 静态页面 --
# 零构建版前端：由后端直接托管，打开 http://127.0.0.1:8000/ 就能用。
# 放在所有 /api 路由之后挂载，避免抢走接口路径。
_WEB_DIR = Path(__file__).resolve().parents[1] / "web"
if _WEB_DIR.is_dir():
    from fastapi.staticfiles import StaticFiles

    app.mount("/", StaticFiles(directory=str(_WEB_DIR), html=True), name="web")


if __name__ == "__main__":
    main()
