// 零构建版前端：原生 JS，无框架、无打包。
//
// 设计要点
// --------
// 1. **状态驱动 + 事件委托**：编辑时不重渲染整个结果区，只更新 state 并就地改
//    几个数字。否则每敲一个字都会重建 DOM，输入框会失焦——这是手写编辑功能最
//    常见的坑。
// 2. **预算由前端重算**：模型给的总额可能和分项对不上，编辑后更不可能对得上。
//    所以门票/餐饮/住宿按明细求和，交通沿用模型估算（没法从明细推导）。
// 3. **地图随编辑重绘**：改顺序/删景点后重画 marker 与折线，否则地图和列表对不上。

const MEAL_LABEL = { breakfast: '早餐', lunch: '午餐', dinner: '晚餐', snack: '加餐' };
const MEAL_ORDER = ['breakfast', 'lunch', 'dinner', 'snack'];

const $ = (id) => document.getElementById(id);

// ------------------------------------------------------------ 用户标识 --
// 每个浏览器一个稳定 id：后端据此分区存行程、记偏好。
// 不做登录——这是"开放给别人用"的最小版本：能隔离，但不认证。
function getUserId() {
  const KEY = 'trip.user';
  try {
    let id = localStorage.getItem(KEY);
    if (!id || !/^[A-Za-z0-9_-]{3,40}$/.test(id)) {
      id = 'u-' + Math.random().toString(36).slice(2, 10);
      localStorage.setItem(KEY, id);
    }
    return id;
  } catch {
    return 'anonymous';
  }
}

const USER_ID = getUserId();

/** 所有 /api 请求都带上用户，后端用它做隔离。 */
function apiFetch(url, opts = {}) {
  const headers = { ...(opts.headers || {}), 'X-User-Id': USER_ID };
  return fetch(url, { ...opts, headers });
}
const escapeHtml = (s) =>
  String(s ?? '').replace(
    /[&<>"']/g,
    (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]
  );

// 纯逻辑（预算重算、排序、校验）放在 logic.js，浏览器和 Node 都能加载，
// 这样这些最容易算错的部分有单元测试兜着（tests/web_logic.test.cjs）。
const L = window.TripLogic;
const { money, isPlaceholderName, validate } = L;

// ------------------------------------------------------------------ 状态 --
const HISTORY_LIMIT = 60;

const state = {
  plan: null,
  markdown: '',
  elapsed: 0,
  errors: [],
  editing: false,
  snapshot: null, // 进入编辑态时的原始数据，用于「撤销改动」
  activeDay: 0,
  // 撤销/重做：存 JSON 字符串，避免引用同一对象被后续修改污染
  history: { past: [], future: [] },
  focusSnapshot: null, // 输入框获得焦点时的数据，失焦且有变化才入栈
  tripId: null, // 服务端行程 id；为 null 表示还没保存过
  tripTitle: '',
  cityResults: [],
  cityActive: -1,
  citySuppressUntil: 0,
  hiddenCities: [],
  routing: {},
  replan: { open: false, pending: null, selected: [], requirement: '' },
  packing: {},
  budgetCheck: {},
  share: null,
  readonly: false,
  toastTimer: null,
  cityQuery: '',
  saveState: 'idle', // idle | saving | saved | error
  saveTimer: null,
  recent: [], // 最近行程列表
};

// ------------------------------------------------------------------ 工具 --
function isoDate(offsetDays) {
  const d = new Date();
  d.setDate(d.getDate() + offsetDays);
  return d.toISOString().slice(0, 10);
}

function syncDays() {
  const a = Date.parse($('start_date').value);
  const b = Date.parse($('end_date').value);
  if (Number.isNaN(a) || Number.isNaN(b) || b < a) return;
  $('days').value = Math.round((b - a) / 86400000) + 1;
}

function initDates() {
  $('start_date').value = isoDate(1);
  $('end_date').value = isoDate(3);
  syncDays();
  $('start_date').addEventListener('change', syncDays);
  $('end_date').addEventListener('change', syncDays);
}

function collectRequest() {
  return {
    city: $('city').value.trim(),
    start_date: $('start_date').value,
    end_date: $('end_date').value,
    days: Number($('days').value) || 1,
    preferences: $('preferences').value,
    budget_level: $('budget_level').value,
    budget_total: parseBudget(),
    notes: ($('notes')?.value || '').trim(),
    transportation: $('transportation').value,
    accommodation: $('accommodation').value,
  };
}

// ------------------------------------------------------------------ 预算 --
// 重算逻辑在 logic.js（有单测），这里只做一层转发，避免两处实现分叉。
function recomputeBudget(plan) {
  return L.recomputeBudget(plan);
}

// ------------------------------------------------------------------ 地图 --
let amapLib = null;
let mapCtx = { map: null, overlays: [] };

function loadScript(src) {
  return new Promise((resolve, reject) => {
    const s = document.createElement('script');
    s.src = src;
    s.async = true;
    s.onload = () => resolve();
    s.onerror = () => reject(new Error('脚本加载失败'));
    document.head.appendChild(s);
  });
}

async function ensureAmap() {
  if (amapLib) return amapLib;
  const cfg = await fetch('/api/config/map').then((r) => r.json());
  if (!cfg.configured) return null;
  window._AMapSecurityConfig = { securityJsCode: cfg.security_code };
  await loadScript(`https://webapi.amap.com/maps?v=2.0&key=${encodeURIComponent(cfg.key)}`);
  amapLib = window.AMap;
  return amapLib;
}

function dayPoints(day) {
  return (day?.attractions ?? [])
    .filter((a) => a.location && a.location.longitude != null)
    .map((a) => ({ lng: a.location.longitude, lat: a.location.latitude, name: a.name }));
}

async function renderMap() {
  const host = $('map-host');
  if (!host || !state.plan) return;
  const day = state.plan.days[state.activeDay] ?? state.plan.days[0] ?? { attractions: [] };
  const points = dayPoints(day);

  if (!points.length) {
    host.innerHTML = '<div class="empty">这一天还没有带坐标的景点，添加景点后地图会自动出现。</div>';
    return;
  }

  const AMap = await ensureAmap().catch(() => null);

  if (!AMap) {
    // 没拿到 JS Key（或域名不在白名单）时降级成静态图。
    // 但**必须说出来**：否则用户以为"地图坏了"，排查时也看不出是配置问题。
    host.innerHTML =
      '<img id="static-map" class="zoomable" alt="行程地图" ' +
      'style="width:100%;max-width:100%;display:block;border-radius:10px" />' +
      '<p class="map-note">交互地图不可用（未配置 JS Key 或域名未加白），' +
      '已改用静态地图；打印 / 存 PDF 用的也是它。</p>';
    await setStaticMap(points);
    return;
  }

  if (!mapCtx.map || !document.getElementById('amap')) {
    host.innerHTML = '<div id="amap" style="width:100%;height:320px;border-radius:10px;overflow:hidden"></div>';
    mapCtx.map = new AMap.Map('amap', { zoom: 11, viewMode: '2D' });
  }
  drawDay(day, AMap, mapCtx.map);
}

async function setStaticMap(points) {
  const img = document.getElementById('static-map');
  if (!img) return;
  const resp = await fetch('/api/staticmap', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      points: points.slice(0, 10).map((p) => ({ lng: p.lng, lat: p.lat })),
      zoom: 11,
    }),
  });
  if (!resp.ok) return;
  img.src = URL.createObjectURL(await resp.blob());
}

function drawDay(day, AMap, map) {
  map.remove(mapCtx.overlays);
  mapCtx.overlays = [];

  const pts = dayPoints(day);
  if (!pts.length) return;

  const markers = pts.map((p, i) => {
    return new AMap.Marker({
      position: [p.lng, p.lat],
      label: {
        content: `${i + 1}. ${p.name || '未命名'}`,
        direction: i % 2 === 0 ? 'right' : 'left',
      },
      title: p.name,
      // 景点常只隔一两百米，zoom 小时会完全重叠；序号小的压在上面
      zIndex: 300 - i * 10,
    });
  });
  mapCtx.overlays.push(...markers);
  map.add(markers);

  if (pts.length > 1) {
    const line = new AMap.Polyline({
      path: pts.map((p) => [p.lng, p.lat]),
      strokeColor: '#2f6bff',
      strokeWidth: 4,
      strokeOpacity: 0.85,
      showDir: true,
      zIndex: 50,
    });
    mapCtx.overlays.push(line);
    map.add(line);
    // 60px 边距 + maxZoom 16：密集点也能分开（zoom 11 时 100 米只有 1.7 像素）
    map.setFitView(markers, false, [60, 60, 60, 60], 16);
  } else {
    map.setZoomAndCenter(15, [pts[0].lng, pts[0].lat]);
  }
}

// ------------------------------------------------------------------ 渲染 --
// ---------------------------------------------------------- 撤销 / 重做 --
function pushHistory() {
  if (!state.plan) return;
  state.history.past.push(JSON.stringify(state.plan));
  if (state.history.past.length > HISTORY_LIMIT) state.history.past.shift();
  state.history.future.length = 0; // 新操作会作废"重做"
}

function canUndo() {
  return state.history.past.length > 0;
}
function canRedo() {
  return state.history.future.length > 0;
}

function undo() {
  if (!canUndo()) return;
  state.history.future.push(JSON.stringify(state.plan));
  state.plan = JSON.parse(state.history.past.pop());
  afterHistoryJump();
}

function redo() {
  if (!canRedo()) return;
  state.history.past.push(JSON.stringify(state.plan));
  state.plan = JSON.parse(state.history.future.pop());
  afterHistoryJump();
}

