(() => {
  const B = window.BasicData || {};
  const root = B.byId ? B.byId("targetProfitAnalysisPage") : document.getElementById("targetProfitAnalysisPage");
  if (!root) return;

  const pack = window.__BASIC_TARGET_PROFIT_ANALYSIS_PACK__ || {};
  const esc = B.esc || ((value) => String(value ?? "").replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;").replaceAll("'", "&#39;"));
  const valueHtml = B.valueHtml || ((field, value) => esc(value ?? "未披露"));
  const label = B.label || ((name) => esc(name));
  const isBlank = (value) => value === null || value === undefined || value === "";
  const pct = (value, digits = 2) => !isBlank(value) && Number.isFinite(Number(value)) ? `${Number(value).toFixed(digits)}%` : "未披露";
  const signedPct = (value, digits = 2) => {
    if (isBlank(value) || !Number.isFinite(Number(value))) return '<span class="value-muted">未披露</span>';
    const number = Number(value);
    const cls = number > 0 ? "ret-pos" : number < 0 ? "ret-neg" : "ret-zero";
    return `<span class="${cls}">${number.toFixed(digits)}%</span>`;
  };
  const count = (value) => Number(value || 0).toLocaleString("zh-CN");
  const num = (value, fallback = null) => !isBlank(value) && Number.isFinite(Number(value)) ? Number(value) : fallback;
  const text = (value) => String(value ?? "");
  const isGfAdvisor = (row) => /广发/.test(`${row?.投顾机构 || ""} ${row?.渠道 || ""}`);

  const state = {
    advisor: "__all__",
    risk: "__all__",
    type: "__all__",
    status: "__all__",
    query: "",
    selectedSeriesId: "",
    selectedPeriodId: "",
    statusGroup: "__all__",
    highlightAdvisor: "",
    seriesQuery: "",
    seriesStatus: "__all__",
    detailXField: "生命周期天数",
    detailYField: "生命周期收益",
    scatterXField: "中位生命周期最大回撤",
    scatterYField: "中位生命周期收益",
    seriesSortField: "期次数",
    seriesSortDir: "desc",
    periodSortField: "成立日期",
    periodSortDir: "desc",
  };
  let queryRenderTimer = null;
  let querySelection = null;
  let queryComposing = false;

  const chartColors = ["#d92d20", "#1570ef", "#0f766e", "#7c3aed", "#b7791f", "#c11574", "#175cd3", "#9a3412", "#2f6f4e", "#6941c6", "#475467", "#ea580c"];
  const statusStyles = {
    running: { key: "running", label: "运行中", color: "#16a34a" },
    profit: { key: "profit", label: "止盈", color: "#d92d20" },
    expired: { key: "expired", label: "到期", color: "#f59e0b" },
    other: { key: "other", label: "其他", color: "#64748b" },
  };
  const statusOrder = ["running", "profit", "expired", "other"];

  function strategyUrl(id) {
    return `./strategy.html?id=${encodeURIComponent(id || "")}`;
  }

  function optionList(values, selected, allLabel = "全部") {
    const unique = [...new Set((values || []).filter(Boolean))].sort((a, b) => a.localeCompare(b, "zh-CN"));
    return [`<option value="__all__">${allLabel}</option>`, ...unique.map((item) => `<option value="${esc(item)}" ${item === selected ? "selected" : ""}>${esc(item)}</option>`)].join("");
  }

  function matchesQuery(row, query) {
    if (!query) return true;
    const q = query.toLowerCase();
    return [
      row.系列名称,
      row.策略名称,
      row.投顾机构,
      row.渠道,
      row.风险等级,
      row.研报产品类型,
      row.生命周期状态,
      row.代表期次,
    ].some((value) => text(value).toLowerCase().includes(q));
  }

  function targetStatus(row) {
    const target = num(row.目标收益率);
    const peak = num(row.生命周期峰值收益);
    const statusText = [row.生命周期状态, row.运作状态, row.天天展示状态, row.策略名称].map(text).join(" ");
    if (/止盈|达标赎回/.test(statusText)) return "已止盈";
    if (/终止|期满|到期|stopped|已结束|非对客/.test(statusText)) return "到期/stopped";
    if (target === null || target <= 0) return "目标未披露";
    if (target > 80) return "目标无法解析";
    if (peak !== null && peak >= target) return "已达标未止盈";
    return "未达标";
  }

  function filteredPeriods(options = {}) {
    const ignoreStatusGroup = Boolean(options.ignoreStatusGroup);
    return (pack.periods || []).filter((row) => {
      if (state.advisor !== "__all__" && row.投顾机构 !== state.advisor) return false;
      if (state.risk !== "__all__" && row.风险等级 !== state.risk) return false;
      if (state.type !== "__all__" && row.研报产品类型 !== state.type) return false;
      if (state.status !== "__all__" && targetStatus(row) !== state.status) return false;
      if (!ignoreStatusGroup && state.statusGroup !== "__all__" && lifecycleStatusStyle(row).key !== state.statusGroup) return false;
      return matchesQuery(row, state.query);
    });
  }

  function filteredSeries(periodRows) {
    const ids = new Set(periodRows.map((row) => row.系列ID));
    return (pack.series || []).filter((row) => ids.has(row.系列ID) && matchesQuery(row, state.query));
  }

  function sortRows(rows, field, dir) {
    const factor = dir === "asc" ? 1 : -1;
    return [...rows].sort((a, b) => {
      const av = a[field];
      const bv = b[field];
      const an = num(av);
      const bn = num(bv);
      if (an !== null || bn !== null) return ((an ?? -Infinity) - (bn ?? -Infinity)) * factor;
      return text(av).localeCompare(text(bv), "zh-CN") * factor;
    });
  }

  function sortButton(field, currentField, currentDir, table) {
    const active = field === currentField;
    const arrow = active ? (currentDir === "asc" ? "↑" : "↓") : "↕";
    return `<button class="sort-head ${active ? "is-active" : ""}" type="button" data-sort-table="${table}" data-sort-field="${esc(field)}">${label(field)}<span class="sort-arrow">${arrow}</span></button>`;
  }

  function kpi(labelName, value, sub = "", formatter = null) {
    const display = formatter ? formatter(value) : valueHtml(labelName, value);
    return `<section class="metric"><div>${label(labelName)}</div><div class="metric-value">${display}</div>${sub ? `<div class="metric-sub">${esc(sub)}</div>` : ""}</section>`;
  }

  function overviewMetrics() {
    const overview = pack.overview || {};
    return `
      <div class="grid target-profit-kpis">
        ${kpi("目标盈系列数", overview.目标盈系列数, "同机构同系列去掉期次后计数")}
        ${kpi("目标盈期次数", overview.目标盈期次数, "保留每一期独立生命周期")}
        ${kpi("运行中期次数", overview.运行中期次数, "仍可继续观察的期次")}
        ${kpi("中位生命周期收益", overview.中位生命周期收益, "从净值起点到终点", signedPct)}
        ${kpi("中位生命周期最大回撤", overview.中位生命周期最大回撤, "运行期间最大承受回撤", pct)}
        ${kpi("收益曾达目标期次数", overview.收益曾达目标期次数, `达标率 ${pct(overview.目标收益达标率)}`, count)}
      </div>
    `;
  }

  function renderGuide() {
    const guide = pack.analysisGuide || {};
    const quality = pack.quality || {};
    return `
      <section class="panel target-profit-method-panel">
        <div class="panel-head">
          <div>
            <h2>分析口径</h2>
            <p class="desc">按目标盈产品的真实业务形态拆成系列和期次两层，既避免多期重复放大，也保留每期可核验结果。</p>
          </div>
        </div>
        <div class="topic-logic-grid target-profit-guide-grid">
          ${Object.entries(guide).map(([key, value]) => `<div><strong>${esc(key)}</strong><p>${esc(value)}</p></div>`).join("")}
        </div>
        <div class="target-profit-quality-row">
          <span>净值覆盖 <b>${count(quality.净值覆盖期次数)}</b> 期</span>
          <span>官方披露曲线 <b>${count(quality.官方披露曲线期次数)}</b> 期</span>
          <span>标准回放曲线 <b>${count(quality.标准回放曲线期次数)}</b> 期</span>
          <span>可解析目标收益 <b>${count(quality.目标收益可解析期次数)}</b> 期</span>
        </div>
      </section>
    `;
  }

  function renderFilters() {
    const periods = pack.periods || [];
    return `
      <section class="panel target-profit-filter-panel">
        <div class="panel-head">
          <div>
            <h2>筛选</h2>
            <p class="desc">筛选同时作用于系列表、期次表和图表；系列只要有一期满足条件就会保留。</p>
          </div>
          <button class="button ghost" type="button" data-action="resetFilters">重置筛选</button>
        </div>
        <div class="insight-filters">
          <label class="filter-field"><span>投顾机构</span><select class="control" data-filter="advisor">${optionList(periods.map((row) => row.投顾机构), state.advisor, "全部机构")}</select></label>
          <label class="filter-field"><span>风险等级</span><select class="control" data-filter="risk">${optionList(periods.map((row) => row.风险等级), state.risk, "全部风险")}</select></label>
          <label class="filter-field"><span>产品类型</span><select class="control" data-filter="type">${optionList(periods.map((row) => row.研报产品类型), state.type, "全部类型")}</select></label>
          <label class="filter-field"><span>目标状态</span><select class="control" data-filter="status">${optionList(periods.map(targetStatus), state.status, "全部状态")}</select></label>
          <label class="filter-field target-profit-search"><span>名称/机构搜索</span><input class="control" data-filter="query" value="${esc(state.query)}" placeholder="输入系列、策略、机构或状态"></label>
        </div>
      </section>
    `;
  }

  function aggregateAdvisor(periodRows, seriesRows) {
    const byAdvisor = new Map();
    periodRows.forEach((row) => {
      const key = row.投顾机构 || "未识别机构";
      if (!byAdvisor.has(key)) byAdvisor.set(key, { 投顾机构: key, 期次数: 0, 系列数: 0, 运行中期次数: 0, 中位生命周期收益样本: [], 中位最大回撤样本: [] });
      const item = byAdvisor.get(key);
      item.期次数 += 1;
      if (row.生命周期状态 === "运行中观察" || row.生命周期状态 === "预约期") item.运行中期次数 += 1;
      if (num(row.生命周期收益) !== null) item.中位生命周期收益样本.push(num(row.生命周期收益));
      if (num(row.生命周期最大回撤) !== null) item.中位最大回撤样本.push(num(row.生命周期最大回撤));
    });
    const seriesCount = new Map();
    seriesRows.forEach((row) => {
      const key = row.投顾机构 || "未识别机构";
      seriesCount.set(key, (seriesCount.get(key) || 0) + 1);
    });
    return [...byAdvisor.values()].map((item) => ({
      ...item,
      系列数: seriesCount.get(item.投顾机构) || 0,
      中位生命周期收益: median(item.中位生命周期收益样本),
      中位最大回撤: median(item.中位最大回撤样本),
    })).sort((a, b) => b.期次数 - a.期次数);
  }

  function median(values) {
    const arr = values
      .filter((value) => value !== null && value !== undefined && value !== "" && Number.isFinite(Number(value)))
      .map(Number)
      .sort((a, b) => a - b);
    if (!arr.length) return null;
    const mid = Math.floor(arr.length / 2);
    return arr.length % 2 ? arr[mid] : (arr[mid - 1] + arr[mid]) / 2;
  }

  function renderAdvisorBars(periodRows, seriesRows) {
    const rows = aggregateAdvisor(periodRows, seriesRows).slice(0, 12);
    const maxValue = Math.max(1, ...rows.map((row) => row.期次数 || 0));
    return `
      <section class="panel">
        <div class="panel-head">
          <div>
            <h2>机构供给分布</h2>
            <p class="desc">看哪些机构目标盈期次数较多，以及典型生命周期收益和回撤水平。</p>
          </div>
        </div>
        <div class="target-bar-list">
          ${rows.map((row) => `
            <button class="target-bar-row ${state.highlightAdvisor === row.投顾机构 ? "is-active" : ""}" type="button" data-highlight-advisor="${esc(row.投顾机构)}" title="高亮${esc(row.投顾机构)}的点">
              <div class="target-bar-label"><strong>${esc(row.投顾机构)}</strong><span>${count(row.系列数)} 个系列，运行中 ${count(row.运行中期次数)} 期</span></div>
              <div class="target-bar-track"><b style="width:${Math.max(3, row.期次数 / maxValue * 100).toFixed(2)}%"></b></div>
              <div class="target-bar-meta"><b>${count(row.期次数)} 期</b><span>${signedPct(row.中位生命周期收益)} / 回撤 ${pct(row.中位最大回撤)}</span></div>
            </button>
          `).join("") || '<div class="empty">暂无数据</div>'}
        </div>
      </section>
    `;
  }

  function scale(value, min, max, start, end) {
    if (max === min) return (start + end) / 2;
    return start + (value - min) / (max - min) * (end - start);
  }

  const periodScatterOptions = [
    { key: "生命周期天数", label: "运行天数", type: "days", value: (row) => num(row.生命周期天数) },
    { key: "生命周期收益", label: "生命周期收益", type: "pct", value: (row) => num(row.生命周期收益) },
    { key: "年化收益", label: "年化收益", type: "pct", value: (row) => num(row.年化收益) },
    { key: "累计收益率", label: "累计收益率", type: "pct", value: (row) => num(row.累计收益率) },
    { key: "近1年", label: "近1年", type: "pct", value: (row) => num(row.近1年) },
    { key: "近6月", label: "近6月", type: "pct", value: (row) => num(row.近6月) },
    { key: "近三月", label: "近三月", type: "pct", value: (row) => num(row.近三月) },
    { key: "目标收益率", label: "目标收益率", type: "pct", value: (row) => num(row.目标收益率) },
    { key: "生命周期峰值收益", label: "生命周期峰值收益", type: "pct", value: (row) => num(row.生命周期峰值收益) },
    { key: "生命周期最大回撤", label: "生命周期最大回撤", type: "pct", value: (row) => num(row.生命周期最大回撤) },
    { key: "最大回撤", label: "最大回撤", type: "pct", value: (row) => num(row.最大回撤) },
    { key: "夏普比率", label: "夏普比率", type: "ratio", value: (row) => num(row.夏普比率) },
    { key: "期次序号", label: "期次序号", type: "count", value: (row) => num(row.期次序号) },
  ];

  const seriesScatterOptions = [
    { key: "中位生命周期收益", label: "中位生命周期收益", type: "pct", value: (row) => num(row.中位生命周期收益) },
    { key: "中位生命周期最大回撤", label: "中位生命周期最大回撤", type: "pct", value: (row) => num(row.中位生命周期最大回撤) },
    { key: "中位生命周期天数", label: "中位生命周期天数", type: "days", value: (row) => num(row.中位生命周期天数) },
    { key: "平均运作天数", label: "平均运作天数", type: "days", value: (row) => num(row.平均运作天数) },
    { key: "止盈期数", label: "止盈期数", type: "count", value: (row) => num(row.止盈期数) },
    { key: "累计期数", label: "累计期数", type: "count", value: (row) => num(row.累计期数) },
    { key: "运行中期数", label: "运行中期数", type: "count", value: (row) => num(row.运行中期数) },
    { key: "止盈率", label: "止盈率", type: "pct", value: (row) => num(row.止盈率) },
    { key: "达标率", label: "达标率", type: "pct", value: (row) => num(row.达标率) },
    { key: "中位近1年", label: "中位近1年", type: "pct", value: (row) => num(row.中位近1年) },
    { key: "中位近6月", label: "中位近6月", type: "pct", value: (row) => num(row.中位近6月) },
    { key: "中位近三月", label: "中位近三月", type: "pct", value: (row) => num(row.中位近三月) },
    { key: "完成期次中位收益", label: "完成期次中位收益", type: "pct", value: (row) => num(row.完成期次中位收益) },
    { key: "中位年化收益", label: "中位年化收益", type: "pct", value: (row) => num(row.中位年化收益) },
    { key: "中位最大回撤", label: "中位最大回撤", type: "pct", value: (row) => num(row.中位最大回撤) },
    { key: "中位夏普比率", label: "中位夏普比率", type: "ratio", value: (row) => num(row.中位夏普比率) },
    { key: "卡玛比率", label: "卡玛比率", type: "ratio", value: (row) => num(row.卡玛比率) },
    { key: "中位权益权重", label: "中位权益权重", type: "pct", value: (row) => num(row.中位权益权重) },
    { key: "中位债券权重", label: "中位债券权重", type: "pct", value: (row) => num(row.中位债券权重) },
    { key: "中位QDII权重", label: "中位QDII权重", type: "pct", value: (row) => num(row.中位QDII权重) },
  ];

  function optionSelect(options, selected) {
    return options.map((item) => `<option value="${esc(item.key)}" ${item.key === selected ? "selected" : ""}>${esc(item.label)}</option>`).join("");
  }

  function metricByKey(options, key) {
    return options.find((item) => item.key === key) || options[0];
  }

  function avg(values) {
    const arr = values.map((value) => num(value)).filter((value) => value !== null);
    if (!arr.length) return null;
    return arr.reduce((sum, value) => sum + value, 0) / arr.length;
  }

  function enrichSeriesRows(seriesRows, periodRows) {
    const bySeries = new Map();
    periodRows.forEach((row) => {
      if (!bySeries.has(row.系列ID)) bySeries.set(row.系列ID, []);
      bySeries.get(row.系列ID).push(row);
    });
    return seriesRows.map((row) => {
      const periods = bySeries.get(row.系列ID) || [];
      const periodCount = periods.length || num(row.期次数, 0);
      const periodStatusCounts = periodStatusCountsForSeries(periods);
      const seriesStatus = seriesStatusFromCounts(periodStatusCounts, row);
      const stopped = periods.length ? periodStatusCounts.profit : num(row.已止盈期次数, 0);
      const running = periods.length ? periodStatusCounts.running : num(row.运行中期次数, 0);
      const medianAnnual = median(periods.map((period) => num(period.年化收益)));
      const medianMdd = median(periods.map((period) => num(period.生命周期最大回撤) ?? num(period.最大回撤)));
      const calmar = medianAnnual !== null && medianMdd !== null && Math.abs(medianMdd) > 0.0001 ? medianAnnual / Math.abs(medianMdd) : null;
      return {
        ...row,
        平均运作天数: avg(periods.map((period) => period.生命周期天数)),
        止盈期数: stopped,
        累计期数: periodCount,
        运行中期数: running,
        止盈率: periodCount ? stopped / periodCount * 100 : null,
        中位年化收益: medianAnnual,
        中位最大回撤: medianMdd,
        中位夏普比率: median(periods.map((period) => num(period.夏普比率))),
        卡玛比率: calmar,
        系列状态Key: seriesStatus.key,
        系列状态: seriesStatus.label,
        系列状态颜色: seriesStatus.color,
        状态分布: periodStatusCounts,
      };
    });
  }

  function metricDisplay(metric, value) {
    if (value === null || value === undefined || !Number.isFinite(Number(value))) return "未披露";
    const number = Number(value);
    if (metric.type === "pct") return `${number.toFixed(2)}%`;
    if (metric.type === "days") return `${Math.round(number).toLocaleString("zh-CN")}天`;
    if (metric.type === "count") return Math.round(number).toLocaleString("zh-CN");
    return number.toFixed(2);
  }

  function metricAxisText(metric, value) {
    if (metric.type === "pct") return `${value.toFixed(0)}%`;
    if (metric.type === "days") return `${Math.round(value)}天`;
    if (metric.type === "count") return `${Math.round(value)}`;
    return value.toFixed(1);
  }

  function metricDomain(values, metric) {
    const arr = values.filter((value) => Number.isFinite(Number(value))).map(Number);
    if (!arr.length) return { min: 0, max: 1 };
    let min = Math.min(...arr);
    let max = Math.max(...arr);
    if (min === max) {
      const pad = Math.max(1, Math.abs(max) * 0.12);
      min -= pad;
      max += pad;
    }
    if (metric.type === "pct" || metric.type === "days" || metric.type === "count") {
      if (min > 0) min = 0;
      const step = metric.type === "days" ? 100 : metric.type === "count" ? 5 : 5;
      max = Math.ceil(max / step) * step;
      min = Math.floor(min / step) * step;
    } else {
      const pad = (max - min) * 0.12;
      min -= pad;
      max += pad;
    }
    if (max === min) max = min + 1;
    return { min, max };
  }

  function ticks(domain) {
    const span = domain.max - domain.min;
    return [0, 0.25, 0.5, 0.75, 1].map((part) => domain.min + span * part);
  }

  function clamp(value, min, max) {
    return Math.max(min, Math.min(max, value));
  }

  function hashText(value) {
    let hash = 2166136261;
    const source = text(value);
    for (let i = 0; i < source.length; i += 1) {
      hash ^= source.charCodeAt(i);
      hash = Math.imul(hash, 16777619);
    }
    return hash >>> 0;
  }

  function jitterPair(id, spread = 3.2) {
    const hash = hashText(id);
    const angle = (hash % 360) / 180 * Math.PI;
    const distance = ((hash >>> 9) % 100) / 100 * spread;
    return { dx: Math.cos(angle) * distance, dy: Math.sin(angle) * distance };
  }

  function plotCoordinate(value, domain, start, end) {
    return scale(value, domain.min, domain.max, start, end);
  }

  function isRunningPeriod(row) {
    return row.生命周期状态 === "运行中观察" || row.生命周期状态 === "预约期";
  }

  function lifecycleStatusStyle(row) {
    const status = targetStatus(row);
    if (isRunningPeriod(row)) return statusStyles.running;
    if (status === "已止盈") return statusStyles.profit;
    if (status === "到期/stopped" || /终止|期满|到期|stopped|已结束|非对客/.test(text(row.生命周期状态))) return statusStyles.expired;
    return { ...statusStyles.other, label: status || statusStyles.other.label };
  }

  function statusCounts(periodRows) {
    const counts = { __all__: periodRows.length, running: 0, profit: 0, expired: 0, other: 0 };
    periodRows.forEach((row) => {
      const key = lifecycleStatusStyle(row).key;
      counts[key] = (counts[key] || 0) + 1;
    });
    return counts;
  }

  function statusLegend(periodRows, labelText = "状态筛选") {
    const counts = statusCounts(periodRows || []);
    const items = [{ key: "__all__", label: "全部", color: "#98a2b3" }, ...statusOrder.map((key) => statusStyles[key])];
    return `<div class="topic-scatter-legend target-status-legend" role="group" aria-label="${esc(labelText)}">
      ${items.map((item) => {
        const active = state.statusGroup === item.key;
        return `<button class="target-status-chip ${active ? "is-active" : ""}" type="button" data-status-group="${esc(item.key)}" title="筛选${esc(item.label)}期次">
          <i style="background:${item.color}"></i><span>${esc(item.label)}</span><em>${count(counts[item.key] || 0)}</em>
        </button>`;
      }).join("")}
    </div>`;
  }

  function periodStatusCountsForSeries(periods) {
    const counts = { running: 0, profit: 0, expired: 0, other: 0 };
    periods.forEach((period) => {
      const key = lifecycleStatusStyle(period).key;
      counts[key] = (counts[key] || 0) + 1;
    });
    return counts;
  }

  function seriesStatusFromCounts(counts, row = {}) {
    const running = counts.running || num(row.运行中期次数, 0);
    const profit = counts.profit || num(row.止盈期数, 0) || num(row.已止盈期次数, 0);
    const expired = counts.expired || 0;
    if (running > 0) return statusStyles.running;
    if (profit > 0) return statusStyles.profit;
    if (expired > 0) return statusStyles.expired;
    return statusStyles.other;
  }

  function renderScatter(seriesRows, periodRows, statusBasePeriods, allSeriesRows = pack.series || [], allPeriodRows = pack.periods || []) {
    const filteredIds = new Set((seriesRows || []).map((row) => row.系列ID));
    const allIds = new Set((allSeriesRows || []).map((row) => row.系列ID));
    const filterContextActive = filteredIds.size < allIds.size || (periodRows || []).length < (allPeriodRows || []).length;
    const enrichedRows = enrichSeriesRows(allSeriesRows, allPeriodRows);
    const xMetric = metricByKey(seriesScatterOptions, state.scatterXField);
    const yMetric = metricByKey(seriesScatterOptions, state.scatterYField);
    const rows = enrichedRows.filter((row) => xMetric.value(row) !== null && yMetric.value(row) !== null);
    const width = 860;
    const height = 390;
    const pad = { left: 58, right: 28, top: 28, bottom: 48 };
    const xDomain = metricDomain(rows.map(xMetric.value), xMetric);
    const yDomain = metricDomain(rows.map(yMetric.value), yMetric);
    const xTicks = ticks(xDomain);
    const yTicks = ticks(yDomain);
    const selectedId = state.selectedSeriesId && rows.some((row) => row.系列ID === state.selectedSeriesId) ? state.selectedSeriesId : "";
    const selectionMode = Boolean(selectedId);
    const pointState = (row) => {
      const selected = row.系列ID === selectedId;
      const advisorHighlighted = state.highlightAdvisor && row.投顾机构 === state.highlightAdvisor;
      const filterMatched = filteredIds.has(row.系列ID);
      const mutedBySelection = selectionMode && !selected;
      const mutedByAdvisor = !selectionMode && state.highlightAdvisor && !advisorHighlighted;
      const mutedByFilter = !selectionMode && !state.highlightAdvisor && filterContextActive && !filterMatched;
      const muted = mutedBySelection || mutedByAdvisor || mutedByFilter;
      const drawRank = selected ? 4 : advisorHighlighted ? 3 : filterMatched ? 2 : muted ? 0 : 1;
      return { selected, advisorHighlighted, filterMatched, muted, drawRank };
    };
    const pointRows = [...rows].sort((a, b) => pointState(a).drawRank - pointState(b).drawRank);
    return `
      <section class="panel target-scatter-panel">
        <div class="panel-head">
          <div>
            <h2>系列点阵</h2>
            <p class="desc">每个点代表一个目标盈系列，可切换收益、回撤、运作天数、止盈期数、止盈率、夏普和卡玛比率等维度；彩色点为当前筛选命中或高亮对象，灰色点为未命中筛选的背景样本，仍可点击切换。</p>
          </div>
          <div class="chart-actions target-scatter-controls">
            <label class="filter-field"><span>横轴</span><select class="control" data-axis="scatterX">${optionSelect(seriesScatterOptions, xMetric.key)}</select></label>
            <label class="filter-field"><span>纵轴</span><select class="control" data-axis="scatterY">${optionSelect(seriesScatterOptions, yMetric.key)}</select></label>
          </div>
        </div>
        <div class="topic-scatter-wrap target-scatter-wrap">
          ${statusLegend(statusBasePeriods || periodRows, "系列点阵状态筛选")}
          <div class="target-scatter-hint"><span><i class="is-active"></i>当前筛选命中/高亮</span><span><i></i>未命中筛选的背景系列</span><span><i class="is-gf"></i>广发基金投顾</span></div>
          <svg class="target-dot-plot target-series-dot-plot" viewBox="0 0 ${width} ${height}" role="img" aria-label="目标盈系列点阵">
            <rect x="${pad.left}" y="${pad.top}" width="${width - pad.left - pad.right}" height="${height - pad.top - pad.bottom}" rx="8" fill="#fbfdff" stroke="#dbe4ee"></rect>
            ${xTicks.map((tick) => {
              const x = scale(tick, xDomain.min, xDomain.max, pad.left, width - pad.right);
              return `<line x1="${x}" y1="${pad.top}" x2="${x}" y2="${height - pad.bottom}" stroke="#e6edf5"></line><text x="${x}" y="${height - 18}" text-anchor="middle" class="topic-axis-text">${metricAxisText(xMetric, tick)}</text>`;
            }).join("")}
            ${yTicks.map((tick) => {
              const y = scale(tick, yDomain.min, yDomain.max, height - pad.bottom, pad.top);
              return `<line x1="${pad.left}" y1="${y}" x2="${width - pad.right}" y2="${y}" stroke="#e6edf5"></line><text x="${pad.left - 10}" y="${y + 4}" text-anchor="end" class="topic-axis-text">${metricAxisText(yMetric, tick)}</text>`;
            }).join("")}
            <text x="${width / 2}" y="${height - 4}" text-anchor="middle" class="topic-axis-title">${esc(xMetric.label)}</text>
            <text x="16" y="${height / 2}" transform="rotate(-90 16 ${height / 2})" text-anchor="middle" class="topic-axis-title">${esc(yMetric.label)}</text>
            ${pointRows.map((row) => {
              const xValue = xMetric.value(row);
              const yValue = yMetric.value(row);
              const jitter = jitterPair(row.系列ID || row.系列名称, 2.4);
              const x = clamp(plotCoordinate(xValue, xDomain, pad.left, width - pad.right) + jitter.dx, pad.left + 4, width - pad.right - 4);
              const y = clamp(plotCoordinate(yValue, yDomain, height - pad.bottom, pad.top) + jitter.dy, pad.top + 4, height - pad.bottom - 4);
              const radius = Math.min(9.2, 3.6 + Math.sqrt(num(row.期次数, 1)) * 0.58);
              const point = pointState(row);
              const style = statusStyles[row.系列状态Key] || statusStyles.other;
              const cls = point.selected ? "is-selected" : point.muted ? "is-background is-muted" : point.advisorHighlighted ? "is-highlighted" : "is-colored";
              const fill = point.muted ? "#cbd5e1" : style.color;
              const filterLabel = filterContextActive ? (point.filterMatched ? "当前筛选命中" : "未命中当前筛选") : "全量样本";
              const gfAdvisor = isGfAdvisor(row);
              const dotRadius = point.selected || point.advisorHighlighted ? radius + 1.4 : radius;
              const gfRing = gfAdvisor ? `<circle class="target-gf-ring ${point.muted ? "is-muted" : ""}" cx="${x.toFixed(2)}" cy="${y.toFixed(2)}" r="${(dotRadius + 2.2).toFixed(2)}"></circle>` : "";
              const tip = `${row.系列名称}｜${style.label}｜${filterLabel}｜${xMetric.label} ${metricDisplay(xMetric, xValue)}｜${yMetric.label} ${metricDisplay(yMetric, yValue)}｜${row.投顾机构 || ""}`;
              return `${gfRing}<circle class="topic-scatter-point target-dot ${cls} ${gfAdvisor ? "is-gf-advisor" : ""}" cx="${x.toFixed(2)}" cy="${y.toFixed(2)}" r="${dotRadius.toFixed(2)}" fill="${fill}" data-select-series="${esc(row.系列ID)}" data-tip="${esc(tip)}"><title>${esc(tip)}</title></circle>`;
            }).join("")}
          </svg>
          ${renderSelectedSeriesDetail(selectedId, rows, allPeriodRows)}
        </div>
      </section>
    `;
  }

  function signedText(value) {
    return !isBlank(value) && Number.isFinite(Number(value)) ? `${Number(value).toFixed(2)}%` : "未披露";
  }

  function renderSelectedSeriesDetail(seriesId, seriesRows = pack.series || [], periodRows = pack.periods || []) {
    const row = (seriesRows || []).find((item) => item.系列ID === seriesId) || (pack.series || []).find((item) => item.系列ID === seriesId);
    if (!row) return '<div class="topic-point-detail"><strong>暂无选中系列</strong><p class="desc">点击点阵或系列表中的系列名称后，仅该系列点保持彩色高亮，其他系列作为灰色背景。</p></div>';
    const periods = (periodRows || pack.periods || []).filter((item) => item.系列ID === row.系列ID);
    return `
      <div class="topic-point-detail target-selected-detail">
        <div class="target-selected-head">
          <strong>${esc(row.系列名称)}</strong>
          <button class="ghost-button" type="button" data-action="clearSeriesSelection">取消系列高亮</button>
        </div>
        <p class="desc">${esc(row.投顾机构)}｜${esc(row.风险等级)}｜${esc(row.研报产品类型)}</p>
        <div class="topic-point-kpis">
          <span>期次数<b>${count(row.期次数)}</b></span>
          <span>运行中<b>${count(row.运行中期次数)}</b></span>
          <span>达标率<b>${pct(row.达标率)}</b></span>
          <span>中位收益<b>${signedPct(row.中位生命周期收益)}</b></span>
          <span>中位回撤<b>${pct(row.中位生命周期最大回撤)}</b></span>
          <span>中位天数<b>${count(row.中位生命周期天数)}</b></span>
          <span>平均天数<b>${metricDisplay({ type: "days" }, row.平均运作天数)}</b></span>
          <span>止盈率<b>${pct(row.止盈率)}</b></span>
          <span>夏普<b>${metricDisplay({ type: "ratio" }, row.中位夏普比率)}</b></span>
          <span>卡玛<b>${metricDisplay({ type: "ratio" }, row.卡玛比率)}</b></span>
        </div>
        <div class="target-period-mini-list">
          ${sortRows(periods, "期次序号", "asc").map((period) => `<a class="link" href="${strategyUrl(period.统一策略ID)}">${esc(period.策略名称)}</a>`).join("")}
        </div>
      </div>
    `;
  }

  function renderLifecycleChart(periodRows, statusBasePeriods) {
    const xMetric = metricByKey(periodScatterOptions, state.detailXField);
    const yMetric = metricByKey(periodScatterOptions, state.detailYField);
    const rows = periodRows.filter((row) => xMetric.value(row) !== null && yMetric.value(row) !== null);
    const width = 920;
    const height = 420;
    const pad = { left: 64, right: 28, top: 28, bottom: 54 };
    const xDomain = metricDomain(rows.map(xMetric.value), xMetric);
    const yDomain = metricDomain(rows.map(yMetric.value), yMetric);
    const xTicks = ticks(xDomain);
    const yTicks = ticks(yDomain);
    const selectedPeriodId = state.selectedPeriodId || rows.find((row) => state.selectedSeriesId && row.系列ID === state.selectedSeriesId)?.统一策略ID || rows[0]?.统一策略ID || "";
    const zeroLine = yDomain.min <= 0 && yDomain.max >= 0
      ? `<line x1="${pad.left}" y1="${scale(0, yDomain.min, yDomain.max, height - pad.bottom, pad.top)}" x2="${width - pad.right}" y2="${scale(0, yDomain.min, yDomain.max, height - pad.bottom, pad.top)}" stroke="#98a2b3" stroke-width="1.2"></line>`
      : "";
    return `
      <section class="panel chart-panel target-line-panel">
        <div class="chart-toolbar">
          <div>
            <h2>明细点阵</h2>
            <p class="desc">每个点代表一个目标盈期次，横纵轴均可切换运行天数、收益、回撤、目标收益、夏普等口径；点位做了轻微固定偏移，减少重叠。</p>
          </div>
          <div class="chart-actions">
            <label class="filter-field"><span>横轴</span><select class="control" data-axis="detailX">${optionSelect(periodScatterOptions, xMetric.key)}</select></label>
            <label class="filter-field"><span>纵轴</span><select class="control" data-axis="detailY">${optionSelect(periodScatterOptions, yMetric.key)}</select></label>
          </div>
        </div>
        <div class="topic-scatter-wrap target-scatter-wrap">
          ${statusLegend(statusBasePeriods || periodRows, "明细点阵状态筛选")}
          <svg class="target-dot-plot target-detail-dot-plot" viewBox="0 0 ${width} ${height}" role="img" aria-label="目标盈明细点阵">
            <rect x="${pad.left}" y="${pad.top}" width="${width - pad.left - pad.right}" height="${height - pad.top - pad.bottom}" rx="8" fill="#fbfdff" stroke="#dbe4ee"></rect>
            ${xTicks.map((tick) => {
              const x = scale(tick, xDomain.min, xDomain.max, pad.left, width - pad.right);
              return `<line x1="${x}" y1="${pad.top}" x2="${x}" y2="${height - pad.bottom}" stroke="#e6edf5"></line><text x="${x}" y="${height - 20}" text-anchor="middle" class="topic-axis-text">${metricAxisText(xMetric, tick)}</text>`;
            }).join("")}
            ${yTicks.map((tick) => {
              const y = scale(tick, yDomain.min, yDomain.max, height - pad.bottom, pad.top);
              return `<line x1="${pad.left}" y1="${y}" x2="${width - pad.right}" y2="${y}" stroke="#e6edf5"></line><text x="${pad.left - 10}" y="${y + 4}" text-anchor="end" class="topic-axis-text">${metricAxisText(yMetric, tick)}</text>`;
            }).join("")}
            ${zeroLine}
            <text x="${width / 2}" y="${height - 5}" text-anchor="middle" class="topic-axis-title">${esc(xMetric.label)}</text>
            <text x="16" y="${height / 2}" transform="rotate(-90 16 ${height / 2})" text-anchor="middle" class="topic-axis-title">${esc(yMetric.label)}</text>
            ${rows.map((row) => {
              const xValue = xMetric.value(row);
              const yValue = yMetric.value(row);
              const jitter = jitterPair(row.统一策略ID || `${row.系列ID}_${row.期次序号}`, 3.8);
              const x = clamp(plotCoordinate(xValue, xDomain, pad.left, width - pad.right) + jitter.dx, pad.left + 3, width - pad.right - 3);
              const y = clamp(plotCoordinate(yValue, yDomain, height - pad.bottom, pad.top) + jitter.dy, pad.top + 3, height - pad.bottom - 3);
              const style = lifecycleStatusStyle(row);
              const selected = row.统一策略ID === selectedPeriodId;
              const sameSeries = state.selectedSeriesId && row.系列ID === state.selectedSeriesId;
              const sameAdvisor = state.highlightAdvisor && row.投顾机构 === state.highlightAdvisor;
              const muted = (state.selectedSeriesId && !sameSeries) || (!state.selectedSeriesId && state.highlightAdvisor && !sameAdvisor);
              const radius = selected ? 5.8 : (sameSeries || sameAdvisor ? 4.9 : 3.6);
              const tip = `${row.策略名称}｜${style.label}｜${xMetric.label}${metricDisplay(xMetric, xValue)}｜${yMetric.label}${metricDisplay(yMetric, yValue)}｜回撤${pct(row.生命周期最大回撤)}`;
              const cls = selected ? "is-selected" : muted ? "is-background is-muted" : (sameSeries || sameAdvisor) ? "is-highlighted" : "is-colored";
              const gfAdvisor = isGfAdvisor(row);
              const gfRing = gfAdvisor ? `<circle class="target-gf-ring ${muted ? "is-muted" : ""}" cx="${x.toFixed(2)}" cy="${y.toFixed(2)}" r="${(radius + 2.1).toFixed(2)}"></circle>` : "";
              return `${gfRing}<circle class="topic-scatter-point ${cls} ${gfAdvisor ? "is-gf-advisor" : ""} target-dot target-life-dot target-life-dot-${style.key}" cx="${x.toFixed(2)}" cy="${y.toFixed(2)}" r="${radius}" fill="${muted ? "#cbd5e1" : style.color}" data-select-period="${esc(row.统一策略ID)}" data-select-series="${esc(row.系列ID)}" data-tip="${esc(tip)}"><title>${esc(tip)}</title></circle>`;
            }).join("")}
          </svg>
          <div class="target-chart-tooltip" id="targetChartTooltip" hidden></div>
          ${renderSelectedPeriodDetail(selectedPeriodId, rows)}
        </div>
      </section>
    `;
  }

  function renderSelectedPeriodDetail(periodId, periodRows) {
    const row = (periodRows || []).find((item) => item.统一策略ID === periodId);
    if (!row) return '<div class="topic-point-detail target-selected-detail"><strong>暂无选中期次</strong><p class="desc">点击生命周期点阵中的点查看该期详细信息。</p></div>';
    const style = lifecycleStatusStyle(row);
    return `
      <div class="topic-point-detail target-selected-detail">
        <strong><a class="link" href="${strategyUrl(row.统一策略ID)}">${esc(row.策略名称)}</a></strong>
        <p class="desc">${esc(row.系列名称)}｜${esc(row.投顾机构)}｜${esc(row.风险等级)}｜<span style="color:${style.color};font-weight:800">${esc(style.label)}</span></p>
        <div class="topic-point-kpis">
          <span>成立日期<b>${valueHtml("成立日期", row.成立日期)}</b></span>
          <span>运行天数<b>${metricDisplay({ type: "days" }, row.生命周期天数)}</b></span>
          <span>目标收益<b>${pct(row.目标收益率)}</b></span>
          <span>生命周期收益<b>${signedPct(row.生命周期收益)}</b></span>
          <span>峰值收益<b>${signedPct(row.生命周期峰值收益)}</b></span>
          <span>生命周期回撤<b>${pct(row.生命周期最大回撤)}</b></span>
          <span>年化收益<b>${signedPct(row.年化收益)}</b></span>
          <span>夏普<b>${metricDisplay({ type: "ratio" }, row.夏普比率)}</b></span>
        </div>
        <p class="desc">${esc(row.生命周期状态依据 || "")}</p>
      </div>
    `;
  }

  function pickLifecycleLinePeriods(periods) {
    const ranked = [...periods].sort((a, b) => {
      const activeA = a.生命周期状态 === "运行中观察" || a.生命周期状态 === "预约期" ? 1 : 0;
      const activeB = b.生命周期状态 === "运行中观察" || b.生命周期状态 === "预约期" ? 1 : 0;
      return activeB - activeA
        || text(b.成立日期).localeCompare(text(a.成立日期), "zh-CN")
        || (num(b.生命周期收益, -999) - num(a.生命周期收益, -999));
    });
    const picked = [];
    const seen = new Set();
    const add = (row) => {
      if (!row || seen.has(row.统一策略ID) || picked.length >= 8) return;
      seen.add(row.统一策略ID);
      picked.push(row);
    };
    ranked.forEach(add);
    if (picked.length < 8) periods.forEach(add);
    return sortRows(picked, "期次序号", "asc");
  }

  function lifecycleTimeline(periods) {
    if (!periods.length) return '<div class="empty">当前筛选下没有该系列期次。</div>';
    const maxDays = Math.max(30, ...periods.map((row) => num(row.生命周期天数, 0)));
    return `<div class="target-lifecycle-strip">
      ${periods.map((row) => {
        const days = num(row.生命周期天数, 0);
        const width = Math.max(4, Math.min(100, days / maxDays * 100));
        const cls = row.生命周期状态 === "运行中观察" || row.生命周期状态 === "预约期" ? "is-active" : (/止盈/.test(row.生命周期状态 || "") ? "is-hit" : "is-closed");
        const tip = `${row.策略名称}｜${row.生命周期状态 || "未披露"}｜收益 ${signedText(row.生命周期收益)}｜峰值 ${signedText(row.生命周期峰值收益)}｜回撤 ${pct(row.生命周期最大回撤)}｜${row.生命周期状态依据 || ""}`;
        return `<div class="target-life-row" data-tip="${esc(tip)}">
          <div class="target-life-name"><a class="link" href="${strategyUrl(row.统一策略ID)}">${esc(row.策略名称)}</a><span>${esc(row.期次版本 || row.子系列名称 || "")}</span></div>
          <div class="target-life-track"><b class="${cls}" style="width:${width.toFixed(2)}%"></b></div>
          <div class="target-life-meta"><span>${esc(row.生命周期状态 || "未披露")}</span><b>${signedPct(row.生命周期收益)}</b><em>${count(days)}天</em></div>
        </div>`;
      }).join("")}
    </div>`;
  }

  function lineChartSvg(periods) {
    if (!periods.length) return '<div class="empty">当前筛选下缺少可绘制净值曲线</div>';
    const width = 920;
    const height = 400;
    const pad = { left: 58, right: 28, top: 24, bottom: 46 };
    const allPoints = periods.flatMap((row) => pack.curves?.[row.统一策略ID] || []);
    const maxDay = Math.max(30, ...allPoints.map((point) => num(point.day, 0)));
    const yMin = Math.min(0, Math.floor(Math.min(...allPoints.map((point) => num(point.returnPct, 0))) / 5) * 5);
    const yMax = Math.max(5, Math.ceil(Math.max(...allPoints.map((point) => num(point.returnPct, 0))) / 5) * 5);
    const xTicks = [0, maxDay * 0.25, maxDay * 0.5, maxDay * 0.75, maxDay];
    const yTicks = [yMin, yMin + (yMax - yMin) * 0.25, yMin + (yMax - yMin) * 0.5, yMin + (yMax - yMin) * 0.75, yMax];
    const pathFor = (points) => points.map((point, index) => {
      const x = scale(num(point.day, 0), 0, maxDay, pad.left, width - pad.right);
      const y = scale(num(point.returnPct, 0), yMin, yMax, height - pad.bottom, pad.top);
      return `${index ? "L" : "M"}${x.toFixed(2)},${y.toFixed(2)}`;
    }).join(" ");
    return `
      <svg viewBox="0 0 ${width} ${height}" role="img" aria-label="目标盈生命周期走势">
        <rect x="${pad.left}" y="${pad.top}" width="${width - pad.left - pad.right}" height="${height - pad.top - pad.bottom}" rx="8" fill="#fbfdff" stroke="#dbe4ee"></rect>
        ${xTicks.map((tick) => {
          const x = scale(tick, 0, maxDay, pad.left, width - pad.right);
          return `<line x1="${x}" y1="${pad.top}" x2="${x}" y2="${height - pad.bottom}" stroke="#e6edf5"></line><text x="${x}" y="${height - 17}" text-anchor="middle" class="topic-axis-text">${Math.round(tick)}天</text>`;
        }).join("")}
        ${yTicks.map((tick) => {
          const y = scale(tick, yMin, yMax, height - pad.bottom, pad.top);
          return `<line x1="${pad.left}" y1="${y}" x2="${width - pad.right}" y2="${y}" stroke="#e6edf5"></line><text x="${pad.left - 10}" y="${y + 4}" text-anchor="end" class="topic-axis-text">${tick.toFixed(0)}%</text>`;
        }).join("")}
        <line x1="${pad.left}" y1="${scale(0, yMin, yMax, height - pad.bottom, pad.top)}" x2="${width - pad.right}" y2="${scale(0, yMin, yMax, height - pad.bottom, pad.top)}" stroke="#98a2b3" stroke-width="1.2"></line>
        ${periods.map((row, idx) => {
          const points = pack.curves?.[row.统一策略ID] || [];
          const color = chartColors[idx % chartColors.length];
          const dots = points.map((point) => {
            const x = scale(num(point.day, 0), 0, maxDay, pad.left, width - pad.right);
            const y = scale(num(point.returnPct, 0), yMin, yMax, height - pad.bottom, pad.top);
            const tip = `${row.策略名称}｜第${Math.round(num(point.day, 0))}天｜${point.date || ""}｜收益 ${signedText(point.returnPct)}｜状态 ${row.生命周期状态 || "未披露"}`;
            return `<circle class="target-line-hover-point" cx="${x.toFixed(2)}" cy="${y.toFixed(2)}" r="5" fill="${color}" data-tip="${esc(tip)}"></circle>`;
          }).join("");
          return `<g><path class="topic-line-path" d="${pathFor(points)}" fill="none" stroke="${color}" stroke-width="2.4"><title>${esc(row.策略名称)}｜生命周期收益 ${signedText(row.生命周期收益)}｜最大回撤 ${pct(row.生命周期最大回撤)}</title></path>${dots}</g>`;
        }).join("")}
      </svg>
    `;
  }

  function seriesTable(rows, periodRows) {
    const enrichedRows = enrichSeriesRows(rows, periodRows);
    const tableRows = enrichedRows.filter((row) => {
      if (state.seriesStatus !== "__all__" && row.系列状态Key !== state.seriesStatus) return false;
      if (!state.seriesQuery) return true;
      return [row.系列名称, row.投顾机构, row.风险等级, row.研报产品类型, row.代表期次, row.系列状态].some((value) => text(value).toLowerCase().includes(state.seriesQuery.toLowerCase()));
    });
    const sorted = sortRows(tableRows, state.seriesSortField, state.seriesSortDir);
    const statusOptions = [{ key: "__all__", label: "全部状态" }, ...statusOrder.map((key) => statusStyles[key])];
    const headers = ["系列名称", "系列状态", "投顾机构", "风险等级", "研报产品类型", "期次数", "运行中期次数", "达标率", "中位生命周期收益", "中位生命周期最大回撤", "中位生命周期天数", "中位近1年", "代表期次"];
    return `
      <section class="panel target-table-panel">
        <div class="panel-head">
          <div>
            <h2>系列分析</h2>
            <p class="desc">当前筛选后 ${count(rows.length)} 个系列，表内命中 ${count(tableRows.length)} 个。点击表头可排序，点击系列名称同步点阵和走势。</p>
          </div>
          <div class="chart-actions target-series-controls">
            <label class="filter-field"><span>系列筛选</span><input class="control" data-series-filter="seriesQuery" value="${esc(state.seriesQuery)}" placeholder="系列、机构、风险或代表期次"></label>
            <label class="filter-field"><span>系列状态</span><select class="control" data-series-filter="seriesStatus">${optionSelect(statusOptions, state.seriesStatus)}</select></label>
          </div>
        </div>
        <div class="table-wrap target-table-wrap">
          <table class="target-table">
            <thead><tr>${headers.map((field) => `<th>${sortButton(field, state.seriesSortField, state.seriesSortDir, "series")}</th>`).join("")}</tr></thead>
            <tbody>
              ${sorted.map((row) => `
                <tr class="${row.系列ID === state.selectedSeriesId ? "is-selected" : ""}">
                  <td><button class="link-button" type="button" data-select-series="${esc(row.系列ID)}">${esc(row.系列名称)}</button></td>
                  <td><span class="target-status-chip readonly"><i style="background:${esc(row.系列状态颜色)}"></i><span>${esc(row.系列状态)}</span></span></td>
                  <td>${esc(row.投顾机构)}</td>
                  <td>${esc(row.风险等级)}</td>
                  <td>${esc(row.研报产品类型)}</td>
                  <td>${count(row.期次数)}</td>
                  <td>${count(row.运行中期次数)}</td>
                  <td>${pct(row.达标率)}</td>
                  <td>${signedPct(row.中位生命周期收益)}</td>
                  <td>${pct(row.中位生命周期最大回撤)}</td>
                  <td>${valueHtml("中位生命周期天数", row.中位生命周期天数)}</td>
                  <td>${signedPct(row.中位近1年)}</td>
                  <td>${esc(row.代表期次)}</td>
                </tr>
              `).join("") || `<tr><td colspan="${headers.length}"><div class="empty">暂无数据</div></td></tr>`}
            </tbody>
          </table>
        </div>
      </section>
    `;
  }

  function periodTable(rows) {
    const sorted = sortRows(rows, state.periodSortField, state.periodSortDir);
    const headers = ["策略名称", "系列名称", "期次序号", "期次版本", "投顾机构", "风险等级", "目标状态", "生命周期状态", "状态依据", "成立日期", "目标收益率", "生命周期收益", "生命周期峰值收益", "生命周期最大回撤", "生命周期天数", "近1年", "最大回撤", "曲线来源"];
    return `
      <section class="panel target-table-panel">
        <div class="panel-head">
          <div>
            <h2>期次明细</h2>
            <p class="desc">共 ${count(rows.length)} 个期次。期次用于核验真实运行结果，不做系列归并。</p>
          </div>
        </div>
        <div class="table-wrap target-table-wrap">
          <table class="target-table">
            <thead><tr>${headers.map((field) => `<th>${sortButton(field, state.periodSortField, state.periodSortDir, "period")}</th>`).join("")}</tr></thead>
            <tbody>
              ${sorted.map((row) => `
                <tr>
                  <td><a class="link" href="${strategyUrl(row.统一策略ID)}">${esc(row.策略名称)}</a></td>
                  <td>${esc(row.系列名称)}</td>
                  <td>${valueHtml("期次序号", row.期次序号)}</td>
                  <td>${esc(row.期次版本 || "")}</td>
                  <td>${esc(row.投顾机构)}</td>
                  <td>${esc(row.风险等级)}</td>
                  <td><span class="target-status">${esc(targetStatus(row))}</span></td>
                  <td><span class="target-status">${esc(row.生命周期状态)}</span></td>
                  <td><span class="small">${esc(row.生命周期状态依据 || "")}</span></td>
                  <td>${valueHtml("成立日期", row.成立日期)}</td>
                  <td>${pct(row.目标收益率)}</td>
                  <td>${signedPct(row.生命周期收益)}</td>
                  <td>${signedPct(row.生命周期峰值收益)}</td>
                  <td>${pct(row.生命周期最大回撤)}</td>
                  <td>${valueHtml("生命周期天数", row.生命周期天数)}</td>
                  <td>${signedPct(row.近1年)}</td>
                  <td>${pct(row.最大回撤)}</td>
                  <td>${esc(row.曲线来源)}</td>
                </tr>
              `).join("") || `<tr><td colspan="${headers.length}"><div class="empty">暂无数据</div></td></tr>`}
            </tbody>
          </table>
        </div>
      </section>
    `;
  }

  function restoreQueryFocus() {
    if (!querySelection) return;
    const input = root.querySelector(querySelection.selector || '[data-filter="query"]');
    if (!input) return;
    input.focus();
    try {
      input.setSelectionRange(querySelection.start, querySelection.end);
    } catch {
      // Some browsers may not allow selection restoration on non-text inputs.
    }
  }

  function render(options = {}) {
    if (!pack.overview) {
      root.innerHTML = `<section class="panel"><h1>目标盈分析</h1><p class="desc">缺少目标盈分析数据包，请先运行报表导出脚本。</p></section>`;
      return;
    }
    const allPeriodRows = pack.periods || [];
    const allSeriesRows = pack.series || [];
    const statusBasePeriods = filteredPeriods({ ignoreStatusGroup: true });
    const periodRows = filteredPeriods();
    const seriesRows = filteredSeries(periodRows);
    if (state.selectedSeriesId && !allSeriesRows.some((row) => row.系列ID === state.selectedSeriesId)) state.selectedSeriesId = "";
    if (state.selectedPeriodId && !periodRows.some((row) => row.统一策略ID === state.selectedPeriodId)) state.selectedPeriodId = "";
    if (state.highlightAdvisor && !allPeriodRows.some((row) => row.投顾机构 === state.highlightAdvisor)) state.highlightAdvisor = "";
    root.innerHTML = `
      <div class="page-title">
        <div>
          <h1>目标盈分析</h1>
          <p class="desc">单独观察目标盈系列产品的供给、生命周期表现、止盈达标和期次质量。数据更新至 ${esc(pack.overview.数据更新至 || "未披露")}。</p>
        </div>
        <div class="title-pills">
          <span class="pill">系列 ${count(seriesRows.length)}</span>
          <span class="pill">期次 ${count(periodRows.length)}</span>
          <span class="pill">生成 ${esc((pack.generatedAt || "").slice(0, 19).replace("T", " "))}</span>
        </div>
      </div>
      ${overviewMetrics()}
      ${renderGuide()}
      ${renderFilters()}
      ${renderAdvisorBars(periodRows, seriesRows)}
      ${renderScatter(seriesRows, periodRows, statusBasePeriods, allSeriesRows, allPeriodRows)}
      ${renderLifecycleChart(periodRows, statusBasePeriods)}
      ${seriesTable(seriesRows, periodRows)}
      ${periodTable(periodRows)}
    `;
    if (options.preserveQueryFocus) restoreQueryFocus();
  }

  root.addEventListener("change", (event) => {
    const target = event.target;
    if (!target) return;
    const filter = target.getAttribute("data-filter");
    if (filter) {
      state[filter] = target.value;
      render();
      return;
    }
    const seriesFilter = target.getAttribute("data-series-filter");
    if (seriesFilter) {
      state[seriesFilter] = target.value;
      render();
      return;
    }
    const axis = target.getAttribute("data-axis");
    if (axis === "detailX") {
      state.detailXField = target.value;
      render();
      return;
    }
    if (axis === "detailY") {
      state.detailYField = target.value;
      render();
      return;
    }
    if (axis === "scatterX") {
      state.scatterXField = target.value;
      render();
      return;
    }
    if (axis === "scatterY") {
      state.scatterYField = target.value;
      render();
      return;
    }
    if (target.getAttribute("data-action") === "selectSeries") {
      state.selectedSeriesId = target.value;
      render();
    }
  });

  root.addEventListener("input", (event) => {
    const target = event.target;
    const globalQuery = target?.getAttribute("data-filter") === "query";
    const seriesQuery = target?.getAttribute("data-series-filter") === "seriesQuery";
    if (globalQuery || seriesQuery) {
      if (globalQuery) state.query = target.value;
      if (seriesQuery) state.seriesQuery = target.value;
      querySelection = { selector: globalQuery ? '[data-filter="query"]' : '[data-series-filter="seriesQuery"]', start: target.selectionStart || 0, end: target.selectionEnd || 0 };
      if (queryComposing) return;
      clearTimeout(queryRenderTimer);
      queryRenderTimer = setTimeout(() => render({ preserveQueryFocus: true }), 250);
    }
  });

  root.addEventListener("compositionstart", (event) => {
    if (event.target?.getAttribute("data-filter") === "query" || event.target?.getAttribute("data-series-filter") === "seriesQuery") queryComposing = true;
  });

  root.addEventListener("compositionend", (event) => {
    const target = event.target;
    const globalQuery = target?.getAttribute("data-filter") === "query";
    const seriesQuery = target?.getAttribute("data-series-filter") === "seriesQuery";
    if (globalQuery || seriesQuery) {
      queryComposing = false;
      if (globalQuery) state.query = target.value;
      if (seriesQuery) state.seriesQuery = target.value;
      querySelection = { selector: globalQuery ? '[data-filter="query"]' : '[data-series-filter="seriesQuery"]', start: target.selectionStart || 0, end: target.selectionEnd || 0 };
      clearTimeout(queryRenderTimer);
      queryRenderTimer = setTimeout(() => render({ preserveQueryFocus: true }), 120);
    }
  });

  root.addEventListener("mousemove", (event) => {
    const tipTarget = event.target.closest("[data-tip]");
    const tip = root.querySelector("#targetChartTooltip");
    if (!tip || !tipTarget) return;
    tip.hidden = false;
    tip.innerHTML = esc(tipTarget.getAttribute("data-tip") || "");
    const rect = root.getBoundingClientRect();
    tip.style.left = `${event.clientX - rect.left + 14}px`;
    tip.style.top = `${event.clientY - rect.top + 14}px`;
  });

  root.addEventListener("mouseleave", () => {
    const tip = root.querySelector("#targetChartTooltip");
    if (tip) tip.hidden = true;
  });

  root.addEventListener("click", (event) => {
    const target = event.target.closest("button, circle");
    if (!target) return;
    if (target.getAttribute("data-action") === "resetFilters") {
      state.advisor = "__all__";
      state.risk = "__all__";
      state.type = "__all__";
      state.status = "__all__";
      state.statusGroup = "__all__";
      state.highlightAdvisor = "";
      state.selectedSeriesId = "";
      state.selectedPeriodId = "";
      state.seriesQuery = "";
      state.seriesStatus = "__all__";
      state.query = "";
      render();
      return;
    }
    const statusGroup = target.getAttribute("data-status-group");
    if (statusGroup) {
      state.statusGroup = state.statusGroup === statusGroup ? "__all__" : statusGroup;
      state.selectedSeriesId = "";
      state.selectedPeriodId = "";
      render();
      return;
    }
    const highlightAdvisor = target.getAttribute("data-highlight-advisor");
    if (highlightAdvisor) {
      state.highlightAdvisor = state.highlightAdvisor === highlightAdvisor ? "" : highlightAdvisor;
      state.selectedSeriesId = "";
      state.selectedPeriodId = "";
      render();
      return;
    }
    if (target.getAttribute("data-action") === "clearSeriesSelection") {
      state.selectedSeriesId = "";
      render();
      return;
    }
    const selectedPeriod = target.getAttribute("data-select-period");
    if (selectedPeriod) {
      state.selectedPeriodId = selectedPeriod;
      const selectedSeries = target.getAttribute("data-select-series");
      if (selectedSeries) state.selectedSeriesId = selectedSeries;
      state.highlightAdvisor = "";
      render();
      return;
    }
    const selected = target.getAttribute("data-select-series");
    if (selected) {
      state.selectedSeriesId = state.selectedSeriesId === selected ? "" : selected;
      state.highlightAdvisor = "";
      render();
      return;
    }
    const sortTable = target.getAttribute("data-sort-table");
    const sortField = target.getAttribute("data-sort-field");
    if (sortTable && sortField) {
      const fieldKey = sortTable === "series" ? "seriesSortField" : "periodSortField";
      const dirKey = sortTable === "series" ? "seriesSortDir" : "periodSortDir";
      if (state[fieldKey] === sortField) {
        state[dirKey] = state[dirKey] === "asc" ? "desc" : "asc";
      } else {
        state[fieldKey] = sortField;
        state[dirKey] = "desc";
      }
      render();
    }
  });

  render();
})();
