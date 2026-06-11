const fs = require("fs");
const path = require("path");
const vm = require("vm");

const root = path.resolve(__dirname, "..");
const summaryPath = path.join(root, "basic_data", "data", "basic_summary.js");
const holdingSnapshotPackPath = path.join(root, "basic_data", "data", "holding_snapshot_pack.json");
const rebalanceFundCategoryPackPath = path.join(root, "basic_data", "data", "rebalance_fund_category_pack.json");
const fundDetailPackPath = path.join(root, "basic_data", "data", "fund_detail_pack.js");
const detailsDir = path.join(root, "basic_data", "data", "details");

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

function writeHoldingSnapshotPack(pack) {
  writeFileUtf8(holdingSnapshotPackPath, JSON.stringify(pack));
}

function writeRebalanceFundCategoryPack(pack) {
  writeFileUtf8(rebalanceFundCategoryPackPath, JSON.stringify(pack));
}

function writeFundDetailPack(pack) {
  writeFileUtf8(fundDetailPackPath, `window.__BASIC_DATA__ = window.__BASIC_DATA__ || {}; window.__BASIC_DATA__.fundDetailPack = ${JSON.stringify(pack)};\n`);
}

function isGuangfaStrategy(row) {
  return /广发基金|广发投顾/.test(`${raw(row["投顾机构"])} ${raw(row["渠道"])}`);
}

function normalizeRisk(row) {
  return raw(row["测算风险等级"]) || raw(row["风险基础分类"]) || raw(row["风险等级"]) || "D0 持仓缺失";
}

function normalizeBusiness(row) {
  return raw(row["业务分类"]) || raw(row["业务主分类"]) || raw(row["主可比池"]) || "未分类";
}

function isTargetProfitProduct(text) {
  const explicitTarget = /目标盈|小目标|目标收益|收益目标|达标|止盈目标|目标.{0,8}止盈|止盈.{0,8}目标|止盈线|达到.{0,12}(目标|止盈)/.test(text);
  const lifecycleExit = /(到期|运作期|封闭期|赎回|发售|期次|第[零一二三四五六七八九十百千万\d]+期|自动终止|到期结束)/.test(text);
  return /目标盈|小目标/.test(text) || (explicitTarget && lifecycleExit);
}

function isSignalStrategy(text) {
  const normalized = raw(text).replace(/\s+/g, "");
  if (/超级定投家|指数100份/.test(normalized)) return true;
  if (/低位加倍投入|手把手带投/.test(normalized)) return true;
  return /信号/.test(normalized) && /买入|卖出|止盈|交易|调仓|发车|跟车|加倍投入|带投/.test(normalized);
}

