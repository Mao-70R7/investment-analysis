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

function tableRange(rowCount, colCount) {
  return `A1:${colLetter(colCount - 1)}${Math.max(1, rowCount)}`;
}

function valueForCell(value) {
  if (value === undefined) return null;
  return value;
}

function rowsToMatrix(headers, rows) {
  return [headers, ...rows.map((row) => headers.map((header) => valueForCell(row[header])))];
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
  used.format.font = { name: "Microsoft YaHei", size: 10, color: "#111827" };
  used.format.borders = {
    insideHorizontal: { style: "thin", color: "#E5E7EB" },
    insideVertical: { style: "thin", color: "#EEF2F7" },
    bottom: { style: "thin", color: "#D1D5DB" },
  };
  const header = sheet.getRange(`A1:${colLetter(colCount - 1)}1`);
  header.format = {
    fill: options.headerFill || "#0F766E",
    font: { bold: true, color: "#FFFFFF", name: "Microsoft YaHei", size: 10 },
    wrapText: true,
  };
  header.format.rowHeight = 34;
  if (rowCount > 1) {
    sheet.getRange(`A2:${colLetter(colCount - 1)}${rowCount}`).format.wrapText = false;
  }
}

function applyFormats(sheet, headers, rowCount, formatMap) {
  if (rowCount < 2) return;
  for (const [header, format] of Object.entries(formatMap)) {
    const index = headers.indexOf(header);
    if (index < 0) continue;
    sheet.getRange(`${colLetter(index)}2:${colLetter(index)}${rowCount}`).format.numberFormat = format;
  }
}

function defaultFormats(headers) {
  const formats = {};
  for (const header of headers) {
    if (
      header.endsWith("收益率") ||
      header.endsWith("最大回撤") ||
      header.endsWith("年化波动率") ||
      header.endsWith("权重") ||
      header.endsWith("均值") ||
      header.endsWith("中位数")
    ) {
      formats[header] = "0.00%";
    }
    if (header.endsWith("风险净值点数") || header.endsWith("有效数") || header.endsWith("产品数") || header === "排名") {
      formats[header] = "#,##0";
    }
    if (header === "解析置信度分数") {
      formats[header] = "0.00";
    }
    if (header.endsWith("_百分点")) {
      formats[header] = "0.0000";
    }
  }
  return formats;
}

function addSheetFromRows(workbook, name, headers, rows, widths, tableName, options = {}) {
  const sheet = workbook.worksheets.add(name);
  const matrix = rowsToMatrix(headers, rows);
  sheet.getRangeByIndexes(0, 0, matrix.length, headers.length).values = matrix;
  styleTable(sheet, matrix.length, headers.length, options);
  setColumnWidths(sheet, widths);
  applyFormats(sheet, headers, matrix.length, { ...defaultFormats(headers), ...(options.formatMap || {}) });
  if (matrix.length > 1 && tableName) {
    sheet.tables.add(tableRange(matrix.length, headers.length), true, tableName);
  }
  return sheet;
}

function intervalHeaders() {
  const labels = ["上半年", "今年以来", "近1月", "近3月", "近6月", "近1年"];
  const headers = [];
  for (const label of labels) {
    headers.push(`${label}收益率`, `${label}最大回撤`, `${label}年化波动率`, `${label}区间`, `${label}收益来源`, `${label}风险来源`, `${label}风险净值点数`);
  }
  return headers;
}

function buildMetaRows(data) {
  const meta = data.meta || {};
  return [
    { 项目: "报告名称", 值: meta.title || "", 说明: "投顾策略和基金产品按统一字段混排" },
    { 项目: "截至日期", 值: meta.asOfDate || "", 说明: "收益、回撤、波动率的目标截止日" },
    { 项目: "生成时间", 值: meta.generatedAt || "", 说明: "导出源数据生成时间" },
    { 项目: "策略范围", 值: meta.strategyScope || "", 说明: "延续当前页面渠道口径" },
    { 项目: "基金范围", 值: meta.fundScope || meta.fofScope || "", 说明: "基金产品池" },
    { 项目: "导出总行数", 值: meta.exportRowCount || 0, 说明: `原始策略包${meta.rawRowCount || meta.rawStrategyRowCount || 0}；排除非展示渠道策略${meta.excludedStrategyRowCount || 0}` },
    { 项目: "机构抽样数", 值: meta.qaSampleCount || 0, 说明: JSON.stringify(meta.qaStatusCounts || {}) },
    { 项目: "核对阈值", 值: `${meta.tolerancePp || 0}个百分点`, 说明: "抽样重算差异不超过该阈值视为一致" },
    { 项目: "来源数据包", 值: meta.sourcePack || "", 说明: "正式页面目录的数据包" },
    { 项目: "来源数据库", 值: meta.sourceDb || "", 说明: "本地SQLite分析库" },
  ];
}

