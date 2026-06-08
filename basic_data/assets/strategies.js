(() => {
  const B = window.BasicData;
  const summary = B.state.summary;
  const root = B.byId("strategyListPage");
  const allStrategies = summary.strategies || [];
  const isCompleteStrategy = (row) => row?.数据完整性 === "完整" && row?.风险等级 !== "D0 持仓缺失" && row?.研报产品类型 !== "持仓缺失/不入池";
  const rowsBase = allStrategies.filter(isCompleteStrategy);
  const state = { page: 1, pageSize: 10, rows: [], sortField: "近一月", sortDir: "desc" };
  const returnHeaders = ["近一周", "近一月", "近三月", "近1年", "今年以来", "累计收益率"];
  const riskHeaders = ["最大回撤", "波动率"];
  const weightHeaders = ["权益基金权重", "债券基金权重", "货币基金权重", "QDII权重", "指数基金权重"];
  const numericHeaders = new Set([...returnHeaders, ...riskHeaders, "夏普比率", ...weightHeaders, "调仓次数"]);
  const dateHeaders = ["最新业绩日期", "最新持仓日", "最近调仓日"];
  const dateHeaderSet = new Set(dateHeaders);
  const riskOrder = ["R0 现金/超低波", "R1 低波", "R2 稳健收益", "R3 均衡稳健", "R4 均衡成长", "R5 权益/进取"];
  const reportTypeOrder = ["纯债型", "固收+型", "股债混合型", "股票型", "多元配置型"];

  function unique(field) {
    return [...new Set(rowsBase.map((row) => row[field]).filter(Boolean))].sort((a, b) => a.localeCompare(b, "zh-CN"));
  }

  function orderedUnique(field, order) {
    const values = [...new Set(rowsBase.map((row) => row[field]).filter(Boolean))];
    return values.sort((a, b) => {
      const ai = order.indexOf(a);
      const bi = order.indexOf(b);
      return (ai === -1 ? 999 : ai) - (bi === -1 ? 999 : bi) || a.localeCompare(b, "zh-CN");
    });
  }

  function options(values) {
    return values.map((value) => `<option value="${B.esc(value)}">${B.esc(value)}</option>`).join("");
  }

  function isGfStrategy(row) {
    return row?.是否广发 === "是" || row?.是否广发策略 === "是" || /广发基金|广发投顾/.test(`${row?.投顾机构 || ""} ${row?.渠道 || ""}`);
  }

  function isClientFacing(row) {
    const current = String(row?.天天当前对客展示 || "");
    const status = String(row?.天天展示状态 || "");
    return current !== "否" && !/非对客|不对客|隐藏|未展示|不展示/.test(status);
  }

  function numberValue(row, field) {
    const value = Number(row[field]);
    return Number.isFinite(value) ? value : null;
  }

  function compareField(a, b, field) {
    if (numericHeaders.has(field)) {
      const av = numberValue(a, field);
      const bv = numberValue(b, field);
      if (av === null && bv === null) return 0;
      if (av === null) return -1;
      if (bv === null) return 1;
      return av - bv;
    }
    return String(a[field] || "").localeCompare(String(b[field] || ""), "zh-CN");
  }

  function applySortPreset(value) {
    const preset = {
      name: ["策略名称", "asc"],
      return: ["累计收益率", "desc"],
      week: ["近一周", "desc"],
      month: ["近一月", "desc"],
      quarter: ["近三月", "desc"],
      year: ["近1年", "desc"],
      drawdown: ["最大回撤", "asc"],
      volatility: ["波动率", "asc"],
      sharpe: ["夏普比率", "desc"],
      holdingDate: ["最新持仓日", "desc"],
      rebalance: ["最近调仓日", "desc"]
    }[value] || ["近一月", "desc"];
    state.sortField = preset[0];
    state.sortDir = preset[1];
  }

  function keywordText(row) {
    return [
      row.searchText,
      row.策略名称,
      row.策略代码,
      row.投顾机构,
      row.渠道,
      row.风险等级,
      row.研报产品类型,
      row.研报股票子类型,
      row.业务分类,
      row.市场地域,
      row.主动被动,
      row.披露策略类型,
      row.天天当前对客展示,
      row.天天展示状态
    ].join(" ").toLowerCase();
  }

  function sortHeader(field, cls = "") {
    const active = state.sortField === field;
    const arrow = active ? (state.sortDir === "asc" ? "▲" : "▼") : "↕";
    return `<th class="${cls}"><span class="sort-head ${active ? "is-active" : ""}" role="button" tabindex="0" data-sort-field="${B.esc(field)}">${B.label(field)}<span class="sort-arrow">${arrow}</span></span></th>`;
  }

  function formatCell(row, field) {
    if (field === "策略名称") {
      return `<a class="link" href="./strategy.html?id=${encodeURIComponent(row.统一策略ID)}">${B.esc(row.策略名称 || "未命名策略")}</a><div class="small">策略代码 ${B.esc(row.策略代码 || "未披露")}</div>`;
    }
    if (returnHeaders.includes(field) || riskHeaders.includes(field)) return B.pctSigned(row[field]);
    if (weightHeaders.includes(field)) return B.pct(row[field]);
    if (field === "夏普比率") return B.fmt(row[field]);
    if (field === "最近调仓日" && !row[field]) return '<span class="value-muted">无历史调仓事件</span>';
    return B.fmt(row[field]);
  }

  function syncScrollbars() {
    const wrap = B.byId("strategyTableWrap");
    const top = B.byId("topScrollbar");
    if (!wrap || !top) return;
    const inner = top.querySelector(".strategy-scrollbar-inner");
    inner.style.width = `${wrap.scrollWidth}px`;
    top.onscroll = () => { wrap.scrollLeft = top.scrollLeft; };
    wrap.onscroll = () => { top.scrollLeft = wrap.scrollLeft; };
  }

  function renderTable(rows) {
    const headers = [
      "策略名称", "渠道", "投顾机构", "研报产品类型", "研报股票子类型", "风险等级", "业务分类", "市场地域", "主动被动",
      "披露策略类型", "天天当前对客展示", "天天展示状态", "最新业绩日期",
      ...returnHeaders, ...riskHeaders, "夏普比率", ...weightHeaders, ...dateHeaders.filter((field) => field !== "最新业绩日期"), "调仓次数"
    ];
    const wideFields = new Set(["投顾机构", "研报产品类型", "研报股票子类型", "风险等级", "业务分类", "主动被动", "披露策略类型", "天天当前对客展示", "天天展示状态"]);
    const head = headers.map((field, index) => {
      const cls = index === 0 ? "sticky-name" : index === 1 ? "sticky-channel" : returnHeaders.includes(field) || riskHeaders.includes(field) || weightHeaders.includes(field) ? "narrow" : wideFields.has(field) ? "wide" : "";
      return sortHeader(field, cls);
    }).join("");
    const body = rows.length ? rows.map((row) => `<tr>${headers.map((field, index) => {
      const cls = index === 0 ? "sticky-name strategy-name-cell" : index === 1 ? "sticky-channel" : returnHeaders.includes(field) || riskHeaders.includes(field) || weightHeaders.includes(field) ? "narrow" : wideFields.has(field) ? "wide" : "";
      return `<td class="${cls}">${formatCell(row, field)}</td>`;
    }).join("")}</tr>`).join("") : `<tr><td colspan="${headers.length}"><div class="empty">暂无数据</div></td></tr>`;
    B.byId("strategyTableWrap").innerHTML = `<table class="strategy-table"><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table>`;
    B.byId("strategyTableWrap").querySelectorAll("[data-sort-field]").forEach((button) => {
      button.addEventListener("click", () => {
        const field = button.dataset.sortField;
        if (state.sortField === field) state.sortDir = state.sortDir === "asc" ? "desc" : "asc";
        else {
          state.sortField = field;
          state.sortDir = field === "最大回撤" || field === "波动率" ? "asc" : numericHeaders.has(field) || dateHeaderSet.has(field) ? "desc" : "asc";
        }
        state.page = 1;
        render();
      });
    });
    requestAnimationFrame(syncScrollbars);
  }

  root.innerHTML = `
    <section class="panel">
      <div class="filters">
        <input id="searchInput" class="control" type="search" placeholder="搜索策略、机构、代码、渠道、风险等级、业务分类、研报类型">
        <select id="strategyScopeSelect" class="control">
          <option value="">全部策略</option>
          <option value="gf">仅看广发策略</option>
          <option value="nonGf">仅看非广发策略</option>
        </select>
        <select id="clientScopeSelect" class="control">
          <option value="">全部策略</option>
          <option value="client">只看对客策略</option>
          <option value="nonClient">只看非对客策略</option>
        </select>
        <select id="institutionSelect" class="control"><option value="">全部投顾机构</option>${options(unique("投顾机构"))}</select>
        <select id="channelSelect" class="control"><option value="">全部渠道</option>${options(unique("渠道"))}</select>
        <select id="reportTypeSelect" class="control"><option value="">全部研报产品类型</option>${options(orderedUnique("研报产品类型", reportTypeOrder))}</select>
        <select id="businessSelect" class="control"><option value="">全部业务分类</option>${options(unique("业务分类"))}</select>
        <select id="riskSelect" class="control"><option value="">全部风险等级</option>${options(orderedUnique("风险等级", riskOrder))}</select>
        <select id="regionSelect" class="control"><option value="">全部市场地域</option>${options(unique("市场地域"))}</select>
        <select id="activePassiveSelect" class="control"><option value="">全部主动/被动</option>${options(unique("主动被动"))}</select>
        <select id="sortSelect" class="control">
          <option value="name">按策略名称</option>
          <option value="return">按累计收益率</option>
          <option value="week">按近一周收益</option>
          <option value="month" selected>按近一月收益</option>
          <option value="quarter">按近三月收益</option>
          <option value="year">按近1年收益</option>
          <option value="drawdown">按最大回撤</option>
          <option value="volatility">按波动率</option>
          <option value="sharpe">按夏普比率</option>
          <option value="holdingDate">按最新持仓日</option>
          <option value="rebalance">按最近调仓日</option>
        </select>
        <button id="resetButton" class="control" type="button">重置</button>
      </div>
      <div class="pager">
        <p id="resultCount" class="desc"></p>
        <div class="pager-controls">
          <label class="small">每页
            <select id="pageSizeSelect" class="control" style="width:84px"><option>10</option><option>20</option><option>50</option><option>100</option></select>
          </label>
          <button id="prevPage" type="button">上一页</button>
          <span id="pageInfo" class="small"></span>
          <button id="nextPage" type="button">下一页</button>
        </div>
      </div>
      <div class="strategy-table-shell">
        <div id="topScrollbar" class="strategy-scrollbar"><div class="strategy-scrollbar-inner"></div></div>
        <div id="strategyTableWrap" class="strategy-table-wrap"></div>
      </div>
    </section>
  `;

  function filterRows() {
    const keyword = B.byId("searchInput").value.trim().toLowerCase();
    const strategyScope = B.byId("strategyScopeSelect").value;
    const clientScope = B.byId("clientScopeSelect").value;
    const institution = B.byId("institutionSelect").value;
    const channel = B.byId("channelSelect").value;
    const reportType = B.byId("reportTypeSelect").value;
    const business = B.byId("businessSelect").value;
    const risk = B.byId("riskSelect").value;
    const region = B.byId("regionSelect").value;
    const activePassive = B.byId("activePassiveSelect").value;
    return rowsBase.filter((row) => {
      if (strategyScope === "gf" && !isGfStrategy(row)) return false;
      if (strategyScope === "nonGf" && isGfStrategy(row)) return false;
      if (clientScope === "client" && !isClientFacing(row)) return false;
      if (clientScope === "nonClient" && isClientFacing(row)) return false;
      if (institution && row.投顾机构 !== institution) return false;
      if (channel && row.渠道 !== channel) return false;
      if (reportType && row.研报产品类型 !== reportType) return false;
      if (business && row.业务分类 !== business) return false;
      if (risk && row.风险等级 !== risk) return false;
      if (region && row.市场地域 !== region) return false;
      if (activePassive && row.主动被动 !== activePassive) return false;
      if (keyword && !keywordText(row).includes(keyword)) return false;
      return true;
    });
  }

  function render() {
    const rows = filterRows().sort((a, b) => {
      const compared = compareField(a, b, state.sortField);
      return state.sortDir === "asc" ? compared : -compared;
    });
    state.rows = rows;
    const maxPage = Math.max(1, Math.ceil(rows.length / state.pageSize));
    state.page = Math.min(state.page, maxPage);
    const pageRows = rows.slice((state.page - 1) * state.pageSize, state.page * state.pageSize);
    const gfCount = rows.filter(isGfStrategy).length;
    const clientCount = rows.filter(isClientFacing).length;
    B.byId("resultCount").textContent = `当前筛选 ${rows.length.toLocaleString("zh-CN")} 条策略，广发 ${gfCount.toLocaleString("zh-CN")} 条，对客 ${clientCount.toLocaleString("zh-CN")} 条`;
    B.byId("pageInfo").textContent = `${state.page} / ${maxPage}`;
    B.byId("prevPage").disabled = state.page <= 1;
    B.byId("nextPage").disabled = state.page >= maxPage;
    renderTable(pageRows);
  }

  function resetPageAndRender() {
    state.page = 1;
    render();
  }

  function setControlFromParam(controlId, paramName = controlId) {
    const value = B.params().get(paramName);
    if (!value) return;
    const el = B.byId(controlId);
    if (!el) return;
    const found = [...el.options || []].some((option) => option.value === value || option.textContent === value);
    if (found) el.value = value;
  }

  function applyInitialParams() {
    const params = B.params();
    if (params.get("q")) B.byId("searchInput").value = params.get("q");
    setControlFromParam("strategyScopeSelect", "strategyScope");
    setControlFromParam("clientScopeSelect", "clientScope");
    setControlFromParam("institutionSelect", "institution");
    setControlFromParam("channelSelect", "channel");
    setControlFromParam("reportTypeSelect", "reportType");
    setControlFromParam("businessSelect", "business");
    setControlFromParam("riskSelect", "risk");
    setControlFromParam("regionSelect", "region");
    setControlFromParam("activePassiveSelect", "activePassive");
    if (params.get("sort")) {
      B.byId("sortSelect").value = params.get("sort");
      applySortPreset(params.get("sort"));
    }
    const pageSize = Number(params.get("pageSize"));
    if ([10, 20, 50, 100].includes(pageSize)) {
      B.byId("pageSizeSelect").value = String(pageSize);
      state.pageSize = pageSize;
    }
  }

  applyInitialParams();
  ["searchInput", "strategyScopeSelect", "clientScopeSelect", "institutionSelect", "channelSelect", "reportTypeSelect", "businessSelect", "riskSelect", "regionSelect", "activePassiveSelect"].forEach((id) => {
    B.byId(id).addEventListener("input", resetPageAndRender);
  });
  B.byId("sortSelect").addEventListener("input", () => {
    applySortPreset(B.byId("sortSelect").value);
    resetPageAndRender();
  });
  B.byId("pageSizeSelect").addEventListener("change", () => {
    state.pageSize = Number(B.byId("pageSizeSelect").value);
    resetPageAndRender();
  });
  B.byId("prevPage").addEventListener("click", () => {
    state.page = Math.max(1, state.page - 1);
    render();
  });
  B.byId("nextPage").addEventListener("click", () => {
    state.page += 1;
    render();
  });
  B.byId("resetButton").addEventListener("click", () => {
    B.byId("searchInput").value = "";
    B.byId("strategyScopeSelect").value = "";
    B.byId("clientScopeSelect").value = "";
    B.byId("institutionSelect").value = "";
    B.byId("channelSelect").value = "";
    B.byId("reportTypeSelect").value = "";
    B.byId("businessSelect").value = "";
    B.byId("riskSelect").value = "";
    B.byId("regionSelect").value = "";
    B.byId("activePassiveSelect").value = "";
    B.byId("sortSelect").value = "month";
    B.byId("pageSizeSelect").value = "10";
    applySortPreset("month");
    state.pageSize = 10;
    resetPageAndRender();
  });
  render();
})();
