import type { PlanResponse, TripRequest } from './types'

/**
 * 生成行程。
 *
 * 超时给到 5 分钟：真实链路是「3 次高德调用 + 1 次 LLM 规划」，
 * 实测 1 天约 25 秒、3 天约 45 秒，但模型排队时会更久。
 * fetch 本身不带超时，这里用 AbortController 显式控制，避免用户一直等。
 */
export async function generateTripPlan(request: TripRequest): Promise<PlanResponse> {
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), 300_000)

  try {
    const resp = await fetch('/api/trip/plan', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(request),
      signal: controller.signal,
    })

    if (!resp.ok) {
      let detail = `HTTP ${resp.status}`
      try {
        const body = await resp.json()
        if (typeof body?.detail === 'string') detail = body.detail
        else if (Array.isArray(body?.detail)) detail = JSON.stringify(body.detail)
      } catch {
        /* 响应体不是 JSON，就用状态码 */
      }
      throw new Error(detail)
    }

    return (await resp.json()) as PlanResponse
  } catch (err) {
    if (err instanceof DOMException && err.name === 'AbortError') {
      throw new Error('请求超时（5 分钟）。行程规划偏慢，可以先把天数改少一点再试。')
    }
    throw err
  } finally {
    clearTimeout(timer)
  }
}

export async function checkHealth(): Promise<{ ok: boolean; amap_key_configured: boolean }> {
  const resp = await fetch('/api/health')
  if (!resp.ok) throw new Error(`HTTP ${resp.status}`)
  return resp.json()
}
