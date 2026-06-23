(() => {
  const B = window.BasicData;
  const summary = B.state.summary;
  const root = B.byId("overviewPage");
  const overview = summary.overview || {};
  const insight = summary.insightData || {};
  const points = Array.isArray(insight.策略表现点) && insight.策略表现点.length ? insight.策略表现点 : (summary.strategies || []);
  const listStats = summary.strategyListStats || {};
  const benchmarkDisclosure = summary.benchmarkDisclosure || {};
  const benchmarkOverview = benchmarkDisclosure.总览 || {};
  const benchmarkByType = benchmarkDisclosure.按研报产品类型 || [];
  const benchmarkByInstitution = benchmarkDisclosure.按机构 || [];
  const fieldAudit = summary.fieldMissingnessAudit || {};
  const fieldAuditRows = fieldAudit.字段缺失审计 || [];

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

  function percent(value) {
    const number = Number(value);
    return Number.isFinite(number) ? `${number.toFixed(2)}%` : "未披露";
  }

  function benchmarkFormatter(row, field) {
    if (field === "披露覆盖率") return B.pct(row[field]);
    return B.fmt(row[field]);
  }

  function actionRow(title, desc, href, action = "进入", gate = "") {
    return `<div class="rank-row">
      <div><strong>${B.esc(title)}</strong><span>${B.esc(desc)}</span></div>
      ${gate ? `<em class="logic-chip">${B.esc(gate)}</em>` : ""}
      <a class="link" href="${B.esc(href)}">${B.esc(action)}</a>
    </div>`;
  }

  function present(value) {
    const text = raw(value).trim();
    return text && !["未披露", "未分类", "未知", "--", "null", "undefined", "NaN"].includes(text);
  }

  function parseNumber(value) {
    if (value === null || value === undefined || value === "") return null;
    if (typeof value === "number") return Number.isFinite(value) ? value : null;
    const cleaned = raw(value).replace(/,/g, "").replace(/%/g, "").trim();
    if (!cleaned) return null;
    const number = Number(cleaned);
    return Number.isFinite(number) ? number : null;
  }

  function median(values) {
    const nums = values.filter((value) => Number.isFinite(value)).sort((a, b) => a - b);
    if (!nums.length) return null;
    const mid = Math.floor(nums.length / 2);
    return nums.length % 2 ? nums[mid] : (nums[mid - 1] + nums[mid]) / 2;
  }

  function pctRatio(numerator, denominator) {
    return denominator ? numerator / denominator * 100 : null;
  }

  const metricSources = [
    { id: "strategyPoint", label: "策略表现点", rows: points, grain: "策略" },
    { id: "strategyList", label: "策略列表", rows: masterStrategies, grain: "策略" },
    { id: "channelStats", label: "渠道统计", rows: summary.channelStats || [], grain: "渠道" },
    { id: "benchmarkType", label: "基准说明-产品类型", rows: benchmarkByType, grain: "产品类型" },
    { id: "benchmarkInstitution", label: "基准说明-机构", rows: benchmarkByInstitution, grain: "机构" },
    { id: "overview", label: "业务总览", rows: [overview], grain: "全局" }
  ];

  function buildMetricCatalog() {
    const dictNames = Object.keys(summary.fieldDictionary || {});
    const sourceFields = new Map();
    metricSources.forEach((source) => {
      (source.rows || []).slice(0, 2000).forEach((row) => {
        Object.keys(row || {}).forEach((field) => {
          if (!sourceFields.has(field)) sourceFields.set(field, []);
          if (!sourceFields.get(field).some((item) => item.id === source.id)) sourceFields.get(field).push(source);
        });
      });
    });
    dictNames.forEach((field) => {
      if (!sourceFields.has(field)) sourceFields.set(field, []);
    });
    return [...sourceFields.entries()]
      .filter(([field]) => !field.startsWith("_") && field !== "searchText" && field !== "detailFile")
      .map(([field, sources]) => ({
        name: field,
        sources,
        inDictionary: Boolean(summary.fieldDictionary?.[field]),
        derived: B.isDerivedField(field),
        description: summary.fieldDictionary?.[field] || ""
      }))
      .sort((a, b) => Number(b.inDictionary) - Number(a.inDictionary) || a.name.localeCompare(b.name, "zh-CN"));
  }

  const metricCatalog = buildMetricCatalog();
  const metricNameSet = new Set(metricCatalog.map((item) => item.name));
  const metricByName = new Map(metricCatalog.map((item) => [item.name, item]));
  let selectedMetricName = metricByName.has("近1年") ? "近1年" : (metricCatalog[0]?.name || "");
  let isComposingMetricSearch = false;

  function scoreMetric(metric, query) {
    if (!query) return metric.inDictionary ? 2 : 1;
    const haystack = `${metric.name} ${metric.description} ${metric.sources.map((item) => item.label).join(" ")}`.toLowerCase();
    const needle = query.toLowerCase().trim();
    if (!needle) return metric.inDictionary ? 2 : 1;
    if (metric.name === query) return 100;
    if (metric.name.includes(query)) return 80 - metric.name.length / 100;
    if (haystack.includes(needle)) return 50 - metric.name.length / 100;
    let pos = 0;
    for (const char of needle) {
      pos = haystack.indexOf(char, pos);
      if (pos < 0) return 0;
      pos += 1;
    }
    return 20 - metric.name.length / 100;
  }

  function metricMatches(query) {
    return metricCatalog
      .map((metric) => ({ metric, score: scoreMetric(metric, query) }))
      .filter((item) => item.score > 0)
      .sort((a, b) => b.score - a.score)
      .slice(0, 18)
      .map((item) => item.metric);
  }

  function bestSourceForMetric(metric) {
    const sourcePriority = ["strategyPoint", "strategyList", "channelStats", "benchmarkType", "benchmarkInstitution", "overview"];
    return sourcePriority
      .map((id) => metric.sources.find((source) => source.id === id))
      .find(Boolean) || metric.sources[0] || metricSources.find((source) => source.id === "strategyPoint");
  }

  function groupCoverage(rows, field, groupField) {
    const groups = new Map();
    (rows || []).forEach((row) => {
      const key = raw(row[groupField]) || "未披露";
      if (!groups.has(key)) groups.set(key, { 分组: key, 样本数: 0, 有效样本: 0, 覆盖率: null });
      const item = groups.get(key);
      item.样本数 += 1;
      if (present(row[field])) item.有效样本 += 1;
    });
    return [...groups.values()]
      .map((row) => ({ ...row, 覆盖率: pctRatio(row.有效样本, row.样本数) }))
      .sort((a, b) => b.样本数 - a.样本数 || b.有效样本 - a.有效样本)
      .slice(0, 20);
  }

  function valueDistribution(rows, field) {
    const values = (rows || []).map((row) => row[field]).filter(present);
    const numericValues = values.map(parseNumber).filter((value) => value !== null);
    const numericShare = values.length ? numericValues.length / values.length : 0;
    if (values.length && numericShare >= 0.7) {
      return {
        type: "numeric",
        rows: [
          { 指标: "有效数值", 值: numericValues.length },
          { 指标: "最小值", 值: Math.min(...numericValues) },
          { 指标: "中位数", 值: median(numericValues) },
          { 指标: "最大值", 值: Math.max(...numericValues) },
          { 指标: "正值数量", 值: numericValues.filter((value) => value > 0).length },
          { 指标: "负值数量", 值: numericValues.filter((value) => value < 0).length }
        ]
      };
    }
    const counts = new Map();
    values.forEach((value) => {
      const key = raw(value);
      counts.set(key, (counts.get(key) || 0) + 1);
    });
    return {
      type: "category",
      rows: [...counts.entries()]
        .map(([字典值, 样本数]) => ({ 字典值, 样本数, 占比: pctRatio(样本数, values.length) }))
        .sort((a, b) => b.样本数 - a.样本数 || a.字典值.localeCompare(b.字典值, "zh-CN"))
        .slice(0, 30)
    };
  }

  function formulaForMetric(field, description) {
    const text = `${field} ${description}`;
    if (/覆盖率|占比/.test(text)) return `${field} = 命中样本数 ÷ 当前统计口径总样本数 × 100%。`;
    if (/净增配|权重变化/.test(text)) return `${field} = 调后权重 - 调前权重；跨基金或跨策略展示时先按当前筛选口径聚合。`;
    if (/收益|涨幅/.test(text)) return `${field} = (期末净值 ÷ 期初净值 - 1) × 100%；期初期末取观察窗口附近最近可用净值。`;
    if (/回撤/.test(text)) return `${field} = 当前净值 ÷ 历史高点 - 1；最大回撤取区间内最深跌幅。`;
    if (/波动/.test(text)) return `${field} = 日收益率标准差 × sqrt(252)。`;
    if (/夏普/.test(text)) return `${field} = 年化收益率 ÷ 年化波动率；当前无风险收益率按0处理。`;
    if (/数量|数$|事件数|样本数/.test(text)) return `${field} = 当前筛选口径下去重计数；策略按统一策略ID，基金按基金代码，事件按调仓事件ID。`;
    return description ? "详见下方业务含义；该字段按当前页面的业务口径展示。" : "当前字段暂无专门说明，请结合页面标题和当前筛选条件理解。";
  }

  function factorsForMetric(field, description) {
    const text = `${field} ${description} ${B.fieldSourceText(field)}`;
    const factors = [...metricNameSet].filter((name) => name !== field && text.includes(name)).slice(0, 12);
    const add = (name) => { if (metricNameSet.has(name) && !factors.includes(name) && name !== field) factors.push(name); };
    if (/收益|回撤|波动|夏普|净值/.test(text)) ["交易日期", "单位净值", "披露单位净值", "策略收益", "基金净值"].forEach(add);
    if (/调仓|权重变化|净增配|换手/.test(text)) ["调仓日期", "调前权重", "调后权重", "权重变化", "调仓事件ID"].forEach(add);
    if (/持仓|资产|行业|基金/.test(text)) ["基金代码", "基金名称", "基金类型", "资产暴露", "行业暴露", "研报大类资产", "研报A股行业"].forEach(add);
    return factors.slice(0, 14);
  }

  function factorLinks(factors) {
    if (!factors.length) return '<span class="muted-text">未识别到可链接标准因子；请补充 fieldDictionary 后会自动出现。</span>';
    return factors.map((name) => `<button class="logic-chip metric-factor-link" type="button" data-metric-select="${B.esc(name)}">${B.esc(name)}</button>`).join("");
  }

  const importantMetricRules = [
    { level: "核心", score: 4, pattern: /策略名称|策略ID|投顾机构|渠道|基金代码|基金名称|持仓|权重|调仓日期|调仓事件|调仓原因|调前|调后/, reason: "直接决定策略、基金、持仓或调仓事件是否能被追溯。" },
    { level: "核心", score: 4, pattern: /收益|回撤|波动|夏普|净值|业绩|基准|近1月|近3月|近6月|近1年|成立以来/, reason: "直接进入收益风险、业绩归因和对比结论。" },
    { level: "重要", score: 3, pattern: /资产|行业|二级分类|基金类型|权益主题|分类来源|分类口径|风险等级|费率/, reason: "影响资产配置、行业配置、产品结构和可销售性判断。" },
    { level: "重要", score: 3, pattern: /对客|展示|最新|更新时间|披露|成立|规模|机构|公司/, reason: "影响样本准入、渠道覆盖和经营口径解释。" }
  ];

  function metricImportance(field, description = "", source = null) {
    const text = `${field} ${description} ${source?.label || ""} ${source?.grain || ""}`;
    const hit = importantMetricRules.find((rule) => rule.pattern.test(text));
    if (hit) return hit;
    return { level: "一般", score: 1, pattern: null, reason: "当前暂未识别为核心经营或投研指标，主要用于补充查看。" };
  }

  function metricCoverageStats(metric) {
    const source = bestSourceForMetric(metric);
    const rows = source?.rows || [];
    const filled = rows.filter((row) => present(row[metric.name]));
    return {
      metric,
      source,
      rows,
      filled,
      coverage: pctRatio(filled.length, rows.length) || 0,
      missing: Math.max(0, rows.length - filled.length),
      importance: metricImportance(metric.name, metric.description, source)
    };
  }

  function weakCoverageText(rows, label) {
    const weak = rows
      .filter((row) => Number(row.样本数 || 0) >= 5 && Number(row.覆盖率 || 0) < 60)
      .slice(0, 5)
      .map((row) => `${row.分组} ${Number(row.覆盖率 || 0).toFixed(1)}%`);
    return weak.length ? `${label}覆盖薄弱：${weak.join("、")}。` : "";
  }

  function numericValuesFor(rows, field) {
    return (rows || []).map((row) => parseNumber(row[field])).filter((value) => value !== null);
  }

  function metricAnomalyFindings(field, rows, filled, coverage, channelRows, institutionRows, dist, importance) {
    const findings = [];
    if (!rows.length) {
      findings.push({ level: "高", text: "当前指标在可用数据源中没有样本，不能进入分析结论。" });
      return findings;
    }
    if (importance.score >= 3 && coverage < 85) {
      findings.push({ level: coverage < 60 ? "高" : "中", text: `${importance.level}指标覆盖率仅 ${coverage.toFixed(2)}%，缺失 ${count(rows.length - filled.length)} 条；${importance.reason}` });
    } else if (coverage < 50) {
      findings.push({ level: "中", text: `覆盖率仅 ${coverage.toFixed(2)}%，建议先排查采集或加工链路，再用于横向比较。` });
    }
    [weakCoverageText(channelRows, "渠道"), weakCoverageText(institutionRows, "机构")].filter(Boolean).forEach((text) => findings.push({ level: "中", text }));

    const missingTextShare = rows.length ? (rows.length - filled.length) / rows.length * 100 : 0;
    if (missingTextShare >= 30 && /分类|资产|行业|费率|风险等级|基准|调仓原因/.test(field)) {
      findings.push({ level: "中", text: `该字段属于解释性字段，缺失率 ${missingTextShare.toFixed(2)}%；页面结论需要同步说明未披露样本范围。` });
    }

    if (dist.type === "category" && filled.length) {
      const top = dist.rows[0];
      const topShare = Number(top?.占比 || 0);
      if (topShare >= 90 && dist.rows.length > 1) {
        findings.push({ level: "低", text: `字典值高度集中，最大值“${top.字典值}”占 ${topShare.toFixed(2)}%；请确认是否为真实集中或默认值填充。` });
      }
    }

    if (dist.type === "numeric") {
      const nums = numericValuesFor(rows, field);
      if (nums.length >= 20) {
        const zeroShare = nums.filter((value) => value === 0).length / nums.length * 100;
        const uniqueCount = new Set(nums.map((value) => value.toFixed(6))).size;
        if (zeroShare >= 80 && !/现金|占比|权重/.test(field)) {
          findings.push({ level: "低", text: `数值中 0 值占 ${zeroShare.toFixed(2)}%，可能存在默认值或未成功回填。` });
        }
        if (uniqueCount <= 2 && nums.length >= 50) {
          findings.push({ level: "低", text: `有效数值只有 ${uniqueCount} 个不同取值，建议确认该指标是否被离散化或被默认值覆盖。` });
        }
        const sorted = nums.slice().sort((a, b) => a - b);
        const p25 = sorted[Math.floor(sorted.length * 0.25)];
        const p75 = sorted[Math.floor(sorted.length * 0.75)];
        const iqr = p75 - p25;
        if (iqr > 0) {
          const lower = p25 - 3 * iqr;
          const upper = p75 + 3 * iqr;
          const outliers = nums.filter((value) => value < lower || value > upper).length;
          if (outliers >= 3 && outliers / nums.length * 100 >= 2) {
            findings.push({ level: "低", text: `检测到 ${count(outliers)} 个可能离群值；建议优先抽查最大/最小样本的原始来源。` });
          }
        }
      }
    }

    if (!findings.length) findings.push({ level: "低", text: "未发现明显覆盖率或分布异常；仍建议在正式引用前抽查原始样本。" });
    return findings;
  }

  function importantMissingMetrics(currentField) {
    return metricCatalog
      .map(metricCoverageStats)
      .filter((item) => item.metric.name !== currentField && item.importance.score >= 3 && item.rows.length >= 10 && item.coverage < 85)
      .sort((a, b) => b.importance.score - a.importance.score || a.coverage - b.coverage || b.rows.length - a.rows.length)
      .slice(0, 12);
  }

  function renderQualityFindings(findings, importance) {
    const items = findings.map((item) => {
      const cls = item.level === "高" ? "is-high" : item.level === "中" ? "is-mid" : "is-low";
      return `<li class="metric-alert-item ${cls}"><span>${B.esc(item.level)}</span><p>${B.esc(item.text)}</p></li>`;
    }).join("");
    return `<section class="metric-alert-panel">
      <div>
        <h3>异常与缺失提示</h3>
        <p class="desc">重要性：${B.esc(importance.level)}。${B.esc(importance.reason)}</p>
      </div>
      <ul class="metric-alert-list">${items}</ul>
    </section>`;
  }

  function renderImportantMissingMetrics(rows) {
    if (!rows.length) {
      return `<section class="metric-alert-panel metric-alert-panel-muted">
        <h3>重要缺失指标</h3>
        <p class="desc">当前未发现覆盖率低于 85% 的核心/重要指标。</p>
      </section>`;
    }
    const body = rows.map((item) => `<tr>
      <td><button class="metric-table-link" type="button" data-metric-select="${B.esc(item.metric.name)}">${B.esc(item.metric.name)}</button></td>
      <td>${B.esc(item.importance.level)}</td>
      <td>${B.esc(item.source?.label || "未知")}</td>
      <td>${item.coverage.toFixed(2)}%</td>
      <td>${count(item.missing)} / ${count(item.rows.length)}</td>
      <td>${B.esc(item.importance.reason)}</td>
    </tr>`).join("");
    return `<section class="metric-alert-panel">
      <div>
        <h3>重要缺失指标</h3>
        <p class="desc">按当前数据动态扫描核心/重要指标，列出覆盖率低于 85% 的字段；点击指标名可切换查看明细。</p>
      </div>
      <div class="table-wrap">
        <table>
          <thead><tr><th>指标</th><th>重要性</th><th>数据源</th><th>覆盖率</th><th>缺失样本</th><th>说明</th></tr></thead>
          <tbody>${body}</tbody>
        </table>
      </div>
    </section>`;
  }

  function metricQualityLabel(coverage, channelRows, institutionRows) {
    const weakChannels = channelRows.filter((row) => Number(row.覆盖率 || 0) < 70).length;
    const weakInstitutions = institutionRows.filter((row) => Number(row.覆盖率 || 0) < 70).length;
    if (coverage >= 95 && weakChannels <= 1 && weakInstitutions <= 3) return "可用于正式分析";
    if (coverage >= 75) return "可用于辅助分析";
    return "仅用于排查，不建议直接下结论";
  }

  function metricAnalyzerSection() {
    return `
      <section class="panel" id="singleMetricPanel">
        <div class="panel-head">
          <div>
            <h2>单指标数据统计分析</h2>
            <p class="desc">搜索任意指标，查看计算口径、依赖因子、字典值集合、渠道覆盖率、机构覆盖率和异常缺失提示，用于判断该指标是否适合进入分析结论。</p>
          </div>
        </div>
        <div class="metric-search-row">
          <label class="metric-search-box">
            <span>指标搜索</span>
            <input id="metricSearchInput" class="control" type="search" autocomplete="off" placeholder="输入指标名、口径关键词或来源，如 近1年、回撤、调仓、基金分类来源">
          </label>
          <div id="metricSearchResults" class="metric-search-results"></div>
        </div>
        <div id="metricAnalysisOutput"></div>
      </section>`;
  }

  function renderMetricSearchResults(query = "") {
    const host = B.byId("metricSearchResults");
    if (!host) return;
    const rows = metricMatches(query);
    host.innerHTML = rows.map((metric) => {
      const active = metric.name === selectedMetricName ? " is-active" : "";
      const sourceText = metric.sources.map((source) => source.label).slice(0, 2).join(" / ") || "仅字典";
      return `<button class="metric-result${active}" type="button" data-metric-select="${B.esc(metric.name)}">
        <strong>${B.esc(metric.name)}</strong><span>${B.esc(sourceText)}</span>
      </button>`;
    }).join("") || '<div class="empty">没有匹配指标</div>';
  }

  function renderMetricAnalysis() {
    const host = B.byId("metricAnalysisOutput");
    if (!host) return;
    const metric = metricByName.get(selectedMetricName) || metricCatalog[0];
    if (!metric) {
      host.innerHTML = '<div class="empty">暂无可分析指标</div>';
      return;
    }
    const source = bestSourceForMetric(metric);
    const rows = source?.rows || [];
    const field = metric.name;
    const filled = rows.filter((row) => present(row[field]));
    const coverage = pctRatio(filled.length, rows.length) || 0;
    const channelRows = groupCoverage(rows, field, "渠道");
    const institutionRows = groupCoverage(rows, field, "投顾机构");
    const dist = valueDistribution(rows, field);
    const description = summary.fieldDictionary?.[field] || "";
    const factors = factorsForMetric(field, description);
    const sourceText = B.fieldSourceText(field);
    const quality = metricQualityLabel(coverage, channelRows, institutionRows);
    const importance = metricImportance(field, description, source);
    const findings = metricAnomalyFindings(field, rows, filled, coverage, channelRows, institutionRows, dist, importance);
    const importantMissing = importantMissingMetrics(field);
    const sourceBadges = (metric.sources || []).map((item) => `<span class="logic-chip">${B.esc(item.label)}｜${B.esc(item.grain)}</span>`).join("");
    host.innerHTML = `
      <section class="metric-analysis-card">
        <div class="metric-analysis-title">
          <div>
            <h3>${B.esc(field)}</h3>
            <p class="desc">${B.esc(source.label)}，样本粒度：${B.esc(source.grain)}；${metric.derived ? "加工字段" : "采集/披露字段"}</p>
          </div>
          <span class="status-badge ${coverage >= 95 ? "status-ok" : coverage >= 75 ? "status-warn" : "status-bad"}">${B.esc(quality)}</span>
        </div>
        <section class="insight-hero metric-kpis">
          ${B.metric("样本数", rows.length, source.label)}
          ${B.metric("有效值", filled.length, "非空且非未披露")}
          ${B.metric("覆盖率", `${coverage.toFixed(2)}%`, "有效值/样本数")}
          ${B.metric("字典值/数值", dist.type === "numeric" ? "数值型" : dist.rows.length, dist.type === "numeric" ? "展示分布统计" : "展示前30个值")}
        </section>
        ${renderQualityFindings(findings, importance)}
        ${renderImportantMissingMetrics(importantMissing)}
        <section class="metric-lineage">
          <div>
            <strong>业务含义</strong>
            <p>${B.esc(description || "该字段暂无专门字典项。")}</p>
          </div>
          <div>
            <strong>计算口径</strong>
            <p>${B.esc(formulaForMetric(field, description))}</p>
          </div>
          <div>
            <strong>业务用途</strong>
            <p>${B.esc(sourceText)}</p>
          </div>
          <div>
            <strong>相关因子</strong>
            <div class="metric-factor-list">${factorLinks(factors)}</div>
          </div>
          <div>
            <strong>可用数据源</strong>
            <div class="metric-factor-list">${sourceBadges || '<span class="muted-text">仅字段字典，当前数据集中未找到同名列。</span>'}</div>
          </div>
        </section>
        <section class="two-col">
          <section>
            <div class="panel-head"><h2>${dist.type === "numeric" ? "数值分布" : "字典值集合"}</h2></div>
            ${B.table(dist.type === "numeric" ? ["指标", "值"] : ["字典值", "样本数", "占比"], dist.rows, (row, h) => h === "占比" ? B.pct(row[h]) : B.fmt(row[h]))}
          </section>
          <section>
            <div class="panel-head"><h2>渠道覆盖率</h2></div>
            ${channelRows.length ? B.table(["分组", "样本数", "有效样本", "覆盖率"], channelRows, (row, h) => h === "覆盖率" ? B.pct(row[h]) : B.fmt(row[h])) : '<div class="empty">该数据源没有渠道字段</div>'}
          </section>
        </section>
        <section>
          <div class="panel-head"><h2>机构覆盖率</h2></div>
          ${institutionRows.length ? B.table(["分组", "样本数", "有效样本", "覆盖率"], institutionRows, (row, h) => h === "覆盖率" ? B.pct(row[h]) : B.fmt(row[h])) : '<div class="empty">该数据源没有投顾机构字段</div>'}
        </section>
      </section>`;
  }

  function setupMetricAnalyzer() {
    const input = B.byId("metricSearchInput");
    if (!input) return;
    input.value = selectedMetricName;
    renderMetricSearchResults(selectedMetricName);
    renderMetricAnalysis();
    input.addEventListener("compositionstart", () => { isComposingMetricSearch = true; });
    input.addEventListener("compositionend", () => {
      isComposingMetricSearch = false;
      renderMetricSearchResults(input.value);
    });
    input.addEventListener("input", () => {
      if (!isComposingMetricSearch) renderMetricSearchResults(input.value);
    });
    root.addEventListener("click", (event) => {
      const button = event.target.closest("[data-metric-select]");
      if (!button) return;
      selectedMetricName = button.getAttribute("data-metric-select") || selectedMetricName;
      input.value = selectedMetricName;
      renderMetricSearchResults(selectedMetricName);
      renderMetricAnalysis();
    });
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

    ${metricAnalyzerSection()}

    <section class="panel">
      <div class="panel-head"><div><h2>天天基金业绩基准说明披露</h2><p class="desc">${B.esc(benchmarkDisclosure.统计口径 || "天天基金/投顾渠道全部策略；按详情页业绩基准说明文本统计。")}</p></div></div>
      <section class="insight-hero">
        ${B.metric("天天基金策略数", benchmarkOverview.策略数 || 0, "渠道口径，不按展示样本过滤")}
        ${B.metric("有业绩基准说明", benchmarkOverview.有业绩基准说明 || 0, "详情页存在可读取披露文本")}
        ${B.metric("无业绩基准说明", benchmarkOverview.无业绩基准说明 || 0, "不使用仅曲线状态替代说明文本")}
        ${B.metric("披露覆盖率", percent(benchmarkOverview.披露覆盖率), benchmarkOverview.详情缺失策略数 ? `详情缺失 ${count(benchmarkOverview.详情缺失策略数)} 条` : "详情文件已覆盖全部天天基金策略")}
      </section>
      <section class="two-col">
        <section>
          <div class="panel-head"><h2>按研报产品类型</h2></div>
          ${B.table(["研报产品类型", "策略数", "有业绩基准说明", "无业绩基准说明", "披露覆盖率"], benchmarkByType, benchmarkFormatter)}
        </section>
        <section>
          <div class="panel-head"><h2>按投顾机构</h2></div>
          ${B.table(["投顾机构", "策略数", "有业绩基准说明", "无业绩基准说明", "披露覆盖率"], benchmarkByInstitution.slice(0, 20), benchmarkFormatter)}
        </section>
      </section>
    </section>

    <details class="panel fold-block">
      <summary>展开数据审计表</summary>
      <div class="row-detail-body">
        <section>
          <div class="panel-head"><div><h2>关键字段缺失审计</h2><p class="desc">${B.esc(fieldAudit.统计口径 || "当前导出包未生成字段缺失审计。")}</p></div></div>
          ${fieldAuditRows.length ? B.table(["指标", "样本数", "有效样本", "缺失样本", "覆盖率", "缺失原因判断", "处理建议"], fieldAuditRows, (row, h) => h === "覆盖率" ? B.pct(row[h]) : B.fmt(row[h])) : '<div class="empty">暂无字段缺失审计。</div>'}
        </section>
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
  setupMetricAnalyzer();
})();
