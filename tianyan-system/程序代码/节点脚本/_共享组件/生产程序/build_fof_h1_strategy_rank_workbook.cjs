const fs = require("fs/promises");
const path = require("path");
const { SpreadsheetFile, Workbook } = require("@oai/artifact-tool");

function colLetter(index) {
  let n = index + 1;
  let out = "";
  while (n > 0) {
    const rem = (n - 1) % 26;
    out = String.fromCharCode(65 + rem) + out;
    n = Math.floor((n - 1) / 26);
  }
  return out;
}

function a1(row, col) {
  return `${colLetter(col)}${row + 1}`;
}

function tableRange(rowCount, colCount) {
  return `A1:${colLetter(colCount - 1)}${Math.max(1, rowCount)}`;
}

function asValue(value) {
  if (value === undefined) return null;
  return value;
}

function rowsToMatrix(headers, rows) {
  return [headers, ...rows.map((row) => headers.map((header) => asValue(row[header])))];
}

function setColumnWidths(sheet, widths) {
  widths.forEach((width, index) => {
    if (!width) return;
    sheet.getRange(`${colLetter(index)}:${colLetter(index)}`).format.columnWidth = width;
  });
}

function styleTable(sheet, rowCount, colCount, options = {}) {
  if (rowCount <= 0 || colCount <= 0) return;
  sheet.showGridLines = false;
  sheet.freezePanes.freezeRows(1);
  const used = sheet.getRange(tableRange(rowCount, colCount));
  used.format.font = { name: "Microsoft YaHei", size: 10 };
  used.format.borders = {
    insideHorizontal: { style: "thin", color: "#E5E7EB" },
    bottom: { style: "thin", color: "#E5E7EB" },
  };
  const header = sheet.getRange(`A1:${colLetter(colCount - 1)}1`);
  header.format = {
    fill: options.headerFill || "#0F766E",
    font: { bold: true, color: "#FFFFFF", name: "Microsoft YaHei", size: 10 },
    wrapText: true,
  };
  header.format.rowHeight = 32;
  if (rowCount > 1) {
    sheet.getRange(`A2:${colLetter(colCount - 1)}${rowCount}`).format.wrapText = false;
  }
}

function applyFormats(sheet, headers, rowCount, formatMap) {
  for (const [header, format] of Object.entries(formatMap)) {
    const index = headers.indexOf(header);
    if (index < 0 || rowCount < 2) continue;
    const range = sheet.getRange(`${colLetter(index)}2:${colLetter(index)}${rowCount}`);
    range.format.numberFormat = format;
  }
}

function addSheetFromRows(workbook, name, headers, rows, widths, formatMap, tableName) {
  const sheet = workbook.worksheets.add(name);
  const matrix = rowsToMatrix(headers, rows);
  sheet.getRangeByIndexes(0, 0, matrix.length, headers.length).values = matrix;
  styleTable(sheet, matrix.length, headers.length);
  setColumnWidths(sheet, widths);
  applyFormats(sheet, headers, matrix.length, formatMap);
  if (matrix.length > 1 && tableName) {
    sheet.tables.add(tableRange(matrix.length, headers.length), true, tableName);
  }
  return sheet;
}

function pctDecimal(value) {
  return value == null || value === "" ? null : Number(value) / 100;
}

function convertPercentRows(rows, fields) {
  return rows.map((row) => {
    const copy = { ...row };
    for (const field of fields) {
      if (copy[field] != null && copy[field] !== "") copy[field] = Number(copy[field]) / 100;
    }
    return copy;
  });
}

