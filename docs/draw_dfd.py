"""数据流图（DFD Level-1）：数据从哪来、经过什么处理、存到哪。

与"流程图"的分工：
  流程图回答"主链路长什么样"；
  DFD 回答"数据在哪几处被加工、被存下来"——所以它必须有处理、外部实体、数据存储、
  以及**带名字的数据流**，缺一样就不是 DFD。

画法约定（Gane-Sarson 派）：
  直角矩形 = 外部实体    圆角矩形 = 处理（带编号）
  左侧带编号栏的矩形 = 数据存储    箭头 + 标签 = 数据流
"""

from __future__ import annotations

from diagram_kit import C, Kit

W, H = 1560, 980
k = Kit(W, H, "智能旅行助手 · 数据流图（Level-1）",
        "处理（圆角）· 外部实体（直角）· 数据存储（左侧编号栏）")

SPINE_X, SPINE_W = 520, 440
SPINE_C = SPINE_X + SPINE_W / 2
ENT_X, ENT_W = 1210, 280
STORE_X, STORE_W = 60, 300


def proc(y, h, no, title, lines):
    k.box(SPINE_X, y, SPINE_W, h, C["graph"], r=10)
    k.d.ellipse([(SPINE_X + 12) * 2, (y + 12) * 2, (SPINE_X + 40) * 2, (y + 40) * 2], fill="#6D28D9")
    k.center(SPINE_X + 26, y + 17, no, k.f_tag, "#FFFFFF")
    k.text(SPINE_X + 50, y + 12, title, k.f_body)
    for i, s in enumerate(lines):
        k.text(SPINE_X + 50, y + 36 + i * 17, s, k.f_small, C["muted"])


def ext(y, h, title, lines):
    k.sharp(ENT_X, y, ENT_W, h, C["ext"], width=2)
    k.text(ENT_X + 14, y + 10, title, k.f_body, "#065F46")
    for i, s in enumerate(lines):
        k.text(ENT_X + 14, y + 34 + i * 16, s, k.f_small, C["muted"])


# ------------------------------------------------------------ 外部实体 --
k.sharp(STORE_X, 112, STORE_W, 92, C["actor"], width=2)
k.text(STORE_X + 14, 122, "用户（浏览器）", k.f_body, "#1E293B")
k.text(STORE_X + 14, 148, "填表 → 看结果 → 改行程", k.f_small, C["muted"])
k.text(STORE_X + 14, 168, "→ 导出 PDF / Markdown", k.f_small, C["muted"])

ext(250, 112, "高德地图 MCP", ["maps_text_search ×2", "maps_weather", "（HTTP 端点 15 个工具）"])
ext(414, 96, "LLM", ["gemini-3-flash-preview", "唯一一次生成调用"])
ext(556, 82, "Unsplash", ["Access Key 公开搜索", "50 次/小时（Demo）"])
ext(836, 88, "高德静态地图 API", ["/v3/staticmap", "按次计费"])

# ------------------------------------------------------------ 处理 --
proc(112, 92, "1.0", "接收并校验需求", ["TripRequest：城市/日期/天数/偏好/预算/交通/住宿"])
proc(246, 124, "2.0", "三路取数（工具节点）", [
    "· 景点：maps_text_search(keywords=偏好景点)",
    "· 天气：maps_weather(city)",
    "· 酒店：maps_text_search(keywords=住宿类型)",
    "载荷校验：缺 forecasts/pois → 重试 1 次 → 记 errors",
])
proc(410, 104, "3.0", "生成行程与预算", [
    "把三份原始数据 + 规则一起交给模型",
    "输出：日程 JSON（景点/餐饮/酒店）+ 四项预算",
])
proc(552, 112, "4.0", "规范化与补图", [
    "day_index 重编号 · 预算四项求和 · 天气按原文重建",
    "景点名 → Unsplash 取图（缓存命中则跳过）",
])
proc(700, 96, "5.0", "返回并渲染", [
    "PlanResponse：plan / markdown / errors / trace",
    "前端：时间线 + 每天静态地图 + 预算，可编辑",
])
proc(836, 80, "6.0", "持久化落盘", [
    "自动保存（1.2 秒防抖）→ data/trips/<id>.json，原子替换",
])

