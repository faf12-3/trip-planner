// 前端集成测试：用 jsdom 把 web/index.html + logic.js + app.js 真跑一遍。
//
// 为什么用「一条场景流」而不是一堆独立用例：前端的编辑功能是**有状态**的
// （进入编辑态 → 改 → 撤销 → 再改 → 完成），每次新建 DOM 会丢掉状态，
// 只能测到"首屏"。这里按真实操作顺序跑一遍，每步断言。
//
//   node tests/web_dom.test.cjs

const fs = require('node:fs');
const path = require('node:path');
const { JSDOM } = require('jsdom');

const ROOT = path.join(__dirname, '..', 'web');
const html = fs.readFileSync(path.join(ROOT, 'index.html'), 'utf-8');
const logicSrc = fs.readFileSync(path.join(ROOT, 'logic.js'), 'utf-8');
const appSrc = fs.readFileSync(path.join(ROOT, 'app.js'), 'utf-8');

function fixture() {
  return {
    plan: {
      city: '南京',
      start_date: '2026-12-22',
      end_date: '2026-12-23',
      overall_suggestions: '博物院需提前预约。',
      days: [
        {
          date: '2026-12-22',
          day_index: 0,
          description: '首日逛博物院',
          transportation: '公共交通',
          accommodation: '经济型酒店',
          hotel: { name: '如家', estimated_cost: 200 },
          attractions: [
            { name: '南京博物院', visit_duration: 180, ticket_price: 0,
              image_url: 'https://img.example/small.jpg',
              image_url_large: 'https://img.example/large.jpg',
              location: { longitude: 118.83, latitude: 32.04 } },
            { name: '明孝陵', visit_duration: 120, ticket_price: 70, image_url: null,
              location: { longitude: 118.84, latitude: 32.06 } },
          ],
          meals: [{ type: 'lunch', name: '鸭血粉丝', estimated_cost: 30 }],
        },
        {
          date: '2026-12-23',
          day_index: 1,
          description: '夫子庙',
          transportation: '公共交通',
          accommodation: '经济型酒店',
          hotel: null,
          attractions: [
            { name: '夫子庙', visit_duration: 90, ticket_price: 30, image_url: null,
              location: { longitude: 118.79, latitude: 32.02 } },
          ],
          meals: [{ type: 'dinner', name: '盐水鸭', estimated_cost: 60 }],
        },
      ],
      weather_info: [{ date: '2026-12-22', day_weather: '晴', night_weather: '多云',
                       day_temp: 8, night_temp: 0, wind_direction: '北', wind_power: '1-3' }],
      budget: { total_attractions: 0, total_hotels: 0, total_meals: 0,
                total_transportation: 60, total: 0 },
    },
    markdown: '# 南京',
    elapsed_ms: 12345,
    trace: [],
    errors: [],
    // 装备清单：后端按规则算好后随规划响应一起返回
    packing: {
      groups: [
        { title: '雨具', items: [{ name: '折叠伞', reason: '11-01 有雨' }] },
        { title: '证件与金钱', items: [{ name: '身份证 / 护照', reason: '入住与购票都要' }] },
      ],
      summary: '2 天 · 有雨 1 天',
    },
    // 条件路由：默认造一场雨，用来验证提示卡片
    routing: {
      needed: true,
      days: [{ date: '2026-12-22', weather: '中雨', reason: '中雨/小雨，室外活动受影响' }],
      violations: [],
    },
  };
}

