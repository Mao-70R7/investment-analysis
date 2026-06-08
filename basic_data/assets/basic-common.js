
window.__BASIC_DATA__ = window.__BASIC_DATA__ || { details: {} };
window.BasicData = (() => {
  const state = window.__BASIC_DATA__;
  const byId = (id) => document.getElementById(id);
  const esc = (value) => String(value ?? "").replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;").replaceAll("'", "&#39;");
  const dict = () => state.summary?.fieldDictionary || {};
  const collectedFieldNames = new Set([
    "数据更新至", "数据刷新时间", "渠道", "渠道类型", "策略数", "策略名称", "投顾机构", "披露策略类型", "披露风险等级",
    "成立日期", "运作状态", "策略代码", "统一策略ID", "起投金额", "投顾费率", "建议持有时长", "业绩基准",
    "标签", "策略概念", "策略描述", "业绩基准说明", "官方单位净值", "官方累计收益", "最新业绩日期", "收益数据截至",
    "最新业绩日", "最新调仓日", "最新持仓日", "最近调仓日", "基金代码", "基金名称", "资产类型", "分组",
    "基金净值", "净值日期", "日涨幅", "调仓日期", "披露日期", "调仓标题", "调前权重", "调后权重",
    "调仓基金数", "调后权重和", "投顾费率", "业绩基准", "原始数据来源", "曲线来源", "披露业绩"
  ]);
  const derivedFieldNames = new Set([
    "数据来源标记", "接入渠道数", "策略总数", "天天策略数", "有历史调仓策略数", "有官方业绩策略数", "纳入回放策略数",
    "策略基金净值缺失数", "完整策略数", "官方业绩覆盖", "历史调仓覆盖", "当前持仓覆盖", "回放覆盖",
    "风险等级", "业务分类", "业务组合分类", "市场地域", "主动被动", "特殊标签", "策略实现标签", "权益基金权重", "债券基金权重",
    "货币基金权重", "混合基金权重", "QDII权重", "指数基金权重", "主动基金权重", "基准权益权重",
    "基准债券权重", "基准货币权重", "基准可用状态", "基础数据等级", "费率状态", "年化投顾费率", "分类依据",
    "运作天数", "数据完整性", "质检情况", "稽核结论", "近一周", "近一月", "近三月", "近1年", "今年以来",
    "累计收益率", "自建累计收益", "与官方偏差", "最大回撤", "当前回撤", "年化收益", "波动率", "夏普比率",
    "单次平均换手率", "年化换手率", "调仓频率", "最近一年调仓次数", "官方对比口径", "可比记录数",
    "持仓来源", "持仓基金数", "权重", "上次调仓后权重", "权重变化", "调仓动作", "调仓基金数", "调后权重和", "区间", "年度",
    "策略收益", "基准收益", "模拟业绩", "基准业绩", "沪深300业绩", "基准公式解析", "调仓后收益率",
    "调仓后收益贡献", "调仓贡献曲线", "曲线数据提示"
    , "洞察评价对象", "全市场完整策略", "广发基金投顾完整策略", "市场覆盖率", "覆盖可比池数", "头部策略数", "中位差",
    "机会评分", "头部差距", "排名分位", "复盘建议", "数据洞察来源", "市场样本数", "广发样本数", "广发中位数",
    "市场中位数", "广发最佳", "广发中位回撤", "市场中位回撤", "所选收益", "池中位收益", "诊断分数",
    "基础数据A级占比", "仅曲线基准占比", "费率缺失占比", "风险未披露占比", "费率状态", "年化投顾费率",
    "覆盖业务分类数", "筛选口径", "分类口径", "广发覆盖率", "广发中位收益", "市场中位收益", "中位回撤",
    "中位波动", "高换手策略数", "高波动策略数", "风险收益象限", "广发基金投顾中位收益", "全市场中位收益",
    "广发基金投顾中位回撤", "广发基金投顾中位波动率", "最新调仓日期", "近一周主动调仓", "近一月主动调仓",
    "近一月广发调仓", "近一月中位换手率", "近一年平均调仓超额", "调仓逻辑", "事件数", "机构数",
    "可评价事件数", "市场调仓胜率", "广发调仓胜率", "标杆机构胜率", "胜率差距", "调仓质量结论", "调仓质量风险", "调仓质量建议",
    "中位单次换手率", "平均调仓超额", "示例原因", "平均单次换手率", "调仓胜率", "主要逻辑",
    "广发基金投顾完整策略", "广发覆盖率", "广发头部产品数", "广发Top3平均收益", "广发Top5平均收益",
    "市场Top3平均收益", "市场Top5平均收益", "广发Top3差距", "广发Top3对Top5差距", "头部达标数",
    "广发Top3平均排名", "广发Top3产品", "产品对比", "相对标杆", "深层结论", "业务风险", "业务动作",
    "广发样本数", "广发覆盖率", "广发中位收益", "广发Top3均值", "市场Top3均值", "标杆差距",
    "标杆产品", "标杆机构", "维度结论", "机会风险", "建议",
    "研报产品类型", "研报股票子类型", "研报大类资产", "研报A股行业", "产品数", "调仓产品数", "调仓覆盖率",
    "中位换手", "可评价胜率", "调仓超额", "主资产方向", "业务读法", "参与策略", "增/减策略", "典型变化",
    "累计净变化", "净变化", "净增配", "中位净增配", "非广发净增配", "广发净增配", "调仓策略", "调仓策略数",
    "调仓强度", "净方向", "主加仓资产", "主减仓资产", "方向集中度", "机会类型", "外部策略权重占比",
    "全策略权重占比", "中位权重", "外部持仓策略数", "外部增减策略数", "外部净增配中位数", "区间收益率",
    "增持策略数", "减持策略数", "持仓策略数", "初持仓比例", "期末持仓比例", "核验证据", "下一步", "负责人关注点", "经营门禁",
    "经营信号", "经营判断"
    , "经营重点", "经营判断", "业务重点", "重点经营", "头部可包装", "需要复盘", "产品补齐", "暂不主推", "重点名单"
    , "近一周经营总览", "事实", "观点", "逻辑", "动作", "近一周全市场中位收益", "近一周广发中位收益",
    "近一周广发相对差", "近一周上涨占比", "近一周广发Top5", "近一周市场Top5", "广发周度位置",
    "周度业务动作", "平台对标参考", "公开资料", "全市场事件数", "广发事件数", "近一周市场Top5均值"
  ]);
  const derivedFieldHints = ["覆盖", "完整", "归属", "分类", "权重", "收益", "回撤", "波动", "夏普", "换手", "频率", "次数", "偏差", "贡献", "解析", "可比", "中枢", "中位", "占比", "评分", "分位", "样本", "差距", "逻辑", "胜率", "Top", "标杆", "结论", "风险", "动作"];
  const specificFieldExplanations = {
    "研报产品类型": "调仓分析的主可比池。按当前持仓权益中枢和多元资产暴露归入纯债型、固收+型、股债混合型、股票型或多元配置型，用于避免不同风险收益特征的策略混在一起比较。",
    "研报股票子类型": "仅对股票型策略进一步拆分。优先根据QDII/海外、行业主题、指数工具、行业轮动或主动优选特征识别；非股票型策略通常为空。",
    "研报大类资产": "底层基金在调仓和仓位分析中的主资产归属，归并到A股、港股、美股、债券、黄金、货币及现金、海外债券、新兴市场、其他发达市场、海外REIT、其他商品等口径。",
    "研报A股行业": "只对名称或主题能够明确识别行业的A股基金归类；宽基指数、主动权益和均衡混合基金不强行穿透行业，因此行业图是覆盖样本口径，不代表全量股票持仓穿透。",
    "产品数": "当前筛选范围内、归入该研报产品类型或业务分类的去重策略数量；目标盈系列在经营视角会按系列归并，D0持仓缺失不进入洞察主图。",
    "调仓产品数": "当前观察窗口内至少有一条调仓事件的去重策略数量，用于判断该类型本期是否真的发生调仓活动。",
    "调仓覆盖率": "调仓产品数除以同类产品数。该比例只反映本期有多少策略发生调仓，不代表调仓质量。",
    "中位换手": "当前观察窗口内该类策略单次调仓换手率的中位数；比平均值更不容易被极端高换手事件扭曲。",
    "可评价胜率": "只在有明确胜负或结果评价的调仓事件中计算。尚未走完调仓后观察窗口的事件显示为待观察，但仍参与事件数、换手率、资产变化和基金流向统计。",
    "调仓超额": "调仓后观察期内策略收益相对评价口径的超额表现；缺少可比收益或结果评价时不参与胜率和超额统计。",
    "主资产方向": "按策略级资产变化识别。先把同一策略同一资产类型在窗口内的变化合并，再看增配/减配策略数和单策略中位变化，方向不集中时显示未形成强方向。",
    "业务读法": "把统计结果翻译成业务含义，例如能否进入深钻、是否仅作样本复盘、是否需要谨慎解读；它不是新增数据源。",
    "参与策略": "当前行统计中有有效资产变化记录的去重策略数量；一只策略在同一资产类型下只计算一次。",
    "增/减策略": "当前资产类型下净增配策略数和净减配策略数。用于判断方向是否由多数策略共同行为形成，而不是少数大额变化造成。",
    "典型变化": "当前资产类型下单策略净变化的中位数，单位为仓位百分点；用于代表典型策略的调仓幅度。",
    "累计净变化": "当前筛选范围内该资产或基金跨策略净增配百分点合计，只作辅助验证；不能单独当作市场方向结论。",
    "净变化": "期末权重减期初权重或区间调仓后权重减调仓前权重，单位为仓位百分点；按具体表格口径在策略、资产或基金层面汇总。",
    "净增配": "区间加仓权重扣减减仓权重后的净值，单位为仓位百分点。正值表示整体增配，负值表示整体减配。",
    "中位净增配": "对每只策略的净增配幅度取中位数，避免少数策略的大幅调仓掩盖多数策略的典型行为。",
    "非广发净增配": "只统计非广发投顾策略对该基金或基金公司的净增配，用于判断外部策略是否认可广发基金产品或某类底层产品。",
    "广发净增配": "只统计广发投顾策略自身的净增配。该指标用于区分内部配置贡献，不能直接当作外部营销机会。",
    "调仓策略": "当前行涉及发生调仓的去重策略数量；展示为数量时不重复计算同一策略的多只基金明细。",
    "调仓策略数": "当前基金、基金公司、资产类型或调仓逻辑下有调仓记录的去重策略数量；用于判断信号覆盖面。",
    "调仓强度": "加仓权重绝对值与减仓权重绝对值之和，单位为仓位百分点；数值越大表示该基金公司或资产方向被交易得越多。",
    "净方向": "根据净增配正负判断为增配、减配或接近平衡；接近平衡时不应解读为明确方向。",
    "主加仓资产": "该机构或基金公司在当前观察窗口内加仓权重最大的研报大类资产，用于概括其主要增配方向。",
    "主减仓资产": "该机构或基金公司在当前观察窗口内减仓权重最大的研报大类资产，用于概括其主要减配方向。",
    "方向集中度": "主加仓或主减仓方向的权重占该机构全部调仓强度的比例；比例越高说明调仓思路越集中。",
    "机会类型": "广发基金机会的业务归类。外部增配验证表示非广发策略也在增配；外部减配预警表示非广发策略净减配；内部配置为主表示主要来自广发自家策略。",
    "外部策略权重占比": "非广发投顾策略当前仓位中配置到该基金的权重占比，用于剔除广发自家策略配置带来的干扰。",
    "全策略权重占比": "当前筛选范围内全部投顾策略仓位中配置到该基金的权重占比，包含广发和非广发策略。",
    "中位权重": "持有该基金的策略中，单只策略期末持仓比例的中位数；用于判断它是少数策略重仓还是多策略小比例持有。",
    "外部持仓策略数": "当前持有该基金的非广发投顾策略数量，用于判断该广发基金是否被外部组合广泛采用。",
    "外部增减策略数": "非广发投顾策略中对该基金增持和减持的策略数量，按策略净变化统计。",
    "外部净增配中位数": "非广发投顾策略对该基金区间净增配幅度的中位数，单位为仓位百分点；比总点位更适合看外部典型行为。",
    "区间收益率": "按页面选择的时间区间计算该基金或策略的收益率；缺少可比净值时显示未披露。",
    "增持策略数": "当前窗口内对该基金、资产或行业净增配的策略数量。",
    "减持策略数": "当前窗口内对该基金、资产或行业净减配的策略数量。",
    "持仓策略数": "期末仍持有该基金或资产的去重策略数量；不是持仓明细行数。",
    "初持仓比例": "当前分析区间起点附近最近可用持仓快照中的策略持仓比例；不会把缺失月份当作0仓位。",
    "期末持仓比例": "当前分析区间终点附近最近可用持仓快照中的策略持仓比例；用于和初持仓比例比较变化。",
    "核验证据": "跳转到能够下钻核验该统计结论的策略列表或明细页；没有明细时说明该数字只来自源表或全局统计。",
    "下一步": "基于经营动作生成的业务处理建议，通常对应产品补齐、投研复盘、销售包装、营销素材或数据补采动作。",
    "负责人关注点": "该统计结果对投顾业务负责人的关键含义，用于提示应关注货架、竞品、底层基金、渠道或数据质量中的哪一类问题。",
    "经营门禁": "数据可用性和经营动作之间的业务门槛。阻断经营结论/先补数据表示样本不能进入主结论；销售前补齐或销售/披露前补齐表示同类判断可用，但销售材料、渠道话术、经理画像或适当性核验前必须补齐对应字段；可直接行动、投研可复盘、先补货架、先做产品决策、月度跟踪用于提示该经营动作当前能推进到哪一步。"
    ,"经营信号": "策略列表中的单策略经营标签。D0、数据不完整或最新持仓缺失会阻断经营结论，归为先补数据；非对客或历史期次归为仅作核验；对客且近1年收益不弱于同类中位、回撤不明显劣势归为可进候选；收益或回撤明显落后归为能力复盘；其余保留观察跟踪。",
    "经营判断": "经营信号的计算依据说明。先展示同一研报产品类型内的近1年收益差和最大回撤差；若费率、投资经理或披露风险缺失，会追加销售门禁提示，表示同类判断可用，但进入销售材料、渠道话术、经理画像或适当性核验前还需补齐对应字段。"
  };
  function isDerivedField(field) {
    const text = String(field || "");
    if (derivedFieldNames.has(text)) return true;
    if (collectedFieldNames.has(text)) return false;
    return derivedFieldHints.some((word) => text.includes(word));
  }
  function fieldExplanation(field) {
    const text = dict()[field];
    if (text) return text;
    const name = String(field || "");
    if (specificFieldExplanations[name]) return specificFieldExplanations[name];
    if (name.includes("占比")) return "按当前筛选范围内该项合计值除以同范围全部可比项合计值计算；切换风险等级、业务分类、市场地域、策略范围或投顾机构后会重新计算。";
    if (name.includes("中位")) return "取当前筛选范围内可用样本排序后的中间值；样本数为偶数时取中间两项的平均值。";
    if (name.includes("策略数") || name.includes("产品数") || name.includes("数量")) return "按当前筛选范围内满足条件的去重策略或产品数量统计；同一策略在同一统计维度下只计算一次。";
    if (name.includes("收益率") || name.includes("收益")) return "按当前页面选择的时间区间计算收益表现；缺少可比净值或披露曲线时显示未披露。";
    if (name.includes("权重")) return "按策略披露或推算的持仓比例计算，单位为百分比；汇总类指标会先在当前筛选范围内求和或取中位数。";
    if (name.includes("回撤")) return "根据收益曲线从阶段高点到后续低点的最大跌幅计算，数值越低代表该区间回撤压力越小。";
    if (name.includes("波动")) return "按收益序列的波动程度折算，数值越低代表该区间净值起伏越小。";
    if (name.includes("胜率")) return "在有明确胜负或结果评价的调仓事件中，正向事件数除以可评价事件数。";
    if (name.includes("排名")) return "在当前表格筛选范围内按所选排序字段重新排序后生成，筛选或排序变化后同步变化。";
    return "按当前页面筛选范围内可用样本汇总或展示；空值表示该项缺少可比样本或无法稳定计算。";
  }
  function showInfoModal(title, body) {
    byId("fieldModalTitle").textContent = title;
    byId("fieldModalBody").textContent = body;
    byId("fieldModal").hidden = false;
  }
  function fmt(value, suffix = "") {
    if (value === null || value === undefined || value === "") return "未披露";
    if (typeof value === "number") return Number.isInteger(value) ? value.toLocaleString("zh-CN") + suffix : value.toLocaleString("zh-CN", { maximumFractionDigits: 4 }) + suffix;
    return esc(value);
  }
  function pct(value) {
    if (value === null || value === undefined || Number.isNaN(Number(value))) return "未披露";
    return `${Number(value).toFixed(2)}%`;
  }
  function pctSigned(value) {
    if (value === null || value === undefined || Number.isNaN(Number(value))) return '<span class="small">未披露</span>';
    const number = Number(value);
    const cls = number > 0 ? "ret-pos" : number < 0 ? "ret-neg" : "ret-zero";
    return `<span class="${cls}">${number.toFixed(2)}%</span>`;
  }
  const returnFieldNames = new Set(["官方累计收益", "自建累计收益", "与官方偏差", "最大回撤", "当前回撤", "年化收益", "波动率", "权重变化", "调仓后收益率", "调仓后收益贡献", "近一周", "近一月", "近三月", "近1年", "今年以来", "累计收益率", "策略收益", "基准收益", "基准业绩", "调仓超额"]);
  const returnFieldHints = ["收益", "回撤", "波动", "偏差", "贡献", "涨幅", "超额"];
  function isReturnField(field) {
    return returnFieldNames.has(field) || returnFieldHints.some((word) => String(field).includes(word));
  }
  function toneClass(field, value) {
    if (!isReturnField(field) || Number.isNaN(Number(value))) return "";
    if (String(field).includes("回撤")) return Number(value) === 0 ? "is-zero" : "is-neg";
    const number = Number(value);
    if (number > 0) return "is-pos";
    if (number < 0) return "is-neg";
    return "is-zero";
  }
  function valueHtml(field, value) {
    if (value === null || value === undefined || value === "") return '<span class="value-muted">未披露</span>';
    if (!Number.isNaN(Number(value)) && String(field).includes("回撤")) {
      const number = Number(value);
      const cls = number === 0 ? "ret-zero" : "ret-neg";
      return `<span class="${cls}">${number.toFixed(2)}%</span>`;
    }
    if (!Number.isNaN(Number(value)) && (String(field).includes("权重") || String(field).includes("费率"))) return `<span class="value-em">${Number(value).toFixed(2)}%</span>`;
    if (!Number.isNaN(Number(value)) && isReturnField(field)) return pctSigned(value);
    if (field.includes("日期") || field.endsWith("日")) return `<span class="value-date">${fmt(value)}</span>`;
    if (field.includes("ID") || field.includes("代码")) return `<span class="value-code">${fmt(value)}</span>`;
    if (typeof value === "number") return `<span class="value-em">${fmt(value)}</span>`;
    return fmt(value);
  }
  function statusBadge(value) {
    const text = String(value || "不完整");
    const cls = text === "完整" ? "ok" : "bad";
    return `<span class="status-badge ${cls}">${esc(text)}</span>`;
  }
  function label(name) {
    const safe = esc(name);
    const mark = isDerivedField(name) ? '<sup class="derived-star" title="基于基础数据加工">*</sup>' : "";
    return `<span class="field-label">${safe}${mark}<button class="info-button" type="button" data-field="${safe}" title="查看字段口径">?</button></span>`;
  }
  function table(headers, rows, formatter) {
    const head = headers.map((item) => `<th>${label(item)}</th>`).join("");
    const body = rows.length ? rows.map((row) => `<tr>${headers.map((h) => `<td>${formatter ? formatter(row, h) : fmt(row[h])}</td>`).join("")}</tr>`).join("") : `<tr><td colspan="${headers.length}"><div class="empty">暂无数据</div></td></tr>`;
    return `<div class="table-wrap"><table><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table></div>`;
  }
  function valueList(rows) {
    return `<div class="value-list">${rows.map((row) => {
      const cls = ["value-row", toneClass(row.字段, row.值), row.字段 === "业绩基准说明" ? "benchmark-row" : ""].filter(Boolean).join(" ");
      return `<div class="${cls}"><strong>${label(row.字段)}</strong><span>${valueHtml(row.字段, row.值)}</span></div>`;
    }).join("")}</div>`;
  }
  function metricValue(labelName, value, formatter) {
    const html = formatter && !String(labelName).includes("回撤") ? formatter(value) : valueHtml(labelName, value);
    return `<div class="core-cell ${toneClass(labelName, value)}"><span>${label(labelName)}</span><strong>${html}</strong></div>`;
  }
  function metric(labelName, value, sub = "") {
    return `<section class="metric"><div>${label(labelName)}</div><div class="metric-value">${fmt(value)}</div>${sub ? `<div class="metric-sub">${esc(sub)}</div>` : ""}</section>`;
  }
  function params() {
    return new URLSearchParams(window.location.search);
  }
  function loadScript(src) {
    return new Promise((resolve, reject) => {
      const script = document.createElement("script");
      script.src = src;
      script.onload = resolve;
      script.onerror = () => reject(new Error(`加载失败：${src}`));
      document.head.appendChild(script);
    });
  }
  const chartColors = {
    "披露业绩": "#d32f2f",
    "模拟业绩": "#1565c0",
    "基准业绩": "#2e7d32",
    "沪深300业绩": "#6a1b9a",
    "调仓前仓位模拟": "#f57c00",
    "调仓后仓位实际": "#00897b"
  };
  function colorForSeries(name) {
    if (chartColors[name]) return chartColors[name];
    if (String(name).startsWith("全局基准")) return "#0f766e";
    return "#475467";
  }
  function returnFromBase(value, base, mode) {
    if (value === null || value === undefined || base === null || base === undefined) return null;
    if (mode === "return" || mode === "return_pct") {
      const denominator = 1 + Number(base) / 100;
      if (!denominator) return null;
      return ((1 + Number(value) / 100) / denominator - 1) * 100;
    }
    if (!Number(base)) return null;
    return (Number(value) / Number(base) - 1) * 100;
  }
  function rangeStartDate(points, range) {
    if (!points.length || range === "all") return null;
    const last = new Date(points[points.length - 1].日期);
    if (range === "ytd") return new Date(last.getFullYear(), 0, 1);
    const days = { "1y": 365, "6m": 183, "3m": 92, "1m": 31 }[range] || 0;
    return new Date(last.getTime() - days * 86400000);
  }
  function pointAtOrBefore(points, dateText) {
    let selected = null;
    for (const point of points) {
      if (String(point.日期) <= dateText) selected = point;
      else break;
    }
    return selected;
  }
  function transformSeries(seriesMap, range = "all", alreadyReturn = false, visibility = {}) {
    const raw = Object.entries(seriesMap || {}).filter(([name]) => visibility[name] !== false).map(([name, payload]) => {
      const points = Array.isArray(payload) ? payload : (payload?.points || []);
      const mode = payload?.模式 || points[0]?.模式 || "nav";
      const start = alreadyReturn ? null : rangeStartDate(points, range);
      const filtered = points
        .filter((p) => p.日期 && p.数值 !== null && p.数值 !== undefined && (!start || new Date(p.日期) >= start))
        .map((p) => ({ 日期: String(p.日期), 数值: Number(p.数值) }))
        .filter((p) => Number.isFinite(p.数值))
        .sort((a, b) => a.日期.localeCompare(b.日期));
      return { name, points: filtered, mode: alreadyReturn ? "return" : mode };
    }).filter((item) => item.points.length);
    if (!raw.length) return {};
    const commonStart = raw.map((item) => item.points[0].日期).sort().at(-1);
    const commonEnd = raw.map((item) => item.points[item.points.length - 1].日期).sort()[0];
    if (!commonStart || !commonEnd || commonStart > commonEnd) return {};
    const dateSet = new Set([commonStart, commonEnd]);
    raw.forEach((item) => item.points.forEach((point) => {
      if (point.日期 >= commonStart && point.日期 <= commonEnd) dateSet.add(point.日期);
    }));
    const dates = [...dateSet].sort();
    const entries = raw.map((item) => {
      const basePoint = pointAtOrBefore(item.points, commonStart) || item.points.find((point) => point.日期 >= commonStart);
      if (!basePoint) return [item.name, []];
      const rows = dates.map((date) => {
        const point = pointAtOrBefore(item.points, date) || item.points.find((candidate) => candidate.日期 >= date);
        if (!point) return null;
        const value = returnFromBase(point.数值, basePoint.数值, item.mode);
        return Number.isFinite(value) ? { 日期: date, 数值: value } : null;
      }).filter(Boolean);
      return [item.name, rows];
    });
    return Object.fromEntries(entries.filter(([, rows]) => rows.length));
  }
  function chartLegendHtml(names, visibility) {
    if (!names.length) return "";
    return `<div class="legend">${names.map((name) => {
      const checked = visibility[name] !== false ? "checked" : "";
      return `<label class="legend-item"><input class="legend-toggle" type="checkbox" data-series-name="${esc(name)}" ${checked}><i style="background:${colorForSeries(name)}"></i><span>${esc(name)}</span></label>`;
    }).join("")}</div>`;
  }
  function drawReturnChart(el, seriesMap, options = {}) {
    const range = options.range || "all";
    const names = Object.keys(seriesMap || {});
    const defaultVisibleSeries = Array.isArray(options.defaultVisibleSeries) ? new Set(options.defaultVisibleSeries) : null;
    const visibilityKey = `${names.join("|")}::${defaultVisibleSeries ? [...defaultVisibleSeries].join("|") : "all"}`;
    if (!el.__seriesVisibility || el.__seriesVisibilityKey !== visibilityKey) {
      el.__seriesVisibility = {};
      names.forEach((name) => { el.__seriesVisibility[name] = defaultVisibleSeries ? defaultVisibleSeries.has(name) : true; });
      el.__seriesVisibilityKey = visibilityKey;
    } else {
      names.forEach((name) => { if (!(name in el.__seriesVisibility)) el.__seriesVisibility[name] = defaultVisibleSeries ? defaultVisibleSeries.has(name) : true; });
    }
    Object.keys(el.__seriesVisibility).forEach((name) => { if (!names.includes(name)) delete el.__seriesVisibility[name]; });
    const transformed = transformSeries(seriesMap, range, !!options.alreadyReturn, el.__seriesVisibility);
    const series = Object.entries(transformed);
    if (!series.length) {
      el.innerHTML = `${chartLegendHtml(names, el.__seriesVisibility)}<div class="empty">暂无可绘制曲线</div>`;
      el.querySelectorAll(".legend-toggle").forEach((input) => {
        input.addEventListener("change", () => {
          el.__seriesVisibility[input.dataset.seriesName] = input.checked;
          drawReturnChart(el, seriesMap, options);
        });
      });
      return;
    }
    const width = Math.max(920, Math.round(el.getBoundingClientRect().width || el.clientWidth || 960));
    const height = options.height || 310;
    const pad = { left: 44, right: 8, top: 22, bottom: 46 };
    const allValues = series.flatMap(([, rows]) => rows.map((row) => row.数值));
    const allDates = [...new Set(series.flatMap(([, rows]) => rows.map((row) => row.日期)))].sort();
    const minDate = new Date(allDates[0]).getTime();
    const maxDate = new Date(allDates[allDates.length - 1]).getTime();
    let min = Math.min(...allValues), max = Math.max(...allValues);
    if (min === max) { min -= 1; max += 1; }
    const yPad = Math.max(0.3, (max - min) * 0.12);
    min -= yPad; max += yPad;
    const xOf = (dateText) => {
      const t = new Date(dateText).getTime();
      return pad.left + (maxDate === minDate ? 0 : (t - minDate) / (maxDate - minDate)) * (width - pad.left - pad.right);
    };
    const yOf = (value) => height - pad.bottom - ((value - min) / (max - min)) * (height - pad.top - pad.bottom);
    const tickCount = Math.min(6, allDates.length);
    const tickDates = tickCount <= 1 ? allDates : Array.from({ length: tickCount }, (_, index) => allDates[Math.round(index * (allDates.length - 1) / (tickCount - 1))]).filter((date, index, arr) => arr.indexOf(date) === index);
    const multiYearAxis = new Date(allDates[0]).getFullYear() !== new Date(allDates[allDates.length - 1]).getFullYear();
    const tickLabel = (date) => multiYearAxis ? date.slice(0, 7) : date.slice(5);
    const xTicks = tickDates.map((date) => {
      const x = xOf(date);
      return `<line class="tick-line" x1="${x}" y1="${pad.top}" x2="${x}" y2="${height - pad.bottom}"/><text class="axis-text" x="${x}" y="${height - 15}" text-anchor="middle">${esc(tickLabel(date))}</text>`;
    }).join("");
    const grid = [0, .25, .5, .75, 1].map((ratio) => {
      const y = pad.top + ratio * (height - pad.top - pad.bottom);
      const value = max - ratio * (max - min);
      return `<line x1="${pad.left}" y1="${y}" x2="${width - pad.right}" y2="${y}" stroke="#edf1f5"/><text class="axis-text" x="8" y="${y + 4}">${value.toFixed(1)}%</text>`;
    }).join("");
    const zero = min < 0 && max > 0 ? `<line x1="${pad.left}" y1="${yOf(0)}" x2="${width - pad.right}" y2="${yOf(0)}" stroke="#cbd5e1" stroke-dasharray="4 4"/>` : "";
    const paths = series.map(([name, rows]) => {
      const color = colorForSeries(name);
      const d = rows.map((row, i) => `${i ? "L" : "M"}${xOf(row.日期).toFixed(1)},${yOf(row.数值).toFixed(1)}`).join(" ");
      return `<path d="${d}" fill="none" stroke="${color}" stroke-width="2.4" stroke-linejoin="round" stroke-linecap="round"/>`;
    }).join("");
    const legend = chartLegendHtml(names, el.__seriesVisibility);
    el.innerHTML = `${legend}<svg viewBox="0 0 ${width} ${height}" role="img" aria-label="${esc(options.title || "收益曲线")}">${xTicks}${grid}${zero}<line x1="${pad.left}" y1="${height-pad.bottom}" x2="${width-pad.right}" y2="${height-pad.bottom}" stroke="#d0d7de"/>${paths}<g class="hover-layer" visibility="hidden"><line class="hover-line" x1="0" y1="${pad.top}" x2="0" y2="${height - pad.bottom}"/><g class="hover-points"></g></g></svg><div class="chart-tooltip" hidden></div>`;
    el.querySelectorAll(".legend-toggle").forEach((input) => {
      input.addEventListener("change", () => {
        el.__seriesVisibility[input.dataset.seriesName] = input.checked;
        drawReturnChart(el, seriesMap, options);
      });
    });
    const svg = el.querySelector("svg");
    const tip = el.querySelector(".chart-tooltip");
    const hoverLayer = svg.querySelector(".hover-layer");
    const hoverLine = svg.querySelector(".hover-line");
    const hoverPoints = svg.querySelector(".hover-points");
    svg.addEventListener("mousemove", (event) => {
      const rect = svg.getBoundingClientRect();
      const viewX = (event.clientX - rect.left) / rect.width * width;
      const nearestDate = allDates.reduce((best, date) => Math.abs(xOf(date) - viewX) < Math.abs(xOf(best) - viewX) ? date : best, allDates[0]);
      const guideX = xOf(nearestDate);
      const rows = series.map(([name, values]) => {
        let nearest = values.find((row) => row.日期 === nearestDate) || pointAtOrBefore(values, nearestDate) || values[0];
        return { name, value: nearest?.数值, color: colorForSeries(name) };
      }).filter((row) => Number.isFinite(row.value));
      hoverLayer.setAttribute("visibility", "visible");
      hoverLine.setAttribute("x1", guideX.toFixed(1));
      hoverLine.setAttribute("x2", guideX.toFixed(1));
      hoverPoints.innerHTML = rows.map((row) => `<circle cx="${guideX.toFixed(1)}" cy="${yOf(row.value).toFixed(1)}" r="4" fill="#fff" stroke="${row.color}" stroke-width="2"/>`).join("");
      tip.innerHTML = `<strong>${esc(nearestDate)}</strong>${rows.map((row) => `<div class="chart-tip-row"><span><i class="chart-dot" style="background:${row.color}"></i>${esc(row.name)}</span><b class="${row.value >= 0 ? "ret-pos" : "ret-neg"}">${row.value.toFixed(2)}%</b></div>`).join("")}`;
      tip.hidden = false;
      const hostRect = el.getBoundingClientRect();
      const localX = event.clientX - hostRect.left;
      const localY = event.clientY - hostRect.top;
      tip.style.left = `${Math.min(localX + 16, el.clientWidth - 250)}px`;
      tip.style.top = `${Math.max(10, localY - 20)}px`;
    });
    svg.addEventListener("mouseleave", () => { tip.hidden = true; hoverLayer.setAttribute("visibility", "hidden"); });
  }
  document.addEventListener("click", (event) => {
    const button = event.target.closest("[data-field]");
    if (!button) return;
    const field = button.getAttribute("data-field");
    showInfoModal(field, fieldExplanation(field));
  });
  document.addEventListener("click", (event) => {
    if (event.target.id === "fieldModal" || event.target.id === "fieldModalClose") {
      byId("fieldModal").hidden = true;
    }
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && byId("fieldModal")) byId("fieldModal").hidden = true;
  });
  return { state, byId, esc, fmt, pct, pctSigned, valueHtml, toneClass, statusBadge, label, table, valueList, metricValue, metric, params, loadScript, drawReturnChart, isDerivedField, fieldExplanation, showInfoModal };
})();
