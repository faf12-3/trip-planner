"""时序图：谁调用谁、什么顺序、各花多久。

与 DFD 的分工：
  DFD 回答"数据在哪几处被加工、存到哪"；
  时序图回答"一次请求在时间轴上都发生了什么"——所以它必须有生命线、有先后、有耗时。

耗时用真实运行记录：北京 3 天 43.4 s、南京 2 天 31.9 s、杭州 1 天 26.7 s。
"""

from __future__ import annotations

from diagram_kit import C, Kit

W, H = 1620, 1030
k = Kit(W, H, "智能旅行助手 · 一次生成的时序图",
        "五条生命线 · 消息自上而下按时间排列 · 括号内为实测耗时（3 天北京行程 43.4 秒）")

NAMES = ["浏览器", "FastAPI", "高德 MCP", "LLM", "本地存储"]
SUB = ["Vue / 零构建", "uvicorn :8000", "HTTP 端点 15 工具", "gemini-3-flash", "data / .cache"]
PAIR = [C["front"], C["api"], C["ext"], ("#FDE68A", "#B45309"), C["store"]]
CENTERS = [145, 475, 805, 1135, 1465]
TOP, BOTTOM = 108, 892


def line_color(c):
    """配色表里存的是 (fill, outline)；画线要用 outline。"""
    return c[1] if isinstance(c, tuple) else c


for name, sub, pair, cx in zip(NAMES, SUB, PAIR, CENTERS):
    k.sharp(cx - 105, TOP, 210, 52, pair, width=2)
    k.center(cx, TOP + 9, name, k.f_body, pair[1])
    k.center(cx, TOP + 30, sub, k.f_tiny, C["muted"])
    k.line((cx, TOP + 52), (cx, BOTTOM), "#CBD5E1", 2, dash=True)

k.text(40, 164, "时间", k.f_tag, C["muted"])


def msg(y, src, dst, label, color=None, width=2, dash=False, t="", tag="", tag_x=None):
    x1, x2 = CENTERS[src], CENTERS[dst]
    k.flow([(x1, y), (x2, y)], label, color=line_color(color or C["faint"]), dash=dash, width=width)
    if t:
        k.right(34, y - 6, t, k.f_tiny, C["muted"])
    if tag:
        k.text(tag_x if tag_x is not None else min(x1, x2) + 20, y + 5, tag, k.f_tag, "#B45309")


def selfmsg(y, idx, label, color=None):
    """自身处理：小回环，不用箭头假装跨进程。"""
    x = CENTERS[idx]
    k.arrow([(x, y), (x + 44, y), (x + 44, y + 20), (x + 2, y + 20)], line_color(color or C["faint"]), 2)
    k.text(x + 54, y - 2, label, k.f_tiny, "#334155")


# ---------------------------------------------------------------- 消息 --
msg(190, 0, 1, "POST /api/trip/plan（城市 · 日期 · 天数 · 偏好 · 预算 · 交通 · 住宿）", C["api"], t="0 s")
selfmsg(232, 1, "校验 TripRequest + Key 预检：无效 Key 0.9 秒即失败，而不是卡 10 分钟", C["api"])

msg(274, 1, 2, "maps_text_search(keywords=偏好景点, city)", C["ext"], t="≈0.3 s")
msg(316, 2, 1, "POI JSON（实测 3374 字符）", C["ext"], dash=True)
msg(358, 1, 2, "maps_weather(city)", C["ext"], t="≈1.5 s")
msg(400, 2, 1, "forecasts（893 字符，含未来 4 天）", C["ext"], dash=True)
msg(442, 1, 2, "maps_text_search(keywords=住宿类型, city)", C["ext"], t="≈2.6 s")
msg(484, 2, 1, "POI JSON（3530 字符）", C["ext"], dash=True)

msg(526, 1, 3, "提示词：三份原始数据 + 全部规则（全程只调一次模型）", ("#B45309", "#B45309"), t="≈2.9 s")
msg(
    568, 3, 1, "行程 JSON + 四项预算（耗时主体，占总时长 70% 以上）",
    ("#B45309", "#B45309"), width=4, dash=True, tag="≈20~70 s", tag_x=900,
)

selfmsg(610, 1, "规范化：day_index 重编号 · 预算求和 · 天气按 forecasts 重建（<10 ms）", C["post"])

msg(652, 1, 4, "读配图缓存（命中即跳过，不再打 Unsplash）", C["store"], t="≈4.1 s")
msg(694, 4, 1, "缓存的图片 URL", C["store"], dash=True)

msg(736, 1, 0, "PlanResponse：plan / markdown / errors / trace", C["out"], width=3, t="≈4.2 s")
msg(778, 0, 1, "POST /api/trips / PUT /api/trips/{id}（1.2 秒防抖）", C["api"])
msg(820, 1, 4, "写 data/trips/<id>.json（临时文件 + 原子替换）", C["store"], t="≈5 s")

# ------------------------------------------------------------ 底部说明 --
band = BOTTOM + 18
k.box(40, band, 470, 78, C["out"], r=8, width=2)
k.text(52, band + 8, "一次生成总耗时（实测）", k.f_tag, C["out"][1])
k.text(52, band + 28, "1 天 26.7 s　·　2 天 31.9 s　·　3 天 43.4 s", k.f_small, "#0F172A")
k.text(52, band + 50, "其中 LLM 占七成以上；三次工具调用合计约 2.6 s", k.f_tiny, C["muted"])

k.box(530, band - 4, 1050, 88, C["err"], r=8, width=2, dash=True)
k.text(544, band + 4, "失败分支（每条都有确定处置，不是「看情况」）", k.f_tag, C["err"][1])
fails = [
    ("工具返回空壳", "缺 forecasts/pois → 校验 → 重试 1 次 → 记 errors，行程继续"),
    ("模型断连 / 超时", "重试 1 次 → 仍失败返回 503（可重试），不是 500"),
    ("补图失败 / 限流", "image_url 留空，只提示，不影响行程"),
]
for i, (a, b) in enumerate(fails):
    yy = band + 28 + i * 19
    k.text(544, yy, f"· {a}：", k.f_tiny, "#7F1D1D")
    k.text(662, yy, b, k.f_tiny, "#7F1D1D")

k.footer("实线 = 请求；虚线 = 返回/回包；左侧为累计耗时；右下三条为失败路径及处置")

out = k.save(r"D:\PRACTICE\docs\智能旅行助手-时序图.png")
print(f"已生成: {out}  {W}x{H}")
