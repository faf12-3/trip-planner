"""提示词：只留规划环节用 LLM（工具参数由请求直接决定，不需要模型猜）。

教材里四个 Agent 各自带提示词、由模型决定调哪个工具。这里刻意收紧：
景点/天气/酒店这三步的工具名和参数是**确定的**（keywords 就是用户偏好、
city 就是目的地），让模型再"决定"一次只会多花一次调用、多一个出错点。
模型的能力用在真正需要判断的地方——排日程和估预算。

注意：JSON 示例单独放常量，不参与 `.format()`——否则示例里的 `{` 会被当成
占位符（第一版就踩了这个 KeyError）。
"""

from __future__ import annotations

_HEAD = """你是行程规划专家。根据给定资料，为目的地生成逐日行程，并估算预算。

**必须严格输出 JSON**（不要 Markdown 代码块、不要解释文字），结构如下：

"""

# 原样保留，不做格式化
_JSON_SCHEMA = """{
  "city": "城市",
  "start_date": "YYYY-MM-DD",
  "end_date": "YYYY-MM-DD",
  "days": [
    {
      "date": "YYYY-MM-DD",
      "day_index": 0,
      "description": "当日概述",
      "transportation": "公共交通",
      "accommodation": "住宿安排",
      "hotel": {"name": "", "address": "", "type": "", "estimated_cost": 0},
      "attractions": [
        {"name": "", "address": "", "visit_duration": 120, "description": "",
         "ticket_price": 0, "location": {"longitude": 116.397, "latitude": 39.916}}
      ],
      "meals": [
        {"type": "breakfast", "name": "", "estimated_cost": 0}
      ]
    }
  ],
  "weather_info": [
    {"date": "YYYY-MM-DD", "day_weather": "", "night_weather": "",
     "day_temp": 18, "night_temp": 9, "wind_direction": "", "wind_power": ""}
  ],
  "overall_suggestions": "实用建议",
  "budget": {
    "total_attractions": 0, "total_hotels": 0,
    "total_meals": 0, "total_transportation": 0, "total": 0
  }
}
"""

_RULES = """
**规划要求**：
1. 只能使用资料里出现过的景点、酒店、餐饮；不要编造不存在的地点
2. 每天安排 2~3 个景点，且必须在**地理上集中**：同一景点群的经纬度相差不超过 0.1 度，
   不要一天里横跨城区（例如把北五环和南二环排在同一天）
3. 每天包含早中晚三餐；当天不需要住宿时 hotel 留空（不要写 "无"）
4. 温度是纯数字（不带 °C）
5. 预算按「门票 + 酒店 × 晚数 + 餐费 + 市内交通」估算，四项相加等于 total
6. 行程天数必须等于 {days} 天，日期从 {start_date} 开始连续
7. day_index 从 0 开始、按顺序递增（第一天 0，第二天 1）
8. 同一个景点只安排一次，不要在多天重复出现；餐饮不要当作景点
9. 如果某项资料为空（例如天气资料为空），对应字段返回空数组，**绝不编造**：
   没有天气数据就不要生成 weather_info，没有酒店资料就不要虚构酒店名
10. 交通预算按下列口径估算（只算市内，不含往返大交通）：
    - 公共交通：每人每天 20~40 元（地铁/公交）
    - 自驾：市内油费 + 停车费，**停车通常是主要开销**（景区周边 10~20 元/小时），
      不含过路费、租车费、违章罚款；据此每天约 80~150 元
    - 打车：每天 80~150 元
    - 步行：0~20 元（偶尔乘车）
    并在 overall_suggestions 里用一句话说明交通费用包含什么
"""


def planner_system(days: int, start_date: str, memory: str = "") -> str:
    """系统提示词。``memory`` 非空时追加一段"用户历史偏好"。

    单独一段而不是拼进需求里：模型对"这是历史、不是本次要求"很敏感，
    混在一起它会拿历史偏好覆盖用户这次的选择。
    """
    base = _HEAD + _JSON_SCHEMA + _RULES.format(days=days, start_date=start_date)
    if not memory.strip():
        return base
    return base + "\n" + memory.strip() + "\n"


def planner_user_prompt(
    *,
    city: str,
    start_date: str,
    end_date: str,
    days: int,
    preferences: str,
    budget_level: str,
    transportation: str,
    accommodation: str,
    attractions_raw: str,
    weather_raw: str,
    hotels_raw: str,
    budget_total: int | None = None,
    notes: str = "",
) -> str:
    budget_line = (
        f"{budget_total} 元（四项合计不得超过）" if budget_total else "未设上限，按档位估算"
    )
    notes_line = notes.strip() or "无"

    return f"""请生成 {city} 的 {days} 日行程。

**用户需求**
- 目的地：{city}
- 日期：{start_date} 至 {end_date}（共 {days} 天）
- 偏好：{preferences}
- 预算档位：{budget_level}
- 交通方式：{transportation}
- 住宿类型：{accommodation}
- 预算上限：{budget_line}
- 其他要求：{notes_line}

**约束优先级**
1. 城市、日期、天数由表单决定，任何情况下不得更改；
2. 「其他要求」优先于默认风格（例如要求"住宿舒适一点"，就把酒店单价往上调，
   同时在上限内压减门票或餐饮）；
3. 预算上限是硬约束：**四项合计必须 ≤ 上限**。确实压不下来时照常输出计划，
   但在 overall_suggestions 里写清"哪一项超了、超多少、建议怎么减"。
4. **不许假称已满足**：如果某条「其他要求」在上限内做不到（例如预算 600 元却要求
   住舒适型酒店），就在 overall_suggestions 里明说"受预算限制，住宿只能选经济型，
   若要舒适需追加约 N 元"。禁止一边选经济型、一边写成"已满足舒适度要求"。

**景点资料（高德 POI 搜索结果）**
{attractions_raw[:3000]}

**天气资料**
{weather_raw[:2000]}

**酒店资料**
{hotels_raw[:2000]}
"""


def routing_note(routing: dict | None) -> str:
    """条件路由命中的时候，给 planner 的约束段。

    故意写得像"硬约束"：模型对"建议"很宽松，对"必须"才认真。
    """
    if not routing or not routing.get("needed"):
        return ""

    days = routing.get("days") or []
    if not days:
        return ""

    lines = ["## 天气导致的行程约束（必须遵守）", ""]
    for item in days:
        lines.append(
            f"- {item.get('date')}：{item.get('weather')}（{item.get('reason')}）"
            f" → 当天的景点**必须**从室内场所中选择（博物馆 / 美术馆 / 科技馆 / 展览馆 / 纪念馆等）"
        )
    lines.append("")
    lines.append("不要在这一天安排公园、广场、山、湖、街区、古镇这类室外场所。")
    if routing.get("indoor_query_failed"):
        lines.append("（室内候选没能查到，请用你已知的同类场所，不要改成室外。）")
    return "\n".join(lines) + "\n"
