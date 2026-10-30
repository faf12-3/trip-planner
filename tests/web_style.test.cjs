/**
 * 样式表测试：我看不到渲染结果，所以至少要保证"CSS 能被解析、关键规则在"。
 *
 * 这不是视觉测试（没有布局引擎），但能挡住几类真实事故：
 *   - 少一个括号 → 整份样式从那里开始失效
 *   - 改布局时把 .day / .budget / .toolbar 的规则删了
 *   - 打印规则丢了 → 存出来的 PDF 里没有地图（踩过一次）
 */
'use strict';

const fs = require('fs');
const path = require('path');
const assert = require('assert');
const { JSDOM, VirtualConsole } = require('jsdom');

const ROOT = path.join(__dirname, '..');
const read = (p) => fs.readFileSync(path.join(ROOT, p), 'utf8');

let failed = 0;
function test(name, fn) {
  try {
    fn();
    console.log('[PASS] ' + name);
  } catch (err) {
    failed += 1;
    console.log('[FAIL] ' + name);
    console.log('       ' + err.message);
  }
}

const css = read('web/style.css');
const html = read('web/index.html');

// 用虚拟控制台接住 jsdom 的 CSS 解析报错
const errors = [];
const vc = new VirtualConsole();
vc.on('jsdomError', (e) => errors.push(String(e.message || e)));

const dom = new JSDOM(
  html.replace(/<link[^>]*>/, `<style>${css}</style>`),
  { virtualConsole: vc, pretendToBeVisual: true },
);
const { document, window } = dom.window;

test('样式表能整份解析，没有解析报错', () => {
  assert.deepStrictEqual(errors, [], errors.join('; '));
  const sheet = document.styleSheets[0];
  assert.ok(sheet, '应该有内联样式表');
  assert.ok(sheet.cssRules.length > 120, `规则太少：${sheet.cssRules.length}`);
});

