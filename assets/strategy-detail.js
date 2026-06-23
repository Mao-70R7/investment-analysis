
(async () => {
  const B = window.BasicData;
  const root = B.byId("strategyDetailPage");
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
  const holdingHeaders = ["基金代码", "基金名称", "权重", "上次调仓后权重", "权重变化", "基金净值", "净值日期", "日涨幅", "调仓后收益率", "调仓后收益贡献"];
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
  function contributionFor(snapshot) {
    const curves = detail.contributionCurves || {};
    if (snapshot && snapshot.id && curves[String(snapshot.id)]) {
      return { snapshot, payload: curves[String(snapshot.id)] };
    }
    const fallback = snapshots.find((item) => item.id !== "current" && curves[String(item.id)]);
    return fallback ? { snapshot: fallback, payload: curves[String(fallback.id)] } : { snapshot: null, payload: null };
  }
  function renderContribution(snapshot) {
    const target = contributionFor(snapshot);
    const desc = B.byId("contributionDesc");
    if (!target.payload) {
      desc.textContent = "暂无可用于绘制调仓贡献曲线的调仓质量评估数据。";
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
    desc.textContent = `${meta.起始日期 || target.snapshot?.日期 || ""} 至 ${meta.结束日期 || "最新"}，默认展示调仓前后仓位曲线；基准、沪深300和全局基准可在图例中勾选。`;
    const defaultVisible = selectedName ? ["调仓前仓位模拟", "调仓后仓位实际", selectedName] : ["调仓前仓位模拟", "调仓后仓位实际"];
    B.drawReturnChart(B.byId("contributionChart"), series, { alreadyReturn: false, title: "调仓贡献曲线", height: 280, defaultVisibleSeries: defaultVisible });
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
