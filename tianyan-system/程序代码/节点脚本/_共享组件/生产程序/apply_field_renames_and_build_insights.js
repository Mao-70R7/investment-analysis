const fs = require("fs");
const path = require("path");
const vm = require("vm");

function usage() {
  console.log("Usage: node 节点脚本/_共享组件/生产程序/apply_field_renames_and_build_insights.js [--report-root DIR]");
}

function parseArgs(argv) {
  let reportRoot = null;
  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i];
    if (arg === "--help" || arg === "-h" || arg === "/?") {
      usage();
      process.exit(0);
    }
    if (arg === "--report-root") {
      reportRoot = argv[i + 1];
      if (!reportRoot) {
        throw new Error("--report-root requires a directory path");
      }
      i += 1;
      continue;
    }
    throw new Error(`Unknown argument: ${arg}`);
  }
  return {
    reportRoot: path.resolve(reportRoot || path.join(__dirname, "..")),
  };
}

const { reportRoot: root } = parseArgs(process.argv.slice(2));
const summaryPath = path.join(root, "basic_data", "data", "basic_summary.js");
const summaryCorePath = path.join(root, "basic_data", "data", "basic_summary_core.js");
const insightDataPackPath = path.join(root, "basic_data", "data", "insight_data_pack.js");
const insightHoldingPackPath = path.join(root, "basic_data", "data", "insight_holding_pack.js");
const insightRebalancePackPath = path.join(root, "basic_data", "data", "insight_rebalance_pack.js");
const insightRebalanceMonthManifestPath = path.join(root, "basic_data", "data", "insight_rebalance_month_manifest.js");
const insightRebalanceMonthDir = path.join(root, "basic_data", "data", "insight_rebalance_months");
const holdingSnapshotPackPath = path.join(root, "basic_data", "data", "holding_snapshot_pack.json");
const holdingSnapshotPackJsPath = path.join(root, "basic_data", "data", "holding_snapshot_pack.js");
const rebalanceFundCategoryPackPath = path.join(root, "basic_data", "data", "rebalance_fund_category_pack.json");
const rebalanceFundCategoryPackJsPath = path.join(root, "basic_data", "data", "rebalance_fund_category_pack.js");
const rebalanceFundCategoryManifestPath = path.join(root, "basic_data", "data", "rebalance_fund_category_manifest.js");
const rebalanceFundCategoryMonthDir = path.join(root, "basic_data", "data", "rebalance_fund_category_months");
const fundDetailPackPath = path.join(root, "basic_data", "data", "fund_detail_pack.js");
const fundEconomicExposurePackPath = path.join(root, "basic_data", "data", "fund_economic_exposure_pack.json");
const aiSemanticIndexPath = path.join(root, "basic_data", "data", "ai_semantic_index.js");
const standardEntityDictionaryPath = path.join(root, "basic_data", "data", "standard_entity_dictionary.js");
const detailsDir = path.join(root, "basic_data", "data", "details");
const projectRoot = path.resolve(__dirname, "..");
const fundLookthroughSnapshotPath = path.join(projectRoot, "outputs", "current_fund_lookthrough_quality", "latest_fund_classification_snapshot.json");
const reportAssetVersion = "basic_data_20260811_minimal_orange_risk_weight_v3";
const LEGACY_BENCHMARK_BUCKET_FIELDS = ["基准" + "权益分档", "广义" + "权益分档", "基准" + "权益分类档"];
const LEGACY_BENCHMARK_BUCKET_NOTE_FIELDS = ["基准" + "权益分档说明", "广义" + "权益分档说明"];
const LEGACY_RISK_WEIGHT_FIELDS = ["广义" + "权益权重"];

function raw(value) {
  return value == null || value === "" ? "" : String(value);
}

function firstField(row, fields) {
  for (const field of fields) {
    if (row && row[field] !== undefined && row[field] !== null && row[field] !== "") return row[field];
  }
  return null;
}

function num(value) {
  if (value == null || value === "") return null;
  const n = Number(value);
  return Number.isFinite(n) ? n : null;
}

function nz(value) {
  return num(value) ?? 0;
}

function clampPercentage(value) {
  const n = num(value);
  if (n === null) return null;
  return Math.max(0, Math.min(100, n));
}

function bucketFromPercentage(value) {
  const n = clampPercentage(value);
  if (n === null) return "";
  if (n <= 0.000001) return "L0";
  return `L${Math.min(10, Math.max(1, Math.ceil(n / 10)))}`;
}

function bucketLevel(value) {
  const match = raw(value).match(/^L(10|[0-9])$/);
  return match ? Number(match[1]) : null;
}

function strategyCompositeAttributes(row) {
  const equity = clampPercentage(row["权益基金权重"]);
  const bond = clampPercentage(row["债券基金权重"]);
  const cash = clampPercentage(row["货币基金权重"]);
  const mixed = clampPercentage(row["混合基金权重"]);
  const qdii = clampPercentage(row["QDII权重"]);
  const index = clampPercentage(row["指数基金权重"]);
  const active = clampPercentage(row["主动基金权重"]);
  const benchmarkOverseasParts = [clampPercentage(row["基准海外权益权重"]), clampPercentage(row["基准资产类别-海外权益"])].filter((value) => value !== null);
  const benchmarkOverseas = benchmarkOverseasParts.length ? Math.max(...benchmarkOverseasParts) : null;
  const benchmarkRisk = clampPercentage(row["基准风险资产权重_百分比"] ?? firstField(row, LEGACY_RISK_WEIGHT_FIELDS));
  const hasPortfolioAllocation = [equity, bond, cash, mixed].some((value) => value !== null);
  const equityCenter = hasPortfolioAllocation ? clampPercentage((equity || 0) + 0.5 * (mixed || 0)) : null;
  const fixedIncomeCenter = hasPortfolioAllocation ? clampPercentage((bond || 0) + (cash || 0) + 0.5 * (mixed || 0)) : null;
  const overseasParts = [qdii, benchmarkOverseas].filter((value) => value !== null);
  const overseasCenter = overseasParts.length ? clampPercentage(Math.max(...overseasParts)) : null;
  const deviation = benchmarkRisk === null || equityCenter === null ? null : Number((equityCenter - benchmarkRisk).toFixed(4));
  return {
    "权益中枢": equityCenter,
    "固收中枢": fixedIncomeCenter,
    "基准风险资产中枢": benchmarkRisk,
    "海外配置中枢": overseasCenter,
    "指数化程度": index,
    "主动管理程度": active,
    "风险资产偏离": deviation,
  };
}

function cleanTerminology(value) {
  if (typeof value !== "string") return value;
  return value
    .replaceAll("主可比池", "业务分类")
    .replaceAll("测算风险等级", "风险等级")
    .replaceAll("业务主分类", "业务分类")
    .replaceAll("风险基础分类", "风险等级")
    .replaceAll("原披露风险等级", "披露风险等级");
}

function loadSummary() {
  const code = fs.readFileSync(summaryPath, "utf8");
  const ctx = { window: { __BASIC_DATA__: {} } };
  vm.createContext(ctx);
  vm.runInContext(code, ctx);
  return ctx.window.__BASIC_DATA__.summary;
}

function loadDetail(filePath) {
  const ctx = { window: { __BASIC_DATA__: { details: {} } } };
  vm.createContext(ctx);
  vm.runInContext(fs.readFileSync(filePath, "utf8"), ctx);
  return Object.values(ctx.window.__BASIC_DATA__.details)[0];
}

function waitMs(ms) {
  Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, ms);
}

function writeFileUtf8(filePath, content) {
  const retryable = new Set(["UNKNOWN", "EBUSY", "EPERM", "EACCES"]);
  let lastError = null;
  for (let attempt = 0; attempt < 30; attempt += 1) {
    try {
      fs.writeFileSync(filePath, content, "utf8");
      return;
    } catch (error) {
      lastError = error;
      if (!retryable.has(error.code)) throw error;
      waitMs(250 + attempt * 50);
    }
  }
  throw lastError;
}

function writeDetail(filePath, detail) {
  writeFileUtf8(filePath, `window.__BASIC_DATA__.details[${JSON.stringify(detail.id)}] = ${JSON.stringify(detail)};\n`);
}

function writeSummary(summary) {
  writeFileUtf8(summaryPath, `window.__BASIC_DATA__.summary = ${JSON.stringify(summary)};\n`);
}

const INSIGHT_REBALANCE_LAZY_FIELDS = new Set([
  "策略资产变化明细",
  "调仓基金月度汇总",
  "调仓基金明细",
  "调仓方向汇总",
  "广发基金调仓机会"
]);

const INSIGHT_REBALANCE_MONTH_FIELDS = new Map([
  ["策略资产变化明细", "调仓日期"],
  ["调仓基金月度汇总", "月份"]
]);

const INSIGHT_HOLDING_LAZY_FIELDS = new Set([
  "当前持仓策略基金明细",
  "当前持仓基金风险明细",
  "当前持仓基金公司风险明细",
  "持仓行业时间序列",
  "持仓时间序列",
  "当前持仓基金类型",
  "当前持仓基金",
  "当前持仓基金公司"
]);

const INSIGHT_LAZY_FIELDS = new Set([...INSIGHT_REBALANCE_LAZY_FIELDS, ...INSIGHT_HOLDING_LAZY_FIELDS]);

function splitSummaryPacks(summary) {
  const insight = summary && typeof summary.insightData === "object" && summary.insightData ? summary.insightData : {};
  const lazyInsight = {};
  const holdingInsight = {};
  const rebalanceInsight = {};
  const rebalanceMonthInsight = {};
  const coreInsight = {};
  for (const [key, value] of Object.entries(insight)) {
    if (INSIGHT_LAZY_FIELDS.has(key)) {
      lazyInsight[key] = value;
      if (INSIGHT_HOLDING_LAZY_FIELDS.has(key)) holdingInsight[key] = value;
      if (INSIGHT_REBALANCE_LAZY_FIELDS.has(key)) {
        if (INSIGHT_REBALANCE_MONTH_FIELDS.has(key)) rebalanceMonthInsight[key] = value;
        else rebalanceInsight[key] = value;
      }
    } else {
      coreInsight[key] = value;
    }
  }
  coreInsight.__lazyPack = {
    externalScript: `./data/insight_data_pack.js?v=${reportAssetVersion}`,
    fields: Object.keys(lazyInsight).sort(),
    packs: {
      holding: {
        global: "__BASIC_INSIGHT_HOLDING_PACK__",
        externalScript: `./data/insight_holding_pack.js?v=${reportAssetVersion}`,
        fields: Object.keys(holdingInsight).sort()
      },
      rebalance: {
        global: "__BASIC_INSIGHT_REBALANCE_PACK__",
        externalScript: `./data/insight_rebalance_pack.js?v=${reportAssetVersion}`,
        fields: Object.keys(rebalanceInsight).sort(),
        monthShards: {
          global: "__BASIC_INSIGHT_REBALANCE_MONTH_PACKS__",
          manifestGlobal: "__BASIC_INSIGHT_REBALANCE_MONTH_MANIFEST__",
          manifestScript: `./data/insight_rebalance_month_manifest.js?v=${reportAssetVersion}`,
          fields: Object.keys(rebalanceMonthInsight).sort()
        }
      }
    }
  };
  const core = { ...summary, insightData: coreInsight, summaryPackMode: "core_lazy_v1" };
  const lazy = {
    version: 1,
    generatedAt: summary?.overview?.数据刷新时间 || summary?.overview?.生成时间 || new Date().toISOString(),
    fields: Object.keys(lazyInsight).sort(),
    insightData: lazyInsight
  };
  const sectionPack = (name, data) => ({
    version: 1,
    section: name,
    generatedAt: summary?.overview?.数据刷新时间 || summary?.overview?.生成时间 || new Date().toISOString(),
    fields: Object.keys(data).sort(),
    insightData: data
  });
  return {
    core,
    lazy,
    holding: sectionPack("holding", holdingInsight),
    rebalance: sectionPack("rebalance", rebalanceInsight),
    rebalanceMonth: sectionPack("rebalance_month", rebalanceMonthInsight)
  };
}

function rowMonthForShard(row, field) {
  const dateField = INSIGHT_REBALANCE_MONTH_FIELDS.get(field);
  const value = row && dateField ? row[dateField] : "";
  return String(value || "").slice(0, 7) || "unknown";
}

function compactObjectRows(rows) {
  const fields = [];
  const seen = new Set();
  for (const row of rows || []) {
    for (const field of Object.keys(row || {})) {
      if (seen.has(field)) continue;
      seen.add(field);
      fields.push(field);
    }
  }
  return {
    fields,
    rows: (rows || []).map((row) => fields.map((field) => row?.[field] ?? null))
  };
}

function writeInsightRebalanceMonthShards(pack) {
  fs.rmSync(insightRebalanceMonthDir, { recursive: true, force: true });
  fs.mkdirSync(insightRebalanceMonthDir, { recursive: true });
  const fields = pack.fields || [];
  const data = pack.insightData || {};
  const byMonth = new Map();
  for (const field of fields) {
    const rows = Array.isArray(data[field]) ? data[field] : [];
    for (const row of rows) {
      const month = rowMonthForShard(row, field);
      if (!byMonth.has(month)) byMonth.set(month, {});
      const bucket = byMonth.get(month);
      if (!bucket[field]) bucket[field] = [];
      bucket[field].push(row);
    }
  }
  const months = [...byMonth.entries()]
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([month, monthData]) => {
      const fileName = `${month}.js`;
      const compactMonthData = Object.fromEntries(fields.map((field) => [field, compactObjectRows(monthData[field] || [])]));
      writeFileUtf8(
        path.join(insightRebalanceMonthDir, fileName),
        `window.__BASIC_INSIGHT_REBALANCE_MONTH_PACKS__ = window.__BASIC_INSIGHT_REBALANCE_MONTH_PACKS__ || {};\nwindow.__BASIC_INSIGHT_REBALANCE_MONTH_PACKS__[${JSON.stringify(month)}] = ${JSON.stringify(compactMonthData)};\n`
      );
      const rows = Object.fromEntries(fields.map((field) => [field, (monthData[field] || []).length]));
      return {
        month,
        rows,
        externalScript: `./data/insight_rebalance_months/${fileName}?v=${reportAssetVersion}`
      };
    });
  const manifest = {
    version: Number(pack.version || 1),
    generatedAt: pack.generatedAt || new Date().toISOString(),
    fields,
    rows: Object.fromEntries(fields.map((field) => [field, Array.isArray(data[field]) ? data[field].length : 0])),
    months
  };
  writeFileUtf8(
    insightRebalanceMonthManifestPath,
    `window.__BASIC_INSIGHT_REBALANCE_MONTH_MANIFEST__ = ${JSON.stringify(manifest)};\n`
  );
}

function writeSummaryPacks(summary) {
  writeSummary(summary);
  const { core, lazy, holding, rebalance, rebalanceMonth } = splitSummaryPacks(summary);
  writeFileUtf8(summaryCorePath, `window.__BASIC_DATA__.summary = ${JSON.stringify(core)};\n`);
  writeFileUtf8(insightDataPackPath, `window.__BASIC_INSIGHT_DATA_PACK__ = ${JSON.stringify(lazy)};\n`);
  writeFileUtf8(insightHoldingPackPath, `window.__BASIC_INSIGHT_HOLDING_PACK__ = ${JSON.stringify(holding)};\n`);
  writeFileUtf8(insightRebalancePackPath, `window.__BASIC_INSIGHT_REBALANCE_PACK__ = ${JSON.stringify(rebalance)};\n`);
  writeInsightRebalanceMonthShards(rebalanceMonth);
}

function writeHoldingSnapshotPack(pack) {
  const payload = JSON.stringify(pack);
  writeFileUtf8(holdingSnapshotPackPath, payload);
  writeFileUtf8(holdingSnapshotPackJsPath, `window.__BASIC_HOLDING_SNAPSHOT_PACK__ = ${payload};\n`);
}

function writeRebalanceFundCategoryPack(pack) {
  const payload = JSON.stringify(pack);
  writeFileUtf8(rebalanceFundCategoryPackPath, payload);
  writeFileUtf8(rebalanceFundCategoryPackJsPath, `window.__BASIC_REBALANCE_FUND_CATEGORY_PACK__ = ${payload};\n`);
  fs.rmSync(rebalanceFundCategoryMonthDir, { recursive: true, force: true });
  fs.mkdirSync(rebalanceFundCategoryMonthDir, { recursive: true });
  const rowsByMonth = new Map();
  for (const row of pack.rows || []) {
    const month = String(row?.[0] || "").slice(0, 7) || "unknown";
    if (!rowsByMonth.has(month)) rowsByMonth.set(month, []);
    rowsByMonth.get(month).push(row);
  }
  const months = [...rowsByMonth.entries()]
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([month, rows]) => {
      const fileName = `${month}.js`;
      writeFileUtf8(
        path.join(rebalanceFundCategoryMonthDir, fileName),
        `window.__BASIC_REBALANCE_FUND_CATEGORY_MONTH_PACKS__ = window.__BASIC_REBALANCE_FUND_CATEGORY_MONTH_PACKS__ || {};\nwindow.__BASIC_REBALANCE_FUND_CATEGORY_MONTH_PACKS__[${JSON.stringify(month)}] = ${JSON.stringify(rows)};\n`
      );
      return {
        month,
        rows: rows.length,
        externalScript: `./data/rebalance_fund_category_months/${fileName}?v=${reportAssetVersion}`
      };
    });
  const manifest = {
    version: Number(pack.version || 1),
    fields: pack.fields || [],
    dict: pack.dict || {},
    rows: (pack.rows || []).length,
    months
  };
  writeFileUtf8(
    rebalanceFundCategoryManifestPath,
    `window.__BASIC_REBALANCE_FUND_CATEGORY_MANIFEST__ = ${JSON.stringify(manifest)};\n`
  );
}

function loadFundLookthroughSnapshot() {
  if (!fs.existsSync(fundLookthroughSnapshotPath)) {
    return { byCode: new Map(), generatedAt: "" };
  }
  try {
    const payload = JSON.parse(fs.readFileSync(fundLookthroughSnapshotPath, "utf8"));
    const byCode = new Map();
    for (const row of payload.rows || []) {
      const code = raw(row.基金代码);
      if (!code) continue;
      const list = byCode.get(code) || [];
      list.push(row);
      byCode.set(code, list);
    }
    for (const list of byCode.values()) {
      list.sort((a, b) => raw(b.报告期).localeCompare(raw(a.报告期)));
    }
    return { byCode, generatedAt: raw(payload.生成时间) };
  } catch (error) {
    console.warn(`[WARN] Failed to read fund lookthrough snapshot: ${error.message}`);
    return { byCode: new Map(), generatedAt: "" };
  }
}

const fundLookthroughSnapshot = loadFundLookthroughSnapshot();

function loadFundEconomicExposurePack() {
  if (!fs.existsSync(fundEconomicExposurePackPath)) {
    return { byCode: new Map(), generatedAt: "" };
  }
  try {
    const payload = JSON.parse(fs.readFileSync(fundEconomicExposurePackPath, "utf8"));
    const fields = payload.fields || [];
    const byCode = new Map();
    const toObject = (row) => {
      if (row && typeof row === "object" && !Array.isArray(row)) return row;
      return Object.fromEntries(fields.map((field, index) => [field, row?.[index] ?? ""]));
    };
    for (const rawRow of payload.rows || []) {
      const row = toObject(rawRow);
      const code = raw(row.基金代码);
      if (code) byCode.set(code, row);
    }
    return { byCode, generatedAt: raw(payload.generatedAt || payload.生成时间) };
  } catch (error) {
    console.warn(`[WARN] Failed to read fund economic exposure pack: ${error.message}`);
    return { byCode: new Map(), generatedAt: "" };
  }
}

const fundEconomicExposurePack = loadFundEconomicExposurePack();

function chooseFundLookthrough(fundCode, asOfDate = "") {
  const list = fundLookthroughSnapshot.byCode.get(raw(fundCode));
  if (!list || !list.length) return null;
  const date = raw(asOfDate);
  if (date) {
    const matched = list.find((row) => raw(row.报告期) && raw(row.报告期) <= date);
    if (matched) return matched;
  }
  return list[0];
}

function chooseFundEconomicExposure(fundCode, asOfDate = "") {
  const row = fundEconomicExposurePack.byCode.get(raw(fundCode));
  if (!row) return null;
  const report = raw(row.报告期);
  const date = raw(asOfDate);
  if (!date || !report || report <= date) return row;
  return null;
}

function exposureObject(value) {
  if (!value) return {};
  if (typeof value === "object" && !Array.isArray(value)) {
    return Object.fromEntries(Object.entries(value)
      .filter(([k, v]) => {
        const label = raw(k);
        return label && !["-", "--", "未识别", "未分类"].includes(label) && Number.isFinite(Number(v));
      })
      .map(([k, v]) => [raw(k), Number(v)]));
  }
  if (typeof value === "string") {
    try {
      const parsed = JSON.parse(value);
      return exposureObject(parsed);
    } catch {
      return {};
    }
  }
  return {};
}

function economicThemeLabels(value) {
  if (!Array.isArray(value)) return "";
  const labels = [];
  for (const item of value) {
    const label = raw(typeof item === "object" ? (item.主题名称 || item.名称 || item.label || item.name) : item);
    if (label && !labels.includes(label)) labels.push(label);
  }
  return labels.join("、");
}

function applyEconomicExposure(result, economic) {
  if (!economic) return result;
  const assetExposure = normalizeExposure(exposureObject(economic.经济资产暴露));
  if (!Object.keys(assetExposure).length) return result;
  const industryExposure = normalizeExposure(exposureObject(economic.经济行业暴露));
  const fundLevelIndustryExposure = absoluteIndustryExposure(assetExposure, industryExposure);
  const themeExposure = themeExposureFromIndustry(industryExposure);
  const groupExposure = groupExposureFromTheme(themeExposure);
  const reportAssetClass = primaryExposure(assetExposure, result.reportAssetClass);
  const reportAIndustry = primaryExposure(industryExposure, "");
  const equityThemeFallback = (assetExposure["A股"] || 0) > 0 ? "宽基/主动权益" : result.equityIndustryTheme;
  const equityIndustryTheme = primaryExposure(themeExposure, equityThemeFallback);
  const equityIndustryGroup = primaryExposure(groupExposure, equityIndustryTheme ? inferEquityIndustryGroup(equityIndustryTheme) : result.equityIndustryGroup);
  const assetMeta = reportAssetThemeMap[reportAssetClass] || {};
  const industryTheme = reportAIndustry ? (industryMeta(reportAIndustry).theme || reportAIndustry) : (assetMeta.theme || result.industryTheme);
  const industryGroup = reportAIndustry ? (industryMeta(reportAIndustry).group || inferIndustryGroup(industryTheme, result.fundType)) : (assetMeta.group || result.industryGroup);
  result.fundName = raw(economic.基金名称) || result.fundName;
  result.company = raw(economic.基金公司) || result.company;
  result.fundType = raw(economic.基金类型) || result.fundType;
  result.industryTheme = industryTheme;
  result.industryGroup = industryGroup;
  result.equityIndustryTheme = equityIndustryTheme;
  result.equityIndustryGroup = equityIndustryGroup;
  result.reportAssetClass = reportAssetClass;
  result.reportAIndustry = reportAIndustry;
  result.assetExposure = assetExposure;
  result.industryExposure = industryExposure;
  result.absoluteIndustryExposure = fundLevelIndustryExposure;
  result.themeExposure = themeExposure;
  result.groupExposure = groupExposure;
  result.confidence = raw(economic.置信度) || result.confidence;
  result.classificationSource = "基金经济暴露快照";
  result.lookthroughReportDate = raw(economic.报告期) || result.lookthroughReportDate;
  result.lookthroughDisclosureDate = "";
  result.lookthroughCoverageStatus = raw(economic.质量状态) || result.lookthroughCoverageStatus;
  result.isEstimated = /兜底|人工/.test(raw(economic.质量状态)) ? "是" : "否";
  result.economicAssetClass = raw(economic.标准资产大类);
  result.economicAssetSubClass = raw(economic.标准资产细类);
  result.economicExposureQuality = raw(economic.质量状态);
  result.economicExposureConfidence = raw(economic.置信度);
  result.economicExposureMethod = raw(economic.穿透方法);
  result.economicExposureEvidence = raw(economic.证据说明);
  result.economicThemeLabels = economicThemeLabels(economic.主题标签);
  result.rawAssetExposure = exposureText(exposureObject(economic.原始资产暴露));
  result.economicAssetExposureText = exposureText(assetExposure);
  result.economicIndustryExposureText = exposureText(fundLevelIndustryExposure);
  result.economicIndustryInternalExposureText = exposureText(industryExposure);
  result.basis = [
    `基金经济暴露快照：报告期${result.lookthroughReportDate || "未标注"}`,
    result.economicExposureMethod ? `穿透方法=${result.economicExposureMethod}` : "",
    result.economicExposureQuality ? `质量状态=${result.economicExposureQuality}` : "",
    result.economicExposureEvidence || ""
  ].filter(Boolean).join("；");
  return result;
}

function writeFundDetailPack(pack) {
  writeFileUtf8(fundDetailPackPath, `window.__BASIC_DATA__ = window.__BASIC_DATA__ || {}; window.__BASIC_DATA__.fundDetailPack = ${JSON.stringify(pack)};\n`);
}

function writeAiSemanticIndex(pack) {
  writeFileUtf8(aiSemanticIndexPath, `window.__AI_STRATEGY_SEMANTIC_INDEX__ = ${JSON.stringify(pack)};\n`);
}

function writeStandardEntityDictionary(pack) {
  writeFileUtf8(standardEntityDictionaryPath, `window.__BASIC_STANDARD_ENTITY_DICTIONARY__ = ${JSON.stringify(pack)};\n`);
}

function requireInput(filePath, label, kind = "file") {
  if (!fs.existsSync(filePath)) {
    throw new Error(`Missing ${label}: ${filePath}`);
  }
  const stat = fs.statSync(filePath);
  if (kind === "directory" && !stat.isDirectory()) {
    throw new Error(`${label} is not a directory: ${filePath}`);
  }
  if (kind === "file" && !stat.isFile()) {
    throw new Error(`${label} is not a file: ${filePath}`);
  }
}

function validateInputs() {
  requireInput(summaryPath, "basic summary JS");
  requireInput(detailsDir, "strategy details directory", "directory");
}

function isGuangfaStrategy(row) {
  return /广发基金|广发投顾/.test(`${raw(row["投顾机构"])} ${raw(row["渠道"])}`);
}

const riskBands = [
  { index: 0, level: "R0 现金/超低波", eqMax: 3, volMax: 0.8, mddMax: 1.2 },
  { index: 1, level: "R1 低波", eqMax: 8, volMax: 2.0, mddMax: 3.0 },
  { index: 2, level: "R2 稳健收益", eqMax: 18, volMax: 4.0, mddMax: 6.0 },
  { index: 3, level: "R3 均衡稳健", eqMax: 35, volMax: 7.5, mddMax: 12.0 },
  { index: 4, level: "R4 均衡成长", eqMax: 55, volMax: 11.0, mddMax: 20.0 },
  { index: 5, level: "R5 权益/进取", eqMax: Infinity, volMax: Infinity, mddMax: Infinity },
];
const riskLabelMap = new Map(riskBands.map((band) => [band.index, band.level]));

function hasRiskHoldingData(row) {
  return ["权益基金权重", "债券基金权重", "货币基金权重", "混合基金权重", "指数基金权重"]
    .some((key) => nz(row[key]) > 0);
}

function metricRiskLevel(value, key) {
  const v = nz(value);
  for (const band of riskBands) {
    const max = key === "eq" ? band.eqMax : key === "vol" ? band.volMax : band.mddMax;
    if (v <= max) return band.index;
  }
  return 5;
}

function metricRiskLabel(index) {
  return riskLabelMap.get(index) || "R5 权益/进取";
}

function measuredRiskResult(row) {
  if (!hasRiskHoldingData(row)) {
    return {
      level: "D0 持仓缺失",
      eqLevel: "",
      volLevel: "",
      mddLevel: "",
      trigger: "持仓权重缺失",
      basis: "权益/债券/货币/混合/指数基金权重均为0或缺失，暂不进入正式风险同档比较。"
    };
  }
  const eqLevel = metricRiskLevel(row["权益基金权重"], "eq");
  const volLevel = metricRiskLevel(row["波动率"], "vol");
  const mddLevel = metricRiskLevel(row["最大回撤"], "mdd");
  const finalLevel = Math.max(eqLevel, volLevel, mddLevel);
  const triggers = [];
  if (eqLevel === finalLevel) triggers.push("权益");
  if (volLevel === finalLevel) triggers.push("波动");
  if (mddLevel === finalLevel) triggers.push("回撤");
  return {
    level: metricRiskLabel(finalLevel),
    eqLevel: metricRiskLabel(eqLevel),
    volLevel: metricRiskLabel(volLevel),
    mddLevel: metricRiskLabel(mddLevel),
    trigger: finalLevel === 0 ? "三项均在R0内" : triggers.join("+"),
    basis: `权益${nz(row["权益基金权重"]).toFixed(2)}%，波动${nz(row["波动率"]).toFixed(2)}%，最大回撤${nz(row["最大回撤"]).toFixed(2)}%；三项分别落档后取最高风险档。`
  };
}

function normalizeRisk(row) {
  return raw(row["测算风险等级"]) || raw(row["风险基础分类"]) || measuredRiskResult(row).level;
}

function normalizeBusiness(row) {
  return raw(row["业务分类"]) || raw(row["业务主分类"]) || raw(row["主可比池"]) || "未分类";
}

function overseasTextScope(...values) {
  return values
    .map(raw)
    .filter(Boolean)
    .join(" ")
    .replace(/兴证全球/g, "兴证")
    .replace(/MSCI\s*(沪深\s*300|中国A股|中国)/gi, "");
}

function hasStrongOverseasText(...values) {
  return /QDII|海外|港股|美股|恒生|纳斯达克|纳指|标普|S&P|美国|印度|越南|日经|日本|德国|DAX|全球(?!版)(?:资产|配置|精选|优选|权益|股票|债券|多元|组合|市场)?/i
    .test(overseasTextScope(...values));
}

function benchmarkOverseasWeight(value) {
  const text = raw(value);
  if (!text) return 0;
  const normalized = text.replace(/（/g, "(").replace(/）/g, ")").replace(/×|＊/g, "*").replace(/＋/g, "+");
  const parts = normalized.split(/[+＋]/).map((part) => part.trim()).filter(Boolean);
  const overseasRe = /恒生|纳斯达克|纳指|标普|S&P|MSCI全球|MSCI发达|美股|港股|日经|印度|越南|德国|DAX/i;
  const domesticMsciRe = /MSCI\s*(沪深\s*300|中国A股|中国)/i;
  let total = 0;
  for (const part of (parts.length ? parts : [normalized])) {
    if (!overseasRe.test(part) || domesticMsciRe.test(part)) continue;
    const pctMatch = part.match(/(\d+(?:\.\d+)?)\s*%/);
    if (pctMatch) total += Number(pctMatch[1]) || 0;
    else if (parts.length <= 1) total += 100;
  }
  return Math.round(total * 10000) / 10000;
}

function overseasEvidence(row) {
  const qdii = nz(row["QDII权重"]);
  const benchmarkWeight = benchmarkOverseasWeight(raw(row["业绩基准说明"]) || raw(row["业绩基准"]) || raw(row["基准公式解析"]));
  const directText = hasStrongOverseasText(row["策略名称"], row["披露策略类型"]);
  const primary = qdii >= 30 || benchmarkWeight >= 30 || directText;
  const mixed = primary || qdii >= 10 || benchmarkWeight >= 10;
  const basis = [
    `QDII/海外持仓${qdii.toFixed(2)}%`,
    `海外基准${benchmarkWeight.toFixed(2)}%`,
    directText ? "策略名称/披露类型含明确海外配置证据" : "",
  ].filter(Boolean).join("，");
  return { qdii, benchmarkWeight, directText, primary, mixed, basis };
}

function refreshOverseasRegionAndLabels(row) {
  const overseas = overseasEvidence(row);
  if (overseas.primary) row["市场地域"] = "海外/全球";
  else if (overseas.qdii >= 10 || overseas.benchmarkWeight >= 10) row["市场地域"] = "国内+海外";
  else row["市场地域"] = "国内";

  const labels = raw(row["特殊标签"])
    .split(/[、,，]/)
    .map((item) => item.trim())
    .filter((item) => item && item !== "无" && item !== "海外全球");
  if (overseas.primary) labels.push("海外全球");
  row["特殊标签"] = [...new Set(labels)].join("、") || "无";
  return overseas;
}

function isTargetProfitProduct(text) {
  const normalized = raw(text);
  const strongBrand = /目标盈|小目标|小赢家|小杏运|步步高|小星愿|小盈加|智盈|智慧目标投|小常乐|常乐/.test(normalized);
  const explicitTarget = /目标收益|收益目标|绝对收益目标|目标止盈|止盈目标|达标即止盈|达标止盈|止盈达标|止盈提醒|达到目标|目标达成|达标退出|达标赎回/.test(normalized);
  const lifecycleExit = /(期次|第[零一二三四五六七八九十百千万\d]+期|\d{1,2}期|到期|期满|运作期|封闭期|续作|赎回|退出|发售|发行|自动终止|stopped|两年期|一年期|年中版|新年特供)/i.test(normalized);
  return strongBrand || (explicitTarget && lifecycleExit);
}

function isSignalStrategy(text) {
  const normalized = raw(text).replace(/\s+/g, "");
  if (/薪动月月投|超级定投家|指数100份/.test(normalized)) return true;
  if (/信号类|信号服务|债市信号指导|买入信号|卖出信号|建议信号|止盈信号|买卖全程信号/.test(normalized)) return true;
  if (/(智能发车|滚动带投|发车带投|带你买卖)/.test(normalized) && /买卖点|买卖|卖出|止盈|信号/.test(normalized)) return true;
  if (/(100份|等额划分|分笔管理|分批投)/.test(normalized) && /发车|买卖|带投|止盈|加倍投入|信号/.test(normalized)) return true;
  return /信号/.test(normalized) && /买入|卖出|止盈|发车|跟车|加倍投入|带投|买卖/.test(normalized);
}

