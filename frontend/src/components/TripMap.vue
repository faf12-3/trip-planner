<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import type { Attraction, DayPlan } from '../types'

const props = defineProps<{ days: DayPlan[] }>()

const currentDay = ref(0)
const mode = ref<'loading' | 'interactive' | 'static' | 'empty'>('loading')
const note = ref('')
const mapEl = ref<HTMLDivElement | null>(null)

let amap: any = null
let map: any = null
let overlays: any[] = []

function pointsOf(day: DayPlan | undefined) {
  return (day?.attractions ?? [])
    .filter((a: Attraction) => a.location)
    .map((a: Attraction) => ({
      lng: a.location!.longitude,
      lat: a.location!.latitude,
      name: a.name,
    }))
}

function loadScript(src: string) {
  return new Promise<void>((resolve, reject) => {
    const s = document.createElement('script')
    s.src = src
    s.async = true
    s.onload = () => resolve()
    s.onerror = () => reject(new Error('高德 JS API 加载失败'))
    document.head.appendChild(s)
  })
}

/** 交互地图：需要「Web端(JS API)」key + 安全密钥，由后端 /api/config/map 下发。 */
async function initInteractive() {
  const cfg = await fetch('/api/config/map').then((r) => r.json())
  if (!cfg.configured) {
    mode.value = 'static'
    note.value = '未配置 JS API Key，已降级为静态地图'
    return
  }
  ;(window as any)._AMapSecurityConfig = { securityJsCode: cfg.security_code }
  await loadScript(`https://webapi.amap.com/maps?v=2.0&key=${encodeURIComponent(cfg.key)}`)
  amap = (window as any).AMap
  if (!mapEl.value) return
  map = new amap.Map(mapEl.value, { zoom: 11, viewMode: '2D' })
  mode.value = 'interactive'
  draw()
}

function draw() {
  if (!amap || !map) return
  overlays.forEach((o) => map.remove(o))
  overlays = []

  const pts = pointsOf(props.days[currentDay.value])
  if (!pts.length) return

  overlays = pts.map(
    (p, i) =>
      new amap.Marker({
        position: [p.lng, p.lat],
        label: {
          content: `${i + 1}. ${p.name}`,
          // 标签左右交错：景点密集时全放右边会互相压住
          direction: i % 2 === 0 ? 'right' : 'left',
        },
        title: p.name,
        // 景点常只隔一两百米，zoom 小时会完全重叠；
        // 序号小的压在上面，保证"第 1 个"不会被后一个盖住。
        zIndex: 300 - i * 10,
      })
  )
  map.add(overlays)

  if (pts.length > 1) {
    const line = new amap.Polyline({
      path: pts.map((p) => [p.lng, p.lat]),
      strokeColor: '#2f6bff',
      strokeWidth: 4,
      strokeOpacity: 0.8,
      showDir: true,
      zIndex: 50,
    })
    map.add(line)
    overlays.push(line)
    // 给 60px 边距并把最大缩放提到 16：只用 setFitView(overlays) 时，
    // 密集点会让地图停在低 zoom，几个标记叠在一起，第一个就被遮住了。
    map.setFitView(overlays, false, [60, 60, 60, 60], 16)
  } else {
    map.setZoomAndCenter(15, [pts[0].lng, pts[0].lat])
  }
}

watch(currentDay, () => {
  if (mode.value === 'interactive') draw()
})

onMounted(async () => {
  if (!props.days.length) {
    mode.value = 'empty'
    return
  }
  try {
    await initInteractive()
  } catch (e: any) {
    mode.value = 'static'
    note.value = `交互地图不可用（${e?.message ?? e}），已降级`
  }
})

onBeforeUnmount(() => {
  if (map) {
    map.destroy()
    map = null
  }
})
</script>

<template>
  <section class="card map-live">
    <h2>🗺️ 地图</h2>

    <div class="actions" style="margin: 0 0 10px">
      <button
        v-for="(d, i) in days"
        :key="i"
        class="btn-ghost"
        :style="i === currentDay ? 'background: var(--brand-soft); color: var(--brand)' : ''"
        style="padding: 6px 12px"
        @click="currentDay = i"
      >
        第 {{ i + 1 }} 天
      </button>
    </div>

    <div v-show="mode === 'interactive'" ref="mapEl" style="width: 100%; height: 320px; border-radius: 10px; overflow: hidden" />

    <p v-if="mode === 'static' || mode === 'empty'" class="desc">
      {{ note || '这一天没有带坐标的景点。' }}
      <template v-if="mode === 'static'">
        交互地图需要「Web端(JS API)」Key；导出图片时也会自动用静态地图。
      </template>
    </p>
  </section>
</template>
