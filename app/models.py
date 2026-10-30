"""数据模型：对应教材 13.2 节，自底向上组合。

为什么用 Pydantic 而不是字典：LLM 吐的是 JSON，外部 MCP 返回的也是 JSON，
中间还要算预算。有模型定义，parse 失败会立刻在边界报错，而不是在预算计算里
冒出一个 `TypeError: can only concatenate str`。
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field, field_validator


def _none_if_empty(value: object, required_keys: tuple[str, ...]) -> object:
    """把空模型（`{}`）或缺关键字段的 dict 变成 None。

    为什么要这个：模型当天不需要住宿时，有时不给 `hotel`，有时给 `"hotel": {}`。
    后者会让 `Hotel.name`（必填）校验失败，整份计划被判无效——实测出现过一次，
    用户等了 60 秒只拿到 502。这里在进入模型前把"空壳"归一成 None。
    """
    if isinstance(value, dict):
        if not value:
            return None
        if not all(value.get(k) not in (None, "") for k in required_keys):
            return None
    return value


class Location(BaseModel):
    """经纬度。高德返回的是 "116.397,39.916" 这样的字符串，由 MCP 层拆好再进来。"""

    longitude: float = Field(..., ge=-180, le=180, description="经度")
    latitude: float = Field(..., ge=-90, le=90, description="纬度")


class Attraction(BaseModel):
    name: str = Field(..., description="景点名称")
    address: str = Field(default="", description="地址")
    location: Optional[Location] = Field(default=None, description="经纬度")

    @field_validator("location", mode="before")
    @classmethod
    def _loc(cls, v):
        return _none_if_empty(v, ("longitude", "latitude"))
    visit_duration: int = Field(default=120, gt=0, description="建议游览分钟数")
    description: str = Field(default="", description="简介")
    category: str = Field(default="景点", description="类别")
    rating: Optional[float] = Field(default=None, ge=0, le=5, description="评分")
    image_url: Optional[str] = Field(
        default=None, description="缩略图 URL（列表用，约 400px）"
    )
    image_url_large: Optional[str] = Field(
        default=None, description="大图 URL（点开看大图用，约 1080px）"
    )
    ticket_price: int = Field(default=0, ge=0, description="门票（元）")


class Meal(BaseModel):
    type: str = Field(..., description="breakfast/lunch/dinner/snack")
    name: str = Field(..., description="餐馆或菜品")
    address: str = Field(default="")
    location: Optional[Location] = None
    description: str = Field(default="")
    estimated_cost: int = Field(default=0, ge=0, description="人均（元）")

    @field_validator("location", mode="before")
    @classmethod
    def _loc(cls, v):
        return _none_if_empty(v, ("longitude", "latitude"))


class Hotel(BaseModel):
    name: str = Field(..., description="酒店名称")
    address: str = Field(default="")
    location: Optional[Location] = None
    price_range: str = Field(default="")

    @field_validator("location", mode="before")
    @classmethod
    def _loc(cls, v):
        return _none_if_empty(v, ("longitude", "latitude"))
    rating: str = Field(default="")
    type: str = Field(default="", description="经济型/舒适型/豪华型")
    estimated_cost: int = Field(default=0, ge=0, description="每晚（元）")


class Budget(BaseModel):
    """预算：由 LLM 估算（本作业的要求），四项分列 + 合计。"""

    total_attractions: int = Field(default=0, ge=0)
    total_hotels: int = Field(default=0, ge=0)
    total_meals: int = Field(default=0, ge=0)
    total_transportation: int = Field(default=0, ge=0)
    total: int = Field(default=0, ge=0)

    def recompute_total(self) -> "Budget":
        """合计 = 四项之和。

        LLM 经常把 total 写错（漏一项、或跟分项对不上），所以入模型后重算一次，
        以分项为准——这样"预算明细"和"总计"永远不会自相矛盾。
        """
        self.total = (
            self.total_attractions
            + self.total_hotels
            + self.total_meals
            + self.total_transportation
        )
        return self


class WeatherInfo(BaseModel):
    date: str = Field(..., description="日期")
    day_weather: str = Field(default="", description="白天天气")
    night_weather: str = Field(default="", description="夜间天气")
    day_temp: int = Field(default=0, description="白天温度（摄氏）")
    night_temp: int = Field(default=0, description="夜间温度（摄氏）")
    wind_direction: str = Field(default="")
    wind_power: str = Field(default="")

    @field_validator("day_temp", "night_temp", mode="before")
    @classmethod
    def _parse_temp(cls, v):
        """高德天气返回 "16°C" / "16℃" 这种字符串。"""
        if isinstance(v, str):
            cleaned = v.replace("°C", "").replace("℃", "").replace("°", "").strip()
            try:
                return int(cleaned)
            except ValueError:
                return 0
        return v


class DayPlan(BaseModel):
    date: str = Field(..., description="日期")
    day_index: int = Field(default=0, ge=0, description="第几天，从 0 开始")
    description: str = Field(default="", description="当日概述")
    transportation: str = Field(default="公共交通")
    accommodation: str = Field(default="")
    hotel: Optional[Hotel] = None

    @field_validator("hotel", mode="before")
    @classmethod
    def _hotel(cls, v):
        # 当天不住宿时模型可能给 {}，别让它把整份计划判成无效
        return _none_if_empty(v, ("name",))
    attractions: list[Attraction] = Field(default_factory=list)
    meals: list[Meal] = Field(default_factory=list)


class TripPlan(BaseModel):
    city: str = Field(..., description="目的地城市")
    start_date: str = Field(..., description="开始日期 YYYY-MM-DD")
    end_date: str = Field(..., description="结束日期 YYYY-MM-DD")
    days: list[DayPlan] = Field(default_factory=list)
    weather_info: list[WeatherInfo] = Field(default_factory=list)
    overall_suggestions: str = Field(default="")
    budget: Optional[Budget] = None


class TripRequest(BaseModel):
    """用户输入（对应书里的表单字段，只是这里从命令行传）。"""

    city: str = Field(..., min_length=1)
    start_date: str
    end_date: str
    days: int = Field(default=3, ge=1, le=15)
    preferences: str = Field(default="历史文化")
    budget_level: str = Field(default="中等", description="穷游/中等/舒适")
    transportation: str = Field(default="公共交通")
    accommodation: str = Field(default="经济型酒店")
    # 总预算上限（元）。与 budget_level（风格档位）不同：档位决定"什么档次"，
    # 这里决定"最多花多少"。留空表示不限。
    budget_total: int | None = Field(default=None, ge=0, le=200000)
    # 自由文本要求，例如"想住舒适一点的酒店""带老人，少走路"。
    # 只用来微调，不允许改天数/日期/城市——那几项由表单字段决定。
    notes: str = Field(default="", max_length=500)

    @field_validator("notes", mode="before")
    @classmethod
    def _clean_notes(cls, v):
        if v is None:
            return ""
        return str(v).strip()[:500]

    @field_validator("days", mode="before")
    @classmethod
    def _days_from_string(cls, v):
        if isinstance(v, str):
            return int(v.strip())
        return v
