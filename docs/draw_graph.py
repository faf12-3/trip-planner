"""LangGraph 编排图：主图（五节点 + 条件分支）+ 重排子图（interrupt）+ 图例。

为什么单独一张图：老的"流程图"里那个四节点横排已经过期了（少了 adjust 条件分支），
而且它混在端到端流程里，节点间的状态读写、条件边的判定点都看不清。
这张图只讲"图"，讲三件事：
  1. 谁连谁（含条件边与两条入边汇到 hotel）
  2. 每个节点读什么、写什么（共享状态 = 黑板）
  3. 第二个图：interrupt 停在哪里、谁给的是 resume

坐标全部用逻辑坐标，Kit 内部按 S=2 超采样再缩回，边缘才干净。
"""

from __future__ import annotations

from diagram_kit import C, Kit

W, H = 1280, 900

TOOL = ("#FFFFFF", "#6D28D9")       # 工具节点：不碰模型
LLM = ("#FDE68A", "#B45309")        # 唯一的模型调用点
IO = ("#ECFDF5", "#047857")         # START / END
HUMAN = ("#E0F2FE", "#0369A1")      # 人
PAUSE = ("#FFF7ED", "#C2410C")      # interrupt 停点

k = Kit(
    W,
    H,
    "智能旅行助手 · LangGraph 编排",
    "主图 = 五节点 + 一条条件边（路由）；重排子图 = interrupt 停住等确认。全流程只有 1 次 LLM 调用。",
)


def node(x, y, w, h, pair, title, lines, f_title=None, f_line=None) -> None:
    """统一节点样式：标题一行 + 若干说明行，居中。"""
    k.box(x, y, w, h, pair, r=9, width=2)
    ft = f_title or k.f_body
    fl = f_line or k.f_small
    k.center(x + w / 2, y + 10, title, ft)
    for i, line in enumerate(lines):
        k.center(x + w / 2, y + 36 + i * 18, line, fl, C["muted"] if i else "#334155")


def tag(x, y, text, color=None) -> None:
    """小标签：白底 + 彩边，用来标"这一步是什么性质"。"""
    f = k.f_tiny
    tw = k.d.textlength(text, font=f) / 2
    k.d.rounded_rectangle(
        [x * 2, y * 2, (x + tw + 16) * 2, (y + 16) * 2], radius=5 * 2,
        fill="#FFFFFF", outline=color or C["rule"], width=2,
    )
    k.text(x + 8, y + 2, text, f, color or C["muted"])


# ============================================================ 主图（上）
Y = 210
H1 = 84
k.text(40, 132, "① 主图：一次规划的控制流", k.f_tag, C["graph"][1])
k.text(40, 152, "节点之间没有「调用关系」，只有共享状态；箭头是图的边，由代码决定，不由模型决定。",
       k.f_tiny, C["muted"])

# 节点坐标
xs = {"start": (40, 66), "attraction": (126, 156), "weather": (322, 156),
      "hotel": (618, 150), "planner": (808, 176), "end": (1024, 66)}

x, w = xs["start"]
node(x, Y + 18, w, 48, IO, "START", [], f_line=k.f_tiny)
x, w = xs["attraction"]
node(x, Y, w, H1, TOOL, "attraction", ["maps_text_search", "写：attractions_raw"])
x, w = xs["weather"]
node(x, Y, w, H1, TOOL, "weather", ["maps_weather", "写：weather_raw"])
x, w = xs["hotel"]
node(x, Y, w, H1, TOOL, "hotel", ["maps_text_search", "写：hotels_raw"])
x, w = xs["planner"]
node(x, Y, w, H1, LLM, "planner", ["LLM ×1 · 读全部 raw", "写：plan"])
x, w = xs["end"]
node(x, Y + 18, w, 48, IO, "END", [], f_line=k.f_tiny)

# 主链路箭头
k.arrow([(40 + 66, Y + 42), (126, Y + 42)], C["line"], 2)
k.arrow([(126 + 156, Y + 42), (322, Y + 42)], C["line"], 2)
k.arrow([(618 + 150, Y + 42), (808, Y + 42)], C["line"], 2)
k.arrow([(808 + 176, Y + 42), (1024, Y + 42)], C["line"], 2)

# 条件分支：weather → adjust（有雨） / weather → hotel（晴朗）
AY = Y + H1 + 64          # adjust 的 y
AX, AW, AH = 470, 150, 78
CX = AX + AW / 2          # 545

# weather → adjust：下 → 右 → 下
k.arrow([(322 + 78, Y + H1), (322 + 78, Y + H1 + 26),
         (CX, Y + H1 + 26), (CX, AY)], C["line"], 2, dash=True)
k.text(400, Y + H1 + 8, "有雨", k.f_tiny, "#B45309")

# weather → hotel：横穿
k.arrow([(322 + 156, Y + 42), (618, Y + 42)], C["line"], 2)
k.text(500, Y + 26, "晴朗（无分支）", k.f_tiny, C["muted"])

node(AX, AY, AW, AH, TOOL, "adjust", ["maps_text_search(室内)", "写：routing（提示词约束）"])

# adjust → hotel：右 → 上
k.arrow([(AX + AW, AY + AH / 2), (618 + 75, AY + AH / 2), (618 + 75, Y + H1)],
        C["line"], 2)