function buildSummaryRows(data) {
  const meta = data.meta;
  return [
    { 指标: "策略总数", 数值: meta.策略总数, 说明: "来自 basic_summary.js 的策略宽表" },
    { 指标: "有H1收益策略数", 数值: meta.有H1收益策略数, 说明: "标准费后净值可计算区间收益" },
    { 指标: "对客策略数", 数值: meta.对客策略数, 说明: "字段：是否对客=是" },
    { 指标: "本地FOF字典总数", 数值: meta.本地FOF字典总数, 说明: "基金标准分类字典 是否FOF=1" },
    { 指标: "有H1收益FOF数", 数值: meta.有H1收益FOF数, 说明: "排行接口有区间/YTD/近6月可用收益" },
    { 指标: "缺H1收益FOF数", 数值: meta.缺H1收益FOF数, 说明: "保留在缺口清单，不进入排名分布" },
    { 指标: "策略标准净值实际最新日", 数值: meta.实际策略标准净值最新日 || "", 说明: "本报表策略收益实际使用到的期末标准净值日期" },
    { 指标: "策略20260630覆盖数", 数值: meta.策略20260630覆盖数, 说明: "期末标准净值日期等于 2026-06-30 的策略数量" },
    { 指标: "FOF净值实际最新日", 数值: meta.实际FOF净值最新日 || "", 说明: "本报表FOF收益实际使用到的排行净值日期" },
    { 指标: "FOF20260630覆盖数", 数值: meta.FOF20260630覆盖数, 说明: "净值日期等于 2026-06-30 的FOF产品数量" },
    { 指标: "FOF收益来源分布", 数值: JSON.stringify(meta.FOF收益来源分布 || {}), 说明: "fof/qdii/all 接口合并后的来源分布" },
  ];
}