function afterHistoryJump() {
  state.editing = true; // 撤销后停在编辑态，方便继续改
  renderToolbar();
  renderResult();
  scheduleSave();
}

// ------------------------------------------------------------ 持久化 --
const SAVE_DEBOUNCE_MS = 1200;

/** 把行程写成服务端可接受的形状（去掉前端临时字段）。 */
function planPayload() {
  return JSON.parse(JSON.stringify(state.plan));
}

function setSaveState(next) {
  state.saveState = next;
  renderToolbar();
}

async function persistNow() {
  if (!state.plan) return;
  setSaveState('saving');
  const body = {
    plan: planPayload(),
    title: state.tripTitle || '',
    markdown: L.toMarkdown(state.plan),
  };
  const isUpdate = Boolean(state.tripId);
  try {
    const resp = await apiFetch(isUpdate ? `/api/trips/${state.tripId}` : '/api/trips', {
      method: isUpdate ? 'PUT' : 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const data = await resp.json();
    if (data?.trip?.id) {
      state.tripId = data.trip.id;
      state.tripTitle = data.trip.title || state.tripTitle;
    }
    setSaveState('saved');
    loadRecent();
  } catch (e) {
    setSaveState('error');
    console.warn('保存失败', e);
  }
}

/** 改动后延迟保存：避免每敲一个字都发一次请求。 */
function scheduleSave() {
  if (!state.plan) return;
  clearTimeout(state.saveTimer);
  state.saveTimer = setTimeout(() => {
    persistNow();
  }, SAVE_DEBOUNCE_MS);
}

async function loadRecent() {
  const host = $('recent-host');
  if (!host) return;
  try {
    const resp = await apiFetch('/api/trips');
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    state.recent = (await resp.json()).items ?? [];
  } catch {
    state.recent = [];
  }
  renderRecent();
}

function renderRecent() {
  const host = $('recent-host');
  if (!host) return;
  if (!state.recent.length) {
    host.innerHTML = '';
    return;
  }
  host.innerHTML = `
    <section class="card">
      <h2>🗂️ 已保存的行程 <span class="chip">${state.recent.length}</span></h2>
      <div class="recent">
        ${state.recent
          .map(
            (t) => `<div class="recent-item${t.id === state.tripId ? ' current' : ''}">
              <button class="recent-open" data-act="trip-open" data-id="${escapeHtml(t.id)}">
                <b>${escapeHtml(t.title || t.city)}</b>
                <span class="meta">${escapeHtml(t.start_date)} ~ ${escapeHtml(t.end_date)} · ${t.days} 天 · ${t.total} 元</span>
              </button>
              <span class="meta">${escapeHtml((t.updated_at || '').replace('T', ' ').slice(0, 16))}</span>
              <button class="btn-icon danger" data-act="trip-del" data-id="${escapeHtml(t.id)}" title="删除">✕</button>
            </div>`
          )
          .join('')}
      </div>
    </section>`;
  syncEmptyLayout();
}

async function openTrip(id) {
  try {
    const resp = await apiFetch(`/api/trips/${id}`);
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const record = await resp.json();
    state.plan = record.plan;
    state.tripId = record.id;
    state.tripTitle = record.title || '';
    state.history = { past: [], future: [] };
    state.editing = false;
    state.activeDay = 0;
    state.errors = [];
    recomputeBudget(state.plan);
    $('form-card')?.classList.add('collapsed');
    renderToolbar();
    renderResult();
    renderRecent();
    setSaveState('saved');
    $('toolbar')?.scrollIntoView?.({ behavior: 'smooth', block: 'start' });
  } catch (e) {
    showError(`打开行程失败：${e.message}`);
  }
}

async function deleteTrip(id) {
  try {
    const resp = await apiFetch(`/api/trips/${id}`, { method: 'DELETE' });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    if (state.tripId === id) {
      state.tripId = null;
      state.saveState = 'idle';
    }
    await loadRecent();
  } catch (e) {
    showError(`删除失败：${e.message}`);
  }
}

function renderToolbar() {
  const t = $('toolbar');
  if (!t) return;
  if (!state.plan) {
    t.hidden = true;
    return;
  }
  t.hidden = false;

  const saveLabel =
    state.saveState === 'saving'
      ? '保存中…'
      : state.saveState === 'saved'
        ? '✓ 已保存'
        : state.saveState === 'error'
          ? '⚠ 保存失败'
          : state.tripId
            ? ''
            : '未保存';

  const undoBtn = `<button class="btn-ghost btn-sm" data-act="undo" ${canUndo() ? '' : 'disabled'} title="Ctrl+Z">↶ 撤销</button>`;
  const redoBtn = `<button class="btn-ghost btn-sm" data-act="redo" ${canRedo() ? '' : 'disabled'} title="Ctrl+Shift+Z">↷ 重做</button>`;

  // 只读分享模式下，所有"能改东西"的按钮**根本不渲染**（不是 CSS 藏起来）——
  // 藏起来但 DOM 里还在，等于给了一条看不见的路径，读屏和键盘照样能碰到。
  const ownerActions = state.readonly
    ? ''
    : `${undoBtn}${redoBtn}
       <button class="btn-primary btn-sm" data-act="edit">✏️ 编辑行程</button>
       <button class="btn-ghost btn-sm" data-act="replan-open" ${state.tripId ? '' : 'disabled'}
               title="${state.tripId ? '只重排指定的几天' : '先保存一次才能用 AI 重排'}">🤖 AI 重排</button>
       <button class="btn-ghost btn-sm" data-act="save">保存</button>`;
  const ownerTail = state.readonly
    ? ''
    : `<span class="spacer"></span>
       <span class="hint">${saveLabel}</span>
       <button class="btn-ghost btn-sm" data-act="restart">重新规划</button>`;

  t.innerHTML = state.editing
    ? `${undoBtn}${redoBtn}
       <button class="btn-primary btn-sm" data-act="save-edit">完成编辑</button>
       <button class="btn-ghost btn-sm" data-act="cancel-edit">放弃全部改动</button>
       <span class="spacer"></span>
       <span class="hint">${saveLabel}</span>
       <button class="btn-ghost btn-sm" data-act="print">打印 / 存为 PDF</button>`
    : `${ownerActions}
       <button class="btn-ghost btn-sm" data-act="print">打印 / 存为 PDF</button>
       <button class="btn-ghost btn-sm" data-act="copy-md">复制 Markdown</button>
       ${
         state.readonly
           ? ''
           : `<button class="btn-ghost btn-sm" data-act="export-ics" ${state.tripId ? '' : 'disabled'}
               title="${state.tripId ? '导出 .ics，可导入手机日历' : '先保存一次才能导出'}">📅 日历</button>
       <button class="btn-ghost btn-sm" data-act="share" ${state.tripId ? '' : 'disabled'}
               title="${state.tripId ? '生成只读链接发给别人' : '先保存一次才能分享'}">🔗 分享</button>`
       }
       ${ownerTail}`;
}

/** 预算输入：空串或非法一律当"不限"（null），不要变成 0。 */
function parseBudget() {
  const el = $('budget_total');
  if (!el) return null;
  const raw = String(el.value || '').trim();
  if (!raw) return null;
  const num = Number(raw);
  if (!Number.isFinite(num) || num <= 0) return null;
  return Math.round(num);
}

// ------------------------------------------------------- 导出/分享/清单 --
/** 导出 .ics：不能用 window.open（带不了 X-User-Id 头），改用 fetch + blob 下载。 */
async function exportIcs() {
  if (!state.tripId) return;
  try {
    const resp = await apiFetch(`/api/trips/${encodeURIComponent(state.tripId)}/export.ics`);
    if (!resp.ok) {
      toast(`导出失败：HTTP ${resp.status}`);
      return;
    }
    const blob = await resp.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    // 文件名从 Content-Disposition 里取，取不到就用兜底名
    const cd = resp.headers?.get?.('content-disposition') || '';
    const star = cd.match(/filename\*=UTF-8''([^;]+)/i);
    a.download = star ? decodeURIComponent(star[1]) : 'trip.ics';
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 4000);
    toast('已开始下载 .ics');
  } catch (err) {
    toast(`导出失败：${err}`);
  }
}

/** 分享：生成只读链接，直接显示出来让人复制（不用弹窗组件，省事且可测）。 */
async function shareTrip() {
  if (!state.tripId) return;
  try {
    const resp = await apiFetch(`/api/trips/${encodeURIComponent(state.tripId)}/share`, {
      method: 'POST',
    });
    if (!resp.ok) {
      toast(`生成链接失败：HTTP ${resp.status}`);
      return;
    }
    const data = await resp.json();
    state.share = { token: data.token, url: `${location.origin}/?share=${data.token}` };
    renderShareBox();
  } catch (err) {
    toast(`生成链接失败：${err}`);
  }
}

function renderShareBox() {
  const host = $('share-host');
  if (!host) return;
  if (!state.share) {
    host.hidden = true;
    host.innerHTML = '';
    return;
  }
  host.hidden = false;
  host.innerHTML = `<section class="card no-print share-card">
    <h2>🔗 只读分享链接</h2>
    <p class="share-tip">拿到这个链接的人能看到行程，但改不了，也看不到你的用户信息。</p>
    <div class="share-row">
      <input id="share-url" readonly value="${escapeHtml(state.share.url)}" />
      <button class="btn-primary btn-sm" data-act="copy-share">复制链接</button>
      <button class="btn-ghost btn-sm" data-act="close-share">收起</button>
    </div>
  </section>`;
}

/**
 * 复制文本：三级兜底，返回用了哪一级（'clipboard' / 'execCommand' / 'manual'）。
 *
 * 为什么不能只调 navigator.clipboard：它在非安全上下文、iframe、或权限被拒时都会 reject，
 * 而拒绝的原因是"环境不允许"，不是"代码写错了"——所以要有后备路径，也要能把原因说出来。
 */
async function copyText(text) {
  const value = String(text ?? '');

  // ① 现代剪贴板 API：需要安全上下文 + 权限
  if (navigator.clipboard && navigator.clipboard.writeText && window.isSecureContext !== false) {
    try {
      await navigator.clipboard.writeText(value);
      return 'clipboard';
    } catch {
      /* 落到下一级 */
    }
  }

  // ② 临时 textarea + execCommand：老办法，但兼容性最好
  try {
    const ta = document.createElement('textarea');
    ta.value = value;
    ta.setAttribute('readonly', '');
    ta.style.position = 'fixed';
    ta.style.top = '-9999px';
    ta.style.opacity = '0';
    document.body.appendChild(ta);
    ta.select();
    const ok = typeof document.execCommand === 'function' && document.execCommand('copy');
    ta.remove();
    if (ok) return 'execCommand';
  } catch {
    /* 落到下一级 */
  }

  // ③ 都不行：摊出来让人手动复制
  showCopyFallback(value);
  return 'manual';
}

/** 手动复制面板：不是"失败提示"，是给出可操作的东西。 */
function showCopyFallback(text) {
  const host = $('copy-fallback');
  if (!host) return;
  host.hidden = false;
  host.innerHTML = `<section class="card no-print copy-fallback-card">
    <h2>⚠ 浏览器不允许自动复制</h2>
    <p class="copy-tip">这通常是因为页面在 iframe 里、或不是 https/localhost。
      下面是完整内容，选中后按 <b>Ctrl+C</b> 复制，或点「全选」。</p>
    <textarea id="copy-fallback-text" rows="10" readonly>${escapeHtml(text)}</textarea>
    <div class="copy-actions">
      <button class="btn-primary btn-sm" data-act="copy-fallback-select">全选</button>
      <button class="btn-ghost btn-sm" data-act="copy-fallback-close">收起</button>
    </div>
  </section>`;
  toast('无法自动复制，已在下方列出内容');
}

function toast(text) {
  const el = $('toast');
  if (!el) return;
  el.textContent = text;
  el.hidden = false;
  clearTimeout(state.toastTimer);
  state.toastTimer = setTimeout(() => {
    el.hidden = true;
  }, 2200);
}

/** 预算校验：只在上限被超出时出现在结果顶部，说清"超多少、从哪减"。 */
function budgetCheckHtml(check) {
  if (!check || !check.limit || check.ok !== false) return '';
  return `<section class="card no-print budget-warn">
    <h2>⚠ 超出预算 ${escapeHtml(String(check.over))} 元</h2>
    <p>${escapeHtml(check.message || '')}</p>
    <p class="budget-note">合计 ${escapeHtml(String(check.total))} 元 / 上限
      ${escapeHtml(String(check.limit))} 元。</p>
  </section>`;
}

/** 装备清单：纯规则算出来的，所以每条都带原因。 */
function packingHtml(packing) {
  const groups = (packing && packing.groups) || [];
  if (!groups.length) return '';
  const blocks = groups
    .map(
      (g) =>
        `<div class="pack-group"><h3>${escapeHtml(g.title)}</h3><ul>` +
        g.items
          .map(
            (it) =>
              `<li><span class="pack-name">${escapeHtml(it.name)}</span>` +
              `<span class="pack-why">${escapeHtml(it.reason || '')}</span></li>`,
          )
          .join('') +
        `</ul></div>`,
    )
    .join('');
  const summary = packing.summary ? `<span class="chip">${escapeHtml(packing.summary)}</span>` : '';
  return `<section class="card packing-card"><h2>🎒 装备清单 ${summary}</h2>
    <div class="pack-grid">${blocks}</div>
    <p class="budget-note">清单按天气、天数、交通与住宿推出来的（规则引擎，不是模型写的）。</p>
  </section>`;
}

// ---------------------------------------------------------- AI 重排 --
// 面板状态放在 state.replan 里，重渲染不会丢（用户勾了一半天不能因为别的事件没了）
function replanDaysHtml() {
  const days = state.plan?.days || [];
  return days
    .map((d, i) => {
      const checked = (state.replan.selected || []).includes(i) ? 'checked' : '';
      const names = (d.attractions || []).map((a) => a.name).join('、') || '（空）';
      return `<label class="replan-day">
        <input type="checkbox" data-bind="replan-day" value="${i}" ${checked} />
        <span class="replan-day-name">第 ${i + 1} 天</span>
        <span class="replan-day-sub">${escapeHtml(d.date)} · ${escapeHtml(names)}</span>
      </label>`;
    })
    .join('');
}

function replanPreviewHtml() {
  const pending = state.replan.pending;
  if (!pending) return '';
  const rows = (pending.preview?.days || [])
    .map(
      (d) =>
        `<div class="replan-diff">
          <div class="replan-diff-head">第 ${d.day_index + 1} 天（${escapeHtml(d.date)}）</div>
          <div class="replan-diff-row"><span class="before">${escapeHtml(d.before.join('、') || '（空）')}</span>
            <span class="arrow">→</span>
            <span class="after">${escapeHtml(d.after.join('、') || '（空）')}</span></div>
          ${
            d.hotel_before !== d.hotel_after
              ? `<div class="replan-diff-row"><span class="before">${escapeHtml(d.hotel_before || '（无住宿）')}</span>
                   <span class="arrow">→</span>
                   <span class="after">${escapeHtml(d.hotel_after || '（无住宿）')}</span></div>`
              : ''
          }
        </div>`,
    )
    .join('');
  return `<div class="replan-preview">
    <p class="replan-tip">将这样改（其它天保持不变）：</p>
    ${rows || '<p class="replan-tip">没有可展示的改动</p>'}
    <div class="replan-actions">
      <button class="btn-primary btn-sm" data-act="replan-apply">确认修改</button>
      <button class="btn-ghost btn-sm" data-act="replan-cancel">取消</button>
    </div>
  </div>`;
}

function renderReplanPanel() {
  const host = $('replan-host');
  if (!host) return;
  if (!state.replan.open && !state.replan.pending) {
    host.innerHTML = '';
    host.hidden = true;
    return;
  }
  host.hidden = false;

  if (state.replan.pending) {
    host.innerHTML = `<section class="card replan-card no-print">
      <h2>🤖 AI 重排 · 待确认</h2>
      ${replanPreviewHtml()}
    </section>`;
    return;
  }

  const selected = (state.replan.selected || []).length;
  host.innerHTML = `<section class="card replan-card no-print">
    <h2>🤖 AI 重排</h2>
    <p class="replan-tip">勾选要重排的天，写一句要求；<b>没勾的天一个字都不会变</b>。</p>
    <div class="replan-days">${replanDaysHtml()}</div>
    <textarea id="replan-req" rows="2" placeholder="例如：这两天要下雨，全换成室内场所">${escapeHtml(state.replan.requirement || '')}</textarea>
    <div class="replan-actions">
      <button class="btn-primary btn-sm" data-act="replan-submit" ${selected ? '' : 'disabled'}>
        重排选中的 ${selected} 天
      </button>
      <button class="btn-ghost btn-sm" data-act="replan-close">收起</button>
      <span class="hint" id="replan-status"></span>
    </div>
  </section>`;
}

function openReplan() {
  if (!state.plan || !state.tripId) return;
  state.replan.open = true;
  state.replan.pending = null;
  renderReplanPanel();
}

function closeReplan() {
  state.replan.open = false;
  state.replan.pending = null;
  renderReplanPanel();
}

function toggleReplanDay(idx, checked) {
  const set = new Set(state.replan.selected || []);
  if (checked) set.add(idx);
  else set.delete(idx);
  state.replan.selected = [...set].sort((a, b) => a - b);
  renderReplanPanel();
}

function setReplanStatus(text) {
  const el = $('replan-status');
  if (el) el.textContent = text;
}

async function submitReplan() {
  const selected = state.replan.selected || [];
  if (!selected.length || !state.tripId) return;
  const req = $('replan-req')?.value ?? '';
  state.replan.requirement = req;
  setReplanStatus('正在请模型重排…');

  try {
    const resp = await apiFetch(`/api/trips/${encodeURIComponent(state.tripId)}/replan`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ selected_days: selected, requirement: req }),
    });
    const data = await resp.json().catch(() => ({}));
    if (!resp.ok) {
      // 409 = 还有没确认的会话；422 = 模型没守住局部性
      setReplanStatus(`失败：${data.detail || resp.status}`);
      return;
    }
    state.replan.pending = data;
    renderReplanPanel();
  } catch (err) {
    setReplanStatus(`失败：${err}`);
  }
}

