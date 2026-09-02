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
  return `A1:${colLetter(colCount - 1)}${Math.max(rowCount, 1)}`;
}

function rowsToMatrix(headers, rows) {
  return [headers, ...rows.map((row) => headers.map((header) => row[header] ?? null))];
}

function cloneRows(rows) {
  return rows.map((row) => ({ ...row }));
}

function convertPercentPoints(rows, fields) {
  const out = cloneRows(rows);
  for (const row of out) {
    for (const field of fields) {
      if (row[field] !== null && row[field] !== undefined && row[field] !== "") {
        row[field] = Number(row[field]) / 100;
      }
    }
  }
  return out;
}

function setWidths(sheet, widths) {
  widths.forEach((width, index) => {
    if (width) sheet.getRange(`${colLetter(index)}:${colLetter(index)}`).format.columnWidth = width;
  });
}

function styleBase(sheet) {
  sheet.showGridLines = false;
  sheet.getRange("A:AZ").format.font = { name: "Microsoft YaHei", size: 10 };
}

function styleTable(sheet, rowCount, colCount, headerFill = "#0F766E") {
  if (!rowCount || !colCount) return;
  sheet.freezePanes.freezeRows(1);
  const header = sheet.getRange(`A1:${colLetter(colCount - 1)}1`);
  header.format = {
    fill: headerFill,
    font: { bold: true, color: "#FFFFFF", name: "Microsoft YaHei", size: 10 },
    wrapText: true,
  };
  header.format.rowHeight = 32;
  const used = sheet.getRange(tableRange(rowCount, colCount));
  used.format.borders = {
    insideHorizontal: { style: "thin", color: "#E5E7EB" },
    bottom: { style: "thin", color: "#E5E7EB" },
  };
}

function addTableSheet(workbook, name, headers, rows, widths, percentPointFields, ratioFields, tableName, headerFill = "#0F766E") {
  const sheet = workbook.worksheets.add(name);
  styleBase(sheet);
  const formatted = convertPercentPoints(rows, percentPointFields);
  const matrix = rowsToMatrix(headers, formatted);
  sheet.getRangeByIndexes(0, 0, matrix.length, headers.length).values = matrix;
  styleTable(sheet, matrix.length, headers.length, headerFill);
  setWidths(sheet, widths);
  for (const field of percentPointFields) {
    const index = headers.indexOf(field);
    if (index >= 0 && matrix.length > 1) {
      sheet.getRange(`${colLetter(index)}2:${colLetter(index)}${matrix.length}`).format.numberFormat = "0.00%";
    }
  }
  for (const field of ratioFields) {
    const index = headers.indexOf(field);
    if (index >= 0 && matrix.length > 1) {
      sheet.getRange(`${colLetter(index)}2:${colLetter(index)}${matrix.length}`).format.numberFormat = "0.0%";
    }
  }
  if (tableName && matrix.length > 1) {
    sheet.tables.add(tableRange(matrix.length, headers.length), true, tableName);
  }
  return sheet;
}

function numberOrZero(value) {
  const number = Number(value);
  return Number.isFinite(number) ? number : 0;
}

