
window.__BASIC_DATA__ = window.__BASIC_DATA__ || { details: {} };
window.BasicData = (() => {
  const state = window.__BASIC_DATA__;
  const byId = (id) => document.getElementById(id);
  const esc = (value) => String(value ?? "").replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;").replaceAll("'", "&#39;");
  const INTERNAL_TEST_NOTICE = "所有数据为测试模拟数据，不构成任何投资意见，仅内部测试使用。";
  const PAGE_LOADING_TEXT = "数据正在加载，请稍等。";
  const strategyListFieldGroups = Object.freeze({
    returns: Object.freeze(["近一周", "近一月", "近三月", "近1年", "今年以来", "累计收益率"]),
    risks: Object.freeze(["最大回撤", "波动率"]),
    weights: Object.freeze(["权益基金权重", "债券基金权重", "货币基金权重", "QDII权重", "指数基金权重"]),
    dates: Object.freeze(["最新业绩日期", "最新持仓日", "最近调仓日"]),
    trailing: Object.freeze([
      "研报产品类型", "研报股票子类型", "业务分类", "市场地域", "主动被动",
      "披露策略类型", "天天当前对客展示", "基准可用状态",
    ]),
  });
  const strategyListInstitutionField = "销售渠道/管理机构";
  const strategyChannelDisplayNames = Object.freeze({ "天天基金/投顾": "天天基金" });
  const strategyListHeaders = Object.freeze([
    "策略名称", strategyListInstitutionField, "基准风险资产权重",
    ...strategyListFieldGroups.returns,
    ...strategyListFieldGroups.risks,
    "夏普比率", "风险等级", "业绩基准说明", "最新业绩日期", "天天展示状态",
    ...strategyListFieldGroups.weights,
    ...strategyListFieldGroups.dates.filter((field) => field !== "最新业绩日期"),
    "调仓次数",
    ...strategyListFieldGroups.trailing,
  ]);
  function strategyInstitutionText(row) {
    const rawChannel = String(row?.渠道 || "").trim();
    const channel = strategyChannelDisplayNames[rawChannel] || rawChannel || "未披露";
    const manager = String(row?.投顾机构 || "").trim() || "未披露";
    return `${channel}/${manager}`;
  }
  function strategyListHeaderLabel(field) {
    if (field === strategyListInstitutionField) return "销售渠道 / 管理机构";
    if (field === "业绩基准说明") return "业绩基准";
    return field === "天天当前对客展示" ? "对客展示" : label(field);
  }
  let noticeScheduled = false;
  const dict = () => state.summary?.fieldDictionary || {};
  const businessFieldDescriptions = {
    "入选策略数": "业务口径：AI核心仓位达到入选标准的策略数量。同一策略只计算一次，用于判断当前主题下可进入重点观察池的产品规模。",
    "均值达标策略数": "业务口径：最近一年平均AI核心仓位达到50%的策略数量。该指标更看重持续配置，而不是短期单次持有。",
    "峰值达标策略数": "业务口径：最近一年曾在任一持仓快照中AI核心仓位达到50%的策略数量。该指标用于发现阶段性重仓AI主题的策略。",
    "AI核心基金数": "业务口径：入选策略中贡献AI核心仓位的底层基金去重数量。用于判断入选结果是由少数基金集中贡献，还是覆盖了更多基金工具。",
    "标准实体AI基金数": "业务口径：底层基金已被标准主题实体识别为AI核心相关的基金数量。只统计有明确主题证据的基金。",
    "点阵样本数": "业务口径：当前点阵图中可比较的策略数量。样本需要具备收益、回撤和AI核心暴露等必要指标。",
    "AI核心均值暴露": "业务口径：策略在观察期内平均持有AI核心相关基金的仓位比例。数值越高，代表策略对AI主题的持续配置越强。",
    "AI核心峰值暴露": "业务口径：策略在观察期内AI核心相关基金仓位达到过的最高比例。用于识别阶段性重仓AI主题的策略。",
    "当前AI核心暴露": "业务口径：策略最新持仓中AI核心相关基金的仓位比例。用于观察策略当前是否仍在配置AI主题。",
    "峰值日期": "业务口径：策略AI核心仓位达到观察期最高值的日期。用于判断重仓AI主题发生在近期还是历史阶段。",
    "主要AI核心基金": "业务口径：对策略AI核心仓位贡献较高的底层基金。展示这些基金是为了说明策略为什么被纳入AI主题观察池。",
  };
  const collectedFieldNames = new Set([
    "数据更新至", "数据刷新时间", "渠道", "渠道类型", "策略数", "策略名称", "投顾机构", "策略类型", "风险等级",
    "成立日期", "运作状态", "策略代码", "统一策略ID", "起投金额", "投顾费率", "建议持有时长", "业绩基准",
    "标签", "策略概念", "策略描述", "业绩基准说明", "官方单位净值", "官方累计收益", "最新业绩日期", "收益数据截至",
    "最新业绩日", "最新调仓日", "最新持仓日", "最近调仓日", "基金代码", "基金名称", "资产类型", "分组",
    "基金净值", "净值日期", "日涨幅", "调仓日期", "披露日期", "调仓标题", "调前权重", "调后权重",
    "调仓基金数", "调后权重和", "投顾费率", "业绩基准", "原始数据来源", "曲线来源", "披露业绩"
  ]);
  const derivedFieldNames = new Set([
    "数据来源标记", "接入渠道数", "策略总数", "天天策略数", "有历史调仓策略数", "有官方业绩策略数", "纳入回放策略数",
    "策略基金净值缺失数", "完整策略数", "官方业绩覆盖", "历史调仓覆盖", "当前持仓覆盖", "回放覆盖",
    "主可比池", "市场地域", "主动被动", "特殊标签", "策略实现标签", "权益基金权重", "债券基金权重",
    "货币基金权重", "混合基金权重", "QDII权重", "指数基金权重", "主动基金权重", "基准权益权重",
    "基准债券权重", "基准货币权重", "基准风险资产权重", "基准风险资产权重_百分比", "基准风险资产权重说明", "权益中枢", "固收中枢", "基准风险资产中枢", "海外配置中枢", "指数化程度", "主动管理程度", "风险资产偏离", "配置风格标签", "基准可用状态", "基础数据等级", "费率状态", "年化投顾费率", "分类依据",
    "运作天数", "数据完整性", "质检情况", "稽核结论", "近一周", "近一月", "近三月", "近6月", "近1年", "今年以来",
    "累计收益率", "自建累计收益", "与官方偏差", "最大回撤", "当前回撤", "年化收益", "波动率", "夏普比率",
    "单次平均换手率", "年化换手率", "调仓频率", "最近一年调仓次数", "官方对比口径", "可比记录数",
    "持仓来源", "持仓基金数", "权重", "上次调仓后权重", "权重变化", "调仓动作", "调仓基金数", "调后权重和", "区间", "年度",
    "策略收益", "基准收益", "模拟业绩", "基准业绩", "沪深300业绩", "基准公式解析", "调仓后收益率",
    "调仓后收益贡献", "调仓贡献曲线", "曲线数据提示"
    , "洞察评价对象", "全市场完整策略", "广发基金投顾完整策略", "市场覆盖率", "覆盖可比池数", "头部策略数", "中位差",
    "机会评分", "头部差距", "排名分位", "复盘建议", "数据洞察来源", "市场样本数", "广发样本数", "广发中位数",
    "市场中位数", "广发最佳", "广发中位回撤", "市场中位回撤", "所选收益", "池中位收益", "诊断分数",
    "基础数据A级占比", "仅曲线基准占比", "费率缺失占比", "风险未披露占比", "费率状态", "年化投顾费率",
    "覆盖策略类型数", "筛选口径", "分类口径", "广发覆盖率", "广发中位收益", "市场中位收益", "中位回撤",
    "中位波动", "高换手策略数", "高波动策略数", "风险收益象限", "广发基金投顾中位收益", "全市场中位收益",
    "广发基金投顾中位回撤", "广发基金投顾中位波动率", "最新调仓日期", "近一周主动调仓", "近一月主动调仓",
    "近一月广发调仓", "近一月中位换手率", "近一年平均调仓超额", "调仓逻辑", "事件数", "机构数",
    "可评价事件数", "市场调仓胜率", "广发调仓胜率", "标杆机构胜率", "胜率差距", "调仓质量结论", "调仓质量风险", "调仓质量建议",
    "中位单次换手率", "平均调仓超额", "示例原因", "平均单次换手率", "调仓胜率", "主要逻辑",
    "广发基金投顾完整策略", "广发覆盖率", "广发头部产品数", "广发Top3平均收益", "广发Top5平均收益",
    "市场Top3平均收益", "市场Top5平均收益", "广发Top3差距", "广发Top3对Top5差距", "头部达标数",
    "广发Top3平均排名", "广发Top3产品", "产品对比", "相对标杆", "深层结论", "业务风险", "业务动作",
    "广发样本数", "广发覆盖率", "广发中位收益", "广发Top3均值", "市场Top3均值", "标杆差距",
    "标杆产品", "标杆机构", "维度结论", "机会风险", "建议"
    , "经营重点", "经营判断", "业务重点", "重点经营", "头部可包装", "需要复盘", "产品补齐", "暂不主推", "重点名单"
    , "近一周经营总览", "事实", "观点", "逻辑", "动作", "近一周全市场中位收益", "近一周广发中位收益",
    "近一周广发相对差", "近一周上涨占比", "近一周广发Top5", "近一周市场Top5", "广发周度位置",
    "周度业务动作", "平台对标参考", "公开资料", "全市场事件数", "广发事件数", "近一周市场Top5均值"
  ]);
  const derivedFieldHints = ["覆盖", "完整", "归属", "分类", "权重", "收益", "回撤", "波动", "夏普", "换手", "频率", "次数", "偏差", "贡献", "解析", "可比", "中枢", "中位", "占比", "评分", "分位", "样本", "差距", "逻辑", "胜率", "Top", "标杆", "结论", "风险", "动作"];
  function isDerivedField(field) {
    const text = String(field || "");
    if (derivedFieldNames.has(text)) return true;
    if (collectedFieldNames.has(text)) return false;
    return derivedFieldHints.some((word) => text.includes(word));
  }
  function fieldSourceText(field) {
    const text = String(field || "");
    if (text.includes("基金") || text.includes("资产") || text.includes("行业")) {
      return "业务说明：用于观察策略底层配置到哪些基金、资产类别或行业主题，以及这些配置在当前筛选范围内的占比和集中度。";
    }
    if (text.includes("调仓") || text.includes("调前") || text.includes("调后") || text.includes("净增配")) {
      return "业务说明：用于观察策略在指定时间窗口内买入、卖出或调整仓位的方向和力度，帮助判断产品经理或投顾组合的配置变化。";
    }
    if (text.includes("收益") || text.includes("净值") || text.includes("回撤") || text.includes("波动") || text.includes("夏普")) {
      return "业务说明：用于评价策略或基金的收益风险表现。收益反映观察期涨跌幅，回撤反映阶段内最大承受损失，波动和夏普用于辅助判断收益稳定性。";
    }
    if (text.includes("持仓") || text.includes("仓位") || text.includes("权重")) {
      return "业务说明：用于观察策略当前或历史配置强度。权重越高，代表该策略对相应基金、资产或主题的配置越集中。";
    }
    return "业务说明：用于当前页面的筛选、排序、对比或概览展示。具体含义以字段名称、所在页面和当前筛选条件为准。";
  }
  function fallbackFieldDescription(field) {
    const text = String(field || "");
    if (text.includes("基金分类依据")) {
      return "计算口径：展示单只基金归类时命中的证据链。优先取基金代码/名称标准字典，其次取平台持仓披露的资产类型或分组，再用基金名称、跟踪指数、QDII/ETF/FOF/REIT/黄金/商品/短债/纯债/可转债等关键词兜底。该字段用于解释为什么基金被归入当前基金类型。";
    }
    if (text.includes("基金分类来源")) {
      return "计算口径：说明该基金分类和暴露字段的来源。主业务口径统一使用基金经济暴露快照；东财F10季报、基金标准分类、名称/指数规则和人工补充规则会合并成可审计的穿透方法与质量状态。";
    }
    if (text.includes("基金穿透报告期")) {
      return "计算口径：当前基金经济暴露使用的报告期。历史快照会优先选择报告期不晚于持仓日期的数据，避免用未来报告期回填历史。";
    }
    if (text.includes("基金穿透覆盖状态")) {
      return "计算口径：exact_quarterly_asset_and_stock 表示已有季报资产配置和股票持仓行业推导；exact_quarterly_asset_only 表示仅有季报资产配置；空值表示走规则估算兜底。";
    }
    if (text.includes("资产暴露") || text.includes("研报大类资产")) {
      return "计算口径：页面资产配置统一使用基金经济暴露快照。先保留季报原始资产配置，再按基金标准分类、名称、跟踪指数、ETF联接、FOF、QDII、黄金、固收指数等规则，把基金/其他高占比重映射为可解释资产。图表权重=sum(策略基金权重*基金经济资产暴露比例)。";
    }
    if (text.includes("行业暴露") || text.includes("研报A股行业")) {
      return "计算口径：页面行业/主题配置统一使用基金经济暴露快照。权益、海外权益、行业主题和指数权益优先使用季报股票持仓及东财股票行业映射；缺少完整股票行业时用主题、指数或名称规则标注质量状态。黄金/商品、纯债、货币、海外债券不适用股票行业穿透。";
    }
    if (text.includes("行业主题") || text.includes("行业大类") || text.includes("权益行业")) {
      return "计算口径：按基金经济资产暴露和经济行业暴露继续归并。非权益资产归入现金管理、纯债/固收、海外债券、贵金属、能源商品等；A股行业优先由季报股票持仓行业推导，再映射到科技制造、消费医药、金融周期等上层主题。";
    }
    if (text.includes("权重占比") || text.includes("占比")) {
      return "计算口径：当前筛选范围内，本行对象的权重合计除以同口径全部对象权重合计。用于基金、基金公司、资产大类、行业主题等聚合项时，先在单只策略内按基金权重或拆分暴露求和，再跨策略汇总。";
    }
    if (text.includes("总权重") || text.includes("持仓权重合计")) {
      return "计算口径：当前筛选范围内持有该基金、公司、资产或行业的策略期末持仓比例求和。该值是跨策略合计点位，不代表任何单一组合的真实仓位。";
    }
    if (text.includes("广发策略权重")) {
      return "计算口径：只统计投顾机构归属为广发的策略，在当前筛选范围内对该基金、基金公司、资产或行业的期末持仓权重合计。";
    }
    if (text.includes("非广发策略权重") || text.includes("外部策略权重")) {
      return "计算口径：剔除广发投顾策略后，其余策略在当前筛选范围内对该基金、基金公司、资产或行业的期末持仓权重合计，用于观察外部策略是否认可该底层资产。";
    }
    if (text.includes("持仓策略数")) {
      return "计算口径：当前筛选范围内期末仍持有该基金、基金公司、资产或行业的去重策略数。同一策略在同一统计项下只计一次。";
    }
    if (text.includes("调仓策略数")) {
      return "计算口径：当前调仓窗口内，对该基金、基金公司、资产或行业发生有效权重变化的去重策略数。有效变化阈值为绝对净变化大于0.0001个百分点。";
    }
    if (text.includes("中位权重")) {
      return "计算口径：当前筛选范围内持有该对象的单个策略持仓比例中位数，表示典型策略的配置强度，不受极端大仓位策略过度影响。";
    }
    if (text.includes("区间收益率") || text.includes("近一周") || text.includes("近一月") || text.includes("近三月") || text.includes("今年以来")) {
      return "计算口径：取观察窗口起止日期附近最近可用净值，收益率=(期末净值/期初净值-1)*100%。策略优先使用官方披露净值，基金使用基金日度净值；窗口内缺少可比净值时显示未披露。";
    }
    if (text.includes("回撤")) {
      return "计算口径：基于清洗后的日度净值序列，逐日计算相对历史高点的跌幅；最大回撤取区间内最深跌幅，当前回撤取最新净值相对历史高点的跌幅。";
    }
    if (text.includes("波动")) {
      return "计算口径：用日收益率标准差按252个交易日年化，公式=std(日收益率)*sqrt(252)。样本不足或净值不连续时不参与正式比较。";
    }
    if (text.includes("夏普")) {
      return "计算口径：年化收益率/年化波动率，当前无风险收益率按0处理；波动率为0或样本不足时为空。";
    }
    if (text.includes("净增配") || text.includes("权重变化")) {
      return "计算口径：调后权重-调前权重。按基金、资产、行业聚合时，先在单只策略内汇总同类基金变化，再跨策略计算合计、中位数、增配策略数和减配策略数。";
    }
    if (text.includes("加仓权重")) {
      return "计算口径：当前窗口内所有正向权重变化的合计，只统计买入或增配部分，不与减仓抵消。";
    }
    if (text.includes("减仓权重")) {
      return "计算口径：当前窗口内所有负向权重变化绝对值的合计，只统计卖出或减配部分，不与加仓抵消。";
    }
    if (text.includes("调仓强度") || text.includes("换手")) {
      return "计算口径：一次调仓中买入与卖出权重变化绝对值的综合强度。单次换手率通常按sum(abs(权重变化))/2估算，年度指标再按策略运作时间折算。";
    }
    if (text.includes("胜率")) {
      return "计算口径：只统计调仓后观察窗口已经结束且可评价的事件，胜率=正向事件数/可评价事件数*100%。未到观察窗口或缺少可比收益的事件不进入分母。";
    }
    if (text.includes("贡献")) {
      return "计算口径：把调后持仓权重与后续区间收益结合估算，近似为调后权重*调仓后收益率/100，用于比较调仓后单只基金或资产对组合收益的影响。";
    }
    if (text.includes("排名") || text.includes("分位") || text.includes("Top")) {
      return "计算口径：在当前筛选后的同一可比池内重排。排序字段随页面选择变化；分位数按同池策略或同类产品的相对位置计算，不跨产品类型混排。";
    }
    if (text.includes("数量") || text.endsWith("数") || text.includes("事件数") || text.includes("样本数")) {
      return "计算口径：按当前筛选范围去重计数。策略按统一策略ID去重，基金按基金代码去重，调仓事件按调仓事件ID去重；明细行数只在明确写作记录数时使用。";
    }
    if (text.includes("日期") || text.endsWith("日")) {
      return "计算口径：取该对象在对应业务表中的最大可用日期。策略业绩看最新业绩日，持仓看最新持仓日或推算持仓日，调仓看最新调仓日，基金净值看最新交易日期。";
    }
    return "业务口径：该指标用于当前页面的筛选、排序或展示。具体含义以字段名称、所在页面标题和当前筛选条件为准；涉及收益时通常表示观察期内涨跌幅，涉及回撤时表示相对阶段高点的下跌幅度，涉及数量时通常按策略、基金或事件去重统计。";
  }
  function showInfoModal(title, body) {
    byId("fieldModalTitle").textContent = title;
    byId("fieldModalBody").textContent = body;
    byId("fieldModalBody").scrollTop = 0;
    byId("fieldModalBody").scrollLeft = 0;
    byId("fieldModal").hidden = false;
  }
  function showHtmlModal(title, html) {
    byId("fieldModalTitle").textContent = title;
    byId("fieldModalBody").innerHTML = html;
    byId("fieldModalBody").scrollTop = 0;
    byId("fieldModalBody").scrollLeft = 0;
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
  const returnFieldNames = new Set(["官方累计收益", "自建累计收益", "与官方偏差", "最大回撤", "当前回撤", "年化收益", "波动率", "权重变化", "调仓后收益率", "调仓后收益贡献", "近一周", "近一月", "近三月", "近6月", "近1年", "今年以来", "累计收益率", "策略收益", "基准收益", "基准业绩", "调仓超额"]);
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
    const fieldName = String(name ?? "");
    const displayName = fieldName
      .replaceAll("广发基金投顾", "广发基金")
      .replaceAll("广发证券易淘金/财富管家", "广发证券")
      .replaceAll("广发证券易淘金/贝塔牛理财", "广发证券");
    const safe = esc(displayName);
    const safeField = esc(fieldName);
    const mark = isDerivedField(name) ? '<sup class="derived-star" title="基于基础数据加工">*</sup>' : "";
    return `<span class="field-label">${safe}${mark}<button class="info-button" type="button" data-field="${safeField}" title="查看字段口径">?</button></span>`;
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
  const GLOBAL_STRATEGY_FILTERS = {
    benchmark: { param: "hasBenchmark", label: "有基准" },
    performance: { param: "hasPerformance", label: "有业绩走势" },
    history: { param: "hasHistory", label: "有历史仓位" },
    active: { param: "clientActive", label: "对客未终止" },
  };
  const INSTITUTION_OVERVIEW_DEFAULT_FILTERS = Object.freeze({
    benchmark: true,
    performance: true,
    history: false,
    active: true,
  });
  function parseFilterFlag(value, fallback = true) {
    if (value === null || value === undefined || value === "") return fallback;
    return !["0", "false", "否", "no", "off"].includes(String(value).toLowerCase());
  }
  function isInstitutionOverviewPage() {
    return /(?:^|\/)institutions\.html$/i.test(window.location.pathname);
  }
  function initialGlobalStrategyFilters() {
    const query = params();
    // Institution overview starts from the business-ready scope while keeping
    // historical holdings optional. Other pages only inherit filters when an
    // institution link explicitly carries query parameters; top-navigation
    // visits must start from their own full data universe.
    return Object.fromEntries(Object.entries(GLOBAL_STRATEGY_FILTERS).map(([key, config]) => [
      key,
      query.has(config.param)
        ? parseFilterFlag(query.get(config.param), true)
        : isInstitutionOverviewPage() && Boolean(INSTITUTION_OVERVIEW_DEFAULT_FILTERS[key]),
    ]));
  }
  const hasExplicitGlobalStrategyFilters = Object.values(GLOBAL_STRATEGY_FILTERS)
    .some((config) => params().has(config.param));
  const globalStrategyFilters = initialGlobalStrategyFilters();
  function appendGlobalStrategyFilters(target) {
    Object.entries(GLOBAL_STRATEGY_FILTERS).forEach(([key, config]) => {
      target.searchParams.set(config.param, globalStrategyFilters[key] ? "1" : "0");
    });
    return target;
  }
  function setGlobalStrategyFilter(key, selected, { syncUrl = true } = {}) {
    if (!(key in GLOBAL_STRATEGY_FILTERS)) return;
    globalStrategyFilters[key] = Boolean(selected);
    if (syncUrl) {
      const target = appendGlobalStrategyFilters(new URL(window.location.href));
      window.history.replaceState({}, "", `${target.pathname}${target.search}${target.hash}`);
    }
  }
  function withGlobalStrategyFilters(href, extra = {}) {
    const target = appendGlobalStrategyFilters(new URL(href, window.location.href));
    Object.entries(extra).forEach(([key, value]) => {
      if (value === null || value === undefined || value === "") target.searchParams.delete(key);
      else target.searchParams.set(key, String(value));
    });
    return target.href;
  }
  function strategyFilterFacts(row) {
    const clean = (value) => String(value || "").trim();
    const stopped = Number(row?.是否历史接口留档 || 0) === 1
      || Number(row?.是否已停止 || 0) === 1
      || /已停止|已终止|已下架|已清盘|期满|已止盈|非对客或已结束|stopped/i.test(
        [row?.策略治理状态, row?.运作状态, row?.天天展示状态].filter(Boolean).join(" ")
      );
    return {
      benchmark: clean(row?.有基准) ? row.有基准 === "是" : Boolean(clean(row?.业绩基准 || row?.业绩基准说明)),
      performance: clean(row?.有业绩走势) ? row.有业绩走势 === "是" : Boolean(clean(row?.最新业绩日期 || row?.收益数据截至)),
      history: clean(row?.有历史仓位) ? row.有历史仓位 === "是" : Number(row?.完整历史仓位快照数 || row?.完整调仓后仓位数 || 0) > 0,
      active: clean(row?.对客未终止) ? row.对客未终止 === "是" : !stopped,
    };
  }
  function matchesGlobalStrategyFilters(row, filters = globalStrategyFilters) {
    const facts = strategyFilterFacts(row);
    return Object.entries(GLOBAL_STRATEGY_FILTERS).every(([key]) => !filters[key] || facts[key]);
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
  function showPageLoading(message = PAGE_LOADING_TEXT) {
    const root = document.querySelector("main") || document.body || document.documentElement;
    if (!root) return null;
    let el = byId("pageLoadingStatus");
    if (!el) {
      el = document.createElement("section");
      el.id = "pageLoadingStatus";
      el.className = "page-loading-status";
      el.setAttribute("role", "status");
      el.setAttribute("aria-live", "polite");
      root.prepend(el);
    }
    el.textContent = message || PAGE_LOADING_TEXT;
    el.hidden = false;
    return el;
  }
  function updatePageLoading(done = 0, total = 0, detail = "") {
    const progress = total ? `（${Math.min(done, total)}/${total}）` : "";
    const suffix = detail ? ` ${detail}` : "";
    return showPageLoading(`${PAGE_LOADING_TEXT}${progress}${suffix}`);
  }
  function hidePageLoading() {
    const el = byId("pageLoadingStatus");
    if (el) el.hidden = true;
  }
  function ensureInternalTestNotice() {
    const standardTitles = Array.from(document.querySelectorAll(".page-title h1, .title-block h1, .system-page-title h1"));
    const main = document.querySelector("main");
    const firstContent = main ? Array.from(main.children).find((child) => child.id !== "pageLoadingStatus" && !child.hidden) : null;
    const fallbackTitle = firstContent?.querySelector("h1, h2");
    const titles = standardTitles.length ? standardTitles : (fallbackTitle ? [fallbackTitle] : []);
    titles.forEach((title) => {
      const parent = title.parentElement;
      if (!parent || Array.from(parent.children).some((child) => child.classList?.contains("internal-test-notice"))) return;
      const notice = document.createElement("p");
      notice.className = "internal-test-notice";
      notice.textContent = INTERNAL_TEST_NOTICE;
      title.insertAdjacentElement("afterend", notice);
    });
  }
  function scheduleInternalTestNotice() {
    if (noticeScheduled) return;
    noticeScheduled = true;
    const run = () => {
      noticeScheduled = false;
      ensureInternalTestNotice();
    };
    if (typeof window.requestAnimationFrame === "function") window.requestAnimationFrame(run);
    else window.setTimeout(run, 0);
  }
  function installPageChromeObserver() {
    scheduleInternalTestNotice();
    if (typeof MutationObserver !== "function" || !document.body) return;
    const observer = new MutationObserver((mutations) => {
      const shouldCheck = mutations.some((mutation) => Array.from(mutation.addedNodes || []).some((node) => {
        if (node.nodeType !== 1) return false;
        return node.matches?.(".page-title, .title-block, .system-page-title, main > section, main > .panel")
          || node.querySelector?.(".page-title h1, .title-block h1, .system-page-title h1, h1, h2");
      }));
      if (shouldCheck) scheduleInternalTestNotice();
    });
    observer.observe(document.body, { childList: true, subtree: true });
  }
  const chartColors = {
    "披露业绩": "#B86B3E",
    "模拟业绩": "#4F7888",
    "基准业绩": "#3F7B56",
    "沪深300业绩": "#756B8B",
    "调仓前仓位模拟": "#A77A38",
    "调仓后仓位实际": "#4D8079"
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
      const allTimeValueMode = range === "all" ? String(payload?.allTimeValueMode || "") : "";
      const start = alreadyReturn ? null : rangeStartDate(points, range);
      const filtered = points
        .filter((p) => p.日期 && p.数值 !== null && p.数值 !== undefined && (!start || new Date(p.日期) >= start))
        .map((p) => ({ 日期: String(p.日期), 数值: Number(p.数值) }))
        .filter((p) => Number.isFinite(p.数值))
        .sort((a, b) => a.日期.localeCompare(b.日期));
      return { name, points: filtered, mode: alreadyReturn ? "return" : mode, allTimeValueMode };
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
      if (item.allTimeValueMode) {
        const rows = item.points
          .filter((point) => point.日期 >= commonStart && point.日期 <= commonEnd)
          .map((point) => {
            const value = item.allTimeValueMode === "unit_nav"
              ? (Number(point.数值) - 1) * 100
              : Number(point.数值);
            return Number.isFinite(value) ? { 日期: point.日期, 数值: value } : null;
          })
          .filter(Boolean);
        return [item.name, rows];
      }
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
        const exact = values.find((row) => row.日期 === nearestDate);
        return { name, value: exact?.数值, color: colorForSeries(name), disclosed: !!exact };
      });
      const disclosedRows = rows.filter((row) => row.disclosed && Number.isFinite(row.value));
      hoverLayer.setAttribute("visibility", "visible");
      hoverLine.setAttribute("x1", guideX.toFixed(1));
      hoverLine.setAttribute("x2", guideX.toFixed(1));
      hoverPoints.innerHTML = disclosedRows.map((row) => `<circle cx="${guideX.toFixed(1)}" cy="${yOf(row.value).toFixed(1)}" r="4" fill="#fff" stroke="${row.color}" stroke-width="2"/>`).join("");
      tip.innerHTML = `<strong>${esc(nearestDate)}</strong>${rows.map((row) => `<div class="chart-tip-row"><span><i class="chart-dot" style="background:${row.color}"></i>${esc(row.name)}</span>${row.disclosed && Number.isFinite(row.value) ? `<b class="${row.value >= 0 ? "ret-pos" : "ret-neg"}">${row.value.toFixed(2)}%</b>` : "<b>该日未披露</b>"}</div>`).join("")}`;
      tip.hidden = false;
      const hostRect = el.getBoundingClientRect();
      const localX = event.clientX - hostRect.left;
      const localY = event.clientY - hostRect.top;
      tip.style.left = `${Math.min(localX + 16, el.clientWidth - 250)}px`;
      tip.style.top = `${Math.max(10, localY - 20)}px`;
    });
    svg.addEventListener("mouseleave", () => { tip.hidden = true; hoverLayer.setAttribute("visibility", "hidden"); });
  }
  const qualityScopeMap = {
    overview: ["首页", "负责人总览", "内网静态部署", "全局"],
    strategies: ["策略列表", "排名样本"],
    insights: ["数据洞察", "仓位分析", "调仓分析", "基金调仓榜单"],
    compare: ["策略对比"],
    ai_topic: ["主题分析", "AI选策略"],
    topic: ["主题分析", "AI选策略"],
    target_profit: ["目标盈分析"],
    ai: ["AI选策略"],
    strategy_detail: ["策略详情", "策略列表", "排名样本"],
    fund_detail: ["基金详情", "仓位分析", "主题分析"],
    quality: []
  };
  function qualityPack() {
    return window.__BASIC_DATA_QUALITY_PACK__ || state.dataQualityPack || {};
  }
  function pageQualityIssues(pageKey = "") {
    const pack = qualityPack();
    const checks = pack.checks || [];
    const scopes = qualityScopeMap[pageKey] || [];
    return checks.filter((row) => {
      const status = row.状态 || row.status;
      if (status !== "warn" && status !== "error" && status !== "bad") return false;
      if (!scopes.length) return true;
      const impact = String(row.影响页面 || "");
      return scopes.some((scope) => impact.includes(scope));
    });
  }
  function renderGlobalQualityGate(pageKey = "", targetId = "globalQualityGate") {
    const el = byId(targetId);
    if (!el) return;
    el.hidden = true;
    el.innerHTML = "";
  }
  document.addEventListener("click", (event) => {
    const button = event.target.closest("[data-field]");
    if (!button) return;
    const field = button.getAttribute("data-field");
    showInfoModal(field, businessFieldDescriptions[field] || dict()[field] || fallbackFieldDescription(field));
  });
  document.addEventListener("click", (event) => {
    if (event.target.id === "fieldModal" || event.target.id === "fieldModalClose") {
      byId("fieldModal").hidden = true;
    }
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && byId("fieldModal")) byId("fieldModal").hidden = true;
  });
  showPageLoading();
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", installPageChromeObserver, { once: true });
  } else {
    installPageChromeObserver();
  }
  window.addEventListener("load", () => {
    hidePageLoading();
    scheduleInternalTestNotice();
  });
  return { state, byId, esc, fmt, pct, pctSigned, valueHtml, toneClass, statusBadge, label, table, valueList, metricValue, metric, params, loadScript, showPageLoading, updatePageLoading, hidePageLoading, ensureInternalTestNotice, drawReturnChart, isDerivedField, fieldSourceText, showInfoModal, showHtmlModal, pageQualityIssues, renderGlobalQualityGate, strategyListHeaders, strategyListFieldGroups, strategyListInstitutionField, strategyInstitutionText, strategyListHeaderLabel, globalStrategyFilters, globalStrategyFilterDefinitions: GLOBAL_STRATEGY_FILTERS, hasExplicitGlobalStrategyFilters, setGlobalStrategyFilter, withGlobalStrategyFilters, strategyFilterFacts, matchesGlobalStrategyFilters };
})();
