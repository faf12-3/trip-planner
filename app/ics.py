"""把行程导出成 .ics 日历（RFC 5545）。

为什么值得单独做：日历是外部格式，**有标准可对照**。写对了，手机/Outlook/Google 日历都能导入；
写错了不会报错，只会「导进去少几个事件」或者「标题里的逗号被截断」——这种静默错误最难查。

三个最容易踩的点，都在这里显式处理：
  1. **转义**：逗号、分号、反斜杠、换行必须转义，否则解析器会把标题切开
  2. **折行**：一行不能超过 75 个八位组，超了要 CRLF + 一个空格续行（中文还要按字节算）
  3. **时区**：本地时间必须带 TZID + VTIMEZONE，否则别人导进日历时会按他所在时区解释
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta
from typing import Any

TZID = "Asia/Shanghai"
PRODID = "-//trip-planner//智能旅行助手//CN"
MAX_OCTETS = 75

# 每天的骨架：早餐 08:00、景点 09:00 起顺排、午餐排到"哪儿算哪儿"、
# 晚餐不早于 18:00、入住不早于 20:00。所有插入都推进游标，因此不可能重叠。
BREAKFAST_AT = 8
DAY_START = 9
LUNCH_NOT_BEFORE = 11 * 60 + 30   # 11:30 之后才排午餐
DINNER_NOT_BEFORE = 18
HOTEL_NOT_BEFORE = 20
MEAL_MINUTES = 60
TRANSIT_MINUTES = 30


def escape(text: str) -> str:
    """RFC 5545 的 TEXT 转义。顺序很重要：先反斜杠再其余，否则会被二次转义。"""
    s = str(text or "")
    s = s.replace("\\", "\\\\")
    s = s.replace(";", "\\;")
    s = s.replace(",", "\\,")
    s = s.replace("\r\n", "\\n").replace("\n", "\\n").replace("\r", "\\n")
    return s


def fold(line: str) -> str:
    """按 75 八位组折行。中文一个字 3 字节，所以必须按字节切，不能按字符切。"""
    raw = line.encode("utf-8")
    if len(raw) <= MAX_OCTETS:
        return line

    out: list[str] = []
    chunk: list[bytes] = []
    size = 0
    limit = MAX_OCTETS
    for ch in line:
        b = ch.encode("utf-8")
        if size + len(b) > limit:
            out.append(b"".join(chunk).decode("utf-8"))
            chunk, size = [], 0
            limit = MAX_OCTETS - 1  # 续行前面要加一个空格
        chunk.append(b)
        size += len(b)
    if chunk:
        out.append(b"".join(chunk).decode("utf-8"))
    return "\r\n ".join(out)


def _stamp(dt: datetime) -> str:
    return dt.strftime("%Y%m%dT%H%M%S")


def _uid(*parts: Any) -> str:
    """UID 要稳定：同一次导出两次得到的 UID 必须一致，否则重复导入会产生重复事件。"""
    digest = hashlib.sha1("|".join(str(p) for p in parts).encode("utf-8")).hexdigest()[:12]
    return f"{digest}@trip-planner.local"


def _day_date(day: Any) -> datetime | None:
    try:
        return datetime.strptime(str(getattr(day, "date", "")), "%Y-%m-%d")
    except (ValueError, TypeError):
        return None


def _day_slots(day: Any) -> list[tuple[Any, int, str, str, str]]:
    """把一个 DayPlan 排成一条不重叠的时间轴。

    返回 [(开始时间, 分钟数, 标题, 描述, 地点)]，调用方只管转成 VEVENT。
    核心是 **单游标**：每次插入后 cursor 往后推，所以任何两条都不会重叠。
    """
    base = _day_date(day)
    if base is None:
        return []

    meals = list(getattr(day, "meals", []) or [])

    def pick(kind: str):
        for m in meals:
            if str(getattr(m, "type", "")).lower() == kind:
                return m
        return None

    breakfast, lunch, dinner = pick("breakfast"), pick("lunch"), pick("dinner")
    known = {id(breakfast), id(lunch), id(dinner)}
    extras = [m for m in meals if id(m) not in known]

    def meal_slot(m: Any, start: Any, minutes: int = MEAL_MINUTES):
        cost = int(getattr(m, "estimated_cost", 0) or 0)
        name = (getattr(m, "name", "") or "").strip() or {
            "breakfast": "早餐", "lunch": "午餐", "dinner": "晚餐",
        }.get(str(getattr(m, "type", "")).lower(), "用餐")
        return (start, minutes, name, f"人均 {cost} 元" if cost else "",
                getattr(m, "address", "") or "")

    slots: list[tuple[Any, int, str, str, str]] = []
    cursor = base.replace(hour=DAY_START, minute=0)

    # 早餐固定 08:00，排在一切之前
    if breakfast is not None:
        slots.append(meal_slot(breakfast, base.replace(hour=BREAKFAST_AT, minute=0), 45))

    lunch_placed = lunch is None
    for att in getattr(day, "attractions", []) or []:
        minutes = int(getattr(att, "visit_duration", 90) or 90)

        # 如果此刻已过 11:30 而午餐还没排，先插午餐（把景点往后推）
        minutes_of_day = cursor.hour * 60 + cursor.minute
        if not lunch_placed and minutes_of_day >= LUNCH_NOT_BEFORE:
            slots.append(meal_slot(lunch, cursor))
            cursor += timedelta(minutes=MEAL_MINUTES + TRANSIT_MINUTES)
            lunch_placed = True

        ticket = int(getattr(att, "ticket_price", 0) or 0)
        desc = ["免费" if ticket == 0 else f"门票 {ticket} 元"]
        if getattr(att, "description", ""):
            desc.append(str(att.description))
        loc = getattr(att, "location", None)
        coord = f"{loc.longitude},{loc.latitude}" if loc else ""
        slots.append((
            cursor, minutes, att.name, "；".join(desc),
            getattr(att, "address", "") or coord,
        ))
        cursor += timedelta(minutes=minutes + TRANSIT_MINUTES)

    # 景点排完了午餐还没落 → 补在 12:00 与当前时刻的较晚者
    if not lunch_placed:
        fallback = max(base.replace(hour=12, minute=0), cursor)
        slots.append(meal_slot(lunch, fallback))
        cursor = fallback + timedelta(minutes=MEAL_MINUTES + TRANSIT_MINUTES)

    if dinner is not None:
        dinner_at = max(base.replace(hour=DINNER_NOT_BEFORE, minute=0), cursor)
        slots.append(meal_slot(dinner, dinner_at))
        cursor = dinner_at + timedelta(minutes=MEAL_MINUTES + TRANSIT_MINUTES)

    # 加餐/夜宵之类：排在晚餐之后
    for extra in extras:
        slots.append(meal_slot(extra, cursor))
        cursor += timedelta(minutes=MEAL_MINUTES + TRANSIT_MINUTES)

    hotel = getattr(day, "hotel", None)
    if hotel is not None and (getattr(hotel, "name", "") or "").strip():
        hotel_at = max(base.replace(hour=HOTEL_NOT_BEFORE, minute=0), cursor)
        price = int(getattr(hotel, "estimated_cost", 0) or 0)
        slots.append((
            hotel_at, 60, f"入住 {hotel.name}", f"{price} 元/晚" if price else "",
            getattr(hotel, "address", "") or "",
        ))

    return slots


def _event(
    *,
    uid: str,
    start: datetime,
    minutes: int,
    summary: str,
    description: str = "",
    location: str = "",
) -> list[str]:
    lines = [
        "BEGIN:VEVENT",
        f"UID:{uid}",
        f"DTSTAMP:{_stamp(now_stamp())}",
        f"DTSTART;TZID={TZID}:{_stamp(start)}",
        f"DTEND;TZID={TZID}:{_stamp(start + timedelta(minutes=max(15, minutes)))}",
        f"SUMMARY:{escape(summary)}",
    ]
    if description:
        lines.append(f"DESCRIPTION:{escape(description)}")
    if location:
        lines.append(f"LOCATION:{escape(location)}")
    lines.append("END:VEVENT")
    return lines


_STAMP_CACHE: list[datetime] = []


def now_stamp() -> datetime:
    """DTSTAMP 用导出时刻；测试里可以固定它。"""
    if _STAMP_CACHE:
        return _STAMP_CACHE[0]
    return datetime.now()


def freeze_stamp(dt: datetime | None = None) -> None:
    _STAMP_CACHE.clear()
    if dt is not None:
        _STAMP_CACHE.append(dt)


def build_calendar(plan: Any, *, trip_id: str = "trip", title: str = "") -> str:
    """把 TripPlan 转成一行不落的 .ics 文本（CRLF 换行）。"""
    lines: list[str] = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        f"PRODID:{PRODID}",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-TIMEZONE:{TZID}",
        # 固定偏移的 VTIMEZONE：中国自 1991 年起不再实行夏令时
        "BEGIN:VTIMEZONE",
        f"TZID:{TZID}",
        "BEGIN:STANDARD",
        "DTSTART:19700101T000000",
        "TZOFFSETFROM:+0800",
        "TZOFFSETTO:+0800",
        "TZNAME:CST",
        "END:STANDARD",
        "END:VTIMEZONE",
    ]

    calendar_name = title or f"{getattr(plan, 'city', '')} 行程"
    lines.append(f"X-WR-CALNAME:{escape(calendar_name)}")

    for day_index, day in enumerate(getattr(plan, "days", []) or []):
        base = _day_date(day)
        if base is None:
            continue  # 日期坏掉就跳过这一天，不要让整份日历失效

        for idx, (start_at, minutes, summary, desc, place) in enumerate(_day_slots(day)):
            lines += _event(
                uid=_uid(trip_id, day_index, idx),
                start=start_at,
                minutes=minutes,
                summary=summary,
                description=desc,
                location=place,
            )

    lines.append("END:VCALENDAR")
    # 每一行都要折行；最后统一用 CRLF 连接（RFC 要求）
    return "\r\n".join(fold(line) for line in lines) + "\r\n"


def filename_for(plan: Any, trip_id: str = "trip") -> str:
    city = str(getattr(plan, "city", "") or "trip")
    start = str(getattr(plan, "start_date", "") or "")
    safe = "".join(ch for ch in city if ch.isalnum() or ch in "-_")
    return f"{safe or trip_id}-{start or trip_id}.ics"
