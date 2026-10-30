"""生成「智能旅行助手」系统流程图（PNG）。

为什么手画而不是用 mermaid/graphviz：
- 这张图要放进汇报材料，需要中文排版可控（字体、换行、对齐）；
- 本机没有 graphviz，mermaid 需要浏览器渲染；
- 用 PIL 直接画，改一个坐标就能调，不依赖任何外部工具。

布局：一条竖向主链路（用户 → 前端 → API → LangGraph → 后处理 → 返回 → 展示 → 存储/导出），
右侧挂外部服务，底部挂失败分支。
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

S = 2  # 超采样倍数：先按逻辑坐标画，最后缩到 1x，边缘更干净
W, H = 1180, 1300  # 逻辑画布

FONT_DIR = Path(r"C:\Windows\Fonts")
REG = FONT_DIR / "msyh.ttc"      # 微软雅黑
BOLD = FONT_DIR / "msyhbd.ttc"

# 配色：按"层"区分，而不是按好看
C = {
    "bg": "#FFFFFF",
    "ink": "#0F172A",
    "muted": "#64748B",
    "line": "#94A3B8",
    "input": ("#EEF2FF", "#4F46E5"),
    "front": ("#E0F2FE", "#0369A1"),
    "api": ("#E0E7FF", "#4338CA"),
    "graph": ("#EDE9FE", "#6D28D9"),
    "post": ("#F5F3FF", "#7C3AED"),
    "out": ("#ECFDF5", "#047857"),
    "store": ("#FEF3C7", "#B45309"),
    "ext": ("#DCFCE7", "#15803D"),
    "err": ("#FEE2E2", "#B91C1C"),
}


def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(BOLD if bold else REG), size * S)


F_TITLE = font(30, True)
F_SUB = font(15)
F_STEP = font(14, True)
F_BODY = font(16)
F_SMALL = font(13)
F_TAG = font(12, True)


class Canvas:
    def __init__(self) -> None:
        self.img = Image.new("RGB", (W * S, H * S), C["bg"])
        self.d = ImageDraw.Draw(self.img)

    # ---------------------------------------------------------------- 基础 --
    def rbox(
        self,
        x: float,
        y: float,
        w: float,
        h: float,
        fill: str,
        outline: str,
        r: float = 10,
        width: int = 2,
        dash: bool = False,
    ) -> None:
        box = [x * S, y * S, (x + w) * S, (y + h) * S]
        if dash:
            self.dashed_round_rect(box, fill, outline, r * S, width)
        else:
            self.d.rounded_rectangle(box, radius=r * S, fill=fill, outline=outline, width=width)

    def dashed_round_rect(self, box, fill, outline, r, width) -> None:
        # PIL 没有虚线圆角矩形；用"实线填充 + 短划线段模拟虚线边框"
        self.d.rounded_rectangle(box, radius=r, fill=fill)
        x0, y0, x1, y1 = box
        step = 10
        for x in range(int(x0) + int(r), int(x1) - int(r), step * 2):
            self.d.line([(x, y0), (min(x + step, x1 - r), y0)], fill=outline, width=width)
            self.d.line([(x, y1), (min(x + step, x1 - r), y1)], fill=outline, width=width)
        for y in range(int(y0) + int(r), int(y1) - int(r), step * 2):
            self.d.line([(x0, y), (x0, min(y + step, y1 - r))], fill=outline, width=width)
            self.d.line([(x1, y), (x1, min(y + step, y1 - r))], fill=outline, width=width)

    def text(self, x: float, y: float, s: str, f, color: str = C["ink"], anchor: str = "la") -> None:
        self.d.text((x * S, y * S), s, font=f, fill=color, anchor=anchor)

    def center(self, cx: float, y: float, s: str, f, color: str = C["ink"]) -> None:
        self.d.text((cx * S, y * S), s, font=f, fill=color, anchor="ma")

    # ---------------------------------------------------------------- 连线 --
    def arrow(self, pts: list[tuple[float, float]], color: str = C["line"], width: int = 2,
              dash: bool = False, head: bool = True) -> None:
        scaled = [(x * S, y * S) for x, y in pts]
        for i in range(len(scaled) - 1):
            a, b = scaled[i], scaled[i + 1]
            if dash:
                self._dash_line(a, b, color, width)
            else:
                self.d.line([a, b], fill=color, width=width)
        if head:
            self._head(scaled[-2], scaled[-1], color, width)

    def _dash_line(self, a, b, color, width, dash=9, gap=7) -> None:
        (x0, y0), (x1, y1) = a, b
        dist = ((x1 - x0) ** 2 + (y1 - y0) ** 2) ** 0.5
        if dist == 0:
            return
        dx, dy = (x1 - x0) / dist, (y1 - y0) / dist
        t = 0.0
        while t < dist:
            e = min(t + dash, dist)
            self.d.line(
                [(x0 + dx * t, y0 + dy * t), (x0 + dx * e, y0 + dy * e)], fill=color, width=width
            )
            t = e + gap

    def _head(self, a, b, color, width) -> None:
        (x0, y0), (x1, y1) = a, b
        dist = ((x1 - x0) ** 2 + (y1 - y0) ** 2) ** 0.5
        if dist == 0:
            return
        dx, dy = (x1 - x0) / dist, (y1 - y0) / dist
        size = 9 * S
        px, py = -dy, dx
        tip = (x1, y1)
        left = (x1 - dx * size + px * size * 0.5, y1 - dy * size + py * size * 0.5)
        right = (x1 - dx * size - px * size * 0.5, y1 - dy * size - py * size * 0.5)
        self.d.polygon([tip, left, right], fill=color)


# ------------------------------------------------------------------ 画布 --
c = Canvas()

# 标题
c.text(40, 34, "智能旅行助手 · 端到端流程", F_TITLE)
c.text(40, 78, "LangGraph 五节点（含条件分支）+ 高德地图 MCP + 单次 LLM 规划", F_SUB, C["muted"])
c.d.line([(40 * S, 108 * S), ((W - 40) * S, 108 * S)], fill="#E2E8F0", width=2)

SPINE_X, SPINE_W = 150, 620  # 主链路列
EXT_X, EXT_W = 810, 330      # 右侧外部服务列
CENTER = SPINE_X + SPINE_W / 2


def step_badge(x: float, y: float, n: str) -> None:
    c.d.ellipse(
        [(x - 13) * S, (y - 13) * S, (x + 13) * S, (y + 13) * S], fill="#1E293B"
    )
    c.center(x, y - 9, n, F_STEP, "#FFFFFF")


# 1 输入
y = 130
c.rbox(SPINE_X, y, SPINE_W, 78, *C["input"])
step_badge(SPINE_X + 26, y + 26, "1")
c.text(SPINE_X + 50, y + 12, "用户填写需求", F_BODY, C["ink"])
c.text(SPINE_X + 50, y + 38, "城市 · 日期 · 天数 · 偏好 · 预算档位 · 交通方式 · 住宿类型", F_SMALL, C["muted"])
c.text(SPINE_X + 50, y + 57, "→ 组装成 TripRequest（Pydantic 校验）", F_SMALL, C["muted"])

c.arrow([(CENTER, y + 78), (CENTER, y + 104)])
y1 = y + 104

# 2 前端
c.rbox(SPINE_X, y1, SPINE_W, 74, *C["front"])
step_badge(SPINE_X + 26, y1 + 26, "2")
c.text(SPINE_X + 50, y1 + 12, "前端页面（两条实现，共用同一份逻辑与样式）", F_BODY)
c.text(SPINE_X + 50, y1 + 37, "Vue3 + Vite :5173（proxy /api）　│　零构建原生 JS  :8000（FastAPI 托管）", F_SMALL, C["muted"])
c.text(SPINE_X + 50, y1 + 56, "进度条 · 错误提示 · 预算实时重算", F_SMALL, C["muted"])

c.arrow([(CENTER, y1 + 74), (CENTER, y1 + 100)])
y2 = y1 + 100

# 3 API
c.rbox(SPINE_X, y2, SPINE_W, 66, *C["api"])
step_badge(SPINE_X + 26, y2 + 26, "3")
c.text(SPINE_X + 50, y2 + 12, "FastAPI  POST /api/trip/plan", F_BODY)
c.text(SPINE_X + 50, y2 + 37, "先做 Key 预检（失败 0.9 秒返回，而不是卡 10 分钟）", F_SMALL, C["muted"])

c.arrow([(CENTER, y2 + 66), (CENTER, y2 + 92)])
y3 = y2 + 92

# 4 LangGraph 四节点
GRAPH_H = 236
c.rbox(SPINE_X - 20, y3, SPINE_W + 40, GRAPH_H, *C["graph"], width=2)
step_badge(SPINE_X + 6, y3 + 24, "4")
c.text(SPINE_X + 30, y3 + 11, "LangGraph 图：五个节点 + 一条条件边（有雨才走 adjust），工具入参由代码固定", F_BODY)

node_w, node_h, gap = 138, 96, 16
nx0 = SPINE_X - 4
ny = y3 + 44
labels = [
    ("attraction", "maps_text_search", "关键词＝偏好景点"),
    ("weather", "maps_weather", "按城市查预报"),
    ("hotel", "maps_text_search", "关键词＝住宿类型"),
    ("planner", "LLM 一次调用", "出行程 JSON + 预算"),
]
fills = [("#FFFFFF", "#6D28D9")] * 3 + [("#FDE68A", "#B45309")]
for i, ((name, tool, note), (f, o)) in enumerate(zip(labels, fills)):
    x = nx0 + i * (node_w + gap)
    c.rbox(x, ny, node_w, node_h, f, o, r=8, width=2)
    c.center(x + node_w / 2, ny + 10, f"{i + 1}. {name}", F_BODY)
    c.center(x + node_w / 2, ny + 38, tool, F_SMALL, C["muted"])
    c.center(x + node_w / 2, ny + 58, note, F_SMALL, C["muted"])
    if i < 3:
        c.arrow([(x + node_w, ny + node_h / 2), (x + node_w + gap, ny + node_h / 2)], C["line"], 2)

c.text(SPINE_X + 6, ny + node_h + 12,
       "每个工具节点：校验载荷（缺 forecasts/pois 视为异常）→ 重试 1 次 → 仍失败记 errors，行程继续",
       F_SMALL, "#5B21B6")
c.text(SPINE_X + 6, ny + node_h + 32,
       "条件边：weather → adjust（仅坏天气）→ hotel；判定为纯函数，详见《LangGraph 图》",
       F_SMALL, C["muted"])

y4 = y3 + GRAPH_H

# 右侧外部服务
c.text(EXT_X, y3 - 4, "外部服务", F_STEP, C["muted"])
ext_items = [
    ("高德地图 MCP", "HTTP 端点 15 个工具 / stdio 12 个", "maps_text_search · maps_weather"),
    ("LLM", "gemini-3-flash-preview", "超时 180 秒；断连重试，失败返回 503"),
    ("Unsplash", "Access Key（Secret 不用）", "补景点配图，磁盘缓存，50 次/小时"),
]
ey = y3 + 14
for name, l1, l2 in ext_items:
    c.rbox(EXT_X, ey, EXT_W, 58, *C["ext"], r=8)
    c.text(EXT_X + 12, ey + 5, name, F_SMALL, "#065F46")
    c.text(EXT_X + 12, ey + 22, l1, F_TAG, C["muted"])
    c.text(EXT_X + 12, ey + 38, l2, F_TAG, C["muted"])
    c.arrow([(EXT_X, ey + 29), (792, ey + 29)], C["ext"][1], 2)
    ey += 68

c.arrow([(CENTER, y4), (CENTER, y4 + 26)])
y5 = y4 + 26

# 5 后处理
c.rbox(SPINE_X, y5, SPINE_W, 92, *C["post"])
step_badge(SPINE_X + 26, y5 + 26, "5")
c.text(SPINE_X + 50, y5 + 10, "确定性后处理（凡是「有唯一正确答案」的都不交给模型）", F_BODY)
c.text(SPINE_X + 50, y5 + 36, "· day_index 按列表顺序重编号，不采信模型给的序号", F_SMALL, C["muted"])
c.text(SPINE_X + 50, y5 + 54, "· 预算四项求和重算 total，避免分项与合计对不上", F_SMALL, C["muted"])
c.text(SPINE_X + 50, y5 + 72, "· 天气由 forecasts 原文重建，模型没抄也不影响", F_SMALL, C["muted"])

c.arrow([(CENTER, y5 + 92), (CENTER, y5 + 118)])
y6 = y5 + 118

# 6 返回
c.rbox(SPINE_X, y6, SPINE_W, 58, *C["out"])
step_badge(SPINE_X + 26, y6 + 26, "6")
c.text(SPINE_X + 50, y6 + 10, "返回 PlanResponse", F_BODY)
c.text(SPINE_X + 50, y6 + 34, "plan（行程 JSON）· markdown · errors（工具失败但行程可用）· trace（每节点字符数）", F_SMALL, C["muted"])

c.arrow([(CENTER, y6 + 58), (CENTER, y6 + 84)])
y7 = y6 + 84

# 7 展示与编辑
c.rbox(SPINE_X, y7, SPINE_W, 92, *C["front"])
step_badge(SPINE_X + 26, y7 + 26, "7")
c.text(SPINE_X + 50, y7 + 10, "前端展示：时间线 + 地图 + 预算，且可编辑", F_BODY)
c.text(SPINE_X + 50, y7 + 36, "· 每日时间线：增删景点/餐饮、↑↓ 排序、改价，预算即时重算", F_SMALL, C["muted"])
c.text(SPINE_X + 50, y7 + 54, "· 撤销 / 重做（Ctrl+Z，粒度＝一次输入框焦点进出）", F_SMALL, C["muted"])
c.text(SPINE_X + 50, y7 + 72, "· 地图：交互地图（JS API 2.0）＋ 每天一张静态图（打印用）", F_SMALL, C["muted"])

c.arrow([(CENTER, y7 + 92), (CENTER, y7 + 118)])
y8 = y7 + 118

# 8 存储与导出
c.rbox(SPINE_X, y8, SPINE_W, 76, *C["store"])
step_badge(SPINE_X + 26, y8 + 26, "8")
c.text(SPINE_X + 50, y8 + 10, "持久化与导出", F_BODY)
c.text(SPINE_X + 50, y8 + 34, "自动保存 POST/PUT /api/trips → data/trips/<id>.json（原子写入，1.2 秒防抖）", F_SMALL, C["muted"])
c.text(SPINE_X + 50, y8 + 54, "导出：打印 / 存 PDF（每天带静态地图）· 复制 Markdown（按当前数据现算）", F_SMALL, C["muted"])

# 失败分支
ey2 = y8 + 92
c.text(SPINE_X, ey2, "失败分支（都做成「可解释的失败」，而不是静默兜底）", F_STEP, C["err"][1])
ey2 += 22
errs = [
    ("Key 无效 / 配额用尽", "预检直接报错，0.9 秒返回并给出可照做的提示"),
    ("工具返回空壳", "校验载荷 → 重试 1 次 → 记 errors，行程继续"),
    ("模型断连", "重试 1 次；仍失败返回 503（可重试）而不是 500"),
    ("地图取不到", "显示状态码，如「暂时取不到（422）」"),
]
for i, (t, d) in enumerate(errs):
    col = i % 2
    row = i // 2
    x = SPINE_X + col * (SPINE_W / 2 + 6)
    yy = ey2 + row * 52
    c.rbox(x, yy, SPINE_W / 2 - 6, 44, *C["err"], r=8, width=1, dash=True)
    c.text(x + 10, yy + 5, t, F_TAG, C["err"][1])
    c.text(x + 10, yy + 22, d, F_TAG, C["muted"])

# 页脚
c.text(40, H - 30, "项目：D:\\PRACTICE\\trip_planner　│　测试：210 项后端 + 39 项前端逻辑 + 32 步 DOM 场景 + 27 项样式检查", F_TAG, C["muted"])

out = Path(r"D:\PRACTICE\docs\智能旅行助手-流程图.png")
out.parent.mkdir(parents=True, exist_ok=True)
final = c.img.resize((W, H), Image.LANCZOS)
final.save(out, "PNG")
print(f"已生成: {out}  {final.size[0]}x{final.size[1]}")
