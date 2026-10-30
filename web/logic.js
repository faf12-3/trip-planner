// 纯逻辑层：不碰 DOM，可被浏览器和 Node 同时加载。
// 抽出来的目的是让"预算重算""景点排序"这类最容易算错的部分能被单元测试覆盖。
(function (root, factory) {
  const api = factory();
  root.TripLogic = api;
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  const PLACEHOLDER_NAMES = ['无', 'None', 'null', '-', ''];

  function money(n) {
    const v = Number(n);
    return Number.isFinite(v) ? v : 0;
  }

  function isPlaceholderName(name) {
    return PLACEHOLDER_NAMES.includes(String(name ?? '').trim());
  }

  /**
   * 按明细重算预算。
   *
   * 门票 / 餐饮 / 住宿 从明细汇总（编辑后必须跟着变）；
   * 交通无法从明细推导（自驾是油费 + 停车费），沿用服务端估算值。
   * 合计永远是四项之和，保证"明细"和"合计"不会自相矛盾。
   */
  function recomputeBudget(plan) {
    if (!plan) return null;
    const b = plan.budget || {};
    let attractions = 0;
    let hotels = 0;
    let meals = 0;

    for (const day of plan.days || []) {
      for (const a of day.attractions || []) attractions += money(a.ticket_price);
      for (const m of day.meals || []) meals += money(m.estimated_cost);
      if (day.hotel && !isPlaceholderName(day.hotel.name)) hotels += money(day.hotel.estimated_cost);
    }

    const transport = money(b.total_transportation);
    plan.budget = {
      total_attractions: attractions,
      total_hotels: hotels,
      total_meals: meals,
      total_transportation: transport,
      total: attractions + hotels + meals + transport,
    };
    return plan.budget;
  }

  /** 交换两个元素；越界返回 false（调用方据此决定是否重绘）。 */
  function moveItem(list, idx, delta) {
    if (!Array.isArray(list)) return false;
    const to = idx + delta;
    if (idx < 0 || idx >= list.length || to < 0 || to >= list.length) return false;
    const tmp = list[idx];
    list[idx] = list[to];
    list[to] = tmp;
    return true;
  }

  function moveAttraction(plan, dayIdx, idx, delta) {
    const day = plan?.days?.[dayIdx];
    if (!day) return false;
    return moveItem(day.attractions || [], idx, delta);
  }

  /** 找出当天缺失的餐次，用于"添加餐饮"默认值。 */
  function nextMealType(day) {
    const order = ['breakfast', 'lunch', 'dinner', 'snack'];
    const used = new Set((day?.meals || []).map((m) => m.type));
    return order.find((t) => !used.has(t)) || 'snack';
  }

  /** 编辑期间的完整性检查，返回给用户看的提示列表。 */
  function validate(plan) {
    const issues = [];
    if (!plan) return issues;
    (plan.days || []).forEach((day, di) => {
      const label = `第 ${di + 1} 天`;
      if (!(day.attractions || []).length) issues.push(`${label}没有景点`);
      (day.attractions || []).forEach((a, ai) => {
        if (!String(a.name || '').trim()) issues.push(`${label}第 ${ai + 1} 个景点没填名字`);
      });
      const bad = (day.meals || []).filter((m) => !String(m.name || '').trim());
      if (bad.length) issues.push(`${label}有 ${bad.length} 条餐饮没填名字`);
    });
    return issues;
  }

  /**
   * 从当前行程生成 Markdown。
   *
   * 为什么要在前端生成：编辑之后，服务端返回的那份 markdown 就已经过期了，
   * 直接复制会得到"改之前"的内容。这里始终按眼前的数据现算。
   */
  function toMarkdown(plan) {
    if (!plan) return '';
    const MEAL = { breakfast: '早餐', lunch: '午餐', dinner: '晚餐', snack: '加餐' };
    const out = [`# ${plan.city || '行程'} 旅行计划`, ''];

    if (plan.start_date || plan.end_date) {
      out.push(`日期：${plan.start_date || '?'} ~ ${plan.end_date || '?'}`, '');
    }
    if (plan.overall_suggestions) out.push(`> ${plan.overall_suggestions}`, '');

    (plan.days || []).forEach((day, i) => {
      out.push(`## 第 ${i + 1} 天（${day.date || ''}）`, '');
      if (day.description) out.push(day.description, '');

      if (day.hotel && !isPlaceholderName(day.hotel.name)) {
        out.push(`- 住宿：${day.hotel.name}（${money(day.hotel.estimated_cost)} 元/晚）`);
      }
      (day.attractions || []).forEach((a, j) => {
        const fee = money(a.ticket_price) > 0 ? `${money(a.ticket_price)} 元` : '免费';
        out.push(`${j + 1}. ${a.name || '未命名'}（${money(a.visit_duration)} 分钟，${fee}）`);
      });
      (day.meals || []).forEach((m) => {
        const label = MEAL[m.type] || m.type || '用餐';
        out.push(`- ${label}：${m.name || '未填写'}（${money(m.estimated_cost)} 元）`);
      });
      out.push('');
    });

    const b = recomputeBudget(plan);
    out.push(
      '## 预算估算',
      '',
      `- 景点门票：${b.total_attractions} 元`,
      `- 酒店住宿：${b.total_hotels} 元`,
      `- 餐饮：${b.total_meals} 元`,
      `- 交通（${(plan.days && plan.days[0] && plan.days[0].transportation) || '市内'}）：${b.total_transportation} 元`,
      `- **合计：${b.total} 元**`,
      ''
    );

    const weather = plan.weather_info || [];
    if (weather.length) {
      out.push('## 天气', '');
      weather.forEach((w) => {
        const wind = w.wind_direction ? `，${w.wind_direction}风 ${w.wind_power} 级` : '';
        out.push(`- ${w.date}：${w.day_weather} / ${w.night_weather}，${w.day_temp}~${w.night_temp}°C${wind}`);
      });
      out.push('');
    }

    return out.join('\n');
  }

// ------------------------------------------------------------- 拖拽 --
  // 把「列表里第 from 项挪到第 to 位」和「跨天移动」做成纯函数：
  // 拖拽本身没法在 jsdom 里测（没有布局、没有坐标），但"挪完顺序对不对"必须能测。
  function moveItemTo(list, from, to) {
    if (!Array.isArray(list)) return false;
    if (!Number.isInteger(from) || !Number.isInteger(to)) return false;
    if (from < 0 || from >= list.length) return false;
    if (from === to) return false;
    // 落点允许等于 length（拖到末尾），钳一下再插
    const target = Math.max(0, Math.min(to, list.length - 1));
    const [item] = list.splice(from, 1);
    list.splice(target, 0, item);
    return true;
  }

  /**
   * 把某天第 fromIdx 个景点移到 toDay 的 toIdx 位（可跨天）。
   * 语义：**占住目标行原来的位置**。同一天时 from<to 相当于往后挪。
   */
  function moveAttractionTo(plan, fromDay, fromIdx, toDay, toIdx) {
    const d1 = plan && plan.days && plan.days[fromDay];
    const d2 = plan && plan.days && plan.days[toDay];
    if (!d1 || !d2) return false;
    const a1 = d1.attractions || [];
    const a2 = d2.attractions || [];
    if (fromIdx < 0 || fromIdx >= a1.length) return false;
    if (toIdx < 0 || toIdx > a2.length) return false;
    if (fromDay === toDay) return moveItemTo(a1, fromIdx, toIdx);

    const [item] = a1.splice(fromIdx, 1);
    a2.splice(Math.min(toIdx, a2.length), 0, item);
    return true;
  }

  // ------------------------------------------------------- 冲突检测 --
  // 编辑能随便改，就得有人告诉你"这天还合不合理"。规则是我自己定的：
  //   1. 单日游览总时长 > 10 小时 → 排不下（超过 12 小时算 error，10~12 算 warn）
  //   2. 相邻两个景点直线距离 > 40 km → 一天里来回跑，实际不可行
  //   3. 同一天重复出现同一景点 → 多半是编辑时手滑
  // 为什么用直线距离而不是真实路径：真实路径要调高德，编辑时每敲一下都请求不现实；
  // 直线距离 × 1.4 大致等于路网距离，作为"粗筛"足够，误报用户自己看得懂。
  const CONFLICT_LIMITS = { maxMinutesPerDay: 600, maxJumpKm: 40, overloadFactor: 1.2 };

  function haversineKm(a, b) {
    const R = 6371;
    const rad = (d) => (d * Math.PI) / 180;
    const dLat = rad(b.lat - a.lat);
    const dLon = rad(b.lng - a.lng);
    const h =
      Math.sin(dLat / 2) ** 2 +
      Math.cos(rad(a.lat)) * Math.cos(rad(b.lat)) * Math.sin(dLon / 2) ** 2;
    return 2 * R * Math.asin(Math.min(1, Math.sqrt(h)));
  }

  function detectConflicts(plan, limits) {
    const L = { ...CONFLICT_LIMITS, ...(limits || {}) };
    const out = [];
    if (!plan) return out;

    (plan.days || []).forEach((day, di) => {
      const label = `第 ${di + 1} 天`;
      const atts = (day.attractions || []).filter((a) => String(a.name || '').trim());

      const minutes = atts.reduce((sum, a) => sum + money(a.visit_duration), 0);
      if (minutes > L.maxMinutesPerDay) {
        const hours = Math.round((minutes / 60) * 10) / 10;
        const tooMuch = minutes > L.maxMinutesPerDay * L.overloadFactor;
        out.push({
          day: di,
          type: 'overload',
          level: tooMuch ? 'error' : 'warn',
          message: `${label}排了约 ${hours} 小时，超过 ${L.maxMinutesPerDay / 60} 小时（不含吃饭与交通）`,
        });
      }

      const seen = new Set();
      atts.forEach((a) => {
        const name = a.name.trim();
        if (seen.has(name)) {
          out.push({ day: di, type: 'duplicate', level: 'warn', message: `${label}重复安排了「${name}」` });
        }
        seen.add(name);
      });

      for (let i = 1; i < atts.length; i += 1) {
        const p = atts[i - 1].location;
        const q = atts[i].location;
        if (!p || !q || p.latitude == null || q.latitude == null) continue;
        const km = haversineKm(
          { lng: p.longitude, lat: p.latitude },
          { lng: q.longitude, lat: q.latitude },
        );
        if (km > L.maxJumpKm) {
          out.push({
            day: di,
            type: 'far-jump',
            level: 'warn',
            message: `${label}第 ${i} 到第 ${i + 1} 个景点相距约 ${Math.round(km)} km，当天来回偏累`,
          });
        }
      }
    });

    return out;
  }

  // ----------------------------------------------------- 天气缺日 --
  // 高德只返回今天起 4 天的预报（实测确认）。行程超过这个范围时，
  // 后面的日期没有天气——**不能悄悄少显示**，要明确告诉用户缺哪几天。
  function missingWeatherDays(plan) {
    const days = (plan && plan.days) || [];
    const covered = new Set(
      (plan && plan.weather_info ? plan.weather_info : []).map((w) => String(w.date || '')),
    );
    return days.map((d) => String(d.date || '')).filter((date) => date && !covered.has(date));
  }

  return {
    money,
    isPlaceholderName,
    recomputeBudget,
    moveItem,
    moveAttraction,
    nextMealType,
    validate,
    toMarkdown,
    detectConflicts,
    missingWeatherDays,
    moveItemTo,
    moveAttractionTo,
    haversineKm,
  };
});
