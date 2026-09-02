const fs = require("fs");
const path = require("path");
const vm = require("vm");

const root = path.resolve(__dirname, "..");
const sourcePath = path.join(root, "basic_data", "data", "basic_summary.js");
const htmlPath = path.join(__dirname, "strategy_risk_classification_report.html");
const jsonPath = path.join(__dirname, "strategy_risk_classification_rows.json");
const csvPath = path.join(__dirname, "strategy_risk_classification_rows.csv");

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

function share(part, total, digits = 1) {
  if (!total) return "-";
  return `${((part / total) * 100).toFixed(digits)}%`;
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
    const key = keyFn(row) || "(空)";
    if (!map.has(key)) map.set(key, []);
    map.get(key).push(row);
  }
  return [...map.entries()].map(([key, bucket]) => ({ key, rows: bucket, count: bucket.length }));
}

function topCounts(rows, keyFn, limit = 4) {
  return groupBy(rows, keyFn)
    .sort((a, b) => b.count - a.count || String(a.key).localeCompare(String(b.key), "zh"))
    .slice(0, limit)
    .map((item) => `${item.key}(${item.count})`)
    .join("、") || "-";
}

function isGuangfa(row) {
  return /广发基金|广发投顾/.test(`${raw(row["投顾机构"])} ${raw(row["渠道"])}`);
}

function isTtfund(row) {
  return raw(row["渠道"]) === "天天基金/投顾" || raw(row["统一策略ID"]).startsWith("ttfund__");
}

function hasHoldingData(row) {
  return ["权益基金权重", "债券基金权重", "货币基金权重", "混合基金权重", "指数基金权重"]
    .some((key) => nz(row[key]) > 0);
}

const riskBands = [
  {
    index: 0,
    level: "R0 现金/超低波",
    short: "R0",
    eqMax: 3,
    volMax: 0.8,
    mddMax: 1.2,
    sales: "现金替代、闲钱管理、低波备用金。",
    research: "重点看底层货币/短债质量、费率、回撤尖刺和流动性。",
    product: "适合做低门槛入口和余额承接，产品差异通常来自体验和稳定性。"
  },
  {
    index: 1,
    level: "R1 低波",
    short: "R1",
    eqMax: 8,
    volMax: 2.0,
    mddMax: 3.0,
    sales: "防守型资金、低波稳健客户、短久期替代。",
    research: "关注债券久期、信用风险、权益/转债扰动是否可控。",
    product: "适合纯债、短债和低权益增强的细分货架。"
  },
  {
    index: 2,
    level: "R2 稳健收益",
    short: "R2",
    eqMax: 18,
    volMax: 4.0,
    mddMax: 6.0,
    sales: "稳健增值、固收+主货架、目标收益型客户承接。",
    research: "看权益仓位效率、回撤控制、胜率和调仓纪律。",
    product: "可拆成低权益固收+、标准固收+、弹性固收+。"
  },
  {
    index: 3,
    level: "R3 均衡稳健",
    short: "R3",
    eqMax: 35,
    volMax: 7.5,
    mddMax: 12.0,
    sales: "中长期配置、家庭资产配置、稳健成长客户。",
    research: "重点看资产配置贡献、再平衡效率和极端市场回撤。",
    product: "适合均衡多资产、养老/目标风险和温和目标盈。"
  },
  {
    index: 4,
    level: "R4 均衡成长",
    short: "R4",
    eqMax: 55,
    volMax: 11.0,
    mddMax: 20.0,
    sales: "权益参与但要求组合化管理的成长型客户。",
    research: "看权益 beta、风格稳定性、主动/指数工具搭配。",
    product: "适合偏股配置、成长多资产、海外或主题卫星组合。"
  },
  {
    index: 5,
    level: "R5 权益/进取",
    short: "R5",
    eqMax: Infinity,
    volMax: Infinity,
    mddMax: Infinity,
    sales: "高权益承受力客户、长期定投、主题机会承接。",
    research: "关注权益暴露、行业集中、最大回撤和持有期体验。",
    product: "适合权益核心、指数工具链、主题/行业和全球权益。"
  }
];

const riskOrder = ["D0 持仓缺失", ...riskBands.map((band) => band.level)];
const riskLabelMap = new Map(riskBands.map((band) => [band.index, band.level]));

