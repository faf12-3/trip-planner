// 前端纯逻辑的单元测试（Node 直接跑，不需要浏览器）。
//   node tests/web_logic.test.cjs

const assert = require('node:assert');
const path = require('node:path');

const logic = require(path.join(__dirname, '..', 'web', 'logic.js'));

let passed = 0;
const failures = [];

function test(name, fn) {
  try {
    fn();
    passed += 1;
    console.log(`  [PASS] ${name}`);
  } catch (err) {
    failures.push([name, err]);
    console.log(`  [FAIL] ${name}`);
    console.log(`         ${err.message.split('\n')[0]}`);
  }
}

function plan(days) {
  return {
    city: '南京',
    start_date: '2026-12-22',
    end_date: '2026-12-23',
    days,
    budget: {
      total_attractions: 999,
      total_hotels: 999,
      total_meals: 999,
      total_transportation: 60,
      total: 999,
    },
  };
}

console.log('=== recomputeBudget ===');

test('门票按明细求和，忽略模型给的旧值', () => {
  const p = plan([
    {
      date: '2026-12-22',
      attractions: [{ name: 'A', ticket_price: 60 }, { name: 'B', ticket_price: 30 }],
      meals: [],
      hotel: null,
    },
  ]);
  const b = logic.recomputeBudget(p);
  assert.strictEqual(b.total_attractions, 90);
  assert.notStrictEqual(b.total_attractions, 999);
});

test('住宿只在有真实酒店名时计入', () => {
  const p = plan([
    { date: '1', attractions: [], meals: [], hotel: { name: '如家', estimated_cost: 300 } },
    { date: '2', attractions: [], meals: [], hotel: { name: '无', estimated_cost: 500 } },
    { date: '3', attractions: [], meals: [], hotel: null },
  ]);
  const b = logic.recomputeBudget(p);
  assert.strictEqual(b.total_hotels, 300, '占位名"无"不该计入');
});

test('餐饮跨天累加', () => {
  const p = plan([
    { date: '1', attractions: [], meals: [{ name: '早', estimated_cost: 20 }], hotel: null },
    { date: '2', attractions: [], meals: [{ name: '午', estimated_cost: 50 }, { name: '晚', estimated_cost: 80 }], hotel: null },
  ]);
  assert.strictEqual(logic.recomputeBudget(p).total_meals, 150);
});

test('合计 = 四项之和', () => {
  const p = plan([
    {
      date: '1',
      attractions: [{ name: 'A', ticket_price: 60 }],
      meals: [{ name: '午', estimated_cost: 40 }],
      hotel: { name: '如家', estimated_cost: 200 },
    },
  ]);
  const b = logic.recomputeBudget(p);
  assert.strictEqual(b.total, 60 + 40 + 200 + 60);
  assert.strictEqual(b.total, b.total_attractions + b.total_hotels + b.total_meals + b.total_transportation);
});

test('交通沿用服务端值（无法从明细推导）', () => {
  const p = plan([{ date: '1', attractions: [], meals: [], hotel: null }]);
  assert.strictEqual(logic.recomputeBudget(p).total_transportation, 60);
});

test('空明细不报错', () => {
  const p = plan([]);
  const b = logic.recomputeBudget(p);
  assert.strictEqual(b.total, 60);
});

test('非法数字按 0 处理', () => {
  const p = plan([
    { date: '1', attractions: [{ name: 'A', ticket_price: 'abc' }], meals: [], hotel: null },
  ]);
  assert.strictEqual(logic.recomputeBudget(p).total_attractions, 0);
});

console.log('\n=== 排序 ===');

test('上移交换相邻两项', () => {
  const p = plan([
    { date: '1', attractions: [{ name: 'A' }, { name: 'B' }, { name: 'C' }], meals: [], hotel: null },
  ]);
  assert.strictEqual(logic.moveAttraction(p, 0, 2, -1), true);
  assert.deepStrictEqual(p.days[0].attractions.map((a) => a.name), ['A', 'C', 'B']);
});

test('第一个不能再上移', () => {
  const p = plan([{ date: '1', attractions: [{ name: 'A' }, { name: 'B' }], meals: [], hotel: null }]);
  assert.strictEqual(logic.moveAttraction(p, 0, 0, -1), false);
  assert.deepStrictEqual(p.days[0].attractions.map((a) => a.name), ['A', 'B']);
});

test('最后一个不能再下移', () => {
  const p = plan([{ date: '1', attractions: [{ name: 'A' }, { name: 'B' }], meals: [], hotel: null }]);
  assert.strictEqual(logic.moveAttraction(p, 0, 1, 1), false);
});

test('越界索引不崩', () => {
  const p = plan([{ date: '1', attractions: [], meals: [], hotel: null }]);
  assert.strictEqual(logic.moveAttraction(p, 5, 0, 1), false);
  assert.strictEqual(logic.moveAttraction(null, 0, 0, 1), false);
});

console.log('\n=== 餐饮补位 ===');

