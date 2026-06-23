(() => {
  const B = window.BasicData;
  const summary = B.state.summary || {};
  const insight = summary.insightData || {};
  const root = B.byId("insightsPage");
  const riskOrder = ["R0 现金/超低波", "R1 低波", "R2 稳健收益", "R3 均衡稳健", "R4 均衡成长", "R5 权益/进取"];
  const dateRanges = [
    { key: "1w", label: "近1周", metric: "近一周", days: 7, monthCount: 1 },
    { key: "1m", label: "近1月", metric: "近一月", days: 31 },
    { key: "3m", label: "近3月", metric: "近三月", days: 92 },
    { key: "1y", label: "近1年", metric: "近1年", days: 365 },
    { key: "ytd", label: "今年以来", metric: "今年以来", ytd: true },
    { key: "all", label: "成立以来", metric: "累计收益率" }
  ];
  const tabs = [
    ["market", "市场总览"],
    ["holding", "仓位分析"],
    ["rebalance", "调仓分析"]
  ];
  const xAxisOptions = ["最大回撤", "波动率", "夏普比率", "卡玛比率"];
  const reportTypeOrder = ["纯债型", "固收+型", "股债混合型", "股票型", "多元配置型", "持仓缺失/不入池"];
  const reportAssetOrder = ["A股", "港股", "美股", "债券", "黄金", "货币及现金", "海外债券", "新兴市场", "其他发达市场", "海外REIT", "其他商品"];
  const reportAssetSet = new Set(reportAssetOrder);
  const state = {
    tab: "market",
    risk: "",
    business: "",
    region: "",
    clientScope: "",
    gfScope: "",
    institution: "",
    range: "1y",
    scatterX: "最大回撤",
    viewPct: 100,
    selectedPointId: "",
    businessProductScope: "",
    businessSortField: "区间收益",
    businessSortDir: "desc",
    businessPages: {},
    openBusiness: "",
    expandedFundKey: "",
    rebalancePage: 1,
    rebalancePageSize: 20,
    rebalanceMode: "month",
    rebalanceMonth: "",
    reportType: ""
  };
  const initParams = new URLSearchParams(window.location.search);
  const initTab = initParams.get("tab");
  if (tabs.some(([key]) => key === initTab)) state.tab = initTab;
  if (initTab === "cockpit") state.tab = "market";
  state.range = initParams.get("range") || state.range;
  state.risk = initParams.get("risk") || state.risk;
  state.business = initParams.get("business") || state.business;
  state.region = initParams.get("region") || state.region;
  state.clientScope = initParams.get("clientScope") || state.clientScope;
  state.gfScope = initParams.get("strategyScope") || initParams.get("gfScope") || state.gfScope;
  state.institution = initParams.get("institution") || state.institution;
  state.rebalanceMode = initParams.get("rebalanceMode") || state.rebalanceMode;
  state.rebalanceMonth = initParams.get("rebalanceMonth") || state.rebalanceMonth;
  state.reportType = initParams.get("reportType") || state.reportType;
  const allPoints = (insight.策略表现点 || []).filter(isDisplayableInsightRow);
  const masterStrategies = (summary.strategies || []).filter(isDisplayableInsightRow);
  const rawPoints = allPoints.filter((row) => row.风险等级 && row.风险等级 !== "D0 持仓缺失");
  const rawPointById = new Map(rawPoints.map((row) => [row.统一策略ID, row]));
  const displayStrategyIds = new Set(rawPoints.map((row) => row.统一策略ID).filter(Boolean));
  const signalDetailStore = new Map();
  const risks = riskOrder.filter((name) => rawPoints.some((row) => row.风险等级 === name));
  const businesses = [...new Set(rawPoints.map((row) => row.业务分类).filter(Boolean))].sort((a, b) => a.localeCompare(b, "zh-CN"));
  const regions = [...new Set(rawPoints.map((row) => row.市场地域).filter(Boolean))].sort((a, b) => a.localeCompare(b, "zh-CN"));
  const institutions = [...new Set(rawPoints.map((row) => row.投顾机构).filter(Boolean))].sort((a, b) => a.localeCompare(b, "zh-CN"));
  const reportTypes = [...new Set(rawPoints.map((row) => row.研报产品类型).filter(Boolean))]
    .sort((a, b) => {
      const ai = reportTypeOrder.indexOf(a);
      const bi = reportTypeOrder.indexOf(b);
      return (ai === -1 ? 999 : ai) - (bi === -1 ? 999 : bi) || a.localeCompare(b, "zh-CN");
    });

  function num(value) {
    if (value === null || value === undefined || value === "") return null;
    const n = Number(value);
    return Number.isFinite(n) ? n : null;
  }

  function raw(value) {
    return value === null || value === undefined ? "" : String(value);
  }

  function isDisplayableInsightRow(row) {
    return !!row
      && (!row.数据完整性 || row.数据完整性 === "完整")
      && row.风险等级 !== "D0 持仓缺失"
      && row.研报产品类型 !== "持仓缺失/不入池";
  }

  function sum(rows, field) {
    return rows.reduce((total, row) => total + (num(row[field]) || 0), 0);
  }

  function median(values) {
    const arr = values.map(num).filter((value) => value !== null).sort((a, b) => a - b);
    if (!arr.length) return null;
    const mid = Math.floor(arr.length / 2);
    return arr.length % 2 ? arr[mid] : (arr[mid - 1] + arr[mid]) / 2;
  }

  function percentile(values, p) {
    const arr = values.map(num).filter((value) => value !== null).sort((a, b) => a - b);
    if (!arr.length) return null;
    const pos = Math.max(0, Math.min(arr.length - 1, (arr.length - 1) * p));
    const low = Math.floor(pos);
    const high = Math.ceil(pos);
    if (low === high) return arr[low];
    return arr[low] + (arr[high] - arr[low]) * (pos - low);
  }

  function avg(values) {
    const arr = values.map(num).filter((value) => value !== null);
    return arr.length ? arr.reduce((a, b) => a + b, 0) / arr.length : null;
  }

  function countText(value) {
    return Number(value || 0).toLocaleString("zh-CN");
  }

  function signedPct(value) {
    return value === null || value === undefined || Number.isNaN(Number(value)) ? '<span class="small">未披露</span>' : B.pctSigned(value);
  }

  function signedPctText(value) {
    if (value === null || value === undefined || Number.isNaN(Number(value))) return "未披露";
    const number = Number(value);
    return `${number > 0 ? "+" : ""}${number.toFixed(2)}%`;
  }

  function pct(value) {
    return value === null || value === undefined || Number.isNaN(Number(value)) ? "未披露" : `${Number(value).toFixed(2)}%`;
  }

  function effectPct(value) {
    return value === null || value === undefined || Number.isNaN(Number(value)) ? "待观察" : `${Number(value).toFixed(2)}%`;
  }

  function effectSigned(value) {
    return value === null || value === undefined || Number.isNaN(Number(value)) ? "待观察" : B.pctSigned(value);
  }

  function effectLabel(value) {
    const text = raw(value);
    if (!text || text === "不可评估" || text === "未披露") return "待观察";
    return B.esc(text);
  }

  function ratioText(value) {
    return value === null || value === undefined || Number.isNaN(Number(value)) ? "未披露" : Number(value).toLocaleString("zh-CN", { maximumFractionDigits: 2 });
  }

  function weightText(value) {
    return value === null || value === undefined || Number.isNaN(Number(value)) ? "未披露" : `${Number(value).toFixed(2)}%`;
  }

  function groupBy(rows, keyFn) {
    const map = new Map();
    rows.forEach((row) => {
      const key = keyFn(row) || "未分类";
      if (!map.has(key)) map.set(key, []);
      map.get(key).push(row);
    });
    return map;
  }

  function isGf(row) {
    return row?.是否广发 === "是" || row?.是否广发策略 === "是" || /广发基金|广发投顾/.test(`${row?.投顾机构 || ""} ${row?.渠道 || ""}`);
  }

  function strategyScopeMatch(row) {
    if (state.gfScope === "gf" && !isGf(row)) return false;
    if (state.gfScope === "nonGf" && isGf(row)) return false;
    if (state.institution && row.投顾机构 !== state.institution) return false;
    return true;
  }

  function isClientFacing(row) {
    const base = row?.统一策略ID ? rawPointById.get(row.统一策略ID) : null;
    const current = raw(row?.天天当前对客展示 || base?.天天当前对客展示);
    const status = raw(row?.天天展示状态 || base?.天天展示状态);
    if (current === "否" || /非对客|不对客|隐藏|未展示|不展示/.test(status)) return false;
    return true;
  }

  function clientScopeMatch(row) {
    return state.clientScope !== "client" || isClientFacing(row);
  }

  function displayScopeMatch(row) {
    if (!row) return false;
    const id = raw(row.统一策略ID);
    if (id && !displayStrategyIds.has(id)) return false;
    if (row.数据完整性 && row.数据完整性 !== "完整") return false;
    if (row.风险等级 === "D0 持仓缺失") return false;
    if (row.研报产品类型 === "持仓缺失/不入池") return false;
    return true;
  }

  function scopedStrategyMatch(row, scope) {
    if (scope === "gf") return isGf(row);
    if (scope === "nonGf") return !isGf(row);
    return true;
  }

  function businessProductScopeMatch(row) {
    return scopedStrategyMatch(row, state.businessProductScope);
  }

  function gfScopeSelect(id, value, className = "control") {
    return `<select id="${id}" class="${className}">
      <option value="" ${value === "" ? "selected" : ""}>全部策略</option>
      <option value="gf" ${value === "gf" ? "selected" : ""}>仅看广发策略</option>
      <option value="nonGf" ${value === "nonGf" ? "selected" : ""}>仅看非广发策略</option>
    </select>`;
  }

  function clientScopeSelect(id, value, className = "control") {
    return `<select id="${id}" class="${className}">
      <option value="" ${value === "" ? "selected" : ""}>全部策略</option>
      <option value="client" ${value === "client" ? "selected" : ""}>只看对客策略</option>
    </select>`;
  }

  function institutionSelect(id, value, className = "control") {
    return `<select id="${id}" class="${className}"><option value="">全部投顾机构</option>${institutions.map((name) => `<option ${name === value ? "selected" : ""}>${B.esc(name)}</option>`).join("")}</select>`;
  }

  function filterField(label, html) {
    return `<label class="filter-field"><span>${B.esc(label)}</span>${html}</label>`;
  }

  function rangeConfig() {
    return dateRanges.find((item) => item.key === state.range) || dateRanges.find((item) => item.key === "1y") || dateRanges[0];
  }

  function returnMetric() {
    return rangeConfig().metric;
  }

  function rangeLabel() {
    return rangeConfig().label;
  }

  function strategyLink(row) {
    return `<a class="link" href="./strategy.html?id=${encodeURIComponent(row.统一策略ID || "")}">${B.esc(row.策略名称 || "未命名策略")}</a>`;
  }

  function fundLabel(row) {
    return row?.基金名称 || row?.基金代码 || "未命名基金";
  }

  function fundLink(row, label = "") {
    if (!row || (!row.基金代码 && !row.基金名称)) return B.esc(label || fundLabel(row));
    return `<a class="link" href="${B.esc(fundDetailUrl(row))}">${B.esc(label || fundLabel(row))}</a>`;
  }

  function kpi(label, value, sub = "", tone = "") {
    return `<section class="insight-kpi ${tone}"><span>${B.esc(label)}</span><strong>${value}</strong>${sub ? `<small>${B.esc(sub)}</small>` : ""}</section>`;
  }

  function tableBlock(headers, rows, formatter) {
    const head = headers.map((h) => `<th>${B.label(h)}</th>`).join("");
    const body = rows.length ? rows.map((row) => `<tr>${headers.map((h) => `<td>${formatter ? formatter(row, h) : B.fmt(row[h])}</td>`).join("")}</tr>`).join("") : `<tr><td colspan="${headers.length}"><div class="empty">暂无数据</div></td></tr>`;
    return `<div class="table-wrap insight-table"><table><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table></div>`;
  }

  function signalDirectionText(value) {
    const n = num(value) || 0;
    if (n > .0001) return "增配";
    if (n < -.0001) return "减配";
    return "变化不明显";
  }

  function groupedFundAdjustments(rows) {
    return [...groupBy(rows || [], (row) => `${row.基金代码 || ""}｜${row.基金名称 || ""}`).entries()].map(([, list]) => {
      const base = list[0] || {};
      const before = sum(list, "调前权重");
      const after = sum(list, "调后权重");
      const change = sum(list, "权重变化");
      return {
        基金代码: base.基金代码,
        基金名称: base.基金名称,
        基金公司: base.基金公司,
        基金类型: base.基金类型,
        调前权重: before,
        调后权重: after,
        权重变化: change
      };
    }).filter((row) => Math.abs(num(row.权重变化) || 0) > .0001)
      .sort((a, b) => Math.abs(num(b.权重变化) || 0) - Math.abs(num(a.权重变化) || 0));
  }

  function fundAdjustmentSummary(rows, limit = 4) {
    const grouped = groupedFundAdjustments(rows);
    if (!grouped.length) return rebalanceFundDetailEmptyText();
    const adds = grouped.filter((row) => (num(row.权重变化) || 0) > 0).slice(0, limit);
    const reduces = grouped.filter((row) => (num(row.权重变化) || 0) < 0).slice(0, limit);
    const item = (row) => `${row.基金名称 || "未命名基金"} ${weightPct(row.调前权重)}→${weightPct(row.调后权重)}（${weightPoint(row.权重变化)}）`;
    return [
      adds.length ? `调增：${adds.map(item).join("；")}` : "",
      reduces.length ? `调减：${reduces.map(item).join("；")}` : ""
    ].filter(Boolean).join("；") || "基金级净变化接近0";
  }

  function strategyAdjustmentSummary(row) {
    const detail = [...(row?._策略明细 || [])]
      .filter((item) => Math.abs(num(item.净变化) || 0) > .0001 || Math.abs(num(item.调仓强度) || 0) > .0001)
      .sort((a, b) => Math.abs(num(b.净变化) || 0) - Math.abs(num(a.净变化) || 0) || Math.abs(num(b.调仓强度) || 0) - Math.abs(num(a.调仓强度) || 0));
    const top = detail[0];
    if (!top) return "当前分类下暂无策略级变化";
    const change = num(top.净变化) || 0;
    const action = change > 0 ? "增配" : (change < 0 ? "减配" : "调整");
    const institution = top.投顾机构 || "未识别机构";
    const name = top.策略名称 || "未命名策略";
    return `${institution}｜${name} ${action}${weightPoint(change)}，${weightPct(top.调前权重)}→${weightPct(top.调后权重)}`;
  }

  function fundAdjustmentCell(rows) {
    const grouped = groupedFundAdjustments(rows);
    if (!grouped.length) return `<span class="small">${B.esc(rebalanceFundDetailEmptyText())}</span>`;
    const visible = grouped.slice(0, 6);
    return `<div class="fund-adjust-list">${visible.map((row) => {
      const change = num(row.权重变化) || 0;
      return `<span class="${change > 0 ? "is-add" : "is-reduce"}"><b>${fundLink(row)}</b><em>${weightPct(row.调前权重)}→${weightPct(row.调后权重)}</em><strong>${weightPoint(change)}</strong></span>`;
    }).join("")}${grouped.length > visible.length ? `<small>另${countText(grouped.length - visible.length)}只</small>` : ""}</div>`;
  }

  function signalDetailButton(label, row) {
    const detail = row._策略明细 || [];
    if (!detail.length) return '<span class="small">无明细</span>';
    const id = `signal-detail-${signalDetailStore.size}`;
    signalDetailStore.set(id, { label, row, detail });
    return `<button type="button" class="detail-button" data-signal-detail="${B.esc(id)}">查看${countText(detail.length)}个</button>`;
  }

  function showSignalDetail(id) {
    const payload = signalDetailStore.get(id);
    if (!payload) return;
    const { label, row, detail } = payload;
    const sorted = [...detail].sort((a, b) => Math.abs(num(b.净变化) || 0) - Math.abs(num(a.净变化) || 0) || raw(a.策略名称).localeCompare(raw(b.策略名称), "zh-CN"));
    const html = `
      <div class="modal-summary">
        <span>分类<b>${B.esc(row.分类 || "未分类")}</b></span>
        <span>判断<b>${B.esc(row.判断 || "方向分歧")}</b></span>
        <span>参与策略<b>${countText(row.参与策略数)}个</b></span>
        <span>增/减策略<b>${countText(row.增持策略数)} / ${countText(row.减持策略数)}</b></span>
        <span>中位净变化<b>${weightPoint(row.中位净变化 ?? row.典型变化)}</b></span>
        <span>累计净变化<b>${weightPoint(row.净变化)}</b></span>
      </div>
      <div class="detail-table">
        <table>
          <thead><tr><th>策略</th><th>投顾机构</th><th>方向</th><th>净变化</th><th>调前权重</th><th>调后权重</th><th>调仓强度</th><th>基金调整</th></tr></thead>
          <tbody>${sorted.map((item) => `<tr>
            <td>${strategyLink(item)}</td>
            <td>${B.esc(item.投顾机构 || "未识别机构")}</td>
            <td><span class="insight-chip ${directionTone(signalDirectionText(item.净变化))}">${signalDirectionText(item.净变化)}</span></td>
            <td>${weightPoint(item.净变化)}</td>
            <td>${weightPct(item.调前权重)}</td>
            <td>${weightPct(item.调后权重)}</td>
            <td>${weightPoint(item.调仓强度)}</td>
            <td>${fundAdjustmentCell(item.基金明细)}</td>
          </tr>`).join("")}</tbody>
        </table>
      </div>
      <p class="detail-note">参与策略表示当前观察窗口内在“${B.esc(label)}=${B.esc(row.分类 || "未分类")}”下发生基金级调增或调减的策略；基金调整列展示具体调增/调减基金及调前、调后仓位。</p>
    `;
    B.showHtmlModal(`${label}｜${row.分类 || "未分类"}｜参与策略明细`, html);
  }

  function barList(rows, labelField, valueField, options = {}) {
    const data = rows.filter((row) => num(row[valueField]) !== null).slice(0, options.limit || 12);
    const max = Math.max(1, ...data.map((row) => Math.abs(num(row[valueField]) || 0)), ...(options.targetField ? data.map((row) => Math.abs(num(row[options.targetField]) || 0)) : []));
    return `<div class="insight-bar-list">${data.map((row) => {
      const value = num(row[valueField]) || 0;
      const target = options.targetField ? num(row[options.targetField]) : null;
      const width = Math.max(2, Math.abs(value) / max * 100);
      const targetWidth = target === null ? 0 : Math.max(2, Math.abs(target) / max * 100);
      return `<div class="insight-bar-row">
        <span class="insight-bar-label" title="${B.esc(row[labelField] || "")}">${B.esc(row[labelField] || "未分类")}</span>
        <span class="insight-bar-track">
          <i class="insight-bar-fill" style="width:${width}%"></i>
          ${target !== null ? `<i class="insight-bar-fill is-target" style="width:${targetWidth}%"></i>` : ""}
        </span>
        <b>${options.formatter ? options.formatter(value, row) : weightText(value)}</b>
      </div>`;
    }).join("")}</div>`;
  }

  function dimensionMatch(row) {
    if (!displayScopeMatch(row)) return false;
    if (row.风险等级 === "D0 持仓缺失") return false;
    if (!strategyScopeMatch(row)) return false;
    if (!clientScopeMatch(row)) return false;
    if (state.risk && row.风险等级 !== state.risk) return false;
    if (state.business && row.业务分类 !== state.business) return false;
    if (state.region && row.市场地域 !== state.region) return false;
    return true;
  }

  function dataQualityScopeMatch(row) {
    if (!displayScopeMatch(row)) return false;
    if (!strategyScopeMatch(row)) return false;
    if (!clientScopeMatch(row)) return false;
    if (state.risk && row.风险等级 !== state.risk) return false;
    if (state.business && row.业务分类 !== state.business) return false;
    if (state.region && row.市场地域 !== state.region) return false;
    return true;
  }

  function reportTypeMatch(row, type = state.reportType) {
    return !type || row.研报产品类型 === type;
  }

  function reportTypeRank(type) {
    const index = reportTypeOrder.indexOf(type);
    return index === -1 ? 999 : index;
  }

  function preRiskMatch(row) {
    if (!displayScopeMatch(row)) return false;
    if (row.风险等级 === "D0 持仓缺失") return false;
    if (!strategyScopeMatch(row)) return false;
    if (!clientScopeMatch(row)) return false;
    if (state.business && row.业务分类 !== state.business) return false;
    if (state.region && row.市场地域 !== state.region) return false;
    return true;
  }

  function normalizeSeriesName(name) {
    return raw(name)
      .replace(/第?[零一二三四五六七八九十百千万\d]{1,5}期/g, "")
      .replace(/\d{1,4}期/g, "")
      .replace(/天天\d{1,4}/g, "天天")
      .replace(/\s+/g, "")
      .replace(/[\\-_—]+$/g, "")
      .replace(/（\s*）/g, "")
      .trim() || raw(name);
  }

  function highestRisk(rows) {
    return [...rows].sort((a, b) => riskOrder.indexOf(b.风险等级) - riskOrder.indexOf(a.风险等级))[0]?.风险等级 || rows[0]?.风险等级 || "未分类";
  }

  function majority(rows, field) {
    return [...groupBy(rows, (row) => row[field]).entries()].sort((a, b) => b[1].length - a[1].length)[0]?.[0] || "未分类";
  }

  function collapseTargetSeries(rows) {
    const out = [];
    const groups = new Map();
    rows.forEach((row) => {
      if (row.业务分类 !== "目标盈系列产品") {
        out.push({ ...row, 期次数: 1, 系列名称: row.策略名称 });
        return;
      }
      const series = normalizeSeriesName(row.策略名称);
      const key = `${row.投顾机构 || ""}｜${row.业务分类 || ""}｜${series}`;
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key).push(row);
    });
    groups.forEach((list) => {
      const metric = returnMetric();
      const best = [...list].filter((row) => num(row[metric]) !== null).sort((a, b) => (num(b[metric]) || -999999) - (num(a[metric]) || -999999))[0] || list[0];
      const series = normalizeSeriesName(best.策略名称);
      const merged = {
        ...best,
        策略名称: list.length > 1 ? `${series}（${list.length}期）` : best.策略名称,
        系列名称: series,
        代表期次: best.策略名称,
        期次数: list.length,
        风险等级: highestRisk(list),
        市场地域: majority(list, "市场地域"),
        主动被动: majority(list, "主动被动"),
        是否广发: list.some(isGf) ? "是" : "否"
      };
      ["近一周", "近一月", "近三月", "近1年", "今年以来", "累计收益率", "最大回撤", "波动率", "夏普比率"].forEach((field) => {
        merged[field] = median(list.map((row) => row[field]));
      });
      out.push(merged);
    });
    return out;
  }

  function strategyRows() {
    return collapseTargetSeries(rawPoints.filter(preRiskMatch)).filter((row) => !state.risk || row.风险等级 === state.risk);
  }

  function metricRaw(row, metric) {
    if (metric === "区间收益") return num(row[returnMetric()]);
    if (metric === "卡玛比率") {
      const ret = num(row[returnMetric()]);
      const dd = num(row.最大回撤);
      return ret === null || dd === null || dd <= 0 ? null : ret / dd;
    }
    return num(row[metric]);
  }

  function metricName(metric) {
    return metric === "区间收益" ? `${rangeLabel()}收益` : metric;
  }

  function metricHtml(row, metric) {
    const value = metricRaw(row, metric);
    if (["区间收益", "波动率", "最大回撤"].includes(metric)) return signedPct(value);
    return ratioText(value);
  }

  function metricPlain(metric, value) {
    if (value === null || value === undefined || Number.isNaN(Number(value))) return "未披露";
    if (["区间收益", "波动率", "最大回撤"].includes(metric)) return `${Number(value).toFixed(2)}%`;
    return Number(value).toFixed(2);
  }

  function formatTick(metric, value) {
    if (["区间收益", "波动率", "最大回撤"].includes(metric)) return `${Number(value).toFixed(1)}%`;
    return Number(value).toFixed(2);
  }

  function lowerIsBetter(metric) {
    return metric === "波动率" || metric === "最大回撤";
  }

  function scaleDomain(values, padRatio = 0.1, viewPct = 100, includeValues = []) {
    const clean = values.map(num).filter((value) => value !== null);
    let min = Math.min(...clean);
    let max = Math.max(...clean);
    if (!Number.isFinite(min) || !Number.isFinite(max)) return [0, 1];
    if (viewPct < 100 && clean.length > 6) {
      const tail = Math.max(0, Math.min(0.24, (100 - viewPct) / 200));
      min = percentile(clean, tail);
      max = percentile(clean, 1 - tail);
    }
    includeValues.map(num).filter((value) => value !== null).forEach((value) => {
      min = Math.min(min, value);
      max = Math.max(max, value);
    });
    if (min === max) { min -= 1; max += 1; }
    const pad = Math.max((max - min) * padRatio, Math.abs(max || 1) * 0.02, 0.1);
    return [min - pad, max + pad];
  }

  function selectedPoint(rows) {
    return rows.find((row) => row.统一策略ID === state.selectedPointId) || null;
  }

  function selectedPointPanel(row) {
    if (!row) return `<div class="insight-callout"><strong>点阵选中策略：</strong>点击图中的点查看对应策略、风险等级、业务分类、业绩和风险指标。</div>`;
    const calmar = metricRaw(row, "卡玛比率");
    return `<div class="insight-callout"><strong>点阵选中策略：</strong>${strategyLink(row)}｜${B.esc(row.投顾机构 || "未披露机构")}｜${B.esc(row.风险等级 || "未分类")}｜${B.esc(row.业务分类 || "未分类")}｜${B.esc(row.市场地域 || "未分类")}<br>
      <strong>${B.esc(rangeLabel())}收益：</strong>${metricHtml(row, "区间收益")}　
      <strong>最大回撤：</strong>${metricHtml(row, "最大回撤")}　
      <strong>波动率：</strong>${metricHtml(row, "波动率")}　
      <strong>夏普比率：</strong>${metricHtml(row, "夏普比率")}　
      <strong>卡玛比率：</strong>${ratioText(calmar)}　
      <strong>期次数：</strong>${countText(row.期次数 || 1)}${row.代表期次 && row.代表期次 !== row.策略名称 ? `　<strong>代表期次：</strong>${B.esc(row.代表期次)}` : ""}</div>`;
  }

  function scatterPlot(rows) {
    const xMetric = state.scatterX;
    const yMetric = "区间收益";
    const data = rows
      .map((row) => ({ row, x: metricRaw(row, xMetric), y: metricRaw(row, yMetric), dd: num(row.最大回撤) }))
      .filter((item) => item.x !== null && item.y !== null);
    if (!data.length) return '<div class="empty">当前筛选下暂无可绘制点阵的数据。</div>';
    const selected = selectedPoint(rows);
    const width = 1040;
    const height = 420;
    const pad = { left: 70, right: 26, top: 28, bottom: 66 };
    const xValues = data.map((item) => item.x);
    const yValues = data.map((item) => item.y);
    const yP75 = percentile(yValues, 0.75);
    const yP50 = percentile(yValues, 0.5);
    const yP25 = percentile(yValues, 0.25);
    const returnLine = yP75;
    const xLine = percentile(xValues, 0.5);
    const selectedX = selected ? metricRaw(selected, xMetric) : null;
    const selectedY = selected ? metricRaw(selected, yMetric) : null;
    const [xMin, xMax] = scaleDomain(xValues, 0.1, state.viewPct, [xLine, selectedX]);
    const [yMin, yMax] = scaleDomain(yValues, 0.1, state.viewPct, [0, yP25, yP50, yP75, selectedY]);
    const visible = data.filter((item) => item.x >= xMin && item.x <= xMax && item.y >= yMin && item.y <= yMax);
    const xOf = (value) => pad.left + (value - xMin) / (xMax - xMin) * (width - pad.left - pad.right);
    const yOf = (value) => height - pad.bottom - (value - yMin) / (yMax - yMin) * (height - pad.top - pad.bottom);
    const inPlotX = (value) => value !== null && value >= xMin && value <= xMax;
    const inPlotY = (value) => value !== null && value >= yMin && value <= yMax;
    const ticks = [0, 0.33, 0.67, 1];
    const grid = ticks.map((ratio) => {
      const x = pad.left + ratio * (width - pad.left - pad.right);
      const y = pad.top + ratio * (height - pad.top - pad.bottom);
      const xValue = xMin + ratio * (xMax - xMin);
      const yValue = yMax - ratio * (yMax - yMin);
      return `<line x1="${x.toFixed(1)}" y1="${pad.top}" x2="${x.toFixed(1)}" y2="${height - pad.bottom}" stroke="#edf1f5"/>
        <text class="axis-text" x="${x.toFixed(1)}" y="${height - 26}" text-anchor="middle">${B.esc(formatTick(xMetric, xValue))}</text>
        <line x1="${pad.left}" y1="${y.toFixed(1)}" x2="${width - pad.right}" y2="${y.toFixed(1)}" stroke="#edf1f5"/>
        <text class="axis-text" x="8" y="${(y + 4).toFixed(1)}">${B.esc(formatTick(yMetric, yValue))}</text>`;
    }).join("");
    const zeroLine = inPlotY(0) ? `<line x1="${pad.left}" y1="${yOf(0).toFixed(1)}" x2="${width - pad.right}" y2="${yOf(0).toFixed(1)}" stroke="#64748b" stroke-width="1.2"/><text class="axis-text" x="${width - pad.right - 62}" y="${(yOf(0) - 6).toFixed(1)}">0收益线</text>` : "";
    const returnDash = inPlotY(yP75) ? `<line x1="${pad.left}" y1="${yOf(yP75).toFixed(1)}" x2="${width - pad.right}" y2="${yOf(yP75).toFixed(1)}" stroke="#b7791f" stroke-width="1.4" stroke-dasharray="6 5"/><text class="axis-text" x="${width - pad.right - 118}" y="${Math.max(pad.top + 14, yOf(yP75) - 8).toFixed(1)}">收益领先线 P75</text>` : "";
    const y50Dash = inPlotY(yP50) ? `<line x1="${pad.left}" y1="${yOf(yP50).toFixed(1)}" x2="${width - pad.right}" y2="${yOf(yP50).toFixed(1)}" stroke="#9fb0c3" stroke-width="1" stroke-dasharray="4 5"/><text class="axis-text" x="${width - pad.right - 92}" y="${Math.max(pad.top + 28, yOf(yP50) - 6).toFixed(1)}">收益中位</text>` : "";
    const xDash = inPlotX(xLine) ? `<line x1="${xOf(xLine).toFixed(1)}" y1="${pad.top}" x2="${xOf(xLine).toFixed(1)}" y2="${height - pad.bottom}" stroke="#b7791f" stroke-width="1.4" stroke-dasharray="6 5"/><text class="axis-text" x="${Math.min(width - pad.right - 118, xOf(xLine) + 8).toFixed(1)}" y="${pad.top + 15}">${B.esc(lowerIsBetter(xMetric) ? "风险中位线" : "效率中位线")}</text>` : "";
    const plotBottom = height - pad.bottom;
    const bandRect = (low, high, fill, opacity, label, labelY) => {
      const topValue = Math.min(yMax, high);
      const bottomValue = Math.max(yMin, low);
      if (bottomValue >= topValue) return "";
      const yTop = yOf(topValue);
      const yBottom = yOf(bottomValue);
      return `<rect x="${pad.left}" y="${yTop.toFixed(1)}" width="${(width - pad.left - pad.right).toFixed(1)}" height="${Math.max(0, yBottom - yTop).toFixed(1)}" fill="${fill}" opacity="${opacity}"/>${label ? `<text class="axis-text" x="${pad.left + 10}" y="${labelY.toFixed(1)}" fill="${fill}">${B.esc(label)}</text>` : ""}`;
    };
    const layerBands = [
      bandRect(yP75, yMax, "#0f7b4f", ".08", "收益领先层", Math.max(pad.top + 18, yOf(Math.min(yMax, yP75)) - 8)),
      bandRect(yP25, yP75, "#166c77", ".035", "", pad.top),
      bandRect(yMin, yP25, "#b42318", ".055", "承压层", Math.min(plotBottom - 8, yOf(Math.max(yMin, yP25)) + 16))
    ].join("");
    let excellentZone = "";
    const canDrawZone = inPlotX(xLine) && inPlotY(yP75);
    if (canDrawZone) {
      const xEdge = xOf(xLine);
      const yEdge = yOf(yP75);
      const zoneX = lowerIsBetter(xMetric) ? pad.left : xEdge;
      const zoneW = lowerIsBetter(xMetric) ? Math.max(0, xEdge - pad.left) : Math.max(0, width - pad.right - xEdge);
      const zoneH = Math.max(0, yEdge - pad.top);
      const labelX = Math.min(width - pad.right - 176, Math.max(pad.left + 8, zoneX + 10));
      excellentZone = `<rect x="${zoneX.toFixed(1)}" y="${pad.top}" width="${zoneW.toFixed(1)}" height="${zoneH.toFixed(1)}" fill="#dff3e8" opacity=".5"/><text class="axis-text" x="${labelX.toFixed(1)}" y="${pad.top + 32}" fill="#0f7b4f">绩优层：P75收益 + ${B.esc(lowerIsBetter(xMetric) ? "低于中位" + xMetric : "高于中位" + xMetric)}</text>`;
    }
    const dots = visible.map((item) => {
      const gf = isGf(item.row);
      const selectedDot = item.row.统一策略ID === state.selectedPointId;
      const excellent = item.y >= yP75 && (lowerIsBetter(xMetric) ? item.x <= xLine : item.x >= xLine);
      const topReturn = item.y >= yP75;
      const goodX = lowerIsBetter(xMetric) ? item.x <= xLine : item.x >= xLine;
      const layer = excellent ? "绩优层" : (topReturn ? "收益领先层" : (item.y >= yP50 && goodX ? "效率较优层" : (item.y < yP25 ? "承压层" : "中位观察层")));
      const radius = selectedDot ? 7 : (excellent ? 5.6 : (gf ? 5 : Math.min(4.8, 2.7 + Math.max(0, item.dd || 0) / 16)));
      const color = gf ? "#b42318" : "#166c77";
      const title = `${item.row.策略名称}｜${item.row.投顾机构 || "未披露机构"}｜${item.row.风险等级}｜${item.row.业务分类}｜${layer}｜${metricName(yMetric)} ${metricPlain(yMetric, item.y)}｜${metricName(xMetric)} ${metricPlain(xMetric, item.x)}`;
      return `<circle class="scatter-point" data-point-id="${B.esc(item.row.统一策略ID || "")}" data-tooltip="${B.esc(title)}" cx="${xOf(item.x).toFixed(1)}" cy="${yOf(item.y).toFixed(1)}" r="${radius.toFixed(1)}" fill="${color}" fill-opacity="${gf ? ".92" : ".38"}" stroke="${selectedDot ? "#111827" : (excellent ? "#b7791f" : (gf ? "#7a1a14" : "#0f4f58"))}" stroke-width="${selectedDot ? "2.4" : (excellent ? "2" : (gf ? "1.2" : ".5"))}" style="cursor:pointer"><title>${B.esc(title)}</title></circle>`;
    }).join("");
    const excellentCount = data.filter((item) => item.y >= yP75 && (lowerIsBetter(xMetric) ? item.x <= xLine : item.x >= xLine)).length;
    const leaderCount = data.filter((item) => item.y >= yP75).length;
    const pressureCount = data.filter((item) => item.y < yP25).length;
    return `<div class="chart" style="height:460px">
      <div class="legend" style="padding:8px 10px 0"><span class="legend-item"><i style="background:#b42318"></i><span>广发基金投顾</span></span><span class="legend-item"><i style="background:#166c77"></i><span>非广发产品</span></span><span class="legend-item"><i style="background:#dff3e8;border:1px solid #b7791f"></i><span>绩优层 ${countText(excellentCount)} 个</span></span><span class="legend-item"><span>收益领先 ${countText(leaderCount)} 个 / 承压 ${countText(pressureCount)} 个</span></span><span class="legend-item"><span>显示 ${countText(visible.length)} / ${countText(data.length)} 点</span></span></div>
      <div id="scatterHoverTip" class="chart-tooltip scatter-hover-tip" hidden></div>
      <svg viewBox="0 0 ${width} ${height}" role="img" aria-label="策略表现点阵图">
        ${layerBands}
        ${excellentZone}
        ${grid}
        ${zeroLine}
        ${returnDash}
        ${y50Dash}
        ${xDash}
        <line x1="${pad.left}" y1="${height - pad.bottom}" x2="${width - pad.right}" y2="${height - pad.bottom}" stroke="#cbd5e1"/>
        <line x1="${pad.left}" y1="${pad.top}" x2="${pad.left}" y2="${height - pad.bottom}" stroke="#cbd5e1"/>
        <text class="axis-text" x="${width / 2}" y="${height - 8}" text-anchor="middle">${B.esc(metricName(xMetric))}</text>
        <text class="axis-text" transform="translate(20 ${height / 2}) rotate(-90)" text-anchor="middle">${B.esc(metricName(yMetric))}</text>
        ${dots}
      </svg>
    </div>`;
  }

  function maxDateFromEvents() {
    const dates = (insight.调仓事件 || []).map((row) => row.调仓日期).filter(Boolean).sort();
    return dates.at(-1) || new Date().toISOString().slice(0, 10);
  }

  function cutoffDate() {
    const cfg = rangeConfig();
    if (cfg.key === "all") return "";
    const max = new Date(maxDateFromEvents());
    if (cfg.ytd) return `${max.getFullYear()}-01-01`;
    max.setDate(max.getDate() - (cfg.days || 365));
    return max.toISOString().slice(0, 10);
  }

  function rangeFiltered(rows, field, monthMode = false) {
    if (monthMode) {
      const data = rows || [];
      const months = [...new Set(data.map((row) => String(row[field] || "")).filter(Boolean))].sort();
      if (!months.length) return data;
      const cfg = rangeConfig();
      if (cfg.key === "all") return data;
      if (cfg.ytd) {
        const latestYear = months.at(-1).slice(0, 4);
        return data.filter((row) => String(row[field] || "").slice(0, 4) === latestYear);
      }
      const keepMonths = cfg.monthCount || Math.max(1, Math.ceil((cfg.days || 31) / 31));
      const allowed = new Set(months.slice(-keepMonths));
      return data.filter((row) => allowed.has(String(row[field] || "")));
    }
    const cutoff = cutoffDate();
    if (!cutoff) return rows || [];
    return (rows || []).filter((row) => String(row[field] || "") >= cutoff);
  }

  function addDays(dateText, days) {
    if (!dateText) return "";
    const date = new Date(`${dateText}T00:00:00`);
    if (Number.isNaN(date.getTime())) return "";
    date.setDate(date.getDate() + days);
    return date.toISOString().slice(0, 10);
  }

  function rangeTargetsFromSnapshotRows(rows) {
    const dates = [...new Set((rows || []).map((row) => row.快照日期).filter(Boolean))].sort();
    if (!dates.length) return { startTarget: "", endTarget: "", earliest: "", latest: "" };
    if (state.tab === "rebalance" && state.rebalanceMode === "month" && currentRebalanceMonth()) {
      const month = currentRebalanceMonth();
      return { startTarget: `${month}-01`, endTarget: monthEndDate(month), earliest: dates[0], latest: dates.at(-1) };
    }
    const cfg = rangeConfig();
    const earliest = dates[0];
    const latest = dates.at(-1);
    if (cfg.key === "all") return { startTarget: earliest, endTarget: latest, earliest, latest };
    if (cfg.ytd) return { startTarget: `${latest.slice(0, 4)}-01-01`, endTarget: latest, earliest, latest };
    return { startTarget: addDays(latest, -(cfg.days || 365)), endTarget: latest, earliest, latest };
  }

  function asOfSnapshotDate(dates, target) {
    if (!dates.length || !target) return "";
    let candidate = "";
    for (const date of dates) {
      if (date <= target) candidate = date;
      else break;
    }
    if (candidate) return candidate;
    return dates.find((date) => date >= target) || "";
  }

  function riskCountRows(rows) {
    return riskOrder.map((risk) => {
      const list = rows.filter((row) => row.风险等级 === risk);
      return { 风险等级: risk, 市场数量: list.length, 广发数量: list.filter(isGf).length };
    }).filter((row) => row.市场数量);
  }

  function businessStats(rows) {
    return [...groupBy(rows, (row) => row.业务分类).entries()].map(([business, list]) => {
      const gf = list.filter(isGf);
      const marketReturn = median(list.map((row) => row[returnMetric()]));
      const gfReturn = median(gf.map((row) => row[returnMetric()]));
      const marketDrawdown = median(list.map((row) => row.最大回撤));
      const gfDrawdown = median(gf.map((row) => row.最大回撤));
      const marketVolatility = median(list.map((row) => row.波动率));
      const gfVolatility = median(gf.map((row) => row.波动率));
      const row = {
        业务分类: business,
        市场数量: list.length,
        广发数量: gf.length,
        广发覆盖率: list.length ? gf.length / list.length * 100 : null,
        市场中位收益: marketReturn,
        广发中位收益: gfReturn,
        收益差: gfReturn === null || marketReturn === null ? null : gfReturn - marketReturn,
        市场中位回撤: marketDrawdown,
        广发中位回撤: gfDrawdown,
        回撤优势: gfDrawdown === null || marketDrawdown === null ? null : marketDrawdown - gfDrawdown,
        市场中位波动: marketVolatility,
        广发中位波动: gfVolatility,
        波动优势: gfVolatility === null || marketVolatility === null ? null : marketVolatility - gfVolatility
      };
      return { ...row, ...businessAction(row) };
    }).sort((a, b) => b.市场数量 - a.市场数量);
  }

  function businessAction(row) {
    const marketSize = num(row.市场数量) || 0;
    const gfCount = num(row.广发数量) || 0;
    const coverage = num(row.广发覆盖率);
    const returnGap = num(row.收益差);
    const drawdownEdge = num(row.回撤优势);
    if (!gfCount && marketSize >= 15) {
      return { 经营动作: "产品补齐", 经营判断: "市场已有有效样本但广发缺位，适合评估产品布局、投顾组合包装或外部合作机会。" };
    }
    if (returnGap !== null && returnGap >= 1 && (drawdownEdge === null || drawdownEdge >= -2)) {
      return { 经营动作: "重点营销", 经营判断: "广发同类中位收益领先，且回撤没有明显劣势，适合进入销售话术、渠道露出和重点名单。" };
    }
    if (coverage !== null && coverage < 8 && marketSize >= 30) {
      return { 经营动作: "梯队扩容", 经营判断: "市场需求有规模但广发覆盖偏薄，应补充不同风险档、期限或场景化产品。" };
    }
    if ((returnGap !== null && returnGap <= -2) || (drawdownEdge !== null && drawdownEdge <= -4)) {
      return { 经营动作: "复盘优化", 经营判断: "广发相对收益或回撤表现落后，应复盘组合构成、底层基金、调仓节奏和营销承诺边界。" };
    }
    return { 经营动作: "保持观察", 经营判断: "当前没有明显领先或缺口，适合跟踪竞品变化、持仓偏好和渠道反馈。" };
  }

  function actionClass(action) {
    if (action === "重点营销") return "good";
    if (action === "复盘优化") return "bad";
    if (action === "产品补齐" || action === "梯队扩容") return "warn";
    return "";
  }

  function actionTone(action) {
    const cls = actionClass(action);
    if (cls === "good") return "is-good";
    if (cls === "bad") return "is-bad";
    if (cls === "warn") return "is-warn";
    return "";
  }

  function signedPoint(value) {
    return value === null || value === undefined || Number.isNaN(Number(value)) ? "未披露" : `${Number(value).toFixed(2)}pct`;
  }

  function weightPoint(value) {
    if (value === null || value === undefined || Number.isNaN(Number(value))) return "未披露";
    const n = Math.abs(Number(value)) < 0.05 ? 0 : Number(value);
    const sign = n > 0 ? "+" : "";
    return `${sign}${n.toLocaleString("zh-CN", { maximumFractionDigits: 1 })}点`;
  }

  function weightPct(value) {
    if (value === null || value === undefined || Number.isNaN(Number(value))) return "未披露";
    return `${Number(value).toLocaleString("zh-CN", { maximumFractionDigits: 2 })}%`;
  }

  function isMarketInstitutionName(name) {
    return raw(name) === "全市场汇总";
  }

  function isGfInstitutionName(name) {
    return /广发基金|广发投顾|广发/.test(raw(name));
  }

  function heatmapInstitutionLabel(name) {
    if (isMarketInstitutionName(name)) return "全市场";
    if (isGfInstitutionName(name)) return "广发基金";
    return raw(name) || "未识别机构";
  }

  function heatmapEmptyCell(title, detail = "无可比数据") {
    return `<td class="heat-cell advisor-heat-cell" style="background:#f8fafc;color:#94a3b8" title="${B.esc(title)}">
      <b>-</b>
      <small>${B.esc(detail)}</small>
    </td>`;
  }

  function heatmapInstitutionColumns(rows, institutionField = "投顾机构", options = {}) {
    const maxNonGf = options.maxNonGf ?? 5;
    const sourceRows = options.sourceRows || rows || [];
    const stats = new Map();
    const addStats = (list, weight = 1) => {
      (list || []).forEach((row) => {
        const institution = raw(row[institutionField] || row.投顾机构);
        if (!institution) return;
        if (!stats.has(institution)) stats.set(institution, { 投顾机构: institution, 策略数: 0, 总点位: 0, 样本数: 0, _策略: new Set() });
        const out = stats.get(institution);
        const strategyId = raw(row.统一策略ID);
        if (strategyId) out._策略.add(strategyId);
        else {
          const strategyCount = num(row.总策略数) ?? num(row.调仓策略数) ?? num(row.持仓策略数) ?? num(row.策略快照数) ?? 1;
          out.策略数 += strategyCount * weight;
        }
        out.总点位 += Math.abs(num(row.总点位) ?? num(row.占比变化) ?? num(row.中位变化) ?? num(row.净增配) ?? 0);
        out.样本数 += 1;
      });
    };
    addStats(sourceRows, 1);
    addStats(rows || [], .001);
    stats.forEach((row) => {
      if (row._策略?.size) row.策略数 = row._策略.size;
      delete row._策略;
    });
    const dataNames = new Set((rows || []).map((row) => raw(row[institutionField] || row.投顾机构)).filter(Boolean));
    const marketKey = dataNames.has("全市场汇总") || stats.has("全市场汇总") ? "全市场汇总" : "";
    const gfCandidates = [...new Set([
      ...[...stats.keys()].filter(isGfInstitutionName),
      ...[...dataNames].filter(isGfInstitutionName),
      ...institutions.filter(isGfInstitutionName)
    ])];
    const gfKey = state.gfScope === "nonGf" ? "" : (gfCandidates
      .map((name) => stats.get(name) || { 投顾机构: name, 策略数: 0, 总点位: 0, 样本数: 0 })
      .sort((a, b) => (num(b.策略数) || 0) - (num(a.策略数) || 0) || (num(b.总点位) || 0) - (num(a.总点位) || 0))[0]?.投顾机构 || "");
    const used = new Set([marketKey, gfKey].filter(Boolean));
    const others = [...stats.values()]
      .filter((row) => row.投顾机构 && !used.has(row.投顾机构) && !isMarketInstitutionName(row.投顾机构) && !isGfInstitutionName(row.投顾机构))
      .sort((a, b) => (num(b.策略数) || 0) - (num(a.策略数) || 0) || (num(b.总点位) || 0) - (num(a.总点位) || 0) || raw(a.投顾机构).localeCompare(raw(b.投顾机构), "zh-CN"))
      .slice(0, maxNonGf)
      .map((row) => row.投顾机构);
    return [
      ...(marketKey ? [marketKey] : []),
      ...(gfKey ? [gfKey] : []),
      ...others
    ].map((key) => ({ key, label: heatmapInstitutionLabel(key) }));
  }

  function rankList(rows, options = {}) {
    const data = (rows || []).slice(0, options.limit || 8);
    if (!data.length) return '<div class="empty">暂无数据</div>';
    return `<div class="rank-list">${data.map((row) => {
      const title = options.title ? options.title(row) : row.基金名称 || row.策略名称 || row.业务分类 || "未命名";
      const sub = options.sub ? options.sub(row) : "";
      const value = options.value ? options.value(row) : "";
      const meta = options.meta ? options.meta(row) : "";
      const href = options.href ? options.href(row) : "";
      const titleHtml = href ? `<a class="link" href="${B.esc(href)}">${B.esc(title)}</a>` : B.esc(title);
      return `<div class="rank-row">
        <div><strong title="${B.esc(title)}">${titleHtml}</strong>${sub ? `<span>${B.esc(sub)}</span>` : ""}</div>
        <div class="rank-value">${value}</div>
        <div class="small">${meta}</div>
      </div>`;
    }).join("")}</div>`;
  }

  function filteredMonthlyFundRows() {
    return rangeFiltered((insight.调仓基金月度汇总 || []).filter(dimensionMatch), "月份", true);
  }

  function rollupMonthlyFunds(rows, onlyGf = false) {
    return [...groupBy((rows || []).filter((row) => !onlyGf || row.是否广发基金 === "是"), (row) => `${row.基金代码}｜${row.基金名称}`).entries()].map(([, list]) => {
      const base = list[0] || {};
      const netChanges = list.map((row) => num(row.净增配)).filter((value) => value !== null);
      return {
        ...base,
        明细数: sum(list, "明细数"),
        调仓事件数: sum(list, "调仓事件数"),
        调仓策略数: sum(list, "调仓策略数"),
        加仓次数: sum(list, "加仓次数"),
        减仓次数: sum(list, "减仓次数"),
        买入次数: sum(list, "买入次数"),
        卖出次数: sum(list, "卖出次数"),
        加仓权重: sum(list, "加仓权重"),
        减仓权重: sum(list, "减仓权重"),
        净增配: sum(list, "净增配"),
        广发策略净增配: sum(list, "广发策略净增配"),
        非广发策略净增配: sum(list, "非广发策略净增配"),
        调仓后收益贡献: sum(list, "调仓后收益贡献"),
        中位净增配: median(netChanges),
        绝对净增配: Math.abs(sum(list, "净增配"))
      };
    }).sort((a, b) => b.绝对净增配 - a.绝对净增配);
  }

  function fundTheme(row) {
    if (raw(row.行业主题)) return raw(row.行业主题);
    const name = raw(row.基金名称);
    const type = raw(row.基金类型) || "混合型";
    if (/货币|现金|活期/.test(name) || type === "货币型") return "现金管理";
    if (/转债|可转债/.test(name)) return "可转债";
    if (/短债|中短债|超短债/.test(name)) return "短债/中短债";
    if (/债|纯债|信用|利率|票息|固收/.test(name) || type === "债券型") return "纯债/固收";
    if (/黄金|白银|贵金属/.test(name)) return "贵金属";
    if (/原油|油气|能源商品/.test(name)) return "能源商品";
    if (/商品|期货/.test(name) || type === "商品型") return "商品基金";
    if (/港股.*互联网|恒生.*科技|互联网.*港股|科技.*港股/.test(name)) return "港股/海外科技";
    if (/港股|恒生|港股通/.test(name)) return "港股市场";
    if (/纳斯达克|纳指|标普|美国|美股/.test(name)) return "美股市场";
    if (/QDII|海外|全球|越南|印度|德国|日本|亚洲/.test(name) || type === "QDII/海外") return "海外区域/全球";
    const industryRules = [
      ["医药生物", /医药|医疗|创新药|生物|健康|中药|药/],
      ["电力设备/新能源", /新能源|光伏|电池|储能|电力设备|低碳|环保|碳中和|绿色/],
      ["电子/半导体", /半导体|芯片|电子|集成电路/],
      ["计算机/人工智能", /AI|人工智能|计算机|软件|数字|信创|云计算|大数据|信息技术/],
      ["通信", /通信|5G/],
      ["传媒/互联网", /互联网|传媒|游戏|文化|文娱|内容|TMT/],
      ["食品饮料", /食品|饮料|白酒|酒/],
      ["消费服务", /消费|家电|旅游|酒店|商贸|零售|农业|农林牧渔|养殖|畜牧/],
      ["国防军工", /军工|国防|航天|航空/],
      ["金融地产", /金融|证券|银行|保险|地产|房地产/],
      ["周期资源", /周期|有色|煤炭|钢铁|化工|材料|资源|稀土|石油/],
      ["高端制造", /高端制造|先进制造|智能制造|机器人|工业|装备|机械|制造/],
      ["汽车", /汽车|智能车|新能源汽车/]
    ];
    const matches = industryRules.filter(([, pattern]) => pattern.test(name)).map(([theme]) => theme);
    if (matches.length === 1) return matches[0];
    if (matches.length > 1) return "跨行业/多主题权益";
    if (/红利|低波|高股息|价值|央企|国企/.test(name)) return "红利价值/央国企";
    if (/ETF|指数|联接|沪深|中证|创业板|科创|上证|深证|宽基|增强|LOF|MSCI|国证|A500|100|300|500|1000|2000/.test(name) || type === "指数型") return "宽基指数";
    if (type === "股票型" || type === "混合型") return "主动权益/均衡";
    return type;
  }

  const reportAIndustryThemes = new Set([
    "电子",
    "计算机",
    "通信",
    "传媒",
    "电力设备",
    "机械设备",
    "汽车",
    "国防军工",
    "医药生物",
    "食品饮料",
    "家用电器",
    "商贸零售",
    "社会服务",
    "农林牧渔",
    "银行",
    "非银金融",
    "房地产",
    "有色金属",
    "基础化工",
    "钢铁",
    "煤炭",
    "石油石化",
    "公用事业",
    "交通运输",
    "建筑材料",
    "建筑装饰",
    "纺织服饰",
    "美容护理",
    "医药生物",
    "电力设备/新能源",
    "电子/半导体",
    "计算机/人工智能",
    "通信",
    "传媒/互联网",
    "食品饮料",
    "消费服务",
    "国防军工",
    "金融地产",
    "周期资源",
    "高端制造",
    "汽车",
    "红利价值/央国企",
    "跨行业/多主题权益"
  ]);

  function isReportAIndustryTheme(value) {
    return reportAIndustryThemes.has(raw(value));
  }

  function rowCategoryForField(row, field) {
    if (row?.分类字段) return row.分类字段 === field ? raw(row.分类) : "";
    return raw(row?.[field] || row?.分类);
  }

  function reliableReportAsset(row) {
    const asset = raw(row?.研报大类资产);
    return reportAssetSet.has(asset) ? asset : "";
  }

  function rollupCompanyAssetDirection(rows) {
    const scoped = (rows || []).filter(reliableReportAsset);
    return [...groupBy(scoped, (row) => `${row.基金公司 || "基金公司待补全"}｜${reliableReportAsset(row)}`).entries()].map(([, list]) => {
      const base = list[0] || {};
      const net = sum(list, "净增配");
      return {
        基金公司: base.基金公司 || "基金公司待补全",
        资产主题: reliableReportAsset(base),
        调仓策略数: sum(list, "调仓策略数"),
        加仓权重: sum(list, "加仓权重"),
        减仓权重: sum(list, "减仓权重"),
        净增配: net,
        中位净增配: median(list.map((row) => num(row.净增配)).filter((value) => value !== null)),
        总点位: sum(list, "加仓权重") + sum(list, "减仓权重"),
        绝对净增配: Math.abs(net)
      };
    }).sort((a, b) => b.绝对净增配 - a.绝对净增配);
  }

  function monthText(value) {
    return raw(value).slice(0, 7);
  }

  function rebalanceMonths() {
    return [...new Set((insight.调仓事件 || [])
      .filter(dimensionMatch)
      .map((row) => monthText(row.调仓日期))
      .filter(Boolean))]
      .sort();
  }

  function currentRebalanceMonth() {
    const months = rebalanceMonths();
    if (state.rebalanceMonth && months.includes(state.rebalanceMonth)) return state.rebalanceMonth;
    const currentMonth = new Date().toISOString().slice(0, 7);
    return months.filter((month) => month < currentMonth).at(-1) || months.at(-1) || "";
  }

  function monthEndDate(month) {
    const date = new Date(`${month}-01T00:00:00`);
    if (Number.isNaN(date.getTime())) return `${month}-31`;
    date.setMonth(date.getMonth() + 1);
    date.setDate(0);
    return date.toISOString().slice(0, 10);
  }

  function rebalanceRangeRows(rows, field, monthMode = false) {
    if (state.rebalanceMode === "month") {
      const month = currentRebalanceMonth();
      return (rows || []).filter((row) => (monthMode ? raw(row[field]) : monthText(row[field])) === month);
    }
    return rangeFiltered(rows || [], field, monthMode);
  }

  function filteredStrategyAssetChangeRows(applyReportType = true) {
    const rows = (insight.策略资产变化明细 || [])
      .filter(dimensionMatch)
      .filter((row) => !applyReportType || reportTypeMatch(row));
    return rebalanceRangeRows(rows, "调仓日期");
  }

  function filteredRebalanceFundCategoryRows(applyReportType = true) {
    const rows = rebalanceFundCategoryRows()
      .filter(dimensionMatch)
      .filter((row) => !applyReportType || reportTypeMatch(row));
    return rebalanceRangeRows(rows, "调仓日期");
  }

  function rollupAdvisorAssetDirection(rows) {
    const scoped = (rows || []).filter(reliableReportAsset);
    const strategyAssetRows = [...groupBy(scoped, (row) => `${row.投顾机构 || "未识别机构"}｜${reliableReportAsset(row)}｜${row.统一策略ID || ""}`).entries()]
      .map(([, list]) => {
        const base = list[0] || {};
        const net = sum(list, "净增配");
        const totalPoint = list.reduce((total, row) => total + (num(row.总点位) || Math.abs(num(row.净增配) || 0)), 0);
        return {
          投顾机构: base.投顾机构 || "未识别机构",
          资产类型: reliableReportAsset(base),
          统一策略ID: base.统一策略ID,
          策略名称: base.策略名称,
          净增配: net,
          总点位: totalPoint
        };
      })
      .filter((row) => Math.abs(num(row.净增配) || 0) > 0.0001 || (num(row.总点位) || 0) > 0.0001);
    const summarize = (list, institution) => {
      const base = list[0] || {};
      const add = list.filter((row) => (num(row.净增配) || 0) > 0.0001);
      const reduce = list.filter((row) => (num(row.净增配) || 0) < -0.0001);
      const netValues = list.map((row) => num(row.净增配)).filter((value) => value !== null);
      return {
        投顾机构: institution || base.投顾机构 || "未识别机构",
        资产类型: base.资产类型 || "混合型",
        增持策略数: add.length,
        减持策略数: reduce.length,
        总策略数: list.length,
        增持中位数: median(add.map((row) => row.净增配)),
        减持中位数: median(reduce.map((row) => row.净增配)),
        中位变化: median(netValues),
        平均变化: avg(netValues),
        净增配: sum(list, "净增配"),
        总点位: sum(list, "总点位")
      };
    };
    const institutionRows = [...groupBy(strategyAssetRows, (row) => `${row.投顾机构}｜${row.资产类型}`).entries()].map(([, list]) => summarize(list));
    const marketRows = [...groupBy(strategyAssetRows, (row) => row.资产类型).entries()].map(([, list]) => summarize(list, "全市场汇总"));
    return [...marketRows, ...institutionRows].sort((a, b) => (num(b.总点位) || 0) - (num(a.总点位) || 0));
  }

  function rollupDirection(rows, field) {
    return [...groupBy(rows || [], (row) => row[field]).entries()].map(([name, list]) => {
      const net = sum(list, "净增配");
      return {
        分类: name,
        明细数: sum(list, "明细数"),
        调仓事件数: sum(list, "调仓事件数"),
        调仓策略数: sum(list, "调仓策略数"),
        加仓权重: sum(list, "加仓权重"),
        减仓权重: sum(list, "减仓权重"),
        净增配: net,
        中位净增配: median(list.map((row) => num(row.净增配)).filter((value) => value !== null)),
        广发策略净增配: sum(list, "广发策略净增配"),
        非广发策略净增配: sum(list, "非广发策略净增配"),
        调仓后收益贡献: sum(list, "调仓后收益贡献"),
        绝对净增配: Math.abs(net)
      };
    }).sort((a, b) => b.绝对净增配 - a.绝对净增配);
  }

  function companyDirectionSummary(companyAssetRows) {
    return [...groupBy(companyAssetRows || [], (row) => row.基金公司).entries()].map(([company, list]) => {
      const net = sum(list, "净增配");
      const addWeight = sum(list, "加仓权重");
      const reduceWeight = sum(list, "减仓权重");
      const abs = list.reduce((total, row) => total + Math.abs(num(row.净增配) || 0), 0);
      const addTheme = [...list].filter((row) => (num(row.净增配) || 0) > 0).sort((a, b) => (num(b.净增配) || 0) - (num(a.净增配) || 0))[0];
      const reduceTheme = [...list].filter((row) => (num(row.净增配) || 0) < 0).sort((a, b) => (num(a.净增配) || 0) - (num(b.净增配) || 0))[0];
      return {
        基金公司: company || "基金公司待补全",
        净方向: net > 1 ? "整体加仓" : (net < -1 ? "整体减仓" : "结构轮动"),
        主加仓资产: addTheme ? `${addTheme.资产主题} ${weightPoint(addTheme.净增配)}` : "无明显加仓",
        主减仓资产: reduceTheme ? `${reduceTheme.资产主题} ${weightPoint(reduceTheme.净增配)}` : "无明显减仓",
        净增配: net,
        加仓权重: addWeight,
        减仓权重: reduceWeight,
        调仓强度: abs,
        调仓策略数: sum(list, "调仓策略数"),
        中位净增配: median(list.map((row) => num(row.中位净增配)).filter((value) => value !== null)),
        涉及主题数: list.length
      };
    }).sort((a, b) => b.调仓强度 - a.调仓强度);
  }

  function companyAssetHeatmap(advisorAssetRows, sourceRows = []) {
    const columns = heatmapInstitutionColumns(advisorAssetRows, "投顾机构", { sourceRows, maxNonGf: 5 });
    const assetTypes = [...groupBy(advisorAssetRows || [], (row) => row.资产类型).entries()]
      .map(([type, list]) => ({ 资产类型: type, 总点位: sum(list, "总点位") }))
      .sort((a, b) => (num(b.总点位) || 0) - (num(a.总点位) || 0))
      .slice(0, 7)
      .map((row) => row.资产类型);
    if (!columns.length || !assetTypes.length) return '<div class="empty">当前筛选下暂无投顾机构资产方向数据。</div>';
    const map = new Map((advisorAssetRows || []).map((row) => [`${row.投顾机构}｜${row.资产类型}`, row]));
    const maxAbs = Math.max(1, ...columns.flatMap((column) => assetTypes.map((type) => Math.abs(num(map.get(`${column.key}｜${type}`)?.中位变化) ?? num(map.get(`${column.key}｜${type}`)?.净增配) ?? 0))));
    const cell = (column, type) => {
      const row = map.get(`${column.key}｜${type}`) || {};
      if (!Object.keys(row).length) return heatmapEmptyCell(`${column.label}｜${type}｜当前筛选区间该资产无主动调仓变化`, "0变化");
      const medianChange = num(row.中位变化);
      const value = medianChange !== null ? medianChange : (num(row.净增配) || 0);
      const alpha = Math.min(.88, .1 + Math.abs(value) / maxAbs * .78);
      const bg = Math.abs(value) < 0.0001 ? "#f8fafc" : (value > 0 ? `rgba(180,35,24,${alpha.toFixed(2)})` : `rgba(15,118,110,${alpha.toFixed(2)})`);
      const color = Math.abs(value) / maxAbs > .45 ? "#fff" : "#172033";
      const addCount = num(row.增持策略数) || 0;
      const reduceCount = num(row.减持策略数) || 0;
      const totalStrategies = num(row.总策略数) || 0;
      const totalPoint = num(row.总点位) || 0;
      const avgChange = num(row.平均变化);
      const title = `${column.label}｜${type}｜增持${countText(addCount)}个策略，中位${weightPoint(row.增持中位数)}｜减持${countText(reduceCount)}个策略，中位${weightPoint(row.减持中位数)}｜中位变化${weightPoint(medianChange)}｜平均变化${weightPoint(avgChange)}｜总点位${weightPoint(totalPoint)}｜策略数${countText(totalStrategies)}`;
      return `<td class="heat-cell advisor-heat-cell" style="background:${bg};color:${color}" title="${B.esc(title)}">
        <b>增${countText(addCount)}｜减${countText(reduceCount)}</b>
        <small>增中位 ${weightPoint(row.增持中位数)}</small>
        <small>减中位 ${weightPoint(row.减持中位数)}</small>
        <small>中位/均值 ${weightPoint(medianChange)} / ${weightPoint(avgChange)}</small>
        <small>总${weightPoint(totalPoint)}｜${countText(totalStrategies)}策</small>
      </td>`;
    };
    return `<div class="heatmap-wrap"><table class="heatmap-table advisor-heatmap-table"><thead><tr><th>资产类型</th>${columns.map((column) => `<th>${B.esc(column.label)}</th>`).join("")}</tr></thead><tbody>${assetTypes.map((type) => `<tr><th>${B.esc(type)}</th>${columns.map((column) => cell(column, type)).join("")}</tr>`).join("")}</tbody></table></div>
      <div class="source-method heatmap-note"><strong>读法</strong> 列固定为全市场、广发基金、当前筛选下按策略数排序的非广发Top5投顾机构。先按每只策略在区间内同一资产类型的调后权重减调前权重累计，再按投顾机构统计；红色表示典型策略净增持，青色表示典型策略净减持，单位为百分点。</div>`;
  }

  function activeAssetBeforeAfterRows(rows) {
    const scoped = (rows || []).filter(reliableReportAsset);
    const strategyAssetRows = [...groupBy(scoped, (row) => `${row.投顾机构 || "未识别机构"}｜${reliableReportAsset(row)}｜${row.统一策略ID || ""}`).entries()]
      .map(([, list]) => {
        const ordered = [...list].sort((a, b) => String(a.调仓日期 || "").localeCompare(String(b.调仓日期 || "")));
        const first = ordered[0] || {};
        const last = ordered.at(-1) || {};
        const before = num(first.调前权重) ?? num(first.调后权重) ?? 0;
        const after = num(last.调后权重) ?? num(last.调前权重) ?? 0;
        return {
          投顾机构: first.投顾机构 || "未识别机构",
          资产类型: reliableReportAsset(first),
          统一策略ID: first.统一策略ID,
          策略名称: first.策略名称,
          调前权重: before,
          调后权重: after,
          权重变化: after - before,
          总点位: ordered.reduce((total, row) => total + (num(row.总点位) || Math.abs(num(row.净增配) || 0)), 0)
        };
      })
      .filter((row) => Math.abs(num(row.权重变化) || 0) > 0.0001 || (num(row.总点位) || 0) > 0.0001);
    const summarize = (list, institution) => {
      const base = list[0] || {};
      const add = list.filter((row) => (num(row.权重变化) || 0) > 0.0001);
      const reduce = list.filter((row) => (num(row.权重变化) || 0) < -0.0001);
      const changes = list.map((row) => num(row.权重变化)).filter((value) => value !== null);
      return {
        投顾机构: institution || base.投顾机构 || "未识别机构",
        资产类型: base.资产类型 || "混合型",
        总策略数: list.length,
        增持策略数: add.length,
        减持策略数: reduce.length,
        期初中位: median(list.map((row) => row.调前权重)),
        期末中位: median(list.map((row) => row.调后权重)),
        中位变化: median(changes),
        平均变化: avg(changes),
        净变化: sum(list, "权重变化"),
        总点位: sum(list, "总点位")
      };
    };
    const institutionRows = [...groupBy(strategyAssetRows, (row) => `${row.投顾机构}｜${row.资产类型}`).entries()].map(([, list]) => summarize(list));
    const marketRows = [...groupBy(strategyAssetRows, (row) => row.资产类型).entries()].map(([, list]) => summarize(list, "全市场汇总"));
    return [...marketRows, ...institutionRows].sort((a, b) => (num(b.总点位) || 0) - (num(a.总点位) || 0));
  }

  function activeAssetBeforeAfterHeatmap(rows) {
    const data = activeAssetBeforeAfterRows(rows);
    const columns = heatmapInstitutionColumns(data, "投顾机构", { sourceRows: rows, maxNonGf: 5 });
    const assetTypes = [...groupBy(data, (row) => row.资产类型).entries()]
      .map(([type, list]) => ({ 资产类型: type, 总点位: sum(list, "总点位") }))
      .sort((a, b) => (num(b.总点位) || 0) - (num(a.总点位) || 0))
      .slice(0, 7)
      .map((row) => row.资产类型);
    if (!columns.length || !assetTypes.length) return '<div class="empty">当前筛选下暂无主动调仓前后资产变化数据。</div>';
    const map = new Map(data.map((row) => [`${row.投顾机构}｜${row.资产类型}`, row]));
    const maxAbs = Math.max(1, ...columns.flatMap((column) => assetTypes.map((type) => Math.abs(num(map.get(`${column.key}｜${type}`)?.中位变化) || 0))));
    const cell = (column, type) => {
      const row = map.get(`${column.key}｜${type}`) || {};
      if (!Object.keys(row).length) return heatmapEmptyCell(`${column.label}｜${type}｜当前筛选区间该资产无主动调仓前后变化`, "0变化");
      const value = num(row.中位变化) || 0;
      const alpha = Math.min(.88, .1 + Math.abs(value) / maxAbs * .78);
      const bg = Math.abs(value) < 0.0001 ? "#f8fafc" : (value > 0 ? `rgba(180,35,24,${alpha.toFixed(2)})` : `rgba(15,118,110,${alpha.toFixed(2)})`);
      const color = Math.abs(value) / maxAbs > .45 ? "#fff" : "#172033";
      const title = `${column.label}｜${type}｜调前中位${weightPoint(row.期初中位)}｜调后中位${weightPoint(row.期末中位)}｜中位变化${weightPoint(row.中位变化)}｜平均变化${weightPoint(row.平均变化)}｜增${countText(row.增持策略数)} / 减${countText(row.减持策略数)}｜策略数${countText(row.总策略数)}`;
      return `<td class="heat-cell advisor-heat-cell" style="background:${bg};color:${color}" title="${B.esc(title)}">
        <b>${weightPoint(row.中位变化)}</b>
        <small>前 ${weightPoint(row.期初中位)}｜后 ${weightPoint(row.期末中位)}</small>
        <small>均值 ${weightPoint(row.平均变化)}</small>
        <small>增${countText(row.增持策略数)}｜减${countText(row.减持策略数)}｜${countText(row.总策略数)}策</small>
      </td>`;
    };
    return `<div class="heatmap-wrap"><table class="heatmap-table advisor-heatmap-table"><thead><tr><th>资产类型</th>${columns.map((column) => `<th>${B.esc(column.label)}</th>`).join("")}</tr></thead><tbody>${assetTypes.map((type) => `<tr><th>${B.esc(type)}</th>${columns.map((column) => cell(column, type)).join("")}</tr>`).join("")}</tbody></table></div>
      <div class="source-method heatmap-note"><strong>读法</strong> 列固定为全市场、广发基金、当前筛选下按策略数排序的非广发Top5投顾机构。仅统计当前区间内有主动调仓的策略，每个策略-资产类型取区间内第一次调仓前权重和最后一次调仓后权重比较；红色表示调后中位仓位上升，青色表示下降，单位为百分点。</div>`;
  }

  function filteredIndustryTimelineRows() {
    return rangeFiltered((insight.持仓行业时间序列 || []).filter(dimensionMatch), "月份", true);
  }

  let holdingSnapshotRowsCache = null;
  let holdingSnapshotPackPromise = null;
  function loadedHoldingSnapshotPack() {
    const inlinePack = insight.持仓日期分类快照;
    if (inlinePack && Array.isArray(inlinePack.rows) && inlinePack.dict) return inlinePack;
    return window.__BASIC_HOLDING_SNAPSHOT_PACK__ || null;
  }

  function ensureHoldingSnapshotPack() {
    if (loadedHoldingSnapshotPack()) return true;
    const meta = insight.持仓日期分类快照 || {};
    if (!meta.external || typeof fetch !== "function") return false;
    if (!holdingSnapshotPackPromise) {
      holdingSnapshotPackPromise = fetch(meta.external, { cache: "no-store" })
        .then((response) => {
          if (!response.ok) throw new Error(`HTTP ${response.status}`);
          return response.json();
        })
        .then((pack) => {
          window.__BASIC_HOLDING_SNAPSHOT_PACK__ = pack;
          holdingSnapshotRowsCache = null;
          render();
        })
        .catch((error) => {
          window.__BASIC_HOLDING_SNAPSHOT_ERROR__ = error?.message || String(error);
          render();
        });
    }
    return false;
  }

  function holdingSnapshotRows() {
    if (holdingSnapshotRowsCache) return holdingSnapshotRowsCache;
    const pack = loadedHoldingSnapshotPack();
    if (!pack) {
      ensureHoldingSnapshotPack();
      return [];
    }
    if (Array.isArray(pack)) {
      holdingSnapshotRowsCache = pack;
      return holdingSnapshotRowsCache;
    }
    const dict = pack.dict || {};
    const fields = pack.fields || [];
    holdingSnapshotRowsCache = (pack.rows || []).map((row) => {
      const strategy = dict.strategies?.[row[0]] || [];
      const version2 = Number(pack.version || 1) >= 2;
      return {
        统一策略ID: strategy[0] || "",
        策略名称: strategy[1] || "",
        快照日期: row[1],
        投顾机构: dict.institutions?.[row[2]] || "",
        是否广发策略: row[3] ? "是" : "否",
        风险等级: dict.risks?.[row[4]] || "",
        业务分类: dict.businesses?.[row[5]] || "",
        研报产品类型: version2 ? (dict.reportTypes?.[row[6]] || "") : "",
        研报股票子类型: version2 ? (dict.reportSubTypes?.[row[7]] || "") : "",
        市场地域: dict.regions?.[row[version2 ? 8 : 6]] || "",
        天天当前对客展示: dict.clients?.[row[version2 ? 9 : 7]] || "",
        天天展示状态: dict.statuses?.[row[version2 ? 10 : 8]] || "",
        分类字段: fields[row[version2 ? 11 : 9]] || "",
        分类: dict.categories?.[row[version2 ? 12 : 10]] || "",
        总权重: row[version2 ? 13 : 11],
        基金数: row[version2 ? 14 : 12] || 0,
        策略快照数: 1
      };
    });
    return holdingSnapshotRowsCache;
  }

  let rebalanceFundCategoryRowsCache = null;
  let rebalanceFundCategoryPackPromise = null;
  function loadedRebalanceFundCategoryPack() {
    const inlinePack = insight.调仓基金分类明细;
    if (inlinePack && Array.isArray(inlinePack.rows) && inlinePack.dict) return inlinePack;
    return window.__BASIC_REBALANCE_FUND_CATEGORY_PACK__ || null;
  }

  function ensureRebalanceFundCategoryPack() {
    if (loadedRebalanceFundCategoryPack()) return true;
    const meta = insight.调仓基金分类明细 || {};
    if (!meta.external || typeof fetch !== "function") return false;
    if (!rebalanceFundCategoryPackPromise) {
      rebalanceFundCategoryPackPromise = fetch(meta.external, { cache: "no-store" })
        .then((response) => {
          if (!response.ok) throw new Error(`HTTP ${response.status}`);
          return response.json();
        })
        .then((pack) => {
          window.__BASIC_REBALANCE_FUND_CATEGORY_PACK__ = pack;
          rebalanceFundCategoryRowsCache = null;
          render();
        })
        .catch((error) => {
          window.__BASIC_REBALANCE_FUND_CATEGORY_ERROR__ = error?.message || String(error);
          render();
        });
    }
    return false;
  }

  function rebalanceFundDetailEmptyText() {
    if (loadedRebalanceFundCategoryPack()) return "当前筛选下无基金级变化";
    if (window.__BASIC_REBALANCE_FUND_CATEGORY_ERROR__) return `基金明细包加载失败：${window.__BASIC_REBALANCE_FUND_CATEGORY_ERROR__}`;
    return "基金明细加载中";
  }

  function rebalanceFundCategoryRows() {
    if (rebalanceFundCategoryRowsCache) return rebalanceFundCategoryRowsCache;
    const pack = loadedRebalanceFundCategoryPack();
    if (!pack) {
      ensureRebalanceFundCategoryPack();
      return [];
    }
    if (Array.isArray(pack)) {
      rebalanceFundCategoryRowsCache = pack;
      return rebalanceFundCategoryRowsCache;
    }
    const dict = pack.dict || {};
    const fields = pack.fields || [];
    rebalanceFundCategoryRowsCache = (pack.rows || []).map((row) => {
      const strategy = dict.strategies?.[row[1]] || [];
      const fund = dict.funds?.[row[13]] || [];
      return {
        调仓日期: row[0],
        月份: raw(row[0]).slice(0, 7),
        统一策略ID: strategy[0] || "",
        策略名称: strategy[1] || "",
        投顾机构: dict.institutions?.[row[2]] || "",
        是否广发策略: row[3] ? "是" : "否",
        风险等级: dict.risks?.[row[4]] || "",
        业务分类: dict.businesses?.[row[5]] || "",
        研报产品类型: dict.reportTypes?.[row[6]] || "",
        研报股票子类型: dict.reportSubTypes?.[row[7]] || "",
        市场地域: dict.regions?.[row[8]] || "",
        天天当前对客展示: dict.clients?.[row[9]] || "",
        天天展示状态: dict.statuses?.[row[10]] || "",
        分类字段: fields[row[11]] || "",
        分类: dict.categories?.[row[12]] || "",
        基金代码: fund[0] || "",
        基金名称: fund[1] || "",
        基金公司: dict.companies?.[row[14]] || "",
        基金类型: dict.fundTypes?.[row[15]] || "",
        调前权重: num(row[16]) || 0,
        调后权重: num(row[17]) || 0,
        权重变化: num(row[18]) || 0,
        调仓动作: dict.actions?.[row[19]] || ""
      };
    });
    return rebalanceFundCategoryRowsCache;
  }

  function filteredHoldingSnapshotRows() {
    return holdingSnapshotRows().filter(dimensionMatch).filter(reportTypeMatch);
  }

  function industryPeriodRows(rows, field) {
    if ((rows || []).some((row) => row.快照日期 && row.分类字段)) return industryPeriodRowsFromSnapshots(rows, field);
    const filtered = field === "研报A股行业" ? (rows || []).filter((row) => isReportAIndustryTheme(rowCategoryForField(row, field))) : (rows || []);
    const months = [...new Set(filtered.map((row) => row.月份).filter(Boolean))].sort();
    if (months.length < 2) return [];
    const firstMonth = months[0];
    const lastMonth = months.at(-1);
    const monthRows = filtered.filter((row) => row.月份 === firstMonth || row.月份 === lastMonth);
    const institutions = [...new Set(monthRows.map((row) => row.投顾机构).filter(Boolean))];
    const build = (institution, list) => {
      const firstRows = list.filter((row) => row.月份 === firstMonth);
      const lastRows = list.filter((row) => row.月份 === lastMonth);
      const firstTotal = sum(firstRows, "总权重");
      const lastTotal = sum(lastRows, "总权重");
      if (firstTotal <= 0 || lastTotal <= 0) return [];
      const keys = [...new Set([...firstRows, ...lastRows].map((row) => raw(row[field])).filter(Boolean))];
      return keys.map((key) => {
        const firstValue = sum(firstRows.filter((row) => raw(row[field]) === key), "总权重");
        const lastValue = sum(lastRows.filter((row) => raw(row[field]) === key), "总权重");
        const firstShare = firstTotal ? firstValue / firstTotal * 100 : 0;
        const lastShare = lastTotal ? lastValue / lastTotal * 100 : 0;
        return {
          投顾机构: institution,
          分类: key,
          期初月份: firstMonth,
          期末月份: lastMonth,
          期初占比: firstShare,
          期末占比: lastShare,
          占比变化: lastShare - firstShare,
          总权重: firstValue + lastValue,
          策略快照数: sum([...firstRows, ...lastRows].filter((row) => raw(row[field]) === key), "策略快照数")
        };
      });
    };
    const rowsOut = [];
    rowsOut.push(...build("全市场汇总", monthRows));
    institutions.forEach((institution) => rowsOut.push(...build(institution, monthRows.filter((row) => row.投顾机构 === institution))));
    return rowsOut;
  }

  function industryPeriodRowsFromSnapshots(rows, field) {
    const filtered = (rows || []).filter((row) => row.分类字段 === field && row.快照日期 && row.统一策略ID && (field !== "研报A股行业" || isReportAIndustryTheme(row.分类)));
    if (!filtered.length) return [];
    const targets = rangeTargetsFromSnapshotRows(filtered);
    if (!targets.startTarget || !targets.endTarget) return [];
    const byStrategy = groupBy(filtered, (row) => row.统一策略ID);
    const agg = new Map();
    const totals = new Map();
    const addTotal = (institution, side, value) => {
      if (!totals.has(institution)) totals.set(institution, { start: 0, end: 0, _strategies: new Set() });
      totals.get(institution)[side] += value;
    };
    const addAggRow = (institution, category, side, value, strategyId, startDate, endDate) => {
      const key = `${institution}｜${category}`;
      if (!agg.has(key)) {
        agg.set(key, {
          投顾机构: institution,
          分类: category,
          期初日期: targets.startTarget,
          期末日期: targets.endTarget,
          _startDates: new Set(),
          _endDates: new Set(),
          _startWeight: 0,
          _endWeight: 0,
          _strategies: new Set()
        });
      }
      const out = agg.get(key);
      if (side === "start") out._startWeight += value;
      else out._endWeight += value;
      if (startDate) out._startDates.add(startDate);
      if (endDate) out._endDates.add(endDate);
      out._strategies.add(strategyId);
    };
    byStrategy.forEach((list, strategyId) => {
      const dates = [...new Set(list.map((row) => row.快照日期).filter(Boolean))].sort();
      const startDate = asOfSnapshotDate(dates, targets.startTarget);
      const endDate = asOfSnapshotDate(dates, targets.endTarget);
      if (!startDate || !endDate || startDate > endDate) return;
      const startRows = list.filter((row) => row.快照日期 === startDate);
      const endRows = list.filter((row) => row.快照日期 === endDate);
      if (!startRows.length || !endRows.length) return;
      const institution = startRows[0]?.投顾机构 || endRows[0]?.投顾机构 || "未识别机构";
      const addRows = (side, rowsAtDate) => {
        rowsAtDate.forEach((row) => {
          const value = num(row.总权重) || 0;
          if (value <= 0 || !row.分类) return;
          for (const institutionKey of ["全市场汇总", institution]) {
            addTotal(institutionKey, side, value);
            if (!totals.has(institutionKey)) totals.set(institutionKey, { start: 0, end: 0, _strategies: new Set() });
            totals.get(institutionKey)._strategies.add(strategyId);
            addAggRow(institutionKey, row.分类, side, value, strategyId, startDate, endDate);
          }
        });
      };
      addRows("start", startRows);
      addRows("end", endRows);
    });
    return [...agg.values()].map((row) => {
      const total = totals.get(row.投顾机构) || { start: 0, end: 0, _strategies: new Set() };
      const firstShare = total.start ? row._startWeight / total.start * 100 : null;
      const lastShare = total.end ? row._endWeight / total.end * 100 : null;
      return {
        投顾机构: row.投顾机构,
        分类: row.分类,
        期初日期: row.期初日期,
        期末日期: row.期末日期,
        期初月份: row.期初日期,
        期末月份: row.期末日期,
        实际期初快照数: row._startDates.size,
        实际期末快照数: row._endDates.size,
        期初占比: firstShare,
        期末占比: lastShare,
        占比变化: firstShare === null || lastShare === null ? null : lastShare - firstShare,
        总权重: row._startWeight + row._endWeight,
        策略快照数: total._strategies?.size || row._strategies.size,
        总策略数: total._strategies?.size || row._strategies.size
      };
    }).filter((row) => row.期初占比 !== null && row.期末占比 !== null);
  }

  function periodFallbackSignalTable(field, label, reason) {
    const options = field === "研报大类资产" ? {} : { requireField: true };
    if (field === "研报A股行业") options.onlyReportAIndustry = true;
    const rows = strategyAssetSignalRows(filteredStrategyAssetChangeRows(true), field, options);
    if (rows.length) {
      return `<div class="source-method"><strong>调仓变化口径</strong> ${B.esc(reason)} 当前改用同一观察窗口内的策略级净变化展示${B.esc(label)}方向：先按单只策略合并同一分类的调前/调后权重变化，再统计增配策略数、减配策略数和中位变化。</div>
        ${assetSignalTable(rows, label, 10)}`;
    }
    return `<div class="source-method"><strong>调仓变化口径</strong> 当前筛选下没有可识别的${B.esc(label)}调仓变化；这通常表示该类策略本期主要调仓发生在债券、现金、宽基或主动权益等无法进一步拆行业的基金上。</div>`;
  }

  function industryPeriodHeatmap(rows, field, label) {
    const scopedRows = field === "研报A股行业" ? (rows || []).filter((row) => isReportAIndustryTheme(rowCategoryForField(row, field))) : (rows || []);
    if (!scopedRows.length && !loadedHoldingSnapshotPack()) {
      const error = window.__BASIC_HOLDING_SNAPSHOT_ERROR__;
      return `<div class="empty">${error ? `持仓日期快照加载失败：${B.esc(error)}` : "正在加载持仓日期快照，用于按策略匹配区间起止最近可用仓位..."}</div>`;
    }
    const hasDateSnapshots = scopedRows.some((row) => row.快照日期 && row.分类字段);
    const rawPeriods = hasDateSnapshots
      ? [...new Set(scopedRows.filter((row) => row.分类字段 === field).map((row) => row.快照日期).filter(Boolean))].sort()
      : [...new Set(scopedRows.map((row) => row.月份).filter(Boolean))].sort();
    const data = industryPeriodRows(scopedRows, field);
    if (!data.length) {
      if (!scopedRows.length) {
        return periodFallbackSignalTable(field, label, `持仓快照已加载，但当前筛选下没有可识别的${label}仓位。`);
      }
      const fieldRows = hasDateSnapshots ? scopedRows.filter((row) => row.分类字段 === field) : scopedRows;
      if (!fieldRows.length) {
        return periodFallbackSignalTable(field, label, `可用持仓快照存在，但${label}分类口径没有覆盖到当前样本。`);
      }
      const periodText = rawPeriods.length ? `${rawPeriods[0]} 至 ${rawPeriods.at(-1)}` : "无可识别日期";
      return periodFallbackSignalTable(field, label, `${label}没有形成可比期初期末占比，可用快照范围：${periodText}。`);
    }
    const firstPeriodForColumns = data[0]?.期初日期 || data[0]?.期初月份 || rawPeriods[0];
    const lastPeriodForColumns = data[0]?.期末日期 || data[0]?.期末月份 || rawPeriods.at(-1);
    const sourceRows = hasDateSnapshots
      ? scopedRows.filter((row) => row.分类字段 === field && (row.快照日期 === firstPeriodForColumns || row.快照日期 === lastPeriodForColumns))
      : scopedRows.filter((row) => row.月份 === firstPeriodForColumns || row.月份 === lastPeriodForColumns);
    const columns = heatmapInstitutionColumns(data, "投顾机构", { sourceRows, maxNonGf: 5 });
    const marketRows = data.filter((row) => row.投顾机构 === "全市场汇总");
    const categories = [...marketRows]
      .sort((a, b) => (num(b.期末占比) || 0) - (num(a.期末占比) || 0) || (num(b.总权重) || 0) - (num(a.总权重) || 0) || Math.abs(num(b.占比变化) || 0) - Math.abs(num(a.占比变化) || 0))
      .slice(0, field === "行业大类" ? 8 : 16)
      .map((row) => row.分类);
    if (!columns.length || !categories.length) return '<div class="empty">当前筛选下暂无分布时间序列。</div>';
    const map = new Map(data.map((row) => [`${row.投顾机构}｜${row.分类}`, row]));
    const maxAbs = Math.max(1, ...columns.flatMap((column) => categories.map((category) => Math.abs(num(map.get(`${column.key}｜${category}`)?.占比变化) || 0))));
    const monthsText = `${marketRows[0]?.期初月份 || ""} 至 ${marketRows[0]?.期末月份 || ""}`;
    const cell = (column, category) => {
      const row = map.get(`${column.key}｜${category}`) || {};
      if (!Object.keys(row).length) {
        return `<td class="heat-cell advisor-heat-cell" style="background:#f8fafc;color:#94a3b8" title="${B.esc(`${column.label}｜${category}｜期初期末均无该分类仓位，或该机构缺少首末月可比快照｜${monthsText}`)}">
          <b>-</b>
          <small>无可比仓位</small>
          <small>${B.esc(monthsText)}</small>
        </td>`;
      }
      const value = num(row.占比变化) || 0;
      const alpha = Math.min(.88, .1 + Math.abs(value) / maxAbs * .78);
      const bg = Math.abs(value) < 0.0001 ? "#f8fafc" : (value > 0 ? `rgba(180,35,24,${alpha.toFixed(2)})` : `rgba(15,118,110,${alpha.toFixed(2)})`);
      const color = Math.abs(value) / maxAbs > .45 ? "#fff" : "#172033";
      const title = `${column.label}｜${category}｜期初${pct(row.期初占比)}｜期末${pct(row.期末占比)}｜变化${weightPoint(row.占比变化)}｜目标窗口${monthsText}｜期初实际快照日${countText(row.实际期初快照数)}个｜期末实际快照日${countText(row.实际期末快照数)}个`;
      return `<td class="heat-cell advisor-heat-cell" style="background:${bg};color:${color}" title="${B.esc(title)}">
        <b>${weightPoint(row.占比变化)}</b>
        <small>初 ${pct(row.期初占比)}｜末 ${pct(row.期末占比)}</small>
        <small>${B.esc(monthsText)}</small>
      </td>`;
    };
    return `<div class="heatmap-wrap"><table class="heatmap-table advisor-heatmap-table"><thead><tr><th>${B.esc(label)}</th>${columns.map((column) => `<th>${B.esc(column.label)}</th>`).join("")}</tr></thead><tbody>${categories.map((category) => `<tr><th>${B.esc(category)}</th>${columns.map((column) => cell(column, category)).join("")}</tr>`).join("")}</tbody></table></div>
      <div class="source-method heatmap-note"><strong>读法</strong> 列固定为全市场、广发基金、当前筛选下按策略数排序的非广发Top5投顾机构。每只策略分别按区间起点和终点取目标日期之前最近一次可用仓位；若起点前没有仓位，则取起点后的第一条可用仓位。各${B.esc(label)}权重在同一列内归一，各行按全市场期末占比从高到低排序；红色表示期末占比高于期初，青色表示下降。当前比较窗口：${B.esc(monthsText)}。</div>`;
  }

  function pagerControls(prefix, page, totalPages, pageSize, total) {
    return `<div class="pager-controls insight-pager">
      <button id="${prefix}Prev" class="pager-btn" type="button" ${page <= 1 ? "disabled" : ""}>上一页</button>
      <span class="small">第 ${countText(page)} / ${countText(totalPages)} 页，共 ${countText(total)} 条</span>
      <button id="${prefix}Next" class="pager-btn" type="button" ${page >= totalPages ? "disabled" : ""}>下一页</button>
      <select id="${prefix}PageSize" class="control compact-control">
        ${[10, 20, 50, 100].map((size) => `<option value="${size}" ${Number(pageSize) === size ? "selected" : ""}>每页${size}条</option>`).join("")}
      </select>
    </div>`;
  }

  function businessSortValue(row, field) {
    if (field === "区间收益") return num(row[returnMetric()]);
    if (field === "卡玛比率") return metricRaw(row, "卡玛比率");
    if (field === "期次数") return num(row.期次数 || 1);
    if (["最大回撤", "波动率", "夏普比率"].includes(field)) return num(row[field]);
    return raw(row[field]);
  }

  function businessProductTable(rows, businessKey) {
    const pageSize = 10;
    const sortField = state.businessSortField || "区间收益";
    const dir = state.businessSortDir === "asc" ? 1 : -1;
    const sorted = [...rows].sort((a, b) => {
      const av = businessSortValue(a, sortField);
      const bv = businessSortValue(b, sortField);
      if (typeof av === "number" && typeof bv === "number") return (av - bv) * dir;
      return raw(av).localeCompare(raw(bv), "zh-CN") * dir;
    }).map((row, index) => ({ ...row, 排名: index + 1 }));
    const totalPages = Math.max(1, Math.ceil(sorted.length / pageSize));
    const page = Math.max(1, Math.min(totalPages, state.businessPages[businessKey] || 1));
    state.businessPages[businessKey] = page;
    const data = sorted.slice((page - 1) * pageSize, page * pageSize);
    const headers = [
      ["排名", "排名"],
      ["策略名称", "策略名称"],
      ["投顾机构", "投顾机构"],
      ["区间收益", `${rangeLabel()}收益`],
      ["最大回撤", "最大回撤"],
      ["波动率", "波动率"],
      ["夏普比率", "夏普比率"],
      ["卡玛比率", "卡玛比率"],
      ["期次数", "期次数"]
    ];
    const head = headers.map(([field, label]) => `<th><span class="sort-label">${B.label(label)}<button class="sort-th" type="button" data-business-sort="${B.esc(field)}" aria-label="按${B.esc(label)}排序">${sortField === field ? (state.businessSortDir === "asc" ? "↑" : "↓") : "↕"}</button></span></th>`).join("");
    const body = data.length ? data.map((row) => `<tr>${headers.map(([field]) => {
      let value = "";
      if (field === "排名") value = countText(row.排名);
      else if (field === "策略名称") value = strategyLink(row);
      else if (field === "区间收益") value = signedPct(row[returnMetric()]);
      else if (field === "最大回撤" || field === "波动率") value = signedPct(row[field]);
      else if (field === "卡玛比率") value = ratioText(metricRaw(row, "卡玛比率"));
      else if (field === "期次数") value = countText(row.期次数 || 1);
      else value = B.fmt(row[field]);
      return `<td>${value}</td>`;
    }).join("")}</tr>`).join("") : `<tr><td colspan="${headers.length}"><div class="empty">暂无数据</div></td></tr>`;
    return `<div class="table-wrap insight-table"><table><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table></div>
      <div class="pager-controls insight-pager">
        <button class="pager-btn" type="button" data-business-page="${B.esc(businessKey)}" data-page-delta="-1" ${page <= 1 ? "disabled" : ""}>上一页</button>
        <span class="small">第 ${countText(page)} / ${countText(totalPages)} 页，共 ${countText(sorted.length)} 条，每页10条</span>
        <button class="pager-btn" type="button" data-business-page="${B.esc(businessKey)}" data-page-delta="1" ${page >= totalPages ? "disabled" : ""}>下一页</button>
      </div>`;
  }

  function businessAnalysisBlock(rows) {
    const stats = businessStats(rows);
    return stats.map((row, index) => {
      const businessKey = row.业务分类 || `业务分类${index + 1}`;
      const list = rows.filter((item) => item.业务分类 === row.业务分类 && businessProductScopeMatch(item));
      return `<details class="fold-block" data-business-key="${B.esc(businessKey)}" ${state.openBusiness === businessKey ? "open" : ""}>
        <summary>${B.esc(row.业务分类)}｜市场 ${countText(row.市场数量)}｜广发 ${countText(row.广发数量)}｜覆盖 ${pct(row.广发覆盖率)}｜收益差 ${row.收益差 === null ? "未披露" : `${Number(row.收益差).toFixed(2)}pct`}｜${B.esc(row.经营动作)}</summary>
        <div class="insight-callout"><strong>${B.esc(row.经营动作)}：</strong>${B.esc(row.经营判断)}</div>
        <h3>当前筛选全部产品</h3>
        ${businessProductTable(list, businessKey)}
      </details>`;
    }).join("");
  }

  function businessDecisionTable(rows) {
    const stats = businessStats(rows);
    return tableBlock(["业务分类", "经营动作", "市场数量", "广发数量", "广发覆盖率", "收益差", "回撤优势", "经营判断"], stats, (row, h) => {
      if (h === "经营动作") return `<span class="insight-chip ${actionClass(row[h])}">${B.esc(row[h])}</span>`;
      if (h.includes("数量")) return countText(row[h]);
      if (h.includes("覆盖率")) return pct(row[h]);
      if (h === "收益差" || h === "回撤优势") return row[h] === null ? "未披露" : `${Number(row[h]).toFixed(2)}pct`;
      if (h === "经营判断") return `<span class="small">${B.esc(row[h])}</span>`;
      return B.fmt(row[h]);
    });
  }

  function topProductText(rows, metric = returnMetric(), limit = 3) {
    const list = [...(rows || [])]
      .filter((row) => num(row[metric]) !== null)
      .sort((a, b) => (num(b[metric]) || -999999) - (num(a[metric]) || -999999))
      .slice(0, limit);
    if (!list.length) return "暂无可比产品";
    return list.map((row) => `${row.策略名称 || "未命名"}（${row.投顾机构 || "未识别机构"}，${metricPlain(metric, row[metric])}）`).join("；");
  }

  function managerAction(row) {
    const marketSize = num(row.市场数量) || 0;
    const gfCount = num(row.广发数量) || 0;
    const coverage = num(row.广发覆盖率) || 0;
    const returnGap = num(row.收益差);
    const drawdownEdge = num(row.回撤优势);
    if (!gfCount && marketSize >= 20) {
      return { 经营动作: "产品空白", 经营判断: "市场已有规模样本但广发缺位，优先判断是否需要自建、联营或借底层基金做组合包装。" };
    }
    if (coverage < 8 && marketSize >= 50) {
      return { 经营动作: "货架偏薄", 经营判断: "市场同类产品多但广发覆盖不足，应补不同风险档、期限、主题或渠道版本，避免销售只能反复推荐少数产品。" };
    }
    if (returnGap !== null && returnGap >= 0 && (drawdownEdge === null || drawdownEdge >= -1.5)) {
      return { 经营动作: "可包装营销", 经营判断: "广发同类中位收益不弱于市场，且回撤没有明显劣势，可以沉淀销售话术、白名单和渠道露出。" };
    }
    if (returnGap !== null && returnGap <= -5) {
      return { 经营动作: "能力复盘", 经营判断: "广发同类收益明显落后市场，应拆到代表产品、底层基金和调仓节奏看差距来源，不建议直接强化营销。" };
    }
    if (marketSize >= 30 && coverage < 15) {
      return { 经营动作: "机会跟踪", 经营判断: "市场有一定规模，广发已有布局但覆盖不深，适合跟踪头部竞品和持仓偏好，等待产品或渠道切入点。" };
    }
    return { 经营动作: "持续跟踪", 经营判断: "当前没有清晰的货架缺口或业绩优势，保留观察即可，不应占用主要经营资源。" };
  }

  function hasBlockingDataIssue(row) {
    if (!row) return true;
    if (row.风险等级 === "D0 持仓缺失" || row.研报产品类型 === "持仓缺失/不入池") return true;
    if (row.数据完整性 && row.数据完整性 !== "完整") return true;
    return !raw(row.最新持仓日);
  }

  function salesGateFields(row) {
    const fields = [];
    if (!row) return fields;
    if (row.费率状态 === "缺失" || num(row.年化投顾费率) === null) fields.push("费率");
    if (!raw(row.投资经理) || row.投资经理 === "未披露") fields.push("投资经理");
    if (!raw(row.披露风险等级) || row.披露风险等级 === "未披露") fields.push("披露风险");
    return fields;
  }

  function managerGate(row, groupRows, gfRows) {
    const action = row.经营动作;
    const candidates = gfRows && gfRows.length ? gfRows : groupRows;
    const usable = (candidates || []).filter((item) => !hasBlockingDataIssue(item));
    const direct = usable.filter((item) => !salesGateFields(item).length);
    const salesFields = [...new Set(usable.flatMap(salesGateFields))];
    if (action === "产品空白") {
      return { 经营门禁: "先做产品决策", 门禁说明: "广发当前无同类样本，不能直接销售；先判断是否自建、联营或包装底层基金。" };
    }
    if (action === "货架偏薄") {
      return { 经营门禁: "先补货架", 门禁说明: "广发覆盖不足，先补风险档、期限、主题或渠道版本，再决定重点营销名单。" };
    }
    if (action === "能力复盘") {
      if (!usable.length) return { 经营门禁: "先补数据", 门禁说明: "广发样本缺少可用持仓或完整数据，先补齐后再做投研复盘。" };
      return { 经营门禁: "投研可复盘", 门禁说明: "同类比较可用于投研复盘；复盘结论进入销售前仍需核验具体产品字段。" };
    }
    if (action === "可包装营销") {
      if (direct.length) return { 经营门禁: "可直接行动", 门禁说明: `${countText(direct.length)}个广发样本字段足够，可先进入销售话术和渠道白名单。` };
      if (usable.length) return { 经营门禁: "销售前补齐", 门禁说明: `同类竞争力判断可用，但广发样本销售材料前需补${salesFields.join("、") || "关键字段"}。` };
      return { 经营门禁: "先补数据", 门禁说明: "没有可用于销售包装的广发完整样本，先补齐持仓和基础字段。" };
    }
    if (action === "机会跟踪") {
      return { 经营门禁: "月度跟踪", 门禁说明: "当前不是本期重点投入项，保留市场规模、竞品和持仓变化的月度监控。" };
    }
    return { 经营门禁: "观察即可", 门禁说明: "没有明确货架缺口、营销优势或能力短板，不占用主要经营资源。" };
  }

  function managerPriority(row) {
    const marketSize = num(row.市场数量) || 0;
    const coverage = num(row.广发覆盖率) || 0;
    const returnGap = num(row.收益差);
    const actionBoost = { 产品空白: 45, 货架偏薄: 35, 能力复盘: 30, 可包装营销: 24, 机会跟踪: 18, 持续跟踪: 5 }[row.经营动作] || 0;
    const scale = Math.min(40, Math.log1p(marketSize) * 9);
    const gap = returnGap === null ? 0 : Math.max(0, -returnGap) * 1.8;
    const thin = Math.max(0, 15 - coverage) * 1.2;
    return actionBoost + scale + gap + thin;
  }

  function managerGroupStats(rows, field) {
    return [...groupBy(rows || [], (row) => row[field] || "未分类").entries()].map(([name, list]) => {
      const gf = list.filter(isGf);
      const non = list.filter((row) => !isGf(row));
      const marketReturn = median(list.map((row) => row[returnMetric()]));
      const gfReturn = median(gf.map((row) => row[returnMetric()]));
      const marketDrawdown = median(list.map((row) => row.最大回撤));
      const gfDrawdown = median(gf.map((row) => row.最大回撤));
      const row = {
        维度: name,
        市场数量: list.length,
        广发数量: gf.length,
        非广发数量: non.length,
        广发覆盖率: list.length ? gf.length / list.length * 100 : null,
        市场中位收益: marketReturn,
        广发中位收益: gfReturn,
        收益差: gfReturn === null || marketReturn === null ? null : gfReturn - marketReturn,
        市场中位回撤: marketDrawdown,
        广发中位回撤: gfDrawdown,
        回撤优势: gfDrawdown === null || marketDrawdown === null ? null : marketDrawdown - gfDrawdown,
        代表竞品: topProductText(non.length ? non : list, returnMetric(), 2),
        广发代表: topProductText(gf, returnMetric(), 2)
      };
      const action = managerAction(row);
      const out = { ...row, ...action };
      Object.assign(out, managerGate(out, list, gf));
      out.经营优先级 = managerPriority(out);
      return out;
    }).sort((a, b) => {
      if (field === "研报产品类型") return reportTypeRank(a.维度) - reportTypeRank(b.维度) || b.经营优先级 - a.经营优先级;
      return b.经营优先级 - a.经营优先级 || b.市场数量 - a.市场数量;
    });
  }

  function managerActionClass(action) {
    if (action === "可包装营销") return "good";
    if (action === "能力复盘") return "bad";
    if (action === "产品空白" || action === "货架偏薄" || action === "机会跟踪") return "warn";
    return "";
  }

  function managerGateClass(gate) {
    if (gate === "可直接行动" || gate === "投研可复盘") return "good";
    if (gate === "先补数据") return "bad";
    if (gate === "销售前补齐" || gate === "先做产品决策" || gate === "先补货架") return "warn";
    return "";
  }

  function managerDecisionCards(reportRows, businessRows, rows) {
    const cards = [];
    const thin = businessRows.find((row) => ["产品空白", "货架偏薄"].includes(row.经营动作));
    const packageable = [...businessRows, ...reportRows].find((row) => row.经营动作 === "可包装营销");
    const review = [...businessRows, ...reportRows].find((row) => row.经营动作 === "能力复盘");
    const gfRows = rows.filter(isGf);
    const marketMedian = median(rows.map((row) => row[returnMetric()]));
    const gfMedian = median(gfRows.map((row) => row[returnMetric()]));
    if (thin) cards.push({
      title: "先补货架",
      value: thin.维度,
      body: `${thin.维度}市场${countText(thin.市场数量)}个、广发${countText(thin.广发数量)}个，覆盖${pct(thin.广发覆盖率)}；${thin.经营判断} 门禁：${thin.经营门禁}。`
    });
    if (review) cards.push({
      title: "先复盘能力",
      value: signedPct(review.收益差),
      body: `${review.维度}广发中位${rangeLabel()}收益相对市场${signedPctText(review.收益差)}，代表竞品：${review.代表竞品} 门禁：${review.经营门禁}。`
    });
    if (packageable) cards.push({
      title: "可转销售话术",
      value: packageable.维度,
      body: `${packageable.维度}广发中位不弱于市场，回撤优势${packageable.回撤优势 === null ? "未披露" : `${Number(packageable.回撤优势).toFixed(2)}pct`}；代表产品：${packageable.广发代表} 门禁：${packageable.经营门禁}。`
    });
    cards.push({
      title: "全局位置",
      value: signedPct(gfMedian === null || marketMedian === null ? null : gfMedian - marketMedian),
      body: `当前筛选下广发${countText(gfRows.length)}个、市场${countText(rows.length)}个；广发中位${rangeLabel()}收益与市场差值如右，适合先按类型拆解，不看总平均。`
    });
    return cards.slice(0, 4);
  }

  function managerNextStep(row, dimensionLabel) {
    const action = row.经营动作;
    const dimension = row.维度 || "未分类";
    const marketText = `市场${countText(row.市场数量)}个、广发${countText(row.广发数量)}个、覆盖${pct(row.广发覆盖率)}`;
    const returnText = row.收益差 === null ? "收益差未披露" : `收益差${Number(row.收益差) >= 0 ? "+" : ""}${Number(row.收益差).toFixed(2)}pct`;
    const drawdownText = row.回撤优势 === null ? "回撤优势未披露" : `回撤优势${Number(row.回撤优势).toFixed(2)}pct`;
    const evidence = `${marketText}；${returnText}；${drawdownText}`;
    const priority = row.经营优先级 >= 75 ? "高" : (row.经营优先级 >= 55 ? "中" : "低");
    const actionMap = {
      产品空白: {
        下一步: "先做竞品货架拆解，判断广发是否需要自建组合、联营组合或用底层基金做场景包装。",
        负责人关注点: "不要先看收益排名，先确认市场规模、客户场景、渠道是否愿意卖。"
      },
      货架偏薄: {
        下一步: "列出同类头部竞品和广发现有产品差异，补风险档、期限、主题或渠道版本。",
        负责人关注点: "重点判断销售是否缺可推荐清单，而不是单只产品能不能打。"
      },
      可包装营销: {
        下一步: row.经营门禁 === "销售前补齐" ? "先补齐费率、投资经理或披露风险字段，再沉淀销售话术、代表产品白名单和渠道露出素材。" : "沉淀销售话术、代表产品白名单和渠道露出素材，同时保留回撤边界说明。",
        负责人关注点: "可以进入营销，但要用同类可比池讲优势，避免跨风险档宣传。"
      },
      能力复盘: {
        下一步: "拆到底层基金、调仓节奏、权益/债券暴露和代表竞品，形成投研复盘清单。",
        负责人关注点: "先暂停强化营销，避免把短板产品推到渠道前台。"
      },
      机会跟踪: {
        下一步: "持续跟踪头部竞品持仓、调仓方向和客户需求变化，等待产品或渠道切入点。",
        负责人关注点: "暂不立刻投入重资源，但要保留月度监控。"
      },
      持续跟踪: {
        下一步: "保留月度观察，不进入本期重点经营动作。",
        负责人关注点: "没有明确货架缺口或业绩优势时，不占用主要经营资源。"
      }
    }[action] || {
      下一步: "结合代表产品和底层持仓做人工复核。",
      负责人关注点: "先确认分类口径和样本质量。"
    };
    return {
      业务维度: dimensionLabel,
      场景: dimension,
      经营动作: action,
      优先级: priority,
      经营门禁: row.经营门禁,
      门禁说明: row.门禁说明,
      证据: evidence,
      ...actionMap,
      经营优先级: row.经营优先级
    };
  }

  function managerActionQueue(reportRows, businessRows) {
    const rows = [
      ...reportRows.map((row) => managerNextStep(row, "研报产品类型")),
      ...businessRows.map((row) => managerNextStep(row, "业务分类"))
    ].filter((row) => row.经营动作 !== "持续跟踪");
    return rows
      .sort((a, b) => (b.经营优先级 || 0) - (a.经营优先级 || 0))
      .slice(0, 8);
  }

  function managerEvidenceParams(dimension, value, action = "") {
    const actionParams = {
      产品空白: { strategyScope: "nonGf", clientScope: "client", sort: "return", pageSize: 50 },
      货架偏薄: { clientScope: "client", sort: "return", pageSize: 50 },
      可包装营销: { strategyScope: "gf", clientScope: "client", businessSignal: "可进候选", pageSize: 50 },
      能力复盘: { strategyScope: "gf", clientScope: "client", businessSignal: "能力复盘", pageSize: 50 },
      机会跟踪: { clientScope: "client", sort: "return", pageSize: 50 }
    }[action] || {};
    if (dimension === "研报产品类型") return { reportType: value, ...actionParams };
    if (dimension === "业务分类") return { business: value, ...actionParams };
    return { ...actionParams };
  }

  function managerEvidenceLink(dimension, value, action = "") {
    return strategyEvidenceLink({ 链接参数: managerEvidenceParams(dimension, value, action) });
  }

  function managerActionQueueTable(rows) {
    return tableBlock(["优先级", "业务维度", "场景", "核验证据", "经营动作", "经营门禁", "证据", "下一步", "负责人关注点"], rows, (row, h) => {
      if (h === "经营动作") return `<span class="insight-chip ${managerActionClass(row[h])}">${B.esc(row[h])}</span>`;
      if (h === "经营门禁") return `<span class="insight-chip ${managerGateClass(row[h])}" title="${B.esc(row.门禁说明 || "")}">${B.esc(row[h])}</span>`;
      if (h === "优先级") return `<span class="insight-chip ${row[h] === "高" ? "bad" : (row[h] === "中" ? "warn" : "")}">${B.esc(row[h])}</span>`;
      if (h === "核验证据") return managerEvidenceLink(row.业务维度, row.场景, row.经营动作);
      if (h === "证据" || h === "下一步" || h === "负责人关注点") return `<span class="small">${B.esc(row[h])}</span>`;
      return B.fmt(row[h]);
    });
  }

  function managerTable(rows, label) {
    return tableBlock([label, "核验证据", "经营动作", "经营门禁", "市场数量", "广发数量", "广发覆盖率", "市场中位收益", "广发中位收益", "收益差", "回撤优势", "经营判断"], rows, (row, h) => {
      if (h === label) return B.fmt(row.维度);
      if (h === "核验证据") return managerEvidenceLink(label, row.维度, row.经营动作);
      if (h === "经营动作") return `<span class="insight-chip ${managerActionClass(row[h])}">${B.esc(row[h])}</span>`;
      if (h === "经营门禁") return `<span class="insight-chip ${managerGateClass(row[h])}" title="${B.esc(row.门禁说明 || "")}">${B.esc(row[h])}</span>`;
      if (h.includes("数量")) return countText(row[h]);
      if (h.includes("覆盖率")) return pct(row[h]);
      if (h.includes("收益") || h === "收益差") return signedPct(row[h]);
      if (h === "回撤优势") return row[h] === null ? "未披露" : `${Number(row[h]).toFixed(2)}pct`;
      if (h === "经营判断") return `<span class="small">${B.esc(row[h])}</span>`;
      return B.fmt(row[h]);
    });
  }

  function managerBenchmarkTable(rows, label) {
    return tableBlock([label, "核验证据", "经营门禁", "代表竞品", "广发代表", "市场数量", "广发数量", "广发覆盖率"], rows, (row, h) => {
      if (h === label) return B.fmt(row.维度);
      if (h === "核验证据") return managerEvidenceLink(label, row.维度, row.经营动作);
      if (h === "经营门禁") return `<span class="insight-chip ${managerGateClass(row[h])}" title="${B.esc(row.门禁说明 || "")}">${B.esc(row[h])}</span>`;
      if (h === "代表竞品" || h === "广发代表") return `<span class="small">${B.esc(row[h])}</span>`;
      if (h.includes("数量")) return countText(row[h]);
      if (h.includes("覆盖率")) return pct(row[h]);
      return B.fmt(row[h]);
    });
  }

  function dataQualityRows(rows) {
    const sourceTotal = num(summary.overview?.策略总数) || masterStrategies.length || allPoints.length;
    const hiddenDetailGap = Math.max(0, sourceTotal - masterStrategies.length);
    const hiddenChannelCount = num(summary.strategyListStats?.隐藏渠道数) || 0;
    const allScoped = allPoints.filter(dataQualityScopeMatch);
    const validRaw = rawPoints.filter(dataQualityScopeMatch);
    const masterScoped = masterStrategies.filter(dataQualityScopeMatch);
    const valid = rows.length;
    const gf = rows.filter(isGf).length;
    const client = rows.filter((row) => isClientFacing(row)).length;
    const d0 = allScoped.filter((row) => row.风险等级 === "D0 持仓缺失").length;
    const mergedTargetPeriods = Math.max(0, validRaw.length - valid);
    const incompleteRaw = masterScoped.filter((row) => row.数据完整性 !== "完整").length;
    const missingFee = masterScoped.filter((row) => num(row.年化投顾费率) === null).length;
    const missingDisclosedRisk = masterScoped.filter((row) => !raw(row.披露风险等级) || row.披露风险等级 === "未披露").length;
    const missingManager = masterScoped.filter((row) => !raw(row.投资经理) || row.投资经理 === "未披露").length;
    return [
      { 项目: "源表策略总数（全局）", 数值: sourceTotal, 业务含义: "基础策略信息表中的全量记录数，不随页面分类筛选变化；它是数据接入规模，不等同于可核验证据规模。", 链接参数: null },
      { 项目: "可核验策略记录", 数值: allScoped.length, 业务含义: "当前筛选下已进入策略列表和洞察明细的记录数，包含目标盈多期、历史期次和持仓缺失样本；不能直接当作经营产品数。", 链接参数: {} },
      { 项目: "未进入策略明细（全局）", 数值: hiddenDetailGap, 业务含义: `全局有${countText(hiddenDetailGap)}条源表策略未进入策略列表和洞察明细，涉及${countText(hiddenChannelCount)}个暂不展示渠道；不能下钻核验，也不能纳入业务结论。`, 链接参数: null },
      { 项目: "有效策略样本", 数值: valid, 业务含义: "当前洞察剔除D0持仓缺失后可用于市场、业绩和持仓分析的策略数量。", 链接参数: {} },
      { 项目: "目标盈期次归并", 数值: mergedTargetPeriods, 业务含义: "目标盈等按期发售产品已在市场总览归并为系列；该数值表示被合并掉的期次记录，避免夸大市场规模。", 链接参数: { business: "目标盈系列产品" } },
      { 项目: "广发样本", 数值: gf, 业务含义: "当前筛选下广发投顾产品数量，决定同类比较是否足够稳定。", 链接参数: { strategyScope: "gf" } },
      { 项目: "对客样本", 数值: client, 业务含义: "当前可明确对客展示或未被标为非对客的策略数量；经营动作优先看对客范围。", 链接参数: { clientScope: "client" } },
      { 项目: "D0持仓缺失", 数值: d0, 业务含义: "这部分不进入洞察主图，只能作为数据补齐清单，不能用于业务判断。", 链接参数: { dataIssue: "d0", risk: "" } },
      { 项目: "不完整策略记录", 数值: incompleteRaw, 业务含义: "这部分字段或关键数据不完整，策略列表会保留用于数据补齐核验，但不应进入经营结论。", 链接参数: { dataIssue: "incomplete" } },
      { 项目: "费率缺失", 数值: missingFee, 业务含义: "缺投顾费率时不能做费率竞争力、渠道让利或产品定价判断；需补采后再进入经营结论。", 链接参数: { dataIssue: "fee" } },
      { 项目: "披露风险缺失", 数值: missingDisclosedRisk, 业务含义: "披露风险缺失不影响系统测算风险等级，但不能用于核验平台展示口径或销售适当性披露差异。", 链接参数: { dataIssue: "disclosedRisk" } },
      { 项目: "投资经理缺失", 数值: missingManager, 业务含义: "当前数据不足以做投资经理排行榜或经理画像；相关页面只能做产品和机构维度分析，需补采经理字段后再展开。", 链接参数: { dataIssue: "manager" } },
      { 项目: "调仓事件", 数值: (insight.调仓事件 || []).filter(dimensionMatch).length, 业务含义: "可用于调仓复盘的事件数量；需要按同类策略分池解读。", 链接参数: {} },
      { 项目: "持仓基金明细", 数值: (insight.当前持仓策略基金明细 || []).filter(dimensionMatch).length, 业务含义: "用于底层基金、基金公司和资产分布分析的策略-基金持仓样本。", 链接参数: {} }
    ];
  }

  function strategyEvidenceUrl(overrides = {}) {
    const params = new URLSearchParams();
    const base = {
      strategyScope: state.gfScope,
      clientScope: state.clientScope,
      institution: state.institution,
      business: state.business,
      region: state.region,
      risk: state.risk
    };
    Object.entries({ ...base, ...overrides }).forEach(([key, value]) => {
      if (value) params.set(key, value);
    });
    const query = params.toString();
    return `./strategies.html${query ? `?${query}` : ""}`;
  }

  function strategyEvidenceLink(row) {
    if (row.链接参数 === null) return '<span class="small">无明细</span>';
    return `<a class="link" href="${B.esc(strategyEvidenceUrl(row.链接参数 || {}))}">查看策略</a>`;
  }

  function dataQualityGate(row) {
    const item = raw(row.项目);
    if (/D0|不完整|持仓缺失/.test(item)) return "阻断经营结论";
    if (/费率|披露风险|投资经理/.test(item)) return "销售/披露前补齐";
    if (/源表|未进入|目标盈/.test(item)) return "口径提示";
    if (/调仓|持仓基金/.test(item)) return "验证明细";
    return "可用样本";
  }

  function cockpitTab() {
    const rows = strategyRows();
    const gf = rows.filter(isGf);
    const reportRows = managerGroupStats(rows, "研报产品类型");
    const businessRows = managerGroupStats(rows, "业务分类");
    const cards = managerDecisionCards(reportRows, businessRows, rows);
    const actionQueue = managerActionQueue(reportRows, businessRows);
    const opportunityCount = businessRows.filter((row) => ["产品空白", "货架偏薄", "机会跟踪"].includes(row.经营动作)).length;
    const reviewCount = businessRows.filter((row) => row.经营动作 === "能力复盘").length;
    const packageCount = businessRows.filter((row) => row.经营动作 === "可包装营销").length;
    const marketMedian = median(rows.map((row) => row[returnMetric()]));
    const gfMedian = median(gf.map((row) => row[returnMetric()]));
    return `
      <section class="insight-hero">
        ${kpi("有效市场样本", countText(rows.length), "不含D0；目标盈同系列合并")}
        ${kpi("广发样本", countText(gf.length), `覆盖 ${pct(rows.length ? gf.length / rows.length * 100 : null)}`)}
        ${kpi(`市场中位${rangeLabel()}收益`, signedPct(marketMedian), "当前筛选")}
        ${kpi(`广发中位${rangeLabel()}收益`, signedPct(gfMedian), "当前筛选", gfMedian !== null && marketMedian !== null && gfMedian >= marketMedian ? "is-good" : "is-warn")}
        ${kpi("货架机会", countText(opportunityCount), "产品空白/偏薄/待跟踪")}
        ${kpi("需复盘场景", countText(reviewCount), `可包装 ${countText(packageCount)} 类`)}
      </section>
      <section class="panel" id="manager-focus">
        <div class="panel-head"><div><h2>负责人先看</h2><p class="desc">把市场规模、广发覆盖、相对业绩和回撤放到同一个经营判断里，只保留需要做动作的信号。</p></div></div>
        ${rebalanceConclusionList(cards)}
        <div class="source-method"><strong>读法</strong> 这里不看全市场平均值，而是先按研报产品类型和业务场景拆池。广发回撤更低但收益落后时，不直接判为“差”，需要区分是稳健定位、底层基金选择还是缺少进攻型货架。</div>
      </section>
      <section class="panel" id="next-actions">
        <div class="panel-head"><div><h2>下一步清单</h2><p class="desc">把经营动作转成可派给产品、投研、销售和营销的任务；只列当前筛选下最需要处理的场景。</p></div></div>
        ${managerActionQueueTable(actionQueue)}
      </section>
      <section class="panel" id="product-map">
        <div class="panel-head"><div><h2>产品类型经营地图</h2><p class="desc">这是负责人层面的第一张表：每类策略市场有多大、广发货架够不够、收益和回撤是否能支持销售包装。</p></div></div>
        ${managerTable(reportRows, "研报产品类型")}
      </section>
      <section class="panel" id="business-opportunity">
        <div class="panel-head"><div><h2>业务场景机会</h2><p class="desc">按经营优先级排序，不按数据量堆砌；优先展示产品空白、货架偏薄、能力复盘和可包装营销场景。</p></div></div>
        ${managerTable(businessRows.slice(0, 12), "业务分类")}
      </section>
      <section class="panel">
        <div class="panel-head"><div><h2>头部竞品与广发代表</h2><p class="desc">每个类型只展示少量代表产品，用于判断销售话术、产品包装和投研复盘要对标谁。</p></div></div>
        ${managerBenchmarkTable(reportRows, "研报产品类型")}
      </section>
      <section class="panel" id="data-risk">
        <div class="panel-head"><div><h2>数据可用性与误读风险</h2><p class="desc">把不能直接用于经营判断的数据单独说明，避免用缺失样本、非对客样本或混合可比池得出结论。</p></div></div>
        ${tableBlock(["项目", "数值", "经营门禁", "业务含义", "核验证据"], dataQualityRows(rows), (row, h) => {
          if (h === "数值") return countText(row[h]);
          if (h === "经营门禁") {
            const gate = dataQualityGate(row);
            const cls = gate === "阻断经营结论" ? "action-watch" : (gate === "销售/披露前补齐" ? "action-hold" : "action-attack");
            return `<span class="insight-chip ${cls}">${B.esc(gate)}</span>`;
          }
          if (h === "核验证据") return strategyEvidenceLink(row);
          return B.fmt(row[h]);
        })}
      </section>`;
  }

  function marketTab() {
    const rows = strategyRows();
    const gf = rows.filter(isGf);
    const marketMedian = median(rows.map((row) => row[returnMetric()]));
    const gfMedian = median(gf.map((row) => row[returnMetric()]));
    const riskRows = riskCountRows(rows);
    const bStats = businessStats(rows);
    const reportRows = managerGroupStats(rows, "研报产品类型");
    const businessRows = managerGroupStats(rows, "业务分类");
    const selected = selectedPoint(rows);
    return `
      <section class="insight-hero">
        ${kpi("全市场产品", countText(rows.length), "目标盈同系列合并；不含D0")}
        ${kpi("广发产品", countText(gf.length), "广发基金投顾")}
        ${kpi(`市场中位${rangeLabel()}收益`, signedPct(marketMedian), "同筛选口径")}
        ${kpi(`广发中位${rangeLabel()}收益`, signedPct(gfMedian), "同筛选口径", gfMedian !== null && marketMedian !== null && gfMedian >= marketMedian ? "is-good" : "is-warn")}
        ${kpi("广发相对差", signedPct(gfMedian === null || marketMedian === null ? null : gfMedian - marketMedian), "广发中位 - 市场中位")}
        ${kpi("目标盈系列", countText(rows.filter((row) => row.业务分类 === "目标盈系列产品").length), "同系列按一个产品多期")}
      </section>
      <section class="panel" id="market-competition">
        <div class="panel-head"><div><h2>产品类型概览</h2><p class="desc">以研报产品类型作为第一可比口径，展示市场数量、广发数量、收益和回撤等客观指标，避免把不同风险收益特征的策略混在一起。</p></div></div>
        ${managerTable(reportRows, "研报产品类型")}
      </section>
      <section class="panel chart-panel">
        <div class="panel-head">
          <div><h2>策略表现点阵</h2><p class="desc">纵轴固定为${B.esc(rangeLabel())}收益率，背景按当前样本动态划分收益领先层、中位观察层和承压层。</p></div>
          <div class="chart-actions">
            ${gfScopeSelect("scatterGfScope", state.gfScope, "control compact-control")}
            ${institutionSelect("scatterInstitution", state.institution, "control institution-control")}
            <select id="scatterX" class="control">${xAxisOptions.map((item) => `<option ${item === state.scatterX ? "selected" : ""}>${B.esc(item)}</option>`).join("")}</select>
            <label class="small" style="display:flex;align-items:center;gap:8px;min-width:220px">视野范围 ${state.viewPct}%
              <input id="scatterViewPct" class="control" type="range" min="55" max="100" step="5" value="${state.viewPct}" style="width:130px;padding:0">
            </label>
          </div>
        </div>
        ${scatterPlot(rows)}
        ${selectedPointPanel(selected)}
      </section>
      <details class="fold-block">
        <summary>验证区：风险数量、业务覆盖和全部产品明细</summary>
        <section class="insight-grid">
          <div class="panel">
            <div class="panel-head"><div><h2>风险等级产品数量</h2><p class="desc">柱为全市场产品数量，细线为广发产品数量。</p></div></div>
            ${barList(riskRows, "风险等级", "市场数量", { targetField: "广发数量", formatter: (value, row) => `市场${countText(value)} / 广发${countText(row.广发数量)}`, limit: 8 })}
          </div>
          <div class="panel">
            <div class="panel-head"><div><h2>业务分类覆盖</h2><p class="desc">按市场产品数量排序，并显示广发覆盖数量。</p></div></div>
            ${barList(bStats, "业务分类", "市场数量", { targetField: "广发数量", formatter: (value, row) => `市场${countText(value)} / 广发${countText(row.广发数量)}`, limit: 10 })}
          </div>
        </section>
        <section class="panel">
          <div class="panel-head"><div><h2>业务分类经营判断</h2><p class="desc">旧业务分类视角保留用于销售、产品和投研分工核验。</p></div></div>
          ${businessDecisionTable(rows)}
        </section>
        <section class="panel">
          <div class="panel-head">
            <div><h2>业务分类分析</h2><p class="desc">展开查看当前筛选范围内的全部产品排名；可单独切换只看广发或非广发。</p></div>
            <div class="chart-actions">
              ${gfScopeSelect("businessProductScope", state.businessProductScope, "control compact-control")}
            </div>
          </div>
          ${businessAnalysisBlock(rows)}
        </section>
      </details>`;
  }

  function filteredHoldingRows() {
    return (insight.当前持仓基金风险明细 || []).filter(dimensionMatch);
  }

  function filteredCompanyRows() {
    return (insight.当前持仓基金公司风险明细 || []).filter(dimensionMatch);
  }

  function filteredHoldingStrategyRows() {
    return (insight.当前持仓策略基金明细 || []).filter(dimensionMatch);
  }

  function holdingKey(row) {
    return `${row.基金代码 || ""}｜${row.基金名称 || ""}`;
  }

  function fundDetailUrl(row) {
    const params = new URLSearchParams();
    if (row.基金代码) params.set("code", row.基金代码);
    if (row.基金名称) params.set("name", row.基金名称);
    return `./fund.html?${params.toString()}`;
  }

  function fundNameCell(row, key) {
    return `<div class="fund-name-actions">
      ${fundLink(row)}
      <button class="mini-link link-button fund-toggle" type="button" data-fund-key="${B.esc(key)}">持仓策略</button>
    </div>`;
  }

  function rollupHoldingDetails(rows, keyFields) {
    return [...groupBy(rows || [], (row) => keyFields.map((field) => row[field] || "").join("｜")).entries()].map(([, list]) => {
      const base = list[0] || {};
      const out = { ...base };
      const strategyIds = new Set(list.map((row) => row.统一策略ID).filter(Boolean));
      const gfStrategyIds = new Set(list.filter(isGf).map((row) => row.统一策略ID).filter(Boolean));
      const nonGfList = list.filter((row) => !isGf(row));
      const nonGfStrategyIds = new Set(nonGfList.map((row) => row.统一策略ID).filter(Boolean));
      const addStrategyIds = new Set(list.filter((row) => (num(row.权重变化) || 0) > 0.0001).map((row) => row.统一策略ID).filter(Boolean));
      const reduceStrategyIds = new Set(list.filter((row) => (num(row.权重变化) || 0) < -0.0001).map((row) => row.统一策略ID).filter(Boolean));
      const nonGfAddStrategyIds = new Set(nonGfList.filter((row) => (num(row.权重变化) || 0) > 0.0001).map((row) => row.统一策略ID).filter(Boolean));
      const nonGfReduceStrategyIds = new Set(nonGfList.filter((row) => (num(row.权重变化) || 0) < -0.0001).map((row) => row.统一策略ID).filter(Boolean));
      out.持仓策略数 = strategyIds.size;
      out.广发策略持仓数 = gfStrategyIds.size;
      out.非广发策略持仓数 = nonGfStrategyIds.size;
      out.增持策略数 = addStrategyIds.size;
      out.减持策略数 = reduceStrategyIds.size;
      out.非广发增持策略数 = nonGfAddStrategyIds.size;
      out.非广发减持策略数 = nonGfReduceStrategyIds.size;
      out.总权重 = sum(list, "期末持仓比例");
      out.广发策略权重 = sum(list.filter(isGf), "期末持仓比例");
      out.非广发策略权重 = sum(nonGfList, "期末持仓比例");
      out.广发产品权重 = sum(list.filter((row) => row.是否广发基金 === "是"), "期末持仓比例");
      out.广发基金产品 = list.some((row) => row.是否广发基金 === "是") ? "是" : "否";
      out.中位权重 = median(list.map((row) => row.期末持仓比例));
      out.非广发净增配中位数 = median(nonGfList.map((row) => row.权重变化));
      out.区间收益率 = median(list.map((row) => row.区间收益率));
      return out;
    }).sort((a, b) => b.总权重 - a.总权重);
  }

  function rollupHolding(rows, keyFields) {
    return [...groupBy(rows, (row) => keyFields.map((field) => row[field] || "").join("｜")).entries()].map(([, list]) => {
      const base = list[0] || {};
      const out = { ...base };
      out.持仓策略数 = sum(list, "持仓策略数");
      out.广发策略持仓数 = sum(list, "广发策略持仓数");
      out.非广发策略持仓数 = sum(list, "非广发策略持仓数");
      out.非广发增持策略数 = sum(list, "非广发增持策略数");
      out.非广发减持策略数 = sum(list, "非广发减持策略数");
      out.总权重 = sum(list, "总权重");
      out.广发策略权重 = sum(list, "广发策略权重");
      out.非广发策略权重 = sum(list, "非广发策略权重");
      out.广发产品权重 = sum(list, "广发产品权重");
      out.中位权重 = median(list.map((row) => row.中位权重));
      out.非广发净增配中位数 = median(list.map((row) => row.非广发净增配中位数));
      return out;
    }).sort((a, b) => b.总权重 - a.总权重);
  }

  function addShare(rows) {
    const total = sum(rows, "总权重");
    const gfTotal = sum(rows, "广发策略权重");
    const nonGfTotal = sum(rows, "非广发策略权重");
    return rows.map((row) => ({
      ...row,
      权重占比: total ? row.总权重 / total * 100 : null,
      广发策略权重占比: gfTotal ? (row.广发策略权重 || 0) / gfTotal * 100 : null,
      非广发策略权重占比: nonGfTotal ? (row.非广发策略权重 || 0) / nonGfTotal * 100 : null,
      广发产品权重占比: total ? (row.广发产品权重 || 0) / total * 100 : null
    }));
  }

  function assetCompanyBreakdown(detailRows) {
    const allTotal = sum(detailRows, "期末持仓比例");
    return [...groupBy(detailRows, (row) => row.基金类型).entries()].map(([type, list]) => {
      const typeWeight = sum(list, "期末持仓比例");
      const typeStrategies = new Set(list.map((row) => row.统一策略ID).filter(Boolean));
      const companyRows = [...groupBy(list, (row) => row.基金公司 || "基金公司待补全").entries()].map(([company, companyList]) => {
        const companyWeight = sum(companyList, "期末持仓比例");
        const companyStrategies = new Set(companyList.map((row) => row.统一策略ID).filter(Boolean));
        return {
          基金公司: company,
          类型内占比: typeWeight ? companyWeight / typeWeight * 100 : null,
          全市场占比: allTotal ? companyWeight / allTotal * 100 : null,
          持仓策略数: companyStrategies.size,
          总权重: companyWeight
        };
      }).sort((a, b) => b.总权重 - a.总权重);
      return {
        基金类型: type || "混合型",
        类型权重占比: allTotal ? typeWeight / allTotal * 100 : null,
        持仓策略数: typeStrategies.size,
        公司明细: companyRows,
        总权重: typeWeight
      };
    }).sort((a, b) => b.总权重 - a.总权重);
  }

  function companyItemsForDisplay(items) {
    const list = items || [];
    const top = list.slice(0, 6);
    const gf = list.find((item) => /广发/.test(item.基金公司 || ""));
    return gf && !top.includes(gf) ? [...top, gf] : top;
  }

  function assetCompanyMatrixBlock(rows) {
    return tableBlock(["基金类型", "类型权重占比", "持仓策略数", "主要基金公司（类型内占比）"], rows, (row, h) => {
      if (h === "类型权重占比") return pct(row[h]);
      if (h === "持仓策略数") return countText(row[h]);
      if (h === "主要基金公司（类型内占比）") {
        return `<div class="asset-company-list">${companyItemsForDisplay(row.公司明细).map((item) => `<span class="asset-company-item ${/广发/.test(item.基金公司 || "") ? "is-gf" : ""}"><b>${B.esc(item.基金公司)}</b><em>${pct(item.类型内占比)}</em><small>全市场${pct(item.全市场占比)}</small></span>`).join("")}</div>`;
      }
      return B.fmt(row[h]);
    });
  }

  function timelineChart(rows) {
    const filtered = rangeFiltered((rows || []).filter(dimensionMatch), "月份", true);
    const typeRank = rollupHolding(filtered, ["基金类型"]);
    const visibleTypes = typeRank.slice(0, 6).map((row) => row.基金类型);
    const months = [...new Set(filtered.map((row) => row.月份))].sort().slice(-6);
    if (!months.length || !visibleTypes.length) return '<div class="empty">当前筛选下暂无持仓时间序列。</div>';
    const monthly = months.map((month) => {
      const list = filtered.filter((row) => row.月份 === month);
      const total = sum(list, "总权重");
      const byType = {};
      visibleTypes.forEach((type) => {
        const value = sum(list.filter((row) => row.基金类型 === type), "总权重");
        byType[type] = total ? value / total * 100 : 0;
      });
      return { month, byType };
    });
    const yMax = Math.min(100, Math.max(10, Math.ceil(Math.max(...monthly.flatMap((row) => visibleTypes.map((type) => row.byType[type] || 0))) / 10) * 10));
    const summaryRows = visibleTypes.map((type) => {
      const first = monthly[0]?.byType[type] || 0;
      const latest = monthly.at(-1)?.byType[type] || 0;
      return { 基金类型: type, 首月占比: first, 最新占比: latest, 区间变化: latest - first };
    }).sort((a, b) => b.最新占比 - a.最新占比);
    const cards = visibleTypes.map((type) => {
      const latest = monthly.at(-1)?.byType[type] || 0;
      const first = monthly[0]?.byType[type] || 0;
      const bars = monthly.map((row, index) => {
        const value = row.byType[type] || 0;
        const prev = index > 0 ? (monthly[index - 1]?.byType[type] || 0) : value;
        const mom = value - prev;
        const fromFirst = value - first;
        const height = yMax ? Math.max(3, value / yMax * 100) : 0;
        const tone = index === 0 ? "is-base" : (mom > 0.05 ? "is-up" : (mom < -0.05 ? "is-down" : "is-flat"));
        return `<div class="asset-month-bar ${tone}" title="${B.esc(row.month)}｜${B.esc(type)}｜占比${pct(value)}｜环比${weightPoint(mom)}｜较首月${weightPoint(fromFirst)}">
          <i style="height:${height.toFixed(1)}%"></i>
          <span>${B.esc(row.month.slice(5))}</span>
          <em class="asset-month-tip">${B.esc(row.month)}<br>${B.esc(type)}<br>占比 ${pct(value)}<br>环比 ${weightPoint(mom)}<br>较首月 ${weightPoint(fromFirst)}</em>
        </div>`;
      }).join("");
      return `<div class="asset-trend-card">
        <div class="asset-trend-head"><strong>${B.esc(type)}</strong><span>${pct(latest)} / ${weightPoint(latest - first)}</span></div>
        <div class="asset-month-bars">${bars}</div>
      </div>`;
    }).join("");
    return `<div class="asset-trend-layout">
      <div>
        <div class="asset-trend-legend"><span><i style="background:#b42318"></i>环比上升</span><span><i style="background:#0f766e"></i>环比下降</span><span><i style="background:#8aa0b6"></i>持平/首月</span></div>
        <div class="asset-trend-grid">${cards}</div>
      </div>
      ${tableBlock(["基金类型", "首月占比", "最新占比", "区间变化"], summaryRows, (row, h) => h.includes("占比") ? pct(row[h]) : (h === "区间变化" ? weightPoint(row[h]) : B.fmt(row[h])))}
    </div>`;
  }

  function gfFundHoldingOpportunityRows(gfFundRows) {
    const rebalanceMap = new Map(rollupMonthlyFunds(filteredMonthlyFundRows(), true).map((row) => [`${row.基金代码}｜${row.基金名称}`, row]));
    return gfFundRows.map((row) => {
      const rebalance = rebalanceMap.get(`${row.基金代码}｜${row.基金名称}`) || {};
      return {
        ...row,
        区间净增配: rebalance.净增配 ?? null,
        区间加仓权重: rebalance.加仓权重 ?? null,
        区间减仓权重: rebalance.减仓权重 ?? null,
        调仓策略数: rebalance.调仓策略数 ?? 0
      };
    }).sort((a, b) => (num(b.非广发策略权重占比) || 0) - (num(a.非广发策略权重占比) || 0) || (num(b.非广发净增配中位数) || 0) - (num(a.非广发净增配中位数) || 0) || (num(b.权重占比) || 0) - (num(a.权重占比) || 0));
  }

  function fundStrategyDetailRows(fundRow) {
    const key = holdingKey(fundRow);
    return filteredHoldingStrategyRows()
      .filter((row) => holdingKey(row) === key)
      .sort((a, b) => (num(b.期末持仓比例) || 0) - (num(a.期末持仓比例) || 0));
  }

  function fundStrategyDetailTable(fundRow) {
    const rows = fundStrategyDetailRows(fundRow);
    const headers = ["策略期名称", "所属公司", "类型", `${rangeLabel()}收益率`, "初持仓比例", "期末持仓比例"];
    const body = rows.length ? rows.map((row) => `<tr>
      <td>${strategyLink(row)}</td>
      <td>${B.fmt(row.投顾机构)}</td>
      <td>${B.fmt(row.业务分类)}</td>
      <td>${signedPct(row[returnMetric()])}</td>
      <td>${pct(row.初持仓比例)}</td>
      <td>${pct(row.期末持仓比例)}</td>
    </tr>`).join("") : `<tr><td colspan="${headers.length}"><div class="empty">当前筛选下暂无持有策略明细。</div></td></tr>`;
    return `<div class="fund-detail-block">
      <div class="fund-detail-title">${fundLink(fundRow)}｜按期末持仓比例排序</div>
      <div class="table-wrap insight-table"><table><thead><tr>${headers.map((h) => `<th>${B.label(h)}</th>`).join("")}</tr></thead><tbody>${body}</tbody></table></div>
    </div>`;
  }

  function fundHoldingTable(rows) {
    const headers = ["基金名称", "基金类型", "资产暴露", "基金分类置信度", "区间收益率", "权重占比", "中位权重", "增持策略数", "减持策略数", "持仓策略数"];
    const body = rows.length ? rows.slice(0, 10).map((row) => {
      const key = holdingKey(row);
      const expanded = state.expandedFundKey === key;
      const main = `<tr class="fund-row ${expanded ? "is-open" : ""}" data-fund-key="${B.esc(key)}">
        <td>${fundNameCell(row, key)}</td>
        <td>${B.fmt(row.基金类型)}</td>
        <td>${B.fmt(row.资产暴露)}</td>
        <td>${B.fmt(row.基金分类置信度)}</td>
        <td>${signedPct(row.区间收益率)}</td>
        <td>${pct(row.权重占比)}</td>
        <td>${pct(row.中位权重)}</td>
        <td>${countText(row.增持策略数)}</td>
        <td>${countText(row.减持策略数)}</td>
        <td>${countText(row.持仓策略数)}</td>
      </tr>`;
      const detail = expanded ? `<tr class="insight-secondary-row"><td colspan="${headers.length}">${fundStrategyDetailTable(row)}</td></tr>` : "";
      return `${main}${detail}`;
    }).join("") : `<tr><td colspan="${headers.length}"><div class="empty">暂无数据</div></td></tr>`;
    return `<div class="table-wrap insight-table fund-holding-table"><table><thead><tr>${headers.map((h) => `<th>${B.label(h)}</th>`).join("")}</tr></thead><tbody>${body}</tbody></table></div>`;
  }

  function holdingConclusionCards(assetRows, companyRows, gfFundRows) {
    const topAsset = assetRows[0];
    const topCompany = companyRows[0];
    const externalGfFunds = gfFundRows.filter((row) => (num(row.非广发策略权重) || 0) > 0).sort((a, b) => (num(b.非广发策略权重占比) || 0) - (num(a.非广发策略权重占比) || 0));
    const topGfFund = externalGfFunds[0] || gfFundRows[0];
    const gfShare = sum(gfFundRows, "权重占比");
    const externalGfShare = sum(gfFundRows, "非广发策略权重占比");
    const internalGfShare = sum(gfFundRows, "广发策略权重占比");
    const cards = [];
    if (topAsset) cards.push({
      title: "市场主配资产",
      value: topAsset.基金类型 || "未分类",
      body: `${topAsset.基金类型 || "未分类"}占当前策略仓位${pct(topAsset.权重占比)}，中位权重${pct(topAsset.中位权重)}；后续看该资产下由哪些基金公司贡献。`
    });
    if (topCompany) cards.push({
      title: "头部基金公司",
      value: topCompany.基金公司 || "未识别",
      body: `${topCompany.基金公司 || "未识别"}在当前持仓中权重占比${pct(topCompany.权重占比)}，持仓策略数${countText(topCompany.持仓策略数)}；用于识别全市场投顾底层基金偏好。`
    });
    cards.push({
      title: "广发基金总渗透",
      value: pct(gfShare),
      body: `广发基金产品在当前筛选持仓中的合计权重占比为${pct(gfShare)}，被持有产品${countText(gfFundRows.length)}只；其中广发策略自身配置占广发策略仓位${pct(internalGfShare)}。`
    });
    cards.push({
      title: "外部策略验证",
      value: pct(externalGfShare),
      body: `非广发策略仓位中配置广发基金的占比为${pct(externalGfShare)}，涉及广发基金${countText(externalGfFunds.length)}只；这才更接近外部营销机会和产品认可度。`
    });
    if (topGfFund) cards.push({
      title: "广发代表基金",
      value: topGfFund.基金名称 || "未命名",
      body: `${topGfFund.基金名称 || "未命名"}外部策略权重占比${pct(topGfFund.非广发策略权重占比)}，非广发持仓策略数${countText(topGfFund.非广发策略持仓数)}；点击下方表格可看由哪些策略持有。`
    });
    return cards;
  }

  function gfFundOpportunityTable(rows) {
    const headers = ["基金名称", "基金类型", "资产暴露", "基金分类置信度", "区间收益率", "外部策略权重占比", "全策略权重占比", "中位权重", "外部持仓策略数", "外部增减策略数", "外部净增配中位数"];
    const body = rows.length ? rows.slice(0, 10).map((row) => {
      const key = holdingKey(row);
      const expanded = state.expandedFundKey === key;
      const main = `<tr class="fund-row ${expanded ? "is-open" : ""}" data-fund-key="${B.esc(key)}">${headers.map((h) => {
        if (h === "基金名称") return `<td>${fundNameCell(row, key)}</td>`;
        if (h === "区间收益率") return `<td>${signedPct(row[h])}</td>`;
        if (h === "外部策略权重占比") return `<td>${pct(row.非广发策略权重占比)}</td>`;
        if (h === "全策略权重占比") return `<td>${pct(row.权重占比)}</td>`;
        if (h === "外部净增配中位数") return `<td>${weightPoint(row.非广发净增配中位数)}</td>`;
        if (h === "外部增减策略数") return `<td>${countText(row.非广发增持策略数)}增 / ${countText(row.非广发减持策略数)}减</td>`;
        if (h.includes("权重")) return `<td>${pct(row[h])}</td>`;
        if (h === "外部持仓策略数") return `<td>${countText(row.非广发策略持仓数)}</td>`;
        if (h.includes("策略数")) return `<td>${countText(row[h])}</td>`;
        return `<td>${B.fmt(row[h])}</td>`;
      }).join("")}</tr>`;
      const detail = expanded ? `<tr class="insight-secondary-row"><td colspan="${headers.length}">${fundStrategyDetailTable(row)}</td></tr>` : "";
      return `${main}${detail}`;
    }).join("") : `<tr><td colspan="${headers.length}"><div class="empty">暂无数据</div></td></tr>`;
    return `<div class="table-wrap insight-table fund-holding-table"><table><thead><tr>${headers.map((h) => `<th>${B.label(h)}</th>`).join("")}</tr></thead><tbody>${body}</tbody></table></div>`;
  }

  function holdingTab() {
    const holdingDetails = filteredHoldingStrategyRows();
    const detailRows = filteredHoldingRows();
    const assetRows = addShare(holdingDetails.length ? rollupHoldingDetails(holdingDetails, ["基金类型"]) : rollupHolding(detailRows, ["基金类型"]));
    const fundRows = addShare(holdingDetails.length ? rollupHoldingDetails(holdingDetails, ["基金代码", "基金名称"]) : rollupHolding(detailRows, ["基金代码", "基金名称"]));
    const companyRows = addShare(holdingDetails.length ? rollupHoldingDetails(holdingDetails, ["基金公司"]) : rollupHolding(filteredCompanyRows(), ["基金公司"]));
    const assetCompanyRows = assetCompanyBreakdown(holdingDetails);
    const gfFundRows = fundRows.filter((row) => row.广发基金产品 === "是");
    const gfOpportunityRows = gfFundHoldingOpportunityRows(gfFundRows);
    const externalGfShare = sum(gfFundRows, "非广发策略权重占比");
    const internalGfShare = sum(gfFundRows, "广发策略权重占比");
    const topAsset = assetRows[0]?.基金类型 || "未识别";
    return `
      <section class="insight-hero">
        ${kpi("持仓基金样本", countText(fundRows.length), "当前仓位聚合")}
        ${kpi("基金公司数", countText(companyRows.length), "当前仓位聚合")}
        ${kpi("主持仓类型", B.esc(topAsset), "按权重占比排序")}
        ${kpi("广发基金产品数", countText(gfFundRows.length), "被全市场策略持有")}
        ${kpi("外部持有广发基金", pct(externalGfShare), "非广发策略仓位口径")}
        ${kpi("广发自持广发基金", pct(internalGfShare), "广发策略仓位口径")}
        ${kpi("时间区间", B.esc(rangeLabel()), "仓位时间序列和调仓共用")}
      </section>
      <section class="panel" id="holding-conclusion">
        <div class="panel-head"><div><h2>仓位经营结论</h2><p class="desc">先回答全市场策略正在配置什么、哪些基金公司受益、广发基金有没有被外部策略验证。</p></div></div>
        ${rebalanceConclusionList(holdingConclusionCards(assetRows, companyRows, gfFundRows))}
      </section>
      <section class="panel chart-panel">
        <div class="panel-head"><div><h2>资产分布时间变化</h2><p class="desc">最近6个月按基金类型拆成独立柱状图；每个月在当前筛选策略集合内按持仓总权重归一，先看资产偏好变化，再看基金公司和具体基金。</p></div></div>
        ${timelineChart(insight.持仓时间序列 || [])}
      </section>
      <section class="insight-grid">
        <div class="panel">
          <div class="panel-head"><div><h2>持仓基金类型占比</h2><p class="desc">筛选范围内全部策略的当前持仓权重按基金类型归一，回答“全市场策略仓位主要配在哪些资产”。</p></div></div>
          ${barList(assetRows, "基金类型", "权重占比", { formatter: (value, row) => `权重占比${pct(value)} / 中位权重${pct(row.中位权重)}` })}
        </div>
        <div class="panel">
          <div class="panel-head"><div><h2>基金公司产品占比</h2><p class="desc">筛选范围内全部策略持仓按基金公司归一，回答“哪些基金公司的产品在投顾仓位中占比更高”。</p></div></div>
          ${barList(companyRows.slice(0, 12), "基金公司", "权重占比", { formatter: (value, row) => `权重占比${pct(value)} / 中位权重${pct(row.中位权重)}` })}
        </div>
      </section>
      <section class="panel" id="gf-fund-opportunity">
        <div class="panel-head"><div><h2>广发基金机会</h2><p class="desc">只看底层基金公司为广发基金的产品，优先观察是否被非广发策略持有或在区间内被增配。</p></div></div>
        <div class="source-method"><strong>读法</strong> 默认按外部策略权重占比排序。外部策略权重占比表示非广发投顾策略仓位中有多少配置到该广发基金；外部净增配中位数只看非广发策略对该基金的单策略权重变化中位数，避免用跨策略合计点位制造夸大结论。</div>
        ${gfFundOpportunityTable(gfOpportunityRows)}
      </section>
      <details class="fold-block">
        <summary>验证区：类型下基金公司、全市场高频基金和广发基金持有策略</summary>
        <section class="panel">
          <div class="panel-head"><div><h2>类型下基金公司占比</h2><p class="desc">每个基金类型内部再拆到基金公司，既看该类型在全市场仓位中的占比，也看类型内部由哪些基金公司贡献。</p></div></div>
          ${assetCompanyMatrixBlock(assetCompanyRows)}
        </section>
        <section class="panel">
          <div class="panel-head"><div><h2>全市场高频持仓基金</h2><p class="desc">按当前筛选范围内的持仓权重占比排序；点击基金行查看持有该基金的策略明细。</p></div></div>
          ${fundHoldingTable(fundRows)}
        </section>
        <section class="panel">
          <div class="panel-head"><div><h2>广发基金高频持仓基金</h2><p class="desc">只看基金公司为广发基金的底层产品，便于识别已被全市场策略高频配置的广发基金。</p></div></div>
          ${fundHoldingTable(gfFundRows)}
        </section>
      </details>`;
  }

  function winValue(row) {
    const text = `${row.胜负 || ""} ${row.结果评价 || ""}`;
    if (/胜|赢|正/.test(text)) return 1;
    if (/负|输|亏|差/.test(text)) return 0;
    return null;
  }

  function rebalanceEvents(applyReportType = true) {
    const rows = (insight.调仓事件 || [])
      .filter(dimensionMatch)
      .filter((row) => !applyReportType || reportTypeMatch(row));
    return rebalanceRangeRows(rows, "调仓日期").sort((a, b) => String(b.调仓日期 || "").localeCompare(String(a.调仓日期 || "")));
  }

  function filteredRebalanceMonthlyFundRows(applyReportType = true) {
    const rows = (insight.调仓基金月度汇总 || [])
      .filter(dimensionMatch)
      .filter((row) => !applyReportType || reportTypeMatch(row));
    return rebalanceRangeRows(rows, "月份", true);
  }

  function institutionStats(events) {
    return [...groupBy(events, (row) => row.投顾机构).entries()].map(([name, list]) => {
      const evaluated = list.map(winValue).filter((value) => value !== null);
      const wins = evaluated.filter(Boolean).length;
      return {
        投顾机构: name,
        事件数: list.length,
        可评价事件数: evaluated.length,
        调仓胜率: evaluated.length ? wins / evaluated.length * 100 : null,
        平均调仓超额: avg(list.map((row) => row.调仓超额 ?? row.方向性超额)),
        平均单次换手率: avg(list.map((row) => row.单次换手率))
      };
    }).sort((a, b) => (num(b.可评价事件数) || 0) - (num(a.可评价事件数) || 0) || b.事件数 - a.事件数);
  }

  function monthlyEventChart(events) {
    const rows = [...groupBy(events, (row) => String(row.调仓日期 || "").slice(0, 7)).entries()].map(([month, list]) => ({ 月份: month, 事件数: list.length })).sort((a, b) => a.月份.localeCompare(b.月份)).slice(-12);
    return barList(rows, "月份", "事件数", { formatter: (value) => countText(value), limit: 12 });
  }

  function directionSignalRows(rows, field) {
    return [...groupBy(rows || [], (row) => row[field] || "未分类").entries()].map(([name, list]) => {
      const addCount = sum(list, "加仓次数") + sum(list, "买入次数");
      const reduceCount = sum(list, "减仓次数") + sum(list, "卖出次数");
      const actionCount = addCount + reduceCount;
      const addRatio = actionCount ? addCount / actionCount * 100 : null;
      const net = sum(list, "净增配");
      const addWeight = sum(list, "加仓权重");
      const reduceWeight = sum(list, "减仓权重");
      const strategyCount = sum(list, "调仓策略数");
      const medianNet = median(list.map((row) => num(row.中位净增配) ?? num(row.净增配)).filter((value) => value !== null));
      const agreement = addRatio === null ? null : Math.abs(addRatio - 50) * 2;
      const strength = Math.abs(net) * (1 + Math.log1p(strategyCount || 0)) * (.65 + (agreement || 0) / 200);
      return {
        分类: name,
        方向: net > .05 ? "净增配" : (net < -.05 ? "净减配" : "结构轮动"),
        增持次数: addCount,
        减持次数: reduceCount,
        增持占比: addRatio,
        净增配: net,
        中位净增配: medianNet,
        加仓权重: addWeight,
        减仓权重: reduceWeight,
        调仓策略数: strategyCount,
        调仓强度: addWeight + reduceWeight,
        共识度: agreement,
        信号强度: strength
      };
    }).sort((a, b) => b.信号强度 - a.信号强度);
  }

  function strategyAssetSignalRows(rows, field = "基金类型", options = {}) {
    const detailFieldSupported = ["研报大类资产", "权益行业主题", "研报A股行业"].includes(field);
    const detailRows = detailFieldSupported
      ? filteredRebalanceFundCategoryRows(true)
        .filter((row) => row.分类字段 === field)
        .filter((row) => !options.onlyReportAIndustry || isReportAIndustryTheme(row.分类))
      : [];
    const detailMap = groupBy(detailRows, (row) => `${row.统一策略ID || ""}｜${row.分类 || ""}`);
    const normalizedRows = (rows || []).map((row) => ({
      ...row,
      _signalCategory: field === "研报大类资产" ? reliableReportAsset(row) : (options.requireField ? raw(row[field]) : raw(row[field] || row.基金类型 || "未分类"))
    })).filter((row) => {
      const value = row._signalCategory;
      return value && value !== "待核验";
    }).filter((row) => {
      if (!options.onlyReportAIndustry) return true;
      return isReportAIndustryTheme(row._signalCategory);
    });
    const strategyAssetRows = [...groupBy(normalizedRows, (row) => `${row.统一策略ID || ""}｜${row._signalCategory}`).entries()]
      .map(([, list]) => {
        const base = list[0] || {};
        const net = sum(list, "净增配");
        const before = sum(list, "调前权重");
        const after = sum(list, "调后权重");
        const strength = sum(list, "总点位");
        const fundDetails = detailMap.get(`${base.统一策略ID || ""}｜${base._signalCategory || ""}`) || [];
        return {
          统一策略ID: base.统一策略ID,
          策略名称: base.策略名称,
          投顾机构: base.投顾机构,
          分类: base._signalCategory,
          净变化: net,
          调仓强度: strength,
          调前权重: before,
          调后权重: after,
          基金明细: fundDetails,
          命中原因: fundAdjustmentSummary(fundDetails)
        };
      })
      .filter((row) => Math.abs(num(row.净变化) || 0) > .0001 || (num(row.调仓强度) || 0) > .0001);
    return [...groupBy(strategyAssetRows, (row) => row.分类).entries()].map(([name, list]) => {
      const add = list.filter((row) => (num(row.净变化) || 0) > .0001);
      const reduce = list.filter((row) => (num(row.净变化) || 0) < -.0001);
      const active = add.length + reduce.length;
      const addRatio = active ? add.length / active * 100 : null;
      const medianChange = median(list.map((row) => row.净变化));
      const net = sum(list, "净变化");
      const strength = sum(list, "调仓强度");
      let judgment = "方向分歧";
      if (active > 0 && addRatio >= 60 && (num(medianChange) || 0) > .2) judgment = active < 8 ? "低覆盖增配" : "多数策略增配";
      else if (addRatio <= 40 && (num(medianChange) || 0) < -.2) judgment = active < 8 ? "低覆盖减配" : "多数策略减配";
      else if (Math.abs(num(medianChange) || 0) < .2) judgment = "方向分歧";
      else if (net > 0 && (num(medianChange) || 0) > .2) judgment = "温和增配";
      else if (net < 0 && (num(medianChange) || 0) < -.2) judgment = "温和减配";
      const clearSignal = /增配|减配/.test(judgment) && judgment !== "方向分歧";
      const score = (clearSignal ? 100 : 0) + Math.abs(num(medianChange) || 0) * 10 + Math.abs((addRatio ?? 50) - 50) + Math.log1p(active) * 4;
      return {
        分类: name,
        判断: judgment,
        参与策略数: list.length,
        增持策略数: add.length,
        减持策略数: reduce.length,
        增持策略占比: addRatio,
        减持策略占比: active ? reduce.length / active * 100 : null,
        增持中位净变化: median(add.map((row) => row.净变化)),
        减持中位净变化: median(reduce.map((row) => row.净变化)),
        典型变化: medianChange,
        中位净变化: medianChange,
        净变化: net,
        调仓强度: strength,
        信号评分: score,
        解释: `${countText(add.length)}个策略增配、${countText(reduce.length)}个策略减配；中位变化${weightPoint(medianChange)}。`,
        _策略明细: list.sort((a, b) => Math.abs(num(b.净变化) || 0) - Math.abs(num(a.净变化) || 0))
      };
    }).sort((a, b) => {
      const absChange = Math.abs(num(b.中位净变化 ?? b.典型变化) || 0) - Math.abs(num(a.中位净变化 ?? a.典型变化) || 0);
      if (Math.abs(absChange) > .0001) return absChange;
      const absNet = Math.abs(num(b.净变化) || 0) - Math.abs(num(a.净变化) || 0);
      if (Math.abs(absNet) > .0001) return absNet;
      return b.参与策略数 - a.参与策略数;
    });
  }

  function directionTone(judgment) {
    if (/增配/.test(raw(judgment))) return "action-attack";
    if (/减配/.test(raw(judgment))) return "action-watch";
    return "action-hold";
  }

  function isClearDirection(row) {
    return !!row && /增配|减配/.test(raw(row.判断)) && !/方向分歧/.test(raw(row.判断));
  }

  function rebalanceEffectRows(events, field) {
    return [...groupBy(events || [], (row) => row[field] || "未分类").entries()].map(([name, list]) => {
      const evaluated = list.map(winValue).filter((value) => value !== null);
      const wins = evaluated.filter(Boolean).length;
      const winRate = evaluated.length ? wins / evaluated.length * 100 : null;
      const avgExtra = avg(list.map((row) => row.调仓超额 ?? row.方向性超额));
      const avgTurnover = avg(list.map((row) => row.单次换手率));
      const score = evaluated.length
        ? (winRate - 50) + (avgExtra || 0) * 2 - Math.max(0, (avgTurnover || 0) - 25) * .12 + Math.log1p(evaluated.length) * 2
        : null;
      return {
        分类: name,
        事件数: list.length,
        可评价事件数: evaluated.length,
        胜率: winRate,
        平均调仓超额: avgExtra,
        平均单次换手率: avgTurnover,
        有效性评分: score,
        样本判断: evaluated.length ? `已评价${countText(evaluated.length)}个` : "效果待观察"
      };
    }).sort((a, b) => (num(b.有效性评分) ?? -999) - (num(a.有效性评分) ?? -999) || b.事件数 - a.事件数);
  }

  function institutionBehaviorRows(events, advisorAssetRows) {
    const assetGroups = groupBy((advisorAssetRows || []).filter((row) => row.投顾机构 && row.投顾机构 !== "全市场汇总"), (row) => row.投顾机构);
    return institutionStats(events).map((row) => {
      const assets = assetGroups.get(row.投顾机构) || [];
      const adds = assets.filter((item) => (num(item.净增配) || 0) > .0001).sort((a, b) => (num(b.净增配) || 0) - (num(a.净增配) || 0));
      const reduces = assets.filter((item) => (num(item.净增配) || 0) < -.0001).sort((a, b) => (num(a.净增配) || 0) - (num(b.净增配) || 0));
      const totalAbs = assets.reduce((total, item) => total + Math.abs(num(item.净增配) || 0), 0);
      const maxAbs = Math.max(0, ...assets.map((item) => Math.abs(num(item.净增配) || 0)));
      return {
        ...row,
        主加仓资产: adds[0] ? `${adds[0].资产类型} ${weightPoint(adds[0].净增配)}` : "无明显加仓",
        主减仓资产: reduces[0] ? `${reduces[0].资产类型} ${weightPoint(reduces[0].净增配)}` : "无明显减仓",
        方向集中度: totalAbs ? maxAbs / totalAbs * 100 : null,
        调仓资产数: assets.length
      };
    }).sort((a, b) => (num(b.可评价事件数) || 0) - (num(a.可评价事件数) || 0) || (num(b.事件数) || 0) - (num(a.事件数) || 0));
  }

  function gfRebalanceOpportunityRows(monthlyFunds) {
    return rollupMonthlyFunds(monthlyFunds, true).map((row) => {
      const nonGfNet = num(row.非广发策略净增配) || 0;
      const gfNet = num(row.广发策略净增配) || 0;
      const totalNet = num(row.净增配) || 0;
      const strategyCount = num(row.调仓策略数) || 0;
      let type = "低变化观察";
      if (nonGfNet > .05) type = "外部增配验证";
      else if (nonGfNet < -.05) type = "外部减配预警";
      else if (gfNet > .05) type = "内部配置为主";
      return {
        ...row,
        机会类型: type,
        非广发策略净增配: nonGfNet,
        广发策略净增配: gfNet,
        机会评分: nonGfNet * 3 + Math.max(0, totalNet) + Math.log1p(strategyCount) * 3 - Math.max(0, -nonGfNet) * 2
      };
    }).sort((a, b) => b.机会评分 - a.机会评分 || (num(b.调仓策略数) || 0) - (num(a.调仓策略数) || 0));
  }

  function reportTypeOverviewRows(events, assetRows) {
    const products = strategyRows().filter((row) => row.研报产品类型);
    const types = [...new Set([
      ...reportTypes,
      ...products.map((row) => row.研报产品类型).filter(Boolean),
      ...(events || []).map((row) => row.研报产品类型).filter(Boolean),
      ...(assetRows || []).map((row) => row.研报产品类型).filter(Boolean)
    ])].sort((a, b) => reportTypeRank(a) - reportTypeRank(b) || a.localeCompare(b, "zh-CN"));
    return types.map((type) => {
      const productRows = products.filter((row) => row.研报产品类型 === type);
      const typeEvents = (events || []).filter((row) => row.研报产品类型 === type);
      const typeAssets = (assetRows || []).filter((row) => row.研报产品类型 === type);
      const activeIds = new Set([
        ...typeEvents.map((row) => row.统一策略ID).filter(Boolean),
        ...typeAssets.map((row) => row.统一策略ID).filter(Boolean)
      ]);
      const evaluated = typeEvents.map(winValue).filter((value) => value !== null);
      const wins = evaluated.filter(Boolean).length;
      const signals = strategyAssetSignalRows(typeAssets, "研报大类资产");
      const signal = signals.find(isClearDirection);
      const coverage = productRows.length ? activeIds.size / productRows.length * 100 : null;
      const sampleText = activeIds.size < 8 ? "低覆盖，仅做明细复盘" : (signal ? "方向相对清晰，可进入深钻" : "方向分歧，谨慎解读");
      return {
        研报产品类型: type,
        产品数: productRows.length,
        调仓产品数: activeIds.size,
        调仓覆盖率: coverage,
        中位换手: median(typeEvents.map((row) => num(row.单次换手率)).filter((value) => value !== null)),
        可评价事件数: evaluated.length,
        胜率: evaluated.length ? wins / evaluated.length * 100 : null,
        调仓超额: avg(typeEvents.map((row) => row.调仓超额 ?? row.方向性超额)),
        主资产方向: signal ? `${signal.分类} ${signal.判断}` : "未形成强方向",
        主资产典型变化: signal ? signal.典型变化 : null,
        业务读法: sampleText,
        事件数: typeEvents.length
      };
    }).filter((row) => row.产品数 || row.事件数 || row.调仓产品数);
  }

  function ensureReportTypeSelection(overviewRows) {
    const available = (overviewRows || []).map((row) => row.研报产品类型).filter(Boolean);
    if (!available.length) {
      state.reportType = "";
      return "";
    }
    if (!state.reportType || !available.includes(state.reportType)) {
      const active = (overviewRows || [])
        .filter((row) => (num(row.调仓产品数) || 0) > 0)
        .sort((a, b) => {
          const ac = raw(a.主资产方向) !== "未形成强方向" ? 1 : 0;
          const bc = raw(b.主资产方向) !== "未形成强方向" ? 1 : 0;
          return bc - ac || (num(b.调仓产品数) || 0) - (num(a.调仓产品数) || 0) || reportTypeRank(a.研报产品类型) - reportTypeRank(b.研报产品类型);
        })[0];
      state.reportType = active?.研报产品类型 || available[0];
    }
    return state.reportType;
  }

  function rebalanceTypeControls(overviewRows) {
    const months = rebalanceMonths();
    const month = currentRebalanceMonth();
    const availableTypes = (overviewRows || []).map((row) => row.研报产品类型).filter(Boolean);
    return `<section class="panel">
      <div class="panel-head"><div><h2>调仓分析口径</h2><p class="desc">默认按研报月报逻辑，先选可比产品类型，再看基金调仓、大类资产变化和行业变化；业务分类仍作为顶部筛选条件。</p></div></div>
      <div class="insight-filters">
        ${filterField("调仓时间模式", `<select id="rebalanceMode" class="control">
          <option value="month" ${state.rebalanceMode === "month" ? "selected" : ""}>月度报告模式</option>
          <option value="range" ${state.rebalanceMode === "range" ? "selected" : ""}>自选区间模式</option>
        </select>`)}
        ${filterField("报告月份", `<select id="rebalanceMonth" class="control" ${state.rebalanceMode !== "month" ? "disabled" : ""}>${months.map((item) => `<option value="${item}" ${item === month ? "selected" : ""}>${B.esc(item)}</option>`).join("") || '<option value="">暂无月份</option>'}</select>`)}
        ${filterField("研报产品类型", `<select id="rebalanceReportType" class="control">${availableTypes.map((type) => `<option value="${B.esc(type)}" ${type === state.reportType ? "selected" : ""}>${B.esc(type)}</option>`).join("") || '<option value="">暂无类型</option>'}</select>`)}
      </div>
      <div class="source-method"><strong>${B.label("调仓时间模式")}</strong> ${state.rebalanceMode === "month" ? `当前复盘 ${B.esc(month || "暂无月份")}；顶部时间区间不影响月度模式。` : `当前使用顶部“${B.esc(rangeLabel())}”时间区间过滤调仓事件、基金月度汇总和策略级资产变化。`} 类型深钻只比较同一研报产品类型，避免把纯债、固收+和股票型策略混在一起得出伪结论。</div>
    </section>`;
  }

  function reportTypeOverviewTable(rows) {
    return tableBlock(["研报产品类型", "产品数", "调仓产品数", "调仓覆盖率", "中位换手", "效果评价覆盖", "调仓超额", "主资产方向", "业务读法"], rows, (row, h) => {
      if (h === "研报产品类型") return `<button type="button" class="link" data-report-type-select="${B.esc(row.研报产品类型)}">${B.esc(row.研报产品类型)}</button>`;
      if (h.includes("数")) return countText(row[h]);
      if (h === "调仓覆盖率" || h === "中位换手") return pct(row[h]);
      if (h === "效果评价覆盖") {
        const n = num(row.可评价事件数) || 0;
        return n ? `${effectPct(row.胜率)}｜${countText(n)}个` : "待观察";
      }
      if (h === "调仓超额") return signedPct(row[h]);
      if (h === "主资产方向") return `<span class="small">${B.esc(row.主资产方向)}${num(row.主资产典型变化) !== null ? `，中位净变化${weightPoint(row.主资产典型变化)}` : ""}</span>`;
      return B.fmt(row[h]);
    });
  }

  function signalDirectionChart(rows, label = "研报大类资产", limit = 10) {
    const source = rows || [];
    const data = [...source]
      .filter((row) => Number.isFinite(num(row.中位净变化 ?? row.典型变化)))
      .slice(0, limit || source.length);
    if (!data.length) return '<div class="empty">当前筛选下暂无可绘制的调仓方向。</div>';
    const maxAbs = Math.max(.5, ...data.map((row) => Math.abs(num(row.中位净变化 ?? row.典型变化) || 0)));
    const axisTicks = [-maxAbs, -maxAbs / 2, 0, maxAbs / 2, maxAbs];
    return `<div class="signal-direction-chart" role="img" aria-label="${B.esc(label)}调仓方向柱状图">
      <div class="signal-chart-axis"><span>${B.esc(label)}</span><span>横轴：仓位变化，0为不变</span></div>
      <div class="signal-x-axis">
        <div></div>
        <div class="signal-x-track">
          <i class="signal-zero"></i>
          ${axisTicks.map((tick, index) => `<span style="left:${(index * 25).toFixed(2)}%">${weightPoint(tick)}</span>`).join("")}
        </div>
        <div></div>
      </div>
      ${data.map((row) => {
        const change = num(row.中位净变化 ?? row.典型变化) || 0;
        const width = Math.max(2, Math.min(50, Math.abs(change) / maxAbs * 50));
        const left = change >= 0 ? 50 : 50 - width;
        const addMedian = num(row.增持中位净变化);
        const reduceMedian = num(row.减持中位净变化);
        const addLabel = addMedian === null ? "无" : weightPoint(addMedian);
        const reduceLabel = reduceMedian === null ? "无" : weightPoint(reduceMedian);
        const title = `${row.分类}｜中位净变化${weightPoint(change)}｜增配${countText(row.增持策略数)}个，增配中位${addLabel}｜减配${countText(row.减持策略数)}个，减配中位${reduceLabel}｜参与${countText(row.参与策略数)}个`;
        return `<div class="signal-chart-row" title="${B.esc(title)}">
          <div class="signal-chart-label">${B.esc(row.分类 || "未分类")}</div>
          <div class="signal-chart-track">
            <i class="signal-zero"></i>
            <i class="signal-bar ${change >= 0 ? "is-add" : "is-reduce"}" style="left:${left.toFixed(2)}%;width:${width.toFixed(2)}%"></i>
          </div>
          <div class="signal-chart-meta">
            <b>${weightPoint(change)}</b>
            <span>增${countText(row.增持策略数)}(${addLabel})｜减${countText(row.减持策略数)}(${reduceLabel})｜共${countText(row.参与策略数)}</span>
          </div>
        </div>`;
      }).join("")}
      <div class="source-method"><strong>读法</strong> 红色向右表示参与策略的中位净增配，绿色向左表示中位净减配；右侧括号为增配策略、减配策略各自的中位净变化。</div>
    </div>`;
  }

  function assetSignalTable(rows, label = "研报大类资产", limit = 8) {
    return tableBlock([label, "判断", "参与策略", "增/减策略", "中位净变化", "累计净变化", "策略调整摘要", "详情"], (rows || []).slice(0, limit), (row, h) => {
      if (h === label) return B.fmt(row.分类);
      if (h === "判断") return `<span class="insight-chip ${directionTone(row.判断)}">${B.esc(row.判断)}</span>`;
      if (h === "参与策略") return countText(row.参与策略数);
      if (h === "增/减策略") return `${countText(row.增持策略数)} / ${countText(row.减持策略数)}`;
      if (h === "中位净变化" || h === "累计净变化") return weightPoint(h === "中位净变化" ? (row.中位净变化 ?? row.典型变化) : row.净变化);
      if (h === "策略调整摘要") return `<span class="small">${B.esc(strategyAdjustmentSummary(row))}</span>`;
      if (h === "详情") return signalDetailButton(label, row);
      return B.fmt(row[h]);
    });
  }

  function rebalanceConclusions(assetRows, logicRows, institutionRows, gfRows) {
    const topAdd = assetRows.find((row) => /增配/.test(row.判断));
    const topReduce = assetRows.find((row) => /减配/.test(row.判断));
    const topSplit = assetRows.find((row) => row.判断 === "方向分歧") || assetRows[0];
    const bestLogic = logicRows.find((row) => (num(row.可评价事件数) || 0) >= 5) || logicRows[0];
    const bestInstitution = institutionRows.find((row) => (num(row.可评价事件数) || 0) >= 5) || institutionRows[0];
    const gfOpportunity = gfRows.find((row) => row.机会类型 === "外部增配验证") || gfRows[0];
    const items = [];
    if (topAdd) items.push({
      title: "多数策略增配",
      value: weightPoint(topAdd.中位净变化 ?? topAdd.典型变化),
      body: `${topAdd.分类}有${countText(topAdd.参与策略数)}个策略参与调仓，${countText(topAdd.增持策略数)}增、${countText(topAdd.减持策略数)}减，中位净变化${weightPoint(topAdd.中位净变化 ?? topAdd.典型变化)}。`
    });
    if (topReduce) items.push({
      title: "多数策略减配",
      value: weightPoint(topReduce.中位净变化 ?? topReduce.典型变化),
      body: `${topReduce.分类}有${countText(topReduce.参与策略数)}个策略参与调仓，${countText(topReduce.增持策略数)}增、${countText(topReduce.减持策略数)}减，中位净变化${weightPoint(topReduce.中位净变化 ?? topReduce.典型变化)}。`
    });
    if (!topAdd && !topReduce && topSplit) items.push({
      title: "方向不够集中",
      value: "分歧",
      body: `${topSplit.分类}参与调仓最多，但增减方向未形成多数共识；这类数据只适合做明细复盘，不适合提炼市场观点。`
    });
    if (bestLogic) items.push({
      title: "较有效调仓逻辑",
      value: effectPct(bestLogic.胜率),
      body: `${bestLogic.分类}已评价事件${countText(bestLogic.可评价事件数)}个，平均超额${bestLogic.平均调仓超额 === null || bestLogic.平均调仓超额 === undefined ? "待观察" : signedPctText(bestLogic.平均调仓超额)}，${bestLogic.样本判断}。`
    });
    if (bestInstitution) items.push({
      title: "机构行为线索",
      value: effectPct(bestInstitution.调仓胜率),
      body: `${bestInstitution.投顾机构}已评价事件${countText(bestInstitution.可评价事件数)}个，主加仓${bestInstitution.主加仓资产}，主减仓${bestInstitution.主减仓资产}。`
    });
    if (gfOpportunity) items.push({
      title: "广发产品机会",
      value: weightPoint(gfOpportunity.非广发策略净增配),
      body: `${gfOpportunity.基金名称}被识别为${gfOpportunity.机会类型}，调仓策略${countText(gfOpportunity.调仓策略数)}个，广发策略净增配${weightPoint(gfOpportunity.广发策略净增配)}。`
    });
    return items;
  }

  function rebalanceConclusionList(items) {
    if (!items.length) return '<div class="empty">当前筛选下调仓方向分散，请查看下方事件、资产变化和基金流向明细。</div>';
    return `<div class="rank-list">${items.map((item) => `<div class="rank-row">
      <div><strong>${B.esc(item.title)}</strong><span>${B.esc(item.body)}</span></div>
      <div class="rank-value" style="white-space:nowrap">${item.value}</div>
    </div>`).join("")}</div>`;
  }

  function rebalanceTab() {
    {
      const allEvents = rebalanceEvents(false);
      const allStrategyAssetRows = filteredStrategyAssetChangeRows(false);
      const overviewRows = reportTypeOverviewRows(allEvents, allStrategyAssetRows);
      const selectedType = ensureReportTypeSelection(overviewRows);
      const typeOverview = overviewRows.find((row) => row.研报产品类型 === selectedType) || {};
      const events = rebalanceEvents(true);
      const evaluated = events.map(winValue).filter((value) => value !== null);
      const monthlyFunds = filteredRebalanceMonthlyFundRows(true);
      const fundRows = rollupMonthlyFunds(monthlyFunds);
      const addRows = [...fundRows].filter((row) => row.净增配 > 0).sort((a, b) => b.净增配 - a.净增配);
      const reduceRows = [...fundRows].filter((row) => row.净增配 < 0).sort((a, b) => a.净增配 - b.净增配);
      const gfOpportunityRows = gfRebalanceOpportunityRows(monthlyFunds);
      const companyAssetRows = rollupCompanyAssetDirection(monthlyFunds);
      const companySummaryRows = companyDirectionSummary(companyAssetRows);
      const strategyAssetChangeRows = filteredStrategyAssetChangeRows(true);
      const assetSignalRows = strategyAssetSignalRows(strategyAssetChangeRows, "研报大类资产");
      const themeSignalRows = strategyAssetSignalRows(strategyAssetChangeRows, "权益行业主题", { requireField: true });
      const industrySignalRows = strategyAssetSignalRows(strategyAssetChangeRows, "研报A股行业", { onlyReportAIndustry: true });
      const advisorAssetRows = rollupAdvisorAssetDirection(strategyAssetChangeRows);
      const institutionRows = institutionBehaviorRows(events, advisorAssetRows);
      const logicEffectRows = rebalanceEffectRows(events, "调仓逻辑");
      const topDirection = assetSignalRows.find(isClearDirection);
      const gfExternalCount = gfOpportunityRows.filter((row) => row.机会类型 === "外部增配验证").length;
      const periodText = state.rebalanceMode === "month" ? `${currentRebalanceMonth() || "暂无月份"} 月报` : rangeLabel();
      const holdingSnapshotRows = filteredHoldingSnapshotRows();
      const totalPages = Math.max(1, Math.ceil(events.length / state.rebalancePageSize));
      if (state.rebalancePage > totalPages) state.rebalancePage = totalPages;
      const detailStart = (state.rebalancePage - 1) * state.rebalancePageSize;
      const detailRows = events.slice(detailStart, detailStart + state.rebalancePageSize);
      return `
        ${rebalanceTypeControls(overviewRows)}
        <section class="insight-hero">
          ${kpi("观察窗口", B.esc(periodText), state.rebalanceMode === "month" ? "最近完整调仓月份优先" : "使用顶部时间区间")}
          ${kpi("研报类型", B.esc(selectedType || "暂无类型"), "同类策略内比较")}
          ${kpi("同类产品数", countText(typeOverview.产品数 || 0), "当前全局筛选后")}
          ${kpi("调仓产品数", countText(typeOverview.调仓产品数 || 0), `覆盖率 ${pct(typeOverview.调仓覆盖率)}`)}
          ${kpi("主资产方向", topDirection ? B.esc(topDirection.分类) : "方向分歧", topDirection ? `${topDirection.判断}，中位净变化${weightPoint(topDirection.中位净变化 ?? topDirection.典型变化)}` : "当前同类策略增减方向不集中")}
          ${kpi("广发外部机会", countText(gfExternalCount), "非广发策略增配广发基金")}
        </section>
        <section class="panel" id="rebalance-overview">
          <div class="panel-head"><div><h2>类型总览矩阵</h2><p class="desc">先按研报产品类型分池，再看每类策略是否真的发生调仓、方向是否集中、效果是否可评价。</p></div></div>
          ${reportTypeOverviewTable(overviewRows)}
        </section>
        <section class="panel">
          <div class="panel-head"><div><h2>${B.esc(selectedType || "选中类型")}：大类资产调仓方向</h2><p class="desc">按策略级口径汇总：同一策略同一研报大类资产先合并区间变化，再统计增配策略数、减配策略数和中位变化。</p></div></div>
          ${signalDirectionChart(assetSignalRows, "研报大类资产", assetSignalRows.length)}
          ${assetSignalTable(assetSignalRows, "研报大类资产", assetSignalRows.length)}
        </section>
        <section class="panel">
          <div class="panel-head"><div><h2>${B.esc(selectedType || "选中类型")}：A股行业变化</h2><p class="desc">仅统计能从基金名称或主题明确识别行业的A股基金；宽基指数、主动权益和均衡混合不强行拆行业。</p></div></div>
          ${signalDirectionChart(industrySignalRows, "研报A股行业", industrySignalRows.length)}
          ${assetSignalTable(industrySignalRows, "研报A股行业", industrySignalRows.length)}
          <div class="source-method"><strong>${B.label("研报A股行业")}</strong> 行业图按基金A股暴露和可识别行业主题拆分；宽基指数、主动权益和均衡混合基金没有底层股票/基准行业权重时不强行拆行业。</div>
        </section>
        <section class="panel">
          <div class="panel-head"><div><h2>${B.esc(selectedType || "选中类型")}：权益主题变化</h2><p class="desc">作为研报行业变化的补充视角：宽基、主动权益、海外权益和明确主题基金都纳入，能解释“A股行业变化”覆盖不足的样本。</p></div></div>
          ${signalDirectionChart(themeSignalRows, "权益行业主题", themeSignalRows.length)}
          ${assetSignalTable(themeSignalRows, "权益行业主题", themeSignalRows.length)}
          <div class="source-method"><strong>${B.label("权益行业主题")}</strong> 该表仍按策略级净变化统计，不做底层股票穿透；宽基指数和主动权益基金归入宽基/主动权益。</div>
        </section>
        <section class="insight-grid">
          <div class="panel">
            <div class="panel-head"><div><h2>基金调入摘要</h2><p class="desc">只展示净增配靠前的少量基金，用于定位需要追踪的底层产品。</p></div></div>
            ${rankList(addRows, { limit: 5, title: (row) => row.基金名称, href: fundDetailUrl, sub: (row) => `${row.基金公司 || ""}｜${row.研报大类资产 || row.基金类型 || ""}`, value: (row) => weightPoint(row.净增配), meta: (row) => `中位${weightPoint(row.中位净增配)}｜${countText(row.调仓策略数)}策` })}
          </div>
          <div class="panel">
            <div class="panel-head"><div><h2>基金调出摘要</h2><p class="desc">只展示净减配靠前的少量基金，先看是否为同类策略共同行为。</p></div></div>
            ${rankList(reduceRows, { limit: 5, title: (row) => row.基金名称, href: fundDetailUrl, sub: (row) => `${row.基金公司 || ""}｜${row.研报大类资产 || row.基金类型 || ""}`, value: (row) => weightPoint(row.净增配), meta: (row) => `中位${weightPoint(row.中位净增配)}｜${countText(row.调仓策略数)}策` })}
          </div>
        </section>
        <section class="panel">
          <div class="panel-head"><div><h2>投顾机构差异</h2><p class="desc">同一研报类型内比较机构调仓频率、可评价效果和主加减仓方向；机构维度用于对标投顾管理人，不等同于底层基金公司。</p></div></div>
          ${tableBlock(["投顾机构", "事件数", "可评价事件数", "调仓胜率", "平均调仓超额", "平均单次换手率", "业务读法"], institutionRows.slice(0, 10), (row, h) => {
            if (h.includes("事件数")) return countText(row[h]);
            if (h.includes("胜率")) return effectPct(row[h]);
            if (h.includes("换手率")) return pct(row[h]);
            if (h.includes("超额")) return effectSigned(row[h]);
            if (h === "业务读法") return `<span class="small">主加仓：${B.esc(row.主加仓资产)}；主减仓：${B.esc(row.主减仓资产)}；方向集中度${pct(row.方向集中度)}。</span>`;
            return B.fmt(row[h]);
          })}
        </section>
        <section class="panel" id="fund-company-opportunity">
          <div class="panel-head"><div><h2>基金公司机会</h2><p class="desc">这里看的是底层基金所属公司被投顾组合增配或减配的方向，用于产品流向和营销机会判断；广发基金单独高亮。</p></div></div>
          ${tableBlock(["基金公司", "净方向", "主加仓资产", "主减仓资产", "净增配", "加仓权重", "减仓权重", "调仓强度", "调仓策略数"], companySummaryRows.slice(0, 12), (row, h) => {
            if (h === "基金公司") return /广发/.test(raw(row[h])) ? `<span class="insight-chip action-attack">${B.esc(row[h])}</span>` : B.fmt(row[h]);
            if (h.includes("权重") || h.includes("净增配") || h === "调仓强度") return weightPoint(row[h]);
            if (h.includes("策略数")) return countText(row[h]);
            return B.fmt(row[h]);
          })}
          <div class="source-method"><strong>广发基金机会</strong> ${gfOpportunityRows.slice(0, 5).map((row) => `${fundLink(row)}（${B.esc(row.机会类型)}，非广发净增配${weightPoint(row.非广发策略净增配)}）`).join("；") || "当前筛选下暂无广发基金调仓机会样本。"}</div>
        </section>
        <details class="fold-block">
          <summary>验证区：调仓逻辑、热力图和基金级大表</summary>
          <section class="panel">
            <div class="panel-head"><div><h2>调仓有效性验证</h2><p class="desc">把调仓逻辑和事后结果放在一起看，辅助识别高频但无效或低频但有效的动作。</p></div></div>
            ${tableBlock(["调仓逻辑", "事件数", "可评价事件数", "胜率", "平均调仓超额", "平均单次换手率", "样本判断"], logicEffectRows.slice(0, 12), (row, h) => {
              if (h === "调仓逻辑") return B.fmt(row.分类);
              if (h.includes("事件数")) return countText(row[h]);
              if (h === "胜率") return effectPct(row[h]);
              if (h.includes("换手率")) return pct(row[h]);
              if (h.includes("超额")) return effectSigned(row[h]);
              return B.fmt(row[h]);
            })}
          </section>
          <section class="panel">
            <div class="panel-head"><div><h2>月度调仓频率</h2><p class="desc">用于验证调仓是否集中发生在某些月份，不作为单独结论。</p></div></div>
            ${monthlyEventChart(events)}
          </section>
          <section class="panel">
            <div class="panel-head"><div><h2>投顾机构资产方向热力图</h2><p class="desc">横向比较全市场、广发基金和非广发Top5投顾机构在同类策略中的资产增减持。</p></div></div>
            ${companyAssetHeatmap(advisorAssetRows, strategyAssetChangeRows)}
          </section>
          <section class="panel">
            <div class="panel-head"><div><h2>主动调仓前后大类资产变化热力图</h2><p class="desc">仅看当前窗口内发生主动调仓的策略，比较第一次调仓前与最后一次调仓后的大类资产仓位。</p></div></div>
            ${activeAssetBeforeAfterHeatmap(strategyAssetChangeRows)}
          </section>
          <section class="panel">
            <div class="panel-head"><div><h2>期初期末研报大类资产变化</h2><p class="desc">按策略匹配区间起止附近最近可用快照，并按基金资产暴露拆分，不把缺失月份当作0仓位。</p></div></div>
            ${industryPeriodHeatmap(holdingSnapshotRows, "研报大类资产", "研报大类资产")}
          </section>
          <section class="panel">
            <div class="panel-head"><div><h2>期初期末A股行业变化</h2><p class="desc">仅统计明确行业主题基金，按全市场期末占比排序。</p></div></div>
            ${industryPeriodHeatmap(holdingSnapshotRows, "研报A股行业", "研报A股行业")}
          </section>
          <section class="insight-grid">
            <div class="panel">
              <div class="panel-head"><div><h2>基金调入榜</h2><p class="desc">基金级明细用于核验，不直接生成市场方向结论。</p></div></div>
              ${tableBlock(["基金名称", "基金公司", "研报大类资产", "净增配", "中位净增配", "调仓策略数"], addRows.slice(0, 10), (row, h) => {
                if (h === "基金名称") return fundLink(row);
                if (h.includes("策略数")) return countText(row[h]);
                if (h.includes("净增配")) return weightPoint(row[h]);
                return B.fmt(row[h]);
              })}
            </div>
            <div class="panel">
              <div class="panel-head"><div><h2>基金调出榜</h2><p class="desc">基金级明细用于核验，不直接生成市场方向结论。</p></div></div>
              ${tableBlock(["基金名称", "基金公司", "研报大类资产", "净增配", "中位净增配", "调仓策略数"], reduceRows.slice(0, 10), (row, h) => {
                if (h === "基金名称") return fundLink(row);
                if (h.includes("策略数")) return countText(row[h]);
                if (h.includes("净增配")) return weightPoint(row[h]);
                return B.fmt(row[h]);
              })}
            </div>
          </section>
        </details>
        <details class="fold-block">
          <summary>原始明细：当前同类策略调仓事件</summary>
          <section class="panel">
            <div class="panel-head"><div><h2>近期调仓明细</h2><p class="desc">用于追溯具体事件，分页显示全部当前筛选结果。</p></div></div>
            ${tableBlock(["调仓日期", "策略名称", "投顾机构", "研报产品类型", "业务分类", "调仓逻辑", "单次换手率", "涉及资产", "胜负"], detailRows, (row, h) => {
              if (h === "策略名称") return strategyLink(row);
              if (h.includes("换手率")) return pct(row[h]);
              if (h === "胜负") return effectLabel(row[h]);
              return B.fmt(row[h]);
            })}
            ${pagerControls("rebalanceDetail", state.rebalancePage, totalPages, state.rebalancePageSize, events.length)}
          </section>
        </details>`;
    }
    const events = rebalanceEvents();
    const evaluated = events.map(winValue).filter((value) => value !== null);
    const winRate = evaluated.length ? evaluated.filter(Boolean).length / evaluated.length * 100 : null;
    const avgExtra = avg(events.map((row) => row.调仓超额 ?? row.方向性超额));
    const monthlyFunds = filteredMonthlyFundRows();
    const fundRows = rollupMonthlyFunds(monthlyFunds);
    const addRows = [...fundRows].filter((row) => row.净增配 > 0).sort((a, b) => b.净增配 - a.净增配);
    const reduceRows = [...fundRows].filter((row) => row.净增配 < 0).sort((a, b) => a.净增配 - b.净增配);
    const gfOpportunityRows = gfRebalanceOpportunityRows(monthlyFunds);
    const companyAssetRows = rollupCompanyAssetDirection(monthlyFunds);
    const companySummaryRows = companyDirectionSummary(companyAssetRows);
    const strategyAssetChangeRows = filteredStrategyAssetChangeRows();
    const assetSignalRows = strategyAssetSignalRows(strategyAssetChangeRows);
    const advisorAssetRows = rollupAdvisorAssetDirection(strategyAssetChangeRows);
    const logicEffectRows = rebalanceEffectRows(events, "调仓逻辑");
    const institutionRows = institutionBehaviorRows(events, advisorAssetRows);
    const conclusions = rebalanceConclusions(assetSignalRows, logicEffectRows, institutionRows, gfOpportunityRows);
    const holdingSnapshotRows = filteredHoldingSnapshotRows();
    const topAssetAdd = assetSignalRows.find((row) => /增配/.test(row.判断));
    const topAssetReduce = assetSignalRows.find((row) => /减配/.test(row.判断));
    const directionSignal = assetSignalRows.find((row) => /增配|减配/.test(row.判断)) || assetSignalRows[0];
    const gfExternalCount = gfOpportunityRows.filter((row) => row.机会类型 === "外部增配验证").length;
    const totalPages = Math.max(1, Math.ceil(events.length / state.rebalancePageSize));
    if (state.rebalancePage > totalPages) state.rebalancePage = totalPages;
    const detailStart = (state.rebalancePage - 1) * state.rebalancePageSize;
    const detailRows = events.slice(detailStart, detailStart + state.rebalancePageSize);
    return `
      <section class="insight-hero">
        ${kpi("区间调仓事件", countText(events.length), "按统一时间区间筛选")}
        ${kpi("可评价事件", countText(evaluated.length), "用于验证调仓后效果")}
        ${kpi("调仓胜率", effectPct(winRate), "已到观察窗口的事件")}
        ${kpi("平均调仓超额", effectSigned(avgExtra), "调仓后收益相对评价口径")}
        ${kpi("方向信号", directionSignal ? B.esc(directionSignal.分类) : "方向分歧", directionSignal ? `${directionSignal.判断}，中位净变化${weightPoint(directionSignal.中位净变化 ?? directionSignal.典型变化)}` : "当前策略增减方向不集中")}
        ${kpi("广发外部机会", countText(gfExternalCount), "非广发策略增配广发基金")}
      </section>
      <section class="panel">
        <div class="panel-head"><div><h2>调仓结论摘要</h2><p class="desc">只保留能回答业务问题的信号：市场方向是否集中、调仓是否有效、机构是否有差异、广发基金是否被外部策略增配。</p></div></div>
        ${rebalanceConclusionList(conclusions)}
        <div class="source-method"><strong>分析口径</strong> 市场方向只使用策略级资产变化明细：先把同一策略同一资产类型在区间内的调仓合并，再统计多少策略增配、多少策略减配和单策略中位变化；基金级月度汇总只用于底层基金机会和明细验证，不再用于生成市场方向结论。</div>
      </section>
      <section class="panel">
        <div class="panel-head"><div><h2>市场方向：策略级资产变化</h2><p class="desc">每一行先看“判断”和“增/减策略数”。只有多数策略同向、且典型策略变化不接近0，才视为有效市场信号；累计净变化仅作辅助。</p></div></div>
        ${tableBlock(["资产类型", "判断", "参与策略", "增/减策略", "中位净变化", "累计净变化", "策略调整摘要"], assetSignalRows.slice(0, 7), (row, h) => {
          if (h === "资产类型") return B.fmt(row.分类);
          if (h === "判断") return `<span class="insight-chip ${directionTone(row.判断)}">${B.esc(row.判断)}</span>`;
          if (h === "参与策略") return countText(row.参与策略数);
          if (h === "增/减策略") return `${countText(row.增持策略数)} / ${countText(row.减持策略数)}`;
          if (h === "中位净变化" || h === "累计净变化") return weightPoint(h === "中位净变化" ? (row.中位净变化 ?? row.典型变化) : row.净变化);
          if (h === "策略调整摘要") return `<span class="small">${B.esc(strategyAdjustmentSummary(row))}</span>`;
          return B.fmt(row[h]);
        })}
      </section>
      <section class="panel">
        <div class="panel-head"><div><h2>调仓有效性</h2><p class="desc">把调仓逻辑和事后结果放在一起看，重点识别“高频但无效”和“低频但有效”的动作。</p></div></div>
        ${tableBlock(["调仓逻辑", "事件数", "可评价事件数", "胜率", "平均调仓超额", "平均单次换手率", "样本判断"], logicEffectRows.slice(0, 12), (row, h) => {
          if (h === "调仓逻辑") return B.fmt(row.分类);
          if (h.includes("事件数")) return countText(row[h]);
          if (h === "胜率") return effectPct(row[h]);
          if (h.includes("换手率")) return pct(row[h]);
          if (h.includes("超额")) return effectSigned(row[h]);
          return B.fmt(row[h]);
        })}
      </section>
      <section class="panel">
        <div class="panel-head"><div><h2>机构行为画像</h2><p class="desc">机构层面同时看调仓胜率、换手和主加减仓资产，用于识别可跟踪、可对标或需警惕的投顾管理人。</p></div></div>
        ${tableBlock(["投顾机构", "事件数", "可评价事件数", "调仓胜率", "平均调仓超额", "平均单次换手率", "业务读法"], institutionRows.slice(0, 8), (row, h) => {
          if (h.includes("事件数")) return countText(row[h]);
          if (h.includes("胜率")) return effectPct(row[h]);
          if (h.includes("换手率")) return pct(row[h]);
          if (h.includes("超额")) return effectSigned(row[h]);
          if (h === "业务读法") return `<span class="small">主加仓：${B.esc(row.主加仓资产)}；主减仓：${B.esc(row.主减仓资产)}；方向集中度${pct(row.方向集中度)}。</span>`;
          return B.fmt(row[h]);
        })}
      </section>
      <details class="fold-block">
        <summary>验证图表：机构热力图、主动调仓前后变化和月度频率</summary>
        <section class="panel">
          <div class="panel-head"><div><h2>月度调仓频率</h2><p class="desc">辅助判断调仓是否集中发生在某些月份，不作为单独结论。</p></div></div>
          ${monthlyEventChart(events)}
        </section>
        <section class="panel">
          <div class="panel-head"><div><h2>投顾机构资产方向热力图</h2><p class="desc">横向比较全市场、广发基金和非广发Top5机构在区间内的资产类型增减持，验证机构画像是否来自真实仓位变化。</p></div></div>
          ${companyAssetHeatmap(advisorAssetRows, strategyAssetChangeRows)}
        </section>
        <section class="panel">
          <div class="panel-head"><div><h2>主动调仓前后大类资产变化热力图</h2><p class="desc">仅看区间内发生主动调仓的策略，比较每只策略第一次调仓前与最后一次调仓后的大类资产中位仓位变化。</p></div></div>
          ${activeAssetBeforeAfterHeatmap(strategyAssetChangeRows)}
        </section>
      </details>
      <section class="panel">
        <div class="panel-head"><div><h2>广发产品机会</h2><p class="desc">优先看非广发策略是否主动增配广发基金；净增配为跨策略累计仓位变化百分点，若只由广发策略贡献，则标记为内部配置为主。</p></div></div>
        ${tableBlock(["基金名称", "基金类型", "机会类型", "非广发净增配", "广发净增配", "调仓策略", "业务读法"], gfOpportunityRows.slice(0, 8), (row, h) => {
          if (h === "基金名称") return fundLink(row);
          if (h === "非广发净增配") return weightPoint(row.非广发策略净增配);
          if (h === "广发净增配") return weightPoint(row.广发策略净增配);
          if (h === "调仓策略") return countText(row.调仓策略数);
          if (h === "机会类型") return `<span class="insight-chip ${row[h] === "外部增配验证" ? "action-attack" : (row[h] === "外部减配预警" ? "action-watch" : "action-hold")}">${B.esc(row[h])}</span>`;
          if (h === "业务读法") return `<span class="small">${B.esc(row.机会类型 === "外部增配验证" ? "非广发策略也在增配，可作为渠道沟通或竞品复盘线索。" : row.机会类型 === "外部减配预警" ? "外部策略净减配，需先核查业绩、费率或风格适配问题。" : "主要由广发策略贡献，不能直接视作外部营销机会。")}</span>`;
          return B.fmt(row[h]);
        })}
      </section>
      <details class="fold-block">
        <summary>基金级交易验证：调入、调出和底层基金公司产品流向</summary>
        <section class="insight-grid">
          <div class="panel">
            <div class="panel-head"><div><h2>基金调入榜</h2><p class="desc">按区间净增配排序，显示全市场投顾明显增配的基金。</p></div></div>
            ${tableBlock(["基金名称", "基金公司", "基金类型", "净增配", "中位净增配", "调仓策略数"], addRows.slice(0, 10), (row, h) => {
              if (h === "基金名称") return fundLink(row);
              if (h.includes("策略数")) return countText(row[h]);
              if (h.includes("净增配")) return weightPoint(row[h]);
              return B.fmt(row[h]);
            })}
          </div>
          <div class="panel">
            <div class="panel-head"><div><h2>基金调出榜</h2><p class="desc">按区间净减配排序，识别被明显降低配置的基金。</p></div></div>
            ${tableBlock(["基金名称", "基金公司", "基金类型", "净增配", "中位净增配", "调仓策略数"], reduceRows.slice(0, 10), (row, h) => {
              if (h === "基金名称") return fundLink(row);
              if (h.includes("策略数")) return countText(row[h]);
              if (h.includes("净增配")) return weightPoint(row[h]);
              return B.fmt(row[h]);
            })}
          </div>
        </section>
        <section class="panel">
          <div class="panel-head"><div><h2>底层基金公司产品流向</h2><p class="desc">这里统计的是被投顾组合买卖的底层基金所属公司，不是投顾机构；用于看哪些基金公司的产品被系统性增配或减配。</p></div></div>
          ${tableBlock(["基金公司", "净方向", "主加仓资产", "主减仓资产", "净增配", "中位净增配", "加仓权重", "减仓权重", "调仓强度", "调仓策略数"], companySummaryRows.slice(0, 12), (row, h) => {
            if (h.includes("权重") || h.includes("净增配") || h === "调仓强度") return weightPoint(row[h]);
            if (h.includes("策略数")) return countText(row[h]);
            return B.fmt(row[h]);
          })}
        </section>
      </details>
      <details class="fold-block">
        <summary>仓位验证视角：期初期末资产、主题和行业分布变化</summary>
        <section class="panel">
          <div class="panel-head"><div><h2>期初期末资产/主题主归属变化热力图</h2><p class="desc">每只基金只落一个主归属：非权益按现金、固收、商品、海外归属，权益基金按明确行业主题、宽基指数或主动权益归属；多行业命中统一归入跨行业/多主题权益。</p></div></div>
          ${industryPeriodHeatmap(holdingSnapshotRows, "行业主题", "资产/主题")}
        </section>
        <section class="panel">
          <div class="panel-head"><div><h2>期初期末权益行业主题分布变化热力图</h2><p class="desc">仅统计权益、指数、海外权益和明确主题基金；现金、固收和商品不进入该图。宽基与主动权益因缺少股票持仓行业穿透，统一归为宽基/主动权益。</p></div></div>
          ${industryPeriodHeatmap(holdingSnapshotRows, "权益行业主题", "权益行业主题")}
        </section>
        <section class="panel">
          <div class="panel-head"><div><h2>期初期末权益行业大类分布变化热力图</h2><p class="desc">在权益行业主题基础上归并为科技制造、消费医药、金融周期/价值、海外权益、宽基/主动权益和跨行业/多主题，便于观察权益方向切换。</p></div></div>
          ${industryPeriodHeatmap(holdingSnapshotRows, "权益行业大类", "权益行业大类")}
        </section>
      </details>
      <details class="fold-block">
        <summary>原始明细：近期调仓事件分页查看</summary>
        <section class="panel">
          <div class="panel-head"><div><h2>近期调仓明细</h2><p class="desc">仅用于追溯具体事件，不作为主结论来源。</p></div></div>
          ${tableBlock(["调仓日期", "策略名称", "投顾机构", "风险等级", "业务分类", "市场地域", "调仓逻辑", "单次换手率", "涉及资产", "胜负"], detailRows, (row, h) => {
            if (h === "策略名称") return strategyLink(row);
            if (h.includes("换手率")) return pct(row[h]);
            if (h === "胜负") return effectLabel(row[h]);
            return B.fmt(row[h]);
          })}
          ${pagerControls("rebalanceDetail", state.rebalancePage, totalPages, state.rebalancePageSize, events.length)}
        </section>
      </details>`;
  }

  function renderContent() {
    if (state.tab === "holding") return holdingTab();
    if (state.tab === "rebalance") return rebalanceTab();
    return marketTab();
  }

  function scrollToHashTarget() {
    const id = decodeURIComponent(String(window.location.hash || "").replace(/^#/, ""));
    if (!id) return;
    const target = document.getElementById(id);
    if (target) target.scrollIntoView({ block: "start" });
  }

  function render() {
    const displayCount = strategyRows().length;
    const holdingDisplayCount = filteredHoldingStrategyRows().length;
    const rebalanceDisplayCount = rebalanceEvents(false).length;
    signalDetailStore.clear();
    root.innerHTML = `
      <section class="page-title">
        <div>
          <h1>数据洞察</h1>
          <p class="desc">按市场总览、仓位分析和调仓分析三类视角展示策略表现、底层基金配置和调仓变化。</p>
        </div>
        <div class="title-pills">
          <span class="pill">产品 ${countText(displayCount)} 个</span>
          <span class="pill">持仓明细 ${countText(holdingDisplayCount)} 行</span>
          <span class="pill">调仓 ${countText(rebalanceDisplayCount)} 条</span>
        </div>
      </section>
      <section class="panel insight-sticky-controls">
        <div class="insight-filters">
          ${filterField("时间区间", `<select id="insightRange" class="control">${dateRanges.map((item) => `<option value="${item.key}" ${item.key === state.range ? "selected" : ""}>${B.esc(item.label)}</option>`).join("")}</select>`)}
          ${filterField("风险等级", `<select id="insightRisk" class="control"><option value="">全部风险等级</option>${risks.map((risk) => `<option ${risk === state.risk ? "selected" : ""}>${B.esc(risk)}</option>`).join("")}</select>`)}
          ${filterField("业务分类", `<select id="insightBusiness" class="control"><option value="">全部业务分类</option>${businesses.map((business) => `<option ${business === state.business ? "selected" : ""}>${B.esc(business)}</option>`).join("")}</select>`)}
          ${filterField("市场地域", `<select id="insightRegion" class="control"><option value="">全部市场地域</option>${regions.map((region) => `<option ${region === state.region ? "selected" : ""}>${B.esc(region)}</option>`).join("")}</select>`)}
          ${filterField("对客范围", clientScopeSelect("insightClientScope", state.clientScope))}
          ${filterField("策略范围", gfScopeSelect("insightGfScope", state.gfScope))}
          ${filterField("投顾机构", institutionSelect("insightInstitution", state.institution))}
        </div>
        <div class="insight-tabs">${tabs.map(([key, label]) => `<button type="button" class="insight-tab-button ${key === state.tab ? "is-active" : ""}" data-tab="${key}">${B.esc(label)}</button>`).join("")}</div>
        <div class="source-method"><strong>${B.label("筛选口径")}</strong> 上方筛选条件同步作用于市场总览、仓位分析、调仓分析的所有图表和表格；默认仅展示数据完整、非D0、非持仓缺失/不入池策略；对客范围可剔除天天投顾明确非对客展示的策略；策略范围可切换全部策略、仅看广发策略、仅看非广发策略；时间区间同时用于区间收益、仓位时间序列和调仓事件；目标盈系列产品在市场总览中按同系列产品多期合并。</div>
      </section>
      <div class="insight-panel-stack">${renderContent()}</div>
    `;
    const resetDataView = () => {
      state.selectedPointId = "";
      state.rebalancePage = 1;
      state.businessPages = {};
      state.openBusiness = "";
      state.expandedFundKey = "";
      state.reportType = "";
    };
    B.byId("insightRange").addEventListener("change", () => { state.range = B.byId("insightRange").value; resetDataView(); render(); });
    B.byId("insightRisk").addEventListener("change", () => { state.risk = B.byId("insightRisk").value; resetDataView(); render(); });
    B.byId("insightBusiness").addEventListener("change", () => { state.business = B.byId("insightBusiness").value; resetDataView(); render(); });
    B.byId("insightRegion").addEventListener("change", () => { state.region = B.byId("insightRegion").value; resetDataView(); render(); });
    B.byId("insightClientScope").addEventListener("change", () => { state.clientScope = B.byId("insightClientScope").value; resetDataView(); render(); });
    B.byId("insightGfScope").addEventListener("change", () => { state.gfScope = B.byId("insightGfScope").value; resetDataView(); render(); });
    B.byId("insightInstitution").addEventListener("change", () => { state.institution = B.byId("insightInstitution").value; resetDataView(); render(); });
    root.querySelectorAll("[data-tab]").forEach((button) => {
      button.addEventListener("click", () => {
        state.tab = button.dataset.tab;
        render();
      });
    });
    root.querySelectorAll("details[data-business-key]").forEach((details) => {
      details.addEventListener("toggle", () => {
        const key = details.dataset.businessKey || "";
        if (details.open) state.openBusiness = key;
        else if (state.openBusiness === key) state.openBusiness = "";
      });
    });
    const scatterX = B.byId("scatterX");
    const scatterViewPct = B.byId("scatterViewPct");
    const scatterGfScope = B.byId("scatterGfScope");
    const scatterInstitution = B.byId("scatterInstitution");
    const businessProductScope = B.byId("businessProductScope");
    const rebalancePrev = B.byId("rebalanceDetailPrev");
    const rebalanceNext = B.byId("rebalanceDetailNext");
    const rebalancePageSize = B.byId("rebalanceDetailPageSize");
    const rebalanceMode = B.byId("rebalanceMode");
    const rebalanceMonth = B.byId("rebalanceMonth");
    const rebalanceReportType = B.byId("rebalanceReportType");
    if (scatterX) scatterX.addEventListener("change", () => { state.scatterX = scatterX.value; render(); });
    if (scatterViewPct) scatterViewPct.addEventListener("input", () => { state.viewPct = Number(scatterViewPct.value) || 100; render(); });
    if (scatterGfScope) scatterGfScope.addEventListener("change", () => { state.gfScope = scatterGfScope.value; resetDataView(); render(); });
    if (scatterInstitution) scatterInstitution.addEventListener("change", () => { state.institution = scatterInstitution.value; resetDataView(); render(); });
    if (businessProductScope) businessProductScope.addEventListener("change", () => { state.businessProductScope = businessProductScope.value; render(); });
    root.querySelectorAll("[data-business-sort]").forEach((button) => {
      button.addEventListener("click", () => {
        const field = button.dataset.businessSort || "区间收益";
        if (state.businessSortField === field) state.businessSortDir = state.businessSortDir === "asc" ? "desc" : "asc";
        else {
          state.businessSortField = field;
          state.businessSortDir = field === "最大回撤" || field === "波动率" ? "asc" : "desc";
        }
        state.businessPages = {};
        render();
      });
    });
    root.querySelectorAll("[data-business-page]").forEach((button) => {
      button.addEventListener("click", () => {
        const key = button.dataset.businessPage || "";
        const delta = Number(button.dataset.pageDelta) || 0;
        state.businessPages[key] = Math.max(1, (state.businessPages[key] || 1) + delta);
        render();
      });
    });
    root.querySelectorAll(".fund-row").forEach((row) => {
      row.addEventListener("click", (event) => {
        if (event.target.closest("a, .info-button")) return;
        const key = row.dataset.fundKey || "";
        state.expandedFundKey = state.expandedFundKey === key ? "" : key;
        render();
      });
    });
    if (rebalancePrev) rebalancePrev.addEventListener("click", () => { state.rebalancePage = Math.max(1, state.rebalancePage - 1); render(); });
    if (rebalanceNext) rebalanceNext.addEventListener("click", () => { state.rebalancePage += 1; render(); });
    if (rebalancePageSize) rebalancePageSize.addEventListener("change", () => { state.rebalancePageSize = Number(rebalancePageSize.value) || 20; state.rebalancePage = 1; render(); });
    if (rebalanceMode) rebalanceMode.addEventListener("change", () => { state.rebalanceMode = rebalanceMode.value || "month"; state.rebalancePage = 1; render(); });
    if (rebalanceMonth) rebalanceMonth.addEventListener("change", () => { state.rebalanceMonth = rebalanceMonth.value || ""; state.rebalancePage = 1; render(); });
    if (rebalanceReportType) rebalanceReportType.addEventListener("change", () => { state.reportType = rebalanceReportType.value || ""; state.rebalancePage = 1; render(); });
    root.querySelectorAll("[data-signal-detail]").forEach((button) => {
      button.addEventListener("click", () => {
        showSignalDetail(button.dataset.signalDetail || "");
      });
    });
    root.querySelectorAll("[data-report-type-select]").forEach((button) => {
      button.addEventListener("click", () => {
        state.reportType = button.dataset.reportTypeSelect || "";
        state.rebalancePage = 1;
        render();
      });
    });
    root.querySelectorAll("[data-point-id]").forEach((point) => {
      point.addEventListener("click", () => {
        state.selectedPointId = point.dataset.pointId || "";
        render();
      });
    });
    const hoverTip = B.byId("scatterHoverTip");
    if (hoverTip) {
      const moveTip = (event, point) => {
        hoverTip.hidden = false;
        hoverTip.innerHTML = `<strong>策略信息</strong><span>${B.esc(point.dataset.tooltip || "")}</span>`;
        const chart = hoverTip.closest(".chart");
        const rect = chart.getBoundingClientRect();
        const rawLeft = event.clientX - rect.left + 14;
        const rawTop = event.clientY - rect.top + 12;
        const left = rawLeft > rect.width - 320 ? Math.max(12, rawLeft - 300) : Math.max(12, rawLeft);
        const top = rawTop > rect.height - 120 ? Math.max(40, rawTop - 118) : Math.max(40, rawTop);
        hoverTip.style.left = `${left}px`;
        hoverTip.style.top = `${top}px`;
      };
      root.querySelectorAll(".scatter-point").forEach((point) => {
        point.addEventListener("pointerenter", (event) => moveTip(event, point));
        point.addEventListener("pointermove", (event) => moveTip(event, point));
        point.addEventListener("pointerleave", () => { hoverTip.hidden = true; });
      });
    }
    requestAnimationFrame(scrollToHashTarget);
  }

  window.addEventListener("hashchange", scrollToHashTarget);
  render();
})();
