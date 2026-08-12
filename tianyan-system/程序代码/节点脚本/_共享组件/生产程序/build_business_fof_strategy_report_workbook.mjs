import fs from "node:fs/promises";
import path from "node:path";
import { SpreadsheetFile, Workbook } from "@oai/artifact-tool";

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

function rangeAddress(rowCount, colCount) {
  return `A1:${colLetter(colCount - 1)}${Math.max(rowCount, 1)}`;
}

function rowsToMatrix(headers, rows) {
  return [headers, ...rows.map((row) => headers.map((header) => row[header] ?? null))];
}

function percentValue(value) {
  if (value === null || value === undefined || value === "") return null;
  return Number(value) / 100;
}

function formatPercentRows(rows, fields) {
  return rows.map((row) => {
    const next = { ...row };
    for (const field of fields) {
      if (next[field] !== null && next[field] !== undefined && next[field] !== "") {
        next[field] = percentValue(next[field]);
      }
    }
    return next;
  });
}

function setWidths(sheet, widths) {
  widths.forEach((width, idx) => {
    if (width) sheet.getRange(`${colLetter(idx)}:${colLetter(idx)}`).format.columnWidth = width;
  });
}

function styleSheet(sheet) {
  sheet.showGridLines = false;
  sheet.getRange("A:Z").format.font = { name: "Microsoft YaHei", size: 10 };
}

function styleTable(sheet, rowCount, colCount, headerFill = "#14532D") {
  if (!rowCount || !colCount) return;
  const header = sheet.getRange(`A1:${colLetter(colCount - 1)}1`);
  header.format = {
    fill: headerFill,
    font: { bold: true, color: "#FFFFFF", name: "Microsoft YaHei", size: 10 },
    wrapText: true,
  };
  header.format.rowHeight = 30;
  const used = sheet.getRange(rangeAddress(rowCount, colCount));
  used.format.borders = {
    insideHorizontal: { style: "thin", color: "#E5E7EB" },
    bottom: { style: "thin", color: "#E5E7EB" },
  };
  sheet.freezePanes.freezeRows(1);
}

function addTableSheet(workbook, name, headers, rows, widths, percentFields = [], tableName = null) {
  const sheet = workbook.worksheets.add(name);
  styleSheet(sheet);
  const formatted = formatPercentRows(rows, percentFields);
  const matrix = rowsToMatrix(headers, formatted);
  sheet.getRangeByIndexes(0, 0, matrix.length, headers.length).values = matrix;
  styleTable(sheet, matrix.length, headers.length);
  setWidths(sheet, widths);
  for (const field of percentFields) {
    const idx = headers.indexOf(field);
    if (idx >= 0 && matrix.length > 1) {
      sheet.getRange(`${colLetter(idx)}2:${colLetter(idx)}${matrix.length}`).format.numberFormat = "0.0%";
    }
  }
  if (tableName && matrix.length > 1) {
    sheet.tables.add(rangeAddress(matrix.length, headers.length), true, tableName);
  }
  return sheet;
}

function mergeTitle(sheet, range, value, fill = "#0F172A") {
  sheet.getRange(range).merge();
  sheet.getRange(range.split(":")[0]).values = [[value]];
  sheet.getRange(range).format = {
    fill,
    font: { bold: true, color: "#FFFFFF", size: 16, name: "Microsoft YaHei" },
    wrapText: true,
  };
}

function writeBlock(sheet, startRow, startCol, rows) {
  if (!rows.length || !rows[0].length) return;
  sheet.getRangeByIndexes(startRow, startCol, rows.length, rows[0].length).values = rows;
}

function chartRange(sheetName, startRow, startCol, rowCount, colCount) {
  const first = `${colLetter(startCol)}${startRow + 1}`;
  const last = `${colLetter(startCol + colCount - 1)}${startRow + rowCount}`;
  return `'${sheetName}'!$${first.replace(/([A-Z]+)(\d+)/, "$1$$$2")}:$${last.replace(/([A-Z]+)(\d+)/, "$1$$$2")}`;
}

function safeNumber(value) {
  return value === null || value === undefined || value === "" ? null : Number(value);
}