test('缺哪顿补哪顿', () => {
  assert.strictEqual(logic.nextMealType({ meals: [{ type: 'breakfast' }] }), 'lunch');
  assert.strictEqual(logic.nextMealType({ meals: [{ type: 'breakfast' }, { type: 'lunch' }, { type: 'dinner' }] }), 'snack');
  assert.strictEqual(logic.nextMealType({ meals: [] }), 'breakfast');
});

console.log('\n=== 校验 ===');

test('空名字会被指出来', () => {
  const p = plan([
    { date: '1', attractions: [{ name: '' }], meals: [{ name: '' }], hotel: null },
  ]);
  const issues = logic.validate(p);
  assert.ok(issues.some((i) => i.includes('没填名字')));
});

test('没有景点会被指出来', () => {
  const p = plan([{ date: '1', attractions: [], meals: [], hotel: null }]);
  assert.ok(logic.validate(p).some((i) => i.includes('没有景点')));
});

test('正常行程没有问题', () => {
  const p = plan([
    { date: '1', attractions: [{ name: 'A' }], meals: [{ name: '午餐', type: 'lunch' }], hotel: null },
  ]);
  assert.deepStrictEqual(logic.validate(p), []);
});

console.log('\n=== Markdown 生成 ===');

test('Markdown 反映当前数据（编辑后不是旧内容）', () => {
  const p = plan([
    {
      date: '2026-12-22',
      description: '首日',
      transportation: '自驾',
      hotel: { name: '如家', estimated_cost: 200 },
      attractions: [{ name: '南京博物院', visit_duration: 180, ticket_price: 0 }],
      meals: [{ type: 'lunch', name: '鸭血粉丝', estimated_cost: 30 }],
    },
  ]);
  const md = logic.toMarkdown(p);
  assert.ok(md.includes('# 南京'));
  assert.ok(md.includes('南京博物院'));
  assert.ok(md.includes('交通（自驾）：60 元'));
  assert.ok(md.includes('合计'));
});

test('没有真实酒店名时不写住宿行', () => {
  const p = plan([
    { date: '1', transportation: '步行', hotel: { name: '无', estimated_cost: 300 }, attractions: [{ name: 'A' }], meals: [] },
  ]);
  // 只看「- 住宿：」这种列表行，别被预算里的「酒店住宿：300 元」误伤
  const lines = logic.toMarkdown(p).split('\n');
  assert.ok(!lines.some((line) => line.startsWith('- 住宿：')), lines.join('|'));
});

test('空计划不抛错', () => {
  assert.strictEqual(logic.toMarkdown(null), '');
  assert.ok(logic.toMarkdown({ city: 'X', days: [] }).includes('# X'));
});

console.log('\n=== 冲突检测 ===');

function dayWith(atts) {
  return { date: '2026-12-22', attractions: atts, meals: [], hotel: null };
}
function att(name, minutes, lng = 116.4, lat = 39.9) {
  return { name, visit_duration: minutes, location: { longitude: lng, latitude: lat } };
}

test('单日超过 10 小时 -> warn', () => {
  const p = plan([dayWith([att('A', 300), att('B', 400)])]);
  const issues = logic.detectConflicts(p);
  const over = issues.filter((i) => i.type === 'overload');
  assert.strictEqual(over.length, 1);
  assert.strictEqual(over[0].level, 'warn');
  assert.ok(over[0].message.includes('超过 10 小时'));
});

test('超过 12 小时 -> error（更严重一档）', () => {
  const p = plan([dayWith([att('A', 400), att('B', 400)])]);
  const over = logic.detectConflicts(p).filter((i) => i.type === 'overload');
  assert.strictEqual(over[0].level, 'error');
});

test('刚好 10 小时不报', () => {
  const p = plan([dayWith([att('A', 300), att('B', 300)])]);
  assert.deepStrictEqual(logic.detectConflicts(p), []);
});

test('相邻景点超过 40km -> far-jump', () => {
  const p = plan([dayWith([att('A', 60, 116.4, 39.9), att('B', 60, 117.2, 39.9)])]);
  const far = logic.detectConflicts(p).filter((i) => i.type === 'far-jump');
  assert.strictEqual(far.length, 1);
  assert.ok(far[0].message.includes('km'));
});

test('近距离不报', () => {
  const p = plan([dayWith([att('A', 60, 116.400, 39.900), att('B', 60, 116.410, 39.905)])]);
  assert.deepStrictEqual(logic.detectConflicts(p), []);
});

test('同一景点重复出现 -> duplicate', () => {
  const p = plan([dayWith([att('故宫', 60), att('故宫', 60)])]);
  const dup = logic.detectConflicts(p).filter((i) => i.type === 'duplicate');
  assert.strictEqual(dup.length, 1);
  assert.ok(dup[0].message.includes('故宫'));
});

test('没有坐标的景点不参与距离判断（不崩）', () => {
  const p = plan([dayWith([{ name: 'A', visit_duration: 60 }, { name: 'B', visit_duration: 60 }])]);
  assert.deepStrictEqual(logic.detectConflicts(p), []);
});