function canonicalBusiness(row) {
  const original = normalizeBusiness(row);
  const text = `${raw(row["策略名称"])} ${raw(row["策略概念"])} ${raw(row["策略描述"])} ${raw(row["特殊标签"])} ${raw(row["披露策略类型"])} ${raw(row["补充识别文本"])}`;
  const equity = nz(row["权益基金权重"]);
  const bond = nz(row["债券基金权重"]);
  const cash = nz(row["货币基金权重"]);
  const qdii = nz(row["QDII权重"]);
  const index = nz(row["指数基金权重"]);
  const mixed = nz(row["混合基金权重"]);
  const bondCash = bond + cash;
  if (raw(row["风险等级"]) === "D0 持仓缺失" || !["权益基金权重", "债券基金权重", "货币基金权重", "混合基金权重", "指数基金权重"].some((key) => nz(row[key]) > 0)) {
    return { business: original || "未分类", basis: "持仓权重缺失，保留原业务分类，不进入正式展示比较。" };
  }
  if (/目标日期|养老|生命周期/.test(text)) {
    return { business: "目标日期/养老型", basis: "名称或标签包含养老/目标日期机制，单列为长期养老场景。" };
  }
  if (isSignalStrategy(text)) {
    return { business: "信号类策略", basis: "名称、策略介绍或合同/协议类说明明确包含指数100份、超级定投家，或信号与买入/卖出/止盈/交易/跟车/发车等执行机制，单列为信号类策略。" };
  }
  if (isTargetProfitProduct(text)) {
    return { business: "目标盈系列产品", basis: "名称或介绍包含目标收益、止盈、达标、小目标、到期或赎回安排，按目标达标运营池分类；仅含“尊享”或普通期次但无目标收益/止盈机制的组合不归入目标盈。" };
  }
  if (cash >= 80) {
    return { business: "现金管理型", basis: `实际货币基金权重${cash.toFixed(2)}%，达到80%现金管理阈值。` };
  }
  if (bondCash >= 90 && equity < 10) {
    return { business: "纯债/短债型", basis: `实际债券+货币权重${bondCash.toFixed(2)}%，权益${equity.toFixed(2)}%，按纯债/短债货架分类；披露策略类型不改变该判断。` };
  }
  if (qdii >= 20 || /海外|全球|QDII|港股|美股|纳斯达克|标普|黄金|商品/.test(text)) {
    return { business: "海外/全球型", basis: `QDII/海外暴露${qdii.toFixed(2)}%，或名称标签含海外/全球/商品关键词。` };
  }
  if (/医药|医疗|消费|新能源|半导体|军工|科技|AI|人工智能|红利|低碳|高端制造|港股互联网/.test(text)) {
    return { business: "主题/行业型", basis: "名称或标签包含明确主题/行业暴露，单列用于营销与投研跟踪。" };
  }
  if (equity >= 40 || index >= 45) {
    return { business: "偏股配置型", basis: `权益${equity.toFixed(2)}%、指数${index.toFixed(2)}%，偏权益资产占主导。` };
  }
  if (bondCash >= 70 && equity < 40) {
    return { business: "固收增强型", basis: `债券+货币权重${bondCash.toFixed(2)}%、权益${equity.toFixed(2)}%，符合固收增强风险收益特征。` };
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
  if (raw(row["风险等级"]) === "D0 持仓缺失") {
    return { type: "持仓缺失/不入池", subType: "", basis: "风险等级为D0或持仓权重缺失，不能稳定映射研报可比池；仅用于数据补齐清单，不进入市场总览、仓位分析和调仓主图。" };
  }
  const hasMultiAsset = qdii >= 20 || /多资产|多元|全球|海外|QDII|黄金|商品|原油|REIT|另类|全天候/.test(text);
  const hasTheme = /行业|主题|医药|医疗|消费|新能源|半导体|军工|科技|AI|人工智能|红利|低碳|高端制造|港股互联网|食品饮料|电子|通信|煤炭|电力设备/.test(text);
  const isIndexDriven = index >= 45 || /指数|ETF|联接|增强|宽基|沪深|中证|创业板|科创|标普|纳斯达克|恒生/.test(text);
  const isRotation = /轮动|赛道|趋势|择时|风格|行业切换|主题切换/.test(text);
  if (equity >= 70) {
    let subType = "主动优选";
    if (qdii >= 20 || /QDII|海外|全球|美股|港股|纳斯达克|标普|恒生/.test(text)) subType = "QDII型";
    else if (hasTheme) subType = "行业主题型";
    else if (isRotation) subType = "行业轮动";
    else if (isIndexDriven) subType = "指数驱动";
    return { type: "股票型", subType, basis: `权益${equity.toFixed(2)}%，按研报股票型可比池；子类=${subType}。` };
  }
  if (hasMultiAsset && qdii >= 10) {
    return { type: "多元配置型", subType: "", basis: `QDII/海外${qdii.toFixed(2)}%，或名称/标签含多资产、全球、商品等多元配置特征。` };
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

function updateStrategyRow(row, supplementalText = "") {
  const disclosedRisk = raw(row["披露风险等级"]) || raw(row["原披露风险等级"]) || raw(row["风险等级"]);
  const measuredRisk = normalizeRisk(row);
  const disclosedType = raw(row["披露策略类型"]) || raw(row["策略类型"]);
  const canonical = canonicalBusiness({ ...row, 风险等级: measuredRisk, 披露策略类型: disclosedType, 补充识别文本: supplementalText });
  const business = canonical.business;
  const report = reportClassification({ ...row, 风险等级: measuredRisk, 业务分类: business, 披露策略类型: disclosedType, 补充识别文本: supplementalText });

  row["披露风险等级"] = disclosedRisk || "未披露";
  row["风险等级"] = measuredRisk;
  row["披露策略类型"] = disclosedType || "未披露";
  row["业务分类"] = business;
  row["研报产品类型"] = report.type;
  row["研报股票子类型"] = report.subType || "";
  row["研报分类依据"] = report.basis;
  row["业务组合分类"] = `${measuredRisk}｜${business}`;
  row["业务分类依据"] = canonical.basis;
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
  for (const [field, value] of Object.entries(row)) {
    row[field] = cleanTerminology(value);
  }
  return row;
}

function renameFieldItems(rows) {
  if (!Array.isArray(rows)) return rows;
  for (const item of rows) {
    if (item.字段 === "策略类型") item.字段 = "披露策略类型";
    else if (item.字段 === "原披露风险等级") item.字段 = "披露风险等级";
    else if (item.字段 === "测算风险等级" || item.字段 === "风险基础分类") item.字段 = "风险等级";
    else if (item.字段 === "业务主分类") item.字段 = "业务分类";
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
  if (/货币|现金|现金管理|活期|天天红|活钱|日鑫|日日鑫|日日薪|日日丰|聚财宝|安鑫宝/.test(directText)) return "货币型";
  if (/白银|原油|油气|黄金|商品|期货/.test(directText)) return "商品型";
  if (domesticDebt && !overseasDebt) return "债券型";
  if (/QDII|海外|全球|亚洲|美元|纳斯达克|纳指|标普|恒生|港股|日经|越南|印度|德国|美国/.test(directText) || (useContext && /QDII|海外|全球|亚洲|美元|纳斯达克|纳指|标普|恒生|港股|日经|越南|印度|德国|美国/.test(contextText))) return "QDII/海外";
  if (/ETF|指数|联接|沪深|中证|创业板|科创|红利|宽基|增强|LOF|MSCI|国证|大数据100|食品饮料|酒|中华交易服务|高股息|港股通/.test(directText) || (useContext && /ETF|指数|联接|沪深|中证|创业板|科创|红利|宽基|增强|LOF|MSCI|国证|大数据100|食品饮料|酒|中华交易服务|高股息|港股通/.test(contextText))) return "指数型";
  if (/债|短债|中短债|纯债|固收|票息|添利|永利|利率|信用|转债|强化收益|稳祥|稳鸿|鸿利|双元|和元|丰元|中高等级|双轮动/.test(directText) || (useContext && /债|短债|中短债|纯债|固收|票息|添利|永利|利率|信用|转债|强化收益|稳祥|稳鸿|鸿利|双元|和元|丰元|中高等级|双轮动/.test(contextText))) return "债券型";
  if (/股票|权益|成长|价值|消费|医药|新能源|科技|半导体|军工|AI|人工智能|先进制造|港股互联网|创新动力|优加生活|优享生活/.test(allText)) return "股票型";
  if (/混合|灵活|配置|偏股|多资产|目标日期|养老|精选|优选|平衡|量化|多元|全天候|牛基|价值精选|股债平衡|幸福增长|智享自由|进取|利安|荣光|信睿|新兴蓝筹|新鑫先锋|新收益|沪港深裕鑫|稳健增长|动态策略|金鹏蓝筹|利鑫|招泰|行业景气|荣尊|安裕回报|宏观策略/.test(allText)) return "混合型";
  return existingText && !/未披露|未识别|其他/.test(existingText) ? existingText : "混合型";
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

  if (/货币|现金|现金管理|活期|天天红|活钱|日鑫|日日鑫|日日薪|日日丰|聚财宝|安鑫宝/.test(directText)) {
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
  const primaryReportAsset = primaryExposure(normalizedAssetExposure, "待核验");
  const primaryReportAIndustry = primaryExposure(industryExposure, "");
  const primaryEquityTheme = primaryExposure(themeExposure, ((normalizedAssetExposure["A股"] || 0) > 0 ? "宽基/主动权益" : ""));
  const primaryEquityGroup = primaryExposure(groupExposure, primaryEquityTheme ? inferEquityIndustryGroup(primaryEquityTheme) : "");
  const primaryIndustryTheme = primaryReportAIndustry ? (industryMeta(primaryReportAIndustry).theme || primaryReportAIndustry) : (reportAssetThemeMap[primaryReportAsset]?.theme || inferIndustryTheme(fundName || text, fundType));
  const primaryIndustryGroup = primaryReportAIndustry ? (industryMeta(primaryReportAIndustry).group || inferIndustryGroup(primaryIndustryTheme, fundType)) : (reportAssetThemeMap[primaryReportAsset]?.group || inferIndustryGroup(primaryIndustryTheme, fundType));

  return {
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
    themeExposure,
    groupExposure,
    confidence,
    basis: evidence.join("；") || "按基金名称关键词和持仓披露分类推断"
  };
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
    start = normalized.find((point) => {
      const t = dateMs(point.日期);
      return t !== null && t >= ytdTime && point.日期 !== latest.日期;
    }) || null;
    if (start && (dateMs(start.日期) - ytdTime) / 86400000 > (options.toleranceDays || 45)) start = null;
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
  for (const [field, value] of Object.entries(metrics)) {
    row[field] = value === null ? null : Number(value.toFixed(4));
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

const summary = loadSummary();
const supplementalTextById = buildSupplementalTextById();
const processedStrategies = (summary.strategies || []).map((row) => updateStrategyRow(row, supplementalTextById.get(row.统一策略ID) || ""));
const sourceRawStrategyCount = Math.max(Number(summary.rawStrategyCount) || 0, processedStrategies.length);

function isDisplayableStrategy(row) {
  return !!row
    && row.数据完整性 === "完整"
    && row.风险等级 !== "D0 持仓缺失"
    && row.研报产品类型 !== "持仓缺失/不入池";
}

const filteredOutStrategies = processedStrategies.filter((row) => !isDisplayableStrategy(row));
summary.rawStrategyCount = sourceRawStrategyCount;
summary.filteredOutStrategyCount = filteredOutStrategies.length;
summary.strategies = processedStrategies.filter(isDisplayableStrategy);
const byId = new Map(summary.strategies.map((row) => [row.统一策略ID, row]));

summary.fieldDictionary = summary.fieldDictionary || {};
delete summary.fieldDictionary["主可比池"];
delete summary.fieldDictionary["策略类型"];
delete summary.fieldDictionary["测算风险等级"];
delete summary.fieldDictionary["原披露风险等级"];
delete summary.fieldDictionary["业务主分类"];
delete summary.fieldDictionary["风险基础分类"];
delete summary.fieldDictionary["基础分类"];
delete summary.fieldDictionary["基金" + "调整摘要"];
Object.assign(summary.fieldDictionary, {
  "筛选口径": "页面顶部筛选条件同时作用于市场总览、仓位分析、调仓分析的所有图表和表格；持仓缺失策略不进入洞察展示；目标盈系列在市场总览中按同系列多期合并。",
  "时间区间": "用于区间收益、资产分布时间变化和调仓分析的统一观察窗口；近1周、近1月、近3月、近1年等收益指标按对应披露收益字段展示，调仓事件按具体调仓日期过滤。期初期末热力图按每只策略在区间起止目标日期附近的最近可用持仓快照计算，避免把非工作日或未披露日误作0仓位。",
  "对客范围": "全部策略包含当前分析库内全部可用策略；只看对客策略会剔除天天投顾明确标记为当前不对客展示、非对客、隐藏或未展示的策略。非天天投顾策略没有相反标记时默认保留。",
  "策略范围": "按投顾机构归属区分全部策略、仅看广发策略和仅看非广发策略；切换后市场总览、仓位分析和调仓分析中的图表、表格和展开明细同步过滤。",
  "风险等级": "系统测算主风险等级。先分别计算权益暴露档、波动档、回撤档：权益暴露档来自当前持仓权益基金权重；波动档来自策略区间波动率；回撤档来自策略最大回撤。最终风险等级取三项中最高风险档，即风险等级=max(权益暴露档、波动档、回撤档)。页面主分类使用该字段，不使用披露风险等级。",
  "披露风险等级": "渠道或平台原始披露的风险等级，仅用于与系统测算风险等级对照。",
  "披露策略类型": "渠道或平台原始披露的策略类型；未披露时显示未披露。",
  "费率状态": "根据策略基础资料中是否披露投顾费率生成。显示缺失时，说明该策略不能进入费率竞争力、渠道定价或让利空间分析。",
  "年化投顾费率": "策略基础资料披露的年化投顾服务费率；缺失时显示未披露，页面不做推算，避免用不完整费率得出经营结论。",
  "投资经理": "策略基础资料披露的投资经理或管理人姓名；当前字段缺失时不能做投资经理排名、经理画像或经理业绩归因。",
  "业务分类": "销售、产品和投研使用的互斥业务场景。判定顺序：D0持仓缺失保留原分类但不进主图；名称/说明含目标日期、养老、生命周期归目标日期/养老型；明确信号执行机制归信号类策略；明确目标收益/止盈/到期赎回机制归目标盈系列；货币权重>=80%归现金管理型；债券+货币>=90%且权益<10%归纯债/短债型；QDII>=20%或名称含海外/全球/商品等归海外/全球型；明确行业主题归主题/行业型；权益>=40%或指数>=45%归偏股配置型；债券+货币>=70%且权益<40%归固收增强型；混合>=20%或权益>=15%或QDII>=5%归多资产配置型；均未命中则保留原分类。",
  "业务分类依据": "展示该策略业务分类命中的具体证据。文本类证据来自策略名称、策略介绍、合同/协议、服务条款、特殊标签；资产类证据来自当前持仓中的权益、债券、货币、QDII、指数、混合权重。例如“货币权重82.30%，达到80%现金管理阈值”。",
  "业务组合分类": "风险等级与业务分类的组合，用于营销机会和投研比较池拆解。",
  "研报产品类型": "调仓分析主可比池，互斥归类。公式/规则：风险等级D0进入“持仓缺失/不入池”；权益基金权重>=70%为股票型；QDII权重>=10%且名称/说明含多资产、全球、海外、商品、REIT等为多元配置型；权益<1%且债券+货币>=80%为纯债型；权益<=20%为固收+型；权益<70%为股债混合型；剩余按多元配置或股债混合兜底。该字段用于调仓分析，避免纯债、固收+、股票型混在一起比较。",
  "研报股票子类型": "只对研报产品类型=股票型继续拆分。规则：QDII权重>=20%或名称/说明命中QDII、海外、全球、美股、港股、纳斯达克、标普、恒生，归QDII型；否则命中行业/主题关键词归行业主题型；命中轮动、赛道、趋势、择时、风格/主题切换归行业轮动；指数基金权重>=45%或名称/说明命中指数、ETF、联接、增强、宽基等归指数驱动；剩余归主动优选。",
  "研报分类依据": "展示研报产品类型和股票子类型命中的可核验证据，主要包含权益权重、债券+货币权重、QDII权重、指数权重，以及策略名称/说明中的全球、主题、轮动、ETF等关键词。例如“权益76.20%，按研报股票型可比池；子类=指数驱动”。",
  "研报大类资产": "调仓和仓位分析使用的资产暴露口径。先给每只基金识别一个展示主类型，再按规则拆分到A股、港股、美股、债券、黄金、货币及现金、海外债券、新兴市场、其他发达市场、海外REIT、其他商品。公式：分类资产权重=sum(基金持仓权重*该基金该资产暴露比例)。例如固收+/偏债混合默认按债券70%、A股25%、现金5%进入大类资产图；纯债默认债券95%、现金5%；宽基/主动权益默认A股95%、现金5%。",
  "研报A股行业": "A股行业主题暴露口径。只有A股暴露且基金名称、主题或指数名称能识别申万一级行业时才进入该字段；宽基指数、主动权益、均衡混合基金在没有底层股票持仓/基准行业权重时不强行拆行业。公式：行业权重=sum(基金持仓权重*A股暴露比例*行业暴露比例)。多主题基金按规则拆分，例如TMT按电子25%、计算机25%、通信20%、传媒30%，金融科技按计算机70%、非银金融30%。",
  "调仓时间模式": "月度报告模式默认取最近一个有调仓记录的完整月份，按研报月报逻辑复盘；自选区间模式沿用顶部时间区间，近1周、近1月、近3月、近1年等控件同步过滤所有调仓图表和表格。",
  "报告月份": "月度报告模式下使用的观察月份。系统从当前筛选范围内有调仓日期的月份中，优先选择早于当前自然月的最近完整月份；手动切换后，调仓事件、基金月度汇总和策略级资产变化都按该月份过滤。",
  "市场地域": "根据当前持仓中海外或QDII基金暴露、策略名称和业绩基准识别国内、海外/全球或混合地域，用于市场结构分析。",
  "排名": "在当前筛选和排序条件下重新生成的序号；切换筛选条件或点击表头排序后同步变化。",
  "基金名称": "底层持仓基金的展示名称；同一基金按基金代码和名称合并统计。",
  "基金类型": "底层基金展示主类型，按基金代码字典、平台持仓披露分类、基金名称关键词和产品形态归并。细分为货币型、短债/中短债、纯债/信用债、债券指数、可转债、固收+/偏债混合、混合偏股、宽基指数、行业/主题指数、行业/主题主动、主动权益、沪港深权益、港股指数/权益、美股指数/权益、其他海外权益、海外债券、黄金/贵金属、商品/能源、REIT、FOF/养老等。该字段用于列表展示和基金公司拆解；资产/行业图使用资产暴露和行业暴露进一步拆分。",
  "基金分类依据": "该基金类型的命中证据。优先使用基金代码/名称字典；其次使用平台持仓披露的资产类型或分组；再按基金名称关键词识别货币、短债、纯债、可转债、指数、行业主题、港股、美股、QDII、黄金、商品、REIT、FOF等。展示为具体证据串，方便核验某只基金为什么归入该类型。",
  "基金分类置信度": "基金分类可靠性等级。A=命中基金代码或名称字典；B=有平台持仓分类且名称规则可解释；C=主要依赖基金名称关键词；D=缺少基金标准档案和平台分类，仅能兜底，需补充天天基金、基金合同、招募说明书或业绩比较基准等标准披露信息。",
  "资产暴露": "该基金进入研报大类资产图的拆分比例。公式：单只基金各资产暴露比例合计100%。示例：短债/中短债=债券85%+现金15%；固收+/偏债混合=债券70%+A股25%+现金5%；沪港深权益=A股55%+港股40%+现金5%；行业主题权益=A股95%+现金5%。",
  "行业暴露": "该基金进入A股行业图的拆分比例，只统计可识别的A股行业主题。公式：单只基金行业暴露比例在可识别行业内合计100%，再乘以该基金A股资产暴露进入图表。宽基、主动权益和均衡混合缺少行业主题时为空，不强行拆分。",
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
  "广发Top3平均收益": "同策略类型内，广发基金投顾按所选收益指标排序前3只策略的平均收益；少于3只时按已有可计算策略平均。",
  "广发Top5平均收益": "同策略类型内，广发基金投顾按所选收益指标排序前5只策略的平均收益；少于5只时按已有可计算策略平均。",
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
  "调仓后收益贡献": "调仓后该基金持仓权重与后续收益表现估算得到的贡献，用于辅助观察调仓效果。"
});
for (const [field, text] of Object.entries(summary.fieldDictionary)) {
  if (typeof text !== "string") continue;
  summary.fieldDictionary[field] = cleanTerminology(text);
}

summary.rebalanceEvents = (summary.rebalanceEvents || []).map((event) => {
  const base = byId.get(event.统一策略ID) || {};
  return {
    ...event,
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
});

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
for (const event of summary.rebalanceEvents || []) {
  rebalanceEventByKey.set(`${raw(event.统一策略ID)}｜${raw(event.调仓日期)}`, event);
}
const rebalanceFundRows = [];
const rebalanceFundCategoryRows = [];
const rebalanceFundMonthlyAgg = new Map();
const directionAgg = new Map();
const gfFundOpportunityAgg = new Map();
const strategyAssetChangeAgg = new Map();
let detailCount = 0;
let holdingRows = 0;

for (const file of fs.readdirSync(detailsDir).filter((name) => name.endsWith(".js"))) {
  const filePath = path.join(detailsDir, file);
  const detail = loadDetail(filePath);
  const row = byId.get(detail.id);
  if (!row) continue;
  detailCount += 1;

  detail.summary = updateStrategyRow({ ...(detail.summary || {}), ...row });
  detail.classification = detail.classification || {};
  detail.classification["风险等级"] = row.风险等级;
  detail.classification["业务分类"] = row.业务分类;
  detail.classification["业务组合分类"] = row.业务组合分类;
  detail.classification["业务分类依据"] = row.业务分类依据;
  detail.classification["研报产品类型"] = row.研报产品类型;
  detail.classification["研报股票子类型"] = row.研报股票子类型;
  detail.classification["研报分类依据"] = row.研报分类依据;
  detail.classification["披露风险等级"] = row.披露风险等级;
  detail.classification["披露策略类型"] = row.披露策略类型;
  for (const key of ["主可比池", "测算风险等级", "风险基础分类", "基础分类", "业务主分类", "原披露风险等级", "策略类型"]) {
    delete detail.classification[key];
  }
  for (const [field, value] of Object.entries(detail.classification)) {
    detail.classification[field] = cleanTerminology(value);
  }
  detail.profileFields = dedupeFieldItems(renameProfileFieldItems(renameFieldItems(detail.profileFields || [])));
  detail.performanceFields = dedupeFieldItems(renameFieldItems(detail.performanceFields || []));
  detail.classificationFields = dedupeFieldItems(renameFieldItems(detail.classificationFields || []));

  detail.profileFields = upsertField(detail.profileFields, "披露风险等级", row.披露风险等级);
  detail.profileFields = upsertField(detail.profileFields, "披露策略类型", row.披露策略类型);
  detail.classificationFields = detail.classificationFields.filter((item) => !["主可比池", "测算风险等级", "风险基础分类", "基础分类", "业务主分类"].includes(item.字段));
  for (const [field, value] of [
    ["风险等级", row.风险等级],
    ["业务分类", row.业务分类],
    ["业务组合分类", row.业务组合分类],
    ["业务分类依据", row.业务分类依据],
    ["业务分类标签", row.业务分类标签],
    ["研报产品类型", row.研报产品类型],
    ["研报股票子类型", row.研报股票子类型],
    ["研报分类依据", row.研报分类依据],
    ["天天展示状态", row.天天展示状态],
    ["天天当前对客展示", row.天天当前对客展示],
    ["天天展示判定依据", row.天天展示判定依据],
  ]) {
    detail.classificationFields = upsertField(detail.classificationFields, field, value || "未披露");
  }
  applyDisclosureCurveQuality(detail, row);
  const includeInDisplay = isDisplayableStrategy(row);

  const isGf = isGuangfaStrategy(row);
  const region = row.市场地域 || "未分类";
  const business = row.业务分类 || "未分类";
  const strategyWeightBase = 1;
  const snapshots = detail.positionSnapshots || [];
  if (!includeInDisplay) {
    writeDetail(filePath, detail);
    continue;
  }
  for (const snap of snapshots) {
    const snapMonth = monthOf(snap.日期);
    const snapshotCategoryAgg = new Map();
    for (const holding of snap.holdings || []) {
      const fundCode = raw(holding.基金代码);
      const rawFundName = raw(holding.基金名称);
      const assetContext = [rawFundName, business, row.策略名称, snap.标题, holding.资产类型, holding.分组].map(raw).filter(Boolean).join(" ");
      const fundClass = classifyFundStandard(fundCode, rawFundName, holding, assetContext);
      const fundName = fundClass.fundName;
      const assetType = fundClass.fundType;
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
      const industryExposure = exposureText(fundClass.industryExposure);
      const isGfFund = isGuangfaFund(fundName) || /广发基金/.test(company);
      if (snap.类型 === "历史调仓" && row.风险等级 !== "D0 持仓缺失") {
        const event = rebalanceEventByKey.get(`${raw(row.统一策略ID)}｜${raw(snap.日期)}`) || {};
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
          基金分类依据: fundClassBasis,
          基金分类置信度: fundClassConfidence,
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
          基金分类依据: fundClassBasis,
          基金分类置信度: fundClassConfidence,
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
          基金分类依据: fundClassBasis,
          基金分类置信度: fundClassConfidence,
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
          基金分类依据: fundClassBasis,
          基金分类置信度: fundClassConfidence,
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
    holdingSnapshotCategoryRows.push(...snapshotCategoryAgg.values());
  }

  writeDetail(filePath, detail);
}

const qualityFilteredOutStrategies = summary.strategies.filter((row) => !isDisplayableStrategy(row));
if (qualityFilteredOutStrategies.length) {
  for (const row of qualityFilteredOutStrategies) byId.delete(row.统一策略ID);
  summary.strategies = summary.strategies.filter(isDisplayableStrategy);
  summary.qualityFilteredOutStrategyCount = qualityFilteredOutStrategies.length;
  summary.rebalanceEvents = (summary.rebalanceEvents || []).filter((event) => byId.has(event.统一策略ID));
}
summary.displayStrategyCount = summary.strategies.length;
summary.filteredOutStrategyCount = Math.max(0, summary.rawStrategyCount - summary.displayStrategyCount);

const marketRows = summary.strategies.filter(isDisplayableStrategy);
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
    研报产品类型: row.研报产品类型,
    研报股票子类型: row.研报股票子类型,
    研报分类依据: row.研报分类依据,
    市场地域: row.市场地域,
    主动被动: row.主动被动,
    披露风险等级: row.披露风险等级,
    披露策略类型: row.披露策略类型,
    数据完整性: row.数据完整性,
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
  const compactRows = rows.map((row) => [
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
  const compactRows = rows.map((row) => [
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
    "基金代码", "基金名称", "基金公司", "基金类型", "基金分类依据", "基金分类置信度",
    "资产暴露", "行业暴露", "行业主题", "行业大类", "权益行业主题", "权益行业大类",
    "研报大类资产", "研报A股行业", "广发基金产品", "总权重", "广发策略权重", "非广发策略权重",
    "持仓策略数", "中位权重", "区间收益率", "增持策略数", "减持策略数"
  ];
  const holdingFields = [
    "基金索引", "统一策略ID", "策略名称", "投顾机构", "渠道", "是否广发策略", "风险等级",
    "业务分类", "研报产品类型", "市场地域", "天天当前对客展示", "初持仓比例", "期末持仓比例",
    "权重变化", "区间收益率", "近1年"
  ];
  const monthlyFields = ["基金索引", "月份", "净增配", "中位净增配", "加仓权重", "减仓权重", "调仓策略数"];
  const fundIndex = new Map();
  const fundKey = (code, name) => `${raw(code)}｜${raw(name)}`;
  const compactFund = (row) => fundFields.map((field) => row[field] ?? "");
  const funds = (fundRows || []).map((row, index) => {
    fundIndex.set(fundKey(row.基金代码, row.基金名称), index);
    return compactFund(row);
  });
  const ensureFundIndex = (row) => {
    const key = fundKey(row.基金代码, row.基金名称);
    if (fundIndex.has(key)) return fundIndex.get(key);
    const index = funds.length;
    fundIndex.set(key, index);
    funds.push(compactFund(row));
    return index;
  };
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
  return { version: 1, fundFields, holdingFields, monthlyFields, funds, holdings, monthly };
}

const currentFundRows = finalizeWeightAgg([...fundAgg.values()]).sort((a, b) => b.总权重 - a.总权重);
const currentCompanyRows = finalizeWeightAgg([...companyAgg.values()]).sort((a, b) => b.总权重 - a.总权重);
const rebalanceFundMonthlyRowsFinal = finalizeRebalanceFundMonthlyRows();
const holdingSnapshotPack = compactHoldingSnapshotRows(holdingSnapshotCategoryRows.sort((a, b) => String(a.快照日期 || "").localeCompare(String(b.快照日期 || ""))));
writeHoldingSnapshotPack(holdingSnapshotPack);
const rebalanceFundCategoryPack = compactRebalanceFundCategoryRows(rebalanceFundCategoryRows.sort((a, b) => String(b.调仓日期 || "").localeCompare(String(a.调仓日期 || ""))));
writeRebalanceFundCategoryPack(rebalanceFundCategoryPack);
writeFundDetailPack(compactFundDetailPack(currentFundRows, currentHoldingStrategyRows, rebalanceFundMonthlyRowsFinal));

summary.insightData = {
  生成时间: new Date().toISOString(),
  指标说明: "风险等级为系统测算主分类；业务分类为销售、投研和产品分析维度。",
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
  当前持仓策略基金明细: currentHoldingStrategyRows.sort((a, b) => b.期末持仓比例 - a.期末持仓比例),
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
  调仓基金月度汇总: rebalanceFundMonthlyRowsFinal,
  策略资产变化明细: finalizeStrategyAssetChangeRows(),
  调仓方向汇总: finalizeDirectionRows(),
  机构调仓能力: institutionCapabilityRows(),
  广发基金调仓机会: finalizeGfFundOpportunityRows(currentFundRows),
  广发策略数: gfRows.length,
  非广发策略数: nonGfRows.length,
  持仓明细行数: holdingRows,
  详情文件数: detailCount
};

writeSummary(summary);

console.log(JSON.stringify({
  strategies: summary.strategies.length,
  displayStrategies: marketRows.length,
  incompleteStrategies: summary.strategies.filter((row) => row.数据完整性 !== "完整").length,
  d0OrHoldingMissingStrategies: summary.strategies.filter((row) => row.风险等级 === "D0 持仓缺失" || row.研报产品类型 === "持仓缺失/不入池").length,
  detailCount,
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