function makeDom(url = 'http://127.0.0.1:8000/') {
  const dom = new JSDOM(html, {
    runScripts: 'outside-only',
    pretendToBeVisual: true,
    url,
  });
  const { window } = dom;

  // 内存版后端：把 /api/trips 的 CRUD 模拟出来，测试无需真实服务
  const db = new Map();
  // 画像桩的状态：删一条后置空，用来验证卡片会收起
  const state_profile = { hints: { preferences: ['历史文化'], city: ['南京'] } };
  // 记录 fetch 调用，供导出/分享断言用
  const state_calls = [];
  // 暴露给测试步骤（它们在 makeDom 外面）
  window.__calls = state_calls;

  // 重排桩：记住「待确认」的新计划
  const state_replan = { pending: null };
  let seq = 0;
  const json = (data, status = 200) => ({
    ok: status < 400,
    status,
    json: async () => data,
    blob: async () => new Blob(),
  });
  const meta = (id, plan, title) => ({
    id,
    title: title || `${plan.city} ${plan.start_date}`,
    city: plan.city,
    start_date: plan.start_date,
    end_date: plan.end_date,
    days: (plan.days || []).length,
    total: (plan.budget || {}).total || 0,
    updated_at: new Date().toISOString(),
  });

  window.fetch = async (url, opts = {}) => {
    const u = String(url);
    const method = (opts.method || 'GET').toUpperCase();

    if (u.includes('/api/trip/plan')) {
        state_calls.push({ url: u, kind: 'plan', body: opts.body });
        const data = fixture();
        if (window.__nextPlanOverrides) {
          Object.assign(data, window.__nextPlanOverrides);
          window.__nextPlanOverrides = null;
        }
        return json(data);
      }
    if (u.includes('/api/config/map')) return json({ configured: false });
    if (u.includes('/api/staticmap')) return json({ ok: true });  // blob 由下面的 json() 提供

    if (u.includes('/api/profile/hints')) {
      state_profile.hints = {};
      return json({ ok: true, profile: { user: 'u-test', trips: 1, hints: {} } });
    }
    if (u.includes('/api/profile')) {
      return json({ user: 'u-test', trips: 1, hints: state_profile.hints });
    }

    if (u.includes('/api/cities')) {
      const q = (u.split('q=')[1] || '').split('&')[0];
      const limit = Number((u.match(/limit=(\d+)/) || [])[1] || 8);
      const key = decodeURIComponent(q || '');
      const all = key.includes('南')
        ? [
            { name: '南京', pinyin: 'nanjing', province: '江苏' },
            { name: '南昌', pinyin: 'nanchang', province: '江西' },
          ]
        : key
          ? [{ name: '北京', pinyin: 'beijing', province: '直辖市' }]
          : [
              { name: '北京', pinyin: 'beijing', province: '直辖市' },
              { name: '上海', pinyin: 'shanghai', province: '直辖市' },
              { name: '广州', pinyin: 'guangzhou', province: '广东' },
              { name: '深圳', pinyin: 'shenzhen', province: '广东' },
              { name: '南京', pinyin: 'nanjing', province: '江苏' },
            ];
      return json({ items: all.slice(0, limit) });
    }

    if (u.includes('/replan/confirm')) {
      const body = JSON.parse(opts.body || '{}');
      if (!state_replan.pending) return json({ detail: '没有等待确认的会话' }, 404);
      const plan = state_replan.pending;
      state_replan.pending = null;
      if (!body.approve) return json({ status: 'rejected' });
      return json({ status: 'applied', plan, markdown: '# AI 重排后' });
    }

    if (u.includes('/replan')) {
      const body = JSON.parse(opts.body || '{}');
      const idMatch = u.match(/\/api\/trips\/([^/?]+)\/replan/);
      const id = idMatch ? decodeURIComponent(idMatch[1]) : '';
      const rec = db.get(id);
      if (!rec) return json({ detail: '行程不存在' }, 404);
      const plan = JSON.parse(JSON.stringify(rec.plan));
      const preview = { days: [] };
      (body.selected_days || []).forEach((d) => {
        const before = (plan.days[d].attractions || []).map((a) => a.name);
        plan.days[d].attractions = [
          { name: `AI-${d + 1}`, visit_duration: 60, ticket_price: 0, meals: [] },
        ];
        preview.days.push({
          day_index: d,
          date: plan.days[d].date,
          before,
          after: [`AI-${d + 1}`],
          hotel_before: (plan.days[d].hotel || {}).name || '',
          hotel_after: (plan.days[d].hotel || {}).name || '',
        });
      });
      state_replan.pending = plan;
      return json({ status: 'pending', thread_id: 'th-1', selected: body.selected_days, preview });
    }

    if (u.includes('/export.ics')) {
      state_calls.push({ url: u, kind: 'ics' });
      return {
        ok: true,
        status: 200,
        headers: { get: () => 'attachment; filename="trip.ics"; filename*=UTF-8\'\'%E5%8D%97%E4%BA%AC.ics' },
        blob: async () => new window.Blob(['BEGIN:VCALENDAR'], { type: 'text/calendar' }),
        json: async () => ({}),
      };
    }

    if (u.includes('/share') && !u.includes('/api/share/')) {
      state_calls.push({ url: u, kind: 'share' });
      return json({ token: 'tok-abc123456789xyz', path: '/?share=tok-abc123456789xyz', read_only: true });
    }

    if (u.includes('/api/share/')) {
      state_calls.push({ url: u, kind: 'share-read' });
      return json({
        read_only: true,
        title: '南京两日（分享）',
        plan: fixture().plan,
        markdown: '# 分享',
        packing: {
          groups: [{ title: '雨具', items: [{ name: '折叠伞', reason: '11-01 有雨' }] }],
          summary: '2 天 · 有雨 1 天',
        },
      });
    }

    if (u.includes('/api/trips')) {
      const idMatch = u.match(/\/api\/trips\/([^/?]+)/);
      const id = idMatch ? decodeURIComponent(idMatch[1]) : null;

      if (method === 'GET' && id) {
        const rec = db.get(id);
        return rec ? json(rec) : json({ detail: 'nf' }, 404);
      }
      if (method === 'GET') {
        const items = [...db.values()].map((r) => r.trip);
        return json({ items });
      }
      const body = opts.body ? JSON.parse(opts.body) : {};
      if (method === 'POST') {
        const newId = `20260101-000000-${String(++seq).padStart(6, '0')}`;
        const trip = meta(newId, body.plan, body.title);
        db.set(newId, { id: newId, trip, plan: body.plan, markdown: body.markdown });
        return json({ ok: true, trip });
      }
      if (method === 'PUT' && id) {
        const trip = meta(id, body.plan, body.title);
        db.set(id, { id, trip, plan: body.plan, markdown: body.markdown });
        return json({ ok: true, trip });
      }
      if (method === 'DELETE' && id) {
        db.delete(id);
        return json({ ok: true });
      }
    }
    return json({}, 404);
  };

  window.scrollTo = () => {};
  window.URL.createObjectURL = () => 'blob:fake-map'; // jsdom 未实现
  window.URL.revokeObjectURL = () => {};
  window.__db = db; // 供断言"服务端存了几份"

  window.eval(logicSrc);
  window.eval(appSrc);
  // 测试用：把 toast 关掉，方便断言"下一次是否又弹出"
  window.__resetToast = () => {
    const el = window.document.getElementById('toast');
    if (el) el.hidden = true;
  };
  return window;
}

