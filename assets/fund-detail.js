
(() => {
  const B = window.BasicData;
  const root = B.byId("fundDetailPage");
  const pack = window.__BASIC_DATA__?.fundDetailPack;
  const query = new URLSearchParams(window.location.search);
  const requestedCode = (query.get("code") || "").trim();
  const requestedName = (query.get("name") || "").trim();

  function empty(message) {
    root.innerHTML = `<section class="panel"><div class="empty">${B.esc(message)}</div></section>`;
  }

  if (!pack || !Array.isArray(pack.funds)) {
    empty("未找到基金详情数据包，请先运行报告数据包重建步骤。");
    return;
  }

  const fundFields = pack.fundFields || [];
  const holdingFields = pack.holdingFields || [];
  const monthlyFields = pack.monthlyFields || [];
  const funds = pack.funds || [];
  const holdings = pack.holdings || [];
  const monthly = pack.monthly || [];

  function toObject(fields, row) {
    return Object.fromEntries((fields || []).map((field, index) => [field, row?.[index] ?? ""]));
  }

  const codeField = fundFields[0] || "基金代码";
  const nameField = fundFields[1] || "基金名称";
  const fundObjects = funds.map((row, index) => ({ index, row, data: toObject(fundFields, row) }));
  const matched = fundObjects.find((item) => {
    const code = String(item.data[codeField] || "").trim();
    const name = String(item.data[nameField] || "").trim();
    return (requestedCode && code === requestedCode) || (requestedName && name === requestedName);
  });

  if (!matched) {
    empty("未找到匹配的基金。");
    return;
  }

  function isPctField(field) {
    return /权重|收益|增配|减配|比例|占比|中位/.test(String(field || ""));
  }

  function valueHtml(field, value) {
    if (value === null || value === undefined || value === "") return '<span class="value-muted">未披露</span>';
    const number = Number(value);
    if (Number.isFinite(number) && isPctField(field)) return B.pctSigned(number);
    if (Number.isFinite(number)) return B.fmt(number);
    return B.esc(value);
  }

  function factGrid(fields, data) {
    return `<div class="stat-grid">${fields.map((field) => `
      <div class="metric-card">
        <span>${B.label(field)}</span>
        <strong>${valueHtml(field, data[field])}</strong>
      </div>
    `).join("")}</div>`;
  }

  function table(fields, rows, formatter) {
    const head = fields.map((field) => `<th>${B.label(field)}</th>`).join("");
    const body = rows.length
      ? rows.map((row) => `<tr>${fields.map((field) => `<td>${formatter ? formatter(row, field) : valueHtml(field, row[field])}</td>`).join("")}</tr>`).join("")
      : `<tr><td colspan="${fields.length}"><div class="empty">暂无数据</div></td></tr>`;
    return `<div class="table-wrap"><table><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table></div>`;
  }

  const fundData = matched.data;
  const fundIndex = matched.index;
  const fundHoldings = holdings
    .filter((row) => Number(row?.[0]) === fundIndex)
    .map((row) => toObject(holdingFields, row))
    .sort((a, b) => Number(b[holdingFields[12]] || 0) - Number(a[holdingFields[12]] || 0));
  const fundMonthly = monthly
    .filter((row) => Number(row?.[0]) === fundIndex)
    .map((row) => toObject(monthlyFields, row))
    .sort((a, b) => String(b[monthlyFields[1]] || "").localeCompare(String(a[monthlyFields[1]] || "")));

  const summaryFields = [0, 1, 2, 3, 6, 7, 12, 14, 15, 16, 17, 18, 19, 20, 21, 22]
    .map((index) => fundFields[index])
    .filter(Boolean);
  const holdingDisplayFields = [2, 3, 4, 5, 6, 7, 8, 10, 11, 12, 13, 14, 15]
    .map((index) => holdingFields[index])
    .filter(Boolean);
  const monthlyDisplayFields = monthlyFields.slice(1);

  root.innerHTML = `
    <section class="panel hero-panel">
      <div class="panel-head">
        <div>
          <p class="eyebrow">底层基金详情</p>
          <h1>${B.esc(fundData[nameField] || "未命名基金")}</h1>
          <p class="desc">${B.esc(fundData[codeField] || "未披露代码")}｜${B.esc(fundData[fundFields[2]] || "未披露基金公司")}｜${B.esc(fundData[fundFields[3]] || "未披露类型")}</p>
        </div>
        <a class="link" href="./insights.html">返回数据洞察</a>
      </div>
      ${factGrid(summaryFields, fundData)}
    </section>
    <section class="panel">
      <div class="panel-head">
        <div>
          <h2>持仓策略</h2>
          <p class="desc">按期末持仓比例排序，共 ${fundHoldings.length.toLocaleString("zh-CN")} 条策略持仓记录。</p>
        </div>
      </div>
      ${table(holdingDisplayFields, fundHoldings)}
    </section>
    <section class="panel">
      <div class="panel-head">
        <div>
          <h2>月度调仓</h2>
          <p class="desc">展示该基金在策略调仓中的净增配、加仓和减仓权重。</p>
        </div>
      </div>
      ${table(monthlyDisplayFields, fundMonthly.slice(0, 60))}
    </section>
  `;
})();