function metricLevel(value, key) {
  const v = nz(value);
  for (const band of riskBands) {
    const max = key === "eq" ? band.eqMax : key === "vol" ? band.volMax : band.mddMax;
    if (v <= max) return band.index;
  }
  return 5;
}

function metricBandLabel(index) {
  return riskLabelMap.get(index) || "R5 权益/进取";
}

function measuredRisk(row) {
  if (!hasHoldingData(row)) {
    return {
      level: "D0 持仓缺失",
      eqLevel: "",
      volLevel: "",
      mddLevel: "",
      trigger: "持仓权重缺失",
      missingMetrics: ["持仓权重"]
    };
  }

  const eqLevel = metricLevel(row["权益基金权重"], "eq");
  const volLevel = metricLevel(row["波动率"], "vol");
  const mddLevel = metricLevel(row["最大回撤"], "mdd");
  const finalLevel = Math.max(eqLevel, volLevel, mddLevel);
  const triggers = [];
  if (eqLevel === finalLevel) triggers.push("权益");
  if (volLevel === finalLevel) triggers.push("波动");
  if (mddLevel === finalLevel) triggers.push("回撤");

  const missingMetrics = [];
  if (num(row["波动率"]) == null) missingMetrics.push("波动率");
  if (num(row["最大回撤"]) == null) missingMetrics.push("最大回撤");

  return {
    level: metricBandLabel(finalLevel),
    eqLevel: metricBandLabel(eqLevel),
    volLevel: metricBandLabel(volLevel),
    mddLevel: metricBandLabel(mddLevel),
    trigger: finalLevel === 0 ? "三项均在R0内" : triggers.join("+"),
    missingMetrics
  };
}

function officialPerformancePresent(row) {
  return Boolean(raw(row["官方单位净值"]) || raw(row["官方累计收益"]) || raw(row["最新业绩日期"]) || raw(row["收益数据截至"]));
}

function ttfundDisplayMark(row) {
  if (!isTtfund(row)) {
    return {
      mark: "非天天策略",
      code: "非天天",
      evidence: "渠道非天天基金/投顾"
    };
  }

  const status = raw(row["运作状态"]);
  const text = `${raw(row["策略名称"])} ${raw(row["投顾机构"])} ${status} ${raw(row["质检情况"])}`;
  const hasOfficial = officialPerformancePresent(row);
  const negativeSignal = /测试|内部|下架|隐藏|不可见|终止|暂停|停止|关闭/.test(text);
  const issueText = /目标盈|小目标|止盈|达标|目标收益|期|尊享|期满|到期/.test(`${raw(row["策略名称"])} ${raw(row["主可比池"])} ${raw(row["特殊标签"])} ${raw(row["策略类型"])}`)
    ? "期次型/目标收益产品"
    : "期次型产品";

  if (negativeSignal || status === "已终止" || !hasOfficial) {
    const reasons = [];
    if (negativeSignal || status === "已终止") reasons.push("存在测试/终止/隐藏等非当前对客信号");
    if (!hasOfficial) reasons.push("缺少官方业绩字段");
    return {
      mark: "非对客展示/已终止或数据缺失",
      code: "否",
      evidence: reasons.join("；") || "不满足对客展示规则"
    };
  }

  if (status === "开放窗口" || status === "listed") {
    return {
      mark: "当前对客展示",
      code: "是",
      evidence: `运作状态=${status}，且有官方业绩字段`
    };
  }

  if (status === "未披露") {
    return {
      mark: "非对客展示/未披露",
      code: "否",
      evidence: "运作状态=未披露；本报告不将其视作对客展示"
    };
  }

  if (/^原始状态[12]$/.test(status)) {
    return {
      mark: "历史期次-曾上架运作",
      code: "历史期次",
      evidence: `运作状态=${status}；识别为${issueText}，属于曾经上架运作但当前非开放窗口的产品`
    };
  }

  return {
    mark: "非对客展示/其他状态",
    code: "否",
    evidence: `运作状态=${status || "空"}，未纳入本次展示判定规则`
  };
}