async function applyReplan() {
  if (!state.replan.pending || !state.tripId) return;
  try {
    const resp = await apiFetch(`/api/trips/${encodeURIComponent(state.tripId)}/replan/confirm`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ approve: true }),
    });
    const data = await resp.json().catch(() => ({}));
    if (!resp.ok) {
      setReplanStatus(`失败：${data.detail || resp.status}`);
      return;
    }
    // 落地：进撤销栈 → 换计划 → 重渲染
    pushHistory();
    state.plan = data.plan;
    state.markdown = data.markdown || '';
    state.replan = { open: false, pending: null, selected: [], requirement: '' };
    renderToolbar();
    renderResult();
    renderReplanPanel();
    scheduleSave();
  } catch (err) {
    setReplanStatus(`失败：${err}`);
  }
}

async function cancelReplan() {
  if (!state.tripId) return;
  try {
    await apiFetch(`/api/trips/${encodeURIComponent(state.tripId)}/replan/confirm`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ approve: false }),
    });
  } catch {
    /* 取消失败无所谓：前端照样收起 */
  }
  closeReplan();
}

function renderBudget() {
  const host = $('budget-host');
  if (!host || !state.plan) return;
  const b = recomputeBudget(state.plan);
  const mode = state.plan.days?.[0]?.transportation || '市内';
  const cell = (label, v, total) =>
    `<div class="cell${total ? ' total' : ''}"><span>${label}</span><strong>${v}</strong></div>`;
  host.innerHTML = `
    <section class="card">
      <h2>💰 预算估算</h2>
      <div class="budget">
        ${cell('景点门票', b.total_attractions)}
        ${cell('酒店住宿', b.total_hotels)}
        ${cell('餐饮', b.total_meals)}
        ${cell(`交通（${escapeHtml(mode)}）`, b.total_transportation)}
        ${cell('合计（元）', b.total, true)}
      </div>
      <p class="budget-note">
        门票/餐饮/住宿按明细实时汇总；交通沿用模型估算（只算市内：自驾为油费 + 停车费，不含过路费与租车费）。
      </p>
    </section>`;
}