function pctRatio(value) {
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

function pctPoint(value) {
  const number = Number(value);
  return Number.isFinite(number) ? number / 100 : null;
}

function countBy(rows, field) {
  const out = new Map();
  for (const row of rows) {
    const key = row[field] || "未分类";
    out.set(key, (out.get(key) || 0) + 1);
  }
  return [...out.entries()].map(([分类, 数量]) => ({ 分类, 数量 })).sort((a, b) => b.数量 - a.数量 || String(a.分类).localeCompare(String(b.分类), "zh-CN"));
}

async function main() {
  const [jsonPath, outputPath, previewDir] = process.argv.slice(2);
  if (!jsonPath || !outputPath) {
    throw new Error("Usage: node build_fof_benchmark_ranking_workbook.cjs <data.json> <output.xlsx> [previewDir]");
  }
  const data = JSON.parse(await fs.readFile(jsonPath, "utf8"));
  const workbook = Workbook.create();

  const meta = data.meta || {};
  const strategyRows = cloneRows(data.strategyRows || []).sort((a, b) => {
    const category = String(a.排名采用分类 || "").localeCompare(String(b.排名采用分类 || ""), "zh-CN");
    if (category !== 0) return category;
    return numberOrZero(a.同类FOF排名 || 999999) - numberOrZero(b.同类FOF排名 || 999999);
  });
  const fofRows = cloneRows(data.fofRows || []).sort((a, b) => String(a.FOF基准细分分类 || "").localeCompare(String(b.FOF基准细分分类 || ""), "zh-CN") || String(a.基金代码 || "").localeCompare(String(b.基金代码 || "")));

  const summary = workbook.worksheets.add("报告摘要");
  styleBase(summary);
  setWidths(summary, [22, 16, 28, 3, 20, 16, 16, 16, 18, 18]);
  summary.getRange("A1:J2").merge();
  summary.getRange("A1").values = [[meta.报告名称 || "FOF基准细分排名报表"]];
  summary.getRange("A1:J2").format = {
    fill: "#0F172A",
    font: { bold: true, color: "#FFFFFF", size: 16, name: "Microsoft YaHei" },
    wrapText: true,
  };
  summary.getRange("A3:J3").merge();
  summary.getRange("A3").values = [[`统计截止：${meta.基金收益截止锚点 || ""}；上游收益批次：${meta.上游收益数据批次 || ""}；生成时间：${meta.生成时间 || ""}`]];
  summary.getRange("A3:J3").format = { fill: "#ECFDF5", font: { bold: true, color: "#14532D", name: "Microsoft YaHei", size: 10 } };

  const f10Covered = numberOrZero(meta.F10业绩比较基准覆盖数);
  const highMid = numberOrZero((meta.FOF基准解析置信度分布 || {}).高) + numberOrZero((meta.FOF基准解析置信度分布 || {}).中);
  const basis = meta.策略排名口径分布 || {};
  const benchmarkRanked = numberOrZero(basis.FOF基准细分分类);
  const fallbackRanked = Object.entries(basis).filter(([key]) => key !== "FOF基准细分分类").reduce((sum, [, value]) => sum + numberOrZero(value), 0);
  const kpiRows = [
    ["FOF产品总数", numberOrZero(meta.FOF总数), "全市场 FOF 产品池"],
    ["F10基准覆盖", f10Covered, `${f10Covered}/${numberOrZero(meta.FOF总数)}，覆盖率 ${(numberOrZero(meta.F10业绩比较基准覆盖率) * 100).toFixed(1)}%`],
    ["高/中置信度", highMid, "可纳入基准细分排名的 FOF 基准样本"],
    ["基准细分重排策略", benchmarkRanked, "策略基准可解析且同类 FOF 样本充足"],
    ["回退原分类策略", fallbackRanked, "基准缺失、低置信度或细分类 FOF 样本不足"],
  ];
  summary.getRangeByIndexes(5, 0, kpiRows.length + 1, 3).values = [["指标", "数值", "说明"], ...kpiRows];
  styleTable(summary, 6 + kpiRows.length, 3, "#0F766E");
  summary.getRange(`B7:B${6 + kpiRows.length}`).format.numberFormat = "#,##0";

  const categoryRows = (data.rankingCategoryRows || []).slice(0, 18).map((row) => ({
    分类: row.分类,
    策略数量: row.策略数量,
    对客策略数量: row.对客策略数量,
    有收益FOF产品数: row.有收益FOF产品数,
    策略中位数H1收益率_百分比: pctPoint(row.策略中位数H1收益率_百分比),
    FOF中位数H1收益率_百分比: pctPoint(row.FOF中位数H1收益率_百分比),
    策略中位数排名百分位: pctRatio(row.策略中位数排名百分位),
  }));
  const categoryHeaders = ["分类", "策略数量", "对客策略数量", "有收益FOF产品数", "策略中位数H1收益率_百分比", "FOF中位数H1收益率_百分比", "策略中位数排名百分位"];
  summary.getRangeByIndexes(13, 0, categoryRows.length + 1, categoryHeaders.length).values = rowsToMatrix(categoryHeaders, categoryRows);
  styleTable(summary, 14 + categoryRows.length, categoryHeaders.length, "#164E63");
  summary.getRange(`E15:G${14 + categoryRows.length}`).format.numberFormat = "0.0%";
  const sampleChart = summary.charts.add("bar", summary.getRange(`A14:D${14 + categoryRows.length}`));
  sampleChart.title = "各排名分类的策略与FOF样本";
  sampleChart.hasLegend = true;
  sampleChart.xAxis = { axisType: "textAxis", textStyle: { fontSize: 9 } };
  sampleChart.yAxis = { numberFormatCode: "#,##0" };
  sampleChart.setPosition("I6", "T21");
  const returnStart = 42;
  const returnRows = [
    ["分类", "策略中位数H1收益率", "FOF中位数H1收益率"],
    ...categoryRows.map((row) => [row.分类, row.策略中位数H1收益率_百分比, row.FOF中位数H1收益率_百分比]),
  ];
  summary.getRangeByIndexes(returnStart - 1, 0, returnRows.length, returnRows[0].length).values = returnRows;
  summary.getRange(`B${returnStart + 1}:C${returnStart + returnRows.length - 1}`).format.numberFormat = "0.0%";
  const returnChart = summary.charts.add("bar", summary.getRange(`A${returnStart}:C${returnStart + returnRows.length - 1}`));
  returnChart.title = "同类策略与FOF中位数H1收益";
  returnChart.hasLegend = true;
  returnChart.xAxis = { axisType: "textAxis", textStyle: { fontSize: 9 } };
  returnChart.yAxis = { numberFormatCode: "0%" };
  returnChart.setPosition("I24", "T39");

  const percentPointFields = [
    "策略H1收益率_百分比",
    "策略H1年化收益率_百分比",
    "基准H1收益率_百分比",
    "相对基准超额_百分点",
    "权益基金权重_百分比",
    "债券基金权重_百分比",
    "货币基金权重_百分比",
    "混合基金权重_百分比",
    "QDII权重_百分比",
    "策略基准权益权重_百分比",
    "策略基准债券权重_百分比",
    "策略基准货币权重_百分比",
    "策略基准商品权重_百分比",
    "策略基准海外权重_百分比",
    "上半年收益率_百分比",
    "排行区间收益率_百分比",
    "今年以来收益率_百分比",
    "近6月收益率_百分比",
    "基准权益权重_百分比",
    "基准债券权重_百分比",
    "基准货币权重_百分比",
    "基准商品权重_百分比",
    "基准海外权重_百分比",
    "基准未知权重_百分比",
    "基准权重合计_百分比",
    "策略平均H1收益率_百分比",
    "策略中位数H1收益率_百分比",
    "FOF平均H1收益率_百分比",
    "FOF中位数H1收益率_百分比",
  ];
  const ratioFields = ["击败同类FOF比例", "排名位置百分位", "策略平均排名百分位", "策略中位数排名百分位", "解析置信度分数", "策略基准解析置信度分数"];

  const strategyHeaders = [
    "统一策略ID",
    "投顾机构",
    "策略名称",
    "是否对客",
    "业务分类",
    "FOF可比分类",
    "策略基准细分分类",
    "策略基准解析置信度",
    "排名采用分类口径",
    "排名采用分类",
    "策略H1收益率_百分比",
    "基准H1收益率_百分比",
    "同类FOF样本数",
    "同类FOF排名",
    "击败同类FOF比例",
    "排名位置百分位",
    "权益基金权重_百分比",
    "QDII权重_百分比",
    "业绩基准",
    "策略基准解析说明",
  ];
  addTableSheet(
    workbook,
    "策略排名",
    strategyHeaders,
    strategyRows,
    [22, 18, 32, 10, 18, 16, 18, 12, 28, 18, 14, 14, 12, 12, 12, 12, 14, 14, 54, 42],
    percentPointFields,
    ratioFields,
    "StrategyBenchmarkRanking",
    "#0F766E"
  );

  const fofHeaders = [
    "基金代码",
    "基金名称",
    "FOF公开分类",
    "FOF基准细分分类",
    "解析置信度",
    "F10基金类型",
    "基准权益权重_百分比",
    "基准债券权重_百分比",
    "基准货币权重_百分比",
    "基准商品权重_百分比",
    "基准海外权重_百分比",
    "基准未知权重_百分比",
    "基准权重合计_百分比",
    "业绩比较基准",
    "上半年收益率_百分比",
    "FOF可比分类",
    "基金公司",
    "基金经理",
    "F10采集状态",
    "数据状态",
  ];
  addTableSheet(
    workbook,
    "FOF产品池",
    fofHeaders,
    fofRows,
    [12, 36, 16, 18, 10, 20, 14, 14, 14, 14, 14, 14, 14, 64, 14, 14, 18, 18, 12, 20],
    percentPointFields,
    ratioFields,
    "FofBenchmarkUniverse",
    "#7C2D12"
  );

  const summaryHeaders = ["分类", "策略数量", "对客策略数量", "FOF产品总数", "有收益FOF产品数", "策略平均H1收益率_百分比", "策略中位数H1收益率_百分比", "FOF平均H1收益率_百分比", "FOF中位数H1收益率_百分比", "策略平均排名百分位", "策略中位数排名百分位"];
  addTableSheet(
    workbook,
    "基准细分汇总",
    summaryHeaders,
    data.benchmarkCategoryRows || [],
    [22, 12, 14, 14, 16, 16, 18, 16, 18, 16, 18],
    percentPointFields,
    ratioFields,
    "BenchmarkCategorySummary",
    "#164E63"
  );
  addTableSheet(
    workbook,
    "排名口径汇总",
    summaryHeaders,
    data.rankingCategoryRows || [],
    [22, 12, 14, 14, 16, 16, 18, 16, 18, 16, 18],
    percentPointFields,
    ratioFields,
    "RankingCategorySummary",
    "#334155"
  );
  addTableSheet(
    workbook,
    "公开分类汇总",
    summaryHeaders,
    data.publicCategoryRows || [],
    [22, 12, 14, 14, 16, 16, 18, 16, 18, 16, 18],
    percentPointFields,
    ratioFields,
    "PublicCategorySummary",
    "#155E75"
  );

  const qualityRows = [
    { 项目: "FOF总数", 数量: meta.FOF总数, 说明: "本次全量 FOF 产品池" },
    { 项目: "F10基准覆盖数", 数量: meta.F10业绩比较基准覆盖数, 说明: "天天 F10 可读取业绩比较基准的 FOF 数量" },
    { 项目: "高置信度", 数量: (meta.FOF基准解析置信度分布 || {}).高 || 0, 说明: "权重完整、未知成分少" },
    { 项目: "中置信度", 数量: (meta.FOF基准解析置信度分布 || {}).中 || 0, 说明: "可用于分组，但存在少量解析瑕疵" },
    { 项目: "低置信度", 数量: (meta.FOF基准解析置信度分布 || {}).低 || 0, 说明: "不直接用于基准细分排名" },
    { 项目: "基准细分重排策略", 数量: benchmarkRanked, 说明: "已按 FOF基准细分分类重排" },
    { 项目: "回退策略", 数量: fallbackRanked, 说明: "保留原 FOF可比分类排名" },
  ];
  const missingRows = (data.missingBenchmarkRows || []).slice(0, 80).map((row) => ({
    项目: "缺F10基准",
    基金代码: row.基金代码,
    名称: row.基金名称,
    分类: row.FOF公开分类,
    说明: row.F10采集状态,
  }));
  const lowRows = (data.lowConfidenceRows || []).slice(0, 80).map((row) => ({
    项目: "低置信度",
    基金代码: row.基金代码,
    名称: row.基金名称,
    分类: row.FOF公开分类,
    说明: row.基准解析说明,
  }));
  const quality = workbook.worksheets.add("数据质量");
  styleBase(quality);
  quality.getRangeByIndexes(0, 0, qualityRows.length + 1, 3).values = rowsToMatrix(["项目", "数量", "说明"], qualityRows);
  styleTable(quality, qualityRows.length + 1, 3, "#7F1D1D");
  quality.getRange("B2:B20").format.numberFormat = "#,##0";
  const issueHeaders = ["项目", "基金代码", "名称", "分类", "说明"];
  const issueRows = [...missingRows, ...lowRows];
  quality.getRangeByIndexes(qualityRows.length + 3, 0, issueRows.length + 1, issueHeaders.length).values = rowsToMatrix(issueHeaders, issueRows);
  styleTable(quality, qualityRows.length + 4 + issueRows.length, issueHeaders.length, "#92400E");
  setWidths(quality, [16, 12, 36, 18, 72]);

  addTableSheet(
    workbook,
    "计算说明",
    ["项目", "说明"],
    data.notes || [],
    [24, 120],
    [],
    [],
    "MethodNotes",
    "#4338CA"
  );

  const errorScan = await workbook.inspect({
    kind: "match",
    searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A",
    options: { useRegex: true, maxResults: 300 },
    summary: "final formula error scan",
  });
  console.log(errorScan.ndjson);

  if (previewDir) {
    await fs.mkdir(previewDir, { recursive: true });
    const previews = {
      报告摘要: "A1:T40",
      策略排名: "A1:T35",
      FOF产品池: "A1:T35",
      基准细分汇总: "A1:K35",
      排名口径汇总: "A1:K35",
      公开分类汇总: "A1:K35",
      数据质量: "A1:E60",
      计算说明: "A1:B20",
    };
    for (const [sheetName, range] of Object.entries(previews)) {
      const preview = await workbook.render({ sheetName, range, scale: 1, format: "png" });
      await fs.writeFile(path.join(previewDir, `${sheetName}.png`), new Uint8Array(await preview.arrayBuffer()));
    }
  }

  await fs.mkdir(path.dirname(outputPath), { recursive: true });
  const xlsx = await SpreadsheetFile.exportXlsx(workbook);
  await xlsx.save(outputPath);
  console.log(`[xlsx] ${outputPath}`);
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