async function main() {
  const [jsonPath, outputPath, previewDir] = process.argv.slice(2);
  if (!jsonPath || !outputPath) {
    throw new Error("Usage: node build_fof_h1_strategy_rank_workbook.cjs <data.json> <output.xlsx> [previewDir]");
  }
  const data = JSON.parse(await fs.readFile(jsonPath, "utf8"));
  const workbook = Workbook.create();

  const percentFields = [
    "权益基金权重_百分比",
    "债券基金权重_百分比",
    "货币基金权重_百分比",
    "混合基金权重_百分比",
    "QDII权重_百分比",
    "策略H1收益率_百分比",
    "策略H1年化收益率_百分比",
    "基准H1收益率_百分比",
    "相对基准超额_百分点",
    "上半年收益率_百分比",
    "排行区间收益率_百分比",
    "今年以来收益率_百分比",
    "近6月收益率_百分比",
    "策略平均H1收益率_百分比",
    "策略中位数H1收益率_百分比",
    "FOF平均H1收益率_百分比",
    "FOF中位数H1收益率_百分比",
  ];
  const strategyRows = convertPercentRows(data.strategyRows, percentFields);
  const fofRows = convertPercentRows(data.fofRows, percentFields);
  const categoryRows = convertPercentRows(data.categoryRows, percentFields);
  const missingRows = convertPercentRows(data.missingFofRows, percentFields);

  const summary = workbook.worksheets.add("摘要");
  summary.showGridLines = false;
  summary.getRange("A1:F1").merge();
  summary.getRange("A1").values = [[data.meta.报告名称]];
  summary.getRange("A1").format = {
    fill: "#0B3B36",
    font: { bold: true, color: "#FFFFFF", size: 16, name: "Microsoft YaHei" },
  };
  summary.getRange("A2:F2").merge();
  summary.getRange("A2").values = [[`生成时间：${data.meta.生成时间}；策略收益锚点：${data.meta.策略收益起始锚点} 至 ${data.meta.策略收益截止锚点}；基金收益锚点：${data.meta.基金收益起始锚点} 至 ${data.meta.基金收益截止锚点}`]];
  summary.getRange("A2").format = { font: { color: "#374151", name: "Microsoft YaHei", size: 10 }, wrapText: true };
  const summaryRows = buildSummaryRows(data);
  const summaryHeaders = ["指标", "数值", "说明"];
  summary.getRangeByIndexes(3, 0, summaryRows.length + 1, summaryHeaders.length).values = rowsToMatrix(summaryHeaders, summaryRows);
  styleTable(summary, summaryRows.length + 4, summaryHeaders.length, { headerFill: "#0F766E" });
  setColumnWidths(summary, [24, 16, 72]);
  summary.getRange("B5:B10").format.numberFormat = "#,##0";

  const categoryHeaders = [
    "FOF可比分类",
    "策略数量",
    "对客策略数量",
    "FOF产品总数",
    "有收益FOF产品数",
    "策略平均H1收益率_百分比",
    "策略中位数H1收益率_百分比",
    "FOF平均H1收益率_百分比",
    "FOF中位数H1收益率_百分比",
    "策略平均排名百分位",
    "策略中位数排名百分位",
  ];
  addSheetFromRows(
    workbook,
    "分类汇总",
    categoryHeaders,
    categoryRows,
    [18, 12, 14, 14, 16, 18, 20, 18, 20, 18, 20],
    {
      策略平均H1收益率_百分比: "0.00%",
      策略中位数H1收益率_百分比: "0.00%",
      FOF平均H1收益率_百分比: "0.00%",
      FOF中位数H1收益率_百分比: "0.00%",
      策略平均排名百分位: "0.00%",
      策略中位数排名百分位: "0.00%",
    },
    "CategorySummary"
  );

  const strategyHeaders = [
    "统一策略ID",
    "渠道",
    "投顾机构",
    "策略名称",
    "是否对客",
    "天天展示状态",
    "是否纳入常规排名",
    "治理状态",
    "风险等级",
    "业务分类",
    "研报产品类型",
    "市场地域",
    "FOF可比分类",
    "FOF分类依据",
    "策略H1收益率_百分比",
    "策略H1年化收益率_百分比",
    "基准H1收益率_百分比",
    "相对基准超额_百分点",
    "同类FOF样本数",
    "同类FOF排名",
    "击败同类FOF比例",
    "排名位置百分位",
    "标准净值起始日",
    "标准净值截止日",
    "策略收益覆盖状态",
    "权益基金权重_百分比",
    "债券基金权重_百分比",
    "货币基金权重_百分比",
    "混合基金权重_百分比",
    "QDII权重_百分比",
    "业绩基准",
    "天天展示判定依据",
  ];
  addSheetFromRows(
    workbook,
    "投顾组合排名",
    strategyHeaders,
    strategyRows,
    [22, 16, 18, 28, 10, 18, 14, 16, 16, 16, 16, 12, 14, 28, 14, 16, 14, 16, 14, 14, 14, 14, 14, 14, 16, 14, 14, 14, 14, 14, 52, 58],
    {
      策略H1收益率_百分比: "0.00%",
      策略H1年化收益率_百分比: "0.00%",
      基准H1收益率_百分比: "0.00%",
      相对基准超额_百分点: "0.00%",
      击败同类FOF比例: "0.00%",
      排名位置百分位: "0.00%",
      权益基金权重_百分比: "0.0%",
      债券基金权重_百分比: "0.0%",
      货币基金权重_百分比: "0.0%",
      混合基金权重_百分比: "0.0%",
      QDII权重_百分比: "0.0%",
    },
    "StrategyRanking"
  );

  const fofHeaders = [
    "基金代码",
    "基金名称",
    "FOF可比分类",
    "天天基金细分类",
    "天天基金大类",
    "天天基金二级分类",
    "基金公司",
    "基金经理",
    "是否QDII",
    "市场地域标签",
    "主动被动标签",
    "投顾资产分类桶",
    "净值日期",
    "单位净值",
    "累计净值",
    "上半年收益率_百分比",
    "排行区间收益率_百分比",
    "今年以来收益率_百分比",
    "近6月收益率_百分比",
    "成立日期",
    "排行接口类型",
    "数据状态",
  ];
  addSheetFromRows(
    workbook,
    "FOF产品池",
    fofHeaders,
    fofRows,
    [12, 36, 14, 16, 12, 18, 18, 20, 10, 16, 14, 16, 14, 12, 12, 16, 18, 16, 16, 14, 12, 24],
    {
      单位净值: "0.0000",
      累计净值: "0.0000",
      上半年收益率_百分比: "0.00%",
      排行区间收益率_百分比: "0.00%",
      今年以来收益率_百分比: "0.00%",
      近6月收益率_百分比: "0.00%",
    },
    "FofUniverse"
  );

  const missingHeaders = ["基金代码", "基金名称", "FOF可比分类", "天天基金细分类", "基金公司", "成立日期", "排行接口类型", "数据状态"];
  addSheetFromRows(
    workbook,
    "数据缺口",
    missingHeaders,
    missingRows,
    [12, 36, 14, 16, 18, 14, 12, 24],
    {},
    "DataGaps"
  );

  addSheetFromRows(
    workbook,
    "计算说明",
    ["项目", "说明"],
    data.notes,
    [24, 110],
    {},
    "MethodNotes"
  );

  const checks = workbook.worksheets.add("检查");
  checks.showGridLines = false;
  const allApiMeta = data.meta.排行接口.find((x) => x.ft === "all") || {};
  const allSourceRows = Number((data.meta.FOF收益来源分布 || {}).all || 0);
  const allApiStatus = allApiMeta.complete ? "OK" : allSourceRows > 0 ? "WARN" : "INFO";
  const checkRows = [
    ["检查项", "结果", "说明"],
    ["策略行数", data.strategyRows.length, "应等于摘要策略总数"],
    ["FOF产品池行数", data.fofRows.length, "应等于本地FOF字典总数"],
    ["缺H1收益FOF数", data.missingFofRows.length, "保留缺口，不进入同类排名分布"],
    ["策略期末锚点覆盖", `${data.meta.策略20260630覆盖数 || 0}/${data.meta.有H1收益策略数 || 0}`, "分母为有H1收益策略数；未覆盖锚点的多为已停止或早期无后续净值策略"],
    ["FOF期末锚点覆盖", `${data.meta.FOF20260630覆盖数 || 0}/${data.meta.有H1收益FOF数 || 0}`, "分母为有H1收益FOF产品数"],
    ["FOF接口fof完整性", data.meta.排行接口.find((x) => x.ft === "fof")?.complete ? "OK" : "WARN", JSON.stringify(data.meta.排行接口.find((x) => x.ft === "fof") || {})],
    ["FOF接口qdii完整性", data.meta.排行接口.find((x) => x.ft === "qdii")?.complete ? "OK" : "WARN", JSON.stringify(data.meta.排行接口.find((x) => x.ft === "qdii") || {})],
    ["FOF接口all完整性", allApiStatus, JSON.stringify({ ...allApiMeta, fallbackFilledFofRows: allSourceRows })],
    ["FOF数据状态分布", "INFO", JSON.stringify(data.meta.FOF数据状态分布 || {})],
  ];
  checks.getRangeByIndexes(0, 0, checkRows.length, 3).values = checkRows;
  styleTable(checks, checkRows.length, 3, { headerFill: "#334155" });
  setColumnWidths(checks, [24, 14, 110]);

  const errorScan = await workbook.inspect({
    kind: "match",
    searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A",
    options: { useRegex: true, maxResults: 300 },
    summary: "formula error scan",
  });
  console.log(errorScan.ndjson);

  if (previewDir) {
    await fs.mkdir(previewDir, { recursive: true });
    const previewRanges = {
      摘要: "A1:F12",
      分类汇总: "A1:K20",
      投顾组合排名: "A1:AF35",
      FOF产品池: "A1:V35",
      数据缺口: "A1:H35",
      计算说明: "A1:B20",
      检查: "A1:C14",
    };
    for (const [sheetName, range] of Object.entries(previewRanges)) {
      const preview = await workbook.render({ sheetName, range, scale: 1, format: "png" });
      await fs.writeFile(path.join(previewDir, `${sheetName}.png`), new Uint8Array(await preview.arrayBuffer()));
    }
  }

  await fs.mkdir(path.dirname(outputPath), { recursive: true });
  const xlsx = await SpreadsheetFile.exportXlsx(workbook);
  await xlsx.save(outputPath);
  console.log(JSON.stringify({ outputPath }, null, 2));
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
