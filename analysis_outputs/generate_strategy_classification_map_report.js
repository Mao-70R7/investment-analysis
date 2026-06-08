const fs = require("fs");
const path = require("path");
const vm = require("vm");

const root = path.resolve(__dirname, "..");
const sourcePath = path.join(root, "basic_data", "data", "basic_summary.js");
const outputPath = path.join(__dirname, "strategy_classification_map_report.html");

const code = fs.readFileSync(sourcePath, "utf8");
const ctx = { window: { __BASIC_DATA__: {} } };
vm.createContext(ctx);
vm.runInContext(code, ctx);

const summary = ctx.window.__BASIC_DATA__.summary;
const strategies = summary.strategies || [];

const primaryOrder = [
  "现金管理/活钱",
  "纯债/短债",
  "固收+稳健增强",
  "目标盈/止盈/小目标",
  "多资产均衡配置",
  "偏股核心配置",
  "主题/行业机会",
  "海外/全球配置",
  "养老/目标日期",
  "未分类",
];

const primaryMeta = {
  "现金管理/活钱": {
    sales: "活钱管理、闲钱暂存、低波备用金",
    research: "收益稳定性、回撤、费率、流动性体验",
    product: "现金管理或货币增强底座",
  },
  "纯债/短债": {
    sales: "低波防守、短债替代、稳健客户承接",
    research: "久期风险、信用风险、低回撤能力",
    product: "短债/纯债货架覆盖",
  },
  "固收+稳健增强": {
    sales: "稳健增值、固收+主货架",
    research: "权益仓位效率、回撤控制、波动调整收益",
    product: "低权益、标准固收+、弹性固收+分层",
  },
  "目标盈/止盈/小目标": {
    sales: "达标体验、期次发车、活动化营销",
    research: "达成率、达成时间、持有期回撤、续作质量",
    product: "低波目标、增强目标、多元目标、进取目标",
  },
  "多资产均衡配置": {
    sales: "家庭资产配置、长期持有、风险分散",
    research: "资产配置能力、再平衡效率、组合稳定性",
    product: "稳健多资产、均衡多资产、成长多资产",
  },
  "偏股核心配置": {
    sales: "权益核心、长期增值、定投承接",
    research: "权益 beta、主动 alpha、风格稳定性",
    product: "主动权益、指数工具、主动+指数组合",
  },
  "主题/行业机会": {
    sales: "主题热点、行业赛道、阶段性营销",
    research: "主题有效性、择时纪律、回撤承受",
    product: "行业基金、ETF、主题工具链",
  },
  "海外/全球配置": {
    sales: "全球分散、海外资产配置、汇率分散",
    research: "海外 beta、汇率、QDII 工具效率",
    product: "全球多资产、海外权益、跨境工具",
  },
  "养老/目标日期": {
    sales: "养老长期、生命周期、退休规划",
    research: "下滑曲线、目标风险、长期回撤管理",
    product: "目标日期、目标风险、养老 FOF",
  },
  "未分类": {
    sales: "待归类",
    research: "需补标签",
    product: "待核验",
  },
};

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