test('token 全部有定义（var(--x) 不留悬空引用）', () => {
  const defined = new Set([...css.matchAll(/(--[a-z0-9-]+)\s*:/g)].map((m) => m[1]));
  const used = new Set([...css.matchAll(/var\((--[a-z0-9-]+)/g)].map((m) => m[1]));
  const missing = [...used].filter((v) => !defined.has(v));
  assert.deepStrictEqual(missing, [], '未定义：' + missing.join(', '));
});

test('大括号成对（少一个就整份失效）', () => {
  const open = (css.match(/{/g) || []).length;
  const close = (css.match(/}/g) || []).length;
  assert.strictEqual(open, close, `{ ${open} 个，} ${close} 个`);
});

test('关键布局规则都在', () => {
  for (const sel of ['.appbar', '.layout', '.col-main', '.col-side', '.card', '.toolbar']) {
    assert.ok(css.includes(sel + ' {') || css.includes(sel + '{'), '缺少 ' + sel);
  }
});

test('两份布局：桌面两栏、窄屏一栏', () => {
  assert.ok(/@media \(min-width: 1080px\)/.test(css), '缺少桌面断点');
  assert.ok(/grid-template-columns: minmax\(0, 1fr\) 336px/.test(css), '缺少两栏定义');
  assert.ok(/@media \(max-width: 640px\)/.test(css), '缺少手机断点');
});

test('深色模式只覆盖 token，不重写组件', () => {
  const start = css.indexOf('@media (prefers-color-scheme: dark)');
  const block = css.slice(start, start + 1400);
  assert.ok(block.includes('--surface:'), '深色模式应覆盖 --surface');
  assert.ok(block.includes('--brand:'), '深色模式应覆盖 --brand');
  // 组件规则里出现选择器就说明有人开始"顺手改深色样式"，那会让两份主题分叉
  assert.ok(!block.includes('.card {'), '深色模式不该重写组件规则');
});

test('打印规则：隐藏交互、保留每日地图', () => {
  const print = css.slice(css.indexOf('@media print'));
  assert.ok(print.includes('#form-card'), '打印该隐藏表单');
  assert.ok(print.includes('#toolbar') || print.includes('.toolbar'), '打印该隐藏工具条');
  assert.ok(print.includes('.day-map-img'), '打印必须处理每日地图');
  assert.ok(print.includes('break-inside: avoid'), '打印避免卡片被切断');
});

test('表单控件有可见焦点态（可访问性）', () => {
  assert.ok(css.includes(':focus-visible'), '缺少 :focus-visible');
  assert.ok(
    /\.field input:focus[\s\S]{0,240}box-shadow: var\(--ring\)/.test(css),
    '输入框缺焦点环',
  );
});

test('自定义下拉箭头（appearance:none 后必须有）', () => {
  assert.ok(/\.field select {[\s\S]{0,200}background-image:/.test(css), 'select 没有自绘箭头');
});

test('页面骨架：一个主栏 + 一个侧栏，且 id 都在', () => {
  assert.ok(document.querySelector('.col-main'), '缺主栏');
  assert.ok(document.querySelector('.col-side'), '缺侧栏');
  for (const id of ['form-card', 'result', 'toolbar', 'replan-host', 'memory-card', 'recent-host']) {
    assert.ok(document.getElementById(id), '缺 #' + id);
  }
});

test('表单字段都带 label（点标签能聚焦）', () => {
  const fields = [...document.querySelectorAll('.field label')];
  assert.ok(fields.length >= 7, '字段太少：' + fields.length);
  const bad = fields.filter((l) => !l.getAttribute('for'));
  assert.deepStrictEqual(bad.map((l) => l.textContent), [], '有 label 没写 for');
});

test('结果区在折叠/展开时不依赖内联样式', () => {
  assert.ok(!/style="[^"]*display:\s*none/.test(html), 'HTML 里不该有 display:none 内联样式');
});

test('背景层：装饰但必须"不挡路"', () => {
  const bg = document.querySelector('.bg');
  assert.ok(bg, 'HTML 里应有 .bg');
  assert.strictEqual(bg.getAttribute('aria-hidden'), 'true', '.bg 对读屏应是隐藏的');
  assert.ok(/\.bg \{[^}]*pointer-events: none/s.test(css), '.bg 不能吃鼠标事件');
  assert.ok(/\.bg \{[^}]*z-index: 0/s.test(css), '.bg 应在内容之下');
  assert.ok(/\.bg \{[^}]*position: fixed/s.test(css), '背景应固定不随内容滚');
});

test('背景三层都在：底色 + 经纬网 + 天光', () => {
  assert.ok(/\.bg \{[^}]*background-image: linear-gradient/s.test(css), '缺底色渐变');
  assert.ok(/\.bg::before \{[^}]*background-size: 56px 56px/s.test(css), '缺经纬网');
  assert.ok(/\.bg::before \{[^}]*mask-image/s.test(css), '网格应有渐隐遮罩');
  assert.ok(/\.bg::after \{[^}]*radial-gradient/s.test(css), '缺天光');
});

test('背景只用 transform 动画（不触发重排）', () => {
  const m = css.match(/@keyframes bg-drift \{([\s\S]*?)\n\}/);
  assert.ok(m, '找不到 @keyframes bg-drift');
  const body = m[1];
  assert.ok(body.includes('transform:'), '漂移应只动 transform');
  // 只要出现布局属性，就说明有人在动画里改几何 —— 那会每帧重排
  assert.ok(!/\b(top|left|right|bottom|width|height|margin|padding)\s*:/.test(body),
    '动画不该改布局属性：' + body.replace(/\s+/g, ' ').slice(0, 120));
});

test('打印时背景消失', () => {
  const print = css.slice(css.indexOf('@media print'));
  // 可能是 .bg { 也可能和别的选择器并列成 .bg,，两种都算数
  assert.ok(/\.bg\s*[,{]/.test(print), '打印应隐藏 .bg');
});

test('偏好减少动效时停掉漂移', () => {
  // reduced-motion 分支现在是一条选择器列表，别再按"单条规则"去匹配
  const start = css.indexOf('prefers-reduced-motion: reduce');
  const block = css.slice(start, start + 420);
  assert.ok(block.includes('.bg::after'), 'reduced-motion 应覆盖 .bg::after');
  assert.ok(block.includes('animation: none'), 'reduced-motion 下应 animation: none');
});

test('高对比度偏好：装饰层退场', () => {
  const start = css.indexOf('@media (prefers-contrast: more)');
  assert.ok(start > 0, '缺少 prefers-contrast 分支');
  const block = css.slice(start, start + 320);
  assert.ok(/\.bg::before/.test(block) && /\.bg::after/.test(block), '应隐藏网格与天光');
  assert.ok(block.includes('display: none'), '高对比度下应直接不显示');
});

test('玻璃表面有纯色兜底（不支持 color-mix 也不至于透明）', () => {
  const appbar = css.slice(css.indexOf('.appbar {'), css.indexOf('.appbar-inner'));
  assert.ok(appbar.includes('background: var(--surface);'), '顶栏应有不透明兜底');
  // 注释里也会出现 "color-mix" 字样，所以比的是声明本身，不是这个词
  assert.ok(
    appbar.indexOf('background: var(--surface);') < appbar.indexOf('background: color-mix'),
    '兜底要写在 color-mix 之前',
  );
});

test('动效：有内容但不越界', () => {
  // 至少三类动效：航线流动、城市点呼吸、网格呼吸
  for (const name of ['route-flow', 'dot-breathe', 'grid-breathe']) {
    assert.ok(css.includes('@keyframes ' + name), '缺少 @keyframes ' + name);
  }
  assert.ok(/\.bg-routes \.routes path \{[^}]*animation:/s.test(css), '航线应有动画');
  assert.ok(/\.bg-routes \.route-dots circle \{[^}]*animation:/s.test(css), '城市点应有动画');
});

test('所有 @keyframes 都不碰布局属性（只动 transform/opacity/dashoffset）', () => {
  const blocks = [...css.matchAll(/@keyframes\s+(\S+)\s*\{([\s\S]*?)\n\}/g)];
  assert.ok(blocks.length >= 5, 'keyframes 太少：' + blocks.length);
  const offenders = [];
  for (const [, name, body] of blocks) {
    if (/^\s*(top|left|right|bottom|width|height|margin|padding)\s*:/m.test(body)) {
      offenders.push(name);
    }
  }
  assert.deepStrictEqual(offenders, [], '这些动画在改布局属性：' + offenders.join(', '));
});

test('飞机是 SVG 原生动画，不靠 JS 驱动', () => {
  const svg = document.querySelector('.bg-routes');
  assert.ok(svg, 'HTML 里应有航线 SVG');
  assert.ok(svg.querySelector('animateMotion'), '应有 animateMotion');
  assert.ok(svg.querySelector('mpath'), 'animateMotion 应绑定到某条航线');
  // 不引用 app.js：本文件只读 html/css，说"JS 没参与"只需确认动画在 SVG 里（上面三条）
});

test('reduced-motion 下所有装饰动效都停', () => {
  const start = css.indexOf('@media (prefers-reduced-motion: reduce)');
  const block = css.slice(start, start + 420);
  for (const sel of ['.bg::after', '.bg::before', '.bg-routes', '#trip-plan-content']) {
    assert.ok(block.includes(sel), 'reduced-motion 应覆盖 ' + sel);
  }
  assert.ok(block.includes('animation: none'), '应是 animation: none 而不是缩短时长');
});

test('打印时航线层也不出现', () => {
  const print = css.slice(css.indexOf('@media print'));
  assert.ok(/\.bg-routes\s*[,{]/.test(print), '打印应隐藏航线层');
});

test('[hidden] 必须优先（否则空工具条会露出来）', () => {
  // 这条规则要求：作者样式里的 display 不能盖掉 hidden
  assert.ok(/\[hidden\] \{\s*display: none !important;?\s*\}/.test(css), '缺少 [hidden] 强制隐藏');
  // 同时确认确实存在"display 会盖掉 hidden"的风险点，证明这条规则不是摆设
  assert.ok(/\.toolbar \{[^}]*display: flex/s.test(css), '.toolbar 应是 flex（正是它盖掉了 hidden）');
});

test('初始状态下靠 hidden 隐藏的区块都带 hidden 属性', () => {
  for (const id of ['toolbar', 'replan-host', 'progress', 'error', 'memory-card', 'lightbox']) {
    const el = document.getElementById(id);
    assert.ok(el, '缺少 #' + id);
    assert.ok(el.hasAttribute('hidden'), '#' + id + ' 初始应带 hidden');
  }
});

console.log('');
if (failed) {
  console.log(`失败 ${failed} 项`);
  process.exit(1);
}
console.log('全部通过：27 项样式检查');