const tick = (ms = 40) => new Promise((r) => setTimeout(r, ms));
const q = (w, s) => w.document.querySelector(s);
const qa = (w, s) => Array.from(w.document.querySelectorAll(s));
const text = (w, s) => (q(w, s)?.textContent || '').replace(/\s+/g, ' ').trim();
const nums = (w) => qa(w, '.budget .cell strong').map((el) => Number(el.textContent));
const click = (w, sel, idx = 0) => {
  const el = qa(w, sel)[idx];
  if (!el) throw new Error(`找不到可点击元素: ${sel}[${idx}]`);
  el.dispatchEvent(new w.Event('click', { bubbles: true }));
};
const type = (w, sel, value, idx = 0) => {
  const el = qa(w, sel)[idx];
  if (!el) throw new Error(`找不到输入框: ${sel}[${idx}]`);
  el.value = value;
  el.dispatchEvent(new w.Event('input', { bubbles: true }));
};
const focusBlur = (w, el) => {
  el.dispatchEvent(new w.Event('focusin', { bubbles: true }));
  el.dispatchEvent(new w.Event('focusout', { bubbles: true }));
};
const attNames = (w) =>
  qa(w, 'input[data-bind="attraction"][data-field="name"]').map((i) => i.value);

// 非编辑态下景点名是文本、编辑态下是 input，断言要同时兼容两种
const viewAttractions = (w) =>
  qa(w, '.timeline .row')
    .filter((row) => /^\d+$/.test((row.querySelector('.rank')?.textContent || '').trim()))
    .map((row) => (row.querySelector('.title')?.textContent || '').trim());
const names = (w) => (q(w, '.editing') ? attNames(w) : viewAttractions(w));

const assert = require('node:assert');

const steps = [];
const step = (name, fn) => steps.push([name, fn]);

// --------------------------------------------------------------- 场景 --
function state_md_ok(w) {
  // Markdown 渲染以 "# " 开头（城市标题）
  return '#';
}

step('① 首屏：只有表单，没有结果与工具条', (w) => {
  assert.ok(q(w, '#form-card'), '表单应在');
  assert.strictEqual(q(w, '#result').innerHTML.trim(), '');
  assert.strictEqual(q(w, '#toolbar').hidden, true);
});

step('①b 城市检索：输入「南」下拉出南京，点一下填进输入框', async (w) => {
  const input = q(w, '#city');
  assert.ok(input, '城市输入框应在');

  input.value = '南';
  input.dispatchEvent(new w.Event('input', { bubbles: true }));
  await tick(220); // 等防抖 120ms + 请求

  const list = q(w, '#city-list');
  assert.strictEqual(list.hidden, false, '下拉应展开');
  const names = qa(w, '#city-list .city-item').map((el) => el.textContent.trim());
  assert.ok(names.some((t) => t.includes('南京')), '下拉里应有南京：' + JSON.stringify(names));

  // 点第一项（南京）
  const first = qa(w, '#city-list [data-city="南京"]')[0];
  assert.ok(first, '应能定位到南京这一项');
  first.dispatchEvent(new w.Event('mousedown', { bubbles: true }));
  await tick(30);

  assert.strictEqual(input.value, '南京', '选中后应写回输入框');
  assert.strictEqual(q(w, '#city-list').hidden, true, '选中后应收起');
});

