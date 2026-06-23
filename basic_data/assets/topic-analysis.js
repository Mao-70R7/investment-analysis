(() => {
  const B = window.BasicData || {};
  const root = B.byId ? B.byId("topicAnalysisPage") : document.getElementById("topicAnalysisPage");
  if (!root) return;

  const pageConfig = window.__BASIC_TOPIC_ANALYSIS_PAGE__ || {};
  const manifest = window.__BASIC_TOPIC_ANALYSIS_MANIFEST__ || manifestFromCombinedPack(window.__BASIC_TOPIC_ANALYSIS_PACK__ || {});
  const esc = B.esc || ((value) => String(value ?? "").replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;").replaceAll("'", "&#39;"));
  const pct = (value, digits = 2) => Number.isFinite(Number(value)) ? `${Number(value).toFixed(digits)}%` : "未披露";
  const signedPct = (value, digits = 2) => {
    if (!Number.isFinite(Number(value))) return '<span class="value-muted">未披露</span>';
    const number = Number(value);
    const cls = number > 0 ? "ret-pos" : number < 0 ? "ret-neg" : "ret-zero";
    return `<span class="${cls}">${number.toFixed(digits)}%</span>`;
  };
  const fmt = (value, digits = 2) => Number.isFinite(Number(value)) ? Number(value).toLocaleString("zh-CN", { maximumFractionDigits: digits }) : "未披露";
  const label = (name) => B.label ? B.label(name) : esc(name);
  const valueHtml = (field, value) => B.valueHtml ? B.valueHtml(field, value) : esc(value ?? "未披露");

  const lineColors = {
    "筛选策略等权走势": "#d92d20",
    "入选策略等权净值": "#d92d20",
    "中证TMT": "#7c3aed",
    "中证全指信息": "#0f766e",
    "TMT150": "#6941c6",
    "科创100": "#b7791f",
    "创业板指": "#c11574",
    "纳斯达克100": "#175cd3",
    "标普500": "#2563eb",
    "恒生指数": "#1570ef",
    "中证新能源": "#16a34a",
    "中证医药卫生": "#c026d3",
    "中证内地消费主题": "#ea580c",
    "中证红利": "#b7791f",
    "上证红利": "#9a3412",
    "上海黄金Au99.99": "#ca8a04",
    "中证短债": "#475467",
    "中证军工": "#7c2d12",
  };
  const selectedPalette = ["#d92d20", "#1570ef", "#7c3aed", "#0f766e", "#b7791f", "#c11574", "#175cd3", "#9a3412", "#2f6f4e", "#6941c6"];
  const fallbackLineColors = ["#475467", "#c11574", "#9a3412", "#2f6f4e", "#344054", "#175cd3"];
  const themeCache = new Map();
  const loadedScriptUrls = new Set();

  if (Array.isArray(window.__BASIC_TOPIC_ANALYSIS_PACK__?.themes)) {
    window.__BASIC_TOPIC_ANALYSIS_PACK__.themes.forEach((theme) => {
      if (theme?.id) themeCache.set(theme.id, theme);
    });
  }

  const topicState = {
    themeId: "",
    strategyId: "__equal__",
    benchmarkNames: null,
    tableSortField: "筛选暴露值",
    tableSortDir: "desc",
    lastTrendHoverData: null,
    exposureMetric: "meanOrPeak",
    exposureThreshold: 20,
    query: "",
    groupFilter: "__all__",
    loadingThemeId: "",
  };

  const exposureMetricOptions = [
    { key: "meanOrPeak", label: "均值或峰值暴露", desc: "最近一年均值或峰值任一达到阈值即入选。" },
    { key: "current", label: "当前暴露", desc: "只看最新持仓中的主题仓位。" },
    { key: "mean", label: "近一年均值暴露", desc: "看最近一年时间加权平均主题仓位。" },
    { key: "peak", label: "近一年峰值暴露", desc: "看最近一年调仓或当前快照中达到过的最高主题仓位。" },
  ];

  function manifestFromCombinedPack(pack) {
    const themes = (pack.themes || []).map((theme) => ({
      id: theme.id,
      name: theme.name,
      group: theme.group || "其他",
      rootEntityKey: theme.rootEntityKey,
      description: theme.description,
      grain: theme.grain,
      dedicated: theme.dedicated,
      defaultThreshold: theme.defaultThreshold,
      summary: theme.summary || {},
      script: "",
    }));
    return {
      version: pack.version,
      generatedAt: pack.generatedAt,
      dataUpdatedTo: pack.dataUpdatedTo,
      window: pack.window,
      themes,
      skippedThemes: pack.skippedThemes || [],
    };
  }

  function strategyUrl(row) {
    const id = row?.统一策略ID || row?.策略ID || row?.id;
    return `./strategy.html?id=${encodeURIComponent(id || "")}`;
  }

  function fundUrl(fund) {
    const code = fund?.code || fund?.基金代码;
    const name = fund?.name || fund?.基金名称;
    if (code) return `./fund.html?code=${encodeURIComponent(code)}`;
    return `./fund.html?name=${encodeURIComponent(name || "")}`;
  }

  function strategyLink(row, className = "") {
    return `<a class="link ${className}" href="${strategyUrl(row)}">${esc(row?.策略名称 || row?.统一策略ID || "未命名策略")}</a>`;
  }

  function fundLink(fund) {
    const name = fund?.name || fund?.基金名称 || fund?.code || fund?.基金代码 || "未命名基金";
    const code = fund?.code || fund?.基金代码;
    const weight = Number.isFinite(Number(fund?.weight)) ? ` ${pct(fund.weight)}` : "";
    return `<a class="topic-fund-chip" href="${fundUrl(fund)}" title="${esc(fund?.hits || fund?.命中依据 || "")}"><strong>${esc(name)}</strong>${code ? `<span>${esc(code)}</span>` : ""}${weight ? `<em>${weight}</em>` : ""}</a>`;
  }

  function groupBy(rows, key) {
    return (rows || []).reduce((acc, row) => {
      const k = row[key] || "未分组";
      (acc[k] ||= []).push(row);
      return acc;
    }, {});
  }

  function activeMeta() {
    return (manifest.themes || []).find((item) => item.id === topicState.themeId) || (manifest.themes || [])[0] || null;
  }

  function summaryNumber(meta, keys) {
    const summary = meta?.summary || {};
    for (const key of keys) {
      const value = Number(summary[key]);
      if (Number.isFinite(value)) return value;
    }
    return 0;
  }

  function availableThemeGroups() {
    return [...new Set((manifest.themes || []).map((item) => item.group || "其他"))].sort((a, b) => a.localeCompare(b, "zh-CN"));
  }

  function filteredThemeMetas() {
    const query = String(topicState.query || "").trim().toLowerCase();
    const group = topicState.groupFilter || "__all__";
    return (manifest.themes || []).filter((item) => {
      if (group !== "__all__" && (item.group || "其他") !== group) return false;
      if (!query) return true;
      return [item.name, item.group, item.description, item.grain, item.rootEntityKey].some((value) => String(value || "").toLowerCase().includes(query));
    });
  }

  function defaultThemeId() {
    const fromUrl = new URLSearchParams(window.location.search).get("theme");
    if (fromUrl && (manifest.themes || []).some((item) => item.id === fromUrl)) return fromUrl;
    if (pageConfig.themeId && (manifest.themes || []).some((item) => item.id === pageConfig.themeId)) return pageConfig.themeId;
    const nonDedicated = (manifest.themes || []).find((item) => !item.dedicated);
    return nonDedicated?.id || manifest.themes?.[0]?.id || "";
  }

  function resetThemeState(meta) {
    topicState.strategyId = "__equal__";
    topicState.benchmarkNames = null;
    topicState.tableSortField = "筛选暴露值";
    topicState.tableSortDir = "desc";
    topicState.exposureMetric = "meanOrPeak";
    topicState.exposureThreshold = Number(meta?.defaultThreshold) || 20;
  }

  function exposureMetricOption() {
    return exposureMetricOptions.find((item) => item.key === topicState.exposureMetric) || exposureMetricOptions[0];
  }

  function exposureValue(row, metric = topicState.exposureMetric) {
    const mean = Number(row?.主题均值暴露 ?? row?.AI核心均值暴露) || 0;
    const peak = Number(row?.主题峰值暴露 ?? row?.AI核心峰值暴露) || 0;
    const current = Number(row?.当前主题暴露 ?? row?.当前AI核心暴露) || 0;
    if (metric === "current") return current;
    if (metric === "mean") return mean;
    if (metric === "peak") return peak;
    return Math.max(mean, peak);
  }

  function themeFunds(row) {
    return row?.主要主题基金 || row?.主要AI核心基金 || [];
  }

  function selectedByExposure(theme) {
    const threshold = Number(topicState.exposureThreshold);
    return (theme.points || []).filter((row) => exposureValue(row) >= threshold).sort((a, b) => {
      const diff = exposureValue(b) - exposureValue(a);
      return diff
        || (Number(b.主题均值暴露 ?? b.AI核心均值暴露) || 0) - (Number(a.主题均值暴露 ?? a.AI核心均值暴露) || 0)
        || (Number(b.近1年收益) || 0) - (Number(a.近1年收益) || 0);
    });
  }

  function buildDynamicSummary(theme, selected) {
    const fundCodes = new Set();
    selected.forEach((row) => themeFunds(row).forEach((fund) => {
      const code = fund?.code || fund?.基金代码 || fund?.name || fund?.基金名称;
      if (code) fundCodes.add(code);
    }));
    const threshold = Number(topicState.exposureThreshold);
    return {
      ...(theme.summary || {}),
      入选策略数: selected.length,
      均值达标策略数: (theme.points || []).filter((row) => (Number(row.主题均值暴露 ?? row.AI核心均值暴露) || 0) >= threshold).length,
      峰值达标策略数: (theme.points || []).filter((row) => (Number(row.主题峰值暴露 ?? row.AI核心峰值暴露) || 0) >= threshold).length,
      当前达标策略数: (theme.points || []).filter((row) => (Number(row.当前主题暴露 ?? row.当前AI核心暴露) || 0) >= threshold).length,
      主题基金数: fundCodes.size,
    };
  }

  function themeView(theme) {
    const selected = selectedByExposure(theme);
    const selectedIds = new Set(selected.map((row) => row.统一策略ID));
    if (topicState.strategyId !== "__equal__" && !selectedIds.has(topicState.strategyId)) {
      topicState.strategyId = "__equal__";
    }
    return {
      ...theme,
      selected,
      summary: buildDynamicSummary(theme, selected),
      activeThreshold: topicState.exposureThreshold,
      activeExposureMetric: exposureMetricOption(),
    };
  }

  function loadScript(src) {
    if (loadedScriptUrls.has(src)) return Promise.resolve();
    return new Promise((resolve, reject) => {
      const script = document.createElement("script");
      script.src = src;
      script.dataset.topicSrc = src;
      script.onload = () => {
        script.dataset.loaded = "1";
        loadedScriptUrls.add(src);
        resolve();
      };
      script.onerror = () => reject(new Error(`主题数据加载失败：${src}`));
      document.head.appendChild(script);
    });
  }

  async function loadTheme(themeId) {
    if (themeCache.has(themeId)) return themeCache.get(themeId);
    const meta = (manifest.themes || []).find((item) => item.id === themeId);
    if (!meta) throw new Error("未找到主题配置");
    if (!meta.script) throw new Error("主题数据包路径缺失");
    const suffix = manifest.generatedAt ? "?" + "v=" + encodeURIComponent(manifest.generatedAt) : "";
    await loadScript(`${meta.script}${suffix}`);
    const loaded = window.__BASIC_TOPIC_ANALYSIS_THEME_PACKS__?.[themeId];
    if (!loaded) throw new Error("主题数据包未写入页面");
    themeCache.set(themeId, loaded);
    return loaded;
  }

  function pathFromPoints(points) {
    return points.map((point, index) => `${index ? "L" : "M"}${point.x.toFixed(2)},${point.y.toFixed(2)}`).join(" ");
  }

  function seriesColor(name, index = 0) {
    if (/基金池等权参考/.test(name)) return "#1570ef";
    return lineColors[name] || fallbackLineColors[index % fallbackLineColors.length];
  }

  function niceTicks(min, max, count = 6) {
    if (!Number.isFinite(min) || !Number.isFinite(max)) return [0, 25, 50, 75, 100];
    if (min === max) return [min - 1, min, min + 1];
    const span = Math.max(max - min, 1e-6);
    const rawStep = span / Math.max(count - 1, 1);
    const power = 10 ** Math.floor(Math.log10(rawStep));
    const normalized = rawStep / power;
    const step = (normalized <= 1 ? 1 : normalized <= 2 ? 2 : normalized <= 5 ? 5 : 10) * power;
    const start = Math.floor(min / step) * step;
    const end = Math.ceil(max / step) * step;
    const ticks = [];
    for (let value = start; value <= end + step / 2; value += step) ticks.push(Number(value.toFixed(6)));
    return ticks.length >= 3 ? ticks : [start, (start + end) / 2, end];
  }

  function timeTicks(xMin, xMax, count = 7) {
    if (!Number.isFinite(xMin) || !Number.isFinite(xMax) || xMin === xMax) return [xMin].filter(Number.isFinite);
    return Array.from({ length: count }, (_, i) => xMin + ((xMax - xMin) * i) / Math.max(count - 1, 1));
  }

  function benchmarkNames(theme) {
    return (theme.benchmarks || []).map((item) => item.name).filter(Boolean);
  }

  function ensureTrendState(theme) {
    if (!topicState.benchmarkNames) topicState.benchmarkNames = new Set(benchmarkNames(theme));
  }

  function buildTrendRows(theme) {
    ensureTrendState(theme);
    const rows = [];
    const strategyId = topicState.strategyId || "__equal__";
    const selectedIds = new Set((theme.selected || []).map((row) => row.统一策略ID));
    (theme.trend || []).forEach((row) => {
      if (row.类型 === "基金池") rows.push(row);
      else if (row.类型 === "参考指数" && topicState.benchmarkNames.has(row.系列)) rows.push(row);
    });
    if (strategyId === "__equal__" && selectedIds.size) rows.push(...makeEqualTrendRows(theme, selectedIds));
    if (strategyId !== "__equal__") {
      (theme.strategyTrend || []).forEach((row) => {
        if (row.统一策略ID === strategyId) rows.push({ ...row, 系列: `策略：${row.系列}` });
      });
    }
    return rows;
  }

  function makeEqualTrendRows(theme, selectedIds) {
    const byDate = {};
    (theme.strategyTrend || []).forEach((row) => {
      if (!selectedIds.has(row.统一策略ID)) return;
      const point = Number(row.指数点位);
      if (!row.日期 || !Number.isFinite(point)) return;
      (byDate[row.日期] ||= []).push(point);
    });
    return Object.entries(byDate)
      .sort(([a], [b]) => a.localeCompare(b))
      .map(([date, values]) => ({
        日期: date,
        系列: "筛选策略等权走势",
        指数点位: values.reduce((sum, value) => sum + value, 0) / Math.max(values.length, 1),
        类型: "策略组合",
      }));
  }

  function renderLineChart(rows) {
    const grouped = groupBy(rows, "系列");
    const seriesEntries = Object.entries(grouped).map(([name, items]) => {
      const rawRows = items.map((row) => ({ t: Date.parse(row.日期), point: Number(row.指数点位), date: row.日期 }))
        .filter((d) => Number.isFinite(d.t) && Number.isFinite(d.point))
        .sort((a, b) => a.t - b.t);
      if (!rawRows.length) return null;
      const base = rawRows[0].point;
      const points = rawRows.map((d) => {
        const value = base ? (d.point / base - 1) * 100 : d.point - base;
        return { ...d, value };
      }).filter((d) => Number.isFinite(d.value));
      return points.length ? [name, points] : null;
    }).filter(Boolean);
    const all = seriesEntries.flatMap(([name, points]) => points.map((point) => ({ name, ...point })));
    if (!all.length) return '<div class="empty">暂无可绘制的走势数据</div>';

    const width = 1060;
    const height = 390;
    const margin = { top: 28, right: 118, bottom: 58, left: 72 };
    const xMin = Math.min(...all.map((d) => d.t));
    const xMax = Math.max(...all.map((d) => d.t));
    const rawYMin = Math.min(...all.map((d) => d.value), 0);
    const rawYMax = Math.max(...all.map((d) => d.value), 0);
    const yPad = Math.max((rawYMax - rawYMin) * 0.12, 0.6);
    const yTicks = niceTicks(rawYMin - yPad, rawYMax + yPad, 6);
    const yMin = yTicks[0];
    const yMax = yTicks[yTicks.length - 1];
    const x = (t) => margin.left + ((t - xMin) / Math.max(xMax - xMin, 1)) * (width - margin.left - margin.right);
    const y = (value) => height - margin.bottom - ((value - yMin) / Math.max(yMax - yMin, 1)) * (height - margin.top - margin.bottom);
    const xTicks = timeTicks(xMin, xMax, 7);
    const seriesSvg = seriesEntries.map(([name, items], seriesIndex) => {
      const points = items.map((d) => ({ ...d, x: x(d.t), yPos: y(d.value) }));
      if (!points.length) return "";
      const last = points[points.length - 1];
      const color = seriesColor(name, seriesIndex);
      return `
        <path class="topic-line-path" d="${pathFromPoints(points.map((d) => ({ x: d.x, y: d.yPos })))}" fill="none" stroke="${color}" stroke-width="3.4" stroke-linecap="round" stroke-linejoin="round" />
        ${points.map((point) => `<circle class="topic-line-dot" cx="${point.x.toFixed(2)}" cy="${point.yPos.toFixed(2)}" r="2.2" fill="${color}" opacity=".34"><title>${esc(name)} ${esc(point.date)} ${point.value.toFixed(2)}%</title></circle>`).join("")}
        <circle cx="${last.x.toFixed(2)}" cy="${last.yPos.toFixed(2)}" r="3.8" fill="${color}"><title>${esc(name)} ${esc(last.date)} ${last.value.toFixed(2)}%</title></circle>
        <text x="${Math.min(last.x + 8, width - 112).toFixed(2)}" y="${last.yPos.toFixed(2)}" fill="${color}" font-size="12" font-weight="750">${esc(name)}</text>`;
    }).join("");
    const hoverDates = [...new Set(all.map((d) => d.date))].sort();
    topicState.lastTrendHoverData = {
      hoverDates,
      xOf: hoverDates.map((date) => ({ date, t: Date.parse(date), x: x(Date.parse(date)) })).filter((item) => Number.isFinite(item.t)),
      series: seriesEntries.map(([name, points], index) => ({
        name,
        color: seriesColor(name, index),
        points: points.map((point) => ({ date: point.date, t: point.t, value: point.value, x: x(point.t), y: y(point.value) })),
      })),
    };
    const axis = `
      ${yTicks.map((tick) => `<line x1="${margin.left}" x2="${width - margin.right}" y1="${y(tick).toFixed(2)}" y2="${y(tick).toFixed(2)}" stroke="#edf2f7" /><text x="${margin.left - 12}" y="${(y(tick) + 4).toFixed(2)}" text-anchor="end" class="topic-axis-text">${fmt(tick, 1)}%</text>`).join("")}
      ${xTicks.map((tick) => {
        const d = new Date(tick);
        const text = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
        return `<line x1="${x(tick).toFixed(2)}" x2="${x(tick).toFixed(2)}" y1="${margin.top}" y2="${height - margin.bottom}" stroke="#f2f4f7" /><text x="${x(tick).toFixed(2)}" y="${height - 26}" text-anchor="middle" class="topic-axis-text">${text}</text>`;
      }).join("")}
      <line x1="${margin.left}" x2="${width - margin.right}" y1="${height - margin.bottom}" y2="${height - margin.bottom}" stroke="#98a2b3" />
      <line x1="${margin.left}" x2="${margin.left}" y1="${margin.top}" y2="${height - margin.bottom}" stroke="#98a2b3" />
      <line x1="${margin.left}" x2="${width - margin.right}" y1="${y(0).toFixed(2)}" y2="${y(0).toFixed(2)}" stroke="#98a2b3" stroke-dasharray="4 4" />
      <text x="${width / 2}" y="${height - 7}" text-anchor="middle" class="topic-axis-title">日期</text>
      <text x="20" y="${height / 2}" text-anchor="middle" transform="rotate(-90 20 ${height / 2})" class="topic-axis-title">相对起点收益率</text>`;
    const legend = seriesEntries.map(([name], index) => `<span><i style="background:${seriesColor(name, index)}"></i>${esc(name)}</span>`).join("");
    return `
      <div class="topic-chart topic-line-chart">
        <div class="topic-chart-legend">${legend}</div>
        <svg viewBox="0 0 ${width} ${height}" role="img" aria-label="主题策略与参考指数走势">${axis}${seriesSvg}<g class="topic-hover-layer" visibility="hidden"><line class="topic-hover-line" x1="0" y1="${margin.top}" x2="0" y2="${height - margin.bottom}" /><g class="topic-hover-points"></g></g><rect class="topic-hover-capture" x="${margin.left}" y="${margin.top}" width="${width - margin.left - margin.right}" height="${height - margin.top - margin.bottom}" /></svg>
        <div class="topic-chart-tooltip" hidden></div>
      </div>`;
  }

  function renderBenchmarkNote(theme) {
    const benchmarks = theme.benchmarks || [];
    const text = benchmarks.length
      ? benchmarks.map((item) => `<li><b>${esc(item.name)}：</b>${esc(item.reason || item.role || "")}</li>`).join("")
      : "<li>当前主题没有足够贴合的本地参考指数，页面仅展示基金池和策略走势。</li>";
    return `
      <div class="topic-benchmark-note">
        <strong>参考指数说明</strong>
        <p>参考指数只用于观察市场背景，不参与主题筛选。主题筛选只看底层基金和策略持仓的标准实体证据。</p>
        <ul>${text}</ul>
      </div>`;
  }

  function renderTrendControls(theme) {
    ensureTrendState(theme);
    const selected = theme.selected || [];
    const strategyOptions = [
      `<option value="__equal__"${topicState.strategyId === "__equal__" ? " selected" : ""}>筛选策略等权组合（${selected.length}只）</option>`,
      ...selected.map((row) => `<option value="${esc(row.统一策略ID)}"${topicState.strategyId === row.统一策略ID ? " selected" : ""}>${esc(row.策略名称)} - ${esc(row.投顾机构 || "")}</option>`),
    ].join("");
    const benchmarkChecks = (theme.benchmarks || []).map((item) => {
      const checked = topicState.benchmarkNames.has(item.name) ? " checked" : "";
      return `<label class="topic-check"><input type="checkbox" data-topic-benchmark="${esc(item.name)}"${checked}> <span>${esc(item.name)}</span></label>`;
    }).join("");
    return `
      <div class="topic-trend-controls">
        <label class="topic-select-label">策略走势
          <select id="topicStrategyTrendSelect" class="control">${strategyOptions}</select>
        </label>
        <div class="topic-benchmark-checks" aria-label="参考指数">${benchmarkChecks}</div>
      </div>`;
  }

  function renderTrendPanel(theme) {
    return `
      <section class="panel">
        <div class="panel-head">
          <div>
            <h2>${esc(theme.name)}参考业绩走势</h2>
            <p class="desc">所有曲线统一展示相对观察起点的收益率；可切换单只入选策略，并与主题基金池和可用参考指数一起观察。</p>
          </div>
        </div>
        ${renderTrendControls(theme)}
        <div id="topicTrendChart">${renderLineChart(buildTrendRows(theme))}</div>
        ${renderBenchmarkNote(theme)}
      </section>`;
  }

  function bindTrendHover() {
    const chart = root.querySelector("#topicTrendChart .topic-line-chart");
    const data = topicState.lastTrendHoverData;
    if (!chart || !data?.xOf?.length || !data?.series?.length) return;
    const svg = chart.querySelector("svg");
    const tip = chart.querySelector(".topic-chart-tooltip");
    const hoverLayer = svg?.querySelector(".topic-hover-layer");
    const hoverLine = svg?.querySelector(".topic-hover-line");
    const hoverPoints = svg?.querySelector(".topic-hover-points");
    if (!svg || !tip || !hoverLayer || !hoverLine || !hoverPoints) return;

    svg.addEventListener("mousemove", (event) => {
      const rect = svg.getBoundingClientRect();
      const xScale = 1060 / rect.width;
      const svgX = (event.clientX - rect.left) * xScale;
      const selected = data.xOf.reduce((best, item) => Math.abs(item.x - svgX) < Math.abs(best.x - svgX) ? item : best, data.xOf[0]);
      const rows = data.series.map((series) => {
        const point = series.points.reduce((best, item) => Math.abs(item.t - selected.t) < Math.abs(best.t - selected.t) ? item : best, series.points[0]);
        return { ...point, name: series.name, color: series.color };
      }).filter((row) => row && Number.isFinite(row.value));
      hoverLayer.setAttribute("visibility", "visible");
      hoverLine.setAttribute("x1", selected.x.toFixed(2));
      hoverLine.setAttribute("x2", selected.x.toFixed(2));
      hoverPoints.innerHTML = rows.map((row) => `<circle class="topic-hover-point" cx="${selected.x.toFixed(2)}" cy="${row.y.toFixed(2)}" r="4" fill="#fff" stroke="${row.color}" stroke-width="2"></circle>`).join("");
      tip.innerHTML = `<strong>${esc(selected.date)} 相对起点收益率</strong>${rows.map((row) => `<div class="topic-tip-row"><span><i style="background:${row.color}"></i>${esc(row.name)}</span><b class="${row.value >= 0 ? "ret-pos" : "ret-neg"}">${row.value.toFixed(2)}%</b></div>`).join("")}`;
      tip.hidden = false;
      const hostRect = chart.getBoundingClientRect();
      const localX = event.clientX - hostRect.left;
      const localY = event.clientY - hostRect.top;
      tip.style.left = `${Math.min(Math.max(8, localX + 14), Math.max(8, chart.clientWidth - 286))}px`;
      tip.style.top = `${Math.max(42, localY - 18)}px`;
    });
    svg.addEventListener("mouseleave", () => {
      tip.hidden = true;
      hoverLayer.setAttribute("visibility", "hidden");
    });
  }

  function refreshTrendChart(theme) {
    const slot = root.querySelector("#topicTrendChart");
    if (slot) {
      slot.innerHTML = renderLineChart(buildTrendRows(theme));
      bindTrendHover();
    }
  }

  function bindTrendControls(theme) {
    const select = root.querySelector("#topicStrategyTrendSelect");
    if (select) {
      select.addEventListener("change", () => {
        topicState.strategyId = select.value || "__equal__";
        refreshTrendChart(theme);
      });
    }
    root.querySelectorAll("[data-topic-benchmark]").forEach((node) => {
      node.addEventListener("change", () => {
        ensureTrendState(theme);
        const name = node.dataset.topicBenchmark;
        if (node.checked) topicState.benchmarkNames.add(name);
        else topicState.benchmarkNames.delete(name);
        refreshTrendChart(theme);
      });
    });
  }

  function renderScatter(theme) {
    const rows = theme.points || [];
    if (!rows.length) return '<div class="empty">暂无策略点阵数据</div>';
    const selectedIds = new Set((theme.selected || []).map((row) => row.统一策略ID));
    const metric = theme.activeExposureMetric || exposureMetricOption();
    const threshold = Number(theme.activeThreshold ?? topicState.exposureThreshold);
    const colorById = {};
    (theme.selected || []).forEach((row, index) => {
      colorById[row.统一策略ID] = selectedPalette[index % selectedPalette.length];
    });
    const width = 1040;
    const height = 390;
    const margin = { top: 28, right: 28, bottom: 58, left: 74 };
    const xMax = Math.max(threshold + 10, 60, ...rows.map((row) => exposureValue(row))) + 5;
    const yValues = rows.map((row) => Number(row.近1年收益)).filter(Number.isFinite);
    const yTickValues = niceTicks(Math.min(-10, ...yValues) - 3, Math.max(20, ...yValues) + 3, 6);
    const yMin = yTickValues[0];
    const yMax = yTickValues[yTickValues.length - 1];
    const x = (value) => margin.left + (Math.max(0, Math.min(value, xMax)) / xMax) * (width - margin.left - margin.right);
    const y = (value) => height - margin.bottom - ((value - yMin) / Math.max(yMax - yMin, 1)) * (height - margin.top - margin.bottom);
    const xTicks = niceTicks(0, Math.min(100, xMax), 5).filter((tick) => tick <= xMax);
    const axis = `
      ${yTickValues.map((tick) => `<line x1="${margin.left}" x2="${width - margin.right}" y1="${y(tick).toFixed(2)}" y2="${y(tick).toFixed(2)}" stroke="#edf2f7" /><text x="${margin.left - 12}" y="${(y(tick) + 4).toFixed(2)}" text-anchor="end" class="topic-axis-text">${tick.toFixed(1)}%</text>`).join("")}
      ${xTicks.map((tick) => `<line x1="${x(tick).toFixed(2)}" x2="${x(tick).toFixed(2)}" y1="${margin.top}" y2="${height - margin.bottom}" stroke="#f2f4f7" /><text x="${x(tick).toFixed(2)}" y="${height - 26}" text-anchor="middle" class="topic-axis-text">${tick}%</text>`).join("")}
      <line x1="${x(threshold).toFixed(2)}" x2="${x(threshold).toFixed(2)}" y1="${margin.top}" y2="${height - margin.bottom}" stroke="#d92d20" stroke-dasharray="5 5" />
      <line x1="${margin.left}" x2="${width - margin.right}" y1="${y(0).toFixed(2)}" y2="${y(0).toFixed(2)}" stroke="#98a2b3" stroke-dasharray="4 4" />
      <line x1="${margin.left}" x2="${width - margin.right}" y1="${height - margin.bottom}" y2="${height - margin.bottom}" stroke="#98a2b3" />
      <line x1="${margin.left}" x2="${margin.left}" y1="${margin.top}" y2="${height - margin.bottom}" stroke="#98a2b3" />
      <text x="${x(threshold) + 6}" y="${margin.top + 14}" font-size="12" fill="#b42318" font-weight="750">${threshold}%暴露阈值</text>
      <text x="${width / 2}" y="${height - 7}" text-anchor="middle" class="topic-axis-title">${esc(metric.label)}</text>
      <text x="22" y="${height / 2}" text-anchor="middle" transform="rotate(-90 22 ${height / 2})" class="topic-axis-title">近1年收益</text>`;
    const circles = rows.map((row, index) => {
      const selected = selectedIds.has(row.统一策略ID);
      const peak = Number(row.主题峰值暴露 ?? row.AI核心峰值暴露) || 0;
      const radius = selected ? 5.8 + Math.min(peak, 100) / 30 : 3.6;
      const color = selected ? colorById[row.统一策略ID] : "#cbd5e1";
      const cx = x(exposureValue(row));
      const cy = y(Number(row.近1年收益) || 0);
      return `<circle class="topic-scatter-point ${selected ? "is-selected" : "is-background"}" data-topic-point="${index}" cx="${cx.toFixed(2)}" cy="${cy.toFixed(2)}" r="${radius.toFixed(2)}" fill="${color}"><title>${esc(row.策略名称)} ${esc(metric.label)}${pct(exposureValue(row))} 近1年${pct(row.近1年收益)}</title></circle>`;
    }).join("");
    const labels = (theme.selected || []).slice(0, 20).map((row) => {
      const cx = x(exposureValue(row));
      const cy = y(Number(row.近1年收益) || 0);
      return `<text class="topic-scatter-label" x="${Math.min(cx + 8, width - 130).toFixed(2)}" y="${(cy - 7).toFixed(2)}" fill="${colorById[row.统一策略ID] || "#d92d20"}">${esc(row.策略名称)}</text>`;
    }).join("");
    return `
      <div class="topic-scatter-wrap">
        <svg viewBox="0 0 ${width} ${height}" role="img" aria-label="策略点阵图：主题暴露与近1年收益">${axis}${circles}${labels}</svg>
        <div class="topic-scatter-legend">
          <span><i class="is-selected"></i>入选策略</span>
          <span><i class="is-background"></i>全市场可比样本</span>
          <span>横轴按${esc(metric.label)}筛选，点大小按峰值暴露调整</span>
        </div>
        <div id="topicScatterDetail" class="topic-point-detail">${renderPointDetail(theme.selected?.[0] || rows[0])}</div>
      </div>`;
  }

  function renderPointDetail(row) {
    if (!row) return '<div class="empty">点击点阵中的策略查看明细</div>';
    const funds = themeFunds(row).slice(0, 6).map(fundLink).join("");
    const metric = exposureMetricOption();
    return `
      <strong>${strategyLink(row)}</strong>
      <div class="topic-point-kpis">
        <span>${label("投顾机构")}<b>${esc(row.投顾机构 || "未披露")}</b></span>
        <span>${label("近1年收益")}<b>${signedPct(row.近1年收益)}</b></span>
        <span>${label("最大回撤")}<b>${valueHtml("最大回撤", row.最大回撤)}</b></span>
        <span>${esc(metric.label)}<b>${pct(exposureValue(row))}</b></span>
        <span>${label("主题均值暴露")}<b>${pct(row.主题均值暴露 ?? row.AI核心均值暴露)}</b></span>
        <span>${label("主题峰值暴露")}<b>${pct(row.主题峰值暴露 ?? row.AI核心峰值暴露)}</b></span>
        <span>${label("当前主题暴露")}<b>${pct(row.当前主题暴露 ?? row.当前AI核心暴露)}</b></span>
        <span>${label("峰值日期")}<b>${esc(row.峰值日期 || "未披露")}</b></span>
      </div>
      <div class="topic-fund-chip-row">${funds || '<span class="value-muted">暂无主题基金证据</span>'}</div>`;
  }

  const selectedTableFields = ["策略名称", "投顾机构", "风险等级", "筛选暴露值", "近1年收益", "最大回撤", "主题均值暴露", "主题峰值暴露", "当前主题暴露", "峰值日期", "主要主题基金"];
  const selectedNumericFields = new Set(["筛选暴露值", "近1年收益", "最大回撤", "主题均值暴露", "主题峰值暴露", "当前主题暴露"]);

  function selectedTableValue(row, field) {
    if (field === "主要主题基金") return themeFunds(row).map((fund) => `${fund.name || fund.基金名称 || ""}${fund.code || fund.基金代码 || ""}${Number(fund.weight || 0).toFixed(4)}`).join("|");
    if (field === "峰值日期") {
      const t = Date.parse(row[field]);
      return Number.isFinite(t) ? t : null;
    }
    if (field === "筛选暴露值") return exposureValue(row);
    if (field === "主题均值暴露") return Number(row.主题均值暴露 ?? row.AI核心均值暴露);
    if (field === "主题峰值暴露") return Number(row.主题峰值暴露 ?? row.AI核心峰值暴露);
    if (field === "当前主题暴露") return Number(row.当前主题暴露 ?? row.当前AI核心暴露);
    if (selectedNumericFields.has(field)) {
      const value = Number(row[field]);
      return Number.isFinite(value) ? value : null;
    }
    return String(row[field] ?? "");
  }

  function compareSelectedRows(a, b, field) {
    const av = selectedTableValue(a, field);
    const bv = selectedTableValue(b, field);
    if (av === null && bv === null) return 0;
    if (av === null) return 1;
    if (bv === null) return -1;
    if (typeof av === "number" && typeof bv === "number") return av - bv;
    return String(av).localeCompare(String(bv), "zh-CN", { numeric: true, sensitivity: "base" });
  }

  function sortedSelectedRows(rows) {
    const field = selectedTableFields.includes(topicState.tableSortField) ? topicState.tableSortField : "筛选暴露值";
    const dir = topicState.tableSortDir === "asc" ? 1 : -1;
    return [...rows].sort((a, b) => compareSelectedRows(a, b, field) * dir || compareSelectedRows(a, b, "策略名称"));
  }

  function selectedSortHead(field) {
    const active = topicState.tableSortField === field;
    const arrow = active ? (topicState.tableSortDir === "asc" ? "↑" : "↓") : "↕";
    return `<span class="sort-head topic-sort-head ${active ? "is-active" : ""}" role="button" tabindex="0" data-topic-sort="${esc(field)}">${esc(field)}<span class="sort-arrow">${arrow}</span></span>`;
  }

  function bindSelectedTableSort(theme) {
    const slot = root.querySelector("#topicSelectedTableSlot");
    if (!slot) return;
    slot.querySelectorAll("[data-topic-sort]").forEach((node) => {
      const activate = () => {
        const field = node.dataset.topicSort;
        if (!selectedTableFields.includes(field)) return;
        if (topicState.tableSortField === field) topicState.tableSortDir = topicState.tableSortDir === "asc" ? "desc" : "asc";
        else {
          topicState.tableSortField = field;
          topicState.tableSortDir = selectedNumericFields.has(field) || field === "峰值日期" ? "desc" : "asc";
        }
        slot.innerHTML = renderSelectedTable(theme);
        bindSelectedTableSort(theme);
      };
      node.addEventListener("click", activate);
      node.addEventListener("keydown", (event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          activate();
        }
      });
    });
  }

  function renderSelectedTable(theme) {
    const rows = sortedSelectedRows(theme.selected || []);
    const body = rows.length ? rows.map((row) => `
      <tr>
        <td>${strategyLink(row)}</td>
        <td>${esc(row.投顾机构 || "未披露")}</td>
        <td>${esc(row.风险等级 || "未披露")}</td>
        <td>${pct(exposureValue(row))}</td>
        <td>${signedPct(row.近1年收益)}</td>
        <td>${valueHtml("最大回撤", row.最大回撤)}</td>
        <td>${pct(row.主题均值暴露 ?? row.AI核心均值暴露)}</td>
        <td>${pct(row.主题峰值暴露 ?? row.AI核心峰值暴露)}</td>
        <td>${pct(row.当前主题暴露 ?? row.当前AI核心暴露)}</td>
        <td>${esc(row.峰值日期 || "未披露")}</td>
        <td><div class="topic-fund-chip-row">${themeFunds(row).slice(0, 5).map(fundLink).join("") || '<span class="value-muted">暂无基金证据</span>'}</div></td>
      </tr>`).join("") : '<tr><td colspan="11"><div class="empty">暂无入选策略</div></td></tr>';
    return `
      <div class="table-wrap topic-selected-table">
        <table>
          <thead><tr>${selectedTableFields.map((field) => `<th>${selectedSortHead(field)}</th>`).join("")}</tr></thead>
          <tbody>${body}</tbody>
        </table>
      </div>`;
  }

  function renderLogic(theme) {
    const logic = theme.logic || {};
    return `
      <section class="panel topic-method-panel">
        <div class="panel-head">
          <div>
            <h2>${esc(theme.name)}筛选口径</h2>
            <p class="desc">${esc(theme.description || "按标准实体索引识别底层基金，并在策略持仓层面聚合主题暴露。")}</p>
          </div>
          <span class="topic-threshold">${esc(theme.threshold || "")}</span>
        </div>
        <div class="topic-logic-grid">
          <div><strong>怎么判断基金</strong><p>${esc(logic.基金识别 || "")}</p></div>
          <div><strong>怎么计算仓位</strong><p>${esc(logic.暴露计算 || "")}</p></div>
          <div><strong>怎么避免误判</strong><p>${esc(logic.排除宽口径 || "")}</p></div>
        </div>
      </section>`;
  }

  function renderExposureControls(theme) {
    const metric = theme.activeExposureMetric || exposureMetricOption();
    const threshold = Number(theme.activeThreshold ?? topicState.exposureThreshold);
    const options = exposureMetricOptions.map((item) => `<option value="${esc(item.key)}"${item.key === topicState.exposureMetric ? " selected" : ""}>${esc(item.label)}</option>`).join("");
    return `
      <section class="panel topic-filter-panel">
        <div class="panel-head">
          <div>
            <h2>主题暴露筛选</h2>
            <p class="desc">${esc(metric.desc)}当前阈值 ${threshold}%，入选 ${fmt(theme.selected?.length || 0, 0)} 只策略。</p>
          </div>
          <span class="topic-threshold">${esc(metric.label)} >= ${threshold}%</span>
        </div>
        <div class="topic-filter-controls">
          <label>筛选指标
            <select id="topicExposureMetric" class="control">${options}</select>
          </label>
          <label>暴露阈值
            <div class="topic-threshold-control">
              <input id="topicExposureThresholdRange" type="range" min="0" max="100" step="1" value="${threshold}">
              <input id="topicExposureThresholdInput" class="control" type="number" min="0" max="100" step="1" value="${threshold}">
              <span>%</span>
            </div>
          </label>
          <div class="topic-filter-counts">
            <span>当前达标 <b>${fmt(theme.summary?.当前达标策略数 || 0, 0)}</b></span>
            <span>均值达标 <b>${fmt(theme.summary?.均值达标策略数 || 0, 0)}</b></span>
            <span>峰值达标 <b>${fmt(theme.summary?.峰值达标策略数 || 0, 0)}</b></span>
          </div>
        </div>
      </section>`;
  }

  function renderThemeSelector() {
    if (pageConfig.lockTheme) return "";
    const themes = filteredThemeMetas();
    const grouped = groupBy(themes, "group");
    const maxSelected = Math.max(1, ...themes.map((item) => summaryNumber(item, ["入选策略数"])));
    const groupOptions = [
      `<option value="__all__"${topicState.groupFilter === "__all__" ? " selected" : ""}>全部主题组</option>`,
      ...availableThemeGroups().map((group) => `<option value="${esc(group)}"${topicState.groupFilter === group ? " selected" : ""}>${esc(group)}</option>`),
    ].join("");
    const groups = Object.entries(grouped).map(([group, items]) => `
      <div class="topic-selector-group">
        <strong>${esc(group)}</strong>
        <div class="topic-chip-row">
          ${items.map((item) => {
            const active = item.id === topicState.themeId;
            const fundCount = summaryNumber(item, ["标准实体主题基金数", "标准实体AI基金数"]);
            const selectedCount = summaryNumber(item, ["入选策略数"]);
            const pointCount = summaryNumber(item, ["点阵样本数"]);
            const width = Math.max(4, Math.min(100, selectedCount / maxSelected * 100));
            return `<button class="topic-theme-chip ${active ? "is-active" : ""}" type="button" data-theme-id="${esc(item.id)}">
              <span>${esc(item.name)}</span>
              <em>${esc(item.grain || "标准实体")} · 阈值${fmt(item.defaultThreshold, 0)}%</em>
              <em>${fmt(fundCount, 0)}只基金 · ${fmt(selectedCount, 0)}只入选策略 · ${fmt(pointCount, 0)}样本</em>
              <i class="topic-chip-bar"><b style="width:${width}%"></b></i>
            </button>`;
          }).join("")}
        </div>
      </div>`).join("");
    const skipped = (manifest.skippedThemes || []).length
      ? `<details class="topic-skipped"><summary>暂不开放的低样本主题 ${fmt(manifest.skippedThemes.length, 0)} 个</summary>${manifest.skippedThemes.map((item) => `<p>${esc(item.name)}：${esc(item.reason)}</p>`).join("")}</details>`
      : "";
    return `
      <section class="panel topic-selector-panel">
        <div class="panel-head">
          <div>
            <h2>主题库 / 多主题筛选</h2>
            <p class="desc">按主题组和关键词筛选多个标准主题；本区只读轻量清单，点击主题后才加载明细分析数据。</p>
          </div>
          <div class="topic-selector-controls">
            <select id="topicGroupFilter" class="control">${groupOptions}</select>
            <input id="topicThemeSearch" class="control" type="search" placeholder="搜索主题、资产、行业" value="${esc(topicState.query)}">
          </div>
        </div>
        ${groups || '<div class="empty">没有匹配的主题</div>'}
        ${skipped}
      </section>`;
  }

  function bindThemeSelector() {
    root.querySelectorAll("[data-theme-id]").forEach((node) => {
      node.addEventListener("click", () => {
        const id = node.dataset.themeId;
        if (!id || id === topicState.themeId) return;
        const meta = (manifest.themes || []).find((item) => item.id === id);
        topicState.themeId = id;
        resetThemeState(meta);
        loadAndRenderTheme(id);
      });
    });
    const search = root.querySelector("#topicThemeSearch");
    const groupFilter = root.querySelector("#topicGroupFilter");
    if (groupFilter) {
      groupFilter.addEventListener("change", () => {
        topicState.groupFilter = groupFilter.value || "__all__";
        const slot = root.querySelector("#topicSelectorSlot");
        if (slot) {
          slot.innerHTML = renderThemeSelector();
          bindThemeSelector();
        }
      });
    }
    if (search) {
      search.addEventListener("input", () => {
        topicState.query = search.value || "";
        const slot = root.querySelector("#topicSelectorSlot");
        if (slot) {
          slot.innerHTML = renderThemeSelector();
          bindThemeSelector();
        }
      });
    }
  }

  function bindExposureControls(theme) {
    const metricSelect = root.querySelector("#topicExposureMetric");
    const range = root.querySelector("#topicExposureThresholdRange");
    const input = root.querySelector("#topicExposureThresholdInput");
    const setThreshold = (value) => {
      const numeric = Math.max(0, Math.min(100, Math.round(Number(value) || 0)));
      topicState.exposureThreshold = numeric;
      render(theme);
    };
    if (metricSelect) {
      metricSelect.addEventListener("change", () => {
        topicState.exposureMetric = metricSelect.value || "meanOrPeak";
        topicState.tableSortField = "筛选暴露值";
        topicState.tableSortDir = "desc";
        render(theme);
      });
    }
    if (range) range.addEventListener("change", () => setThreshold(range.value));
    if (input) input.addEventListener("change", () => setThreshold(input.value));
  }

  function render(theme) {
    const view = themeView(theme);
    const meta = activeMeta() || {};
    const pageTitle = pageConfig.title || (pageConfig.lockTheme ? `${theme.name}主题分析` : "主题分析");
    root.innerHTML = `
      <div class="page-title">
        <div>
          <h1>${esc(pageTitle)}</h1>
          <p class="desc">${pageConfig.lockTheme ? "独立跟踪AI核心主题暴露、相关策略表现和可回溯基金证据。" : `当前主题：${esc(theme.name)}。可从主题库切换到其他标准实体主题。`}</p>
        </div>
        <div class="title-pills">
          <span class="pill">数据更新至 ${esc(manifest.dataUpdatedTo || "-")}</span>
          <span class="pill">窗口 ${esc(manifest.window?.start || "-")} 至 ${esc(manifest.window?.end || "-")}</span>
          <span class="pill">${esc(meta.grain || theme.grain || "标准实体")}</span>
        </div>
      </div>
      <div id="topicSelectorSlot">${renderThemeSelector()}</div>
      <div class="grid topic-kpi-grid">
        ${B.metric ? B.metric("入选策略数", view.summary.入选策略数 ?? 0, `${view.activeExposureMetric.label}>=${view.activeThreshold}%`) : ""}
        ${B.metric ? B.metric("均值达标策略数", view.summary.均值达标策略数 ?? 0, `近一年均值>=${view.activeThreshold}%`) : ""}
        ${B.metric ? B.metric("峰值达标策略数", view.summary.峰值达标策略数 ?? 0, `近一年峰值>=${view.activeThreshold}%`) : ""}
        ${B.metric ? B.metric("主题基金数", view.summary.主题基金数 ?? view.summary.AI核心基金数 ?? 0, "当前筛选结果中的证据基金去重") : ""}
      </div>
      ${renderExposureControls(view)}
      ${renderLogic(view)}
      ${renderTrendPanel(view)}
      <section class="panel">
        <div class="panel-head">
          <div>
            <h2>策略点阵图</h2>
            <p class="desc">横轴为主题暴露，纵轴为近1年收益；点击点位查看策略和命中基金证据。</p>
          </div>
        </div>
        ${renderScatter(view)}
      </section>
      <section class="panel">
        <div class="panel-head">
          <div>
            <h2>筛选策略主题暴露说明</h2>
            <p class="desc">当前口径：${esc(view.activeExposureMetric.label)}达到${esc(view.activeThreshold)}%；基金证据展示对筛选结果贡献较高的底层持仓。</p>
          </div>
        </div>
        <div id="topicSelectedTableSlot">${renderSelectedTable(view)}</div>
      </section>`;
    root.querySelectorAll("[data-topic-point]").forEach((node) => {
      node.addEventListener("click", () => {
        const row = view.points?.[Number(node.dataset.topicPoint)];
        const detail = root.querySelector("#topicScatterDetail");
        if (detail) detail.innerHTML = renderPointDetail(row);
      });
    });
    bindThemeSelector();
    bindExposureControls(theme);
    bindTrendControls(view);
    bindTrendHover();
    bindSelectedTableSort(view);
  }

  function renderLoading(meta) {
    root.innerHTML = `
      <section class="panel">
        <div class="panel-head">
          <div>
            <h2>${esc(meta?.name || "主题分析")}</h2>
            <p class="desc">正在加载该主题的数据包。</p>
          </div>
        </div>
        <div class="empty">正在加载 ${esc(meta?.name || "")} 数据...</div>
      </section>`;
  }

  async function loadAndRenderTheme(themeId) {
    const meta = (manifest.themes || []).find((item) => item.id === themeId);
    topicState.loadingThemeId = themeId;
    renderLoading(meta);
    try {
      const theme = await loadTheme(themeId);
      if (topicState.loadingThemeId !== themeId) return;
      render(theme);
    } catch (error) {
      root.innerHTML = `<section class="panel"><div class="empty">主题数据加载失败：${esc(error.message || error)}</div></section>`;
    }
  }

  if (!manifest.themes?.length) {
    root.innerHTML = '<section class="panel"><div class="empty">主题分析数据包未生成。请先运行 scripts/build_topic_analysis_pack.py。</div></section>';
    return;
  }

  topicState.themeId = defaultThemeId();
  resetThemeState(activeMeta());
  loadAndRenderTheme(topicState.themeId);
})();