function esc(value) {
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

function countBy(rows, keyFn) {
  const map = new Map();
  for (const row of rows) {
    const key = keyFn(row);
    if (!map.has(key)) map.set(key, []);
    map.get(key).push(row);
  }
  return [...map.entries()].map(([key, bucket]) => ({ key, rows: bucket, count: bucket.length }));
}

function topCounts(rows, keyFn, limit = 3) {
  return countBy(rows, keyFn)
    .sort((a, b) => b.count - a.count || a.key.localeCompare(b.key, "zh"))
    .slice(0, limit)
    .map((item) => `${item.key}(${item.count})`)
    .join("、");
}

function isGuangfa(row) {
  return /广发基金|广发投顾/.test(`${raw(row["投顾机构"])} ${raw(row["渠道"])}`);
}

function hasHoldingData(row) {
  return ["权益基金权重", "债券基金权重", "货币基金权重", "混合基金权重", "QDII权重", "指数基金权重"]
    .some((key) => nz(row[key]) > 0);
}

function primaryCategory(row) {
  const pool = raw(row["主可比池"]);
  const map = {
    "现金管理型": "现金管理/活钱",
    "纯债/短债型": "纯债/短债",
    "固收增强型": "固收+稳健增强",
    "目标盈系列产品": "目标盈/止盈/小目标",
    "多资产配置型": "多资产均衡配置",
    "偏股配置型": "偏股核心配置",
    "主题/行业型": "主题/行业机会",
    "海外/全球型": "海外/全球配置",
    "目标日期/养老型": "养老/目标日期",
  };
  return map[pool] || "未分类";
}

function riskBand(row) {
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

function targetRisk(row) {
  if (!hasHoldingData(row)) return "目标盈-持仓缺失待核验";
  const eq = nz(row["权益基金权重"]);
  const vol = nz(row["波动率"]);
  const mdd = nz(row["最大回撤"]);
  if (eq <= 8 && vol <= 1.8 && mdd <= 2.2) return "目标盈-低波型";
  if (eq <= 15 && vol <= 3.0 && mdd <= 3.8) return "目标盈-稳健型";
  if (eq <= 25 && vol <= 5.0 && mdd <= 7.0) return "目标盈-增强型";
  if (eq <= 50 && vol <= 10.0 && mdd <= 15.0) return "目标盈-平衡/多元型";
  return "目标盈-进取/权益型";
}

function targetTheme(row) {
  const joined = `${raw(row["策略名称"])} ${raw(row["特殊标签"])} ${raw(row["策略概念"])} ${raw(row["策略描述"])} ${raw(row["分类依据"])}`;
  if (/海外|全球|QDII/.test(joined) || nz(row["QDII权重"]) >= 10) return "目标盈-跨境/全球型";
  if (/黄金|商品/.test(joined)) return "目标盈-黄金/商品型";
  if (/主题|趋势|行业|赛道|红利|指数|定投/.test(joined) && nz(row["权益基金权重"]) >= 20) return "目标盈-主题/工具型";
  return "";
}

function secondaryCategory(row) {
  const primary = primaryCategory(row);
  const eq = nz(row["权益基金权重"]);
  const debt = nz(row["债券基金权重"]);
  const cash = nz(row["货币基金权重"]);
  const qdii = nz(row["QDII权重"]);
  const index = nz(row["指数基金权重"]);
  const name = raw(row["策略名称"]);
  const tags = `${raw(row["特殊标签"])} ${raw(row["策略实现标签"])} ${name}`;

  if (primary === "目标盈/止盈/小目标") {
    const themed = targetTheme(row);
    if (themed) return themed;
    return targetRisk(row);
  }
  if (!hasHoldingData(row)) return `${primary}-持仓缺失待核验`;
  if (primary === "现金管理/活钱") {
    if (cash >= 80) return "现金-货币主导";
    if (debt >= 40) return "现金-货债增强";
    return "现金-低波配置";
  }
  if (primary === "纯债/短债") {
    if (cash >= 30) return "短债-货债流动型";
    if (eq > 3) return "纯债-含少量权益待核验";
    if (nz(row["波动率"]) <= 1.2) return "纯债-低波短债";
    return "纯债-标准纯债";
  }
  if (primary === "固收+稳健增强") {
    if (eq <= 8) return "固收+-低权益";
    if (eq <= 18) return "固收+-标准型";
    return "固收+-弹性增强";
  }
  if (primary === "多资产均衡配置") {
    if (qdii >= 10) return "多资产-全球分散";
    if (eq <= 25) return "多资产-稳健配置";
    if (eq <= 45) return "多资产-均衡配置";
    return "多资产-成长配置";
  }
  if (primary === "偏股核心配置") {
    if (index >= 50 || /指数|ETF|被动/.test(tags)) return "偏股-指数/工具";
    if (eq >= 80) return "偏股-高权益主动";
    return "偏股-核心主动";
  }
  if (primary === "主题/行业机会") {
    if (/黄金|商品/.test(tags)) return "主题-黄金/商品";
    if (/指数|ETF|被动/.test(tags) || index >= 50) return "主题-指数工具";
    if (qdii >= 10 || /海外|全球/.test(tags)) return "主题-跨境主题";
    return "主题-行业/赛道主动";
  }
  if (primary === "海外/全球配置") {
    if (eq >= 60) return "海外-权益进取";
    if (qdii >= 30) return "海外-全球多资产";
    return "海外-跨境混合";
  }
  if (primary === "养老/目标日期") {
    const year = Number((name.match(/20[3-6][0-9]/) || [])[0]);
    if (year && year <= 2035) return "养老-近端目标日期";
    if (year && year <= 2045) return "养老-中端目标日期";
    if (year) return "养老-远端目标日期";
    return "养老-目标风险/稳健养老";
  }
  return "未分类";
}

function operationType(row) {
  const name = raw(row["策略名称"]);
  if (/第?\s*[0-9０-９]{1,3}\s*期|[0-9０-９]{1,3}\s*期/.test(name)) return "期次发车";
  if (/永续|天天/.test(name)) return "连续运作";
  if (nz(row["最近一年调仓次数"]) >= 4 || nz(row["调仓频率"]) >= 6) return "高触达维护";
  return "常设货架";
}

function implementation(row) {
  const tag = raw(row["策略实现标签"]);
  if (/QDII|海外/.test(tag) || nz(row["QDII权重"]) >= 10) return "QDII/海外工具";
  if (/指数/.test(tag) || nz(row["指数基金权重"]) >= 50) return "指数/被动工具";
  if (nz(row["指数基金权重"]) >= 20 && nz(row["主动基金权重"]) >= 20) return "主动+指数混合";
  if (/FOF|养老/.test(tag)) return "FOF/养老工具";
  return "主动基金组合";
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

function summaryRow(rows, key, totalCount = strategies.length) {
  const gfRows = rows.filter(isGuangfa);
  return {
    key,
    rows,
    count: rows.length,
    marketShare: rows.length / totalCount * 100,
    gfCount: gfRows.length,
    gfShare: rows.length ? gfRows.length / rows.length * 100 : null,
    completeShare: rows.length ? rows.filter((row) => raw(row["数据完整性"]) === "完整").length / rows.length * 100 : null,
    institutionCount: new Set(rows.map((row) => raw(row["投顾机构"])).filter(Boolean)).size,
    eqMedian: median(rows.map((row) => num(row["权益基金权重"]))),
    annMedian: median(rows.map((row) => num(row["年化收益"]))),
    volMedian: median(rows.map((row) => num(row["波动率"]))),
    mddMedian: median(rows.map((row) => num(row["最大回撤"]))),
    topInstitutions: topCounts(rows, (row) => raw(row["投顾机构"]) || "未披露", 4),
    gfExamples: gfRows.slice(0, 5).map((row) => raw(row["策略名称"])).join("、"),
    topRisk: topCounts(rows, riskBand, 3),
    topImplementation: topCounts(rows, implementation, 3),
  };
}

function summarizeBy(rows, keyFn, totalCount = strategies.length) {
  return countBy(rows, keyFn).map((item) => summaryRow(item.rows, item.key, totalCount));
}

function sortPrimary(rows) {
  return rows.sort((a, b) => {
    const ai = primaryOrder.indexOf(a.key);
    const bi = primaryOrder.indexOf(b.key);
    return (ai === -1 ? 999 : ai) - (bi === -1 ? 999 : bi);
  });
}

function sortSecondary(rows) {
  return rows.sort((a, b) => {
    const ap = primaryCategory(a.rows[0] || {});
    const bp = primaryCategory(b.rows[0] || {});
    const ai = primaryOrder.indexOf(ap);
    const bi = primaryOrder.indexOf(bp);
    return (ai === -1 ? 999 : ai) - (bi === -1 ? 999 : bi) || b.count - a.count || a.key.localeCompare(b.key, "zh");
  });
}

function table(headers, rows, cls = "") {
  return `<table class="${cls}"><thead><tr>${headers.map((header) => `<th>${esc(header)}</th>`).join("")}</tr></thead><tbody>${rows.map((row) => `<tr>${row.map((cell) => `<td>${cell}</td>`).join("")}</tr>`).join("")}</tbody></table>`;
}

function bar(value, max) {
  const width = max > 0 ? Math.max(2, Math.min(100, value / max * 100)) : 0;
  return `<div class="bar"><span style="width:${width.toFixed(1)}%"></span></div>`;
}

function pill(value, type = "") {
  return `<span class="pill ${type}">${esc(value)}</span>`;
}

function marketTone(row) {
  if (row.count >= 180) return "大池";
  if (row.count >= 80) return "中大池";
  if (row.count >= 35) return "中小池";
  return "小池";
}

function gfTone(row, gfOverallShare) {
  if (!row.gfCount) return "空白";
  if (row.gfShare >= gfOverallShare * 1.4) return "相对高配";
  if (row.gfShare >= gfOverallShare * 0.7) return "基本覆盖";
  return "相对低配";
}

const displayCount = summary.strategyListStats?.["展示策略数"] || strategies.length;
const overviewTotal = summary.overview?.["策略总数"];
const hiddenChannels = summary.strategyListStats?.["隐藏渠道数"] || 0;
const hiddenChannelIds = summary.strategyListStats?.["隐藏渠道ID"] || [];
const dataDate = summary.overview?.["数据更新至"] || "";

const gfRows = strategies.filter(isGuangfa);
const gfOverallShare = gfRows.length / strategies.length * 100;
const primarySummary = sortPrimary(summarizeBy(strategies, primaryCategory));
const secondarySummary = sortSecondary(summarizeBy(strategies, secondaryCategory));
const gfBySecondary = sortSecondary(summarizeBy(gfRows, secondaryCategory, gfRows.length));
const targetRows = strategies.filter((row) => primaryCategory(row) === "目标盈/止盈/小目标");
const targetIssueRows = targetRows.filter((row) => operationType(row) === "期次发车");
const targetSeriesCount = new Set(targetRows.map((row) => `${raw(row["投顾机构"])} / ${normalizeSeriesName(row["策略名称"])}`)).size;
const targetNoHolding = targetRows.filter((row) => !hasHoldingData(row)).length;

const maxPrimaryCount = Math.max(...primarySummary.map((row) => row.count));
const maxSecondaryCount = Math.max(...secondarySummary.map((row) => row.count));

const primaryRows = primarySummary.map((row) => {
  const meta = primaryMeta[row.key] || {};
  return [
    `<strong>${esc(row.key)}</strong>${bar(row.count, maxPrimaryCount)}<div class="muted">${esc(meta.sales || "")}</div>`,
    `${row.count}<br><span class="muted">${pct(row.marketShare)} / ${marketTone(row)}</span>`,
    `${row.institutionCount}<br><span class="muted">${esc(row.topInstitutions)}</span>`,
    `${row.gfCount}<br><span class="muted">${pct(row.gfShare)} / ${gfTone(row, gfOverallShare)}</span>`,
    esc(row.gfExamples || "无"),
    `${fmt(row.eqMedian)} / ${pct(row.annMedian)} / ${pct(row.volMedian)} / ${pct(row.mddMedian)}`,
    `${esc(row.topRisk)}<br><span class="muted">${esc(row.topImplementation)}</span>`,
    esc(meta.product || ""),
  ];
});

const secondaryRows = secondarySummary.map((row) => {
  const primary = primaryCategory(row.rows[0] || {});
  return [
    esc(primary),
    `<strong>${esc(row.key)}</strong>${bar(row.count, maxSecondaryCount)}`,
    `${row.count}<br><span class="muted">${pct(row.marketShare)}</span>`,
    `${row.institutionCount}<br><span class="muted">${esc(row.topInstitutions)}</span>`,
    `${row.gfCount}<br><span class="muted">${pct(row.gfShare)} / ${gfTone(row, gfOverallShare)}</span>`,
    esc(row.gfExamples || "无"),
    `${fmt(row.eqMedian)} / ${pct(row.annMedian)} / ${pct(row.volMedian)} / ${pct(row.mddMedian)}`,
    `${esc(row.topRisk)}<br><span class="muted">${esc(row.topImplementation)}</span>`,
  ];
});

const gfSecondaryRows = gfBySecondary.map((row) => {
  const primary = primaryCategory(row.rows[0] || {});
  const marketPeer = secondarySummary.find((item) => item.key === row.key);
  return [
    esc(primary),
    esc(row.key),
    String(row.count),
    marketPeer ? `${marketPeer.count}` : "-",
    marketPeer ? pct(marketPeer.gfShare) : "-",
    `${fmt(row.eqMedian)} / ${pct(row.annMedian)} / ${pct(row.mddMedian)}`,
    esc(row.rows.slice(0, 8).map((item) => raw(item["策略名称"])).join("、")),
  ];
});

const ruleRows = [
  ["一级分类", "优先使用系统主可比池并映射为销售/产品货架：现金、纯债、固收+、目标盈、多资产、偏股、主题、海外、养老。", "保证每条策略只进入一个一级池，适合做市场规模和广发布局对比。"],
  ["二级分类", "在一级池内按目标机制、风险预算、资产权重、地域、实现方式、名称/标签拆分。", "二级池用于真实比较、营销子货架和产品线缺口识别。"],
  ["风险预算", "权益权重 + 波动率 + 最大回撤；目标盈单独使用低波、稳健、增强、平衡、进取五档。", "同风险桶内可比较，跨风险桶只做客户适配和销售定位。"],
  ["目标盈期次", "名称含期次的策略归为期次发车，系列名通过剔除期次号归并。", "避免把同一系列多期重复发车误认为多个独立产品线。"],
  ["广发口径", "投顾机构或渠道包含“广发基金/广发投顾”。", "用于衡量广发在每个市场分类中的覆盖和相对高低配。"],
  ["数据质量", "持仓权重全部为 0 或空的策略进入待核验，不进入正式考核比较池。", "仍可用于销售/产品线索，但需补持仓、可买状态和生命周期。"],
];

const primaryTable = table(
  ["一级分类", "市场情况", "机构覆盖", "广发情况", "广发代表策略", "权益/年化/波动/回撤中位数", "主要风险与实现方式", "产品分析含义"],
  primaryRows,
  "wide"
);

const secondaryTable = table(
  ["一级分类", "二级分类", "市场情况", "机构覆盖", "广发情况", "广发代表策略", "权益/年化/波动/回撤中位数", "主要风险与实现方式"],
  secondaryRows,
  "wide"
);

const gfTable = table(
  ["一级分类", "广发二级分类", "广发策略数", "全市场同类数", "同类广发占比", "广发权益/年化/回撤中位数", "广发策略样例"],
  gfSecondaryRows,
  "wide"
);

const rulesTable = table(["维度", "规则", "用途"], ruleRows.map((row) => row.map(esc)), "wide");

const primaryCards = primarySummary.map((row) => {
  const tone = gfTone(row, gfOverallShare);
  const toneClass = tone === "空白" ? "empty" : tone === "相对高配" ? "strong" : tone === "相对低配" ? "weak" : "";
  return `<div class="class-card">
    <div class="class-card-head">
      <h3>${esc(row.key)}</h3>
      ${pill(tone, toneClass)}
    </div>
    <div class="metric-line"><span>市场</span><strong>${row.count}</strong><span>${pct(row.marketShare)}</span></div>
    <div class="metric-line"><span>广发</span><strong>${row.gfCount}</strong><span>${pct(row.gfShare)}</span></div>
    <p>${esc(primaryMeta[row.key]?.sales || "")}</p>
  </div>`;
}).join("");

const generatedAt = new Date().toLocaleString("zh-CN", { hour12: false });

const html = `<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>全市场投顾策略分类地图</title>
  <style>
    :root {
      --ink: #17202a;
      --muted: #617080;
      --line: #d8e0e8;
      --soft: #f4f7fa;
      --panel: #ffffff;
      --accent: #186b77;
      --accent2: #8a5a12;
      --green: #1f7a4d;
      --red: #b42318;
      --orange: #a36200;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      background: #eef2f6;
      color: var(--ink);
      font: 14px/1.55 -apple-system, BlinkMacSystemFont, "Segoe UI", "Microsoft YaHei", sans-serif;
    }
    header {
      background: #0c3540;
      color: white;
      padding: 30px 36px;
    }
    header h1 { margin: 0 0 8px; font-size: 28px; letter-spacing: 0; }
    header p { margin: 4px 0; color: #dcecef; max-width: 1120px; }
    main { max-width: 1320px; margin: 0 auto; padding: 24px 28px 44px; }
    section {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 22px;
      margin-bottom: 18px;
      box-shadow: 0 1px 2px rgba(20, 30, 45, .04);
    }
    h2 { margin: 0 0 12px; font-size: 20px; }
    h3 { margin: 0; font-size: 15px; }
    p { margin: 8px 0; }
    code {
      background: rgba(255,255,255,.15);
      border: 1px solid rgba(255,255,255,.25);
      border-radius: 4px;
      padding: 1px 4px;
    }
    .note {
      color: var(--muted);
      font-size: 13px;
    }
    .callout {
      border-left: 4px solid var(--accent);
      background: #eef8fb;
      padding: 12px 14px;
      border-radius: 0 8px 8px 0;
      margin: 12px 0;
    }
    .stats {
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 12px;
      margin: 14px 0 0;
    }
    .stat {
      background: var(--soft);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 14px;
      min-height: 94px;
    }
    .stat span { display: block; color: var(--muted); font-size: 13px; }
    .stat strong { display: block; font-size: 24px; margin: 4px 0; }
    .cards {
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 12px;
      margin-top: 12px;
    }
    .class-card {
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 14px;
      background: #fbfcfe;
    }
    .class-card-head {
      display: flex;
      justify-content: space-between;
      gap: 8px;
      align-items: center;
      margin-bottom: 10px;
    }
    .metric-line {
      display: grid;
      grid-template-columns: 48px 64px 1fr;
      gap: 6px;
      align-items: baseline;
      color: var(--muted);
      margin: 3px 0;
    }
    .metric-line strong { color: var(--ink); font-size: 18px; }
    .pill {
      display: inline-block;
      padding: 2px 8px;
      border-radius: 999px;
      background: #e8eef4;
      color: #33404c;
      white-space: nowrap;
      font-weight: 650;
      font-size: 12px;
    }
    .pill.strong { background: #e2f6ea; color: var(--green); }
    .pill.weak { background: #fff0d6; color: var(--orange); }
    .pill.empty { background: #fde7e4; color: var(--red); }
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
    .wide td:nth-child(n+2), .wide th:nth-child(n+2) { white-space: nowrap; }
    .wide td:nth-child(1), .wide td:nth-child(2), .wide td:last-child { white-space: normal; }
    .muted { color: var(--muted); font-size: 12px; }
    ul { margin: 8px 0 0 20px; padding: 0; }
    li { margin: 5px 0; }
    footer {
      color: var(--muted);
      text-align: center;
      font-size: 12px;
      padding-top: 8px;
    }
    @media (max-width: 980px) {
      header { padding: 24px 18px; }
      main { padding: 18px 12px 34px; }
      .stats, .cards { grid-template-columns: 1fr; }
      section { padding: 16px; }
      table { display: block; overflow-x: auto; }
      th, td { min-width: 112px; }
    }
  </style>
</head>
<body>
  <header>
    <h1>全市场投顾策略分类地图</h1>
    <p>数据来源：<code>basic_data/data/basic_summary.js</code>。当前可分析明细为 ${strategies.length} 条展示策略；overview 策略总数为 ${overviewTotal}，其中 ${hiddenChannels} 个渠道在列表中隐藏（${hiddenChannelIds.map(esc).join("、")}）。</p>
    <p>数据更新至 ${esc(dataDate)}；报告生成 ${esc(generatedAt)}。本报告先聚焦分类地图、市场情况和广发情况，不展开具体营销机会排序。</p>
  </header>
  <main>
    <section>
      <h2>分类地图口径</h2>
      <div class="callout">
        <p><strong>一级分类</strong>是销售/产品货架，保持互斥，用来回答“市场有多大、广发有没有布局”。</p>
        <p><strong>二级分类</strong>是更接近真实比较池或营销子货架的拆分，用来回答“同类里广发处在什么位置”。风险、地域、实现方式和数据质量作为解释字段，不额外重复计数。</p>
      </div>
      <div class="stats">
        <div class="stat"><span>可分析策略</span><strong>${displayCount}</strong><span>当前展示明细</span></div>
        <div class="stat"><span>一级分类数</span><strong>${primarySummary.length}</strong><span>互斥主货架</span></div>
        <div class="stat"><span>二级分类数</span><strong>${secondarySummary.length}</strong><span>互斥子货架</span></div>
        <div class="stat"><span>广发策略</span><strong>${gfRows.length}</strong><span>全市场占比 ${pct(gfOverallShare)}</span></div>
      </div>
      <div class="cards">${primaryCards}</div>
    </section>

    <section>
      <h2>分类规则</h2>
      ${rulesTable}
    </section>

    <section>
      <h2>一级分类：市场情况与广发情况</h2>
      <p class="note">“权益/年化/波动/回撤中位数”均使用当前策略明细字段。广发情况中的“相对高配/低配”以广发整体占比 ${pct(gfOverallShare)} 为粗基准。</p>
      ${primaryTable}
    </section>

    <section>
      <h2>二级分类：市场情况与广发情况</h2>
      <p class="note">二级分类在一级分类内互斥。目标盈的跨境/黄金/主题型优先单列，其余按风险预算拆分。</p>
      ${secondaryTable}
    </section>

    <section>
      <h2>广发策略落点</h2>
      <p class="note">以下只看广发已有策略，右侧给出同类全市场规模和广发占比，便于后续再做产品布局深挖。</p>
      ${gfTable}
    </section>

    <section>
      <h2>目标盈分类边界</h2>
      <p>目标盈主池共有 ${targetRows.length} 条记录，其中 ${targetIssueRows.length} 条为期次发车型；按“机构 + 归并系列名”后约 ${targetSeriesCount} 个系列。另有 ${targetNoHolding} 条持仓缺失或权重全为 0，适合先做数据核验，不进入正式比较池。</p>
      <ul>
        <li>低波/稳健目标盈适合销售达标体验和稳健活动。</li>
        <li>增强/平衡目标盈适合中风险客户的目标收益子货架。</li>
        <li>主题、全球、黄金或高权益目标盈要单列，不能代表“稳健目标盈”。</li>
      </ul>
    </section>

    <footer>本报告由本地 Node 脚本生成，不改写原始数据。生成脚本：analysis_outputs/generate_strategy_classification_map_report.js</footer>
  </main>
</body>
</html>`;

fs.writeFileSync(outputPath, html, "utf8");
console.log(outputPath);
