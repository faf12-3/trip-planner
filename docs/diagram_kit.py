"""流程图/DFD/时序图/状态图共用的绘图层。

为什么单独抽出来：四张图要放在同一份材料里，配色、字号、圆角、箭头粗细必须一致，
否则一眼就能看出是拼凑的。这里把「形状 + 文字 + 箭头 + 图例」定死，各图只负责布局。

坐标一律用逻辑坐标（如 1180 宽），内部乘 S 超采样，最后缩回 1x——边缘才干净。
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

S = 2  # 超采样倍数

# 配色：按"层/语义"分配，不按好看分配
C = {
    "bg": "#FFFFFF",
    "ink": "#0F172A",
    "muted": "#64748B",
    "faint": "#94A3B8",
    "line": "#94A3B8",
    "rule": "#E2E8F0",
    "input": ("#EEF2FF", "#4F46E5"),
    "front": ("#E0F2FE", "#0369A1"),
    "api": ("#E0E7FF", "#4338CA"),
    "graph": ("#EDE9FE", "#6D28D9"),
    "post": ("#F5F3FF", "#7C3AED"),
    "out": ("#ECFDF5", "#047857"),
    "store": ("#FEF3C7", "#B45309"),
    "ext": ("#DCFCE7", "#15803D"),
    "err": ("#FEE2E2", "#B91C1C"),
    "note": ("#F8FAFC", "#CBD5E1"),
    "actor": ("#E2E8F0", "#334155"),
}

FONT_DIR = Path(r"C:\Windows\Fonts")
_REG = FONT_DIR / "msyh.ttc"
_BOLD = FONT_DIR / "msyhbd.ttc"


def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(_BOLD if bold else _REG), size * S)


class Kit:
    """一张图的画布。所有子图共用它，保证视觉一致。"""

    def __init__(self, w: int, h: int, title: str = "", subtitle: str = "") -> None:
        self.w, self.h = w, h
        self.img = Image.new("RGB", (w * S, h * S), C["bg"])
        self.d = ImageDraw.Draw(self.img)

        self.f_title = font(28, True)
        self.f_sub = font(14)
        self.f_body = font(15)
        self.f_small = font(12)
        self.f_tiny = font(11)
        self.f_tag = font(11, True)

        if title:
            self.text(36, 30, title, self.f_title)
        if subtitle:
            self.text(36, 68, subtitle, self.f_sub, C["muted"])
        if title or subtitle:
            self.line((36, 96), (w - 36, 96), C["rule"], 2)

    # ---------------------------------------------------------------- 文字 --
    def text(self, x, y, s, f, color=C["ink"], anchor="la") -> None:
        self.d.text((x * S, y * S), s, font=f, fill=color, anchor=anchor)

    def center(self, cx, y, s, f, color=C["ink"]) -> None:
        self.d.text((cx * S, y * S), s, font=f, fill=color, anchor="ma")

    def right(self, x, y, s, f, color=C["ink"]) -> None:
        self.d.text((x * S, y * S), s, font=f, fill=color, anchor="ra")

    # ---------------------------------------------------------------- 形状 --
    def box(self, x, y, w, h, pair, r=10, width=2, dash=False) -> None:
        fill, outline = pair
        box = [x * S, y * S, (x + w) * S, (y + h) * S]
        if dash:
            self.d.rounded_rectangle(box, radius=r * S, fill=fill)
            self._dashed_border(box, outline, r * S, width)
        else:
            self.d.rounded_rectangle(box, radius=r * S, fill=fill, outline=outline, width=width)

    def sharp(self, x, y, w, h, pair, width=2, dash=False) -> None:
        """直角矩形：DFD 里的"外部实体"、时序图里的"参与者"，形状本身带语义。"""
        fill, outline = pair
        box = [x * S, y * S, (x + w) * S, (y + h) * S]
        self.d.rectangle(box, fill=fill)
        if dash:
            self._dashed_border(box, outline, 0, width)
        else:
            self.d.rectangle(box, outline=outline, width=width)

    def store(self, x, y, w, h, tag, label, sub="") -> None:
        """数据存储（Gane-Sarson）：矩形 + 左侧编号栏。"""
        fill, outline = C["store"]
        self.d.rectangle([x * S, y * S, (x + w) * S, (y + h) * S], fill=fill, outline=outline, width=2)
        self.d.line([(x + 46) * S, y * S, (x + 46) * S, (y + h) * S], fill=outline, width=2)
        self.center(x + 23, y + h / 2 - 9, tag, self.f_tag, outline)
        self.text(x + 58, y + (h / 2 - 24 if sub else h / 2 - 9), label, self.f_small)
        if sub:
            self.text(x + 58, y + h / 2 + 6, sub, self.f_tiny, C["muted"])

    # ---------------------------------------------------------------- 连线 --
    def line(self, a, b, color=C["line"], width=2, dash=False) -> None:
        sa, sb = (a[0] * S, a[1] * S), (b[0] * S, b[1] * S)
        if dash:
            self._dash(sa, sb, color, width)
        else:
            self.d.line([sa, sb], fill=color, width=width)

    def arrow(self, pts, color=C["line"], width=2, dash=False, head=True, both=False) -> None:
        s = [(x * S, y * S) for x, y in pts]
        for i in range(len(s) - 1):
            if dash:
                self._dash(s[i], s[i + 1], color, width)
            else:
                self.d.line([s[i], s[i + 1]], fill=color, width=width)
        if head:
            self._head(s[-2], s[-1], color)
        if both:
            self._head(s[1], s[0], color)

    def flow(self, pts, label="", color=None, side="above", dash=False, both=False, width=2) -> None:
        """带标签的数据流/消息。标签自带白底，避免和线交叉看不清。"""
        col = color or C["faint"]
        self.arrow(pts, col, width, dash=dash, both=both)
        if label:
            mid = self._mid(pts)
            f = self.f_tiny
            tw = self.d.textlength(label, font=f) / S
            x = mid[0] - tw / 2
            y = mid[1] - (16 if side == "above" else -4)
            pad = 3
            self.d.rectangle(
                [(x - pad) * S, (y - 1) * S, (x + tw + pad) * S, (y + 14) * S], fill=C["bg"]
            )
            self.text(x, y, label, f, "#334155")

    @staticmethod
    def _mid(pts):
        if len(pts) == 2:
            return ((pts[0][0] + pts[1][0]) / 2, (pts[0][1] + pts[1][1]) / 2)
        # 折线：取最长一段的中点
        best, seg = -1, (pts[0], pts[1])
        for i in range(len(pts) - 1):
            d = abs(pts[i + 1][0] - pts[i][0]) + abs(pts[i + 1][1] - pts[i][1])
            if d > best:
                best, seg = d, (pts[i], pts[i + 1])
        return ((seg[0][0] + seg[1][0]) / 2, (seg[0][1] + seg[1][1]) / 2)

    def _dash(self, a, b, color, width, dash=9, gap=7) -> None:
        (x0, y0), (x1, y1) = a, b
        dist = ((x1 - x0) ** 2 + (y1 - y0) ** 2) ** 0.5
        if not dist:
            return
        dx, dy = (x1 - x0) / dist, (y1 - y0) / dist
        t = 0.0
        while t < dist:
            e = min(t + dash * S, dist)
            self.d.line([(x0 + dx * t, y0 + dy * t), (x0 + dx * e, y0 + dy * e)], fill=color, width=width)
            t = e + gap * S

    def _dashed_border(self, box, color, r, width) -> None:
        x0, y0, x1, y1 = box
        step = 9 * S
        for x in range(int(x0) + int(r), int(x1) - int(r), step * 2):
            self.d.line([(x, y0), (min(x + step, x1 - r), y0)], fill=color, width=width)
            self.d.line([(x, y1), (min(x + step, x1 - r), y1)], fill=color, width=width)
        for y in range(int(y0) + int(r), int(y1) - int(r), step * 2):
            self.d.line([(x0, y), (x0, min(y + step, y1 - r))], fill=color, width=width)
            self.d.line([(x1, y), (x1, min(y + step, y1 - r))], fill=color, width=width)

    def _head(self, a, b, color, size=8) -> None:
        (x0, y0), (x1, y1) = a, b
        dist = ((x1 - x0) ** 2 + (y1 - y0) ** 2) ** 0.5
        if not dist:
            return
        dx, dy = (x1 - x0) / dist, (y1 - y0) / dist
        px, py = -dy, dx
        s = size * S
        self.d.polygon(
            [
                (x1, y1),
                (x1 - dx * s + px * s * 0.5, y1 - dy * s + py * s * 0.5),
                (x1 - dx * s - px * s * 0.5, y1 - dy * s - py * s * 0.5),
            ],
            fill=color,
        )

    # ---------------------------------------------------------------- 图例 --
    def legend(self, x, y, items, title="图例") -> None:
        """图例是"专业"和"草图"的分界线：形状有语义时必须解释。"""
        self.text(x, y, title, self.f_tag, C["muted"])
        yy = y + 20
        for kind, label in items:
            if kind == "proc":
                self.box(x, yy, 34, 18, C["graph"], r=5, width=2)
            elif kind == "ext":
                self.sharp(x, yy, 34, 18, C["ext"], width=2)
            elif kind == "store":
                self.d.rectangle([x * S, yy * S, (x + 34) * S, (yy + 18) * S], fill=C["store"][0],
                                 outline=C["store"][1], width=2)
                self.d.line([(x + 11) * S, yy * S, (x + 11) * S, (yy + 18) * S], fill=C["store"][1], width=2)
            elif kind == "flow":
                self.arrow([(x, yy + 9), (x + 34, yy + 9)], C["faint"], 2)
            elif kind == "dash":
                self.arrow([(x, yy + 9), (x + 34, yy + 9)], C["err"][1], 2, dash=True)
            self.text(x + 44, yy + 2, label, self.f_tiny, "#334155")
            yy += 26

    def footer(self, s: str) -> None:
        self.text(36, self.h - 26, s, self.f_tiny, C["muted"])

    def save(self, path: str | Path) -> Path:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        self.img.resize((self.w, self.h), Image.LANCZOS).save(p, "PNG")
        return p
