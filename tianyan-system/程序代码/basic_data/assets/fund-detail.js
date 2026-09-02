(() => {
  const B = window.BasicData;
  const root = B.byId("fundDetailPage");
  const pack = window.__BASIC_DATA__?.fundDetailPack;
  const economicPack = window.__BASIC_FUND_ECONOMIC_EXPOSURE_PACK__ || null;
  const semanticIndex = window.__AI_STRATEGY_SEMANTIC_INDEX__ || null;
  const query = new URLSearchParams(window.location.search);
  const requestedCode = (query.get("code") || query.get("id") || "").trim();
  const requestedName = (query.get("name") || "").trim();

  function empty(message) {
    root.innerHTML = `<section class="panel"><div class="empty">${B.esc(message)}</div></section>`;
  }

  if (!pack || !Array.isArray(pack.funds)) {
    empty("未找到基金详情数据包，请先运行报告数据包重建步骤。");
    return;
  }

  const fundFields = pack.fundFields || [];
  const holdingFields = pack.holdingFields || [];
  const monthlyFields = pack.monthlyFields || [];
  const funds = pack.funds || [];
  const holdings = pack.holdings || [];
  const monthly = pack.monthly || [];

  function toObject(fields, row) {
    return Object.fromEntries((fields || []).map((field, index) => [field, row?.[index] ?? ""]));
  }

  const codeField = fundFields[0] || "基金代码";
  const nameField = fundFields[1] || "基金名称";
  const fundObjects = funds.map((row, index) => ({ index, row, data: toObject(fundFields, row) }));
  const matchedFromPack = fundObjects.find((item) => {
    const code = String(item.data[codeField] || "").trim();
    const name = String(item.data[nameField] || "").trim();
    return (requestedCode && code === requestedCode) || (requestedName && name === requestedName);
  });
  const fallbackFundData = requestedCode
    ? { [codeField]: requestedCode, [nameField]: requestedName || requestedCode }
    : null;
  const matched = matchedFromPack || (fallbackFundData ? { index: -1, row: null, data: fallbackFundData } : null);

  if (!matched) {
    empty("未找到匹配的基金。");
    return;
  }

  function economicSnapshotFor(code) {
    const fields = economicPack?.fields || [];
    const rows = economicPack?.rows || [];
    const codeIndex = fields.indexOf("基金代码");
    if (codeIndex < 0) return {};
    const row = rows.find((item) => String(item?.[codeIndex] || "").trim() === String(code || "").trim());
    if (!row) return {};
    const source = toObject(fields, row);
    return {
      ...source,
      基金分类来源: "基金经济暴露快照",
      基金分类依据: source.证据说明 || "",
      基金穿透报告期: source.报告期 || "",
      基金穿透覆盖状态: source.质量状态 || "",
      经济资产大类: source.标准资产大类 || "",
      经济资产细类: source.标准资产细类 || "",
      经济主题标签: source.主题标签 || [],
      经济暴露报告期: source.报告期 || "",
      经济暴露证据说明: source.证据说明 || "",
      经济暴露置信度: source.置信度 || "",
      经济暴露质量状态: source.质量状态 || "",
    };
  }

  const fundEconomicSnapshot = economicSnapshotFor(matched.data[codeField]);

  function raw(value) {
    return value === null || value === undefined ? "" : String(value);
  }

  function num(value) {
    if (value === null || value === undefined || value === "") return null;
    const number = Number(value);
    return Number.isFinite(number) ? number : null;
  }

  function nonEmpty(value) {
    return value !== null && value !== undefined && value !== "";
  }

  function mergeNonEmpty(...sources) {
    const merged = {};
    sources.forEach((source) => {
      Object.entries(source || {}).forEach(([key, value]) => {
        if (nonEmpty(value)) merged[key] = value;
      });
    });
    return merged;
  }

  function isSignedPctField(field) {
    return /收益|变化|增配|减配|涨跌幅|回撤|波动|日收益率|贡献/.test(String(field || ""));
  }

  function isPctField(field) {
    return /权重|收益|增配|减配|比例|占比|中位|近\d|今年以来|成立以来|涨跌幅|回撤|波动率|投顾费率|七日年化/.test(String(field || ""));
  }

  function isTextField(field) {
    return /代码|ID|编号/.test(String(field || ""));
  }

  function valueHtml(field, value) {
    if (value === null || value === undefined || value === "") return '<span class="value-muted">未披露</span>';
    if (/^是否/.test(String(field || "")) && (value === 0 || value === 1 || value === "0" || value === "1")) {
      return Number(value) ? "是" : "否";
    }
    if (Array.isArray(value)) {
      return value.length ? value.map((item) => B.esc(raw(item?.主题名称 || item?.名称 || item))).join("、") : '<span class="value-muted">未披露</span>';
    }
    if (typeof value === "object") {
      const rows = Object.entries(value).filter(([, v]) => nonEmpty(v));
      return rows.length ? rows.map(([k, v]) => `${B.esc(k)} ${isPctField(k) || Number.isFinite(Number(v)) ? B.pct(Number(v)) : B.esc(v)}`).join("、") : '<span class="value-muted">未披露</span>';
    }
    if (isTextField(field)) return B.esc(String(value));
    const number = Number(value);
    if (Number.isFinite(number) && isSignedPctField(field)) return B.pctSigned(number);
    if (Number.isFinite(number) && isPctField(field)) return B.pct(number);
    if (Number.isFinite(number)) return B.fmt(number);
    return B.esc(value);
  }

  function semanticRows(packName) {
    const semanticPack = semanticIndex?.[packName];
    if (!semanticPack || !Array.isArray(semanticPack.rows)) return [];
    const fields = semanticPack.fields || [];
    return semanticPack.rows.map((row) => Object.fromEntries(fields.map((field, index) => [field, row[index] ?? ""])));
  }

  function fundEntityRows(fundData, enrichment) {
    const code = raw(fundData[codeField]).trim();
    const name = raw(fundData[nameField]).trim();
    const embeddedPack = enrichment?.fundEntityPack;
    if (embeddedPack?.rows) {
      const fields = embeddedPack.fields || [];
      return embeddedPack.rows
        .map((row) => Object.fromEntries(fields.map((field, index) => [field, row[index] ?? ""])))
        .sort((a, b) => (num(b.暴露比例) || 0) - (num(a.暴露比例) || 0));
    }
    return semanticRows("fundEntities")
      .filter((row) => (code && raw(row.基金代码).trim() === code) || (name && raw(row.基金名称).trim() === name))
      .sort((a, b) => {
        const typeOrder = ["资产大类", "资产", "指数", "地域", "行业主题", "产品形态", "风格"];
        const av = typeOrder.indexOf(raw(a.实体类型));
        const bv = typeOrder.indexOf(raw(b.实体类型));
        if (av !== bv) return (av < 0 ? 99 : av) - (bv < 0 ? 99 : bv);
        return (num(b.暴露比例) || 0) - (num(a.暴露比例) || 0);
      });
  }

  function fundEntityBadge(row) {
    const source = [row.来源字段 || row.来源, row.来源值].filter(Boolean).join("：");
    const meta = [row.实体等级, source, row.抽取规则ID].filter(Boolean).join("｜");
    return `<div class="entity-badge">
      <div><strong>${B.esc(row.实体名称 || row.实体Key || "未命名实体")}</strong><span>${B.esc(row.实体类型 || "实体")}｜${B.esc(meta || "未披露来源")}</span></div>
      <em>${num(row.暴露比例) !== null ? B.pct(row.暴露比例) : "未量化"}</em>
      ${row.证据 ? `<p>${B.esc(row.证据)}</p>` : ""}
    </div>`;
  }

  function fundEntitySection(fundData, enrichment) {
    const rows = fundEntityRows(fundData, enrichment);
    const groups = ["资产大类", "资产", "指数", "地域", "行业主题", "产品形态", "风格"].map((type) => ({
      type,
      rows: rows.filter((row) => row.实体类型 === type).slice(0, 12),
    })).filter((group) => group.rows.length);
    return `<section class="panel entity-panel fund-entity-section">
      <div class="panel-head">
        <div>
          <h2>基金实体画像</h2>
          <p class="desc">基于基金经济暴露快照、主题标签、指数和基金名称抽取；原始季报资产配置仅作为审计信息保留。</p>
        </div>
        <span class="pill">${rows.length.toLocaleString("zh-CN")} 个实体</span>
      </div>
      ${groups.length ? groups.map((group) => `
        <div class="entity-group">
          <h3>${B.esc(group.type)}</h3>
          <div class="entity-grid">${group.rows.map(fundEntityBadge).join("")}</div>
        </div>
      `).join("") : '<div class="empty">当前基金暂无可展示实体。请先重建 AI 语义索引。</div>'}
    </section>`;
  }

  function pickFields(fields, names) {
    return names.filter((name) => fields.includes(name));
  }

  function strategyHref(row) {
    const id = String(row?.[holdingFields[1]] || "").trim();
    return id ? `./strategy.html?id=${encodeURIComponent(id)}` : "";
  }

  function holdingValueHtml(row, field) {
    if (field === holdingFields[2]) {
      const href = strategyHref(row);
      const value = row[field];
      if (href && nonEmpty(value)) {
        return `<a class="link" href="${href}">${B.esc(value)}</a>`;
      }
    }
    return valueHtml(field, row[field]);
  }

  function table(fields, rows, formatter) {
    const head = fields.map((field) => `<th>${B.label(field)}</th>`).join("");
    const body = rows.length
      ? rows.map((row) => `<tr>${fields.map((field) => `<td>${formatter ? formatter(row, field) : valueHtml(field, row[field])}</td>`).join("")}</tr>`).join("")
      : `<tr><td colspan="${fields.length}"><div class="empty">暂无数据</div></td></tr>`;
    return `<div class="table-wrap"><table><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table></div>`;
  }

  function dataFromSources(field, sources) {
    for (const source of sources) {
      if (source && nonEmpty(source[field])) return source[field];
    }
    return "";
  }

  function infoCard(field, sources) {
    return `<div class="fund-info-card">
      <span>${B.label(field)}</span>
      <strong>${valueHtml(field, dataFromSources(field, sources))}</strong>
    </div>`;
  }

  function infoSection(title, fields, sources) {
    const cards = fields.filter((field) => sources.some((source) => source && Object.prototype.hasOwnProperty.call(source, field))).map((field) => infoCard(field, sources)).join("");
    return cards ? `<section class="fund-info-section"><h3>${B.esc(title)}</h3><div class="fund-info-grid">${cards}</div></section>` : "";
  }

  function themeTagsHtml(value) {
    const tags = Array.isArray(value) ? value : [];
    if (!tags.length) return "";
    return `<div class="fund-theme-tags">${tags.slice(0, 16).map((tag) => `<span>${B.esc(tag.主题名称 || tag.名称 || tag)}</span>`).join("")}</div>`;
  }

  function chartPath(points, xOf, yOf) {
    return points.map((point, index) => `${index ? "L" : "M"}${xOf(point).toFixed(1)},${yOf(point).toFixed(1)}`).join(" ");
  }

  function navChartSeries(enrichment) {
    const nav = enrichment?.nav || {};
    const benchmark = enrichment?.benchmark || {};
    return [
      { key: "fund", name: "基金净值", color: "#c02f2f", rows: (nav.rows || []).filter((row) => Number.isFinite(Number(row.走势图指数))) },
      { key: "benchmark", name: benchmark.name ? `基准：${benchmark.name}` : "基准走势", color: "#2563eb", rows: (benchmark.rows || []).filter((row) => Number.isFinite(Number(row.走势图指数))) },
    ].filter((series) => series.rows.length >= 2);
  }

  function navChartModel(enrichment) {
    const series = navChartSeries(enrichment);
    if (!series.length) return null;
    const width = 980;
    const height = 320;
    const pad = { left: 62, right: 28, top: 28, bottom: 48 };
    const allRows = series.flatMap((item) => item.rows);
    const values = allRows.map((row) => Number(row.走势图指数));
    let min = Math.min(...values);
    let max = Math.max(...values);
    if (max === min) {
      min -= 1;
      max += 1;
    }
    const range = max - min;
    min -= range * 0.08;
    max += range * 0.08;
    const allDates = Array.from(new Set(allRows.map((row) => String(row.交易日期 || "")).filter(Boolean))).sort();
    const firstTime = new Date(allDates[0]).getTime();
    const lastTime = new Date(allDates[allDates.length - 1]).getTime();
    const xOfDate = (dateText) => {
      const t = new Date(dateText).getTime();
      return pad.left + (lastTime === firstTime ? 0 : (t - firstTime) / (lastTime - firstTime)) * (width - pad.left - pad.right);
    };
    const yOfValue = (value) => height - pad.bottom - ((Number(value) - min) / (max - min)) * (height - pad.top - pad.bottom);
    return { series, allDates, width, height, pad, min, max, xOfDate, yOfValue };
  }

  function fundNavSvg(enrichment) {
    const model = navChartModel(enrichment);
    if (!model) return '<div class="empty">基金净值或基准走势数据不足，无法绘制曲线。</div>';
    const { series, allDates, width, height, pad, min, max, xOfDate, yOfValue } = model;
    const grid = [0, 0.25, 0.5, 0.75, 1].map((ratio) => {
      const y = pad.top + ratio * (height - pad.top - pad.bottom);
      const value = max - ratio * (max - min);
      return `<line x1="${pad.left}" y1="${y}" x2="${width - pad.right}" y2="${y}" stroke="#edf1f5"/><text class="axis-text" x="8" y="${y + 4}">${value.toFixed(1)}</text>`;
    }).join("");
    const tickIndexes = [0, Math.floor((allDates.length - 1) / 2), allDates.length - 1].filter((item, index, arr) => arr.indexOf(item) === index);
    const ticks = tickIndexes.map((index) => {
      const dateText = allDates[index];
      const x = xOfDate(dateText);
      return `<line class="tick-line" x1="${x}" y1="${pad.top}" x2="${x}" y2="${height - pad.bottom}"/><text class="axis-text" x="${x}" y="${height - 18}" text-anchor="middle">${B.esc(String(dateText).slice(0, 10))}</text>`;
    }).join("");
    const paths = series.map((item) => {
      const d = chartPath(item.rows, (row) => xOfDate(row.交易日期), (row) => yOfValue(row.走势图指数));
      const last = item.rows[item.rows.length - 1];
      return `<path d="${d}" fill="none" stroke="${item.color}" stroke-width="${item.key === "fund" ? "3.4" : "2.8"}" stroke-linejoin="round" stroke-linecap="round"/>
        <circle cx="${xOfDate(last.交易日期).toFixed(1)}" cy="${yOfValue(last.走势图指数).toFixed(1)}" r="4.5" fill="#fff" stroke="${item.color}" stroke-width="2"/>`;
    }).join("");
    const legend = `<div class="fund-nav-legend">${series.map((item) => `<span><i style="background:${item.color}"></i>${B.esc(item.name)}</span>`).join("")}</div>`;
    return `<div class="fund-nav-svg-wrap" data-fund-nav-chart>
      ${legend}
      <svg viewBox="0 0 ${width} ${height}" role="img" aria-label="基金净值走势图">
        ${grid}${ticks}
        <line x1="${pad.left}" y1="${height - pad.bottom}" x2="${width - pad.right}" y2="${height - pad.bottom}" stroke="#d0d7de"/>
        <line x1="${pad.left}" y1="${pad.top}" x2="${pad.left}" y2="${height - pad.bottom}" stroke="#d0d7de"/>
        <text class="axis-title" x="${width / 2}" y="${height - 6}" text-anchor="middle">交易日期</text>
        <text class="axis-title" transform="translate(16 ${height / 2}) rotate(-90)" text-anchor="middle">归一化指数（首日=100）</text>
        ${paths}
        <g class="fund-nav-hover-layer" visibility="hidden">
          <line class="fund-nav-hover-line" x1="0" y1="${pad.top}" x2="0" y2="${height - pad.bottom}"/>
          <g class="fund-nav-hover-points"></g>
        </g>
        <rect class="fund-nav-hover-capture" x="${pad.left}" y="${pad.top}" width="${width - pad.left - pad.right}" height="${height - pad.top - pad.bottom}"/>
      </svg>
      <div class="fund-nav-tooltip" hidden></div>
    </div>`;
  }

  function pointAtOrBefore(rows, dateText) {
    let selected = null;
    for (const row of rows) {
      if (String(row.交易日期) <= dateText) selected = row;
      else break;
    }
    return selected;
  }

  function wireFundNavChart(enrichment) {
    const wrap = root.querySelector("[data-fund-nav-chart]");
    if (!wrap) return;
    const svg = wrap.querySelector("svg");
    const tip = wrap.querySelector(".fund-nav-tooltip");
    const hoverLayer = wrap.querySelector(".fund-nav-hover-layer");
    const hoverLine = wrap.querySelector(".fund-nav-hover-line");
    const hoverPoints = wrap.querySelector(".fund-nav-hover-points");
    const capture = wrap.querySelector(".fund-nav-hover-capture");
    const model = navChartModel(enrichment);
    if (!svg || !tip || !model) return;
    const { series, allDates, width, pad, xOfDate, yOfValue } = model;
    const handleMove = (event) => {
      const rect = svg.getBoundingClientRect();
      const viewX = (event.clientX - rect.left) / rect.width * width;
      const nearestDate = allDates.reduce((best, date) => Math.abs(xOfDate(date) - viewX) < Math.abs(xOfDate(best) - viewX) ? date : best, allDates[0]);
      const guideX = xOfDate(nearestDate);
      const rows = series.map((item) => {
        const row = item.rows.find((point) => point.交易日期 === nearestDate) || pointAtOrBefore(item.rows, nearestDate) || item.rows[0];
        const value = num(row?.走势图指数);
        const base = num(item.rows[0]?.走势图指数);
        return { ...item, row, value, returnValue: value !== null && base ? (value / base - 1) * 100 : null };
      }).filter((item) => item.value !== null);
      hoverLayer.setAttribute("visibility", "visible");
      hoverLine.setAttribute("x1", guideX.toFixed(1));
      hoverLine.setAttribute("x2", guideX.toFixed(1));
      hoverPoints.innerHTML = rows.map((item) => `<circle cx="${guideX.toFixed(1)}" cy="${yOfValue(item.value).toFixed(1)}" r="4" fill="#fff" stroke="${item.color}" stroke-width="2"/>`).join("");
      tip.innerHTML = `<strong>${B.esc(nearestDate)}</strong>${rows.map((item) => `
        <div class="fund-nav-tip-row">
          <span><i style="background:${item.color}"></i>${B.esc(item.name)}</span>
          <b>${item.value.toFixed(2)}</b>
          <em class="${(item.returnValue || 0) >= 0 ? "ret-pos" : "ret-neg"}">${B.pctSigned(item.returnValue || 0)}</em>
        </div>
      `).join("")}`;
      tip.hidden = false;
      const host = wrap.getBoundingClientRect();
      const localX = event.clientX - host.left;
      const localY = event.clientY - host.top;
      tip.style.left = `${Math.min(localX + 14, Math.max(12, wrap.clientWidth - 310))}px`;
      tip.style.top = `${Math.max(36, localY - 10)}px`;
    };
    const handleLeave = () => {
      tip.hidden = true;
      hoverLayer.setAttribute("visibility", "hidden");
    };
    svg.addEventListener("mousemove", handleMove);
    svg.addEventListener("pointermove", handleMove);
    svg.addEventListener("mouseleave", handleLeave);
    svg.addEventListener("pointerleave", handleLeave);
    if (capture) {
      capture.addEventListener("mousemove", handleMove);
      capture.addEventListener("pointermove", handleMove);
      capture.addEventListener("mouseleave", handleLeave);
      capture.addEventListener("pointerleave", handleLeave);
    }
  }

  function navSection(enrichment) {
    const nav = enrichment?.nav || {};
    const benchmark = enrichment?.benchmark || {};
    const latest = nav.latest || {};
    const rows = nav.rows || [];
    const first = rows[0];
    const last = rows[rows.length - 1];
    const trendReturn = first && last && Number(first.走势图指数) ? (Number(last.走势图指数) / Number(first.走势图指数) - 1) * 100 : null;
    const kpis = [
      ["最新日期", latest.交易日期],
      ["走势图口径", nav.basis],
      ["区间收益率", trendReturn],
      ["最新单位净值", latest.单位净值],
      ["最新累计净值", latest.累计净值],
      ["最新日收益率_百分比", latest.日收益率_百分比],
      ["基准名称", benchmark.name],
    ];
    return `<section class="panel fund-nav-panel">
      <div class="panel-head">
        <div>
          <h2>基金业绩走势</h2>
          <p class="desc">近 ${nav.lookbackDays || 370} 天净值按周频压缩展示；基金和基准均归一到首个可用点=100，便于比较相对走势。基准选择：${B.esc(benchmark.reason || "按基金类型默认匹配")}。</p>
        </div>
        <span class="pill">${rows.length.toLocaleString("zh-CN")} 个图表点</span>
      </div>
      <div class="fund-kpi-strip">${kpis.map(([field, value]) => `<div><span>${B.label(field)}</span><strong>${valueHtml(field, value)}</strong></div>`).join("")}</div>
      ${fundNavSvg(enrichment)}
    </section>`;
  }

  const assetFields = [
    ["股票", "股票占比_百分比", "#c02f2f"],
    ["债券", "债券占比_百分比", "#2563eb"],
    ["现金", "现金占比_百分比", "#0f7b4f"],
    ["基金", "基金占比_百分比", "#7c3aed"],
    ["商品", "商品占比_百分比", "#b7791f"],
    ["存托凭证", "存托凭证占比_百分比", "#0f766e"],
    ["其他", "其他占比_百分比", "#667085"],
  ];

  function assetBars(report) {
    if (!report) return '<div class="empty">暂无资产配置数据</div>';
    const bars = assetFields
      .map(([label, field, color]) => ({ label, field, color, value: num(report[field]) }))
      .filter((item) => item.value !== null)
      .sort((a, b) => b.value - a.value);
    return bars.length ? `<div class="fund-asset-bars">${bars.map((item) => `
      <div class="fund-asset-bar">
        <span>${B.esc(item.label)}</span>
        <div><i style="width:${Math.max(1, Math.min(100, item.value)).toFixed(2)}%;background:${item.color}"></i></div>
        <strong>${B.pct(item.value)}</strong>
      </div>
    `).join("")}</div>` : '<div class="empty">暂无资产配置比例</div>';
  }

  function assetReportCard(title, report) {
    if (!report) return "";
    return `<div class="fund-report-card">
      <div class="fund-report-card-head">
        <h3>${B.esc(title)}</h3>
        <span>${B.esc(report.报告期 || "未披露报告期")}</span>
      </div>
      ${assetBars(report)}
      <div class="fund-report-meta">
        <span>披露日期：${B.esc(report.披露日期 || "未披露")}</span>
        <span>净资产：${valueHtml("净资产_亿元", report.净资产_亿元)} 亿元</span>
        <span>来源：${B.esc(report.数据来源 || "未披露")}</span>
      </div>
    </div>`;
  }

  function assetSection(enrichment) {
    const reports = enrichment?.assetReports || [];
    const latest = reports[0] || null;
    const latestAnnual = reports.find((report) => String(report.报告期 || "").endsWith("-12-31") && report !== latest) || reports.find((report) => String(report.报告期 || "").endsWith("-12-31"));
    const tableFields = ["报告期", "披露日期", "股票占比_百分比", "债券占比_百分比", "现金占比_百分比", "基金占比_百分比", "商品占比_百分比", "其他占比_百分比", "净资产_亿元", "数据来源"];
    return `<section class="panel fund-asset-panel">
      <div class="panel-head">
        <div>
          <h2>季报/年报资产配置</h2>
          <p class="desc">来自基金季报资产配置表，展示最新报告期与最近年报；比例为占基金净值比例，可能因衍生品、杠杆或四舍五入导致合计不等于 100%。</p>
        </div>
      </div>
      <div class="fund-report-grid">
        ${assetReportCard("最新报告期", latest)}
        ${assetReportCard("最近年报", latestAnnual)}
      </div>
      ${table(tableFields, reports)}
    </section>`;
  }

  function holdingReportTable(kind, report) {
    if (!report) return '<div class="empty">暂无持仓明细</div>';
    const fields = kind === "stock"
      ? ["股票代码", "股票名称", "占基金净值比例_百分比", "持股数_万股", "持仓市值_万元", "数据来源"]
      : ["债券代码", "债券名称", "占基金净值比例_百分比", "持债数量", "持仓市值_万元", "债券类型", "数据来源"];
    return `<div class="fund-report-block">
      <div class="fund-report-card-head">
        <h3>${kind === "stock" ? "股票持仓" : "债券持仓"}｜${B.esc(report.reportDate || "未披露报告期")}</h3>
        <span>前 ${report.rowLimit || report.rows.length} 条，占比合计 ${B.pct(report.totalWeight || 0)}</span>
      </div>
      ${table(fields, report.rows || [])}
    </div>`;
  }

  function industryReport(report) {
    if (!report) return '<div class="empty">暂无行业配置数据</div>';
    const rows = report.rows || [];
    return `<div class="fund-report-block">
      <div class="fund-report-card-head">
        <h3>股票行业配置｜${B.esc(report.reportDate || "未披露报告期")}</h3>
        <span>样本权重合计 ${B.pct(report.totalWeight || 0)}</span>
      </div>
      <div class="fund-industry-bars">
        ${rows.map((row) => {
          const value = num(row.占基金净值比例_百分比) || 0;
          return `<div class="fund-industry-bar">
            <span>${B.esc(row.行业一级 || "未识别")}</span>
            <div><i style="width:${Math.max(1, Math.min(100, value)).toFixed(2)}%"></i></div>
            <strong>${B.pct(value)}</strong>
            <em>${B.esc(row.股票持仓样本数 || 0)} 只</em>
          </div>`;
        }).join("") || '<div class="empty">暂无行业配置数据</div>'}
      </div>
    </div>`;
  }

  function holdingCoveragePanel(enrichment) {
    const coverage = enrichment?.holdingCoverage || {};
    const items = [
      ["资产配置报告期", coverage.资产配置报告期],
      ["股票明细行数", coverage.股票明细行数],
      ["债券明细行数", coverage.债券明细行数],
      ["最新其他占比_百分比", coverage.最新其他占比_百分比],
      ["最新基金占比_百分比", coverage.最新基金占比_百分比],
    ];
    return `<div class="fund-coverage-panel">
      <div class="fund-coverage-grid">${items.map(([field, value]) => `<div><span>${B.label(field)}</span><strong>${valueHtml(field, value)}</strong></div>`).join("")}</div>
      <p>${B.esc(coverage.覆盖说明 || "当前基金暂无持仓覆盖说明。")}</p>
    </div>`;
  }

  function lookthroughInsufficientNotice(enrichment) {
    const coverage = enrichment?.holdingCoverage || {};
    const assetReports = enrichment?.assetReports || [];
    const latestAsset = assetReports[0] || {};
    const stockReports = enrichment?.stockReports || [];
    const stockRows = Math.max(
      num(coverage.股票明细行数) || 0,
      stockReports.reduce((acc, report) => acc + ((report.rows || []).length || 0), 0)
    );
    if (stockRows > 0) return "";
    const fundPct = num(coverage.最新基金占比_百分比 ?? latestAsset.基金占比_百分比) || 0;
    const otherPct = num(coverage.最新其他占比_百分比 ?? latestAsset.其他占比_百分比) || 0;
    const combined = fundPct + otherPct;
    const profileText = [
      matched.data[nameField],
      matched.data.基金类型,
      matched.data.二级分类,
      matched.data.标准资产大类,
      matched.data.标准资产细类,
      matched.data.资产暴露,
      matched.data.基金分类依据,
    ].map(raw).join(" ");
    const needsLookthrough = /ETF联接|联接|FOF|QDII-FOF|QDII|基金中基金|底层基金|指数/.test(profileText);
    if (combined < 30 && !(needsLookthrough && combined >= 15)) return "";
    return `<div class="fund-lookthrough-notice">
      <strong>穿透口径提示</strong>
      <p>最新资产配置中“基金/其他”合计 ${B.pct(combined)}（基金 ${B.pct(fundPct)}，其他 ${B.pct(otherPct)}），且当前股票明细为空。ETF联接、FOF、QDII-FOF或持有底层基金/指数的样本，权益暴露可能留在底层基金或指数成分中；这不是“无权益暴露”，而是底层基金/指数穿透不足。</p>
    </div>`;
  }

  function holdingReportsSection(enrichment) {
    const stockReports = enrichment?.stockReports || [];
    const bondReports = enrichment?.bondReports || [];
    const assetReports = enrichment?.assetReports || [];
    const fallbackAssetCard = (!stockReports.length && !bondReports.length && assetReports[0]) ? assetReportCard("报告资产配置（明细未披露时展示）", assetReports[0]) : "";
    const latestStockBlock = stockReports[0] ? holdingReportTable("stock", stockReports[0]) : '<div class="empty">暂无股票重仓股明细</div>';
    return `<section class="panel fund-holding-report-panel">
      <div class="panel-head">
        <div>
          <h2>季报/年报持仓明细</h2>
          <p class="desc">股票、债券持仓来自东财 F10 基金定期报告解析；股票表展示季报披露的重仓股。行业映射当前覆盖不足，前台不展示股票行业配置。</p>
        </div>
      </div>
      ${holdingCoveragePanel(enrichment)}
      ${lookthroughInsufficientNotice(enrichment)}
      ${fallbackAssetCard}
      ${latestStockBlock}
      ${stockReports[1] ? holdingReportTable("stock", stockReports[1]) : ""}
      ${bondReports[0] ? holdingReportTable("bond", bondReports[0]) : ""}
      ${bondReports[1] ? holdingReportTable("bond", bondReports[1]) : ""}
    </section>`;
  }

  function exposureStringObject(value) {
    if (value && typeof value === "object" && !Array.isArray(value)) {
      return Object.fromEntries(
        Object.entries(value)
          .map(([key, item]) => [raw(key).trim(), num(item)])
          .filter(([key, item]) => key && item !== null)
      );
    }
    if (typeof value !== "string") return {};
    const out = {};
    value.split(/[、,，;；]+/).forEach((part) => {
      const item = part.trim();
      const match = item.match(/^(.+?)(-?\d+(?:\.\d+)?)%$/);
      if (match) out[match[1].trim()] = Number(match[2]);
    });
    return out;
  }

  function classificationSnapshotSection(fundData, enrichment, economicSnapshot) {
    const snapshots = enrichment?.classificationSnapshots || [];
    const authoritative = mergeNonEmpty(fundData, economicSnapshot);
    if (!snapshots.length && !nonEmpty(authoritative.经济资产暴露) && !nonEmpty(authoritative.资产暴露)) return "";
    const latest = snapshots[0];
    const economicAssetParsed = exposureStringObject(authoritative.经济资产暴露 || authoritative.资产暴露 || "");
    const economicIndustryParsed = exposureStringObject(authoritative.经济行业暴露 || authoritative.行业暴露 || "");
    const rawAssetParsed = exposureStringObject(authoritative.原始资产暴露 || "");
    const rawIndustryParsed = exposureStringObject(authoritative.原始行业暴露 || "");
    const economicAsset = hasMeaningfulExposure(economicAssetParsed) ? economicAssetParsed : (latest?.资产暴露 || {});
    const economicIndustry = hasMeaningfulExposure(economicIndustryParsed) ? economicIndustryParsed : (latest?.行业暴露 || {});
    const rawAsset = hasMeaningfulExposure(rawAssetParsed) ? rawAssetParsed : (latest?.资产暴露 || {});
    const rawIndustry = hasMeaningfulExposure(rawIndustryParsed) ? rawIndustryParsed : (latest?.行业暴露 || {});
    const showEconomicIndustry = hasMeaningfulExposure(economicIndustry);
    const showRawIndustry = hasMeaningfulExposure(rawIndustry);
    return `<section class="panel fund-classification-panel">
      <div class="panel-head">
        <div>
          <h2>基金经济暴露快照</h2>
          <p class="desc">业务分析统一使用经济暴露；原始季报资产配置只用于审计追溯，避免把 ETF 联接、FOF、黄金和固收指数的“基金/其他”误当成真实资产方向。</p>
        </div>
        <span class="pill">${B.esc(authoritative.经济暴露报告期 || latest?.报告期 || "未披露报告期")}</span>
      </div>
      <div class="fund-report-grid">
        <div class="fund-report-card"><h3>经济资产暴露（业务口径）</h3>${assetObjectBars(economicAsset)}</div>
        ${showEconomicIndustry ? `<div class="fund-report-card"><h3>经济行业暴露</h3>${assetObjectBars(economicIndustry)}</div>` : ""}
        <div class="fund-report-card"><h3>原始季报资产配置（审计）</h3>${assetObjectBars(rawAsset)}</div>
        ${showRawIndustry ? `<div class="fund-report-card"><h3>原始行业暴露（审计）</h3>${assetObjectBars(rawIndustry)}</div>` : ""}
      </div>
      <div class="source-method"><strong>穿透方法</strong> ${B.esc(authoritative.穿透方法 || "未披露")}；<strong>质量状态</strong> ${B.esc(authoritative.经济暴露质量状态 || "未披露")}；<strong>置信度</strong> ${B.esc(authoritative.经济暴露置信度 || "未披露")}。${authoritative.经济暴露证据说明 ? B.esc(authoritative.经济暴露证据说明) : ""}</div>
      ${themeTagsHtml(latest?.主题标签)}
    </section>`;
  }

  function hasMeaningfulExposure(obj) {
    return Object.entries(obj || {}).some(([name, value]) => {
      const label = raw(name).trim();
      return label && label !== "未识别" && num(value) !== null;
    });
  }

  function assetObjectBars(obj) {
    const source = typeof obj === "string" ? exposureStringObject(obj) : obj;
    const rows = Object.entries(source || {})
      .map(([name, value]) => ({ name: raw(name).trim(), value: num(value) }))
      .filter((row) => row.name && row.name !== "未识别" && row.value !== null)
      .sort((a, b) => b.value - a.value);
    return rows.length ? `<div class="fund-asset-bars compact">${rows.slice(0, 12).map((row) => `
      <div class="fund-asset-bar">
        <span>${B.esc(row.name)}</span>
        <div><i style="width:${Math.max(1, Math.min(100, row.value)).toFixed(2)}%"></i></div>
        <strong>${B.pct(row.value)}</strong>
      </div>
    `).join("")}</div>` : '<div class="empty">暂无穿透暴露</div>';
  }

  function buildProfileSections(fundData, enrichment, economicSnapshot) {
    const profile = enrichment?.profile || {};
    const rawSources = [economicSnapshot || {}, profile.dictionary || {}, profile.info || {}, profile.navSummary || {}, profile.publicSnapshot || {}, profile.fundF10 || {}, profile.fofSnapshot || {}, profile.fofF10 || {}, fundData];
    const businessBenchmark = {
      "基准风险资产权重": dataFromSources("基准风险资产权重", rawSources),
      "基准风险资产权重_百分比": dataFromSources("基准风险资产权重_百分比", rawSources),
      "基准风险资产权重说明": dataFromSources("基准风险资产权重说明", rawSources) || dataFromSources("基准风险资产口径说明", rawSources),
    };
    const sources = [businessBenchmark, ...rawSources];
    const topicTags = (profile.dictionary || {}).主题标签 || (profile.info || {}).主题标签 || [];
    return `<section class="panel fund-profile-panel">
      <div class="panel-head">
        <div>
          <h2>基金基础信息</h2>
          <p class="desc">按业务用途分组展示，避免把基金识别、分类、净值覆盖和投顾持仓影响混在同一张表里。</p>
        </div>
      </div>
      ${infoSection("基础识别", ["基金代码", "基金名称", "标准基金名称", "基金公司", "基金经理", "基金类型", "基金状态"], sources)}
      ${infoSection("标准分类", ["天天基金大类", "天天基金二级分类", "二级分类", "标准资产大类", "标准资产细类", "经济资产大类", "经济资产细类", "投顾资产分类桶", "主动被动标签", "市场地域标签", "跟踪指数", "跟踪指数_名称推断"], sources)}
      ${infoSection("基准与业绩口径", ["基准风险资产权重", "基准风险资产权重_百分比", "基准风险资产权重说明", "非权益比较轨道", "正式可比池", "可比池样本资格", "可比池说明", "基准结构类型", "业绩比较基准", "基准映射置信度", "基准权益权重_百分比", "基准债券权重_百分比", "基准货币权重_百分比", "基准商品权重_百分比", "基准另类权重_百分比", "基准未知权重_百分比", "基准互斥权重合计_百分比", "基准港股权益权重_百分比", "基准海外权益权重_百分比", "FOF公开分类", "FOF基准细分分类", "F10基金类型", "F10成立日期", "上半年收益率_百分比", "今年以来收益率_百分比", "近1月收益率_百分比", "近3月收益率_百分比", "近6月收益率_百分比", "近1年收益率_百分比", "上半年最大回撤_百分比", "近1年最大回撤_百分比", "上半年年化波动率_百分比", "近1年年化波动率_百分比", "收益数据状态", "风险数据状态"], sources)}
      ${themeTagsHtml(topicTags)}
      ${infoSection("经济暴露口径", ["基金分类来源", "基金分类依据", "基金穿透报告期", "基金穿透覆盖状态", "是否估算分类", "经济资产暴露", "经济行业暴露", "经济主题标签", "经济暴露报告期", "穿透方法", "经济暴露置信度", "经济暴露质量状态"], sources)}
      ${infoSection("原始季报审计口径", ["原始资产暴露", "原始行业暴露", "资产暴露", "行业暴露", "行业主题", "行业大类", "权益行业主题", "权益行业大类", "研报大类资产", "研报A股行业"], sources)}
      ${infoSection("净值覆盖", ["净值口径", "是否货币基金", "历史起始日期", "历史结束日期", "历史记录数", "最新净值", "最新净值日期", "最新单位净值", "最新累计净值", "最新日收益率_百分比", "最新每万份收益", "最新七日年化收益率_百分比"], sources)}
      ${infoSection("投顾持仓影响", ["广发基金产品", "总权重", "广发策略权重", "非广发策略权重", "持仓策略数", "中位权重", "区间收益率", "增持策略数", "减持策略数"], sources)}
    </section>`;
  }

  async function loadEnrichment(fundData) {
    const code = raw(fundData[codeField]).trim();
    if (!code) return null;
    window.__BASIC_DATA__ = window.__BASIC_DATA__ || {};
    window.__BASIC_DATA__.fundEnrichmentDetails = window.__BASIC_DATA__.fundEnrichmentDetails || {};
    if (window.__BASIC_DATA__.fundEnrichmentDetails[code]) return window.__BASIC_DATA__.fundEnrichmentDetails[code];
    const manifestVersion = raw(window.__BASIC_DATA__.fundEnrichmentManifest?.generatedAt || "");
    const versionQuery = manifestVersion ? `${"?v="}${encodeURIComponent(manifestVersion)}` : "";
    const basePath = window.MinimalPublish?.detailPath
      ? window.MinimalPublish.detailPath("fund_details", code)
      : `./data/fund_details/${encodeURIComponent(code)}.js`;
    const src = `${basePath}${versionQuery}`;
    try {
      await B.loadScript(src);
    } catch (error) {
      console.warn(error);
      return null;
    }
    return window.__BASIC_DATA__.fundEnrichmentDetails[code] || null;
  }

  async function renderPage() {
    const fundData = matched.data;
    const fundIndex = matched.index;
    root.innerHTML = `<section class="panel"><div class="empty">基金详情加载中...</div></section>`;
    const enrichment = await loadEnrichment(fundData);
    const fundHoldings = holdings
      .filter((row) => Number(row?.[0]) === fundIndex)
      .map((row) => toObject(holdingFields, row))
      .sort((a, b) => Number(b.期末持仓比例 || 0) - Number(a.期末持仓比例 || 0));
    const fundMonthly = monthly
      .filter((row) => Number(row?.[0]) === fundIndex)
      .map((row) => toObject(monthlyFields, row))
      .sort((a, b) => String(b[monthlyFields[1]] || "").localeCompare(String(a[monthlyFields[1]] || "")));
    const holdingDisplayFields = pickFields(holdingFields, [
      "策略名称", "投顾机构", "渠道", "是否广发策略", "风险等级", "业务分类",
      "研报产品类型", "市场地域", "天天当前对客展示", "基金分类来源", "基金穿透报告期",
      "初持仓比例", "期末持仓比例", "权重变化", "区间收益率", "近1年"
    ]);
    const monthlyDisplayFields = monthlyFields.slice(1);
    const heroMetrics = pickFields(fundFields, ["总权重", "持仓策略数", "中位权重", "区间收益率", "增持策略数", "减持策略数"]);
    document.title = `${fundData[nameField] || "基金详情"}｜基金详情`;
    const profile = enrichment?.profile || {};
    const merged = mergeNonEmpty(fundData, profile.navSummary, profile.info, profile.dictionary, fundEconomicSnapshot);
    root.innerHTML = `
      <section class="panel hero-panel fund-hero-panel">
        <div class="fund-hero">
          <div>
            <p class="eyebrow">底层基金详情</p>
            <h1>${B.esc(fundData[nameField] || "未命名基金")}</h1>
            <p class="desc">${B.esc(fundData[codeField] || "未披露代码")}｜${B.esc(merged.基金公司 || "未披露基金公司")}｜${B.esc(merged.基金类型 || "未披露类型")}</p>
            <div class="fund-chip-row">
              ${["基金类型", "二级分类", "经济资产大类", "经济资产细类", "标准资产大类", "标准资产细类", "研报大类资产", "基金穿透报告期"].filter((field) => nonEmpty(merged[field])).map((field) => `<span>${B.label(field)} ${valueHtml(field, merged[field])}</span>`).join("")}
            </div>
          </div>
          <div class="fund-hero-metrics">${heroMetrics.map((field) => `<div class="hero-kpi ${B.toneClass(field, fundData[field])}"><span>${B.label(field)}</span><strong>${valueHtml(field, fundData[field])}</strong></div>`).join("")}</div>
        </div>
        ${enrichment ? "" : '<div class="hero-support"><div class="empty">尚未找到该基金的增强详情包。请运行 scripts/构建基金详情增强数据.py 或每日更新 BAT。</div></div>'}
      </section>
      ${buildProfileSections(fundData, enrichment, fundEconomicSnapshot)}
      ${fundEntitySection(merged, enrichment)}
      ${classificationSnapshotSection(fundData, enrichment, fundEconomicSnapshot)}
      ${navSection(enrichment)}
      ${assetSection(enrichment)}
      ${holdingReportsSection(enrichment)}
      <section class="panel fund-table-panel">
        <div class="panel-head">
          <div>
            <h2>持仓策略</h2>
            <p class="desc">按期末持仓比例排序，共 ${fundHoldings.length.toLocaleString("zh-CN")} 条策略持仓记录；策略名称可点击进入策略详情页。</p>
          </div>
        </div>
        ${table(holdingDisplayFields, fundHoldings, holdingValueHtml)}
      </section>
      <section class="panel fund-table-panel">
        <div class="panel-head">
          <div>
            <h2>月度调仓</h2>
            <p class="desc">展示该基金在策略调仓中的净增配、加仓和减仓权重。</p>
          </div>
        </div>
        ${table(monthlyDisplayFields, fundMonthly.slice(0, 80))}
      </section>
    `;
    wireFundNavChart(enrichment);
  }

  renderPage();
})();