step('①c 点一下输入框就展开列表（不用先输入字符）', async (w) => {
  const input = q(w, '#city');
  assert.strictEqual(q(w, '#city-list').hidden, true, '初始应是收起的');

  // 清空后再点：验证"不敲字也能出列表"这条路径（上一步把值改成了南京）
  input.value = '';
  input.dispatchEvent(new w.Event('click', { bubbles: true }));
  await tick(80);

  const list = q(w, '#city-list');
  assert.strictEqual(list.hidden, false, '点击后应展开');
  const names = qa(w, '#city-list .city-item').map((el) => el.textContent.trim());
  assert.ok(names.length > 0, '应显示热门城市：' + JSON.stringify(names));
  assert.ok(names.some((t) => t.includes('北京')), '默认列表应含北京：' + JSON.stringify(names));
  // 空输入应当看到"全部"而不是只有几个热门
  assert.ok(names.length >= 5, '空输入应展示全部城市，实际 ' + names.length);
  const head = (q(w, '#city-list .city-head') || {}).textContent || '';
  assert.ok(head.includes('全部'), '应有「全部 N 个城市」表头，实际：' + head);

  // Esc 收起
  input.dispatchEvent(new w.KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
  await tick(20);
  assert.strictEqual(q(w, '#city-list').hidden, true, 'Esc 后应收起');

  // 恢复一个有效城市：后续步骤要提交表单（上一步把输入清空了）
  q(w, '#city').value = '北京';
});

step('①d 右键取消城市：从列表消失、写进 localStorage、可一键恢复', async (w) => {
  const input = q(w, '#city');
  input.value = '';
  input.dispatchEvent(new w.Event('click', { bubbles: true }));
  await tick(80);

  const before = qa(w, '#city-list .city-item').length;
  assert.ok(before > 0, '应有候选城市');

  // 右键第一项（北京）
  const first = qa(w, '#city-list .city-item')[0];
  const name = first.dataset.city;
  first.dispatchEvent(new w.Event('contextmenu', { bubbles: true, cancelable: true }));
  await tick(20);

  const after = qa(w, '#city-list .city-item').map((el) => el.dataset.city);
  assert.ok(!after.includes(name), `右键后不该再有 ${name}：` + JSON.stringify(after));
  const stored = JSON.parse(w.localStorage.getItem('trip.hiddenCities') || '[]');
  assert.ok(stored.includes(name), '应写进 localStorage：' + JSON.stringify(stored));

  // 恢复
  const restore = q(w, '#city-list [data-restore]');
  assert.ok(restore, '应出现恢复入口');
  restore.dispatchEvent(new w.Event('click', { bubbles: true }));
  await tick(80);
  const restored = JSON.parse(w.localStorage.getItem('trip.hiddenCities') || '[]');
  assert.strictEqual(restored.length, 0, '恢复后隐藏列表应清空');

  // 输入框右键 = 清空
  input.value = '上海';
  input.dispatchEvent(new w.Event('contextmenu', { bubbles: true, cancelable: true }));
  assert.strictEqual(input.value, '', '右键输入框应清空输入');

  // 后续步骤要提交表单，补回一个有效城市
  input.value = '北京';
});

step('①e 城市按省份折叠：直辖市默认展开，其余点开才显示', async (w) => {
  const input = q(w, '#city');
  input.value = '';
  input.dispatchEvent(new w.Event('click', { bubbles: true }));
  await tick(80);

  const groups = qa(w, '#city-list .city-group');
  assert.ok(groups.length >= 2, '应渲染多个省份分组，实际 ' + groups.length);

  const names = groups.map((g) => g.dataset.province);
  assert.ok(names.includes('直辖市'), '应有直辖市分组：' + JSON.stringify(names));
  assert.ok(names.includes('广东'), '应有广东分组：' + JSON.stringify(names));

  // 直辖市默认展开
  const muni = groups.find((g) => g.dataset.province === '直辖市');
  assert.strictEqual(muni.open, true, '直辖市应默认展开');
  assert.ok(
    muni.querySelectorAll('.city-item').length >= 2,
    '直辖市组里应有城市'
  );

  // 广东默认收起；展开后才出现城市
  const gd = groups.find((g) => g.dataset.province === '广东');
  assert.strictEqual(gd.open, false, '广东应默认收起');
  gd.open = true;
  const gdNames = Array.from(gd.querySelectorAll('.city-item')).map((el) => el.dataset.city);
  assert.ok(gdNames.includes('广州'), '展开广东应看到广州：' + JSON.stringify(gdNames));

  // 点一个城市仍能写回输入框
  gd.querySelector('[data-city="深圳"]').dispatchEvent(new w.Event('mousedown', { bubbles: true }));
  await tick(30);
  assert.strictEqual(input.value, '深圳', '点选后应写回输入框');
});

step('①f 记忆卡片：显示记住的偏好，点 × 能删掉', async (w) => {
  await tick(40); // 等启动时的 loadMemory

  const card = q(w, '#memory-card');
  assert.ok(card, '记忆卡片应存在');
  assert.strictEqual(card.hidden, false, '有画像时应显示');
  const chips = qa(w, '#memory-card .memory-chip');
  assert.ok(chips.length >= 2, '应显示画像条目，实际 ' + chips.length);

  // 点第一条的 ×
  const del = qa(w, '#memory-card .memory-del')[0];
  del.dispatchEvent(new w.Event('click', { bubbles: true }));
  await tick(60);

  const after = qa(w, '#memory-card .memory-chip').length;
  assert.strictEqual(after, 0, '删完后卡片应收起（桩返回空画像）');
  assert.strictEqual(q(w, '#memory-card').hidden, true, '空画像时卡片应隐藏');
});

step('② 点「开始规划」→ 渲染两天行程、工具条出现', async (w) => {
  // 新字段：预算与其他要求要真的进请求体
  q(w, '#budget_total').value = '2000';
  q(w, '#notes').value = '  想住舒适一点的酒店  ';

  click(w, '#submit');
  await tick(120);
  assert.strictEqual(qa(w, '.timeline .day').length, 2, '应渲染 2 天');
  assert.ok(text(w, '#result').includes('南京博物院'));
  assert.strictEqual(q(w, '#toolbar').hidden, false);
});

step('③ 生成后自动保存：服务端有 1 份，工具条显示已保存', async (w) => {
  await tick(80);
  assert.strictEqual(w.__db.size, 1, `实际 ${w.__db.size} 份`);
  assert.ok(text(w, '#toolbar').includes('已保存'), text(w, '#toolbar'));
  assert.strictEqual(qa(w, '.recent-item').length, 1, '最近行程列表应有 1 项');
});

step('④ 预算按明细汇总：门票 100 / 住宿 200 / 餐饮 90 / 交通 60 / 合计 450', (w) => {
  assert.deepStrictEqual(nums(w), [100, 200, 90, 60, 450]);
});

step('⑤ 地图未配置 JS Key 时降级为静态图，不抛错', (w) => {
  const host = q(w, '#map-host');
  assert.ok(host, '地图容器应在');
  assert.ok(/static-map|img/.test(host.innerHTML), `实际: ${host.innerHTML.slice(0, 80)}`);
});

step('⑤b 每天各有一张静态地图（打印时不会只有第一天有图）', async (w) => {
  await tick(120); // 等 hydrateDayMaps 的并发请求
  const imgs = qa(w, '.day-map-img');
  assert.strictEqual(imgs.length, 2, `两天应各有一张，实际 ${imgs.length}`);
  const card = q(w, '#map-host')?.closest('.card');
  assert.ok(card?.classList.contains('no-print'), '交互地图卡片应标 no-print');
});

step('⑤c 点每日地图能看大图，Esc 关掉', async (w) => {
  const map = qa(w, '.day-map-img')[0];
  assert.ok(map, '应先有每日地图');

  // 点图 -> 灯箱出现，且大图 src 与缩略图一致
  map.dispatchEvent(new w.Event('click', { bubbles: true }));
  await tick();
  const lb = q(w, '#lightbox');
  assert.ok(lb, '灯箱容器应在');
  assert.strictEqual(lb.hidden, false, '点图后灯箱应显示');
  assert.strictEqual(q(w, '#lightbox-img').src, map.src, '大图 src 应与所点图片一致');

  // Esc 关闭
  w.document.body.dispatchEvent(new w.KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
  await tick();
  assert.strictEqual(q(w, '#lightbox').hidden, true, 'Esc 后应关闭');
});

step('⑤d 点缩略图看大图时用大尺寸，而不是缩略图', async (w) => {
  const thumb = qa(w, '.thumb.zoomable')[0];
  assert.ok(thumb, '应有可点的缩略图');
  assert.strictEqual(thumb.src, 'https://img.example/small.jpg', '缩略图应是 small');

  thumb.dispatchEvent(new w.Event('click', { bubbles: true }));
  await tick();
  assert.strictEqual(q(w, '#lightbox').hidden, false, '灯箱应打开');
  assert.strictEqual(
    q(w, '#lightbox-img').src,
    'https://img.example/large.jpg',
    '灯箱应使用 large 尺寸'
  );

  q(w, '#lightbox .lb-close').dispatchEvent(new w.Event('click', { bubbles: true }));
  await tick();
  assert.strictEqual(q(w, '#lightbox').hidden, true, '点 ✕ 后应关闭');
});

step('⑤e 条件路由：下雨天显示「已按室内安排」提示', async (w) => {
  const card = q(w, '.routing-card');
  assert.ok(card, '应有天气调整卡片');
  const text = card.textContent;
  assert.ok(text.includes('中雨'), '应写明天气：' + text);
  assert.ok(text.includes('室内'), '应说明已改室内：' + text);
  assert.strictEqual(q(w, '.routing-warn'), null, '无违规不该显示警告');
});

step('⑤f 天气缺日：没预报的那天要明确提示', async (w) => {
  // 夹具是 2 天行程、只有 1 天天气 -> 应提示"还有 1 天没有预报"
  const note = q(w, '.weather-missing');
  assert.ok(note, '应显示缺日提示');
  assert.ok(note.textContent.includes('1 天'), '应说明缺几天：' + note.textContent);
  assert.ok(note.textContent.includes('2026-12-23'), '应写明缺哪一天：' + note.textContent);
  assert.ok(note.textContent.includes('4 天'), '应解释原因（高德只给 4 天）');
});

step('⑥ 非编辑态直接删景点（用户报的路径）→ 数量减少', async (w) => {
  assert.ok(!q(w, '.editing'), '此时不在编辑态');
  assert.strictEqual(qa(w, '[data-act="att-del"]').length, 3, '三个景点都有删除按钮');
  click(w, '[data-act="att-del"]'); // 删第一个：南京博物院（门票 0）
  await tick();
  const left = names(w);
  assert.deepStrictEqual(left, ['明孝陵', '夫子庙'], `实际 ${JSON.stringify(left)}`);
  // 删掉的这个景点是免费的，所以门票总额不变（70+30=100）——金额不是判断依据
  assert.deepStrictEqual(nums(w).slice(0, 1), [100]);
});

step('⑦ 撤销把删掉的景点恢复（并停在编辑态方便继续改）', async (w) => {
  const btn = qa(w, '[data-act="undo"]')[0];
  assert.ok(btn, '工具条应有撤销按钮');
  assert.strictEqual(btn.disabled, false, '删除后撤销应可用');
  click(w, '[data-act="undo"]');
  await tick();
  assert.deepStrictEqual(names(w), ['南京博物院', '明孝陵', '夫子庙']);
  assert.ok(q(w, '.editing'), '撤销后应进入编辑态');
});

step('⑧ 重做再做一遍删除；撤销到底后按钮禁用', async (w) => {
  click(w, '[data-act="redo"]');
  await tick();
  assert.strictEqual(names(w).length, 2, '重做后又少一个');

  click(w, '[data-act="undo"]');
  await tick();
  assert.deepStrictEqual(names(w), ['南京博物院', '明孝陵', '夫子庙']);

  // 栈空 → 撤销按钮禁用（点不动，数据也不会变）
  const undoBtn = qa(w, '[data-act="undo"]')[0];
  assert.strictEqual(undoBtn.disabled, true, '栈空时撤销应禁用');
  assert.strictEqual(names(w).length, 3);
});

step('⑨「完成编辑」退出编辑态，「编辑行程」再进来', async (w) => {
  click(w, '[data-act="save-edit"]');
  await tick();
  assert.ok(!q(w, '.editing'), '应退出编辑态');
  assert.strictEqual(qa(w, 'input[data-bind="attraction"]').length, 0, '静态视图不应有输入框');

  click(w, '[data-act="edit"]');
  await tick();
  assert.ok(q(w, '.editing'));
  assert.strictEqual(qa(w, 'input[data-bind="attraction"][data-field="name"]').length, 3);
});

step('⑩ 改门票 70→120：预算即时变 150/500，输入框不失焦', async (w) => {
  type(w, 'input[data-bind="attraction"][data-field="ticket_price"]', '120', 1);
  await tick();
  assert.deepStrictEqual(nums(w), [150, 200, 90, 60, 500]);
  assert.strictEqual(qa(w, 'input[data-bind="attraction"][data-field="ticket_price"]')[1].value, '120');
});

step('⑪ 一次焦点进出只记一步撤销（不是每个按键一步）', async (w) => {
  const input = qa(w, 'input[data-bind="attraction"][data-field="ticket_price"]')[0];
  input.dispatchEvent(new w.Event('focusin', { bubbles: true }));
  input.value = '11';
  input.dispatchEvent(new w.Event('input', { bubbles: true }));
  input.value = '12';
  input.dispatchEvent(new w.Event('input', { bubbles: true }));
  input.dispatchEvent(new w.Event('focusout', { bubbles: true }));
  await tick();
  assert.strictEqual(nums(w)[0], 162, '两次按键合计只算一次改动：12+120+30=162');
});

step('⑫ 下移景点：顺序交换', async (w) => {
  click(w, '[data-act="att-down"][data-idx="0"]');
  await tick();
  assert.deepStrictEqual(names(w).slice(0, 2), ['明孝陵', '南京博物院']);
});

step('⑬ 撤销能把顺序也还原', async (w) => {
  click(w, '[data-act="undo"]');
  await tick();
  assert.deepStrictEqual(names(w).slice(0, 2), ['南京博物院', '明孝陵']);
});

step('⑬b 单日排太久 → 页面上出现冲突提示，改回去就消失', async (w) => {
  type(w, 'input[data-bind="attraction"][data-field="visit_duration"]', '400', 0);
  type(w, 'input[data-bind="attraction"][data-field="visit_duration"]', '400', 1);
  await tick();
  const shown = qa(w, '.day-issues .issue').map((e) => e.textContent).join(' ');
  assert.ok(shown.includes('超过'), '期望出现过载提示，实际：' + shown);

  // 改小之后提示应立即消失（不靠重渲染整页）
  type(w, 'input[data-bind="attraction"][data-field="visit_duration"]', '60', 0);
  type(w, 'input[data-bind="attraction"][data-field="visit_duration"]', '60', 1);
  await tick();
  assert.strictEqual(qa(w, '.day-issues .issue').length, 0, '改小后不该还有提示');
});

step('⑬c 拖拽：同一天内把第 1 个景点拖到第 2 个位置', async (w) => {
  // 第 1 天只有 2 个景点（南京博物院、明孝陵），所以拖 0→1
  const rows = qa(w, '[data-drag-day="0"]');
  assert.strictEqual(rows.length, 2, '第 1 天应有 2 个可拖拽行，实际 ' + rows.length);

  const drag = (el, type) => {
    const ev = new w.Event(type, { bubbles: true, cancellable: true });
    el.dispatchEvent(ev);
    return ev;
  };

  drag(qa(w, '[data-drag-day="0"][data-drag-idx="0"]')[0], 'dragstart');
  drag(qa(w, '[data-drag-day="0"][data-drag-idx="1"]')[0], 'dragover');
  drag(qa(w, '[data-drag-day="0"][data-drag-idx="1"]')[0], 'drop');
  await tick();

  assert.strictEqual(names(w)[0], '明孝陵', '拖拽未生效：' + JSON.stringify(names(w)));
});

step('⑭ 新增景点 + 新增餐饮', async (w) => {
  const before = names(w).length;
  click(w, '[data-act="att-add"]');
  await tick();
  assert.strictEqual(names(w).length, before + 1);

  const meals = qa(w, 'input[data-bind="meal"][data-field="name"]').length;
  click(w, '[data-act="meal-add"]');
  await tick();
  assert.strictEqual(qa(w, 'input[data-bind="meal"][data-field="name"]').length, meals + 1);
});

step('⑮「放弃全部改动」回到进入编辑前', async (w) => {
  click(w, '[data-act="cancel-edit"]');
  await tick();
  assert.ok(!q(w, '.editing'));
  assert.deepStrictEqual(nums(w), [100, 200, 90, 60, 450], '应回到初始数据');
});

step('⑯ 改价后保存 → 走 PUT 更新同一份，不新增记录', async (w) => {
  click(w, '[data-act="edit"]');
  await tick();
  const input = qa(w, 'input[data-bind="attraction"][data-field="ticket_price"]')[0];
  input.value = '44';
  input.dispatchEvent(new w.Event('input', { bubbles: true }));
  click(w, '[data-act="save-edit"]'); // 完成编辑（= 视图态）
  await tick();
  click(w, '[data-act="save"]'); // 立即保存，不等防抖
  await tick(150);

  assert.strictEqual(w.__db.size, 1, `应仍是 1 份，实际 ${w.__db.size}`);
  const stored = [...w.__db.values()][0];
  assert.strictEqual(stored.plan.days[0].attractions[0].ticket_price, 44, '服务端应存到新值');
  assert.ok(text(w, '#toolbar').includes('已保存'), text(w, '#toolbar'));
});
step('⑰ 最近行程列表点开能载入服务端数据', async (w) => {
  click(w, '[data-act="trip-open"]');
  await tick(120);
  assert.ok(q(w, '.timeline'), '应渲染出行程');
  assert.strictEqual(qa(w, '.recent-item')[0].className.includes('current'), true, '当前项应高亮');
});

step('⑰b AI 重排：勾一天 → 提交 → 看到改动 → 确认后生效，且能撤销', async (w) => {
  // 工具条应有入口
  const openBtn = q(w, '[data-act="replan-open"]');
  assert.ok(openBtn, '工具条应有「AI 重排」');
  click(w, '[data-act="replan-open"]');
  await tick();

  const panel = q(w, '#replan-host .replan-card');
  assert.ok(panel, '面板应出现');
  const days = qa(w, '#replan-host [data-bind="replan-day"]');
  assert.strictEqual(days.length, 2, '应列出两天');

  // 勾第 2 天
  days[1].checked = true;
  days[1].dispatchEvent(new w.Event('change', { bubbles: true }));
  await tick();
  assert.ok(
    q(w, '[data-act="replan-submit"]').textContent.includes('1 天'),
    '按钮应显示已选 1 天'
  );

  // 填要求并提交
  q(w, '#replan-req').value = '改成室内';
  click(w, '[data-act="replan-submit"]');
  await tick(40);

  const preview = q(w, '#replan-host .replan-preview');
  assert.ok(preview, '应显示待确认的改动');
  assert.ok(preview.textContent.includes('AI-2'), '应显示改为 AI-2：' + preview.textContent);

  // 确认
  const before = names(w).slice();
  click(w, '[data-act="replan-apply"]');
  await tick(60);
  const after = names(w);
  assert.ok(after.includes('AI-2'), '第 2 天应已换成 AI-2：' + JSON.stringify(after));
  assert.strictEqual(after[0], before[0], '第 1 天不该变');

  // 撤销
  click(w, '[data-act="undo"]');
  await tick();
  assert.ok(!names(w).includes('AI-2'), '撤销后应恢复');

  // 撤销会停在编辑态（既有行为），回到查看态，后面步骤要用工具条
  assert.ok(q(w, '[data-act="save-edit"]'), '撤销后应处于编辑态');
  click(w, '[data-act="save-edit"]');
  await tick(40);
});

step('⑰c 装备清单：规划完就能看到，每条带原因', async (w) => {
  const card = q(w, '.packing-card');
  assert.ok(card, '应显示装备清单卡片');
  const text = card.textContent;
  assert.ok(text.includes('折叠伞'), '清单里应有伞：' + text.slice(0, 80));
  assert.ok(text.includes('11-01 有雨'), '应说明为什么带：' + text.slice(0, 80));
  assert.ok(text.includes('有雨 1 天'), '应有摘要');
});

step('⑰d 导出 .ics：点按钮会请求导出接口并触发下载', async (w) => {
  const downloads = [];
  const origCreate = w.document.createElement.bind(w.document);
  w.document.createElement = (tag) => {
    const el = origCreate(tag);
    if (String(tag).toLowerCase() === 'a') {
      el.click = () => downloads.push(el.download);  // jsdom 不真下载，记下文件名
    }
    return el;
  };

  click(w, '[data-act="export-ics"]');
  await tick(60);

  const call = w.__calls.find((c) => c.kind === 'ics');
  assert.ok(call, '应请求导出接口：' + JSON.stringify(w.__calls));
  assert.ok(call.url.includes('/export.ics'), call.url);
  assert.strictEqual(downloads.length, 1, '应触发一次下载');
  assert.ok(downloads[0].endsWith('.ics'), '文件名应以 .ics 结尾：' + downloads[0]);
});

step('⑰e 分享：生成只读链接并能复制', async (w) => {
  click(w, '[data-act="share"]');
  await tick(60);

  const box = q(w, '.share-card');
  assert.ok(box, '应出现分享卡片');
  const value = q(w, '#share-url').value;
  assert.ok(value.includes('?share='), '链接应带 share 参数：' + value);
  assert.ok(value.includes('tok-abc123456789xyz'), value);
});

step('⑰g 复制 Markdown：剪贴板被拒时要有兜底，而不是只报失败', async (w) => {
  // 环境①：clipboard 可用
  const written = [];
  Object.defineProperty(w.navigator, 'clipboard', {
    configurable: true,
    value: { writeText: async (t) => written.push(t) },
  });
  Object.defineProperty(w, 'isSecureContext', { configurable: true, value: true });

  click(w, '[data-act="copy-md"]');
  await tick(60);
  assert.strictEqual(written.length, 1, '应写入剪贴板一次');
  assert.ok(written[0].includes(state_md_ok(w)), '剪贴板内容应是 Markdown：' + written[0].slice(0, 60));

  // 环境②：clipboard 拒绝，execCommand 可用
  Object.defineProperty(w.navigator, 'clipboard', {
    configurable: true,
    value: { writeText: async () => { throw new Error('NotAllowedError'); } },
  });
  const copied = [];
  w.document.execCommand = (cmd) => {
    copied.push(cmd);
    return cmd === 'copy';
  };
  w.__resetToast();
  click(w, '[data-act="copy-md"]');
  await tick(60);
  assert.ok(copied.includes('copy'), '应退到 execCommand：' + JSON.stringify(copied));
  assert.strictEqual(q(w, '#copy-fallback').hidden, true, '兜底成功时不该弹面板');

  // 环境③：两条路都不通 → 摊出内容
  Object.defineProperty(w.navigator, 'clipboard', { configurable: true, value: undefined });
  w.document.execCommand = () => false;
  click(w, '[data-act="copy-md"]');
  await tick(60);
  const host = q(w, '#copy-fallback');
  assert.strictEqual(host.hidden, false, '都失败时应显示手动复制面板');
  const ta = q(w, '#copy-fallback-text');
  assert.ok(ta && ta.value.length > 20, '面板里应有可复制的正文');
  assert.ok(ta.value.includes('#'), '内容应是 Markdown：' + ta.value.slice(0, 40));

  // 收起
  click(w, '[data-act="copy-fallback-close"]');
  await tick(20);
  assert.strictEqual(q(w, '#copy-fallback').hidden, true, '收起后应隐藏');
});

step('⑱ 删除已保存的行程 → 列表清空', async (w) => {
  click(w, '[data-act="trip-del"]');
  await tick(120);
  assert.strictEqual(w.__db.size, 0, '服务端应删除');
  assert.strictEqual(qa(w, '.recent-item').length, 0, '列表应清空');
});

step('⑲「重新规划」清空结果', async (w) => {
  click(w, '[data-act="restart"]');
  await tick();
  assert.strictEqual(q(w, '#result').innerHTML.trim(), '');
  assert.strictEqual(q(w, '#toolbar').hidden, true);
});

step('⑳a 新字段：预算与其他要求要进请求体（空预算 → null）', async (w) => {
  const lastPlan = () => {
    const posts = w.__calls.filter((c) => c.kind === 'plan');
    assert.ok(posts.length >= 1, '应有规划请求');
    return JSON.parse(posts[posts.length - 1].body || '{}');
  };

  // 填 → 提交 → 断言
  click(w, '[data-act="restart"]');
  await tick(40);
  q(w, '#budget_total').value = '2000';
  q(w, '#notes').value = '  想住舒适一点的酒店  ';
  click(w, '#submit');
  await tick(120);

  const body = lastPlan();
  assert.strictEqual(body.budget_total, 2000, '预算应传数字：' + JSON.stringify(body));
  assert.strictEqual(body.notes, '想住舒适一点的酒店', '备注应去首尾空格：' + JSON.stringify(body.notes));

  // 清空 → 提交 → 应该是 null，不是 0
  click(w, '[data-act="restart"]');
  await tick(40);
  q(w, '#budget_total').value = '';
  q(w, '#notes').value = '';
  click(w, '#submit');
  await tick(120);

  const body2 = lastPlan();
  assert.strictEqual(body2.budget_total, null, '空预算应为 null：' + JSON.stringify(body2));
  assert.strictEqual(body2.notes, '', '空备注应为空串');
});

step('⑳b 超预算时显示提示卡片（说清超多少）', async (w) => {
  w.__nextPlanOverrides = {
    budget_check: {
      limit: 2000, total: 2400, over: 400, ok: false,
      message: '预算 2000 元，合计 2400 元，超出 400 元。四项里住宿占得最多（1400 元），可从这一项压缩。',
    },
  };
  click(w, '[data-act="restart"]');
  await tick(40);
  q(w, '#budget_total').value = '2000';
  click(w, '#submit');
  await tick(150);

  const card = q(w, '.budget-warn');
  assert.ok(card, '应显示超预算提示');
  assert.ok(card.textContent.includes('400'), '应写出超出金额：' + card.textContent.slice(0, 60));
  assert.ok(card.textContent.includes('2000'), '应写出上限');
});

// 清理这两步造出来的数据，避免影响后面的只读测试
step('⑳c 清理：删掉刚才多存的行程', async (w) => {
  if (w.__db && w.__db.size) {
    for (const id of Array.from(w.__db.keys())) {
      await w.fetch(`/api/trips/${id}`, { method: 'DELETE' });
    }
  }
});

step('⑳ 只读分享模式：隐藏编辑入口，仍能看到行程（放在最后，因为只读是终态）', async (w) => {
  await w.enterShareMode('tok-abc123456789xyz');
  await tick(60);

  assert.strictEqual(q(w, '[data-act="edit"]'), null, '只读模式下不该有编辑按钮');
  assert.ok(q(w, '.timeline .day'), '仍应渲染行程');
  const banner = q(w, '#share-banner');
  assert.ok(banner && banner.hidden === false, '应显示只读横幅');
});

step('㉑ 带 ?share= 打开：启动路径就要进只读模式（不是只有函数存在）', async () => {
  const w2 = makeDom('http://127.0.0.1:8000/?share=tok-abc123456789xyz');
  await tick(200);

  assert.ok(
    w2.document.body.classList.contains('share-mode'),
    'body 应带 share-mode 类',
  );
  assert.strictEqual(q(w2, '[data-act="edit"]'), null, '只读模式下不该有编辑按钮');
  assert.ok(q(w2, '.timeline .day'), '应渲染出分享的行程');
  const banner = q(w2, '#share-banner');
  assert.ok(banner && banner.hidden === false, '应显示只读横幅');
  assert.ok(
    banner.textContent.includes('只读分享'),
    '横幅文案应说明是只读：' + banner.textContent,
  );
});

// --------------------------------------------------------------- 执行 --
(async () => {
  const window = makeDom();
  let passed = 0;
  const failed = [];
  for (const [name, fn] of steps) {
    try {
      await fn(window);
      passed += 1;
      console.log(`  [PASS] ${name}`);
    } catch (err) {
      failed.push(name);
      console.log(`  [FAIL] ${name}`);
      console.log(`         ${String(err.message).split('\n')[0]}`);
    }
  }
  window.close();
  console.log('');
  if (failed.length) {
    console.log(`失败 ${failed.length} / 通过 ${passed}`);
    process.exit(1);
  }
  console.log(`全部通过：${passed} 步`);
})();