function canonicalBusiness(row) {
  const original = normalizeBusiness(row);
  const governanceText = `${raw(row["策略治理状态"])} ${raw(row["分析分组"])} ${raw(row["治理状态"])} ${raw(row["规则说明"])}`;
  const text = `${raw(row["策略名称"])} ${raw(row["策略概念"])} ${raw(row["策略描述"])} ${raw(row["特殊标签"])} ${raw(row["披露策略类型"])} ${raw(row["补充识别文本"])} ${governanceText}`;
  const overseas = overseasEvidence(row);
  const equity = nz(row["权益基金权重"]);
  const bond = nz(row["债券基金权重"]);
  const cash = nz(row["货币基金权重"]);
  const qdii = nz(row["QDII权重"]);
  const index = nz(row["指数基金权重"]);
  const mixed = nz(row["混合基金权重"]);
  const bondCash = bond + cash;
  const benchmarkBucket = raw(row["基准风险资产权重"]);
  const benchmarkLevel = bucketLevel(benchmarkBucket);
  const comparisonTrack = raw(row["非权益比较轨道"]);
  if (/目标日期|养老|生命周期/.test(text)) {
    return { business: "目标日期/养老型", basis: "名称或标签包含养老/目标日期机制，单列为长期养老场景。" };
  }
  if (isSignalStrategy(text)) {
    return { business: "信号类策略", basis: "名称、策略介绍或标签明确包含薪动月月投、指数100份、超级定投家，或100份/分笔管理、智能发车、滚动带投与买卖/止盈信号等执行机制，单列为信号类策略。" };
  }
  if (isTargetProfitProduct(text)) {
    return { business: "目标盈系列产品", basis: /同系列目标盈继承/.test(governanceText) ? "同一投顾、同一去期次系列已有明确目标盈证据，本期按目标盈系列继承归类。" : "名称或介绍包含目标盈/小目标/小杏运品牌、期次，或明确目标收益/达标止盈/到期赎回机制，按目标达标运营池分类；普通止盈止损、预期兑现后止盈或仅含到期时间不归入目标盈。" };
  }
  if ((raw(row["风险等级"]) === "D0 持仓缺失" || !["权益基金权重", "债券基金权重", "货币基金权重", "混合基金权重", "指数基金权重"].some((key) => nz(row[key]) > 0)) && benchmarkLevel === null) {
    return { business: original || "未分类", basis: "持仓权重缺失，保留原业务分类，不进入正式展示比较。" };
  }
  if (overseas.primary) {
    return { business: "海外/全球型", basis: `${benchmarkBucket ? `基准风险资产权重${benchmarkBucket}；` : ""}强海外证据归属：${overseas.basis}；通用投资范围、机构品牌和黄金/商品不触发海外分类。` };
  }
  if (/医药|医疗|消费|新能源|半导体|军工|科技|AI|人工智能|红利|低碳|高端制造|港股互联网/.test(text)) {
    return { business: "主题/行业型", basis: `${benchmarkBucket ? `基准风险资产权重${benchmarkBucket}；` : ""}名称或标签包含明确主题/行业暴露，单列用于营销与投研跟踪。` };
  }
  if (/商品主导|另类主导/.test(comparisonTrack)) {
    return { business: "商品/另类型", basis: `基准风险资产权重${benchmarkBucket || "未分档"}，非权益比较轨道为${comparisonTrack}，按商品/另类策略单列。` };
  }
  if (/货币主导/.test(comparisonTrack) || cash >= 80) {
    return { business: "现金管理型", basis: `基准风险资产权重${benchmarkBucket || "未分档"}，${comparisonTrack || `实际货币基金权重${cash.toFixed(2)}%`}显示现金管理特征。` };
  }
  if (/债券主导/.test(comparisonTrack) && benchmarkLevel !== null && benchmarkLevel <= 3) {
    const business = benchmarkLevel === 0 || bondCash >= 90 ? "纯债/短债型" : "固收增强型";
    return { business, basis: `基准风险资产权重${benchmarkBucket}为主分类依据，非权益比较轨道为债券主导；持仓债券+货币权重${bondCash.toFixed(2)}%用于复核。` };
  }
  if (benchmarkLevel !== null) {
    if (benchmarkLevel === 0) {
      const business = bondCash >= 70 ? "纯债/短债型" : "多资产配置型";
      return { business, basis: `基准风险资产权重${benchmarkBucket}为主分类依据；结合债券+货币权重${bondCash.toFixed(2)}%和${comparisonTrack || "未明确非权益轨道"}细分。` };
    }
    if (benchmarkLevel <= 3) {
      return { business: "固收增强型", basis: `基准风险资产权重${benchmarkBucket}为主分类依据，归入低权益增强区间；持仓权重仅用于复核。` };
    }
    if (benchmarkLevel <= 6) {
      return { business: "多资产配置型", basis: `基准风险资产权重${benchmarkBucket}为主分类依据，归入均衡多资产区间；地域、主动被动和持仓结构作为并列属性。` };
    }
    return { business: "偏股配置型", basis: `基准风险资产权重${benchmarkBucket}为主分类依据，归入高风险资产配置区间；地域、主题和实现方式作为并列属性。` };
  }
  if (bondCash >= 90 && equity < 10) {
    return { business: "纯债/短债型", basis: `基准风险资产权重缺失，使用实际债券+货币权重${bondCash.toFixed(2)}%、权益${equity.toFixed(2)}%兜底。` };
  }
  if (equity >= 40 || index >= 45) {
    return { business: "偏股配置型", basis: `基准风险资产权重缺失，使用权益${equity.toFixed(2)}%、指数${index.toFixed(2)}%兜底。` };
  }
  if (bondCash >= 70 && equity < 40) {
    return { business: "固收增强型", basis: `基准风险资产权重缺失，使用债券+货币权重${bondCash.toFixed(2)}%、权益${equity.toFixed(2)}%兜底。` };
  }
  if (mixed >= 20 || equity >= 15 || qdii >= 5) {
    return { business: "多资产配置型", basis: `权益${equity.toFixed(2)}%、混合${mixed.toFixed(2)}%、QDII${qdii.toFixed(2)}%，体现多资产配置。` };
  }
  return { business: original || "未分类", basis: "未命中资产主导规则，保留原业务分类并进入观察。" };
}

function reportClassification(row) {
  const text = `${raw(row["策略名称"])} ${raw(row["策略概念"])} ${raw(row["策略描述"])} ${raw(row["特殊标签"])} ${raw(row["业务分类"])} ${raw(row["主动被动"])} ${raw(row["策略实现标签"])} ${raw(row["补充识别文本"])}`;
  const equity = nz(row["权益基金权重"]);
  const bond = nz(row["债券基金权重"]);
  const cash = nz(row["货币基金权重"]);
  const qdii = nz(row["QDII权重"]);
  const index = nz(row["指数基金权重"]);
  const mixed = nz(row["混合基金权重"]);
  const bondCash = bond + cash;
  const overseas = overseasEvidence(row);
  if (raw(row["风险等级"]) === "D0 持仓缺失") {
    return { type: "持仓缺失/不入池", subType: "", basis: "风险等级为D0或持仓权重缺失，不能稳定映射研报可比池；仅用于数据补齐清单，不进入市场总览、仓位分析和调仓主图。" };
  }
  const hasMultiAsset = overseas.mixed || /多资产|多元|黄金|商品|原油|REIT|另类|全天候/.test(text);
  const hasTheme = /行业|主题|医药|医疗|消费|新能源|半导体|军工|科技|AI|人工智能|红利|低碳|高端制造|港股互联网|食品饮料|电子|通信|煤炭|电力设备/.test(text);
  const isIndexDriven = index >= 45 || /指数|ETF|联接|增强|宽基|沪深|中证|创业板|科创|标普|纳斯达克|恒生/.test(text);
  const isRotation = /轮动|赛道|趋势|择时|风格|行业切换|主题切换/.test(text);
  if (equity >= 70) {
    let subType = "主动优选";
    if (overseas.primary || qdii >= 20) subType = "QDII型";
    else if (hasTheme) subType = "行业主题型";
    else if (isRotation) subType = "行业轮动";
    else if (isIndexDriven) subType = "指数驱动";
    return { type: "股票型", subType, basis: `权益${equity.toFixed(2)}%，按研报股票型可比池；子类=${subType}。` };
  }
  if (hasMultiAsset && (qdii >= 10 || overseas.benchmarkWeight >= 10)) {
    return { type: "多元配置型", subType: "", basis: `QDII/海外${qdii.toFixed(2)}%，海外基准${overseas.benchmarkWeight.toFixed(2)}%，或名称/标签含多资产、商品等多元配置特征。` };
  }
  if (equity < 1 && bondCash >= 80) {
    return { type: "纯债型", subType: "", basis: `权益${equity.toFixed(2)}%，债券+货币${bondCash.toFixed(2)}%，按研报纯债型可比池。` };
  }
  if (equity <= 20) {
    return { type: "固收+型", subType: "", basis: `权益${equity.toFixed(2)}%，按研报固收+型可比池。` };
  }
  if (equity < 70) {
    return { type: "股债混合型", subType: "", basis: `权益${equity.toFixed(2)}%，按研报股债混合型可比池。` };
  }
  if (mixed >= 20 || hasMultiAsset) {
    return { type: "多元配置型", subType: "", basis: `混合${mixed.toFixed(2)}%、QDII${qdii.toFixed(2)}%，按多元配置观察。` };
  }
  return { type: "股债混合型", subType: "", basis: "未命中更细资产规则，默认进入股债混合型观察。" };
}

function detailSupplementalText(detail) {
  const parts = [];
  const fieldLabelRe = /策略|合同|协议|服务|说明|概念|描述|标签|规则|投顾|条款/;
  const derivedFieldRe = /业务分类|业务组合分类|业务分类标签|业务分类依据|分类依据|风险分类依据|风险等级|披露风险等级|权益风险档|波动风险档|回撤风险档|风险触发指标|策略实现标签|天天/;
  const add = (value) => {
    const text = raw(value);
    if (text && text !== "未披露") parts.push(text);
  };
  for (const key of ["策略名称", "策略概念", "策略描述", "特殊标签", "披露策略类型", "标签"]) {
    add(detail?.summary?.[key]);
  }
  for (const item of [...(detail?.profileFields || []), ...(detail?.classificationFields || [])]) {
    const field = raw(item.字段);
    if (derivedFieldRe.test(field)) continue;
    if (fieldLabelRe.test(field)) add(`${field}=${item.值}`);
  }
  return parts.join(" ");
}

function buildSupplementalTextById() {
  const map = new Map();
  if (!fs.existsSync(detailsDir)) return map;
  for (const file of fs.readdirSync(detailsDir).filter((name) => name.endsWith(".js"))) {
    try {
      const detail = loadDetail(path.join(detailsDir, file));
      map.set(detail.id, detailSupplementalText(detail));
    } catch {
      // Ignore one broken detail file rather than blocking the whole export.
    }
  }
  return map;
}

function buildDetailSummaryById() {
  const map = new Map();
  if (!fs.existsSync(detailsDir)) return map;
  for (const file of fs.readdirSync(detailsDir).filter((name) => name.endsWith(".js"))) {
    try {
      const detail = loadDetail(path.join(detailsDir, file));
      const id = raw(detail?.id || detail?.summary?.统一策略ID);
      if (id) map.set(id, { ...(detail.summary || {}), 统一策略ID: id });
    } catch {
      // Keep the export resilient if one detail payload is malformed.
    }
  }
  return map;
}

function mergeStrategiesWithDetailSummaries(strategies, detailSummariesById) {
  const map = new Map();
  for (const row of strategies || []) {
    const id = raw(row?.统一策略ID);
    if (!id) continue;
    map.set(id, { ...(detailSummariesById.get(id) || {}), ...row, 统一策略ID: id });
  }
  for (const [id, row] of detailSummariesById.entries()) {
    if (!map.has(id)) map.set(id, { ...row, 统一策略ID: id });
  }
  return [...map.values()];
}

const cappedPercentageFields = [
  "权益基金权重",
  "债券基金权重",
  "货币基金权重",
  "混合基金权重",
  "QDII权重",
  "指数基金权重",
  "主动基金权重",
  "基准权益权重",
  "基准债券权重",
  "基准货币权重",
  "基准资产已映射权重",
  "基准资产未映射权重",
  "基准资产大类-权益",
  "基准资产大类-债券",
  "基准资产大类-其他",
  "基准资产类别-A股",
  "基准资产类别-港股",
  "基准资产类别-海外权益",
  "基准资产类别-债券",
  "基准资产类别-商品",
  "基准资产类别-现金",
  "基准资产类别-其他"
];

function normalizeOperatingStatus(value) {
  const text = raw(value).trim();
  if (!text) return "未披露";
  if (/终止|结束|下架/.test(text)) return "已终止";
  if (text === "公开披露" || text === "未披露" || text === "正常运作") return text;
  if (["listed", "原始状态1", "原始状态2", "开放窗口"].includes(text)) return "正常运作";
  return text;
}

function normalizeRowForDelivery(row) {
  row["运作状态"] = normalizeOperatingStatus(row["运作状态"]);
  for (const field of cappedPercentageFields) {
    const value = num(row[field]);
    if (value !== null && value > 100 && value <= 100.5) row[field] = 100;
  }
  return row;
}

function updateStrategyRow(row, supplementalText = "") {
  const disclosedRisk = raw(row["披露风险等级"]) || raw(row["原披露风险等级"]) || raw(row["风险等级"]);
  const risk = measuredRiskResult(row);
  const measuredRisk = raw(row["测算风险等级"]) || raw(row["风险基础分类"]) || risk.level;
  const disclosedType = raw(row["披露策略类型"]) || raw(row["策略类型"]);
  const legacyBenchmarkBucket = raw(firstField(row, LEGACY_BENCHMARK_BUCKET_FIELDS));
  const canonicalBenchmarkBucket = raw(row["基准风险资产权重"]);
  const benchmarkRiskWeight = clampPercentage(row["基准风险资产权重_百分比"] ?? firstField(row, LEGACY_RISK_WEIGHT_FIELDS));
  const unknownWeight = nz(row["基准资产未映射权重"] ?? row["基准未知权重"]);
  const benchmarkBucket = canonicalBenchmarkBucket || (unknownWeight <= 0.01 ? bucketFromPercentage(benchmarkRiskWeight) : "") || legacyBenchmarkBucket;
  const benchmarkBucketDescription = raw(row["基准风险资产权重说明"]) || raw(firstField(row, LEGACY_BENCHMARK_BUCKET_NOTE_FIELDS)) || (benchmarkBucket ? "基准风险资产权重按业绩基准中的权益、商品和另类风险资产合计权重划分。" : "基准风险资产权重不足或含未映射成分，暂不分档。");
  row["基准风险资产权重"] = benchmarkBucket || "";
  row["基准风险资产权重说明"] = benchmarkBucketDescription;
  row["基准风险资产权重_百分比"] = benchmarkRiskWeight;
  Object.assign(row, strategyCompositeAttributes(row));
  const canonical = canonicalBusiness({ ...row, 风险等级: measuredRisk, 披露策略类型: disclosedType, 补充识别文本: supplementalText });
  const business = canonical.business;
  const report = reportClassification({ ...row, 风险等级: measuredRisk, 业务分类: business, 披露策略类型: disclosedType, 补充识别文本: supplementalText });

  row["披露风险等级"] = disclosedRisk || "未披露";
  row["风险等级"] = measuredRisk;
  row["权益风险档"] = risk.eqLevel;
  row["波动风险档"] = risk.volLevel;
  row["回撤风险档"] = risk.mddLevel;
  row["风险触发指标"] = risk.trigger;
  row["风险分类依据"] = risk.basis;
  row["披露策略类型"] = disclosedType || "未披露";
  row["业务分类"] = business;
  row["研报产品类型"] = report.type;
  row["研报股票子类型"] = report.subType || "";
  row["研报分类依据"] = report.basis;
  row["业务组合分类"] = `${measuredRisk}｜${business}`;
  row["业务分类依据"] = canonical.basis;
  row["基准风险资产权重"] = benchmarkBucket || "";
  row["配置风格标签"] = [benchmarkBucket, row["业务分类"], row["市场地域"], row["主动被动"]].map(raw).filter(Boolean).join("｜");
  refreshOverseasRegionAndLabels(row);
  row.searchText = [
    row["统一策略ID"],
    row["策略代码"],
    row["策略名称"],
    row["渠道"],
    row["投顾机构"],
    measuredRisk,
    row["披露风险等级"],
    row["披露策略类型"],
    business,
    row["研报产品类型"],
    row["研报股票子类型"],
    row["业务组合分类"],
    row["市场地域"],
    row["主动被动"],
    row["基准风险资产权重"],
    row["权益中枢"],
    row["基准风险资产中枢"],
    row["配置风格标签"],
    row["基准可用状态"],
    row["业绩基准说明"],
    row["业绩基准"],
    row["业务分类标签"],
    row["天天展示状态"]
  ].map(raw).filter(Boolean).join(" ");

  delete row["原披露风险等级"];
  delete row["策略类型"];
  delete row["主可比池"];
  delete row["测算风险等级"];
  delete row["风险基础分类"];
  delete row["基础分类"];
  delete row["业务主分类"];
  [...LEGACY_BENCHMARK_BUCKET_FIELDS, ...LEGACY_BENCHMARK_BUCKET_NOTE_FIELDS, ...LEGACY_RISK_WEIGHT_FIELDS].forEach((field) => delete row[field]);
  for (const [field, value] of Object.entries(row)) {
    row[field] = cleanTerminology(value);
  }
  normalizeRowForDelivery(row);
  return row;
}

function renameFieldItems(rows) {
  if (!Array.isArray(rows)) return rows;
  for (const item of rows) {
    if (item.字段 === "策略类型") item.字段 = "披露策略类型";
    else if (item.字段 === "原披露风险等级") item.字段 = "披露风险等级";
    else if (item.字段 === "测算风险等级" || item.字段 === "风险基础分类") item.字段 = "风险等级";
    else if (item.字段 === "业务主分类") item.字段 = "业务分类";
    else if (LEGACY_BENCHMARK_BUCKET_FIELDS.includes(item.字段)) item.字段 = "基准风险资产权重";
    else if (LEGACY_BENCHMARK_BUCKET_NOTE_FIELDS.includes(item.字段)) item.字段 = "基准风险资产权重说明";
    else if (LEGACY_RISK_WEIGHT_FIELDS.includes(item.字段)) item.字段 = "基准风险资产权重_百分比";
    item.值 = cleanTerminology(item.值);
  }
  return rows;
}

function renameProfileFieldItems(rows) {
  if (!Array.isArray(rows)) return rows;
  for (const item of rows) {
    if (item.字段 === "风险等级") item.字段 = "披露风险等级";
    item.值 = cleanTerminology(item.值);
  }
  return rows;
}

function dedupeFieldItems(rows) {
  const map = new Map();
  for (const item of rows || []) {
    if (!item || !item.字段) continue;
    const existing = map.get(item.字段);
    if (!existing || existing.值 === "未披露" || existing.值 === "" || existing.值 == null) {
      map.set(item.字段, item);
    }
  }
  return [...map.values()];
}

function upsertField(rows, field, value) {
  const arr = Array.isArray(rows) ? rows : [];
  const existing = arr.find((item) => item.字段 === field);
  if (existing) existing.值 = value;
  else arr.push({ 字段: field, 值: value });
  return arr;
}

function inferAssetType(name, existing, context = "") {
  const fundText = raw(name);
  const existingText = raw(existing);
  const contextText = raw(context);
  const directText = `${existingText} ${fundText}`;
  const allText = `${directText} ${contextText}`;
  const hasFundIdentity = Boolean(fundText && !/^\d{6}$/.test(fundText));
  const useContext = !hasFundIdentity;
  const overseasDebt = /海外债|美元债|亚洲债|全球债|QDII债|债券.*QDII|QDII.*债|中资美元债|离岸债/.test(directText);
  const domesticDebt = /中债|国开债|农发债|政策性金融债|金融债|信用债|利率债|同业存单|存单|债券指数|债券ETF|债券型指数|债券基金|短债|中短债|超短债|纯债|固收|票息|添利|永利|债|转债|可转债/.test(directText);
  if (/货币|现金|现金管理|保证金|活期|天天红|活钱|日鑫|日日鑫|日日薪|日日丰|聚财宝|安鑫宝/.test(directText)) return "货币型";
  if (/白银|原油|油气|黄金|商品|期货/.test(directText)) return "商品型";
  if (domesticDebt && !overseasDebt) return "债券型";
  if (/QDII|海外|全球|亚洲|美元|纳斯达克|纳指|标普|恒生|港股|日经|越南|印度|德国|美国/.test(directText) || (useContext && /QDII|海外|全球|亚洲|美元|纳斯达克|纳指|标普|恒生|港股|日经|越南|印度|德国|美国/.test(contextText))) return "QDII/海外";
  if (/ETF|指数|联接|沪深|中证|创业板|科创|红利|宽基|增强|LOF|MSCI|国证|大数据100|食品饮料|酒|中华交易服务|高股息|港股通/.test(directText) || (useContext && /ETF|指数|联接|沪深|中证|创业板|科创|红利|宽基|增强|LOF|MSCI|国证|大数据100|食品饮料|酒|中华交易服务|高股息|港股通/.test(contextText))) return "指数型";
  if (/债|短债|中短债|纯债|固收|票息|添利|永利|利率|信用|转债|强化收益|稳祥|稳鸿|鸿利|双元|和元|丰元|中高等级|双轮动/.test(directText) || (useContext && /债|短债|中短债|纯债|固收|票息|添利|永利|利率|信用|转债|强化收益|稳祥|稳鸿|鸿利|双元|和元|丰元|中高等级|双轮动/.test(contextText))) return "债券型";
  if (/股票|权益|成长|价值|消费|医药|新能源|科技|半导体|军工|AI|人工智能|先进制造|港股互联网|创新动力|优加生活|优享生活/.test(allText)) return "股票型";
  if (/混合|灵活|配置|偏股|多资产|目标日期|养老|精选|优选|平衡|量化|多元|全天候|牛基|价值精选|股债平衡|幸福增长|智享自由|进取|利安|荣光|信睿|新兴蓝筹|新鑫先锋|新收益|沪港深裕鑫|稳健增长|动态策略|金鹏蓝筹|利鑫|招泰|行业景气|荣尊|安裕回报|宏观策略/.test(allText)) return "混合型";
  return existingText && !/未披露|未识别|其他|^[a-z]$|^[0-9]$/.test(existingText) ? existingText : "混合型";
}

const equityIndustryRules = [
  { theme: "医药生物", group: "消费医药", pattern: /医药|医疗|创新药|生物|健康|中药|药/ },
  { theme: "电力设备/新能源", group: "科技制造", pattern: /新能源|光伏|电池|储能|电力设备|低碳|环保|碳中和|绿色/ },
  { theme: "电子/半导体", group: "科技制造", pattern: /半导体|芯片|电子|集成电路/ },
  { theme: "计算机/人工智能", group: "科技制造", pattern: /AI|人工智能|计算机|软件|数字|信创|云计算|大数据|信息技术/ },
  { theme: "通信", group: "科技制造", pattern: /通信|5G/ },
  { theme: "传媒/互联网", group: "科技制造", pattern: /互联网|传媒|游戏|文化|文娱|内容|TMT/ },
  { theme: "食品饮料", group: "消费医药", pattern: /食品|饮料|白酒|酒/ },
  { theme: "消费服务", group: "消费医药", pattern: /消费|家电|旅游|酒店|商贸|零售|农业|农林牧渔|养殖|畜牧/ },
  { theme: "国防军工", group: "科技制造", pattern: /军工|国防|航天|航空/ },
  { theme: "金融地产", group: "金融周期/价值", pattern: /金融|证券|银行|保险|地产|房地产/ },
  { theme: "周期资源", group: "金融周期/价值", pattern: /周期|有色|煤炭|钢铁|化工|材料|资源|稀土|石油/ },
  { theme: "高端制造", group: "科技制造", pattern: /高端制造|先进制造|智能制造|机器人|工业|装备|机械|制造/ },
  { theme: "汽车", group: "科技制造", pattern: /汽车|智能车|新能源汽车/ }
];

function inferIndustryTheme(name, assetType) {
  const text = `${raw(name)} ${raw(assetType)}`;
  if (/货币|现金|活期|日鑫|日日鑫|日日薪|日日丰|聚财宝|安鑫宝/.test(text) || assetType === "货币型") return "现金管理";
  if (/转债|可转债/.test(text)) return "可转债";
  if (/短债|中短债|超短债/.test(text)) return "短债/中短债";
  if (/债|纯债|信用|利率|票息|固收/.test(text) || assetType === "债券型") return "纯债/固收";
  if (/黄金|白银|贵金属/.test(text)) return "贵金属";
  if (/原油|油气|能源商品/.test(text)) return "能源商品";
  if (/商品|期货/.test(text) || assetType === "商品型") return "商品基金";
  if (/港股.*互联网|恒生.*科技|互联网.*港股|科技.*港股/.test(text)) return "港股/海外科技";
  if (/港股|恒生|港股通/.test(text)) return "港股市场";
  if (/纳斯达克|纳指|标普|美国|美股/.test(text)) return "美股市场";
  if (/QDII|海外|全球|越南|印度|德国|日本|亚洲/.test(text) || assetType === "QDII/海外") return "海外区域/全球";

  const matches = equityIndustryRules.filter((rule) => rule.pattern.test(text));
  if (matches.length === 1) return matches[0].theme;
  if (matches.length > 1) return "跨行业/多主题权益";
  if (/红利|低波|高股息|价值|央企|国企/.test(text)) return "红利价值/央国企";
  if (/ETF|指数|联接|沪深|中证|创业板|科创|上证|深证|宽基|增强|LOF|MSCI|国证|A500|100|300|500|1000|2000/.test(text) || assetType === "指数型") return "宽基指数";
  if (assetType === "股票型" || assetType === "混合型") return "主动权益/均衡";
  return "主动权益/均衡";
}

function inferIndustryGroup(theme, assetType) {
  const text = `${raw(theme)} ${raw(assetType)}`;
  if (/现金/.test(text)) return "现金";
  if (/债|固收/.test(text)) return "固收";
  if (/贵金属|能源商品|商品/.test(text)) return "商品";
  if (/海外|港股|美股|全球/.test(text)) return "海外";
  const matched = equityIndustryRules.find((rule) => rule.theme === theme);
  if (matched) return matched.group;
  if (/红利|价值|央国企/.test(text)) return "金融周期/价值";
  if (/宽基|主动权益|均衡|跨行业/.test(text)) return "权益宽基/均衡";
  return "权益宽基/均衡";
}

function inferEquityIndustryTheme(theme) {
  const value = raw(theme);
  if (!value || /现金|债|固收|转债|商品|贵金属|能源商品/.test(value)) return "";
  if (/海外区域|港股市场|美股市场/.test(value)) return "海外宽基/区域";
  if (/宽基指数|主动权益/.test(value)) return "宽基/主动权益";
  return value;
}

function inferEquityIndustryGroup(theme) {
  const value = raw(theme);
  if (!value) return "";
  if (/海外/.test(value)) return "海外权益";
  if (/宽基|主动权益/.test(value)) return "宽基/主动权益";
  const matched = equityIndustryRules.find((rule) => rule.theme === value);
  if (matched) return matched.group;
  if (/红利|价值|央国企|金融|周期/.test(value)) return "金融周期/价值";
  if (/跨行业/.test(value)) return "跨行业/多主题";
  return "跨行业/多主题";
}

function inferReportAssetClass(fundName, assetType, industryTheme) {
  const text = `${raw(fundName)} ${raw(assetType)} ${raw(industryTheme)}`;
  if (/货币|现金|活期|日鑫|日日|聚财宝|安鑫宝/.test(text) || assetType === "货币型") return "货币及现金";
  if (/海外债|美元债|亚洲债|全球债|QDII债/.test(text)) return "海外债券";
  if (/债|短债|纯债|中债|固收|票息|信用|利率/.test(text) || assetType === "债券型") return "债券";
  if (/黄金|白银|贵金属/.test(text)) return "黄金";
  if (/原油|油气|商品|期货|能源商品/.test(text) || assetType === "商品型") return "其他商品";
  if (/REIT|房地产信托/.test(text)) return "海外REIT";
  if (/港股|恒生|H股|港股通/.test(text)) return "港股";
  if (/纳斯达克|纳指|标普|美股|美国|S&P|NASDAQ/.test(text)) return "美股";
  if (/越南|印度|巴西|新兴市场|东盟|亚洲精选/.test(text)) return "新兴市场";
  if (/日本|德国|欧洲|发达市场|全球|海外|QDII/.test(text) || assetType === "QDII/海外") return "其他发达市场";
  if (/股票|指数|ETF|联接|混合|权益|行业|主题|宽基|沪深|中证|创业板|科创/.test(text) || ["股票型", "指数型", "混合型"].includes(assetType)) return "A股";
  return "待核验";
}

function inferReportAIndustry(reportAssetClass, equityIndustryTheme, industryTheme) {
  if (reportAssetClass !== "A股") return "";
  const value = raw(equityIndustryTheme) || raw(industryTheme);
  if (!value || /宽基|主动权益|均衡|跨行业|海外|现金|债|固收|商品|黄金/.test(value)) return "";
  const rules = [
    ["电子", /电子|半导体|芯片/],
    ["基础化工", /化工|材料|周期资源/],
    ["通信", /通信|5G/],
    ["煤炭", /煤炭/],
    ["电力设备", /电力设备|新能源|光伏|电池|储能|低碳|碳中和/],
    ["公用事业", /公用事业|环保/],
    ["交通运输", /交通运输/],
    ["机械设备", /机械|高端制造|先进制造|装备|机器人|制造/],
    ["钢铁", /钢铁/],
    ["纺织服饰", /纺织|服饰/],
    ["建筑材料", /建筑材料/],
    ["商贸零售", /商贸|零售/],
    ["农林牧渔", /农业|农林牧渔|养殖|畜牧/],
    ["建筑装饰", /建筑装饰/],
    ["有色金属", /有色|稀土|金属/],
    ["国防军工", /军工|国防|航天|航空/],
    ["美容护理", /美容/],
    ["社会服务", /旅游|酒店|社会服务/],
    ["银行", /银行/],
    ["汽车", /汽车|智能车/],
    ["医药生物", /医药|医疗|生物|创新药|中药/],
    ["房地产", /房地产|地产/],
    ["石油石化", /石油|石化/],
    ["非银金融", /证券|保险|金融/],
    ["传媒", /传媒|互联网|游戏|文化|TMT/],
    ["计算机", /计算机|人工智能|AI|软件|数字|信创|云计算|信息技术/],
    ["食品饮料", /食品|饮料|白酒|酒/],
    ["家用电器", /家电|家用电器/]
  ];
  return rules.find(([, pattern]) => pattern.test(value))?.[0] || "";
}

const standardIndustryRules = [
  { industry: "电子", theme: "电子/半导体", group: "科技制造", pattern: /电子|半导体|芯片|集成电路|光刻|消费电子/ },
  { industry: "计算机", theme: "计算机/人工智能", group: "科技制造", pattern: /计算机|人工智能|AI|软件|数字|信创|云计算|大数据|信息技术|数据要素|算力/ },
  { industry: "通信", theme: "通信", group: "科技制造", pattern: /通信|5G|6G|光通信|卫星互联网/ },
  { industry: "传媒", theme: "传媒/互联网", group: "科技制造", pattern: /传媒|互联网|游戏|文化|文娱|内容|TMT|动漫|影视/ },
  { industry: "电力设备", theme: "电力设备/新能源", group: "科技制造", pattern: /新能源|光伏|电池|储能|电力设备|低碳|碳中和|绿色电力|风电|锂电/ },
  { industry: "机械设备", theme: "高端制造", group: "科技制造", pattern: /机械|高端制造|先进制造|智能制造|机器人|工业母机|装备|制造/ },
  { industry: "汽车", theme: "汽车", group: "科技制造", pattern: /汽车|智能车|新能源汽车|车联网|整车|零部件/ },
  { industry: "国防军工", theme: "国防军工", group: "科技制造", pattern: /军工|国防|航天|航空|北斗|军民融合/ },
  { industry: "医药生物", theme: "医药生物", group: "消费医药", pattern: /医药|医疗|创新药|生物|健康|中药|药|疫苗|CXO/ },
  { industry: "食品饮料", theme: "食品饮料", group: "消费医药", pattern: /食品|饮料|白酒|酒|消费龙头/ },
  { industry: "家用电器", theme: "消费服务", group: "消费医药", pattern: /家电|家用电器/ },
  { industry: "商贸零售", theme: "消费服务", group: "消费医药", pattern: /商贸|零售|电商/ },
  { industry: "社会服务", theme: "消费服务", group: "消费医药", pattern: /旅游|酒店|社会服务|教育|免税/ },
  { industry: "农林牧渔", theme: "消费服务", group: "消费医药", pattern: /农业|农林牧渔|养殖|畜牧|种业/ },
  { industry: "银行", theme: "金融地产", group: "金融周期/价值", pattern: /银行/ },
  { industry: "非银金融", theme: "金融地产", group: "金融周期/价值", pattern: /证券|保险|券商|非银|金融科技/ },
  { industry: "房地产", theme: "金融地产", group: "金融周期/价值", pattern: /房地产|地产|物业/ },
  { industry: "有色金属", theme: "周期资源", group: "金融周期/价值", pattern: /有色|稀土|金属|铜|铝|锂|钴/ },
  { industry: "基础化工", theme: "周期资源", group: "金融周期/价值", pattern: /化工|材料|新材料/ },
  { industry: "钢铁", theme: "周期资源", group: "金融周期/价值", pattern: /钢铁/ },
  { industry: "煤炭", theme: "周期资源", group: "金融周期/价值", pattern: /煤炭|煤/ },
  { industry: "石油石化", theme: "周期资源", group: "金融周期/价值", pattern: /石油|石化|油气/ },
  { industry: "公用事业", theme: "周期资源", group: "金融周期/价值", pattern: /公用事业|环保|电力|水务|燃气/ },
  { industry: "交通运输", theme: "周期资源", group: "金融周期/价值", pattern: /交通运输|物流|航运|航空运输|港口|高速/ },
  { industry: "建筑材料", theme: "周期资源", group: "金融周期/价值", pattern: /建筑材料|建材|水泥|玻璃/ },
  { industry: "建筑装饰", theme: "周期资源", group: "金融周期/价值", pattern: /建筑装饰|建筑工程|基建/ },
  { industry: "纺织服饰", theme: "消费服务", group: "消费医药", pattern: /纺织|服饰|服装/ },
  { industry: "美容护理", theme: "消费服务", group: "消费医药", pattern: /美容|护理|化妆品/ }
];

const reportAssetThemeMap = {
  "货币及现金": { theme: "现金管理", group: "现金" },
  "债券": { theme: "纯债/固收", group: "固收" },
  "海外债券": { theme: "海外债券", group: "海外固收" },
  "黄金": { theme: "贵金属", group: "商品" },
  "其他商品": { theme: "能源/商品", group: "商品" },
  "海外REIT": { theme: "海外REIT", group: "海外" },
  "港股": { theme: "港股市场", group: "海外" },
  "美股": { theme: "美股市场", group: "海外" },
  "新兴市场": { theme: "海外区域/全球", group: "海外" },
  "其他发达市场": { theme: "海外区域/全球", group: "海外" }
};

const reportAssetOrder = ["A股", "港股", "美股", "债券", "货币及现金", "黄金", "海外债券", "新兴市场", "其他发达市场", "海外REIT", "其他商品"];
const reportAssetSet = new Set(reportAssetOrder);

function reportAssetRank(asset) {
  const index = reportAssetOrder.indexOf(raw(asset));
  return index >= 0 ? index : reportAssetOrder.length;
}

function sortedReportAssetText(values) {
  const unique = [...new Set(Array.from(values || []).map(raw).filter(Boolean))];
  return unique.sort((a, b) => reportAssetRank(a) - reportAssetRank(b) || a.localeCompare(b, "zh-CN")).join("、");
}

function fallbackReportAssetText(value) {
  const text = raw(value);
  if (!text) return "";
  const tokens = text.split(/[,\s，、/]+/).map(raw).filter(Boolean);
  const assets = [];
  for (const token of tokens) {
    if (reportAssetSet.has(token)) assets.push(token);
    else if (/货币|现金/.test(token)) assets.push("货币及现金");
    else if (/债|固收|利率|信用/.test(token)) assets.push("债券");
    else if (/港股|恒生/.test(token)) assets.push("港股");
    else if (/美股|纳斯达克|标普/.test(token)) assets.push("美股");
    else if (/黄金|贵金属/.test(token)) assets.push("黄金");
    else if (/商品|原油|能源/.test(token)) assets.push("其他商品");
    else if (/REIT/.test(token)) assets.push("海外REIT");
    else if (/海外|全球|QDII/.test(token)) assets.push("其他发达市场");
    else if (/A股|权益|股票|混合|指数|宽基|行业|主题/.test(token)) assets.push("A股");
  }
  if (assets.length) return sortedReportAssetText(assets);
  return tokens.length && tokens.every((token) => /^[0-9a-z]+$/i.test(token)) ? "未识别资产" : text;
}

function pointText(value) {
  const n = num(value);
  if (n === null) return "未披露";
  const abs = Math.abs(n);
  const digits = abs >= 10 ? 1 : 2;
  return `${n > 0 ? "+" : ""}${Number(n.toFixed(digits))}点`;
}

function addMove(map, key, value) {
  const label = raw(key);
  const n = nz(value);
  if (!label || label === "待核验" || Math.abs(n) <= 0.0001) return;
  map.set(label, (map.get(label) || 0) + n);
}

function topMoves(map, positive, limit = 3) {
  return [...(map || new Map()).entries()]
    .filter(([, value]) => positive ? value > 0.0001 : value < -0.0001)
    .sort((a, b) => Math.abs(b[1]) - Math.abs(a[1]))
    .slice(0, limit)
    .map(([label, value]) => `${label}${pointText(value)}`);
}

function topFundMoves(rows, positive, limit = 3) {
  return [...(rows || [])]
    .filter((row) => positive ? nz(row.权重变化) > 0.0001 : nz(row.权重变化) < -0.0001)
    .sort((a, b) => Math.abs(nz(b.权重变化)) - Math.abs(nz(a.权重变化)))
    .slice(0, limit)
    .map((row) => `${raw(row.基金名称 || row.基金代码 || "未命名基金")}${pointText(row.权重变化)}`);
}

