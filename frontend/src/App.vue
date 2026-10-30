<script setup lang="ts">
import { onMounted, ref } from 'vue'
import TripForm from './components/TripForm.vue'
import TripResult from './components/TripResult.vue'
import { checkHealth } from './api'
import type { TripPlan } from './types'

const plan = ref<TripPlan | null>(null)
const markdown = ref('')
const elapsed = ref(0)
const backendOk = ref<boolean | null>(null)

// 持久化：生成后自动存一份，之后每次改动都 PUT 更新同一份
const tripId = ref<string | null>(null)
interface TripMeta {
  id: string
  title: string
  city: string
  start_date: string
  end_date: string
  days: number
  total: number
  updated_at: string
}
const recent = ref<TripMeta[]>([])

async function loadRecent() {
  try {
    const resp = await fetch('/api/trips')
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`)
    recent.value = (await resp.json()).items ?? []
  } catch {
    recent.value = []
  }
}

async function openTrip(id: string) {
  try {
    const resp = await fetch(`/api/trips/${id}`)
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`)
    const record = await resp.json()
    plan.value = record.plan
    tripId.value = record.id
    markdown.value = record.markdown ?? ''
    elapsed.value = 0
    requestAnimationFrame(() => {
      document.getElementById('result-anchor')?.scrollIntoView({ behavior: 'smooth', block: 'start' })
    })
  } catch {
    /* 打不开就保持当前页面 */
  }
}

async function deleteTrip(id: string) {
  try {
    await fetch(`/api/trips/${id}`, { method: 'DELETE' })
    if (tripId.value === id) tripId.value = null
    await loadRecent()
  } catch {
    /* 忽略 */
  }
}

onMounted(async () => {
  // 先把"后端在不在"这件事显示出来：否则用户点了按钮才发现 8000 没起
  try {
    const h = await checkHealth()
    backendOk.value = h.ok
  } catch {
    backendOk.value = false
  }
  loadRecent()
})

function onDone(payload: { plan: TripPlan; markdown: string; elapsed: number }) {
  plan.value = payload.plan
  markdown.value = payload.markdown
  elapsed.value = payload.elapsed
  tripId.value = null
  // 出结果后滚到结果区，省一次手动拖动
  requestAnimationFrame(() => {
    document.getElementById('result-anchor')?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  })
}

function onSaved(payload: { id: string; title: string }) {
  tripId.value = payload.id
  loadRecent()
}

function reset() {
  plan.value = null
  markdown.value = ''
  tripId.value = null
  loadRecent()
  window.scrollTo({ top: 0, behavior: 'smooth' })
}
</script>

<template>
  <div class="page">
    <header class="hero">
      <h1>✈️ 智能旅行助手</h1>
      <p>高德地图 MCP（真实接口）+ LLM 规划 · 预算由模型估算</p>
    </header>

    <div v-if="backendOk === false" class="card">
      <div class="error" style="margin: 0">
        连不上后端。请先在另一个终端启动：<br />
        <code>cd /d D:\PRACTICE\trip_planner &amp;&amp; ..\.venv2\Scripts\python.exe -m app.api</code>
      </div>
    </div>

    <TripForm v-if="!plan" @done="onDone" />

    <!-- 已保存的行程：刷新页面后从这里接着改 -->
    <section v-if="!plan && recent.length" class="card">
      <h2>🗂️ 已保存的行程 <span class="chip">{{ recent.length }}</span></h2>
      <div class="recent">
        <div v-for="t in recent" :key="t.id" class="recent-item">
          <button class="recent-open" @click="openTrip(t.id)">
            <b>{{ t.title || t.city }}</b>
            <span class="meta">
              {{ t.start_date }} ~ {{ t.end_date }} · {{ t.days }} 天 · {{ t.total }} 元
            </span>
          </button>
          <span class="meta">{{ (t.updated_at || '').replace('T', ' ').slice(0, 16) }}</span>
          <button class="btn-icon danger" title="删除" @click="deleteTrip(t.id)">✕</button>
        </div>
      </div>
    </section>

    <template v-else-if="plan">
      <div id="result-anchor" />
      <TripResult
        :plan="plan"
        :markdown="markdown"
        :elapsed="elapsed"
        :trip-id="tripId"
        @saved="onSaved"
      />
      <div class="actions" style="justify-content: center">
        <button class="btn-ghost" @click="reset">重新规划</button>
      </div>
    </template>

    <footer class="footer">
      数据来源：高德地图 MCP · 模型：gemini-3-flash-preview · 无前端构建依赖时可退化到 CLI
    </footer>
  </div>
</template>