function widthsFor(headers, overrides = {}) {
  return headers.map((header) => {
    if (overrides[header]) return overrides[header];
    if (header.includes("说明") || header.includes("基准")) return 24;
    if (header.includes("名称") || header.includes("机构")) return 30;
    if (header.includes("区间") || header.includes("来源")) return 18;
    if (header.includes("收益") || header.includes("回撤") || header.includes("波动") || header.includes("权重")) return 13;
    if (header.includes("是否")) return 10;
    return 14;
  });
}

async function main() {
  const [jsonPath, outputPath, previewDir] = process.argv.slice(2);
  if (!jsonPath || !outputPath) {
    throw new Error("Usage: node build_advisor_fof_mixed_performance_workbook.cjs <source.json> <output.xlsx> [previewDir]");
  }
  const data = JSON.parse(await fs.readFile(jsonPath, "utf8"));
  const workbook = Workbook.create();

  const mixedHeaders = [
    "排名",
    "产品类型",
    "基金主类型",
    "基金类型标签",
    "产品ID",
    "产品代码",
    "产品名称",
    "机构",
    "渠道",
    "管理人/经理",
    "是否对客",
    "是否广发",
    "展示状态",
    "数据状态",
    "成立日期",
    "是否FOF",
    "是否QDII",
    "是否ETF",
    "是否LOF",
    "是否REITs",
    "标准资产大类",
    "标准资产细类",
    "基准风险资产权重",
    "基准风险资产权重说明",
    "基准风险资产权重来源",
    "分类依据",
    "业务/公开分类",
    "FOF公开分类",
    "FOF基准细分分类",
    "风险等级",
    "基准权益权重",
    "基准债券权重",
    "基准货币权重",
    "基准商品权重",
    "基准海外权重",
    "基准未知权重",
    "业绩比较基准",
    "解析置信度",
    "解析置信度分数",
    ...intervalHeaders(),
    "详情链接",
  ];
  addSheetFromRows(
    workbook,
    "混排榜",
    mixedHeaders,
    data.rows || [],
    widthsFor(mixedHeaders, {
      排名: 9,
      产品类型: 12,
      基金主类型: 12,
      基金类型标签: 24,
      产品ID: 22,
      产品代码: 14,
      产品名称: 36,
      机构: 26,
      渠道: 18,
      "管理人/经理": 18,
      业绩比较基准: 56,
      详情链接: 34,
    }),
    "MixedRanking"
  );

  const bucketHeaders = [
    "基准风险资产权重",
    "基准风险资产权重说明",
    "产品类型",
    "基金主类型",
    "产品数",
    "上半年收益有效数",
    "上半年收益均值",
    "上半年收益中位数",
    "上半年最大回撤有效数",
    "上半年最大回撤均值",
    "上半年最大回撤中位数",
    "上半年年化波动率有效数",
    "上半年年化波动率均值",
    "上半年年化波动率中位数",
  ];
  addSheetFromRows(
    workbook,
    "分档汇总",
    bucketHeaders,
    data.bucketSummary || [],
    widthsFor(bucketHeaders, { 基准风险资产权重: 12, 基金主类型: 14, 产品数: 10 }),
    "BucketSummary",
    { headerFill: "#155E75" }
  );

  const qaHeaders = [
    "抽样维度",
    "产品类型",
    "基金主类型",
    "机构",
    "产品代码",
    "产品名称",
    "基准风险资产权重",
    "基准风险资产权重来源",
    "上半年收益率",
    "上半年最大回撤",
    "上半年年化波动率",
    "核对字段数",
    "最大收益差异_百分点",
    "最大风险差异_百分点",
    "核对状态",
    "核对说明",
  ];
  addSheetFromRows(
    workbook,
    "机构抽样核对",
    qaHeaders,
    data.qaRows || [],
    widthsFor(qaHeaders, { 抽样维度: 32, 产品名称: 38, 核对说明: 78 }),
    "InstitutionSampleQA",
    { headerFill: "#334155" }
  );

  const coverageHeaders = ["项目", "值", "说明"];
  addSheetFromRows(
    workbook,
    "数据覆盖与说明",
    coverageHeaders,
    [...buildMetaRows(data), ...(data.coverageRows || [])],
    [26, 34, 90],
    "CoverageNotes",
    { headerFill: "#6D28D9" }
  );

  const noteHeaders = ["字段", "说明"];
  addSheetFromRows(
    workbook,
    "字段说明",
    noteHeaders,
    data.fieldNotes || [],
    [24, 110],
    "FieldNotes",
    { headerFill: "#374151" }
  );

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
      混排榜: "A1:AZ35",
      分档汇总: "A1:N30",
      机构抽样核对: "A1:P35",
      数据覆盖与说明: "A1:C35",
      字段说明: "A1:B20",
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