function makeRebalanceResearchAgg() {
  return {
    assets: new Map(),
    industries: new Map(),
    funds: []
  };
}

function addRebalanceResearchMove(agg, row, fundClass) {
  if (!agg) return;
  const change = nz(row.权重变化);
  if (Math.abs(change) <= 0.0001) return;
  agg.funds.push({
    基金代码: row.基金代码,
    基金名称: row.基金名称,
    权重变化: change
  });
  for (const item of categoryExposureRows(fundClass)) {
    const scaled = change * item.share / 100;
    if (item.field === "研报大类资产") addMove(agg.assets, item.category, scaled);
    if (item.field === "权益行业主题" || item.field === "研报A股行业") addMove(agg.industries, item.category, scaled);
  }
}

function buildRebalanceResearchSummary(snap, event, agg) {
  if (!agg) return "";
  const addAssets = topMoves(agg.assets, true);
  const reduceAssets = topMoves(agg.assets, false);
  const addIndustries = topMoves(agg.industries, true);
  const reduceIndustries = topMoves(agg.industries, false);
  const addFunds = topFundMoves(agg.funds, true);
  const reduceFunds = topFundMoves(agg.funds, false);
  const parts = [];
  if (addAssets.length || reduceAssets.length) {
    parts.push(`资产层面：${addAssets.length ? `增配${addAssets.join("、")}` : ""}${addAssets.length && reduceAssets.length ? "；" : ""}${reduceAssets.length ? `减配${reduceAssets.join("、")}` : ""}。`);
  }
  if (addIndustries.length || reduceIndustries.length) {
    parts.push(`行业层面：${addIndustries.length ? `增配${addIndustries.join("、")}` : ""}${addIndustries.length && reduceIndustries.length ? "；" : ""}${reduceIndustries.length ? `减配${reduceIndustries.join("、")}` : ""}。`);
  }
  if (addFunds.length || reduceFunds.length) {
    parts.push(`基金层面：${addFunds.length ? `主要调增${addFunds.join("、")}` : ""}${addFunds.length && reduceFunds.length ? "；" : ""}${reduceFunds.length ? `主要调减${reduceFunds.join("、")}` : ""}。`);
  }
  const logic = raw(event?.调仓逻辑 || snap?.调仓逻辑);
  const reason = raw(event?.调仓原因 || snap?.调仓原因);
  if (logic || reason) {
    parts.push(`综合判断：${logic ? `本次更接近“${logic}”` : "本次调仓"}${reason ? `，与披露原因“${reason.slice(0, 80)}${reason.length > 80 ? "..." : ""}”相互印证` : ""}。`);
  }
  return parts.join("");
}

function addExposure(exposure, key, share) {
  const label = raw(key);
  const value = Number(share);
  if (!label || !Number.isFinite(value) || Math.abs(value) < 0.0001) return;
  exposure[label] = (exposure[label] || 0) + value;
}

function normalizeExposure(exposure) {
  const entries = Object.entries(exposure || {}).filter(([, value]) => Number.isFinite(value) && Math.abs(value) > 0.0001);
  const total = entries.reduce((acc, [, value]) => acc + value, 0);
  if (!total) return {};
  const scaled = {};
  for (const [key, value] of entries) scaled[key] = Number((value * 100 / total).toFixed(4));
  return scaled;
}

function exposureText(exposure) {
  const entries = Object.entries(exposure || {}).filter(([, value]) => Number.isFinite(value) && Math.abs(value) > 0.0001);
  return entries.sort((a, b) => b[1] - a[1]).map(([key, value]) => `${key}${Number(value).toFixed(value % 1 ? 1 : 0)}%`).join("、");
}

function equityAssetShareFromExposure(assetExposure) {
  return Object.entries(assetExposure || {}).reduce((acc, [asset, share]) => {
    if (/A股|港股|美股|新兴市场|其他发达市场|海外权益|存托凭证|REIT/.test(raw(asset))) {
      return acc + (Number(share) || 0);
    }
    return acc;
  }, 0);
}

function absoluteIndustryExposure(assetExposure, industryExposure) {
  const equityShare = equityAssetShareFromExposure(assetExposure);
  if (!equityShare || !Object.keys(industryExposure || {}).length) return {};
  const out = {};
  for (const [industry, share] of Object.entries(industryExposure || {})) {
    const value = equityShare * (Number(share) || 0) / 100;
    if (Math.abs(value) > 0.0001) out[industry] = Number(value.toFixed(4));
  }
  return out;
}

function primaryExposure(exposure, fallback = "") {
  return Object.entries(exposure || {}).sort((a, b) => b[1] - a[1])[0]?.[0] || fallback;
}

function industryMatches(text) {
  const value = raw(text);
  return standardIndustryRules.filter((rule) => rule.pattern.test(value));
}

function industryMeta(industry) {
  return standardIndustryRules.find((rule) => rule.industry === industry) || {};
}

function thematicIndustryExposure(text, matches) {
  const value = raw(text);
  const exposure = {};
  const add = (industry, share) => addExposure(exposure, industry, share);
  if (/TMT/.test(value)) {
    add("电子", 25); add("计算机", 25); add("通信", 20); add("传媒", 30);
    return normalizeExposure(exposure);
  }
  if (/人工智能|AI|算力/.test(value) && /传媒|游戏|互联网|TMT/.test(value)) {
    add("计算机", 45); add("传媒", 30); add("电子", 25);
    return normalizeExposure(exposure);
  }
  if (/金融科技|互联网金融/.test(value)) {
    add("计算机", 70); add("非银金融", 30);
    return normalizeExposure(exposure);
  }
  if (/新能源汽车|智能车|车联网/.test(value)) {
    add("汽车", 55); add("电力设备", 30); add("电子", 15);
    return normalizeExposure(exposure);
  }
  if (/低碳|碳中和|绿色/.test(value) && /环保|公用事业|电力/.test(value)) {
    add("电力设备", 50); add("公用事业", 30); add("基础化工", 20);
    return normalizeExposure(exposure);
  }
  if (!matches.length) return {};
  const each = 100 / matches.length;
  for (const match of matches) add(match.industry, each);
  return normalizeExposure(exposure);
}

function themeExposureFromIndustry(industryExposure) {
  const exposure = {};
  for (const [industry, share] of Object.entries(industryExposure || {})) {
    const meta = industryMeta(industry);
    addExposure(exposure, meta.theme || industry, share);
  }
  return normalizeExposure(exposure);
}

function groupExposureFromTheme(themeExposure) {
  const exposure = {};
  for (const [theme, share] of Object.entries(themeExposure || {})) {
    const matched = standardIndustryRules.find((rule) => rule.theme === theme);
    const group = matched?.group || inferEquityIndustryGroup(theme) || inferIndustryGroup(theme, "");
    addExposure(exposure, group, share);
  }
  return normalizeExposure(exposure);
}

function classifyFundStandard(fundCode, fundNameInput, holding = {}, context = "") {
  const meta = manualFundMeta[fundCode] || manualFundMeta[raw(fundNameInput)] || {};
  const inputName = raw(fundNameInput);
  const fundName = /^\d{6}$/.test(inputName) && meta.name ? meta.name : inputName;
  const platformType = raw(holding.资产类型 || holding.分组);
  const text = `${fundName} ${raw(meta.type)} ${platformType} ${raw(context)}`;
  const directText = `${fundName} ${raw(meta.type)} ${platformType}`;
  const matches = industryMatches(text);
  const hasManual = Object.keys(meta).length > 0;
  const hasIndex = /ETF|指数|联接|LOF|增强|沪深|中证|上证|深证|创业板|科创|MSCI|国证|A500|100|300|500|1000|2000|红利|高股息/.test(text);
  const isIndustryFund = matches.length > 0 || /行业|主题|赛道|产业/.test(text);
  let fundType = inferAssetType(fundName, meta.type || holding.资产类型 || holding.分组, text);
  const evidence = [];
  let confidence = hasManual ? "A 基金代码/名称字典" : (platformType ? "B 平台持仓分类+名称规则" : "C 名称关键词规则");
  if (hasManual) evidence.push("命中基金代码/名称字典");
  if (platformType) evidence.push(`持仓披露分类=${platformType}`);

  if (/货币|现金|现金管理|保证金|活期|天天红|活钱|日鑫|日日鑫|日日薪|日日丰|聚财宝|安鑫宝/.test(directText)) {
    fundType = "货币型";
    evidence.push("名称/分类命中货币或现金管理");
  } else if (/可转债|转债/.test(directText)) {
    fundType = "可转债";
    evidence.push("名称/分类命中可转债");
  } else if (/短债|中短债|超短债|30天|60天|90天|添利|现金增利|同业存单|存单/.test(directText)) {
    fundType = "短债/中短债";
    evidence.push("名称/分类命中短债、中短债、存单或短期限理财债");
  } else if (/海外债|美元债|亚洲债|全球债|QDII债|中资美元债|离岸债/.test(directText)) {
    fundType = "海外债券";
    evidence.push("名称/分类命中海外债券");
  } else if (/债|纯债|信用债|利率债|中债|国开债|农发债|政策性金融债|金融债|债券基金|票息|永利|固收/.test(directText)) {
    fundType = hasIndex ? "债券指数" : "纯债/信用债";
    evidence.push(hasIndex ? "名称命中债券指数/ETF" : "名称/分类命中债券或固收");
  } else if (/黄金|白银|贵金属/.test(directText)) {
    fundType = "黄金/贵金属";
    evidence.push("名称命中黄金/贵金属");
  } else if (/原油|油气|商品|期货|能源商品/.test(directText)) {
    fundType = "商品/能源";
    evidence.push("名称命中商品或能源");
  } else if (/REIT|房地产信托/.test(directText)) {
    fundType = "REIT";
    evidence.push("名称命中REIT");
  } else if (/沪港深/.test(text)) {
    fundType = "沪港深权益";
    evidence.push("名称命中沪港深");
  } else if (/港股|恒生|H股|港股通/.test(text)) {
    fundType = hasIndex ? "港股指数" : "港股权益";
    evidence.push(hasIndex ? "名称命中港股指数/ETF" : "名称命中港股权益");
  } else if (/纳斯达克|纳指|标普|美股|美国|S&P|NASDAQ/.test(text)) {
    fundType = hasIndex ? "美股指数" : "美股权益";
    evidence.push(hasIndex ? "名称命中美股指数/ETF" : "名称命中美股权益");
  } else if (/QDII|海外|全球|越南|印度|德国|日本|亚洲|欧洲|发达市场|新兴市场/.test(text)) {
    fundType = "其他海外权益";
    evidence.push("名称/分类命中QDII或海外区域");
  } else if (hasIndex) {
    fundType = isIndustryFund ? "行业/主题指数" : "宽基指数";
    evidence.push(isIndustryFund ? "名称命中指数且带行业/主题" : "名称命中宽基指数/ETF/联接");
  } else if (isIndustryFund) {
    fundType = "行业/主题主动";
    evidence.push("名称命中行业/主题关键词");
  } else if (/股票|权益|成长|价值|消费|医药|新能源|科技|半导体|军工|AI|人工智能|先进制造/.test(text)) {
    fundType = "主动权益";
    evidence.push("名称/分类命中股票或权益");
  } else if (/FOF|养老|目标日期|生命周期/.test(text)) {
    fundType = "FOF/养老";
    evidence.push("名称命中FOF、养老或目标日期");
  } else if (/混合|灵活|配置|平衡|多资产|多元|全天候|宏观策略/.test(text) || /混合型/.test(raw(meta.type))) {
    fundType = /稳健|安泰|安鑫|保本|回报|双禧|宝利|瑞利|润利|宏利|腾利|鑫|利安|荣光|招泰|固收|债/.test(text) ? "固收+/偏债混合" : "混合偏股";
    evidence.push(fundType === "固收+/偏债混合" ? "混合基金名称偏稳健/回报/固收" : "混合基金名称偏权益或均衡配置");
  } else if (!hasManual && !platformType) {
    confidence = "D 待补标准档案";
    evidence.push("缺少基金标准档案和平台分类，使用兜底规则");
  }

  const assetExposure = {};
  switch (fundType) {
    case "货币型":
      addExposure(assetExposure, "货币及现金", 100);
      break;
    case "短债/中短债":
      addExposure(assetExposure, "债券", 85);
      addExposure(assetExposure, "货币及现金", 15);
      break;
    case "纯债/信用债":
    case "债券指数":
      addExposure(assetExposure, "债券", 95);
      addExposure(assetExposure, "货币及现金", 5);
      break;
    case "海外债券":
      addExposure(assetExposure, "海外债券", 95);
      addExposure(assetExposure, "货币及现金", 5);
      break;
    case "可转债":
      addExposure(assetExposure, "债券", 70);
      addExposure(assetExposure, "A股", 25);
      addExposure(assetExposure, "货币及现金", 5);
      break;
    case "固收+/偏债混合":
      addExposure(assetExposure, "债券", 70);
      addExposure(assetExposure, "A股", 25);
      addExposure(assetExposure, "货币及现金", 5);
      break;
    case "混合偏股":
      addExposure(assetExposure, "A股", 65);
      addExposure(assetExposure, "债券", 25);
      addExposure(assetExposure, "货币及现金", 10);
      break;
    case "FOF/养老":
      addExposure(assetExposure, "A股", 45);
      addExposure(assetExposure, "债券", 45);
      addExposure(assetExposure, "货币及现金", 10);
      break;
    case "沪港深权益":
      addExposure(assetExposure, "A股", 55);
      addExposure(assetExposure, "港股", 40);
      addExposure(assetExposure, "货币及现金", 5);
      break;
    case "港股指数":
    case "港股权益":
      addExposure(assetExposure, "港股", 95);
      addExposure(assetExposure, "货币及现金", 5);
      break;
    case "美股指数":
    case "美股权益":
      addExposure(assetExposure, "美股", 95);
      addExposure(assetExposure, "货币及现金", 5);
      break;
    case "其他海外权益":
      addExposure(assetExposure, /越南|印度|巴西|新兴市场|东盟|亚洲/.test(text) ? "新兴市场" : "其他发达市场", 95);
      addExposure(assetExposure, "货币及现金", 5);
      break;
    case "黄金/贵金属":
      addExposure(assetExposure, "黄金", 95);
      addExposure(assetExposure, "货币及现金", 5);
      break;
    case "商品/能源":
      addExposure(assetExposure, "其他商品", 95);
      addExposure(assetExposure, "货币及现金", 5);
      break;
    case "REIT":
      addExposure(assetExposure, "海外REIT", 95);
      addExposure(assetExposure, "货币及现金", 5);
      break;
    case "行业/主题指数":
    case "行业/主题主动":
    case "主动权益":
    case "宽基指数":
    default:
      addExposure(assetExposure, "A股", 95);
      addExposure(assetExposure, "货币及现金", 5);
      break;
  }
  const normalizedAssetExposure = normalizeExposure(assetExposure);
  let industryExposure = {};
  if ((normalizedAssetExposure["A股"] || 0) > 0) {
    industryExposure = thematicIndustryExposure(text, matches);
  }
  const themeExposure = themeExposureFromIndustry(industryExposure);
  const groupExposure = groupExposureFromTheme(themeExposure);
  const fundLevelIndustryExposure = absoluteIndustryExposure(normalizedAssetExposure, industryExposure);
  const primaryReportAsset = primaryExposure(normalizedAssetExposure, "待核验");
  const primaryReportAIndustry = primaryExposure(industryExposure, "");
  const primaryEquityTheme = primaryExposure(themeExposure, ((normalizedAssetExposure["A股"] || 0) > 0 ? "宽基/主动权益" : ""));
  const primaryEquityGroup = primaryExposure(groupExposure, primaryEquityTheme ? inferEquityIndustryGroup(primaryEquityTheme) : "");
  const primaryIndustryTheme = primaryReportAIndustry ? (industryMeta(primaryReportAIndustry).theme || primaryReportAIndustry) : (reportAssetThemeMap[primaryReportAsset]?.theme || inferIndustryTheme(fundName || text, fundType));
  const primaryIndustryGroup = primaryReportAIndustry ? (industryMeta(primaryReportAIndustry).group || inferIndustryGroup(primaryIndustryTheme, fundType)) : (reportAssetThemeMap[primaryReportAsset]?.group || inferIndustryGroup(primaryIndustryTheme, fundType));

  const result = {
    fundName,
    company: inferCompany(fundName, meta.company || holding.基金公司),
    fundType,
    industryTheme: primaryIndustryTheme,
    industryGroup: primaryIndustryGroup,
    equityIndustryTheme: primaryEquityTheme,
    equityIndustryGroup: primaryEquityGroup,
    reportAssetClass: primaryReportAsset,
    reportAIndustry: primaryReportAIndustry,
    assetExposure: normalizedAssetExposure,
    industryExposure,
    absoluteIndustryExposure: fundLevelIndustryExposure,
    themeExposure,
    groupExposure,
    confidence,
    basis: evidence.join("；") || "按基金名称关键词和持仓披露分类推断",
    classificationSource: "规则估算",
    lookthroughReportDate: "",
    lookthroughDisclosureDate: "",
    lookthroughCoverageStatus: "",
    isEstimated: "是"
  };
  const asOfDate = holding.__asOfDate || holding.持仓日期 || holding.日期 || "";
  const lookthrough = chooseFundLookthrough(fundCode, asOfDate);
  const ltAssetExposure = exposureObject(lookthrough?.资产暴露);
  if (lookthrough && Object.keys(ltAssetExposure).length) {
    const ltIndustryExposure = exposureObject(lookthrough.行业暴露);
    const ltThemeExposure = themeExposureFromIndustry(ltIndustryExposure);
    const ltGroupExposure = groupExposureFromTheme(ltThemeExposure);
    const ltReportAsset = primaryExposure(ltAssetExposure, result.reportAssetClass);
    const ltReportAIndustry = primaryExposure(ltIndustryExposure, "");
    const ltEquityTheme = primaryExposure(ltThemeExposure, ((ltAssetExposure["A股"] || 0) > 0 ? "宽基/主动权益" : result.equityIndustryTheme));
    const ltEquityGroup = primaryExposure(ltGroupExposure, ltEquityTheme ? inferEquityIndustryGroup(ltEquityTheme) : result.equityIndustryGroup);
    const ltIndustryTheme = ltReportAIndustry ? (industryMeta(ltReportAIndustry).theme || ltReportAIndustry) : result.industryTheme;
    const ltIndustryGroup = ltReportAIndustry ? (industryMeta(ltReportAIndustry).group || inferIndustryGroup(ltIndustryTheme, result.fundType)) : result.industryGroup;
    result.fundName = raw(lookthrough.基金名称) || result.fundName;
    result.company = raw(lookthrough.基金公司) || result.company;
    result.fundType = raw(lookthrough.基金类型) || result.fundType;
    result.industryTheme = ltIndustryTheme;
    result.industryGroup = ltIndustryGroup;
    result.equityIndustryTheme = ltEquityTheme;
    result.equityIndustryGroup = ltEquityGroup;
    result.reportAssetClass = ltReportAsset;
    result.reportAIndustry = ltReportAIndustry;
    result.assetExposure = normalizeExposure(ltAssetExposure);
    result.industryExposure = normalizeExposure(ltIndustryExposure);
    result.absoluteIndustryExposure = absoluteIndustryExposure(result.assetExposure, result.industryExposure);
    result.themeExposure = ltThemeExposure;
    result.groupExposure = ltGroupExposure;
    result.confidence = "A 季报穿透";
    result.classificationSource = raw(lookthrough.分类来源) || "东财F10季报穿透";
    result.lookthroughReportDate = raw(lookthrough.报告期);
    result.lookthroughDisclosureDate = raw(lookthrough.披露日期);
    result.lookthroughCoverageStatus = raw(lookthrough.覆盖状态);
    result.isEstimated = raw(lookthrough.是否估算) || "否";
    result.basis = [
      `${result.classificationSource}：报告期${result.lookthroughReportDate || "未标注"}`,
      "资产暴露来自基金F10季报资产配置",
      result.reportAIndustry ? "行业暴露由季报股票持仓和东财股票行业字段推导" : "行业暴露缺失时仅使用资产配置"
    ].join("；");
  }
  return applyEconomicExposure(result, chooseFundEconomicExposure(fundCode, asOfDate));
}

function categoryExposureRows(fundClass) {
  const rows = [];
  const push = (field, category, share) => {
    if (!category || !Number.isFinite(share) || Math.abs(share) < 0.0001) return;
    rows.push({ field, category, share });
  };
  const aShare = fundClass.assetExposure?.["A股"] || 0;
  for (const [asset, share] of Object.entries(fundClass.assetExposure || {})) {
    push("研报大类资产", asset, share);
    if (asset === "A股") {
      if (Object.keys(fundClass.industryExposure || {}).length) {
        for (const [industry, industryShare] of Object.entries(fundClass.industryExposure)) {
          const absoluteShare = share * industryShare / 100;
          const meta = industryMeta(industry);
          push("研报A股行业", industry, absoluteShare);
          push("权益行业主题", meta.theme || industry, absoluteShare);
          push("权益行业大类", meta.group || "", absoluteShare);
          push("行业主题", meta.theme || industry, absoluteShare);
          push("行业大类", meta.group || "", absoluteShare);
        }
      } else {
        push("权益行业主题", "宽基/主动权益", share);
        push("权益行业大类", "宽基/主动权益", share);
        push("行业主题", "宽基/主动权益", share);
        push("行业大类", "权益宽基/均衡", share);
      }
    } else {
      const mapped = reportAssetThemeMap[asset];
      if (mapped) {
        push("行业主题", mapped.theme, share);
        push("行业大类", mapped.group, share);
        if (/港股|美股|新兴市场|其他发达市场/.test(asset)) {
          push("权益行业主题", "海外宽基/区域", share);
          push("权益行业大类", "海外权益", share);
        }
      }
    }
  }
  if (aShare > 0 && !rows.some((row) => row.field === "研报大类资产" && row.category === "A股")) {
    push("研报大类资产", "A股", aShare);
  }
  return rows;
}

function scaledRow(row, scale, overrides) {
  const factor = Number(scale) / 100;
  const fields = ["调前权重", "调后权重", "权重变化", "加仓权重", "减仓权重", "净增配", "总点位"];
  const out = { ...row, ...overrides };
  for (const field of fields) {
    if (row[field] != null) out[field] = nz(row[field]) * factor;
  }
  return out;
}

function addStrategyAssetChangeByExposure(map, row, fundClass) {
  const exposures = categoryExposureRows(fundClass)
    .filter((item) => ["研报大类资产", "权益行业主题", "研报A股行业"].includes(item.field));
  if (!exposures.length) {
    addStrategyAssetChange(map, row);
    return;
  }
  for (const item of exposures) {
    const overrides = {
      行业主题: "",
      行业大类: "",
      权益行业主题: "",
      权益行业大类: "",
      研报大类资产: "",
      研报A股行业: ""
    };
    if (item.field === "研报大类资产") overrides.研报大类资产 = item.category;
    if (item.field === "权益行业主题") {
      overrides.权益行业主题 = item.category;
      overrides.权益行业大类 = inferEquityIndustryGroup(item.category);
    }
    if (item.field === "研报A股行业") overrides.研报A股行业 = item.category;
    addStrategyAssetChange(map, scaledRow(row, item.share, overrides));
  }
}

function pushRebalanceFundCategoryRows(target, row, fundClass) {
  const exposures = categoryExposureRows(fundClass)
    .filter((item) => ["研报大类资产", "权益行业主题", "研报A股行业"].includes(item.field));
  for (const item of exposures) {
    const factor = item.share / 100;
    const change = nz(row.权重变化) * factor;
    if (Math.abs(change) <= 0.0001) continue;
    target.push({
      调仓日期: row.调仓日期,
      月份: monthOf(row.调仓日期),
      统一策略ID: row.统一策略ID,
      策略名称: row.策略名称,
      投顾机构: row.投顾机构,
      是否广发策略: row.是否广发策略,
      调仓事件ID: row.调仓事件ID || "",
      天天当前对客展示: row.天天当前对客展示 || "",
      天天展示状态: row.天天展示状态 || "",
      风险等级: row.风险等级,
      业务分类: row.业务分类,
      研报产品类型: row.研报产品类型 || "未分类",
      研报股票子类型: row.研报股票子类型 || "",
      市场地域: row.市场地域,
      分类字段: item.field,
      分类: item.category,
      基金代码: row.基金代码,
      基金名称: row.基金名称,
      基金公司: row.基金公司,
      基金类型: row.基金类型,
      调前权重: nz(row.调前权重) * factor,
      调后权重: nz(row.调后权重) * factor,
      权重变化: change,
      调仓动作: row.调仓动作
    });
  }
}

const manualFundMeta = {
  "南方利安A": { company: "南方基金", type: "混合型" },
  "南方利安C": { company: "南方基金", type: "混合型" },
  "圆信永丰优加生活": { company: "圆信永丰基金", type: "股票型" },
  "国投瑞银白银期货(LOF)A": { company: "国投瑞银基金", type: "商品型" },
  "国投瑞银白银期货(LOF)C": { company: "国投瑞银基金", type: "商品型" },
  "南方荣光A": { company: "南方基金", type: "混合型" },
  "南方荣光C": { company: "南方基金", type: "混合型" },
  "南方原油A": { company: "南方基金", type: "商品型" },
  "南方原油C": { company: "南方基金", type: "商品型" },
  "淳厚信睿A": { company: "淳厚基金", type: "混合型" },
  "南方和元A": { company: "南方基金", type: "债券型" },
  "鹏华酒A": { company: "鹏华基金", type: "指数型" },
  "鹏华酒C": { company: "鹏华基金", type: "指数型" },
  "前海开源聚财宝B": { company: "前海开源基金", type: "货币型" },
  "平安日鑫A": { company: "平安基金", type: "货币型" },
  "南方双元A": { company: "南方基金", type: "债券型" },
  "万家新兴蓝筹A": { company: "万家基金", type: "混合型" },
  "万家新兴蓝筹C": { company: "万家基金", type: "混合型" },
  "大成360互联网+大数据100A": { company: "大成基金", type: "指数型" },
  "大成360互联网+大数据100C": { company: "大成基金", type: "指数型" },
  "诺安油气能源": { company: "诺安基金", type: "QDII/海外" },
  "国泰国证食品饮料行业(LOF)A": { company: "国泰基金", type: "指数型" },
  "圆信永丰强化收益A": { company: "圆信永丰基金", type: "债券型" },
  "万家日日薪B": { company: "万家基金", type: "货币型" },
  "平安新鑫先锋C": { company: "平安基金", type: "混合型" },
  "博时新收益C": { company: "博时基金", type: "混合型" },
  "中信建投稳祥A": { company: "中信建投基金", type: "债券型" },
  "中金MSCI质量A": { company: "中金基金", type: "指数型" },
  "前海开源沪港深裕鑫C": { company: "前海开源基金", type: "混合型" },
  "鑫元鸿利A": { company: "鑫元基金", type: "债券型" },
  "003034": { name: "平安日鑫A", company: "平安基金", type: "货币型" },
  "004369": { name: "前海开源聚财宝B", company: "前海开源基金", type: "货币型" },
  "519512": { name: "万家日日薪B", company: "万家基金", type: "货币型" },
  "006012": { name: "中信保诚稳鸿C", company: "中信保诚基金", type: "债券型" },
  "008421": { name: "广发招泰C", company: "广发基金", type: "混合型" },
  "001526": { name: "鑫元安鑫宝A", company: "鑫元基金", type: "货币型" },
  "004331": { name: "太平日日鑫B", company: "太平基金", type: "货币型" },
  "004330": { name: "太平日日鑫A", company: "太平基金", type: "货币型" },
  "003535": { name: "浦银安盛日日丰B", company: "浦银安盛基金", type: "货币型" },
  "020009": { name: "国泰金鹏蓝筹", company: "国泰基金", type: "混合型" },
  "000893": { name: "工银创新动力", company: "工银瑞信基金", type: "股票型" },
  "004958": { name: "圆信永丰优享生活", company: "圆信永丰基金", type: "股票型" },
  "519723": { name: "交银双轮动A/B", company: "交银施罗德基金", type: "债券型" },
  "540003": { name: "汇丰晋信动态策略A", company: "汇丰晋信基金", type: "混合型" },
  "000355": { name: "南方丰元A", company: "南方基金", type: "债券型" },
  "270002": { name: "广发稳健增长A", company: "广发基金", type: "混合型" },
  "001503": { name: "南方利鑫C", company: "南方基金", type: "混合型" },
  "012432": { name: "国投瑞银安泰混合C", company: "国投瑞银基金", type: "混合型" },
  "003603": { name: "景顺长城泰安回报混合A", company: "景顺长城基金", type: "混合型" },
  "006084": { name: "融通研究优选混合", company: "融通基金", type: "混合型" },
  "001499": { name: "国投瑞银新增长混合A", company: "国投瑞银基金", type: "混合型" },
  "010569": { name: "海富通惠睿精选混合C", company: "海富通基金", type: "混合型" },
  "003435": { name: "博时鑫泽混合C", company: "博时基金", type: "混合型" },
  "002531": { name: "博时保泽保本C", company: "博时基金", type: "混合型" },
  "009277": { name: "融通行业景气C", company: "融通基金", type: "混合型" },
  "161606": { name: "融通行业景气A", company: "融通基金", type: "混合型" },
  "003938": { name: "南方荣尊A", company: "南方基金", type: "混合型" },
  "004824": { name: "摩根安裕回报C", company: "摩根基金", type: "混合型" },
  "000029": { name: "富国宏观策略A", company: "富国基金", type: "混合型" },
  "166012": { name: "中欧信用增利债券(LOF)C", company: "中欧基金", type: "债券型" },
  "166004": { name: "中欧稳健收益债券C", company: "中欧基金", type: "债券型" },
  "000954": { name: "国泰睿吉灵活配置混合C", company: "国泰基金", type: "混合型" },
  "160119": { name: "南方中证500ETF联接(LOF)A", company: "南方基金", type: "指数型" },
  "001963": { name: "中欧天禧债券", company: "中欧基金", type: "债券型" },
  "001979": { name: "南方沪港深价值主题灵活配置混合", company: "南方基金", type: "混合型" },
  "007326": { name: "国投瑞银新增长混合C", company: "国投瑞银基金", type: "混合型" },
  "002262": { name: "中银宝利混合C", company: "中银基金", type: "混合型" },
  "002414": { name: "中银瑞利混合C", company: "中银基金", type: "混合型" },
  "013627": { name: "华夏周期驱动混合发起式C", company: "华夏基金", type: "混合型" },
  "519963": { name: "长信利盈混合A", company: "长信基金", type: "混合型" },
  "001196": { name: "东方鼎新灵活配置混合A", company: "东方基金", type: "混合型" },
  "004150": { name: "博时鑫惠混合C", company: "博时基金", type: "混合型" },
  "002261": { name: "中银宝利混合A", company: "中银基金", type: "混合型" },
  "519960": { name: "长信利广灵活配置混合C", company: "长信基金", type: "混合型" },
  "003967": { name: "中银润利混合C", company: "中银基金", type: "混合型" },
  "008324": { name: "宝盈祥利稳健配置混合A", company: "宝盈基金", type: "混合型" },
  "010511": { name: "博时鑫康混合C", company: "博时基金", type: "混合型" },
  "014862": { name: "申万菱信双禧混合C", company: "申万菱信基金", type: "混合型" },
  "519624": { name: "银河君耀混合C", company: "银河基金", type: "混合型" },
  "003981": { name: "中银证券瑞益灵活配置混合C", company: "中银证券", type: "混合型" },
  "002559": { name: "博时鑫瑞混合C", company: "博时基金", type: "混合型" },
  "008479": { name: "景顺长城泰申回报混合", company: "景顺长城基金", type: "混合型" },
  "010478": { name: "景顺长城泰祥回报混合", company: "景顺长城基金", type: "混合型" },
  "004732": { name: "万家瑞尧灵活配置混合C", company: "万家基金", type: "混合型" },
  "010212": { name: "景顺长城顺鑫回报混合C", company: "景顺长城基金", type: "混合型" },
  "008058": { name: "鹏华鑫享稳健混合A", company: "鹏华基金", type: "混合型" },
  "002503": { name: "中银腾利混合C", company: "中银基金", type: "混合型" },
  "015225": { name: "汇添富中证细分化工产业主题指数增强发起式A", company: "汇添富基金", type: "指数型" },
  "000953": { name: "国泰睿吉灵活配置混合A", company: "国泰基金", type: "混合型" },
  "012646": { name: "建信中证全指证券公司ETF联接C", company: "建信基金", type: "指数型" },
  "010508": { name: "博时鑫康混合A", company: "博时基金", type: "混合型" },
  "003512": { name: "申万菱信安鑫优选混合C", company: "申万菱信基金", type: "混合型" },
  "003118": { name: "光大保德信吉鑫混合C", company: "光大保德信基金", type: "混合型" },
  "014609": { name: "中欧周期景气混合发起式C", company: "中欧基金", type: "混合型" },
  "003493": { name: "申万菱信安鑫优选混合A", company: "申万菱信基金", type: "混合型" },
  "002111": { name: "华宝新起点混合", company: "华宝基金", type: "混合型" },
  "519627": { name: "银河君润混合A", company: "银河基金", type: "混合型" },
  "013606": { name: "华夏中证内地低碳经济主题ETF联接C", company: "华夏基金", type: "指数型" },
  "013605": { name: "华夏中证内地低碳经济主题ETF联接A", company: "华夏基金", type: "指数型" },
  "010568": { name: "海富通惠睿精选混合A", company: "海富通基金", type: "混合型" },
  "002813": { name: "博时保泰保本混合A", company: "博时基金", type: "混合型" },
  "002435": { name: "中银宏利C", company: "中银基金", type: "混合型" },
  "519134": { name: "海富通富祥混合", company: "海富通基金", type: "混合型" },
  "005397": { name: "南方安养混合", company: "南方基金", type: "混合型" },
  "010961": { name: "九泰久安量化C", company: "九泰基金", type: "股票型" }
};

const companyPrefixes = [
  ["广发", "广发基金"], ["易方达", "易方达基金"], ["华夏", "华夏基金"], ["南方", "南方基金"], ["嘉实", "嘉实基金"],
  ["富国", "富国基金"], ["汇添富", "汇添富基金"], ["招商", "招商基金"], ["博时", "博时基金"], ["鹏华", "鹏华基金"],
  ["工银瑞信", "工银瑞信基金"], ["中欧", "中欧基金"], ["兴全", "兴证全球基金"], ["兴证全球", "兴证全球基金"],
  ["交银", "交银施罗德基金"], ["华安", "华安基金"], ["国泰", "国泰基金"], ["天弘", "天弘基金"], ["万家", "万家基金"],
  ["银华", "银华基金"], ["景顺长城", "景顺长城基金"], ["大成", "大成基金"], ["中银", "中银基金"], ["华泰柏瑞", "华泰柏瑞基金"],
  ["泰康", "泰康基金"], ["永赢", "永赢基金"], ["兴业", "兴业基金"], ["国投瑞银", "国投瑞银基金"], ["摩根", "摩根基金"],
  ["建信", "建信基金"], ["平安", "平安基金"], ["安信", "安信基金"], ["东方红", "东方红资管"], ["睿远", "睿远基金"],
  ["泉果", "泉果基金"], ["国联安", "国联安基金"], ["海富通", "海富通基金"], ["长城", "长城基金"], ["长信", "长信基金"],
  ["国海富兰克林", "国海富兰克林基金"], ["华宝", "华宝基金"], ["诺安", "诺安基金"], ["民生加银", "民生加银基金"],
  ["宝盈", "宝盈基金"], ["贝莱德", "贝莱德基金"], ["博道", "博道基金"], ["财通资管", "财通资管"], ["财通", "财通基金"],
  ["创金合信", "创金合信基金"], ["创金", "创金合信基金"], ["淳厚", "淳厚基金"], ["东财", "东财基金"], ["东方", "东方基金"],
  ["方正富邦", "方正富邦基金"], ["蜂巢", "蜂巢基金"], ["国金", "国金基金"], ["国寿安保", "国寿安保基金"], ["红土创新", "红土创新基金"],
  ["宏利", "宏利基金"], ["华富", "华富基金"], ["华商", "华商基金"], ["华泰保兴", "华泰保兴基金"], ["汇安", "汇安基金"],
  ["汇泉", "汇泉基金"], ["嘉合", "嘉合基金"], ["金鹰", "金鹰基金"], ["民生", "民生加银基金"], ["农银", "农银汇理基金"],
  ["浦银安盛", "浦银安盛基金"], ["前海开源", "前海开源基金"], ["上银", "上银基金"], ["申万菱信", "申万菱信基金"],
  ["泰信", "泰信基金"], ["西部利得", "西部利得基金"], ["信澳", "信澳基金"], ["鑫元", "鑫元基金"], ["银河", "银河基金"],
  ["英大", "英大基金"], ["圆信永丰", "圆信永丰基金"], ["中加", "中加基金"], ["中庚", "中庚基金"], ["中金", "中金基金"],
  ["中融", "国联基金"], ["国联", "国联基金"], ["中泰", "中泰资管"], ["朱雀", "朱雀基金"], ["浙商", "浙商基金"],
  ["中信保诚", "中信保诚基金"], ["太平", "太平基金"], ["融通", "融通基金"], ["汇丰晋信", "汇丰晋信基金"], ["东吴", "东吴基金"]
];