test('空计划不报错', () => {
  assert.deepStrictEqual(logic.detectConflicts(null), []);
  assert.deepStrictEqual(logic.detectConflicts({ days: [] }), []);
});

test('haversine 距离量级正确（北京到上海约 1060km）', () => {
  const km = logic.haversineKm({ lng: 116.407, lat: 39.904 }, { lng: 121.473, lat: 31.230 });
  assert.ok(km > 1000 && km < 1120, `实际 ${Math.round(km)} km`);
});

console.log('\n=== 拖拽移动（纯函数）===');

function plan2() {
  return plan([
    { date: '1', attractions: [{ name: 'A' }, { name: 'B' }, { name: 'C' }], meals: [], hotel: null },
    { date: '2', attractions: [{ name: 'X' }], meals: [], hotel: null },
  ]);
}

test('同一天：把第 1 个拖到第 3 个的位置', () => {
  const p = plan2();
  assert.strictEqual(logic.moveAttractionTo(p, 0, 0, 0, 2), true);
  assert.deepStrictEqual(p.days[0].attractions.map((a) => a.name), ['B', 'C', 'A']);
});

test('同一天：把最后一个拖到最前', () => {
  const p = plan2();
  assert.strictEqual(logic.moveAttractionTo(p, 0, 2, 0, 0), true);
  assert.deepStrictEqual(p.days[0].attractions.map((a) => a.name), ['C', 'A', 'B']);
});

test('拖到自己身上不动，且返回 false', () => {
  const p = plan2();
  assert.strictEqual(logic.moveAttractionTo(p, 0, 1, 0, 1), false);
  assert.deepStrictEqual(p.days[0].attractions.map((a) => a.name), ['A', 'B', 'C']);
});

test('跨天：第 1 天的 A 拖到第 2 天', () => {
  const p = plan2();
  assert.strictEqual(logic.moveAttractionTo(p, 0, 0, 1, 1), true);
  assert.deepStrictEqual(p.days[0].attractions.map((a) => a.name), ['B', 'C']);
  assert.deepStrictEqual(p.days[1].attractions.map((a) => a.name), ['X', 'A']);
});

test('跨天拖到空列表', () => {
  const p = plan2();
  p.days[1].attractions = [];
  assert.strictEqual(logic.moveAttractionTo(p, 0, 1, 1, 0), true);
  assert.deepStrictEqual(p.days[1].attractions.map((a) => a.name), ['B']);
});

test('越界索引不崩、不改数据', () => {
  const p = plan2();
  assert.strictEqual(logic.moveAttractionTo(p, 0, 9, 0, 0), false);
  assert.strictEqual(logic.moveAttractionTo(p, 5, 0, 0, 0), false);
  assert.strictEqual(logic.moveAttractionTo(null, 0, 0, 0, 0), false);
  assert.deepStrictEqual(p.days[0].attractions.map((a) => a.name), ['A', 'B', 'C']);
});

test('moveItemTo：允许拖到末尾之后（钳到最后一个位置）', () => {
  const list = ['a', 'b', 'c'];
  assert.strictEqual(logic.moveItemTo(list, 0, 3), true);
  assert.deepStrictEqual(list, ['b', 'c', 'a']);
});

test('moveItemTo：非数组/非整数返回 false', () => {
  assert.strictEqual(logic.moveItemTo(null, 0, 1), false);
  assert.strictEqual(logic.moveItemTo(['a'], 0.5, 1), false);
});


console.log('\n=== 天气缺日 ===');

function planWithWeather(dates, weatherDates) {
  return {
    days: dates.map((d) => ({ date: d, attractions: [], meals: [], hotel: null })),
    weather_info: weatherDates.map((d) => ({ date: d, day_weather: '晴', night_weather: '多云' })),
  };
}

test('全部日期都有预报 -> 不缺', () => {
  const p = planWithWeather(['2026-11-01', '2026-11-02'], ['2026-11-01', '2026-11-02']);
  assert.deepStrictEqual(logic.missingWeatherDays(p), []);
});

test('长行程只覆盖前几天 -> 缺后面几天', () => {
  // 高德只给 4 天：6 天行程从明天开始，只覆盖 3 天
  const p = planWithWeather(
    ['2026-10-29', '2026-10-30', '2026-10-31', '2026-11-01', '2026-11-02', '2026-11-03'],
    ['2026-10-29', '2026-10-30', '2026-10-31'],
  );
  assert.deepStrictEqual(logic.missingWeatherDays(p), ['2026-11-01', '2026-11-02', '2026-11-03']);
});

test('完全没有预报 -> 全缺', () => {
  const p = planWithWeather(['2026-11-01'], []);
  assert.deepStrictEqual(logic.missingWeatherDays(p), ['2026-11-01']);
});

test('空计划不崩', () => {
  assert.deepStrictEqual(logic.missingWeatherDays(null), []);
  assert.deepStrictEqual(logic.missingWeatherDays({ days: [] }), []);
});

console.log('');
if (failures.length) {
  console.log(`失败 ${failures.length} 项 / 通过 ${passed} 项`);
  process.exit(1);
}
console.log(`全部通过：${passed} 项`);