function attractionHtml(day, d, i, editing) {
  const a = day.attractions[i];
  const thumb = a.image_url
    ? `<img class="${editing ? 'thumb-lg' : 'thumb'} zoomable" src="${escapeHtml(a.image_url)}"
             data-large="${escapeHtml(a.image_url_large || a.image_url)}"
             alt="${escapeHtml(a.name)}" loading="lazy" />`
    : '';

  if (editing) {
    return `<div class="row" draggable="true" data-drag-day="${d}" data-drag-idx="${i}">
      <span class="grip" title="拖拽调整顺序">⋮⋮</span>
      ${thumb}
      <span class="rank">${i + 1}</span>
      <div class="body">
        <div class="edit-row three">
          <input data-bind="attraction" data-day="${d}" data-idx="${i}" data-field="name"
                 value="${escapeHtml(a.name)}" placeholder="景点名称" />
          <input data-bind="attraction" data-day="${d}" data-idx="${i}" data-field="visit_duration"
                 type="number" min="10" step="15" value="${money(a.visit_duration)}" title="游览分钟" />
          <input data-bind="attraction" data-day="${d}" data-idx="${i}" data-field="ticket_price"
                 type="number" min="0" step="10" value="${money(a.ticket_price)}" title="门票" />
        </div>
        <div class="sub">第二列=游览分钟，第三列=门票（元）</div>
      </div>
      <div class="ops" style="opacity:1">
        <button class="btn-icon" data-act="att-up" data-day="${d}" data-idx="${i}" title="上移">↑</button>
        <button class="btn-icon" data-act="att-down" data-day="${d}" data-idx="${i}" title="下移">↓</button>
        <button class="btn-icon danger" data-act="att-del" data-day="${d}" data-idx="${i}" title="删除">✕</button>
      </div>
    </div>`;
  }

  const coord = a.location ? ` · ${a.location.longitude},${a.location.latitude}` : '';
  return `<div class="row">
    ${thumb}
    <span class="rank">${i + 1}</span>
    <div class="body">
      <div class="title">${escapeHtml(a.name)}</div>
      <div class="sub">${money(a.visit_duration)} 分钟 · ${money(a.ticket_price) > 0 ? money(a.ticket_price) + ' 元' : '免费'}${coord}</div>
    </div>
    <div class="ops">
      <button class="btn-icon" data-act="att-up" data-day="${d}" data-idx="${i}" title="上移">↑</button>
      <button class="btn-icon" data-act="att-down" data-day="${d}" data-idx="${i}" title="下移">↓</button>
      <button class="btn-icon danger" data-act="att-del" data-day="${d}" data-idx="${i}" title="删除">✕</button>
    </div>
  </div>`;
}

function mealHtml(day, d, i, editing) {
  const m = day.meals[i];
  const label = escapeHtml(MEAL_LABEL[m.type] ?? m.type);
  if (editing) {
    return `<div class="row">
      <span class="rank tag-meal">${label}</span>
      <div class="body">
        <div class="edit-row">
          <input data-bind="meal" data-day="${d}" data-idx="${i}" data-field="name"
                 value="${escapeHtml(m.name)}" placeholder="餐厅或菜品" />
          <input data-bind="meal" data-day="${d}" data-idx="${i}" data-field="estimated_cost"
                 type="number" min="0" step="10" value="${money(m.estimated_cost)}" title="人均（元）" />
        </div>
      </div>
      <div class="ops" style="opacity:1">
        <button class="btn-icon danger" data-act="meal-del" data-day="${d}" data-idx="${i}" title="删除">✕</button>
      </div>
    </div>`;
  }
  return `<div class="row">
    <span class="rank tag-meal">${label}</span>
    <div class="body">
      <div class="title">${escapeHtml(m.name)}</div>
      <div class="sub">${money(m.estimated_cost)} 元</div>
    </div>
    <div class="ops">
      <button class="btn-icon danger" data-act="meal-del" data-day="${d}" data-idx="${i}" title="删除">✕</button>
    </div>
  </div>`;
}

function hotelHtml(day, d, editing) {
  const h = day.hotel;
  const name = h?.name ?? '';
  if (editing) {
    return `<div class="row">
      <span class="rank tag-hotel">宿</span>
      <div class="body">
        <div class="edit-row">
          <input data-bind="hotel" data-day="${d}" data-field="name"
                 value="${escapeHtml(name)}" placeholder="酒店名（留空表示当天不住宿）" />
          <input data-bind="hotel" data-day="${d}" data-field="estimated_cost"
                 type="number" min="0" step="50" value="${money(h?.estimated_cost)}" title="元/晚" />
        </div>
      </div>
    </div>`;
  }
  if (isPlaceholderName(name)) return '';
  return `<div class="row">
    <span class="rank tag-hotel">宿</span>
    <div class="body">
      <div class="title">${escapeHtml(name)}</div>
      <div class="sub">${money(h?.estimated_cost)} 元/晚</div>
    </div>
  </div>`;
}

function dayHtml(day, d, editing) {
  const attrs = editing
    ? `<div class="edit-day">
         <input data-bind="day" data-day="${d}" data-field="description"
                value="${escapeHtml(day.description)}" placeholder="当天概述" />
         <select data-bind="day" data-day="${d}" data-field="transportation">
           ${['公共交通', '打车', '自驾', '步行']
             .map((t) => `<option ${t === day.transportation ? 'selected' : ''}>${t}</option>`)
             .join('')}
         </select>
       </div>`
    : day.description
      ? `<p class="day-desc">${escapeHtml(day.description)}</p>`
      : '';

  const attractions = (day.attractions ?? [])
    .map((_, i) => attractionHtml(day, d, i, editing))
    .join('');
  const meals = (day.meals ?? []).map((_, i) => mealHtml(day, d, i, editing)).join('');

  const addRow = editing
    ? `<div class="add-row">
         <button data-act="att-add" data-day="${d}">+ 添加景点</button>
         <button data-act="meal-add" data-day="${d}">+ 添加餐饮</button>
       </div>`
    : '';

  return `<article class="day" data-day="${d + 1}">
    <div class="day-head">
      <h3>第 ${d + 1} 天</h3>
      <span class="date">${escapeHtml(day.date)}</span>
      <span class="chip strong">${escapeHtml(day.transportation)}</span>
      <span class="chip">${(day.attractions ?? []).length} 个景点</span>
    </div>
    <div class="day-issues" data-day-issues="${d}"></div>
    ${attrs}
    ${hotelHtml(day, d, editing)}
    ${attractions || '<div class="empty">这天还没有景点</div>'}
    ${meals}
    ${addRow}
    <!-- 每天一张静态地图：打印/存 PDF 时不会只有第一天有图 -->
    ${editing ? '' : `<div class="day-map" data-day-map="${d}"></div>`}
  </article>`;
}

/**
 * 冲突检测结果按天渲染。
 *
 * 只更新 `[data-day-issues]` 这几个小容器，不重渲染整页——编辑时每敲一下都重建 DOM
 * 会让输入框失焦，这个坑在做编辑功能时已经踩过一次。
 */
function renderIssues() {
  if (!state.plan) return;
  const issues = L.detectConflicts(state.plan);
  const byDay = new Map();
  issues.forEach((it) => {
    if (!byDay.has(it.day)) byDay.set(it.day, []);
    byDay.get(it.day).push(it);
  });

  document.querySelectorAll('[data-day-issues]').forEach((host) => {
    const day = Number(host.dataset.dayIssues);
    const list = byDay.get(day) || [];
    if (!list.length) {
      host.innerHTML = '';
      return;
    }
    host.innerHTML = list
      .map(
        (it) =>
          `<div class="issue ${it.level}">${it.level === 'error' ? '⛔' : '⚠'} ${escapeHtml(it.message)}</div>`,
      )
      .join('');
  });
}

/**
 * 条件路由的展示。
 *
 * 三件事要说清楚：哪几天有雨、系统据此做了什么、模型有没有照做。
 * 最后一条最重要——约束进了提示词不等于模型会遵守，所以后端还会做确定性校验，
 * 有违规就在这里明说，而不是让用户自己发现"雨天排了公园"。
 */
