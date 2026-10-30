// 与 web/logic.js 共用同一份实现（Vite 直接 import 那个 UMD 文件，副作用是挂到
// globalThis.TripLogic 上）。这样"预算怎么算""怎么排序"只有一处代码、一处测试。
import '../../web/logic.js'

export interface Budget {
  total_attractions: number
  total_hotels: number
  total_meals: number
  total_transportation: number
  total: number
}

interface TripLogic {
  money(n: unknown): number
  isPlaceholderName(name: unknown): boolean
  recomputeBudget(plan: any): Budget
  moveItem<T>(list: T[], idx: number, delta: number): boolean
  moveAttraction(plan: any, dayIdx: number, idx: number, delta: number): boolean
  nextMealType(day: any): string
  validate(plan: any): string[]
  toMarkdown(plan: any): string
}

const g = globalThis as unknown as { TripLogic?: TripLogic }

export function logic(): TripLogic {
  const l = g.TripLogic
  if (!l) throw new Error('web/logic.js 未加载')
  return l
}
