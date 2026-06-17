
(() => {
  const B = window.BasicData;
  const root = B.byId("fundDetailPage");
  const pack = window.__BASIC_DATA__?.fundDetailPack;
  const semanticIndex = window.__AI_STRATEGY_SEMANTIC_INDEX__ || null;
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

  function raw(value) {
    return value === null || value === undefined ? "" : String(value);
  }

  function num(value) {
    if (value === null || value === undefined || value === "") return null;
    const number = Number(value);
    return Number.isFinite(number) ? number : null;
  }

  function isPctField(field) {
    return /权重|收益|增配|减配|比例|占比|中位|近\d|今年以来|成立以来|涨跌幅|回撤|波动率|投顾费率/.test(String(field || ""));
  }

  function isTextField(field) {
    return /代码|ID|编号/.test(String(field || ""));
  }

  function valueHtml(field, value) {
    if (value === null || value === undefined || value === "") return '<span class="value-muted">未披露</span>';
    if (isTextField(field)) return B.esc(String(value));
    const number = Number(value);
    if (Number.isFinite(number) && isPctField(field)) return B.pctSigned(number);
    if (Number.isFinite(number)) return B.fmt(number);
    return B.esc(value);
  }

  function semanticRows(packName) {
    const semanticPack = semanticIndex?.[packName];
    if (!semanticPack || !Array.isArray(semanticPack.rows)) return [];
    const fields = semanticPack.fields || [];
    return semanticPack.rows.map((row) => Object.fromEntries(fields.map((field, index) => [field, row[index] ?? ""])));
  }

  function fundEntityRows(fundData) {
    const code = raw(fundData[codeField]).trim();
    const name = raw(fundData[nameField]).trim();
    return semanticRows("fundEntities")
      .filter((row) => (code && raw(row.基金代码).trim() === code) || (name && raw(row.基金名称).trim() === name))
      .sort((a, b) => {
        const typeOrder = ["资产大类", "资产", "指数", "地域", "行业主题", "产品形态", "风格"];
        const av = typeOrder.indexOf(raw(a.实体类型));
        const bv = typeOrder.indexOf(raw(b.实体类型));
        if (av !== bv) return (av < 0 ? 99 : av) - (bv < 0 ? 99 : bv);
        return (num(b.暴露比例) || 0) - (num(a.暴露比例) || 0);
      });
  }

  function fundEntityBadge(row) {
    const source = [row.来源字段 || row.来源, row.来源值].filter(Boolean).join("：");
    const meta = [row.实体等级, source, row.抽取规则ID].filter(Boolean).join("｜");
    return `<div class="entity-badge">
      <div><strong>${B.esc(row.实体名称 || row.实体Key || "未命名实体")}</strong><span>${B.esc(row.实体类型 || "实体")}｜${B.esc(meta || "未披露来源")}</span></div>
      <em>${num(row.暴露比例) !== null ? B.pct(row.暴露比例) : "未量化"}</em>
      ${row.证据 ? `<p>${B.esc(row.证据)}</p>` : ""}
      ${row.规则版本 ? `<p>规则版本：${B.esc(row.规则版本)}</p>` : ""}
    </div>`;
  }

  function fundEntitySection(fundData) {
    const rows = fundEntityRows(fundData);
    const groups = ["资产大类", "资产", "指数", "地域", "行业主题", "产品形态", "风格"].map((type) => ({
      type,
      rows: rows.filter((row) => row.实体类型 === type).slice(0, 12),
    })).filter((group) => group.rows.length);
    return `<section class="panel entity-panel">
      <div class="panel-head">
        <div>
          <h2>基金实体描述</h2>
          <p class="desc">基于基金分类、资产暴露、行业主题、研报大类资产和基金名称抽取；暴露比例来自基金资产暴露或结构化分类。</p>
        </div>
        <span class="pill">${rows.length.toLocaleString("zh-CN")} 个实体</span>
      </div>
      ${groups.length ? groups.map((group) => `
        <div class="entity-group">
          <h3>${B.esc(group.type)}</h3>
          <div class="entity-grid">${group.rows.map(fundEntityBadge).join("")}</div>
        </div>
      `).join("") : '<div class="empty">当前基金暂无可展示实体。请先重建 AI 语义索引。</div>'}
    </section>`;
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

  function pickFields(fields, names) {
    return names.filter((name) => fields.includes(name));
  }

  function strategyHref(row) {
    const id = String(row?.[holdingFields[1]] || "").trim();
    return id ? `./strategy.html?id=${encodeURIComponent(id)}` : "";
  }

  function holdingValueHtml(row, field) {
    if (field === holdingFields[2]) {
      const href = strategyHref(row);
      const value = row[field];
      if (href && value !== null && value !== undefined && value !== "") {
        return `<a class="link" href="${href}">${B.esc(value)}</a>`;
      }
    }
    return valueHtml(field, row[field]);
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

  const summaryFields = pickFields(fundFields, [
    "基金代码", "基金名称", "基金公司", "基金类型", "二级分类", "资产暴露", "行业暴露",
    "研报大类资产", "广发基金产品", "总权重", "广发策略权重", "非广发策略权重",
    "持仓策略数", "中位权重", "区间收益率", "增持策略数", "减持策略数"
  ]);
  const holdingDisplayFields = pickFields(holdingFields, [
    "策略名称", "投顾机构", "渠道", "是否广发策略", "风险等级", "业务分类",
    "研报产品类型", "天天当前对客展示", "初持仓比例", "期末持仓比例",
    "权重变化", "区间收益率", "近1年"
  ]);
  const monthlyDisplayFields = monthlyFields.slice(1);
  document.title = `${fundData[nameField] || "基金详情"}｜基金详情`;

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
      <h2>基金基础信息</h2>
      ${factGrid(summaryFields, fundData)}
    </section>
    ${fundEntitySection(fundData)}
    <section class="panel">
      <div class="panel-head">
        <div>
          <h2>持仓策略</h2>
          <p class="desc">按期末持仓比例排序，共 ${fundHoldings.length.toLocaleString("zh-CN")} 条策略持仓记录。</p>
        </div>
      </div>
      ${table(holdingDisplayFields, fundHoldings, holdingValueHtml)}
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
