<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import TripMap from './TripMap.vue'
import { logic } from '../logic'
import type { Attraction, DayPlan, Meal, TripPlan } from '../types'

const props = defineProps<{
  plan: TripPlan
  markdown: string
  elapsed: number
  tripId: string | null
}>()
const emit = defineEmits<{
  (e: 'saved', payload: { id: string; title: string }): void
}>()

const L = logic()
const MEAL_LABEL: Record<string, string> = {
  breakfast: '早餐',
  lunch: '午餐',
  dinner: '晚餐',
  snack: '加餐',
}
const MEAL_ORDER = ['breakfast', 'lunch', 'dinner', 'snack']

// 编辑直接改 plan 的深层副本，避免"改了又撤销"时污染父组件的数据
const draft = ref<TripPlan>(clone(props.plan))
const editing = ref(false)
const exporting = ref('')
const transportMode = computed(() => draft.value.days?.[0]?.transportation || '市内')

function clone<T>(v: T): T {
  return JSON.parse(JSON.stringify(v))
}

const days = computed(() => draft.value.days ?? [])
const weather = computed(() => draft.value.weather_info ?? [])
const budget = computed(() => L.recomputeBudget(draft.value))
const issues = computed(() => (editing.value ? L.validate(draft.value) : []))

watch(
  () => props.plan,
  (next) => {
    draft.value = clone(next)
    editing.value = false
    loadDayMaps()
  }
)

// ------------------------------------------------------------ 每日地图 --
// 交互地图只有一个实例，导出时只会印出当前那天（用户实测：PDF 里只有第一天地图）。
// 所以每天各嵌一张服务端出的静态图，截图/打印自然每天都全。
const dayMapUrls = ref<Record<number, string>>({})

async function loadDayMaps() {
  const plan = draft.value
  const urls: Record<number, string> = {}
  await Promise.all(
    (plan.days ?? []).map(async (day, d) => {
      const pts = (day.attractions ?? [])
        .filter((a) => a.location)
        .map((a) => ({ lng: a.location!.longitude, lat: a.location!.latitude }))
      if (!pts.length) return
      try {
        const resp = await fetch('/api/staticmap', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ points: pts.slice(0, 10), zoom: 11 }),
        })
        if (!resp.ok) return
        urls[d] = URL.createObjectURL(await resp.blob())
      } catch {
        /* 单天取不到就留空，不影响其它天 */
      }
    })
  )
  dayMapUrls.value = urls
}

// ---------------------------------------------------------------- 撤销 --
// 存 JSON 字符串而不是对象：对象是引用，后续修改会把历史一起改掉。
const past = ref<string[]>([])
const future = ref<string[]>([])
const focusSnapshot = ref<string | null>(null)
const canUndo = computed(() => past.value.length > 0)
const canRedo = computed(() => future.value.length > 0)

function pushHistory() {
  past.value.push(JSON.stringify(draft.value))
  if (past.value.length > 60) past.value.shift()
  future.value = []
}

function undo() {
  if (!canUndo.value) return
  future.value.push(JSON.stringify(draft.value))
  draft.value = JSON.parse(past.value.pop() as string)
  editing.value = true
}

function redo() {
  if (!canRedo.value) return
  past.value.push(JSON.stringify(draft.value))
  draft.value = JSON.parse(future.value.pop() as string)
  editing.value = true
}

function onFocusIn() {
  focusSnapshot.value = JSON.stringify(draft.value)
}

// 一次"焦点进出"算一步，而不是每个按键一步，否则按二十次才退一个字
function onFocusOut() {
  if (focusSnapshot.value && focusSnapshot.value !== JSON.stringify(draft.value)) {
    past.value.push(focusSnapshot.value)
    if (past.value.length > 60) past.value.shift()
    future.value = []
  }
  focusSnapshot.value = null
}

// ---------------------------------------------------------------- 持久化 --
const saveState = ref<'idle' | 'saving' | 'saved' | 'error'>('idle')
let saveTimer: ReturnType<typeof setTimeout> | null = null