const enriched = strategies.map((row) => {
  const risk = measuredRisk(row);
  const ttMark = ttfundDisplayMark(row);
  return {
    "统一策略ID": raw(row["统一策略ID"]),
    "策略代码": raw(row["策略代码"]),
    "策略名称": raw(row["策略名称"]),
    "渠道": raw(row["渠道"]),
    "投顾机构": raw(row["投顾机构"]),
    "原披露风险等级": raw(row["风险等级"]),
    "测算风险等级": risk.level,
    "风险基础分类": risk.level,
    "权益风险档": risk.eqLevel,
    "波动风险档": risk.volLevel,
    "回撤风险档": risk.mddLevel,
    "风险触发指标": risk.trigger,
    "风险测算缺失项": risk.missingMetrics.join("、"),
    "权益基金权重": num(row["权益基金权重"]),
    "债券基金权重": num(row["债券基金权重"]),
    "货币基金权重": num(row["货币基金权重"]),
    "混合基金权重": num(row["混合基金权重"]),
    "指数基金权重": num(row["指数基金权重"]),
    "QDII权重": num(row["QDII权重"]),
    "波动率": num(row["波动率"]),
    "最大回撤": num(row["最大回撤"]),
    "主可比池": raw(row["主可比池"]),
    "业务主分类": raw(row["业务主分类"]) || raw(row["主可比池"]),
    "业务组合分类": raw(row["业务组合分类"]) || `${risk.level}｜${raw(row["主可比池"]) || "未分类"}`,
    "业务分类标签": raw(row["业务分类标签"]),
    "市场地域": raw(row["市场地域"]),
    "数据完整性": raw(row["数据完整性"]),
    "基础数据等级": raw(row["基础数据等级"]),
    "运作状态": raw(row["运作状态"]),
    "最新业绩日期": raw(row["最新业绩日期"]),
    "收益数据截至": raw(row["收益数据截至"]),
    "是否广发相关": isGuangfa(row) ? "是" : "否",
    "是否天天策略": isTtfund(row) ? "是" : "否",
    "天天当前对客展示": ttMark.code === "是" ? "是" : ttMark.code === "非天天" ? "非天天" : "否",
    "天天对客展示标记": ttMark.code,
    "天天展示状态": ttMark.mark,
    "天天展示分层": ttMark.mark,
    "天天展示判定依据": ttMark.evidence
  };
});

const riskIndex = new Map(riskOrder.map((level, index) => [level, index]));
enriched.sort((a, b) => {
  const riskDiff = (riskIndex.get(a["测算风险等级"]) ?? 999) - (riskIndex.get(b["测算风险等级"]) ?? 999);
  if (riskDiff) return riskDiff;
  const channelDiff = a["渠道"].localeCompare(b["渠道"], "zh");
  if (channelDiff) return channelDiff;
  return a["策略名称"].localeCompare(b["策略名称"], "zh");
});

const gfRows = enriched.filter((row) => row["是否广发相关"] === "是");
const ttRows = enriched.filter((row) => row["是否天天策略"] === "是");
const overallGfShare = gfRows.length / (enriched.length || 1);

function marketPosition(bucket, gfBucket) {
  if (!bucket.length) return "无样本";
  const gfShare = gfBucket.length / bucket.length;
  if (!gfBucket.length && bucket.length >= 30) return "广发暂无布局，若该风险档符合客户货架，可作为补位评估池。";
  if (gfShare < overallGfShare * 0.6 && bucket.length >= 30) return "广发相对低配，适合复核产品供给和营销承接能力。";
  if (gfShare > overallGfShare * 1.4 && gfBucket.length >= 5) return "广发布局高于市场平均，可进一步做同档产品分层和差异化话术。";
  return "广发覆盖接近市场平均，重点看细分可比池内的质量和体验差异。";
}

function statsFor(rows) {
  return {
    count: rows.length,
    medEq: median(rows.map((row) => row["权益基金权重"])),
    medVol: median(rows.map((row) => row["波动率"])),
    medMdd: median(rows.map((row) => row["最大回撤"])),
    topPools: topCounts(rows, (row) => row["主可比池"], 4),
    topOrgs: topCounts(rows, (row) => row["投顾机构"], 4),
    topDisplay: topCounts(rows, (row) => row["天天展示分层"], 4)
  };
}

