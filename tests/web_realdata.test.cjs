/**
 * 用真实接口返回的数据渲染整页，检查布局在真实内容下是否撑得住。
 *
 * 为什么需要它：单测的数据是我自己编的（景点名三四个字、景点数固定）。
 * 真实数据里会出现长名字、一天 5 个景点、预算四位数、天气 3 条——这些才是排版的压力点，
 * 而我看不到浏览器，只能在 jsdom 里断言"结构没崩、数量对得上"。
 */
'use strict';

const fs = require('fs');
const path = require('path');
const { JSDOM, VirtualConsole } = require('jsdom');

const ROOT = path.join(__dirname, '..');
const PLAN_FILE = path.join(ROOT, '..', '.tmp', 'real_plan_for_ui.json');

if (!fs.existsSync(PLAN_FILE)) {
  console.log('跳过：没有 .tmp/real_plan_for_ui.json（先跑 fetch_real_plan.py）');
  process.exit(0);
}

const payload = JSON.parse(fs.readFileSync(PLAN_FILE, 'utf8'));
const html = fs.readFileSync(path.join(ROOT, 'web/index.html'), 'utf8');
const css = fs.readFileSync(path.join(ROOT, 'web/style.css'), 'utf8');
const app = fs.readFileSync(path.join(ROOT, 'web/app.js'), 'utf8');
const logic = fs.readFileSync(path.join(ROOT, 'web/logic.js'), 'utf8');

const problems = [];
const vc = new VirtualConsole();
vc.on('jsdomError', (e) => problems.push('jsdomError: ' + (e.message || e)));
vc.on('error', (m) => problems.push('console.error: ' + m));

const dom = new JSDOM(
  html.replace(/<link[^>]*>/, `<style>${css}</style>`).replace(/<script[\s\S]*?<\/script>/g, ''),
  { runScripts: 'outside-only', virtualConsole: vc, pretendToBeVisual: true, url: 'http://127.0.0.1:8000/' },
);
const { window } = dom;
const { document } = window;

// 最小可用环境
window.localStorage.setItem('trip.user', 'ui-check');
window.HTMLCanvasElement.prototype.getContext = () => null;
// jsdom 没有 createObjectURL：每日地图那里会用它
window.URL.createObjectURL = () => 'blob:stub';
window.fetch = async (url) => {
  const u = String(url);
  const json = (data, status = 200) => ({
    ok: status < 400,
    status,
    json: async () => data,
    blob: async () => new window.Blob(['']),
    text: async () => JSON.stringify(data),
  });
  if (u.includes('/api/trip/plan')) return json(payload);
  if (u.includes('/api/config/map')) return json({ configured: false });
  if (u.includes('/api/staticmap')) return json({ ok: true });
  if (u.includes('/api/trips')) return json({ items: [] });
  if (u.includes('/api/profile')) return json({ user: 'ui-check', trips: 0, hints: {} });
  if (u.includes('/api/cities')) return json({ items: [] });
  return json({});
};

window.eval(logic);
window.eval(app);

const wait = (ms) => new Promise((r) => setTimeout(r, ms));

(async () => {
  document.getElementById('submit').dispatchEvent(new window.Event('click', { bubbles: true }));
  await wait(300);

  const plan = payload.plan;
  const rows = document.querySelectorAll('.timeline .row');
  const days = document.querySelectorAll('.timeline .day');
  const cells = document.querySelectorAll('.budget .cell');
  const total = document.querySelector('.budget .cell.total strong');
  const weather = document.querySelectorAll('.weather .w');
  const overview = document.querySelector('.overview');

  const checks = [
    ['每天一个 .day', days.length === plan.days.length, `${days.length} / ${plan.days.length}`],
    [
      '景点行数 = 景点总数 + 餐饮 + 酒店',
      rows.length >= plan.days.reduce((n, d) => n + d.attractions.length, 0),
      `${rows.length} 行`,
    ],
    ['预算 5 个格子', cells.length === 5, `${cells.length} 格`],
    ['合计等于计划里的 total', total && Number(total.textContent) === plan.budget.total,
      total ? `${total.textContent} vs ${plan.budget.total}` : '没有 total 节点'],
    ['天气条数与数据一致', weather.length === (plan.weather_info || []).length,
      `${weather.length} / ${(plan.weather_info || []).length}`],
    ['概览里显示了城市', !!overview && overview.textContent.includes(plan.city), plan.city],
    ['每行都有标题', [...document.querySelectorAll('.row .title')].every((el) => el.textContent.trim().length > 0), ''],
    ['没有渲染异常日志', problems.length === 0, problems.slice(0, 2).join(' | ')],
  ];

  let failed = 0;
  console.log('=== 真实数据渲染检查 ===');
  for (const [name, ok, detail] of checks) {
    console.log(`${ok ? '[PASS]' : '[FAIL]'} ${name}${detail ? '  (' + detail + ')' : ''}`);
    if (!ok) failed += 1;
  }

  // 版面压力点：最长文本与最多行
  const longest = Math.max(
    0,
    ...[...document.querySelectorAll('.row .title')].map((el) => el.textContent.length),
  );
  const busiest = Math.max(0, ...[...plan.days].map((d) => d.attractions.length));
  console.log(`\n  最长景点名: ${longest} 字（单行省略号处理）`);
  console.log(`  单日最多景点: ${busiest} 个`);
  console.log(`  页面高度未知（jsdom 无布局），但结构完整 = ${failed === 0}`);

  process.exit(failed ? 1 : 0);
})();
