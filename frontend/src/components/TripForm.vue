<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { generateTripPlan } from '../api'
import type { TripRequest } from '../types'

const emit = defineEmits<{ (e: 'done', payload: { plan: any; markdown: string; elapsed: number }): void }>()

const form = reactive<TripRequest>({
  city: '北京',
  start_date: defaultDate(1),
  end_date: defaultDate(3),
  days: 3,
  preferences: '历史文化',
  budget_level: '中等',
  transportation: '公共交通',
  accommodation: '经济型酒店',
})

const loading = ref(false)
const error = ref('')
const progress = ref(0)
const status = ref('')
let timer: number | undefined

/** 日期天数联动：改日期就重算天数，改天数就重算结束日期。 */
const autoDays = computed(() => {
  const a = Date.parse(form.start_date)
  const b = Date.parse(form.end_date)
  if (Number.isNaN(a) || Number.isNaN(b) || b < a) return 1
  return Math.round((b - a) / 86_400_000) + 1
})

function onDatesChanged() {
  form.days = autoDays.value
}

function onDaysChanged() {
  const a = Date.parse(form.start_date)
  if (Number.isNaN(a)) return
  const end = new Date(a + (Math.max(1, form.days) - 1) * 86_400_000)
  form.end_date = end.toISOString().slice(0, 10)
}

function defaultDate(offsetDays: number): string {
  const d = new Date()
  d.setDate(d.getDate() + offsetDays)
  return d.toISOString().slice(0, 10)
}

onMounted(() => {
  form.days = autoDays.value
})

async function submit() {
  if (!form.city.trim()) {
    error.value = '先填目的地城市'
    return
  }
  loading.value = true
  error.value = ''
  progress.value = 0
  status.value = '正在搜索景点…'

  // 进度是模拟的：后端一次性返回，没有流式进度可拿。
  // 文案按节点顺序走，让等待有信息量。
  timer = window.setInterval(() => {
    if (progress.value < 90) {
      progress.value += 7
      if (progress.value <= 30) status.value = '正在搜索景点（高德 POI）…'
      else if (progress.value <= 50) status.value = '正在查询天气…'
      else if (progress.value <= 65) status.value = '正在筛选酒店…'
      else status.value = '正在生成行程与预算（LLM）…'
    }
  }, 700)

  try {
    const resp = await generateTripPlan({ ...form, days: autoDays.value })
    progress.value = 100
    status.value = '完成'
    emit('done', { plan: resp.plan, markdown: resp.markdown, elapsed: resp.elapsed_ms })
  } catch (e: any) {
    error.value = e?.message ?? String(e)
  } finally {
    window.clearInterval(timer)
    loading.value = false
  }
}
</script>

<template>
  <section class="card">
    <h2>🗺️ 规划一次旅行</h2>

    <div class="grid-2">
      <div class="field">
        <label>目的地城市</label>
        <input v-model.trim="form.city" placeholder="如：北京" />
      </div>
      <div class="field">
        <label>偏好</label>
        <select v-model="form.preferences">
          <option>历史文化</option>
          <option>自然风光</option>
          <option>美食</option>
          <option>亲子</option>
          <option>购物</option>
        </select>
      </div>

      <div class="field">
        <label>出发日期</label>
        <input v-model="form.start_date" type="date" @change="onDatesChanged" />
      </div>
      <div class="field">
        <label>结束日期</label>
        <input v-model="form.end_date" type="date" @change="onDatesChanged" />
      </div>

      <div class="field">
        <label>天数（与日期联动）</label>
        <input v-model.number="form.days" type="number" min="1" max="15" @change="onDaysChanged" />
      </div>
      <div class="field">
        <label>预算档位</label>
        <select v-model="form.budget_level">
          <option>穷游</option>
          <option>中等</option>
          <option>舒适</option>
        </select>
      </div>

      <div class="field">
        <label>交通方式</label>
        <select v-model="form.transportation">
          <option>公共交通</option>
          <option>打车</option>
          <option>自驾</option>
          <option>步行</option>
        </select>
      </div>
      <div class="field">
        <label>住宿类型</label>
        <select v-model="form.accommodation">
          <option>经济型酒店</option>
          <option>舒适型酒店</option>
          <option>豪华型酒店</option>
          <option>民宿</option>
        </select>
      </div>
    </div>

    <div class="actions">
      <button class="btn-primary" :disabled="loading" @click="submit">
        {{ loading ? '生成中…' : '开始规划' }}
      </button>
      <span class="meta" style="color: var(--muted); font-size: 13px">
        真实调用高德 MCP（15 个工具）+ LLM，通常 25~45 秒
      </span>
    </div>

    <div v-if="loading" class="progress">
      <div class="bar"><i :style="{ width: progress + '%' }" /></div>
      <p>{{ status }}</p>
    </div>

    <div v-if="error" class="error">{{ error }}</div>
  </section>
</template>
