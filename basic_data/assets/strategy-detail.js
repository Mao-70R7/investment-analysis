
(async () => {
  const B = window.BasicData;
  const root = B.byId("strategyDetailPage");
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
  const intervalHeaders = ["口径", "近一周", "近一月", "近三月", "近1年", "今年以来", "成立以来"];
  const curveRows = ["披露业绩", "模拟业绩", "基准业绩", "沪深300业绩"];
  const holdingHeaders = ["基金代码", "基金名称", "二级分类", "权重", "上次调仓后权重", "权重变化", "基金净值", "净值日期", "日涨幅", "调仓后收益率", "调仓后收益贡献"];
  const snapshots = detail.positionSnapshots || [];
  const globalBenchmarks = B.state.summary?.globalBenchmarks || [];
  let activeRange = "all";
  let activePerformanceTab = "interval";
  let activeSnapshotIndex = 0;
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
    "资产配置估算": "基于 fund_detail_pack 中的基金资产暴露字段拆分股票、固收、现金、基金、其他和其中可转债。缺少资产暴露时按基金标准分类做兜底估算，不等同于基金最新季报原文披露。",
    "基金持有区间收益": "优先使用策略详情中该基金调仓后收益率；缺失时使用基金详情包内的区间收益率。",
    "基金近1年收益": "使用该基金日度净值计算的近1年收益率；缺失时显示未披露。"
  });
  const fundPack = window.__BASIC_DATA__?.fundDetailPack || {};
  const fundFields = fundPack.fundFields || [];
  const fundObjects = (fundPack.funds || []).map((row) => Object.fromEntries(fundFields.map((field, index) => [field, row[index] ?? ""])));
  const fundDataByCode = new Map(fundObjects.map((row) => [raw(row.基金代码), row]).filter(([code]) => code));
  const fundDataByName = new Map(fundObjects.map((row) => [raw(row.基金名称), row]).filter(([name]) => name));
  function semanticRows(packName) {
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
    return `<section class="panel entity-panel">
      <div class="panel-head">
        <div>
          <h2>实体图谱</h2>
          <p class="desc">基于最新持仓基金的结构化分类、资产暴露和基金名称抽取；权重为策略持仓权重按基金暴露比例汇总。</p>
        </div>
        <span class="pill">${rows.length.toLocaleString("zh-CN")} 个实体</span>
      </div>
      ${groups.length ? groups.map((group) => `
        <div class="entity-group">
          <h3>${B.esc(group.type)}</h3>
          <div class="entity-grid">${group.rows.map(entityBadge).join("")}</div>
        </div>
      `).join("") : '<div class="empty">当前策略暂无可展示实体。请先重建 AI 语义索引。</div>'}
    </section>`;
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
    const text = [row.资产类型, row.基金同类分组, row.分组, data.基金类型, data.研报大类资产, data.资产暴露].map(raw).join(" ");
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
    const pairs = parseExposurePairs(data.资产暴露 || data.研报大类资产 || "");
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
          <h2>组合基金持仓</h2>
          <p class="desc">参考组合持仓表模式展示底层基金、组合占比、配置日志和基金资产暴露。资产配置列来自基金详情包的资产暴露解析；缺失时按基金分类兜底估算。</p>
        </div>
        <span class="pill">${rows.length.toLocaleString("zh-CN")} 只基金</span>
      </div>
      <div class="portfolio-table-wrap">
        <table class="portfolio-holding-table">
          <thead>
            <tr class="portfolio-super-head">
              <th colspan="5">组合持仓与占比</th>
              <th colspan="2">配置日志</th>
              <th colspan="6">基金资产配置估算</th>
              <th colspan="2">区间个基表现</th>
            </tr>
            <tr>
              <th>${B.label("基金类型分类")}</th>
              <th>${B.label("基金代码")}</th>
              <th>${B.label("基金名称")}</th>
              <th>${B.label("二级分类")}</th>
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
          <tbody>${body || '<tr><td colspan="15"><div class="empty">暂无当前组合基金持仓</div></td></tr>'}</tbody>
          <tfoot>
            <tr>
              <td colspan="4">组合合计</td>
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
    const names = ["研报产品类型", "研报股票子类型", "研报分类依据", "风险等级", "权益风险档", "波动风险档", "回撤风险档", "风险触发指标", "风险分类依据", "业务分类", "业务分类依据", "业务组合分类", "业务分类标签", "天天展示状态", "天天当前对客展示", "天天上架生命周期", "天天展示判定依据", "市场地域", "主动被动", "特殊标签", "策略实现标签", "权益基金权重", "债券基金权重", "货币基金权重", "混合基金权重", "QDII权重", "指数基金权重", "主动基金权重", "基准权益权重", "基准债券权重", "基准货币权重", "基准可用状态", "基础数据等级", "分类依据"];
    return names.map((name) => ({ 字段: name, 值: classificationMap[name] ?? "未披露" }));
  }
  function classChip(labelName, value, main = false) {
    return `<div class="class-chip ${main ? "is-main" : ""}"><span>${B.label(labelName)}</span><strong>${B.valueHtml(labelName, value)}</strong></div>`;
  }
  function classMetric(labelName, value) {
    return `<div class="class-metric"><span>${B.label(labelName)}</span><strong>${B.valueHtml(labelName, value)}</strong></div>`;
  }
  function classificationSummary() {
    const holdingWeights = ["权益基金权重", "债券基金权重", "货币基金权重", "QDII权重", "指数基金权重", "主动基金权重"];
    const benchmarkWeights = ["基准权益权重", "基准债券权重", "基准货币权重"];
    return `<div class="classification-summary">
      <div class="class-chip-grid">
        ${classChip("研报产品类型", classificationMap.研报产品类型 || detail.summary.研报产品类型, true)}
        ${!isBlank(classificationMap.研报股票子类型 || detail.summary.研报股票子类型) ? classChip("研报股票子类型", classificationMap.研报股票子类型 || detail.summary.研报股票子类型) : ""}
        ${classChip("风险等级", classificationMap.风险等级 || detail.summary.风险等级, true)}
        ${classChip("业务分类", classificationMap.业务分类 || detail.summary.业务分类)}
        ${classChip("天天当前对客展示", classificationMap.天天当前对客展示 || detail.summary.天天当前对客展示)}
        ${classChip("天天展示状态", classificationMap.天天展示状态)}
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
  function sourceCards() {
    const sources = detail.curveSources || {};
    const meta = detail.benchmarkMeta || {};
    const metaText = meta.基准公式解析 ? `${meta.基准公式解析}${(meta.缺失组件 || []).length ? `；缺失：${meta.缺失组件.join("、")}` : ""}` : "";
    const selected = selectedGlobalBenchmark();
    const globalText = selected ? `<p><b>全局基准：</b>${B.esc(selected.name)}（${B.esc(selected.code)}），区间 ${B.esc(selected.start || "未披露")} 至 ${B.esc(selected.end || "未披露")}；数据来源：${B.esc(selected.source || "指数日度行情")}</p>` : "";
    const warnings = (detail.curveWarnings || []).map((text) => `<p class="warn"><b>${B.label("曲线数据提示")}：</b>${B.esc(text)}</p>`).join("");
    return `<div class="source-note-list">${warnings}${curveRows.map((name) => `<p><b>${B.esc(name)}：</b>${B.esc(sources[name] || "未生成来源说明")}</p>`).join("")}${globalText}${metaText ? `<p><b>基准公式解析：</b>${B.esc(metaText)}</p>` : ""}</div>`;
  }
  function isClientFacingSummary() {
    const current = String(classificationMap.天天当前对客展示 || detail.summary.天天当前对客展示 || "");
    const status = String(classificationMap.天天展示状态 || detail.summary.天天展示状态 || "");
    return !(current === "否" || /非对客|不对客|隐藏|未展示|不展示/.test(status));
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
    return `<div class="table-wrap interval-matrix"><table><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table></div>`;
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
    return `<div class="data-tabs"><button type="button" data-performance-tab="interval" class="${activePerformanceTab === "interval" ? "is-active" : ""}">常用区间</button><button type="button" data-performance-tab="annual" class="${activePerformanceTab === "annual" ? "is-active" : ""}">年度业绩</button></div>`;
  }
  function renderPerformanceTable() {
    B.byId("performanceTable").innerHTML = activePerformanceTab === "annual" ? annualPerformanceTable() : intervalMatrixTable();
  }
  function renderPerformanceTabs() {
    B.byId("performanceTabs").innerHTML = performanceTabsHtml();
    B.byId("performanceTabs").querySelectorAll("[data-performance-tab]").forEach((button) => {
      button.addEventListener("click", () => {
        activePerformanceTab = button.dataset.performanceTab;
        renderPerformanceTabs();
        renderPerformanceTable();
      });
    });
    renderPerformanceTable();
  }
  function holdingValue(row, h) {
    if (h === "基金代码") return `<strong>${fundLink(row, row[h] || "")}</strong>`;
    if (h === "基金名称") return fundLink(row, row[h] || "未命名基金");
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
    const defaultSeries = selectedName ? ["披露业绩", selectedName] : ["披露业绩"];
    B.drawReturnChart(B.byId("navChart"), mainChartSeries(), { range: activeRange, title: "净值曲线", defaultVisibleSeries: defaultSeries, maxGapDays: 45 });
    const sourceHost = B.byId("sourceCards");
    if (sourceHost) sourceHost.innerHTML = sourceCards();
  }
  function renderRangeTabs() {
    B.byId("rangeTabs").innerHTML = rangeButtons();
    B.byId("rangeTabs").querySelectorAll("button").forEach((button) => {
      button.addEventListener("click", () => {
        activeRange = button.dataset.range;
        renderRangeTabs();
        renderMainChart();
      });
    });
  }
  function renderSnapshotList() {
    const list = B.byId("rebalanceList");
    list.innerHTML = snapshots.length ? snapshots.map((snap, index) => `
      <button class="rebalance-item ${index === activeSnapshotIndex ? "is-active" : ""}" type="button" data-snapshot-index="${index}">
        <strong>${B.esc(snap.类型 || "")}｜${B.esc(snap.日期 || "未披露日期")}</strong>
        <span>${B.esc(snap.标题 || "")}</span>
        <span>${B.esc(snap.说明 || "")}</span>
      </button>`).join("") : '<div class="empty">暂无仓位快照</div>';
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
    B.byId("holdingTable").innerHTML = holdingTable(snap.holdings || []);
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

  root.innerHTML = `
    <section class="page-title">
      <div>
        <a class="link" href="./strategies.html">返回策略列表</a>
        <h1>策略详情</h1>
        <p class="desc">用于核验单个产品的经营定位、研报同类可比池、业务分类、对客状态、业绩和仓位。</p>
      </div>
      <span class="pill">${B.label("统一策略ID")} ${B.esc(detail.id)}</span>
    </section>
    <section class="panel hero-panel">
      <div class="strategy-hero">
        <div>
          <div class="hero-title">
            <h1>${B.esc(detail.summary.策略名称)}</h1>
            ${B.statusBadge(detail.summary.数据完整性)}
          </div>
          <div class="hero-meta">
            <span class="pill">${B.esc(detail.summary.渠道)}</span>
            <span class="pill">${B.esc(classificationMap.研报产品类型 || detail.summary.研报产品类型 || "未披露研报类型")}</span>
            <span class="pill">${B.esc(classificationMap.业务分类 || detail.summary.业务分类 || "未分类")}</span>
            <span class="pill">对客 ${B.esc(classificationMap.天天当前对客展示 || detail.summary.天天当前对客展示 || "未披露")}</span>
            <span class="pill">${B.esc(detail.summary.披露策略类型 || "未披露类型")}</span>
            <span class="pill">${B.esc(detail.summary.运作状态 || "未披露运作状态")}</span>
          </div>
          <div class="hero-dates">
            ${topFact("成立日期", detail.summary.成立日期, "is-date")}
            ${topFact("最新业绩日期", detail.summary.最新业绩日期 || detail.summary.收益数据截至 || "未披露", "is-date")}
            ${topFact("数据刷新时间", dataRefreshTime || "未披露")}
            <div class="date-card is-date"><span>${B.label("运作天数")}</span><strong>${B.fmt(detail.summary.运作天数, " 天")}</strong></div>
            ${topFact("投顾机构", profileMap.投顾机构 || detail.summary.投顾机构 || "未披露")}
            ${topFact("研报产品类型", classificationMap.研报产品类型 || detail.summary.研报产品类型 || "未披露")}
            ${topFact("研报股票子类型", classificationMap.研报股票子类型 || detail.summary.研报股票子类型 || "未披露")}
            ${topFact("风险等级", classificationMap.风险等级 || detail.summary.风险等级 || "未披露")}
            ${topFact("披露风险等级", profileMap.披露风险等级 || detail.summary.披露风险等级 || "未披露")}
            ${topFact("天天当前对客展示", classificationMap.天天当前对客展示 || detail.summary.天天当前对客展示 || "未披露")}
            ${topFact("投顾费率", profileMap.投顾费率 || "未披露")}
            ${topFact("市场地域", classificationMap.市场地域 || "未披露")}
            ${topFact("主动被动", classificationMap.主动被动 || "未披露")}
          </div>
          <p class="desc">${B.esc(detail.summary.运作状态 || "未披露运作状态")}｜最新业绩日 ${B.esc(detail.summary.最新业绩日期 || detail.summary.收益数据截至 || "未披露")}｜最新持仓日 ${B.esc(detail.holdingMeta.最新持仓日 || "未披露")}｜持仓来源 ${B.esc(detail.holdingMeta.持仓来源 || "未披露")}</p>
        </div>
        ${returnGrid()}
      </div>
      <div class="hero-support profile-compact">
        <div class="profile-block strategy-info-block">
          <h3>策略基本信息</h3>
          ${B.valueList(compactInfoRows())}
        </div>
        <div class="profile-block classification-block">
          <h3>分类影响指标</h3>
          ${classificationSummary()}
        </div>
        <div class="profile-block evaluation-block">
          <h3>评价核心数据</h3>
          ${coreMetrics()}
        </div>
        ${benchmarkInfo()}
      </div>
    </section>
    ${portfolioHoldingsSection()}
    ${entityGraphSection()}
    <section class="panel chart-panel">
      <div class="panel-head">
        <div>
          <h2>净值曲线</h2>
          <p class="desc">默认成立以来，切换区间后各曲线均按该区间起点归零展示相对收益率。</p>
        </div>
        <div class="chart-actions">
          ${globalBenchmarkSelectHtml()}
          <div id="rangeTabs"></div>
        </div>
      </div>
      <div id="navChart" class="chart"></div>
      <div id="sourceCards">${sourceCards()}</div>
    </section>
    <section class="panel">
      <div class="panel-head">
        <div><h2>区间业绩</h2><p class="desc">常用区间按最新可用点回看；年度业绩按自然年度首末可用点计算。</p></div>
        <div id="performanceTabs"></div>
      </div>
      <div id="performanceTable"></div>
    </section>
    <section class="panel">
      <div class="panel-head">
        <div>
          <h2>仓位</h2>
          <p class="desc">左侧为当前仓位和历史调仓列表，点击后右侧切换对应基金仓位明细。</p>
        </div>
        <span class="pill">${B.esc(detail.holdingMeta.稽核结论 || "未生成稽核")}</span>
      </div>
      <div class="position-layout">
        <div id="rebalanceList" class="rebalance-list"></div>
        <div class="position-detail">
          <div id="holdingHead" class="holding-head"></div>
          <div id="rebalanceResearch"></div>
          <div id="holdingTable"></div>
        </div>
      </div>
    </section>
    <section class="panel chart-panel">
      <div class="panel-head">
        <div>
          <h2>调仓贡献曲线</h2>
          <p id="contributionDesc" class="desc"></p>
        </div>
        <div class="chart-actions">
          ${contributionGlobalBenchmarkSelectHtml()}
        </div>
      </div>
      <div id="contributionChart" class="chart"></div>
    </section>
    <section class="panel">
      <div class="panel-head"><div><h2>数据质量与其他信息</h2><p class="desc">保留原详情页的质量检查、持仓口径和低覆盖字段；低覆盖或空值较多的字段默认折叠。</p></div></div>
      <div class="quality-grid">
        ${(detail.qualityChecks || []).map((row) => `<div class="quality-card"><h3>${B.esc(row.项目)}</h3>${B.statusBadge(row.结论)}<p>${B.esc(row.说明)}</p></div>`).join("")}
      </div>
      <details class="fold-block">
        <summary>持仓口径与其他字段</summary>
        ${B.valueList(classificationInfoRows())}
        ${B.valueList(Object.entries(detail.holdingMeta || {}).map(([字段, 值]) => ({ 字段, 值 })))}
        ${B.valueList(otherRows())}
      </details>
    </section>
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
})();