function inferCompany(name, existing = "") {
  const disclosed = raw(existing);
  if (disclosed && !/未披露|未识别|其他/.test(disclosed)) return disclosed;
  const text = raw(name);
  const hit = companyPrefixes.find(([prefix]) => text.startsWith(prefix));
  if (hit) return hit[1];
  if (/^\d{6}$/.test(text)) return `基金公司未披露(${text})`;
  const fallback = text.match(/^([\u4e00-\u9fa5]{2,6})/);
  return fallback ? `${fallback[1]}基金` : "基金公司未披露";
}

function isGuangfaFund(name) {
  return /^广发/.test(raw(name));
}

function addAgg(map, key, patch) {
  if (!map.has(key)) map.set(key, { ...patch });
  const row = map.get(key);
  for (const [field, value] of Object.entries(patch)) {
    if (typeof value === "number") row[field] = (row[field] || 0) + value;
    else if (!(field in row)) row[field] = value;
  }
  return row;
}

function dedupeRowsByKey(rows, keyFn, preferFn) {
  const map = new Map();
  for (const row of rows || []) {
    const key = keyFn(row);
    if (!key) continue;
    const existing = map.get(key);
    if (!existing || preferFn(row, existing)) map.set(key, row);
  }
  return [...map.values()];
}

function monthOf(date) {
  return raw(date).slice(0, 7) || "未知";
}

function dateMs(date) {
  const t = new Date(raw(date)).getTime();
  return Number.isFinite(t) ? t : null;
}

function daysBetweenDates(a, b) {
  const start = dateMs(a);
  const end = dateMs(b);
  if (start === null || end === null) return null;
  return Math.abs(end - start) / 86400000;
}

function normalizeCurvePoints(points) {
  return (points || [])
    .map((point) => ({ ...point, 日期: raw(point.日期), 数值: num(point.数值) }))
    .filter((point) => point.日期 && point.数值 !== null)
    .sort((a, b) => a.日期.localeCompare(b.日期));
}

function curveReturn(value, base, mode) {
  const v = num(value);
  const b = num(base);
  if (v === null || b === null) return null;
  if (mode === "return" || mode === "return_pct") {
    const denominator = 1 + b / 100;
    if (!denominator) return null;
    return ((1 + v / 100) / denominator - 1) * 100;
  }
  if (!b) return null;
  return (v / b - 1) * 100;
}

function curveGapDiagnostics(points, limitDays = 45) {
  const normalized = normalizeCurvePoints(points);
  let maxGap = 0;
  let start = "";
  let end = "";
  let count = 0;
  for (let i = 1; i < normalized.length; i += 1) {
    const gap = daysBetweenDates(normalized[i - 1].日期, normalized[i].日期);
    if (gap === null) continue;
    if (gap > limitDays) count += 1;
    if (gap > maxGap) {
      maxGap = gap;
      start = normalized[i - 1].日期;
      end = normalized[i].日期;
    }
  }
  return { maxGap, start, end, count, hasLongGap: maxGap > limitDays };
}

function hasCurveGapBetween(points, startDate, endDate, limitDays = 45) {
  const selected = normalizeCurvePoints(points).filter((point) => point.日期 >= startDate && point.日期 <= endDate);
  for (let i = 1; i < selected.length; i += 1) {
    const gap = daysBetweenDates(selected[i - 1].日期, selected[i].日期);
    if (gap !== null && gap > limitDays) return true;
  }
  return false;
}

function nearestPeriodStart(points, targetTime, latestDate, toleranceDays = 10) {
  let best = null;
  let bestDiff = Infinity;
  for (const point of points) {
    if (point.日期 === latestDate) continue;
    const diff = Math.abs((dateMs(point.日期) ?? Infinity) - targetTime) / 86400000;
    if (diff < bestDiff) {
      best = point;
      bestDiff = diff;
    }
  }
  return best && bestDiff <= toleranceDays ? best : null;
}

function periodCurveReturn(points, mode, options) {
  const normalized = normalizeCurvePoints(points);
  if (normalized.length < 2) return null;
  const latest = normalized.at(-1);
  const latestTime = dateMs(latest.日期);
  if (latestTime === null) return null;
  let start = null;
  if (options.days) {
    start = nearestPeriodStart(normalized, latestTime - options.days * 86400000, latest.日期, options.toleranceDays || 10);
  } else if (options.ytd) {
    const latestDate = new Date(latestTime);
    const ytdTime = new Date(latestDate.getFullYear(), 0, 1).getTime();
    for (const point of normalized) {
      const t = dateMs(point.日期);
      if (t !== null && t <= ytdTime) start = point;
      else if (t !== null && t > ytdTime) break;
    }
    if (!start) {
      start = normalized.find((point) => {
        const t = dateMs(point.日期);
        return t !== null && t >= ytdTime && point.日期 !== latest.日期;
      }) || null;
      if (start && (dateMs(start.日期) - ytdTime) / 86400000 > (options.toleranceDays || 45)) start = null;
    }
  } else if (options.daily) {
    start = normalized.at(-2);
    const gap = start ? daysBetweenDates(start.日期, latest.日期) : null;
    if (gap === null || gap > (options.maxGapDays || 7)) start = null;
  }
  if (!start || start.日期 === latest.日期) return null;
  if (hasCurveGapBetween(normalized, start.日期, latest.日期, options.maxGapDays || 45)) return null;
  return curveReturn(latest.数值, start.数值, mode);
}

function setFieldValue(items, field, value) {
  return upsertField(items || [], field, value === undefined ? null : value);
}

function upsertQualityCheck(checks, item) {
  const rows = checks || [];
  const index = rows.findIndex((row) => row.项目 === item.项目);
  if (index >= 0) rows[index] = { ...rows[index], ...item };
  else rows.push(item);
  return rows;
}

function applyDisclosureCurveQuality(detail, row) {
  const curve = detail.curves?.["披露业绩"] || {};
  const points = normalizeCurvePoints(curve.points || []);
  if (points.length < 2) return;
  const mode = curve.模式 || points[0]?.模式 || "nav";
  const diag = curveGapDiagnostics(points, 45);
  const metrics = {
    日涨跌幅: periodCurveReturn(points, mode, { daily: true, maxGapDays: 7 }),
    近一周: periodCurveReturn(points, mode, { days: 7, toleranceDays: 5, maxGapDays: 45 }),
    近一月: periodCurveReturn(points, mode, { days: 31, toleranceDays: 10, maxGapDays: 45 }),
    近三月: periodCurveReturn(points, mode, { days: 92, toleranceDays: 15, maxGapDays: 45 }),
    "近6月": periodCurveReturn(points, mode, { days: 183, toleranceDays: 20, maxGapDays: 45 }),
    "近1年": periodCurveReturn(points, mode, { days: 365, toleranceDays: 25, maxGapDays: 45 }),
    今年以来: periodCurveReturn(points, mode, { ytd: true, toleranceDays: 45, maxGapDays: 45 })
  };
  const officialPreservedFields = new Set(["近一周", "近一月", "近三月", "近6月", "近1年", "今年以来"]);
  for (const [field, value] of Object.entries(metrics)) {
    const existing = num(row[field]);
    const shouldPreserve = officialPreservedFields.has(field) && existing !== null;
    row[field] = shouldPreserve ? existing : value === null ? null : Number(value.toFixed(4));
    detail.summary[field] = row[field];
    detail.performanceFields = setFieldValue(detail.performanceFields, field, row[field]);
  }
  if (diag.hasLongGap) {
    const warning = `披露业绩曲线存在长缺口：${diag.start} 至 ${diag.end} 相隔${Math.round(diag.maxGap)}天；短区间收益和风险内排名不使用该缺口两端直接计算。`;
    row.数据完整性 = "数据不全";
    detail.summary.数据完整性 = "数据不全";
    row.质检情况 = raw(row.质检情况).includes("披露业绩长缺口") ? row.质检情况 : `${raw(row.质检情况)}；披露业绩长缺口`.replace(/^；/, "");
    detail.summary.质检情况 = row.质检情况;
    detail.curveWarnings = [...new Set([...(detail.curveWarnings || []), warning])];
    detail.qualityChecks = upsertQualityCheck(detail.qualityChecks, {
      项目: "官方披露业绩",
      结论: "不完整",
      说明: warning
    });
  }
}

function rebalanceLogic(event) {
  const text = `${raw(event.调仓标题)} ${raw(event.调仓原因)} ${raw(event.涉及资产)}`;
  if (/风险|回撤|防御|止盈|约束|超出|比例/.test(text)) return "风险控制/再平衡";
  if (/基金经理|季报|半年报|年报|替换|调出|调入|产品/.test(text)) return "基金优选/产品替换";
  if (/债|久期|利率|信用|短债|中短债|流动性/.test(text)) return "固收久期/债券配置";
  if (/海外|全球|QDII|港股|美股|纳斯达克|标普|黄金|商品/.test(text)) return "海外/商品配置";
  if (/权益|股票|科技|成长|AI|新能源|医药|消费|军工|半导体|周期/.test(text)) return "权益结构/主题切换";
  return "组合再平衡/常规调整";
}

function winValue(event) {
  const result = raw(event.胜负) || raw(event.结果评价);
  if (/胜|赢|正/.test(result)) return 1;
  if (/负|输|亏|差/.test(result)) return 0;
  return null;
}

function actionFromChange(before, after, change, disclosed) {
  const text = raw(disclosed);
  if (text) return text;
  if (after > 0 && before <= 0) return "买入";
  if (after <= 0 && before > 0) return "卖出";
  if (change > 0) return "增配";
  if (change < 0) return "减配";
  return "持有";
}

function secondaryCategoryFromFundClass(holding = {}, fundClass = {}) {
  const candidates = [
    fundClass.fundType,
    fundClass.equityIndustryTheme,
    fundClass.industryTheme,
    holding.二级分类,
    holding.分组,
    holding.基金同类分组,
    fundClass.reportAssetClass
  ];
  const emptyLabels = new Set(["待核验", "未披露", "未分类", "未知", "--", "a", "1", "2", "3", "4", "5", "6", "7", "8"]);
  return candidates.map(raw).find((value) => value && !emptyLabels.has(value)) || "未披露";
}

function standardFundPrimaryType(fundClass = {}) {
  const type = raw(fundClass.fundType);
  const asset = raw(fundClass.reportAssetClass);
  if (/货币|现金/.test(type) || asset === "货币及现金") return "货币市场基金";
  if (/海外债券/.test(type)) return "QDII基金";
  if (/短债|中短债|纯债|信用债|债券指数|可转债/.test(type) || asset === "债券") return "债券型基金";
  if (/固收\+|偏债混合|混合偏股/.test(type)) return "混合型基金";
  if (/FOF|养老/.test(type)) return "FOF基金";
  if (/黄金|贵金属|商品|能源/.test(type) || /黄金|其他商品/.test(asset)) return "商品型基金";
  if (/REIT/.test(type) || asset === "海外REIT") return "REITs基金";
  if (/互认基金/.test(type) && /权益/.test(asset)) return "股票型基金";
  if (/港股|美股|海外|QDII|沪港深/.test(type) || /港股|美股|新兴市场|其他发达市场/.test(asset)) return "QDII基金";
  if (/行业|主题|主动权益|宽基指数|股票|权益|指数/.test(type) || asset === "A股") return "股票型基金";
  return "待补分类";
}

function isMissingClassValue(value) {
  const text = raw(value);
  return !text || ["待核验", "未披露", "未分类", "未知", "--", "a", "1", "2", "3", "4", "5", "6", "7", "8"].includes(text);
}

function addDirectionAgg(map, row) {
  const key = [
    row.风险等级,
    row.业务分类,
    row.市场地域,
    row.基金类型,
    row.基金公司,
    row.是否广发基金
  ].join("｜");
  if (!map.has(key)) {
    map.set(key, {
      风险等级: row.风险等级,
      业务分类: row.业务分类,
      市场地域: row.市场地域,
      基金类型: row.基金类型,
      基金公司: row.基金公司,
      是否广发基金: row.是否广发基金,
      明细数: 0,
      买入增配权重: 0,
      卖出减配权重: 0,
      净增配: 0,
      广发策略净增配: 0,
      非广发策略净增配: 0,
      正贡献: 0,
      负贡献: 0
    });
  }
  const out = map.get(key);
  const change = nz(row.权重变化);
  const contribution = nz(row.调仓后收益贡献);
  out.明细数 += 1;
  out.净增配 += change;
  if (change > 0) out.买入增配权重 += change;
  if (change < 0) out.卖出减配权重 += Math.abs(change);
  if (row.是否广发策略 === "是") out.广发策略净增配 += change;
  else out.非广发策略净增配 += change;
  if (contribution >= 0) out.正贡献 += contribution;
  else out.负贡献 += contribution;
  return out;
}

function addGfFundOpportunity(map, row) {
  if (row.是否广发基金 !== "是") return;
  const key = `${row.基金代码}｜${row.基金名称}`;
  if (!map.has(key)) {
    map.set(key, {
      基金代码: row.基金代码,
      基金名称: row.基金名称,
      基金公司: row.基金公司,
      基金类型: row.基金类型,
      加仓次数: 0,
      减仓次数: 0,
      买入次数: 0,
      卖出次数: 0,
      调前权重: 0,
      调后权重: 0,
      加仓权重: 0,
      减仓权重: 0,
      净增配: 0,
      调仓后收益贡献: 0,
      当前持仓策略数: 0,
      当前持仓权重: 0
    });
  }
  const out = map.get(key);
  const change = nz(row.权重变化);
  const action = raw(row.调仓动作);
  if (change > 0) {
    out.加仓次数 += 1;
    out.加仓权重 += change;
  } else if (change < 0) {
    out.减仓次数 += 1;
    out.减仓权重 += Math.abs(change);
  }
  if (action === "买入") out.买入次数 += 1;
  if (action === "卖出") out.卖出次数 += 1;
  out.净增配 += change;
  out.调仓后收益贡献 += nz(row.调仓后收益贡献);
}

function addRebalanceFundMonthlyAgg(map, row) {
  const key = [
    monthOf(row.调仓日期),
    row.风险等级,
    row.业务分类,
    row.研报产品类型 || "",
    row.研报股票子类型 || "",
    row.市场地域,
    row.投顾机构,
    row.是否广发策略,
    row.天天当前对客展示 || "",
    row.天天展示状态 || "",
    row.基金代码,
    row.基金名称,
    row.基金类型,
    row.研报大类资产 || "",
    row.研报A股行业 || "",
    row.是否广发基金
  ].join("｜");
  if (!map.has(key)) {
    map.set(key, {
      月份: monthOf(row.调仓日期),
      风险等级: row.风险等级,
      业务分类: row.业务分类,
      研报产品类型: row.研报产品类型 || "未分类",
      研报股票子类型: row.研报股票子类型 || "",
      市场地域: row.市场地域,
      投顾机构: row.投顾机构,
      是否广发策略: row.是否广发策略,
      天天当前对客展示: row.天天当前对客展示 || "",
      天天展示状态: row.天天展示状态 || "",
      基金代码: row.基金代码,
      基金名称: row.基金名称,
      基金公司: row.基金公司,
      基金类型: row.基金类型,
      基金分类依据: row.基金分类依据,
      基金分类来源: row.基金分类来源 || "",
      基金穿透报告期: row.基金穿透报告期 || "",
      基金穿透覆盖状态: row.基金穿透覆盖状态 || "",
      是否估算分类: row.是否估算分类 || "",
      资产暴露: row.资产暴露,
      行业暴露: row.行业暴露,
      行业主题: row.行业主题,
      行业大类: row.行业大类,
      权益行业主题: row.权益行业主题,
      权益行业大类: row.权益行业大类,
      研报大类资产: row.研报大类资产 || "",
      研报A股行业: row.研报A股行业 || "",
      是否广发基金: row.是否广发基金,
      明细数: 0,
      调仓事件数: 0,
      调仓策略数: 0,
      加仓次数: 0,
      减仓次数: 0,
      买入次数: 0,
      卖出次数: 0,
      调前权重: 0,
      调后权重: 0,
      加仓权重: 0,
      减仓权重: 0,
      净增配: 0,
      广发策略净增配: 0,
      非广发策略净增配: 0,
      调仓后收益贡献: 0,
      _事件: new Set(),
      _策略: new Set()
    });
  }
  const out = map.get(key);
  const change = nz(row.权重变化);
  const action = raw(row.调仓动作);
  out.明细数 += 1;
  out._事件.add(row.调仓事件ID);
  out._策略.add(row.统一策略ID);
  out.调前权重 += nz(row.调前权重);
  out.调后权重 += nz(row.调后权重);
  if (change > 0) {
    out.加仓次数 += 1;
    out.加仓权重 += change;
  } else if (change < 0) {
    out.减仓次数 += 1;
    out.减仓权重 += Math.abs(change);
  }
  if (action === "买入") out.买入次数 += 1;
  if (action === "卖出") out.卖出次数 += 1;
  out.净增配 += change;
  if (row.是否广发策略 === "是") out.广发策略净增配 += change;
  else out.非广发策略净增配 += change;
  out.调仓后收益贡献 += nz(row.调仓后收益贡献);
}

function addStrategyAssetChange(map, row) {
  const key = [
    row.调仓日期,
    row.统一策略ID,
    row.风险等级,
    row.业务分类,
    row.研报产品类型 || "",
    row.研报股票子类型 || "",
    row.市场地域,
    row.投顾机构,
    row.是否广发策略,
    row.天天当前对客展示 || "",
    row.天天展示状态 || "",
    row.基金类型,
    row.行业主题 || "",
    row.行业大类 || "",
    row.权益行业主题 || "",
    row.权益行业大类 || "",
    row.研报大类资产 || "",
    row.研报A股行业 || ""
  ].join("｜");
  if (!map.has(key)) {
    map.set(key, {
      调仓日期: row.调仓日期,
      月份: monthOf(row.调仓日期),
      统一策略ID: row.统一策略ID,
      策略名称: row.策略名称,
      投顾机构: row.投顾机构,
      渠道: row.渠道,
      是否广发策略: row.是否广发策略,
      天天当前对客展示: row.天天当前对客展示 || "",
      天天展示状态: row.天天展示状态 || "",
      风险等级: row.风险等级,
      业务分类: row.业务分类,
      研报产品类型: row.研报产品类型 || "未分类",
      研报股票子类型: row.研报股票子类型 || "",
      市场地域: row.市场地域,
      基金类型: row.基金类型,
      行业主题: row.行业主题 || "",
      行业大类: row.行业大类 || "",
      权益行业主题: row.权益行业主题 || "",
      权益行业大类: row.权益行业大类 || "",
      研报大类资产: row.研报大类资产 || "",
      研报A股行业: row.研报A股行业 || "",
      明细数: 0,
      增持明细数: 0,
      减持明细数: 0,
      调前权重: 0,
      调后权重: 0,
      加仓权重: 0,
      减仓权重: 0,
      净增配: 0,
      总点位: 0
    });
  }
  const out = map.get(key);
  const change = nz(row.权重变化);
  out.明细数 += 1;
  out.调前权重 += nz(row.调前权重);
  out.调后权重 += nz(row.调后权重);
  out.净增配 += change;
  out.总点位 += Math.abs(change);
  if (change > 0) {
    out.增持明细数 += 1;
    out.加仓权重 += change;
  } else if (change < 0) {
    out.减持明细数 += 1;
    out.减仓权重 += Math.abs(change);
  }
}

validateInputs();

const summary = loadSummary();
const supplementalTextById = buildSupplementalTextById();
const detailSummariesById = buildDetailSummaryById();
const sourceStrategies = mergeStrategiesWithDetailSummaries(summary.strategies || [], detailSummariesById);
const processedStrategies = sourceStrategies.map((row) => updateStrategyRow(row, supplementalTextById.get(row.统一策略ID) || ""));
const sourceRawStrategyCount = Math.max(Number(summary.rawStrategyCount) || 0, processedStrategies.length);

function isDisplayableStrategy(row) {
  if (!row) return false;
  if (row.风险等级 === "D0 持仓缺失") return false;
  if (row.研报产品类型 === "持仓缺失/不入池") return false;
  const insightScope = raw(row.洞察评价对象).trim();
  if (insightScope) return insightScope !== "仅列表保留";
  return row.数据完整性 === "完整";
}

function isTtfundStrategy(row) {
  return raw(row?.渠道).includes("天天基金") || raw(row?.统一策略ID).startsWith("ttfund__");
}

function fieldItemValue(items, fieldName) {
  const item = (items || []).find((entry) => raw(entry?.字段) === fieldName);
  return item ? item.值 : "";
}

function validBenchmarkStatement(value) {
  const text = raw(value).replace(/\s+/g, " ").trim();
  if (!text) return "";
  const compact = text.replace(/\s+/g, "");
  if (["-", "--", "无", "暂无", "暂无数据", "未披露", "null", "none", "None"].includes(compact)) return "";
  if (/未披露业绩基准|暂无业绩基准|无业绩基准|未提供业绩基准|不适用/.test(compact)) return "";
  return text;
}

function benchmarkStatementFromDetail(detail, row) {
  const candidates = [
    detail?.benchmarkMeta?.业绩基准说明,
    detail?.benchmarkStatus?.业绩基准文本,
    fieldItemValue(detail?.profileFields, "业绩基准说明"),
    fieldItemValue(detail?.profileFields, "业绩基准"),
    detail?.summary?.业绩基准说明,
    detail?.summary?.业绩基准,
    row?.业绩基准说明,
    row?.业绩基准
  ];
  for (const value of candidates) {
    const text = validBenchmarkStatement(value);
    if (text) return text;
  }
  return "";
}

function pctOf(part, total) {
  if (!total) return 0;
  return Math.round((part / total) * 10000) / 100;
}

function makeBenchmarkDisclosureRecord(row, detail = null) {
  const statement = detail ? benchmarkStatementFromDetail(detail, row) : "";
  return {
    统一策略ID: raw(row.统一策略ID),
    策略名称: raw(row.策略名称),
    投顾机构: raw(row.投顾机构) || "未披露",
    研报产品类型: raw(row.研报产品类型) || "未分类",
    有业绩基准说明: statement ? 1 : 0,
    业绩基准说明: statement,
    详情文件状态: detail ? "已读取" : "详情缺失"
  };
}

function groupedBenchmarkDisclosureRows(records, groupField) {
  const groups = new Map();
  for (const record of records) {
    const key = raw(record[groupField]) || "未披露";
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(record);
  }
  return [...groups.entries()]
    .map(([key, list]) => {
      const disclosed = list.filter((record) => record.有业绩基准说明).length;
      return {
        [groupField]: key,
        策略数: list.length,
        有业绩基准说明: disclosed,
        无业绩基准说明: list.length - disclosed,
        披露覆盖率: pctOf(disclosed, list.length)
      };
    })
    .sort((a, b) => b.策略数 - a.策略数 || b.有业绩基准说明 - a.有业绩基准说明 || raw(a[groupField]).localeCompare(raw(b[groupField]), "zh-CN"));
}

function buildBenchmarkDisclosureSummary(strategies, disclosureById) {
  const records = (strategies || [])
    .filter(isTtfundStrategy)
    .map((row) => disclosureById.get(raw(row.统一策略ID)) || makeBenchmarkDisclosureRecord(row, null));
  const disclosed = records.filter((record) => record.有业绩基准说明).length;
  const missingDetails = records.filter((record) => record.详情文件状态 === "详情缺失").length;
  return {
    统计口径: "天天基金/投顾渠道全部策略；业绩基准说明只认详情文件中的原始披露文本，不用基准曲线状态替代。",
    总览: {
      策略数: records.length,
      有业绩基准说明: disclosed,
      无业绩基准说明: records.length - disclosed,
      披露覆盖率: pctOf(disclosed, records.length),
      详情缺失策略数: missingDetails
    },
    按研报产品类型: groupedBenchmarkDisclosureRows(records, "研报产品类型"),
    按机构: groupedBenchmarkDisclosureRows(records, "投顾机构"),
    披露样例: records
      .filter((record) => record.有业绩基准说明)
      .slice(0, 8)
      .map((record) => ({
        策略名称: record.策略名称,
        投顾机构: record.投顾机构,
        研报产品类型: record.研报产品类型,
        业绩基准说明: record.业绩基准说明
      }))
  };
}

const filteredOutStrategies = processedStrategies.filter((row) => !isDisplayableStrategy(row));
const displayStrategies = processedStrategies.filter(isDisplayableStrategy);
summary.rawStrategyCount = sourceRawStrategyCount;
summary.filteredOutStrategyCount = filteredOutStrategies.length;
summary.displayStrategyCount = displayStrategies.length;
summary.strategies = processedStrategies;
const allById = new Map(processedStrategies.map((row) => [row.统一策略ID, row]));
const byId = new Map(displayStrategies.map((row) => [row.统一策略ID, row]));
const benchmarkDisclosureById = new Map();

