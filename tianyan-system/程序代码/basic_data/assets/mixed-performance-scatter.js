(() => {
  const B = window.BasicData || {};
  const pack = window.__MIXED_PERFORMANCE_SCATTER_PACK__;
  const root = document.getElementById("mixedPerformanceScatterPage");
  if (!root) return;

  const esc = B.esc || ((value) => String(value ?? "").replace(/[&<>"']/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch])));
  const intervals = pack?.meta?.intervals || ["上半年", "今年以来", "近1月", "近3月", "近6月", "近1年"];
  const bucketOrder = pack?.meta?.bucketOrder || ["L0", "L1", "L2", "L3", "L4", "L5", "L6", "L7", "L8", "L9", "L10"];
  const bucketLabels = pack?.meta?.bucketLabels || {};
  const broadBucketLabels = pack?.meta?.broadEquityBucketLabels || bucketLabels;
  const rows = Array.isArray(pack?.rows) ? pack.rows : [];
  const riskMetrics = [
    { key: "maxDrawdown", label: "最大回撤", axis: "收益区间回撤", better: "越靠右回撤越小" },
    { key: "volatility", label: "年化波动率", axis: "收益区间波动率", better: "越靠左波动越小" },
  ];
  const state = {
    productType: "all",
    channel: "",
    institution: "",
    buckets: [],
    broadBuckets: [],
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
  const initialParams = B.params ? B.params() : new URLSearchParams(window.location.search);
  const incomingProductType = cleanParam(initialParams.get("productType"));
  if (["all", "公募基金", "投顾策略"].includes(incomingProductType)) state.productType = incomingProductType;
  state.channel = cleanParam(initialParams.get("channel"));
  state.institution = cleanParam(initialParams.get("institution"));
  const incomingRiskWeight = cleanParam(initialParams.get("riskWeight"));
  if (incomingRiskWeight && bucketOrder.includes(incomingRiskWeight)) state.broadBuckets = [incomingRiskWeight];
  let searchTimer = null;
  let searchComposing = false;
  let restoreSearchFocus = false;
  const rankCache = new Map();
  const peerStatsCache = new Map();
  const broadBucketStatsCache = new Map();
  const abilityMetricRankCache = new Map();
  const multiPeriodProfileCache = new Map();
  let restoreFilterSnapshot = null;
  let openMultiKey = "";
  let opportunityProfileId = "";
  let advantagePanelOpen = false;
  let opportunityPanelOpen = false;
  let scatterRenderState = null;

  function cleanParam(value) {
    return String(value || "").trim();
  }

  if (!pack || !rows.length) {
    root.innerHTML = '<section class="panel"><div class="empty">未找到收益风险点阵数据包，请先运行页面数据包生成脚本。</div></section>';
    return;
  }

  const productCategories = {
    marketFund: { label: "全市场基金产品", fill: "#B9D6C2", stroke: "#3F7B56", className: "is-market-fund", legendClass: "mixed-market-fund-legend", drawOrder: 1, radius: 3.8 },
    marketStrategy: { label: "全市场投顾产品", fill: "#B9CED6", stroke: "#4F7888", className: "is-market-strategy", legendClass: "mixed-market-strategy-legend", drawOrder: 2, radius: 4.2 },
    gfFund: { label: "广发基金产品", fill: "#E3BFA6", stroke: "#B86B3E", className: "is-gf-fund", legendClass: "mixed-gf-fund-legend", drawOrder: 3, radius: 5.2 },
    gfStrategy: { label: "广发投顾产品", fill: "#DEB3B0", stroke: "#B5524A", className: "is-gf-strategy", legendClass: "mixed-gf-strategy-legend", drawOrder: 4, radius: 5.8 },
  };
  const advantageStrengthDefinitions = {
    obvious: {
      title: "明显优势",
      subtitle: "六维能力综合得分达到头部区间，收益、风险效率和跨周期证据同时成立，适合重点跟进。",
      stars: 5,
      threshold: 80,
    },
    strong: {
      title: "较强优势",
      subtitle: "至少两类关键能力明显靠前，且没有被回撤、波动或持续性显著拖累。",
      stars: 4,
      threshold: 65,
    },
    partial: {
      title: "部分优势",
      subtitle: "有清晰单点优势，或收益与风险中只有部分指标占优，仍需补充验证。",
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
  const opportunityDefinitions = {
    comprehensive: {
      title: "A1 重点营销产品",
      subtitle: "核心可比池、扩大口径和风险调整指标同时有证据，适合作为当前重点运营素材。",
      badge: "综合优势",
      stars: 5,
      className: "is-opportunity-comprehensive",
    },
    steady: {
      title: "A2 稳健体验优势",
      subtitle: "收益不一定最激进，但回撤、波动或风险调整收益更友好，适合存量客户替代和稳健配置。",
      badge: "稳健体验",
      stars: 4,
      className: "is-opportunity-steady",
    },
    offensive: {
      title: "A3 进攻弹性优势",
      subtitle: "收益弹性突出，但必须同步说明回撤和波动边界，适合高风险承受客群和行情型专题。",
      badge: "进攻弹性",
      stars: 4,
      className: "is-opportunity-offensive",
    },
    scenario: {
      title: "B1 场景型优势",
      subtitle: "在特定基准风险资产权重、比较轨道或产品类型中有明确相对亮点，适合做场景化素材。",
      badge: "场景机会",
      stars: 3,
      className: "is-opportunity-scenario",
    },
    improvement: {
      title: "B2 改善型机会",
      subtitle: "短周期表现明显改善，但长期证据还不充分，适合投研观察和小范围验证。",
      badge: "改善机会",
      stars: 3,
      className: "is-opportunity-improvement",
    },
    notReady: {
      title: "C 暂不主推",
      subtitle: "数据、风险或多口径证据不足，只保留观察，不进入机会卡。",
      badge: "暂不主推",
      stars: 1,
      className: "is-opportunity-not-ready",
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
    state.channel = "";
    state.institution = "";
    state.buckets = [];
    state.broadBuckets = [];
    state.comparisonTracks = [];
    state.fundMainTypes = [];
    state.interval = "上半年";
    state.riskMetric = "maxDrawdown";
    state.gfOnly = false;
    state.search = "";
    state.advantageProductScope = "all";
    state.selectedId = "";
    state.tableSort = { key: "return", direction: "desc" };
    Object.keys(B.globalStrategyFilterDefinitions || {}).forEach((key) => {
      B.setGlobalStrategyFilter?.(key, false, { syncUrl: false });
    });
    restoreFilterSnapshot = null;
    openMultiKey = "";
    opportunityProfileId = "";
    resetPage();
    syncFilterUrl();
  }

  function syncFilterUrl() {
    const target = new URL(window.location.href);
    const setOrDelete = (key, value, emptyValue = "") => {
      if (value === emptyValue || value === null || value === undefined || value === "") target.searchParams.delete(key);
      else target.searchParams.set(key, String(value));
    };
    setOrDelete("productType", state.productType, "all");
    setOrDelete("channel", state.channel);
    setOrDelete("institution", state.institution);
    setOrDelete("riskWeight", selectedValues("broadBuckets").length === 1 ? selectedValues("broadBuckets")[0] : "");
    Object.entries(B.globalStrategyFilterDefinitions || {}).forEach(([key, config]) => {
      target.searchParams.set(config.param, B.globalStrategyFilters?.[key] ? "1" : "0");
    });
    window.history.replaceState({}, "", `${target.pathname}${target.search}${target.hash}`);
  }

  function filterSnapshot() {
    return {
      productType: state.productType,
      channel: state.channel,
      institution: state.institution,
      buckets: selectedValues("buckets"),
      broadBuckets: selectedValues("broadBuckets"),
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
    state.channel = snapshot.channel || "";
    state.institution = snapshot.institution || "";
    state.buckets = cloneValues(snapshot.buckets);
    state.broadBuckets = cloneValues(snapshot.broadBuckets);
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
    opportunityProfileId = "";
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

  function returnDrawdownRatio(data) {
    const ret = numeric(data?.return);
    const drawdown = numeric(data?.maxDrawdown);
    const risk = drawdown === null ? null : Math.abs(drawdown);
    if (ret === null || risk === null || risk <= 1e-9) return null;
    return ret / risk;
  }

  function returnVolatilityRatio(data) {
    const ret = numeric(data?.return);
    const volatility = numeric(data?.volatility);
    if (ret === null || volatility === null || volatility <= 1e-9) return null;
    return ret / volatility;
  }

  function formatRatio(value) {
    const number = numeric(value);
    return number === null ? "--" : number.toFixed(2);
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

  function productBucketContext(row) {
    const productType = clean(row?.productType);
    const bucket = clean(row?.bucket);
    return productType && bucket ? `${productType} ${bucket}` : "";
  }

  function productTrackContext(row) {
    const productType = clean(row?.productType);
    const track = clean(row?.comparisonTrack);
    return productType && track ? `${productType} ${track}` : "";
  }

  function broadBucketContext(row) {
    return clean(row?.broadEquityBucket);
  }

  function productBroadBucketContext(row) {
    const productType = clean(row?.productType);
    const bucket = clean(row?.broadEquityBucket);
    return productType && bucket ? `${productType} ${bucket}` : "";
  }

  function trackContext(row) {
    return clean(row?.comparisonTrack);
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
    const productBucketGroups = new Map();
    const broadBucketGroups = new Map();
    const productBroadBucketGroups = new Map();
    const productTrackGroups = new Map();
    const trackGroups = new Map();
    rows.filter((row) => intervalReturn(row, interval) !== null).forEach((row) => {
      addRankGroup(bucketGroups, row.bucket, row);
      addRankGroup(peerGroups, row.formalPeerPool, row);
      addRankGroup(typePeerGroups, typePeerContext(row), row);
      addRankGroup(productBucketGroups, productBucketContext(row), row);
      addRankGroup(broadBucketGroups, broadBucketContext(row), row);
      addRankGroup(productBroadBucketGroups, productBroadBucketContext(row), row);
      addRankGroup(productTrackGroups, productTrackContext(row), row);
      addRankGroup(trackGroups, trackContext(row), row);
    });
    const rankings = {
      bucket: buildRankMap(bucketGroups, interval),
      peer: buildRankMap(peerGroups, interval),
      typePeer: buildRankMap(typePeerGroups, interval),
      productBucket: buildRankMap(productBucketGroups, interval),
      broadBucket: buildRankMap(broadBucketGroups, interval),
      productBroadBucket: buildRankMap(productBroadBucketGroups, interval),
      productTrack: buildRankMap(productTrackGroups, interval),
      track: buildRankMap(trackGroups, interval),
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
      productBucket: rankings.productBucket.get(key) || null,
      broadBucket: rankings.broadBucket.get(key) || null,
      productBroadBucket: rankings.productBroadBucket.get(key) || null,
      productTrack: rankings.productTrack.get(key) || null,
      track: rankings.track.get(key) || null,
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
    const returnDrawdownRatios = peerRows
      .map((row) => returnDrawdownRatio(intervalData(row, interval)))
      .filter((value) => value !== null);
    const returnVolatilityRatios = peerRows
      .map((row) => returnVolatilityRatio(intervalData(row, interval)))
      .filter((value) => value !== null);
    const result = {
      count: peerRows.length,
      returnMedian: median(peerRows.map((row) => numeric(intervalData(row, interval).return))),
      returnQ75: quantile(peerRows.map((row) => numeric(intervalData(row, interval).return)), 0.75),
      drawdownMedian: median(peerRows.map((row) => numeric(intervalData(row, interval).maxDrawdown))),
      drawdownQ75: quantile(peerRows.map((row) => numeric(intervalData(row, interval).maxDrawdown)), 0.75),
      volatilityMedian: median(peerRows.map((row) => numeric(intervalData(row, interval).volatility))),
      volatilityQ25: quantile(peerRows.map((row) => numeric(intervalData(row, interval).volatility)), 0.25),
      returnDrawdownMedian: median(returnDrawdownRatios),
      returnDrawdownQ75: quantile(returnDrawdownRatios, 0.75),
      returnVolatilityMedian: median(returnVolatilityRatios),
      returnVolatilityQ75: quantile(returnVolatilityRatios, 0.75),
    };
    peerStatsCache.set(key, result);
    return result;
  }

  function broadBucketStatsFor(row, interval = state.interval) {
    const bucket = clean(row?.broadEquityBucket);
    const key = `${interval}\u0001${bucket}`;
    if (!bucket) return null;
    if (broadBucketStatsCache.has(key)) return broadBucketStatsCache.get(key);
    const bucketRows = rows.filter((item) => clean(item.broadEquityBucket) === bucket && intervalComplete(item, interval));
    const returnDrawdownRatios = bucketRows.map((item) => returnDrawdownRatio(intervalData(item, interval))).filter((value) => value !== null);
    const returnVolatilityRatios = bucketRows.map((item) => returnVolatilityRatio(intervalData(item, interval))).filter((value) => value !== null);
    const result = {
      count: bucketRows.length,
      returnMedian: median(bucketRows.map((item) => numeric(intervalData(item, interval).return))),
      returnQ75: quantile(bucketRows.map((item) => numeric(intervalData(item, interval).return)), 0.75),
      drawdownMedian: median(bucketRows.map((item) => numeric(intervalData(item, interval).maxDrawdown))),
      drawdownQ75: quantile(bucketRows.map((item) => numeric(intervalData(item, interval).maxDrawdown)), 0.75),
      volatilityMedian: median(bucketRows.map((item) => numeric(intervalData(item, interval).volatility))),
      volatilityQ25: quantile(bucketRows.map((item) => numeric(intervalData(item, interval).volatility)), 0.25),
      returnDrawdownMedian: median(returnDrawdownRatios),
      returnDrawdownQ75: quantile(returnDrawdownRatios, 0.75),
      returnVolatilityMedian: median(returnVolatilityRatios),
      returnVolatilityQ75: quantile(returnVolatilityRatios, 0.75),
    };
    broadBucketStatsCache.set(key, result);
    return result;
  }

  function bucketScene(row, field = "bucket") {
    const value = clean(row?.[field]);
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

  function valuesFor(field, { strategyOnly = false } = {}) {
    const set = new Set();
    rows.forEach((row) => {
      if (strategyOnly && row.productType !== "投顾策略") return;
      const value = clean(row[field]);
      if (value) set.add(value);
    });
    return Array.from(set).sort((a, b) => {
      if (field === "bucket" || field === "broadEquityBucket") {
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
      || Boolean(state.channel)
      || Boolean(state.institution)
      || selectedValues("broadBuckets").length > 0
      || selectedValues("comparisonTracks").length > 0
      || selectedValues("fundMainTypes").length > 0
      || state.interval !== "上半年"
      || state.riskMetric !== "maxDrawdown"
      || state.gfOnly
      || Boolean(state.search.trim())
      || Object.keys(B.globalStrategyFilterDefinitions || {}).some((key) => Boolean(B.globalStrategyFilters?.[key]));
  }

  function matchesGlobalPackFilters(row) {
    if (row.productType !== "投顾策略") return true;
    const filters = B.globalStrategyFilters || {};
    return (!filters.benchmark || Boolean(row.hasBenchmark))
      && (!filters.performance || Boolean(row.hasPerformance))
      && (!filters.history || Boolean(row.hasHistoryPosition))
      && (!filters.active || Boolean(row.clientActive));
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
      row.broadEquityBucket,
      row.comparisonTrack,
      row.formalPeerPool,
      row.benchmark,
    ].join(" ").toLowerCase();
    return haystack.includes(needle);
  }

  function filteredRows({ requireCurrent = false } = {}) {
    return rows.filter((row) => {
      if (state.productType !== "all" && row.productType !== state.productType) return false;
      if (state.channel && clean(row.channel) !== state.channel) return false;
      if (state.institution && clean(row.institution) !== state.institution) return false;
      if (!matchesGlobalPackFilters(row)) return false;
      if (!matchesMulti(row, "broadBuckets", "broadEquityBucket")) return false;
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
    if (key === "broadEquityBucket") {
      const index = bucketOrder.indexOf(row.broadEquityBucket);
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
    if (key === "bucketRank") return ranks.broadBucket?.rank ?? null;
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
    const broadBucketText = selectionSummary("broadBuckets", "全部基准风险资产权重", broadBucketLabels);
    const trackText = selectionSummary("comparisonTracks", "全部轨道");
    const peerPoolCount = new Set(list.map((row) => clean(row.formalPeerPool)).filter(Boolean)).size;
    return `<section class="grid mixed-kpi-grid">
      ${metricCard("当前筛选产品", list.length.toLocaleString("zh-CN"), `${fundCount} 只基金 / ${strategyCount} 条投顾`)}
      ${metricCard("当前可绘制点阵", plottableCount.toLocaleString("zh-CN"), `缺指标产品保留在列表，以 -- 展示`)}
      ${metricCard("当前筛选口径", `${broadBucketText} / ${trackText}`, `${peerPoolCount.toLocaleString("zh-CN")} 个正式可比池，数据包 ${broadCount.toLocaleString("zh-CN")} 条`)}
      ${metricCard("广发产品", gfCount.toLocaleString("zh-CN"), "列表按字段排序，点阵按广发基金/投顾分色置顶")}
      ${metricCard("数据截止", pack.meta?.intervalAsOfDates?.[state.interval] || pack.meta?.asOfDate || "未披露", `${state.interval} / ${currentRiskMetric().label}`)}
    </section>`;
  }

  function currentRiskMetric() {
    return riskMetrics.find((metric) => metric.key === state.riskMetric) || riskMetrics[0];
  }

  function renderControls() {
    const channelValues = valuesFor("channel", { strategyOnly: true });
    const institutionValues = valuesFor("institution", { strategyOnly: true });
    const broadBucketValues = valuesFor("broadEquityBucket");
    const trackValues = valuesFor("comparisonTrack");
    const fundTypeValues = valuesFor("fundMainType");
    const globalLabels = Object.entries(B.globalStrategyFilterDefinitions || {})
      .filter(([key]) => B.globalStrategyFilters?.[key])
      .map(([, config]) => config.label);
    const incomingScope = [
      state.channel ? `销售渠道：${state.channel}` : "",
      state.institution ? `投顾管理人：${state.institution}` : "",
      globalLabels.length ? `全局数据条件：${globalLabels.join("、")}` : "",
    ].filter(Boolean);
    return `<section class="panel mixed-control-panel">
      <div class="panel-head">
        <div>
          <h1>投顾策略 + 公募基金全市场产品排名</h1>
          <p class="desc">保留策略列表当前可查询策略和全市场主份额公募基金；未分档或缺少区间指标的产品仍可查询，点阵只绘制当前区间坐标完整的产品。</p>
        </div>
        <button class="mixed-reset-filters" type="button" data-reset-filters${hasActiveFilters() ? "" : " disabled"}>重置条件</button>
      </div>
      ${incomingScope.length ? `<div class="mixed-incoming-scope"><b>从机构总览带入</b>${incomingScope.map((item) => `<span>${esc(item)}</span>`).join("")}<small>以下条件已按机构总览原值执行，可在本页直接微调；重置会清除全部联动条件。</small></div>` : ""}
      <div class="mixed-filter-grid">
        <label>产品范围<select id="mixedProductType" class="control">
          <option value="all"${state.productType === "all" ? " selected" : ""}>基金 + 投顾</option>
          <option value="公募基金"${state.productType === "公募基金" ? " selected" : ""}>公募基金</option>
          <option value="投顾策略"${state.productType === "投顾策略" ? " selected" : ""}>投顾策略</option>
        </select></label>
        <label>销售渠道<select id="mixedChannel" class="control">${optionHtml(channelValues, state.channel || "all", "全部销售渠道")}</select></label>
        <label>投顾管理人<select id="mixedInstitution" class="control">${optionHtml(institutionValues, state.institution || "all", "全部投顾管理人")}</select></label>
        ${multiControlHtml({ id: "mixedBroadBucket", label: "基准风险资产权重", stateKey: "broadBuckets", values: broadBucketValues, allLabel: "全部基准风险资产权重", labels: broadBucketLabels })}
        ${multiControlHtml({ id: "mixedComparisonTrack", label: "比较轨道", stateKey: "comparisonTracks", values: trackValues, allLabel: "全部轨道" })}
        ${multiControlHtml({ id: "mixedFundType", label: "基金类型", stateKey: "fundMainTypes", values: fundTypeValues, allLabel: "全部类型" })}
        <label>收益区间<select id="mixedInterval" class="control">${intervals.map((item) => `<option value="${esc(item)}"${item === state.interval ? " selected" : ""}>${esc(item)}</option>`).join("")}</select></label>
        <label>风险指标<select id="mixedRiskMetric" class="control">${riskMetrics.map((item) => `<option value="${esc(item.key)}"${item.key === state.riskMetric ? " selected" : ""}>${esc(item.label)}</option>`).join("")}</select></label>
        <label>搜索<input id="mixedSearch" class="control" type="search" value="${esc(state.search)}" placeholder="名称、代码、机构、基准"></label>
        <label class="mixed-check"><input id="mixedGfOnly" type="checkbox"${state.gfOnly ? " checked" : ""}> 只看广发</label>
      </div>
      <fieldset class="mixed-linked-filter-fieldset"><legend>策略数据条件（与机构总览一致）</legend><div class="mixed-linked-filter-grid">
        ${Object.entries(B.globalStrategyFilterDefinitions || {}).map(([key, config]) => `<label class="mixed-check"><input type="checkbox" data-global-filter="${esc(key)}"${B.globalStrategyFilters?.[key] ? " checked" : ""}> ${esc(config.label)}</label>`).join("")}
      </div></fieldset>
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
    const scopeText = `${selectionSummary("buckets", "全部分档", bucketLabels)} / ${selectionSummary("broadBuckets", "全部基准风险资产权重", broadBucketLabels)} / ${selectionSummary("comparisonTracks", "全部轨道")}`;
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
    const medianBucket = clean(selected?.broadEquityBucket);
    const medianBucketLabel = broadBucketLabels[medianBucket] || medianBucket;
    const peerRows = medianBucket
      ? rows.filter((row) => clean(row.broadEquityBucket) === medianBucket && hasCurrentMetrics(row))
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
        <text class="mixed-peer-label" x="${margin.left + 8}" y="${(y - 6).toFixed(1)}">${esc(medianBucketLabel)}收益中位数</text>
        <text class="mixed-peer-label" x="${(x + 6).toFixed(1)}" y="${margin.top + 16}">同风险权重产品中位数</text>`;
    })();
    const points = pointRows.map((row) => {
      const xValue = currentRisk(row);
      const yValue = currentReturn(row);
      if (xValue === null || yValue === null) return null;
      const cx = scale(xValue, xDomain, [margin.left, width - margin.right]);
      const cy = scale(yValue, yDomain, [height - margin.bottom, margin.top]);
      const category = productCategory(row);
      return {
        id: row.id,
        name: row.name,
        label: category.label,
        x: cx,
        y: cy,
        radius: category.radius,
        fill: category.fill,
        stroke: category.stroke,
        selected: row.id === state.selectedId,
        tooltip: `${category.label} · ${row.name} ${formatRateText(yValue, true)} / ${formatRateText(xValue)}`,
      };
    }).filter(Boolean);
    scatterRenderState = { width, height, points };

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
          <div class="mixed-scatter-stage">
          <canvas id="mixedScatterCanvas" class="mixed-scatter-canvas" width="${width}" height="${height}" role="img" aria-label="收益风险点阵，可点击产品点查看详情"></canvas>
          <svg class="ai-scatter-svg mixed-scatter-svg" viewBox="0 0 ${width} ${height}" aria-hidden="true">
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
          </svg>
          <div id="mixedScatterTooltip" class="mixed-scatter-tooltip" hidden></div>
          </div>
        </div>
        <div id="mixedDetailSlot" class="ai-scatter-detail-slot">${renderDetail(selected)}</div>
      </div>
    </section>`;
  }

  function scatterPointAt(event, canvas) {
    if (!scatterRenderState) return null;
    const bounds = canvas.getBoundingClientRect();
    const x = ((event.clientX - bounds.left) / bounds.width) * scatterRenderState.width;
    const y = ((event.clientY - bounds.top) / bounds.height) * scatterRenderState.height;
    for (let index = scatterRenderState.points.length - 1; index >= 0; index -= 1) {
      const point = scatterRenderState.points[index];
      const hitRadius = Math.max(7, point.radius + 3);
      if (((point.x - x) ** 2) + ((point.y - y) ** 2) <= hitRadius ** 2) return point;
    }
    return null;
  }

  function drawScatterCanvas() {
    const canvas = document.getElementById("mixedScatterCanvas");
    if (!canvas || !scatterRenderState) return;
    const context = canvas.getContext("2d");
    if (!context) return;
    context.clearRect(0, 0, scatterRenderState.width, scatterRenderState.height);
    scatterRenderState.points.forEach((point) => {
      context.beginPath();
      context.arc(point.x, point.y, point.radius, 0, Math.PI * 2);
      context.fillStyle = point.fill;
      context.fill();
      context.lineWidth = point.selected ? 3 : 1.2;
      context.strokeStyle = point.selected ? "#111827" : point.stroke;
      context.stroke();
    });
    const selected = scatterRenderState.points.find((point) => point.selected);
    if (selected) {
      context.beginPath();
      context.arc(selected.x, selected.y, selected.radius + 3, 0, Math.PI * 2);
      context.lineWidth = 2;
      context.strokeStyle = "#111827";
      context.stroke();
    }

    const tooltip = document.getElementById("mixedScatterTooltip");
    canvas.addEventListener("pointermove", (event) => {
      const point = scatterPointAt(event, canvas);
      canvas.style.cursor = point ? "pointer" : "default";
      if (!tooltip) return;
      if (!point) {
        tooltip.hidden = true;
        return;
      }
      const bounds = canvas.getBoundingClientRect();
      tooltip.textContent = point.tooltip;
      tooltip.style.left = `${event.clientX - bounds.left + 12}px`;
      tooltip.style.top = `${event.clientY - bounds.top + 12}px`;
      tooltip.hidden = false;
    });
    canvas.addEventListener("pointerleave", () => {
      if (tooltip) tooltip.hidden = true;
    });
    canvas.addEventListener("click", (event) => {
      const point = scatterPointAt(event, canvas);
      if (!point) return;
      state.selectedId = point.id;
      render();
    });
  }

  function productBadges(row) {
    if (!row) return "";
    const category = productCategory(row);
    return [
      `<span class="rank-tag mixed-category-badge ${category.className}">${esc(category.label)}</span>`,
      `<span class="rank-tag ${isStrategy(row) ? "is-strategy" : "is-fof"}">${esc(row.productType)}</span>`,
      row.fundMainType ? `<span class="rank-tag">${esc(row.fundMainType)}</span>` : "",
      row.broadEquityBucket ? `<span class="rank-tag">基准风险资产权重 ${esc(broadBucketLabels[row.broadEquityBucket] || row.broadEquityBucket)}</span>` : "",
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
        <div><span>基准风险资产权重</span><strong>${formatRate(row.broadEquityWeight)}</strong></div>
        <div><span>净值区间</span><strong>${esc(data.range || "--")}</strong></div>
        <div><span>基准风险资产权重排名</span><strong>${formatRank(ranks.bucket)}</strong></div>
        <div><span>基准可比归档排名</span><strong>${formatRank(ranks.peer)}</strong></div>
        <div><span>同产品类型排名</span><strong>${formatRank(ranks.typePeer)}</strong></div>
      </div>
      <div class="mixed-benchmark">
        <strong>业绩比较基准</strong>
        <p>${esc(row.benchmark || "未披露")}</p>
        <span>${esc(row.bucketSource || "未标识")} · ${esc(row.formalPeerPool || "未进入正式可比池")} · ${esc(row.peerPoolNote || "")}</span>
        <p>互斥向量：权益 ${formatRateText(row.benchmarkEquityWeight)}；债券 ${formatRateText(row.benchmarkBondWeight)}；货币 ${formatRateText(row.benchmarkCashWeight)}；商品 ${formatRateText(row.benchmarkCommodityWeight)}；另类 ${formatRateText(row.benchmarkAlternativeWeight)}；未知 ${formatRateText(row.benchmarkUnknownWeight)}。港股权益 ${formatRateText(row.benchmarkHkEquityWeight)}、海外权益 ${formatRateText(row.benchmarkOverseasEquityWeight)}为权益子项，不重复计入合计。</p>
        <p>基准风险资产权重：${esc(broadBucketLabels[row.broadEquityBucket] || row.broadEquityBucket || "未分档")}；基准风险资产权重 ${formatRateText(row.broadEquityWeight)}。${esc(row.broadEquityMethod || "基准风险资产=权益+商品+另类。")}</p>
        <span>${esc(row.bucketNote || "")}；比较轨道按非权益资产80%主导规则计算。</span>
        ${row.broadEquityNote ? `<span>${esc(row.broadEquityNote)}</span>` : ""}
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

  function formatScore(value) {
    const number = numeric(value);
    return number === null ? "--" : number.toFixed(1);
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

  function rankLayerDefinitions(ranks) {
    return [
      { key: "peer", label: "正式可比池", info: ranks.peer, scores: [28, 21, 9], strict: true },
      { key: "typePeer", label: "同产品类型可比池", info: ranks.typePeer, scores: [22, 16, 7], strict: true },
      { key: "bucket", label: "同基准风险资产权重", info: ranks.bucket, scores: [12, 8, 3], broad: true },
      { key: "productBucket", label: "同产品类型+基准风险资产权重", info: ranks.productBucket, scores: [10, 7, 3], broad: true },
      { key: "productTrack", label: "同产品类型+比较轨道", info: ranks.productTrack, scores: [8, 5, 2], broad: true },
      { key: "track", label: "同比较轨道", info: ranks.track, scores: [5, 3, 1], broad: true },
    ].filter((layer) => layer.info && Number.isFinite(layer.info.rank) && Number.isFinite(layer.info.total));
  }

  function opportunityRankLayerDefinitions(ranks) {
    return [
      { key: "broadBucket", label: "同基准风险资产权重", info: ranks.broadBucket, scores: [32, 24, 10], strict: true },
      { key: "productBroadBucket", label: "同产品类型+基准风险资产权重", info: ranks.productBroadBucket, scores: [24, 18, 8], broad: true },
      { key: "peer", label: "正式可比池校验", info: ranks.peer, scores: [10, 7, 3] },
      { key: "track", label: "比较轨道校验", info: ranks.track, scores: [5, 3, 1] },
    ].filter((layer) => layer.info && Number.isFinite(layer.info.rank) && Number.isFinite(layer.info.total));
  }

  function rankLayerScore(layer) {
    return rankScore(layer.info, layer.scores[0], layer.scores[1], layer.scores[2]);
  }

  function topRankLayers(layers, threshold = 0.25) {
    return layers.filter((layer) => {
      const pct = rankPercent(layer.info);
      return pct !== null && pct <= threshold;
    });
  }

  function bestRankLayer(layers) {
    const ordered = [...layers].sort((a, b) => {
      const aPct = rankPercent(a.info);
      const bPct = rankPercent(b.info);
      const pctDiff = (aPct ?? 99) - (bPct ?? 99);
      if (Math.abs(pctDiff) > 1e-9) return pctDiff;
      const priority = { broadBucket: 0, productBroadBucket: 1, peer: 2, typePeer: 3, bucket: 4, productBucket: 5, productTrack: 6, track: 7 };
      return (priority[a.key] ?? 99) - (priority[b.key] ?? 99);
    });
    return ordered[0] || null;
  }

  function primaryRankInfo(row) {
    const ranks = rankInfo(row);
    const best = bestRankLayer(rankLayerDefinitions(ranks));
    return best?.info || ranks.peer || ranks.typePeer || ranks.bucket || null;
  }

  function advantageEvaluation(row, options = {}) {
    const ranks = rankInfo(row);
    const rankLayerBuilder = options.rankLayerBuilder || rankLayerDefinitions;
    const rankLayers = rankLayerBuilder(ranks);
    const topLayers = topRankLayers(rankLayers, 0.25);
    const eliteLayers = topRankLayers(rankLayers, 0.10);
    const strictTopLayers = topLayers.filter((layer) => layer.strict);
    const broadTopLayers = topLayers.filter((layer) => layer.broad);
    const stats = options.statsResolver ? options.statsResolver(row) : peerStatsFor(row.formalPeerPool);
    const comparisonMode = options.comparisonMode || "formal";
    const data = intervalData(row);
    const ret = numeric(data.return);
    const drawdown = numeric(data.maxDrawdown);
    const volatility = numeric(data.volatility);
    const retDrawdown = returnDrawdownRatio(data);
    const retVolatility = returnVolatilityRatio(data);
    let score = 0;
    const evidence = [];
    const caveats = [];
    let riskPositive = 0;
    let riskNegative = 0;
    let riskAdjustedPositive = 0;

    if (topLayers.length >= 2) {
      evidence.push(`多口径收益靠前：${topLayers.slice(0, 4).map((layer) => `${layer.label}${rankHeadline(layer.info)}`).join("；")}`);
    } else if (topLayers.length === 1) {
      evidence.push(`${topLayers[0].label}收益${rankHeadline(topLayers[0].info)}`);
      caveats.push("优势主要来自单一口径，需要结合更宽分类和风险指标确认");
    }
    if (strictTopLayers.length && broadTopLayers.length) {
      evidence.push(comparisonMode === "broad"
        ? `同基准风险资产权重总池与${broadTopLayers.slice(0, 2).map((layer) => layer.label).join("、")}均居前25%，优势不依赖单一产品类型`
        : `从精确可比池扩大到${broadTopLayers.slice(0, 2).map((layer) => layer.label).join("、")}后仍居前25%，不是单一窄分类偶然靠前`);
    }

    if (stats && stats.count >= 5) {
      if (ret !== null && stats.returnQ75 !== null && ret >= stats.returnQ75) {
        evidence.push(`收益高于同池前25%阈值${formatRateText(stats.returnQ75, true)}`);
      } else if (ret !== null && stats.returnMedian !== null && ret >= stats.returnMedian) {
        evidence.push(`收益高于同池中位数${formatRateText(stats.returnMedian, true)}`);
      } else if (ret !== null && stats.returnMedian !== null) {
        caveats.push(`收益低于同池中位数${formatRateText(stats.returnMedian, true)}`);
      }

      if (drawdown !== null && stats.drawdownQ75 !== null && drawdown >= stats.drawdownQ75) {
        riskPositive += 1;
        evidence.push(`回撤处于同池较优区间，最大回撤${formatRateText(drawdown)}`);
      } else if (drawdown !== null && stats.drawdownMedian !== null && drawdown >= stats.drawdownMedian) {
        riskPositive += 1;
        evidence.push(`回撤优于同池中位数`);
      } else if (drawdown !== null && stats.drawdownMedian !== null) {
        riskNegative += 1;
        caveats.push(`回撤弱于同池中位数`);
      }

      if (volatility !== null && stats.volatilityQ25 !== null && volatility <= stats.volatilityQ25) {
        riskPositive += 1;
        evidence.push(`波动处于同池较低区间，年化波动${formatRateText(volatility)}`);
      } else if (volatility !== null && stats.volatilityMedian !== null && volatility <= stats.volatilityMedian) {
        riskPositive += 1;
        evidence.push(`波动低于同池中位数`);
      } else if (volatility !== null && stats.volatilityMedian !== null) {
        riskNegative += 1;
        caveats.push(`波动高于同池中位数`);
      }

      if (retDrawdown !== null && stats.returnDrawdownQ75 !== null && retDrawdown >= stats.returnDrawdownQ75) {
        riskAdjustedPositive += 1;
        evidence.push(`收益/回撤比${formatRatio(retDrawdown)}，达到同池前25%风险调整区间`);
      } else if (retDrawdown !== null && stats.returnDrawdownMedian !== null && retDrawdown >= stats.returnDrawdownMedian) {
        riskAdjustedPositive += 1;
        evidence.push(`收益/回撤比${formatRatio(retDrawdown)}优于同池中位数`);
      } else if (retDrawdown !== null && stats.returnDrawdownMedian !== null && ret > 0) {
        riskNegative += 1;
        caveats.push(`收益/回撤比低于同池中位数`);
      }

      if (retVolatility !== null && stats.returnVolatilityQ75 !== null && retVolatility >= stats.returnVolatilityQ75) {
        riskAdjustedPositive += 1;
        evidence.push(`收益/波动比${formatRatio(retVolatility)}，达到同池前25%风险调整区间`);
      } else if (retVolatility !== null && stats.returnVolatilityMedian !== null && retVolatility >= stats.returnVolatilityMedian) {
        riskAdjustedPositive += 1;
        evidence.push(`收益/波动比${formatRatio(retVolatility)}优于同池中位数`);
      } else if (retVolatility !== null && stats.returnVolatilityMedian !== null && ret > 0) {
        caveats.push(`收益/波动比低于同池中位数`);
      }
    } else {
      caveats.push("同池有效样本不足5个，不能形成稳定同类结论");
    }

    if (ret === null) caveats.push("当前区间收益缺失，不计算优势强度");
    if (drawdown === null || volatility === null) caveats.push("风险指标不完整，仅能作为收益线索观察");
    if (topLayers.length >= 2 && (riskPositive + riskAdjustedPositive) >= 2) {
      evidence.push("收益领先与风险控制同时成立，综合优势强于单一收益排名靠前");
    }
    if (topLayers.length >= 2 && (riskPositive + riskAdjustedPositive) === 0) {
      caveats.push("虽然多口径收益靠前，但风险控制没有相对优势，暂不判定为明显优势");
    }
    if (riskNegative >= 2 && riskAdjustedPositive === 0) {
      caveats.push("收益靠前伴随回撤或波动压力，客户沟通不能只强调排名");
    }

    const capability = coreAbilityProfile(row, { comparisonMode, rankLayerBuilder });
    score = capability.score;
    if (topLayers.length >= 2 && (riskPositive + riskAdjustedPositive) === 0) score = Math.min(score, 69.9);
    if (riskNegative >= 2 && riskAdjustedPositive === 0) score = Math.max(0, score - 6);
    if (!strictTopLayers.length && score >= 80) {
      score = 79;
      caveats.push(comparisonMode === "broad"
        ? "同产品类型子池表现较好，但基准风险资产权重总池未进入前25%，不升级为明显优势"
        : "扩大口径表现较好，但精确可比池未进入前25%，不升级为明显优势");
    }
    if (topLayers.length < 2 && score >= 65) {
      score = 64;
      caveats.push("未形成多口径共同领先，优势强度不上调到较强以上");
    }
    if (drawdown === null || volatility === null) score = Math.min(score, 35);

    score = Math.max(0, Math.min(100, score));
    const strengthKey = scoreStrength(score);
    const strength = advantageStrengthDefinitions[strengthKey];
    const stars = Math.max(1, Math.min(5, strength.stars - (caveats.length >= 2 ? 1 : 0)));
    const primaryLayer = bestRankLayer(topLayers.length ? topLayers : rankLayers);
    const riskSummary = (riskPositive + riskAdjustedPositive) >= 2
      ? "收益和风险控制共同占优"
      : (riskPositive + riskAdjustedPositive) === 1
        ? "收益之外有一项风险指标支持"
        : "优势主要来自收益，风险侧需谨慎复核";
    const contextSummary = topLayers.length >= 2
      ? `命中${topLayers.length}个靠前口径${strictTopLayers.length && broadTopLayers.length ? (comparisonMode === "broad" ? "，基准风险资产权重总池和产品类型子池同时有效" : "，精确和扩大分类同时有效") : ""}`
      : "仅命中单一或弱排名口径";
    return {
      row,
      score,
      strengthKey,
      strength,
      stars,
      contextSummary,
      riskSummary,
      rankLayers,
      topLayers,
      strictTopLayers,
      broadTopLayers,
      riskPositive,
      riskAdjustedPositive,
      riskNegative,
      evidence: evidence.slice(0, 5),
      caveats: caveats.slice(0, 4),
      primaryRank: primaryLayer?.info || primaryRankInfo(row),
      stats,
      comparisonMode,
      abilityMetrics: capability.metrics,
      abilityCoverage: capability.coverage,
    };
  }

  function bucketLevel(row, field = "bucket") {
    const bucket = clean(row?.[field]);
    if (!/^L\d+$/.test(bucket)) return null;
    const value = Number(bucket.slice(1));
    return Number.isFinite(value) ? value : null;
  }

  function bestRankInfoForInterval(row, interval = state.interval, rankLayerBuilder = rankLayerDefinitions) {
    const layers = rankLayerBuilder(rankInfo(row, interval));
    const best = bestRankLayer(layers);
    return best?.info || null;
  }

  function bestRankPercentForInterval(row, interval = state.interval, rankLayerBuilder = rankLayerDefinitions) {
    return rankPercent(bestRankInfoForInterval(row, interval, rankLayerBuilder));
  }

  function multiPeriodProfile(row, rankLayerBuilder = rankLayerDefinitions) {
    const profileMode = rankLayerBuilder === opportunityRankLayerDefinitions ? "broad" : "formal";
    const cacheKey = `${profileMode}|${rowKey(row)}`;
    if (multiPeriodProfileCache.has(cacheKey)) return multiPeriodProfileCache.get(cacheKey);
    const preferred = ["近3月", "近6月", "上半年", "今年以来", "近1年", "近3年"].filter((interval) => intervals.includes(interval));
    const periods = preferred
      .filter((interval) => intervalReturn(row, interval) !== null)
      .map((interval) => ({
        interval,
        ret: intervalReturn(row, interval),
        rank: bestRankInfoForInterval(row, interval, rankLayerBuilder),
        pct: bestRankPercentForInterval(row, interval, rankLayerBuilder),
      }));
    const ranked = periods.filter((period) => period.pct !== null);
    const top30 = ranked.filter((period) => period.pct <= 0.30);
    const top50 = ranked.filter((period) => period.pct <= 0.50);
    const weak = ranked.filter((period) => period.pct > 0.60);
    const shortPeriods = ranked.filter((period) => ["近3月", "近6月"].includes(period.interval));
    const longPeriods = ranked.filter((period) => ["近1年", "近3年"].includes(period.interval));
    const shortBest = shortPeriods.length ? Math.min(...shortPeriods.map((period) => period.pct)) : null;
    const longBest = longPeriods.length ? Math.min(...longPeriods.map((period) => period.pct)) : null;
    const improving = shortBest !== null && shortBest <= 0.25 && (longBest === null || longBest > 0.45);
    const result = {
      periods,
      ranked,
      top30,
      top50,
      weak,
      shortBest,
      longBest,
      improving,
    };
    multiPeriodProfileCache.set(cacheKey, result);
    return result;
  }

  function riskEfficiencyValue(row) {
    const data = intervalData(row);
    const values = [returnDrawdownRatio(data), returnVolatilityRatio(data)].filter((value) => value !== null);
    return values.length ? values.reduce((sum, value) => sum + value, 0) / values.length : null;
  }

  function multiPeriodAbilityValues(row, rankLayerBuilder) {
    const profile = multiPeriodProfile(row, rankLayerBuilder);
    if (profile.ranked.length < 2) return { winRate: null, consistency: null, profile };
    const percentiles = profile.ranked.map((period) => period.pct);
    const count = percentiles.length;
    const averagePercentile = percentiles.reduce((sum, value) => sum + value, 0) / count;
    const topHalfRate = profile.top50.length / count;
    const coverage = Math.min(1, count / 4);
    const variance = percentiles.reduce((sum, value) => sum + ((value - averagePercentile) ** 2), 0) / count;
    const dispersion = Math.sqrt(variance);
    const worstPercentile = Math.max(...percentiles);
    const winRate = coverage * ((topHalfRate * 0.6) + ((1 - averagePercentile) * 0.4));
    const consistency = coverage * (
      ((1 - averagePercentile) * 0.5)
      + ((1 - worstPercentile) * 0.3)
      + ((1 - Math.min(1, dispersion * 2.5)) * 0.2)
    );
    return { winRate: Math.max(0, winRate), consistency: Math.max(0, consistency), profile };
  }

  function coreAbilityProfile(row, options = {}) {
    const comparisonMode = options.comparisonMode || "formal";
    const rankLayerBuilder = options.rankLayerBuilder || (comparisonMode === "broad" ? opportunityRankLayerDefinitions : rankLayerDefinitions);
    const poolField = comparisonMode === "broad" ? "broadEquityBucket" : "formalPeerPool";
    const poolValue = clean(row?.[poolField]);
    const context = comparisonMode === "broad"
      ? `基准风险资产权重 ${broadBucketLabels[poolValue] || poolValue || "未分档"}`
      : (poolValue || "正式可比池");
    const rankOptions = { poolField, context };
    const periodValues = multiPeriodAbilityValues(row, rankLayerBuilder);
    const data = intervalData(row);
    const efficiency = riskEfficiencyValue(row);
    const metricDefs = [
      {
        key: "return",
        label: "收益竞争力",
        weight: 22,
        value: currentReturn(row),
        rank: metricRank(row, (candidate) => intervalReturn(candidate), { ...rankOptions, higherBetter: true, metricKey: "return" }),
        valueText: formatRateText(currentReturn(row), true),
        explain: "当前区间收益在同类中的位置，权重22%。",
      },
      {
        key: "drawdown",
        label: "回撤控制",
        weight: 18,
        value: numeric(data.maxDrawdown),
        rank: metricRank(row, (candidate) => numeric(intervalData(candidate).maxDrawdown), { ...rankOptions, higherBetter: true, metricKey: "drawdown" }),
        valueText: formatRateText(data.maxDrawdown),
        explain: "最大回撤越浅越好，直接反映客户亏损体验，权重18%。",
      },
      {
        key: "volatility",
        label: "波动控制",
        weight: 12,
        value: numeric(data.volatility),
        rank: metricRank(row, (candidate) => numeric(intervalData(candidate).volatility), { ...rankOptions, higherBetter: false, metricKey: "volatility" }),
        valueText: formatRateText(data.volatility),
        explain: "年化波动越低越好，衡量持有过程平稳度，权重12%。",
      },
      {
        key: "riskEfficiency",
        label: "风险收益效率",
        weight: 20,
        value: efficiency,
        rank: metricRank(row, riskEfficiencyValue, { ...rankOptions, higherBetter: true, metricKey: "risk-efficiency" }),
        valueText: `收益/回撤 ${formatRatio(returnDrawdownRatio(data))} · 收益/波动 ${formatRatio(returnVolatilityRatio(data))}`,
        explain: "合并收益/回撤与收益/波动，避免重复计分，权重20%。",
      },
      {
        key: "multiPeriodWinRate",
        label: "多周期胜率",
        weight: 13,
        value: periodValues.winRate,
        rank: metricRank(row, (candidate) => multiPeriodAbilityValues(candidate, rankLayerBuilder).winRate, { ...rankOptions, higherBetter: true, metricKey: `${comparisonMode}-period-win-rate` }),
        valueText: `${periodValues.profile.top50.length}/${periodValues.profile.ranked.length} 个周期进入前50%`,
        explain: "观察多个区间进入同类前50%的频率，并对周期不足降权，权重13%。",
      },
      {
        key: "multiPeriodConsistency",
        label: "多周期持续性",
        weight: 15,
        value: periodValues.consistency,
        rank: metricRank(row, (candidate) => multiPeriodAbilityValues(candidate, rankLayerBuilder).consistency, { ...rankOptions, higherBetter: true, metricKey: `${comparisonMode}-period-consistency` }),
        valueText: periodValues.profile.ranked.length ? `${periodValues.profile.ranked.length} 个有效周期` : "周期证据不足",
        explain: "综合平均排名、最弱周期和排名离散度，权重15%。",
      },
    ];
    const metrics = metricDefs.map((metric) => ({
      ...metric,
      score: abilityScoreFromRank(metric.rank),
    }));
    const score = metrics.reduce((sum, metric) => sum + (metric.score * metric.weight / 100), 0);
    const coverage = metrics.reduce((sum, metric) => sum + (metric.rank?.rank ? metric.weight : 0), 0);
    return { metrics, score: Number(score.toFixed(1)), coverage };
  }

  function opportunityTypeFor(row, evaluation, profile) {
    const ret = currentReturn(row);
    const level = bucketLevel(row, "broadEquityBucket");
    const riskSupport = evaluation.riskPositive + evaluation.riskAdjustedPositive;
    const hasStrictAndBroad = Boolean(evaluation.strictTopLayers.length && evaluation.broadTopLayers.length);
    const bestPct = evaluation.rankLayers.length
      ? Math.min(...evaluation.rankLayers.map((layer) => rankPercent(layer.info)).filter((value) => value !== null))
      : null;
    const isHighEquity = level !== null && level >= 7;
    if (ret === null) return "notReady";
    if (evaluation.score >= 78 && hasStrictAndBroad && riskSupport >= 2 && evaluation.riskNegative <= 1) return "comprehensive";
    if (riskSupport >= 3 && evaluation.riskNegative <= 1 && ret >= 0 && (evaluation.score >= 58 || evaluation.topLayers.length >= 1)) return "steady";
    if (ret > 0 && bestPct !== null && bestPct <= 0.15 && (isHighEquity || evaluation.riskNegative >= 1 || evaluation.score >= 58)) return "offensive";
    if (profile.improving && ret > 0 && evaluation.riskNegative <= 2) return "improvement";
    if ((evaluation.score >= 50 || evaluation.topLayers.length >= 1) && evaluation.riskNegative <= 2) return "scenario";
    return "notReady";
  }

  function opportunityScore(row, evaluation, profile, typeKey) {
    const rankScores = evaluation.rankLayers
      .map((layer) => abilityScoreFromRank(layer.info))
      .filter((value) => Number.isFinite(value))
      .sort((a, b) => b - a)
      .slice(0, 2);
    const rankEvidenceScore = rankScores.length ? rankScores.reduce((sum, value) => sum + value, 0) / rankScores.length : 0;
    const periodCoverageScore = Math.min(100, (profile.ranked.length / 5) * 100);
    const score = (evaluation.score * 0.86) + (rankEvidenceScore * 0.08) + (periodCoverageScore * 0.06);
    return Number(Math.max(0, Math.min(99.9, score)).toFixed(1));
  }

  function opportunityExplanation(row, evaluation, profile, typeKey) {
    const def = opportunityDefinitions[typeKey] || opportunityDefinitions.notReady;
    const data = intervalData(row);
    const track = clean(row.comparisonTrack) || "未形成轨道";
    const broadBucket = clean(row.broadEquityBucket);
    const broadLabel = broadBucketLabels[broadBucket] || broadBucket || "未分档";
    const rankedPeriods = profile.top30.slice(0, 3).map((period) => `${period.interval}前${(period.pct * 100).toFixed(1)}%`);
    const rankEvidence = evaluation.topLayers.length
      ? evaluation.topLayers.slice(0, 3).map((layer) => exactRankText(layer.info, layer.label)).join("；")
      : exactRankText(evaluation.primaryRank, `基准风险资产权重${broadLabel}`);
    const relativeEvidence = relativeMetricEvidence(row, evaluation).join("；") || "当前缺少可稳定解释的中位数差异";
    const periodText = rankedPeriods.length
      ? `${rankedPeriods.join("；")}`
      : "尚无周期进入前30%";
    const riskSupport = evaluation.riskPositive + evaluation.riskAdjustedPositive;
    const productFit = customerUseCase(row, evaluation, "broad");
    const usageDecision = typeKey === "steady"
      ? "可优先用于同基准权益档的存量替代，卖点是回撤、波动或风险效率，而非追逐最高收益"
      : typeKey === "offensive"
        ? "只作为进攻仓位候选，客户必须接受收益弹性对应的净值波动"
        : typeKey === "improvement"
          ? "只进入观察或小比例试配，不能把短期改善包装成长期能力"
          : riskSupport >= 2
            ? "可进入同基准权益档优先替代清单，收益和风险效率需要同时展示"
            : "只做赛道型备选，不进入普适主推清单";
    const customerFit = `${productFit}；${usageDecision}`;
    const mainClaim = riskSupport >= 2
      ? `${state.interval}收益和风险效率在${broadLabel}内同时靠前`
      : evaluation.riskNegative >= 2
        ? `${state.interval}收益有弹性，但需要用更深回撤或更高波动换取`
        : `${state.interval}在${broadLabel}内存在局部排名优势`;
    const boundary = evaluation.caveats[0] || (profile.weak.length ? `${profile.weak[0].interval}排名落在后40%，多周期稳定性仍需验证` : "不得外推为跨基准风险资产权重的绝对领先");
    const nextCheck = researchNextCheck(row, evaluation, opportunityRankLayerDefinitions);
    const advantageType = researchAdvantageType(evaluation);
    return {
      headline: `${def.badge}：${rankEvidence}；${relativeEvidence}。`,
      customer: `适用场景：${bucketScene(row, "broadEquityBucket")}、${track}。${customerFit}。当前${state.interval}收益${formatRateText(data.return, true)}、最大回撤${formatRateText(data.maxDrawdown)}、年化波动${formatRateText(data.volatility)}；客户需要接受的代价是：${boundary}。`,
      research: `主比较口径为基准风险资产权重${broadLabel}。竞争力性质：${advantageType}。排名证据：${rankEvidence}。相对中位数：${relativeEvidence}。多周期证据：${periodText}。下一步验证：${nextCheck}。`,
      action: `业务动作：${typeKey === "comprehensive" || typeKey === "steady" ? "进入重点替代和客户触达清单" : typeKey === "offensive" || typeKey === "scenario" ? "进入定向客群和场景素材清单" : "只进入跟踪清单"}。主话术：${mainClaim}。目标客群：${customerFit}。素材必须同时展示“${rankEvidence}”和回撤/波动数据。禁用边界：${boundary}。`,
    };
  }

  function opportunityEvaluation(row) {
    const evaluation = advantageEvaluation(row, {
      rankLayerBuilder: opportunityRankLayerDefinitions,
      statsResolver: broadBucketStatsFor,
      comparisonMode: "broad",
    });
    const profile = multiPeriodProfile(row, opportunityRankLayerDefinitions);
    const typeKey = opportunityTypeFor(row, evaluation, profile);
    const score = opportunityScore(row, evaluation, profile, typeKey);
    const explanation = opportunityExplanation(row, evaluation, profile, typeKey);
    const def = opportunityDefinitions[typeKey] || opportunityDefinitions.notReady;
    return {
      row,
      evaluation,
      profile,
      typeKey,
      def,
      score,
      explanation,
    };
  }

  function rankPercentText(info) {
    const pct = rankPercent(info);
    return pct === null ? "--" : `前 ${(pct * 100).toFixed(1)}%`;
  }

  function weakRankText(info) {
    if (!info || !Number.isFinite(info.rank) || !Number.isFinite(info.total) || info.total <= 0) return "排名不计算";
    const bottomPct = ((info.total - info.rank + 1) / info.total) * 100;
    return `第${info.rank.toLocaleString("zh-CN")}/${info.total.toLocaleString("zh-CN")}，处于后${bottomPct.toFixed(1)}%`;
  }

  function exactRankText(info, label = "排名") {
    const pct = rankPercent(info);
    if (!info || !Number.isFinite(info.rank) || !Number.isFinite(info.total) || pct === null) return `${label}不计算`;
    return `${label}第${info.rank.toLocaleString("zh-CN")}/${info.total.toLocaleString("zh-CN")}（前${(pct * 100).toFixed(1)}%）`;
  }

  function formatPercentagePoints(value) {
    const number = numeric(value);
    return number === null ? "--" : `${Math.abs(number * 100).toFixed(2)}个百分点`;
  }

  function relativeMetricEvidence(row, evaluation) {
    const data = intervalData(row);
    const stats = evaluation?.stats;
    if (!stats || stats.count < 5) return [];
    const ret = numeric(data.return);
    const drawdown = numeric(data.maxDrawdown);
    const volatility = numeric(data.volatility);
    const evidence = [];
    if (ret !== null && stats.returnMedian !== null) {
      const diff = ret - stats.returnMedian;
      evidence.push(`收益${diff >= 0 ? "高于" : "低于"}同档中位数${formatPercentagePoints(diff)}`);
    }
    if (drawdown !== null && stats.drawdownMedian !== null) {
      const diff = drawdown - stats.drawdownMedian;
      evidence.push(`最大回撤较同档中位数${diff >= 0 ? "浅" : "深"}${formatPercentagePoints(diff)}`);
    }
    if (volatility !== null && stats.volatilityMedian !== null) {
      const diff = stats.volatilityMedian - volatility;
      evidence.push(`年化波动较同档中位数${diff >= 0 ? "低" : "高"}${formatPercentagePoints(diff)}`);
    }
    return evidence;
  }

  function abilityScoreFromRank(info) {
    if (!info || !Number.isFinite(info.rank) || !Number.isFinite(info.total) || info.total <= 0) return 0;
    const percentileMidpoint = Math.max(0, Math.min(1, (info.rank - 0.5) / info.total));
    return Number((100 * (1 - Math.sqrt(percentileMidpoint))).toFixed(1));
  }

  function metricRank(row, valueFn, { higherBetter = true, context = "", poolField = "formalPeerPool", metricKey = "metric" } = {}) {
    const value = valueFn(row);
    if (value === null) return { value: null, rank: null, total: null, context };
    const pool = clean(row?.[poolField]);
    if (!pool) return { value, rank: null, total: null, context };
    const cacheKey = `${state.interval}|${poolField}|${metricKey}|${higherBetter ? "desc" : "asc"}`;
    if (!abilityMetricRankCache.has(cacheKey)) {
      const groups = new Map();
      rows.forEach((item) => {
        const itemPool = clean(item?.[poolField]);
        const itemValue = valueFn(item);
        if (!itemPool || itemValue === null) return;
        if (!groups.has(itemPool)) groups.set(itemPool, []);
        groups.get(itemPool).push({ row: item, value: itemValue });
      });
      const result = new Map();
      groups.forEach((items, itemPool) => {
        const sorted = items.sort((a, b) => {
          const diff = higherBetter ? (b.value - a.value) : (a.value - b.value);
          return diff || clean(a.row.name).localeCompare(clean(b.row.name), "zh-CN") || clean(a.row.id).localeCompare(clean(b.row.id), "zh-CN");
        });
        let rank = 0;
        let previous = null;
        const byRow = new Map();
        sorted.forEach((item, index) => {
          if (previous === null || Math.abs(item.value - previous) > 1e-12) rank = index + 1;
          byRow.set(rowKey(item.row), { rank, total: sorted.length });
          previous = item.value;
        });
        result.set(itemPool, byRow);
      });
      abilityMetricRankCache.set(cacheKey, result);
    }
    const ranking = abilityMetricRankCache.get(cacheKey)?.get(pool)?.get(rowKey(row));
    return { value, rank: ranking?.rank ?? null, total: ranking?.total ?? null, context: context || pool || "正式可比池" };
  }

  function stabilityValue(row) {
    const profile = multiPeriodProfile(row);
    if (!profile.ranked.length) return null;
    const avgPct = profile.ranked.reduce((sum, period) => sum + period.pct, 0) / profile.ranked.length;
    const score = profile.top30.length * 18 + profile.top50.length * 6 - profile.weak.length * 10 + (profile.improving ? 6 : 0) + Math.max(0, 40 - avgPct * 40);
    return Math.max(0, score);
  }

  function businessOpportunityRank(item) {
    const broadBucket = clean(item.row.broadEquityBucket);
    const gfItems = filteredRows({ requireCurrent: true })
      .filter((row) => row.isGuangfa && matchesAdvantageProductScope(row) && clean(row.broadEquityBucket) === broadBucket)
      .map(opportunityEvaluation)
      .filter((candidate) => candidate.typeKey !== "notReady" && candidate.score >= 45)
      .sort((a, b) => (b.score - a.score) || clean(a.row.name).localeCompare(clean(b.row.name), "zh-CN"));
    let rank = 0;
    let previous = null;
    let resultRank = null;
    gfItems.forEach((candidate, index) => {
      if (previous === null || Math.abs(candidate.score - previous) > 1e-9) rank = index + 1;
      if (rowKey(candidate.row) === rowKey(item.row)) resultRank = rank;
      previous = candidate.score;
    });
    const bucketLabel = broadBucketLabels[broadBucket] || broadBucket || "未分档";
    return { value: item.score, rank: resultRank, total: gfItems.length, context: `广发${bucketLabel}机会池` };
  }

  function abilityProfile(item) {
    return item.evaluation?.abilityMetrics || coreAbilityProfile(item.row, {
      comparisonMode: "broad",
      rankLayerBuilder: opportunityRankLayerDefinitions,
    }).metrics;
  }

  function strongestAbilities(metrics, limit = 2) {
    return [...metrics]
      .filter((metric) => metric.key !== "business" && metric.score > 0)
      .sort((a, b) => b.score - a.score)
      .slice(0, limit);
  }

  function weakestAbility(metrics) {
    return [...metrics]
      .filter((metric) => metric.key !== "business" && metric.rank?.rank && metric.rank?.total)
      .sort((a, b) => a.score - b.score)[0] || null;
  }

  function metricRankEvidence(metric) {
    if (!metric) return "指标不足";
    return `${metric.label}${exactRankText(metric.rank, "")}，指标值${metric.valueText}`;
  }

  function opportunityStrengthDegree(item, metrics) {
    const usable = metrics.filter((metric) => metric.key !== "business" && metric.rank?.rank && metric.rank?.total);
    const top10 = usable.filter((metric) => rankPercent(metric.rank) <= 0.10);
    const top25 = usable.filter((metric) => rankPercent(metric.rank) <= 0.25);
    if (item.score >= 85 && top10.length >= 2) return `${item.def.badge}证据很强：${top10.length}项能力进入同风险权重产品前10%`;
    if (item.score >= 72 && top25.length >= 2) return `${item.def.badge}证据较强：${top25.length}项能力进入同风险权重产品前25%`;
    if (top25.length >= 1) return `${item.def.badge}明确但集中：${top25.length}项能力进入同风险权重产品前25%`;
    return `${item.def.badge}仍属阶段性线索：当前证据更适合观察或场景化使用`;
  }

  function opportunityCardFocus(item) {
    const metrics = abilityProfile(item);
    const strong = strongestAbilities(metrics, 2);
    const weak = weakestAbility(metrics);
    const degree = opportunityStrengthDegree(item, metrics);
    const strongText = strong.length ? strong.map(metricRankEvidence).join("；") : item.explanation.headline;
    const weakPct = rankPercent(weak?.rank);
    const boundary = weak && weakPct !== null && weakPct > 0.50
      ? `；短板是${weak.label}${weakRankText(weak.rank)}`
      : "";
    return `${degree}。${strongText}${boundary}。`;
  }

  function renderAbilityRadar(metrics) {
    const size = 300;
    const cx = 150;
    const cy = 150;
    const radius = 96;
    const axes = metrics.slice(0, 6);
    const pointFor = (index, score = 100, scale = 1) => {
      const angle = (-90 + (360 / axes.length) * index) * Math.PI / 180;
      const r = radius * scale * (score / 100);
      return [cx + Math.cos(angle) * r, cy + Math.sin(angle) * r];
    };
    const grid = [0.25, 0.5, 0.75, 1].map((scale) => {
      const points = axes.map((_, index) => pointFor(index, 100, scale).map((value) => value.toFixed(1)).join(",")).join(" ");
      return `<polygon class="mixed-profile-radar-grid" points="${points}"></polygon>`;
    }).join("");
    const spokes = axes.map((metric, index) => {
      const end = pointFor(index, 100, 1);
      const label = pointFor(index, 100, 1.17);
      return `<line class="mixed-profile-radar-spoke" x1="${cx}" y1="${cy}" x2="${end[0].toFixed(1)}" y2="${end[1].toFixed(1)}"></line>
        <text class="mixed-profile-radar-label" x="${label[0].toFixed(1)}" y="${label[1].toFixed(1)}" text-anchor="middle">${esc(metric.label)}</text>`;
    }).join("");
    const points = axes.map((metric, index) => pointFor(index, metric.score, 1).map((value) => value.toFixed(1)).join(",")).join(" ");
    return `<svg class="mixed-profile-radar" viewBox="0 0 ${size} ${size}" role="img" aria-label="产品六维能力画像">
      ${grid}
      ${spokes}
      <polygon class="mixed-profile-radar-area" points="${points}"></polygon>
      ${axes.map((metric, index) => {
        const point = pointFor(index, metric.score, 1);
        return `<circle class="mixed-profile-radar-point" cx="${point[0].toFixed(1)}" cy="${point[1].toFixed(1)}" r="4"></circle>`;
      }).join("")}
    </svg>`;
  }

  function customerUseCase(row, evaluation, mode = "formal") {
    const level = bucketLevel(row, mode === "broad" ? "broadEquityBucket" : "bucket");
    const track = clean(row.comparisonTrack);
    const name = clean(row.name);
    const category = productCategory(row).label;
    const fundType = clean(row.fundMainType);
    if (track.includes("商品")) {
      if (/黄金|金矿/.test(name)) return "只面向已有黄金配置需求、需要分散股债风险的客户，不替代现金管理或固收产品";
      if (/原油|油气|能源/.test(name)) return "只面向理解商品周期和高波动特征的战术配置客户，不作为长期核心底仓";
      return "只面向有明确商品配置目的的客户，必须单列商品价格波动风险";
    }
    if (track.includes("另类")) {
      if (/REIT|不动产|基础设施/i.test(name)) return "面向需要基础设施资产分散配置、能接受估值和流动性波动的客户";
      return "面向有明确另类资产配置需求的客户，不与货币或纯债产品直接替代";
    }
    if (track.includes("货币") && (level === null || level <= 1)) return "面向现金管理和短期资金停泊需求，核心价值应看流动性与收益稳定性，不以高收益弹性作为卖点";
    if (track.includes("债券") && (level === null || level <= 3)) return "面向希望控制权益暴露、在固收底仓上争取增强收益的客户，适合做同风险档存量替代";
    if (fundType.includes("QDII") || /全球|海外|美国|香港|美元/.test(name)) return "面向已有海外配置需求并能承担汇率、海外市场波动的客户，不作为单一人民币资产替代";
    if (level !== null && level >= 8) return category.includes("投顾")
      ? "面向能承受高权益波动、希望通过组合化管理获取长期权益弹性的客户"
      : "面向能承受高权益波动、对该基金风格或赛道有长期认知的客户";
    if (level !== null && level >= 4) return category.includes("投顾")
      ? "面向希望在一只组合内平衡收益弹性与回撤约束的中高风险客户"
      : "面向有中等以上风险承受能力、需要在同权益暴露下筛选更优产品的客户";
    return category.includes("投顾")
      ? "面向偏稳健、希望用组合管理降低单基金选择成本的客户"
      : "面向偏稳健、以同风险档产品替代为主要诉求的客户";
  }

  function researchAdvantageType(evaluation) {
    if (evaluation.riskPositive >= 2 && evaluation.riskAdjustedPositive >= 1) return "收益、回撤/波动与风险效率同时成立，属于可用于替代决策的综合优势";
    if (evaluation.riskAdjustedPositive >= 2 && evaluation.riskNegative >= 2) return "绝对回撤和波动偏弱，但高收益使风险调整指标仍靠前；属于高风险下的进攻效率，不属于稳健或综合优势";
    if (evaluation.riskAdjustedPositive >= 2) return "核心强项是单位风险换取的收益，不是单纯依赖高波动抬升收益";
    if (evaluation.riskPositive >= 2) return "核心强项是回撤与波动控制，收益领先程度应作为第二判断条件";
    if (evaluation.topLayers.length >= 2 && evaluation.riskNegative >= 2) return "核心强项集中在收益弹性，但回撤或波动代价明显，不属于综合优势";
    if (evaluation.topLayers.length >= 2) return "核心强项是多个排名口径同时靠前，仍需更多风险和跨周期证据确认持续性";
    return "目前只形成局部排名线索，证据不足以支持稳定竞争力结论";
  }

  function researchNextCheck(row, evaluation, rankLayerBuilder = rankLayerDefinitions) {
    const profile = multiPeriodProfile(row, rankLayerBuilder);
    if (profile.weak.length) return `优先复核${profile.weak.slice(0, 2).map((period) => `${period.interval}后40%`).join("、")}的落后原因，区分风格阶段性失效还是持续能力不足`;
    if (profile.ranked.length < 3) return "可用排名周期少于3个，先补足中长期区间再判断稳定能力";
    if (evaluation.riskNegative >= 2) return "重点拆解高收益是否主要由更高权益弹性、行业集中或更深回撤换取";
    if (evaluation.caveats.length) return evaluation.caveats.slice(0, 2).join("；");
    return "继续跟踪后续区间排名是否维持在前30%，防止单一区间胜出被误判为长期能力";
  }

  function advantageCustomerText(row, evaluation) {
    const data = intervalData(row);
    const track = clean(row.comparisonTrack) || "未形成轨道";
    const rankEvidence = evaluation.topLayers.length
      ? evaluation.topLayers.slice(0, 2).map((layer) => exactRankText(layer.info, layer.label)).join("；")
      : exactRankText(evaluation.primaryRank, "主要口径");
    const relative = relativeMetricEvidence(row, evaluation);
    const riskSupport = evaluation.riskPositive + evaluation.riskAdjustedPositive;
    const fit = customerUseCase(row, evaluation);
    const value = riskSupport >= 3
      ? "可把它作为同类替代候选，因为收益、回撤/波动和风险效率至少有三项相对证据"
      : riskSupport >= 2
        ? "可以强调收益改善并未明显牺牲持有体验，但仍需保留区间和样本边界"
        : evaluation.riskNegative >= 2
          ? "只能作为收益弹性工具，不能包装成兼顾收益和稳健体验"
          : "当前更适合作为备选观察，不宜据此推动大范围存量替代";
    const boundary = evaluation.caveats[0] || "结论仅在当前可比池和区间内成立";
    return `适用客户：${fit}。实际价值：${value}。产品证据：${rankEvidence}；${relative.join("；") || `当前${state.interval}收益${formatRateText(data.return, true)}、最大回撤${formatRateText(data.maxDrawdown)}、年化波动${formatRateText(data.volatility)}`}。使用边界：${bucketScene(row)}、${track}；${boundary}。`;
  }

  function advantageResearchText(row, evaluation) {
    const profile = multiPeriodProfile(row);
    const ranks = evaluation.topLayers.length
      ? evaluation.topLayers.slice(0, 4).map((layer) => exactRankText(layer.info, layer.label)).join("；")
      : exactRankText(evaluation.primaryRank, "主要口径");
    const periods = profile.top30.length
      ? profile.top30.slice(0, 4).map((period) => `${period.interval}前${(period.pct * 100).toFixed(1)}%`).join("；")
      : "没有周期进入前30%";
    const relative = relativeMetricEvidence(row, evaluation).join("；") || "同池中位数差异不足以形成稳定判断";
    const caveat = evaluation.caveats.length ? evaluation.caveats.slice(0, 2).join("；") : "暂无明显口径冲突";
    const advantageType = researchAdvantageType(evaluation);
    const nextCheck = researchNextCheck(row, evaluation);
    return `竞争力判断：${advantageType}，优势强度${formatScore(evaluation.score)}分。排名证据：${ranks}。同池差异：${relative}。跨周期表现：${periods}。下一步验证：${nextCheck}。补充边界：${caveat}。`;
  }

  function advantageMarketingText(row, evaluation) {
    const track = clean(row.comparisonTrack) || "当前可比池";
    const bestLayer = evaluation.topLayers[0] || bestRankLayer(evaluation.rankLayers);
    const rankClaim = bestLayer ? exactRankText(bestLayer.info, bestLayer.label) : "暂无稳定收益排名";
    const riskSupport = evaluation.riskPositive + evaluation.riskAdjustedPositive;
    const lead = riskSupport >= 2
      ? `“${rankClaim}，且回撤/波动或风险调整收益有支撑”`
      : evaluation.riskNegative >= 2
        ? `“${rankClaim}，突出收益弹性但同步披露风险代价”`
        : `“${rankClaim}，作为${track}场景备选”`;
    const audience = customerUseCase(row, evaluation);
    const action = evaluation.strengthKey === "obvious" || evaluation.strengthKey === "strong" ? "可进入重点话术池" : evaluation.strengthKey === "partial" ? "只进入备选素材池" : "仅保留观察";
    const boundary = evaluation.caveats[0] || "不得写成跨风险档、跨资产结构的全市场领先";
    const proof = relativeMetricEvidence(row, evaluation).slice(0, 2).join("；") || `${state.interval}收益${formatRateText(intervalData(row).return, true)}、最大回撤${formatRateText(intervalData(row).maxDrawdown)}`;
    return `营销动作：${action}。主张只聚焦${lead}，并用“${proof}”做数据支撑。目标客群：${audience}。材料必须同时展示区间、排名分母、最大回撤和波动率。禁止表述：${boundary}。`;
  }

  function advantageFocusText(evaluation) {
    const top = evaluation.topLayers.length
      ? evaluation.topLayers.slice(0, 2).map((layer) => exactRankText(layer.info, layer.label)).join("；")
      : exactRankText(evaluation.primaryRank, "主要口径");
    const relative = relativeMetricEvidence(evaluation.row, evaluation).slice(0, 2).join("；");
    return `${evaluation.strength.title}（${formatScore(evaluation.score)}分）：${top}${relative ? `；${relative}` : ""}`;
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
          <b>${formatScore(evaluation.score)}</b>
          <small>优势强度</small>
        </div>
      </div>
      <div class="mixed-highlight-rank">${formatRank(evaluation.primaryRank)}</div>
      <div class="mixed-highlight-basis"><b>优势判断</b>${esc(advantageFocusText(evaluation))}</div>
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
      return `<details class="panel mixed-highlight-panel mixed-highlight-collapsible" data-advantage-panel${advantagePanelOpen ? " open" : ""}>
        <summary class="mixed-highlight-summary">
          <div><h2>广发优势产品</h2><p>当前筛选条件下没有收益、回撤、波动指标完整的广发产品。</p></div>
          <span class="pill">0 个产品</span>
        </summary>
        <div class="mixed-highlight-body">
          <div class="mixed-highlight-actions">${scopeControl}${restoreButton}</div>
        </div>
      </details>`;
    }
    if (!gfRows.length) {
      return `<details class="panel mixed-highlight-panel mixed-highlight-collapsible" data-advantage-panel${advantagePanelOpen ? " open" : ""}>
        <summary class="mixed-highlight-summary">
          <div><h2>广发优势产品</h2><p>当前优势区筛选为“${esc(advantageProductScopeLabel())}”，没有指标完整的匹配产品。</p></div>
          <span class="pill">0 个${esc(advantageProductScopeLabel())}</span>
        </summary>
        <div class="mixed-highlight-body">
          <div class="mixed-highlight-actions">${scopeControl}${restoreButton}</div>
        </div>
      </details>`;
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
    return `<details class="panel mixed-highlight-panel mixed-highlight-collapsible" data-advantage-panel${advantagePanelOpen ? " open" : ""}>
      <summary class="mixed-highlight-summary">
        <div>
          <h2>广发优势产品</h2>
          <p>按 ${esc(state.interval)} 收益区间联动；默认折叠，展开查看产品卡和详细证据。</p>
        </div>
        <span class="pill">${gfRows.length.toLocaleString("zh-CN")} 个${esc(advantageProductScopeLabel())}</span>
      </summary>
      <div class="mixed-highlight-body">
        <div class="mixed-highlight-actions">
          ${scopeControl}
          ${restoreButton}
        </div>
        <div class="mixed-advantage-method">
          <strong>评分口径</strong>
          <span>总分按六维加权：收益22%、回撤18%、波动12%、风险收益效率20%、多周期胜率13%、多周期持续性15%。</span>
          <span>单项分数采用非线性同类分位映射，前10%内部继续拉开差距；周期不足会降低胜率和持续性得分。</span>
          <span>星级表示当前筛选条件下的相对优势强度，不代表跨风险档绝对优劣。</span>
        </div>
        <div class="mixed-strength-groups">${renderedGroups || '<div class="empty">当前筛选下没有达到优势评分门槛的广发产品。</div>'}</div>
      </div>
    </details>`;
  }

  function renderOpportunityCard(item) {
    const row = item.row;
    const data = intervalData(row);
    const category = productCategory(row);
    const selectedClass = row.id === state.selectedId ? " is-selected" : "";
    const topPeriodText = item.profile.top30.length
      ? item.profile.top30.slice(0, 3).map((period) => `${period.interval}前${(period.pct * 100).toFixed(1)}%`).join(" / ")
      : "多周期待观察";
    const focusText = opportunityCardFocus(item);
    const riskSupport = item.evaluation.riskPositive + item.evaluation.riskAdjustedPositive;
    return `<li class="mixed-advantage-card mixed-opportunity-card ${category.className} ${item.def.className}${selectedClass}" data-profile-id="${esc(row.id)}" tabindex="0" role="button" aria-label="打开${esc(row.name)}能力画像">
      <div class="mixed-advantage-card-head">
        <div class="mixed-highlight-main">
          ${row.detailUrl ? `<a href="${esc(row.detailUrl)}">${esc(row.name)}</a>` : `<strong>${esc(row.name)}</strong>`}
          <span>${esc(row.code || row.id)} · ${esc(category.label)} · ${esc(broadBucketLabels[row.broadEquityBucket] || row.broadEquityBucket || "基准风险资产权重未分档")}</span>
        </div>
        <div class="mixed-advantage-score">
          ${renderStars(item.def.stars)}
          <b>${formatScore(item.score)}</b>
          <small>机会分</small>
        </div>
      </div>
      <div class="mixed-opportunity-badge-row">
        <span class="mixed-opportunity-badge">${esc(item.def.badge)}</span>
        <span>${esc(topPeriodText)}</span>
      </div>
      <div class="mixed-opportunity-focus"><b>为什么值得看</b><p>${esc(focusText)}</p></div>
      <div class="mixed-highlight-metrics">
        <span>${esc(state.interval)} ${formatRate(data.return, true)}</span>
        <span>${esc(rankPercentText(item.evaluation.primaryRank))}</span>
        <span>风险支持 ${esc(String(riskSupport))} 项</span>
      </div>
      <div class="mixed-opportunity-actions">
        <button class="mixed-highlight-select" type="button" data-profile-open="${esc(row.id)}">能力画像</button>
        <button class="mixed-highlight-select" type="button" data-opportunity-select="${esc(row.id)}">查看同基准风险资产权重点阵</button>
      </div>
    </li>`;
  }

  function renderOpportunityGroup(key, items) {
    const def = opportunityDefinitions[key];
    return `<section class="mixed-strength-section mixed-opportunity-section ${esc(def.className)}">
      <div class="mixed-strength-head">
        <div>
          <h3>${renderStars(def.stars)} ${esc(def.title)}</h3>
          <p>${esc(def.subtitle)}</p>
        </div>
        <span>${items.length.toLocaleString("zh-CN")} 个</span>
      </div>
      <ol class="mixed-strength-grid">${items.map(renderOpportunityCard).join("")}</ol>
    </section>`;
  }

  function renderBusinessOpportunities(list) {
    const allGfRows = list.filter((row) => row.isGuangfa && hasCurrentMetrics(row));
    const scopedRows = allGfRows.filter(matchesAdvantageProductScope);
    const restoreButton = restoreFilterSnapshot
      ? '<button class="mixed-restore-filter" type="button" data-restore-highlight-filter>恢复筛选</button>'
      : "";
    const scopeControl = renderAdvantageScopeControl(allGfRows);
    if (!allGfRows.length) {
      return `<details class="panel mixed-highlight-panel mixed-highlight-collapsible mixed-opportunity-panel" data-opportunity-panel${opportunityPanelOpen ? " open" : ""}>
        <summary class="mixed-highlight-summary mixed-opportunity-summary">
          <div><h2>广发业务机会分析</h2><p>当前筛选条件下没有收益、回撤、波动完整的广发产品，暂不生成机会卡。</p></div>
          <span class="pill">0 个产品</span>
          <span class="mixed-opportunity-toggle" aria-hidden="true"></span>
        </summary>
        <div class="mixed-highlight-body"><div class="mixed-highlight-actions">${scopeControl}${restoreButton}</div></div>
      </details>`;
    }
    if (!scopedRows.length) {
      return `<details class="panel mixed-highlight-panel mixed-highlight-collapsible mixed-opportunity-panel" data-opportunity-panel${opportunityPanelOpen ? " open" : ""}>
        <summary class="mixed-highlight-summary mixed-opportunity-summary">
          <div><h2>广发业务机会分析</h2><p>当前选择“${esc(advantageProductScopeLabel())}”，没有收益、回撤、波动完整的匹配产品。</p></div>
          <span class="pill">0 个${esc(advantageProductScopeLabel())}</span>
          <span class="mixed-opportunity-toggle" aria-hidden="true"></span>
        </summary>
        <div class="mixed-highlight-body"><div class="mixed-highlight-actions">${scopeControl}${restoreButton}</div></div>
      </details>`;
    }
    const evaluated = scopedRows
      .map(opportunityEvaluation)
      .sort((a, b) => (b.score - a.score) || compareValues(currentReturn(a.row), currentReturn(b.row), "desc") || clean(a.row.name).localeCompare(clean(b.row.name), "zh-CN"));
    const displayItems = evaluated.filter((item) => item.typeKey !== "notReady" && item.score >= 45).slice(0, 24);
    const notReadyCount = evaluated.filter((item) => item.typeKey === "notReady" || item.score < 45).length;
    const groups = { comprehensive: [], steady: [], offensive: [], scenario: [], improvement: [] };
    displayItems.forEach((item) => groups[item.typeKey]?.push(item));
    const renderedGroups = ["comprehensive", "steady", "offensive", "scenario", "improvement"]
      .filter((key) => groups[key].length)
      .map((key) => renderOpportunityGroup(key, groups[key]))
      .join("");
    return `<details class="panel mixed-highlight-panel mixed-highlight-collapsible mixed-opportunity-panel" data-opportunity-panel${opportunityPanelOpen ? " open" : ""}>
      <summary class="mixed-highlight-summary mixed-opportunity-summary">
        <div>
          <h2>广发业务机会分析</h2>
          <p>以基准风险资产权重作为主排名口径。六维分位加权决定机会强度，同产品类型、正式可比池和比较轨道作为解释与校验证据。</p>
        </div>
        <div class="mixed-highlight-actions">
          <span class="pill">${scopedRows.length.toLocaleString("zh-CN")} 个${esc(advantageProductScopeLabel())}</span>
          <span class="pill">${notReadyCount.toLocaleString("zh-CN")} 个暂不主推/证据不足</span>
        </div>
        <span class="mixed-opportunity-toggle" aria-hidden="true"></span>
      </summary>
      <div class="mixed-highlight-body">
        <div class="mixed-highlight-actions">${scopeControl}${restoreButton}</div>
        <div class="mixed-advantage-method">
          <strong>判断口径</strong>
          <span>六维权重：收益22%、回撤18%、波动12%、风险收益效率20%、多周期胜率13%、多周期持续性15%。</span>
          <span>评分使用同基准风险资产权重的非线性分位，前排继续拉开差距；A1要求收益、风险效率和跨周期证据同时成立。</span>
          <span>B1只做明确赛道机会，B2只做近期改善观察，不能包装成长期能力。</span>
          <span>点击卡片看能力画像；点击“查看同基准风险资产权重点阵”会保留该基准风险资产权重并默认选中产品。</span>
        </div>
        <div class="mixed-strength-groups">${renderedGroups || '<div class="empty">当前筛选下没有达到业务机会门槛的广发产品。</div>'}</div>
      </div>
    </details>`;
  }

  function renderAbilityTable(metrics) {
    return `<div class="mixed-profile-ability-table">
      ${metrics.map((metric) => `<div class="mixed-profile-ability-row">
        <div>
          <strong>${esc(metric.label)}</strong>
          <span>${esc(metric.explain)}</span>
        </div>
        <b>${esc(formatScore(metric.score))}</b>
        <em>${esc(metric.valueText)}</em>
        <small>${metric.rank?.rank ? `${esc(metric.rank.rank.toLocaleString("zh-CN"))}/${esc(metric.rank.total.toLocaleString("zh-CN"))} · ${esc(rankPercentText(metric.rank))}` : "不计算"}</small>
      </div>`).join("")}
    </div>`;
  }

  function renderOpportunityProfileModal(list) {
    if (!opportunityProfileId) return "";
    const row = rows.find((item) => item.id === opportunityProfileId);
    if (!row) return "";
    const item = opportunityEvaluation(row);
    const metrics = abilityProfile(item);
    const category = productCategory(row);
    const strong = strongestAbilities(metrics, 3);
    const data = intervalData(row);
    return `<div class="mixed-profile-modal" data-profile-overlay>
      <article class="mixed-profile-dialog ${category.className}" role="dialog" aria-modal="true" aria-label="${esc(row.name)}能力画像">
        <button class="mixed-profile-close" type="button" data-profile-close aria-label="关闭">×</button>
        <header class="mixed-profile-head">
          <div>
            <span class="mixed-opportunity-badge">${esc(item.def.badge)}</span>
            <h2>${esc(row.name)}</h2>
            <p>${esc(row.code || row.id)} · ${esc(category.label)} · ${esc(row.formalPeerPool || "未进入正式可比池")}</p>
          </div>
          <div class="mixed-profile-score">
            ${renderStars(item.def.stars)}
            <b>${formatScore(item.score)}</b>
            <span>业务机会分</span>
          </div>
        </header>
        <section class="mixed-profile-summary">
          <div>
            <strong>一句话结论</strong>
            <p>${esc(opportunityCardFocus(item))}</p>
          </div>
          <div>
            <strong>最强能力</strong>
            <p>${esc(strong.length ? strong.map((metric) => `${metric.label}${rankPercentText(metric.rank)}`).join("、") : "暂未形成稳定强项")}</p>
          </div>
          <div>
            <strong>当前区间</strong>
            <p>${esc(state.interval)}收益 ${formatRateText(data.return, true)}；回撤 ${formatRateText(data.maxDrawdown)}；波动 ${formatRateText(data.volatility)}</p>
          </div>
        </section>
        <section class="mixed-profile-body">
          <div class="mixed-profile-radar-card">
            <h3>六维能力画像</h3>
            <p class="desc">兼顾客户盈利体验与投研评价，单项均在同一基准风险资产权重内比较。</p>
            ${renderAbilityRadar(metrics)}
          </div>
          <div class="mixed-profile-evidence-card">
            <h3>指标排名证据</h3>
            ${renderAbilityTable(metrics)}
          </div>
        </section>
        <section class="mixed-profile-views">
          <div><b>客户角度</b><p>${esc(item.explanation.customer)}</p></div>
          <div><b>投研角度</b><p>${esc(item.explanation.research)}</p></div>
          <div><b>运营动作</b><p>${esc(item.explanation.action)}</p></div>
        </section>
        <footer class="mixed-profile-actions">
          ${row.detailUrl ? `<a class="mixed-profile-link" href="${esc(row.detailUrl)}">打开详情页</a>` : ""}
          <button class="mixed-highlight-select" type="button" data-profile-select="${esc(row.id)}">查看同基准风险资产权重点阵</button>
          <button class="mixed-profile-close-secondary" type="button" data-profile-close>关闭</button>
        </footer>
      </article>
    </div>`;
  }

  function selectAdvantageProduct(id, mode = "formal") {
    const row = rows.find((item) => item.id === id);
    if (!row) return;
    if (!restoreFilterSnapshot) restoreFilterSnapshot = filterSnapshot();
    if (mode === "broad") {
      state.productType = "all";
      state.buckets = [];
      state.broadBuckets = clean(row.broadEquityBucket) ? [clean(row.broadEquityBucket)] : [];
      state.comparisonTracks = [];
      state.fundMainTypes = [];
    } else {
      state.productType = clean(row.productType) || "all";
      state.buckets = clean(row.bucket) ? [clean(row.bucket)] : [];
      state.broadBuckets = clean(row.broadEquityBucket) ? [clean(row.broadEquityBucket)] : [];
      state.comparisonTracks = clean(row.comparisonTrack) ? [clean(row.comparisonTrack)] : [];
      state.fundMainTypes = clean(row.fundMainType) ? [clean(row.fundMainType)] : [];
    }
    state.gfOnly = false;
    state.search = "";
    state.selectedId = row.id;
    state.tableSort = { key: "return", direction: "desc" };
    openMultiKey = "";
    opportunityProfileId = "";
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
            <th>${sortHeader("bucketRank", "基准风险资产权重排名")}</th>
            <th>${sortHeader("peerRank", "基准可比归档排名")}</th>
            <th>${sortHeader("typePeerRank", "同产品类型排名")}</th>
            <th>${sortHeader("drawdown", "最大回撤")}</th>
            <th>${sortHeader("volatility", "年化波动")}</th>
            <th>${sortHeader("broadEquityBucket", "基准风险资产权重")}</th>
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
              <td>${esc(broadBucketLabels[row.broadEquityBucket] || row.broadEquityBucket || "未分档")}</td>
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
    const includedUnbucketed = Number(pack.meta?.includedUnbucketedRowCount || 0);
    return `<section class="panel mixed-note-panel">
      <h2>数据口径</h2>
      <p>投顾策略与策略列表使用同一可查询口径，公募基金按主份额展示。未分档产品 ${includedUnbucketed.toLocaleString("zh-CN")} 条、缺完整收益风险区间产品 ${includedNoComplete.toLocaleString("zh-CN")} 条均保留在列表，缺失指标显示为 --。基准风险资产权重统一按业绩基准中的权益、商品和另类风险资产合计权重划分 L0—L10，作为策略分类和排名的首层口径；正式比较再结合非权益轨道、地域和产品类型。点阵只绘制当前区间收益和风险坐标齐全的产品。所有百分比字段按源数据小数比率乘以 100 展示。</p>
    </section>`;
  }

  function queueSearch(value) {
    window.clearTimeout(searchTimer);
    searchTimer = window.setTimeout(() => {
      state.search = value;
      state.selectedId = "";
      restoreFilterSnapshot = null;
      openMultiKey = "";
      opportunityProfileId = "";
      restoreSearchFocus = true;
      resetPage();
      render();
    }, 600);
  }

  function bindEvents() {
    const productType = document.getElementById("mixedProductType");
    const channel = document.getElementById("mixedChannel");
    const institution = document.getElementById("mixedInstitution");
    const interval = document.getElementById("mixedInterval");
    const riskMetric = document.getElementById("mixedRiskMetric");
    const gfOnly = document.getElementById("mixedGfOnly");
    const search = document.getElementById("mixedSearch");
    productType?.addEventListener("change", (event) => { state.productType = event.target.value; state.selectedId = ""; restoreFilterSnapshot = null; openMultiKey = ""; opportunityProfileId = ""; resetPage(); syncFilterUrl(); render(); });
    channel?.addEventListener("change", (event) => { state.channel = event.target.value === "all" ? "" : event.target.value; if (state.channel) state.productType = "投顾策略"; state.selectedId = ""; restoreFilterSnapshot = null; openMultiKey = ""; opportunityProfileId = ""; resetPage(); syncFilterUrl(); render(); });
    institution?.addEventListener("change", (event) => { state.institution = event.target.value === "all" ? "" : event.target.value; if (state.institution) state.productType = "投顾策略"; state.selectedId = ""; restoreFilterSnapshot = null; openMultiKey = ""; opportunityProfileId = ""; resetPage(); syncFilterUrl(); render(); });
    interval?.addEventListener("change", (event) => { state.interval = event.target.value; state.selectedId = ""; restoreFilterSnapshot = null; openMultiKey = ""; opportunityProfileId = ""; resetPage(); render(); });
    riskMetric?.addEventListener("change", (event) => { state.riskMetric = event.target.value; state.selectedId = ""; restoreFilterSnapshot = null; openMultiKey = ""; opportunityProfileId = ""; resetPage(); render(); });
    gfOnly?.addEventListener("change", (event) => { state.gfOnly = event.target.checked; state.selectedId = ""; restoreFilterSnapshot = null; openMultiKey = ""; opportunityProfileId = ""; resetPage(); render(); });
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
        opportunityProfileId = "";
        resetPage();
        syncFilterUrl();
        render();
      });
    });
    root.querySelectorAll("[data-global-filter]").forEach((node) => {
      node.addEventListener("change", (event) => {
        B.setGlobalStrategyFilter?.(node.getAttribute("data-global-filter"), event.target.checked, { syncUrl: false });
        state.selectedId = "";
        restoreFilterSnapshot = null;
        opportunityProfileId = "";
        resetPage();
        syncFilterUrl();
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
    root.querySelectorAll("[data-advantage-panel]").forEach((node) => {
      node.addEventListener("toggle", () => {
        advantagePanelOpen = node.open;
      });
    });
    root.querySelectorAll("[data-opportunity-panel]").forEach((node) => {
      node.addEventListener("toggle", () => {
        opportunityPanelOpen = node.open;
      });
    });
    root.querySelectorAll("[data-advantage-scope]").forEach((node) => {
      node.addEventListener("click", () => {
        const scope = node.getAttribute("data-advantage-scope") || "all";
        state.advantageProductScope = ["all", "strategy", "fund"].includes(scope) ? scope : "all";
        opportunityProfileId = "";
        render();
      });
    });
    search?.addEventListener("compositionstart", () => {
      searchComposing = true;
      window.clearTimeout(searchTimer);
    });
    search?.addEventListener("compositionend", (event) => {
      searchComposing = false;
      queueSearch(event.target.value);
    });
    search?.addEventListener("input", (event) => {
      if (searchComposing || event.isComposing) return;
      queueSearch(event.target.value);
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
    root.querySelectorAll("[data-opportunity-select]").forEach((node) => {
      node.addEventListener("click", (event) => {
        event.preventDefault();
        event.stopPropagation();
        selectAdvantageProduct(node.getAttribute("data-opportunity-select") || "", "broad");
      });
    });
    root.querySelectorAll("[data-profile-open]").forEach((node) => {
      node.addEventListener("click", (event) => {
        event.preventDefault();
        event.stopPropagation();
        opportunityProfileId = node.getAttribute("data-profile-open") || "";
        render();
      });
    });
    root.querySelectorAll("[data-profile-id]").forEach((node) => {
      node.addEventListener("click", (event) => {
        if (event.target && event.target.closest("a, button")) return;
        opportunityProfileId = node.getAttribute("data-profile-id") || "";
        render();
      });
      node.addEventListener("keydown", (event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          opportunityProfileId = node.getAttribute("data-profile-id") || "";
          render();
        }
      });
    });
    root.querySelectorAll("[data-profile-close]").forEach((node) => {
      node.addEventListener("click", (event) => {
        event.preventDefault();
        opportunityProfileId = "";
        render();
      });
    });
    root.querySelectorAll("[data-profile-overlay]").forEach((node) => {
      node.addEventListener("click", (event) => {
        if (event.target !== node) return;
        opportunityProfileId = "";
        render();
      });
    });
    root.querySelectorAll("[data-profile-select]").forEach((node) => {
      node.addEventListener("click", (event) => {
        event.preventDefault();
        event.stopPropagation();
        selectAdvantageProduct(node.getAttribute("data-profile-select") || "", "broad");
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
        opportunityProfileId = "";
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
      renderBusinessOpportunities(list),
      renderTable(list),
      renderFootnote(),
      renderOpportunityProfileModal(list),
    ].join("");
    drawScatterCanvas();
    bindEvents();
    if (restoreSearchFocus) {
      restoreSearchFocus = false;
      const search = document.getElementById("mixedSearch");
      if (search) {
        search.focus({ preventScroll: true });
        const caret = search.value.length;
        search.setSelectionRange?.(caret, caret);
      }
    }
    if (pendingScrollToScatter) {
      pendingScrollToScatter = false;
      root.querySelector(".mixed-scatter-panel")?.scrollIntoView({ block: "start", behavior: "smooth" });
    }
  }

  render();
})();