function routingHtml(routing) {
  if (!routing || !routing.needed) return '';
  const days = routing.days || [];
  if (!days.length) return '';

  const rows = days
    .map(
      (d) =>
        `<li>${escapeHtml(d.date)}：<strong>${escapeHtml(d.weather || '')}</strong>` +
        ` — 已按室内场所安排 <span class="routing-reason">（${escapeHtml(d.reason || '')}）</span></li>`,
    )
    .join('');

  const violations = routing.violations || [];
  const warn = violations.length
    ? `<div class="routing-warn">模型没有完全遵守：<ul>${violations
        .map((v) => `<li>${escapeHtml(v.message || '')}</li>`)
        .join('')}</ul>建议手动把这些景点换成室内场所。</div>`
    : '';

  return `<section class="card no-print routing-card"><h2>☔ 天气调整</h2>
    <ul class="routing-list">${rows}</ul>${warn}</section>`;
}

function renderResult() {
  const host = $('result');
  if (!host || !state.plan) return;
  const p = state.plan;

  const routingCard = routingHtml(state.routing);

  const errors = (state.errors ?? []).length
    ? `<section class="card no-print"><h2>⚠️ 数据提示</h2>
         <div class="error" style="margin:0">${state.errors.map((e) => '· ' + escapeHtml(e)).join('<br>')}</div>
         <p class="budget-note">行程仍然可用；上面是本次没取到的高德数据。</p></section>`
    : '';

  const tabs = p.days
    .map(
      (d, i) =>
        `<button class="btn-ghost btn-sm" data-act="day-tab" data-day="${i}"
           style="${i === state.activeDay ? 'background:var(--brand-soft);color:var(--brand)' : ''}">第 ${i + 1} 天</button>`
    )
    .join(' ');

  host.innerHTML = `
    <div id="trip-plan-content" class="${state.editing ? 'editing' : ''}">
      <section class="card">
        <h2>📋 行程概览</h2>
        <div class="overview">
          <span>目的地 <b>${escapeHtml(p.city)}</b></span>
          <span>日期 <b>${escapeHtml(p.start_date)} ~ ${escapeHtml(p.end_date)}</b></span>
          <span>天数 <b>${p.days.length}</b></span>
          <span>生成耗时 <b>${(state.elapsed / 1000).toFixed(1)}s</b></span>
          ${state.editing ? '<span class="chip strong">编辑中</span>' : ''}
          ${memoryChipHtml()}
        </div>
        ${p.overall_suggestions ? `<p class="suggestion">${escapeHtml(p.overall_suggestions)}</p>` : ''}
        ${
          state.editing
            ? `<div class="edit-day" style="margin-top:12px">
                 <input data-bind="plan" data-field="city" value="${escapeHtml(p.city)}" placeholder="城市" />
                 <input data-bind="plan" data-field="overall_suggestions" value="${escapeHtml(p.overall_suggestions)}" placeholder="总体建议" />
               </div>`
            : ''
        }
      </section>

      <div id="budget-host"></div>

      <section class="card no-print">
        <h2>🗺️ 地图</h2>
        <div class="actions" style="margin:0 0 10px">${tabs}</div>
        <div id="map-host"></div>
        <p class="budget-note">这里是可缩放的地图，只在屏幕上显示；打印/存 PDF 会用每天的静态地图。</p>
      </section>

      ${budgetCheckHtml(state.budgetCheck)}
      ${packingHtml(state.packing)}
      ${routingCard}
    ${errors}

      <section class="card">
        <h2>📅 每日行程</h2>
        <div class="timeline">
          ${p.days.map((day, d) => dayHtml(day, d, state.editing)).join('')}
        </div>
      </section>

      ${weatherHtml(p.weather_info)}
    </div>`;

  renderBudget();
  renderMap();
  hydrateDayMaps();
  renderIssues();
}

/**
 * 给每天补一张静态地图。
 *
 * 交互地图只有一个实例，打印时只会印出当前选中的那天——用户实测"PDF 里只有第一天的
 * 地图"。所以每天各自嵌一张服务端出的静态图，打印/存 PDF 自然每天都全。
 */
async function hydrateDayMaps() {
  if (!state.plan || state.editing) return;

  const jobs = state.plan.days.map(async (day, d) => {
    const host = document.querySelector(`[data-day-map="${d}"]`);
    if (!host) return;
    const points = dayPoints(day);
    if (!points.length) {
      host.innerHTML = '<div class="empty" style="padding:12px">这一天没有带坐标的景点</div>';
      return;
    }
    const resp = await fetch('/api/staticmap', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      // 只传坐标。早先这里把 dayPoints() 的结果整体传了过去（含 name），
      // 后端按 dict[str, float] 解析 name 直接 422，页面上每张地图都成了"暂时取不到"。
      body: JSON.stringify({
        points: points.slice(0, 10).map((p) => ({ lng: p.lng, lat: p.lat })),
        zoom: 11,
      }),
    }).catch(() => null);

    if (!resp || !resp.ok) {
      // 把状态码写出来，别让排查只能靠猜
      const code = resp ? resp.status : 'network';
      host.innerHTML = `<div class="empty" style="padding:12px">地图暂时取不到（${code}）</div>`;
      return;
    }
    const url = URL.createObjectURL(await resp.blob());
    host.innerHTML = `<img class="day-map-img zoomable" src="${url}" alt="第 ${d + 1} 天地图" loading="lazy" />`;
  });

  await Promise.all(jobs);
}

function memoryChipHtml() {
  const hints = state.memory?.hints || {};
  const parts = [];
  if (hints.preferences) parts.push(hints.preferences[0]);
  if (hints.budget_level) parts.push(hints.budget_level[0]);
  if (!parts.length) return '';
  // 有历史偏好才显示：没有就别假装记得
  const used = state.memory?.used ? '已参考' : '已记住';
  return `<span class="chip" title="用户 ${escapeHtml(state.memory?.user || '')} 的历史偏好">${used}：${escapeHtml(parts.join(' · '))}</span>`;
}

function weatherHtml(list) {
  if (!list || !list.length) return '';

  const missing = L.missingWeatherDays(state.plan);
  const note = missing.length
    ? `<p class="weather-missing">还有 ${missing.length} 天没有预报（${missing
        .map((d) => escapeHtml(d))
        .join('、')}）——高德只提供今天起 4 天的天气，更远的日子查不到。</p>`
    : '';

  return `<section class="card"><h2>🌤️ 天气</h2><div class="weather">${list
    .map(
      (w) => `<div class="w"><b>${escapeHtml(w.date)}</b>
        <span>${escapeHtml(w.day_weather)} / ${escapeHtml(w.night_weather)}</span><br>
        <span>${w.day_temp}~${w.night_temp}°C${w.wind_direction ? ` · ${escapeHtml(w.wind_direction)}风 ${escapeHtml(w.wind_power)} 级` : ''}</span></div>`
    )
    .join('')}</div>${note}</section>`;
}

// -------------------------------------------------------------- 编辑操作 --
function beginEdit() {
  state.snapshot = JSON.parse(JSON.stringify(state.plan));
  state.editing = true;
  renderToolbar();
  renderResult();
}

function saveEdit() {
  state.editing = false;
  state.snapshot = null;
  renderToolbar();
  renderResult();
}

function cancelEdit() {
  if (state.snapshot) state.plan = state.snapshot;
  state.editing = false;
  state.snapshot = null;
  renderToolbar();
  renderResult();
}

function ensureHotel(day) {
  if (!day.hotel) day.hotel = { name: '', address: '', location: null, price_range: '', rating: '', type: '', estimated_cost: 0 };
  return day.hotel;
}

function applyBind(el) {
  const dayIdx = Number(el.dataset.day ?? -1);
  const idx = Number(el.dataset.idx ?? -1);
  const field = el.dataset.field;
  const kind = el.dataset.bind;
  const raw = el.value;
  const numeric = el.type === 'number';

  if (kind === 'plan') {
    state.plan[field] = raw;
    return;
  }
  const day = state.plan.days[dayIdx];
  if (!day) return;

  if (kind === 'day') {
    day[field] = raw;
    return;
  }
  if (kind === 'attraction') {
    const a = day.attractions[idx];
    if (!a) return;
    a[field] = numeric ? money(raw) : raw;
    return;
  }
  if (kind === 'meal') {
    const m = day.meals[idx];
    if (!m) return;
    m[field] = numeric ? money(raw) : raw;
    return;
  }
  if (kind === 'hotel') {
    const h = ensureHotel(day);
    h[field] = numeric ? money(raw) : raw;
  }
}