const riskSummary = riskOrder.map((level) => {
  const bucket = enriched.filter((row) => row["测算风险等级"] === level);
  const gfBucket = bucket.filter((row) => row["是否广发相关"] === "是");
  const ttBucket = bucket.filter((row) => row["是否天天策略"] === "是");
  const displayBucket = bucket.filter((row) => row["天天对客展示标记"] === "是");
  const stats = statsFor(bucket);
  const gfStats = statsFor(gfBucket);
  return {
    level,
    bucket,
    gfBucket,
    ttBucket,
    displayBucket,
    stats,
    gfStats,
    opportunity: marketPosition(bucket, gfBucket)
  };
});

function riskRuleTable() {
  const rows = [
    `<tr><td>D0 持仓缺失</td><td>权益/债券/货币/混合/指数基金权重均为 0 或缺失</td><td>不进入正式同档比较，优先补齐持仓</td></tr>`,
    ...riskBands.map((band) => {
      if (band.index === 5) {
        return `<tr><td>${esc(band.level)}</td><td>权益 > 55%，或波动 > 11.0%，或最大回撤 > 20.0%</td><td>任一风险指标超出 R4 上限，即进入 R5</td></tr>`;
      }
      return `<tr><td>${esc(band.level)}</td><td>权益 <= ${band.eqMax}%；波动 <= ${band.volMax}%；最大回撤 <= ${band.mddMax}%</td><td>权益、波动、回撤分别落档，最终风险等级取三项中的最高风险档</td></tr>`;
    })
  ];
  return rows.join("\n");
}

function riskMapRows() {
  return riskSummary.map((item) => {
    const marketCount = item.bucket.length;
    const gfCount = item.gfBucket.length;
    const ttCount = item.ttBucket.length;
    const displayCount = item.displayBucket.length;
    return `<tr>
      <td class="strong">${esc(item.level)}</td>
      <td>${marketCount}<span class="muted"> / ${share(marketCount, enriched.length)}</span></td>
      <td>${gfCount}<span class="muted"> / 档内${share(gfCount, marketCount)}，广发内部${share(gfCount, gfRows.length)}</span></td>
      <td>${ttCount}<span class="muted"> / 对客${displayCount}</span></td>
      <td>${pct(item.stats.medEq)} / ${pct(item.stats.medVol)} / ${pct(item.stats.medMdd)}</td>
      <td>${esc(item.stats.topPools)}</td>
      <td>${esc(item.gfStats.topPools)}</td>
      <td>${esc(item.opportunity)}</td>
    </tr>`;
  }).join("\n");
}

function displayRows() {
  return groupBy(enriched, (row) => row["天天展示分层"])
    .sort((a, b) => {
      const order = ["当前对客展示", "历史期次-曾上架运作", "非对客展示/未披露", "非对客展示/已终止或数据缺失", "非对客展示/其他状态", "非天天策略"];
      return order.indexOf(a.key) - order.indexOf(b.key);
    })
    .map((item) => `<tr>
      <td class="strong">${esc(item.key)}</td>
      <td>${item.count}</td>
      <td>${share(item.count, enriched.length)}</td>
      <td>${esc(topCounts(item.rows, (row) => row["运作状态"], 5))}</td>
      <td>${esc(topCounts(item.rows, (row) => row["测算风险等级"], 5))}</td>
      <td>${esc(topCounts(item.rows, (row) => row["主可比池"], 5))}</td>
    </tr>`)
    .join("\n");
}

function riskDisplayMatrixRows() {
  const marks = ["是", "历史期次", "否", "非天天"];
  return riskSummary.map((item) => {
    const cells = marks.map((mark) => item.bucket.filter((row) => row["天天对客展示标记"] === mark).length);
    return `<tr><td class="strong">${esc(item.level)}</td>${cells.map((value) => `<td>${value}</td>`).join("")}</tr>`;
  }).join("\n");
}

function poolMatrixRows(rows, isGfOnly = false) {
  const source = isGfOnly ? rows.filter((row) => row["是否广发相关"] === "是") : rows;
  const poolNames = groupBy(source, (row) => row["主可比池"])
    .sort((a, b) => b.count - a.count || a.key.localeCompare(b.key, "zh"))
    .map((item) => item.key);
  return poolNames.map((pool) => {
    const poolRows = source.filter((row) => row["主可比池"] === pool);
    const cells = riskOrder.map((level) => poolRows.filter((row) => row["测算风险等级"] === level).length);
    return `<tr><td class="strong">${esc(pool)}</td><td>${poolRows.length}</td>${cells.map((value) => `<td>${value || ""}</td>`).join("")}</tr>`;
  }).join("\n");
}