# ------------------------------------------------------------ 数据存储 --
k.store(STORE_X, 580, STORE_W, 68, "D2", "配图缓存", ".cache/unsplash.json")
k.store(STORE_X, 842, STORE_W, 68, "D1", "行程存储", "data/trips/*.json")

# ------------------------------------------------------------ 数据流 --
# 用户 → 1.0
k.flow([(STORE_X + STORE_W, 158), (SPINE_X, 158)], "需求（7 个字段）")

# 1.0 → 2.0
k.flow([(SPINE_C, 204), (SPINE_C, 246)], "TripRequest")

# 高德 MCP → 2.0（三条独立数据流）
for i, label in enumerate(["景点 POI", "天气 forecasts", "酒店 POI"]):
    yy = 276 + i * 32
    k.flow([(ENT_X, yy), (SPINE_X + SPINE_W, yy)], label)

# 2.0 → 3.0
k.flow([(SPINE_C, 370), (SPINE_C, 410)], "三份原始数据（原样）")

# 3.0 ↔ LLM
k.flow([(SPINE_X + SPINE_W, 462), (ENT_X, 462)], "提示词（含三份数据 + 规则）", both=True)

# 3.0 → 4.0
k.flow([(SPINE_C, 514), (SPINE_C, 552)], "行程草稿（模型 JSON）")

# 4.0 → Unsplash
k.flow([(SPINE_X + SPINE_W, 592), (ENT_X, 592)], "景点名 + 城市", both=True, side="below")

# 4.0 ↔ D2
k.flow([(SPINE_X, 614), (STORE_X + STORE_W, 614)], "图片 URL（按城市|景点名缓存）", side="below")

# 4.0 → 5.0
k.flow([(SPINE_C, 664), (SPINE_C, 700)], "规范化后的行程")

# 5.0 ↔ 高德静态图
k.flow([(SPINE_X + SPINE_W, 762), (ENT_X, 880)], "点位坐标 → PNG（每天一张）", dash=False)

# 5.0 → 6.0
k.flow([(SPINE_C, 796), (SPINE_C, 836)], "用户改动后触发")

# 6.0 ↔ D1
k.flow([(SPINE_X, 876), (STORE_X + STORE_W, 876)], "行程 JSON（原子写入）", both=True)

# 5.0 → 用户（结果回到起点）
k.flow(
    [(SPINE_X, 748), (410, 748), (410, 186), (STORE_X + STORE_W, 186)],
    "PlanResponse",
)

# ------------------------------------------------------------ 图例与注 --
k.legend(STORE_X, 236, [
    ("proc", "处理（编号 = 执行顺序）"),
    ("ext", "外部实体（本系统之外）"),
    ("store", "数据存储（落盘位置）"),
    ("flow", "数据流（线上标数据名）"),
], title="图例")

k.text(STORE_X, 396, "注：", k.f_tag, C["muted"])
k.text(STORE_X, 414, "静态地图另有一层内存缓存（上限 40 张，", k.f_tiny, C["muted"])
k.text(STORE_X, 430, "同点位命中直接返回），未画进图里以免遮挡；", k.f_tiny, C["muted"])
k.text(STORE_X, 446, "Unsplash 补图失败只留空 image_url，不影响行程。", k.f_tiny, C["muted"])

k.footer(
    "项目：D:\\PRACTICE\\trip_planner　│　"
    "命名约定：处理用「编号 + 动词」，数据流用名词，存储用 D+编号"
)

out = k.save(r"D:\PRACTICE\docs\智能旅行助手-数据流图.png")
print(f"已生成: {out}  {W}x{H}")