const ACTIONS = {
  edit: () => beginEdit(),
  'export-ics': () => exportIcs(),
  'share': () => shareTrip(),
  'copy-share': async () => {
    const v = state.share?.url || '';
    const how = await copyText(v);
    if (how === 'manual') return;
    toast('已复制链接');
  },
  'close-share': () => {
    state.share = null;
    renderShareBox();
  },
  'replan-open': () => openReplan(),
  'replan-close': () => closeReplan(),
  'replan-submit': () => submitReplan(),
  'replan-apply': () => applyReplan(),
  'replan-cancel': () => cancelReplan(),
  'save-edit': () => saveEdit(),
  'cancel-edit': () => cancelEdit(),
  undo: () => undo(),
  redo: () => redo(),
  save: () => {
    clearTimeout(state.saveTimer);
    persistNow();
  },
  'trip-open': (btn) => openTrip(btn.dataset.id),
  'trip-del': (btn) => deleteTrip(btn.dataset.id),
  restart: () => {
    state.plan = null;
    // 结果清空了，回到空状态居中
    setTimeout(syncEmptyLayout, 0);
    state.editing = false;
    state.errors = [];
    state.history = { past: [], future: [] };
    state.tripId = null;
    state.tripTitle = '';
    state.saveState = 'idle';
    $('result').innerHTML = '';
    renderToolbar();
    renderRecent();
    window.scrollTo?.({ top: 0, behavior: 'smooth' });
  },
  print: () => window.print(),
  'copy-md': async (btn) => {
    // 用当前数据现算，而不是服务端返回的那份——编辑之后它已经过期了
    const md = state.plan ? L.toMarkdown(state.plan) : state.markdown;
    const how = await copyText(md);
    btn.textContent = how === 'manual' ? '已列在下方' : '已复制';
    if (how !== 'manual') toast('Markdown 已复制');
    setTimeout(() => renderToolbar(), 1500);
  },
  'copy-fallback-select': () => {
    const ta = $('copy-fallback-text');
    if (!ta) return;
    ta.focus();
    ta.select();
  },
  'copy-fallback-close': () => {
    const host = $('copy-fallback');
    if (host) {
      host.hidden = true;
      host.innerHTML = '';
    }
  },
  'lightbox-close': () => closeLightbox(),
  'forget-hint': (btn) => forgetHint(btn.dataset.field, btn.dataset.value),
  'day-tab': (btn) => {
    state.activeDay = Number(btn.dataset.day);
    renderResult();
  },
  'att-up': (btn) => withHistory(() => moveAttraction(Number(btn.dataset.day), Number(btn.dataset.idx), -1)),
  'att-down': (btn) => withHistory(() => moveAttraction(Number(btn.dataset.day), Number(btn.dataset.idx), +1)),
  'att-del': (btn) =>
    withHistory(() => {
      const day = state.plan.days[Number(btn.dataset.day)];
      day.attractions.splice(Number(btn.dataset.idx), 1);
    }),
  'att-add': (btn) =>
    withHistory(() => {
      const day = state.plan.days[Number(btn.dataset.day)];
      day.attractions.push({
        name: '',
        address: '',
        location: null,
        visit_duration: 120,
        description: '',
        category: '景点',
        rating: null,
        image_url: null,
        ticket_price: 0,
      });
    }),
  'meal-add': (btn) =>
    withHistory(() => {
      const day = state.plan.days[Number(btn.dataset.day)];
      const used = new Set((day.meals ?? []).map((m) => m.type));
      const next = MEAL_ORDER.find((t) => !used.has(t)) ?? 'snack';
      day.meals.push({ type: next, name: '', address: '', location: null, description: '', estimated_cost: 0 });
    }),
  'meal-del': (btn) =>
    withHistory(() => {
      const day = state.plan.days[Number(btn.dataset.day)];
      day.meals.splice(Number(btn.dataset.idx), 1);
    }),
};

/**
 * 带撤销记录的结构性改动：先入栈、再改、再重绘。
 *
 * 「没点编辑行程也能删景点」是允许的（方便），但必须能撤销——
 * 所以所有会改变数据的点击都走这里。
 */
function withHistory(mutate) {
  pushHistory();
  mutate();
  renderToolbar();
  renderResult();
  scheduleSave();
}

// 排序逻辑在 logic.js（有单测）；这里只负责"变了就重绘"
function moveAttraction(dayIdx, idx, delta) {
  return L.moveAttraction(state.plan, dayIdx, idx, delta);
}

// ------------------------------------------------------------------ 灯箱 --
// 图片在页面里只有 72~96px 宽，看不清；点一下放大看。
// 为什么做成"一个固定容器 + 换 src"而不是每次 new 一个弹层：
// 反复开关不会堆积 DOM，也不需要管理一堆事件监听。
let lightboxEl = null;

function ensureLightbox() {
  if (lightboxEl) return lightboxEl;
  // 页面里可能已经有静态容器（index.html 里那份），有就复用。
  // 曾无条件新建，导致页面上出现两个 id=lightbox：打开的是新建那个，
  // 样式和检查读到的是静态那个 —— 表现就是"点了没反应"。
  const existing = document.getElementById('lightbox');
  if (existing) {
    lightboxEl = existing;
    return lightboxEl;
  }
  const el = document.createElement('div');
  el.className = 'lightbox';
  el.id = 'lightbox';
  el.hidden = true;
  el.innerHTML =
    '<button class="lb-close" type="button" data-act="lightbox-close" aria-label="关闭">✕</button>' +
    '<img id="lightbox-img" alt="" />' +
    '<p class="lb-cap" id="lightbox-cap"></p>';
  document.body.appendChild(el);
  lightboxEl = el;
  return el;
}

function openLightbox(src, caption) {
  const el = ensureLightbox();
  const img = el.querySelector('#lightbox-img');
  const cap = el.querySelector('#lightbox-cap');
  img.src = src;
  img.alt = caption || '';
  cap.textContent = caption || '';
  el.hidden = false;
  document.body.classList.add('lb-open');
  // 焦点给关闭按钮：键盘能直接 Esc / Enter 关掉
  el.querySelector('.lb-close').focus();
}

function closeLightbox() {
  if (!lightboxEl) return;
  lightboxEl.hidden = true;
  lightboxEl.querySelector('#lightbox-img').src = '';
  document.body.classList.remove('lb-open');
}

// ------------------------------------------------------------------ 拖拽 --
// 用模块变量而不是 dataTransfer 记录拖动源：jsdom 没有 dataTransfer，
// 而"拖完顺序对不对"这件事必须在测试里可验证。
let dnd = null;

function clearDropMarks() {
  document.querySelectorAll('.drop-target').forEach((el) => el.classList.remove('drop-target'));
  document.querySelectorAll('.dragging').forEach((el) => el.classList.remove('dragging'));
}

function onDragStart(ev) {
  const row = ev.target.closest('[data-drag-day]');
  if (!row) return;
  dnd = { day: Number(row.dataset.dragDay), idx: Number(row.dataset.dragIdx) };
  row.classList.add('dragging');
  if (ev.dataTransfer) {
    ev.dataTransfer.effectAllowed = 'move';
    try {
      ev.dataTransfer.setData('text/plain', `${dnd.day}:${dnd.idx}`);
    } catch {
      /* 某些浏览器在 dragover 前不让写，忽略 */
    }
  }
}

function onDragOver(ev) {
  const row = ev.target.closest('[data-drag-day]');
  if (!row || !dnd) return;
  ev.preventDefault(); // 不阻止默认行为就不允许 drop
  if (ev.dataTransfer) ev.dataTransfer.dropEffect = 'move';
  clearDropMarks();
  document.querySelectorAll('.dragging').forEach((el) => el.classList.add('dragging'));
  row.classList.add('drop-target');
}

function onDrop(ev) {
  const row = ev.target.closest('[data-drag-day]');
  if (!row || !dnd) return;
  ev.preventDefault();
  const from = dnd;
  dnd = null;
  clearDropMarks();

  const toDay = Number(row.dataset.dragDay);
  const toIdx = Number(row.dataset.dragIdx);
  if (from.day === toDay && from.idx === toIdx) return;

  pushHistory(); // 先存快照再改，撤销才有得退
  if (!L.moveAttractionTo(state.plan, from.day, from.idx, toDay, toIdx)) return;
  renderToolbar();
  renderResult();
  scheduleSave();
}