summary.fieldDictionary = summary.fieldDictionary || {};
delete summary.fieldDictionary["主可比池"];
delete summary.fieldDictionary["策略类型"];
delete summary.fieldDictionary["测算风险等级"];
delete summary.fieldDictionary["原披露风险等级"];
delete summary.fieldDictionary["基准风险资产权重"];
delete summary.fieldDictionary["基准风险资产权重"];
delete summary.fieldDictionary["基准风险资产权重说明"];
delete summary.fieldDictionary["基准风险资产权重"];
delete summary.fieldDictionary["业务主分类"];
delete summary.fieldDictionary["风险基础分类"];
delete summary.fieldDictionary["基础分类"];
delete summary.fieldDictionary["基金" + "调整摘要"];
Object.assign(summary.fieldDictionary, {
  "筛选口径": "页面顶部筛选条件同时作用于市场总览、仓位分析、调仓分析和策略对比的所有图表和表格；基准风险资产权重是首层分类和优先筛选口径，其他条件在此基础上继续细分。默认纳入完整策略，以及已具备最新披露业绩和最新持仓明细的扩展样本；D0 持仓缺失和持仓缺失/不入池策略仍然剔除。目标盈系列在市场总览中按同系列多期合并。",
  "时间区间": "用于区间收益、资产分布时间变化和调仓分析的统一观察窗口；近1周、近1月、近3月、近1年等收益指标按对应披露收益字段展示，调仓事件按具体调仓日期过滤。期初期末热力图按每只策略在区间起止目标日期附近的最近可用持仓快照计算，避免把非工作日或未披露日误作0仓位。",
  "基准风险资产权重": "统一按业绩基准中的权益、商品和另类风险资产合计权重分档：0%为L0，0%-10%为L1，之后每10个百分点一档，90%-100%为L10。该字段是策略分类、同类推荐、市场总览和首层筛选的主口径。",
  "基准风险资产权重说明": "说明基准风险资产权重的业务计算口径。若基准仍有超过0.01%的未映射权重，则不硬分档。",
  "基准风险资产权重_百分比": "业绩基准中权益、商品和另类资产权重之和；港股和海外权益是权益子项，不重复计算。",
  "权益中枢": "当前持仓权益基金权重 + 0.5 × 混合基金权重，用于观察策略实际权益配置中心。",
  "固收中枢": "当前持仓债券基金权重 + 货币基金权重 + 0.5 × 混合基金权重。",
  "基准风险资产中枢": "等于基准风险资产权重_百分比，是 L0-L10 分档的连续数值基础。",
  "海外配置中枢": "当前QDII权重与基准海外权益权重中的较高值，用于跨渠道观察海外配置程度。",
  "指数化程度": "当前持仓中指数基金、ETF、ETF联接和指数增强工具的聚合权重。",
  "主动管理程度": "当前持仓中主动管理基金的聚合权重。",
  "风险资产偏离": "权益中枢减去基准风险资产中枢；正值表示实际持仓相对基准更进取，负值表示更保守。",
  "配置风格标签": "由基准风险资产权重、业务分类、市场地域和主动被动组合形成，可按业务问题继续叠加筛选。",
  "对客范围": "全部策略包含当前分析库内全部可用策略；只看对客策略会剔除天天投顾明确标记为当前不对客展示、非对客、隐藏或未展示的策略。非天天投顾策略没有相反标记时默认保留。",
  "策略范围": "按投顾机构归属区分全部策略、仅看广发策略和仅看非广发策略；切换后市场总览、仓位分析和调仓分析中的图表、表格和展开明细同步过滤。",
  "风险等级": "系统测算主风险等级。先分别计算权益暴露档、波动档、回撤档：权益暴露档来自当前持仓权益基金权重；波动档来自策略区间波动率；回撤档来自策略最大回撤。最终风险等级取三项中最高风险档，即风险等级=max(权益暴露档、波动档、回撤档)。页面主分类使用该字段，不使用披露风险等级。",
  "披露风险等级": "渠道或平台原始披露的风险等级，仅用于与系统测算风险等级对照。",
  "披露策略类型": "渠道或平台原始披露的策略类型；未披露时显示未披露。",
  "费率状态": "根据策略基础资料中是否披露投顾费率生成。显示缺失时，说明该策略不能进入费率竞争力、渠道定价或让利空间分析。",
  "年化投顾费率": "策略基础资料披露的年化投顾服务费率；缺失时显示未披露，页面不做推算，避免用不完整费率得出经营结论。",
  "投资经理": "策略基础资料披露的投资经理或管理人姓名；当前字段缺失时不能做投资经理排名、经理画像或经理业绩归因。",
  "业务分类": "销售、产品和投研使用的互斥业务场景。判定顺序：D0持仓缺失保留原分类但不进主图；名称/说明含目标日期、养老、生命周期归目标日期/养老型；明确信号执行机制归信号类策略；目标盈/小目标/小盈加/智盈等品牌，或明确目标收益/达标止盈机制且具备期次、到期、赎回等生命周期证据时归目标盈系列；货币权重>=80%归现金管理型；债券+货币>=90%且权益<10%归纯债/短债型；QDII/海外基金权重>=30%、海外基准权重>=30%或策略名称/披露类型有明确境外市场词时归海外/全球型，通用投资范围、机构品牌、黄金/商品不触发；明确行业主题归主题/行业型；权益>=40%或指数>=45%归偏股配置型；债券+货币>=70%且权益<40%归固收增强型；混合>=20%或权益>=15%或QDII>=5%归多资产配置型；均未命中则保留原分类。",
  "业务分类依据": "展示该策略业务分类命中的具体证据。文本类证据来自策略名称、策略介绍、合同/协议、服务条款、特殊标签；资产类证据来自当前持仓中的权益、债券、货币、QDII、指数、混合权重。例如“货币权重82.30%，达到80%现金管理阈值”。",
  "业务组合分类": "风险等级与业务分类的组合，用于营销机会和投研比较池拆解。",
  "研报产品类型": "调仓分析主可比池，互斥归类。公式/规则：风险等级D0进入“持仓缺失/不入池”；权益基金权重>=70%为股票型；QDII/海外基金权重>=10%、海外基准权重>=10%或名称/披露类型含明确海外市场词且同时具备多资产、REIT等配置特征时为多元配置型；权益<1%且债券+货币>=80%为纯债型；权益<=20%为固收+型；权益<70%为股债混合型；剩余按多元配置或股债混合兜底。该字段用于调仓分析，避免纯债、固收+、股票型混在一起比较。",
  "研报股票子类型": "只对研报产品类型=股票型继续拆分。规则：存在强海外证据或QDII权重>=20%时归QDII型；否则命中行业/主题关键词归行业主题型；命中轮动、赛道、趋势、择时、风格/主题切换归行业轮动；指数基金权重>=45%或名称/说明命中指数、ETF、联接、增强、宽基等归指数驱动；剩余归主动优选。",
  "研报分类依据": "展示研报产品类型和股票子类型命中的可核验证据，主要包含权益权重、债券+货币权重、QDII权重、指数权重，以及策略名称/说明中的全球、主题、轮动、ETF等关键词。例如“权益76.20%，按研报股票型可比池；子类=指数驱动”。",
  "研报大类资产": "调仓和仓位分析使用基金经济暴露快照作为主口径。先保留季报原始资产配置，再按基金标准分类、名称、跟踪指数、ETF联接、FOF、QDII、黄金、固收指数等规则，把基金/其他高占比重映射为可解释资产。公式：策略分类资产权重=sum(策略持仓基金权重*基金经济资产暴露比例/100)。原始季报资产配置只在基金详情中作为审计信息展示。",
  "研报A股行业": "A股行业/主题穿透口径。权益、海外权益、行业主题和指数权益基金优先使用基金季报股票持仓明细及东财股票行业映射；缺少完整股票行业时用主题、指数或名称规则标注质量状态。黄金/商品、纯债、货币、海外债券不适用股票行业穿透，不计入缺失率。公式：策略行业权重=sum(策略持仓基金权重*经济权益资产暴露比例*经济行业暴露比例/10000)。",
  "调仓时间模式": "月度报告模式默认取最近一个有调仓记录的完整月份，按研报月报逻辑复盘；自选区间模式沿用顶部时间区间，近1周、近1月、近3月、近1年等控件同步过滤所有调仓图表和表格。",
  "报告月份": "月度报告模式下使用的观察月份。系统从当前筛选范围内有调仓日期的月份中，优先选择早于当前自然月的最近完整月份；手动切换后，调仓事件、基金月度汇总和策略级资产变化都按该月份过滤。",
  "市场地域": "根据当前持仓中海外或QDII基金暴露、海外业绩基准和策略名称/披露类型中的明确境外市场词识别国内、海外/全球或混合地域；通用投资范围、机构品牌、黄金/商品不触发海外地域。",
  "排名": "在当前筛选和排序条件下重新生成的序号；切换筛选条件或点击表头排序后同步变化。",
  "基金名称": "底层持仓基金的展示名称；同一基金按基金代码和名称合并统计。",
  "基金类型": "底层基金一级标准分类。统一归并为股票型基金、混合型基金、债券型基金、货币市场基金、QDII基金、FOF基金、商品型基金、REITs基金或待补分类，用于基金类型筛选和类型占比图，避免把指数、地域、主题和资产口径混在同一层级。",
  "二级分类": "基金展示细分分类。保留宽基指数、行业/主题指数、主动权益、短债/中短债、纯债/信用债、固收+/偏债混合、美股指数、港股指数、黄金/贵金属等细分口径，用于核验具体产品特征，不替代一级基金类型。",
  "调仓原因": "渠道或 App 对该次调仓披露的原因文本。缺失时页面显示未披露；AI 投研总结会基于调仓前后资产、行业和基金变化补充解释，但不会反向改写原始披露原因。",
  "历史盈利概率": "基于策略披露业绩曲线优先、模拟业绩曲线兜底，滚动计算从任意历史日期买入并持有 1 月、3 月、6 月、1 年后收益大于 0 的窗口占比。盈利概率=正收益窗口数/可计算窗口数。",
  "策略对比": "数据洞察页中面向最多 5 只策略的横向比较视图。核心指标、业绩曲线、资产配置、行业配置、权益主题、选基效果和历史盈利概率均只使用当前选中的策略集合计算。",
  "大类资产配置对比": "策略对比页按最新持仓快照中的研报大类资产分类聚合。每只基金可按资产暴露拆分进入多个大类，策略内各大类权重合计后用于横向比较。",
  "行业配置对比": "策略对比页按最新持仓快照中的研报A股行业分类聚合。行业暴露来自基金分类和拆分规则，不等同于基金公司披露的股票明细行业持仓。",
  "权益主题配置对比": "策略对比页按最新持仓快照中的权益行业主题分类聚合，用于观察权益方向的主题集中度；缺少主题拆分的数据不强行归类。",
  "选基效果": "策略对比页的选基效果由两部分组成：历史调仓胜率按已完成评价的调仓事件中跑赢或正超额事件占比计算；当前持仓选基质量按近1月、近3月、近6月、近1年同类收益排名前50%的基金仓位占比计算。",
  "历史调仓胜率": "按策略历史调仓事件的调仓评价或调仓超额收益判断胜负。跑赢、胜、正超额记为胜；跑输、负超额记为负；无收益窗口或评价不足的事件不纳入分母。",
  "前50%仓位占比": "对策略当前正权重持仓逐只基金计算。基金同类分组优先使用投顾资产分类桶、天天基金细分类/大类和标准资产分类；在同一分组内按近1月、近3月、近6月、近1年复权收益排名，排名位于前50%的基金权重合计除以当前持仓正权重合计。",
  "基金同类分组": "基金用于同类排名的分组。优先取基金标准分类字典中的投顾资产分类桶，其次取天天基金细分类、天天基金大类、标准资产细类或标准资产大类；仍缺失时归入未分类。",
  "近一月同类排名": "基金近31个自然日复权收益在同类分组内从高到低排序得到的名次，使用基金日度净值表中目标日前后可用净值点计算。",
  "近三月同类排名": "基金近92个自然日复权收益在同类分组内从高到低排序得到的名次，使用基金日度净值表中目标日前后可用净值点计算。",
  "近6月同类排名": "基金近183个自然日复权收益在同类分组内从高到低排序得到的名次，使用基金日度净值表中目标日前后可用净值点计算。",
  "近1年同类排名": "基金近365个自然日复权收益在同类分组内从高到低排序得到的名次，使用基金日度净值表中目标日前后可用净值点计算。",
  "基金分类依据": "该基金类型的命中证据。优先使用基金代码/名称字典；其次使用平台持仓披露的资产类型或分组；再按基金名称关键词识别货币、短债、纯债、可转债、指数、行业主题、港股、美股、QDII、黄金、商品、REIT、FOF等。展示为具体证据串，方便核验某只基金为什么归入该类型。",
  "资产暴露": "该基金进入研报大类资产图的经济拆分比例。经济暴露先保留原始季报资产配置，再对基金/其他、ETF联接、FOF、QDII、黄金、商品、固收指数等做业务重映射。缺少足够证据时才使用基金类型规则兜底，并在质量状态中标注。",
  "行业暴露": "该基金进入行业/主题图的经济拆分比例。权益、海外权益、行业主题和指数权益要求行业或主题证据；黄金/商品、纯债、货币、海外债券不适用股票行业穿透。行业结构优先来自季报股票持仓和股票行业映射，缺少时使用主题/指数规则并标注质量状态。",
  "基金分类来源": "基金分类和暴露字段的数据来源。基金经济暴露快照表示已对原始季报资产配置做标准化重映射；规则估算表示当前未取得足够穿透证据，使用基金类型、名称和平台披露分类兜底。",
  "基金穿透报告期": "当前基金经济暴露使用的报告期。历史持仓快照会优先选择报告期不晚于持仓日期的数据，避免使用未来报告期回填历史。",
  "基金穿透覆盖状态": "基金穿透覆盖状态。exact_quarterly_asset_and_stock 表示已有季报资产配置和股票持仓行业推导；exact_quarterly_asset_only 表示仅有季报资产配置；空值表示走规则估算兜底。",
  "经济资产暴露": "基金经济暴露快照中的标准资产拆分结果，是页面资产配置、策略对比、主题分析和AI实体识别的主业务口径。",
  "经济行业暴露": "基金经济暴露快照中的行业或主题拆分结果。只对权益、海外权益、行业主题、指数权益等适用对象要求覆盖；黄金、纯债、货币、海外债券不适用股票行业穿透。",
  "原始资产暴露": "基金季报资产配置的原始解析结果，仅用于审计追溯。若原始结果出现基金/其他高占比，页面业务分析仍以经济资产暴露为准。",
  "穿透方法": "构建基金经济暴露时命中的规则组合，例如黄金商品、固收优先、QDII权益、ETF联接、FOF/QDII-FOF或标准分类兜底。",
  "经济暴露证据说明": "经济暴露重映射的可核验证据链，包括来源字段、命中关键词、原始基金/其他占比和标准分类信息。",
  "经济暴露质量状态": "经济暴露记录的质量标签。通过或基金/其他已重映射可作为主业务口径；标准分类兜底和需人工补充应在负责人复盘时降级使用。",
  "基金公司": "优先使用持仓基金披露的管理人；缺失时根据基金名称前缀识别基金公司，用于公司维度占比和调仓方向分析。",
  "行业主题": "资产主题展示口径，按基金资产暴露分摊。非权益资产按现金管理、纯债/固收、海外债券、贵金属、能源/商品、海外REIT等归类；A股可识别行业主题按申万一级行业映射到医药生物、电力设备/新能源、电子/半导体、计算机/人工智能、传媒/互联网、食品饮料、金融地产、周期资源等主题；A股宽基或主动权益无行业主题时归宽基/主动权益。",
  "行业大类": "行业主题上层归并，用于观察大方向变化。现金、固收、商品、海外保持独立；A股行业进一步归为科技制造、消费医药、金融周期/价值、权益宽基/均衡等。公式：行业大类权重=sum(对应行业主题权重)。",
  "权益行业主题": "权益资产的主题暴露口径。A股行业主题按申万一级行业映射到主题；宽基指数和主动权益基金因缺少底层股票行业穿透，统一归为宽基/主动权益；港股、美股及海外区域权益归为海外宽基/区域。现金、固收和商品不进入该字段。",
  "权益行业大类": "权益行业主题的上层归并，包含科技制造、消费医药、金融周期/价值、海外权益、宽基/主动权益等。公式：权益行业大类权重=sum(对应权益行业主题权重)，不统计现金、固收和商品基金。",
  "区间收益率": "在当前时间区间内可获得的基金或策略收益表现；基金持仓表取该基金在相关持仓样本中的可用收益中位数。",
  "权重占比": "当前筛选范围内该基金、基金类型或基金公司持仓权重合计，除以同范围全部持仓权重合计后得到。",
  "全策略权重占比": "当前筛选范围内该基金在所有投顾策略期末持仓权重中的占比，包含广发策略和非广发策略；用于观察总体渗透，不直接等同外部认可度。",
  "外部策略权重占比": "当前筛选范围内非广发投顾策略期末仓位中配置到该基金的权重占比。广发基金机会表默认按该指标排序，用于剔除广发自家策略配置干扰。",
  "广发策略权重占比": "当前筛选范围内广发投顾策略期末仓位中配置到该基金、基金类型或基金公司的权重占比，用于区分内部配置和外部策略持有。",
  "非广发策略权重占比": "当前筛选范围内非广发投顾策略期末仓位中配置到该基金、基金类型或基金公司的权重占比，用于观察外部策略是否认可该底层产品。",
  "持仓权重合计": "当前分析库中持有该基金的各投顾策略期末持仓比例求和。该数值是跨策略合计点位，不代表某一只组合的仓位。",
  "全市场权重占比": "当前分析库全部策略基金持仓权重中，该基金权重合计所占比例。公式：全市场权重占比=该基金期末持仓比例合计/全部基金期末持仓比例合计*100%。",
  "高频持仓基金门槛": "全市场高频持仓基金、广发基金高频持仓基金和广发基金机会表只把单策略期末持仓比例大于0.5%的基金计为有效持仓。低于等于0.5%的尾部仓位仍保留在策略详情和基金详情，但不进入高频统计，避免小仓位噪声放大持仓策略数。",
  "日涨跌幅": "策略披露业绩曲线最近两个可用点之间的涨跌幅。若两点间隔超过7天，视为日度数据不连续，不参与风险等级内排名。",
  "近6月": "以策略披露业绩曲线最新日期向前约183天计算区间收益。若观察窗口内存在超过45天的披露缺口，显示未披露并不参与排名。",
  "中位权重": "当前筛选范围内持有该基金或分类的单个策略持仓比例中位数，用于观察典型配置力度。",
  "持仓策略数": "当前筛选范围内期末仍持有该基金、基金类型或基金公司的去重策略数量；同一策略在同一统计项下只计一次。",
  "外部持仓策略数": "当前筛选范围内期末持有该基金的非广发投顾策略数量；同一策略只计一次，用于识别广发基金是否被外部策略验证。",
  "广发策略持仓数": "当前筛选范围内期末持有该基金、基金类型或基金公司的广发投顾策略数量；同一策略只计一次。",
  "非广发策略持仓数": "当前筛选范围内期末持有该基金、基金类型或基金公司的非广发投顾策略数量；同一策略只计一次。",
  "外部增减策略数": "当前筛选范围内，非广发投顾策略对该基金期末持仓比例高于初始持仓比例的策略数和低于初始持仓比例的策略数，按“增/减”合并展示。",
  "外部净增配中位数": "当前筛选范围内，非广发投顾策略对该基金的单策略权重变化中位数；单策略权重变化等于期末持仓比例减初始持仓比例，避免用跨策略合计点位夸大调仓方向。",
  "增持策略数": "当前筛选范围内该基金期末持仓比例高于初始持仓比例的去重策略数量。",
  "减持策略数": "当前筛选范围内该基金期末持仓比例低于初始持仓比例的去重策略数量。",
  "初持仓比例": "该策略在本次持仓观察前或上次调仓后的该基金持仓比例；缺失时按0处理。",
  "期末持仓比例": "该策略最新可用持仓快照中该基金的持仓比例。",
  "类型权重占比": "当前筛选范围内某一基金类型的持仓权重合计，除以同范围全部基金持仓权重合计。",
  "首月占比": "最近6个月观察窗口首个月中，该基金类型持仓权重占当月全部持仓权重的比例。",
  "最新占比": "最近6个月观察窗口最后一个月中，该基金类型持仓权重占当月全部持仓权重的比例。",
  "区间变化": "最新占比减去首月占比，单位为百分点，用于判断该资产类型在最近6个月的升降方向。",
  "区间净增配": "当前时间区间内该基金或分类在调仓事件中的调后权重减调前权重合计，正数表示净增配，负数表示净减配；没有发生该方向调仓时按0变化或不展示该行。",
  "经营动作": "按市场数量、广发覆盖、收益差和回撤优势归纳出的业务观察标签，包括产品空白、货架偏薄、可包装营销、能力复盘、机会跟踪和持续跟踪；仅用于历史兼容字段说明。",
  "经营判断": "对业务观察标签的文字解释，说明该分类为什么适合产品补齐、销售包装、投研复盘或继续跟踪。",
  "经营优先级": "历史业务观察排序分数，综合市场样本规模、广发覆盖不足程度、相对收益差和业务观察类型生成；只用于排序，不作为绝对评分。",
  "产品空白": "经营动作之一，指当前筛选范围内某类市场已有规模样本但广发没有对应产品，需要判断是否补货架、联营或重新包装。",
  "货架偏薄": "经营动作之一，指市场同类产品数量较多但广发覆盖率偏低，通常需要补充不同风险档、期限、主题或渠道版本。",
  "可包装营销": "经营动作之一，指广发同类中位收益不弱于市场，且回撤没有明显劣势，适合沉淀销售话术、渠道露出和重点名单。",
  "能力复盘": "经营动作之一，指广发同类收益明显落后市场，需要拆到代表产品、底层基金和调仓节奏看差距来源。",
  "机会跟踪": "经营动作之一，指市场有一定规模、广发已有布局但覆盖不深，适合继续跟踪头部竞品和持仓偏好，寻找产品或渠道切入点。",
  "代表竞品": "当前维度内非广发策略按所选时间区间收益排序靠前的代表产品，用于明确销售、产品和投研复盘时应该对标谁。",
  "广发代表": "当前维度内广发策略按所选时间区间收益排序靠前的代表产品，用于判断广发是否已有可包装、可主推或需复盘的产品。",
  "业务含义": "对数据可用性、样本范围或指标限制的业务解释，帮助判断该数据能否直接用于分析。",
  "核验证据": "跳转到策略列表或明细页的下钻入口。链接会带上当前策略范围、对客范围、业务分类、市场地域、风险等级或筛选条件，用于核对具体样本。",
  "业务维度": "观察维度，区分该任务来自研报产品类型还是业务分类，避免把产品可比池和销售场景混在一起。",
  "场景": "当前经营动作对应的具体研报产品类型或业务分类，例如固收+型、股票型、目标盈系列产品、海外/全球型等。",
  "证据": "生成该业务观察标签时使用的核心量化依据，包含市场数量、广发数量、广发覆盖率、广发相对市场收益差和回撤优势。",
  "下一步": "将经营动作翻译成可执行工作，例如竞品货架拆解、补产品版本、沉淀销售话术、投研能力复盘或月度跟踪。",
  "负责人关注点": "面向投顾业务负责人的决策提醒，说明该任务应优先关注产品布局、渠道销售、营销边界、投研复盘还是继续观察。",
  "项目": "数据可用性表中的检查项，例如源表策略总数、可核验策略记录、未进入策略明细、有效策略样本、目标盈期次归并、D0持仓缺失、费率缺失、披露风险缺失、投资经理缺失、调仓事件和持仓基金明细。",
  "数值": "数据可用性表中对应项目的数量；源表策略总数和未进入策略明细为全局数据接入口径，可核验策略记录和缺失项按当前页面筛选口径从策略列表明细计算，有效策略样本按剔除D0并归并目标盈期次后的经营口径计算。",
  "广发覆盖率": "当前分类中广发策略数量除以全市场策略数量，用于衡量广发在该业务分类下的货架覆盖程度。",
  "收益差": "当前分类中广发策略区间收益中位数减去全市场策略区间收益中位数。",
  "回撤优势": "当前分类中全市场最大回撤中位数减去广发最大回撤中位数；数值越高表示广发回撤越低。",
  "调仓逻辑": "根据调仓标题、原因和涉及资产归纳为风险控制、基金替换、固收配置、权益主题切换、海外商品配置或常规再平衡。",
  "事件数": "当前筛选范围内符合条件的调仓事件数量。",
  "产品数": "当前全局筛选条件下归入该研报产品类型的去重策略数。公式：产品数=count(distinct 策略)，其中D0持仓缺失不进入洞察主图；目标盈多期产品在系列口径下按同一系列合并。",
  "调仓产品数": "当前调仓观察窗口内发生有效调仓的去重策略数。公式：调仓产品数=count(distinct 策略)，条件为有调仓事件，或该策略至少一个资产/行业/基金的|调后权重-调前权重|>0.0001。",
  "调仓覆盖率": "同类策略调仓覆盖面。公式：调仓覆盖率=调仓产品数/产品数*100%。它只说明本期有多少策略动了仓位，不代表调仓质量。",
  "中位换手": "同类策略调仓强度的典型值。公式：median(单次换手率)，只统计当前窗口内有换手率记录的调仓事件。",
  "效果评价覆盖": "类型总览中的调仓效果覆盖口径，展示为“胜率｜可评价事件数”。公式：可评价事件数=count(调仓后观察窗口已结束且能判定胜负的调仓事件)，胜率=正向事件数/可评价事件数*100%；尚未到观察窗口的事件显示待观察，不参与胜率分子分母。",
  "主资产方向": "从策略级大类资产变化中挑选最清晰方向。先对每只策略每个研报大类资产计算策略资产净变化=sum(调后权重)-sum(调前权重)，再生成资产方向判断；优先展示方向清晰且|中位净变化|靠前的资产。方向分歧或中位变化接近0时显示未形成强方向。",
  "参与策略": "当前行命中的去重策略数。公式：对每只策略在本行资产/行业下计算策略分类净变化=sum(调后权重)-sum(调前权重)，若|策略分类净变化|>0.0001或调仓强度>0.0001，则计入参与策略；同一策略在同一行只算一次。",
  "判断": "方向标签由参与策略的增减分布和中位净变化共同生成。公式：增持占比=增持策略数/(增持策略数+减持策略数)。若增持占比>=60%且中位净变化>0.2点，则为多数策略增配，参与策略少于8个时标为低覆盖增配；若增持占比<=40%且中位净变化<-0.2点，则为多数策略减配/低覆盖减配；若中位净变化绝对值<0.2点，或增减方向不一致，判为方向分歧；其余按净变化和中位数标为温和增配/温和减配。",
  "增/减策略": "当前行中增持策略数和减持策略数。公式：增持策略数=count(策略分类净变化>0.0001)，减持策略数=count(策略分类净变化<-0.0001)，按去重策略统计。",
  "典型变化": "历史兼容字段，含义等同于中位净变化。公式：median(策略分类净变化)，单位为仓位百分点。",
  "中位净变化": "当前行参与策略的单策略净变化中位数。公式：先对每只策略在该资产/行业下汇总策略分类净变化=sum(调后权重)-sum(调前权重)，再取median(策略分类净变化)。它表示“典型参与策略”在该分类上调高或调低了多少仓位，不是所有策略合计变化。",
  "累计净变化": "当前行参与策略净变化合计。公式：sum(策略分类净变化)，单位为仓位百分点；只作为强度参考，不能单独作为市场方向结论。",
  "策略调整摘要": "当前分类下变化幅度最大的策略摘要。公式：先对每只策略在该资产/行业下汇总策略分类净变化=sum(调后权重)-sum(调前权重)，再按|策略分类净变化|降序取第一名，展示投顾机构、策略名称、增配/减配方向、净变化、调前权重和调后权重。",
  "详情": "当前行的可点击核验入口。调仓方向表中点击后展示参与策略清单，逐只列出投顾机构、调前权重、调后权重、净变化、调仓强度，以及该分类下具体调增/调减基金和比例。",
  "业务读法": "由增持策略数、减持策略数和中位净变化直接生成。例如“3个策略增配、1个策略减配；中位净变化+1.2点”。它只解释当前行统计结果，不引入额外主观判断。",
  "胜率": "已到调仓后观察窗口且有结果评价的事件中，正向事件数量除以已评价事件数量；观察窗口未到的事件不参与胜率，但仍参与调仓事件、换手、资产变化和基金流向统计。",
  "可评价胜率": "类型总览矩阵中的效果评价覆盖字段。先统计该研报产品类型内已到观察窗口并有结果评价的调仓事件数；未到观察窗口显示待观察，不影响调仓主分析。",
  "可评价事件数": "调仓事件中已到观察窗口且带有明确胜负或结果评价、可用于胜率统计的事件数量。",
  "调仓胜率": "可评价调仓事件中正向事件数量除以可评价事件数量。",
  "样本判断": "调仓效果表的样本覆盖说明。公式：若可评价事件数>0，显示“已评价N个”；若可评价事件数=0，显示“效果待观察”。该字段不判断好坏，只说明胜率样本是否已经形成。",
  "涉及资产": "调仓事件明细中本次调仓覆盖的资产方向。由该事件涉及基金的研报大类资产去重后拼接，例如A股、债券、货币及现金；若明细中无法识别资产，则使用调仓标题或基金类型兜底。",
  "胜负": "调仓事件效果判定。只在调仓后观察窗口已结束且有可比收益时计算：若调仓后收益或方向性超额为正，记为胜；若为负，记为负；观察窗口未结束或缺少可比收益时不参与胜率统计。",
  "广发Top3平均收益": "同策略类型内，广发基金按所选收益指标排序前3只策略的平均收益；少于3只时按已有可计算策略平均。",
  "广发Top5平均收益": "同策略类型内，广发基金按所选收益指标排序前5只策略的平均收益；少于5只时按已有可计算策略平均。",
  "调仓质量风险": "调仓质量分析中识别出的风险点，例如胜率落后、仍处观察期、超额为负或交易逻辑不稳定。",
  "平均调仓超额": "当前筛选范围内可用调仓超额表现的平均值，用于粗略衡量调仓后相对收益质量。",
  "平均单次换手率": "当前筛选范围内单次调仓换手率的平均值，用于观察调仓强度。",
  "净方向": "底层基金公司在当前窗口内所有被调仓产品的净增配合计大于1点时为整体加仓，小于-1点时为整体减仓，介于两者之间为结构轮动。",
  "主加仓资产": "某基金公司旗下产品在当前窗口内净增配最高的研报大类资产或主题，展示该资产和对应净变化点位。",
  "主减仓资产": "某基金公司旗下产品在当前窗口内净减配最明显的研报大类资产或主题，展示该资产和对应净变化点位。",
  "加仓权重": "当前筛选范围内所有正向权重变化的合计，单位为百分点。",
  "减仓权重": "当前筛选范围内所有负向权重变化的绝对值合计，单位为百分点。",
  "调仓强度": "当前筛选范围内加仓权重与减仓权重的合计，表示调仓动作规模，不代表净方向。",
  "单次换手率": "一次调仓中买入与卖出权重变动的综合比例，用于衡量该次调仓幅度。",
  "净方向": "基金公司在当前筛选区间内整体净增配大于阈值记为整体加仓，净减配大于阈值记为整体减仓，其余记为结构轮动。",
  "主加仓资产": "该基金公司净增配最大的资产主题及其净增配幅度。",
  "主减仓资产": "该基金公司净减配最大的资产主题及其净减配幅度。",
  "净增配": "主动调仓口径的净变化。公式：净增配=sum(调后权重)-sum(调前权重)。正值表示净加仓，负值表示净减仓；按基金、基金公司、资产或行业维度汇总时先在策略内合并，再做跨策略统计。",
  "调前权重": "调仓事件发生前，该策略在对应基金、资产或行业上的持仓权重合计。若一只策略同一窗口内有多条同类调仓，先把该策略该分类的调前权重汇总后参与计算。",
  "调后权重": "调仓事件发生后，该策略在对应基金、资产或行业上的持仓权重合计。净变化=调后权重-调前权重，用于判断增配或减配。",
  "资产类型": "按底层基金类型归并后的资产方向，例如债券型、混合型、指数型、股票型、货币型、商品型或QDII/海外；历史调仓只有策略场景、缺少基金名称时，用该策略场景对应的主资产类型兜底。",
  "期初占比": "对每只策略分别取时间区间起点目标日期之前最近一次可用持仓快照；若起点前没有仓位，则取起点后的第一条可用仓位。再将该分类权重除以对应投顾机构或全市场的同口径总权重。",
  "期末占比": "对每只策略分别取时间区间终点目标日期之前最近一次可用持仓快照，再将该分类权重除以对应投顾机构或全市场的同口径总权重；热力图默认按全市场期末占比从高到低排序。",
  "占比变化": "期末占比减去期初占比，单位为百分点；正值表示该分类在区间内仓位占比上升。起止日没有快照时按每只策略最近可用仓位补齐，不把缺失日期当作0仓位。",
  "快照日期": "该条持仓分类快照对应的仓位披露日或调仓日。期初期末热力图不再按自然月硬切，而是按每只策略在区间起止目标日期之前最近一次可用仓位取数；若策略在起点前没有仓位，则取起点后的第一条可用仓位。",
  "快照类型": "区分当前仓位和历史调仓仓位。期初期末热力图使用两类快照共同还原策略在目标日期附近的有效仓位。",
  "总点位": "区间内加仓点位与减仓点位绝对值之和，用于衡量该资产主题或基金公司的调仓强度。",
  "加仓权重": "区间内正向权重变化的合计值，只统计增配和买入部分。",
  "减仓权重": "区间内负向权重变化的绝对值合计，只统计减配和卖出部分。",
  "调仓强度": "各资产主题净增配绝对值合计，用于衡量基金公司资产调整力度。",
  "资产主题": "根据基金名称和基金类型归并为固收、货币、权益宽基、权益行业主题、海外、商品等调仓观察主题。",
  "调仓策略数": "当前筛选范围内对该基金或资产主题发生有效权重变化的去重策略数量。",
  "中位净增配": "当前筛选范围内单个策略对该基金净增配幅度的中位数。",
  "调仓后收益贡献": "调仓后该基金持仓权重与后续收益表现估算得到的贡献，用于辅助观察调仓效果。",
  "有业绩基准说明": "天天基金/投顾渠道策略详情中披露了可读取的业绩基准说明文本。只认业绩基准说明、业绩基准或基准文本等原始文本字段，不用仅有基准曲线替代。",
  "无业绩基准说明": "天天基金/投顾渠道策略详情中没有可读取的业绩基准说明文本；详情文件缺失也计入未披露。",
  "披露覆盖率": "有业绩基准说明的策略数除以对应统计口径下的策略数，展示为百分比。",
  "详情缺失策略数": "策略列表存在但对应详情文件未能读取的数量；这类样本无法核验业绩基准说明，披露统计中按未披露处理。"
});
for (const [field, text] of Object.entries(summary.fieldDictionary)) {
  if (typeof text !== "string") continue;
  summary.fieldDictionary[field] = cleanTerminology(text);
}

function rebalanceBusinessKey(event) {
  const eventId = raw(event.调仓事件ID);
  if (eventId) return eventId;
  const turn = num(event.单次换手率);
  const assetKey = raw(event.涉及资产原始编码) || raw(event.涉及资产);
  const businessKey = [
    event.统一策略ID,
    event.调仓日期,
    event.调仓标题,
    event.调仓原因,
    assetKey,
    turn === null ? "" : turn.toFixed(4)
  ].map(raw).join("｜");
  return businessKey;
}

function preferRebalanceEvent(event, existing) {
  const score = (row) => {
    let value = 0;
    if (raw(row.胜负) || raw(row.结果评价)) value += 100;
    if (num(row.调仓超额) !== null || num(row.方向性超额) !== null) value += 50;
    value += Math.min(30, num(row.调仓基金数) || 0);
    if (raw(row.披露日期)) value += 1;
    return value;
  };
  return score(event) > score(existing);
}

function eventAssetLookupKeys(event) {
  return [
    raw(event.调仓事件ID),
    `${raw(event.统一策略ID)}｜${raw(event.调仓日期)}`
  ].filter(Boolean);
}

function buildEventReportAssetLookup(rows) {
  const lookup = new Map();
  for (const row of rows || []) {
    if (row.分类字段 !== "研报大类资产") continue;
    const asset = raw(row.分类);
    if (!reportAssetSet.has(asset)) continue;
    for (const key of eventAssetLookupKeys(row)) {
      if (!lookup.has(key)) lookup.set(key, new Set());
      lookup.get(key).add(asset);
    }
  }
  return lookup;
}

function translatedEventAssets(event, lookup) {
  for (const key of eventAssetLookupKeys(event)) {
    const assets = lookup.get(key);
    if (assets?.size) return sortedReportAssetText(assets);
  }
  return fallbackReportAssetText(event.涉及资产);
}

function enrichRebalanceEventAssets(events, lookup) {
  return (events || []).map((event) => {
    const assets = translatedEventAssets(event, lookup);
    const withAssets = {
      ...event,
      涉及资产: assets || raw(event.涉及资产) || "未识别资产"
    };
    return {
      ...withAssets,
      调仓逻辑: rebalanceLogic(withAssets)
    };
  });
}

summary.rebalanceEvents = dedupeRowsByKey((summary.rebalanceEvents || []).map((event) => {
  const base = allById.get(event.统一策略ID) || {};
  return {
    ...event,
    涉及资产原始编码: raw(event.涉及资产原始编码) || raw(event.涉及资产),
    风险等级: base.风险等级 || "未分类",
    业务分类: base.业务分类 || "未分类",
    研报产品类型: base.研报产品类型 || "未分类",
    研报股票子类型: base.研报股票子类型 || "",
    市场地域: base.市场地域 || "未分类",
    主动被动: base.主动被动 || "未分类",
    披露风险等级: base.披露风险等级 || "未披露",
    披露策略类型: base.披露策略类型 || "未披露",
    天天展示状态: base.天天展示状态 || "",
    天天当前对客展示: base.天天当前对客展示 || "",
    是否广发策略: isGuangfaStrategy(base) ? "是" : "否",
    调仓逻辑: rebalanceLogic(event)
  };
}), rebalanceBusinessKey, preferRebalanceEvent);

const fundAgg = new Map();
const companyAgg = new Map();
const riskFundAgg = new Map();
const riskCompanyAgg = new Map();
const assetAgg = new Map();
const timelineAgg = new Map();
const industryTimelineAgg = new Map();
const holdingSnapshotCategoryRows = [];
const currentHoldingStrategyRows = [];
const rebalanceEventByKey = new Map();
const rebalanceEventById = new Map();
for (const event of summary.rebalanceEvents || []) {
  rebalanceEventByKey.set(`${raw(event.统一策略ID)}｜${raw(event.调仓日期)}`, event);
  if (raw(event.调仓事件ID)) rebalanceEventById.set(raw(event.调仓事件ID), event);
}

function rebalanceEventForSnapshot(strategyId, snap) {
  return rebalanceEventById.get(raw(snap?.id)) || rebalanceEventByKey.get(`${raw(strategyId)}｜${snapshotDate(snap)}`) || {};
}

function enrichSnapshotEventFields(strategyId, snap) {
  if (!/历史调仓/.test(raw(snap?.类型))) return snap;
  const event = rebalanceEventForSnapshot(strategyId, snap);
  snap.调仓事件ID = raw(event.调仓事件ID) || raw(snap.id);
  snap.披露日期 = raw(event.披露日期) || raw(snap.披露日期);
  snap.调仓原因 = raw(event.调仓原因) || raw(snap.调仓原因);
  snap.涉及资产 = raw(event.涉及资产) || raw(snap.涉及资产);
  snap.调仓逻辑 = raw(event.调仓逻辑) || rebalanceLogic({ ...event, ...snap });
  snap.调仓标题 = raw(event.调仓标题) || raw(snap.标题);
  return snap;
}

function alignContributionCurvesToSnapshots(detail) {
  const curves = detail.contributionCurves || {};
  const entries = Object.entries(curves);
  if (!entries.length) return;
  const byDate = new Map();
  for (const [curveId, payload] of entries) {
    const date = raw(payload?.起始日期);
    if (!date) continue;
    if (!byDate.has(date)) byDate.set(date, []);
    byDate.get(date).push([curveId, payload]);
  }
  for (const snap of detail.positionSnapshots || []) {
    if (raw(snap?.id) === "current" || curves[raw(snap?.id)]) continue;
    const candidates = byDate.get(snapshotDate(snap)) || [];
    if (!candidates.length) continue;
    const preferred = candidates.find(([, payload]) => {
      const series = payload?.series || {};
      return ["调仓前仓位模拟", "调仓后仓位实际"].some((name) => Array.isArray(series[name]?.points) && series[name].points.length >= 2);
    }) || candidates[0];
    curves[raw(snap.id)] = preferred[1];
    snap.贡献曲线ID = preferred[0];
  }
  detail.contributionCurves = curves;
}

function snapshotDate(snap) {
  return raw(snap?.日期).slice(0, 10);
}

