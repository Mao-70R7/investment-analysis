const fs = require("fs");
const path = require("path");
const vm = require("vm");

const root = path.resolve(__dirname, "..");
const summaryPath = path.join(root, "basic_data", "data", "basic_summary.js");
const detailsDir = path.join(root, "basic_data", "data", "details");

function loadJsSummary(filePath) {
  const code = fs.readFileSync(filePath, "utf8");
  const ctx = { window: { __BASIC_DATA__: {} } };
  vm.createContext(ctx);
  vm.runInContext(code, ctx);
  return ctx.window.__BASIC_DATA__.summary;
}

function loadDetail(filePath) {
  const code = fs.readFileSync(filePath, "utf8");
  const ctx = { window: { __BASIC_DATA__: { details: {} } } };
  vm.createContext(ctx);
  vm.runInContext(code, ctx);
  return Object.values(ctx.window.__BASIC_DATA__.details)[0];
}

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

function hasHoldingData(row) {
  return ["权益基金权重", "债券基金权重", "货币基金权重", "混合基金权重", "指数基金权重"]
    .some((key) => nz(row[key]) > 0);
}

const riskBands = [
  { index: 0, level: "R0 现金/超低波", eqMax: 3, volMax: 0.8, mddMax: 1.2 },
  { index: 1, level: "R1 低波", eqMax: 8, volMax: 2.0, mddMax: 3.0 },
  { index: 2, level: "R2 稳健收益", eqMax: 18, volMax: 4.0, mddMax: 6.0 },
  { index: 3, level: "R3 均衡稳健", eqMax: 35, volMax: 7.5, mddMax: 12.0 },
  { index: 4, level: "R4 均衡成长", eqMax: 55, volMax: 11.0, mddMax: 20.0 },
  { index: 5, level: "R5 权益/进取", eqMax: Infinity, volMax: Infinity, mddMax: Infinity },
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

function riskResult(row) {
  if (!hasHoldingData(row)) {
    return {
      level: "D0 持仓缺失",
      eqLevel: "",
      volLevel: "",
      mddLevel: "",
      trigger: "持仓权重缺失",
      basis: "权益/债券/货币/混合/指数基金权重均为0或缺失，暂不进入正式风险同档比较。"
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
  const level = riskLabelMap.get(finalLevel) || "R5 权益/进取";
  return {
    level,
    eqLevel: riskLabelMap.get(eqLevel) || "R5 权益/进取",
    volLevel: riskLabelMap.get(volLevel) || "R5 权益/进取",
    mddLevel: riskLabelMap.get(mddLevel) || "R5 权益/进取",
    trigger: finalLevel === 0 ? "三项均在R0内" : triggers.join("+"),
    basis: `权益${nz(row["权益基金权重"]).toFixed(2)}%，波动${nz(row["波动率"]).toFixed(2)}%，最大回撤${nz(row["最大回撤"]).toFixed(2)}%；三项分别落档后取最高风险档。`
  };
}

function isTtfund(row) {
  return raw(row["渠道"]) === "天天基金/投顾" || raw(row["统一策略ID"]).startsWith("ttfund__");
}

function officialPerformancePresent(row) {
  return Boolean(raw(row["官方单位净值"]) || raw(row["官方累计收益"]) || raw(row["最新业绩日期"]) || raw(row["收益数据截至"]));
}

function isIssueProduct(row) {
  const text = `${raw(row["策略名称"])} ${raw(row["主可比池"])} ${raw(row["特殊标签"])} ${raw(row["策略类型"])}`;
  return /目标盈|小目标|止盈|达标|目标收益|期|尊享|期满|到期/.test(text);
}

function ttfundDisplay(row) {
  if (!isTtfund(row)) {
    return {
      current: "非天天",
      status: "非天天策略",
      lifecycle: "非天天策略",
      evidence: "渠道非天天基金/投顾"
    };
  }

  const status = raw(row["运作状态"]);
  const text = `${raw(row["策略名称"])} ${raw(row["投顾机构"])} ${status} ${raw(row["质检情况"])}`;
  const hasOfficial = officialPerformancePresent(row);
  const negativeSignal = /测试|内部|下架|隐藏|不可见|终止|暂停|停止|关闭/.test(text);

  if (negativeSignal || status === "已终止" || !hasOfficial) {
    const reasons = [];
    if (negativeSignal || status === "已终止") reasons.push("存在测试/终止/隐藏等非当前对客信号");
    if (!hasOfficial) reasons.push("缺少官方业绩字段");
    return {
      current: "否",
      status: "非对客展示/已终止或数据缺失",
      lifecycle: "已终止或不可展示",
      evidence: reasons.join("；") || "不满足当前对客展示规则"
    };
  }

  if (status === "开放窗口" || status === "listed") {
    return {
      current: "是",
      status: "当前对客展示",
      lifecycle: "当前开放/可展示",
      evidence: `运作状态=${status}，且有官方业绩字段`
    };
  }

  if (/^原始状态[12]$/.test(status)) {
    const issueText = isIssueProduct(row) ? "期次型/目标收益产品" : "期次型产品";
    return {
      current: "否",
      status: "历史期次-曾上架运作",
      lifecycle: "历史期次/到期或止盈结束",
      evidence: `运作状态=${status}；识别为${issueText}，属于曾经上架运作但当前非开放窗口的产品`
    };
  }

  if (status === "未披露") {
    return {
      current: "否",
      status: "非对客展示/未披露",
      lifecycle: "未披露/不可确认当前展示",
      evidence: "运作状态=未披露；本报告不将其视作当前对客展示"
    };
  }

  return {
    current: "否",
    status: "非对客展示/其他状态",
    lifecycle: "其他非开放状态",
    evidence: `运作状态=${status || "空"}，未命中当前对客展示规则`
  };
}

function businessTags(row) {
  return [row["市场地域"], row["主动被动"], row["特殊标签"], row["策略实现标签"]]
    .map(raw)
    .filter((value) => value && value !== "无")
    .join("｜") || "无";
}

function enrich(row) {
  const risk = riskResult(row);
  const display = ttfundDisplay(row);
  const business = raw(row["主可比池"]) || "未分类";
  row["测算风险等级"] = risk.level;
  row["风险基础分类"] = risk.level;
  row["基础分类"] = risk.level;
  row["权益风险档"] = risk.eqLevel;
  row["波动风险档"] = risk.volLevel;
  row["回撤风险档"] = risk.mddLevel;
  row["风险触发指标"] = risk.trigger;
  row["风险分类依据"] = risk.basis;
  row["原披露风险等级"] = raw(row["风险等级"]);
  row["业务主分类"] = business;
  row["业务分类"] = business;
  row["业务组合分类"] = `${risk.level}｜${business}`;
  row["业务分类标签"] = businessTags(row);
  row["天天当前对客展示"] = display.current;
  row["天天对客展示标记"] = display.current;
  row["天天展示状态"] = display.status;
  row["天天上架生命周期"] = display.lifecycle;
  row["天天展示判定依据"] = display.evidence;
  row.searchText = `${raw(row.searchText)} ${risk.level} ${business} ${row["业务组合分类"]} ${row["业务分类标签"]} ${display.current} ${display.status} ${display.lifecycle}`.trim();
  return row;
}

function countBy(rows, key) {
  const map = new Map();
  for (const row of rows) {
    const value = raw(row[key]) || "(空)";
    map.set(value, (map.get(value) || 0) + 1);
  }
  return [...map.entries()].sort((a, b) => {
    if (key === "测算风险等级") return riskOrder.indexOf(a[0]) - riskOrder.indexOf(b[0]);
    return b[1] - a[1] || a[0].localeCompare(b[0], "zh");
  }).map(([name, count]) => ({ 名称: name, 数量: count }));
}

function upsertField(rows, field, value) {
  if (!Array.isArray(rows)) return [{ 字段: field, 值: value }];
  const existing = rows.find((item) => item.字段 === field);
  if (existing) existing.值 = value;
  else rows.push({ 字段: field, 值: value });
  return rows;
}

function writeSummary(summary) {
  fs.writeFileSync(summaryPath, `window.__BASIC_DATA__.summary = ${JSON.stringify(summary)};\n`, "utf8");
}

function writeDetail(filePath, detail) {
  fs.writeFileSync(filePath, `window.__BASIC_DATA__.details[${JSON.stringify(detail.id)}] = ${JSON.stringify(detail)};\n`, "utf8");
}

const summary = loadJsSummary(summaryPath);
const rows = summary.strategies || [];
const byId = new Map(rows.map((row) => [row["统一策略ID"], row]));
rows.forEach(enrich);

summary.fieldDictionary = summary.fieldDictionary || {};
Object.assign(summary.fieldDictionary, {
  "测算风险等级": "系统按权益基金权重、波动率、最大回撤分别落档，并取三项中的最高风险档生成的基础分类。该字段互斥且归一，用于全市场、广发和业务机会分析的第一层分类。",
  "风险基础分类": "与测算风险等级一致，作为系统页面的基础分类字段。",
  "基础分类": "与测算风险等级一致，保留为通用分类入口字段。",
  "权益风险档": "仅按权益基金权重落入的风险档，用于解释测算风险等级的触发来源。",
  "波动风险档": "仅按年化波动率落入的风险档，用于解释测算风险等级的触发来源。",
  "回撤风险档": "仅按最大回撤落入的风险档，用于解释测算风险等级的触发来源。",
  "风险触发指标": "权益、波动、回撤三项中触发最终测算风险等级的指标；多个指标同时触发时用加号连接。",
  "风险分类依据": "测算风险等级使用的权益权重、波动率、最大回撤和取最高风险档规则说明。",
  "原披露风险等级": "渠道或平台原始披露的风险等级，保留用于合规展示和与测算风险等级对照。",
  "业务主分类": "沿用原主可比池，作为销售、投研、产品分析的业务维度，不再作为基础分类的唯一入口。",
  "业务分类": "与业务主分类一致，用于页面展示和外部分析口径。",
  "业务组合分类": "风险基础分类与业务主分类的组合，例如 R2 稳健收益｜固收增强型，用于营销货架和投研比较池拆解。",
  "业务分类标签": "市场地域、主动/被动、特殊标签、策略实现标签的组合，用于业务衍生筛选。",
  "天天当前对客展示": "天天基金/投顾策略当前是否可确认为对客展示；历史期次产品不计为当前对客展示。",
  "天天对客展示标记": "与天天当前对客展示一致，取值为是、否、非天天。",
  "天天展示状态": "将天天策略分为当前对客展示、历史期次-曾上架运作、非对客展示/未披露、非对客展示/已终止或数据缺失等状态。",
  "天天上架生命周期": "区分当前开放、历史期次/到期或止盈结束、未披露/不可确认当前展示、已终止或不可展示。",
  "天天展示判定依据": "天天展示状态的判定原因，基于运作状态、官方业绩字段和测试/终止等非对客信号。"
});

summary.riskClassificationStats = {
  规则: "权益、波动率、最大回撤分别落档，最终取最高风险档；持仓缺失为D0。",
  风险等级分布: countBy(rows, "测算风险等级"),
  广发风险等级分布: countBy(rows.filter((row) => /广发基金|广发投顾/.test(`${raw(row["投顾机构"])} ${raw(row["渠道"])}`)), "测算风险等级"),
  天天展示状态分布: countBy(rows.filter(isTtfund), "天天展示状态")
};

const detailFiles = fs.readdirSync(detailsDir).filter((file) => file.endsWith(".js"));
let updatedDetails = 0;
for (const file of detailFiles) {
  const filePath = path.join(detailsDir, file);
  const detail = loadDetail(filePath);
  const row = byId.get(detail.id);
  if (!row) continue;
  detail.summary = detail.summary || {};
  for (const field of [
    "测算风险等级",
    "风险基础分类",
    "基础分类",
    "权益风险档",
    "波动风险档",
    "回撤风险档",
    "风险触发指标",
    "风险分类依据",
    "原披露风险等级",
    "业务主分类",
    "业务分类",
    "业务组合分类",
    "业务分类标签",
    "天天当前对客展示",
    "天天对客展示标记",
    "天天展示状态",
    "天天上架生命周期",
    "天天展示判定依据"
  ]) {
    detail.summary[field] = row[field];
    detail.classificationFields = upsertField(detail.classificationFields, field, row[field]);
  }
  writeDetail(filePath, detail);
  updatedDetails += 1;
}

writeSummary(summary);

console.log(JSON.stringify({
  summaryPath,
  detailFiles: detailFiles.length,
  updatedDetails,
  strategies: rows.length,
  riskCounts: summary.riskClassificationStats.风险等级分布,
  ttfundDisplayCounts: summary.riskClassificationStats.天天展示状态分布
}, null, 2));