// 事件委托：整个结果区只挂几个监听，避免每次渲染重复绑定
function bindDelegates() {
  const root = document.body;
  root.addEventListener('click', (ev) => {
    const btn = ev.target.closest('[data-act]');
    if (!btn) return;
    const fn = ACTIONS[btn.dataset.act];
    if (fn) {
      ev.preventDefault();
      fn(btn);
    }
  });

  // 输入类改动：只重算预算，不重渲染整页——否则输入框会失焦
  root.addEventListener('input', (ev) => {
    const el = ev.target.closest('[data-bind]');
    if (!el) return;
    if (el.dataset.bind === 'replan-day') {
      toggleReplanDay(Number(el.value), el.checked);
      return;
    }
    applyBind(el);
    renderBudget();
    renderIssues();
    scheduleSave();
  });

  root.addEventListener('change', (ev) => {
    const el = ev.target.closest('[data-bind]');
    if (!el) return;
    if (el.dataset.bind === 'replan-day') {
      toggleReplanDay(Number(el.value), el.checked);
      return;
    }
    applyBind(el);
    renderBudget();
    renderIssues();
    if (el.dataset.bind === 'hotel' || el.dataset.bind === 'day') renderResult();
    if (el.dataset.bind === 'attraction' && el.dataset.field === 'name') renderMap();
    scheduleSave();
  });

  // 撤销的粒度：一次"焦点进出"记为一步，而不是每个按键一步。
  // 否则按 20 次 Ctrl+Z 才退掉一个字。
  root.addEventListener('focusin', (ev) => {
    const el = ev.target.closest('[data-bind]');
    if (!el || !state.plan) return;
    state.focusSnapshot = JSON.stringify(state.plan);
  });

  root.addEventListener('focusout', (ev) => {
    const el = ev.target.closest('[data-bind]');
    if (!el || !state.focusSnapshot) return;
    if (state.focusSnapshot !== JSON.stringify(state.plan)) {
      // 有实际改动才入栈（并把栈底的旧值恢复成立即前的状态）
      state.history.past.push(state.focusSnapshot);
      if (state.history.past.length > HISTORY_LIMIT) state.history.past.shift();
      state.history.future.length = 0;
      renderToolbar();
    }
    state.focusSnapshot = null;
  });

  // 点图片看大图（只认带 zoomable 的，避免误伤其它 img）
  root.addEventListener('click', (ev) => {
    const img = ev.target.closest('img.zoomable');
    if (img) {
      ev.preventDefault();
      // 缩略图只有 ~400px，点开用同图的大尺寸（后端存了 image_url_large）
      openLightbox(img.dataset.large || img.src, img.alt || '');
      return;
    }
    // 点遮罩空白处关闭（点在图片本身上不关）
    const lb = ev.target.closest('#lightbox');
    if (lb && !ev.target.closest('#lightbox-img') && !ev.target.closest('.lb-close')) {
      closeLightbox();
    }
  });

  document.addEventListener('dragstart', onDragStart);
  document.addEventListener('dragover', onDragOver);
  document.addEventListener('drop', onDrop);
  document.addEventListener('dragend', () => {
    dnd = null;
    clearDropMarks();
  });

  // 快捷键：Ctrl+Z 撤销、Ctrl+Shift+Z / Ctrl+Y 重做、Ctrl+S 保存
  root.addEventListener('keydown', (ev) => {
    if (ev.key === 'Escape' && lightboxEl && !lightboxEl.hidden) {
      ev.preventDefault();
      closeLightbox();
      return;
    }
    if (!state.plan) return;
    const mod = ev.ctrlKey || ev.metaKey;
    if (!mod) return;
    const key = ev.key.toLowerCase();
    if (key === 'z' && !ev.shiftKey) {
      ev.preventDefault();
      undo();
    } else if ((key === 'z' && ev.shiftKey) || key === 'y') {
      ev.preventDefault();
      redo();
    } else if (key === 's') {
      ev.preventDefault();
      clearTimeout(state.saveTimer);
      persistNow();
    }
  });
}

// ------------------------------------------------------------ 记忆画像 --
const HINT_LABEL = {
  preferences: '偏好',
  budget_level: '预算',
  transportation: '交通',
  accommodation: '住宿',
  city: '常去',
};

async function loadMemory() {
  try {
    const resp = await apiFetch('/api/profile');
    if (!resp.ok) return;
    renderMemory(await resp.json());
  } catch {
    /* 拉不到画像不影响主流程 */
  }
}

function renderMemory(profile) {
  const card = $('memory-card');
  const body = $('memory-body');
  if (!card || !body) return;

  const hints = profile.hints || {};
  const entries = Object.entries(hints).filter(([, v]) => v && v.length);
  if (!entries.length) {
    card.hidden = true;
    body.innerHTML = '';
    return;
  }

  card.hidden = false;
  body.innerHTML = entries
    .map(
      ([field, values]) =>
        `<div class="memory-row"><span class="memory-field">${HINT_LABEL[field] || field}</span>` +
        values
          .map(
            (v) =>
              `<span class="memory-chip" data-field="${escapeHtml(field)}" data-value="${escapeHtml(v)}">` +
              `${escapeHtml(v)}<button type="button" class="memory-del" data-act="forget-hint"` +
              ` data-field="${escapeHtml(field)}" data-value="${escapeHtml(v)}" title="删掉这一条">×</button>` +
              `</span>`,
          )
          .join('') +
        `</div>`,
    )
    .join('');
  syncEmptyLayout();
}

async function forgetHint(field, value) {
  try {
    const resp = await apiFetch(
      `/api/profile/hints?field=${encodeURIComponent(field)}&value=${encodeURIComponent(value)}`,
      { method: 'DELETE' },
    );
    if (!resp.ok) return;
    renderMemory((await resp.json()).profile);
  } catch {
    /* 忽略 */
  }
}

// ------------------------------------------------------------ 城市检索 --
// 下拉列表由后端给（自带一份城市清单），前端做防抖 + 丢弃过期响应。
// 为什么要丢过期响应：快速输入 "南"→"南京" 时，两个请求可能乱序返回，
// 用户会看到"南京"已经被替换成"南"的结果。
let cityTimer = null;
let citySeq = 0;

const HIDDEN_KEY = 'trip.hiddenCities';

/** 按省份分组，保持首次出现顺序。 */
function groupByProvince(items) {
  const order = [];
  const map = new Map();
  items.forEach((c) => {
    const key = c.province || '其他';
    if (!map.has(key)) {
      map.set(key, []);
      order.push(key);
    }
    map.get(key).push(c);
  });
  return order.map((province) => ({ province, cities: map.get(province) }));
}

function loadHiddenCities() {
  try {
    const raw = localStorage.getItem(HIDDEN_KEY);
    const arr = raw ? JSON.parse(raw) : [];
    return Array.isArray(arr) ? arr.filter((x) => typeof x === 'string') : [];
  } catch {
    return [];
  }
}

function saveHiddenCities(list) {
  state.hiddenCities = list;
  try {
    localStorage.setItem(HIDDEN_KEY, JSON.stringify(list));
  } catch {
    /* 隐私模式写不了 localStorage，功能降级为"本次有效" */
  }
}

function cityItems() {
  return state.cityResults || [];
}

/** 右键某项：以后不再出现；如果它就是输入框里的值，一并清掉。 */
function hideCity(name) {
  if (!name) return;
  const list = new Set(state.hiddenCities || []);
  list.add(name);
  saveHiddenCities([...list]);

  const input = $('city');
  if (input && input.value.trim() === name) {
    input.value = '';
    clearTimeout(cityTimer);
  }
  renderCityList(cityItems().filter((c) => c.name !== name));
}

/** 恢复全部被隐藏的城市，并重新查一次。 */
async function restoreHiddenCities() {
  saveHiddenCities([]);
  await fetchCities($('city')?.value.trim() || '');
}

function renderCityList(items) {
  state.cityResults = items;
  state.cityActive = items.length ? 0 : -1;

  const list = $('city-list');
  if (!list) return;

  const hiddenCount = (state.hiddenCities || []).length;
  const restoreHint = hiddenCount
    ? `<button type="button" class="city-restore" data-restore="1">已隐藏 ${hiddenCount} 个城市 · 点此恢复</button>`
    : '';

  const head = state.cityQuery
    ? `<div class="city-head">匹配 ${items.length} 个 · 继续输入可缩小范围</div>`
    : `<div class="city-head">全部 ${items.length} 个城市 · 输入可筛选</div>`;

  if (!items.length) {
    list.innerHTML = `<div class="city-empty">没有匹配的城市</div>${restoreHint}`;
    list.hidden = false;
    return;
  }

  const itemHtml = (c, i, activeIdx) =>
    `<button type="button" class="city-item${i === activeIdx ? ' active' : ''}"` +
    ` role="option" data-city="${escapeHtml(c.name)}" data-idx="${i}">` +
    `<span class="city-name">${escapeHtml(c.name)}</span>` +
    `<span class="city-py">${escapeHtml(c.pinyin || '')}</span></button>`;

  if (state.cityQuery) {
    // 搜索态：平铺，少一次点击
    list.innerHTML =
      head + items.map((c, i) => itemHtml(c, i, state.cityActive)).join('') + restoreHint;
  } else {
    // 浏览态：按省份折叠。直辖市默认展开，其余收起
    const groups = groupByProvince(items);
    list.innerHTML =
      head +
      groups
        .map(
          (g) =>
            `<details class="city-group" data-province="${escapeHtml(g.province)}"${
              g.province === '直辖市' ? ' open' : ''
            }>` +
            `<summary>${escapeHtml(g.province)}<span class="city-count">${g.cities.length}</span></summary>` +
            `<div class="city-group-body">` +
            g.cities.map((c, i) => itemHtml(c, i, -1)).join('') +
            `</div></details>`,
        )
        .join('') +
      restoreHint;
  }
  list.hidden = false;
  $('city')?.setAttribute('aria-expanded', 'true');
}

const CITY_SUPPRESS_MS = 300;

/** 主动展开：按当前输入重新查一次，再显示。 */
async function openCityList(force = false) {
  // force=true 表示用户主动点击：抑制窗口只用来挡 pickCity 归还焦点引发的那次 focus，
  // 不该挡住真实点击（否则"选完立刻再点"会没反应，测试与环境都遇到过）。
  if (!force && Date.now() < (state.citySuppressUntil || 0)) return;

  const list = $('city-list');
  if (!list) return;

  // 每次都按当前输入重查：点开时输入框里可能是上一个城市的名字，
  // 直接复用旧结果会显示一串和当前文字无关的城市（测试抓到过）。
  await fetchCities($('city')?.value.trim() || '');
  if (Date.now() < (state.citySuppressUntil || 0)) return; // 拉取期间被选掉了

  // 输入框里的值如果正好是某个城市，把高亮定位到它
  const current = $('city')?.value.trim();
  const idx = cityItems().findIndex((c) => c.name === current);
  if (idx >= 0) {
    state.cityActive = idx;
    document.querySelectorAll('#city-list .city-item').forEach((el, i) => {
      el.classList.toggle('active', i === idx);
    });
  }
}

function closeCityList() {
  const list = $('city-list');
  if (!list) return;
  list.hidden = true;
  $('city')?.setAttribute('aria-expanded', 'false');
}