k.text(AX + AW + 8, AY + AH / 2 - 18, "补查完成", k.f_tiny, C["muted"])

# 判定点标注
# 分支点用实心点表示；说明文字放左侧空白，放右边会压到 hotel 框
k.d.ellipse([(322 + 156 - 5) * 2, (Y + 42 - 5) * 2, (322 + 156 + 5) * 2, (Y + 42 + 5) * 2],
            fill=C["graph"][1])
k.text(40, Y + H1 + 14, "分支判定在 _after_weather()：纯函数，非模型", k.f_tiny, C["graph"][1])

# 黑板说明
k.text(40, AY + AH + 22,
       "共享状态（黑板）：每个节点只写自己的字段，planner 是唯一汇聚点；"
       "errors / trace 用 operator.add 追加，其余字段后写覆盖。",
       k.f_small, C["muted"])

# ========================================================== 重排子图（下）
Y2 = 560
k.text(40, Y2 - 40, "② 重排子图：只改指定的几天（interrupt + checkpointer）", k.f_tag, C["out"][1])

r2 = {"start": (40, 66), "draft": (126, 200), "confirm": (372, 196),
      "apply": (614, 210), "end": (880, 66)}
x, w = r2["start"]
node(x, Y2, w, 48, IO, "START", [], f_line=k.f_tiny)
x, w = r2["draft"]
node(x, Y2 - 16, w, 96, LLM, "draft", ["调模型 → 守门校验", "越界带反馈重试 ≤2 次", "写：draft / attempts"])
x, w = r2["confirm"]
node(x, Y2 - 16, w, 96, PAUSE, "confirm", ["interrupt() 停住", "等人 approve / reject", "写：decision"])
x, w = r2["apply"]
node(x, Y2 - 16, w, 96, TOOL, "apply", ["未选中的天直接取旧对象", "预算按明细重算", "写：final_plan"])
x, w = r2["end"]
node(x, Y2 + 8, w, 48, IO, "END", [], f_line=k.f_tiny)

k.arrow([(40 + 66, Y2 + 24), (126, Y2 + 24)], C["line"], 2)
k.arrow([(126 + 200, Y2 + 24), (372, Y2 + 24)], C["line"], 2)
k.arrow([(372 + 196, Y2 + 24), (614, Y2 + 24)], C["line"], 2)
k.arrow([(614 + 210, Y2 + 24), (880, Y2 + 24)], C["line"], 2)

# 人：在 confirm 下方，双向虚线
HX, HY, HW, HH = 372, Y2 + 124, 196, 80
node(HX, HY, HW, HH, HUMAN, "人（用户）", ["点确认 / 取消", "Command(resume=…)"], f_line=k.f_tiny)
k.arrow([(HX + HW / 2, HY), (HX + HW / 2, Y2 - 16 + 96)], C["faint"], 2, dash=True, both=True)
k.text(590, HY + 22, "唯一能「否决」模型的地方", k.f_tiny, HUMAN[1])

# 存点标注
tag(40, Y2 + 96, "checkpointer: InMemorySaver", C["store"][1])
tag(40, Y2 + 122, "thread_id = user:trip:随机", C["store"][1])
k.text(40, Y2 + 150, "进程重启即丢，须单 worker 常驻", k.f_tiny, C["muted"])

# 守门说明
k.text(614, Y2 + 98,
       "守门：结构锁死 + 未选中天逐字比对 + 每天 1~6 个景点", k.f_tiny, "#5B21B6")
k.text(614, Y2 + 116, "违反 → 422，原计划完全不动", k.f_tiny, C["muted"])

# ============================================================== 图例（底）
LY = H - 108
k.line((40, LY - 14), (W - 40, LY - 14), C["rule"], 2)
k.text(40, LY, "图例", k.f_tag, C["muted"])

legend = [
    (TOOL, "工具节点（不碰模型，确定性）"),
    (LLM, "LLM 调用点"),
    (PAUSE, "interrupt 停点"),
    (HUMAN, "人（approve / reject）"),
    (IO, "控制流端点"),
]
lx = 100
for pair, label in legend:
    k.box(lx, LY - 2, 26, 16, pair, r=4, width=2)
    k.text(lx + 34, LY, label, k.f_tiny, "#334155")
    lx += 34 + int(k.d.textlength(label, font=k.f_tiny) / 2) + 30

k.arrow([(lx, LY + 6), (lx + 34, LY + 6)], C["line"], 2)
k.text(lx + 42, LY, "状态/控制流", k.f_tiny, "#334155")
lx += 42 + int(k.d.textlength("状态/控制流", font=k.f_tiny) / 2) + 30
k.arrow([(lx, LY + 6), (lx + 34, LY + 6)], C["faint"], 2, dash=True)
k.text(lx + 42, LY, "条件边 / 双向确认", k.f_tiny, "#334155")

k.footer(
    "源码：app/graph.py（装配）· app/nodes.py（节点）· app/state.py（黑板）· "
    "app/replan.py（重排子图）· app/routing.py（判定纯函数）"
)

out = k.save(r"D:\PRACTICE\docs\智能旅行助手-LangGraph图.png")
print(f"已生成: {out}  {out.stat().st_size // 1024} KB")
