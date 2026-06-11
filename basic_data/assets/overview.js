(() => {
  const B = window.BasicData;
  const summary = B.state.summary;
  const root = B.byId("overviewPage");
  const overview = summary.overview || {};
  const insight = summary.insightData || {};
  const points = insight.策略表现点 || [];
  const listStats = summary.strategyListStats || {};

  function raw(value) {
    return value === null || value === undefined ? "" : String(value);
  }

  function isGf(row) {
    return row?.是否广发 === "是" || row?.是否广发策略 === "是" || /广发基金|广发投顾/.test(`${row?.投顾机构 || ""} ${row?.渠道 || ""}`);
  }

  function isClientFacing(row) {
    const current = raw(row?.天天当前对客展示);
    const status = raw(row?.天天展示状态);
    return !(current === "否" || /非对客|不对客|隐藏|未展示|不展示/.test(status));
  }

  function normalizeSeriesName(name) {
    return raw(name)
      .replace(/第?[零一二三四五六七八九十百千万\d]{1,5}期/g, "")
      .replace(/\d{1,4}期/g, "")
      .replace(/天天\d{1,4}/g, "天天")
      .replace(/\s+/g, "")
      .replace(/[\\-_—]+$/g, "")
      .replace(/（\s*）/g, "")
      .trim() || raw(name);
  }

  function collapseTargetSeries(rows) {
    const out = [];
    const groups = new Map();
    rows.forEach((row) => {
      if (row.业务分类 !== "目标盈系列产品") {
        out.push(row);
        return;
      }
      const key = `${row.投顾机构 || ""}｜${normalizeSeriesName(row.策略名称)}`;
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key).push(row);
    });
    groups.forEach((list) => out.push({ ...list[0], 期次数: list.length }));
    return out;
  }

  const sourceTotal = Number(overview.策略总数 || 0);
  const masterStrategies = summary.strategies || [];
  const evidenceRecords = points.length;
  const hiddenGap = Math.max(0, sourceTotal - evidenceRecords);
  const operatingRows = collapseTargetSeries(points);
  const targetSeriesRows = operatingRows.filter((row) => row.业务分类 === "目标盈系列产品");
  const targetPeriodRecords = targetSeriesRows.reduce((sum, row) => sum + Number(row.期次数 || 1), 0);
  const targetMerged = Math.max(0, targetPeriodRecords - targetSeriesRows.length);
  const gfRows = operatingRows.filter(isGf);
  const clientRows = operatingRows.filter(isClientFacing);
  const hiddenChannels = Number(listStats.隐藏渠道数 || 0);

  function count(value) {
    return Number(value || 0).toLocaleString("zh-CN");
  }

  function actionRow(title, desc, href, action = "进入", gate = "") {
    return `<div class="rank-row">
      <div><strong>${B.esc(title)}</strong><span>${B.esc(desc)}</span></div>
      ${gate ? `<em class="logic-chip">${B.esc(gate)}</em>` : ""}
      <a class="link" href="${B.esc(href)}">${B.esc(action)}</a>
    </div>`;
  }

  root.innerHTML = `
    <section class="page-title">
      <div>
        <h1>投顾业务工作台</h1>
        <p class="desc">先按业务任务进入对应分析区，再回到策略列表核验证据；源表、可核验明细和经营样本在这里明确分开。</p>
      </div>
      <div class="title-pills">
        <span class="pill">数据更新至 ${B.esc(overview.数据更新至 || "未披露")}</span>
        <span class="pill">生成时间 ${B.esc(overview.生成时间 || "未披露")}</span>
      </div>
    </section>

    <section class="insight-hero">
      ${B.metric("源表策略总数", sourceTotal, "数据接入规模")}
      ${B.metric("完整可展示策略", evidenceRecords, `默认过滤数据不全、D0和持仓缺失；未进入明细 ${count(hiddenGap)} 条`)}
      ${B.metric("系列归并样本", operatingRows.length, `目标盈 ${count(targetPeriodRecords)} 期压缩为 ${count(targetSeriesRows.length)} 系列`)}
      ${B.metric("广发可展示样本", gfRows.length, `覆盖 ${operatingRows.length ? (gfRows.length / operatingRows.length * 100).toFixed(2) : "0.00"}%`)}
      ${B.metric("对客可展示样本", clientRows.length, "用于销售/营销优先判断")}
    </section>

    <section class="panel">
      <div class="panel-head"><div><h2>负责人使用路径</h2><p class="desc">把页面入口按业务动作排列，不从底层数据表开始看。</p></div></div>
      <div class="rank-list">
        ${actionRow("经营驾驶舱", "先看货架机会、可包装营销、能力复盘和广发基金机会。", "./insights.html?tab=cockpit#manager-focus", "看结论", "先看门禁")}
        ${actionRow("市场竞争力", "按研报产品类型看广发在哪些同类池有销售话术、哪里货架薄。", "./insights.html?tab=market&clientScope=client#market-competition", "看市场", "同类可比")}
        ${actionRow("广发基金外部验证", "只看非广发策略仓位中广发基金被持有和增配情况，剔除自家配置干扰。", "./insights.html?tab=holding&strategyScope=nonGf#gf-fund-opportunity", "看机会", "外部验证")}
        ${actionRow("月度调仓复盘", "默认进入最近完整调仓月份，按研报产品类型分池看调仓方向。", "./insights.html?tab=rebalance#rebalance-overview", "看调仓", "研判信号")}
        ${actionRow("策略证据页", "按业务分类、研报产品类型、风险等级和机构直接核验完整可展示策略。", "./strategies.html?clientScope=client&pageSize=50", "查策略", "逐条核验")}
      </div>
    </section>

    <section class="panel">
      <div class="panel-head"><div><h2>当前展示口径</h2><p class="desc">页面默认只展示完整可比策略，数据不全、D0和持仓缺失样本不进入列表、图表和调仓分析。</p></div></div>
      <div class="rank-list">
        <div class="rank-row"><div><strong>源表不等于展示样本</strong><span>源表 ${count(sourceTotal)} 条；完整可展示策略 ${count(evidenceRecords)} 条；${count(hiddenGap)} 条来自 ${count(hiddenChannels)} 个暂不展示渠道或非当前展示口径。</span></div><span class="rank-value">全局口径</span></div>
        <div class="rank-row"><div><strong>系列样本不等于策略记录</strong><span>目标盈按系列归并，${count(targetPeriodRecords)} 条目标盈期次压缩为 ${count(targetSeriesRows.length)} 个系列，合并掉 ${count(targetMerged)} 条重复期次，系列归并后样本为 ${count(operatingRows.length)} 个。</span></div><a class="link" href="./insights.html?tab=market">看市场总览</a></div>
      </div>
    </section>

    <details class="panel fold-block">
      <summary>展开数据审计表</summary>
      <div class="row-detail-body">
        <section>
          <div class="panel-head"><div><h2>渠道覆盖</h2><p class="desc">仅作为数据审计使用。经营结论不要直接按渠道混合比较，需回到数据洞察按同类策略分池。</p></div></div>
          ${B.table(["渠道", "渠道类型", "策略数", "完整策略数", "官方业绩覆盖", "历史调仓覆盖", "当前持仓覆盖", "回放覆盖", "最新业绩日", "最新调仓日"], summary.channelStats || [], (row, h) => h.endsWith("覆盖") ? B.pct(row[h]) : B.fmt(row[h]))}
        </section>
        <section class="two-col">
          <section>
            <div class="panel-head"><h2>核心表记录数</h2></div>
            ${B.table(["表名", "记录数"], summary.tableCounts || [])}
          </section>
          <section>
            <div class="panel-head"><h2>字段口径字典</h2></div>
            <p class="desc">实际使用时点击字段名旁的问号查看具体计算口径；这里仅做字典索引。</p>
            ${B.table(["表名", "记录数"], Object.keys(summary.fieldDictionary || {}).map((name, index) => ({ 表名: name, 记录数: index + 1 })))}
          </section>
        </section>
      </div>
    </details>
  `;
})();
