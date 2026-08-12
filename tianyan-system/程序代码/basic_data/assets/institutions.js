(() => {
  const B = window.BasicData;
  const root = B.byId("institutionPage");
  const summary = B.state.summary || {};
  const strategies = summary.strategies || [];
  const adjustmentEvents = summary.institutionAdjustmentEvents || (summary.rebalanceEvents || []).map((row) => ({
    事件ID: row.调仓事件ID,
    统一策略ID: row.统一策略ID,
    调整日期: row.调仓日期,
    事件类型: "普通调仓",
    调整说明: row.调仓原因 || row.调仓标题 || "普通调仓",
  }));
  const overview = summary.overview || {};
  const bucketOrder = Array.from({ length: 11 }, (_, index) => `L${index}`);
  const state = {
    dimension: "channel",
    days: 30,
    selected: { channel: "", manager: "" },
    selectedDate: "",
    filters: B.globalStrategyFilters,
  };

  if (!root || !strategies.length) {
    if (root) root.innerHTML = '<section class="panel"><div class="empty">机构统计所需的策略清单尚未加载。</div></section>';
    B.hidePageLoading();
    return;
  }

  const count = (value) => Number(value || 0).toLocaleString("zh-CN");
  const clean = (value) => String(value || "").trim();
  const dateValue = (value) => {
    const text = clean(value).slice(0, 10);
    const parsed = /^\d{4}-\d{2}-\d{2}$/.test(text) ? new Date(`${text}T00:00:00`) : null;
    return parsed && Number.isFinite(parsed.getTime()) ? parsed : null;
  };
  const dateText = (value) => `${value.getFullYear()}-${String(value.getMonth() + 1).padStart(2, "0")}-${String(value.getDate()).padStart(2, "0")}`;
  const shortDate = (value) => clean(value).slice(5).replace("-", "/");
  const dimensionField = () => state.dimension === "channel" ? "渠道" : "投顾机构";
  const dimensionName = () => state.dimension === "channel" ? "销售渠道" : "投顾管理人";
  const entityName = (row) => clean(row[dimensionField()]) || "未披露";
  const riskBucket = (row) => clean(row.基准风险资产权重) || "未分档";

  function summarize(field) {
    const groups = new Map();
    strategies.forEach((row) => {
      const name = clean(row[field]) || "未披露";
      groups.set(name, (groups.get(name) || 0) + 1);
    });
    return { total: [...groups.values()].reduce((sum, value) => sum + value, 0), groups };
  }

  const channelTotals = summarize("渠道");
  const managerTotals = summarize("投顾机构");
  if (channelTotals.total !== strategies.length || managerTotals.total !== strategies.length) {
    root.innerHTML = '<section class="panel"><div class="empty">销售渠道、投顾管理人和策略清单未对账，机构总览已停止渲染。</div></section>';
    B.hidePageLoading();
    return;
  }

  function filteredStrategies() {
    return strategies.filter((row) => B.matchesGlobalStrategyFilters(row, state.filters));
  }

  function bucketCounts(rows) {
    const counts = Object.fromEntries(bucketOrder.map((bucket) => [bucket, 0]));
    let unbucketed = 0;
    rows.forEach((row) => {
      const bucket = riskBucket(row);
      if (bucket in counts) counts[bucket] += 1;
      else unbucketed += 1;
    });
    return { counts, unbucketed };
  }

  function entityRows(rows) {
    const groups = new Map();
    rows.forEach((row) => {
      const name = entityName(row);
      const item = groups.get(name) || { name, strategies: [], channelCounts: new Map() };
      item.strategies.push(row);
      const channel = clean(row.渠道) || "未披露";
      item.channelCounts.set(channel, (item.channelCounts.get(channel) || 0) + 1);
      groups.set(name, item);
    });
    return [...groups.values()].sort((a, b) => b.strategies.length - a.strategies.length || a.name.localeCompare(b.name, "zh-CN"));
  }

  function trend(rows) {
    const strategyMap = new Map(rows.map((row) => [clean(row.统一策略ID), row]));
    const validEvents = adjustmentEvents.filter((event) => strategyMap.has(clean(event.统一策略ID)) && dateValue(event.调整日期));
    const anchor = validEvents.reduce((latest, event) => {
      const current = dateValue(event.调整日期);
      return !latest || current > latest ? current : latest;
    }, null) || dateValue(overview.数据更新至) || new Date();
    const start = new Date(anchor);
    start.setDate(start.getDate() - state.days + 1);
    const byDate = new Map();
    const windowStrategies = new Set();
    const windowManagers = new Set();
    validEvents.forEach((event) => {
      const current = dateValue(event.调整日期);
      if (current < start || current > anchor) return;
      const strategyId = clean(event.统一策略ID);
      const day = dateText(current);
      if (!byDate.has(day)) byDate.set(day, new Set());
      byDate.get(day).add(strategyId);
      windowStrategies.add(strategyId);
      windowManagers.add(clean(strategyMap.get(strategyId)?.投顾机构) || "未披露");
    });
    const points = [];
    for (let index = 0; index < state.days; index += 1) {
      const current = new Date(start);
      current.setDate(start.getDate() + index);
      const day = dateText(current);
      points.push({ date: day, value: byDate.get(day)?.size || 0 });
    }
    return { points, strategyCount: windowStrategies.size, managerCount: windowManagers.size, anchor: dateText(anchor) };
  }

  function lineChart(points) {
    const width = 760;
    const height = 220;
    const pad = { left: 42, right: 16, top: 14, bottom: 30 };
    const plotWidth = width - pad.left - pad.right;
    const plotHeight = height - pad.top - pad.bottom;
    const maxValue = Math.max(1, ...points.map((point) => point.value));
    const x = (index) => pad.left + (points.length <= 1 ? 0 : index / (points.length - 1) * plotWidth);
    const y = (value) => pad.top + plotHeight - value / maxValue * plotHeight;
    const path = points.map((point, index) => `${index ? "L" : "M"}${x(index).toFixed(2)},${y(point.value).toFixed(2)}`).join(" ");
    const area = `${path} L${x(points.length - 1).toFixed(2)},${(pad.top + plotHeight).toFixed(2)} L${x(0).toFixed(2)},${(pad.top + plotHeight).toFixed(2)} Z`;
    const ticks = [0, Math.ceil(maxValue / 2), maxValue].filter((value, index, values) => values.indexOf(value) === index);
    const labelIndexes = [0, Math.floor((points.length - 1) / 2), points.length - 1].filter((value, index, values) => values.indexOf(value) === index);
    return `<svg class="institution-line-chart" viewBox="0 0 ${width} ${height}" role="img" aria-label="每日发生调整的去重策略数走势，可选择日期查看策略明细">
      <defs><linearGradient id="institutionArea" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#f36b15" stop-opacity=".20"/><stop offset="1" stop-color="#f36b15" stop-opacity=".02"/></linearGradient></defs>
      ${ticks.map((tick) => `<g><line x1="${pad.left}" y1="${y(tick)}" x2="${width - pad.right}" y2="${y(tick)}" class="institution-grid-line"/><text x="${pad.left - 9}" y="${y(tick) + 4}" text-anchor="end">${tick}</text></g>`).join("")}
      <path d="${area}" fill="url(#institutionArea)"/><path d="${path}" class="institution-trend-line"/>
      ${points.map((point, index) => `<circle cx="${x(index)}" cy="${y(point.value)}" r="${point.value ? 5 : 3.2}" class="institution-trend-dot ${state.selectedDate === point.date ? "is-selected" : ""}" role="button" tabindex="0" data-point-date="${point.date}" aria-label="${point.date}，${point.value} 个策略调整"><title>${point.date}：${point.value} 个策略调整；点击查看明细</title></circle>`).join("")}
      ${labelIndexes.map((index) => `<text x="${x(index)}" y="${height - 7}" text-anchor="${index === 0 ? "start" : index === points.length - 1 ? "end" : "middle"}">${shortDate(points[index].date)}</text>`).join("")}
    </svg>`;
  }

  function rankingLink(bucket, scope = {}) {
    return B.withGlobalStrategyFilters("./mixed-performance-scatter.html", {
      productType: "投顾策略",
      riskWeight: bucket,
      channel: scope.channel || "",
      institution: scope.institution || "",
    });
  }

  function bucketBars(rows, { compact = false, channel = "", institution = "" } = {}) {
    const distribution = bucketCounts(rows);
    const maxValue = Math.max(1, ...Object.values(distribution.counts));
    return `<div class="institution-bucket-bars ${compact ? "is-compact" : ""}">
      ${bucketOrder.map((bucket) => `<a class="institution-bucket-row" href="${B.esc(rankingLink(bucket, { channel, institution }))}" title="到全市场产品排名查看 ${bucket} 策略"><span>${bucket}</span><i><b style="width:${(distribution.counts[bucket] / maxValue * 100).toFixed(2)}%"></b></i><strong>${count(distribution.counts[bucket])}</strong></a>`).join("")}
    </div><p class="institution-unbucketed">未分档 ${count(distribution.unbucketed)} 个；不把缺失基准推断为 L0。</p>`;
  }

  function filterButton(key, label, description) {
    const selected = state.filters[key];
    return `<button type="button" class="institution-filter ${selected ? "is-active" : ""}" data-filter="${key}" aria-pressed="${selected}"><span>${selected ? "✓" : ""}</span><b>${label}</b><small>${description}</small></button>`;
  }

  function selectedDateRows(rows) {
    if (!state.selectedDate) return [];
    const strategyMap = new Map(rows.map((row) => [clean(row.统一策略ID), row]));
    const groups = new Map();
    adjustmentEvents.forEach((event) => {
      const strategyId = clean(event.统一策略ID);
      if (clean(event.调整日期).slice(0, 10) !== state.selectedDate || !strategyMap.has(strategyId)) return;
      const item = groups.get(strategyId) || { strategy: strategyMap.get(strategyId), descriptions: new Set(), types: new Set() };
      item.descriptions.add(clean(event.调整说明) || clean(event.事件类型) || "调整");
      item.types.add(clean(event.事件类型));
      groups.set(strategyId, item);
    });
    return [...groups.values()].sort((a, b) => clean(a.strategy.策略名称).localeCompare(clean(b.strategy.策略名称), "zh-CN"));
  }

  function adjustmentList(rows) {
    const items = selectedDateRows(rows);
    const heading = state.selectedDate ? `${state.selectedDate} 调仓策略 ${count(items.length)} 个` : "选择走势图中的日期查看调仓策略";
    return `<details class="institution-adjustment-details" id="institutionAdjustmentDetails">
      <summary><span>${B.esc(heading)}</span><small>展开 / 折叠</small></summary>
      <div class="institution-adjustment-table-wrap"><table class="institution-adjustment-table"><colgroup><col class="institution-adjustment-col-name"><col class="institution-adjustment-col-channel"><col class="institution-adjustment-col-manager"><col class="institution-adjustment-col-risk"><col class="institution-adjustment-col-date"><col class="institution-adjustment-col-note"><col class="institution-adjustment-col-return"></colgroup><thead><tr><th>策略名称</th><th>销售渠道</th><th>投顾管理机构</th><th>基准风险资产权重</th><th>调仓日期</th><th>调仓说明</th><th>近一月收益率</th></tr></thead><tbody>
        ${items.length ? items.map((item) => {
          const row = item.strategy;
          return `<tr><td><a class="link" href="./strategy.html?id=${encodeURIComponent(clean(row.统一策略ID))}">${B.esc(row.策略名称 || "未命名策略")}</a></td><td>${B.esc(row.渠道 || "未披露")}</td><td>${B.esc(row.投顾机构 || "未披露")}</td><td>${B.esc(riskBucket(row))}</td><td>${B.esc(state.selectedDate)}</td><td class="institution-adjustment-note">${B.esc([...item.descriptions].join("；"))}</td><td>${B.pctSigned(row.近一月)}</td></tr>`;
        }).join("") : `<tr><td colspan="7"><div class="empty">${state.selectedDate ? "当天没有符合当前筛选条件的调仓策略。" : "请选择走势图中的日期点。"}</div></td></tr>`}
      </tbody></table></div>
    </details>`;
  }

  function managerChannelSummary(item) {
    if (state.dimension !== "manager") return `<small>${dimensionName()}</small>`;
    const text = [...item.channelCounts.entries()]
      .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0], "zh-CN"))
      .map(([channel, total]) => `${channel} ${count(total)}`)
      .join(" · ");
    return `<small>${B.esc(text || "渠道未披露")}</small>`;
  }

  function render() {
    const rows = filteredStrategies();
    const entities = entityRows(rows);
    const selectedKey = state.dimension;
    if (!entities.some((item) => item.name === state.selected[selectedKey])) state.selected[selectedKey] = entities[0]?.name || "";
    const selectedEntity = entities.find((item) => item.name === state.selected[selectedKey]);
    const selectedRows = selectedEntity?.strategies || [];
    const trendData = trend(rows);
    const selectedScope = state.dimension === "channel" ? { channel: state.selected[selectedKey] } : { institution: state.selected[selectedKey] };
    const strategyLinkParam = state.dimension === "channel" ? "channel" : "institution";
    const selectedLink = B.withGlobalStrategyFilters("./strategies.html", { [strategyLinkParam]: state.selected[selectedKey] });
    const rankingBaseLink = B.withGlobalStrategyFilters("./mixed-performance-scatter.html", { productType: "投顾策略" });
    const selectedRankingLink = B.withGlobalStrategyFilters("./mixed-performance-scatter.html", { productType: "投顾策略", ...selectedScope });

    root.innerHTML = `
      <section class="panel institution-controls institution-global-controls">
        <div class="institution-global-title"><div><span class="eyebrow">数据筛选范围</span><h1>机构总览</h1><p>条件仅作用于本页；点击本页“查看策略、查看排名或分档”链接时会随链接带入，直接使用顶部菜单不会继承。</p></div><span class="institution-global-count">当前 ${count(rows.length)} 个策略</span></div>
        <div class="institution-filters" aria-label="全局策略数据条件">
          ${filterButton("benchmark", "有基准", "披露可追溯基准")}
          ${filterButton("performance", "有业绩走势", "至少两个官方净值点")}
          ${filterButton("history", "有历史仓位", "完整快照或调仓后仓位")}
          ${filterButton("active", "对客未终止", "当前对客且未终止")}
        </div>
      </section>

      <section class="institution-top-grid">
        <article class="panel institution-trend-card">
          <div class="institution-card-head"><div><span class="eyebrow">市场调仓走势</span><h2>调仓总览</h2><p>普通调仓与发车信号合并后，按日去重统计；选择点位可查看对应策略。</p></div><div class="institution-range" role="group" aria-label="走势图区间">${[[7, "近一周"], [14, "近两周"], [30, "近一月"]].map(([days, label]) => `<button type="button" data-days="${days}" class="${state.days === days ? "is-active" : ""}">${label}</button>`).join("")}</div></div>
          <div class="institution-trend-body"><div class="institution-trend-kpis"><div><span>调仓策略总数</span><strong>${count(trendData.strategyCount)}</strong><small>窗口内去重</small></div><div><span>投顾管理机构数</span><strong>${count(trendData.managerCount)}</strong><small>发生过调整</small></div><div><span>数据截至</span><strong class="is-date">${B.esc(trendData.anchor)}</strong><small>${state.days} 个自然日</small></div></div><div class="institution-chart-wrap">${lineChart(trendData.points)}</div></div>
          ${adjustmentList(rows)}
        </article>
        <article class="panel institution-market-buckets">
          <div class="institution-card-head"><div><span class="eyebrow">基准风险资产权重</span><a class="institution-chart-title-link" href="${B.esc(rankingBaseLink)}"><h2>策略总数 <strong>${count(rows.length)}</strong></h2><p>L0—L10 分布 →</p></a></div></div>
          ${bucketBars(rows, { compact: true })}
        </article>
      </section>

      <section class="panel institution-controls institution-dimension-controls">
        <div class="institution-tabs" role="tablist"><button type="button" data-dimension="channel" class="${state.dimension === "channel" ? "is-active" : ""}">销售渠道</button><button type="button" data-dimension="manager" class="${state.dimension === "manager" ? "is-active" : ""}">投顾管理人</button></div>
      </section>

      <section class="institution-workspace-grid">
        <aside class="panel institution-entity-panel">
          <div class="institution-section-head"><div><span>${dimensionName()}总数</span><strong>${count(entities.length)}</strong></div><div><span>策略总数</span><strong>${count(rows.length)}</strong></div></div>
          <div class="institution-entity-list" role="listbox" aria-label="${dimensionName()}列表">
            ${entities.map((item) => `<button type="button" role="option" aria-selected="${item.name === state.selected[selectedKey]}" class="institution-entity-row ${item.name === state.selected[selectedKey] ? "is-active" : ""}" data-entity="${B.esc(item.name)}"><span><b>${B.esc(item.name)}</b>${managerChannelSummary(item)}</span><strong>${count(item.strategies.length)}</strong></button>`).join("") || '<div class="empty">当前条件下暂无机构。</div>'}
          </div>
        </aside>
        <article class="panel institution-selected-panel">
          <div class="institution-selected-head"><div><span class="eyebrow">当前机构分布</span><a class="institution-chart-title-link" href="${B.esc(selectedRankingLink)}"><h2>${B.esc(state.selected[selectedKey] || "暂无可选机构")}</h2><p>${dimensionName()} · ${count(selectedRows.length)} 个策略 · 查看排名 →</p></a></div>${selectedEntity ? `<a class="link" href="${B.esc(selectedLink)}">查看策略</a>` : ""}</div>
          ${bucketBars(selectedRows, selectedScope)}
        </article>
      </section>

      <details class="panel institution-method"><summary>统计口径与数据边界</summary><div><p><b>基准风险资产权重：</b>按业绩基准中的权益、商品和另类风险资产合计权重划分 L0—L10；缺基准或无法可靠拆分时保留“未分档”。</p><p><b>有历史仓位：</b>只认基金权重全部精确且合计 99%—101% 的官方历史快照或完整调仓后仓位；发车新增资金分配比例不是存量仓位。</p><p><b>走势：</b>普通调仓与发车信号合并，按日期和策略去重；区间总数为窗口内去重策略数，不是折线点位求和。</p><p><b>投顾管理人缺失：</b>源端未披露时归入“未披露”，不根据策略名称、持仓或销售渠道推断。</p></div></details>`;

    root.querySelectorAll("[data-days]").forEach((button) => button.addEventListener("click", () => { state.days = Number(button.dataset.days); state.selectedDate = ""; render(); }));
    root.querySelectorAll("[data-dimension]").forEach((button) => button.addEventListener("click", () => { state.dimension = button.dataset.dimension; state.selectedDate = ""; render(); }));
    root.querySelectorAll("[data-filter]").forEach((button) => button.addEventListener("click", () => {
      const key = button.dataset.filter;
      B.setGlobalStrategyFilter(key, !state.filters[key]);
      state.selectedDate = "";
      render();
    }));
    root.querySelectorAll("[data-entity]").forEach((button) => button.addEventListener("click", () => { state.selected[state.dimension] = button.dataset.entity; render(); }));
    root.querySelectorAll("[data-point-date]").forEach((point) => {
      const selectPoint = () => {
        state.selectedDate = point.getAttribute("data-point-date") || "";
        render();
        const details = B.byId("institutionAdjustmentDetails");
        if (details) details.open = true;
      };
      point.addEventListener("click", selectPoint);
      point.addEventListener("keydown", (event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          selectPoint();
        }
      });
    });
  }

  render();
  B.hidePageLoading();
})();
