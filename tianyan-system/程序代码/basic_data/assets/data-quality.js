(() => {
  const B = window.BasicData || window.BasicDataCommon;
  const root = document.getElementById("dataQualityPage");
  const pack = window.__BASIC_DATA_QUALITY_PACK__ || {};
  const dictionary = window.__BASIC_STANDARD_ENTITY_DICTIONARY__ || {};
  const summary = window.__BASIC_DATA__?.summary || {};

  if (!root || !B) return;

  const esc = (value) => B.esc ? B.esc(value) : String(value ?? "").replace(/[&<>"']/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]));
  const checks = Array.isArray(pack.checks) ? pack.checks : [];
  const metrics = pack.metrics || {};
  const files = pack.files || {};
  const gaps = pack.importantGaps || {};
  const explanations = pack.口径说明 || {};
  const dataPackManifest = window.__BASIC_DATA_PACK_MANIFEST__ || pack.dataPackManifest || {};
  const fieldDictionary = summary.fieldDictionary || {};
  const entities = Array.isArray(dictionary.实体列表) ? dictionary.实体列表 : Array.isArray(dictionary.entities) ? dictionary.entities : [];
  const typeSummary = Array.isArray(dictionary.按类型汇总) ? dictionary.按类型汇总 : Array.isArray(dictionary.typeSummary) ? dictionary.typeSummary : [];

  function value(row, ...keys) {
    for (const key of keys) {
      if (row && row[key] !== undefined && row[key] !== null && row[key] !== "") return row[key];
    }
    return "";
  }

  function text(value, fallback = "未披露") {
    if (value === null || value === undefined || value === "") return `<span class="value-muted">${fallback}</span>`;
    return esc(value);
  }

  function num(value) {
    const number = Number(value);
    return Number.isFinite(number) ? number.toLocaleString("zh-CN") : "未披露";
  }

  function pct(value) {
    const number = Number(value);
    return Number.isFinite(number) ? `${number.toFixed(2)}%` : "未披露";
  }

  function bytes(value) {
    const number = Number(value || 0);
    if (!Number.isFinite(number)) return "未披露";
    if (number >= 1024 * 1024) return `${(number / 1024 / 1024).toFixed(2)} MB`;
    if (number >= 1024) return `${(number / 1024).toFixed(2)} KB`;
    return `${number.toLocaleString("zh-CN")} bytes`;
  }

  function statusLabel(status) {
    const value = String(status || "unknown").toLowerCase();
    if (value === "ok") return "通过";
    if (value === "warn") return "提示";
    if (value === "fail") return "失败";
    return status || "未知";
  }

  function statusBadge(status) {
    const value = String(status || "unknown").toLowerCase();
    const cls = value === "ok" ? "status-ok" : value === "warn" ? "status-warn" : "status-bad";
    return `<span class="status-badge ${cls}">${esc(statusLabel(status))}</span>`;
  }

  function card(label, value, sub, status) {
    return `<div class="quality-card">
      <h3>${esc(label)}</h3>
      <div class="metric-value">${status ? statusBadge(status) : esc(value)}</div>
      ${status ? `<p>${esc(value)}</p>` : ""}
      ${sub ? `<p>${esc(sub)}</p>` : ""}
    </div>`;
  }

  function table(headers, rows, formatter) {
    const safeRows = Array.isArray(rows) ? rows : [];
    const head = headers.map((header) => `<th>${esc(header)}</th>`).join("");
    const body = safeRows.length
      ? safeRows.map((row) => `<tr>${headers.map((header) => `<td>${formatter ? formatter(row, header) : text(row?.[header])}</td>`).join("")}</tr>`).join("")
      : `<tr><td colspan="${headers.length}"><div class="empty">暂无数据</div></td></tr>`;
    return `<div class="table-wrap"><table><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table></div>`;
  }

  function findCheck(name) {
    return checks.find((item) => String(value(item, "项目", "name")).includes(name)) || {};
  }

  function renderCheckTable() {
    return table(["项目", "状态", "当前值", "门槛", "影响页面", "修复责任脚本", "说明"], checks, (row, header) => {
      if (header === "状态") return statusBadge(value(row, "状态", "status"));
      if (header === "说明") return `<span class="small">${text(value(row, "说明", "reason"))}</span>`;
      if (header === "影响页面" || header === "修复责任脚本") return `<span class="small">${text(value(row, header))}</span>`;
      return text(value(row, header, header === "当前值" ? "value" : header));
    });
  }

  function renderFiles() {
    const rows = Object.entries(files).map(([name, info]) => ({
      数据包: name,
      状态: info?.exists && Number(info?.bytes || 0) > 0 ? "ok" : "fail",
      大小: info?.bytes || 0,
      更新时间: info?.mtime || "",
      路径: String(info?.path || "").replace(/^.*?basic_data[\\/]/, "basic_data/"),
    }));
    return table(["数据包", "状态", "大小", "更新时间", "路径"], rows, (row, header) => {
      if (header === "状态") return statusBadge(row.状态);
      if (header === "大小") return bytes(row.大小);
      if (header === "路径") return `<span class="small">${text(row.路径)}</span>`;
      return text(row[header]);
    });
  }

  function renderChecksByStatus(status) {
    const rows = checks.filter((item) => String(value(item, "状态", "status")).toLowerCase() === status);
    return table(["项目", "当前值", "门槛", "说明"], rows, (row, header) => {
      if (header === "说明") return `<span class="small">${text(value(row, "说明"))}</span>`;
      return text(value(row, header));
    });
  }

  function renderMetricDictionary() {
    const rows = [
      {
        指标: "股票行业未识别率",
        当前值: `${pct(metrics.stockIndustry?.unmappedRate)}；未识别 ${num(metrics.stockIndustry?.unmapped_rows)} / ${num(metrics.stockIndustry?.total_rows)} 行`,
        计算口径: explanations.股票行业未识别率 || fieldDictionary.股票行业未识别率 || "",
        关联因子: "基金季度股票持仓.行业一级、股票行业映射补充表",
      },
      {
        指标: "基金资产暴露覆盖",
        当前值: `${pct(metrics.fundClassification?.assetExposureRate)}；覆盖 ${num(metrics.fundClassification?.asset_exposure_funds)} / ${num(metrics.fundClassification?.funds)} 只`,
        计算口径: explanations.基金资产暴露覆盖 || fieldDictionary.基金资产暴露覆盖 || "",
        关联因子: "基金经济暴露快照.经济资产暴露JSON、基金分类快照.资产暴露JSON、基金季度资产配置",
      },
      {
        指标: "基金行业暴露覆盖",
        当前值: `${pct(metrics.fundClassification?.industryExposureRate)}；覆盖 ${num(metrics.fundClassification?.industry_exposure_funds)} / ${num(metrics.fundClassification?.industry_applicable_funds)} 只应穿透基金`,
        计算口径: explanations.基金行业暴露覆盖 || fieldDictionary.基金行业暴露覆盖 || "",
        关联因子: "基金经济暴露快照.经济行业暴露JSON、基金季度股票持仓.行业一级、基金主题/指数规则",
      },
      {
        指标: "当前持仓基金行业覆盖",
        当前值: `${pct(metrics.fundClassification?.heldIndustryExposureRate)}；覆盖 ${num(metrics.fundClassification?.held_industry_exposure_funds)} / ${num(metrics.fundClassification?.held_industry_applicable_funds)} 只当前持仓基金`,
        计算口径: explanations.当前持仓基金行业覆盖 || "",
        关联因子: "策略当前持仓.基金权重_百分比、基金经济暴露快照.经济行业暴露JSON",
      },
      {
        指标: "策略治理标签",
        当前值: `${num(metrics.strategyGovernance?.governanceRows)} 条；常规排名 ${num(metrics.strategyGovernance?.regularRankStrategies)} 条`,
        计算口径: explanations.策略治理标签 || "",
        关联因子: "策略信息、策略当前持仓、策略调仓事件、策略日度业绩",
      },
      {
        指标: "调仓事件重复业务键",
        当前值: `${num(metrics.duplicateRebalance?.duplicateGroups)} 组重复；${num(metrics.duplicateRebalance?.duplicateEvents)} 条冗余`,
        计算口径: explanations.调仓事件重复业务键 || "",
        关联因子: "策略调仓事件.渠道ID/统一策略ID/调仓日期/本次仓位日期/调仓标题/调仓原因",
      },
      {
        指标: "重要策略元数据缺失",
        当前值: `${pct(metrics.strategyMetadata?.missingAnyImportantRate)}；样本 ${num(metrics.strategyMetadata?.regularStrategies)} 只策略`,
        计算口径: explanations.重要策略元数据缺失 || "",
        关联因子: "策略信息.成立日期/风险等级/投顾费率/业绩基准",
      },
    ];
    return table(["指标", "当前值", "计算口径", "关联因子"], rows, (row, header) => {
      if (header === "计算口径" || header === "关联因子") return `<span class="small">${text(row[header])}</span>`;
      return text(row[header]);
    });
  }

  function renderGapSection(title, desc, headers, rows, formatter) {
    return `<section class="panel">
      <div class="panel-head"><div><h2>${esc(title)}</h2><p class="muted">${esc(desc)}</p></div><span class="pill">${num((rows || []).length)} 条</span></div>
      ${table(headers, rows || [], formatter)}
    </section>`;
  }

  function renderUnmappedStocks() {
    return renderGapSection(
      "股票行业映射缺口",
      "优先补充被多只基金持有或合计权重较高的股票行业映射。",
      ["股票代码", "股票名称", "基金数", "持仓行数", "合计权重"],
      pack.unmappedStocks || [],
      (row, header) => (header === "合计权重" ? pct(row[header]) : text(row[header]))
    );
  }

  function renderEntityTypeSummary() {
    if (!typeSummary.length) return "";
    const headers = Object.keys(typeSummary[0] || {}).slice(0, 6);
    return `<section class="panel">
      <div class="panel-head"><div><h2>标准实体字典摘要</h2><p class="muted">AI 选策略、主题分析、基金详情和策略详情共用同一实体字典。</p></div></div>
      ${table(headers, typeSummary)}
    </section>`;
  }

  function renderEntityRows(keyword = "") {
    const query = keyword.trim().toLowerCase();
    const rows = entities
      .filter((row) => {
        if (!query) return true;
        return Object.values(row || {}).some((raw) => {
          const text = typeof raw === "object" ? JSON.stringify(raw) : String(raw || "");
          return text.toLowerCase().includes(query);
        });
      })
      .slice(0, 200);
    const headers = ["实体名称", "实体类型", "命中基金数", "命中策略数", "口径说明"];
    return table(headers, rows, (row, header) => {
      const aliases = {
        实体名称: ["实体名称", "label", "name", "key"],
        实体类型: ["实体类型", "type"],
        命中基金数: ["命中基金数", "fundCount"],
        命中策略数: ["命中策略数", "strategyCount"],
        口径说明: ["口径说明", "note"],
      }[header] || [header];
      const raw = value(row, ...aliases);
      if (header === "口径说明") return `<span class="small">${text(raw)}</span>`;
      return text(raw);
    });
  }

  function renderEntities() {
    if (!entities.length) return "";
    return `<section class="panel entity-panel">
      <div class="panel-head">
        <div><h2>标准实体字典明细</h2><p class="muted">可按实体名称、类型、证据或说明检索。</p></div>
        <input id="entitySearch" class="control compact-control" type="search" placeholder="筛选实体、类型或证据">
      </div>
      <div id="entityTable">${renderEntityRows()}</div>
    </section>`;
  }

  function renderPackageSummary() {
    if (!dataPackManifest || !Array.isArray(dataPackManifest.files)) return "";
    const maxFile = dataPackManifest.maxFile || {};
    return `<section class="panel">
      <div class="panel-head"><div><h2>内网包体积清单</h2><p class="muted">用于判断静态目录迁移后的加载压力和按需拆包效果。</p></div></div>
      <div class="quality-grid">
        ${card("数据包总量", bytes(dataPackManifest.totalBytes || 0), `${num(dataPackManifest.totalFiles)} 个 js/json 文件`)}
        ${card("最大单包", bytes(maxFile.bytes || 0), maxFile.path || "未披露")}
        ${card("首屏依赖估算", bytes(dataPackManifest.firstScreenBytes || 0), "首页/策略列表核心包")}
      </div>
    </section>`;
  }

  const failRows = checks.filter((item) => String(value(item, "状态", "status")).toLowerCase() === "fail");
  const warnRows = checks.filter((item) => String(value(item, "状态", "status")).toLowerCase() === "warn");
  const stockCheck = findCheck("股票行业未识别率");
  const assetCheck = findCheck("基金资产暴露覆盖");
  const industryCheck = findCheck("基金行业暴露覆盖");
  const heldIndustryCheck = findCheck("当前持仓基金行业覆盖");

  root.innerHTML = `
    <section class="panel">
      <div class="panel-head">
        <div>
          <h1>数据质量</h1>
          <p class="muted">生成时间：${text(pack.generatedAt)}；精细化验收当前只严格覆盖天天基金和广发基金渠道。</p>
        </div>
        ${statusBadge(pack.status)}
      </div>
      <div class="quality-grid">
        ${card("总体状态", pack.status === "ok" ? "全部通过" : pack.status === "warn" ? "存在提示项" : "存在失败项", `失败 ${failRows.length} 项；提示 ${warnRows.length} 项`, pack.status)}
        ${card("股票行业识别率", pct(metrics.stockIndustry?.mappedRate), `未识别 ${num(metrics.stockIndustry?.unmapped_rows)} / ${num(metrics.stockIndustry?.total_rows)} 行；${value(stockCheck, "门槛") || ""}`)}
        ${card("基金资产暴露覆盖", pct(metrics.fundClassification?.assetExposureRate), `覆盖 ${num(metrics.fundClassification?.asset_exposure_funds)} / ${num(metrics.fundClassification?.funds)} 只；${value(assetCheck, "门槛") || ""}`)}
        ${card("基金行业暴露覆盖", pct(metrics.fundClassification?.industryExposureRate), `覆盖 ${num(metrics.fundClassification?.industry_exposure_funds)} / ${num(metrics.fundClassification?.industry_applicable_funds)} 只；${value(industryCheck, "门槛") || ""}`)}
        ${card("当前持仓行业覆盖", pct(metrics.fundClassification?.heldIndustryExposureRate), `覆盖 ${num(metrics.fundClassification?.held_industry_exposure_funds)} / ${num(metrics.fundClassification?.held_industry_applicable_funds)} 只；${value(heldIndustryCheck, "门槛") || ""}`)}
      </div>
    </section>

    <section class="panel">
      <div class="panel-head"><div><h2>异常提示</h2><p class="muted">失败项会阻断带验收门槛的更新；提示项不阻断，但会影响分析可信度或加载体验。</p></div></div>
      ${failRows.length ? `<h3>失败项</h3>${renderChecksByStatus("fail")}` : '<div class="empty">当前没有失败项。</div>'}
      ${warnRows.length ? `<h3 style="margin-top:18px">提示项</h3>${renderChecksByStatus("warn")}` : ""}
    </section>

    <section class="two-col">
      <div class="panel">
        <div class="panel-head"><div><h2>数据新鲜度</h2><p class="muted">策略持仓、调仓和基金季报穿透的最新可用日期。</p></div></div>
        <div class="quality-grid">
          ${card("最新持仓日", metrics.strategyHolding?.latest_holding_date || "未披露", `策略 ${num(metrics.strategyHolding?.strategies)} 只，持仓行 ${num(metrics.strategyHolding?.holding_rows)}`)}
          ${card("最新调仓日", metrics.rebalance?.latest_rebalance_date || "未披露", `调仓明细 ${num(metrics.rebalance?.rows)} 行，策略 ${num(metrics.rebalance?.strategies)} 只`)}
          ${card("最新经济暴露报告期", metrics.fundClassification?.latest_report || "未披露", `基金经济暴露快照 ${num(metrics.fundClassification?.funds)} 只`)}
        </div>
      </div>
      <div class="panel">
        <div class="panel-head"><div><h2>指标口径</h2><p class="muted">核心数据质量指标、计算公式和关联因子。</p></div></div>
        ${renderMetricDictionary()}
      </div>
    </section>

    <section class="panel">
      <div class="panel-head"><div><h2>验收项</h2><p class="muted">用于增量更新结束后的自动门槛判断。</p></div></div>
      ${renderCheckTable()}
    </section>

    ${renderGapSection("应穿透基金缺经济行业暴露", "当前持仓权重 >0.5% 且应做行业/主题穿透的基金，如经济行业暴露缺失，会直接影响仓位分析、主题分析和AI实体判断。黄金/商品、纯债、货币、海外债券不计入此缺口。", ["基金代码", "基金名称", "基金类型", "二级分类", "持仓策略数", "全市场当前持仓权重", "缺口说明"], gaps.industryExposureMissingHeldFunds || [], (row, header) => header === "全市场当前持仓权重" ? pct(row[header]) : text(row[header]))}
    ${renderGapSection("股票资产基金缺股票持仓", "最新季报股票资产占比 >0.5% 但没有同报告期股票持仓明细的基金，需要优先补采季报重仓股。", ["基金代码", "基金名称", "报告期", "股票占比"], gaps.positiveStockMissingFunds || [], (row, header) => header === "股票占比" ? pct(row[header]) : text(row[header]))}
    ${renderGapSection("重要策略元数据缺失", "成立日期、风险等级、投顾费率、业绩基准缺失会影响策略筛选、策略对比和风险收益解释。", ["统一策略ID", "渠道ID", "策略名称", "投顾机构", "缺失字段"], gaps.strategyMetadataMissing || [])}
    ${renderGapSection("特殊策略治理样本", "测试组合、信号类策略、已停止目标盈/期次策略会进入单独分析，不进入常规排名。", ["统一策略ID", "渠道ID", "策略名称", "投顾机构", "治理状态", "分析分组", "是否纳入常规排名", "业绩分析截止日期", "规则说明"], gaps.specialStrategies || [], (row, header) => header === "规则说明" ? `<span class="small">${text(row[header])}</span>` : text(row[header]))}
    ${renderGapSection("当前权重未完整披露策略", "这类策略仍可纳入常规排名，但当前仓位使用最近调仓后权重按基金净值滚动到最新净值日，并在口径中标注。", ["统一策略ID", "渠道ID", "策略名称", "投顾机构", "原始持仓权重合计", "原始持仓行数", "最近调仓日期", "持仓处理方式"], gaps.currentWeightIncompleteStrategies || [], (row, header) => header === "原始持仓权重合计" ? pct(row[header]) : text(row[header]))}
    ${renderUnmappedStocks()}
    ${renderEntityTypeSummary()}
    ${renderEntities()}

    <section class="panel">
      <div class="panel-head"><div><h2>页面数据包</h2><p class="muted">用于检查外部同步目录是否包含完整页面依赖，并发现过大或过期数据包。</p></div></div>
      ${renderFiles()}
    </section>
    ${renderPackageSummary()}
  `;

  const input = document.getElementById("entitySearch");
  const tableHost = document.getElementById("entityTable");
  input?.addEventListener("input", () => {
    tableHost.innerHTML = renderEntityRows(input.value || "");
  });
})();
