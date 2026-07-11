(() => {
  const B = window.BasicData;
  const pack = window.__ADVISOR_FOF_RANKING_PACK__;
  const root = B.byId("advisorFofRankingPage");
  if (!root) return;

  const state = {
    interval: "上半年",
    entity: "all",
    category: "all",
    customer: "all",
    gfOnly: false,
    search: "",
    pageSize: 80,
    scatterXMetric: "",
    scatterYMetric: "",
    selectedPointId: "",
  };
  let searchTimer = null;

  if (!pack || !Array.isArray(pack.rows)) {
    root.innerHTML = '<section class="panel"><div class="empty">未找到投顾-FOF排名数据包，请先运行页面数据包重建。</div></section>';
    return;
  }

  const rows = pack.rows || [];
  const intervals = (pack.meta && pack.meta.intervals) || [
    { label: "上半年", description: "上半年收益" },
    { label: "今年以来", description: "年初以来收益" },
    { label: "近1月", description: "近一个月收益" },
    { label: "近3月", description: "近三个月收益" },
    { label: "近6月", description: "近六个月收益" },
    { label: "近1年", description: "近一年收益" },
  ];

  function optionHtml(values, current, allLabel) {
    const head = `<option value="all"${current === "all" ? " selected" : ""}>${B.esc(allLabel)}</option>`;
    return head + values.map((value) => `<option value="${B.esc(value)}"${value === current ? " selected" : ""}>${B.esc(value)}</option>`).join("");
  }

  function numericOrNull(value) {
    if (value === null || value === undefined || value === "") return null;
    const number = Number(value);
    return Number.isFinite(number) ? number : null;
  }

  function numberValue(row, interval) {
    const value = numericOrNull(row?.returns?.[interval]);
    return Number.isFinite(value) ? value : null;
  }

  function hasReturn(row) {
    return numberValue(row, state.interval) !== null;
  }

  function riskValue(row, interval, field) {
    const value = numericOrNull(row?.riskMetrics?.[interval]?.[field]);
    return Number.isFinite(value) ? value : null;
  }

  function riskProfileValue(row, field) {
    const value = numericOrNull(row?.riskProfile?.[field]);
    return Number.isFinite(value) ? value : null;
  }

  function validReturnCount(row) {
    return intervals.reduce((count, item) => count + (numberValue(row, item.label) !== null ? 1 : 0), 0);
  }

  function matchesSearch(row) {
    const needle = state.search.trim().toLowerCase();
    if (!needle) return true;
    const haystack = [
      row.name,
      row.code,
      row.id,
      row.institution,
      row.channel,
      row.manager,
      row.rankingCategory,
      row.benchmarkEquityBucket,
      row.fofPublicCategory,
      row.fofBenchmarkCategory,
    ].join(" ").toLowerCase();
    return haystack.includes(needle);
  }

  function customerMatch(row) {
    if (state.customer === "all" || row.entityType !== "投顾策略") return true;
    if (state.customer === "yes") return row.isCustomer === "是";
    if (state.customer === "no") return row.isCustomer !== "是";
    return true;
  }

  function filterRows() {
    return rows.filter((row) => {
      if (state.entity !== "all" && row.entityType !== state.entity) return false;
      if (state.category !== "all" && row.rankingCategory !== state.category) return false;
      if (state.gfOnly && !row.isGuangfa) return false;
      if (!customerMatch(row)) return false;
      return matchesSearch(row);
    });
  }

  function rankedRows() {
    const filtered = filterRows();
    const valid = filtered.filter(hasReturn).sort((a, b) => {
      const diff = numberValue(b, state.interval) - numberValue(a, state.interval);
      return diff || String(a.name || "").localeCompare(String(b.name || ""), "zh-CN");
    });
    const invalid = filtered.filter((row) => !hasReturn(row)).sort((a, b) => String(a.name || "").localeCompare(String(b.name || ""), "zh-CN"));
    valid.forEach((row, index) => {
      row.__rank = index + 1;
    });
    invalid.forEach((row) => {
      row.__rank = "";
    });
    return [...valid, ...invalid];
  }

  function entityButton(value, label) {
    return `<button type="button" class="${state.entity === value ? "is-active" : ""}" data-entity="${B.esc(value)}">${B.esc(label)}</button>`;
  }

  function pct(value) {
    return value === null || value === undefined ? '<span class="small">未披露</span>' : B.pctSigned(value);
  }

  function fmtMetric(value, metric) {
    if (value === null || value === undefined) return "未披露";
    if (metric && metric.format === "text") return B.esc(value || "未披露");
    if (metric && metric.format === "count") return Number(value).toLocaleString("zh-CN");
    return B.pctSigned(value);
  }

  function fmtMetricText(value, metric) {
    if (value === null || value === undefined || value === "") return "未披露";
    if (metric && metric.format === "text") return String(value);
    if (metric && metric.format === "count") return Number(value).toLocaleString("zh-CN");
    const number = Number(value);
    if (!Number.isFinite(number)) return String(value);
    return `${number > 0 ? "+" : ""}${number.toFixed(2)}%`;
  }

  function valueClass(value) {
    if (value === null || value === undefined) return "";
    const number = Number(value);
    if (!Number.isFinite(number)) return "";
    if (number > 0) return "is-pos";
    if (number < 0) return "is-neg";
    return "is-zero";
  }

  function tag(text, cls = "") {
    return text ? `<span class="rank-tag ${cls}">${B.esc(text)}</span>` : "";
  }

  function metricBlock(label, value, sub = "") {
    return `<section class="metric advisor-rank-metric"><div>${B.esc(label)}</div><div class="metric-value">${B.esc(value)}</div>${sub ? `<div class="metric-sub">${B.esc(sub)}</div>` : ""}</section>`;
  }

  function renderMetrics(filtered, valid) {
    const gfCount = filtered.filter((row) => row.isGuangfa).length;
    const strategyCount = filtered.filter((row) => row.entityType === "投顾策略").length;
    const fofCount = filtered.filter((row) => row.entityType === "FOF基金").length;
    const customerCount = filtered.filter((row) => row.entityType === "投顾策略" && row.isCustomer === "是").length;
    return `<section class="grid advisor-rank-grid">
      ${metricBlock("当前样本", filtered.length.toLocaleString("zh-CN"), `${strategyCount} 条投顾策略 / ${fofCount} 只FOF`)}
      ${metricBlock("可排名样本", valid.length.toLocaleString("zh-CN"), `${state.interval} 有收益数据`)}
      ${metricBlock("广发产品", gfCount.toLocaleString("zh-CN"), "表格和图中已高亮")}
      ${metricBlock("对客策略", customerCount.toLocaleString("zh-CN"), "仅投顾策略适用")}
    </section>`;
  }

  function scatterMetrics() {
    const metrics = [
      {
        key: "risk:benchmarkEquity",
        label: "基准权益权重",
        group: "风险",
        format: "pct",
        value: (row) => riskProfileValue(row, "benchmarkEquityWeight"),
      },
      {
        key: "risk:benchmarkBond",
        label: "基准债券权重",
        group: "风险",
        format: "pct",
        value: (row) => riskProfileValue(row, "benchmarkBondWeight"),
      },
      {
        key: "risk:benchmarkOverseas",
        label: "基准海外权重",
        group: "风险",
        format: "pct",
        value: (row) => riskProfileValue(row, "benchmarkOverseasWeight"),
      },
      {
        key: "risk:benchmarkCommodity",
        label: "基准商品权重",
        group: "风险",
        format: "pct",
        value: (row) => riskProfileValue(row, "benchmarkCommodityWeight"),
      },
    ];
    intervals.forEach((item) => {
      metrics.push({
        key: `return:${item.label}`,
        label: `${item.label}收益`,
        group: "收益",
        format: "pct",
        value: (row) => numberValue(row, item.label),
      });
      metrics.push({
        key: `drawdown:${item.label}`,
        label: `${item.label}最大回撤`,
        group: "风险",
        format: "pct",
        value: (row) => riskValue(row, item.label, "maxDrawdown"),
      });
      metrics.push({
        key: `volatility:${item.label}`,
        label: `${item.label}年化波动率`,
        group: "风险",
        format: "pct",
        value: (row) => riskValue(row, item.label, "volatility"),
      });
    });
    metrics.push({
      key: "coverage:return",
      label: "收益区间覆盖数",
      group: "数据",
      format: "count",
      value: validReturnCount,
    });
    return metrics;
  }

  function metricMap() {
    return new Map(scatterMetrics().map((metric) => [metric.key, metric]));
  }

  function defaultXMetric() {
    return `drawdown:${state.interval}`;
  }

  function defaultYMetric() {
    return `return:${state.interval}`;
  }

  function resolveMetric(key, fallback) {
    const map = metricMap();
    return map.get(key) || map.get(fallback) || map.values().next().value;
  }

  function metricSelect(id, label, currentKey) {
    const metrics = scatterMetrics();
    const groups = [];
    metrics.forEach((metric) => {
      let group = groups.find((item) => item.name === metric.group);
      if (!group) {
        group = { name: metric.group, items: [] };
        groups.push(group);
      }
      group.items.push(metric);
    });
    return `<label class="advisor-scatter-control"><span>${B.esc(label)}</span><select id="${B.esc(id)}">
      ${groups.map((group) => `<optgroup label="${B.esc(group.name)}">${group.items.map((metric) => `<option value="${B.esc(metric.key)}"${metric.key === currentKey ? " selected" : ""}>${B.esc(metric.label)}</option>`).join("")}</optgroup>`).join("")}
    </select></label>`;
  }

  function scatterDomain(values) {
    let min = Math.min(...values);
    let max = Math.max(...values);
    if (!Number.isFinite(min) || !Number.isFinite(max)) return [0, 1];
    if (min === max) {
      const pad = Math.max(Math.abs(min) * 0.2, 1);
      return [min - pad, max + pad];
    }
    const pad = (max - min) * 0.08;
    return [min - pad, max + pad];
  }

  function scatterTicks(min, max, count = 5) {
    if (!Number.isFinite(min) || !Number.isFinite(max) || count <= 1) return [];
    return Array.from({ length: count }, (_, index) => min + (max - min) * index / (count - 1));
  }

  function scatterPointId(row) {
    return `${row.entityType || ""}::${row.id || row.code || row.name || ""}`;
  }

  function renderScatterDetail(row, xMetric, yMetric) {
    if (!row) {
      return `<div class="advisor-scatter-detail is-empty">
        <strong>点阵选中产品</strong>
        <p>点击图中的点查看产品名称、机构、分类、收益风险指标和详情页入口。</p>
      </div>`;
    }
    const xValue = xMetric.value(row);
    const yValue = yMetric.value(row);
    const profileItems = [
      { key: "bucket:benchmarkEquity", label: "基准权益分档", value: row.benchmarkEquityBucket || row?.riskProfile?.benchmarkEquityBucket, format: "text" },
      { key: "risk:benchmarkEquity", label: "基准权益权重", value: riskProfileValue(row, "benchmarkEquityWeight") },
      { key: "risk:benchmarkBond", label: "基准债券权重", value: riskProfileValue(row, "benchmarkBondWeight") },
    ].filter((item) => item.key !== xMetric.key && item.key !== yMetric.key);
    return `<div class="advisor-scatter-detail">
      <div class="advisor-scatter-detail-head">
        <div>
          <strong>${detailLink(row)}</strong>
          <div class="small">${B.esc(row.code || row.id || "")} · ${B.esc(row.institution || row.channel || "未披露机构")}</div>
        </div>
        <div>${tag(row.entityType, row.entityType === "投顾策略" ? "is-strategy" : "is-fof")} ${row.isGuangfa ? tag("广发", "is-gf") : ""}</div>
      </div>
      <div class="advisor-scatter-detail-grid">
        <div><span>${B.esc(yMetric.label)}</span><b class="${valueClass(yValue)}">${fmtMetric(yValue, yMetric)}</b></div>
        <div><span>${B.esc(xMetric.label)}</span><b class="${valueClass(xValue)}">${fmtMetric(xValue, xMetric)}</b></div>
        ${profileItems.map((item) => `<div><span>${B.esc(item.label)}</span><b>${fmtMetric(item.value)}</b></div>`).join("")}
        <div><span>基准权益分档</span><b>${B.esc(row.benchmarkEquityBucket || row.rankingCategory || "未分档")}</b></div>
        <div><span>基准细分</span><b>${B.esc(row.fofBenchmarkCategory || "未披露")}</b></div>
      </div>
      <p class="desc">业绩基准：${B.esc(row.benchmark || "未披露")}</p>
    </div>`;
  }

  function renderScatter(rankedRows) {
    const xMetric = resolveMetric(state.scatterXMetric, defaultXMetric());
    const yMetric = resolveMetric(state.scatterYMetric, defaultYMetric());
    state.scatterXMetric = xMetric.key;
    state.scatterYMetric = yMetric.key;
    const points = rankedRows.map((row) => ({
      row,
      id: scatterPointId(row),
      x: xMetric.value(row),
      y: yMetric.value(row),
    })).filter((item) => item.x !== null && item.y !== null);
    if (!points.length) {
      return `<div class="advisor-scatter-card">
        <div class="advisor-scatter-controls">${metricSelect("rankScatterX", "X轴", xMetric.key)}${metricSelect("rankScatterY", "Y轴", yMetric.key)}</div>
        <div class="empty">当前筛选下缺少可绘制的收益/风险指标。</div>
      </div>`;
    }
    const width = 940;
    const height = 500;
    const pad = { left: 76, right: 30, top: 34, bottom: 58 };
    const plotW = width - pad.left - pad.right;
    const plotH = height - pad.top - pad.bottom;
    const [xMin, xMax] = scatterDomain(points.map((item) => item.x));
    const [yMin, yMax] = scatterDomain(points.map((item) => item.y));
    const xScale = (value) => pad.left + (value - xMin) / (xMax - xMin || 1) * plotW;
    const yScale = (value) => pad.top + plotH - (value - yMin) / (yMax - yMin || 1) * plotH;
    const selected = points.find((item) => item.id === state.selectedPointId);
    const selectedRow = selected ? selected.row : null;
    const gfCount = points.filter((item) => item.row.isGuangfa).length;
    const strategyCount = points.filter((item) => item.row.entityType === "投顾策略").length;
    const fofCount = points.filter((item) => item.row.entityType === "FOF基金").length;
    return `<div class="advisor-scatter-card">
      <div class="advisor-scatter-head">
        <div class="advisor-scatter-controls">
          ${metricSelect("rankScatterX", "X轴", xMetric.key)}
          ${metricSelect("rankScatterY", "Y轴", yMetric.key)}
        </div>
        <div class="advisor-scatter-legend">
          <span><i class="legend-dot is-strategy"></i>投顾策略 ${strategyCount.toLocaleString("zh-CN")}</span>
          <span><i class="legend-dot is-fof"></i>FOF ${fofCount.toLocaleString("zh-CN")}</span>
          <span><i class="legend-dot is-gf"></i>广发 ${gfCount.toLocaleString("zh-CN")}</span>
        </div>
      </div>
      <div class="advisor-scatter-layout">
        <div class="advisor-scatter-wrap">
          <svg class="advisor-scatter-svg" viewBox="0 0 ${width} ${height}" role="img" aria-label="投顾策略与FOF收益风险点阵">
            <rect x="${pad.left}" y="${pad.top}" width="${plotW}" height="${plotH}" class="advisor-scatter-bg"></rect>
            ${scatterTicks(xMin, xMax).map((tick) => {
              const x = xScale(tick);
              return `<line x1="${x}" y1="${pad.top}" x2="${x}" y2="${pad.top + plotH}" class="advisor-scatter-grid"></line><text x="${x}" y="${height - 31}" text-anchor="middle" class="advisor-scatter-axis">${B.esc(fmtMetricText(tick, xMetric))}</text>`;
            }).join("")}
            ${scatterTicks(yMin, yMax).map((tick) => {
              const y = yScale(tick);
              return `<line x1="${pad.left}" y1="${y}" x2="${pad.left + plotW}" y2="${y}" class="advisor-scatter-grid"></line><text x="${pad.left - 10}" y="${y + 4}" text-anchor="end" class="advisor-scatter-axis">${B.esc(fmtMetricText(tick, yMetric))}</text>`;
            }).join("")}
            <line x1="${pad.left}" y1="${pad.top + plotH}" x2="${pad.left + plotW}" y2="${pad.top + plotH}" class="advisor-scatter-axis-line"></line>
            <line x1="${pad.left}" y1="${pad.top}" x2="${pad.left}" y2="${pad.top + plotH}" class="advisor-scatter-axis-line"></line>
            ${points.map((item) => {
              const row = item.row;
              const typeClass = row.entityType === "投顾策略" ? "is-strategy" : "is-fof";
              const gfClass = row.isGuangfa ? " is-gf" : "";
              const selectedClass = item.id === state.selectedPointId ? " is-selected" : "";
              const radius = row.isGuangfa ? 6.2 : 4.8;
              const label = `${row.name}，${xMetric.label}${fmtMetricText(item.x, xMetric)}，${yMetric.label}${fmtMetricText(item.y, yMetric)}`;
              return `<circle cx="${xScale(item.x).toFixed(2)}" cy="${yScale(item.y).toFixed(2)}" r="${radius}" class="advisor-scatter-dot ${typeClass}${gfClass}${selectedClass}" data-scatter-id="${B.esc(item.id)}" tabindex="0" role="button" aria-label="${B.esc(label)}"><title>${B.esc(label)}</title></circle>`;
            }).join("")}
            <text x="${pad.left + plotW / 2}" y="${height - 9}" text-anchor="middle" class="advisor-scatter-label">${B.esc(xMetric.label)}</text>
            <text transform="translate(18 ${pad.top + plotH / 2}) rotate(-90)" text-anchor="middle" class="advisor-scatter-label">${B.esc(yMetric.label)}</text>
          </svg>
        </div>
        ${renderScatterDetail(selectedRow, xMetric, yMetric)}
      </div>
    </div>`;
  }

  function detailLink(row) {
    const href = row.detailUrl || "";
    const name = B.esc(row.name || row.id || "");
    return href ? `<a class="link" href="${B.esc(href)}">${name}</a>` : name;
  }

  function tableRows(displayRows) {
    if (!displayRows.length) {
      return '<tr><td colspan="13"><div class="empty">当前筛选下暂无产品。</div></td></tr>';
    }
    return displayRows.map((row) => {
      const selectedValue = numberValue(row, state.interval);
      return `<tr class="${row.isGuangfa ? "is-gf-row" : ""}">
        <td class="rank-no">${row.__rank || "-"}</td>
        <td>${tag(row.entityType, row.entityType === "投顾策略" ? "is-strategy" : "is-fof")} ${row.isGuangfa ? tag("广发", "is-gf") : ""}</td>
        <td class="rank-name-cell">${detailLink(row)}<div class="small">${B.esc(row.code || row.id || "")}</div></td>
        <td>${B.esc(row.institution || "未披露")}<div class="small">${B.esc(row.manager || row.channel || "")}</div></td>
        <td>${B.esc(row.benchmarkEquityBucket || row.rankingCategory || "未分档")}<div class="small">${B.esc(row.rankingCategoryBasis || "")}</div></td>
        <td>${B.esc(row.fofPublicCategory || "未披露")}</td>
        <td>${B.esc(row.fofBenchmarkCategory || "未披露")}<div class="small">${B.esc(row.parseConfidence || "")}</div></td>
        <td class="${valueClass(selectedValue)}">${pct(selectedValue)}</td>
        <td>${pct(numberValue(row, "上半年"))}</td>
        <td>${pct(numberValue(row, "近1月"))}</td>
        <td>${pct(numberValue(row, "近3月"))}</td>
        <td>${B.esc(row.isCustomer || "未披露")}</td>
        <td>${B.esc(row.dataStatus || "")}</td>
      </tr>`;
    }).join("");
  }

  function renderTable(ranked) {
    const displayRows = ranked.slice(0, state.pageSize);
    return `<div class="table-wrap advisor-rank-table-wrap">
      <table class="advisor-rank-table">
        <thead>
          <tr>
            <th>排名</th>
            <th>产品类型</th>
            <th>产品名称</th>
            <th>机构/经理</th>
            <th>基准权益分档</th>
            <th>公开分类</th>
            <th>基准细分</th>
            <th>${B.esc(state.interval)}</th>
            <th>上半年</th>
            <th>近1月</th>
            <th>近3月</th>
            <th>是否对客</th>
            <th>数据状态</th>
          </tr>
        </thead>
        <tbody>${tableRows(displayRows)}</tbody>
      </table>
    </div>
    <p class="desc">当前显示前 ${displayRows.length.toLocaleString("zh-CN")} 条；排名只在当前筛选后的可排名样本内重排，缺少所选区间收益的产品排在底部且不赋排名。</p>`;
  }

  function renderControls() {
    const intervalOptions = intervals.map((item) => `<option value="${B.esc(item.label)}"${item.label === state.interval ? " selected" : ""}>${B.esc(item.label)}</option>`).join("");
    const categories = pack.typeOptions || [];
    return `<section class="panel advisor-rank-controls">
      <div class="panel-head">
        <div>
          <h2>排名筛选</h2>
          <p class="desc">${B.esc(pack.meta?.classificationNote || "")}</p>
        </div>
        <button class="small-button" type="button" data-reset-ranking>重置</button>
      </div>
      <div class="advisor-rank-filter-grid">
        <label class="strategy-filter-field"><span>收益区间</span><select id="rankInterval">${intervalOptions}</select><em>切换后全表重排</em></label>
        <label class="strategy-filter-field"><span>基准权益分档</span><select id="rankCategory">${optionHtml(categories, state.category, "全部分档")}</select><em>按同一基准权益分档混排</em></label>
        <label class="strategy-filter-field"><span>策略对客状态</span><select id="rankCustomer">
          <option value="all"${state.customer === "all" ? " selected" : ""}>全部投顾策略</option>
          <option value="yes"${state.customer === "yes" ? " selected" : ""}>仅对客投顾</option>
          <option value="no"${state.customer === "no" ? " selected" : ""}>仅非对客投顾</option>
        </select><em>FOF基金不适用该字段</em></label>
        <label class="strategy-filter-field"><span>搜索</span><input id="rankSearch" value="${B.esc(state.search)}" placeholder="产品、代码、机构、分类"><em>支持模糊匹配</em></label>
      </div>
      <div class="advisor-rank-segment-row">
        <div class="data-tabs advisor-rank-segment">
          ${entityButton("all", "投顾+FOF混排")}
          ${entityButton("投顾策略", "仅投顾策略")}
          ${entityButton("FOF基金", "仅FOF基金")}
        </div>
        <label class="rank-check"><input id="rankGfOnly" type="checkbox"${state.gfOnly ? " checked" : ""}> 只看广发产品</label>
        <label class="rank-page-size">显示 <select id="rankPageSize">
          ${[50, 80, 120, 200, 500].map((size) => `<option value="${size}"${state.pageSize === size ? " selected" : ""}>${size}</option>`).join("")}
        </select> 条</label>
      </div>
    </section>`;
  }

  function renderCategorySummary() {
    const categories = (pack.categoryRows || []).slice(0, 12);
    return `<section class="panel">
      <div class="panel-head"><div><h2>主要分档样本</h2><p class="desc">用于判断当前可比池是否足够大；排名时可在“基准权益分档”中进一步收窄。</p></div></div>
      <div class="category-chip-list">
        ${categories.map((row) => `<button type="button" class="category-chip-button ${state.category === row.分类 ? "is-active" : ""}" data-category-pick="${B.esc(row.分类)}">
          <strong>${B.esc(row.分类)}</strong>
          <span>${B.esc(row.产品数)} 个产品 · 投顾 ${B.esc(row.投顾策略数)} / FOF ${B.esc(row.FOF基金数)}</span>
        </button>`).join("")}
      </div>
    </section>`;
  }

  function render() {
    const ranked = rankedRows();
    const valid = ranked.filter(hasReturn);
    const selectedInterval = intervals.find((item) => item.label === state.interval) || {};
    root.innerHTML = `
      <section class="page-title">
        <div>
          <h1>投顾-FOF排名</h1>
          <p class="desc">投顾策略与FOF基金统一进入基准权益分档池，可按收益区间、产品类型和分档混合排名。</p>
        </div>
        <div class="title-pills">
          <span class="pill">数据截至 ${B.esc(pack.meta?.dataUpdatedTo || "")}</span>
          <span class="pill">总样本 ${B.esc(pack.meta?.totalCount || rows.length)}</span>
          <span class="pill">广发 ${B.esc(pack.meta?.guangfaCount || 0)}</span>
        </div>
      </section>
      ${renderControls()}
      ${renderMetrics(ranked, valid)}
      <section class="panel">
        <div class="panel-head">
          <div>
            <h2>收益-风险点阵</h2>
            <p class="desc">${B.esc(selectedInterval.description || "")}；点阵、分类样本和排名明细均按当前筛选条件同步更新。</p>
          </div>
        </div>
        ${renderScatter(ranked)}
      </section>
      ${renderCategorySummary()}
      <section class="panel">
        <div class="panel-head"><div><h2>排名明细</h2><p class="desc">广发产品以浅色底和“广发”标签高亮。</p></div></div>
        ${renderTable(ranked)}
      </section>
    `;
  }

  root.addEventListener("click", (event) => {
    const entityButtonEl = event.target.closest("[data-entity]");
    if (entityButtonEl) {
      state.entity = entityButtonEl.dataset.entity || "all";
      state.selectedPointId = "";
      render();
      return;
    }
    const categoryButton = event.target.closest("[data-category-pick]");
    if (categoryButton) {
      state.category = categoryButton.dataset.categoryPick || "all";
      state.selectedPointId = "";
      render();
      return;
    }
    const point = event.target.closest("[data-scatter-id]");
    if (point) {
      state.selectedPointId = point.dataset.scatterId || "";
      render();
      return;
    }
    if (event.target.closest("[data-reset-ranking]")) {
      state.interval = "上半年";
      state.entity = "all";
      state.category = "all";
      state.customer = "all";
      state.gfOnly = false;
      state.search = "";
      state.pageSize = 80;
      state.scatterXMetric = "";
      state.scatterYMetric = "";
      state.selectedPointId = "";
      render();
    }
  });

  root.addEventListener("change", (event) => {
    const target = event.target;
    if (!target) return;
    if (target.id === "rankInterval") {
      state.interval = target.value;
      state.scatterXMetric = defaultXMetric();
      state.scatterYMetric = defaultYMetric();
      state.selectedPointId = "";
    }
    if (target.id === "rankCategory") {
      state.category = target.value;
      state.selectedPointId = "";
    }
    if (target.id === "rankCustomer") {
      state.customer = target.value;
      state.selectedPointId = "";
    }
    if (target.id === "rankGfOnly") {
      state.gfOnly = target.checked;
      state.selectedPointId = "";
    }
    if (target.id === "rankPageSize") state.pageSize = Number(target.value) || 80;
    if (target.id === "rankScatterX") state.scatterXMetric = target.value;
    if (target.id === "rankScatterY") state.scatterYMetric = target.value;
    render();
  });

  root.addEventListener("input", (event) => {
    const target = event.target;
    if (!target || target.id !== "rankSearch") return;
    state.search = target.value;
    state.selectedPointId = "";
    clearTimeout(searchTimer);
    searchTimer = setTimeout(render, 180);
  });

  render();
})();