function exampleRows(mark) {
  return enriched
    .filter((row) => row["天天展示分层"] === mark)
    .slice(0, 12)
    .map((row) => `<tr>
      <td>${esc(row["策略名称"])}</td>
      <td>${esc(row["投顾机构"])}</td>
      <td>${esc(row["运作状态"])}</td>
      <td>${esc(row["测算风险等级"])}</td>
      <td>${esc(row["主可比池"])}</td>
      <td>${esc(row["天天展示判定依据"])}</td>
    </tr>`)
    .join("\n");
}

function allStrategyRows() {
  return enriched.map((row) => `<tr>
    <td>${esc(row["测算风险等级"])}</td>
    <td>${esc(row["风险触发指标"])}</td>
    <td>${esc(row["策略名称"])}</td>
    <td>${esc(row["渠道"])}</td>
    <td>${esc(row["投顾机构"])}</td>
    <td>${esc(row["主可比池"])}</td>
    <td>${esc(row["原披露风险等级"])}</td>
    <td>${pct(row["权益基金权重"])}</td>
    <td>${pct(row["波动率"])}</td>
    <td>${pct(row["最大回撤"])}</td>
    <td>${esc(row["天天对客展示标记"])}</td>
    <td>${esc(row["天天展示分层"])}</td>
    <td>${esc(row["运作状态"])}</td>
    <td>${esc(row["数据完整性"])}</td>
  </tr>`).join("\n");
}

