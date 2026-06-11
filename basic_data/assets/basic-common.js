
window.__BASIC_DATA__ = window.__BASIC_DATA__ || { details: {} };
window.BasicData = (() => {
  const state = window.__BASIC_DATA__;
  const byId = (id) => document.getElementById(id);
  const esc = (value) => String(value ?? "").replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;").replaceAll("'", "&#39;");
  const dict = () => state.summary?.fieldDictionary || {};
  const collectedFieldNames = new Set([
    "数据更新至", "数据刷新时间", "渠道", "渠道类型", "策略数", "策略名称", "投顾机构", "策略类型", "风险等级",
    "成立日期", "运作状态", "策略代码", "统一策略ID", "起投金额", "投顾费率", "建议持有时长", "业绩基准",
    "标签", "策略概念", "策略描述", "业绩基准说明", "官方单位净值", "官方累计收益", "最新业绩日期", "收益数据截至",
    "最新业绩日", "最新调仓日", "最新持仓日", "最近调仓日", "基金代码", "基金名称", "资产类型", "分组",
    "基金净值", "净值日期", "日涨幅", "调仓日期", "披露日期", "调仓标题", "调前权重", "调后权重",
    "调仓基金数", "调后权重和", "投顾费率", "业绩基准", "原始数据来源", "曲线来源", "披露业绩"
  ]);
  const derivedFieldNames = new Set([
    "数据来源标记", "接入渠道数", "策略总数", "天天策略数", "有历史调仓策略数", "有官方业绩策略数", "纳入回放策略数",
    "策略基金净值缺失数", "完整策略数", "官方业绩覆盖", "历史调仓覆盖", "当前持仓覆盖", "回放覆盖",
    "主可比池", "市场地域", "主动被动", "特殊标签", "策略实现标签", "权益基金权重", "债券基金权重",
    "货币基金权重", "混合基金权重", "QDII权重", "指数基金权重", "主动基金权重", "基准权益权重",
    "基准债券权重", "基准货币权重", "基准可用状态", "基础数据等级", "费率状态", "年化投顾费率", "分类依据",
    "运作天数", "数据完整性", "质检情况", "稽核结论", "近一周", "近一月", "近三月", "近1年", "今年以来",
    "累计收益率", "自建累计收益", "与官方偏差", "最大回撤", "当前回撤", "年化收益", "波动率", "夏普比率",
    "单次平均换手率", "年化换手率", "调仓频率", "最近一年调仓次数", "官方对比口径", "可比记录数",
    "持仓来源", "持仓基金数", "权重", "上次调仓后权重", "权重变化", "调仓动作", "调仓基金数", "调后权重和", "区间", "年度",
    "策略收益", "基准收益", "模拟业绩", "基准业绩", "沪深300业绩", "基准公式解析", "调仓后收益率",
    "调仓后收益贡献", "调仓贡献曲线", "曲线数据提示"
    , "洞察评价对象", "全市场完整策略", "广发基金投顾完整策略", "市场覆盖率", "覆盖可比池数", "头部策略数", "中位差",
    "机会评分", "头部差距", "排名分位", "复盘建议", "数据洞察来源", "市场样本数", "广发样本数", "广发中位数",
    "市场中位数", "广发最佳", "广发中位回撤", "市场中位回撤", "所选收益", "池中位收益", "诊断分数",
    "基础数据A级占比", "仅曲线基准占比", "费率缺失占比", "风险未披露占比", "费率状态", "年化投顾费率",
    "覆盖策略类型数", "筛选口径", "分类口径", "广发覆盖率", "广发中位收益", "市场中位收益", "中位回撤",
    "中位波动", "高换手策略数", "高波动策略数", "风险收益象限", "广发基金投顾中位收益", "全市场中位收益",
    "广发基金投顾中位回撤", "广发基金投顾中位波动率", "最新调仓日期", "近一周主动调仓", "近一月主动调仓",
    "近一月广发调仓", "近一月中位换手率", "近一年平均调仓超额", "调仓逻辑", "事件数", "机构数",
    "可评价事件数", "市场调仓胜率", "广发调仓胜率", "标杆机构胜率", "胜率差距", "调仓质量结论", "调仓质量风险", "调仓质量建议",
    "中位单次换手率", "平均调仓超额", "示例原因", "平均单次换手率", "调仓胜率", "主要逻辑",
    "广发基金投顾完整策略", "广发覆盖率", "广发头部产品数", "广发Top3平均收益", "广发Top5平均收益",
    "市场Top3平均收益", "市场Top5平均收益", "广发Top3差距", "广发Top3对Top5差距", "头部达标数",
    "广发Top3平均排名", "广发Top3产品", "产品对比", "相对标杆", "深层结论", "业务风险", "业务动作",
    "广发样本数", "广发覆盖率", "广发中位收益", "广发Top3均值", "市场Top3均值", "标杆差距",
    "标杆产品", "标杆机构", "维度结论", "机会风险", "建议"
    , "经营重点", "经营判断", "业务重点", "重点经营", "头部可包装", "需要复盘", "产品补齐", "暂不主推", "重点名单"
    , "近一周经营总览", "事实", "观点", "逻辑", "动作", "近一周全市场中位收益", "近一周广发中位收益",
    "近一周广发相对差", "近一周上涨占比", "近一周广发Top5", "近一周市场Top5", "广发周度位置",
    "周度业务动作", "平台对标参考", "公开资料", "全市场事件数", "广发事件数", "近一周市场Top5均值"
  ]);
  const derivedFieldHints = ["覆盖", "完整", "归属", "分类", "权重", "收益", "回撤", "波动", "夏普", "换手", "频率", "次数", "偏差", "贡献", "解析", "可比", "中枢", "中位", "占比", "评分", "分位", "样本", "差距", "逻辑", "胜率", "Top", "标杆", "结论", "风险", "动作"];
  function isDerivedField(field) {
    const text = String(field || "");
    if (derivedFieldNames.has(text)) return true;
    if (collectedFieldNames.has(text)) return false;
    return derivedFieldHints.some((word) => text.includes(word));
  }
  function fieldSourceText(field) {
    return isDerivedField(field)
      ? "数据来源：字段名称带 *，表示基于基础数据加工、汇总、计算或规则归类得到。"
      : "数据来源：字段名称不带 *，表示采集数据或平台直接披露字段。";
  }
  function showInfoModal(title, body) {
    byId("fieldModalTitle").textContent = title;
    byId("fieldModalBody").textContent = body;
    byId("fieldModal").hidden = false;
  }
  function showHtmlModal(title, html) {
    byId("fieldModalTitle").textContent = title;
    byId("fieldModalBody").innerHTML = html;
    byId("fieldModal").hidden = false;
  }
  function fmt(value, suffix = "") {
    if (value === null || value === undefined || value === "") return "未披露";
    if (typeof value === "number") return Number.isInteger(value) ? value.toLocaleString("zh-CN") + suffix : value.toLocaleString("zh-CN", { maximumFractionDigits: 4 }) + suffix;
    return esc(value);
  }
  function pct(value) {
    if (value === null || value === undefined || Number.isNaN(Number(value))) return "未披露";
    return `${Number(value).toFixed(2)}%`;
  }
  function pctSigned(value) {
    if (value === null || value === undefined || Number.isNaN(Number(value))) return '<span class="small">未披露</span>';
    const number = Number(value);
    const cls = number > 0 ? "ret-pos" : number < 0 ? "ret-neg" : "ret-zero";
    return `<span class="${cls}">${number.toFixed(2)}%</span>`;
  }
  const returnFieldNames = new Set(["官方累计收益", "自建累计收益", "与官方偏差", "最大回撤", "当前回撤", "年化收益", "波动率", "权重变化", "调仓后收益率", "调仓后收益贡献", "近一周", "近一月", "近三月", "近1年", "今年以来", "累计收益率", "策略收益", "基准收益", "基准业绩", "调仓超额"]);
  const returnFieldHints = ["收益", "回撤", "波动", "偏差", "贡献", "涨幅", "超额"];
  function isReturnField(field) {
    return returnFieldNames.has(field) || returnFieldHints.some((word) => String(field).includes(word));
  }
  function toneClass(field, value) {
    if (!isReturnField(field) || Number.isNaN(Number(value))) return "";
    if (String(field).includes("回撤")) return Number(value) === 0 ? "is-zero" : "is-neg";
    const number = Number(value);
    if (number > 0) return "is-pos";
    if (number < 0) return "is-neg";
    return "is-zero";
  }
  function valueHtml(field, value) {
    if (value === null || value === undefined || value === "") return '<span class="value-muted">未披露</span>';
    if (!Number.isNaN(Number(value)) && String(field).includes("回撤")) {
      const number = Number(value);
      const cls = number === 0 ? "ret-zero" : "ret-neg";
      return `<span class="${cls}">${number.toFixed(2)}%</span>`;
    }
    if (!Number.isNaN(Number(value)) && (String(field).includes("权重") || String(field).includes("费率"))) return `<span class="value-em">${Number(value).toFixed(2)}%</span>`;
    if (!Number.isNaN(Number(value)) && isReturnField(field)) return pctSigned(value);
    if (field.includes("日期") || field.endsWith("日")) return `<span class="value-date">${fmt(value)}</span>`;
    if (field.includes("ID") || field.includes("代码")) return `<span class="value-code">${fmt(value)}</span>`;
    if (typeof value === "number") return `<span class="value-em">${fmt(value)}</span>`;
    return fmt(value);
  }
  function statusBadge(value) {
    const text = String(value || "不完整");
    const cls = text === "完整" ? "ok" : "bad";
    return `<span class="status-badge ${cls}">${esc(text)}</span>`;
  }
  function label(name) {
    const safe = esc(name);
    const mark = isDerivedField(name) ? '<sup class="derived-star" title="基于基础数据加工">*</sup>' : "";
    return `<span class="field-label">${safe}${mark}<button class="info-button" type="button" data-field="${safe}" title="查看字段口径">?</button></span>`;
  }
  function table(headers, rows, formatter) {
    const head = headers.map((item) => `<th>${label(item)}</th>`).join("");
    const body = rows.length ? rows.map((row) => `<tr>${headers.map((h) => `<td>${formatter ? formatter(row, h) : fmt(row[h])}</td>`).join("")}</tr>`).join("") : `<tr><td colspan="${headers.length}"><div class="empty">暂无数据</div></td></tr>`;
    return `<div class="table-wrap"><table><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table></div>`;
  }
  function valueList(rows) {
    return `<div class="value-list">${rows.map((row) => {
      const cls = ["value-row", toneClass(row.字段, row.值), row.字段 === "业绩基准说明" ? "benchmark-row" : ""].filter(Boolean).join(" ");
      return `<div class="${cls}"><strong>${label(row.字段)}</strong><span>${valueHtml(row.字段, row.值)}</span></div>`;
    }).join("")}</div>`;
  }
  function metricValue(labelName, value, formatter) {
    const html = formatter && !String(labelName).includes("回撤") ? formatter(value) : valueHtml(labelName, value);
    return `<div class="core-cell ${toneClass(labelName, value)}"><span>${label(labelName)}</span><strong>${html}</strong></div>`;
  }
  function metric(labelName, value, sub = "") {
    return `<section class="metric"><div>${label(labelName)}</div><div class="metric-value">${fmt(value)}</div>${sub ? `<div class="metric-sub">${esc(sub)}</div>` : ""}</section>`;
  }
  function params() {
    return new URLSearchParams(window.location.search);
  }
  function loadScript(src) {
    return new Promise((resolve, reject) => {
      const script = document.createElement("script");
      script.src = src;
      script.onload = resolve;
      script.onerror = () => reject(new Error(`加载失败：${src}`));
      document.head.appendChild(script);
    });
  }
  const chartColors = {
    "披露业绩": "#d32f2f",
    "模拟业绩": "#1565c0",
    "基准业绩": "#2e7d32",
    "沪深300业绩": "#6a1b9a",
    "调仓前仓位模拟": "#f57c00",
    "调仓后仓位实际": "#00897b"
  };
  function colorForSeries(name) {
    if (chartColors[name]) return chartColors[name];
    if (String(name).startsWith("全局基准")) return "#0f766e";
    return "#475467";
  }
  function returnFromBase(value, base, mode) {
    if (value === null || value === undefined || base === null || base === undefined) return null;
    if (mode === "return" || mode === "return_pct") {
      const denominator = 1 + Number(base) / 100;
      if (!denominator) return null;
      return ((1 + Number(value) / 100) / denominator - 1) * 100;
    }
    if (!Number(base)) return null;
    return (Number(value) / Number(base) - 1) * 100;
  }
  function rangeStartDate(points, range) {
    if (!points.length || range === "all") return null;
    const last = new Date(points[points.length - 1].日期);
    if (range === "ytd") return new Date(last.getFullYear(), 0, 1);
    const days = { "1y": 365, "6m": 183, "3m": 92, "1m": 31 }[range] || 0;
    return new Date(last.getTime() - days * 86400000);
  }
  function pointAtOrBefore(points, dateText) {
    let selected = null;
    for (const point of points) {
      if (String(point.日期) <= dateText) selected = point;
      else break;
    }
    return selected;
  }
  function transformSeries(seriesMap, range = "all", alreadyReturn = false, visibility = {}) {
    const raw = Object.entries(seriesMap || {}).filter(([name]) => visibility[name] !== false).map(([name, payload]) => {
      const points = Array.isArray(payload) ? payload : (payload?.points || []);
      const mode = payload?.模式 || points[0]?.模式 || "nav";
      const start = alreadyReturn ? null : rangeStartDate(points, range);
      const filtered = points
        .filter((p) => p.日期 && p.数值 !== null && p.数值 !== undefined && (!start || new Date(p.日期) >= start))
        .map((p) => ({ 日期: String(p.日期), 数值: Number(p.数值) }))
        .filter((p) => Number.isFinite(p.数值))
        .sort((a, b) => a.日期.localeCompare(b.日期));
      return { name, points: filtered, mode: alreadyReturn ? "return" : mode };
    }).filter((item) => item.points.length);
    if (!raw.length) return {};
    const commonStart = raw.map((item) => item.points[0].日期).sort().at(-1);
    const commonEnd = raw.map((item) => item.points[item.points.length - 1].日期).sort()[0];
    if (!commonStart || !commonEnd || commonStart > commonEnd) return {};
    const dateSet = new Set([commonStart, commonEnd]);
    raw.forEach((item) => item.points.forEach((point) => {
      if (point.日期 >= commonStart && point.日期 <= commonEnd) dateSet.add(point.日期);
    }));
    const dates = [...dateSet].sort();
    const entries = raw.map((item) => {
      const basePoint = pointAtOrBefore(item.points, commonStart) || item.points.find((point) => point.日期 >= commonStart);
      if (!basePoint) return [item.name, []];
      const rows = dates.map((date) => {
        const point = pointAtOrBefore(item.points, date) || item.points.find((candidate) => candidate.日期 >= date);
        if (!point) return null;
        const value = returnFromBase(point.数值, basePoint.数值, item.mode);
        return Number.isFinite(value) ? { 日期: date, 数值: value } : null;
      }).filter(Boolean);
      return [item.name, rows];
    });
    return Object.fromEntries(entries.filter(([, rows]) => rows.length));
  }
  function chartLegendHtml(names, visibility) {
    if (!names.length) return "";
    return `<div class="legend">${names.map((name) => {
      const checked = visibility[name] !== false ? "checked" : "";
      return `<label class="legend-item"><input class="legend-toggle" type="checkbox" data-series-name="${esc(name)}" ${checked}><i style="background:${colorForSeries(name)}"></i><span>${esc(name)}</span></label>`;
    }).join("")}</div>`;
  }
  function drawReturnChart(el, seriesMap, options = {}) {
    const range = options.range || "all";
    const names = Object.keys(seriesMap || {});
    const defaultVisibleSeries = Array.isArray(options.defaultVisibleSeries) ? new Set(options.defaultVisibleSeries) : null;
    const visibilityKey = `${names.join("|")}::${defaultVisibleSeries ? [...defaultVisibleSeries].join("|") : "all"}`;
    if (!el.__seriesVisibility || el.__seriesVisibilityKey !== visibilityKey) {
      el.__seriesVisibility = {};
      names.forEach((name) => { el.__seriesVisibility[name] = defaultVisibleSeries ? defaultVisibleSeries.has(name) : true; });
      el.__seriesVisibilityKey = visibilityKey;
    } else {
      names.forEach((name) => { if (!(name in el.__seriesVisibility)) el.__seriesVisibility[name] = defaultVisibleSeries ? defaultVisibleSeries.has(name) : true; });
    }
    Object.keys(el.__seriesVisibility).forEach((name) => { if (!names.includes(name)) delete el.__seriesVisibility[name]; });
    const transformed = transformSeries(seriesMap, range, !!options.alreadyReturn, el.__seriesVisibility);
    const series = Object.entries(transformed);
    if (!series.length) {
      el.innerHTML = `${chartLegendHtml(names, el.__seriesVisibility)}<div class="empty">暂无可绘制曲线</div>`;
      el.querySelectorAll(".legend-toggle").forEach((input) => {
        input.addEventListener("change", () => {
          el.__seriesVisibility[input.dataset.seriesName] = input.checked;
          drawReturnChart(el, seriesMap, options);
        });
      });
      return;
    }
    const width = Math.max(920, Math.round(el.getBoundingClientRect().width || el.clientWidth || 960));
    const height = options.height || 310;
    const pad = { left: 44, right: 8, top: 22, bottom: 46 };
    const allValues = series.flatMap(([, rows]) => rows.map((row) => row.数值));
    const allDates = [...new Set(series.flatMap(([, rows]) => rows.map((row) => row.日期)))].sort();
    const minDate = new Date(allDates[0]).getTime();
    const maxDate = new Date(allDates[allDates.length - 1]).getTime();
    let min = Math.min(...allValues), max = Math.max(...allValues);
    if (min === max) { min -= 1; max += 1; }
    const yPad = Math.max(0.3, (max - min) * 0.12);
    min -= yPad; max += yPad;
    const xOf = (dateText) => {
      const t = new Date(dateText).getTime();
      return pad.left + (maxDate === minDate ? 0 : (t - minDate) / (maxDate - minDate)) * (width - pad.left - pad.right);
    };
    const yOf = (value) => height - pad.bottom - ((value - min) / (max - min)) * (height - pad.top - pad.bottom);
    const tickCount = Math.min(6, allDates.length);
    const tickDates = tickCount <= 1 ? allDates : Array.from({ length: tickCount }, (_, index) => allDates[Math.round(index * (allDates.length - 1) / (tickCount - 1))]).filter((date, index, arr) => arr.indexOf(date) === index);
    const multiYearAxis = new Date(allDates[0]).getFullYear() !== new Date(allDates[allDates.length - 1]).getFullYear();
    const tickLabel = (date) => multiYearAxis ? date.slice(0, 7) : date.slice(5);
    const xTicks = tickDates.map((date) => {
      const x = xOf(date);
      return `<line class="tick-line" x1="${x}" y1="${pad.top}" x2="${x}" y2="${height - pad.bottom}"/><text class="axis-text" x="${x}" y="${height - 15}" text-anchor="middle">${esc(tickLabel(date))}</text>`;
    }).join("");
    const grid = [0, .25, .5, .75, 1].map((ratio) => {
      const y = pad.top + ratio * (height - pad.top - pad.bottom);
      const value = max - ratio * (max - min);
      return `<line x1="${pad.left}" y1="${y}" x2="${width - pad.right}" y2="${y}" stroke="#edf1f5"/><text class="axis-text" x="8" y="${y + 4}">${value.toFixed(1)}%</text>`;
    }).join("");
    const zero = min < 0 && max > 0 ? `<line x1="${pad.left}" y1="${yOf(0)}" x2="${width - pad.right}" y2="${yOf(0)}" stroke="#cbd5e1" stroke-dasharray="4 4"/>` : "";
    const paths = series.map(([name, rows]) => {
      const color = colorForSeries(name);
      const d = rows.map((row, i) => `${i ? "L" : "M"}${xOf(row.日期).toFixed(1)},${yOf(row.数值).toFixed(1)}`).join(" ");
      return `<path d="${d}" fill="none" stroke="${color}" stroke-width="2.4" stroke-linejoin="round" stroke-linecap="round"/>`;
    }).join("");
    const legend = chartLegendHtml(names, el.__seriesVisibility);
    el.innerHTML = `${legend}<svg viewBox="0 0 ${width} ${height}" role="img" aria-label="${esc(options.title || "收益曲线")}">${xTicks}${grid}${zero}<line x1="${pad.left}" y1="${height-pad.bottom}" x2="${width-pad.right}" y2="${height-pad.bottom}" stroke="#d0d7de"/>${paths}<g class="hover-layer" visibility="hidden"><line class="hover-line" x1="0" y1="${pad.top}" x2="0" y2="${height - pad.bottom}"/><g class="hover-points"></g></g></svg><div class="chart-tooltip" hidden></div>`;
    el.querySelectorAll(".legend-toggle").forEach((input) => {
      input.addEventListener("change", () => {
        el.__seriesVisibility[input.dataset.seriesName] = input.checked;
        drawReturnChart(el, seriesMap, options);
      });
    });
    const svg = el.querySelector("svg");
    const tip = el.querySelector(".chart-tooltip");
    const hoverLayer = svg.querySelector(".hover-layer");
    const hoverLine = svg.querySelector(".hover-line");
    const hoverPoints = svg.querySelector(".hover-points");
    svg.addEventListener("mousemove", (event) => {
      const rect = svg.getBoundingClientRect();
      const viewX = (event.clientX - rect.left) / rect.width * width;
      const nearestDate = allDates.reduce((best, date) => Math.abs(xOf(date) - viewX) < Math.abs(xOf(best) - viewX) ? date : best, allDates[0]);
      const guideX = xOf(nearestDate);
      const rows = series.map(([name, values]) => {
        let nearest = values.find((row) => row.日期 === nearestDate) || pointAtOrBefore(values, nearestDate) || values[0];
        return { name, value: nearest?.数值, color: colorForSeries(name) };
      }).filter((row) => Number.isFinite(row.value));
      hoverLayer.setAttribute("visibility", "visible");
      hoverLine.setAttribute("x1", guideX.toFixed(1));
      hoverLine.setAttribute("x2", guideX.toFixed(1));
      hoverPoints.innerHTML = rows.map((row) => `<circle cx="${guideX.toFixed(1)}" cy="${yOf(row.value).toFixed(1)}" r="4" fill="#fff" stroke="${row.color}" stroke-width="2"/>`).join("");
      tip.innerHTML = `<strong>${esc(nearestDate)}</strong>${rows.map((row) => `<div class="chart-tip-row"><span><i class="chart-dot" style="background:${row.color}"></i>${esc(row.name)}</span><b class="${row.value >= 0 ? "ret-pos" : "ret-neg"}">${row.value.toFixed(2)}%</b></div>`).join("")}`;
      tip.hidden = false;
      const hostRect = el.getBoundingClientRect();
      const localX = event.clientX - hostRect.left;
      const localY = event.clientY - hostRect.top;
      tip.style.left = `${Math.min(localX + 16, el.clientWidth - 250)}px`;
      tip.style.top = `${Math.max(10, localY - 20)}px`;
    });
    svg.addEventListener("mouseleave", () => { tip.hidden = true; hoverLayer.setAttribute("visibility", "hidden"); });
  }
  document.addEventListener("click", (event) => {
    const button = event.target.closest("[data-field]");
    if (!button) return;
    const field = button.getAttribute("data-field");
    showInfoModal(field, `${fieldSourceText(field)}\n\n${dict()[field] || "该字段来自当前分析库，页面仅做业务化展示。"}`);
  });
  document.addEventListener("click", (event) => {
    if (event.target.id === "fieldModal" || event.target.id === "fieldModalClose") {
      byId("fieldModal").hidden = true;
    }
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && byId("fieldModal")) byId("fieldModal").hidden = true;
  });
  return { state, byId, esc, fmt, pct, pctSigned, valueHtml, toneClass, statusBadge, label, table, valueList, metricValue, metric, params, loadScript, drawReturnChart, isDerivedField, fieldSourceText, showInfoModal, showHtmlModal };
})();
