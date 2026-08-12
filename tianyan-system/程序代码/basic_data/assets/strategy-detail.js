
(async () => {
  const B = window.BasicData;
  const root = B.byId("strategyDetailPage");
  document.body.classList.add("strategy-detail-page");
  const semanticIndex = window.__AI_STRATEGY_SEMANTIC_INDEX__ || null;
  const id = B.params().get("id");
  const item = B.state.summary.strategies.find((row) => row.统一策略ID === id);
  if (!item) {
    root.innerHTML = '<section class="panel"><div class="empty">未找到策略，请从策略列表进入。</div></section>';
    return;
  }
  await B.loadScript(item.detailFile);
  const detail = B.state.details[id];
  if (!detail) {
    root.innerHTML = '<section class="panel"><div class="empty">策略详情文件加载失败。</div></section>';
    return;
  }
  const overview = B.state.summary?.overview || {};
  const dataRefreshTime = overview.数据刷新时间 || overview.生成时间 || "";
  const ranges = [
    ["all", "成立以来"],
    ["1y", "近1年"],
    ["6m", "近6月"],
    ["3m", "近3月"],
    ["1m", "近1月"],
    ["ytd", "今年以来"]
  ];
  const intervalHeaders = ["口径", "近一周", "近一月", "近三月", "近6月", "近1年", "今年以来", "成立以来"];
  const curveRows = ["披露业绩", "模拟业绩", "基准业绩", "沪深300业绩"];
  const holdingHeaders = ["基金名称", "二级分类", "上次调仓后权重", "权重", "权重变化", "调仓后收益率", "调仓后收益贡献"];
  const snapshots = detail.positionSnapshots || [];
  const signalEvents = detail.signalEvents || [];
  const signalSummary = detail.signalSummary || {};
  const globalBenchmarks = B.state.summary?.globalBenchmarks || [];
  const officialCurvePoints = detail.curves?.披露业绩?.points || [];
  const simulatedCurvePoints = detail.curves?.模拟业绩?.points || [];
  const isLegacyArchive = Number(detail.summary?.是否历史接口留档 || 0) === 1;
  const hasDrawableStrategyCurve = officialCurvePoints.length >= 2 || (!isLegacyArchive && simulatedCurvePoints.length >= 2);
  const hasAnnualPerformance = (detail.annualMatrix || []).some((row) => Object.entries(row || {}).some(([key, value]) => key !== "年度" && num(value) !== null));
  const hasRiskMetrics = ["最大回撤", "当前回撤", "年化收益", "波动率", "夏普比率"].some((field) => num(detail.summary?.[field]) !== null);
  let activeRange = "all";
  let activePerformanceTab = hasDrawableStrategyCurve ? "curve" : "interval";
  let activeSnapshotIndex = Math.max(0, snapshots.findIndex((snap) => snap.id !== "current"));
  let holdingSortField = "权重";
  let holdingSortDir = "desc";
  let selectedGlobalBenchmarkCode = "";
  let selectedContributionGlobalBenchmarkCode = "";

  function topFact(labelName, value, extraClass = "") {
    return `<div class="date-card ${extraClass}"><span>${B.label(labelName)}</span><strong>${B.valueHtml(labelName, value)}</strong></div>`;
  }
  function isBlank(value) {
    return value === null || value === undefined || value === "" || value === "未披露";
  }
  function raw(value) {
    return value === null || value === undefined ? "" : String(value);
  }
  function flagOn(value) {
    if (value === true || value === 1) return true;
    return /^(1|true|是|Y)$/i.test(raw(value).trim());
  }
  function fundDetailUrl(row) {
    const params = new URLSearchParams();
    if (row.基金代码) params.set("code", row.基金代码);
    if (row.基金名称) params.set("name", row.基金名称);
    return `./fund.html?${params.toString()}`;
  }
  function fundLink(row, label) {
    if (!row || (!row.基金代码 && !row.基金名称)) return B.esc(label || "未命名基金");
    return `<a class="link" href="${B.esc(fundDetailUrl(row))}">${B.esc(label || row.基金名称 || row.基金代码 || "未命名基金")}</a>`;
  }
  function num(value) {
    if (value === null || value === undefined || value === "") return null;
    const number = Number(value);
    return Number.isFinite(number) ? number : null;
  }
  function median(values) {
    const arr = values.map(num).filter((value) => value !== null).sort((a, b) => a - b);
    if (!arr.length) return null;
    const mid = Math.floor(arr.length / 2);
    return arr.length % 2 ? arr[mid] : (arr[mid - 1] + arr[mid]) / 2;
  }
  function mapFields(rows) {
    return Object.fromEntries((rows || []).map((row) => [row.字段, row.值]));
  }
  const profileMap = mapFields(detail.profileFields);
  const performanceMap = mapFields(detail.performanceFields);
  const classificationMap = mapFields(detail.classificationFields);
  Object.assign(B.state.summary.fieldDictionary = B.state.summary.fieldDictionary || {}, {
    "组合基金持仓": "展示当前策略组合持有的底层基金。组合占比来自策略当前持仓或推算持仓；配置日优先取该策略最近一次历史调仓日；持有时长按最新持仓日与配置日之间的自然日差计算。",
    "基金类型分类": "按持仓基金的资产类型、基金同类分组和基金详情包中的基金类型归并为现金类、固收类、股票类、混合类或其他。",
    "配置日": "该组合当前配置的起始日期。优先使用最新历史调仓日期；若没有历史调仓事件，则使用当前持仓日期。",
    "持有时长": "最新持仓日减去配置日得到的自然日天数。无法解析日期时显示未披露。",
    "资产配置估算": "优先基于 fund_detail_pack 中的基金经济暴露快照拆分股票、固收、现金、基金、其他和其中可转债；原始季报资产配置仅用于基金详情审计，不作为默认业务结论。",
    "基金分类来源": "说明该基金分类和暴露字段的来源。基金经济暴露快照表示已对季报原始资产配置中的基金/其他、ETF联接、FOF、QDII、黄金和固收指数做业务重映射；规则估算表示暂未取得足够穿透数据，使用基金类型、名称和平台披露分类兜底。",
    "基金穿透报告期": "当前基金经济暴露使用的报告期。历史持仓快照会优先选择报告期不晚于持仓日期的数据。",
    "基金穿透覆盖状态": "exact_quarterly_asset_and_stock 表示已有季报资产配置和股票持仓行业推导；exact_quarterly_asset_only 表示仅有季报资产配置；空值表示走规则估算兜底。",
    "基金持有区间收益": "优先使用策略详情中该基金调仓后收益率；缺失时使用基金详情包内的区间收益率。",
    "基金近1年收益": "使用该基金日度净值计算的近1年收益率；缺失时显示未披露。",
    "历史发车信号": "信号类策略的历史发车、买入、卖出、加仓、减仓指令。每次信号按基金级权重变化拆成局部调仓，并用基金后续收益评价指令方向。",
    "信号胜率": "买入/加仓后基金上涨判为胜，卖出/减仓后基金下跌判为胜；净值或观察期不足的指令不进入分母。",
    "信号加权方向收益": "按基金指令调整强度加权的方向收益。买入/加仓取基金区间收益，卖出/减仓取基金区间收益的相反数。",
    "权重变化_百分点": "该基金在本次信号中的调后权重减调前权重。正值代表买入或加仓，负值代表卖出或减仓。"
  });
  const fundPack = window.__BASIC_DATA__?.fundDetailPack || {};
  const fundFields = fundPack.fundFields || [];
  const fundObjects = (fundPack.funds || []).map((row) => Object.fromEntries(fundFields.map((field, index) => [field, row[index] ?? ""])));
  const fundDataByCode = new Map(fundObjects.map((row) => [raw(row.基金代码), row]).filter(([code]) => code));
  const fundDataByName = new Map(fundObjects.map((row) => [raw(row.基金名称), row]).filter(([name]) => name));
  function semanticRows(packName) {
    if (packName === "strategyEntities" && detail.strategyEntityPack?.rows) {
      const fields = detail.strategyEntityPack.fields || [];
      return detail.strategyEntityPack.rows.map((row) => Object.fromEntries(fields.map((field, index) => [field, row[index] ?? ""])));
    }
    const pack = semanticIndex?.[packName];
    if (!pack || !Array.isArray(pack.rows)) return [];
    const fields = pack.fields || [];
    return pack.rows.map((row) => Object.fromEntries(fields.map((field, index) => [field, row[index] ?? ""])));
  }
  function strategyEntityRows() {
    return semanticRows("strategyEntities")
      .filter((row) => raw(row.统一策略ID) === raw(detail.id))
      .filter((row) => num(row.权重) !== null && num(row.权重) > 0.0001)
      .sort((a, b) => (num(b.权重) || 0) - (num(a.权重) || 0));
  }
  function entityBadge(row) {
    const source = [row.来源字段, row.来源值].filter(Boolean).join("：");
    const meta = [row.实体等级, source, row.抽取规则ID].filter(Boolean).join("｜");
    return `<div class="entity-badge">
      <div><strong>${B.esc(row.实体名称 || row.实体Key || "未命名实体")}</strong><span>${B.esc(row.实体类型 || "实体")}${meta ? `｜${B.esc(meta)}` : ""}</span></div>
      <em>${B.pct(row.权重)}</em>
      ${row.证据基金 ? `<p>${B.esc(row.证据基金)}</p>` : ""}
      ${row.规则版本 ? `<p>规则版本：${B.esc(row.规则版本)}</p>` : ""}
    </div>`;
  }
  function entityGraphSection() {
    const rows = strategyEntityRows();
    const primaryTypes = ["资产大类", "资产", "指数", "地域", "行业主题", "产品形态", "风格"];
    const groups = primaryTypes.map((type) => ({
      type,
      rows: rows.filter((row) => row.实体类型 === type).slice(0, type === "资产大类" ? 8 : 12),
    })).filter((group) => group.rows.length);
    return `<details class="panel entity-panel collapsible-panel">
      <summary class="collapsible-summary">
        <div>
          <h2>实体图谱</h2>
          <p class="desc">基于最新持仓基金的经济暴露、主题标签和基金名称抽取；权重为策略持仓权重按基金经济暴露比例汇总。</p>
        </div>
        <span class="pill">${rows.length.toLocaleString("zh-CN")} 个实体</span>
      </summary>
      <div class="collapsible-body">
        ${groups.length ? groups.map((group) => `
          <div class="entity-group">
            <h3>${B.esc(group.type)}</h3>
            <div class="entity-grid">${group.rows.map(entityBadge).join("")}</div>
          </div>
        `).join("") : '<div class="empty">当前策略暂无可展示实体。请先重建 AI 语义索引。</div>'}
      </div>
    </details>`;
  }
  function currentHoldingSnapshot() {
    return snapshots.find((snap) => snap.id === "current") || snapshots.find((snap) => snap.类型 === "当前仓位") || snapshots[0] || { holdings: [] };
  }
  function latestRebalanceDate() {
    return snapshots
      .filter((snap) => snap.id !== "current" && snap.日期)
      .map((snap) => snap.日期)
      .sort()
      .at(-1) || "";
  }
  function fundData(row) {
    return fundDataByCode.get(raw(row.基金代码)) || fundDataByName.get(raw(row.基金名称)) || {};
  }
  function parseDate(value) {
    const text = raw(value).slice(0, 10);
    if (!/^\d{4}-\d{2}-\d{2}$/.test(text)) return null;
    const date = new Date(`${text}T00:00:00`);
    return Number.isNaN(date.getTime()) ? null : date;
  }
  function dayDiff(start, end) {
    const startDate = parseDate(start);
    const endDate = parseDate(end);
    if (!startDate || !endDate) return null;
    return Math.max(0, Math.round((endDate - startDate) / 86400000));
  }
  function holdingCategory(row, data) {
    const text = [row.资产类型, row.基金同类分组, row.分组, data.基金类型, data.研报大类资产].map(raw).join(" ");
    if (/货币|现金/.test(text)) return "现金类";
    if (/可转债|债|固收|短债|纯债/.test(text)) return "固收类";
    if (/股票|权益|A股|港股|美股|QDII|指数|ETF/.test(text)) return "股票类";
    if (/混合|多资产|FOF|基金中基金/.test(text)) return "混合类";
    return "其他";
  }
  function secondaryCategory(row, data) {
    return row.分组 || row.基金同类分组 || data.权益行业主题 || data.行业主题 || data.基金类型 || data.研报大类资产 || "--";
  }
  function parseExposurePairs(text) {
    const result = [];
    raw(text).split(/[、,，;；]+/).forEach((part) => {
      const item = part.trim();
      if (!item) return;
      const match = item.match(/^(.+?)(-?\d+(?:\.\d+)?)%$/);
      if (match) result.push({ name: match[1].trim(), value: Number(match[2]) });
    });
    return result;
  }
  function exposureBucket(name) {
    if (/可转债|转债/.test(name)) return "其中可转债";
    if (/货币|现金/.test(name)) return "现金";
    if (/债|固收/.test(name)) return "固收";
    if (/基金|FOF/.test(name)) return "基金";
    if (/股票|权益|A股|港股|美股|海外|全球|行业|主题|科技|消费|医药|制造|成长|价值|周期|红利/.test(name)) return "股票";
    return "其他";
  }
  function fallbackAllocation(row, data) {
    const text = [row.资产类型, row.基金同类分组, row.分组, data.基金类型, data.研报大类资产, data.经济资产暴露, data.资产暴露].map(raw).join(" ");
    const allocation = { 股票: 0, 固收: 0, 现金: 0, 基金: 0, 其他: 0, 其中可转债: 0 };
    if (/可转债|转债/.test(text)) {
      allocation.固收 = 100;
      allocation.其中可转债 = 100;
    } else if (/债|固收|短债|纯债/.test(text)) allocation.固收 = 100;
    else if (/货币|现金/.test(text)) allocation.现金 = 100;
    else if (/FOF|基金中基金/.test(text)) allocation.基金 = 100;
    else if (/股票|权益|A股|港股|美股|QDII|指数|ETF/.test(text)) allocation.股票 = 100;
    else allocation.其他 = 100;
    return allocation;
  }
  function fundAllocation(row, data) {
    const allocation = { 股票: 0, 固收: 0, 现金: 0, 基金: 0, 其他: 0, 其中可转债: 0 };
    const pairs = parseExposurePairs(data.经济资产暴露 || data.资产暴露 || data.研报大类资产 || "");
    if (!pairs.length) return fallbackAllocation(row, data);
    pairs.forEach((pair) => {
      const bucket = exposureBucket(pair.name);
      if (bucket === "其中可转债") {
        allocation.其中可转债 += pair.value;
        allocation.固收 += pair.value;
      } else if (allocation[bucket] !== undefined) {
        allocation[bucket] += pair.value;
      }
    });
    return allocation;
  }
  function pctCell(value) {
    const n = num(value);
    return n === null ? '<span class="small">--</span>' : B.pct(n);
  }
  function portfolioWeightHtml(value, maxWeight) {
    const n = num(value) || 0;
    const width = Math.max(3, Math.min(100, maxWeight > 0 ? (n / maxWeight) * 100 : 0));
    return `<div class="portfolio-weight"><span><i style="width:${width.toFixed(2)}%"></i></span><b>${B.pct(n)}</b></div>`;
  }
  function portfolioFundRows() {
    const snap = currentHoldingSnapshot();
    const holdings = (snap.holdings || []).filter((row) => (num(row.权重) || 0) > 0);
    const configDate = latestRebalanceDate() || snap.日期 || detail.holdingMeta.最新持仓日 || "";
    const currentDate = snap.日期 || detail.holdingMeta.最新持仓日 || configDate;
    const rows = holdings.map((row) => {
      const data = fundData(row);
      const allocation = fundAllocation(row, data);
      return {
        ...row,
        _fundData: data,
        _category: holdingCategory(row, data),
        _secondary: secondaryCategory(row, data),
        _allocation: allocation,
        _configDate: configDate || row.持仓日期 || "",
        _holdingDays: dayDiff(configDate || row.持仓日期, currentDate),
        _intervalReturn: num(row.调仓后收益率 ?? data.区间收益率),
        _oneYearReturn: num(row.近1年收益),
      };
    });
    const order = new Map(["现金类", "固收类", "股票类", "混合类", "其他"].map((name, index) => [name, index]));
    return rows.sort((a, b) => (order.get(a._category) ?? 99) - (order.get(b._category) ?? 99) || (num(b.权重) || 0) - (num(a.权重) || 0));
  }
  function portfolioHoldingsSection() {
    const rows = portfolioFundRows();
    const isSignalHoldingView = flagOn(detail.summary?.是否信号类组合);
    const holdingTitle = isSignalHoldingView ? "信号/候选池基金明细" : "组合基金持仓";
    const holdingDesc = isSignalHoldingView
      ? "信号类策略按发车信号、买入/卖出指令或候选池口径展示基金明细；表内占比用于表达信号强度、候选池权重或披露份数换算，不应解读为真实组合持仓权重。资产配置列优先来自基金经济暴露；缺失时按基金分类兜底估算。"
      : "参考组合持仓表模式展示底层基金、组合占比、配置日志和基金经济资产暴露。资产配置列优先来自基金经济暴露；缺失时按基金分类兜底估算。";
    const holdingSuperHead = isSignalHoldingView ? "信号/候选池基金与占比" : "组合持仓与占比";
    const holdingTotalLabel = isSignalHoldingView ? "口径合计" : "组合合计";
    const holdingEmpty = isSignalHoldingView ? "暂无信号/候选池基金明细" : "暂无当前组合基金持仓";
    const maxWeight = Math.max(0, ...rows.map((row) => num(row.权重) || 0));
    const groups = new Map();
    rows.forEach((row) => {
      const list = groups.get(row._category) || [];
      list.push(row);
      groups.set(row._category, list);
    });
    const totalWeight = rows.reduce((acc, row) => acc + (num(row.权重) || 0), 0);
    const totals = { 股票: 0, 固收: 0, 现金: 0, 基金: 0, 其他: 0, 其中可转债: 0 };
    rows.forEach((row) => {
      const weight = num(row.权重) || 0;
      Object.keys(totals).forEach((key) => {
        totals[key] += weight * ((num(row._allocation[key]) || 0) / 100);
      });
    });
    const weightedReturn = (field) => {
      const valid = rows.filter((row) => num(row[field]) !== null && (num(row.权重) || 0) > 0);
      const weight = valid.reduce((acc, row) => acc + (num(row.权重) || 0), 0);
      if (!weight) return null;
      return valid.reduce((acc, row) => acc + (num(row.权重) || 0) * (num(row[field]) || 0), 0) / weight;
    };
    const body = [...groups.entries()].map(([category, list]) => list.map((row, index) => `<tr>
      ${index === 0 ? `<td class="portfolio-category" rowspan="${list.length}">${B.esc(category)}</td>` : ""}
      <td class="portfolio-code">${fundLink(row, row.基金代码 || "--")}</td>
      <td class="portfolio-name">${fundLink(row, row.基金名称 || row.基金代码 || "未命名基金")}</td>
      <td>${B.esc(row._secondary || "--")}</td>
      <td>${B.esc(row._fundData.基金分类来源 || "规则估算")}</td>
      <td>${B.esc(row._fundData.基金穿透报告期 || "--")}</td>
      <td>${portfolioWeightHtml(row.权重, maxWeight)}</td>
      <td>${B.esc(row._configDate || "--")}</td>
      <td>${row._holdingDays === null ? "--" : row._holdingDays}</td>
      <td>${pctCell(row._allocation.股票)}</td>
      <td>${pctCell(row._allocation.固收)}</td>
      <td>${pctCell(row._allocation.现金)}</td>
      <td>${pctCell(row._allocation.基金)}</td>
      <td>${pctCell(row._allocation.其他)}</td>
      <td>${pctCell(row._allocation.其中可转债)}</td>
      <td class="${returnTone(row._intervalReturn)}">${B.pctSigned(row._intervalReturn)}</td>
      <td class="${returnTone(row._oneYearReturn)}">${B.pctSigned(row._oneYearReturn)}</td>
    </tr>`).join("")).join("");
    return `<section class="panel portfolio-holding-panel">
      <div class="panel-head">
        <div>
          <h2>${holdingTitle}</h2>
          <p class="desc">${holdingDesc}</p>
        </div>
        <span class="pill">${rows.length.toLocaleString("zh-CN")} 只基金</span>
      </div>
      <div class="portfolio-table-wrap">
        <table class="portfolio-holding-table">
          <thead>
            <tr class="portfolio-super-head">
              <th colspan="7">${holdingSuperHead}</th>
              <th colspan="2">配置日志</th>
              <th colspan="6">基金经济资产配置</th>
              <th colspan="2">区间个基表现</th>
            </tr>
            <tr>
              <th>${B.label("基金类型分类")}</th>
              <th>${B.label("基金代码")}</th>
              <th>${B.label("基金名称")}</th>
              <th>${B.label("二级分类")}</th>
              <th>${B.label("基金分类来源")}</th>
              <th>${B.label("基金穿透报告期")}</th>
              <th>${B.label("组合占比")}</th>
              <th>${B.label("配置日")}</th>
              <th>${B.label("持有时长")}</th>
              <th>股票</th>
              <th>固收</th>
              <th>现金</th>
              <th>基金</th>
              <th>其他</th>
              <th>其中可转债</th>
              <th>${B.label("基金持有区间收益")}</th>
              <th>${B.label("基金近1年收益")}</th>
            </tr>
          </thead>
          <tbody>${body || `<tr><td colspan="17"><div class="empty">${holdingEmpty}</div></td></tr>`}</tbody>
          <tfoot>
            <tr>
              <td colspan="6">${holdingTotalLabel}</td>
              <td>${B.pct(totalWeight)}</td>
              <td>--</td>
              <td>--</td>
              <td>${pctCell(totals.股票)}</td>
              <td>${pctCell(totals.固收)}</td>
              <td>${pctCell(totals.现金)}</td>
              <td>${pctCell(totals.基金)}</td>
              <td>${pctCell(totals.其他)}</td>
              <td>${pctCell(totals.其中可转债)}</td>
              <td>${B.pctSigned(weightedReturn("_intervalReturn"))}</td>
              <td>${B.pctSigned(weightedReturn("_oneYearReturn"))}</td>
            </tr>
          </tfoot>
        </table>
      </div>
    </section>`;
  }
  function pairCard(title, rows) {
    const body = rows.map(([labelName, value, formatter]) => `
      <div class="pair-row"><span>${B.label(labelName)}</span><strong>${formatter ? formatter(value) : B.valueHtml(labelName, value)}</strong></div>
    `).join("");
    return `<div class="paired-card"><h3>${B.esc(title)}</h3>${body}</div>`;
  }
  function coreLine(title, rows) {
    return `<section class="core-line"><h4>${B.esc(title)}</h4><div class="core-line-values">${rows.map(([labelName, value, formatter]) => B.metricValue(labelName, value, formatter)).join("")}</div></section>`;
  }
  function coreMetrics() {
    return `<div class="core-metric-board">
      ${coreLine("风险收益", [
        ["最大回撤", detail.summary.最大回撤, B.pctSigned],
        ["当前回撤", detail.summary.当前回撤, B.pctSigned],
        ["波动率", performanceMap.波动率, B.pct],
        ["夏普比率", performanceMap.夏普比率]
      ])}
      ${coreLine("分类口径", [
        ["研报产品类型", classificationMap.研报产品类型 || detail.summary.研报产品类型],
        ["风险等级", classificationMap.风险等级 || detail.summary.风险等级],
        ["业务分类", classificationMap.业务分类 || detail.summary.业务分类],
        ["天天当前对客展示", classificationMap.天天当前对客展示 || detail.summary.天天当前对客展示]
      ])}
      ${coreLine("持仓交易", [
        ["持仓基金数", detail.holdingMeta.持仓基金数],
        ["最近调仓日", latestRebalanceText()],
        ["年化换手率", performanceMap.年化换手率, B.pct],
        ["基础数据等级", classificationMap.基础数据等级]
      ])}
    </div>`;
  }
  function selectedRows(rows, names) {
    const byName = mapFields(rows);
    return names.filter((name) => !isBlank(byName[name])).map((name) => ({ 字段: name, 值: byName[name] }));
  }
  function otherRows() {
    const primary = new Set(["统一策略ID", "策略代码", "策略名称", "渠道", "投顾机构", "披露策略类型", "披露风险等级", "风险等级", "成立日期", "运作天数", "运作状态", "官方单位净值", "自建单位净值", "费前单位净值", "费后单位净值", "官方累计收益", "自建累计收益", "与官方偏差", "年化收益", "最大回撤", "波动率", "夏普比率", "官方对比口径", "可比记录数", "建议持有时长", "起投金额", "投顾费率", "业绩基准", "业绩基准说明", "标签", "策略概念"]);
    return [...(detail.profileFields || []), ...(detail.performanceFields || [])].filter((row) => !primary.has(row.字段));
  }
  function compactInfoRows() {
    const byName = mapFields(detail.profileFields || []);
    return ["策略代码", "披露策略类型", "披露风险等级", "建议持有时长", "起投金额", "标签", "策略概念"].map((name) => ({ 字段: name, 值: byName[name] ?? "未披露" }));
  }
  function classificationInfoRows() {
    const names = ["研报产品类型", "研报股票子类型", "研报分类依据", "风险等级", "权益风险档", "波动风险档", "回撤风险档", "风险触发指标", "风险分类依据", "业务分类", "业务分类依据", "业务组合分类", "业务分类标签", "天天展示状态", "天天当前对客展示", "天天上架生命周期", "天天展示判定依据", "基准风险资产权重", "基准风险资产权重_百分比", "基准风险资产权重说明", "权益中枢", "固收中枢", "基准风险资产中枢", "海外配置中枢", "指数化程度", "主动管理程度", "风险资产偏离", "配置风格标签", "市场地域", "主动被动", "特殊标签", "策略实现标签", "权益基金权重", "债券基金权重", "货币基金权重", "混合基金权重", "QDII权重", "指数基金权重", "主动基金权重", "基准权益权重", "基准债券权重", "基准货币权重", "基准结构类型", "非权益比较轨道", "正式可比池", "可比池样本资格", "可比池说明", "基准互斥权重合计_百分比", "基准港股权益权重", "基准海外权益权重", "是否多元策略", "多元策略标签", "基准映射置信度", "基准资产已映射权重", "基准资产未映射权重", "基准资产大类-权益", "基准资产大类-债券", "基准资产大类-现金", "基准资产大类-商品", "基准资产大类-另类", "基准资产大类-其他", "基准资产类别-A股", "基准资产类别-港股", "基准资产类别-海外权益", "基准资产类别-债券", "基准资产类别-商品", "基准资产类别-现金", "基准资产类别-其他", "基准可用状态", "基础数据等级", "分类依据"];
    return names.map((name) => ({ 字段: name, 值: name === "基准风险资产权重" ? (classificationMap.基准风险资产权重 || "未披露") : (classificationMap[name] ?? "未披露") }));
  }
  function classChip(labelName, value, main = false) {
    return `<div class="class-chip ${main ? "is-main" : ""}"><span>${B.label(labelName)}</span><strong>${B.valueHtml(labelName, value)}</strong></div>`;
  }
  function classMetric(labelName, value) {
    return `<div class="class-metric"><span>${B.label(labelName)}</span><strong>${B.valueHtml(labelName, value)}</strong></div>`;
  }
  function classificationSummary() {
    const holdingWeights = ["权益基金权重", "债券基金权重", "货币基金权重", "QDII权重", "指数基金权重", "主动基金权重"];
    const benchmarkWeights = ["基准风险资产权重_百分比", "基准权益权重", "基准债券权重", "基准货币权重", "基准资产大类-商品", "基准资产大类-另类"];
    return `<div class="classification-summary">
      <div class="class-chip-grid">
        ${classChip("研报产品类型", classificationMap.研报产品类型 || detail.summary.研报产品类型, true)}
        ${!isBlank(classificationMap.研报股票子类型 || detail.summary.研报股票子类型) ? classChip("研报股票子类型", classificationMap.研报股票子类型 || detail.summary.研报股票子类型) : ""}
        ${classChip("风险等级", classificationMap.风险等级 || detail.summary.风险等级, true)}
        ${classChip("业务分类", classificationMap.业务分类 || detail.summary.业务分类)}
        ${classChip("天天当前对客展示", classificationMap.天天当前对客展示 || detail.summary.天天当前对客展示)}
        ${classChip("天天展示状态", classificationMap.天天展示状态)}
        ${classChip("基准风险资产权重", classificationMap.基准风险资产权重)}
        ${classChip("配置风格标签", classificationMap.配置风格标签)}
        ${classChip("非权益比较轨道", classificationMap.非权益比较轨道)}
        ${classChip("正式可比池", classificationMap.正式可比池)}
        ${classChip("市场地域", classificationMap.市场地域)}
        ${classChip("主动被动", classificationMap.主动被动)}
        ${classChip("特殊标签", classificationMap.特殊标签)}
        ${classChip("策略实现标签", classificationMap.策略实现标签)}
        ${classChip("基准可用状态", classificationMap.基准可用状态)}
      </div>
      <div class="class-section-title">持仓分类权重</div>
      <div class="class-metric-grid">${holdingWeights.map((name) => classMetric(name, classificationMap[name])).join("")}</div>
      <div class="class-section-title">基准拆分</div>
      <div class="class-metric-grid">${benchmarkWeights.map((name) => classMetric(name, classificationMap[name])).join("")}${classMetric("基础数据等级", classificationMap.基础数据等级)}</div>
      <div class="class-basis"><strong>${B.label("研报分类依据")}</strong><span>${B.esc(classificationMap.研报分类依据 || "未披露")}</span></div>
      <div class="class-basis"><strong>${B.label("分类依据")}</strong><span>${B.esc(classificationMap.分类依据 || "未披露")}</span></div>
    </div>`;
  }
  function benchmarkAssetStructure() {
    const metaFields = ["基准风险资产权重", "基准风险资产权重说明", "非权益比较轨道", "正式可比池", "可比池样本资格", "基准映射置信度"];
    const majorFields = ["基准资产大类-权益", "基准资产大类-债券", "基准资产大类-现金", "基准资产大类-商品", "基准资产大类-另类", "基准资产大类-其他"];
    const categoryFields = ["基准资产类别-A股", "基准资产类别-港股", "基准资产类别-海外权益", "基准资产类别-债券", "基准资产类别-商品", "基准资产类别-现金", "基准资产类别-其他"];
    const coverageFields = ["基准互斥权重合计_百分比", "基准港股权益权重", "基准海外权益权重", "基准资产已映射权重", "基准资产未映射权重"];
    return `<div class="classification-summary benchmark-asset-summary">
      <div class="class-chip-grid">${metaFields.map((name) => classChip(name, classificationMap[name])).join("")}</div>
      <div class="class-section-title">资产大类</div>
      <div class="class-metric-grid">${majorFields.map((name) => classMetric(name, classificationMap[name])).join("")}${coverageFields.map((name) => classMetric(name, classificationMap[name])).join("")}</div>
      <div class="class-section-title">资产类别</div>
      <div class="class-metric-grid">${categoryFields.map((name) => classMetric(name, classificationMap[name])).join("")}</div>
    </div>`;
  }
  function benchmarkInfo() {
    const text = profileMap.业绩基准说明 ?? profileMap.业绩基准 ?? "未披露";
    return `<div class="benchmark-strip"><strong>${B.label("业绩基准说明")}</strong><span>${B.esc(text)}</span></div>`;
  }
  function latestRebalanceText() {
    if (!isBlank(detail.summary.最近调仓日)) return B.fmt(detail.summary.最近调仓日);
    const history = snapshots.find((snap) => snap.id !== "current" && !isBlank(snap.日期));
    if (history) return B.fmt(history.日期);
    if (!isBlank(detail.holdingMeta.最新持仓日)) return `${B.esc(detail.holdingMeta.最新持仓日)}（无历史调仓事件）`;
    return "无历史调仓事件";
  }
  function signedReturnText(value) {
    const n = num(value);
    if (n === null) return "未披露";
    const sign = n > 0 ? "+" : "";
    return `${sign}${n.toLocaleString("zh-CN", { maximumFractionDigits: 2 })}%`;
  }
  function drawdownText(value) {
    const n = num(value);
    if (n === null) return "未披露";
    const drawdown = n > 0 ? -n : n;
    return `${drawdown.toLocaleString("zh-CN", { maximumFractionDigits: 2 })}%`;
  }
  function returnTone(value) {
    const n = num(value);
    if (n === null || Math.abs(n) < 0.0001) return "is-zero";
    return n > 0 ? "is-pos" : "is-neg";
  }
  function riskPeerRows() {
    const risk = classificationMap.风险等级 || detail.summary.风险等级 || "";
    return (B.state.summary.strategies || []).filter((row) => {
      if (!risk || row.风险等级 !== risk) return false;
      if (row.风险等级 === "D0 持仓缺失") return false;
      return row.数据完整性 === "完整";
    });
  }
  function returnRank(field, value) {
    const currentValue = num(value);
    if (currentValue === null) return "未排名";
    if (detail.summary.数据完整性 !== "完整") return "数据不全";
    const peers = riskPeerRows().map((row) => num(row[field])).filter((peerValue) => peerValue !== null);
    if (!peers.length) return "未排名";
    const rank = peers.filter((peerValue) => peerValue > currentValue).length + 1;
    return `${rank}/${peers.length}`;
  }
  function returnCell(labelName, fieldName) {
    const value = detail.summary[fieldName];
    return `<div class="return-cell ${returnTone(value)}">
      <span>${B.esc(labelName)}</span>
      <strong>${signedReturnText(value)}</strong>
      <em>${B.esc(returnRank(fieldName, value))}</em>
    </div>`;
  }
  function returnGrid() {
    const items = [
      ["日涨跌幅", "日涨跌幅"],
      ["近一周", "近一周"],
      ["近一月", "近一月"],
      ["近3月", "近三月"],
      ["近6月", "近6月"],
      ["今年以来", "今年以来"],
      ["近一年", "近1年"],
      ["累计收益率", "累计收益率"],
      ["年化收益率", "年化收益"]
    ];
    return `<div class="return-grid">${items.map(([labelName, fieldName]) => returnCell(labelName, fieldName)).join("")}</div>`;
  }
  function overviewFact(labelName, value, wide = false) {
    return `<div class="strategy-key-fact ${wide ? "is-wide" : ""}">
      <span>${B.label(labelName)}</span>
      <strong>${B.valueHtml(labelName, value)}</strong>
    </div>`;
  }
  function overviewFacts() {
    const strategyClass = [
      classificationMap.研报产品类型 || detail.summary.研报产品类型,
      classificationMap.研报股票子类型 || detail.summary.研报股票子类型 || classificationMap.业务分类 || detail.summary.业务分类,
    ].filter((value, index, values) => !isBlank(value) && values.indexOf(value) === index).join(" / ") || "未披露";
    const benchmark = profileMap.业绩基准说明 || profileMap.业绩基准 || detail.summary.业绩基准 || "未披露";
    return `<div class="strategy-key-facts">
      ${overviewFact("成立日期", detail.summary.成立日期)}
      ${overviewFact("最新业绩日期", detail.summary.最新业绩日期 || detail.summary.收益数据截至 || "未披露")}
      ${overviewFact("最新持仓日", detail.holdingMeta.最新持仓日 || "未披露")}
      ${overviewFact("投顾费率", profileMap.投顾费率 || detail.summary.年化投顾费率 || "未披露")}
      ${overviewFact("策略分类", strategyClass, true)}
      ${overviewFact("业绩基准", benchmark, true)}
    </div>`;
  }
  function ownershipFacts() {
    const channel = profileMap.渠道 || detail.summary.渠道 || "未披露";
    const manager = profileMap.投顾机构 || detail.summary.投顾机构 || "未披露";
    return `<div class="strategy-ownership-facts" aria-label="策略销售与管理信息">
      <div><span>销售渠道</span><strong>${B.esc(channel)}</strong></div>
      <div><span>投顾管理人</span><strong>${B.esc(manager)}</strong></div>
    </div>`;
  }
  function primaryMetricCard(labelName, fieldName, options = {}) {
    const value = detail.summary[fieldName];
    const tone = options.risk ? "is-risk" : returnTone(value);
    const note = options.note || (options.rank === false ? "风险指标" : `同类 ${returnRank(fieldName, value)}`);
    return `<div class="strategy-primary-metric ${tone}">
      <span>${B.esc(labelName)}</span>
      <strong>${options.risk ? drawdownText(value) : signedReturnText(value)}</strong>
      <em>${B.esc(note)}</em>
    </div>`;
  }
  function primaryMetricGrid() {
    return `<div class="strategy-primary-metrics">
      ${primaryMetricCard("今年以来", "今年以来")}
      ${primaryMetricCard("近1年", "近1年")}
      ${primaryMetricCard("成立以来", "累计收益率")}
      ${primaryMetricCard("最大回撤", "最大回撤", { risk: true, rank: false })}
    </div>`;
  }
  function riskMetricsPanel() {
    const rows = [
      ["最大回撤", detail.summary.最大回撤, drawdownText],
      ["当前回撤", detail.summary.当前回撤, drawdownText],
      ["年化收益", detail.summary.年化收益, B.pctSigned],
      ["波动率", performanceMap.波动率, B.pct],
      ["夏普比率", performanceMap.夏普比率, B.fmt],
      ["年化换手率", performanceMap.年化换手率, B.pct],
      ["风险等级", classificationMap.风险等级 || detail.summary.风险等级, B.fmt],
      ["披露风险等级", profileMap.披露风险等级 || detail.summary.披露风险等级, B.fmt],
    ];
    return `<div class="strategy-risk-grid">${rows.map(([labelName, value, formatter]) => `
      <div class="strategy-risk-item">
        <span>${B.label(labelName)}</span>
        <strong>${isBlank(value) ? "未披露" : formatter(value)}</strong>
      </div>`).join("")}</div>`;
  }
  function currentAssetAllocation(rows) {
    const keys = ["股票", "固收", "现金", "基金", "其他"];
    const classByKey = { 股票: "is-equity", 固收: "is-fixed", 现金: "is-cash", 基金: "is-fund", 其他: "is-other" };
    const totals = Object.fromEntries(keys.map((key) => [key, 0]));
    rows.forEach((row) => {
      const weight = num(row.权重) || 0;
      keys.forEach((key) => {
        totals[key] += weight * ((num(row._allocation?.[key]) || 0) / 100);
      });
    });
    const total = keys.reduce((acc, key) => acc + totals[key], 0);
    const items = keys.map((key) => ({
      key,
      value: total > 0 ? totals[key] / total * 100 : 0,
      className: classByKey[key],
    }));
    return `<div class="strategy-asset-allocation">
      <div class="strategy-asset-bar" aria-label="当前仓位资产分布">${items.filter((item) => item.value > 0.01).map((item) => `<span class="${item.className}" style="width:${item.value.toFixed(4)}%" title="${B.esc(item.key)} ${B.pct(item.value)}"></span>`).join("")}</div>
      <div class="strategy-asset-legend">${items.map((item) => `<span><i class="${item.className}"></i>${B.esc(item.key)} <strong>${B.pct(item.value)}</strong></span>`).join("")}</div>
    </div>`;
  }
  function compactHoldingTable(rows, mode = "current") {
    const sorted = [...rows].sort((a, b) => (num(b.权重) || 0) - (num(a.权重) || 0));
    const headers = mode === "current"
      ? ["基金名称", "二级分类", "权重", "权重变化", "调仓后收益率", "调仓后收益贡献"]
      : holdingHeaders;
    const body = sorted.length ? sorted.map((row) => `<tr>${headers.map((h) => `<td>${holdingValue(row, h)}</td>`).join("")}</tr>`).join("") : `<tr><td colspan="${headers.length}"><div class="empty">暂无持仓明细</div></td></tr>`;
    return `<div class="table-wrap strategy-holding-table-wrap"><table class="compact-table strategy-holding-table"><thead><tr>${headers.map((h) => `<th>${B.label(h)}</th>`).join("")}</tr></thead><tbody>${body}</tbody></table></div>`;
  }
  function compactHoldingCards(rows, mode = "current") {
    const sorted = [...rows].sort((a, b) => (num(b.权重) || 0) - (num(a.权重) || 0));
    return `<div class="strategy-holding-cards">${sorted.length ? sorted.map((row) => `<article class="strategy-holding-card">
      <div><strong>${fundLink(row, row.基金名称 || row.基金代码 || "未命名基金")}</strong><span>${B.esc(row.基金代码 || "--")}｜${B.esc(secondaryCategory(row, fundData(row)) || "未分类")}</span></div>
      <dl>
        ${mode === "history" ? `<div><dt>调前权重</dt><dd>${B.pct(row.上次调仓后权重)}</dd></div>` : ""}
        <div><dt>${mode === "history" ? "调后权重" : "当前权重"}</dt><dd>${B.pct(row.权重)}</dd></div>
        <div><dt>权重变化</dt><dd class="${returnTone(row.权重变化)}">${B.pctSigned(row.权重变化)}</dd></div>
        <div><dt>调仓后收益</dt><dd class="${returnTone(row.调仓后收益率)}">${B.pctSigned(row.调仓后收益率)}</dd></div>
        <div><dt>收益贡献</dt><dd class="${returnTone(row.调仓后收益贡献)}">${B.pctSigned(row.调仓后收益贡献)}</dd></div>
      </dl>
    </article>`).join("") : '<div class="empty">暂无持仓明细</div>'}</div>`;
  }
  function currentHoldingSection() {
    const snapshot = currentHoldingSnapshot();
    const rows = portfolioFundRows();
    const totalWeight = rows.reduce((acc, row) => acc + (num(row.权重) || 0), 0);
    return `<section id="strategy-holding" class="panel strategy-section strategy-current-holding">
      <div class="panel-head strategy-section-head">
        <div>
          <h2>当前仓位</h2>
          <p class="desc">截至 ${B.esc(snapshot.日期 || detail.holdingMeta.最新持仓日 || "未披露")}，来源：${B.esc(detail.holdingMeta.持仓来源 || snapshot.说明 || "未披露")}。</p>
        </div>
        <div class="strategy-section-meta"><span>${rows.length.toLocaleString("zh-CN")} 只基金</span><span>合计 ${B.pct(totalWeight)}</span></div>
      </div>
      <div class="strategy-subsection-title"><h3>资产分布</h3><p>按当前持仓基金经济资产暴露汇总</p></div>
      ${currentAssetAllocation(rows)}
      <div class="strategy-subsection-title"><h3>当前基金持仓列表</h3><p>按当前权重从高到低排列</p></div>
      ${compactHoldingTable(rows, "current")}
      ${compactHoldingCards(rows, "current")}
    </section>`;
  }
  function historicalSnapshots() {
    return snapshots.map((snapshot, index) => ({ snapshot, index })).filter(({ snapshot }) => snapshot.id !== "current" && snapshot.类型 !== "当前仓位");
  }
  function latestHistoricalSnapshot() {
    return historicalSnapshots()[0] || null;
  }
  function rebalanceChangeStats(snapshot) {
    const result = { 新进: 0, 增配: 0, 减配: 0, 退出: 0 };
    (snapshot?.holdings || []).forEach((row) => {
      const current = num(row.权重) || 0;
      const previous = num(row.上次调仓后权重) || 0;
      const change = num(row.权重变化) ?? (current - previous);
      const action = raw(row.调仓动作);
      if (/新进|调入|买入/.test(action) || (previous <= 0.0001 && current > 0.0001)) result.新进 += 1;
      else if (/退出|调出|卖出/.test(action) || (previous > 0.0001 && current <= 0.0001)) result.退出 += 1;
      else if (/增配|加仓/.test(action) || change > 0.0001) result.增配 += 1;
      else if (/减配|减仓/.test(action) || change < -0.0001) result.减配 += 1;
    });
    return result;
  }
  function latestRebalanceSummary() {
    const entry = latestHistoricalSnapshot();
    if (!entry) return '<div class="empty">暂无历史调仓记录</div>';
    const snapshot = entry.snapshot;
    const stats = rebalanceChangeStats(snapshot);
    const reason = raw(snapshot.调仓原因).trim() || "该次调仓未披露具体原因。";
    return `<div class="strategy-latest-rebalance">
      <div class="strategy-latest-rebalance-head">
        <div><span>最近调仓</span><strong>${B.esc(snapshot.日期 || "未披露日期")}</strong></div>
        <p>${B.esc(snapshot.标题 || "组合调整")}｜${B.esc(snapshot.说明 || `${(snapshot.holdings || []).length} 只基金`)}</p>
      </div>
      <div class="strategy-rebalance-stats">
        <div><span>新进</span><strong>${stats.新进}</strong></div>
        <div><span>增配</span><strong>${stats.增配}</strong></div>
        <div><span>减配</span><strong>${stats.减配}</strong></div>
        <div><span>退出</span><strong>${stats.退出}</strong></div>
      </div>
      <div class="strategy-rebalance-reason"><strong>调仓说明</strong><p>${B.esc(reason)}</p></div>
    </div>`;
  }
  function sourceCards() {
    const sources = detail.curveSources || {};
    const meta = detail.benchmarkMeta || {};
    const metaText = meta.基准公式解析 ? `${meta.基准公式解析}${(meta.缺失组件 || []).length ? `；缺失：${meta.缺失组件.join("、")}` : ""}` : "";
    const selected = selectedGlobalBenchmark();
    const globalText = selected ? `<p><b>全局基准：</b>${B.esc(selected.name)}（${B.esc(selected.code)}），区间 ${B.esc(selected.start || "未披露")} 至 ${B.esc(selected.end || "未披露")}；数据来源：${B.esc(selected.source || "指数日度行情")}</p>` : "";
    const warnings = (detail.curveWarnings || []).map((text) => `<p class="warn"><b>${B.label("曲线数据提示")}：</b>${B.esc(text)}</p>`).join("");
    return `<div class="source-note-list">${warnings}${curveRows.map((name) => `<p><b>${B.esc(name)}：</b>${B.esc(sources[name] || "未生成来源说明")}</p>`).join("")}${globalText}${metaText ? `<p><b>基准公式解析：</b>${B.esc(metaText)}</p>` : ""}</div>`;
  }
  function strategyRelationNotice() {
    const relation = detail.strategyRelation || {};
    if (!relation.官方业绩策略ID) return "";
    const parentName = relation.母策略名称 || relation.母策略ID || "母策略";
    const parentLink = relation.母策略ID
      ? `<a class="link" href="./strategy.html?id=${encodeURIComponent(relation.母策略ID)}">${B.esc(parentName)}</a>`
      : B.esc(parentName);
    return `<div class="source-note-list"><p class="warn"><b>母子策略业绩口径：</b>本期暂无独立披露净值，当前披露曲线及区间收益共享母策略${parentLink}，仅代表产品系列披露业绩，不代表本期独立成立以来收益。</p></div>`;
  }
  function isClientFacingSummary() {
    const current = String(classificationMap.天天当前对客展示 || detail.summary.天天当前对客展示 || "");
    const status = String(classificationMap.天天展示状态 || detail.summary.天天展示状态 || "");
    return !(current === "否" || /非对客|不对客|隐藏|未展示|不展示/.test(status));
  }
  function isStoppedSummary() {
    const s = detail.summary || {};
    if (Number(s.是否历史接口留档 || 0) === 1) return true;
    if (Number(s.是否已停止 || 0) === 1) return true;
    const status = [s.策略治理状态, s.运作状态, classificationMap.天天展示状态, s.天天展示状态].filter(Boolean).join(" ");
    return /已停止|已终止|已下架|已清盘|期满|已止盈|非对客或已结束|stopped/i.test(status);
  }
  function lifecycleSummaryLabel() {
    return isLegacyArchive ? "历史接口留档" : "策略已下架";
  }
  function stoppedStrategyBanner() {
    if (!isStoppedSummary()) return "";
    return `<div class="strategy-stopped-banner" role="status">
      <strong>${B.esc(lifecycleSummaryLabel())}</strong>
      <span>${isLegacyArchive ? "该产品来自广发证券贝塔牛历史接口，不是当前财富管家货架产品；已有官方区间收益保留查询，无法取得的日度走势图不构造。" : "该策略不参与当前常规排名；历史净值、收益、持仓和调仓数据仅保留用于查询与复盘。"}</span>
    </div>`;
  }
  function isGfSummary() {
    return detail.summary?.是否广发 === "是" || detail.summary?.是否广发策略 === "是" || /广发基金|广发投顾/.test(`${detail.summary?.投顾机构 || ""} ${detail.summary?.渠道 || ""}`);
  }
  function strategyPoolUrl(params = {}) {
    const query = new URLSearchParams();
    Object.entries(params).forEach(([key, value]) => {
      if (value) query.set(key, value);
    });
    const suffix = query.toString();
    return `./strategies.html${suffix ? `?${suffix}` : ""}`;
  }
  function peerRows() {
    const reportType = classificationMap.研报产品类型 || detail.summary.研报产品类型 || "";
    return (B.state.summary.strategies || []).filter((row) => {
      if (!reportType || row.研报产品类型 !== reportType) return false;
      if (row.风险等级 === "D0 持仓缺失") return false;
      if (row.数据完整性 !== "完整") return false;
      return true;
    });
  }
  function blockingIssues(issues) {
    return (issues || []).filter((item) => /数据不完整|D0|持仓缺失/.test(item));
  }
  function salesGateFields(issues) {
    const fields = [];
    if ((issues || []).includes("费率缺失")) fields.push("费率");
    if ((issues || []).includes("投资经理缺失")) fields.push("投资经理");
    if ((issues || []).includes("披露风险缺失")) fields.push("披露风险");
    return fields;
  }
  function gateText(issues) {
    const blocking = blockingIssues(issues);
    const sales = salesGateFields(issues);
    if (blocking.length) return `${blocking.join("、")}，先补数据再进入经营结论。`;
    if (sales.length) return `同类竞争力判断可用；进入销售材料、渠道话术或经理画像前需补${sales.join("、")}。`;
    return "关键经营字段可用于当前页面判断。";
  }
  function strategyDecision() {
    const reportType = classificationMap.研报产品类型 || detail.summary.研报产品类型 || "未披露";
    const peers = peerRows();
    const rankPeers = peers.filter((row) => num(row.近1年) !== null);
    const sorted = [...rankPeers].sort((a, b) => (num(b.近1年) || -999999) - (num(a.近1年) || -999999));
    const rank = sorted.findIndex((row) => row.统一策略ID === detail.id) + 1;
    const peerMedianReturn = median(peers.map((row) => row.近1年));
    const peerMedianDrawdown = median(peers.map((row) => row.最大回撤));
    const ret = num(detail.summary.近1年);
    const drawdown = num(detail.summary.最大回撤);
    const retGap = ret === null || peerMedianReturn === null ? null : ret - peerMedianReturn;
    const drawdownGap = drawdown === null || peerMedianDrawdown === null ? null : drawdown - peerMedianDrawdown;
    const issues = [];
    if (detail.summary.数据完整性 !== "完整") issues.push("数据不完整");
    if ((classificationMap.风险等级 || detail.summary.风险等级) === "D0 持仓缺失") issues.push("D0持仓缺失");
    if (isBlank(detail.holdingMeta.最新持仓日)) issues.push("最新持仓缺失");
    if (isBlank(profileMap.投顾费率) && isBlank(detail.summary.年化投顾费率)) issues.push("费率缺失");
    if (isBlank(profileMap.投资经理) && isBlank(detail.summary.投资经理)) issues.push("投资经理缺失");
    if (isBlank(profileMap.披露风险等级 || detail.summary.披露风险等级)) issues.push("披露风险缺失");
    let action = "观察跟踪";
    let tone = "is-warn";
    let actionText = "同类位置没有形成明确营销或复盘信号，保留月度跟踪。";
    const blocking = blockingIssues(issues);
    const salesFields = salesGateFields(issues);
    if (blocking.length) {
      action = "先补数据";
      tone = "is-bad";
      actionText = "关键数据不足，不能把该产品直接纳入经营结论，先补齐持仓和基础字段。";
    } else if (!isClientFacingSummary()) {
      action = "仅作核验";
      tone = "is-warn";
      actionText = "当前不是明确对客展示产品，更适合作为历史或竞品证据，不直接进入销售动作。";
    } else if (retGap !== null && retGap >= 0 && (drawdownGap === null || drawdownGap <= 1.5)) {
      action = "可进候选";
      tone = "is-good";
      actionText = "近1年收益不弱于同类中位，回撤没有明显劣势，可进入同类候选名单再看持仓和费率。";
    } else if ((retGap !== null && retGap <= -3) || (drawdownGap !== null && drawdownGap >= 3)) {
      action = "能力复盘";
      tone = "is-bad";
      actionText = "近1年收益或回撤明显落后同类中位，先拆底层基金、仓位和调仓节奏，不建议直接营销。";
    }
    if (!blocking.length && salesFields.length && action !== "仅作核验") {
      actionText += `但销售材料和渠道话术前需补${salesFields.join("、")}。`;
    }
    return { reportType, peers, rankPeers, rank, peerMedianReturn, peerMedianDrawdown, retGap, drawdownGap, issues, action, tone, actionText };
  }
  function decisionCard(title, value, body, tone = "") {
    return `<div class="focus-decision-card ${tone}"><strong>${value}</strong><p><b>${B.esc(title)}：</b>${body}</p></div>`;
  }
  function rangeButtons() {
    return `<div class="range-tabs">${ranges.map(([key, text]) => `<button type="button" data-range="${key}" class="${key === activeRange ? "is-active" : ""}">${B.esc(text)}</button>`).join("")}</div>`;
  }
  function selectedGlobalBenchmark() {
    return globalBenchmarks.find((row) => row.code === selectedGlobalBenchmarkCode) || null;
  }
  function selectedContributionGlobalBenchmark() {
    return globalBenchmarks.find((row) => row.code === selectedContributionGlobalBenchmarkCode) || null;
  }
  function selectedGlobalBenchmarkSeriesName() {
    const selected = selectedGlobalBenchmark();
    return selected ? `全局基准：${selected.name}` : "";
  }
  function selectedContributionGlobalBenchmarkSeriesName() {
    const selected = selectedContributionGlobalBenchmark();
    return selected ? `全局基准：${selected.name}` : "";
  }
  function mainChartSeries() {
    const series = { ...(detail.curves || {}) };
    const strategyId = String(detail.summary?.统一策略ID || "");
    if (strategyId.startsWith("gfbank_cgb__")) {
      ["披露业绩", "基准业绩"].forEach((name) => {
        const payload = series[name];
        const points = Array.isArray(payload) ? payload : (payload?.points || []);
        const mode = payload?.模式 || points[0]?.模式 || "nav";
        if (!points.length) return;
        const allTimeValueMode = ["return", "return_pct"].includes(mode)
          ? "return_pct"
          : (mode === "nav" && Number(points[0]?.数值) > 0 && Number(points[0]?.数值) <= 10 ? "unit_nav" : "");
        if (!allTimeValueMode) return;
        series[name] = Array.isArray(payload)
          ? { 模式: mode, points: payload, allTimeValueMode }
          : { ...payload, allTimeValueMode };
      });
    }
    const selected = selectedGlobalBenchmark();
    if (selected && Array.isArray(selected.points) && selected.points.length) {
      series[selectedGlobalBenchmarkSeriesName()] = { 模式: "nav", points: selected.points };
    }
    return series;
  }
  function globalBenchmarkSelectHtml() {
    return `<select id="globalBenchmarkSelect" class="control benchmark-select"><option value="">选择全局基准</option>${globalBenchmarks.map((row) => `<option value="${B.esc(row.code)}" ${row.code === selectedGlobalBenchmarkCode ? "selected" : ""}>${B.esc(row.name)}｜${B.esc(row.code)}</option>`).join("")}</select>`;
  }
  function contributionGlobalBenchmarkSelectHtml() {
    return `<select id="contributionGlobalBenchmarkSelect" class="control benchmark-select"><option value="">选择全局基准</option>${globalBenchmarks.map((row) => `<option value="${B.esc(row.code)}" ${row.code === selectedContributionGlobalBenchmarkCode ? "selected" : ""}>${B.esc(row.name)}｜${B.esc(row.code)}</option>`).join("")}</select>`;
  }
  function intervalMatrixTable() {
    const byName = Object.fromEntries((detail.intervalMatrix || []).map((row) => [row.口径, row]));
    const rows = curveRows.map((name) => byName[name] || { 口径: name });
    const head = intervalHeaders.map((h) => `<th>${B.label(h)}</th>`).join("");
    const body = rows.map((row) => `<tr>${intervalHeaders.map((h) => {
      if (h === "口径") return `<td><strong>${B.esc(row[h])}</strong></td>`;
      return `<td>${B.pctSigned(row[h])}</td>`;
    }).join("")}</tr>`).join("");
    const disclosed = byName.披露业绩 || {};
    const intervalFields = intervalHeaders.slice(1);
    const available = intervalFields.filter((field) => num(disclosed[field]) !== null);
    const missing = intervalFields.filter((field) => num(disclosed[field]) === null);
    const disclosure = !hasDrawableStrategyCurve
      ? `<div class="strategy-performance-availability"><strong>当前只有官方区间收益，没有真实历史走势图。</strong><span>已披露：${B.esc(available.join("、") || "无")}；未披露：${B.esc(missing.join("、") || "无")}。缺失区间不补算、不用截图或图像反推点替代。</span></div>`
      : "";
    return `${disclosure}<div class="table-wrap interval-matrix"><table><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table></div>`;
  }
  function annualPerformanceTable() {
    const headers = ["年度", "披露业绩", "模拟业绩", "基准业绩", "沪深300业绩"];
    const rows = detail.annualMatrix || [];
    const head = headers.map((h) => `<th>${B.label(h)}</th>`).join("");
    const body = rows.length ? rows.map((row) => `<tr>${headers.map((h) => {
      if (h === "年度") return `<td><strong>${B.esc(row[h])}</strong></td>`;
      return `<td>${B.pctSigned(row[h])}</td>`;
    }).join("")}</tr>`).join("") : `<tr><td colspan="${headers.length}"><div class="empty">暂无年度业绩</div></td></tr>`;
    return `<div class="table-wrap interval-matrix"><table><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table></div>`;
  }
  function performanceTabsHtml() {
    const tabs = [
      ...(hasDrawableStrategyCurve ? [["curve", "净值曲线"]] : []),
      ["interval", hasDrawableStrategyCurve ? "区间收益" : "官方区间收益"],
      ...(hasAnnualPerformance ? [["annual", "年度收益"]] : []),
      ...(hasRiskMetrics ? [["risk", "风险指标"]] : []),
    ];
    return `<div class="data-tabs strategy-performance-tabs">${tabs.map(([key, text]) => `<button type="button" data-performance-tab="${key}" class="${activePerformanceTab === key ? "is-active" : ""}">${text}</button>`).join("")}</div>`;
  }
  function renderPerformanceTable() {
    const host = B.byId("performanceTable");
    if (!host) return;
    host.innerHTML = activePerformanceTab === "annual" ? annualPerformanceTable() : intervalMatrixTable();
  }
  function renderPerformanceTabs() {
    const host = B.byId("performanceTabs");
    if (!host) return;
    host.innerHTML = performanceTabsHtml();
    host.querySelectorAll("[data-performance-tab]").forEach((button) => {
      button.addEventListener("click", () => {
        activePerformanceTab = button.dataset.performanceTab;
        renderPerformanceTabs();
      });
    });
    document.querySelectorAll("[data-performance-pane]").forEach((pane) => {
      pane.hidden = pane.dataset.performancePane !== activePerformanceTab;
    });
    if (["interval", "annual"].includes(activePerformanceTab)) renderPerformanceTable();
    if (activePerformanceTab === "curve") renderMainChart();
  }
  function holdingValue(row, h) {
    if (h === "基金代码") return `<strong>${fundLink(row, row[h] || "")}</strong>`;
    if (h === "基金名称") return `<div class="strategy-fund-name">${fundLink(row, row[h] || "未命名基金")}<small>${B.esc(row.基金代码 || "--")}</small></div>`;
    if (h === "二级分类") return B.esc(secondaryCategory(row, fundData(row)));
    if (["权重", "上次调仓后权重"].includes(h)) return B.pct(row[h]);
    if (["权重变化", "日涨幅", "调仓后收益率", "调仓后收益贡献"].includes(h)) return B.pctSigned(row[h]);
    return B.fmt(row[h]);
  }
  function holdingSortHeader(h) {
    const active = holdingSortField === h;
    const arrow = active ? (holdingSortDir === "asc" ? "▲" : "▼") : "↕";
    return `<th><span class="sort-head ${active ? "is-active" : ""}" role="button" tabindex="0" data-holding-sort="${B.esc(h)}">${B.label(h)}<span class="sort-arrow">${arrow}</span></span></th>`;
  }
  function compareHolding(a, b, h) {
    if (["权重", "上次调仓后权重", "权重变化", "基金净值", "日涨幅", "调仓后收益率", "调仓后收益贡献"].includes(h)) {
      const av = Number(a[h]);
      const bv = Number(b[h]);
      return (Number.isFinite(av) ? av : -999999) - (Number.isFinite(bv) ? bv : -999999);
    }
    if (h.includes("日期")) return String(a[h] || "").localeCompare(String(b[h] || ""));
    return String(a[h] || "").localeCompare(String(b[h] || ""), "zh-CN");
  }
  function holdingTable(rows) {
    const sortedRows = [...rows].sort((a, b) => {
      const compared = compareHolding(a, b, holdingSortField);
      return holdingSortDir === "asc" ? compared : -compared;
    });
    const head = holdingHeaders.map((h) => holdingSortHeader(h)).join("");
    const body = sortedRows.length ? sortedRows.map((row) => `<tr>${holdingHeaders.map((h) => `<td>${holdingValue(row, h)}</td>`).join("")}</tr>`).join("") : `<tr><td colspan="${holdingHeaders.length}"><div class="empty">暂无持仓明细</div></td></tr>`;
    return `<div class="table-wrap"><table class="compact-table"><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table></div>`;
  }
  function renderMainChart() {
    const selectedName = selectedGlobalBenchmarkSeriesName();
    const chartHost = B.byId("navChart");
    if (!chartHost) return;
    const officialPoints = officialCurvePoints;
    const simulatedPoints = simulatedCurvePoints;
    if (!hasDrawableStrategyCurve) {
      chartHost.innerHTML = `
        <div class="empty">
          <strong>暂无真实业绩走势图</strong><br/>
          当前渠道尚未提供可验证的结构化逐日净值或收益序列；页面仅展示已取得的官方区间收益，不使用截图或图像反推点替代。
        </div>`;
      const sourceHost = B.byId("sourceCards");
      if (sourceHost) sourceHost.innerHTML = sourceCards();
      return;
    }
    const primaryName = officialPoints.length >= 2 ? "披露业绩" : "模拟业绩";
    const series = mainChartSeries();
    const benchmarkPayload = series["基准业绩"];
    const benchmarkPoints = Array.isArray(benchmarkPayload) ? benchmarkPayload : (benchmarkPayload?.points || []);
    const defaultSeries = [
      primaryName,
      ...(benchmarkPoints.length >= 2 ? ["基准业绩"] : []),
      ...(selectedName ? [selectedName] : []),
    ];
    B.drawReturnChart(chartHost, series, { range: activeRange, title: "净值曲线", defaultVisibleSeries: defaultSeries, maxGapDays: 45 });
    const sourceHost = B.byId("sourceCards");
    if (sourceHost) sourceHost.innerHTML = sourceCards();
  }
  function renderRangeTabs() {
    const host = B.byId("rangeTabs");
    if (!host) return;
    host.innerHTML = rangeButtons();
    host.querySelectorAll("button").forEach((button) => {
      button.addEventListener("click", () => {
        activeRange = button.dataset.range;
        renderRangeTabs();
        renderMainChart();
      });
    });
  }
  function renderSnapshotList() {
    const list = B.byId("rebalanceList");
    const history = historicalSnapshots();
    list.innerHTML = history.length ? history.map(({ snapshot: snap, index }) => `
      <button class="rebalance-item ${index === activeSnapshotIndex ? "is-active" : ""}" type="button" data-snapshot-index="${index}">
        <strong>${B.esc(snap.日期 || "未披露日期")}</strong>
        <span>${B.esc(snap.标题 || "组合调整")}</span>
        <span>${B.esc(snap.说明 || `${(snap.holdings || []).length} 只基金`)}</span>
      </button>`).join("") : '<div class="empty">暂无历史调仓</div>';
    list.querySelectorAll("[data-snapshot-index]").forEach((button) => {
      button.addEventListener("click", () => {
        activeSnapshotIndex = Number(button.dataset.snapshotIndex);
        renderPositions();
      });
    });
  }
  function contributionSeriesNames(payload) {
    const series = payload?.series || {};
    return Object.entries(series)
      .filter(([, item]) => Array.isArray(item?.points) && item.points.length >= 2)
      .map(([name]) => name);
  }
  function hasDrawableContributionPayload(payload) {
    return contributionSeriesNames(payload).length > 0;
  }
  function preferredContributionFallback() {
    return snapshots
      .filter((item) => item.id !== "current")
      .map((item) => ({ snapshot: item, payload: contributionPayloadForSnapshot(item) }))
      .find((item) => hasDrawableContributionPayload(item.payload)) || { snapshot: null, payload: null };
  }
  function contributionPayloadForSnapshot(snapshot) {
    const curves = detail.contributionCurves || {};
    if (snapshot?.id && curves[String(snapshot.id)]) return curves[String(snapshot.id)];
    const date = raw(snapshot?.日期);
    if (!date) return null;
    return Object.values(curves).find((payload) => raw(payload?.起始日期) === date && hasDrawableContributionPayload(payload))
      || Object.values(curves).find((payload) => raw(payload?.起始日期) === date)
      || null;
  }
  function contributionFor(snapshot) {
    if (snapshot && snapshot.id && snapshot.id !== "current") {
      return { snapshot, payload: contributionPayloadForSnapshot(snapshot) };
    }
    return preferredContributionFallback();
  }
  function contributionEmptyText(snapshot, payload) {
    if (snapshot?.id === "current") return "当前仓位不是一次调仓事件；已优先寻找最近一条可评价历史调仓。若仍为空，说明该策略暂无调仓质量曲线。";
    if (payload && !hasDrawableContributionPayload(payload)) return "该次调仓已有调仓质量记录，但调仓前、调仓后、基准和沪深300曲线在该区间均没有足够可画点。常见原因是区间端点缺少策略净值或基金净值。";
    return "该次调仓尚无可用于绘制贡献曲线的调仓质量评估数据，通常是缺少调仓质量事件记录、下次调仓锚点或可比净值区间。";
  }
  function renderContribution(snapshot) {
    const target = contributionFor(snapshot);
    const desc = B.byId("contributionDesc");
    const drawableNames = contributionSeriesNames(target.payload);
    if (!target.payload || !drawableNames.length) {
      desc.textContent = contributionEmptyText(target.snapshot || snapshot, target.payload);
      B.drawReturnChart(B.byId("contributionChart"), {}, { alreadyReturn: false, title: "调仓贡献曲线" });
      return;
    }
    const meta = target.payload || {};
    const selected = selectedContributionGlobalBenchmark();
    const selectedName = selectedContributionGlobalBenchmarkSeriesName();
    const series = { ...(target.payload.series || {}) };
    if (selected && Array.isArray(selected.points) && selected.points.length) {
      series[selectedName] = { 模式: "nav", points: selected.points };
    }
    const usingCurrentFallback = snapshot?.id === "current" && target.snapshot && target.snapshot.id !== "current";
    const dateRange = `${meta.起始日期 || target.snapshot?.日期 || ""} 至 ${meta.结束日期 || "最新"}`;
    desc.textContent = usingCurrentFallback
      ? `当前仓位默认对标最近一次可评价调仓：${target.snapshot.日期 || ""}｜${target.snapshot.标题 || ""}。${dateRange}，默认展示调仓前后仓位曲线；基准、沪深300和全局基准可在图例中勾选。`
      : `${dateRange}，默认展示调仓前后仓位曲线；基准、沪深300和全局基准可在图例中勾选。`;
    const preferred = ["调仓前仓位模拟", "调仓后仓位实际"].filter((name) => drawableNames.includes(name));
    const defaultVisible = [...(preferred.length ? preferred : drawableNames.slice(0, 2)), ...(selectedName ? [selectedName] : [])];
    B.drawReturnChart(B.byId("contributionChart"), series, { alreadyReturn: false, title: "调仓贡献曲线", height: 280, defaultVisibleSeries: defaultVisible });
  }
  function snapshotResearchBlock(snap) {
    if (snap.类型 !== "历史调仓") return "";
    const meta = [
      snap.披露日期 ? `披露日期：${B.esc(snap.披露日期)}` : "",
      snap.调仓逻辑 ? `逻辑：${B.esc(snap.调仓逻辑)}` : "",
      snap.涉及资产 ? `涉及资产：${B.esc(snap.涉及资产)}` : ""
    ].filter(Boolean);
    const reason = raw(snap.调仓原因).trim() || "该次调仓未披露具体原因。";
    const summary = raw(snap.AI投研总结 || snap.投研摘要).trim() || "当前调仓明细缺少足够资产/行业分类信息，暂无法生成投研摘要。";
    return `
      <div class="rebalance-research">
        <div class="rebalance-research-meta">${meta.map((text) => `<span>${text}</span>`).join("")}</div>
        <div class="rebalance-research-grid">
          <div><strong>披露原因</strong><p>${B.esc(reason)}</p></div>
          <div><strong>AI投研摘要</strong><p>${B.esc(summary)}</p></div>
        </div>
      </div>`;
  }
  function renderPositions() {
    activeSnapshotIndex = Math.max(0, Math.min(activeSnapshotIndex, Math.max(0, snapshots.length - 1)));
    const snap = snapshots[activeSnapshotIndex] || { holdings: [] };
    renderSnapshotList();
    B.byId("holdingHead").innerHTML = `
      <div>
        <h3>${B.esc(snap.标题 || "当前仓位")}</h3>
        <p>${B.esc(snap.类型 || "")}｜${B.esc(snap.日期 || "未披露日期")}｜${B.esc(snap.说明 || "")}</p>
      </div>
      <span class="pill">${(snap.holdings || []).length.toLocaleString("zh-CN")} 只基金</span>`;
    const researchHost = B.byId("rebalanceResearch");
    if (researchHost) researchHost.innerHTML = snapshotResearchBlock(snap);
    B.byId("holdingTable").innerHTML = compactHoldingTable(snap.holdings || [], "history");
    const cardsHost = B.byId("holdingCards");
    if (cardsHost) cardsHost.innerHTML = compactHoldingCards(snap.holdings || [], "history");
    B.byId("holdingTable").querySelectorAll("[data-holding-sort]").forEach((button) => {
      button.addEventListener("click", (event) => {
        if (event.target.closest("[data-field]")) return;
        const field = button.dataset.holdingSort;
        if (holdingSortField === field) holdingSortDir = holdingSortDir === "asc" ? "desc" : "asc";
        else {
          holdingSortField = field;
          holdingSortDir = ["权重", "上次调仓后权重", "基金净值", "日涨幅", "调仓后收益率", "调仓后收益贡献"].includes(field) ? "desc" : "asc";
        }
        renderPositions();
      });
    });
    renderContribution(snap);
  }

  function signalMetricCard(labelName, value, formatter = B.fmt, sub = "") {
    const htmlValue = value === null || value === undefined || value === "" ? "未披露" : formatter(value);
    return `<div class="signal-metric"><span>${B.label(labelName)}</span><strong>${htmlValue}</strong>${sub ? `<em>${B.esc(sub)}</em>` : ""}</div>`;
  }
  function signalDirectionClass(direction) {
    if (["买入", "加仓", "调入"].includes(raw(direction))) return "is-buy";
    if (["卖出", "减仓", "调出"].includes(raw(direction))) return "is-sell";
    return "is-neutral";
  }
  function signalReturnCell(row, labelName) {
    const value = row[`方向收益_${labelName}`];
    const rating = raw(row[`评价_${labelName}`]);
    const status = rating && rating !== "不可评价" ? rating : "";
    return `<span class="${returnTone(value)}">${B.pctSigned(value)}</span>${status ? `<small class="signal-rating">${B.esc(status)}</small>` : ""}`;
  }
  function signalInstructionTable(rows) {
    const headers = ["基金代码", "基金名称", "分组名称", "指令方向", "调前权重", "调后权重", "权重变化", "1月", "3月", "6月", "1年", "数据状态"];
    const body = rows.length ? rows.map((row) => `<tr>
      <td>${fundLink(row, row.基金代码 || "--")}</td>
      <td>${fundLink(row, row.基金名称 || row.基金代码 || "未命名基金")}</td>
      <td>${B.esc(row.分组名称 || "--")}</td>
      <td><span class="signal-direction ${signalDirectionClass(row.指令方向)}">${B.esc(row.指令方向 || "--")}</span></td>
      <td>${B.pct(row.调前权重_百分比)}</td>
      <td>${B.pct(row.调后权重_百分比)}</td>
      <td class="${returnTone(row.权重变化_百分点)}">${B.pctSigned(row.权重变化_百分点)}</td>
      <td>${signalReturnCell(row, "1月")}</td>
      <td>${signalReturnCell(row, "3月")}</td>
      <td>${signalReturnCell(row, "6月")}</td>
      <td>${signalReturnCell(row, "1年")}</td>
      <td>${B.esc(row.数据状态 || "--")}</td>
    </tr>`).join("") : `<tr><td colspan="${headers.length}"><div class="empty">该信号暂无基金级指令明细</div></td></tr>`;
    return `<div class="table-wrap"><table class="compact-table signal-instruction-table"><thead><tr>${headers.map((h) => `<th>${B.label(h)}</th>`).join("")}</tr></thead><tbody>${body}</tbody></table></div>`;
  }
  function signalEventCard(event, index) {
    const instructions = event.instructions || [];
    const title = event.信号标题 || event.信号原因 || "信号调整";
    const reason = event.信号原因 || "未披露具体信号原因";
    const metrics = [
      ["指令数", event.指令数, B.fmt, `买入${event.买入指令数 || 0} / 卖出${event.卖出指令数 || 0}`],
      ["净买入权重", event.净买入权重_百分点, B.pctSigned, "调后-调前合计"],
      ["总调整强度", event.总调整强度_百分点, B.pct, "权重变化绝对值合计"],
      ["信号胜率", event.胜率_3月, B.pct, "3月方向评价"],
      ["信号加权方向收益", event.加权方向收益_3月, B.pctSigned, "3月强度加权"],
    ];
    return `<details class="signal-event-card" ${index === 0 ? "open" : ""}>
      <summary>
        <div>
          <strong>${B.esc(event.信号日期 || "未披露日期")} ${B.esc(event.信号时间 || "")}</strong>
          <span>${B.esc(title)}</span>
        </div>
        <em>${instructions.length.toLocaleString("zh-CN")} 条基金指令</em>
      </summary>
      <div class="signal-event-body">
        <p class="desc">${B.esc(reason)}</p>
        <div class="signal-metric-grid">${metrics.map(([labelName, value, formatter, sub]) => signalMetricCard(labelName, value, formatter, sub)).join("")}</div>
        ${signalInstructionTable(instructions)}
        ${event.原始快照路径 ? `<p class="small">原始快照：${B.esc(event.原始快照路径)}</p>` : ""}
      </div>
    </details>`;
  }
  function signalSection() {
    if (!signalEvents.length && !Number(signalSummary.信号事件数 || 0)) return "";
    const desc = "信号类策略按发车信号和基金级买卖指令复盘。每条信号被视为一次局部调仓，胜率按买卖方向和后续基金收益评价。";
    const metrics = [
      ["信号事件数", signalSummary.信号事件数, B.fmt, "历史发车/调整次数"],
      ["最近信号日", signalSummary.最近信号日, B.fmt, "最新披露信号"],
      ["信号指令数", signalSummary.信号指令数, B.fmt, "基金级买卖指令"],
      ["信号胜率", signalSummary.信号胜率_3月, B.pct, "3月方向胜率"],
      ["信号加权方向收益", signalSummary.信号加权方向收益_3月, B.pctSigned, "3月强度加权"],
    ];
    return `<section class="panel signal-panel">
      <div class="panel-head">
        <div>
          <h2>历史发车信号</h2>
          <p class="desc">${B.esc(desc)}</p>
        </div>
        <span class="pill">${(signalEvents.length || Number(signalSummary.信号事件数 || 0)).toLocaleString("zh-CN")} 次信号</span>
      </div>
      <div class="signal-metric-grid">${metrics.map(([labelName, value, formatter, sub]) => signalMetricCard(labelName, value, formatter, sub)).join("")}</div>
      <div class="signal-event-list">${signalEvents.length ? signalEvents.map(signalEventCard).join("") : '<div class="empty">已识别为信号类策略，但当前详情包暂无历史信号事件。请先运行“构建信号类策略事件.py”。</div>'}</div>
    </section>`;
  }

  function governanceSection() {
    const s = detail.summary || {};
    const fields = [
      ["治理状态", s.策略治理状态],
      ["分析分组", s.分析分组],
      ["是否纳入常规排名", s.是否纳入常规排名],
      ["是否单独分析", s.是否单独分析],
      ["业绩分析截止日期", s.业绩分析截止日期],
      ["持仓处理方式", s.持仓处理方式],
      ["调仓展示方式", s.调仓展示方式],
      ["规则说明", s.治理规则说明],
    ].filter(([, value]) => !isBlank(value));
    if (!fields.length) return "";
    const state = s.策略治理状态 || "正常运行";
    const isSignal = flagOn(s.是否信号类组合);
    const badges = [
      s.是否测试组合 ? "测试组合剔除" : "",
      isSignal ? "信号类策略" : "",
      s.是否目标盈期次 ? "目标盈/期次" : "",
      s.是否已停止 ? "已停止" : "",
      Number(s.是否纳入常规排名 ?? 1) === 1 ? "纳入常规排名" : "不纳入常规排名",
    ].filter(Boolean);
    const desc = isSignal
      ? "该策略按信号清单、买入/卖出指令和份数管理展示，不把候选基金池误当成真实持仓权重。"
      : s.是否已停止 || s.是否目标盈期次
        ? "该策略进入历史/期次复盘口径，业绩分析以停止或到期前可得披露数据为边界。"
        : state === "当前基金权重未完整披露"
          ? "App 当前详情未披露基金级权重，页面使用最新调仓后权重并按基金净值滚动补齐。"
          : "该策略按普通投顾组合口径进入常规列表、排名、调仓和持仓分析。";
    return `<section class="panel">
      <div class="panel-head">
        <div>
          <h2>策略治理口径</h2>
          <p class="desc">${B.esc(desc)}</p>
        </div>
        <span class="pill">${B.esc(state)}</span>
      </div>
      <div class="hero-meta">${badges.map((item) => `<span class="pill">${B.esc(item)}</span>`).join("")}</div>
      ${B.valueList(fields.map(([字段, 值]) => ({ 字段, 值 })))}
    </section>`;
  }

  function targetIssueNumber(name) {
    const textValue = raw(name);
    const match = textValue.match(/(?:第)?(\d{1,5})\s*期/) || textValue.match(/目标盈\s*(\d{6,8})/) || textValue.match(/(\d{6,8})$/);
    return match ? Number(match[1]) : null;
  }

  function normalizeTargetFamilyName(name) {
    let value = raw(name);
    value = value.replace(/第?[零〇一二三四五六七八九十百千万\d]{1,5}期/g, "");
    value = value.replace(/\d{1,5}期/g, "");
    value = value.replace(/目标盈\s*\d{6,8}$/g, "目标盈");
    value = value.replace(/\d{6,8}$/g, "");
    value = value.replace(/[（(][^）)]{1,16}?版[）)]/g, "");
    value = value.replace(/天天\d*/g, "");
    value = value.replace(/年中版/g, "");
    value = value.replace(/\s+/g, "");
    value = value.replace(/[\\\-_—]+$/g, "");
    value = value.replace(/（\s*）/g, "");
    return value.trim() || raw(name);
  }

  function targetSeriesSection() {
    const summary = detail.summary || {};
    const isTarget = summary.业务分类 === "目标盈系列产品" || flagOn(summary.是否目标盈期次) || /目标盈|小目标|小赢家|小星愿|止盈/.test(raw(summary.策略名称));
    if (!isTarget) return "";
    const familyName = normalizeTargetFamilyName(summary.策略名称);
    const allRows = (B.state.summary?.strategies || []).filter((row) => {
      if (!(row.业务分类 === "目标盈系列产品" || flagOn(row.是否目标盈期次) || /目标盈|小目标|小赢家|小星愿|止盈/.test(raw(row.策略名称)))) return false;
      return normalizeTargetFamilyName(row.策略名称) === familyName;
    }).sort((a, b) => {
      const ai = targetIssueNumber(a.策略名称);
      const bi = targetIssueNumber(b.策略名称);
      if (ai !== null || bi !== null) return (ai ?? 99999999) - (bi ?? 99999999);
      return raw(a.成立日期).localeCompare(raw(b.成立日期), "zh-CN") || raw(a.策略名称).localeCompare(raw(b.策略名称), "zh-CN");
    });
    if (allRows.length <= 1) return "";
    const activeRows = allRows.filter((row) => row.天天当前对客展示 === "是" && !/终止|到期|期满|stopped|已结束|非对客|下架/.test(`${row.运作状态 || ""} ${row.天天展示状态 || ""}`));
    const currentId = summary.统一策略ID || detail.id;
    return `<section class="panel target-related-panel">
      <div class="panel-head">
        <div>
          <h2>同系列期次</h2>
          <p class="desc">按产品名称去掉期次号、发行批次号、“天天”和版本括号后归为同一系列；版本差异仍保留在期次名称中。</p>
        </div>
        <div class="title-pills">
          <span class="pill">${B.esc(familyName)}</span>
          <span class="pill">${allRows.length.toLocaleString("zh-CN")} 期</span>
          <span class="pill">运行中 ${activeRows.length.toLocaleString("zh-CN")} 期</span>
        </div>
      </div>
      <div class="target-related-list">
        ${allRows.map((row) => {
          const isCurrent = row.统一策略ID === currentId;
          const status = row.天天当前对客展示 === "是" ? "运行中/展示" : (row.运作状态 || row.天天展示状态 || "未披露");
          return `<a class="target-related-chip ${isCurrent ? "is-current" : ""}" href="./strategy.html?id=${encodeURIComponent(row.统一策略ID || "")}">
            <strong>${B.esc(row.策略名称 || row.统一策略ID)}</strong>
            <span>${B.esc(status)}｜${B.esc(row.成立日期 || row.最新业绩日期 || "日期未披露")}｜${B.pctSigned(row.近1年)}</span>
          </a>`;
        }).join("")}
      </div>
      <p class="small"><a class="link" href="./target-profit-analysis.html">进入目标盈分析页查看全系列生命周期表现</a></p>
    </section>`;
  }

  function otherInformationSection() {
    const secondaryModules = [governanceSection(), targetSeriesSection(), signalSection(), entityGraphSection()].filter(Boolean).join("");
    const benchmarkBucket = classificationMap.基准风险资产权重 || "未披露";
    return `<section id="strategy-more" class="panel strategy-section strategy-more-section">
      <div class="panel-head strategy-section-head">
        <div><h2>其他信息</h2><p class="desc">完整分类依据、产品字段、专项分析和穿透口径默认折叠。</p></div>
      </div>
      <div class="strategy-more-list">
        <details class="strategy-more-block">
          <summary><div><strong>策略分类与基准</strong><span>基准风险资产权重 ${B.esc(benchmarkBucket)}｜${B.esc(profileMap.业绩基准 || detail.summary.业绩基准 || "基准未披露")}</span></div></summary>
          <div class="strategy-more-body">
            <div class="profile-block classification-block"><h3>分类影响指标</h3>${classificationSummary()}</div>
            <div class="profile-block benchmark-asset-block"><h3>基准资产结构</h3>${benchmarkAssetStructure()}</div>
            ${benchmarkInfo()}
          </div>
        </details>
        <details class="strategy-more-block">
          <summary><div><strong>产品基础信息与治理口径</strong><span>代码、风险、起投金额、建议持有期及特殊治理规则</span></div></summary>
          <div class="strategy-more-body">
            ${B.valueList(compactInfoRows())}
            <div class="strategy-secondary-modules">${secondaryModules || '<div class="empty">暂无专项治理或关联信息</div>'}</div>
          </div>
        </details>
        <details class="strategy-more-block">
          <summary><div><strong>完整持仓穿透信息</strong><span>基金分类、配置日志、经济资产暴露及基金区间表现</span></div></summary>
          <div class="strategy-more-body strategy-detailed-holdings">${portfolioHoldingsSection()}</div>
        </details>
        <details class="strategy-more-block">
          <summary><div><strong>完整字段与数据口径</strong><span>低频字段、持仓来源和计算说明</span></div></summary>
          <div class="strategy-more-body">
            ${B.valueList(classificationInfoRows())}
            ${B.valueList(Object.entries(detail.holdingMeta || {}).map(([字段, 值]) => ({ 字段, 值 })))}
            ${B.valueList(otherRows())}
          </div>
        </details>
      </div>
    </section>`;
  }

  function bindSectionNavigation() {
    const links = [...document.querySelectorAll("[data-strategy-section-link]")];
    if (!links.length) return;
    links.forEach((link) => link.addEventListener("click", () => {
      links.forEach((item) => item.classList.toggle("is-active", item === link));
    }));
    if (!("IntersectionObserver" in window)) return;
    const sectionById = new Map(links.map((link) => [link.getAttribute("href").slice(1), link]));
    const observer = new IntersectionObserver((entries) => {
      const visible = entries.filter((entry) => entry.isIntersecting).sort((a, b) => b.intersectionRatio - a.intersectionRatio)[0];
      if (!visible) return;
      const activeLink = sectionById.get(visible.target.id);
      links.forEach((link) => link.classList.toggle("is-active", link === activeLink));
    }, { rootMargin: "-18% 0px -68% 0px", threshold: [0, 0.15, 0.45] });
    sectionById.forEach((link, sectionId) => {
      const section = document.getElementById(sectionId);
      if (section) observer.observe(section);
    });
  }

  root.innerHTML = `
    <section id="strategy-overview" class="panel strategy-overview strategy-section">
      <a class="strategy-back-link" href="./strategies.html">← 返回策略列表</a>
      <div class="strategy-identity-row">
        <div class="strategy-identity">
          <div class="strategy-title-line"><h1>${B.esc(detail.summary.策略名称)}</h1>${isStoppedSummary() ? `<span class="strategy-lifecycle-badge is-stopped">${B.esc(isLegacyArchive ? "历史接口留档" : "已下架")}</span>` : ""}${B.statusBadge(detail.summary.数据完整性)}</div>
          <p>销售渠道 ${B.esc(profileMap.渠道 || detail.summary.渠道 || "未披露")} ｜ 投顾管理人 ${B.esc(profileMap.投顾机构 || detail.summary.投顾机构 || "未披露")}</p>
        </div>
        <div class="strategy-status-list ${isStoppedSummary() ? "is-stopped" : ""}">
          <span>对客 ${B.esc(classificationMap.天天当前对客展示 || detail.summary.天天当前对客展示 || "未披露")}</span>
          <span>${B.esc(detail.summary.运作状态 || "运作状态未披露")}</span>
          <span>${B.esc(classificationMap.研报产品类型 || detail.summary.研报产品类型 || "类型未披露")}</span>
          <span>${B.esc(classificationMap.业务分类 || detail.summary.业务分类 || "业务分类未披露")}</span>
          <span>${B.esc(classificationMap.基准风险资产权重 || "基准风险资产权重未披露")}</span>
        </div>
      </div>
      ${stoppedStrategyBanner()}
      ${ownershipFacts()}
      ${overviewFacts()}
      ${primaryMetricGrid()}
      <div class="strategy-overview-foot">数据刷新 ${B.esc(dataRefreshTime || "未披露")}｜策略代码 ${B.esc(profileMap.策略代码 || detail.summary.策略代码 || "未披露")}｜页面版本 ${B.esc(window.MinimalPublish?.buildId || "本地完整版")}</div>
    </section>
    <nav class="strategy-section-nav" aria-label="策略详情页内导航">
      <a class="is-active" data-strategy-section-link href="#strategy-overview">概览</a>
      <a data-strategy-section-link href="#strategy-performance">业绩</a>
      <a data-strategy-section-link href="#strategy-holding">当前仓位</a>
      <a data-strategy-section-link href="#strategy-rebalance">调仓记录</a>
      <a data-strategy-section-link href="#strategy-more">更多信息</a>
    </nav>
    <section id="strategy-performance" class="panel strategy-section strategy-performance-section">
      <div class="panel-head strategy-section-head">
        <div><h2>业绩与风险</h2><p class="desc">收益区间优先采用官方披露，曲线和年度数据用于趋势及相对表现分析。</p></div>
        <div id="performanceTabs"></div>
      </div>
      ${strategyRelationNotice()}
      <div data-performance-pane="curve">
        <div class="strategy-chart-toolbar">
          ${globalBenchmarkSelectHtml()}
          <div id="rangeTabs"></div>
        </div>
        <div id="navChart" class="chart strategy-main-chart"></div>
        <details class="strategy-source-details"><summary>数据来源与计算口径</summary><div id="sourceCards">${sourceCards()}</div></details>
      </div>
      <div data-performance-pane="interval" hidden>${intervalMatrixTable()}</div>
      <div data-performance-pane="annual" hidden>${annualPerformanceTable()}</div>
      <div data-performance-pane="risk" hidden>${riskMetricsPanel()}</div>
    </section>
    ${currentHoldingSection()}
    <section id="strategy-rebalance" class="panel strategy-section strategy-rebalance-section">
      <div class="panel-head strategy-section-head">
        <div><h2>调仓记录</h2><p class="desc">先看最近一次调仓摘要，再按时间线查看历史调仓明细与调仓后表现。</p></div>
      </div>
      ${latestRebalanceSummary()}
      <div class="strategy-rebalance-workspace">
        <aside class="strategy-rebalance-timeline"><div class="strategy-subsection-title"><h3>历史时间线</h3><p>${historicalSnapshots().length.toLocaleString("zh-CN")} 次调仓</p></div><div id="rebalanceList" class="rebalance-list"></div></aside>
        <div class="position-detail strategy-rebalance-detail">
          <div class="strategy-subsection-title"><h3>选中调仓明细与贡献分析</h3><p>点击左侧时间点切换</p></div>
          <div id="holdingHead" class="holding-head"></div>
          <div id="rebalanceResearch"></div>
          <div id="holdingTable"></div>
          <div id="holdingCards"></div>
          <div class="strategy-contribution-block">
            <div class="panel-head">
              <div><h3>调仓贡献曲线</h3><p id="contributionDesc" class="desc"></p></div>
              <div class="chart-actions">${contributionGlobalBenchmarkSelectHtml()}</div>
            </div>
            <div id="contributionChart" class="chart strategy-contribution-chart"></div>
          </div>
        </div>
      </div>
    </section>
    ${otherInformationSection()}
  `;
  const globalBenchmarkSelect = B.byId("globalBenchmarkSelect");
  if (globalBenchmarkSelect) {
    globalBenchmarkSelect.addEventListener("change", () => {
      selectedGlobalBenchmarkCode = globalBenchmarkSelect.value;
      renderMainChart();
    });
  }
  const contributionGlobalBenchmarkSelect = B.byId("contributionGlobalBenchmarkSelect");
  if (contributionGlobalBenchmarkSelect) {
    contributionGlobalBenchmarkSelect.addEventListener("change", () => {
      selectedContributionGlobalBenchmarkCode = contributionGlobalBenchmarkSelect.value;
      renderContribution(snapshots[activeSnapshotIndex] || null);
    });
  }
  renderRangeTabs();
  renderPerformanceTabs();
  renderMainChart();
  renderPositions();
  bindSectionNavigation();
})();