function csvEscape(value) {
  const s = raw(value);
  if (/[",\n\r]/.test(s)) return `"${s.replace(/"/g, '""')}"`;
  return s;
}

const csvColumns = [
  "统一策略ID",
  "策略代码",
  "策略名称",
  "渠道",
  "投顾机构",
  "原披露风险等级",
  "测算风险等级",
  "风险基础分类",
  "权益风险档",
  "波动风险档",
  "回撤风险档",
  "风险触发指标",
  "风险测算缺失项",
  "权益基金权重",
  "债券基金权重",
  "货币基金权重",
  "混合基金权重",
  "指数基金权重",
  "QDII权重",
  "波动率",
  "最大回撤",
  "主可比池",
  "业务主分类",
  "业务组合分类",
  "业务分类标签",
  "市场地域",
  "数据完整性",
  "基础数据等级",
  "运作状态",
  "最新业绩日期",
  "收益数据截至",
  "是否广发相关",
  "是否天天策略",
  "天天当前对客展示",
  "天天对客展示标记",
  "天天展示状态",
  "天天展示分层",
  "天天展示判定依据"
];

const hiddenCount = (summary.overview?.["策略总数"] || 0) - enriched.length;
const generatedAt = new Date().toISOString().replace("T", " ").slice(0, 19);

const html = `<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>投顾策略测算风险等级基础分类地图</title>
  <style>
    :root {
      color-scheme: light;
      --ink: #172033;
      --muted: #687186;
      --line: #d9deea;
      --panel: #ffffff;
      --soft: #f5f7fb;
      --brand: #1d5fd0;
      --accent: #0f8b6f;
      --warn: #9a5b00;
      --bad: #a13030;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Microsoft YaHei", sans-serif;
      color: var(--ink);
      background: #f0f3f8;
      line-height: 1.55;
    }
    header {
      padding: 28px 32px 18px;
      background: #ffffff;
      border-bottom: 1px solid var(--line);
    }
    main { padding: 22px 32px 40px; }
    h1 { margin: 0 0 10px; font-size: 26px; letter-spacing: 0; }
    h2 { margin: 28px 0 10px; font-size: 20px; letter-spacing: 0; }
    h3 { margin: 20px 0 8px; font-size: 16px; letter-spacing: 0; }
    p { margin: 6px 0; }
    .muted { color: var(--muted); font-size: 12px; }
    .note {
      padding: 10px 12px;
      border-left: 4px solid var(--brand);
      background: #eef4ff;
      margin: 12px 0;
    }
    .grid {
      display: grid;
      grid-template-columns: repeat(5, minmax(150px, 1fr));
      gap: 10px;
      margin-top: 16px;
    }
    .card {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 14px;
      min-height: 90px;
    }
    .card .label { color: var(--muted); font-size: 12px; }
    .card .value { font-size: 24px; font-weight: 700; margin-top: 4px; }
    table {
      width: 100%;
      border-collapse: collapse;
      background: var(--panel);
      border: 1px solid var(--line);
      margin: 10px 0 18px;
    }
    th, td {
      border-bottom: 1px solid var(--line);
      border-right: 1px solid var(--line);
      padding: 8px 9px;
      text-align: left;
      vertical-align: top;
      font-size: 13px;
    }
    th {
      background: #e9eef7;
      font-weight: 700;
      position: sticky;
      top: 0;
      z-index: 1;
    }
    tr:last-child td { border-bottom: 0; }
    td:last-child, th:last-child { border-right: 0; }
    .strong { font-weight: 700; }
    .scroll { overflow: auto; max-height: 620px; border: 1px solid var(--line); background: #fff; }
    .scroll table { margin: 0; border: 0; min-width: 1280px; }
    .pill {
      display: inline-block;
      padding: 2px 7px;
      border-radius: 999px;
      background: #edf4f2;
      color: var(--accent);
      font-size: 12px;
      font-weight: 700;
      margin-right: 5px;
    }
    details {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 10px 12px;
      margin: 12px 0;
    }
    summary {
      cursor: pointer;
      font-weight: 700;
    }
    .two-col {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 16px;
    }
    @media (max-width: 1100px) {
      .grid { grid-template-columns: repeat(2, minmax(150px, 1fr)); }
      .two-col { grid-template-columns: 1fr; }
      header, main { padding-left: 18px; padding-right: 18px; }
    }
  </style>
</head>
<body>
  <header>
    <h1>投顾策略测算风险等级基础分类地图</h1>
    <p>生成时间：${esc(generatedAt)}；原始数据更新至：${esc(summary.overview?.["数据更新至"] || "-")}；数据文件：basic_data/data/basic_summary.js。</p>
    <p class="muted">本报告将“测算风险等级”作为基础分类；天天投顾是否对客展示作为附加标记，不参与风险基础分类。</p>
  </header>

  <main>
    <div class="grid">
      <div class="card"><div class="label">本次重算策略</div><div class="value">${enriched.length}</div><div class="muted">summary.strategies 全量</div></div>
      <div class="card"><div class="label">概要策略总数</div><div class="value">${esc(summary.overview?.["策略总数"] || "-")}</div><div class="muted">隐藏渠道未出明细：${hiddenCount > 0 ? hiddenCount : 0}</div></div>
      <div class="card"><div class="label">广发相关策略</div><div class="value">${gfRows.length}</div><div class="muted">投顾机构/渠道含广发基金或广发投顾</div></div>
      <div class="card"><div class="label">天天投顾策略</div><div class="value">${ttRows.length}</div><div class="muted">渠道=天天基金/投顾</div></div>
      <div class="card"><div class="label">天天可确认对客</div><div class="value">${enriched.filter((row) => row["天天对客展示标记"] === "是").length}</div><div class="muted">开放窗口/listed 且无非对客信号</div></div>
    </div>

    <div class="note">
      <p><span class="pill">数据范围</span>当前可读取明细为 1154 条展示策略；概要中策略总数为 ${esc(summary.overview?.["策略总数"] || "-")} 条，隐藏渠道未提供逐策略明细，本报告不对缺失明细造数。</p>
      <p><span class="pill">分类原则</span>风险基础分类互斥且归一：先判断是否持仓缺失；其余策略分别按权益权重、波动率、最大回撤落档，最终取三项中的最高风险档。</p>
    </div>

    <h2>一、风险等级规则</h2>
    <table>
      <thead><tr><th>测算风险等级</th><th>入档阈值</th><th>解释</th></tr></thead>
      <tbody>${riskRuleTable()}</tbody>
    </table>

    <h2>二、基础分类地图：按测算风险等级</h2>
    <table>
      <thead>
        <tr>
          <th>风险等级</th>
          <th>市场数量/占比</th>
          <th>广发数量/占比</th>
          <th>天天数量/可确认对客</th>
          <th>中位数：权益/波动/回撤</th>
          <th>市场主要可比池</th>
          <th>广发主要可比池</th>
          <th>广发情况与机会提示</th>
        </tr>
      </thead>
      <tbody>${riskMapRows()}</tbody>
    </table>

    <div class="two-col">
      <section>
        <h2>三、市场：风险 × 原主可比池</h2>
        <table>
          <thead><tr><th>主可比池</th><th>合计</th>${riskOrder.map((level) => `<th>${esc(level)}</th>`).join("")}</tr></thead>
          <tbody>${poolMatrixRows(enriched, false)}</tbody>
        </table>
      </section>
      <section>
        <h2>四、广发：风险 × 原主可比池</h2>
        <table>
          <thead><tr><th>主可比池</th><th>合计</th>${riskOrder.map((level) => `<th>${esc(level)}</th>`).join("")}</tr></thead>
          <tbody>${poolMatrixRows(enriched, true)}</tbody>
        </table>
      </section>
    </div>

    <h2>五、天天投顾对客展示标记</h2>
    <div class="note">
      <p>明细字段中没有直接的“对客展示/上架/可见”字段，本报告使用“运作状态 + 非对客信号 + 官方业绩字段”形成测算标记。</p>
      <p>规则：开放窗口或 listed 且无测试/终止/隐藏等信号，标为“当前对客展示”；原始状态1/2 多为目标盈、尊享等期次产品，标为“历史期次-曾上架运作”；未披露、已终止、测试/隐藏/缺官方业绩，标为非对客展示。</p>
    </div>
    <table>
      <thead><tr><th>展示分层</th><th>数量</th><th>全量占比</th><th>主要运作状态</th><th>主要风险等级</th><th>主要可比池</th></tr></thead>
      <tbody>${displayRows()}</tbody>
    </table>

    <h3>天天展示标记 × 风险等级</h3>
    <table>
      <thead><tr><th>风险等级</th><th>当前对客展示</th><th>历史期次</th><th>非对客展示</th><th>非天天</th></tr></thead>
      <tbody>${riskDisplayMatrixRows()}</tbody>
    </table>

    <details>
      <summary>查看天天展示标记样本</summary>
      <h3>当前对客展示样本</h3>
      <table><thead><tr><th>策略</th><th>投顾机构</th><th>状态</th><th>风险</th><th>主可比池</th><th>依据</th></tr></thead><tbody>${exampleRows("当前对客展示")}</tbody></table>
      <h3>非对客展示样本</h3>
      <table><thead><tr><th>策略</th><th>投顾机构</th><th>状态</th><th>风险</th><th>主可比池</th><th>依据</th></tr></thead><tbody>${exampleRows("非对客展示/未披露")}${exampleRows("非对客展示/已终止或数据缺失")}</tbody></table>
      <h3>历史期次样本</h3>
      <table><thead><tr><th>策略</th><th>投顾机构</th><th>状态</th><th>风险</th><th>主可比池</th><th>依据</th></tr></thead><tbody>${exampleRows("历史期次-曾上架运作")}</tbody></table>
    </details>

    <h2>六、全量策略明细</h2>
    <p class="muted">完整结构化数据已同步输出：strategy_risk_classification_rows.csv 与 strategy_risk_classification_rows.json。</p>
    <div class="scroll">
      <table>
        <thead>
          <tr>
            <th>测算风险</th><th>触发指标</th><th>策略名称</th><th>渠道</th><th>投顾机构</th><th>主可比池</th><th>原披露风险</th><th>权益</th><th>波动</th><th>回撤</th><th>天天标记</th><th>天天分层</th><th>状态</th><th>数据完整性</th>
          </tr>
        </thead>
        <tbody>${allStrategyRows()}</tbody>
      </table>
    </div>
  </main>
</body>
</html>`;

fs.writeFileSync(htmlPath, html, "utf8");
fs.writeFileSync(jsonPath, JSON.stringify(enriched, null, 2), "utf8");
const csv = [
  csvColumns.map(csvEscape).join(","),
  ...enriched.map((row) => csvColumns.map((column) => csvEscape(row[column])).join(","))
].join("\n");
fs.writeFileSync(csvPath, `\uFEFF${csv}`, "utf8");

console.log(JSON.stringify({
  htmlPath,
  jsonPath,
  csvPath,
  rows: enriched.length,
  riskCounts: riskSummary.map((item) => [item.level, item.bucket.length]),
  guangfaRows: gfRows.length,
  ttfundRows: ttRows.length,
  ttfundDisplayCounts: groupBy(enriched, (row) => row["天天展示分层"]).map((item) => [item.key, item.count])
}, null, 2));
