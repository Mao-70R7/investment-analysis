const fs = require("fs");
const path = require("path");
const vm = require("vm");

const root = path.resolve(__dirname, "..");
const sourcePath = path.join(root, "basic_data", "data", "basic_summary.js");
const outputPath = path.join(__dirname, "strategy_classification_framework_report.html");

const code = fs.readFileSync(sourcePath, "utf8");
const ctx = { window: { __BASIC_DATA__: {} } };
vm.createContext(ctx);
vm.runInContext(code, ctx);

const summary = ctx.window.__BASIC_DATA__.summary;
const strategies = summary.strategies || [];

function raw(value) {
  return value == null || value === "" ? "" : String(value);
}

function num(value) {
  if (value == null || value === "") return null;
  const n = Number(value);
  return Number.isFinite(n) ? n : null;
}

function nz(value) {
  return num(value) ?? 0;
}

function escapeHtml(value) {
  return raw(value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function fmt(value, digits = 1) {
  const n = num(value);
  return n == null ? "-" : n.toFixed(digits);
}

function pct(value, digits = 1) {
  const n = num(value);
  return n == null ? "-" : `${n.toFixed(digits)}%`;
}

function median(values) {
  const arr = values.filter((value) => value != null && Number.isFinite(value)).sort((a, b) => a - b);
  if (!arr.length) return null;
  const mid = Math.floor(arr.length / 2);
  return arr.length % 2 ? arr[mid] : (arr[mid - 1] + arr[mid]) / 2;
}

function groupBy(rows, keyFn) {
  const map = new Map();
  for (const row of rows) {
    const key = keyFn(row);
    if (!map.has(key)) map.set(key, []);
    map.get(key).push(row);
  }
  return [...map.entries()].map(([key, bucket]) => ({ key, rows: bucket, count: bucket.length }));
}

function topCounts(rows, keyFn, limit = 3) {
  return groupBy(rows, keyFn)
    .sort((a, b) => b.count - a.count || a.key.localeCompare(b.key, "zh"))
    .slice(0, limit)
    .map((item) => `${item.key}(${item.count})`)
    .join("、");
}

function isGuangfa(row) {
  return /广发基金|广发投顾/.test(`${raw(row["投顾机构"])} ${raw(row["渠道"])}`);
}

function hasHoldingData(row) {
  return ["权益基金权重", "债券基金权重", "货币基金权重", "混合基金权重", "指数基金权重"]
    .some((key) => nz(row[key]) > 0);
}

function isTargetProfit(row) {
  return raw(row["主可比池"]) === "目标盈系列产品" || /目标盈|小目标|止盈/.test(`${raw(row["策略名称"])} ${raw(row["特殊标签"])}`);
}

function productMechanism(row) {
  const pool = raw(row["主可比池"]);
  if (pool === "目标盈系列产品") return "目标盈/止盈机制";
  if (pool === "目标日期/养老型") return "养老/目标日期机制";
  if (pool === "现金管理型") return "现金/流动性机制";
  return "标准配置机制";
}

function riskBudget(row) {
  if (!hasHoldingData(row)) return "D0 持仓缺失";
  const eq = nz(row["权益基金权重"]);
  const vol = nz(row["波动率"]);
  const mdd = nz(row["最大回撤"]);
  if (eq <= 3 && vol <= 0.8 && mdd <= 1.2) return "R0 现金/超低波";
  if (eq <= 8 && vol <= 2.0 && mdd <= 3.0) return "R1 低波";
  if (eq <= 18 && vol <= 4.0 && mdd <= 6.0) return "R2 稳健收益";
  if (eq <= 35 && vol <= 7.5 && mdd <= 12.0) return "R3 均衡稳健";
  if (eq <= 55 && vol <= 11.0 && mdd <= 20.0) return "R4 均衡成长";
  return "R5 权益/进取";
}

function assetStructure(row) {
  if (!hasHoldingData(row)) return "持仓缺失/待核验";
  const buckets = [
    ["权益主导", nz(row["权益基金权重"])],
    ["债券主导", nz(row["债券基金权重"])],
    ["货币主导", nz(row["货币基金权重"])],
    ["混合基金主导", nz(row["混合基金权重"])],
  ].sort((a, b) => b[1] - a[1]);
  const top = buckets[0];
  const second = buckets[1];
  if (top[1] < 45 || top[1] - second[1] < 5) return "多资产分散/均衡";
  return top[0];
}

function marketScope(row) {
  const field = raw(row["市场地域"]);
  const qdii = nz(row["QDII权重"]);
  if (/海外|全球/.test(field)) return "海外/全球";
  if (/混合|跨境|国内\+海外/.test(field)) return "国内+海外";
  if (field === "国内") return "国内";
  if (qdii >= 30) return "海外/全球";
  if (qdii > 0) return "国内+海外";
  return "地域待识别";
}

function implementationEngine(row) {
  if (!hasHoldingData(row)) return "工具属性待核验";
  const active = nz(row["主动基金权重"]);
  const index = nz(row["指数基金权重"]);
  if (index >= 70) return "指数/被动工具主导";
  if (active >= 70 && index < 30) return "主动基金主导";
  if (active >= 30 && index >= 30) return "主动+指数混合";
  if (index >= 30) return "指数工具参与";
  if (active > 0) return "主动基金参与";
  return "工具属性待核验";
}

function themeExposure(row) {
  const text = `${raw(row["主可比池"])} ${raw(row["特殊标签"])} ${raw(row["策略名称"])} ${raw(row["策略概念"])} ${raw(row["策略描述"])}`;
  const hasTheme = /主题|行业|赛道|趋势|红利|医药|科技|新能源|消费|价值|成长/.test(text);
  const hasCommodity = /商品|黄金|贵金属/.test(text);
  if (hasTheme && hasCommodity) return "主题+商品暴露";
  if (hasCommodity) return "商品/黄金暴露";
  if (hasTheme) return "行业/主题暴露";
  return "非主题/宽基";
}

function issueForm(row) {
  const name = raw(row["策略名称"]);
  const hasIssue = /第?\s*[0-9０-９]{1,3}\s*期|[0-9０-９]{1,3}\s*期/.test(name);
  const hasContinuous = /天天|永续|月月|季季/.test(name);
  if (hasIssue && hasContinuous) return "期次发车-连续命名";
  if (hasIssue) return "期次发车";
  if (hasContinuous) return "连续/永续";
  return "常设开放";
}

function maintenanceIntensity(row) {
  const recent = num(row["最近一年调仓次数"]);
  const freq = num(row["调仓频率"]);
  const count = num(row["调仓次数"]);
  if (recent == null && freq == null && count == null) return "维护强度待核验";
  if ((recent ?? 0) >= 4 || (freq ?? 0) >= 4) return "高触达维护";
  if ((recent ?? 0) >= 1 || (freq ?? 0) >= 1) return "常规维护";
  if ((count ?? 0) > 0) return "低频维护";
  return "无调仓/持有型";
}

function dataComparability(row) {
  if (raw(row["数据完整性"]) !== "完整") return "数据链不完整";
  if (!hasHoldingData(row)) return "持仓缺失";
  if (num(row["年化收益"]) == null || num(row["波动率"]) == null || num(row["最大回撤"]) == null) return "绩效指标缺失";
  return "完整可比";
}

function normalizeSeriesName(name) {
  return raw(name)
    .replace(/[（(](年中版|新年版|测试)[）)]/g, "")
    .replace(/第?\s*[0-9０-９]{1,3}\s*期/g, "")
    .replace(/\s+/g, "")
    .replace(/投顾服务/g, "")
    .replace(/组合策略/g, "")
    .replace(/组合/g, "")
    .replace(/策略/g, "")
    .trim();
}

const axes = [
  {
    id: "mechanism",
    name: "产品机制/客户承诺",
    principle: "用客户实际购买到的产品承诺划分，不把目标盈、养老、现金服务和普通配置强行混排。",
    classifier: productMechanism,
    rules: {
      "目标盈/止盈机制": "主可比池为目标盈系列产品，或具备明确目标盈/止盈产品机制。",
      "养老/目标日期机制": "主可比池为目标日期/养老型。",
      "现金/流动性机制": "主可比池为现金管理型。",
      "标准配置机制": "不具备上述事件、生命周期或流动性承诺的开放式配置策略。",
    },
  },
  {
    id: "risk",
    name: "风险预算",
    principle: "用权益权重、波动率、最大回撤共同切分，形成可比较的风险带。",
    classifier: riskBudget,
    rules: {
      "D0 持仓缺失": "当前资产权重不可用，先不进入正式比较池。",
      "R0 现金/超低波": "权益<=3%、波动<=0.8%、最大回撤<=1.2%。",
      "R1 低波": "权益<=8%、波动<=2.0%、最大回撤<=3.0%。",
      "R2 稳健收益": "权益<=18%、波动<=4.0%、最大回撤<=6.0%。",
      "R3 均衡稳健": "权益<=35%、波动<=7.5%、最大回撤<=12.0%。",
      "R4 均衡成长": "权益<=55%、波动<=11.0%、最大回撤<=20.0%。",
      "R5 权益/进取": "超出上述风险预算，主要承担权益或高波动风险。",
    },
  },
  {
    id: "asset",
    name: "当前资产结构",
    principle: "用当前持仓中最大的资产桶划分；最大桶不明显时归为多资产分散。",
    classifier: assetStructure,
    rules: {
      "权益主导": "权益基金权重为最大资产桶且优势明显。",
      "债券主导": "债券基金权重为最大资产桶且优势明显。",
      "货币主导": "货币基金权重为最大资产桶且优势明显。",
      "混合基金主导": "混合基金权重为最大资产桶且优势明显。",
      "多资产分散/均衡": "最大资产桶低于45%，或前两大资产桶差距不足5pct。",
      "持仓缺失/待核验": "持仓资产权重全为空或为0。",
    },
  },
  {
    id: "market",
    name: "市场地域",
    principle: "区分人民币国内配置、国内+海外混合、海外/全球配置。",
    classifier: marketScope,
    rules: {
      "国内": "市场地域字段为国内，且 QDII 权重不形成跨境暴露。",
      "国内+海外": "存在少量 QDII 或跨境/混合地域暴露。",
      "海外/全球": "市场地域为海外/全球，或 QDII 权重达到较高水平。",
      "地域待识别": "地域字段与 QDII 暴露均不可识别。",
    },
  },
  {
    id: "engine",
    name: "实现引擎",
    principle: "区分主动选基、指数工具、主动+指数混合，便于归因和产品能力分析。",
    classifier: implementationEngine,
    rules: {
      "主动基金主导": "主动基金权重>=70%，且指数权重<30%。",
      "指数/被动工具主导": "指数基金权重>=70%。",
      "主动+指数混合": "主动基金和指数基金均>=30%。",
      "指数工具参与": "指数基金权重>=30%，但未达到主导。",
      "主动基金参与": "主动基金权重>0，但未达到主导。",
      "工具属性待核验": "持仓或主动/指数工具属性不足。",
    },
  },
  {
    id: "theme",
    name: "主题暴露形态",
    principle: "主题、商品、宽基单独成轴，避免把主题策略和普通偏股/目标盈混成一个最终池。",
    classifier: themeExposure,
    rules: {
      "非主题/宽基": "未识别出行业、主题、商品、黄金等集中暴露。",
      "行业/主题暴露": "名称、标签、主池或描述出现行业/主题/赛道/趋势等集中暴露。",
      "商品/黄金暴露": "出现商品、黄金、贵金属等暴露。",
      "主题+商品暴露": "同时具备主题与商品/黄金暴露。",
    },
  },
  {
    id: "issue",
    name: "发行/展示形态",
    principle: "区分期次发车、连续命名、常设开放，服务销售节奏和存续分析。",
    classifier: issueForm,
    rules: {
      "期次发车-连续命名": "名称同时包含期次和天天/月月/永续等连续词。",
      "期次发车": "名称包含明确期次。",
      "连续/永续": "名称包含天天、月月、季季、永续等连续运作词，但无期次。",
      "常设开放": "未识别期次或连续命名。",
    },
  },
  {
    id: "maintenance",
    name: "调仓维护强度",
    principle: "用最近一年调仓次数和年化调仓频率定义服务触达强度。",
    classifier: maintenanceIntensity,
    rules: {
      "高触达维护": "最近一年调仓>=4次，或调仓频率>=4次/年。",
      "常规维护": "最近一年调仓>=1次，或调仓频率>=1次/年。",
      "低频维护": "历史有调仓，但近期或年化频率较低。",
      "无调仓/持有型": "调仓次数为0。",
      "维护强度待核验": "调仓次数与频率字段不可用。",
    },
  },
  {
    id: "quality",
    name: "数据可比性",
    principle: "将能否进入正式比较池单独成轴，避免数据缺口污染分类判断。",
    classifier: dataComparability,
    rules: {
      "完整可比": "数据完整、持仓可用、年化收益/波动/回撤可用。",
      "数据链不完整": "数据完整性字段不为完整。",
      "持仓缺失": "历史/绩效可能存在，但当前资产权重不可用。",
      "绩效指标缺失": "持仓可用，但年化收益、波动或最大回撤缺失。",
    },
  },
];

function summarize(rows, key, total = strategies.length) {
  const gfRows = rows.filter(isGuangfa);
  return {
    key,
    count: rows.length,
    share: rows.length / total * 100,
    gfCount: gfRows.length,
    gfShare: rows.length ? gfRows.length / rows.length * 100 : null,
    institutions: new Set(rows.map((row) => raw(row["投顾机构"])).filter(Boolean)).size,
    eqMedian: median(rows.map((row) => num(row["权益基金权重"]))),
    annMedian: median(rows.map((row) => num(row["年化收益"]))),
    volMedian: median(rows.map((row) => num(row["波动率"]))),
    mddMedian: median(rows.map((row) => num(row["最大回撤"]))),
    topInstitutions: topCounts(rows, (row) => raw(row["投顾机构"]) || "未披露", 4),
    gfExamples: gfRows.slice(0, 5).map((row) => raw(row["策略名称"])).join("、"),
  };
}

function axisSummaries(axis) {
  return groupBy(strategies, axis.classifier)
    .map((item) => summarize(item.rows, item.key))
    .sort((a, b) => b.count - a.count || a.key.localeCompare(b.key, "zh"));
}

function table(headers, rows, cls = "") {
  return `<table class="${cls}"><thead><tr>${headers.map((h) => `<th>${escapeHtml(h)}</th>`).join("")}</tr></thead><tbody>${rows.map((row) => `<tr>${row.map((cell) => `<td>${cell}</td>`).join("")}</tr>`).join("")}</tbody></table>`;
}

function bar(value, max) {
  const width = max > 0 ? Math.max(2, Math.min(100, (value / max) * 100)) : 0;
  return `<div class="bar"><span style="width:${width.toFixed(1)}%"></span></div>`;
}

function axisSection(axis) {
  const rows = axisSummaries(axis);
  const max = Math.max(...rows.map((row) => row.count));
  const body = rows.map((row) => [
    `<strong>${escapeHtml(row.key)}</strong>${bar(row.count, max)}<div class="muted">${escapeHtml(axis.rules[row.key] || "")}</div>`,
    `${row.count}<br><span class="muted">${pct(row.share)}</span>`,
    `${row.gfCount}<br><span class="muted">${pct(row.gfShare)}</span>`,
    `${row.institutions}<br><span class="muted">${escapeHtml(row.topInstitutions)}</span>`,
    `${fmt(row.eqMedian)} / ${pct(row.annMedian)} / ${pct(row.volMedian)} / ${pct(row.mddMedian)}`,
    escapeHtml(row.gfExamples || "无"),
  ]);
  return `<section>
    <h2>${escapeHtml(axis.name)}</h2>
    <p>${escapeHtml(axis.principle)}</p>
    ${table(["分类值", "市场数/占比", "广发数/占比", "机构覆盖", "权益/年化/波动/回撤中位数", "广发样例"], body, "wide")}
  </section>`;
}

function matrix(rowAxis, colAxis, rows) {
  const rowValues = groupBy(rows, rowAxis.classifier).sort((a, b) => b.count - a.count || a.key.localeCompare(b.key, "zh")).map((item) => item.key);
  const colValues = groupBy(rows, colAxis.classifier).sort((a, b) => b.count - a.count || a.key.localeCompare(b.key, "zh")).map((item) => item.key);
  const body = rowValues.map((rv) => {
    const line = [`<strong>${escapeHtml(rv)}</strong>`];
    for (const cv of colValues) {
      const bucket = rows.filter((row) => rowAxis.classifier(row) === rv && colAxis.classifier(row) === cv);
      const gf = bucket.filter(isGuangfa).length;
      line.push(bucket.length ? `${bucket.length}<span class="muted"> / 广发${gf}</span>` : `<span class="muted">-</span>`);
    }
    return line;
  });
  return table([rowAxis.name, ...colValues], body, "matrix");
}

function derivedSegment(name, rule, useCase, filterFn, overlap = "可重叠") {
  const rows = strategies.filter(filterFn);
  const gfRows = rows.filter(isGuangfa);
  return {
    name,
    rule,
    useCase,
    overlap,
    count: rows.length,
    share: rows.length / strategies.length * 100,
    gfCount: gfRows.length,
    gfShare: rows.length ? gfRows.length / rows.length * 100 : null,
    topInstitutions: topCounts(rows, (row) => raw(row["投顾机构"]) || "未披露", 3),
  };
}

const derivedSegments = [
  derivedSegment("活钱管理货架", "产品机制=现金/流动性 或 风险预算=R0", "销售：闲钱、备用金、现金替代", (row) => productMechanism(row) === "现金/流动性机制" || riskBudget(row) === "R0 现金/超低波"),
  derivedSegment("低波防守货架", "风险预算=R0/R1 且资产结构非权益主导", "销售/市场：低风险客户承接", (row) => ["R0 现金/超低波", "R1 低波"].includes(riskBudget(row)) && assetStructure(row) !== "权益主导"),
  derivedSegment("稳健增值货架", "风险预算=R2，且数据完整可比", "销售/投研：固收+和稳健目标收益", (row) => riskBudget(row) === "R2 稳健收益" && dataComparability(row) === "完整可比"),
  derivedSegment("目标达标运营池", "产品机制=目标盈/止盈机制", "营销：发车、达标、续作、复购", (row) => productMechanism(row) === "目标盈/止盈机制"),
  derivedSegment("家庭配置池", "风险预算=R3/R4 或 资产结构=多资产分散/均衡", "销售/产品：家庭资产配置、长期持有", (row) => ["R3 均衡稳健", "R4 均衡成长"].includes(riskBudget(row)) || assetStructure(row) === "多资产分散/均衡"),
  derivedSegment("权益核心池", "风险预算=R5 且主题暴露=非主题/宽基", "投研：核心权益能力与长期 alpha", (row) => riskBudget(row) === "R5 权益/进取" && themeExposure(row) === "非主题/宽基"),
  derivedSegment("主题营销池", "主题暴露不等于非主题/宽基", "营销/市场：主题活动、行业赛道、热点承接", (row) => themeExposure(row) !== "非主题/宽基"),
  derivedSegment("全球配置池", "市场地域=国内+海外 或 海外/全球", "销售/产品：全球分散和 QDII 工具链", (row) => ["国内+海外", "海外/全球"].includes(marketScope(row))),
  derivedSegment("养老长期池", "产品机制=养老/目标日期机制", "产品/销售：养老长期和生命周期规划", (row) => productMechanism(row) === "养老/目标日期机制"),
];

const comparableTuples = groupBy(
  strategies.filter((row) => dataComparability(row) === "完整可比"),
  (row) => [
    productMechanism(row),
    riskBudget(row),
    marketScope(row),
    implementationEngine(row),
  ].join(" × "),
)
  .map((item) => summarize(item.rows, item.key))
  .sort((a, b) => b.count - a.count || a.key.localeCompare(b.key, "zh"))
  .slice(0, 20);

const tupleTable = table(
  ["投研比较池坐标", "市场数/占比", "广发数/占比", "机构覆盖", "权益/年化/波动/回撤中位数", "广发样例"],
  comparableTuples.map((row) => [
    escapeHtml(row.key),
    `${row.count}<br><span class="muted">${pct(row.share)}</span>`,
    `${row.gfCount}<br><span class="muted">${pct(row.gfShare)}</span>`,
    `${row.institutions}<br><span class="muted">${escapeHtml(row.topInstitutions)}</span>`,
    `${fmt(row.eqMedian)} / ${pct(row.annMedian)} / ${pct(row.volMedian)} / ${pct(row.mddMedian)}`,
    escapeHtml(row.gfExamples || "无"),
  ]),
  "wide",
);

const derivedTable = table(
  ["衍生分类/视图", "构造规则", "用途", "是否可重叠", "市场数/占比", "广发数/占比", "头部机构"],
  derivedSegments.map((row) => [
    `<strong>${escapeHtml(row.name)}</strong>`,
    escapeHtml(row.rule),
    escapeHtml(row.useCase),
    escapeHtml(row.overlap),
    `${row.count}<br><span class="muted">${pct(row.share)}</span>`,
    `${row.gfCount}<br><span class="muted">${pct(row.gfShare)}</span>`,
    escapeHtml(row.topInstitutions),
  ]),
  "wide",
);

const targetRows = strategies.filter(isTargetProfit);
const targetIssueRows = targetRows.filter((row) => /期次发车/.test(issueForm(row)));
const targetSeriesCount = new Set(targetRows.map((row) => `${raw(row["投顾机构"])} / ${normalizeSeriesName(row["策略名称"])}`)).size;
const gfRows = strategies.filter(isGuangfa);
const gfShare = gfRows.length / strategies.length * 100;
const dataDate = summary.overview?.["数据更新至"] || "";
const generatedAt = new Date().toLocaleString("zh-CN", { hour12: false });

const html = `<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>投顾策略分类体系框架</title>
  <style>
    :root {
      --ink: #16202a;
      --muted: #607080;
      --line: #d8e1e8;
      --soft: #f4f7fa;
      --panel: #fff;
      --accent: #166b76;
      --accent2: #8b5a13;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      color: var(--ink);
      background: #edf2f6;
      font: 14px/1.55 -apple-system, BlinkMacSystemFont, "Segoe UI", "Microsoft YaHei", sans-serif;
    }
    header {
      background: #0b3540;
      color: white;
      padding: 30px 36px;
    }
    header h1 { margin: 0 0 8px; font-size: 28px; letter-spacing: 0; }
    header p { margin: 4px 0; color: #d9e9ed; max-width: 1180px; }
    main { max-width: 1360px; margin: 0 auto; padding: 24px 28px 44px; }
    section {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 22px;
      margin-bottom: 18px;
      box-shadow: 0 1px 2px rgba(18, 28, 40, .04);
    }
    h2 { margin: 0 0 10px; font-size: 20px; }
    h3 { margin: 0 0 8px; font-size: 16px; }
    p { margin: 8px 0; }
    .callout {
      border-left: 4px solid var(--accent);
      background: #eef8fb;
      border-radius: 0 8px 8px 0;
      padding: 12px 14px;
      margin: 12px 0;
    }
    .stats {
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 12px;
      margin-top: 14px;
    }
    .stat {
      background: var(--soft);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 14px;
      min-height: 92px;
    }
    .stat span { display: block; color: var(--muted); font-size: 13px; }
    .stat strong { display: block; font-size: 24px; margin: 4px 0; }
    table {
      width: 100%;
      border-collapse: collapse;
      margin-top: 10px;
    }
    th, td {
      border-bottom: 1px solid var(--line);
      padding: 8px 9px;
      text-align: left;
      vertical-align: top;
    }
    th {
      background: #edf3f7;
      color: #263746;
      font-weight: 700;
      white-space: nowrap;
    }
    .wide td:nth-child(n+2), .wide th:nth-child(n+2), .matrix td:nth-child(n+2), .matrix th:nth-child(n+2) {
      white-space: nowrap;
    }
    .wide td:first-child, .wide td:last-child, .matrix td:first-child {
      white-space: normal;
    }
    .muted {
      color: var(--muted);
      font-size: 12px;
    }
    .bar {
      height: 7px;
      background: #e2e9ef;
      border-radius: 999px;
      margin-top: 6px;
      overflow: hidden;
    }
    .bar span {
      display: block;
      height: 100%;
      background: var(--accent);
    }
    ul { margin: 8px 0 0 20px; padding: 0; }
    li { margin: 5px 0; }
    code {
      background: rgba(255,255,255,.16);
      border: 1px solid rgba(255,255,255,.25);
      border-radius: 4px;
      padding: 1px 4px;
    }
    footer {
      color: var(--muted);
      text-align: center;
      padding-top: 8px;
      font-size: 12px;
    }
    @media (max-width: 980px) {
      header { padding: 24px 18px; }
      main { padding: 18px 12px 34px; }
      .stats { grid-template-columns: 1fr; }
      section { padding: 16px; }
      table { display: block; overflow-x: auto; }
      th, td { min-width: 112px; }
    }
  </style>
</head>
<body>
  <header>
    <h1>投顾策略分类体系框架</h1>
    <p>基于 <code>basic_data/data/basic_summary.js</code> 当前可分析的 ${strategies.length} 条展示策略。数据更新至 ${escapeHtml(dataDate)}；报告生成 ${escapeHtml(generatedAt)}。</p>
    <p>设计目标：基础分类轴内部互斥且穷尽；不再用 A&gt;B&gt;C 优先级压成唯一最终分类；衍生分类只作为营销、投研、市场分析视图。</p>
  </header>
  <main>
    <section>
      <h2>设计原则</h2>
      <div class="callout">
        <p><strong>基础分类是坐标系，不是单一标签。</strong> 每条策略都会在多个基础分类轴上各取一个值，例如“目标盈/止盈机制 × R2 稳健收益 × 债券主导 × 国内 × 主动基金主导 × 非主题/宽基 × 期次发车”。</p>
        <p><strong>衍生分类是业务视图。</strong> 营销货架、投研比较池、市场布局矩阵都从基础轴组合而来，可以局部、可重叠、可按问题调整，但不能反过来污染基础分类。</p>
      </div>
      <div class="stats">
        <div class="stat"><span>展示策略</span><strong>${strategies.length}</strong><span>基础分类全量覆盖</span></div>
        <div class="stat"><span>广发策略</span><strong>${gfRows.length}</strong><span>全市场占比 ${pct(gfShare)}</span></div>
        <div class="stat"><span>基础分类轴</span><strong>${axes.length}</strong><span>每轴内部互斥穷尽</span></div>
        <div class="stat"><span>目标盈记录</span><strong>${targetRows.length}</strong><span>${targetIssueRows.length} 条期次型，约 ${targetSeriesCount} 个系列</span></div>
      </div>
    </section>

    ${axes.map(axisSection).join("\n")}

    <section>
      <h2>基础轴交叉：产品机制 × 风险预算</h2>
      <p>单轴解释分类，交叉矩阵解释市场结构。单元格格式为“全市场数 / 广发数”。</p>
      ${matrix(axes[0], axes[1], strategies)}
    </section>

    <section>
      <h2>基础轴交叉：市场地域 × 风险预算</h2>
      ${matrix(axes[3], axes[1], strategies)}
    </section>

    <section>
      <h2>基础轴交叉：实现引擎 × 风险预算</h2>
      ${matrix(axes[4], axes[1], strategies)}
    </section>

    <section>
      <h2>投研比较池示例</h2>
      <p>投研比较池不使用“最终分类”，而是用基础坐标组合：产品机制 × 风险预算 × 市场地域 × 实现引擎，并且要求数据可比。下表展示样本最多的前 20 个坐标。</p>
      ${tupleTable}
    </section>

    <section>
      <h2>衍生分类视图</h2>
      <p>以下分类服务具体业务问题，可以重叠。例如一个策略可以同时进入“目标达标运营池”和“全球配置池”。这些视图不用于唯一归属。</p>
      ${derivedTable}
    </section>

    <section>
      <h2>后续落地方式</h2>
      <ul>
        <li>策略主表增加 9 个基础分类字段，形成稳定坐标系。</li>
        <li>投研评价用“基础坐标 + 数据可比性”生成比较池，样本不足时只展示不排名。</li>
        <li>销售和营销可以按活动目标定义衍生视图，例如目标达标、低波防守、全球配置、主题营销。</li>
        <li>广发产品布局分析优先看交叉矩阵：在哪些基础坐标中市场有规模但广发覆盖弱，再进入策略和底层基金层面。</li>
      </ul>
    </section>

    <footer>生成脚本：analysis_outputs/generate_strategy_classification_framework_report.js。本报告不改写原始数据。</footer>
  </main>
</body>
</html>`;

fs.writeFileSync(outputPath, html, "utf8");
console.log(outputPath);