function pickCity(name) {
  const input = $('city');
  // 先把抑制窗口立起来，再动焦点：否则 focus 会把刚关掉的下拉又打开
  state.citySuppressUntil = Date.now() + CITY_SUPPRESS_MS;
  if (input && name) {
    input.value = name;
    // 不派发 input 事件：那会再排一次防抖查询，把刚选定的结果冲掉
    clearTimeout(cityTimer);
  }
  closeCityList();
  input?.focus();
}

/** 只取"当前看得见的"候选项：折叠起来的组不参与键盘移动。 */
function visibleCityButtons() {
  const list = $('city-list');
  if (!list) return [];
  return Array.from(list.querySelectorAll('.city-item')).filter((el) => {
    const group = el.closest('details');
    return !group || group.open;
  });
}

function moveCityActive(delta) {
  const buttons = visibleCityButtons();
  if (!buttons.length) return;
  state.cityActive = (state.cityActive + delta + buttons.length) % buttons.length;
  buttons.forEach((el, i) => {
    el.classList.toggle('active', i === state.cityActive);
  });
  buttons[state.cityActive].scrollIntoView?.({ block: 'nearest' });
}

// 空输入就给全量（下拉可滚动），有输入才限制条数，避免一次刷太多
const CITY_LIMIT_ALL = 400;
const CITY_LIMIT_FILTER = 20;

async function fetchCities(q, limit) {
  const seq = (citySeq += 1);
  const n = limit || (q ? CITY_LIMIT_FILTER : CITY_LIMIT_ALL);
  state.cityQuery = q;
  try {
    const resp = await apiFetch(`/api/cities?q=${encodeURIComponent(q)}&limit=${n}`);
    if (!resp.ok) return;
    const data = await resp.json();
    if (seq !== citySeq) return; // 过期响应，丢掉
    renderCityList(data.items || []);
  } catch {
    /* 网络问题就让下拉保持原样，不打断输入 */
  }
}

function bindCityCombo() {
  const input = $('city');
  const list = $('city-list');
  if (!input || !list) return;

  input.addEventListener('input', () => {
    clearTimeout(cityTimer);
    const q = input.value.trim();
    cityTimer = setTimeout(() => fetchCities(q), 120);
  });

  // 点一下就展开（force=true 绕过抑制窗口：那个窗口只挡程序还焦引发的 focus）
  input.addEventListener('click', () => {
    openCityList(true);
  });
  input.addEventListener('focus', () => {
    openCityList();
  });

  input.addEventListener('keydown', (ev) => {
    if (ev.key === 'ArrowDown') {
      ev.preventDefault();
      if (list.hidden) fetchCities(input.value.trim());
      moveCityActive(1);
    } else if (ev.key === 'ArrowUp') {
      ev.preventDefault();
      moveCityActive(-1);
    } else if (ev.key === 'Enter') {
      const buttons = visibleCityButtons();
      if (!list.hidden && buttons.length) {
        ev.preventDefault();
        const active =
          buttons.find((el) => el.classList.contains('active')) || buttons[0];
        pickCity(active.dataset.city);
      }
    } else if (ev.key === 'Escape') {
      state.citySuppressUntil = Date.now() + CITY_SUPPRESS_MS;
      closeCityList();
    }
  });

  // mousedown 而不是 click：输入框失焦会先触发 blur，click 可能来不及
  const choose = (ev) => {
    const btn = ev.target.closest('[data-city]');
    if (!btn) return;
    ev.preventDefault();
    pickCity(btn.dataset.city);
  };
  list.addEventListener('mousedown', choose);
  list.addEventListener('click', (ev) => {
    if (ev.target.closest('[data-restore]')) {
      ev.preventDefault();
      restoreHiddenCities();
      return;
    }
    choose(ev);
  });

  // 右键：列表项 -> 以后不再出现；输入框 -> 清空当前输入
  list.addEventListener('contextmenu', (ev) => {
    const btn = ev.target.closest('[data-city]');
    if (!btn) return;
    ev.preventDefault();
    hideCity(btn.dataset.city);
  });
  input.addEventListener('contextmenu', (ev) => {
    if (!input.value.trim()) return; // 没内容就别劫持浏览器菜单
    ev.preventDefault();
    input.value = '';
    closeCityList();
  });

  document.addEventListener('mousedown', (ev) => {
    if (!ev.target.closest('.combo')) closeCityList();
  });
}

// ------------------------------------------------------------------ 提交 --
function setLoading(on) {
  $('submit').disabled = on;
  $('submit').textContent = on ? '生成中…' : '开始规划';
  $('progress').hidden = !on;
  if (!on) $('bar').style.width = '0%';
}

let timer = null;
function startFakeProgress() {
  let p = 0;
  const stages = [
    [30, '正在搜索景点（高德 POI）…'],
    [50, '正在查询天气…'],
    [65, '正在筛选酒店…'],
    [90, '正在生成行程与预算（LLM）…'],
  ];
  $('bar').style.width = '0%';
  $('status').textContent = '正在连接高德 MCP…';
  timer = setInterval(() => {
    if (p < 90) {
      p += 6;
      $('bar').style.width = p + '%';
      const hit = stages.find(([limit]) => p <= limit);
      if (hit) $('status').textContent = hit[1];
    }
  }, 700);
}

function stopFakeProgress() {
  clearInterval(timer);
  $('bar').style.width = '100%';
}

function showError(msg) {
  $('error').hidden = false;
  $('error').textContent = msg;
}

async function submit() {
  const req = collectRequest();
  if (!req.city) {
    showError('先填目的地城市');
    return;
  }
  $('error').hidden = true;
  setLoading(true);
  startFakeProgress();

  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 300000);

  try {
    const resp = await apiFetch('/api/trip/plan', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(req),
      signal: controller.signal,
    });
    if (!resp.ok) {
      let detail = `HTTP ${resp.status}`;
      try {
        const body = await resp.json();
        if (typeof body.detail === 'string') detail = body.detail;
      } catch {
        /* 非 JSON */
      }
      throw new Error(detail);
    }
    const data = await resp.json();
    stopFakeProgress();

    state.plan = data.plan;
    state.markdown = data.markdown;
    state.elapsed = data.elapsed_ms;
    state.errors = data.errors ?? [];
    state.memory = data.memory ?? {};
    state.routing = data.routing ?? {};
    state.packing = data.packing ?? {};
    state.budgetCheck = data.budget_check ?? {};
    loadMemory();
    state.editing = false;
    state.activeDay = 0;
    state.history = { past: [], future: [] };
    state.tripId = null;
    state.tripTitle = '';
    state.saveState = 'idle';

    recomputeBudget(state.plan);
    $('form-card').classList.add('collapsed');
    renderToolbar();
    renderResult();
    // 生成完立刻存一份：这样刷新页面也能从「已保存的行程」里找回来
    persistNow();
    $('toolbar')?.scrollIntoView?.({ behavior: 'smooth', block: 'start' });
  } catch (e) {
    showError(e.name === 'AbortError' ? '请求超时（5 分钟），可以先把天数改少再试' : e.message);
  } finally {
    clearTimeout(timeout);
    setLoading(false);
  }
}

/** 只读模式：URL 带 ?share=<token> 时进这里，隐藏所有能改东西的入口。
 *
 * 为什么单独一个函数而不是塞进 init：DOM 测试可以直接调它，
 * 不用改 location（jsdom 里改 URL 要重建整个窗口）。
 */
async function enterShareMode(token) {
  state.readonly = true;
  document.body.classList.add('share-mode');
  const resp = await apiFetch(`/api/share/${encodeURIComponent(token)}`);
  if (!resp.ok) {
    $('result').innerHTML =
      '<section class="card"><h2>链接无效</h2><p>这个分享链接不存在，或者已经被撤销了。</p></section>';
    return;
  }
  const data = await resp.json();
  state.plan = data.plan;
  state.packing = data.packing || {};
  state.markdown = data.markdown || '';
  state.routing = {};
  state.elapsed = 0;
  state.readonlyTitle = data.title || '';
  renderResult();
  renderToolbar();
  const banner = $('share-banner');
  if (banner) {
    banner.hidden = false;
    banner.textContent = data.title
      ? `只读分享：${data.title}`
      : '只读分享（别人发给你的行程，不能修改）';
  }
}

// ------------------------------------------------------------------ 启动 --
/** 空状态校正：没有结果、且侧栏没内容时，把两栏收成一栏并居中。
 *
 * 否则宽屏上会出现"左边一张卡、右边一大片空白"——网格仍然占着第二列。
 */
function syncEmptyLayout() {
  const layout = document.querySelector('.layout');
  if (!layout) return;
  const sideHasContent =
    !!document.querySelector('#memory-card:not([hidden])') ||
    document.querySelectorAll('.recent-item').length > 0;

  // 两种"单列"要分开：
  //   no-side  —— 有结果但侧栏没东西（新部署、没有历史行程）：主栏铺满，不留空洞
  //   is-empty —— 连结果都没有：收成窄栏居中，更像一个"入口"
  layout.classList.toggle('no-side', !sideHasContent);
  layout.classList.toggle('is-empty', !state.plan && !sideHasContent);
}

initDates();
bindDelegates();
loadMemory();
state.hiddenCities = loadHiddenCities();
bindCityCombo();
$('submit').addEventListener('click', submit);

// 只读分享优先：URL 带 ?share=<token> 时接管整个页面，不跑表单流程。
// 这一行之前漏了——函数写了但没人调，所以打开链接还是空表单。
const shareToken = new URLSearchParams(location.search).get('share');
if (shareToken) {
  enterShareMode(shareToken);
} else {
  // 打开页面就把已保存的行程列出来，方便接着改
  loadRecent();
}
