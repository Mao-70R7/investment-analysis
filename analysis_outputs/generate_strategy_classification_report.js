const fs = require("fs");
const path = require("path");
const vm = require("vm");

const root = path.resolve(__dirname, "..");
const sourcePath = path.join(root, "basic_data", "data", "basic_summary.js");
const outputPath = path.join(__dirname, "strategy_classification_refined_report.html");

const code = fs.readFileSync(sourcePath, "utf8");
const ctx = { window: { __BASIC_DATA__: {} } };
vm.createContext(ctx);
vm.runInContext(code, ctx);

const summary = ctx.window.__BASIC_DATA__.summary;
const strategies = summary.strategies || [];

function text(value) {
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

function fmt(value, digits = 1) {
  const n = num(value);
  if (n == null) return "-";
  return n.toFixed(digits);
}

function pct(value, digits = 1) {
  const n = num(value);
  if (n == null) return "-";
  return `${n.toFixed(digits)}%`;
}

function escapeHtml(value) {
  return text(value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
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
    const bucket = map.get(key) || [];
    bucket.push(row);
    map.set(key, bucket);
  }
  return [...map.entries()].map(([key, bucket]) => ({ key, rows: bucket, count: bucket.length }));
}

function isGuangfa(row) {
  return /广发基金|广发投顾/.test(`${text(row["投顾机构"])} ${text(row["渠道"])}`);
}

function hasHoldingData(row) {
  return ["权益基金权重", "债券基金权重", "货币基金权重", "混合基金权重", "QDII权重", "指数基金权重"]
    .some((key) => nz(row[key]) > 0);
}

function mainClass(row) {
  const pool = text(row["主可比池"]);
  const map = {
    "目标盈系列产品": "目标盈/止盈/小目标",
    "现金管理型": "现金管理/活钱",
    "纯债/短债型": "纯债/短债",
    "固收增强型": "固收+稳健增强",
    "多资产配置型": "多资产均衡配置",
    "偏股配置型": "偏股核心配置",
    "主题/行业型": "主题/行业机会",
    "海外/全球型": "海外/全球配置",
    "目标日期/养老型": "养老/目标日期",
  };
  return map[pool] || pool || "未分类";
}

function riskBand(row) {
  if (!hasHoldingData(row)) return "D0 持仓缺失/待核验";
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
  if (!hasHoldingData(row)) return "D0 持仓缺失/待核验";
  const eq = nz(row["权益基金权重"]);
  const vol = nz(row["波动率"]);
  const mdd = nz(row["最大回撤"]);
  if (eq <= 8 && vol <= 1.8 && mdd <= 2.2) return "T1 低波目标盈";
  if (eq <= 15 && vol <= 3.0 && mdd <= 3.8) return "T2 稳健目标盈";
  if (eq <= 25 && vol <= 5.0 && mdd <= 7.0) return "T3 增强目标盈";
  if (eq <= 50 && vol <= 10.0 && mdd <= 15.0) return "T4 平衡/多元目标盈";
  return "T5 进取/权益目标盈";
}

function targetMechanism(row) {
  const joined = `${text(row["策略名称"])} ${text(row["特殊标签"])} ${text(row["策略概念"])} ${text(row["策略描述"])}`;
  if (/海外|全球/.test(joined) || nz(row["QDII权重"]) >= 10) return "跨境/全球目标盈";
  if (/黄金|商品/.test(joined)) return "黄金/商品目标盈";
  if (/主题|趋势|行业|赛道|红利|指数|定投/.test(joined) && nz(row["权益基金权重"]) >= 20) return "主题/工具化目标盈";
  if (/低波|微波|稳稳|稳健/.test(joined)) return "低波稳健目标盈";
  if (/多元|平衡|均衡|Pro|升级/.test(joined)) return "多元平衡目标盈";
  return "标准期次目标盈";
}

function operationCadence(row) {
  const name = text(row["策略名称"]);
  if (/测试/.test(name)) return "测试/内部";
  if (/第?\s*[0-9０-９]{1,3}\s*期|[0-9０-９]{1,3}\s*期/.test(name)) return "期次发车型";
  if (/永续|天天/.test(name)) return "连续运作型";
  if (nz(row["最近一年调仓次数"]) >= 4 || nz(row["调仓频率"]) >= 6) return "高触达维护型";
  return "常设货架型";
}

function implementation(row) {
  const tag = text(row["策略实现标签"]);
  if (/QDII|海外/.test(tag) || nz(row["QDII权重"]) >= 10) return "QDII/海外工具";
  if (/指数/.test(tag) || nz(row["指数基金权重"]) >= 50) return "指数/被动工具";
  if (/主动.*指数|指数.*主动/.test(tag) || (nz(row["指数基金权重"]) >= 20 && nz(row["主动基金权重"]) >= 20)) return "主动+指数混合";
  if (/FOF|养老/.test(tag)) return "FOF/养老工具";
  return "主动基金组合";
}

function marketRegion(row) {
  if (text(row["市场地域"])) return text(row["市场地域"]);
  if (nz(row["QDII权重"]) >= 30) return "海外/全球";
  if (nz(row["QDII权重"]) > 0) return "国内+海外";
  return "国内";
}

function marketingScene(row) {
  const cls = mainClass(row);
  if (cls === "现金管理/活钱") return "活钱管理、备用金";
  if (cls === "纯债/短债") return "低波防守、短债替代";
  if (cls === "固收+稳健增强") return "稳健增值、固收+";
  if (cls === "目标盈/止盈/小目标") {
    const risk = targetRisk(row);
    if (risk.startsWith("T1") || risk.startsWith("T2")) return "达标体验、稳健活动";
    if (risk.startsWith("T3") || risk.startsWith("T4")) return "目标收益增强";
    if (risk.startsWith("T5")) return "进取目标、权益机会";
    return "目标盈待核验";
  }
  if (cls === "多资产均衡配置") return "家庭资产配置、长期持有";
  if (cls === "偏股核心配置") return "权益核心、长期增值";
  if (cls === "主题/行业机会") return "主题热点、活动营销";
  if (cls === "海外/全球配置") return "全球配置、分散人民币资产";
  if (cls === "养老/目标日期") return "养老长期、生命周期";
  return "其他";
}

function normalizeSeriesName(name) {
  return text(name)
    .replace(/[（(](年中版|新年版|测试)[）)]/g, "")
    .replace(/第?\s*[0-9０-９]{1,3}\s*期/g, "")
    .replace(/\s+/g, "")
    .replace(/投顾服务/g, "")
    .replace(/组合策略/g, "")
    .replace(/组合/g, "")
    .replace(/策略/g, "")
    .trim();
}

function rowSummary(rows, key) {
  const gf = rows.filter(isGuangfa);
  const institutions = new Set(rows.map((row) => text(row["投顾机构"])).filter(Boolean));
  return {
    key,
    count: rows.length,
    gfCount: gf.length,
    gfShare: rows.length ? (gf.length / rows.length) * 100 : null,
    institutions: institutions.size,
    complete: rows.filter((row) => text(row["数据完整性"]) === "完整").length,
    eqMedian: median(rows.map((row) => num(row["权益基金权重"]))),
    annMedian: median(rows.map((row) => num(row["年化收益"]))),
    volMedian: median(rows.map((row) => num(row["波动率"]))),
    mddMedian: median(rows.map((row) => num(row["最大回撤"]))),
  };
}

function summarizeBy(rows, keyFn) {
  return countBy(rows, keyFn)
    .map(({ key, rows: bucket }) => rowSummary(bucket, key))
    .sort((a, b) => b.count - a.count || a.key.localeCompare(b.key, "zh"));
}

function takeTop(rows, n = 12) {
  return rows.slice().sort((a, b) => b.count - a.count || a.key.localeCompare(b.key, "zh")).slice(0, n);
}

function table(headers, rows, className = "") {
  const head = `<thead><tr>${headers.map((h) => `<th>${escapeHtml(h)}</th>`).join("")}</tr></thead>`;
  const body = `<tbody>${rows.map((row) => `<tr>${row.map((cell) => `<td>${cell}</td>`).join("")}</tr>`).join("")}</tbody>`;
  return `<table class="${className}">${head}${body}</table>`;
}

function statCard(label, value, note = "") {
  return `<div class="stat"><div class="stat-label">${escapeHtml(label)}</div><div class="stat-value">${escapeHtml(value)}</div><div class="stat-note">${escapeHtml(note)}</div></div>`;
}

function bar(value, max, label) {
  const pctValue = max > 0 ? Math.max(4, Math.min(100, (value / max) * 100)) : 0;
  return `<div class="bar-cell"><div class="bar-track"><div class="bar-fill" style="width:${pctValue.toFixed(1)}%"></div></div><span>${escapeHtml(label)}</span></div>`;
}

const total = strategies.length;
const gfRows = strategies.filter(isGuangfa);
const gfTotalShare = (gfRows.length / total) * 100;
const targetRows = strategies.filter((row) => text(row["主可比池"]) === "目标盈系列产品");
const targetKeywordRows = strategies.filter((row) => /目标盈|小目标|止盈|达标|心愿|星愿|稳稳/.test([
  row["策略名称"],
  row["策略类型"],
  row["特殊标签"],
  row["策略概念"],
  row["策略描述"],
].join(" ")));
const targetCadence = summarizeBy(targetRows, operationCadence);
const targetIssueRows = targetRows.filter((row) => operationCadence(row) === "期次发车型");
const targetSeriesKeys = new Set(targetRows.map((row) => `${text(row["投顾机构"])} / ${normalizeSeriesName(row["策略名称"])}`));
const targetWithData = targetRows.filter(hasHoldingData);
const targetNoData = targetRows.filter((row) => !hasHoldingData(row));

const mainSummary = summarizeBy(strategies, mainClass);
const riskSummary = summarizeBy(strategies, riskBand);
const targetRiskSummary = summarizeBy(targetRows, targetRisk);
const targetMechanismSummary = summarizeBy(targetRows, targetMechanism);
const targetSeriesSummary = takeTop(summarizeBy(targetRows, (row) => `${text(row["投顾机构"])} / ${normalizeSeriesName(row["策略名称"])}`), 18);
const gfMainSummary = summarizeBy(gfRows, mainClass);
const gfTargetRows = targetRows.filter(isGuangfa);
const falsePositiveRows = targetKeywordRows.filter((row) => text(row["主可比池"]) !== "目标盈系列产品");

const opportunityRows = mainSummary.map((item) => {
  const relative = item.gfShare == null ? null : item.gfShare - gfTotalShare;
  let priority = "维持/观察";
  let action = "保持当前覆盖，重点看单策略质量和渠道可见度。";
  if (item.count >= 80 && item.gfShare < gfTotalShare * 0.65) {
    priority = "高";
    action = "市场池大且广发份额低，适合做产品线补位、销售主题或竞品复盘。";
  } else if (item.count >= 50 && item.gfShare < gfTotalShare) {
    priority = "中";
    action = "已有样本但份额低于广发整体策略占比，适合找细分切口。";
  } else if (item.gfShare > gfTotalShare * 1.25) {
    priority = "已有优势";
    action = "广发布局相对充分，下一步看差异化卖点和头部策略复用。";
  }
  return { ...item, relative, priority, action };
}).sort((a, b) => {
  const rank = { "高": 3, "中": 2, "已有优势": 1, "维持/观察": 0 };
  return rank[b.priority] - rank[a.priority] || b.count - a.count;
});

const maxMainCount = Math.max(...mainSummary.map((row) => row.count));
const maxTargetRiskCount = Math.max(...targetRiskSummary.map((row) => row.count));

const mainTable = table(
  ["业务主类", "全市场策略数", "广发策略数", "广发占比", "机构数", "权益中位数", "年化中位数", "波动中位数", "回撤中位数"],
  mainSummary.map((row) => [
    `${escapeHtml(row.key)}${bar(row.count, maxMainCount, `${row.count}`)}`,
    String(row.count),
    String(row.gfCount),
    pct(row.gfShare),
    String(row.institutions),
    fmt(row.eqMedian),
    pct(row.annMedian),
    pct(row.volMedian),
    pct(row.mddMedian),
  ]),
  "wide"
);

const opportunityTable = table(
  ["机会等级", "分类", "全市场", "广发", "广发占比", "相对广发整体", "业务动作"],
  opportunityRows.map((row) => [
    `<span class="pill ${row.priority === "高" ? "high" : row.priority === "中" ? "mid" : row.priority === "已有优势" ? "good" : ""}">${escapeHtml(row.priority)}</span>`,
    escapeHtml(row.key),
    String(row.count),
    String(row.gfCount),
    pct(row.gfShare),
    `${row.relative == null ? "-" : (row.relative >= 0 ? "+" : "") + row.relative.toFixed(1)}pct`,
    escapeHtml(row.action),
  ]),
  "wide"
);

const targetRiskTable = table(
  ["目标盈风险桶", "策略数", "广发数", "广发占比", "权益中位数", "年化中位数", "波动中位数", "回撤中位数", "适用业务口径"],
  targetRiskSummary.map((row) => {
    const business = row.key.startsWith("D0") ? "不进正式比较池；先补持仓和可买状态。"
      : row.key.startsWith("T1") ? "低波达标、短周期营销、现金增强替代。"
        : row.key.startsWith("T2") ? "稳健小目标、固收+销售主货架。"
          : row.key.startsWith("T3") ? "增强目标收益、适合和稳健产品分层销售。"
            : row.key.startsWith("T4") ? "多元平衡目标，适合中风险客户。"
              : "权益机会/主题目标，只能和高权益目标盈比较。";
    return [
      `${escapeHtml(row.key)}${bar(row.count, maxTargetRiskCount, `${row.count}`)}`,
      String(row.count),
      String(row.gfCount),
      pct(row.gfShare),
      fmt(row.eqMedian),
      pct(row.annMedian),
      pct(row.volMedian),
      pct(row.mddMedian),
      escapeHtml(business),
    ];
  }),
  "wide"
);

const targetMechanismTable = table(
  ["目标盈机制/卖点", "策略数", "广发数", "权益中位数", "年化中位数", "说明"],
  targetMechanismSummary.map((row) => {
    const notes = {
      "标准期次目标盈": "目标盈主力形态，多为同一系列不同期次，适合活动化、续作和达标体验运营。",
      "低波稳健目标盈": "低波、微波、稳健等关键词明显，适合低风险客群承接。",
      "多元平衡目标盈": "多资产或 Pro/升级版，不能和低波型直接比收益。",
      "主题/工具化目标盈": "权益或指数工具色彩强，适合营销但要单列风险提示。",
      "跨境/全球目标盈": "全球/QDII 暴露，汇率和海外市场 Beta 另行分池。",
      "黄金/商品目标盈": "商品或黄金是主要卖点，适合作为配置补充而非固收+替代。",
    };
    return [
      escapeHtml(row.key),
      String(row.count),
      String(row.gfCount),
      fmt(row.eqMedian),
      pct(row.annMedian),
      escapeHtml(notes[row.key] || ""),
    ];
  }),
  "wide"
);

const targetCadenceTable = table(
  ["运作节奏", "策略数", "占目标盈比例", "广发数", "解释"],
  targetCadence.map((row) => {
    const notes = {
      "期次发车型": "同一产品系列按不同期次发行或展示，是 338 条目标盈记录膨胀的主因。",
      "常设货架型": "不是强期次形态，更适合常态化销售货架和投研长期跟踪。",
      "连续运作型": "类似永续/天天运作，重点看持有体验和续作质量。",
      "测试/内部": "不进入正式销售或考核口径。",
      "高触达维护型": "调仓或更新频率高，更接近高维护策略。",
    };
    return [escapeHtml(row.key), String(row.count), pct((row.count / targetRows.length) * 100), String(row.gfCount), escapeHtml(notes[row.key] || "")];
  }),
  "wide"
);

const targetSeriesTable = table(
  ["归并后目标盈系列 Top", "策略记录数", "广发数", "权益中位数", "年化中位数", "波动中位数", "回撤中位数"],
  targetSeriesSummary.map((row) => [
    escapeHtml(row.key),
    String(row.count),
    String(row.gfCount),
    fmt(row.eqMedian),
    pct(row.annMedian),
    pct(row.volMedian),
    pct(row.mddMedian),
  ]),
  "wide"
);

const gfTargetTable = table(
  ["广发目标盈策略", "风险桶", "机制卖点", "运作节奏", "权益", "债券", "货币", "年化", "波动", "最大回撤", "判断"],
  gfTargetRows
    .slice()
    .sort((a, b) => targetRisk(a).localeCompare(targetRisk(b), "zh") || text(a["策略名称"]).localeCompare(text(b["策略名称"]), "zh"))
    .map((row) => {
      const risk = targetRisk(row);
      let note = "可进入对应细分池比较。";
      if (risk.startsWith("D0")) note = "当前持仓权重缺失，先不进正式比较池。";
      if (risk.startsWith("T5")) note = "权益/回撤显著高，不能代表稳健目标盈。";
      if (/超级定投/.test(text(row["策略名称"]))) note = "更像权益定投/进取目标，不宜放入低波目标盈营销。";
      return [
        escapeHtml(row["策略名称"]),
        escapeHtml(risk),
        escapeHtml(targetMechanism(row)),
        escapeHtml(operationCadence(row)),
        fmt(row["权益基金权重"]),
        fmt(row["债券基金权重"]),
        fmt(row["货币基金权重"]),
        pct(row["年化收益"]),
        pct(row["波动率"]),
        pct(row["最大回撤"]),
        escapeHtml(note),
      ];
    }),
  "wide"
);

const falsePositiveTable = table(
  ["不应并入目标盈的关键词命中样本", "当前主池", "机构", "权益", "波动", "最大回撤", "处理建议"],
  falsePositiveRows.map((row) => [
    escapeHtml(row["策略名称"]),
    escapeHtml(row["主可比池"]),
    escapeHtml(row["投顾机构"]),
    fmt(row["权益基金权重"]),
    pct(row["波动率"]),
    pct(row["最大回撤"]),
    "名称含“稳稳/幸福”等营销词，但没有目标盈/止盈标签，保留原主池。",
  ]),
  "wide"
);

const taxonomyTable = table(
  ["维度", "字段/规则", "服务目标", "用于广发机会挖掘时的用法"],
  [
    ["业务主类", "主可比池校正：现金、纯债、固收+、目标盈、多资产、偏股、主题、海外、养老", "销售货架、产品布局、竞品覆盖", "先看广发在哪些主类低于整体占比，再下钻到风险桶。"],
    ["风险预算", "权益权重 + 波动率 + 最大回撤，目标盈另设 T1-T5", "投研比较、客户风险适配", "同主类内只比较同风险桶；跨桶只做营销定位。"],
    ["目标盈机制", "低波稳健、多元平衡、主题工具、跨境全球、黄金商品、标准期次", "营销卖点、活动设计", "判断广发目标盈是稳健主打、增强主打，还是缺全球/主题切口。"],
    ["运作节奏", "期次发车、常设货架、连续运作、高触达维护、测试内部", "销售运营、复购续作、服务触达", "期次型看发车节奏和达成率；常设型看长期留存和可买状态。"],
    ["实现方式", "主动基金组合、指数/被动工具、主动+指数、QDII/海外、FOF/养老", "投研归因、产品能力拆解", "同类策略看广发底层产品与竞品工具链是否短板。"],
    ["数据质量/生命周期", "完整性、持仓缺失、运作状态、成立时间、费率状态", "正式考核、合规展示", "D0 或不可买策略只用于线索，不用于销售主推和排名。"],
  ].map((row) => row.map(escapeHtml)),
  "wide"
);

const dataDate = summary.overview?.["数据更新至"] || summary.overview?.["数据刷新时间"] || "";
const generatedAt = new Date().toLocaleString("zh-CN", { hour12: false });

const html = `<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>投顾策略精准分类与广发布局机会分析</title>
  <style>
    :root {
      --ink: #17202a;
      --muted: #5b6674;
      --line: #d9e0e8;
      --soft: #f5f7fa;
      --panel: #ffffff;
      --accent: #1f7a8c;
      --accent-2: #7a5c1f;
      --good: #1b7f4d;
      --warn: #a36200;
      --bad: #b42318;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      color: var(--ink);
      background: #eef2f6;
      font: 14px/1.55 -apple-system, BlinkMacSystemFont, "Segoe UI", "Microsoft YaHei", sans-serif;
    }
    header {
      background: #0e3641;
      color: white;
      padding: 30px 36px 26px;
    }
    header h1 {
      margin: 0 0 8px;
      font-size: 28px;
      letter-spacing: 0;
    }
    header p {
      margin: 4px 0;
      color: #d7e7ec;
      max-width: 1080px;
    }
    main {
      max-width: 1280px;
      margin: 0 auto;
      padding: 24px 28px 44px;
    }
    section {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 22px;
      margin: 0 0 18px;
      box-shadow: 0 1px 2px rgba(18, 27, 38, 0.04);
    }
    h2 {
      font-size: 20px;
      margin: 0 0 12px;
    }
    h3 {
      font-size: 16px;
      margin: 18px 0 10px;
    }
    p { margin: 8px 0; }
    .stats {
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 12px;
      margin: 14px 0 6px;
    }
    .stat {
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 14px;
      background: var(--soft);
      min-height: 104px;
    }
    .stat-label {
      color: var(--muted);
      font-size: 13px;
      margin-bottom: 6px;
    }
    .stat-value {
      font-weight: 700;
      font-size: 24px;
      margin-bottom: 4px;
    }
    .stat-note {
      color: var(--muted);
      font-size: 12px;
    }
    .grid-2 {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 16px;
      align-items: start;
    }
    table {
      width: 100%;
      border-collapse: collapse;
      margin: 10px 0 6px;
      background: white;
      table-layout: auto;
    }
    th, td {
      border-bottom: 1px solid var(--line);
      padding: 8px 9px;
      vertical-align: top;
      text-align: left;
    }
    th {
      background: #edf3f7;
      color: #243442;
      font-weight: 650;
      white-space: nowrap;
    }
    td {
      color: #25313e;
    }
    .wide td:nth-child(n+2), .wide th:nth-child(n+2) {
      white-space: nowrap;
    }
    .wide td:last-child {
      white-space: normal;
    }
    .note {
      color: var(--muted);
      font-size: 13px;
    }
    .callout {
      border-left: 4px solid var(--accent);
      background: #eef8fb;
      padding: 12px 14px;
      margin: 12px 0;
      border-radius: 0 8px 8px 0;
    }
    .pill {
      display: inline-block;
      padding: 2px 8px;
      border-radius: 999px;
      background: #e8edf3;
      color: #2d3a46;
      font-weight: 650;
      white-space: nowrap;
    }
    .pill.high { background: #fde7e4; color: var(--bad); }
    .pill.mid { background: #fff1d6; color: var(--warn); }
    .pill.good { background: #e3f6eb; color: var(--good); }
    .bar-cell {
      display: grid;
      grid-template-columns: minmax(120px, 1fr) auto;
      gap: 8px;
      align-items: center;
      margin-top: 6px;
    }
    .bar-track {
      height: 8px;
      background: #e3e9ef;
      border-radius: 999px;
      overflow: hidden;
    }
    .bar-fill {
      height: 100%;
      background: var(--accent);
    }
    ul { margin: 8px 0 0 20px; padding: 0; }
    li { margin: 5px 0; }
    code {
      background: #eef2f6;
      border: 1px solid #dce3ea;
      border-radius: 4px;
      padding: 1px 4px;
    }
    footer {
      color: var(--muted);
      font-size: 12px;
      text-align: center;
      padding: 10px 0 0;
    }
    @media (max-width: 960px) {
      header { padding: 24px 18px; }
      main { padding: 18px 12px 34px; }
      .stats, .grid-2 { grid-template-columns: 1fr; }
      section { padding: 16px; }
      table { display: block; overflow-x: auto; }
      th, td { min-width: 108px; }
    }
  </style>
</head>
<body>
  <header>
    <h1>投顾策略精准分类与广发布局机会分析</h1>
    <p>基于 <code>basic_data/data/basic_summary.js</code> 的 ${total} 条展示策略、${summary.rebalanceEvents?.length || 2335} 条调仓事件派生。数据更新至：${escapeHtml(dataDate)}；报告生成：${escapeHtml(generatedAt)}。</p>
    <p>目标：同一套分类同时支持投研考核、销售货架、营销活动、产品布局诊断与广发基金潜在业务机会挖掘。</p>
  </header>
  <main>
    <section>
      <h2>结论摘要</h2>
      <div class="callout">
        <p><strong>目标盈不是 338 个独立产品线。</strong> 数据中确实有 ${targetRows.length} 条“目标盈系列产品”记录，且全部带目标盈/止盈类标签；但其中 ${targetIssueRows.length} 条是期次发车型，归并到“机构 + 系列名”后约 ${targetSeriesKeys.size} 个系列。销售、营销和产品分析应该看“系列 + 风险桶 + 期次节奏”，不是把 338 条直接当作同质目标盈。</p>
        <p><strong>广发当前目标盈布局不弱但结构要重分。</strong> 广发目标盈 ${gfTargetRows.length} 条，覆盖幸福小列车、小心愿、Pro、多元平衡、超级定投家；其中“超级定投家”是高权益/高回撤，不应代表稳健目标盈。</p>
      </div>
      <div class="stats">
        ${statCard("全市场策略数", String(total), "展示策略，不含隐藏渠道")}
        ${statCard("广发策略数", `${gfRows.length}`, `占全市场 ${pct(gfTotalShare)}`)}
        ${statCard("目标盈记录数", `${targetRows.length}`, `${targetIssueRows.length} 条为期次发车型`)}
        ${statCard("目标盈归并系列", `${targetSeriesKeys.size}`, `${targetNoData.length} 条持仓缺失待核验`)}
      </div>
    </section>

    <section>
      <h2>推荐分类体系</h2>
      <p>不要只用一个“主可比池”。正式落地建议把每条策略打成六组标签：业务主类、风险预算、目标盈机制、运作节奏、实现方式、数据质量/生命周期。这样同一数据既能用于排名，也能用于销售货架和产品机会挖掘。</p>
      ${taxonomyTable}
    </section>

    <section>
      <h2>全市场业务主类与广发布局</h2>
      <p class="note">“广发占比”按该分类内广发策略数 / 全市场策略数计算；广发整体展示策略占比为 ${pct(gfTotalShare)}，可作为布局强弱的粗基准。</p>
      ${mainTable}
    </section>

    <section>
      <h2>广发潜在业务机会</h2>
      <p>机会不是简单看广发是否有产品，而是看市场池规模、广发相对份额、销售场景是否清晰、以及能否用广发底层产品能力形成差异化。</p>
      ${opportunityTable}
      <h3>优先判断</h3>
      <ul>
        <li><strong>目标盈/小目标：</strong>市场最大池，广发 ${gfTargetRows.length}/${targetRows.length}，低于广发整体策略占比。机会在于把“幸福小列车/小心愿/Pro/多元平衡/进取”拆成明确货架，而不是合并成一个目标盈。</li>
        <li><strong>主题/行业机会：</strong>全市场有 ${mainSummary.find((row) => row.key === "主题/行业机会")?.count || 0} 条，广发只有 ${mainSummary.find((row) => row.key === "主题/行业机会")?.gfCount || 0} 条。若广发具备 ETF、主动权益或行业基金能力，这里适合做营销切口，但要单列高风险提示。</li>
        <li><strong>海外/全球配置：</strong>广发覆盖相对充分，可进一步判断是否能从“全球配置”延展到“全球目标盈/海外主题目标盈”的活动型产品。</li>
      </ul>
    </section>

    <section>
      <h2>目标盈复核：为什么不能再粗分</h2>
      <div class="stats">
        ${statCard("目标盈主池", `${targetRows.length}`, "全部命中目标盈/止盈标签")}
        ${statCard("关键词误命中", `${falsePositiveRows.length}`, "含稳稳/幸福等词但不是目标盈")}
        ${statCard("期次发车型", `${targetIssueRows.length}`, `${pct((targetIssueRows.length / targetRows.length) * 100)} 的目标盈记录`)}
        ${statCard("持仓缺失/待核验", `${targetNoData.length}`, "不进入正式比较池")}
      </div>
      <h3>目标盈风险桶</h3>
      ${targetRiskTable}
      <h3>目标盈机制/卖点</h3>
      ${targetMechanismTable}
      <h3>目标盈运作节奏</h3>
      ${targetCadenceTable}
      <h3>归并后目标盈系列 Top</h3>
      ${targetSeriesTable}
    </section>

    <section>
      <h2>广发目标盈应如何重分</h2>
      <p>广发目标盈内部至少要拆成稳健达标、增强目标、多元平衡和进取权益四类；其中持仓缺失的期次先做数据补齐，不进入正式排名。</p>
      ${gfTargetTable}
    </section>

    <section>
      <h2>不能误并入目标盈的样本</h2>
      <p class="note">这些策略名称含“稳稳/幸福”等销售词，但系统主池和标签并非目标盈/止盈。后续规则不能只靠营销关键词匹配。</p>
      ${falsePositiveTable}
    </section>

    <section>
      <h2>落地建议</h2>
      <ul>
        <li><strong>投研：</strong>排名池使用“业务主类 × 风险预算 × 市场地域 × 实现方式 × 运作节奏”的交集；目标盈另看达成率、达成时间、到期/续作质量和持有期最大回撤。</li>
        <li><strong>销售：</strong>货架按客户需求组织：活钱、短债低波、稳健固收+、目标达标、多元配置、权益进取、主题机会、全球配置、养老长期。</li>
        <li><strong>营销：</strong>期次型目标盈适合活动化发车和续作；常设型策略适合长期货架；主题/工具化目标盈适合市场热点，但不能包装成稳健目标盈。</li>
        <li><strong>产品分析：</strong>对广发做“有无覆盖、覆盖深度、竞品拥挤度、底层产品能力、数据完整性”五项评分，优先看目标盈细分和主题/行业机会。</li>
        <li><strong>数据补齐：</strong>目标盈需要补目标收益率、目标期限、是否达标、达标日期、到期/续作状态；信号发车类还需补信号日期、建议买卖日期、跟车可买状态、信号后最大回撤。</li>
      </ul>
    </section>

    <footer>
      来源：${escapeHtml(path.relative(root, sourcePath))}。本报告为本地数据再分类结果，不改写原始数据。
    </footer>
  </main>
</body>
</html>`;

fs.writeFileSync(outputPath, html, "utf8");
console.log(outputPath);
