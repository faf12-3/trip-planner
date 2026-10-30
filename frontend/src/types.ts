// 与后端 Pydantic 模型一一对应。改后端字段时这里要同步，
// 好处是字段拼错会在编译期就报出来，而不是等到页面白屏。

export interface Location {
  longitude: number
  latitude: number
}

export interface Attraction {
  name: string
  address: string
  location: Location | null
  visit_duration: number
  description: string
  category: string
  rating: number | null
  image_url: string | null
  ticket_price: number
}

export interface Meal {
  type: string
  name: string
  address: string
  location: Location | null
  description: string
  estimated_cost: number
}

export interface Hotel {
  name: string
  address: string
  location: Location | null
  price_range: string
  rating: string
  type: string
  estimated_cost: number
}

export interface Budget {
  total_attractions: number
  total_hotels: number
  total_meals: number
  total_transportation: number
  total: number
}

export interface WeatherInfo {
  date: string
  day_weather: string
  night_weather: string
  day_temp: number
  night_temp: number
  wind_direction: string
  wind_power: string
}

export interface DayPlan {
  date: string
  day_index: number
  description: string
  transportation: string
  accommodation: string
  hotel: Hotel | null
  attractions: Attraction[]
  meals: Meal[]
}

export interface TripPlan {
  city: string
  start_date: string
  end_date: string
  days: DayPlan[]
  weather_info: WeatherInfo[]
  overall_suggestions: string
  budget: Budget | null
}

export interface TripRequest {
  city: string
  start_date: string
  end_date: string
  days: number
  preferences: string
  budget_level: string
  transportation: string
  accommodation: string
}

export interface PlanResponse {
  plan: TripPlan
  markdown: string
  elapsed_ms: number
  trace: string[]
  /** 某个工具失败但行程仍可用时的说明；前端应展示为"提示"而不是报错 */
  errors: string[]
}
