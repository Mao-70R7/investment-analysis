(() => {
  const B = window.BasicData || {};
  const pack = window.__MIXED_PERFORMANCE_SCATTER_PACK__;
  const root = document.getElementById("mixedPerformanceScatterPage");
  if (!root) return;

  const esc = B.esc || ((value) => String(value ?? "").replace(/[&<>"']/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch])));
  const intervals = pack?.meta?.intervals || ["上半年", "今年以来", "近1月", "近3月", "近6月", "近1年"];
  const bucketOrder = pack?.meta?.bucketOrder || ["L0", "L1", "L2", "L3", "L4", "L5", "L6", "L7", "L8", "L9", "L10"];
  const bucketLabels = pack?.meta?.bucketLabels || {};
  const rows = Array.isArray(pack?.rows) ? pack.rows : [];
  const riskMetrics = [
    { key: "maxDrawdown", label: "最大回撤", axis: "收益区间回撤", better: "越靠右回撤越小" },
    { key: "volatility", label: "年化波动率", axis: "收益区间波动率", better: "越靠左波动越小" },
  ];
  const state = {
    productType: "all",
    buckets: [],
    comparisonTracks: [],
    fundMainTypes: [],
    interval: "上半年",
    riskMetric: "maxDrawdown",
    gfOnly: false,
    search: "",
    advantageProductScope: "all",
    selectedId: "",
    tableSort: { key: "return", direction: "desc" },
    pageSize: 100,
    page: 1,
  };
  let searchTimer = null;
  const rankCache = new Map();
  const peerStatsCache = new Map();
  let restoreFilterSnapshot = null;
  let openMultiKey = "";

  if (!pack || !rows.length) {
    root.innerHTML = '<section class="panel"><div class="empty">未找到收益风险点阵数据包，请先运行页面数据包生成脚本。</div></section>';
    return;
  }

  const productCategories = {
    marketFund: { label: "全市场基金产品", fill: "#bbf7d0", stroke: "#15803d", className: "is-market-fund", legendClass: "mixed-market-fund-legend", drawOrder: 1, radius: 3.8 },
    marketStrategy: { label: "全市场投顾产品", fill: "#60a5fa", stroke: "#1d4ed8", className: "is-market-strategy", legendClass: "mixed-market-strategy-legend", drawOrder: 2, radius: 4.2 },
    gfFund: { label: "广发基金产品", fill: "#facc15", stroke: "#a16207", className: "is-gf-fund", legendClass: "mixed-gf-fund-legend", drawOrder: 3, radius: 5.2 },
    gfStrategy: { label: "广发投顾产品", fill: "#ef4444", stroke: "#991b1b", className: "is-gf-strategy", legendClass: "mixed-gf-strategy-legend", drawOrder: 4, radius: 5.8 },
  };
  const advantageStrengthDefinitions = {
    obvious: {
      title: "明显优势",
      subtitle: "同池或同产品类型排名靠前，且至少一个风险指标相对同池中位数更友好，适合重点跟进。",
      stars: 5,
      threshold: 80,
    },
    strong: {
      title: "较强优势",
      subtitle: "收益排名靠前，风险表现没有明显拖累，适合进入重点观察和场景化推荐池。",
      stars: 4,
      threshold: 65,
    },
    partial: {
      title: "部分优势",
      subtitle: "有清晰单点优势，但风险、样本规模或赛道适配仍需补充验证。",
      stars: 3,
      threshold: 50,
    },
    watch: {
      title: "观察优势",
      subtitle: "当前筛选条件下具备一定相对亮点，但不宜直接包装成强结论。",
      stars: 2,
      threshold: 0,
    },
  };
  let pendingScrollToScatter = false;

  function isStrategy(row) {
    return row?.productType === "投顾策略";
  }

  function productCategoryKey(row) {
    if (row?.isGuangfa && isStrategy(row)) return "gfStrategy";
    if (row?.isGuangfa) return "gfFund";
    return isStrategy(row) ? "marketStrategy" : "marketFund";
  }

  function productCategory(row) {
    return productCategories[productCategoryKey(row)] || productCategories.marketFund;
  }

  function matchesAdvantageProductScope(row) {
    if (state.advantageProductScope === "strategy") return isStrategy(row);
    if (state.advantageProductScope === "fund") return row?.productType === "公募基金";
    return true;
  }

  function advantageProductScopeLabel() {
    if (state.advantageProductScope === "strategy") return "广发投顾产品";
    if (state.advantageProductScope === "fund") return "广发基金产品";
    return "广发产品";
  }

  function renderAdvantageScopeControl(allRows) {
    const scopes = [
      { key: "all", label: "全部产品", count: allRows.length },
      { key: "strategy", label: "只看投顾", count: allRows.filter(isStrategy).length },
      { key: "fund", label: "只看基金", count: allRows.filter((row) => row.productType === "公募基金").length },
    ];
    return `<div class="mixed-advantage-scope" role="group" aria-label="广发优势产品范围">
      ${scopes.map((scope) => `<button type="button" data-advantage-scope="${esc(scope.key)}" class="${state.advantageProductScope === scope.key ? "is-active" : ""}">${esc(scope.label)}<span>${scope.count.toLocaleString("zh-CN")}</span></button>`).join("")}
    </div>`;
  }

  function resetPage() {
    state.page = 1;
  }

  function cloneValues(values) {
    return Array.isArray(values) ? [...values] : [];
  }

  function selectedValues(key) {
    return cloneValues(state[key]).filter(Boolean);
  }

  function selectedSet(key) {
    return new Set(selectedValues(key));
  }

  function matchesMulti(row, stateKey, field) {
    const selected = selectedSet(stateKey);
    if (!selected.size) return true;
    return selected.has(clean(row[field]));
  }

  function updateMultiSelection(stateKey, value, checked) {
    const current = selectedSet(stateKey);
    if (checked) current.add(value);
    else current.delete(value);
    state[stateKey] = Array.from(current);
  }

  function resetAllFilters() {
    state.productType = "all";
    state.buckets = [];
    state.comparisonTracks = [];
    state.fundMainTypes = [];
    state.interval = "上半年";
    state.riskMetric = "maxDrawdown";
    state.gfOnly = false;
    state.search = "";
    state.advantageProductScope = "all";
    state.selectedId = "";
    state.tableSort = { key: "return", direction: "desc" };
    restoreFilterSnapshot = null;
    openMultiKey = "";
    resetPage();
  }

  function filterSnapshot() {
    return {
      productType: state.productType,
      buckets: selectedValues("buckets"),
      comparisonTracks: selectedValues("comparisonTracks"),
      fundMainTypes: selectedValues("fundMainTypes"),
      interval: state.interval,
      riskMetric: state.riskMetric,
      gfOnly: state.gfOnly,
      search: state.search,
      advantageProductScope: state.advantageProductScope,
      selectedId: state.selectedId,
      tableSort: { ...state.tableSort },
      pageSize: state.pageSize,
      page: state.page,
    };
  }

  function restoreFilters() {
    if (!restoreFilterSnapshot) return;
    const snapshot = restoreFilterSnapshot;
    state.productType = snapshot.productType || "all";
    state.buckets = cloneValues(snapshot.buckets);
    state.comparisonTracks = cloneValues(snapshot.comparisonTracks);
    state.fundMainTypes = cloneValues(snapshot.fundMainTypes);
    state.interval = snapshot.interval || "上半年";
    state.riskMetric = snapshot.riskMetric || "maxDrawdown";
    state.gfOnly = Boolean(snapshot.gfOnly);
    state.search = snapshot.search || "";
    state.advantageProductScope = snapshot.advantageProductScope || "all";
    state.selectedId = snapshot.selectedId || "";
    state.tableSort = snapshot.tableSort ? { ...snapshot.tableSort } : { key: "return", direction: "desc" };
    state.pageSize = snapshot.pageSize || 100;
    state.page = snapshot.page || 1;
    restoreFilterSnapshot = null;
    openMultiKey = "";
  }

  function numeric(value) {
    if (value === null || value === undefined || value === "" || typeof value === "boolean") return null;
    const number = Number(value);
    return Number.isFinite(number) ? number : null;
  }

  function intervalData(row, interval = state.interval) {
    return row?.intervals?.[interval] || {};
  }

  function intervalReturn(row, interval = state.interval) {
    return numeric(intervalData(row, interval).return);
  }

  function currentReturn(row) {
    return intervalReturn(row);
  }

  function currentRisk(row) {
    return numeric(intervalData(row)[state.riskMetric]);
  }

  function hasCurrentMetrics(row) {
    return intervalComplete(row);
  }

  function intervalComplete(row, interval = state.interval) {
    const data = intervalData(row, interval);
    return numeric(data.return) !== null && numeric(data.maxDrawdown) !== null && numeric(data.volatility) !== null;
  }

  function rateClass(value) {
    const number = numeric(value);
    if (number === null) return "";
    if (number > 0) return "ret-pos";
    if (number < 0) return "ret-neg";
    return "ret-zero";
  }

  function formatRate(value, signed = false) {
    const number = numeric(value);
    if (number === null) return '<span class="small">--</span>';
    const scaled = number * 100;
    const sign = signed && scaled > 0 ? "+" : "";
    return `<span class="${rateClass(number)}">${sign}${scaled.toFixed(2)}%</span>`;
  }

  function formatRateText(value, signed = false) {
    const number = numeric(value);
    if (number === null) return "--";
    const scaled = number * 100;
    const sign = signed && scaled > 0 ? "+" : "";
    return `${sign}${scaled.toFixed(2)}%`;
  }

  function formatPp(value) {
    const number = numeric(value);
    if (number === null) return "--";
    return `${Math.abs(number * 100).toFixed(2)}pct`;
  }

  function formatNumber(value) {
    const number = numeric(value);
    return number === null ? "--" : number.toLocaleString("zh-CN");
  }

  function median(values) {
    const sorted = values.filter((value) => Number.isFinite(value)).sort((a, b) => a - b);
    if (!sorted.length) return null;
    const mid = Math.floor(sorted.length / 2);
    return sorted.length % 2 ? sorted[mid] : (sorted[mid - 1] + sorted[mid]) / 2;
  }

  function quantile(values, p) {
    const sorted = values.filter((value) => Number.isFinite(value)).sort((a, b) => a - b);
    if (!sorted.length) return null;
    const position = (sorted.length - 1) * p;
    const lower = Math.floor(position);
    const upper = Math.ceil(position);
    if (lower === upper) return sorted[lower];
    return sorted[lower] + (sorted[upper] - sorted[lower]) * (position - lower);
  }

  function clean(value) {
    return String(value ?? "").trim();
  }

  function rowKey(row) {
    return [row?.id, row?.code, row?.name, row?.productType].map(clean).join("\u0001");
  }

  function typePeerContext(row) {
    return [
      clean(row?.productType) || "未标识",
      clean(row?.bucket) || "未分档",
      clean(row?.comparisonTrack) || "未形成",
    ].join(" ");
  }

  function addRankGroup(groups, context, row) {
    const key = clean(context);
    if (!key) return;
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(row);
  }

  function buildRankMap(groups, interval) {
    const output = new Map();
    groups.forEach((groupRows, context) => {
      const sorted = [...groupRows].sort((a, b) => {
        const left = intervalReturn(a, interval);
        const right = intervalReturn(b, interval);
        return (right - left) || clean(a.name).localeCompare(clean(b.name), "zh-CN") || clean(a.id).localeCompare(clean(b.id), "zh-CN");
      });
      let rank = 0;
      let previousReturn = null;
      sorted.forEach((row, index) => {
        const ret = intervalReturn(row, interval);
        if (previousReturn === null || Math.abs(ret - previousReturn) > 1e-12) {
          rank = index + 1;
        }
        output.set(rowKey(row), { rank, total: sorted.length, context });
        previousReturn = ret;
      });
    });
    return output;
  }

  function rankingsForInterval(interval = state.interval) {
    if (rankCache.has(interval)) return rankCache.get(interval);
    const bucketGroups = new Map();
    const peerGroups = new Map();
    const typePeerGroups = new Map();
    rows.filter((row) => intervalReturn(row, interval) !== null).forEach((row) => {
      addRankGroup(bucketGroups, row.bucket, row);
      addRankGroup(peerGroups, row.formalPeerPool, row);
      addRankGroup(typePeerGroups, typePeerContext(row), row);
    });
    const rankings = {
      bucket: buildRankMap(bucketGroups, interval),
      peer: buildRankMap(peerGroups, interval),
      typePeer: buildRankMap(typePeerGroups, interval),
    };
    rankCache.set(interval, rankings);
    return rankings;
  }

  function rankInfo(row, interval = state.interval) {
    const rankings = rankingsForInterval(interval);
    const key = rowKey(row);
    return {
      bucket: rankings.bucket.get(key) || null,
      peer: rankings.peer.get(key) || null,
      typePeer: rankings.typePeer.get(key) || null,
    };
  }

  function formatRank(info) {
    if (!info || !Number.isFinite(info.rank) || !Number.isFinite(info.total)) return '<span class="small">不计算</span>';
    return `<span class="mixed-rank-value"><b>${esc(info.rank.toLocaleString("zh-CN"))}/${esc(info.total.toLocaleString("zh-CN"))}</b><small>（${esc(info.context)}）</small></span>`;
  }

  function rankText(info) {
    if (!info || !Number.isFinite(info.rank) || !Number.isFinite(info.total)) return "当前口径样本不足，暂不做硬性排名判断。";
    return `在${info.context}中收益排名第${info.rank.toLocaleString("zh-CN")}/${info.total.toLocaleString("zh-CN")}。`;
  }

  function peerStatsFor(pool, interval = state.interval) {
    const key = `${interval}\u0001${clean(pool)}`;
    if (!clean(pool)) return null;
    if (peerStatsCache.has(key)) return peerStatsCache.get(key);
    const peerRows = rows.filter((row) => row.formalPeerPool === pool && intervalComplete(row, interval));
    const result = {
      count: peerRows.length,
      returnMedian: median(peerRows.map((row) => numeric(intervalData(row, interval).return))),
      returnQ75: quantile(peerRows.map((row) => numeric(intervalData(row, interval).return)), 0.75),
      drawdownMedian: median(peerRows.map((row) => numeric(intervalData(row, interval).maxDrawdown))),
      drawdownQ75: quantile(peerRows.map((row) => numeric(intervalData(row, interval).maxDrawdown)), 0.75),
      volatilityMedian: median(peerRows.map((row) => numeric(intervalData(row, interval).volatility))),
      volatilityQ25: quantile(peerRows.map((row) => numeric(intervalData(row, interval).volatility)), 0.25),
    };
    peerStatsCache.set(key, result);
    return result;
  }

  function bucketScene(row) {
    const value = clean(row.bucket);
    const level = value.startsWith("L") ? Number(value.slice(1)) : null;
    if (!Number.isFinite(level)) return "未明确权益仓位";
    if (level === 0) return "低权益或非权益底仓";
    if (level <= 2) return "低权益稳健增强";
    if (level <= 5) return "均衡配置";
    if (level <= 7) return "权益增强配置";
    return "高权益进攻配置";
  }

  function trackScene(row) {
    const track = clean(row.comparisonTrack);
    if (track.includes("债券")) return "同池多以债券或固收底仓承接风险，客户关注点是收益增强能否覆盖回撤体验。";
    if (track.includes("货币")) return "同池更接近现金管理或低波替代，客户关注点是收益稳定性和流动性预期。";
    if (track.includes("商品")) return "同池受黄金、原油等商品价格驱动，客户关注点是组合分散和波动承受边界。";
    if (track.includes("另类")) return "同池偏另类资产，客户关注点是相关性、估值波动和持有周期。";
    if (track.includes("多资产")) return "同池强调多资产分散，客户关注点是不同资产之间的平衡和回撤控制。";
    if (track.includes("纯权益")) return "同池以权益弹性为主，客户关注点是上涨捕捉能力和下跌阶段的持有纪律。";
    return "同池资产结构较分散，客户沟通需要先说明比较边界。";
  }

  function valuesFor(field) {
    const set = new Set();
    rows.forEach((row) => {
      const value = clean(row[field]);
      if (value) set.add(value);
    });
    return Array.from(set).sort((a, b) => {
      if (field === "bucket") {
        const ia = bucketOrder.indexOf(a);
        const ib = bucketOrder.indexOf(b);
        return (ia < 0 ? 99 : ia) - (ib < 0 ? 99 : ib);
      }
      return a.localeCompare(b, "zh-CN");
    });
  }

  function optionHtml(values, current, allLabel, labels = {}) {
    const head = `<option value="all"${current === "all" ? " selected" : ""}>${esc(allLabel)}</option>`;
    return head + values.map((value) => `<option value="${esc(value)}"${value === current ? " selected" : ""}>${esc(labels[value] || value)}</option>`).join("");
  }

  function selectionSummary(stateKey, allLabel, labels = {}) {
    const selected = selectedValues(stateKey);
    if (!selected.length) return allLabel;
    if (selected.length <= 2) return selected.map((value) => labels[value] || value).join("、");
    return `${labels[selected[0]] || selected[0]} 等 ${selected.length} 项`;
  }

  function hasActiveFilters() {
    return state.productType !== "all"
      || selectedValues("buckets").length > 0
      || selectedValues("comparisonTracks").length > 0
      || selectedValues("fundMainTypes").length > 0
      || state.interval !== "上半年"
      || state.riskMetric !== "maxDrawdown"
      || state.gfOnly
      || Boolean(state.search.trim());
  }

  function multiControlHtml({ id, label, stateKey, values, allLabel, labels = {} }) {
    const selected = selectedSet(stateKey);
    return `<details class="mixed-multi-control" id="${esc(id)}"${openMultiKey === stateKey ? " open" : ""}>
      <summary><span>${esc(label)}</span><b>${esc(selectionSummary(stateKey, allLabel, labels))}</b></summary>
      <div class="mixed-multi-panel">
        <label class="mixed-multi-option is-all"><input type="checkbox" data-multi-all="${esc(stateKey)}"${selected.size ? "" : " checked"}> ${esc(allLabel)}</label>
        ${values.map((value) => `<label class="mixed-multi-option"><input type="checkbox" data-multi-filter="${esc(stateKey)}" data-multi-value="${esc(value)}"${selected.has(value) ? " checked" : ""}> ${esc(labels[value] || value)}</label>`).join("")}
      </div>
    </details>`;
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
      row.fundMainType,
      row.fundTypeTags,
      row.bucket,
      row.comparisonTrack,
      row.formalPeerPool,
      row.benchmark,
    ].join(" ").toLowerCase();
    return haystack.includes(needle);
  }

  function filteredRows({ requireCurrent = false } = {}) {
    return rows.filter((row) => {
      if (state.productType !== "all" && row.productType !== state.productType) return false;
      if (!matchesMulti(row, "buckets", "bucket")) return false;
      if (!matchesMulti(row, "comparisonTracks", "comparisonTrack")) return false;
      if (!matchesMulti(row, "fundMainTypes", "fundMainType")) return false;
      if (state.gfOnly && !row.isGuangfa) return false;
      if (!matchesSearch(row)) return false;
      if (requireCurrent && !hasCurrentMetrics(row)) return false;
      return true;
    });
  }

  function sortedRows() {
    return filteredRows().sort(compareRows);
  }

  function sortValue(row, key) {
    const data = intervalData(row);
    const ranks = rankInfo(row);
    if (key === "product") return clean(row.name) || clean(row.code) || clean(row.id);
    if (key === "productType") return `${clean(row.productType)} ${clean(row.fundMainType)} ${clean(row.fundTypeTags)}`;
    if (key === "institution") return clean(row.institution);
    if (key === "bucket") {
      const index = bucketOrder.indexOf(row.bucket);
      return index < 0 ? 999 : index;
    }
    if (key === "return") return currentReturn(row);
    if (key === "risk") return currentRisk(row);
    if (key === "drawdown") return numeric(data.maxDrawdown);
    if (key === "volatility") return numeric(data.volatility);
    if (key === "range") return clean(data.range);
    if (key === "bucketSource") return clean(row.bucketSource);
    if (key === "comparisonTrack") return clean(row.comparisonTrack);
    if (key === "formalPeerPool") return clean(row.formalPeerPool);
    if (key === "absoluteRank") return numeric(row.absoluteReturnRank);
    if (key === "bucketRank") return ranks.bucket?.rank ?? null;
    if (key === "peerRank") return ranks.peer?.rank ?? null;
    if (key === "typePeerRank") return ranks.typePeer?.rank ?? null;
    return "";
  }

  function compareValues(left, right, direction) {
    const factor = direction === "asc" ? 1 : -1;
    const leftMissing = left === null || left === undefined || left === "";
    const rightMissing = right === null || right === undefined || right === "";
    if (leftMissing && rightMissing) return 0;
    if (leftMissing) return 1;
    if (rightMissing) return -1;
    if (typeof left === "number" && typeof right === "number") {
      return (left - right) * factor;
    }
    return String(left).localeCompare(String(right), "zh-CN", { numeric: true }) * factor;
  }

  function compareRows(a, b) {
    const { key, direction } = state.tableSort;
    return compareValues(sortValue(a, key), sortValue(b, key), direction)
      || compareValues(currentReturn(a), currentReturn(b), "desc")
      || clean(a.name).localeCompare(clean(b.name), "zh-CN");
  }

  function selectedRow(list) {
    return list.find((row) => row.id === state.selectedId) || list[0] || null;
  }

  function metricCard(label, value, sub = "") {
    return `<section class="metric mixed-metric"><div>${esc(label)}</div><div class="metric-value">${esc(value)}</div>${sub ? `<div class="metric-sub">${esc(sub)}</div>` : ""}</section>`;
  }

  function renderMetrics(list, broadList) {
    const gfCount = list.filter((row) => row.isGuangfa).length;
    const strategyCount = list.filter((row) => isStrategy(row)).length;
    const fundCount = list.filter((row) => row.productType === "公募基金").length;
    const broadCount = broadList.length;
    const plottableCount = list.filter((row) => hasCurrentMetrics(row)).length;
    const bucketText = selectionSummary("buckets", "全部分档", bucketLabels);
    const trackText = selectionSummary("comparisonTracks", "全部轨道");
    const peerPoolCount = new Set(list.map((row) => clean(row.formalPeerPool)).filter(Boolean)).size;
    return `<section class="grid mixed-kpi-grid">
      ${metricCard("当前筛选产品", list.length.toLocaleString("zh-CN"), `${fundCount} 只基金 / ${strategyCount} 条投顾`)}
      ${metricCard("当前可绘制点阵", plottableCount.toLocaleString("zh-CN"), `缺指标产品保留在列表，以 -- 展示`)}
      ${metricCard("当前筛选口径", `${bucketText} / ${trackText}`, `${peerPoolCount.toLocaleString("zh-CN")} 个正式可比池，数据包 ${broadCount.toLocaleString("zh-CN")} 条`)}
      ${metricCard("广发产品", gfCount.toLocaleString("zh-CN"), "列表按字段排序，点阵按广发基金/投顾分色置顶")}
      ${metricCard("数据截止", pack.meta?.asOfDate || "2026-06-30", `${state.interval} / ${currentRiskMetric().label}`)}
    </section>`;
  }

  function currentRiskMetric() {
    return riskMetrics.find((metric) => metric.key === state.riskMetric) || riskMetrics[0];
  }

  function renderControls() {
    const bucketValues = valuesFor("bucket");
    const trackValues = valuesFor("comparisonTrack");
    const fundTypeValues = valuesFor("fundMainType");
    return `<section class="panel mixed-control-panel">
      <div class="panel-head">
        <div>
          <h1>投顾策略 + 公募基金全市场产品排名</h1>
          <p class="desc">仅过滤未分档产品；缺收益、回撤或波动的产品保留在列表并显示 --，点阵只绘制当前区间坐标完整的产品。</p>
        </div>
        <button class="mixed-reset-filters" type="button" data-reset-filters${hasActiveFilters() ? "" : " disabled"}>重置条件</button>
      </div>
      <div class="mixed-filter-grid">
        <label>产品范围<select id="mixedProductType" class="control">
          <option value="all"${state.productType === "all" ? " selected" : ""}>基金 + 投顾</option>
          <option value="公募基金"${state.productType === "公募基金" ? " selected" : ""}>公募基金</option>
          <option value="投顾策略"${state.productType === "投顾策略" ? " selected" : ""}>投顾策略</option>
        </select></label>
        ${multiControlHtml({ id: "mixedBucket", label: "基准权益分档", stateKey: "buckets", values: bucketValues, allLabel: "全部分档", labels: bucketLabels })}
        ${multiControlHtml({ id: "mixedComparisonTrack", label: "比较轨道", stateKey: "comparisonTracks", values: trackValues, allLabel: "全部轨道" })}
        ${multiControlHtml({ id: "mixedFundType", label: "基金类型", stateKey: "fundMainTypes", values: fundTypeValues, allLabel: "全部类型" })}
        <label>收益区间<select id="mixedInterval" class="control">${intervals.map((item) => `<option value="${esc(item)}"${item === state.interval ? " selected" : ""}>${esc(item)}</option>`).join("")}</select></label>
        <label>风险指标<select id="mixedRiskMetric" class="control">${riskMetrics.map((item) => `<option value="${esc(item.key)}"${item.key === state.riskMetric ? " selected" : ""}>${esc(item.label)}</option>`).join("")}</select></label>
        <label>搜索<input id="mixedSearch" class="control" type="search" value="${esc(state.search)}" placeholder="名称、代码、机构、基准"></label>
        <label class="mixed-check"><input id="mixedGfOnly" type="checkbox"${state.gfOnly ? " checked" : ""}> 只看广发</label>
      </div>
    </section>`;
  }

  function niceDomain(values, padRatio = 0.08) {
    const finite = values.filter((value) => Number.isFinite(value));
    if (!finite.length) return [0, 1];
    let min = Math.min(...finite);
    let max = Math.max(...finite);
    if (min === max) {
      const pad = Math.abs(min || 1) * 0.1;
      min -= pad;
      max += pad;
    } else {
      const pad = (max - min) * padRatio;
      min -= pad;
      max += pad;
    }
    return [min, max];
  }

  function scale(value, domain, range) {
    const [d0, d1] = domain;
    const [r0, r1] = range;
    if (d1 === d0) return (r0 + r1) / 2;
    return r0 + ((value - d0) / (d1 - d0)) * (r1 - r0);
  }

  function axisTicks(domain, count = 5) {
    const [min, max] = domain;
    if (!Number.isFinite(min) || !Number.isFinite(max)) return [];
    const ticks = [];
    for (let index = 0; index < count; index += 1) {
      ticks.push(min + ((max - min) * index) / (count - 1));
    }
    return ticks;
  }

  function renderLegend(list) {
    return Object.entries(productCategories).map(([key, category]) => {
      const count = list.filter((row) => productCategoryKey(row) === key).length;
      return `<span class="${category.legendClass}"><i></i>${esc(category.label)} ${count.toLocaleString("zh-CN")}</span>`;
    }).join("");
  }

  function renderScatter(list) {
    const riskMetric = currentRiskMetric();
    const scopeText = `${selectionSummary("buckets", "全部分档", bucketLabels)} / ${selectionSummary("comparisonTracks", "全部轨道")}`;
    const plottableRows = list.filter((row) => currentRisk(row) !== null && currentReturn(row) !== null);
    const width = 920;
    const height = 520;
    const margin = { top: 28, right: 34, bottom: 70, left: 78 };
    const plotW = width - margin.left - margin.right;
    const plotH = height - margin.top - margin.bottom;
    const xValues = plottableRows.map(currentRisk).filter((value) => value !== null);
    const yValues = plottableRows.map(currentReturn).filter((value) => value !== null);
    const xDomain = niceDomain(xValues);
    const yDomain = niceDomain(yValues);
    const yZero = yDomain[0] <= 0 && yDomain[1] >= 0 ? scale(0, yDomain, [height - margin.bottom, margin.top]) : null;
    const xTicks = axisTicks(xDomain, 6);
    const yTicks = axisTicks(yDomain, 6);
    const pointRows = [...plottableRows].sort((a, b) => (productCategory(a).drawOrder - productCategory(b).drawOrder) || clean(a.name).localeCompare(clean(b.name), "zh-CN"));
    const selected = selectedRow(list);
    const peerRows = selected?.formalPeerPool
      ? rows.filter((row) => row.formalPeerPool === selected.formalPeerPool && hasCurrentMetrics(row))
      : [];
    const peerReturns = peerRows.map(currentReturn).filter((value) => value !== null).sort((a, b) => a - b);
    const peerRisks = peerRows.map(currentRisk).filter((value) => value !== null).sort((a, b) => a - b);
    const median = (values) => values.length ? (values.length % 2 ? values[(values.length - 1) / 2] : (values[values.length / 2 - 1] + values[values.length / 2]) / 2) : null;
    const peerReturnMedian = peerRows.length >= 5 ? median(peerReturns) : null;
    const peerRiskMedian = peerRows.length >= 5 ? median(peerRisks) : null;
    const peerLines = peerReturnMedian === null || peerRiskMedian === null ? "" : (() => {
      const y = scale(peerReturnMedian, yDomain, [height - margin.bottom, margin.top]);
      const x = scale(peerRiskMedian, xDomain, [margin.left, width - margin.right]);
      return `<line class="mixed-peer-line" x1="${margin.left}" x2="${width - margin.right}" y1="${y.toFixed(1)}" y2="${y.toFixed(1)}"></line>
        <line class="mixed-peer-line" x1="${x.toFixed(1)}" x2="${x.toFixed(1)}" y1="${margin.top}" y2="${height - margin.bottom}"></line>
        <text class="mixed-peer-label" x="${margin.left + 8}" y="${(y - 6).toFixed(1)}">${esc(selected.formalPeerPool)}收益中位数</text>
        <text class="mixed-peer-label" x="${(x + 6).toFixed(1)}" y="${margin.top + 16}">风险中位数</text>`;
    })();
    const points = pointRows.map((row, index) => {
      const xValue = currentRisk(row);
      const yValue = currentReturn(row);
      if (xValue === null || yValue === null) return "";
      const cx = scale(xValue, xDomain, [margin.left, width - margin.right]);
      const cy = scale(yValue, yDomain, [height - margin.bottom, margin.top]);
      const category = productCategory(row);
      const cls = ["mixed-scatter-dot", category.className, row.isGuangfa ? "is-guangfa" : "", row.id === state.selectedId ? "is-selected" : ""].filter(Boolean).join(" ");
      return `<circle class="${cls}" data-id="${esc(row.id)}" data-index="${index}" cx="${cx.toFixed(1)}" cy="${cy.toFixed(1)}" r="${category.radius}" fill="${category.fill}" stroke="${category.stroke}"><title>${esc(category.label)} · ${esc(row.name)} ${formatRateText(yValue, true)} / ${formatRateText(xValue)}</title></circle>`;
    }).join("");

    return `<section class="panel mixed-scatter-panel">
      <div class="ai-scatter-head">
        <div>
          <strong>收益-风险点阵</strong>
          <span>X轴：${esc(riskMetric.axis)}；Y轴：${esc(state.interval)}收益率。${esc(riskMetric.better)}</span>
        </div>
        <div class="ai-scatter-controls"><span class="pill">${esc(scopeText)}</span><span class="pill">当前绘制 ${plottableRows.length.toLocaleString("zh-CN")} / 筛选 ${list.length.toLocaleString("zh-CN")} 个产品</span></div>
      </div>
      <div class="ai-scatter-legend mixed-scatter-legend">${renderLegend(plottableRows)}</div>
      <div class="mixed-scatter-layout">
        <div class="ai-scatter-wrap">
          <svg class="ai-scatter-svg mixed-scatter-svg" viewBox="0 0 ${width} ${height}" role="img" aria-label="收益风险点阵">
            <rect class="ai-scatter-bg" x="${margin.left}" y="${margin.top}" width="${plotW}" height="${plotH}" rx="6"></rect>
            ${xTicks.map((tick) => {
              const x = scale(tick, xDomain, [margin.left, width - margin.right]);
              return `<line class="ai-scatter-grid" x1="${x.toFixed(1)}" x2="${x.toFixed(1)}" y1="${margin.top}" y2="${height - margin.bottom}"></line><text class="ai-scatter-axis" x="${x.toFixed(1)}" y="${height - margin.bottom + 24}" text-anchor="middle">${formatRateText(tick)}</text>`;
            }).join("")}
            ${yTicks.map((tick) => {
              const y = scale(tick, yDomain, [height - margin.bottom, margin.top]);
              return `<line class="ai-scatter-grid" x1="${margin.left}" x2="${width - margin.right}" y1="${y.toFixed(1)}" y2="${y.toFixed(1)}"></line><text class="ai-scatter-axis" x="${margin.left - 10}" y="${(y + 4).toFixed(1)}" text-anchor="end">${formatRateText(tick, true)}</text>`;
            }).join("")}
            ${yZero === null ? "" : `<line class="mixed-zero-line" x1="${margin.left}" x2="${width - margin.right}" y1="${yZero.toFixed(1)}" y2="${yZero.toFixed(1)}"></line>`}
            ${peerLines}
            <line class="ai-scatter-axis-line" x1="${margin.left}" x2="${width - margin.right}" y1="${height - margin.bottom}" y2="${height - margin.bottom}"></line>
            <line class="ai-scatter-axis-line" x1="${margin.left}" x2="${margin.left}" y1="${margin.top}" y2="${height - margin.bottom}"></line>
            <text class="ai-scatter-label" x="${margin.left + plotW / 2}" y="${height - 22}" text-anchor="middle">${esc(riskMetric.axis)}</text>
            <text class="ai-scatter-label" transform="translate(22 ${margin.top + plotH / 2}) rotate(-90)" text-anchor="middle">${esc(state.interval)}收益率</text>
            <g class="mixed-point-layer">${points}</g>
          </svg>
        </div>
        <div id="mixedDetailSlot" class="ai-scatter-detail-slot">${renderDetail(selected)}</div>
      </div>
    </section>`;
  }

  function productBadges(row) {
    if (!row) return "";
    const category = productCategory(row);
    return [
      `<span class="rank-tag mixed-category-badge ${category.className}">${esc(category.label)}</span>`,
      `<span class="rank-tag ${isStrategy(row) ? "is-strategy" : "is-fof"}">${esc(row.productType)}</span>`,
      row.fundMainType ? `<span class="rank-tag">${esc(row.fundMainType)}</span>` : "",
      row.bucket ? `<span class="rank-tag">${esc(bucketLabels[row.bucket] || row.bucket)}</span>` : "",
      row.comparisonTrack ? `<span class="rank-tag">${esc(row.comparisonTrack)}</span>` : "",
    ].join("");
  }

  function renderDetail(row) {
    if (!row) {
      return '<div class="ai-scatter-detail is-empty"><strong>暂无可比较产品</strong><p>请调整筛选条件。</p></div>';
    }
    const data = intervalData(row);
    const category = productCategory(row);
    const ranks = rankInfo(row);
    return `<article class="ai-scatter-detail mixed-detail ${category.className} ${row.isGuangfa ? "is-guangfa" : ""}">
      <div class="ai-scatter-detail-head">
        <div>
          <strong>${row.detailUrl ? `<a href="${esc(row.detailUrl)}">${esc(row.name)}</a>` : esc(row.name)}</strong>
          <span>${esc(row.code || row.id)} · ${esc(row.institution || "未知机构")} · ${esc(row.channel || "")}</span>
        </div>
        ${row.detailUrl ? `<a class="mixed-detail-link" href="${esc(row.detailUrl)}">详情页</a>` : ""}
      </div>
      <div class="mixed-badge-row">${productBadges(row)}</div>
      <div class="ai-scatter-detail-grid">
        <div><span>${esc(state.interval)}收益率</span><strong>${formatRate(data.return, true)}</strong></div>
        <div><span>${esc(currentRiskMetric().label)}</span><strong>${formatRate(data[state.riskMetric])}</strong></div>
        <div><span>年化波动率</span><strong>${formatRate(data.volatility)}</strong></div>
        <div><span>风险净值点数</span><strong>${esc(formatNumber(data.navPoints))}</strong></div>
        <div><span>基准权益权重</span><strong>${formatRate(row.benchmarkEquityWeight)}</strong></div>
        <div><span>净值区间</span><strong>${esc(data.range || "--")}</strong></div>
        <div><span>基准权益分档排名</span><strong>${formatRank(ranks.bucket)}</strong></div>
        <div><span>基准可比归档排名</span><strong>${formatRank(ranks.peer)}</strong></div>
        <div><span>同产品类型排名</span><strong>${formatRank(ranks.typePeer)}</strong></div>
      </div>
      <div class="mixed-benchmark">
        <strong>业绩比较基准</strong>
        <p>${esc(row.benchmark || "未披露")}</p>
        <span>${esc(row.bucketSource || "未标识")} · ${esc(row.formalPeerPool || "未进入正式可比池")} · ${esc(row.peerPoolNote || "")}</span>
        <p>互斥向量：权益 ${formatRateText(row.benchmarkEquityWeight)}；债券 ${formatRateText(row.benchmarkBondWeight)}；货币 ${formatRateText(row.benchmarkCashWeight)}；商品 ${formatRateText(row.benchmarkCommodityWeight)}；另类 ${formatRateText(row.benchmarkAlternativeWeight)}；未知 ${formatRateText(row.benchmarkUnknownWeight)}。港股权益 ${formatRateText(row.benchmarkHkEquityWeight)}、海外权益 ${formatRateText(row.benchmarkOverseasEquityWeight)}为权益子项，不重复计入合计。</p>
        <span>${esc(row.bucketNote || "")}；比较轨道按非权益资产80%主导规则计算。</span>
      </div>
    </article>`;
  }

  function rankPercent(info) {
    if (!info || !Number.isFinite(info.rank) || !Number.isFinite(info.total) || info.total <= 0) return null;
    return info.rank / info.total;
  }

  function rankHeadline(info) {
    if (!info || !Number.isFinite(info.rank) || !Number.isFinite(info.total)) return "当前口径不计算收益排名";
    const pct = (info.rank / info.total) * 100;
    return `${info.context} 第 ${info.rank.toLocaleString("zh-CN")}/${info.total.toLocaleString("zh-CN")}，约前 ${pct.toFixed(1)}%`;
  }

  function rankScore(info, top10, top25, top50) {
    const pct = rankPercent(info);
    if (pct === null) return 0;
    if (pct <= 0.10) return top10;
    if (pct <= 0.25) return top25;
    if (pct <= 0.50) return top50;
    return 0;
  }

  function scoreStrength(score) {
    if (score >= advantageStrengthDefinitions.obvious.threshold) return "obvious";
    if (score >= advantageStrengthDefinitions.strong.threshold) return "strong";
    if (score >= advantageStrengthDefinitions.partial.threshold) return "partial";
    return "watch";
  }

  function renderStars(stars) {
    const count = Math.max(1, Math.min(5, Math.round(stars || 1)));
    return `<span class="mixed-star-rating" aria-label="${count}星"><span>${"★★★★★".slice(0, count)}</span><i>${"★★★★★".slice(count)}</i></span>`;
  }

  function primaryRankInfo(row) {
    const ranks = rankInfo(row);
    return ranks.peer || ranks.typePeer || ranks.bucket || null;
  }

  function advantageEvaluation(row) {
    const ranks = rankInfo(row);
    const stats = peerStatsFor(row.formalPeerPool);
    const data = intervalData(row);
    const ret = numeric(data.return);
    const drawdown = numeric(data.maxDrawdown);
    const volatility = numeric(data.volatility);
    let score = 0;
    const evidence = [];
    const caveats = [];

    score += rankScore(ranks.peer, 34, 26, 12);
    score += rankScore(ranks.typePeer, 24, 18, 8);
    score += rankScore(ranks.bucket, 14, 10, 4);

    if (ranks.peer && rankPercent(ranks.peer) <= 0.25) evidence.push(`正式可比池收益${rankHeadline(ranks.peer)}`);
    if (ranks.typePeer && rankPercent(ranks.typePeer) <= 0.25) evidence.push(`同产品类型收益${rankHeadline(ranks.typePeer)}`);
    if (ranks.bucket && rankPercent(ranks.bucket) <= 0.25) evidence.push(`同权益分档收益${rankHeadline(ranks.bucket)}`);

    if (stats && stats.count >= 5) {
      if (ret !== null && stats.returnQ75 !== null && ret >= stats.returnQ75) {
        score += 16;
        evidence.push(`收益高于同池前25%阈值${formatRateText(stats.returnQ75, true)}`);
      } else if (ret !== null && stats.returnMedian !== null && ret >= stats.returnMedian) {
        score += 8;
        evidence.push(`收益高于同池中位数${formatRateText(stats.returnMedian, true)}`);
      } else if (ret !== null && stats.returnMedian !== null) {
        score -= 8;
        caveats.push(`收益低于同池中位数${formatRateText(stats.returnMedian, true)}`);
      }

      if (drawdown !== null && stats.drawdownQ75 !== null && drawdown >= stats.drawdownQ75) {
        score += 10;
        evidence.push(`回撤处于同池较优区间，最大回撤${formatRateText(drawdown)}`);
      } else if (drawdown !== null && stats.drawdownMedian !== null && drawdown >= stats.drawdownMedian) {
        score += 5;
        evidence.push(`回撤优于同池中位数`);
      } else if (drawdown !== null && stats.drawdownMedian !== null) {
        score -= 4;
        caveats.push(`回撤弱于同池中位数`);
      }

      if (volatility !== null && stats.volatilityQ25 !== null && volatility <= stats.volatilityQ25) {
        score += 8;
        evidence.push(`波动处于同池较低区间，年化波动${formatRateText(volatility)}`);
      } else if (volatility !== null && stats.volatilityMedian !== null && volatility <= stats.volatilityMedian) {
        score += 4;
        evidence.push(`波动低于同池中位数`);
      } else if (volatility !== null && stats.volatilityMedian !== null) {
        score -= 3;
        caveats.push(`波动高于同池中位数`);
      }
    } else {
      caveats.push("同池有效样本不足5个，不能形成稳定同类结论");
    }

    if (ret === null) caveats.push("当前区间收益缺失，不计算优势强度");
    if (drawdown === null || volatility === null) caveats.push("风险指标不完整，仅能作为收益线索观察");

    score = Math.max(0, Math.min(100, score));
    const strengthKey = scoreStrength(score);
    const strength = advantageStrengthDefinitions[strengthKey];
    const stars = Math.max(1, Math.min(5, strength.stars - (caveats.length >= 2 ? 1 : 0)));
    return {
      row,
      score,
      strengthKey,
      strength,
      stars,
      evidence: evidence.slice(0, 5),
      caveats: caveats.slice(0, 4),
      primaryRank: primaryRankInfo(row),
      stats,
    };
  }

  function advantageCustomerText(row, evaluation) {
    const data = intervalData(row);
    const level = clean(row.bucket);
    const track = clean(row.comparisonTrack) || "未形成轨道";
    const riskHint = Number(level.slice(1)) >= 8
      ? "这类产品权益弹性高，适合能接受净值波动、希望提高上涨捕捉的客户；沟通时要把回撤承受和持有周期放在收益之前。"
      : "这类产品更适合在同等资产结构里做替代筛选；客户沟通重点是收益改善是否伴随可接受的回撤和波动。";
    const riskText = numeric(data.maxDrawdown) === null
      ? "当前缺少回撤或波动坐标，客户侧只能作为收益线索，不能承诺风险体验。"
      : `当前${state.interval}收益${formatRateText(data.return, true)}，最大回撤${formatRateText(data.maxDrawdown)}，年化波动${formatRateText(data.volatility)}。`;
    return `${bucketScene(row)}、${track}。${riskHint}${riskText}`;
  }

  function advantageResearchText(row, evaluation) {
    const rank = evaluation.primaryRank ? rankHeadline(evaluation.primaryRank) : "当前区间没有可用收益排名";
    const evidence = evaluation.evidence.length ? evaluation.evidence.join("；") : "暂未形成多指标共同优势";
    const caveat = evaluation.caveats.length ? `需要复核：${evaluation.caveats.join("；")}。` : "暂无明显数据口径风险。";
    return `${rank}。投研上优先看正式可比池，不跨 L 档、不跨比较轨道下结论；当前证据为：${evidence}。${caveat}`;
  }

  function advantageMarketingText(row, evaluation) {
    const strength = evaluation.strength.title;
    const track = clean(row.comparisonTrack);
    if (evaluation.strengthKey === "obvious" || evaluation.strengthKey === "strong") {
      return `可作为“${strength}”候选进入重点话术池，建议围绕${track || "当前可比池"}、同类排名和风险边界展开，不要写成全市场无条件领先。适合做客户分层触达、同类替代推荐或广发产品线亮点素材。`;
    }
    if (evaluation.strengthKey === "partial") {
      return "适合做备选素材或投研跟踪，不建议作为主推标题。营销使用时应只讲清楚具体强项，并同步披露回撤、波动或样本不足的限制。";
    }
    return "更适合放入观察名单，先核对基准、净值和渠道定位；除非客户场景高度匹配，否则不建议作为主动营销主推。";
  }

  function renderAdvantageCard(evaluation) {
    const row = evaluation.row;
    const data = intervalData(row);
    const category = productCategory(row);
    const selectedClass = row.id === state.selectedId ? " is-selected" : "";
    return `<li class="mixed-advantage-card ${category.className}${selectedClass}" data-highlight-id="${esc(row.id)}" tabindex="0" role="button">
      <div class="mixed-advantage-card-head">
        <div class="mixed-highlight-main">
          ${row.detailUrl ? `<a href="${esc(row.detailUrl)}">${esc(row.name)}</a>` : `<strong>${esc(row.name)}</strong>`}
          <span>${esc(row.code || row.id)} · ${esc(category.label)} · ${esc(row.formalPeerPool || "未进入正式可比池")}</span>
        </div>
        <div class="mixed-advantage-score">
          ${renderStars(evaluation.stars)}
          <b>${Math.round(evaluation.score)}</b>
          <small>优势强度</small>
        </div>
      </div>
      <div class="mixed-highlight-rank">${formatRank(evaluation.primaryRank)}</div>
      <div class="mixed-highlight-metrics">
        <span>${esc(state.interval)} ${formatRate(data.return, true)}</span>
        <span>回撤 ${formatRate(data.maxDrawdown)}</span>
        <span>波动 ${formatRate(data.volatility)}</span>
      </div>
      <div class="mixed-evidence-list">
        ${(evaluation.evidence.length ? evaluation.evidence : ["当前优势主要来自收益排名，风险侧仍需结合产品定位复核。"]).slice(0, 3).map((item) => `<span>${esc(item)}</span>`).join("")}
      </div>
      <details class="mixed-advantage-detail">
        <summary>客户 / 投研 / 营销说明</summary>
        <div class="mixed-advantage-view"><b>客户角度</b><p>${esc(advantageCustomerText(row, evaluation))}</p></div>
        <div class="mixed-advantage-view"><b>投研角度</b><p>${esc(advantageResearchText(row, evaluation))}</p></div>
        <div class="mixed-advantage-view"><b>营销角度</b><p>${esc(advantageMarketingText(row, evaluation))}</p></div>
      </details>
      <button class="mixed-highlight-select" type="button" data-highlight-select="${esc(row.id)}">筛选并选中产品</button>
    </li>`;
  }

  function renderAdvantageGroup(key, evaluations) {
    const def = advantageStrengthDefinitions[key];
    return `<section class="mixed-strength-section is-${esc(key)}">
      <div class="mixed-strength-head">
        <div>
          <h3>${renderStars(def.stars)} ${esc(def.title)}</h3>
          <p>${esc(def.subtitle)}</p>
        </div>
        <span>${evaluations.length.toLocaleString("zh-CN")} 个</span>
      </div>
      <ol class="mixed-strength-grid">${evaluations.map(renderAdvantageCard).join("")}</ol>
    </section>`;
  }

  function renderGuangfaHighlights(list) {
    const allGfRows = list.filter((row) => row.isGuangfa && hasCurrentMetrics(row));
    const gfRows = allGfRows.filter(matchesAdvantageProductScope);
    const restoreButton = restoreFilterSnapshot
      ? '<button class="mixed-restore-filter" type="button" data-restore-highlight-filter>恢复筛选</button>'
      : "";
    const scopeControl = renderAdvantageScopeControl(allGfRows);
    if (!allGfRows.length) {
      return `<section class="panel mixed-highlight-panel">
        <div class="panel-head">
          <div><h2>广发优势产品</h2><p class="desc">当前筛选条件下没有收益、回撤、波动指标完整的广发产品。</p></div>
          <div class="mixed-highlight-actions">${scopeControl}${restoreButton}</div>
        </div>
      </section>`;
    }
    if (!gfRows.length) {
      return `<section class="panel mixed-highlight-panel">
        <div class="panel-head">
          <div><h2>广发优势产品</h2><p class="desc">当前优势区筛选为“${esc(advantageProductScopeLabel())}”，没有收益、回撤、波动指标完整的匹配产品。</p></div>
          <div class="mixed-highlight-actions">${scopeControl}${restoreButton}</div>
        </div>
      </section>`;
    }
    const unique = new Map();
    gfRows
      .map(advantageEvaluation)
      .filter((item) => item.score > 0)
      .sort((a, b) => (b.score - a.score) || compareValues(currentReturn(a.row), currentReturn(b.row), "desc") || clean(a.row.name).localeCompare(clean(b.row.name), "zh-CN"))
      .forEach((item) => {
        if (!unique.has(item.row.id)) unique.set(item.row.id, item);
      });
    const evaluations = Array.from(unique.values()).slice(0, 24);
    const groups = { obvious: [], strong: [], partial: [], watch: [] };
    evaluations.forEach((item) => groups[item.strengthKey].push(item));
    const renderedGroups = ["obvious", "strong", "partial", "watch"]
      .filter((key) => groups[key].length)
      .map((key) => renderAdvantageGroup(key, groups[key]))
      .join("");
    return `<section class="panel mixed-highlight-panel">
      <div class="panel-head">
        <div>
          <h2>广发优势产品</h2>
          <p class="desc">按当前筛选条件和 ${esc(state.interval)} 收益区间联动更新。每个产品只出现一次，按优势强度排序；优势强度综合收益排名、同池回撤、同池波动和赛道适配，但不跨风险档下绝对结论。</p>
        </div>
        <div class="mixed-highlight-actions">
          ${scopeControl}
          <span class="pill">${gfRows.length.toLocaleString("zh-CN")} 个${esc(advantageProductScopeLabel())}</span>
          ${restoreButton}
        </div>
      </div>
      <div class="mixed-advantage-method">
        <strong>评分口径</strong>
        <span>正式可比池排名权重最高，其次是同产品类型和同权益分档排名。</span>
        <span>回撤、波动优于同池中位数会加分；缺风险指标不进入优势卡。</span>
        <span>星级表示当前筛选条件下的相对优势强度，不代表跨风险档绝对优劣。</span>
      </div>
      <div class="mixed-strength-groups">${renderedGroups || '<div class="empty">当前筛选下没有达到优势评分门槛的广发产品。</div>'}</div>
    </section>`;
  }

  function selectAdvantageProduct(id) {
    const row = rows.find((item) => item.id === id);
    if (!row) return;
    if (!restoreFilterSnapshot) restoreFilterSnapshot = filterSnapshot();
    state.productType = clean(row.productType) || "all";
    state.buckets = clean(row.bucket) ? [clean(row.bucket)] : [];
    state.comparisonTracks = clean(row.comparisonTrack) ? [clean(row.comparisonTrack)] : [];
    state.fundMainTypes = clean(row.fundMainType) ? [clean(row.fundMainType)] : [];
    state.gfOnly = false;
    state.search = "";
    state.selectedId = row.id;
    state.tableSort = { key: "return", direction: "desc" };
    openMultiKey = "";
    resetPage();
    pendingScrollToScatter = true;
    render();
  }

  function sortHeader(key, label) {
    const active = state.tableSort.key === key;
    const arrow = active ? (state.tableSort.direction === "asc" ? "▲" : "▼") : "↕";
    return `<button class="sort-head mixed-sort-head ${active ? "is-active" : ""}" type="button" data-mixed-sort="${esc(key)}">${esc(label)}<span class="sort-arrow">${arrow}</span></button>`;
  }

  function totalPages(list) {
    return Math.max(1, Math.ceil(list.length / state.pageSize));
  }

  function clampPage(list) {
    const pages = totalPages(list);
    state.page = Math.min(Math.max(1, state.page), pages);
    return pages;
  }

  function paginationButton(label, page, disabled = false, active = false) {
    return `<button class="${active ? "is-active" : ""}" type="button" data-mixed-page="${esc(page)}"${disabled ? " disabled" : ""}>${esc(label)}</button>`;
  }

  function pageButtons(pageCount) {
    const pages = new Set([1, pageCount, state.page - 2, state.page - 1, state.page, state.page + 1, state.page + 2]);
    const ordered = Array.from(pages).filter((page) => page >= 1 && page <= pageCount).sort((a, b) => a - b);
    const output = [];
    let previous = 0;
    ordered.forEach((page) => {
      if (previous && page - previous > 1) output.push('<span class="mixed-pager-ellipsis">...</span>');
      output.push(paginationButton(String(page), page, false, page === state.page));
      previous = page;
    });
    return output.join("");
  }

  function renderPager(total, startIndex, endIndex, pageCount) {
    const sizeOptions = [50, 100, 180, 300];
    return `<div class="pager mixed-pager">
      <span class="mixed-pager-info">第 ${state.page.toLocaleString("zh-CN")} / ${pageCount.toLocaleString("zh-CN")} 页，显示 ${total ? (startIndex + 1).toLocaleString("zh-CN") : 0}-${endIndex.toLocaleString("zh-CN")} / ${total.toLocaleString("zh-CN")}</span>
      <div class="pager-controls">
        ${paginationButton("首页", 1, state.page === 1)}
        ${paginationButton("上一页", state.page - 1, state.page === 1)}
        ${pageButtons(pageCount)}
        ${paginationButton("下一页", state.page + 1, state.page === pageCount)}
        ${paginationButton("末页", pageCount, state.page === pageCount)}
        <label class="mixed-page-size">每页
          <select class="control" data-mixed-page-size>
            ${sizeOptions.map((size) => `<option value="${size}"${size === state.pageSize ? " selected" : ""}>${size}</option>`).join("")}
          </select>
        </label>
      </div>
    </div>`;
  }

  function renderTable(list) {
    const pageCount = clampPage(list);
    const total = list.length;
    const startIndex = total ? (state.page - 1) * state.pageSize : 0;
    const endIndex = Math.min(startIndex + state.pageSize, total);
    const display = list.slice(startIndex, endIndex);
    return `<section class="panel mixed-table-panel">
      <div class="panel-head">
        <div>
          <h2>产品列表</h2>
          <p class="desc">默认按当前区间收益率降序展示；点击任意列头可切换升序/降序。广发产品只做高亮，不参与置顶。</p>
        </div>
        <span class="pill">共 ${total.toLocaleString("zh-CN")} 条</span>
      </div>
      ${renderPager(total, startIndex, endIndex, pageCount)}
      <div class="table-wrap mixed-table-wrap">
        <table class="mixed-table">
          <thead><tr>
            <th>${sortHeader("product", "产品")}</th>
            <th>${sortHeader("productType", "类型")}</th>
            <th>${sortHeader("return", `${state.interval}收益`)}</th>
            <th>${sortHeader("bucketRank", "基准权益分档排名")}</th>
            <th>${sortHeader("peerRank", "基准可比归档排名")}</th>
            <th>${sortHeader("typePeerRank", "同产品类型排名")}</th>
            <th>${sortHeader("drawdown", "最大回撤")}</th>
            <th>${sortHeader("volatility", "年化波动")}</th>
            <th>${sortHeader("bucket", "基准权益分档")}</th>
            <th>${sortHeader("comparisonTrack", "比较轨道")}</th>
            <th>${sortHeader("formalPeerPool", "正式可比池")}</th>
            <th>${sortHeader("range", "净值区间")}</th>
            <th>${sortHeader("bucketSource", "基准来源")}</th>
            <th>${sortHeader("institution", "机构")}</th>
          </tr></thead>
          <tbody>${display.length ? display.map((row) => {
            const data = intervalData(row);
            const category = productCategory(row);
            const ranks = rankInfo(row);
            return `<tr class="${[category.className, row.isGuangfa ? "is-guangfa" : ""].filter(Boolean).join(" ")}" data-id="${esc(row.id)}">
              <td class="mixed-product-cell">${row.detailUrl ? `<a href="${esc(row.detailUrl)}">${esc(row.name)}</a>` : `<strong>${esc(row.name)}</strong>`}<span>${esc(row.code || row.id)}</span>${row.isGuangfa ? `<em>${esc(category.label)}</em>` : ""}</td>
              <td>${productBadges(row)}</td>
              <td>${formatRate(data.return, true)}</td>
              <td>${formatRank(ranks.bucket)}</td>
              <td>${formatRank(ranks.peer)}</td>
              <td>${formatRank(ranks.typePeer)}</td>
              <td>${formatRate(data.maxDrawdown)}</td>
              <td>${formatRate(data.volatility)}</td>
              <td>${esc(bucketLabels[row.bucket] || row.bucket)}</td>
              <td>${esc(row.comparisonTrack || "未形成")}</td>
              <td>${esc(row.formalPeerPool || "未进入")}</td>
              <td>${esc(data.range || "--")}</td>
              <td>${esc(row.bucketSource || "未标识")}</td>
              <td>${esc(row.institution || "未知机构")}</td>
            </tr>`;
          }).join("") : '<tr><td colspan="14"><div class="empty">当前筛选条件下没有产品</div></td></tr>'}</tbody>
        </table>
      </div>
    </section>`;
  }

  function renderFootnote() {
    const includedNoComplete = Number(pack.meta?.includedNoCompleteMetricRowCount || 0);
    return `<section class="panel mixed-note-panel">
      <h2>数据口径</h2>
      <p>页面数据来自混排榜工作簿源包，生成时仅剔除未分档产品 ${Number(pack.meta?.excludedUnbucketedRowCount || 0).toLocaleString("zh-CN")} 条；有分档但缺完整收益-风险区间的产品 ${includedNoComplete.toLocaleString("zh-CN")} 条保留在列表，缺失指标显示为 --。列表排名按当前收益区间动态计算：基准权益分档排名使用同一 L 档，基准可比归档排名使用同一正式可比池，同产品类型排名使用“产品类型 + L 档 + 比较轨道”；点阵只绘制当前区间收益和风险坐标齐全的产品。所有百分比字段按源数据小数比率乘以 100 展示。</p>
    </section>`;
  }

  function bindEvents() {
    const productType = document.getElementById("mixedProductType");
    const interval = document.getElementById("mixedInterval");
    const riskMetric = document.getElementById("mixedRiskMetric");
    const gfOnly = document.getElementById("mixedGfOnly");
    const search = document.getElementById("mixedSearch");
    productType?.addEventListener("change", (event) => { state.productType = event.target.value; state.selectedId = ""; restoreFilterSnapshot = null; openMultiKey = ""; resetPage(); render(); });
    interval?.addEventListener("change", (event) => { state.interval = event.target.value; state.selectedId = ""; restoreFilterSnapshot = null; openMultiKey = ""; resetPage(); render(); });
    riskMetric?.addEventListener("change", (event) => { state.riskMetric = event.target.value; state.selectedId = ""; restoreFilterSnapshot = null; openMultiKey = ""; resetPage(); render(); });
    gfOnly?.addEventListener("change", (event) => { state.gfOnly = event.target.checked; state.selectedId = ""; restoreFilterSnapshot = null; openMultiKey = ""; resetPage(); render(); });
    root.querySelectorAll("[data-multi-all], [data-multi-filter]").forEach((node) => {
      node.addEventListener("change", (event) => {
        const allKey = node.getAttribute("data-multi-all");
        const filterKey = node.getAttribute("data-multi-filter");
        openMultiKey = allKey || filterKey || "";
        if (allKey) {
          state[allKey] = [];
        } else if (filterKey) {
          updateMultiSelection(filterKey, node.getAttribute("data-multi-value") || "", event.target.checked);
        }
        state.selectedId = "";
        restoreFilterSnapshot = null;
        resetPage();
        render();
      });
    });
    root.querySelectorAll("[data-reset-filters]").forEach((node) => {
      node.addEventListener("click", () => {
        resetAllFilters();
        render();
      });
    });
    root.querySelectorAll("[data-restore-highlight-filter]").forEach((node) => {
      node.addEventListener("click", () => {
        restoreFilters();
        clampPage(filteredRows());
        render();
      });
    });
    root.querySelectorAll("[data-advantage-scope]").forEach((node) => {
      node.addEventListener("click", () => {
        const scope = node.getAttribute("data-advantage-scope") || "all";
        state.advantageProductScope = ["all", "strategy", "fund"].includes(scope) ? scope : "all";
        render();
      });
    });
    search?.addEventListener("input", (event) => {
      window.clearTimeout(searchTimer);
      searchTimer = window.setTimeout(() => {
        state.search = event.target.value;
        state.selectedId = "";
        restoreFilterSnapshot = null;
        openMultiKey = "";
        resetPage();
        render();
      }, 180);
    });

    root.querySelectorAll(".mixed-scatter-dot").forEach((node) => {
      node.addEventListener("click", () => {
        state.selectedId = node.getAttribute("data-id") || "";
        render();
      });
    });
    root.querySelectorAll(".mixed-table tbody tr[data-id]").forEach((node) => {
      node.addEventListener("click", (event) => {
        if (event.target && event.target.closest("a")) return;
        state.selectedId = node.getAttribute("data-id") || "";
        render();
      });
    });
    root.querySelectorAll("[data-highlight-select]").forEach((node) => {
      node.addEventListener("click", (event) => {
        event.preventDefault();
        event.stopPropagation();
        selectAdvantageProduct(node.getAttribute("data-highlight-select") || "");
      });
    });
    root.querySelectorAll("[data-highlight-id]").forEach((node) => {
      node.addEventListener("click", (event) => {
        if (event.target && event.target.closest("a, details, summary, button")) return;
        selectAdvantageProduct(node.getAttribute("data-highlight-id") || "");
      });
      node.addEventListener("keydown", (event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          selectAdvantageProduct(node.getAttribute("data-highlight-id") || "");
        }
      });
    });
    root.querySelectorAll("[data-mixed-sort]").forEach((node) => {
      node.addEventListener("click", () => {
        const key = node.getAttribute("data-mixed-sort") || "return";
        if (state.tableSort.key === key) {
          state.tableSort.direction = state.tableSort.direction === "asc" ? "desc" : "asc";
        } else {
          state.tableSort.key = key;
          state.tableSort.direction = ["return", "risk", "drawdown", "volatility"].includes(key) ? "desc" : "asc";
        }
        state.selectedId = "";
        resetPage();
        render();
      });
    });
    root.querySelectorAll("[data-mixed-page]").forEach((node) => {
      node.addEventListener("click", () => {
        const page = Number(node.getAttribute("data-mixed-page"));
        if (Number.isFinite(page)) {
          state.page = page;
          clampPage(filteredRows());
        }
        render();
      });
    });
    root.querySelectorAll("[data-mixed-page-size]").forEach((node) => {
      node.addEventListener("change", (event) => {
        const size = Number(event.target.value);
        state.pageSize = Number.isFinite(size) && size > 0 ? size : 100;
        resetPage();
        render();
      });
    });
  }

  function render() {
    const broadList = filteredRows({ requireCurrent: false });
    const list = sortedRows();
    const selected = selectedRow(list);
    if (selected) state.selectedId = selected.id;
    root.innerHTML = [
      renderControls(),
      renderMetrics(list, broadList),
      renderScatter(list),
      renderGuangfaHighlights(list),
      renderTable(list),
      renderFootnote(),
    ].join("");
    bindEvents();
    if (pendingScrollToScatter) {
      pendingScrollToScatter = false;
      root.querySelector(".mixed-scatter-panel")?.scrollIntoView({ block: "start", behavior: "smooth" });
    }
  }

  render();
})();
