(() => {
  const B = window.BasicData || {};
  const root = B.byId ? B.byId("topicAnalysisPage") : document.getElementById("topicAnalysisPage");
  const pack = window.__BASIC_TOPIC_ANALYSIS_PACK__ || {};
  const esc = B.esc || ((value) => String(value ?? "").replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;").replaceAll("'", "&#39;"));
  const pct = (value, digits = 2) => Number.isFinite(Number(value)) ? `${Number(value).toFixed(digits)}%` : "未披露";
  const signedPct = (value, digits = 2) => {
    if (!Number.isFinite(Number(value))) return '<span class="value-muted">未披露</span>';
    const number = Number(value);
    const cls = number > 0 ? "ret-pos" : number < 0 ? "ret-neg" : "ret-zero";
    return `<span class="${cls}">${number.toFixed(digits)}%</span>`;
  };
  const fmt = (value, digits = 2) => Number.isFinite(Number(value)) ? Number(value).toLocaleString("zh-CN", { maximumFractionDigits: digits }) : "未披露";
  const label = (name) => B.label ? B.label(name) : esc(name);
  const valueHtml = (field, value) => B.valueHtml ? B.valueHtml(field, value) : esc(value ?? "未披露");

  const lineColors = {
    "入选策略等权净值": "#d92d20",
    "AI核心基金池等权参考": "#1570ef",
    "中证TMT": "#7c3aed",
  };
  const selectedPalette = ["#d92d20", "#1570ef", "#7c3aed", "#0f766e", "#b7791f", "#c11574", "#175cd3", "#9a3412", "#2f6f4e", "#6941c6"];

  function strategyUrl(row) {
    const id = row?.统一策略ID || row?.策略ID || row?.id;
    return `./strategy.html?id=${encodeURIComponent(id || "")}`;
  }

  function fundUrl(fund) {
    const code = fund?.code || fund?.基金代码;
    const name = fund?.name || fund?.基金名称;
    if (code) return `./fund.html?code=${encodeURIComponent(code)}`;
    return `./fund.html?name=${encodeURIComponent(name || "")}`;
  }

  function strategyLink(row, className = "") {
    return `<a class="link ${className}" href="${strategyUrl(row)}">${esc(row?.策略名称 || row?.统一策略ID || "未命名策略")}</a>`;
  }

  function fundLink(fund) {
    const name = fund?.name || fund?.基金名称 || fund?.code || fund?.基金代码 || "未命名基金";
    const code = fund?.code || fund?.基金代码;
    const weight = Number.isFinite(Number(fund?.weight)) ? ` ${pct(fund.weight)}` : "";
    return `<a class="topic-fund-chip" href="${fundUrl(fund)}" title="${esc(fund?.hits || fund?.命中依据 || "")}"><strong>${esc(name)}</strong>${code ? `<span>${esc(code)}</span>` : ""}${weight ? `<em>${weight}</em>` : ""}</a>`;
  }

  function groupBy(rows, key) {
    return (rows || []).reduce((acc, row) => {
      const k = row[key] || "未分组";
      (acc[k] ||= []).push(row);
      return acc;
    }, {});
  }

  function pathFromPoints(points) {
    return points.map((point, index) => `${index ? "L" : "M"}${point.x.toFixed(2)},${point.y.toFixed(2)}`).join(" ");
  }

  function renderLineChart(rows) {
    const grouped = groupBy(rows, "系列");
    const all = [];
    Object.entries(grouped).forEach(([name, items]) => {
      items.forEach((row) => {
        const t = Date.parse(row.日期);
        const y = Number(row.指数点位);
        if (Number.isFinite(t) && Number.isFinite(y)) all.push({ name, t, y, date: row.日期 });
      });
    });
    if (!all.length) return '<div class="empty">暂无可绘制的走势数据</div>';

    const width = 980;
    const height = 340;
    const margin = { top: 22, right: 72, bottom: 38, left: 54 };
    const xMin = Math.min(...all.map((d) => d.t));
    const xMax = Math.max(...all.map((d) => d.t));
    const rawYMin = Math.min(...all.map((d) => d.y), 100);
    const rawYMax = Math.max(...all.map((d) => d.y), 100);
    const yPad = Math.max((rawYMax - rawYMin) * 0.12, 2);
    const yMin = rawYMin - yPad;
    const yMax = rawYMax + yPad;
    const x = (t) => margin.left + ((t - xMin) / Math.max(xMax - xMin, 1)) * (width - margin.left - margin.right);
    const y = (value) => height - margin.bottom - ((value - yMin) / Math.max(yMax - yMin, 1)) * (height - margin.top - margin.bottom);
    const yTicks = Array.from({ length: 5 }, (_, i) => yMin + ((yMax - yMin) * i) / 4);
    const xTicks = Array.from({ length: 5 }, (_, i) => xMin + ((xMax - xMin) * i) / 4);
    const seriesSvg = Object.entries(grouped).map(([name, items]) => {
      const points = items.map((row) => ({ t: Date.parse(row.日期), y: Number(row.指数点位), date: row.日期 }))
        .filter((d) => Number.isFinite(d.t) && Number.isFinite(d.y))
        .sort((a, b) => a.t - b.t)
        .map((d) => ({ ...d, x: x(d.t), yPos: y(d.y) }));
      if (!points.length) return "";
      const last = points[points.length - 1];
      const color = lineColors[name] || "#475467";
      return `
        <path class="topic-line-path" d="${pathFromPoints(points.map((d) => ({ x: d.x, y: d.yPos })))}" fill="none" stroke="${color}" stroke-width="3.4" stroke-linecap="round" stroke-linejoin="round" />
        <circle cx="${last.x.toFixed(2)}" cy="${last.yPos.toFixed(2)}" r="3.8" fill="${color}"><title>${esc(name)} ${esc(last.date)} ${fmt(last.y)}</title></circle>
        <text x="${Math.min(last.x + 8, width - 68).toFixed(2)}" y="${last.yPos.toFixed(2)}" fill="${color}" font-size="12" font-weight="750">${esc(name)}</text>`;
    }).join("");
    const axis = `
      ${yTicks.map((tick) => `<line x1="${margin.left}" x2="${width - margin.right}" y1="${y(tick).toFixed(2)}" y2="${y(tick).toFixed(2)}" stroke="#edf2f7" /><text x="${margin.left - 10}" y="${(y(tick) + 4).toFixed(2)}" text-anchor="end" font-size="11" fill="#667085">${fmt(tick, 1)}</text>`).join("")}
      ${xTicks.map((tick) => {
        const d = new Date(tick);
        const text = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
        return `<text x="${x(tick).toFixed(2)}" y="${height - 12}" text-anchor="middle" font-size="11" fill="#667085">${text}</text>`;
      }).join("")}
      <line x1="${margin.left}" x2="${width - margin.right}" y1="${y(100).toFixed(2)}" y2="${y(100).toFixed(2)}" stroke="#98a2b3" stroke-dasharray="4 4" />`;
    const legend = Object.keys(grouped).map((name) => `<span><i style="background:${lineColors[name] || "#475467"}"></i>${esc(name)}</span>`).join("");
    return `
      <div class="topic-chart topic-line-chart">
        <div class="topic-chart-legend">${legend}</div>
        <svg viewBox="0 0 ${width} ${height}" role="img" aria-label="AI核心高暴露策略与参考指数走势">${axis}${seriesSvg}</svg>
      </div>`;
  }

  function renderScatter(theme) {
    const rows = theme.points || [];
    if (!rows.length) return '<div class="empty">暂无策略点阵数据</div>';
    const selectedIds = new Set((theme.selected || []).map((row) => row.统一策略ID));
    const colorById = {};
    (theme.selected || []).forEach((row, index) => {
      colorById[row.统一策略ID] = selectedPalette[index % selectedPalette.length];
    });
    const width = 980;
    const height = 360;
    const margin = { top: 22, right: 24, bottom: 48, left: 62 };
    const xMax = Math.max(60, ...rows.map((row) => Number(row.AI核心均值暴露) || 0)) + 5;
    const yValues = rows.map((row) => Number(row.近1年收益)).filter(Number.isFinite);
    const yMin = Math.min(-10, ...yValues) - 3;
    const yMax = Math.max(20, ...yValues) + 3;
    const x = (value) => margin.left + (Math.max(0, Math.min(value, xMax)) / xMax) * (width - margin.left - margin.right);
    const y = (value) => height - margin.bottom - ((value - yMin) / Math.max(yMax - yMin, 1)) * (height - margin.top - margin.bottom);
    const yTicks = Array.from({ length: 5 }, (_, i) => yMin + ((yMax - yMin) * i) / 4);
    const xTicks = [0, 25, 50, 75, 100].filter((tick) => tick <= xMax);
    const axis = `
      ${yTicks.map((tick) => `<line x1="${margin.left}" x2="${width - margin.right}" y1="${y(tick).toFixed(2)}" y2="${y(tick).toFixed(2)}" stroke="#edf2f7" /><text x="${margin.left - 10}" y="${(y(tick) + 4).toFixed(2)}" text-anchor="end" font-size="11" fill="#667085">${tick.toFixed(1)}%</text>`).join("")}
      ${xTicks.map((tick) => `<line x1="${x(tick).toFixed(2)}" x2="${x(tick).toFixed(2)}" y1="${margin.top}" y2="${height - margin.bottom}" stroke="#f2f4f7" /><text x="${x(tick).toFixed(2)}" y="${height - 18}" text-anchor="middle" font-size="11" fill="#667085">${tick}%</text>`).join("")}
      <line x1="${x(50).toFixed(2)}" x2="${x(50).toFixed(2)}" y1="${margin.top}" y2="${height - margin.bottom}" stroke="#d92d20" stroke-dasharray="5 5" />
      <line x1="${margin.left}" x2="${width - margin.right}" y1="${y(0).toFixed(2)}" y2="${y(0).toFixed(2)}" stroke="#98a2b3" stroke-dasharray="4 4" />
      <text x="${x(50) + 6}" y="${margin.top + 14}" font-size="11" fill="#b42318" font-weight="750">50%暴露阈值</text>
      <text x="${width / 2}" y="${height - 2}" text-anchor="middle" font-size="12" fill="#475467">AI核心均值暴露</text>
      <text x="16" y="${height / 2}" text-anchor="middle" transform="rotate(-90 16 ${height / 2})" font-size="12" fill="#475467">近1年收益</text>`;
    const circles = rows.map((row, index) => {
      const selected = selectedIds.has(row.统一策略ID);
      const peak = Number(row.AI核心峰值暴露) || 0;
      const radius = selected ? 5.8 + Math.min(peak, 100) / 30 : 3.6;
      const color = selected ? colorById[row.统一策略ID] : "#cbd5e1";
      const cx = x(Number(row.AI核心均值暴露) || 0);
      const cy = y(Number(row.近1年收益) || 0);
      return `<circle class="topic-scatter-point ${selected ? "is-selected" : "is-background"}" data-topic-point="${index}" cx="${cx.toFixed(2)}" cy="${cy.toFixed(2)}" r="${radius.toFixed(2)}" fill="${color}"><title>${esc(row.策略名称)} 均值${pct(row.AI核心均值暴露)} 近1年${pct(row.近1年收益)}</title></circle>`;
    }).join("");
    const labels = (theme.selected || []).map((row) => {
      const cx = x(Number(row.AI核心均值暴露) || 0);
      const cy = y(Number(row.近1年收益) || 0);
      return `<text class="topic-scatter-label" x="${Math.min(cx + 8, width - 130).toFixed(2)}" y="${(cy - 7).toFixed(2)}" fill="${colorById[row.统一策略ID] || "#d92d20"}">${esc(row.策略名称)}</text>`;
    }).join("");
    return `
      <div class="topic-scatter-wrap">
        <svg viewBox="0 0 ${width} ${height}" role="img" aria-label="策略点阵图：AI核心暴露与近1年收益">${axis}${circles}${labels}</svg>
        <div class="topic-scatter-legend">
          <span><i class="is-selected"></i>入选策略</span>
          <span><i class="is-background"></i>全市场可比样本</span>
          <span>点大小按AI核心峰值暴露调整</span>
        </div>
        <div id="topicScatterDetail" class="topic-point-detail">${renderPointDetail(theme.selected?.[0] || rows[0])}</div>
      </div>`;
  }

  function renderPointDetail(row) {
    if (!row) return '<div class="empty">点击点阵中的策略查看明细</div>';
    const funds = (row.主要AI核心基金 || []).slice(0, 6).map(fundLink).join("");
    return `
      <strong>${strategyLink(row)}</strong>
      <div class="topic-point-kpis">
        <span>${label("投顾机构")}<b>${esc(row.投顾机构 || "未披露")}</b></span>
        <span>${label("近1年收益")}<b>${signedPct(row.近1年收益)}</b></span>
        <span>${label("最大回撤")}<b>${valueHtml("最大回撤", row.最大回撤)}</b></span>
        <span>${label("AI核心均值暴露")}<b>${pct(row.AI核心均值暴露)}</b></span>
        <span>${label("AI核心峰值暴露")}<b>${pct(row.AI核心峰值暴露)}</b></span>
        <span>${label("峰值日期")}<b>${esc(row.峰值日期 || "未披露")}</b></span>
      </div>
      <div class="topic-fund-chip-row">${funds || '<span class="value-muted">暂无AI核心基金证据</span>'}</div>`;
  }

  function renderSelectedTable(theme) {
    const rows = theme.selected || [];
    const body = rows.length ? rows.map((row) => `
      <tr>
        <td>${strategyLink(row)}</td>
        <td>${esc(row.投顾机构 || "未披露")}</td>
        <td>${esc(row.风险等级 || "未披露")}</td>
        <td>${signedPct(row.近1年收益)}</td>
        <td>${valueHtml("最大回撤", row.最大回撤)}</td>
        <td>${pct(row.AI核心均值暴露)}</td>
        <td>${pct(row.AI核心峰值暴露)}</td>
        <td>${pct(row.当前AI核心暴露)}</td>
        <td>${esc(row.峰值日期 || "未披露")}</td>
        <td><div class="topic-fund-chip-row">${(row.主要AI核心基金 || []).slice(0, 5).map(fundLink).join("") || '<span class="value-muted">暂无基金证据</span>'}</div></td>
      </tr>`).join("") : '<tr><td colspan="10"><div class="empty">暂无入选策略</div></td></tr>';
    return `
      <div class="table-wrap topic-selected-table">
        <table>
          <thead>
            <tr>
              <th>${label("策略名称")}</th>
              <th>${label("投顾机构")}</th>
              <th>${label("风险等级")}</th>
              <th>${label("近1年收益")}</th>
              <th>${label("最大回撤")}</th>
              <th>${label("AI核心均值暴露")}</th>
              <th>${label("AI核心峰值暴露")}</th>
              <th>${label("当前AI核心暴露")}</th>
              <th>${label("峰值日期")}</th>
              <th>${label("主要AI核心基金")}</th>
            </tr>
          </thead>
          <tbody>${body}</tbody>
        </table>
      </div>`;
  }

  function renderLogic(theme) {
    const logic = theme.logic || {};
    const keywords = (logic.核心关键词 || []).map((item) => `<span>${esc(item)}</span>`).join("");
    return `
      <section class="panel topic-method-panel">
        <div class="panel-head">
          <div>
            <h2>AI核心暴露技术逻辑</h2>
            <p class="desc">本页按最近一年策略持仓和调仓快照动态计算；底层数据更新后，重新执行报表导出即可联动刷新。</p>
          </div>
          <span class="topic-threshold">${esc(theme.threshold || "")}</span>
        </div>
        <div class="topic-logic-grid">
          <div><strong>基金识别</strong><p>${esc(logic.基金识别 || "")}</p></div>
          <div><strong>暴露计算</strong><p>${esc(logic.暴露计算 || "")}</p></div>
          <div><strong>排除宽口径</strong><p>${esc(logic.排除宽口径 || "")}</p></div>
        </div>
        <div class="topic-keyword-row">${keywords}</div>
      </section>`;
  }

  function render(theme) {
    const summary = theme.summary || {};
    root.innerHTML = `
      <div class="page-title">
        <div>
          <h1>主题分析</h1>
          <p class="desc">独立跟踪市场主题暴露、相关策略表现和可回溯基金证据。当前主题：${esc(theme.name || "AI核心行情")}。</p>
        </div>
        <div class="title-pills">
          <span class="pill">数据更新至 ${esc(pack.dataUpdatedTo || "-")}</span>
          <span class="pill">窗口 ${esc(pack.window?.start || "-")} 至 ${esc(pack.window?.end || "-")}</span>
        </div>
      </div>
      <div class="grid topic-kpi-grid">
        ${B.metric ? B.metric("入选策略数", summary.入选策略数 ?? 0, "均值或峰值满足严格AI核心暴露阈值") : ""}
        ${B.metric ? B.metric("均值达标策略数", summary.均值达标策略数 ?? 0, "最近一年时间加权均值>=50%") : ""}
        ${B.metric ? B.metric("峰值达标策略数", summary.峰值达标策略数 ?? 0, "调仓/当前快照峰值>=50%") : ""}
        ${B.metric ? B.metric("AI核心基金数", summary.AI核心基金数 ?? 0, "入选策略主要证据基金去重") : ""}
      </div>
      ${renderLogic(theme)}
      <section class="panel">
        <div class="panel-head">
          <div>
            <h2>AI主题参考业绩走势</h2>
            <p class="desc">指数点位统一以窗口内首个可比交易日归一为100；用于观察入选策略是否同步吃到AI核心主题行情。</p>
          </div>
        </div>
        ${renderLineChart(theme.trend || [])}
      </section>
      <section class="panel">
        <div class="panel-head">
          <div>
            <h2>策略点阵图</h2>
            <p class="desc">横轴为最近一年AI核心均值暴露，纵轴为近1年收益；点击点位查看策略和命中基金证据。</p>
          </div>
        </div>
        ${renderScatter(theme)}
      </section>
      <section class="panel">
        <div class="panel-head">
          <div>
            <h2>入选策略AI核心暴露说明</h2>
            <p class="desc">严格口径：AI核心暴露均值或峰值达到50%；基金证据只展示命中AI核心关键词的高权重基金。</p>
          </div>
        </div>
        ${renderSelectedTable(theme)}
      </section>`;
    root.querySelectorAll("[data-topic-point]").forEach((node) => {
      node.addEventListener("click", () => {
        const row = theme.points?.[Number(node.dataset.topicPoint)];
        const detail = root.querySelector("#topicScatterDetail");
        if (detail) detail.innerHTML = renderPointDetail(row);
      });
    });
  }

  if (!root) return;
  const theme = pack.themes?.[0];
  if (!theme) {
    root.innerHTML = '<section class="panel"><div class="empty">主题分析数据包未生成。请先运行 scripts/build_topic_analysis_pack.py。</div></section>';
    return;
  }
  render(theme);
})();