async function persistNow() {
  saveState.value = 'saving'
  const body = {
    plan: JSON.parse(JSON.stringify(draft.value)),
    title: draft.value.city ? '' : '',
    markdown: L.toMarkdown(draft.value),
  }
  const isUpdate = Boolean(props.tripId)
  try {
    const resp = await fetch(isUpdate ? `/api/trips/${props.tripId}` : '/api/trips', {
      method: isUpdate ? 'PUT' : 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`)
    const data = await resp.json()
    if (data?.trip?.id) emit('saved', { id: data.trip.id, title: data.trip.title ?? '' })
    saveState.value = 'saved'
  } catch {
    saveState.value = 'error'
  }
}

function scheduleSave() {
  if (saveTimer) clearTimeout(saveTimer)
  saveTimer = setTimeout(() => persistNow(), 1200)
}

function saveNow() {
  if (saveTimer) clearTimeout(saveTimer)
  persistNow()
}

// 结构性改动统一走这里：先记历史、再改、再保存
function withHistory(mutate: () => void) {
  pushHistory()
  mutate()
  scheduleSave()
}

// ---------------------------------------------------------------- 编辑 --
function beginEdit() {
  draft.value = clone(props.plan)
  editing.value = true
}

function saveEdit() {
  editing.value = false
  saveNow()
}

function cancelEdit() {
  // 回到"进入编辑前"的数据，同时把撤销栈清掉——否则历史里混着已放弃的改动
  draft.value = clone(props.plan)
  past.value = []
  future.value = []
  editing.value = false
}

function moveAttraction(dayIdx: number, idx: number, delta: number) {
  withHistory(() => L.moveAttraction(draft.value, dayIdx, idx, delta))
}

function removeAttraction(dayIdx: number, idx: number) {
  withHistory(() => draft.value.days[dayIdx].attractions.splice(idx, 1))
}

function addAttraction(dayIdx: number) {
  withHistory(() => {
    draft.value.days[dayIdx].attractions.push({
      name: '',
      address: '',
      location: null,
      visit_duration: 120,
      description: '',
      category: '景点',
      rating: null,
      image_url: null,
      ticket_price: 0,
    } as Attraction)
  })
}

function addMeal(dayIdx: number) {
  withHistory(() => {
    const day = draft.value.days[dayIdx]
    day.meals.push({
      type: L.nextMealType(day) as string,
      name: '',
      address: '',
      location: null,
      description: '',
      estimated_cost: 0,
    } as Meal)
  })
}

function removeMeal(dayIdx: number, idx: number) {
  withHistory(() => draft.value.days[dayIdx].meals.splice(idx, 1))
}

function ensureHotel(day: DayPlan) {
  if (!day.hotel) {
    day.hotel = {
      name: '',
      address: '',
      location: null,
      price_range: '',
      rating: '',
      type: '',
      estimated_cost: 0,
    }
  }
  return day.hotel
}

// ---------------------------------------------------------------- 展示 --
function price(a: Attraction) {
  return a.ticket_price > 0 ? `${a.ticket_price} 元` : '免费'
}
function coord(a: Attraction) {
  return a.location ? `${a.location.longitude},${a.location.latitude}` : ''
}
function hotelName(day: DayPlan) {
  const n = day.hotel?.name ?? ''
  return L.isPlaceholderName(n) ? '' : n
}

// ---------------------------------------------------------------- 导出 --
async function copyMarkdown() {
  // 用当前（可能已编辑）的数据现算，而不是父组件那份旧的 markdown
  const md = L.toMarkdown(draft.value)
  try {
    await navigator.clipboard.writeText(md)
    exporting.value = '已复制 Markdown'
  } catch {
    exporting.value = '复制失败（浏览器不给剪贴板权限）'
  }
  setTimeout(() => (exporting.value = ''), 2000)
}

async function withExportPrep<T>(fn: () => Promise<T>): Promise<T> {
  // 交互地图是 canvas，html2canvas 抓它会出问题；每天那张静态图才是要导出的内容
  const live = Array.from(document.querySelectorAll<HTMLElement>('.map-live'))
  live.forEach((el) => {
    el.style.display = 'none'
  })

  // 等图片解码完成，否则截图里每天的地图是空白
  const imgs = Array.from(document.querySelectorAll<HTMLImageElement>('.day-map-img'))
  await Promise.all(
    imgs.map((img) =>
      img.complete
        ? Promise.resolve()
        : new Promise<void>((resolve) => {
            img.onload = () => resolve()
            img.onerror = () => resolve()
          })
    )
  )

  try {
    return await fn()
  } finally {
    live.forEach((el) => {
      el.style.display = ''
    })
  }
}
async function exportImage() {
  const target = document.getElementById('trip-plan-content')
  if (!target) return
  exporting.value = '正在生成图片…'
  try {
    const { default: html2canvas } = await import('html2canvas')
    const canvas = await withExportPrep(() =>
      html2canvas(target, { backgroundColor: '#ffffff', scale: 2 })
    )
    const link = document.createElement('a')
    link.download = `${draft.value.city}旅行计划.png`
    link.href = canvas.toDataURL('image/png')
    link.click()
    exporting.value = '图片已导出'
  } catch (e: any) {
    exporting.value = `导出失败：${e?.message ?? e}`
  }
  setTimeout(() => (exporting.value = ''), 2500)
}

async function exportPdf() {
  const target = document.getElementById('trip-plan-content')
  if (!target) return
  exporting.value = '正在生成 PDF…'
  try {
    const [{ default: html2canvas }, { default: jsPDF }] = await Promise.all([
      import('html2canvas'),
      import('jspdf'),
    ])
    const canvas = await withExportPrep(() =>
      html2canvas(target, { backgroundColor: '#ffffff', scale: 2 })
    )
    const pdf = new jsPDF('p', 'mm', 'a4')
    const img = canvas.toDataURL('image/png')
    pdf.addImage(img, 'PNG', 0, 0, 210, (canvas.height * 210) / canvas.width)
    pdf.save(`${draft.value.city}旅行计划.pdf`)
    exporting.value = 'PDF 已导出'
  } catch (e: any) {
    exporting.value = `导出失败：${e?.message ?? e}`
  }
  setTimeout(() => (exporting.value = ''), 2500)
}

onMounted(() => {
  /* 无副作用：编辑态默认关闭 */
})
onBeforeUnmount(() => {
  /* 占位：以后若有地图实例需要销毁，在这里处理 */
})
</script>

<template>
  <div class="toolbar">
    <button class="btn-ghost btn-sm" :disabled="!canUndo" title="Ctrl+Z" @click="undo">
      ↶ 撤销
    </button>
    <button class="btn-ghost btn-sm" :disabled="!canRedo" title="Ctrl+Shift+Z" @click="redo">
      ↷ 重做
    </button>
    <template v-if="!editing">
      <button class="btn-primary btn-sm" @click="beginEdit">✏️ 编辑行程</button>
      <button class="btn-ghost btn-sm" @click="saveNow">保存</button>
      <button class="btn-ghost btn-sm" @click="exportImage">导出图片</button>
      <button class="btn-ghost btn-sm" @click="exportPdf">导出 PDF</button>
      <button class="btn-ghost btn-sm" @click="copyMarkdown">复制 Markdown</button>
    </template>
    <template v-else>
      <button class="btn-primary btn-sm" @click="saveEdit">完成编辑</button>
      <button class="btn-ghost btn-sm" @click="cancelEdit">放弃全部改动</button>
      <span class="hint">每步改动会自动保存；导出时用改后的行程</span>
    </template>
    <span class="spacer" />
    <span class="hint">
      {{ exporting || (saveState === 'saving' ? '保存中…' : saveState === 'saved' ? '✓ 已保存' : saveState === 'error' ? '⚠ 保存失败' : '') }}
    </span>
  </div>

  <div id="trip-plan-content" :class="{ editing }">
    <section class="card">
      <h2>📋 行程概览</h2>
      <div class="overview">
        <span>目的地 <b>{{ draft.city }}</b></span>
        <span>日期 <b>{{ draft.start_date }} ~ {{ draft.end_date }}</b></span>
        <span>天数 <b>{{ days.length }}</b></span>
        <span>生成耗时 <b>{{ (elapsed / 1000).toFixed(1) }}s</b></span>
        <span v-if="editing" class="chip strong">编辑中</span>
      </div>
      <p v-if="!editing && draft.overall_suggestions" class="suggestion">
        {{ draft.overall_suggestions }}
      </p>
      <div v-else-if="editing" class="edit-day">
        <input v-model="draft.city" placeholder="城市" />
        <input v-model="draft.overall_suggestions" placeholder="总体建议" />
      </div>
      <ul v-if="issues.length" class="error" style="margin: 12px 0 0; padding-left: 30px">
        <li v-for="(x, i) in issues" :key="i">{{ x }}</li>
      </ul>
    </section>

    <section class="card">
      <h2>💰 预算估算</h2>
      <div class="budget">
        <div class="cell"><span>景点门票</span><strong>{{ budget.total_attractions }}</strong></div>
        <div class="cell"><span>酒店住宿</span><strong>{{ budget.total_hotels }}</strong></div>
        <div class="cell"><span>餐饮</span><strong>{{ budget.total_meals }}</strong></div>
        <div class="cell">
          <span>交通（{{ transportMode }}）</span><strong>{{ budget.total_transportation }}</strong>
        </div>
        <div class="cell total"><span>合计（元）</span><strong>{{ budget.total }}</strong></div>
      </div>
      <p class="budget-note">
        门票/餐饮/住宿按明细实时汇总；交通沿用模型估算（只算市内：自驾为油费 + 停车费，不含过路费与租车费）。
      </p>
    </section>

    <TripMap :days="days" />

    <section class="card">
      <h2>📅 每日行程</h2>
      <div class="timeline">
        <article
          v-for="(day, d) in days"
          :key="d"
          class="day"
          :data-day="d + 1"
        >
          <div class="day-head">
            <h3>第 {{ d + 1 }} 天</h3>
            <span class="date">{{ day.date }}</span>
            <span class="chip strong">{{ day.transportation }}</span>
            <span class="chip">{{ day.attractions.length }} 个景点</span>
          </div>

          <div v-if="editing" class="edit-day">
            <input v-model="day.description" placeholder="当天概述" />
            <select v-model="day.transportation">
              <option v-for="t in ['公共交通', '打车', '自驾', '步行']" :key="t">{{ t }}</option>
            </select>
          </div>
          <p v-else-if="day.description" class="day-desc">{{ day.description }}</p>

          <!-- 酒店 -->
          <div class="row">
            <span class="rank" style="background: #fdf3e0; color: #8a5a00">宿</span>
            <div class="body">
              <template v-if="editing">
                <div class="edit-row">
                  <input
                    :value="ensureHotel(day).name"
                    placeholder="酒店名（留空表示当天不住宿）"
                    @input="ensureHotel(day).name = ($event.target as HTMLInputElement).value"
                  />
                  <input
                    :value="ensureHotel(day).estimated_cost"
                    type="number"
                    min="0"
                    step="50"
                    @input="ensureHotel(day).estimated_cost = Number(($event.target as HTMLInputElement).value)"
                  />
                </div>
              </template>
              <template v-else-if="hotelName(day)">
                <div class="title">{{ hotelName(day) }}</div>
                <div class="sub">{{ day.hotel?.estimated_cost }} 元/晚</div>
              </template>
              <template v-else>
                <div class="sub">当天不安排住宿</div>
              </template>
            </div>
          </div>

          <!-- 景点 -->
          <div v-for="(a, i) in day.attractions" :key="`a-${i}`" class="row">
            <img v-if="a.image_url" :class="editing ? 'thumb-lg' : 'thumb'" :src="a.image_url" alt="" />
            <span class="rank">{{ i + 1 }}</span>
            <div class="body">
              <template v-if="editing">
                <div class="edit-row three">
                  <input v-model="a.name" placeholder="景点名称" />
                  <input v-model.number="a.visit_duration" type="number" min="10" step="15" title="游览分钟" />
                  <input v-model.number="a.ticket_price" type="number" min="0" step="10" title="门票" />
                </div>
              </template>
              <template v-else>
                <div class="title">{{ a.name }}</div>
                <div class="sub">
                  {{ a.visit_duration }} 分钟 · {{ price(a) }}
                  <template v-if="coord(a)">· {{ coord(a) }}</template>
                </div>
              </template>
            </div>
            <div v-if="editing" class="ops" style="opacity: 1">
              <button class="btn-icon" title="上移" @click="moveAttraction(d, i, -1)">↑</button>
              <button class="btn-icon" title="下移" @click="moveAttraction(d, i, 1)">↓</button>
              <button class="btn-icon danger" title="删除" @click="removeAttraction(d, i)">✕</button>
            </div>
          </div>

          <!-- 餐饮 -->
          <div v-for="(m, i) in day.meals" :key="`m-${i}`" class="row">
            <span class="rank tag-meal" style="background: #e8f8f1; color: var(--ok)">
              {{ MEAL_LABEL[m.type] ?? m.type }}
            </span>
            <div class="body">
              <template v-if="editing">
                <div class="edit-row">
                  <input v-model="m.name" placeholder="餐厅或菜品" />
                  <input v-model.number="m.estimated_cost" type="number" min="0" step="10" />
                </div>
              </template>
              <template v-else>
                <div class="title">{{ m.name }}</div>
                <div class="sub">{{ m.estimated_cost }} 元</div>
              </template>
            </div>
            <div v-if="editing" class="ops" style="opacity: 1">
              <button class="btn-icon danger" title="删除" @click="removeMeal(d, i)">✕</button>
            </div>
          </div>

          <div v-if="editing" class="add-row">
            <button @click="addAttraction(d)">+ 添加景点</button>
            <button @click="addMeal(d)">+ 添加餐饮</button>
          </div>

          <!-- 每天一张静态地图：导出/打印时每天都全，不会只有第一天 -->
          <div v-if="!editing" class="day-map">
            <img
              v-if="dayMapUrls[d]"
              class="day-map-img"
              :src="dayMapUrls[d]"
              :alt="`第 ${d + 1} 天地图`"
            />
          </div>
        </article>
      </div>
    </section>

    <section v-if="weather.length" class="card">
      <h2>🌤️ 天气</h2>
      <div class="weather">
        <div v-for="(w, i) in weather" :key="i" class="w">
          <b>{{ w.date }}</b>
          <span>{{ w.day_weather }} / {{ w.night_weather }}</span><br />
          <span>{{ w.day_temp }}~{{ w.night_temp }}°C</span>
          <span v-if="w.wind_direction"> · {{ w.wind_direction }}风 {{ w.wind_power }} 级</span>
        </div>
      </div>
    </section>
  </div>
</template>