async function main() {
  const [jsonPath, outputPath, previewDir] = process.argv.slice(2);
  if (!jsonPath || !outputPath) {
    throw new Error("Usage: node build_business_fof_strategy_report_workbook.mjs <data.json> <output.xlsx> [previewDir]");
  }
  const data = JSON.parse(await fs.readFile(jsonPath, "utf8"));
  const workbook = Workbook.create();

  const categories = ["稳健型", "均衡型", "进取型", "海外/QDII", "其他"];
  const summaryMap = new Map(data.categorySummary.map((row) => [row.产品定位分类, row]));
  const summaryRows = categories.map((category) => summaryMap.get(category)).filter(Boolean);

  const report = workbook.worksheets.add("报告摘要");
  styleSheet(report);
  setWidths(report, [16, 12, 30, 3, 14, 14, 14, 14, 14, 14, 14]);
  mergeTitle(report, "A1:K2", data.meta.报告名称);
  report.getRange("A3:K3").merge();
  report.getRange("A3").values = [[`统计截止日：${data.meta.统计截止日}    主排名口径：${data.meta.主排名口径}`]];
  report.getRange("A3").format = { fill: "#ECFDF5", font: { color: "#14532D", bold: true, name: "Microsoft YaHei", size: 11 } };

  const kpis = [
    ["投顾策略", data.meta.投顾策略数, "覆盖全市场投顾策略"],
    ["对客策略", data.meta.对客策略数, "当前对业务展示的策略"],
    ["FOF产品", data.meta.FOF产品数, "全市场FOF产品池"],
    ["有收益FOF", data.meta.有收益FOF数, "可参与收益对比的FOF"],
  ];
  writeBlock(report, 5, 0, kpis.map((row) => [row[0], row[1], row[2]]));
  report.getRange("A6:C9").format = {
    fill: "#F8FAFC",
    borders: { preset: "outside", style: "thin", color: "#CBD5E1" },
    font: { name: "Microsoft YaHei", size: 10 },
  };
  report.getRange("B6:B9").format = { font: { bold: true, size: 16, color: "#0F766E", name: "Microsoft YaHei" }, numberFormat: "#,##0" };

  const conclusions = [
    ["核心结论", "稳健、均衡、进取三类的FOF对照样本较充足，适合做同类收益排名。"],
    ["分类判断", "主排名采用产品定位可比口径；资产细分分类用于解释投顾策略的风险暴露，不单独替代主排名。"],
    ["重点提醒", "海外/QDII类FOF收益样本偏少，排名只适合作为参考，不建议形成强结论。"],
    ["阅读方式", "先看图表页判断样本是否足够，再看多区间排名判断策略表现是否稳定。"],
  ];
  report.getRange("E6:K6").merge();
  report.getRange("E6").values = [["业务结论"]];
  report.getRange("E6:K6").format = { fill: "#92400E", font: { bold: true, color: "#FFFFFF", name: "Microsoft YaHei", size: 11 } };
  conclusions.forEach((row, idx) => {
    const excelRow = 7 + idx * 2;
    report.getRange(`E${excelRow}:E${excelRow + 1}`).merge();
    report.getRange(`F${excelRow}:K${excelRow + 1}`).merge();
    report.getRange(`E${excelRow}`).values = [[row[0]]];
    report.getRange(`F${excelRow}`).values = [[row[1]]];
  });
  report.getRange("E7:K14").format = {
    fill: "#FFFBEB",
    borders: { preset: "all", style: "thin", color: "#FDE68A" },
    wrapText: true,
    font: { name: "Microsoft YaHei", size: 10 },
  };
  report.getRange("E7:E14").format.font = { bold: true, color: "#92400E", name: "Microsoft YaHei", size: 10 };

  const summaryHeaders = ["产品定位分类", "投顾策略数", "对客策略数", "FOF产品数", "有收益FOF数", "2026上半年策略中位收益", "2026上半年FOF中位收益", "样本判断"];
  const summaryMatrix = rowsToMatrix(summaryHeaders, formatPercentRows(summaryRows, ["2026上半年策略中位收益", "2026上半年FOF中位收益"]));
  writeBlock(report, 17, 0, summaryMatrix);
  styleTable(report, 17 + summaryMatrix.length, summaryHeaders.length, "#0F766E");
  report.getRange("F19:G23").format.numberFormat = "0.0%";
  setWidths(report, [16, 12, 30, 12, 12, 16, 16, 20]);

  const charts = workbook.worksheets.add("图表页");
  styleSheet(charts);
  charts.getRange("A:Q").format.columnWidth = 13;
  mergeTitle(charts, "A1:Q2", "图表解读：先看样本，再看收益，再看稳定性", "#164E63");

  const coverageTable = [["产品定位分类", "投顾策略", "对客策略", "有收益FOF"], ...summaryRows.map((row) => [row.产品定位分类, row.投顾策略数, row.对客策略数, row.有收益FOF数])];
  writeBlock(charts, 4, 0, coverageTable);
  charts.getRangeByIndexes(4, 0, coverageTable.length, coverageTable[0].length).format.font = { name: "Microsoft YaHei", size: 10 };
  const coverageChart = charts.charts.add("bar", charts.getRange(`A5:D${4 + coverageTable.length}`));
  coverageChart.title = "样本覆盖：稳健、均衡、进取具备主要对照池";
  coverageChart.hasLegend = true;
  coverageChart.xAxis = { axisType: "textAxis", textStyle: { fontSize: 9 } };
  coverageChart.yAxis = { numberFormatCode: "#,##0" };
  coverageChart.setPosition("F4", "Q18");
  charts.getRange("F19:Q20").merge();
  charts.getRange("F19").values = [["口径说明：本图使用产品定位可比口径；投顾策略与FOF产品都先归入稳健、均衡、进取、海外等组，再比较每组样本是否充足。"]];
  charts.getRange("F19:Q20").format = { fill: "#F0FDFA", wrapText: true, font: { color: "#115E59", name: "Microsoft YaHei", size: 10 } };

  const h1Table = [["产品定位分类", "策略中位收益", "FOF中位收益"], ...summaryRows.map((row) => [row.产品定位分类, percentValue(row["2026上半年策略中位收益"]), percentValue(row["2026上半年FOF中位收益"])])];
  writeBlock(charts, 23, 0, h1Table);
  charts.getRange(`B25:C${23 + h1Table.length}`).format.numberFormat = "0.0%";
  const h1Chart = charts.charts.add("bar", charts.getRange(`A24:C${23 + h1Table.length}`));
  h1Chart.title = "2026上半年：同类策略与FOF中位收益对比";
  h1Chart.hasLegend = true;
  h1Chart.xAxis = { axisType: "textAxis", textStyle: { fontSize: 9 } };
  h1Chart.yAxis = { numberFormatCode: "0%" };
  h1Chart.setPosition("F23", "Q37");
  charts.getRange("F38:Q39").merge();
  charts.getRange("F38").values = [["口径说明：本图仍使用产品定位可比口径，展示同一分类内投顾策略和FOF产品的中位收益，不跨分类比较。"]];
  charts.getRange("F38:Q39").format = { fill: "#EFF6FF", wrapText: true, font: { color: "#1D4ED8", name: "Microsoft YaHei", size: 10 } };

  const intervalLabels = data.intervals.map((item) => item.label);
  const intervalRows = [["收益区间", "稳健型", "均衡型", "进取型"], ...intervalLabels.map((label) => {
    const row = [label];
    for (const category of ["稳健型", "均衡型", "进取型"]) {
      const found = data.intervalSummary.find((item) => item.收益区间 === label && item.产品定位分类 === category);
      row.push(safeNumber(found?.平均击败比例));
    }
    return row;
  })];
  writeBlock(charts, 43, 0, intervalRows);
  charts.getRange(`B45:D${43 + intervalRows.length}`).format.numberFormat = "0%";
  const intervalChart = charts.charts.add("line", charts.getRange(`A44:D${43 + intervalRows.length}`));
  intervalChart.title = "多区间表现：平均击败同类FOF比例";
  intervalChart.hasLegend = true;
  intervalChart.xAxis = { axisType: "textAxis", textStyle: { fontSize: 9 } };
  intervalChart.yAxis = { numberFormatCode: "0%", min: 0, max: 1 };
  intervalChart.setPosition("F43", "Q57");
  charts.getRange("F58:Q59").merge();
  charts.getRange("F58").values = [["口径说明：本图仅展示FOF样本相对充足的稳健、均衡、进取三类。数值越高，表示该类投顾策略在该收益区间内整体越靠前。"]];
  charts.getRange("F58:Q59").format = { fill: "#F7FEE7", wrapText: true, font: { color: "#3F6212", name: "Microsoft YaHei", size: 10 } };

  const rules = workbook.worksheets.add("分类口径");
  styleSheet(rules);
  mergeTitle(rules, "A1:H2", "分类口径说明", "#365314");
  const ruleRows = [
    ["口径", "定位", "适合回答的问题", "投顾策略归类方式", "FOF产品归类方式", "报告中的使用方式"],
    ["产品定位可比口径", "主排名口径", "同一类产品中，策略相对FOF处在什么位置", "按海外特征和权益占比映射到稳健、均衡、进取、海外等组", "按公开产品定位归入稳健、均衡、进取、海外等组", "用于多区间排名和核心图表"],
    ["资产细分口径", "辅助解释口径", "策略本身偏债、均衡还是偏股", "按权益占比细分为现金/低波、稳健、均衡偏债、均衡、均衡偏股、高权益等", "当前FOF全市场穿透覆盖不足，不作为全市场主排名依据", "用于解释策略风险暴露"],
    ["表现稳定性口径", "结果校验口径", "表现是否只依赖单一区间", "比较近1月、近3月、近6月、近1年和上半年多个区间", "同样在产品定位可比组内比较", "用于筛选表现更稳定的策略"],
    ["特殊标签口径", "筛选口径", "哪些策略不适合直接混排", "识别海外、信号类、目标盈、已停止、样本偏少等标签", "识别海外和样本偏少组", "用于提醒业务谨慎解读"],
  ];
  writeBlock(rules, 4, 0, ruleRows);
  styleTable(rules, 4 + ruleRows.length, ruleRows[0].length, "#4D7C0F");
  mergeTitle(rules, "A1:H2", "分类口径说明", "#365314");
  rules.getRange("A3:F3").merge();
  rules.getRange("A3").values = [["分类口径说明"]];
  rules.getRange("A3:F3").format = {
    fill: "#365314",
    font: { bold: true, color: "#FFFFFF", name: "Microsoft YaHei", size: 13 },
  };
  rules.getRange("A5:F5").format = {
    fill: "#4D7C0F",
    font: { bold: true, color: "#FFFFFF", name: "Microsoft YaHei", size: 10 },
    wrapText: true,
  };
  setWidths(rules, [20, 16, 34, 42, 42, 30]);
  rules.getRange("A5:F9").format.wrapText = true;

  const categoryHeaders = ["产品定位分类", "投顾策略数", "对客策略数", "FOF产品数", "有收益FOF数", "2026上半年策略中位收益", "2026上半年FOF中位收益", "样本判断"];
  addTableSheet(
    workbook,
    "分类对比",
    categoryHeaders,
    summaryRows,
    [16, 12, 12, 12, 12, 18, 18, 20],
    ["2026上半年策略中位收益", "2026上半年FOF中位收益"],
    "CategoryComparison"
  );

  const matrixHeaders = ["产品定位分类", ...["现金/低波", "稳健型", "均衡偏债", "均衡型", "均衡偏股", "高权益/进取", "海外/QDII", "其他/需复核"]];
  const matrixSheet = addTableSheet(
    workbook,
    "资产细分矩阵",
    matrixHeaders,
    data.classificationMatrix,
    [16, 12, 12, 12, 12, 12, 14, 14, 14],
    [],
    "AssetMatrix"
  );
  matrixSheet.getRange("A1:I1").format.fill = "#7C2D12";

  const intervalHeaders = [
    "机构",
    "产品名称",
    "是否对客",
    "产品定位分类",
    "资产细分分类",
    "特殊标签",
    "2026上半年收益率",
    "2026上半年同类FOF数",
    "2026上半年同类排名",
    "2026上半年击败比例",
    "2026上半年排名状态",
    "近1月收益率",
    "近1月击败比例",
    "近3月收益率",
    "近3月击败比例",
    "近6月收益率",
    "近6月击败比例",
    "近1年收益率",
    "近1年击败比例",
    "分类建议",
  ];
  addTableSheet(
    workbook,
    "多区间排名",
    intervalHeaders,
    data.strategyRows,
    [18, 32, 10, 14, 14, 18, 14, 14, 14, 14, 20, 12, 12, 12, 12, 12, 12, 12, 12, 18],
    ["2026上半年收益率", "2026上半年击败比例", "近1月收益率", "近1月击败比例", "近3月收益率", "近3月击败比例", "近6月收益率", "近6月击败比例", "近1年收益率", "近1年击败比例"],
    "IntervalRanking"
  );

  const topHeaders = [
    "机构",
    "产品名称",
    "是否对客",
    "产品定位分类",
    "资产细分分类",
    "2026上半年收益率",
    "2026上半年同类排名",
    "2026上半年击败比例",
    "近3月击败比例",
    "近6月击败比例",
    "近1年击败比例",
    "特殊标签",
  ];
  addTableSheet(
    workbook,
    "重点策略",
    topHeaders,
    data.topRankRows,
    [18, 32, 10, 14, 14, 14, 14, 14, 12, 12, 12, 18],
    ["2026上半年收益率", "2026上半年击败比例", "近3月击败比例", "近6月击败比例", "近1年击败比例"],
    "TopStrategies"
  );

  const fofHeaders = ["产品代码", "产品名称", "产品定位分类", "产品公开分类", "数据状态", "2026上半年收益率", "近1月收益率", "近3月收益率", "近6月收益率", "近1年收益率"];
  addTableSheet(
    workbook,
    "FOF对照池",
    fofHeaders,
    data.fofRows,
    [12, 36, 14, 20, 12, 14, 12, 12, 12, 12],
    ["2026上半年收益率", "近1月收益率", "近3月收益率", "近6月收益率", "近1年收益率"],
    "FofPool"
  );

  const boundary = workbook.worksheets.add("阅读边界");
  styleSheet(boundary);
  mergeTitle(boundary, "A1:F2", "阅读边界与业务提醒", "#7F1D1D");
  const boundaryRows = [
    ["主题", "业务说明"],
    ...data.businessNotes.map((row) => [row.主题, row.说明]),
    ["样本充足性", "稳健、均衡、进取三类适合看同类排名；海外/QDII样本偏少，只适合方向性参考。"],
    ["排名解释", "击败比例表示策略收益高于同类FOF的比例；例如80%表示高于同类中约八成FOF。"],
    ["不建议的解读", "不要把不同分类之间的收益排名直接横向比较，也不要把单一区间表现作为唯一判断。"],
  ];
  writeBlock(boundary, 4, 0, boundaryRows);
  styleTable(boundary, 4 + boundaryRows.length, 2, "#991B1B");
  mergeTitle(boundary, "A1:F2", "阅读边界与业务提醒", "#7F1D1D");
  boundary.getRange("A3:B3").merge();
  boundary.getRange("A3").values = [["阅读边界与业务提醒"]];
  boundary.getRange("A3:B3").format = {
    fill: "#7F1D1D",
    font: { bold: true, color: "#FFFFFF", name: "Microsoft YaHei", size: 13 },
  };
  boundary.getRange("A5:B5").format = {
    fill: "#991B1B",
    font: { bold: true, color: "#FFFFFF", name: "Microsoft YaHei", size: 10 },
  };
  setWidths(boundary, [22, 95]);
  boundary.getRange(`A5:B${4 + boundaryRows.length}`).format.wrapText = true;

  const errors = await workbook.inspect({
    kind: "match",
    searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A",
    options: { useRegex: true, maxResults: 200 },
    summary: "final formula error scan",
  });
  console.log(errors.ndjson);

  if (previewDir) {
    await fs.mkdir(previewDir, { recursive: true });
    const previews = {
      报告摘要: "A1:K24",
      图表页: "A1:Q60",
      分类口径: "A1:F10",
      多区间排名: "A1:T30",
      阅读边界: "A1:B12",
    };
    for (const [sheetName, range] of Object.entries(previews)) {
      const preview = await workbook.render({ sheetName, range, scale: 1, format: "png" });
      await fs.writeFile(path.join(previewDir, `${sheetName}.png`), new Uint8Array(await preview.arrayBuffer()));
    }
  }

  await fs.mkdir(path.dirname(outputPath), { recursive: true });
  const xlsx = await SpreadsheetFile.exportXlsx(workbook);
  await xlsx.save(outputPath);
  console.log(JSON.stringify({ outputPath }, null, 2));
}

await main();