function snapshotHoldings(snap) {
  return Array.isArray(snap?.holdings) ? snap.holdings : [];
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

function contributionPayloadForSnapshot(detail, snap) {
  const curves = detail?.contributionCurves || {};
  const snapId = raw(snap?.id);
  if (snapId && curves[snapId]) return curves[snapId];
  const date = snapshotDate(snap);
  if (!date) return null;
  const candidates = Object.values(curves).filter((payload) => raw(payload?.起始日期) === date);
  return candidates.find((payload) => hasDrawableContributionPayload(payload)) || candidates[0] || null;
}

function snapshotChoiceScore(snap, strategyId, index, detail = null) {
  const date = snapshotDate(snap);
  const matchedEvent = rebalanceEventByKey.get(`${raw(strategyId)}｜${date}`);
  const contribution = contributionPayloadForSnapshot(detail, snap);
  let score = index / 1000000;
  if (raw(snap?.id) === "current") score += 100000;
  if (hasDrawableContributionPayload(contribution)) score += 50000;
  else if (contribution) score += 25000;
  if (matchedEvent && raw(snap?.id) === raw(matchedEvent.调仓事件ID)) score += 10000;
  if (raw(snap?.调仓原因)) score += 5000;
  if (raw(snap?.AI投研总结 || snap?.投研摘要)) score += 3000;
  if (/历史调仓/.test(raw(snap?.类型))) score += 1000;
  score += Math.min(999, snapshotHoldings(snap).length);
  return score;
}

function dedupePositionSnapshots(snapshots, strategyId, detail = null) {
  const selected = new Map();
  (snapshots || []).forEach((snap, index) => {
    const date = snapshotDate(snap);
    const key = raw(snap?.id) === "current" ? "current" : date;
    if (!key) return;
    const candidate = { snap, score: snapshotChoiceScore(snap, strategyId, index, detail) };
    if (!selected.has(key) || candidate.score > selected.get(key).score) selected.set(key, candidate);
  });
  return [...selected.values()]
    .map((item) => item.snap)
    .sort((a, b) => {
      if (raw(a?.id) === "current") return -1;
      if (raw(b?.id) === "current") return 1;
      return snapshotDate(b).localeCompare(snapshotDate(a));
    });
}

function enrichSnapshotResearchFields(strategyId, row, snap) {
  if (!/历史调仓/.test(raw(snap?.类型))) return;
  const researchAgg = makeRebalanceResearchAgg();
  for (const holding of snap.holdings || []) {
    const fundCode = raw(holding.基金代码);
    const rawFundName = raw(holding.基金名称);
    const assetContext = [rawFundName, row?.业务分类, row?.策略名称, snap.标题, holding.资产类型, holding.分组].map(raw).filter(Boolean).join(" ");
    const fundClass = classifyFundStandard(fundCode, rawFundName, { ...holding, __asOfDate: snap?.日期 }, assetContext);
    addRebalanceResearchMove(researchAgg, {
      基金代码: fundCode,
      基金名称: fundClass.fundName,
      权重变化: nz(holding.权重变化)
    }, fundClass);
  }
  const event = rebalanceEventForSnapshot(strategyId, snap);
  const assets = sortedReportAssetText([...researchAgg.assets.keys()]);
  if (assets) snap.涉及资产 = assets;
  const summaryText = buildRebalanceResearchSummary(snap, event, researchAgg);
  if (summaryText) {
    snap.AI投研总结 = summaryText;
    snap.投研摘要 = summaryText;
  }
}

function enrichSnapshotHoldingClassification(row, snap) {
  for (const holding of snap?.holdings || []) {
    const fundCode = raw(holding.基金代码);
    const rawFundName = raw(holding.基金名称);
    const assetContext = [
      rawFundName,
      row?.业务分类,
      row?.策略名称,
      snap?.标题,
      holding.资产类型,
      holding.分组,
      holding.二级分类
    ].map(raw).filter(Boolean).join(" ");
    const fundClass = classifyFundStandard(fundCode, rawFundName, { ...holding, __asOfDate: snap?.日期 }, assetContext);
    const primaryFundType = standardFundPrimaryType(fundClass);
    const secondaryCategory = secondaryCategoryFromFundClass(holding, fundClass);
    if (fundClass.fundName && isMissingClassValue(holding.基金名称)) holding.基金名称 = fundClass.fundName;
    holding.基金类型 = primaryFundType;
    if (isMissingClassValue(holding.资产类型)) holding.资产类型 = primaryFundType || fundClass.reportAssetClass;
    if (isMissingClassValue(holding.分组)) holding.分组 = secondaryCategory;
    holding.二级分类 = secondaryCategory;
    if (isMissingClassValue(holding.研报大类资产)) holding.研报大类资产 = fundClass.reportAssetClass;
    if (isMissingClassValue(holding.行业主题)) holding.行业主题 = fundClass.industryTheme;
  }
}

function sanitizeDetailSnapshots(detail, row = {}) {
  const strategyId = raw(row?.统一策略ID || detail?.summary?.统一策略ID || detail?.id);
  const snapshots = dedupePositionSnapshots(detail.positionSnapshots || [], strategyId, detail);
  for (const snap of snapshots) enrichSnapshotHoldingClassification(row || detail.summary || {}, snap);
  for (const snap of snapshots) enrichSnapshotEventFields(strategyId, snap);
  detail.positionSnapshots = snapshots;
  alignContributionCurvesToSnapshots(detail);
  for (const snap of snapshots) enrichSnapshotResearchFields(strategyId, row || detail.summary || {}, snap);
  return snapshots;
}

const rebalanceFundRows = [];
const rebalanceFundCategoryRows = [];
const rebalanceFundMonthlyAgg = new Map();
const directionAgg = new Map();
const gfFundOpportunityAgg = new Map();
const strategyAssetChangeAgg = new Map();
let detailCount = 0;
let holdingRows = 0;
let orphanDetailCount = 0;
const processedDetailFiles = new Set();

for (const file of fs.readdirSync(detailsDir).filter((name) => name.endsWith(".js"))) {
  const filePath = path.join(detailsDir, file);
  const detail = loadDetail(filePath);
  const row = allById.get(detail.id);
  if (!row) continue;
  processedDetailFiles.add(path.resolve(filePath).toLowerCase());
  detailCount += 1;

  detail.summary = updateStrategyRow({ ...(detail.summary || {}), ...row });
  if (isTtfundStrategy(row)) {
    benchmarkDisclosureById.set(raw(row.统一策略ID), makeBenchmarkDisclosureRecord(row, detail));
  }
  detail.classification = detail.classification || {};
  detail.classification["风险等级"] = row.风险等级;
  detail.classification["业务分类"] = row.业务分类;
  detail.classification["业务组合分类"] = row.业务组合分类;
  detail.classification["业务分类依据"] = row.业务分类依据;
  detail.classification["基准风险资产权重"] = row.基准风险资产权重 || "";
  detail.classification["基准风险资产权重说明"] = row.基准风险资产权重说明 || "";
  detail.classification["基准风险资产权重_百分比"] = row.基准风险资产权重_百分比;
  for (const field of ["权益中枢", "固收中枢", "基准风险资产中枢", "海外配置中枢", "指数化程度", "主动管理程度", "风险资产偏离", "配置风格标签"]) {
    detail.classification[field] = row[field];
  }
  detail.classification["研报产品类型"] = row.研报产品类型;
  detail.classification["研报股票子类型"] = row.研报股票子类型;
  detail.classification["研报分类依据"] = row.研报分类依据;
  detail.classification["披露风险等级"] = row.披露风险等级;
  detail.classification["披露策略类型"] = row.披露策略类型;
  for (const key of ["主可比池", "测算风险等级", "风险基础分类", "基础分类", "业务主分类", "原披露风险等级", "策略类型"]) {
    delete detail.classification[key];
  }
  [...LEGACY_BENCHMARK_BUCKET_FIELDS, ...LEGACY_BENCHMARK_BUCKET_NOTE_FIELDS, ...LEGACY_RISK_WEIGHT_FIELDS].forEach((field) => delete detail.classification[field]);
  for (const [field, value] of Object.entries(detail.classification)) {
    detail.classification[field] = cleanTerminology(value);
  }
  detail.profileFields = dedupeFieldItems(renameProfileFieldItems(renameFieldItems(detail.profileFields || [])));
  detail.performanceFields = dedupeFieldItems(renameFieldItems(detail.performanceFields || []));
  detail.classificationFields = dedupeFieldItems(renameFieldItems(detail.classificationFields || []));

  detail.profileFields = upsertField(detail.profileFields, "披露风险等级", row.披露风险等级);
  detail.profileFields = upsertField(detail.profileFields, "披露策略类型", row.披露策略类型);
  detail.classificationFields = detail.classificationFields.filter((item) => !["主可比池", "测算风险等级", "风险基础分类", "基础分类", "业务主分类", ...LEGACY_BENCHMARK_BUCKET_FIELDS, ...LEGACY_BENCHMARK_BUCKET_NOTE_FIELDS, ...LEGACY_RISK_WEIGHT_FIELDS].includes(item.字段));
  for (const [field, value] of [
    ["风险等级", row.风险等级],
    ["业务分类", row.业务分类],
    ["业务组合分类", row.业务组合分类],
    ["业务分类依据", row.业务分类依据],
    ["业务分类标签", row.业务分类标签],
    ["基准风险资产权重", row.基准风险资产权重],
    ["基准风险资产权重说明", row.基准风险资产权重说明],
    ["基准风险资产权重_百分比", row.基准风险资产权重_百分比],
    ["权益中枢", row.权益中枢],
    ["固收中枢", row.固收中枢],
    ["基准风险资产中枢", row.基准风险资产中枢],
    ["海外配置中枢", row.海外配置中枢],
    ["指数化程度", row.指数化程度],
    ["主动管理程度", row.主动管理程度],
    ["风险资产偏离", row.风险资产偏离],
    ["配置风格标签", row.配置风格标签],
    ["研报产品类型", row.研报产品类型],
    ["研报股票子类型", row.研报股票子类型],
    ["研报分类依据", row.研报分类依据],
    ["天天展示状态", row.天天展示状态],
    ["天天当前对客展示", row.天天当前对客展示],
    ["天天展示判定依据", row.天天展示判定依据],
  ]) {
    const displayValue = value === null || value === undefined || value === "" ? "未披露" : value;
    detail.classificationFields = upsertField(detail.classificationFields, field, displayValue);
  }
  applyDisclosureCurveQuality(detail, row);
  const includeInDisplay = isDisplayableStrategy(row);

  const isGf = isGuangfaStrategy(row);
  const region = row.市场地域 || "未分类";
  const business = row.业务分类 || "未分类";
  const strategyWeightBase = 1;
  const snapshots = sanitizeDetailSnapshots(detail, row);
  if (!includeInDisplay) {
    writeDetail(filePath, detail);
    continue;
  }
  for (const snap of snapshots) {
    const snapMonth = monthOf(snap.日期);
    const snapshotCategoryAgg = new Map();
    const researchAgg = /历史调仓/.test(raw(snap.类型)) ? makeRebalanceResearchAgg() : null;
    for (const holding of snap.holdings || []) {
      const fundCode = raw(holding.基金代码);
      const rawFundName = raw(holding.基金名称);
      const assetContext = [rawFundName, business, row.策略名称, snap.标题, holding.资产类型, holding.分组].map(raw).filter(Boolean).join(" ");
      const fundClass = classifyFundStandard(fundCode, rawFundName, { ...holding, __asOfDate: snap?.日期 }, assetContext);
      const fundName = fundClass.fundName;
      const assetType = standardFundPrimaryType(fundClass);
      const industryTheme = fundClass.industryTheme;
      const industryGroup = fundClass.industryGroup;
      const equityIndustryTheme = fundClass.equityIndustryTheme;
      const equityIndustryGroup = fundClass.equityIndustryGroup;
      const reportAssetClass = fundClass.reportAssetClass;
      const reportAIndustry = fundClass.reportAIndustry;
      const company = fundClass.company;
      const fundClassBasis = fundClass.basis;
      const fundClassConfidence = fundClass.confidence;
      const assetExposure = exposureText(fundClass.assetExposure);
      const industryExposure = exposureText(fundClass.absoluteIndustryExposure || fundClass.industryExposure);
      const economicAssetExposureText = fundClass.economicAssetExposureText || assetExposure;
      const economicIndustryExposureText = fundClass.economicIndustryExposureText || industryExposure;
      const fundLookthroughFields = {
        基金分类来源: fundClass.classificationSource || "规则估算",
        基金穿透报告期: fundClass.lookthroughReportDate || "",
        基金穿透披露日期: fundClass.lookthroughDisclosureDate || "",
        基金穿透覆盖状态: fundClass.lookthroughCoverageStatus || "",
        是否估算分类: fundClass.isEstimated || "是",
        原始资产暴露: fundClass.rawAssetExposure || "",
        经济资产暴露: economicAssetExposureText,
        经济行业暴露: economicIndustryExposureText,
        经济主题标签: fundClass.economicThemeLabels || "",
        经济资产大类: fundClass.economicAssetClass || "",
        经济资产细类: fundClass.economicAssetSubClass || "",
        经济暴露报告期: fundClass.lookthroughReportDate || "",
        穿透方法: fundClass.economicExposureMethod || "",
        经济暴露证据说明: fundClass.economicExposureEvidence || "",
        经济暴露置信度: fundClass.economicExposureConfidence || "",
        经济暴露质量状态: fundClass.economicExposureQuality || ""
      };
      const secondaryCategory = secondaryCategoryFromFundClass(holding, fundClass);
      const isGfFund = isGuangfaFund(fundName) || /广发基金/.test(company);
      if (snap.类型 === "历史调仓" && row.风险等级 !== "D0 持仓缺失") {
        const event = rebalanceEventForSnapshot(row.统一策略ID, snap);
        const beforeWeight = nz(holding.上次调仓后权重 ?? holding.调前权重);
        const afterWeight = nz(holding.权重 ?? holding.调后权重);
        const change = num(holding.权重变化) ?? (afterWeight - beforeWeight);
        const action = actionFromChange(beforeWeight, afterWeight, change, holding.调仓动作);
        const detailRow = {
          调仓事件ID: event.调仓事件ID || snap.id || `${row.统一策略ID}｜${snap.日期}`,
          统一策略ID: row.统一策略ID,
          策略名称: row.策略名称,
          投顾机构: row.投顾机构,
          渠道: row.渠道,
          是否广发策略: isGf ? "是" : "否",
          天天当前对客展示: row.天天当前对客展示 || "",
          天天展示状态: row.天天展示状态 || "",
          风险等级: row.风险等级,
          业务分类: business,
          研报产品类型: row.研报产品类型 || "未分类",
          研报股票子类型: row.研报股票子类型 || "",
          市场地域: region,
          主动被动: row.主动被动 || "未分类",
          调仓日期: snap.日期,
          调仓标题: event.调仓标题 || snap.标题 || "",
          调仓原因: event.调仓原因 || "",
          调仓逻辑: event.调仓逻辑 || rebalanceLogic(event),
          胜负: event.胜负 || event.结果评价 || "",
          调仓超额: num(event.调仓超额 ?? event.方向性超额),
          单次换手率: num(event.单次换手率),
          基金代码: fundCode,
          基金名称: fundName,
          基金公司: company,
          基金类型: assetType,
          二级分类: secondaryCategory,
          基金分类依据: fundClassBasis,
          ...fundLookthroughFields,
          资产暴露: assetExposure,
          行业暴露: industryExposure,
          行业主题: industryTheme,
          行业大类: industryGroup,
          权益行业主题: equityIndustryTheme,
          权益行业大类: equityIndustryGroup,
          研报大类资产: reportAssetClass,
          研报A股行业: reportAIndustry,
          是否广发基金: isGfFund ? "是" : "否",
          调前权重: beforeWeight,
          调后权重: afterWeight,
          权重变化: change,
          调仓动作: action,
          调仓后收益率: num(holding.调仓后收益率),
          调仓后收益贡献: num(holding.调仓后收益贡献)
        };
        rebalanceFundRows.push(detailRow);
        if (Math.abs(change) > 0.0001) {
          addRebalanceFundMonthlyAgg(rebalanceFundMonthlyAgg, detailRow);
          addDirectionAgg(directionAgg, detailRow);
          addGfFundOpportunity(gfFundOpportunityAgg, detailRow);
          addStrategyAssetChangeByExposure(strategyAssetChangeAgg, detailRow, fundClass);
          pushRebalanceFundCategoryRows(rebalanceFundCategoryRows, detailRow, fundClass);
          addRebalanceResearchMove(researchAgg, detailRow, fundClass);
        }
      }
      const weight = nz(holding.权重);
      if (weight <= 0) continue;
      if (row.风险等级 !== "D0 持仓缺失") {
        for (const category of categoryExposureRows(fundClass)) {
          const categoryField = category.field;
          const categoryValue = category.category;
          if (!categoryValue || categoryValue === "待核验") continue;
          const categoryWeight = weight * category.share / 100;
          const categoryKey = `${categoryField}｜${categoryValue}`;
          const categoryRow = addAgg(snapshotCategoryAgg, categoryKey, {
            统一策略ID: row.统一策略ID,
            策略名称: row.策略名称,
            快照日期: snap.日期,
            快照月份: snapMonth,
            快照类型: snap.类型 || "",
            投顾机构: row.投顾机构 || "未识别机构",
            渠道: row.渠道 || "",
            是否广发策略: isGf ? "是" : "否",
            天天当前对客展示: row.天天当前对客展示 || "",
            天天展示状态: row.天天展示状态 || "",
            风险等级: row.风险等级,
            业务分类: business,
            研报产品类型: row.研报产品类型 || "未分类",
            研报股票子类型: row.研报股票子类型 || "",
            市场地域: region,
            分类字段: categoryField,
            分类: categoryValue,
            总权重: 0,
            基金数: 0,
            策略快照数: 1
          });
          categoryRow.总权重 += categoryWeight;
          categoryRow.基金数 += 1;
        }
      }
      if (snap.id === "current") {
        holdingRows += 1;
        const initialWeight = nz(holding.上次调仓后权重 ?? holding.调前权重);
        const currentChange = num(holding.权重变化) ?? (weight - initialWeight);
        const holdingReturn = num(holding.调仓后收益率);
        currentHoldingStrategyRows.push({
          统一策略ID: row.统一策略ID,
          策略名称: row.策略名称,
          投顾机构: row.投顾机构,
          渠道: row.渠道,
          是否广发策略: isGf ? "是" : "否",
          天天当前对客展示: row.天天当前对客展示 || "",
          天天展示状态: row.天天展示状态 || "",
          风险等级: row.风险等级,
          业务分类: business,
          研报产品类型: row.研报产品类型 || "未分类",
          研报股票子类型: row.研报股票子类型 || "",
          市场地域: region,
          基金代码: fundCode,
          基金名称: fundName,
          基金公司: company,
          基金类型: assetType,
          二级分类: secondaryCategory,
          基金分类依据: fundClassBasis,
          ...fundLookthroughFields,
          资产暴露: assetExposure,
          行业暴露: industryExposure,
          行业主题: industryTheme,
          行业大类: industryGroup,
          权益行业主题: equityIndustryTheme,
          权益行业大类: equityIndustryGroup,
          研报大类资产: reportAssetClass,
          研报A股行业: reportAIndustry,
          是否广发基金: isGfFund ? "是" : "否",
          区间收益率: holdingReturn,
          日涨跌幅: num(row["日涨跌幅"]),
          近一周: num(row["近一周"]),
          近一月: num(row["近一月"]),
          近三月: num(row["近三月"]),
          "近6月": num(row["近6月"]),
          近1年: num(row["近1年"]),
          今年以来: num(row["今年以来"]),
          累计收益率: num(row["累计收益率"]),
          初持仓比例: initialWeight,
          期末持仓比例: weight,
          权重变化: currentChange
        });
        const fundKey = `${fundCode}｜${fundName}`;
        const fund = addAgg(fundAgg, fundKey, {
          基金代码: fundCode,
          基金名称: fundName,
          基金公司: company,
          基金类型: assetType,
          二级分类: secondaryCategory,
          基金分类依据: fundClassBasis,
          ...fundLookthroughFields,
          资产暴露: assetExposure,
          行业暴露: industryExposure,
          行业主题: industryTheme,
          行业大类: industryGroup,
          权益行业主题: equityIndustryTheme,
          权益行业大类: equityIndustryGroup,
          研报大类资产: reportAssetClass,
          研报A股行业: reportAIndustry,
          持仓策略数: 0,
          总权重: 0,
          广发策略权重: 0,
          非广发策略权重: 0,
          广发基金产品: isGfFund ? "是" : "否",
          _权重样本: [],
          _收益样本: [],
          _策略: new Set(),
          _增持策略: new Set(),
          _减持策略: new Set()
        });
        fund.持仓策略数 += strategyWeightBase;
        fund.总权重 += weight;
        if (isGf) fund.广发策略权重 += weight;
        else fund.非广发策略权重 += weight;
        fund._权重样本.push(weight);
        if (holdingReturn !== null) fund._收益样本.push(holdingReturn);
        fund._策略.add(row.统一策略ID);
        if (currentChange > 0.0001) fund._增持策略.add(row.统一策略ID);
        if (currentChange < -0.0001) fund._减持策略.add(row.统一策略ID);

        const clientKey = `${row.天天当前对客展示 || ""}｜${row.天天展示状态 || ""}`;
        const riskFundKey = `${row.风险等级}｜${business}｜${region}｜${row.投顾机构 || "未识别机构"}｜${isGf ? "是" : "否"}｜${clientKey}｜${fundCode}｜${fundName}`;
        const riskFund = addAgg(riskFundAgg, riskFundKey, {
          风险等级: row.风险等级,
          业务分类: business,
          研报产品类型: row.研报产品类型 || "未分类",
          研报股票子类型: row.研报股票子类型 || "",
          市场地域: region,
          投顾机构: row.投顾机构 || "未识别机构",
          是否广发策略: isGf ? "是" : "否",
          天天当前对客展示: row.天天当前对客展示 || "",
          天天展示状态: row.天天展示状态 || "",
          基金代码: fundCode,
          基金名称: fundName,
          基金公司: company,
          基金类型: assetType,
          二级分类: secondaryCategory,
          基金分类依据: fundClassBasis,
          ...fundLookthroughFields,
          资产暴露: assetExposure,
          行业暴露: industryExposure,
          行业主题: industryTheme,
          行业大类: industryGroup,
          权益行业主题: equityIndustryTheme,
          权益行业大类: equityIndustryGroup,
          研报大类资产: reportAssetClass,
          研报A股行业: reportAIndustry,
          持仓策略数: 0,
          总权重: 0,
          广发策略权重: 0,
          非广发策略权重: 0,
          广发基金产品: isGfFund ? "是" : "否",
          _权重样本: [],
          _收益样本: [],
          _策略: new Set(),
          _增持策略: new Set(),
          _减持策略: new Set()
        });
        riskFund.持仓策略数 += strategyWeightBase;
        riskFund.总权重 += weight;
        if (isGf) riskFund.广发策略权重 += weight;
        else riskFund.非广发策略权重 += weight;
        riskFund._权重样本.push(weight);
        if (holdingReturn !== null) riskFund._收益样本.push(holdingReturn);
        riskFund._策略.add(row.统一策略ID);
        if (currentChange > 0.0001) riskFund._增持策略.add(row.统一策略ID);
        if (currentChange < -0.0001) riskFund._减持策略.add(row.统一策略ID);

        const companyRow = addAgg(companyAgg, company, { 基金公司: company, 持仓策略数: 0, 总权重: 0, 广发策略权重: 0, 非广发策略权重: 0, 广发产品权重: 0, _权重样本: [], _策略: new Set() });
        companyRow.持仓策略数 += strategyWeightBase;
        companyRow.总权重 += weight;
        if (isGf) companyRow.广发策略权重 += weight;
        else companyRow.非广发策略权重 += weight;
        if (isGfFund) companyRow.广发产品权重 += weight;
        companyRow._权重样本.push(weight);
        companyRow._策略.add(row.统一策略ID);

        const riskCompanyKey = `${row.风险等级}｜${business}｜${region}｜${row.投顾机构 || "未识别机构"}｜${isGf ? "是" : "否"}｜${clientKey}｜${company}`;
        const riskCompany = addAgg(riskCompanyAgg, riskCompanyKey, { 风险等级: row.风险等级, 业务分类: business, 市场地域: region, 投顾机构: row.投顾机构 || "未识别机构", 是否广发策略: isGf ? "是" : "否", 天天当前对客展示: row.天天当前对客展示 || "", 天天展示状态: row.天天展示状态 || "", 基金公司: company, 持仓策略数: 0, 总权重: 0, 广发策略权重: 0, 非广发策略权重: 0, 广发产品权重: 0, _权重样本: [], _策略: new Set() });
        riskCompany.持仓策略数 += strategyWeightBase;
        riskCompany.总权重 += weight;
        if (isGf) riskCompany.广发策略权重 += weight;
        else riskCompany.非广发策略权重 += weight;
        if (isGfFund) riskCompany.广发产品权重 += weight;
        riskCompany._权重样本.push(weight);
        riskCompany._策略.add(row.统一策略ID);

        const assetKey = `${row.风险等级}｜${business}｜${region}｜${row.投顾机构 || "未识别机构"}｜${isGf ? "是" : "否"}｜${clientKey}｜${assetType}`;
        const assetRow = addAgg(assetAgg, assetKey, { 风险等级: row.风险等级, 业务分类: business, 市场地域: region, 投顾机构: row.投顾机构 || "未识别机构", 是否广发策略: isGf ? "是" : "否", 天天当前对客展示: row.天天当前对客展示 || "", 天天展示状态: row.天天展示状态 || "", 基金类型: assetType, 持仓策略数: 0, 总权重: 0, 广发策略权重: 0, 非广发策略权重: 0, _策略: new Set() });
        assetRow.持仓策略数 += strategyWeightBase;
        assetRow.总权重 += weight;
        if (isGf) assetRow.广发策略权重 += weight;
        else assetRow.非广发策略权重 += weight;
        assetRow._策略.add(row.统一策略ID);
      }

      const timelineKey = `${snapMonth}｜${row.风险等级}｜${business}｜${region}｜${row.投顾机构 || "未识别机构"}｜${isGf ? "是" : "否"}｜${row.天天当前对客展示 || ""}｜${row.天天展示状态 || ""}｜${assetType}`;
      const timeline = addAgg(timelineAgg, timelineKey, { 月份: snapMonth, 风险等级: row.风险等级, 业务分类: business, 市场地域: region, 投顾机构: row.投顾机构 || "未识别机构", 是否广发策略: isGf ? "是" : "否", 天天当前对客展示: row.天天当前对客展示 || "", 天天展示状态: row.天天展示状态 || "", 基金类型: assetType, 总权重: 0, 策略快照数: 0 });
      timeline.总权重 += weight;
      timeline.策略快照数 += 1;
      const industryTimelineKey = `${snapMonth}｜${row.风险等级}｜${business}｜${region}｜${row.投顾机构 || "未识别机构"}｜${isGf ? "是" : "否"}｜${row.天天当前对客展示 || ""}｜${row.天天展示状态 || ""}｜${industryGroup}｜${industryTheme}｜${equityIndustryGroup}｜${equityIndustryTheme}`;
      const industryTimeline = addAgg(industryTimelineAgg, industryTimelineKey, { 月份: snapMonth, 风险等级: row.风险等级, 业务分类: business, 市场地域: region, 投顾机构: row.投顾机构 || "未识别机构", 是否广发策略: isGf ? "是" : "否", 天天当前对客展示: row.天天当前对客展示 || "", 天天展示状态: row.天天展示状态 || "", 行业大类: industryGroup, 行业主题: industryTheme, 权益行业大类: equityIndustryGroup, 权益行业主题: equityIndustryTheme, 总权重: 0, 策略快照数: 0 });
      industryTimeline.总权重 += weight;
      industryTimeline.策略快照数 += 1;
    }
    if (researchAgg) {
      const event = rebalanceEventForSnapshot(row.统一策略ID, snap);
      const assets = sortedReportAssetText([...researchAgg.assets.keys()]);
      if (assets) snap.涉及资产 = assets;
      snap.AI投研总结 = buildRebalanceResearchSummary(snap, event, researchAgg);
      snap.投研摘要 = snap.AI投研总结;
    }
    holdingSnapshotCategoryRows.push(...snapshotCategoryAgg.values());
  }

  writeDetail(filePath, detail);
}

for (const file of fs.readdirSync(detailsDir).filter((name) => name.endsWith(".js"))) {
  const filePath = path.join(detailsDir, file);
  const resolved = path.resolve(filePath).toLowerCase();
  if (processedDetailFiles.has(resolved)) continue;
  const detail = loadDetail(filePath);
  const row = { ...(detail.summary || {}), 统一策略ID: raw(detail.id || detail.summary?.统一策略ID) };
  sanitizeDetailSnapshots(detail, row);
  writeDetail(filePath, detail);
  orphanDetailCount += 1;
}

summary.rebalanceEvents = enrichRebalanceEventAssets(summary.rebalanceEvents, buildEventReportAssetLookup(rebalanceFundCategoryRows));

const qualityFilteredOutStrategies = summary.strategies.filter((row) => !isDisplayableStrategy(row));
summary.qualityFilteredOutStrategyCount = qualityFilteredOutStrategies.length;
summary.displayStrategyCount = displayStrategies.length;
summary.filteredOutStrategyCount = Math.max(0, summary.rawStrategyCount - summary.displayStrategyCount);

const marketRows = displayStrategies;
const gfRows = marketRows.filter(isGuangfaStrategy);
const nonGfRows = marketRows.filter((row) => !isGuangfaStrategy(row));
const displayRebalanceEvents = (summary.rebalanceEvents || []).filter((event) => isDisplayableStrategy(byId.get(event.统一策略ID)));
const metrics = ["日涨跌幅", "近一周", "近一月", "近三月", "近6月", "近1年", "今年以来", "累计收益率", "年化收益", "最大回撤", "波动率", "夏普比率"];

function strategyPoint(row) {
  return {
    统一策略ID: row.统一策略ID,
    策略名称: row.策略名称,
    渠道: row.渠道,
    投顾机构: row.投顾机构,
    是否广发: isGuangfaStrategy(row) ? "是" : "否",
    风险等级: row.风险等级,
    业务分类: row.业务分类,
    业务分类依据: row.业务分类依据,
    基准风险资产权重: row.基准风险资产权重 || "",
    权益中枢: row.权益中枢,
    基准风险资产中枢: row.基准风险资产中枢,
    配置风格标签: row.配置风格标签 || "",
    研报产品类型: row.研报产品类型,
    研报股票子类型: row.研报股票子类型,
    研报分类依据: row.研报分类依据,
    市场地域: row.市场地域,
    主动被动: row.主动被动,
    披露风险等级: row.披露风险等级,
    披露策略类型: row.披露策略类型,
    数据完整性: row.数据完整性,
    洞察评价对象: row.洞察评价对象,
    天天展示状态: row.天天展示状态,
    天天当前对客展示: row.天天当前对客展示,
    ...Object.fromEntries(metrics.map((field) => [field, num(row[field])]))
  };
}

function median(values) {
  const arr = values.filter((value) => Number.isFinite(value)).sort((a, b) => a - b);
  if (!arr.length) return null;
  const mid = Math.floor(arr.length / 2);
  return arr.length % 2 ? arr[mid] : (arr[mid - 1] + arr[mid]) / 2;
}

function normalizeTargetSeriesName(name) {
  return raw(name)
    .replace(/第?[零一二三四五六七八九十百千万\d]{1,5}期/g, "")
    .replace(/\d{1,4}期/g, "")
    .replace(/天天\d{1,4}/g, "天天")
    .replace(/\s+/g, "")
    .replace(/[\\-_—]+$/g, "")
    .replace(/（\s*）/g, "")
    .trim() || raw(name);
}

function majorityValue(rows, field) {
  const map = new Map();
  for (const row of rows) {
    const value = row[field] || "未分类";
    map.set(value, (map.get(value) || 0) + 1);
  }
  return [...map.entries()].sort((a, b) => b[1] - a[1])[0]?.[0] || "未分类";
}

function highestRiskValue(rows) {
  const order = ["R0 现金/超低波", "R1 低波", "R2 稳健收益", "R3 均衡稳健", "R4 均衡成长", "R5 权益/进取"];
  return [...rows].sort((a, b) => order.indexOf(b.风险等级) - order.indexOf(a.风险等级))[0]?.风险等级 || rows[0]?.风险等级 || "未分类";
}

function collapseTargetSeriesForInsight(rows) {
  const out = [];
  const groups = new Map();
  for (const row of rows || []) {
    if (row.业务分类 !== "目标盈系列产品") {
      out.push(row);
      continue;
    }
    const key = `${row.投顾机构 || "未识别机构"}｜${normalizeTargetSeriesName(row.策略名称)}`;
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(row);
  }
  for (const list of groups.values()) {
    const best = [...list].sort((a, b) => nz(b["近1年"] ?? b.累计收益率) - nz(a["近1年"] ?? a.累计收益率))[0] || list[0];
    const merged = {
      ...best,
      策略名称: normalizeTargetSeriesName(best.策略名称),
      代表期次: best.策略名称,
      期次数: list.length,
      风险等级: highestRiskValue(list),
      基准风险资产权重: majorityValue(list, "基准风险资产权重"),
      权益中枢: median(list.map((row) => num(row.权益中枢)).filter((value) => value !== null)),
      基准风险资产中枢: median(list.map((row) => num(row.基准风险资产中枢)).filter((value) => value !== null)),
      市场地域: majorityValue(list, "市场地域"),
      主动被动: majorityValue(list, "主动被动"),
      研报产品类型: majorityValue(list, "研报产品类型"),
      研报股票子类型: majorityValue(list, "研报股票子类型")
    };
    for (const field of metrics) merged[field] = median(list.map((row) => num(row[field])).filter((value) => value !== null));
    out.push(merged);
  }
  return out;
}

const insightMarketRows = collapseTargetSeriesForInsight(marketRows);

function finalizeWeightAgg(rows) {
  return rows.map((row) => {
    const weights = Array.isArray(row._权重样本) ? row._权重样本 : [];
    const returns = Array.isArray(row._收益样本) ? row._收益样本 : [];
    const out = {
      ...row,
      持仓策略数: row._策略 instanceof Set ? row._策略.size : row.持仓策略数,
      增持策略数: row._增持策略 instanceof Set ? row._增持策略.size : row.增持策略数,
      减持策略数: row._减持策略 instanceof Set ? row._减持策略.size : row.减持策略数,
      中位权重: median(weights),
      区间收益率: median(returns)
    };
    delete out._权重样本;
    delete out._收益样本;
    delete out._策略;
    delete out._增持策略;
    delete out._减持策略;
    return out;
  });
}

function groupedStats(groupField, rows = insightMarketRows) {
  const keys = [...new Set(rows.map((row) => row[groupField]).filter(Boolean))];
  return keys.map((key) => {
    const bucket = rows.filter((row) => row[groupField] === key);
    const gf = bucket.filter(isGuangfaStrategy);
    const non = bucket.filter((row) => !isGuangfaStrategy(row));
    return {
      维度: groupField,
      类型: key,
      市场数量: bucket.length,
      广发数量: gf.length,
      非广发数量: non.length,
      市场近1年中位: median(bucket.map((row) => num(row["近1年"]))),
      广发近1年中位: median(gf.map((row) => num(row["近1年"]))),
      非广发近1年中位: median(non.map((row) => num(row["近1年"]))),
      市场回撤中位: median(bucket.map((row) => num(row["最大回撤"]))),
      广发回撤中位: median(gf.map((row) => num(row["最大回撤"]))),
      市场波动中位: median(bucket.map((row) => num(row["波动率"]))),
      广发波动中位: median(gf.map((row) => num(row["波动率"]))),
      广发覆盖率: bucket.length ? gf.length / bucket.length * 100 : null
    };
  }).sort((a, b) => b.市场数量 - a.市场数量);
}

function opportunityRows() {
  const groups = [...new Set(insightMarketRows.map((row) => `${row.风险等级}｜${row.业务分类}`).filter(Boolean))];
  return groups.map((key) => {
    const [risk, business] = key.split("｜");
    const bucket = insightMarketRows.filter((row) => row.风险等级 === risk && row.业务分类 === business);
    const gf = bucket.filter(isGuangfaStrategy);
    const non = bucket.filter((row) => !isGuangfaStrategy(row));
    const gfBest = [...gf].sort((a, b) => nz(b["近1年"]) - nz(a["近1年"]))[0] || null;
    const marketBest = [...bucket].sort((a, b) => nz(b["近1年"]) - nz(a["近1年"]))[0] || null;
    const gfMedian = median(gf.map((row) => num(row["近1年"])));
    const marketMedian = median(bucket.map((row) => num(row["近1年"])));
    const gap = gfMedian !== null && marketMedian !== null ? gfMedian - marketMedian : null;
    let conclusion = "观察";
    if (!gf.length && bucket.length >= 10) conclusion = "广发缺位";
    else if (gf.length && gap !== null && gap >= 0) conclusion = "可营销";
    else if (gf.length && gap !== null && gap < -5) conclusion = "需复盘";
    else if (gf.length < 3 && bucket.length >= 20) conclusion = "梯队偏薄";
    return {
      风险等级: risk,
      业务分类: business,
      市场数量: bucket.length,
      广发数量: gf.length,
      非广发数量: non.length,
      广发中位近1年: gfMedian,
      市场中位近1年: marketMedian,
      中位差: gap,
      广发最佳产品: gfBest?.策略名称 || "",
      标杆产品: marketBest?.策略名称 || "",
      标杆机构: marketBest?.投顾机构 || "",
      结论: conclusion
    };
  }).sort((a, b) => {
    const priority = { "广发缺位": 4, "可营销": 3, "梯队偏薄": 2, "需复盘": 1, "观察": 0 };
    return (priority[b.结论] || 0) - (priority[a.结论] || 0) || b.市场数量 - a.市场数量;
  });
}

function businessAction(row) {
  const size = nz(row.市场数量);
  const gfCount = nz(row.广发数量);
  const coverage = num(row.广发覆盖率);
  const returnGap = num(row.收益差);
  const drawdownEdge = num(row.回撤优势);
  if (!gfCount && size >= 15) return "产品补齐";
  if (returnGap !== null && returnGap >= 1 && (drawdownEdge === null || drawdownEdge >= -2)) return "重点营销";
  if (coverage !== null && coverage < 8 && size >= 30) return "梯队扩容";
  if ((returnGap !== null && returnGap <= -2) || (drawdownEdge !== null && drawdownEdge <= -4)) return "复盘优化";
  return "保持观察";
}

function businessJudgement(row) {
  if (row.经营动作 === "重点营销") return "广发同类收益领先且风险劣势不明显，可进入渠道话术和重点名单。";
  if (row.经营动作 === "产品补齐") return "市场已有规模但广发缺位，优先评估产品布局或投顾组合包装。";
  if (row.经营动作 === "梯队扩容") return "市场样本充足但广发覆盖偏薄，应补齐风险档、期限或场景。";
  if (row.经营动作 === "复盘优化") return "广发收益或回撤落后，优先复盘组合、底层基金和调仓节奏。";
  return "暂未出现明确优势或缺口，保持跟踪竞品、仓位和渠道反馈。";
}

function businessDiagnosisRows() {
  return groupedStats("业务分类").map((row) => {
    const out = {
      业务分类: row.类型,
      市场数量: row.市场数量,
      广发数量: row.广发数量,
      广发覆盖率: row.广发覆盖率,
      市场近1年中位: row.市场近1年中位,
      广发近1年中位: row.广发近1年中位,
      收益差: row.广发近1年中位 === null || row.市场近1年中位 === null ? null : row.广发近1年中位 - row.市场近1年中位,
      市场回撤中位: row.市场回撤中位,
      广发回撤中位: row.广发回撤中位,
      回撤优势: row.广发回撤中位 === null || row.市场回撤中位 === null ? null : row.市场回撤中位 - row.广发回撤中位,
      市场波动中位: row.市场波动中位,
      广发波动中位: row.广发波动中位
    };
    out.经营动作 = businessAction(out);
    out.经营判断 = businessJudgement(out);
    return out;
  }).sort((a, b) => {
    const priority = { 重点营销: 5, 产品补齐: 4, 梯队扩容: 3, 复盘优化: 2, 保持观察: 1 };
    return (priority[b.经营动作] || 0) - (priority[a.经营动作] || 0) || b.市场数量 - a.市场数量;
  });
}

function institutionCapabilityRows() {
  const groups = new Map();
  for (const event of displayRebalanceEvents) {
    const name = raw(event.投顾机构) || "未识别机构";
    if (!groups.has(name)) groups.set(name, []);
    groups.get(name).push(event);
  }
  return [...groups.entries()].map(([name, rows]) => {
    const evaluated = rows.map(winValue).filter((value) => value !== null);
    const wins = evaluated.filter(Boolean).length;
    return {
      投顾机构: name,
      事件数: rows.length,
      可评价事件数: evaluated.length,
      调仓胜率: evaluated.length ? wins / evaluated.length * 100 : null,
      平均调仓超额: median(rows.map((row) => num(row.调仓超额 ?? row.方向性超额))),
      平均单次换手率: median(rows.map((row) => num(row.单次换手率))),
      广发机构: /广发基金|广发投顾/.test(name) ? "是" : "否"
    };
  }).sort((a, b) => b.可评价事件数 - a.可评价事件数 || b.事件数 - a.事件数);
}

function finalizeDirectionRows() {
  return [...directionAgg.values()]
    .map((row) => ({ ...row, 绝对净增配: Math.abs(row.净增配) }))
    .sort((a, b) => b.绝对净增配 - a.绝对净增配);
}

function finalizeRebalanceFundMonthlyRows() {
  const rows = [...rebalanceFundMonthlyAgg.values()].map((row) => {
    const out = {
      ...row,
      调仓事件数: row._事件.size,
      调仓策略数: row._策略.size,
      绝对净增配: Math.abs(row.净增配)
    };
    delete out._事件;
    delete out._策略;
    return out;
  });
  const selected = new Map();
  const byMonth = new Map();
  for (const row of rows) {
    if (!byMonth.has(row.月份)) byMonth.set(row.月份, []);
    byMonth.get(row.月份).push(row);
  }
  for (const list of byMonth.values()) {
    for (const row of list.filter((item) => item.是否广发基金 === "是" && item.绝对净增配 > 0.0001)) {
      selected.set([row.月份, row.风险等级, row.业务分类, row.研报产品类型 || "", row.研报股票子类型 || "", row.市场地域, row.投顾机构, row.是否广发策略, row.天天当前对客展示 || "", row.天天展示状态 || "", row.基金代码, row.基金名称, row.研报大类资产 || "", row.研报A股行业 || ""].join("｜"), row);
    }
    for (const row of [...list].sort((a, b) => b.绝对净增配 - a.绝对净增配).slice(0, 260)) {
      selected.set([row.月份, row.风险等级, row.业务分类, row.研报产品类型 || "", row.研报股票子类型 || "", row.市场地域, row.投顾机构, row.是否广发策略, row.天天当前对客展示 || "", row.天天展示状态 || "", row.基金代码, row.基金名称, row.研报大类资产 || "", row.研报A股行业 || ""].join("｜"), row);
    }
  }
  return [...selected.values()]
    .sort((a, b) => b.月份.localeCompare(a.月份) || b.绝对净增配 - a.绝对净增配);
}

function finalizeGfFundOpportunityRows(currentFundRows) {
  for (const row of currentFundRows) {
    if (row.广发基金产品 !== "是") continue;
    const key = `${row.基金代码}｜${row.基金名称}`;
    if (!gfFundOpportunityAgg.has(key)) {
      gfFundOpportunityAgg.set(key, {
        基金代码: row.基金代码,
        基金名称: row.基金名称,
        基金公司: row.基金公司,
        基金类型: row.基金类型,
        加仓次数: 0,
        减仓次数: 0,
        买入次数: 0,
        卖出次数: 0,
        加仓权重: 0,
        减仓权重: 0,
        净增配: 0,
        调仓后收益贡献: 0,
        当前持仓策略数: 0,
        当前持仓权重: 0
      });
    }
    const out = gfFundOpportunityAgg.get(key);
    out.当前持仓策略数 = row.持仓策略数 || 0;
    out.当前持仓权重 = row.总权重 || 0;
  }
  return [...gfFundOpportunityAgg.values()].sort((a, b) =>
    Math.abs(b.净增配) - Math.abs(a.净增配) || b.当前持仓权重 - a.当前持仓权重
  );
}

function finalizeStrategyAssetChangeRows() {
  return [...strategyAssetChangeAgg.values()]
    .map((row) => ({
      ...row,
      绝对净增配: Math.abs(row.净增配)
    }))
    .sort((a, b) => String(b.调仓日期 || "").localeCompare(String(a.调仓日期 || "")) || b.绝对净增配 - a.绝对净增配);
}

function finalizeTimelineRows() {
  const rows = [...timelineAgg.values()].sort((a, b) => a.月份.localeCompare(b.月份));
  const months = [...new Set(rows.map((row) => row.月份).filter(Boolean))].sort().slice(-6);
  return rows.filter((row) => months.includes(row.月份));
}

function finalizeIndustryTimelineRows() {
  const rows = [...industryTimelineAgg.values()].sort((a, b) => a.月份.localeCompare(b.月份));
  const months = [...new Set(rows.map((row) => row.月份).filter(Boolean))].sort().slice(-6);
  return rows.filter((row) => months.includes(row.月份));
}

function compactHoldingSnapshotRows(rows) {
  const fields = ["行业主题", "行业大类", "权益行业主题", "权益行业大类", "研报大类资产", "研报A股行业"];
  const dict = {
    strategies: [],
    institutions: [],
    risks: [],
    businesses: [],
    reportTypes: [],
    reportSubTypes: [],
    regions: [],
    clients: [],
    statuses: [],
    categories: []
  };
  const maps = Object.fromEntries(Object.keys(dict).map((key) => [key, new Map()]));
  const intern = (key, value) => {
    const text = raw(value);
    if (!maps[key].has(text)) {
      maps[key].set(text, dict[key].length);
      dict[key].push(text);
    }
    return maps[key].get(text);
  };
  const strategyMap = new Map();
  const strategyIndex = (id, name) => {
    const key = raw(id);
    if (!strategyMap.has(key)) {
      strategyMap.set(key, dict.strategies.length);
      dict.strategies.push([key, raw(name)]);
    }
    return strategyMap.get(key);
  };
  const fieldIndex = new Map(fields.map((field, index) => [field, index]));
  const cleanRows = dedupeRowsByKey(
    rows,
    (row) => [
      row.统一策略ID,
      row.快照日期,
      row.投顾机构,
      row.是否广发策略,
      row.风险等级,
      row.业务分类,
      row.研报产品类型 || "未分类",
      row.研报股票子类型 || "",
      row.市场地域,
      row.天天当前对客展示,
      row.天天展示状态,
      row.分类字段,
      row.分类
    ].map(raw).join("｜"),
    (row, existing) => {
      const fundGap = nz(row.基金数) - nz(existing.基金数);
      if (Math.abs(fundGap) > 0.0001) return fundGap > 0;
      return Math.abs(nz(row.总权重)) > Math.abs(nz(existing.总权重));
    }
  );
  const compactRows = cleanRows.map((row) => [
    strategyIndex(row.统一策略ID, row.策略名称),
    row.快照日期,
    intern("institutions", row.投顾机构),
    row.是否广发策略 === "是" ? 1 : 0,
    intern("risks", row.风险等级),
    intern("businesses", row.业务分类),
    intern("reportTypes", row.研报产品类型 || "未分类"),
    intern("reportSubTypes", row.研报股票子类型 || ""),
    intern("regions", row.市场地域),
    intern("clients", row.天天当前对客展示),
    intern("statuses", row.天天展示状态),
    fieldIndex.get(row.分类字段),
    intern("categories", row.分类),
    Number((num(row.总权重) || 0).toFixed(4)),
    row.基金数 || 0
  ]);
  return { version: 2, fields, dict, rows: compactRows };
}

function compactRebalanceFundCategoryRows(rows) {
  const categoryFields = ["研报大类资产", "权益行业主题", "研报A股行业"];
  const dict = {
    strategies: [],
    institutions: [],
    risks: [],
    businesses: [],
    reportTypes: [],
    reportSubTypes: [],
    regions: [],
    clients: [],
    statuses: [],
    categories: [],
    funds: [],
    companies: [],
    fundTypes: [],
    actions: []
  };
  const maps = Object.fromEntries(Object.keys(dict).map((key) => [key, new Map()]));
  const intern = (key, value) => {
    const text = raw(value);
    if (!maps[key].has(text)) {
      maps[key].set(text, dict[key].length);
      dict[key].push(text);
    }
    return maps[key].get(text);
  };
  const strategyMap = new Map();
  const strategyIndex = (id, name) => {
    const key = raw(id);
    if (!strategyMap.has(key)) {
      strategyMap.set(key, dict.strategies.length);
      dict.strategies.push([key, raw(name)]);
    }
    return strategyMap.get(key);
  };
  const fundMap = new Map();
  const fundIndex = (code, name) => {
    const key = `${raw(code)}｜${raw(name)}`;
    if (!fundMap.has(key)) {
      fundMap.set(key, dict.funds.length);
      dict.funds.push([raw(code), raw(name)]);
    }
    return fundMap.get(key);
  };
  const fieldIndex = new Map(categoryFields.map((field, index) => [field, index]));
  const cleanRows = dedupeRowsByKey(
    rows,
    (row) => [
      row.调仓日期,
      row.统一策略ID,
      row.投顾机构,
      row.风险等级,
      row.业务分类,
      row.研报产品类型 || "未分类",
      row.研报股票子类型 || "",
      row.市场地域,
      row.天天当前对客展示,
      row.天天展示状态,
      row.分类字段,
      row.分类,
      row.基金代码,
      row.基金名称,
      row.基金公司,
      row.基金类型,
      row.调仓动作
    ].map(raw).join("｜"),
    (row, existing) => Math.abs(nz(row.权重变化)) > Math.abs(nz(existing.权重变化))
  );
  const compactRows = cleanRows.map((row) => [
    row.调仓日期,
    strategyIndex(row.统一策略ID, row.策略名称),
    intern("institutions", row.投顾机构),
    row.是否广发策略 === "是" ? 1 : 0,
    intern("risks", row.风险等级),
    intern("businesses", row.业务分类),
    intern("reportTypes", row.研报产品类型 || "未分类"),
    intern("reportSubTypes", row.研报股票子类型 || ""),
    intern("regions", row.市场地域),
    intern("clients", row.天天当前对客展示),
    intern("statuses", row.天天展示状态),
    fieldIndex.get(row.分类字段),
    intern("categories", row.分类),
    fundIndex(row.基金代码, row.基金名称),
    intern("companies", row.基金公司),
    intern("fundTypes", row.基金类型),
    Number(nz(row.调前权重).toFixed(4)),
    Number(nz(row.调后权重).toFixed(4)),
    Number(nz(row.权重变化).toFixed(4)),
    intern("actions", row.调仓动作)
  ]);
  return { version: 1, fields: categoryFields, dict, rows: compactRows };
}

function compactFundDetailPack(fundRows, strategyHoldingRows, monthlyRows) {
  const fundFields = [
    "基金代码", "基金名称", "基金公司", "基金类型", "二级分类", "基金分类依据",
    "基金分类来源", "基金穿透报告期", "基金穿透覆盖状态", "是否估算分类",
    "资产暴露", "行业暴露", "行业主题", "行业大类", "权益行业主题", "权益行业大类",
    "原始资产暴露", "原始行业暴露", "经济资产暴露", "经济行业暴露", "经济主题标签",
    "经济资产大类", "经济资产细类", "经济暴露报告期", "穿透方法", "经济暴露证据说明",
    "经济暴露置信度", "经济暴露质量状态",
    "研报大类资产", "研报A股行业", "广发基金产品", "总权重", "广发策略权重", "非广发策略权重",
    "持仓策略数", "中位权重", "区间收益率", "增持策略数", "减持策略数"
  ];
  const holdingFields = [
    "基金索引", "统一策略ID", "策略名称", "投顾机构", "渠道", "是否广发策略", "风险等级",
    "业务分类", "研报产品类型", "市场地域", "天天当前对客展示", "基金分类来源", "基金穿透报告期", "初持仓比例", "期末持仓比例",
    "权重变化", "区间收益率", "近1年"
  ];
  const monthlyFields = ["基金索引", "月份", "净增配", "中位净增配", "加仓权重", "减仓权重", "调仓策略数"];
  const fundIndex = new Map();
  const fundKey = (code, name) => {
    const normalizedCode = raw(code).trim();
    return /^\d{6}$/.test(normalizedCode) ? `code:${normalizedCode}` : `name:${raw(name).trim()}`;
  };
  const emptyLabels = new Set(["", "待核验", "未披露", "未分类", "未知", "--", "a", "1", "2", "3", "4", "5", "6", "7", "8"]);
  const fundSecondaryFallback = (row = {}) => [
    row.二级分类,
    row.基金类型,
    row.行业主题,
    row.权益行业主题,
    row.研报大类资产,
    row.行业大类
  ].map(raw).find((value) => !emptyLabels.has(value)) || "待补分类";
  const compactFund = (row) => fundFields.map((field) => field === "二级分类" ? fundSecondaryFallback(row) : (row[field] ?? ""));
  const hasValue = (value) => value !== undefined && value !== null && value !== "";
  const fundNameScore = (value, code) => {
    const text = raw(value).trim();
    if (!text) return -1;
    const isCodeOnly = text === raw(code).trim() || /^\d{6}$/.test(text);
    return (isCodeOnly ? 0 : 1000) + text.length;
  };
  const mergeFundFields = (target, source) => {
    for (const field of fundFields) {
      if (!hasValue(source?.[field])) continue;
      if (field === "基金名称" && fundNameScore(source[field], source.基金代码) > fundNameScore(target[field], target.基金代码)) {
        target[field] = source[field];
        continue;
      }
      if (!hasValue(target[field])) target[field] = source[field];
    }
  };
  const fundObjects = [];
  const ensureFundIndex = (row) => {
    const key = fundKey(row.基金代码, row.基金名称);
    if (fundIndex.has(key)) {
      const index = fundIndex.get(key);
      mergeFundFields(fundObjects[index], row);
      return index;
    }
    const index = fundObjects.length;
    fundIndex.set(key, index);
    fundObjects.push({ ...row });
    return index;
  };
  for (const row of fundRows || []) ensureFundIndex(row);
  const holdings = (strategyHoldingRows || []).map((row) => [
    ensureFundIndex(row),
    row.统一策略ID || "",
    row.策略名称 || "",
    row.投顾机构 || "",
    row.渠道 || "",
    row.是否广发策略 || "",
    row.风险等级 || "",
    row.业务分类 || "",
    row.研报产品类型 || "",
    row.市场地域 || "",
    row.天天当前对客展示 || "",
    row.基金分类来源 || "",
    row.基金穿透报告期 || "",
    num(row.初持仓比例),
    num(row.期末持仓比例),
    num(row.权重变化),
    num(row.区间收益率),
    num(row["近1年"])
  ]);
  const monthly = (monthlyRows || []).map((row) => [
    ensureFundIndex(row),
    row.月份 || "",
    num(row.净增配),
    num(row.中位净增配),
    num(row.加仓权重),
    num(row.减仓权重),
    num(row.调仓策略数) || 0
  ]);
  const funds = fundObjects.map(compactFund);
  return { version: 1, fundFields, holdingFields, monthlyFields, funds, holdings, monthly };
}

const AI_ENTITY_CATALOG = [
  { key: "equity", label: "权益资产", type: "资产大类", aliases: ["权益", "股票资产", "股票类资产", "权益类"], patterns: ["权益|股票型|主动权益|宽基|A股|港股|美股"], note: "权益资产宽口径，由A股、港股、美股及其他权益子实体向上汇总。" },
  { key: "a_equity", label: "A股", type: "资产", aliases: ["A股", "国内权益", "中国股票", "内地股票"], parentKeys: ["equity"], patterns: ["A股|国内权益|中国股票|内地股票|沪深|中证|创业板|科创|红利"], note: "A股资产口径来自研报大类资产和基金资产暴露。" },
  { key: "hk_equity", label: "港股", type: "资产", aliases: ["港股", "香港股票", "香港权益", "H股"], parentKeys: ["equity", "overseas"], patterns: ["港股|香港股票|香港权益|恒生|H股"], note: "港股宽口径，不等同于恒生科技。" },
  { key: "us_equity", label: "美股", type: "资产", aliases: ["美股", "美国股票", "美国权益", "美国市场"], parentKeys: ["equity", "overseas"], patterns: ["美股|美国股票|美国权益|美国市场|纳斯达克|NASDAQ|标普\\s*500|S&P\\s*500|SP500|道琼斯"], note: "美股宽口径，不等同于纳指；由研报大类资产、资产暴露、指数实体共同汇总。" },
  { key: "overseas", label: "海外资产", type: "资产大类", aliases: ["海外", "海外资产", "全球", "QDII", "境外", "全球资产"], patterns: ["海外|全球|QDII|境外|海外债券|海外REIT|其他发达市场|新兴市场"], note: "海外宽口径，包含港股、美股、海外债券、新兴市场、其他发达市场、海外REIT 等。" },
  { key: "developed_market", label: "其他发达市场", type: "地域", aliases: ["其他发达市场", "发达市场", "成熟市场"], parentKeys: ["overseas", "equity"], patterns: ["其他发达市场|发达市场|成熟市场|日本|德国|英国|欧洲"], note: "除美股/港股以外的发达市场权益或区域资产。" },
  { key: "emerging_market", label: "新兴市场", type: "地域", aliases: ["新兴市场", "新兴国家", "东盟", "巴西"], parentKeys: ["overseas", "equity"], patterns: ["新兴市场|东盟|巴西|亚洲"], note: "新兴市场区域权益或相关基金；印度、越南等国家口径由子实体向上汇总。" },
  { key: "japan_equity", label: "日本权益", type: "地域", aliases: ["日本", "日经", "日本股票"], parentKeys: ["developed_market"], patterns: ["日本|日经|Nikkei"], note: "日本市场相关基金。" },
  { key: "germany_equity", label: "德国权益", type: "地域", aliases: ["德国", "DAX", "德国DAX"], parentKeys: ["developed_market"], patterns: ["德国|DAX"], note: "德国市场相关基金。" },
  { key: "uk_equity", label: "英国权益", type: "地域", aliases: ["英国", "富时100", "FTSE100"], parentKeys: ["developed_market"], patterns: ["英国|富时\\s*100|FTSE\\s*100"], note: "英国市场相关基金。" },
  { key: "india_equity", label: "印度权益", type: "地域", aliases: ["印度", "印度股票"], parentKeys: ["emerging_market"], patterns: ["印度"], note: "印度市场相关基金。" },
  { key: "vietnam_equity", label: "越南权益", type: "地域", aliases: ["越南", "越南市场"], parentKeys: ["emerging_market"], patterns: ["越南"], note: "越南市场相关基金。" },
  { key: "nasdaq100", label: "纳指/纳斯达克100", type: "指数", aliases: ["纳指", "纳斯达克", "纳斯达克100", "NASDAQ", "NASDAQ-100", "NASDAQ 100", "NDX"], parentKeys: ["us_equity"], patterns: ["纳指|纳斯达克|NASDAQ|NDX"], note: "纳指为美股子口径，只在明确命中纳斯达克相关基金或分类时成立。" },
  { key: "sp500", label: "标普500", type: "指数", aliases: ["标普500", "标普 500", "S&P500", "S&P 500", "SP500", "SPX"], parentKeys: ["us_equity"], patterns: ["标普\\s*500|S&P\\s*500|SP500|SPX"], note: "标普500为美股子口径。" },
  { key: "dowjones", label: "道琼斯", type: "指数", aliases: ["道琼斯", "道指", "DOW"], parentKeys: ["us_equity"], patterns: ["道琼斯|道指|DOW"], note: "道琼斯指数相关基金。" },
  { key: "hstech", label: "恒生科技", type: "指数", aliases: ["恒生科技", "恒科"], parentKeys: ["hk_equity"], patterns: ["恒生科技|恒科"], note: "港股科技指数口径。" },
  { key: "hsi", label: "恒生指数", type: "指数", aliases: ["恒生指数", "恒指", "HSI"], parentKeys: ["hk_equity"], patterns: ["恒生指数|恒指|HSI"], note: "香港恒生指数口径。" },
  { key: "hs300", label: "沪深300", type: "指数", aliases: ["沪深300", "沪深 300", "CSI300", "000300"], parentKeys: ["a_equity"], patterns: ["沪深\\s*300|CSI\\s*300|000300"], note: "A股宽基指数口径。" },
  { key: "csi500", label: "中证500", type: "指数", aliases: ["中证500", "中证 500", "CSI500", "000905"], parentKeys: ["a_equity"], patterns: ["中证\\s*500|CSI\\s*500|000905"], note: "A股中盘宽基指数口径。" },
  { key: "csi1000", label: "中证1000", type: "指数", aliases: ["中证1000", "中证 1000", "CSI1000", "000852"], parentKeys: ["a_equity"], patterns: ["中证\\s*1000|CSI\\s*1000|000852"], note: "A股小盘宽基指数口径。" },
  { key: "chinext", label: "创业板", type: "指数", aliases: ["创业板", "创业板指"], parentKeys: ["a_equity"], patterns: ["创业板"], note: "创业板相关指数或主题。" },
  { key: "dividend", label: "红利/高股息", type: "风格", aliases: ["红利", "高股息", "股息", "红利低波"], parentKeys: ["a_equity"], patterns: ["红利|高股息|股息|红利低波"], note: "红利或高股息风格。" },
  { key: "fixed_income", label: "固收资产", type: "资产大类", aliases: ["固收", "固定收益", "债券资产"], patterns: ["固收|固定收益|债券|债券型|债基|短债|纯债|信用债|可转债|金融债|政策性金融债|国开债|利率债|海外债券|美元债"], note: "固收宽口径，包含债券、短债、信用债、可转债和海外债。" },
  { key: "bond", label: "债券", type: "资产", aliases: ["债券", "债券型", "债基"], parentKeys: ["fixed_income"], patterns: ["债券|债券型|债基|金融债|政策性金融债|国开债|利率债"], note: "债券资产宽口径。" },
  { key: "short_bond", label: "短债/中短债", type: "资产", aliases: ["短债", "中短债", "短期债"], parentKeys: ["bond"], patterns: ["短债|中短债|短期债"], note: "短债和中短债基金。" },
  { key: "pure_bond", label: "纯债", type: "资产", aliases: ["纯债"], parentKeys: ["bond"], patterns: ["纯债"], note: "纯债基金。" },
  { key: "credit_bond", label: "信用债", type: "资产", aliases: ["信用债"], parentKeys: ["bond"], patterns: ["信用债"], note: "信用债基金。" },
  { key: "convertible_bond", label: "可转债", type: "资产", aliases: ["可转债", "转债"], parentKeys: ["bond", "equity"], patterns: ["可转债|转债"], note: "可转债具备债性和权益弹性。" },
  { key: "overseas_bond", label: "海外债券", type: "资产", aliases: ["海外债", "海外债券", "亚洲债"], parentKeys: ["fixed_income", "overseas"], patterns: ["海外债|海外债券|亚洲债|票息"], note: "海外固收/亚洲债宽口径；美元债由子实体向上汇总。" },
  { key: "usd_bond", label: "美元债", type: "资产", aliases: ["美元债", "美元债券"], parentKeys: ["overseas_bond"], patterns: ["美元债"], note: "美元债子口径。" },
  { key: "cash", label: "货币及现金", type: "资产", aliases: ["货币", "现金", "现金管理", "货币基金"], patterns: ["货币及现金|货币型|货币基金|现金管理"], note: "货币基金和现金管理资产。" },
  { key: "commodity", label: "商品", type: "资产大类", aliases: ["商品", "大宗商品"], patterns: ["商品|大宗商品"], note: "商品宽口径，包含黄金、贵金属和明确能源商品等；石油天然气行业主题不默认上卷为商品资产。" },
  { key: "precious_metals", label: "贵金属", type: "资产", aliases: ["贵金属"], parentKeys: ["commodity"], patterns: ["贵金属"], note: "贵金属资产，黄金会作为子实体向上汇总。" },
  { key: "gold", label: "黄金", type: "资产", aliases: ["黄金", "商品黄金", "黄金ETF", "黄金联接"], parentKeys: ["precious_metals"], patterns: ["黄金|商品黄金|黄金ETF"], note: "黄金持仓由研报大类资产、资产暴露和基金名称共同核验。" },
  { key: "energy_commodity", label: "能源商品", type: "资产", aliases: ["能源商品"], parentKeys: ["commodity"], patterns: ["能源商品|能源/商品"], note: "能源商品资产口径；“能源”单独出现时不默认等同于石油或能源商品。" },
  { key: "oil_gas", label: "石油/天然气", type: "主题", aliases: ["石油", "油气", "石油天然气", "天然气", "原油"], parentKeys: [], patterns: ["石油|天然气|油气|原油"], note: "石油天然气行业主题，优先来自基金名称和行业主题；不默认等同于能源商品资产。" },
  { key: "reit", label: "REIT", type: "资产", aliases: ["REIT", "REITs", "公募REIT", "海外REIT"], parentKeys: ["overseas"], patterns: ["REIT|REITs|公募REIT|海外REIT"], note: "REIT/海外REIT 资产。" },
  { key: "index_fund", label: "指数基金", type: "产品形态", aliases: ["指数", "指数基金", "被动指数", "宽基指数"], patterns: ["指数|ETF|联接|增强|宽基"], note: "指数和ETF工具型基金。" },
  { key: "etf_link", label: "ETF联接", type: "产品形态", aliases: ["ETF联接", "联接基金", "ETF连接"], parentKeys: ["index_fund"], patterns: ["ETF联接|联接"], note: "ETF联接基金。" },
  { key: "active_equity", label: "主动权益", type: "产品形态", aliases: ["主动权益", "主动股票", "主动基金"], parentKeys: ["equity"], patterns: ["主动权益|主动股票|主动基金|主动优选"], note: "主动权益/主动股票类基金。" },
  { key: "fof", label: "FOF", type: "产品形态", aliases: ["FOF", "基金中基金"], patterns: ["FOF|基金中基金"], note: "FOF 产品。" },
  { key: "pension", label: "养老/目标日期", type: "产品形态", aliases: ["养老", "目标日期", "目标风险"], patterns: ["养老|目标日期|目标风险"], note: "养老、目标日期或目标风险产品。" },
  { key: "fixed_income_plus", label: "固收+", type: "产品形态", aliases: ["固收+", "固收加", "固收增强"], parentKeys: ["fixed_income", "equity"], patterns: ["固收\\+|固收加|固收增强|偏债混合|二级债"], note: "固收增强/偏债混合类资产配置。" },
  { key: "technology", label: "科技宽口径", type: "行业主题", aliases: ["科技", "科技成长", "TMT"], parentKeys: ["equity"], patterns: ["科技|TMT|计算机|电子|通信|互联网"], note: "科技宽口径，不等同于AI核心；AI、光模块、半导体、通信、信息技术等细分实体分别核验后向上汇总。" },
  { key: "ai_core", label: "AI核心", type: "行业主题", aliases: ["AI", "AI主题", "AI核心", "人工智能主题", "AI产业链"], parentKeys: ["technology"], patterns: ["AI核心|AI主题|人工智能|AIGC|大模型|算力|CPO|光模块|光通信|数据中心|GPU"], note: "AI核心口径，包含人工智能应用、算力基础设施、光模块/CPO、半导体等明确AI产业链证据；不因泛泛科技/TMT自动命中。" },
  { key: "ai_theme", label: "人工智能/大模型", type: "行业主题", aliases: ["人工智能", "大模型", "AIGC", "生成式AI", "机器学习"], parentKeys: ["ai_core"], patterns: ["人工智能|AIGC|生成式AI|大模型|机器学习|(^|[^A-Za-z])AI([^A-Za-z]|$)"], note: "人工智能、大模型、AIGC等直接AI主题。" },
  { key: "ai_compute", label: "算力/云计算/数据中心", type: "行业主题", aliases: ["算力", "云计算", "数据中心", "IDC", "GPU", "服务器", "液冷", "东数西算"], parentKeys: ["ai_core", "digital_economy"], patterns: ["算力|云计算|数据中心|IDC|GPU|服务器|液冷|东数西算"], note: "AI算力和云数据中心基础设施。" },
  { key: "optical_module_cpo", label: "光模块/CPO/光通信", type: "行业主题", aliases: ["光模块", "CPO", "光通信", "光器件", "硅光", "800G", "1.6T"], parentKeys: ["ai_core", "communication"], patterns: ["光模块|CPO|光通信|光器件|硅光|800G|1\\.6T"], note: "光模块、CPO、光通信等AI算力网络链条；不能用普通通信宽口径替代。" },
  { key: "semiconductor", label: "半导体/芯片", type: "行业主题", aliases: ["半导体", "芯片", "集成电路", "晶圆", "封测"], parentKeys: ["ai_core", "technology"], patterns: ["半导体|芯片|集成电路|晶圆|封测"], note: "半导体/芯片主题；单独的“电子”不自动等同半导体。" },
  { key: "electronics", label: "电子", type: "行业主题", aliases: ["电子", "消费电子", "电子元件"], parentKeys: ["technology"], patterns: ["消费电子|电子元件|电子/半导体|电子行业"], note: "电子宽口径，只有明确半导体/芯片或AI链条证据时才进入对应子实体。" },
  { key: "digital_economy", label: "数字经济/信创", type: "行业主题", aliases: ["数字经济", "数据要素", "信创", "软件", "信息技术", "大数据"], parentKeys: ["technology"], patterns: ["数字经济|数据要素|信创|软件|大数据|信息技术"], note: "数字经济、数据要素、信创和软件服务主题。" },
  { key: "communication", label: "通信/5G/6G", type: "行业主题", aliases: ["通信", "5G", "6G", "通信设备"], parentKeys: ["technology"], patterns: ["通信|5G|6G|通信设备"], note: "通信宽口径；光模块/CPO需要明确光通信链条证据。" },
  { key: "internet_media", label: "互联网/传媒/游戏", type: "行业主题", aliases: ["互联网", "传媒", "游戏", "港股互联网"], parentKeys: ["technology"], patterns: ["互联网|传媒|游戏|港股互联网"], note: "互联网、传媒、游戏和平台经济主题。" },
  { key: "robotics", label: "机器人/智能制造", type: "行业主题", aliases: ["机器人", "智能制造", "工业自动化"], parentKeys: ["ai_core", "advanced_manufacturing"], patterns: ["机器人|智能制造|工业自动化"], note: "机器人、智能制造和工业自动化主题。" },
  { key: "auto_driving", label: "智能驾驶", type: "行业主题", aliases: ["智能驾驶", "自动驾驶", "无人驾驶", "车联网"], parentKeys: ["ai_core", "ev"], patterns: ["智能驾驶|自动驾驶|无人驾驶|车联网"], note: "智能驾驶和车联网主题。" },
  { key: "advanced_manufacturing", label: "高端制造", type: "行业主题", aliases: ["高端制造", "先进制造", "制造升级"], parentKeys: ["equity"], patterns: ["高端制造|先进制造|制造升级|智能制造|机械设备|自动化"], note: "高端制造宽口径，包含机械设备、自动化、工业软件等。" },
  { key: "industrial_automation", label: "机械设备/自动化", type: "行业主题", aliases: ["机械设备", "自动化", "工业母机", "机床"], parentKeys: ["advanced_manufacturing"], patterns: ["机械设备|自动化|工业母机|机床"], note: "机械设备、工业母机和自动化方向。" },
  { key: "new_materials", label: "新材料", type: "行业主题", aliases: ["新材料", "先进材料"], parentKeys: ["advanced_manufacturing", "materials"], patterns: ["新材料|先进材料"], note: "先进材料、新材料主题。" },
  { key: "new_energy", label: "新能源", type: "行业主题", aliases: ["新能源", "新能源产业"], parentKeys: ["equity"], patterns: ["新能源|新能源产业"], note: "新能源主题父级；光伏、风电、储能、锂电、新能源车、电网等由子实体向上汇总。" },
  { key: "solar", label: "光伏", type: "行业主题", aliases: ["光伏", "太阳能"], parentKeys: ["new_energy"], patterns: ["光伏|太阳能"], note: "光伏产业链。" },
  { key: "wind_power", label: "风电", type: "行业主题", aliases: ["风电", "风能"], parentKeys: ["new_energy"], patterns: ["风电|风能"], note: "风电产业链。" },
  { key: "energy_storage", label: "储能/锂电", type: "行业主题", aliases: ["储能", "锂电", "动力电池", "电池"], parentKeys: ["new_energy"], patterns: ["储能|锂电|动力电池|电池"], note: "储能、锂电和动力电池主题。" },
  { key: "ev", label: "新能源车", type: "行业主题", aliases: ["新能源车", "新能源汽车", "智能汽车", "电动车"], parentKeys: ["new_energy"], patterns: ["新能源车|新能源汽车|智能汽车|电动车"], note: "新能源车主题。" },
  { key: "power_grid", label: "电网/特高压", type: "行业主题", aliases: ["电网", "特高压", "充电桩", "电力设备"], parentKeys: ["new_energy"], patterns: ["电网|特高压|充电桩|电力设备"], note: "电网、特高压、充电设施和电力设备主题。" },
  { key: "pharma", label: "医药健康", type: "行业主题", aliases: ["医药", "医疗", "生物医药", "医药健康"], parentKeys: ["equity"], patterns: ["医药|医疗|生物医药|医药健康"], note: "医药健康宽口径。" },
  { key: "innovative_drug", label: "创新药", type: "行业主题", aliases: ["创新药", "生物科技", "生物技术"], parentKeys: ["pharma"], patterns: ["创新药|生物科技|生物技术"], note: "创新药、生物科技主题。" },
  { key: "cxo", label: "CXO", type: "行业主题", aliases: ["CXO", "CRO", "CDMO"], parentKeys: ["pharma"], patterns: ["CXO|CRO|CDMO"], note: "CXO、CRO、CDMO医药外包主题。" },
  { key: "medical_device", label: "医疗器械", type: "行业主题", aliases: ["医疗器械", "器械"], parentKeys: ["pharma"], patterns: ["医疗器械|器械"], note: "医疗器械主题。" },
  { key: "tcm", label: "中药", type: "行业主题", aliases: ["中药"], parentKeys: ["pharma"], patterns: ["中药"], note: "中药主题。" },
  { key: "consumption", label: "消费", type: "行业主题", aliases: ["消费", "大消费"], parentKeys: ["equity"], patterns: ["消费|食品饮料|白酒|家电|美容护理|旅游|零售"], note: "消费宽口径。" },
  { key: "baijiu", label: "白酒/食品饮料", type: "行业主题", aliases: ["白酒", "食品饮料", "酒"], parentKeys: ["consumption"], patterns: ["白酒|食品饮料|酒"], note: "白酒和食品饮料主题。" },
  { key: "home_appliance", label: "家电", type: "行业主题", aliases: ["家电", "家用电器"], parentKeys: ["consumption"], patterns: ["家电|家用电器"], note: "家电主题。" },
  { key: "tourism", label: "旅游酒店/出行服务", type: "行业主题", aliases: ["旅游", "酒店", "出行", "航空"], parentKeys: ["consumption"], patterns: ["旅游|酒店|出行|航空"], note: "旅游酒店、航空和出行服务主题。" },
  { key: "military", label: "军工/航空航天", type: "行业主题", aliases: ["军工", "国防军工", "航空航天"], parentKeys: ["advanced_manufacturing"], patterns: ["军工|国防军工|航空航天"], note: "军工和航空航天主题。" },
  { key: "finance", label: "金融", type: "行业主题", aliases: ["金融", "非银金融"], parentKeys: ["equity"], patterns: ["金融|非银金融"], note: "金融宽口径；银行、证券/券商、保险由子实体向上汇总。" },
  { key: "bank", label: "银行", type: "行业主题", aliases: ["银行"], parentKeys: ["finance"], patterns: ["银行"], note: "银行主题。" },
  { key: "brokerage", label: "证券/券商", type: "行业主题", aliases: ["证券", "券商", "非银"], parentKeys: ["finance"], patterns: ["证券|券商|非银"], note: "证券、券商和非银金融主题。" },
  { key: "insurance", label: "保险", type: "行业主题", aliases: ["保险"], parentKeys: ["finance"], patterns: ["保险"], note: "保险主题。" },
  { key: "real_estate", label: "地产/房地产", type: "行业主题", aliases: ["地产", "房地产"], parentKeys: ["equity"], patterns: ["地产|房地产"], note: "地产和房地产主题。" },
  { key: "materials", label: "周期资源/材料", type: "行业主题", aliases: ["周期", "资源", "材料", "周期资源"], parentKeys: ["equity"], patterns: ["周期|资源|材料|周期资源"], note: "周期资源和材料父级；有色、煤炭、钢铁、化工等由子实体向上汇总。" },
  { key: "nonferrous", label: "有色金属", type: "行业主题", aliases: ["有色", "有色金属", "铜", "铝", "锂"], parentKeys: ["materials"], patterns: ["有色|有色金属|铜|铝|锂"], note: "有色金属和工业金属主题。" },
  { key: "coal", label: "煤炭", type: "行业主题", aliases: ["煤炭"], parentKeys: ["materials"], patterns: ["煤炭"], note: "煤炭主题。" },
  { key: "steel", label: "钢铁", type: "行业主题", aliases: ["钢铁"], parentKeys: ["materials"], patterns: ["钢铁"], note: "钢铁主题。" },
  { key: "chemical", label: "基础化工", type: "行业主题", aliases: ["化工", "基础化工"], parentKeys: ["materials"], patterns: ["基础化工|化工"], note: "基础化工主题。" },
  { key: "low_vol", label: "低波", type: "风格", aliases: ["低波", "低波动", "低波稳健"], parentKeys: ["equity"], patterns: ["低波|低波动|低波稳健"], note: "低波动风格。" },
  { key: "value_style", label: "价值", type: "风格", aliases: ["价值", "价值风格"], parentKeys: ["equity"], patterns: ["价值风格|价值"], note: "价值风格。" },
  { key: "growth_style", label: "成长", type: "风格", aliases: ["成长", "成长风格"], parentKeys: ["equity"], patterns: ["成长风格|成长"], note: "成长风格。" },
  { key: "quality_style", label: "质量", type: "风格", aliases: ["质量", "高质量"], parentKeys: ["equity"], patterns: ["高质量|质量"], note: "质量风格。" },
  { key: "central_soe", label: "央国企/中特估", type: "风格", aliases: ["央国企", "国企", "中特估", "国企改革"], parentKeys: ["dividend", "value_style"], patterns: ["央国企|国企改革|中特估"], note: "央国企、中特估和国企改革相关风格。" },
  { key: "esg_green", label: "ESG/绿色低碳", type: "主题", aliases: ["ESG", "绿色低碳", "碳中和", "环保"], parentKeys: ["equity"], patterns: ["ESG|绿色低碳|碳中和|环保|节能"], note: "ESG、绿色低碳、环保和碳中和主题。" }
];

const AI_ENTITY_BY_KEY = new Map(AI_ENTITY_CATALOG.map((entity) => [entity.key, entity]));
const AI_ENTITY_INDEX_VERSION = 6;
const AI_ENTITY_RULE_VERSION = "strict-entity-v4.20260622";
const AI_ENTITY_GENERATED_AT = new Date().toISOString();
const AI_ENTITY_ASSET_TYPES = new Set(["资产", "资产大类", "地域"]);
const AI_ENTITY_NON_ANCHORED_ZERO_TYPES = new Set(["地域", "行业主题", "主题", "风格", "产品形态", "指数"]);
const AI_ENTITY_VERIFIED_FIELDS = new Set([
  "资产暴露",
  "基金类型",
  "基金二级分类",
  "基金同类分组",
  "基金分类依据",
  "行业暴露",
  "行业主题",
  "行业大类",
  "权益行业主题",
  "权益行业大类",
  "研报大类资产",
  "研报A股行业",
  "基金名称"
]);
const AI_ENTITY_EVIDENCE_EXCLUDES = {
  commodity: [/有色金属|有色|周期资源|煤炭|钢铁|基础化工/],
  energy_commodity: [/新能源|电力设备|光伏|储能|电动车|智能汽车/],
  ai_core: [/科技宽口径|科技成长|TMT|互联网|传媒|游戏/],
  ai_theme: [/科技宽口径|科技成长|TMT|互联网|传媒|游戏/],
  optical_module_cpo: [/通信\/5G\/6G$|通信设备$/],
  semiconductor: [/电子行业$|消费电子$/]
};
const AI_ENTITY_EVIDENCE_REQUIRES = {
  commodity: /商品|大宗商品|黄金|贵金属|能源\/商品|能源商品|石油|天然气|油气|原油/,
  energy_commodity: /能源\/商品|能源商品|石油|天然气|油气|原油/,
  ai_core: /AI核心|AI主题|人工智能|AIGC|大模型|算力|CPO|光模块|光通信|数据中心|GPU|半导体|芯片|集成电路|机器人|智能驾驶/,
  optical_module_cpo: /光模块|CPO|光通信|光器件|硅光|800G|1\.6T/,
  semiconductor: /半导体|芯片|集成电路|晶圆|封测/
};

function entityEvidencePolicy(entity) {
  if (!entity) return "";
  if (entity.type === "资产大类") return "hard_filter_requires_verified_or_child_rollup";
  if (entity.type === "指数") return "hard_filter_requires_explicit_index_name_or_classification";
  if (entity.type === "行业主题" || entity.type === "主题") return "hard_filter_requires_industry_theme_or_explicit_fund_name";
  if (entity.type === "产品形态") return "hard_filter_requires_product_type_or_explicit_fund_name";
  return "hard_filter_requires_structured_source_or_explicit_fund_name";
}

function standardEntityCatalog() {
  return AI_ENTITY_CATALOG.map((entity) => ({
    key: entity.key,
    label: entity.label,
    type: entity.type,
    dimension: entity.dimension || entity.type || "",
    queryAliases: entity.aliases || [],
    aliases: entity.aliases || [],
    parentKeys: entity.parentKeys || [],
    evidencePolicy: entityEvidencePolicy(entity),
    ruleVersion: AI_ENTITY_RULE_VERSION,
    note: entity.note || ""
  }));
}

function normalizeAiText(value) {
  return raw(value)
    .toLowerCase()
    .replace(/[Ａ-Ｚａ-ｚ０-９]/g, (char) => String.fromCharCode(char.charCodeAt(0) - 0xfee0))
    .replace(/[\s_\-－—/\\()（）【】\[\]：:,.，。]+/g, "");
}

function escapeAiRegExp(value) {
  return raw(value).replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function entityMatchesText(entity, value) {
  const text = raw(value);
  if (!text) return false;
  const compact = normalizeAiText(text);
  if ((entity.aliases || []).some((alias) => {
    const needle = normalizeAiText(alias);
    return needle && compact.includes(needle);
  })) return true;
  return (entity.patterns || []).some((pattern) => {
    try {
      return new RegExp(pattern, "i").test(text);
    } catch (_error) {
      return new RegExp(escapeAiRegExp(pattern), "i").test(text);
    }
  });
}

function entityDirectlyNamed(entity, value) {
  const text = normalizeAiText(value);
  if (!text || !entity) return false;
  const names = [entity.label, ...(entity.aliases || [])]
    .map(normalizeAiText)
    .filter(Boolean);
  return names.some((name) => text === name || text.includes(name));
}

function entityHasAncestor(childKey, ancestorKey, seen = new Set()) {
  if (!childKey || !ancestorKey || seen.has(childKey)) return false;
  seen.add(childKey);
  const entity = AI_ENTITY_BY_KEY.get(childKey);
  for (const parentKey of entity?.parentKeys || []) {
    if (parentKey === ancestorKey || entityHasAncestor(parentKey, ancestorKey, seen)) return true;
  }
  return false;
}

function pruneAncestorMatches(matches, value) {
  return (matches || []).filter((entity) => {
    const isAncestor = matches.some((other) => other.key !== entity.key && entityHasAncestor(other.key, entity.key));
    if (!isAncestor) return true;
    return entityDirectlyNamed(entity, value);
  });
}

function entityRuleId(field, entity) {
  if (field === "资产暴露") return `asset_exposure:${entity.key}`;
  if (field === "基金名称") return `explicit_fund_name:${entity.key}`;
  if (/研报大类资产|研报A股行业|行业主题|权益行业主题|基金类型|基金分类依据|行业暴露|行业大类|权益行业大类/.test(field)) {
    return `structured_field:${field}:${entity.key}`;
  }
  return `structured_field:${entity.key}`;
}

function matchEntitiesForEvidence(field, value) {
  if (!raw(value) || !AI_ENTITY_VERIFIED_FIELDS.has(field)) return [];
  const matches = AI_ENTITY_CATALOG.filter((entity) => {
    if (["产品形态", "指数"].includes(entity.type)
      && !["基金名称", "基金类型", "基金二级分类", "基金同类分组", "基金分类依据"].includes(field)) {
      return false;
    }
    if (entity.key === "commodity" && field === "基金名称"
      && !/大宗商品|商品型|商品基金|商品指数|商品期货|商品黄金|黄金|贵金属|原油|油气|能源化工|有色金属期货/.test(raw(value))) {
      return false;
    }
    if (!entityMatchesText(entity, value)) return false;
    if (field === "资产暴露" && !AI_ENTITY_ASSET_TYPES.has(entity.type)) return false;
    if (AI_ENTITY_EVIDENCE_REQUIRES[entity.key] && !AI_ENTITY_EVIDENCE_REQUIRES[entity.key].test(raw(value))) return false;
    return !(AI_ENTITY_EVIDENCE_EXCLUDES[entity.key] || []).some((pattern) => pattern.test(raw(value)));
  });
  return pruneAncestorMatches(matches, value);
}

function parseExposureParts(value) {
  const text = raw(value).replace(/，/g, "+").replace(/、/g, "+").replace(/；/g, "+");
  const parts = [];
  for (const rawPart of text.split("+")) {
    const part = rawPart.trim();
    if (!part) continue;
    const match = part.match(/^(.+?)([-+]?\d+(?:\.\d+)?)%$/);
    if (match) {
      parts.push({ label: match[1].trim(), share: Number(match[2]) });
    }
  }
  return parts.filter((part) => Number.isFinite(part.share));
}

function compactEvidence(value, maxLength = 120) {
  const text = raw(value).replace(/\s+/g, " ").trim();
  return text.length > maxLength ? `${text.slice(0, maxLength)}...` : text;
}

function isExposureSensitiveEntityType(type) {
  return ["资产", "资产大类", "地域", "行业主题", "主题", "风格", "产品形态", "指数"].includes(type);
}

function isDerivedExposureField(field) {
  return /研报大类资产|研报A股行业|行业主题|行业大类|权益行业主题|权益行业大类/.test(field);
}

function evidenceExposurePct(field, entity, options = {}) {
  if (isDerivedExposureField(field)) return 0;
  if (options.hasStructuredAssetExposure && field !== "资产暴露" && AI_ENTITY_ASSET_TYPES.has(entity.type)) return 0;
  if (field !== "资产暴露" && field !== "行业暴露" && AI_ENTITY_NON_ANCHORED_ZERO_TYPES.has(entity.type)) return 0;
  return 100;
}

function addEntityToMap(map, entity, patch) {
  if (!entity) return;
  const patchLevel = patch.level || "verified";
  const existing = map.get(entity.key) || {
    key: entity.key,
    label: entity.label,
    type: entity.type,
    exposurePct: 0,
    confidence: 0,
    exposureAnchored: false,
    level: patchLevel,
    ruleId: "",
    sourceField: "",
    sourceValue: "",
    sources: new Set(),
    evidence: [],
    evidenceChains: []
  };
  const exposurePct = patch.exposurePct === null || patch.exposurePct === undefined ? 100 : Number(patch.exposurePct);
  const incomingExposure = Number.isFinite(exposurePct) ? exposurePct : 100;
  const exposureSensitive = isExposureSensitiveEntityType(existing.type);
  if (!(existing.exposureAnchored && !patch.exposureAnchored && exposureSensitive)) {
    existing.exposurePct = Math.max(existing.exposurePct || 0, incomingExposure);
  }
  if (patch.exposureAnchored) existing.exposureAnchored = true;
  existing.confidence = Math.max(existing.confidence || 0, Number(patch.confidence) || 0.7);
  if (existing.level !== "verified" || patchLevel === "verified") existing.level = patchLevel;
  if (!existing.ruleId || patchLevel === "verified") existing.ruleId = patch.ruleId || existing.ruleId;
  if (!existing.sourceField || patchLevel === "verified") existing.sourceField = patch.sourceField || patch.source || existing.sourceField;
  if (!existing.sourceValue || patchLevel === "verified") existing.sourceValue = patch.sourceValue || existing.sourceValue;
  if (patch.source) existing.sources.add(patch.source);
  if (patch.evidence) existing.evidence.push(compactEvidence(patch.evidence));
  existing.evidenceChains.push({
    entityKey: entity.key,
    entityName: entity.label,
    entityType: entity.type,
    level: patchLevel,
    sourceField: patch.sourceField || patch.source || "",
    sourceValue: raw(patch.sourceValue || ""),
    evidence: compactEvidence(patch.evidence || ""),
    ruleId: patch.ruleId || "",
    ruleVersion: AI_ENTITY_RULE_VERSION,
    generatedAt: AI_ENTITY_GENERATED_AT
  });
  map.set(entity.key, existing);
}

function addParentEntities(map) {
  let changed = true;
  while (changed) {
    changed = false;
    for (const item of Array.from(map.values())) {
      const entity = AI_ENTITY_BY_KEY.get(item.key);
      for (const parentKey of entity?.parentKeys || []) {
        const parent = AI_ENTITY_BY_KEY.get(parentKey);
        if (!parent) continue;
        const existing = map.get(parent.key);
        const exposurePct = item.exposurePct === null || item.exposurePct === undefined ? 100 : Number(item.exposurePct);
        const exposureSensitive = isExposureSensitiveEntityType(parent.type);
        if (!item.exposureAnchored && exposureSensitive) continue;
        if (!existing || (existing.exposurePct || 0) < exposurePct) {
          addEntityToMap(map, parent, {
            exposurePct,
            exposureAnchored: item.exposureAnchored,
            confidence: Math.max(0.65, (item.confidence || 0.8) - 0.05),
            level: "derived",
            source: `父级:${item.label}`,
            sourceField: "父级汇总",
            sourceValue: item.label,
            evidence: `${item.label} => ${parent.label}`,
            ruleId: `parent_rollup:${item.key}->${parent.key}`
          });
          changed = true;
        }
      }
    }
  }
}

function hasEntityEvidence(item, predicate) {
  return (item?.evidenceChains || []).some((chain) => predicate({
    ...chain,
    sourceText: `${chain.sourceField || ""} ${chain.sourceValue || ""} ${chain.evidence || ""}`
  }));
}

function pruneWeakAssetConflicts(map, row) {
  const hasCommodityEvidence = ["gold", "precious_metals", "commodity"].some((key) => map.has(key));
  const equity = map.get("equity");
  if (!hasCommodityEvidence || !equity) return;

  const explicitEquityEvidence = hasEntityEvidence(equity, (chain) => {
    if (!/资产暴露|研报大类资产|行业暴露|行业主题|行业大类|权益行业主题|权益行业大类|研报A股行业/.test(chain.sourceField || "")) return false;
    return /权益|股票|A股|港股|美股|沪深|中证|科技|消费|医药|新能源|金融|材料|电子|半导体|通信/.test(chain.sourceText || "");
  });
  if (explicitEquityEvidence) return;

  const assetText = raw(`${row.资产暴露 || ""} ${row.研报大类资产 || ""}`);
  if (/权益|股票|A股|港股|美股/.test(assetText)) return;

  map.delete("equity");
}

function fundEntitiesFromRow(row) {
  const fields = [
    "基金名称", "基金代码", "基金类型", "基金二级分类", "基金同类分组",
    "资产暴露", "行业暴露", "行业主题", "行业大类",
    "权益行业主题", "权益行业大类", "研报大类资产", "研报A股行业"
  ];
  const map = new Map();
  const exposureParts = parseExposureParts(row.资产暴露);
  const industryExposureParts = parseExposureParts(row.行业暴露);
  for (const part of exposureParts) {
    if (!Number.isFinite(part.share) || part.share <= 0.0001) continue;
    for (const entity of matchEntitiesForEvidence("资产暴露", part.label)) {
      addEntityToMap(map, entity, {
        exposurePct: part.share,
        exposureAnchored: true,
        confidence: 0.98,
        level: "verified",
        source: "资产暴露",
        sourceField: "资产暴露",
        sourceValue: `${part.label}${part.share}%`,
        evidence: `${part.label}${part.share}%`,
        ruleId: entityRuleId("资产暴露", entity)
      });
    }
  }
  if (exposureParts.length) addParentEntities(map);
  for (const part of industryExposureParts) {
    if (!Number.isFinite(part.share) || part.share <= 0.0001) continue;
    for (const entity of matchEntitiesForEvidence("行业暴露", part.label)) {
      addEntityToMap(map, entity, {
        exposurePct: part.share,
        exposureAnchored: true,
        confidence: 0.96,
        level: "verified",
        source: "行业暴露",
        sourceField: "行业暴露",
        sourceValue: `${part.label}${part.share}%`,
        evidence: `${part.label}${part.share}%`,
        ruleId: entityRuleId("行业暴露", entity)
      });
    }
  }
  if (industryExposureParts.length) addParentEntities(map);
  for (const field of fields) {
    const value = row[field];
    if (!raw(value)) continue;
    if (field === "资产暴露" && exposureParts.length) continue;
    if (field === "行业暴露" && industryExposureParts.length) continue;
    for (const entity of matchEntitiesForEvidence(field, value)) {
      const exposurePct = evidenceExposurePct(field, entity, {
        hasStructuredAssetExposure: exposureParts.length > 0
      });
      const derivedExposureField = exposurePct <= 0;
      const sourceWeight = /资产暴露|研报大类资产|研报A股行业|行业主题|权益行业主题/.test(field) ? 0.94 : (field === "基金名称" ? 0.86 : 0.82);
      addEntityToMap(map, entity, {
        exposurePct,
        confidence: sourceWeight,
        level: derivedExposureField ? "derived" : "verified",
        source: field,
        sourceField: field,
        sourceValue: value,
        evidence: `${field}:${value}`,
        ruleId: entityRuleId(field, entity)
      });
    }
  }
  addParentEntities(map);
  pruneWeakAssetConflicts(map, row);
  addParentEntities(map);
  return Array.from(map.values()).map((item) => ({
    key: item.key,
    label: item.label,
    type: item.type,
    exposurePct: Number((item.exposurePct || 0).toFixed(4)),
    confidence: Number((item.confidence || 0).toFixed(4)),
    level: item.level || "verified",
    sourceField: item.sourceField || Array.from(item.sources)[0] || "",
    sourceValue: item.sourceValue || "",
    ruleId: item.ruleId || "",
    ruleVersion: AI_ENTITY_RULE_VERSION,
    generatedAt: AI_ENTITY_GENERATED_AT,
    source: Array.from(item.sources).slice(0, 4).join("、"),
    evidence: [...new Set(item.evidence)].slice(0, 4).join("；"),
    evidenceChain: item.evidenceChains.slice(0, 8)
  })).sort((a, b) => {
    if (a.type !== b.type) return a.type.localeCompare(b.type, "zh-CN");
    return b.exposurePct - a.exposurePct || a.label.localeCompare(b.label, "zh-CN");
  });
}

function buildAiSemanticIndex(summary, currentHoldingRows, fundRows) {
  const strategyById = new Map((summary.strategies || []).map((row) => [raw(row.统一策略ID), row]));
  const fundEntityRows = [];
  const fundEntityByFund = new Map();
  const fundRowsByKey = new Map();
  for (const row of [...(fundRows || []), ...(currentHoldingRows || [])]) {
    const key = `${raw(row.基金代码)}｜${raw(row.基金名称)}`;
    if (!raw(row.基金代码) && !raw(row.基金名称)) continue;
    if (!fundRowsByKey.has(key)) fundRowsByKey.set(key, row);
  }
  for (const [fundKey, row] of fundRowsByKey.entries()) {
    const entities = fundEntitiesFromRow(row);
    fundEntityByFund.set(fundKey, entities);
    for (const entity of entities) {
      fundEntityRows.push([
        raw(row.基金代码),
        raw(row.基金名称),
        entity.key,
        entity.label,
        entity.type,
        entity.exposurePct,
        entity.confidence,
        entity.level,
        entity.sourceField,
        entity.sourceValue,
        entity.source,
        entity.evidence,
        entity.ruleId,
        entity.ruleVersion,
        entity.generatedAt
      ]);
    }
  }

  const dedupedHoldingByFund = new Map();
  for (const row of currentHoldingRows || []) {
    const strategy = strategyById.get(raw(row.统一策略ID)) || {};
    const holdingDate = raw(strategy.最新持仓日 || row.最新持仓日 || "");
    const weight = num(row.期末持仓比例) || 0;
    if (!raw(row.统一策略ID) || weight <= 0) continue;
    const fundCode = raw(row.基金代码);
    const fundName = raw(row.基金名称);
    const dedupeKey = [raw(row.统一策略ID), holdingDate, fundCode || fundName, fundName].join("｜");
    const existing = dedupedHoldingByFund.get(dedupeKey);
    if (!existing || weight > existing.weight) {
      dedupedHoldingByFund.set(dedupeKey, { row, weight, holdingDate });
    }
  }

  const strategyAgg = new Map();
  const holdingRows = [];
  for (const item of dedupedHoldingByFund.values()) {
    const row = item.row;
    const strategy = strategyById.get(raw(row.统一策略ID)) || {};
    const holdingDate = raw(strategy.最新持仓日 || item.holdingDate || row.最新持仓日 || "");
    const weight = item.weight;
    holdingRows.push([
      raw(row.统一策略ID),
      raw(row.策略名称),
      holdingDate,
      raw(row.基金代码),
      raw(row.基金名称),
      raw(row.基金类型),
      raw(row.研报大类资产),
      raw(row.行业主题),
      raw(row.权益行业主题),
      raw(row.研报A股行业),
      weight
    ]);
    const fundKey = `${raw(row.基金代码)}｜${raw(row.基金名称)}`;
    for (const entity of fundEntityByFund.get(fundKey) || fundEntitiesFromRow(row)) {
      const entityExposurePct = entity.exposurePct === null || entity.exposurePct === undefined ? 100 : Number(entity.exposurePct);
      const contribution = weight * (Number.isFinite(entityExposurePct) ? entityExposurePct : 100) / 100;
      if (contribution <= 0.0001) continue;
      const aggKey = `${raw(row.统一策略ID)}｜${entity.key}`;
      if (!strategyAgg.has(aggKey)) {
        strategyAgg.set(aggKey, {
          strategyId: raw(row.统一策略ID),
          strategyName: raw(row.策略名称),
          entity,
          weightPct: 0,
          latestDate: holdingDate,
          funds: new Map(),
          confidence: 0
        });
      }
      const agg = strategyAgg.get(aggKey);
      if (holdingDate && holdingDate > raw(agg.latestDate)) agg.latestDate = holdingDate;
      agg.confidence = Math.max(agg.confidence, entity.confidence || 0.75);
      const fundLabel = `${raw(row.基金名称) || raw(row.基金代码)} ${contribution.toFixed(2)}%`;
      const existingFund = agg.funds.get(fundKey);
      if (!existingFund || contribution > existingFund.weight) {
        agg.funds.set(fundKey, {
        label: fundLabel,
        weight: contribution,
        evidence: entity.evidence,
        sourceField: entity.sourceField,
        sourceValue: entity.sourceValue,
        level: entity.level,
        ruleId: entity.ruleId
        });
      }
    }
  }

  strategyAgg.forEach((agg) => {
    agg.weightPct = Math.min(100, Array.from(agg.funds.values()).reduce((total, fund) => total + (num(fund.weight) || 0), 0));
  });

  const strategyEntityRows = Array.from(strategyAgg.values())
    .map((agg) => {
      const funds = Array.from(agg.funds.values()).sort((a, b) => b.weight - a.weight);
      const evidence = funds.slice(0, 5).map((item) => item.label).join("、");
      const evidenceDetail = funds.slice(0, 5).map((item) => {
        const source = [item.sourceField, item.sourceValue].filter(Boolean).join(":");
        return [item.label, item.level, source, item.ruleId].filter(Boolean).join("｜");
      }).join("；");
      return [
        agg.strategyId,
        agg.strategyName,
        agg.entity.key,
        agg.entity.label,
        agg.entity.type,
        Number(agg.weightPct.toFixed(4)),
        raw(agg.latestDate),
        evidence,
        funds.length,
        Number((agg.confidence || 0).toFixed(4)),
        "derived",
        "最新持仓基金实体汇总",
        evidenceDetail,
        `strategy_holding_rollup:${agg.entity.key}`,
        AI_ENTITY_RULE_VERSION,
        AI_ENTITY_GENERATED_AT
      ];
    })
    .sort((a, b) => String(a[0]).localeCompare(String(b[0])) || Number(b[5]) - Number(a[5]));

  return {
    version: AI_ENTITY_INDEX_VERSION,
    source: "current_holding_strategy_rows_with_fund_classification",
    generatedAt: new Date().toISOString(),
    fields: ["统一策略ID", "策略名称", "持仓日期", "基金代码", "基金名称", "基金类型", "研报大类资产", "行业主题", "权益行业主题", "研报A股行业", "权重"],
    strategyCount: new Set(holdingRows.map((row) => row[0])).size,
    holdingCount: holdingRows.length,
    entityCount: AI_ENTITY_CATALOG.length,
    fundEntityCount: fundEntityRows.length,
    strategyEntityCount: strategyEntityRows.length,
    dataQuality: {
      rawHoldingRows: (currentHoldingRows || []).length,
      dedupedHoldingRows: holdingRows.length,
      dedupedBy: "统一策略ID+持仓日期+基金代码/基金名称，重复持仓取最大权重，防止多口径证据重复累加。",
      strategyEntityAggregation: "策略层实体暴露按最新持仓基金实体贡献求和；同一基金同一实体只计一次，多个字段作为证据链保留。"
    },
    entityGraph: {
      version: "standard-entity-graph-v2.20260622",
      dimensions: ["asset_class", "market_region", "standard_industry", "investment_theme", "index_underlying", "style_factor", "product_strategy"],
      aggregationRules: [
        "资产类别、地域、标准行业尽量按同维度去重汇总。",
        "投资主题、风格、指数为多标签暴露，允许重叠；父级按子实体基金集合并集汇总，不做父子简单相加。",
        "策略实体仅由最新持仓基金实体按策略持仓权重聚合，不由模型直接判断。"
      ]
    },
    entityCatalog: standardEntityCatalog(),
    queryAliasCatalog: standardEntityCatalog().map((entity) => ({
      key: entity.key,
      label: entity.label,
      type: entity.type,
      aliases: entity.queryAliases,
      note: entity.note
    })),
    rows: holdingRows,
    fundEntities: {
      fields: ["基金代码", "基金名称", "实体Key", "实体名称", "实体类型", "暴露比例", "置信度", "实体等级", "来源字段", "来源值", "来源", "证据", "抽取规则ID", "规则版本", "生成时间"],
      rows: fundEntityRows
    },
    strategyEntities: {
      fields: ["统一策略ID", "策略名称", "实体Key", "实体名称", "实体类型", "权重", "持仓日期", "证据基金", "基金数", "置信度", "实体等级", "来源字段", "来源值", "抽取规则ID", "规则版本", "生成时间"],
      rows: strategyEntityRows
    }
  };
}

function buildStandardEntityDictionary(aiIndex) {
  const fundFields = aiIndex?.fundEntities?.fields || [];
  const fundRows = aiIndex?.fundEntities?.rows || [];
  const strategyFields = aiIndex?.strategyEntities?.fields || [];
  const strategyRows = aiIndex?.strategyEntities?.rows || [];
  const idx = (fields, name) => fields.indexOf(name);
  const fundKeyIndex = idx(fundFields, "实体Key");
  const fundCodeIndex = idx(fundFields, "基金代码");
  const fundNameIndex = idx(fundFields, "基金名称");
  const fundSourceIndex = idx(fundFields, "来源字段");
  const fundValueIndex = idx(fundFields, "来源值");
  const strategyKeyIndex = idx(strategyFields, "实体Key");
  const strategyIdIndex = idx(strategyFields, "统一策略ID");
  const strategyNameIndex = idx(strategyFields, "策略名称");
  const rowsByKey = new Map(standardEntityCatalog().map((entity) => [entity.key, {
    ...entity,
    命中基金数: 0,
    命中策略数: 0,
    证据基金: [],
    证据策略: []
  }]));
  const fundSeen = new Set();
  for (const row of fundRows) {
    const key = raw(row[fundKeyIndex]);
    const code = raw(row[fundCodeIndex]);
    if (!key || !code || !rowsByKey.has(key)) continue;
    const seenKey = `${key}|${code}`;
    if (fundSeen.has(seenKey)) continue;
    fundSeen.add(seenKey);
    const item = rowsByKey.get(key);
    item.命中基金数 += 1;
    if (item.证据基金.length < 8) {
      item.证据基金.push({
        基金代码: code,
        基金名称: raw(row[fundNameIndex]),
        来源字段: raw(row[fundSourceIndex]),
        来源值: raw(row[fundValueIndex])
      });
    }
  }
  const strategySeen = new Set();
  for (const row of strategyRows) {
    const key = raw(row[strategyKeyIndex]);
    const id = raw(row[strategyIdIndex]);
    if (!key || !id || !rowsByKey.has(key)) continue;
    const seenKey = `${key}|${id}`;
    if (strategySeen.has(seenKey)) continue;
    strategySeen.add(seenKey);
    const item = rowsByKey.get(key);
    item.命中策略数 += 1;
    if (item.证据策略.length < 8) {
      item.证据策略.push({
        统一策略ID: id,
        策略名称: raw(row[strategyNameIndex])
      });
    }
  }
  const rows = Array.from(rowsByKey.values())
    .map((row) => ({
      ...row,
      实体Key: row.key,
      实体名称: row.label,
      实体类型: row.type,
      别名: row.aliases || [],
      父实体Key: row.parentKeys || [],
      口径说明: row.note || ""
    }))
    .sort((a, b) => raw(a.type).localeCompare(raw(b.type), "zh-CN") || b.命中策略数 - a.命中策略数 || raw(a.label).localeCompare(raw(b.label), "zh-CN"));
  const typeMap = new Map();
  for (const row of rows) {
    const type = row.type || "未分类";
    const item = typeMap.get(type) || { 实体类型: type, 实体数: 0, 命中基金数: 0, 命中策略数: 0 };
    item.实体数 += 1;
    item.命中基金数 += row.命中基金数 || 0;
    item.命中策略数 += row.命中策略数 || 0;
    typeMap.set(type, item);
  }
  return {
    version: 1,
    generatedAt: new Date().toISOString(),
    ruleVersion: AI_ENTITY_RULE_VERSION,
    source: "apply_field_renames_and_build_insights.AI_ENTITY_CATALOG",
    说明: "标准实体字典用于AI选策略、主题分析、策略持仓实体暴露和自定义分析。命中数基于当前最新持仓和基金实体证据动态计算。",
    汇总: {
      实体数: rows.length,
      命中基金实体行数: fundRows.length,
      命中策略实体行数: strategyRows.length
    },
    按类型汇总: Array.from(typeMap.values()).sort((a, b) => b.命中策略数 - a.命中策略数),
    实体列表: rows
  };
}

const currentFundRows = finalizeWeightAgg([...fundAgg.values()]).sort((a, b) => b.总权重 - a.总权重);
const currentCompanyRows = finalizeWeightAgg([...companyAgg.values()]).sort((a, b) => b.总权重 - a.总权重);
const rebalanceFundMonthlyRowsFinal = finalizeRebalanceFundMonthlyRows();
const rebalanceFundMonthlySummaryRows = rebalanceFundMonthlyRowsFinal.slice(0, 50000);
const currentHoldingStrategyRowsFinal = currentHoldingStrategyRows.sort((a, b) => b.期末持仓比例 - a.期末持仓比例);
const strategyAssetChangeRowsFinal = finalizeStrategyAssetChangeRows();
const strategyAssetChangeSummaryRows = strategyAssetChangeRowsFinal
  .filter((row) => Math.abs(nz(row.净增配)) > 0.0001 || Math.abs(nz(row.总点位)) > 0.0001)
  .slice(0, 60000);
const holdingSnapshotPack = compactHoldingSnapshotRows(holdingSnapshotCategoryRows.sort((a, b) => String(a.快照日期 || "").localeCompare(String(b.快照日期 || ""))));
writeHoldingSnapshotPack(holdingSnapshotPack);
const rebalanceFundCategoryPack = compactRebalanceFundCategoryRows(rebalanceFundCategoryRows.sort((a, b) => String(b.调仓日期 || "").localeCompare(String(a.调仓日期 || ""))));
writeRebalanceFundCategoryPack(rebalanceFundCategoryPack);
writeFundDetailPack(compactFundDetailPack(currentFundRows, currentHoldingStrategyRowsFinal, rebalanceFundMonthlyRowsFinal));
const aiSemanticIndex = buildAiSemanticIndex(summary, currentHoldingStrategyRowsFinal, currentFundRows);
writeAiSemanticIndex(aiSemanticIndex);
writeStandardEntityDictionary(buildStandardEntityDictionary(aiSemanticIndex));

summary.benchmarkDisclosure = buildBenchmarkDisclosureSummary(summary.strategies, benchmarkDisclosureById);

summary.insightData = {
  生成时间: new Date().toISOString(),
  指标说明: "基准风险资产权重为市场总览首层分类；风险等级和业务分类分别保留为风险控制与经营分析维度。",
  策略表现点: marketRows.map(strategyPoint),
  风险等级统计: groupedStats("风险等级"),
  业务分类统计: groupedStats("业务分类"),
  机会矩阵: opportunityRows(),
  业务分类经营诊断: businessDiagnosisRows(),
  当前持仓基金: currentFundRows.slice(0, 300),
  当前持仓基金公司: currentCompanyRows.slice(0, 100),
  当前持仓基金风险明细: finalizeWeightAgg([...riskFundAgg.values()]).sort((a, b) => b.总权重 - a.总权重),
  当前持仓基金公司风险明细: finalizeWeightAgg([...riskCompanyAgg.values()]).sort((a, b) => b.总权重 - a.总权重),
  当前持仓基金类型: finalizeWeightAgg([...assetAgg.values()]).sort((a, b) => b.总权重 - a.总权重),
  当前持仓策略基金明细: currentHoldingStrategyRowsFinal,
  持仓时间序列: finalizeTimelineRows(),
  持仓行业时间序列: finalizeIndustryTimelineRows(),
  持仓日期分类快照: {
    external: "data/holding_snapshot_pack.json",
    rows: holdingSnapshotPack.rows.length,
    fields: holdingSnapshotPack.fields,
    strategies: holdingSnapshotPack.dict.strategies.length,
    institutions: holdingSnapshotPack.dict.institutions.length,
    categories: holdingSnapshotPack.dict.categories.length
  },
  调仓事件: displayRebalanceEvents,
  调仓基金明细行数: rebalanceFundRows.length,
  调仓基金月度汇总口径: "按月、风险等级、业务分类、研报产品类型、市场地域、投顾机构、底层基金和研报大类资产聚合；保留每月净增减配绝对值前260个基金，以及全部广发基金调仓行。",
  调仓基金分类明细: {
    external: "data/rebalance_fund_category_pack.json",
    manifestScript: `./data/rebalance_fund_category_manifest.js?v=${reportAssetVersion}`,
    rows: rebalanceFundCategoryPack.rows.length,
    fields: rebalanceFundCategoryPack.fields,
    strategies: rebalanceFundCategoryPack.dict.strategies.length,
    categories: rebalanceFundCategoryPack.dict.categories.length,
    funds: rebalanceFundCategoryPack.dict.funds.length
  },
  调仓基金明细: rebalanceFundRows
    .filter((row) => Math.abs(nz(row.权重变化)) > 0.0001)
    .sort((a, b) => String(b.调仓日期 || "").localeCompare(String(a.调仓日期 || "")))
    .slice(0, 2000),
  调仓基金月度汇总: rebalanceFundMonthlySummaryRows,
  策略资产变化明细: strategyAssetChangeSummaryRows,
  大明细截断说明: {
    策略资产变化明细: `summary保留最近优先且有有效变化的前${strategyAssetChangeSummaryRows.length}行；完整分类拆分见 data/rebalance_fund_category_pack.json 和 data/holding_snapshot_pack.json。`,
    调仓基金月度汇总: `summary保留最近优先前${rebalanceFundMonthlySummaryRows.length}行；基金详情页与调仓分类pack保留更完整的调仓基金信息。`,
    策略资产变化明细原始行数: strategyAssetChangeRowsFinal.length,
    调仓基金月度汇总原始行数: rebalanceFundMonthlyRowsFinal.length
  },
  调仓方向汇总: finalizeDirectionRows(),
  机构调仓能力: institutionCapabilityRows(),
  广发基金调仓机会: finalizeGfFundOpportunityRows(currentFundRows),
  广发策略数: gfRows.length,
  非广发策略数: nonGfRows.length,
  持仓明细行数: holdingRows,
  详情文件数: detailCount
};

writeSummaryPacks(summary);

console.log(JSON.stringify({
  strategies: summary.strategies.length,
  displayStrategies: marketRows.length,
  incompleteStrategies: summary.strategies.filter((row) => row.数据完整性 !== "完整").length,
  d0OrHoldingMissingStrategies: summary.strategies.filter((row) => row.风险等级 === "D0 持仓缺失" || row.研报产品类型 === "持仓缺失/不入池").length,
  detailCount,
  orphanDetailCount,
  holdingRows,
  riskCounts: groupedStats("风险等级").map((row) => [row.类型, row.市场数量, row.广发数量]),
  insight: {
    points: summary.insightData.策略表现点.length,
    funds: summary.insightData.当前持仓基金.length,
    companies: summary.insightData.当前持仓基金公司.length,
    timeline: summary.insightData.持仓时间序列.length,
    industryTimeline: summary.insightData.持仓行业时间序列.length,
    holdingSnapshots: summary.insightData.持仓日期分类快照.rows,
    rebalance: summary.insightData.调仓事件.length
  }
}, null, 2));
