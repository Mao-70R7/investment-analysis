(() => {
  const B = window.BasicData;
  const summary = B.state.summary || {};
  const root = B.byId("aiStrategyPage");
  if (!root) return;

  const allRows = summary.strategies || [];
  const holdingPack = window.__BASIC_HOLDING_SNAPSHOT_PACK__ || null;
  const semanticIndex = window.__AI_STRATEGY_SEMANTIC_INDEX__ || null;
  const topicPack = window.__BASIC_AI_TOPIC_EVIDENCE_PACK__ || window.__BASIC_TOPIC_ANALYSIS_PACK__ || null;
  const fundDetailPack = window.__BASIC_DATA__?.fundDetailPack || null;
  const modelConfigStorageKey = "aiStrategyModelConfigV5";
  const aiConfigFileDefault = Object.assign({}, window.__AI_STRATEGY_CONFIG__ || {});
  clearStoredModelConfig();
  const aiConfig = Object.assign({}, aiConfigFileDefault);
  window.__AI_STRATEGY_CONFIG__ = aiConfig;
  const builtInModelProfiles = {
    local: {
      label: "本地模型（同源代理）",
      provider: "same-origin-llm-proxy",
      baseUrl: "/llmapi/v1",
      endpoint: "/llmapi/v1/chat/completions",
      model: "deepseek-v4-flash-inner",
      timeoutMs: 45000,
      responseFormat: false,
      apiKey: "",
      headers: {},
    },
    codex: {
      label: "Codex桥接模型",
      provider: "codex-cli-local-proxy",
      baseUrl: "http://127.0.0.1:8787/v1",
      endpoint: "http://127.0.0.1:8787/v1/chat/completions",
      model: "gpt-5.4-mini",
      timeoutMs: 120000,
      responseFormat: true,
      apiKey: "",
      headers: {},
    },
  };
  let modelBackoffUntil = 0;
  const allowedReturnMetrics = new Set(["近一周", "近一月", "近三月", "近6月", "近1年", "今年以来", "累计收益率", "年化收益"]);
  const allowedReportTypes = new Set(["固收+型", "纯债型", "股票型", "多元配置型", "股债混合型", "海外/全球型", "主题/行业型", "现金管理型", "偏股配置型"]);
  const businessRoutingCatalog = Object.freeze([
    { id: "channel", label: "销售渠道", aliases: ["渠道", "销售渠道", "平台", "销售平台"], summary: "策略在哪个业务渠道展示或销售。", neighbors: ["strategy"], fields: ["渠道"] },
    { id: "advisor_organization", label: "投顾管理机构", aliases: ["投顾机构", "管理机构", "投顾管理人", "管理人"], summary: "负责管理或提供策略服务的标准机构。", neighbors: ["strategy"], fields: ["投顾机构"] },
    { id: "strategy_series", label: "策略系列", aliases: ["系列", "母策略", "子策略", "期次", "第几期"], summary: "同一品牌或机制下的母策略、子策略和期次集合。", neighbors: ["strategy_relationship", "strategy", "performance_observation"], fields: ["策略名称", "母策略名称", "策略关系类型", "官方业绩口径"] },
    { id: "strategy", label: "策略", aliases: ["策略", "产品", "组合", "投顾产品"], summary: "AI筛选的主业务对象，承载名称、分类、风险、状态和标签。", neighbors: ["strategy_governance", "performance_observation", "benchmark_specification", "holding_snapshot"], fields: ["策略名称", "渠道", "投顾机构", "披露策略类型", "披露风险等级", "研报产品类型", "研报股票子类型", "业务分类", "风险等级", "成立日期", "运作状态", "市场地域", "主动被动", "特殊标签", "策略实现标签", "业务组合分类"] },
    { id: "strategy_relationship", label: "策略关系", aliases: ["母子关系", "继承", "共享业绩", "共用基准"], summary: "已经验证的母子、期次和数据复用关系。", neighbors: ["strategy_series", "strategy", "performance_observation", "benchmark_specification"], fields: ["母策略名称", "策略关系类型", "官方业绩口径", "业绩基准继承口径"] },
    { id: "strategy_governance", label: "生命周期与治理", aliases: ["治理", "下架", "终止", "停止", "展示", "排名资格", "有效策略"], summary: "策略当前运作、展示、排名和异常处理状态。", neighbors: ["strategy", "business_data_quality"], fields: ["策略治理状态", "分析分组", "是否测试组合", "是否信号类组合", "是否目标盈期次", "是否已停止", "是否纳入常规排名", "仅列表展示", "是否单独分析", "业绩分析截止日期", "持仓处理方式", "调仓展示方式", "运作状态", "天天当前对客展示", "天天展示状态"] },
    { id: "fee_policy", label: "费率政策", aliases: ["费率", "投顾费", "服务费", "管理费"], summary: "策略当前披露的投顾费率及可比较状态。", neighbors: ["strategy"], fields: ["年化投顾费率", "费率状态"] },
    { id: "benchmark_specification", label: "业绩基准", aliases: ["基准", "业绩基准", "比较基准", "基准说明"], summary: "策略用于比较业绩的基准定义、说明和可用状态。", neighbors: ["benchmark_exposure_snapshot", "performance_observation", "risk_observation"], fields: ["业绩基准", "业绩基准说明", "基准可用状态", "基准结构类型", "基准风险资产权重", "非权益比较轨道", "正式可比池"] },
    { id: "benchmark_exposure_snapshot", label: "基准资产配置", aliases: ["基准配置", "基准权益", "基准债券", "基准风险资产", "风险资产权重"], summary: "基准中权益、债券、现金、商品、另类和地域资产的标准权重。", neighbors: ["benchmark_specification", "strategy_allocation_profile"], fields: ["基准权益权重", "基准债券权重", "基准货币权重", "基准风险资产权重", "基准风险资产权重_百分比", "基准港股权益权重", "基准海外权益权重", "基准资产大类-权益", "基准资产大类-债券", "基准资产大类-现金", "基准资产大类-商品", "基准资产大类-另类", "基准资产大类-其他"] },
    { id: "strategy_allocation_profile", label: "策略配置画像", aliases: ["权益中枢", "固收中枢", "配置画像", "配置风格", "指数化", "主动管理", "风险资产偏离"], summary: "由持仓、基准、风险和调仓组合形成的可组合配置特征。", neighbors: ["benchmark_exposure_snapshot", "holding_snapshot", "risk_observation"], fields: ["权益中枢", "固收中枢", "基准风险资产中枢", "海外配置中枢", "指数化程度", "主动管理程度", "风险资产偏离", "权益风险档", "波动风险档", "回撤风险档", "配置风格标签"] },
    { id: "performance_observation", label: "策略业绩", aliases: ["业绩", "收益", "回报", "净值", "累计收益", "区间收益"], summary: "策略在标准区间或当前截止日上的收益和净值表现。", neighbors: ["risk_observation", "benchmark_specification", "peer_ranking"], fields: ["近一周", "近一月", "近三月", "近6月", "近1年", "今年以来", "累计收益率", "年化收益", "官方单位净值", "官方累计收益", "自建累计收益", "与官方偏差", "最新业绩日期", "收益数据截至", "日涨跌幅"] },
    { id: "risk_observation", label: "风险指标", aliases: ["风险", "回撤", "最大回撤", "当前回撤", "波动", "夏普"], summary: "策略的回撤、波动、夏普和风险分档。", neighbors: ["performance_observation", "strategy_allocation_profile"], fields: ["最大回撤", "当前回撤", "波动率", "夏普比率", "风险等级", "风险数据截至", "权益风险档", "波动风险档", "回撤风险档", "风险触发指标"] },
    { id: "peer_ranking", label: "同类池与排名", aliases: ["同类", "排名", "前十", "前10%", "分位", "可比池"], summary: "策略进入正式可比池的资格和同类评价；当前宽表只支持可比池及资格，具体名次需底层排名事实。", neighbors: ["performance_observation", "benchmark_exposure_snapshot", "strategy_governance"], support: "partial", fields: ["正式可比池", "可比池样本资格", "可比池说明", "是否纳入常规排名"] },
    { id: "holding_snapshot", label: "策略持仓", aliases: ["持仓", "当前持仓", "最新持仓", "持有", "仓位"], summary: "策略在最新披露日的持仓快照和基金头寸。", neighbors: ["fund", "fund_classification", "fund_exposure_snapshot", "strategy_allocation_profile"], fields: ["最新持仓日", "持仓基金数", "权益基金权重", "债券基金权重", "货币基金权重", "混合基金权重", "QDII权重", "指数基金权重", "主动基金权重", "__holding_entity"] },
    { id: "rebalance_event", label: "调仓行为", aliases: ["调仓", "换手", "加仓", "减仓", "买入", "卖出", "调仓频率"], summary: "策略的调仓日期、次数、频率和换手统计；基金级历史动作需底层调仓明细。", neighbors: ["holding_snapshot", "fund"], support: "partial", fields: ["最近调仓日", "调仓次数", "单次平均换手率", "年化换手率", "调仓频率", "最近一年调仓次数"] },
    { id: "signal_event", label: "信号行为", aliases: ["信号", "买入信号", "卖出信号", "定投信号", "信号胜率"], summary: "信号策略的事件、指令数量和标准区间评价。", neighbors: ["fund", "performance_observation"], fields: ["是否信号类组合", "信号事件数", "最近信号日", "信号指令数", "买入指令数", "卖出指令数", "加仓指令数", "减仓指令数", "信号胜率_1月", "信号胜率_3月", "信号胜率_6月", "信号胜率_1年", "信号加权方向收益_1月", "信号加权方向收益_3月", "信号加权方向收益_6月", "信号加权方向收益_1年"] },
    { id: "fund", label: "基金", aliases: ["基金", "基金名称", "基金公司"], summary: "策略持仓、调仓或信号涉及的基金业务对象。", neighbors: ["holding_snapshot", "fund_classification", "fund_exposure_snapshot"], support: "partial", fields: ["__holding_entity"] },
    { id: "fund_classification", label: "基金分类", aliases: ["基金类型", "资产类型", "指数基金", "债券基金", "QDII", "ETF"], summary: "基金的资产、地域、主动被动、指数和产品形态分类。", neighbors: ["fund", "holding_snapshot"], fields: ["__holding_entity", "权益基金权重", "债券基金权重", "货币基金权重", "混合基金权重", "QDII权重", "指数基金权重", "主动基金权重"] },
    { id: "fund_exposure_snapshot", label: "基金经济暴露", aliases: ["行业暴露", "主题暴露", "地域暴露", "海外暴露", "黄金暴露", "AI主题"], summary: "基金及策略穿透后的资产、行业、主题和地域暴露。", neighbors: ["fund", "holding_snapshot"], fields: ["__holding_entity", "海外配置中枢", "QDII权重"] },
    { id: "business_data_quality", label: "业务数据完整性", aliases: ["数据完整", "数据缺失", "业绩完整", "业绩缺失", "缺业绩", "缺基准", "缺仓位", "数据质量"], summary: "从业务使用角度分别描述业绩、基准、持仓和综合数据是否足以筛选和分析。", neighbors: ["strategy_governance", "performance_observation", "benchmark_specification", "holding_snapshot"], fields: ["业绩完整", "业绩完整性", "业绩完整性说明", "数据完整性", "基础数据等级", "基准可用状态", "最新业绩日期", "最新持仓日", "质检情况"] },
  ]);
  const virtualFields = [
    { field: "__benchmark_text", label: "业绩基准文本" },
    { field: "__holding_entity", label: "最新持仓明细" },
    { field: "__source_any", label: "机构/渠道/策略名称" },
    { field: "__gf_any", label: "机构/渠道/策略名称命中" },
    { field: "__any_text", label: "全字段文本" },
    { field: "风险等级序号", label: "风险等级序号" },
    { field: "持仓实体判断", label: "持仓实体判断" },
    { field: "持仓实体权重", label: "持仓实体权重" },
    { field: "持仓实体证据", label: "持仓实体证据" },
    { field: "海外资产判断", label: "海外资产判断" },
    { field: "海外资产权重", label: "海外资产权重" },
    { field: "海外资产分类", label: "海外资产分类" },
    { field: "黄金判断", label: "黄金判断" },
  ];
  const fallbackSemanticEntityCatalog = [
    {
      key: "nasdaq100",
      label: "纳指/纳斯达克100",
      type: "指数",
      aliases: ["纳指", "纳斯达克", "纳斯达克100", "NASDAQ", "NASDAQ-100", "NASDAQ 100", "NDX", "纳斯达克指数"],
      categoryPattern: /纳指|纳斯达克|NASDAQ/i,
      note: "优先按最新持仓基金名称、基金代码及持仓分类命中；当前数据没有统一跟踪指数明细时，不把美股宽口径等同于纳指。"
    },
    {
      key: "sp500",
      label: "标普500",
      type: "指数",
      aliases: ["标普500", "标普 500", "标普五百", "S&P500", "S&P 500", "SP500", "SPX"],
      categoryPattern: /标普\s*500|S&P\s*500|SP500|SPX/i,
      note: "优先按最新持仓基金名称、基金代码及持仓分类命中。"
    },
    {
      key: "hs300",
      label: "沪深300",
      type: "指数",
      aliases: ["沪深300", "沪深 300", "CSI300", "000300"],
      note: "优先按最新持仓基金名称、基金代码及持仓分类命中。"
    },
    {
      key: "csi500",
      label: "中证500",
      type: "指数",
      aliases: ["中证500", "中证 500", "CSI500", "000905"],
      note: "优先按最新持仓基金名称、基金代码及持仓分类命中。"
    },
    {
      key: "csi1000",
      label: "中证1000",
      type: "指数",
      aliases: ["中证1000", "中证 1000", "CSI1000", "000852"],
      note: "优先按最新持仓基金名称、基金代码及持仓分类命中。"
    },
    {
      key: "gold",
      label: "黄金",
      type: "资产",
      aliases: ["黄金", "商品黄金", "贵金属"],
      categoryPattern: /黄金|贵金属|商品黄金/i,
      note: "优先使用最新持仓快照分类和基金名称证据。"
    },
    {
      key: "ai_core",
      label: "AI核心",
      type: "行业主题",
      aliases: ["AI", "AI主题", "AI核心", "人工智能", "人工智能主题", "AI产业链"],
      evidenceAliases: ["人工智能", "AI", "AIGC", "大模型", "算力", "光模块", "CPO", "光通信", "数据中心", "GPU", "半导体", "芯片"],
      categoryPattern: /人工智能|AIGC|大模型|算力|光模块|CPO|光通信|数据中心|GPU|半导体|芯片/i,
      parentKeys: ["technology"],
      note: "AI核心严格口径，不因泛泛科技/TMT自动命中。"
    },
    {
      key: "ai_compute",
      label: "算力/云计算/数据中心",
      type: "行业主题",
      aliases: ["算力", "云计算", "数据中心", "IDC", "GPU", "服务器"],
      evidenceAliases: ["算力", "云计算", "数据中心", "IDC", "GPU", "服务器"],
      categoryPattern: /算力|云计算|数据中心|IDC|GPU|服务器/i,
      parentKeys: ["ai_core"],
      note: "AI算力和云数据中心基础设施。"
    },
    {
      key: "optical_module_cpo",
      label: "光模块/CPO/光通信",
      type: "行业主题",
      aliases: ["光模块", "CPO", "光通信", "光器件", "硅光", "800G"],
      evidenceAliases: ["光模块", "CPO", "光通信", "光器件", "硅光", "800G"],
      categoryPattern: /光模块|CPO|光通信|光器件|硅光|800G/i,
      parentKeys: ["ai_core", "communication"],
      note: "光模块、CPO、光通信等AI算力网络链条；普通通信不自动等同光模块。"
    },
    {
      key: "semiconductor",
      label: "半导体/芯片",
      type: "行业主题",
      aliases: ["半导体", "芯片", "集成电路", "晶圆", "封测"],
      evidenceAliases: ["半导体", "芯片", "集成电路", "晶圆", "封测"],
      categoryPattern: /半导体|芯片|集成电路|晶圆|封测/i,
      parentKeys: ["ai_core", "technology"],
      note: "半导体/芯片主题，单独的电子宽口径不自动等同半导体。"
    },
    {
      key: "tech_manufacturing",
      label: "科技制造",
      type: "行业主题",
      aliases: ["科技", "科技制造", "高端制造", "智能制造", "半导体", "芯片", "集成电路", "人工智能", "AI", "计算机", "软件", "信息技术", "通信", "5G"],
      evidenceAliases: ["科技", "科技制造", "半导体", "芯片", "集成电路", "人工智能", "AI", "计算机", "软件", "信息技术", "通信", "5G"],
      categoryPattern: /科技|科技制造|半导体|芯片|集成电路|人工智能|AI|计算机|软件|信息技术|通信|5G/i,
      note: "把用户输入的“科技”等模糊词归并到标准行业主题「科技制造」，证据仍来自基金名称、基金画像行业主题和持仓分类。"
    },
    {
      key: "us_equity",
      label: "美股",
      type: "资产",
      aliases: ["美股", "美国股票", "美国权益", "美国市场"],
      evidenceAliases: ["美股市场", "美国", "纳斯达克", "标普500", "S&P500", "S&P 500", "SP500", "NASDAQ", "道琼斯"],
      categoryPattern: /美股|美股市场|美国股票|美国权益|美国市场/i,
      note: "优先按最新持仓快照中的美股/美股市场结构化分类命中；这是美股宽口径，不等同于纳指。"
    },
    {
      key: "hk_equity",
      label: "港股",
      type: "资产",
      aliases: ["港股", "香港股票", "恒生", "H股"],
      evidenceAliases: ["港股市场", "香港市场"],
      categoryPattern: /港股|港股市场|香港股票|香港市场|恒生|H股/i,
      note: "这是港股宽口径。"
    },
    {
      key: "germany_equity",
      label: "德国",
      type: "国家/地区",
      aliases: ["德国", "德国市场", "德国股票", "德股", "德国DAX", "DAX"],
      evidenceAliases: ["德国", "德国市场", "德国DAX", "德股", "DAX"],
      categoryPattern: /德国|德国市场|德国DAX|德股|DAX/i,
      parentKeys: ["overseas"],
      note: "按最新持仓基金名称、基金代码、资产类型和分类中的德国、DAX等证据命中。"
    },
    {
      key: "japan_equity",
      label: "日本",
      type: "国家/地区",
      aliases: ["日本", "日本市场", "日本股票", "日股", "日经", "日经225", "Nikkei", "Nikkei225", "TOPIX"],
      evidenceAliases: ["日本", "日本市场", "日股", "日经", "日经225", "Nikkei", "TOPIX"],
      categoryPattern: /日本|日本市场|日股|日经|Nikkei|TOPIX/i,
      parentKeys: ["overseas"],
      note: "按最新持仓基金名称、基金代码、资产类型和分类中的日本、日经225、Nikkei、TOPIX等证据命中。"
    },
    {
      key: "overseas",
      label: "海外资产",
      type: "资产",
      aliases: ["海外", "海外资产", "全球", "QDII", "境外", "海外权益", "全球资产"],
      categoryPattern: /QDII|海外|全球|港股|美股|其他发达市场|新兴市场|海外债券|海外REIT|海外权益|海外固收/i,
      note: "按海外/QDII/全球/港股/美股等宽口径持仓证据匹配。"
    },
  ];
  let fundProfileCache = null;
  const semanticEntityCatalog = normalizeSemanticCatalog(mergeSemanticCatalog([
    ...((semanticIndex?.entityCatalog && Array.isArray(semanticIndex.entityCatalog) && semanticIndex.entityCatalog.length) ? semanticIndex.entityCatalog : fallbackSemanticEntityCatalog),
    ...dynamicSemanticEntityCatalog(),
  ]));
  const indexedStandardEntityKeys = new Set(((semanticIndex?.entityCatalog && Array.isArray(semanticIndex.entityCatalog)) ? semanticIndex.entityCatalog : [])
    .map((entity) => raw(entity?.key))
    .filter(Boolean));
  const operatorLabels = {
    contains: "包含",
    contains_any: "包含任一",
    contains_all: "同时包含",
    weight_gte: "持仓权重大于等于",
    "not contains": "不包含",
    "=": "等于",
    "!=": "不等于",
    in: "等于任一",
    "not in": "不等于任一",
    ">=": "大于等于",
    "<=": "小于等于",
    ">": "大于",
    "<": "小于",
    "is empty": "为空",
    "is not empty": "有值",
  };
  const operatorOptions = ["contains", "contains_any", "not contains", "in", "not in", ">=", "<=", ">", "<", "=", "!=", "is not empty", "is empty"];
  const singleValueCategoricalFields = new Set(["研报产品类型", "业务分类", "市场地域", "主动被动", "运作状态", "基础数据等级", "风险等级", "业务组合分类"]);
  const defaultQuery = "找成立一年以上，回撤在3个点以内，收益率在5个点以上，持仓含黄金的策略。";
  const state = {
    query: new URLSearchParams(window.location.search).get("q") || defaultQuery,
    metric: "累计收益率",
    completeOnly: true,
    rows: [],
    parsed: null,
    sortField: "累计收益率",
    sortDir: "desc",
    limit: 100,
    searchSeq: 0,
    selectedCompareIds: [],
    selectedScatterId: "",
    scatterXField: "",
    scatterYField: "",
    lastResult: null,
  };
  const compareMaxCount = 5;
  const scatterMetricOptions = [
    "最大回撤",
    "当前回撤",
    "累计收益率",
    "年化收益",
    "近6月",
    "近1年",
    "今年以来",
    "近三月",
    "近一月",
    "波动率",
    "夏普比率",
    "权益基金权重",
    "债券基金权重",
    "货币基金权重",
    "QDII权重",
    "指数基金权重",
    "主动基金权重",
    "持仓基金数",
    "调仓次数",
    "最近一年调仓次数",
    "单次平均换手率",
    "年化换手率",
  ];
  const percentMetricFields = new Set([
    "最大回撤",
    "当前回撤",
    "累计收益率",
    "年化收益",
    "近6月",
    "近1年",
    "今年以来",
    "近三月",
    "近一月",
    "波动率",
    "权益基金权重",
    "债券基金权重",
    "货币基金权重",
    "QDII权重",
    "指数基金权重",
    "主动基金权重",
    "单次平均换手率",
    "年化换手率",
  ]);

  function raw(value) {
    return value === null || value === undefined ? "" : String(value);
  }

  function readStoredModelConfig() {
    try {
      const text = window.localStorage?.getItem(modelConfigStorageKey);
      if (!text) return {};
      const parsed = JSON.parse(text);
      return parsed && typeof parsed === "object" && !Array.isArray(parsed) ? parsed : {};
    } catch (error) {
      return {};
    }
  }

  function writeStoredModelConfig(config) {
    try {
      window.localStorage?.setItem(modelConfigStorageKey, JSON.stringify(config || {}));
    } catch (error) {
      // localStorage can be unavailable in hardened browser modes; runtime config still applies.
    }
  }

  function clearStoredModelConfig() {
    try {
      window.localStorage?.removeItem(modelConfigStorageKey);
    } catch (error) {
      // Ignore storage cleanup failures.
    }
  }

  function normalizeModelBaseUrl(value) {
    let text = raw(value).trim();
    if (!text) return "";
    text = text.replace(/\/+$/, "");
    return text.replace(/\/chat\/completions$/i, "");
  }

  function normalizeModelEndpoint(value, baseUrl = "") {
    const explicit = raw(value).trim();
    if (explicit) {
      const cleaned = explicit.replace(/\/+$/, "");
      return /\/chat\/completions$/i.test(cleaned) ? cleaned : `${cleaned}/chat/completions`;
    }
    const base = normalizeModelBaseUrl(baseUrl);
    return base ? `${base}/chat/completions` : "";
  }

  function modelBaseUrl(config = aiConfig) {
    return normalizeModelBaseUrl(config.baseUrl || config.endpoint || "");
  }

  function modelChatEndpoint(config = aiConfig) {
    return normalizeModelEndpoint(config.endpoint || "", config.baseUrl || "");
  }

  function isCodexProxyConfig(config = aiConfig) {
    return /codex/i.test(raw(config.provider)) || /127\.0\.0\.1:8787|localhost:8787/.test(raw(config.endpoint));
  }

  function modelDisplayLabel(config = aiConfig) {
    if (config.enabled === false) return "模型解析未启用";
    const endpoint = modelBaseUrl(config) || modelChatEndpoint(config) || "未配置";
    const profile = modelProfiles(config)[inferModelProfileKey(config)]?.label || raw(config.provider || "模型");
    return `${profile} ${raw(config.model || "未配置模型")} @ ${endpoint}`;
  }

  function parseHeadersJson(text) {
    const value = raw(text).trim();
    if (!value) return {};
    const parsed = JSON.parse(value);
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) throw new Error("headers 必须是 JSON 对象");
    return parsed;
  }

  function modelProfiles(config = aiConfig) {
    const fileProfiles = config.modelProfiles && typeof config.modelProfiles === "object" && !Array.isArray(config.modelProfiles)
      ? config.modelProfiles
      : {};
    return { ...builtInModelProfiles, ...fileProfiles };
  }

  function inferModelProfileKey(config = aiConfig) {
    const explicit = raw(config.profile || config.modelProfile).trim();
    if (explicit) return explicit;
    const provider = raw(config.provider);
    const endpoint = raw(config.endpoint || config.baseUrl);
    if (/codex/i.test(provider) || /127\.0\.0\.1:8787|localhost:8787/.test(endpoint)) return "codex";
    if (/same-origin|inner-ds|deepseek|llmapi/i.test(provider) || /\/llmapi\/v1|10\.89\.189\.109/.test(endpoint)) return "local";
    return "custom";
  }

  function applyModelProfile(profileKey, persist = false) {
    const key = raw(profileKey || "custom");
    if (key === "custom") {
      aiConfig.profile = "custom";
      if (persist) writeStoredModelConfig(aiConfig);
      return aiConfig;
    }
    const profile = modelProfiles(aiConfig)[key];
    if (!profile) return aiConfig;
    const next = {
      ...aiConfig,
      ...profile,
      profile: key,
      enabled: aiConfig.enabled !== false,
      mode: aiConfig.mode || "hybrid-parse",
      rateLimitBackoffMs: aiConfig.rateLimitBackoffMs || profile.rateLimitBackoffMs || 60000,
      modelProfiles: aiConfig.modelProfiles || aiConfigFileDefault.modelProfiles || {},
      codexBridge: aiConfig.codexBridge || aiConfigFileDefault.codexBridge || {},
    };
    return applyRuntimeModelConfig(next, persist);
  }

  function applyRuntimeModelConfig(nextConfig, persist = true) {
    const normalized = {
      enabled: nextConfig.enabled !== false,
      profile: raw(nextConfig.profile || nextConfig.modelProfile || inferModelProfileKey(nextConfig)).trim() || "custom",
      provider: raw(nextConfig.provider || "inner-ds-openai-compatible").trim() || "inner-ds-openai-compatible",
      baseUrl: normalizeModelBaseUrl(nextConfig.baseUrl || nextConfig.endpoint || ""),
      endpoint: normalizeModelEndpoint(nextConfig.endpoint || "", nextConfig.baseUrl || nextConfig.endpoint || ""),
      model: raw(nextConfig.model || "").trim(),
      timeoutMs: Math.min(Math.max(Number(nextConfig.timeoutMs) || 45000, 800), 120000),
      mode: raw(nextConfig.mode || "hybrid-parse").trim() || "hybrid-parse",
      apiKey: raw(nextConfig.apiKey || ""),
      headers: nextConfig.headers && typeof nextConfig.headers === "object" && !Array.isArray(nextConfig.headers) ? nextConfig.headers : {},
      rateLimitBackoffMs: Math.max(Number(nextConfig.rateLimitBackoffMs) || 60000, 1000),
      responseFormat: nextConfig.responseFormat !== false,
      modelProfiles: nextConfig.modelProfiles || aiConfig.modelProfiles || aiConfigFileDefault.modelProfiles || {},
      codexBridge: nextConfig.codexBridge || aiConfig.codexBridge || aiConfigFileDefault.codexBridge || {},
    };
    Object.assign(aiConfig, normalized);
    window.__AI_STRATEGY_CONFIG__ = aiConfig;
    modelBackoffUntil = 0;
    if (persist) writeStoredModelConfig(normalized);
    return normalized;
  }

  function num(value) {
    if (value === null || value === undefined || value === "") return null;
    const n = Number(value);
    return Number.isFinite(n) ? n : null;
  }

  function dateFrom(value) {
    const text = raw(value).trim()
      .replace(/[年/.]/g, "-")
      .replace(/月/g, "-")
      .replace(/日/g, "");
    const match = text.match(/^(\d{4})-(\d{1,2})-(\d{1,2})/);
    if (!match) return null;
    const date = new Date(Number(match[1]), Number(match[2]) - 1, Number(match[3]));
    if (Number.isNaN(date.getTime())) return null;
    if (date.getFullYear() !== Number(match[1]) || date.getMonth() !== Number(match[2]) - 1 || date.getDate() !== Number(match[3])) return null;
    return date;
  }

  function dateText(date) {
    if (!date) return "未识别";
    const yyyy = date.getFullYear();
    const mm = String(date.getMonth() + 1).padStart(2, "0");
    const dd = String(date.getDate()).padStart(2, "0");
    return `${yyyy}-${mm}-${dd}`;
  }

  function latestDataDate() {
    const dates = allRows
      .flatMap((row) => [dateFrom(row.收益数据截至), dateFrom(row.最新业绩日期), dateFrom(row.最新持仓日)])
      .filter(Boolean)
      .sort((a, b) => b - a);
    return dates[0] || null;
  }

  function packIndex(row, version2, indexV2, indexV1) {
    return row[version2 ? indexV2 : indexV1];
  }

  let holdingEvidenceCache = null;
  function holdingEvidenceByStrategy() {
    if (holdingEvidenceCache) return holdingEvidenceCache;
    holdingEvidenceCache = new Map();
    if (!holdingPack || !Array.isArray(holdingPack.rows)) return holdingEvidenceCache;
    const version2 = Number(holdingPack.version || 1) >= 2;
    const dict = holdingPack.dict || {};
    const fields = holdingPack.fields || [];
    const goldPattern = /黄金|贵金属|商品黄金/;
    const overseasPattern = /QDII|海外|全球|港股|美股|其他发达市场|新兴市场|海外债券|海外REIT/;
    holdingPack.rows.forEach((packRow) => {
      const strategy = dict.strategies?.[packRow[0]] || [];
      const strategyId = strategy[0] || "";
      const date = raw(packRow[1]);
      if (!strategyId || !date) return;
      let evidence = holdingEvidenceCache.get(strategyId);
      if (!evidence || date > evidence.date) {
        evidence = {
          date,
          checked: 0,
          hasGold: false,
          goldWeight: 0,
          goldLabels: new Set(),
          hasOverseas: false,
          overseasWeight: 0,
          overseasLabels: new Set(),
        };
        holdingEvidenceCache.set(strategyId, evidence);
      }
      if (date !== evidence.date) return;
      const field = fields[packIndex(packRow, version2, 11, 9)] || "";
      const category = dict.categories?.[packIndex(packRow, version2, 12, 10)] || "";
      const weight = num(packIndex(packRow, version2, 13, 11)) || 0;
      evidence.checked += 1;
      if (goldPattern.test(`${field} ${category}`) && weight > 0.0001) {
        evidence.hasGold = true;
        if (field === "研报大类资产") evidence.goldWeight += weight;
        evidence.goldLabels.add(`${field}:${category}`);
      }
      if (overseasPattern.test(`${field} ${category}`) && weight > 0.0001) {
        evidence.hasOverseas = true;
        if (field === "研报大类资产") evidence.overseasWeight += weight;
        evidence.overseasLabels.add(`${field}:${category}`);
      }
    });
    holdingEvidenceCache.forEach((evidence) => {
      evidence.goldLabels = Array.from(evidence.goldLabels);
      evidence.overseasLabels = Array.from(evidence.overseasLabels);
    });
    return holdingEvidenceCache;
  }

  let holdingCategoryCache = null;
  function holdingCategoriesByStrategy() {
    if (holdingCategoryCache) return holdingCategoryCache;
    holdingCategoryCache = new Map();
    if (!holdingPack || !Array.isArray(holdingPack.rows)) return holdingCategoryCache;
    const version2 = Number(holdingPack.version || 1) >= 2;
    const dict = holdingPack.dict || {};
    const fields = holdingPack.fields || [];
    holdingPack.rows.forEach((packRow) => {
      const strategy = dict.strategies?.[packRow[0]] || [];
      const strategyId = strategy[0] || "";
      const date = raw(packRow[1]);
      if (!strategyId || !date) return;
      let evidence = holdingCategoryCache.get(strategyId);
      if (!evidence || date > evidence.date) {
        evidence = { date, checked: 0, rows: [] };
        holdingCategoryCache.set(strategyId, evidence);
      }
      if (date !== evidence.date) return;
      const field = fields[packIndex(packRow, version2, 11, 9)] || "";
      const category = dict.categories?.[packIndex(packRow, version2, 12, 10)] || "";
      const weight = num(packIndex(packRow, version2, 13, 11)) || 0;
      evidence.checked += 1;
      if (weight > 0.0001) evidence.rows.push({ field, category, weight, date });
    });
    holdingCategoryCache.forEach((evidence) => evidence.rows.sort((a, b) => (b.weight || 0) - (a.weight || 0)));
    return holdingCategoryCache;
  }

  function normalizeSearchText(value) {
    return raw(value)
      .toLowerCase()
      .replace(/[Ａ-Ｚａ-ｚ０-９]/g, (char) => String.fromCharCode(char.charCodeAt(0) - 0xfee0))
      .replace(/[\s_\-－—/\\()（）【】\[\]：:,.，。]+/g, "");
  }

  function containsAlias(text, aliases) {
    const haystack = normalizeSearchText(text);
    return (aliases || []).some((alias) => {
      const needle = normalizeSearchText(alias);
      return needle && haystack.includes(needle);
    });
  }

  function compactFundCompanyName(value) {
    return raw(value)
      .replace(/基金管理有限公司|基金有限公司|管理有限公司|有限公司/g, "")
      .replace(/基金$/g, "")
      .trim();
  }

  function isPlausibleFundCompany(value) {
    const compact = compactFundCompanyName(value);
    if (!compact || compact.length > 7) return false;
    if (/科技|创新|成长|互联|智能|产业|消费|医疗|医药|红利|精选|核心|价值/.test(compact) && !/兴证全球/.test(compact)) return false;
    return true;
  }

  function fundProfileByKey() {
    if (fundProfileCache) return fundProfileCache;
    fundProfileCache = new Map();
    const pack = fundDetailPack;
    if (!pack || !Array.isArray(pack.funds)) return fundProfileCache;
    const fields = pack.fundFields || [];
    const indexOf = (field) => fields.indexOf(field);
    const idx = {
      code: indexOf("基金代码"),
      name: indexOf("基金名称"),
      company: indexOf("基金公司"),
      type: indexOf("基金类型"),
      secondary: indexOf("二级分类"),
      theme: indexOf("行业主题"),
      asset: indexOf("研报大类资产"),
      industry: indexOf("研报A股行业"),
    };
    pack.funds.forEach((row) => {
      const profile = {
        code: raw(row[idx.code]),
        name: raw(row[idx.name]),
        company: raw(row[idx.company]),
        type: raw(row[idx.type]),
        secondary: raw(row[idx.secondary]),
        theme: raw(row[idx.theme]),
        asset: raw(row[idx.asset]),
        industry: raw(row[idx.industry]),
      };
      if (profile.code) fundProfileCache.set(`code:${profile.code}`, profile);
      if (profile.name) fundProfileCache.set(`name:${normalizeSearchText(profile.name)}`, profile);
    });
    return fundProfileCache;
  }

  function fundProfileForHoldingLike(item) {
    const byKey = fundProfileByKey();
    const code = raw(item?.fundCode || item?.基金代码);
    const name = raw(item?.fundName || item?.基金名称);
    return (code && byKey.get(`code:${code}`)) || (name && byKey.get(`name:${normalizeSearchText(name)}`)) || null;
  }

  function inferredFundCompanyFromName(value) {
    const text = raw(value).trim();
    const match = text.match(/^([\u4e00-\u9fa5A-Za-z]+?)(?:基金|货币|纯债|债券|短债|中短债|混合|股票|指数|ETF|联接|增强|配置|精选|红利|黄金|全球|海外|沪深|中证|国证|恒生|纳斯达克|标普|日经|DAX)/);
    const inferred = match ? `${match[1]}基金` : "";
    return isPlausibleFundCompany(inferred) ? inferred : "";
  }

  function fundCompanyForHoldingLike(item) {
    const company = raw(item?.fundCompany || item?.基金公司 || fundProfileForHoldingLike(item)?.company || inferredFundCompanyFromName(item?.fundName || item?.基金名称)).trim();
    return isPlausibleFundCompany(company) ? company : "";
  }

  function entityAliasesForValue(value, extra = []) {
    const text = raw(value).trim();
    const aliases = new Set([text, ...extra.map(raw).filter(Boolean)]);
    const compact = text.replace(/\s+/g, "");
    const addAliases = (items) => items.forEach((item) => aliases.add(item));
    text.split(/[\/／、,，|｜\s]+/).map((item) => item.trim()).filter((item) => item.length >= 2).forEach((item) => aliases.add(item));
    if (/科技制造/.test(compact)) addAliases(["科技制造", "科技", "高端制造", "智能制造"]);
    if (/电子|半导体|芯片|集成电路/.test(compact)) addAliases(["电子", "半导体", "芯片", "集成电路"]);
    if (/计算机|人工智能|信息技术|软件|AI/.test(compact)) addAliases(["计算机", "人工智能", "AI", "软件", "信息技术"]);
    if (/通信|5G/.test(compact)) addAliases(["通信", "5G"]);
    if (/黄金|贵金属|商品/.test(compact)) addAliases(["黄金", "贵金属", "商品黄金"]);
    if (/医药|医疗|生物|创新药|中药/.test(compact)) addAliases(["医药", "医疗", "生物医药", "创新药", "中药"]);
    if (/消费|食品|饮料|白酒/.test(compact)) addAliases(["消费", "食品饮料", "白酒"]);
    if (/新能源|光伏|电池|储能|电力设备/.test(compact)) addAliases(["新能源", "光伏", "电池", "储能", "电力设备"]);
    if (/金融|银行|证券|保险|地产/.test(compact)) addAliases(["金融", "银行", "证券", "保险", "地产"]);
    return [...aliases].filter(Boolean);
  }

  function mergeSemanticCatalog(items) {
    const merged = new Map();
    (items || []).forEach((item) => {
      const key = raw(item?.key || item?.label).trim();
      if (!key) return;
      const existing = merged.get(key);
      if (!existing) {
        merged.set(key, { ...item });
        return;
      }
      existing.aliases = [...new Set([...(existing.aliases || []), ...(item.aliases || [])].filter(Boolean))];
      existing.evidenceAliases = [...new Set([...(existing.evidenceAliases || []), ...(item.evidenceAliases || [])].filter(Boolean))];
      existing.patterns = [...new Set([...(existing.patterns || []), ...(item.patterns || [])].filter(Boolean))];
      existing.parentKeys = [...new Set([...(existing.parentKeys || []), ...(item.parentKeys || [])].filter(Boolean))];
    });
    return Array.from(merged.values());
  }

  function dynamicSemanticEntityCatalog() {
    const fields = semanticIndex?.fields || [];
    const indexOf = (field) => fields.indexOf(field);
    const idx = {
      code: indexOf("基金代码"),
      name: indexOf("基金名称"),
      company: indexOf("基金公司"),
      assetType: indexOf("资产类型"),
      secondary: indexOf("二级分类"),
      group: indexOf("分组"),
      peerGroup: indexOf("基金同类分组"),
      weight: indexOf("权重"),
    };
    const buckets = new Map();
    const add = (type, label, aliases = []) => {
      const clean = raw(label).trim();
      if (!clean || clean === "未披露") return;
      const key = `${type}:${normalizeSearchText(clean)}`;
      const bucket = buckets.get(key) || { key, label: clean, type, aliases: [], evidenceAliases: [], patterns: [], note: `来自当前最新持仓明细动态聚合的${type}实体。` };
      bucket.aliases = [...new Set([...bucket.aliases, ...entityAliasesForValue(clean, aliases)].filter(Boolean))];
      bucket.evidenceAliases = [...new Set([...bucket.evidenceAliases, clean, ...aliases].filter(Boolean))];
      buckets.set(key, bucket);
    };
    (semanticIndex?.rows || []).forEach((row) => {
      const weight = idx.weight >= 0 ? num(row[idx.weight]) : null;
      if (weight !== null && weight <= 0.0001) return;
      const fundName = idx.name >= 0 ? raw(row[idx.name]) : "";
      const fundCode = idx.code >= 0 ? raw(row[idx.code]) : "";
      const company = idx.company >= 0 ? raw(row[idx.company]) : "";
      const profile = fundProfileForHoldingLike({ fundCode, fundName });
      const fundCompany = company || raw(profile?.company) || fundCompanyForHoldingLike({ fundCode, fundName });
      if (fundCompany && isPlausibleFundCompany(fundCompany)) add("基金公司", fundCompany, [compactFundCompanyName(fundCompany)].filter(Boolean));
      [["资产类型", idx.assetType], ["基金二级分类", idx.secondary], ["持仓分组", idx.group], ["基金同类/主题", idx.peerGroup]].forEach(([type, index]) => {
        if (index >= 0) add(type, row[index]);
      });
      [
        ["基金画像类型", profile?.type],
        ["基金画像二级分类", profile?.secondary],
        ["基金画像行业主题", profile?.theme],
        ["基金画像大类资产", profile?.asset],
        ["基金画像行业", profile?.industry],
      ].forEach(([type, value]) => add(type, value));
    });
    return Array.from(buckets.values());
  }

  function normalizeSemanticCatalog(catalog) {
    const source = Array.isArray(catalog) && catalog.length ? catalog : [];
    return source.map((entity) => ({
      ...entity,
      aliases: Array.isArray(entity.aliases) ? entity.aliases.filter(Boolean) : [],
      evidenceAliases: Array.isArray(entity.evidenceAliases) ? entity.evidenceAliases.filter(Boolean) : [],
      parentKeys: Array.isArray(entity.parentKeys) ? entity.parentKeys.filter(Boolean) : [],
      patterns: Array.isArray(entity.patterns) ? entity.patterns.filter(Boolean) : [],
    }));
  }

  function entityPatternMatches(entity, text) {
    if (!entity || !raw(text)) return false;
    if (entity.categoryPattern && entity.categoryPattern.test(text)) return true;
    return (entity.patterns || []).some((pattern) => {
      try {
        return new RegExp(pattern, "i").test(raw(text));
      } catch (error) {
        return false;
      }
    });
  }

  function entityMatchesEvidence(entity, text, aliases = []) {
    const matchAliases = [...new Set([...(aliases || []), ...(entity?.evidenceAliases || [])].map(raw).filter(Boolean))];
    return entityPatternMatches(entity, text) || containsAlias(text, matchAliases);
  }

  function escapedRegExp(value) {
    return raw(value).replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  }

  function resolveSemanticEntity(value) {
    const text = normalizeSearchText(value);
    if (!text) return null;
    return semanticEntityCatalog.find((entity) => entity.key === value
      || normalizeSearchText(entity.label).includes(text)
      || (entity.aliases || []).some((alias) => normalizeSearchText(alias) === text || text.includes(normalizeSearchText(alias)) || normalizeSearchText(alias).includes(text))) || null;
  }

  function isIndexedStandardEntity(entity) {
    const key = raw(entity?.key);
    return !!key && !key.includes(":") && indexedStandardEntityKeys.has(key);
  }

  function entityAliases(entity, fallback) {
    const aliases = entity ? [entity.label, ...(entity.aliases || [])] : [fallback];
    return [...new Set(aliases.map(raw).filter(Boolean))];
  }

  function hasNegativeCueForAlias(text, alias) {
    if (!raw(alias).trim()) return false;
    const aliasPattern = escapedRegExp(alias);
    const negativeCue = "(?:不含|不包含|不持有|未持有|不是|非|未|没有|无|剔除|排除|不要|不想|不能有|不得持有|避免)";
    const sameClauseChars = "[^\\s，。；、,.但且并同最好而又]";
    if (new RegExp(`${negativeCue}${sameClauseChars}{0,12}${aliasPattern}`, "i").test(text)) return true;
    if (new RegExp(`${aliasPattern}${sameClauseChars}{0,8}(?:除外|排除|剔除|不要|不含|不包含)`, "i").test(text)) return true;
    const compactText = normalizeSearchText(text);
    const compactAlias = normalizeSearchText(alias);
    return !!compactAlias && [
      `不含${compactAlias}`,
      `不包含${compactAlias}`,
      `不持有${compactAlias}`,
      `不是${compactAlias}`,
      `非${compactAlias}`,
      `排除${compactAlias}`,
      `剔除${compactAlias}`,
      `${compactAlias}除外`,
      `${compactAlias}不要`
    ].some((pattern) => compactText.includes(pattern));
  }

  function hasNegativeCueForEntity(text, entity) {
    return entityAliases(entity, entity?.label || "").some((alias) => hasNegativeCueForAlias(text, alias));
  }

  function hasLowOverseasPreference(query) {
    const text = raw(query);
    const lowCue = "(?:少一点|少一些|尽量少|最好少|较少|低一点|低配|少配|不要太多|不宜太多|控制|压低|降低)";
    const overseasCue = "(?:QDII|海外|全球|境外|海外资产|全球资产|海外配置)";
    return new RegExp(`${overseasCue}[^\\s，。；、,.]{0,8}${lowCue}`, "i").test(text)
      || new RegExp(`${lowCue}[^\\s，。；、,.]{0,8}${overseasCue}`, "i").test(text);
  }

  function explicitlyRequestsOverseas(query) {
    const text = raw(query);
    if (hasLowOverseasPreference(text)) return false;
    if (/(不含|不包含|不持有|未持有|剔除|排除|不要).{0,8}(QDII|海外|全球|境外|海外资产|全球资产)/i.test(text)) return false;
    return /(含|包含|持有|配置|有).{0,8}(QDII|海外|全球|境外|海外资产|全球资产)|海外资产|全球资产|QDII|海外|全球|境外/i.test(text);
  }

  let semanticHoldingCache = null;
  function semanticHoldingsByStrategy() {
    if (semanticHoldingCache) return semanticHoldingCache;
    semanticHoldingCache = new Map();
    if (!semanticIndex || !Array.isArray(semanticIndex.rows)) return semanticHoldingCache;
    const fields = semanticIndex.fields || [];
    const fieldAt = (row, field, fallbackFields = []) => {
      const candidates = [field, ...fallbackFields];
      for (const name of candidates) {
        const index = fields.indexOf(name);
        if (index >= 0) return row[index];
      }
      return undefined;
    };
    semanticIndex.rows.forEach((row) => {
      const strategyId = raw(fieldAt(row, "统一策略ID"));
      if (!strategyId) return;
      const fundCode = raw(fieldAt(row, "基金代码"));
      const fundName = raw(fieldAt(row, "基金名称"));
      const profile = fundProfileForHoldingLike({ fundCode, fundName });
      const companyCandidate = raw(fieldAt(row, "基金公司") || profile?.company);
      const holding = {
        strategyId,
        strategyName: raw(fieldAt(row, "策略名称")),
        date: raw(fieldAt(row, "持仓日期")),
        fundCode,
        fundName,
        fundCompany: isPlausibleFundCompany(companyCandidate) ? companyCandidate : "",
        assetType: raw(fieldAt(row, "资产类型", ["基金类型"])),
        secondaryCategory: raw(fieldAt(row, "二级分类", ["基金二级分类"])),
        group: raw(fieldAt(row, "分组", ["研报大类资产"])),
        peerGroup: raw(fieldAt(row, "基金同类分组", ["行业主题", "权益行业主题", "研报A股行业"])),
        profileType: raw(profile?.type),
        profileSecondaryCategory: raw(profile?.secondary),
        profileTheme: raw(profile?.theme),
        profileAsset: raw(profile?.asset),
        profileIndustry: raw(profile?.industry),
        weight: num(fieldAt(row, "权重")) || 0,
      };
      if (!holding.fundCompany) holding.fundCompany = fundCompanyForHoldingLike(holding);
      if (!semanticHoldingCache.has(strategyId)) semanticHoldingCache.set(strategyId, []);
      semanticHoldingCache.get(strategyId).push(holding);
    });
    semanticHoldingCache.forEach((rows) => rows.sort((a, b) => (b.weight || 0) - (a.weight || 0)));
    return semanticHoldingCache;
  }

  function holdingText(holding) {
    return [
      holding.fundCode,
      holding.fundName,
      holding.fundCompany,
      holding.assetType,
      holding.secondaryCategory,
      holding.group,
      holding.peerGroup,
      holding.profileType,
      holding.profileSecondaryCategory,
      holding.profileTheme,
      holding.profileAsset,
      holding.profileIndustry,
    ].map(raw).filter(Boolean).join(" ");
  }

  function aiTopicTheme() {
    const themes = Array.isArray(topicPack?.themes) ? topicPack.themes : [];
    return themes.find((theme) => /AI核心|人工智能|AI/.test(raw(theme.name || theme.id))) || null;
  }

  let aiTopicStrategyCache = null;
  function aiTopicEvidenceByStrategy() {
    if (aiTopicStrategyCache) return aiTopicStrategyCache;
    aiTopicStrategyCache = new Map();
    const theme = aiTopicTheme();
    if (!theme) return aiTopicStrategyCache;
    const rows = [...(Array.isArray(theme.points) ? theme.points : []), ...(Array.isArray(theme.selected) ? theme.selected : [])];
    rows.forEach((row) => {
      const strategyId = raw(row.统一策略ID);
      if (!strategyId) return;
      const current = num(row.当前AI核心暴露) || 0;
      const existing = aiTopicStrategyCache.get(strategyId);
      if (!existing || current > (num(existing.当前AI核心暴露) || 0)) aiTopicStrategyCache.set(strategyId, row);
    });
    return aiTopicStrategyCache;
  }

  function aiTopicFundPattern(entityKey) {
    const key = raw(entityKey);
    if (key === "ai_core") return /./;
    if (key === "semiconductor") return /半导体|芯片|集成电路/i;
    if (key === "ai_theme") return /直接AI|人工智能|AIGC|大模型|机器学习/i;
    if (key === "robotics") return /机器人/i;
    if (key === "ai_compute") return /算力|云计算|数据中心|GPU|服务器|液冷|东数西算/i;
    if (key === "optical_module_cpo") return /光模块|CPO|光通信|光器件|硅光|800G|1\.6T/i;
    if (key === "communication") return /通信设备|5G|6G/i;
    return null;
  }

  function aiTopicEntityEvidence(row, entity) {
    const key = raw(entity?.key || entity);
    const theme = aiTopicTheme();
    const topicRow = aiTopicEvidenceByStrategy().get(raw(row.统一策略ID));
    if (!theme || !key) return { known: false, hasEntity: false, entity, weight: 0, date: raw(row.最新持仓日), labels: [] };
    const currentWeight = num(topicRow?.当前AI核心暴露) || 0;
    const funds = Array.isArray(topicRow?.主要AI核心基金) ? topicRow.主要AI核心基金 : [];
    const pattern = aiTopicFundPattern(key);
    if (!pattern) return { known: false, hasEntity: false, entity, weight: 0, date: raw(row.最新持仓日), labels: [] };
    const matchedFunds = key === "ai_core"
      ? funds
      : funds.filter((fund) => pattern.test(`${raw(fund.name)} ${raw(fund.hits)}`));
    const weight = key === "ai_core"
      ? currentWeight
      : Math.min(100, matchedFunds.reduce((total, fund) => total + (num(fund.weight) || 0), 0));
    const fundLabels = matchedFunds.slice(0, 6).map((fund) => {
      const source = raw(fund.hits);
      return `${raw(fund.name || fund.code || "未命名基金")}${fund.weight ? ` ${formatPct(fund.weight)}` : ""}${source ? `：${source}` : ""}`;
    });
    const hasEntity = weight > 0.0001 && matchedFunds.length > 0;
    return {
      known: aiTopicEvidenceByStrategy().size > 0,
      hasEntity,
      entity,
      weight,
      date: raw(topicRow?.峰值日期 || row.最新持仓日),
      labels: hasEntity ? [`${entity?.label || key} ${formatPct(weight)}：${fundLabels.join("、")}`] : [],
      matches: matchedFunds.map((fund) => ({
        entityKey: key,
        entityName: entity?.label || key,
        entityType: entity?.type || "行业主题",
        weight: num(fund.weight) || 0,
        date: raw(topicRow?.峰值日期 || row.最新持仓日),
        evidence: `${raw(fund.name || fund.code)}${fund.hits ? `：${fund.hits}` : ""}`,
        sourceField: "AI专题包",
        sourceValue: raw(fund.hits || theme.name || "AI核心行情"),
        ruleId: "topic_pack:ai_core_exposure",
      })),
      strict: true,
      source: "topic_analysis_pack",
    };
  }

  let strategyEntityCache = null;
  function strategyEntitiesByStrategy() {
    if (strategyEntityCache) return strategyEntityCache;
    strategyEntityCache = new Map();
    const pack = semanticIndex?.strategyEntities;
    if (!pack || !Array.isArray(pack.rows)) return strategyEntityCache;
    const fields = pack.fields || [];
    const indexOf = (field) => fields.indexOf(field);
    const idx = {
      strategyId: indexOf("统一策略ID"),
      strategyName: indexOf("策略名称"),
      entityKey: indexOf("实体Key"),
      entityName: indexOf("实体名称"),
      entityType: indexOf("实体类型"),
      weight: indexOf("权重"),
      date: indexOf("持仓日期"),
      evidence: indexOf("证据基金"),
      fundCount: indexOf("基金数"),
      confidence: indexOf("置信度"),
      level: indexOf("实体等级"),
      sourceField: indexOf("来源字段"),
      sourceValue: indexOf("来源值"),
      ruleId: indexOf("抽取规则ID"),
      ruleVersion: indexOf("规则版本"),
      generatedAt: indexOf("生成时间"),
    };
    pack.rows.forEach((row) => {
      const strategyId = raw(row[idx.strategyId]);
      const entityKey = raw(row[idx.entityKey]);
      if (!strategyId || !entityKey) return;
      const item = {
        strategyId,
        strategyName: raw(row[idx.strategyName]),
        entityKey,
        entityName: raw(row[idx.entityName]),
        entityType: raw(row[idx.entityType]),
        weight: num(row[idx.weight]) || 0,
        date: raw(row[idx.date]),
        evidence: raw(row[idx.evidence]),
        fundCount: num(row[idx.fundCount]) || 0,
        confidence: num(row[idx.confidence]) || 0,
        level: idx.level >= 0 ? raw(row[idx.level]) : "",
        sourceField: idx.sourceField >= 0 ? raw(row[idx.sourceField]) : "",
        sourceValue: idx.sourceValue >= 0 ? raw(row[idx.sourceValue]) : "",
        ruleId: idx.ruleId >= 0 ? raw(row[idx.ruleId]) : "",
        ruleVersion: idx.ruleVersion >= 0 ? raw(row[idx.ruleVersion]) : "",
        generatedAt: idx.generatedAt >= 0 ? raw(row[idx.generatedAt]) : "",
      };
      if (!strategyEntityCache.has(strategyId)) strategyEntityCache.set(strategyId, []);
      strategyEntityCache.get(strategyId).push(item);
    });
    strategyEntityCache.forEach((rows) => rows.sort((a, b) => (b.weight || 0) - (a.weight || 0)));
    return strategyEntityCache;
  }

  function strategyEntityEvidence(row, termOrEntity) {
    const entity = typeof termOrEntity === "object" && termOrEntity ? termOrEntity : resolveSemanticEntity(termOrEntity);
    const key = entity?.key || raw(termOrEntity);
    const rows = strategyEntitiesByStrategy().get(raw(row.统一策略ID)) || [];
    if (!semanticIndex?.strategyEntities || !key) return { known: false, hasEntity: false, entity, weight: 0, date: raw(row.最新持仓日), labels: [] };
    const matches = rows.filter((item) => item.entityKey === key);
    const rawWeight = matches.reduce((total, item) => total + (num(item.weight) || 0), 0);
    const weight = Math.min(100, rawWeight);
    const topicEvidence = aiTopicEntityEvidence(row, entity || (matches[0] ? { key, label: matches[0].entityName, type: matches[0].entityType } : { key, label: key }));
    if (topicEvidence.hasEntity && (!matches.length || (num(topicEvidence.weight) || 0) > weight)) return topicEvidence;
    const labels = matches
      .map((item) => {
        const displayWeight = Math.min(100, num(item.weight) || 0);
        return `${item.entityName || entity?.label || key}${displayWeight ? ` ${formatPct(displayWeight)}` : ""}${item.evidence ? `：${item.evidence}` : ""}`;
      })
      .slice(0, 6);
    return {
      known: true,
      hasEntity: matches.length > 0 && weight > 0.0001,
      entity: entity || (matches[0] ? { key, label: matches[0].entityName, type: matches[0].entityType } : null),
      weight,
      date: matches[0]?.date || raw(row.最新持仓日),
      labels,
      matches,
    };
  }

  function structuredHoldingEntityEvidence(row, entity, aliases) {
    const strategyId = raw(row.统一策略ID);
    const evidence = holdingCategoriesByStrategy().get(strategyId);
    if (!evidence) return { known: false, hasEntity: false, date: raw(row.最新持仓日), weight: 0, matches: [], labels: [] };
    const matchAliases = [...new Set([...(aliases || []), ...(entity?.evidenceAliases || [])].map(raw).filter(Boolean))];
    const matches = (evidence.rows || []).filter((item) => {
      const text = `${item.field} ${item.category}`;
      return entityMatchesEvidence(entity, text, matchAliases);
    });
    const primaryMatches = matches.filter((item) => item.field === "研报大类资产");
    const weightedMatches = primaryMatches.length ? primaryMatches : matches;
    const labels = matches.slice(0, 8).map((item) => `${item.field}:${item.category}${item.weight ? ` ${formatPct(item.weight)}` : ""}`);
    return {
      known: evidence.checked > 0,
      hasEntity: matches.length > 0,
      date: evidence.date,
      weight: Math.min(100, weightedMatches.reduce((total, item) => total + (num(item.weight) || 0), 0)),
      matches,
      labels,
    };
  }

  function holdingEntityEvidence(row, term) {
    const entity = resolveSemanticEntity(term);
    const aliases = [...new Set([...entityAliases(entity, term), ...(entity?.evidenceAliases || [])].map(raw).filter(Boolean))];
    const strategyId = raw(row.统一策略ID);
    const indexed = strategyEntityEvidence(row, entity || term);
    const holdings = semanticHoldingsByStrategy().get(strategyId) || [];
    const directMatches = holdings.filter((holding) => (holding.weight || 0) > 0.0001 && entityMatchesEvidence(entity, holdingText(holding), aliases));
    if (isIndexedStandardEntity(entity)) {
      const directWeight = directMatches.reduce((total, holding) => total + (num(holding.weight) || 0), 0);
      const directLabels = directMatches.slice(0, 6).map((holding) => `基金:${holding.fundName || holding.fundCode || "未命名基金"}${holding.weight ? ` ${formatPct(holding.weight)}` : ""}`);
      const hasEntity = indexed.hasEntity || directMatches.length > 0;
      return {
        known: indexed.known || holdings.length > 0,
        hasEntity,
        entity: indexed.entity || entity,
        term: raw(term),
        aliases,
        date: indexed.date || directMatches[0]?.date || holdings[0]?.date || raw(row.最新持仓日),
        weight: indexed.hasEntity && (num(indexed.weight) || 0) > 0.0001 ? indexed.weight : Math.min(100, directWeight),
        matches: [...(indexed.matches || []), ...directMatches],
        labels: [...new Set([...(indexed.labels || []), ...directLabels])].slice(0, 10),
        strict: true,
      };
    }
    const structured = structuredHoldingEntityEvidence(row, entity, aliases);
    const matches = directMatches;
    const semanticLabels = matches.slice(0, 6).map((holding) => `基金:${holding.fundName || holding.fundCode || "未命名基金"}${holding.weight ? ` ${formatPct(holding.weight)}` : ""}`);
    const labels = [...new Set([...(indexed.labels || []), ...(structured.labels || []), ...semanticLabels].filter(Boolean))].slice(0, 10);
    const semanticWeight = matches.reduce((total, holding) => total + (num(holding.weight) || 0), 0);
    const hasEntity = indexed.hasEntity || structured.hasEntity || matches.length > 0;
    const known = indexed.known || structured.known || holdings.length > 0;
    return {
      known,
      hasEntity,
      entity: indexed.entity || entity,
      term: raw(term),
      aliases,
      date: indexed.date || structured.date || matches[0]?.date || holdings[0]?.date || raw(row.最新持仓日),
      weight: indexed.hasEntity ? indexed.weight : (structured.hasEntity ? structured.weight : semanticWeight),
      matches: [...(indexed.matches || []), ...(structured.matches || []), ...matches],
      labels,
    };
  }

  function holdingEntityFilterItems(filter) {
    if (Array.isArray(filter?.anyOfEntities) && filter.anyOfEntities.length) {
      return filter.anyOfEntities.map((item) => {
        const resolved = resolveSemanticEntity(item.key || item.term || item.label);
        return {
          key: resolved?.key || item.key || "",
          term: item.term || resolved?.aliases?.[0] || item.key || item.label,
          label: resolved?.label || item.label || item.term || item.key,
          negative: !!item.negative,
        };
      });
    }
    if (raw(filter?.op) === "contains_any" && raw(filter?.value).includes("|")) {
      return raw(filter.value).split("|").map((term) => term.trim()).filter(Boolean).map((term) => {
        const resolved = resolveSemanticEntity(term);
        return {
          key: resolved?.key || "",
          term,
          label: resolved?.label || term,
          negative: false,
        };
      });
    }
    const resolved = resolveSemanticEntity(filter?.value || filter?.semanticEntity || "");
    return [{
      key: resolved?.key || filter?.semanticEntity || "",
      term: filter?.value || resolved?.aliases?.[0] || filter?.semanticEntity || "",
      label: resolved?.label || raw(filter?.value || filter?.semanticEntity || ""),
      negative: filter?.op === "not contains" || filter?.op === "!=",
    }];
  }

  function holdingEntityEvidenceForFilter(row, filter) {
    const items = holdingEntityFilterItems(filter);
    const evidences = items.map((item) => holdingEntityEvidence(row, item.key || item.term));
    const matched = evidences.filter((evidence) => evidence.hasEntity);
    const labels = [...new Set(evidences.flatMap((evidence) => evidence.labels || []))].slice(0, 12);
    const entityLabels = items.map((item, index) => evidences[index]?.entity?.label || item.label || item.term).filter(Boolean);
    const entitySummaries = evidences.map((evidence, index) => ({
      label: evidence.entity?.label || items[index]?.label || items[index]?.term || "",
      weight: Math.min(100, num(evidence.weight) || 0),
      hasEntity: !!evidence.hasEntity,
      labels: (evidence.labels || []).slice(0, 2),
    }));
    return {
      known: evidences.some((evidence) => evidence.known),
      hasEntity: matched.length > 0,
      hasAllEntities: evidences.length ? evidences.every((evidence) => evidence.hasEntity) : false,
      entity: { label: entityLabels.join("、") || raw(filter?.value || filter?.semanticEntity || ""), type: "持仓实体" },
      term: entityLabels.join("、"),
      aliases: items.flatMap((item) => entityAliases(resolveSemanticEntity(item.key || item.term), item.term)),
      date: evidences.find((evidence) => evidence.date)?.date || raw(row.最新持仓日),
      weight: Math.min(100, matched.reduce((total, evidence) => total + (num(evidence.weight) || 0), 0)),
      matches: evidences.flatMap((evidence) => evidence.matches || []),
      labels,
      evidences,
      entitySummaries,
    };
  }

  function holdingEntityRelationMode(query, entities) {
    if ((entities || []).length <= 1) return "single";
    const text = raw(query);
    if (/(任一|任意|其中之一|之一|任一种|任意一个|或者|或)/.test(text)) return "any";
    return "all";
  }

  function holdingEntityWeightMode(query) {
    const text = raw(query);
    if (/(分别|各自|都).{0,8}(超过|大于|不低于|不少于|以上)/.test(text)) return "each";
    return "sum";
  }

  function activeHoldingEntityFilter(parsed = state.parsed) {
    return (parsed?.filters || []).find((filter) => filter.field === "__holding_entity") || null;
  }

  function asOfDate() {
    const latest = latestDataDate();
    return latest || new Date();
  }

  function yearsAgo(date, years) {
    const out = new Date(date.getTime());
    out.setFullYear(out.getFullYear() - Math.trunc(years));
    if (!Number.isInteger(years)) out.setDate(out.getDate() - Math.round((years % 1) * 365.25));
    return out;
  }

  function calendarMonthsAgo(date, months) {
    const wholeMonths = Math.max(0, Math.round(Number(months) || 0));
    const day = date.getDate();
    const out = new Date(date.getFullYear(), date.getMonth(), 1);
    out.setMonth(out.getMonth() - wholeMonths);
    const monthEnd = new Date(out.getFullYear(), out.getMonth() + 1, 0).getDate();
    out.setDate(Math.min(day, monthEnd));
    return out;
  }

  function calendarDaysAgo(date, days) {
    const out = new Date(date.getTime());
    out.setDate(out.getDate() - Math.max(0, Math.round(Number(days) || 0)));
    return out;
  }

  function relativeDateBoundary(expression, asOf) {
    const text = raw(expression).replace(/\s+/g, "");
    if (!text || !asOf) return null;
    if (/本月/.test(text)) {
      const date = new Date(asOf.getFullYear(), asOf.getMonth(), 1);
      return { date, value: dateText(date), op: ">=", periodLabel: "本月" };
    }
    if (/今年|本年度/.test(text)) {
      const date = new Date(asOf.getFullYear(), 0, 1);
      return { date, value: dateText(date), op: ">=", periodLabel: "今年" };
    }
    if (/(?:最近|近|过去)(?:半年|半年度)|半年(?:以内|内|之内)/.test(text)) {
      const date = calendarMonthsAgo(asOf, 6);
      return { date, value: dateText(date), op: ">=", periodLabel: "最近6个月" };
    }
    if (/(?:最近|近|过去)(?:一季度|1季度)|(?:一季度|1季度)(?:以内|内|之内)/.test(text)) {
      const date = calendarMonthsAgo(asOf, 3);
      return { date, value: dateText(date), op: ">=", periodLabel: "最近3个月" };
    }
    const match = text.match(/(?:最近|近|过去)([0-9]+(?:\.[0-9]+)?|[一二两三四五六七八九十半]+)(天|日|周|星期|个?月|年)(?:以内|内|之内)?/)
      || text.match(/([0-9]+(?:\.[0-9]+)?|[一二两三四五六七八九十半]+)(天|日|周|星期|个?月|年)(?:以内|内|之内)/);
    if (!match) return null;
    const count = chineseNumber(match[1]);
    if (count === null || count < 0) return null;
    const unit = match[2];
    let date = null;
    let periodLabel = "";
    if (/月/.test(unit)) {
      date = calendarMonthsAgo(asOf, count);
      periodLabel = `最近${count}个月`;
    } else if (/年/.test(unit)) {
      date = calendarMonthsAgo(asOf, count * 12);
      periodLabel = `最近${count}年`;
    } else if (/周|星期/.test(unit)) {
      date = calendarDaysAgo(asOf, count * 7);
      periodLabel = `最近${count}周`;
    } else {
      date = calendarDaysAgo(asOf, count);
      periodLabel = `最近${count}天`;
    }
    return { date, value: dateText(date), op: ">=", periodLabel };
  }

  function establishedRelativeDateCondition(query, asOf) {
    const text = raw(query);
    if (!/(成立|设立|创建|新成立|新策略|新产品)/.test(text)) return null;
    return relativeDateBoundary(text, asOf);
  }

  const explicitDateFieldRules = Object.freeze([
    { field: "成立日期", aliases: ["成立日期", "成立时间", "成立日", "设立日期", "设立时间", "设立日"] },
    { field: "最新业绩日期", aliases: ["最新业绩日期", "业绩日期", "最新收益日期"] },
    { field: "收益数据截至", aliases: ["收益数据截至", "收益截至日期", "业绩截至日期"] },
    { field: "最新持仓日", aliases: ["最新持仓日", "最新持仓日期", "持仓日期"] },
    { field: "最近调仓日", aliases: ["最近调仓日", "最近调仓日期", "调仓日期"] },
  ]);

  function explicitDateOperator(value) {
    const text = raw(value).replace(/\s+/g, "");
    if (/^(?:大于等于|不早于|不少于|>=|≥)$/.test(text)) return ">=";
    if (/^(?:小于等于|不晚于|不超过|<=|≤)$/.test(text)) return "<=";
    if (/^(?:大于|晚于|之后|以后|>)$/.test(text)) return ">";
    if (/^(?:小于|早于|之前|以前|<)$/.test(text)) return "<";
    return "=";
  }

  function explicitDateFilters(query) {
    const text = raw(query);
    const operatorPattern = "(大于等于|小于等于|不早于|不晚于|不少于|不超过|之后|以后|之前|以前|晚于|早于|大于|小于|等于|为|是|>=|<=|≥|≤|>|<|=)?";
    const datePattern = "(\\d{4}(?:-|/|\\.|年)\\d{1,2}(?:-|/|\\.|月)\\d{1,2}日?)";
    const filters = [];
    explicitDateFieldRules.forEach((rule) => {
      if (!filterFieldNames().includes(rule.field)) return;
      for (const alias of rule.aliases) {
        const match = text.match(new RegExp(`${escapedRegExp(alias)}\\s*${operatorPattern}\\s*${datePattern}`));
        if (!match) continue;
        const date = dateFrom(match[2]);
        if (!date) break;
        const op = explicitDateOperator(match[1]);
        const value = dateText(date);
        filters.push({ field: rule.field, op, value, label: `${rule.field} ${op} ${value}` });
        break;
      }
    });
    return filters;
  }

  function chineseNumber(value) {
    const text = raw(value).replace(/\s+/g, "");
    if (!text) return null;
    if (/^\d+(\.\d+)?$/.test(text)) return Number(text);
    if (text === "半") return 0.5;
    const digit = { 零: 0, 一: 1, 二: 2, 两: 2, 三: 3, 四: 4, 五: 5, 六: 6, 七: 7, 八: 8, 九: 9 };
    if (digit[text] !== undefined) return digit[text];
    if (text === "十") return 10;
    const tenMatch = text.match(/^([一二两三四五六七八九])?十([一二两三四五六七八九])?$/);
    if (tenMatch) return (tenMatch[1] ? digit[tenMatch[1]] : 1) * 10 + (tenMatch[2] ? digit[tenMatch[2]] : 0);
    return null;
  }

  function normalizeQuery(text) {
    return raw(text).replace(/[，。；、]/g, " ").replace(/\s+/g, " ").trim();
  }

  function preferredFieldOrder() {
    return [
      "策略名称", "投顾机构", "渠道", "业务分类", "研报产品类型", "风险等级", "成立日期", "运作状态", "业绩完整", "业绩完整性", "数据完整性",
      "累计收益率", "近6月", "近1年", "近三月", "近一月", "近一周", "今年以来", "最大回撤", "当前回撤", "年化收益", "波动率",
      "市场地域", "主可比池", "主动被动", "特殊标签", "策略实现标签", "权益基金权重", "债券基金权重", "货币基金权重", "混合基金权重", "QDII权重",
      "最新业绩日期", "最新持仓日", "最近调仓日", "调仓次数", "年化投顾费率", "基准可用状态", "基础数据等级", "费率状态",
      "策略代码", "统一策略ID", "searchText",
    ];
  }

  function actualFieldNames() {
    const fields = new Set();
    allRows.forEach((row) => Object.keys(row || {}).forEach((key) => {
      if (!raw(key).startsWith("_")) fields.add(key);
    }));
    const preferred = preferredFieldOrder().filter((field) => fields.has(field));
    const rest = Array.from(fields)
      .filter((field) => !preferred.includes(field))
      .sort((a, b) => a.localeCompare(b, "zh-CN"));
    return [...preferred, ...rest];
  }

  let benchmarkFieldCache = null;
  function benchmarkFieldNames() {
    if (benchmarkFieldCache) return benchmarkFieldCache;
    benchmarkFieldCache = actualFieldNames().filter((field) => /基准/.test(field)
      && !/状态|置信度|分类档|资产大类|资产类别|权益|债券|商品|现金|其他|是否多元/.test(field));
    return benchmarkFieldCache;
  }

  function benchmarkText(row) {
    return benchmarkFieldNames().map((field) => raw(row?.[field])).filter(Boolean).join(" ");
  }

  function filterFieldNames() {
    return modelFilterFieldNames();
  }

  function fieldLabel(field) {
    const virtual = virtualFields.find((item) => item.field === field);
    if (virtual) return virtual.label;
    if (field === "searchText") return "搜索文本";
    return field || "未选择字段";
  }

  const businessRoutingById = new Map(businessRoutingCatalog.map((item) => [item.id, item]));
  const businessFieldCardCache = new Map();

  function businessRoutingPromptCatalog() {
    return businessRoutingCatalog.map((item) => ({
      id: item.id,
      label: item.label,
      aliases: item.aliases,
      definition: item.summary,
      relatedEntities: item.neighbors || [],
      executionSupport: item.support || "direct",
    }));
  }

  function localBusinessRoute(queryText) {
    const query = normalizeSearchText(queryText);
    const ranked = businessRoutingCatalog.map((item) => {
      const aliases = [...(item.aliases || []), item.label];
      const matches = aliases.filter((alias) => alias && query.includes(normalizeSearchText(alias)));
      const longest = Math.max(0, ...matches.map((alias) => normalizeSearchText(alias).length));
      return { item, score: matches.length * 10 + longest };
    }).filter((item) => item.score > 0).sort((a, b) => b.score - a.score);
    const selected = ranked.slice(0, 5).map(({ item, score }) => ({
      entityId: item.id,
      sourceText: item.label,
      confidence: Math.min(0.95, 0.55 + score / 100),
      source: "local-dictionary",
    }));
    if (!selected.some((item) => item.entityId === "strategy")) {
      selected.unshift({ entityId: "strategy", sourceText: "策略", confidence: 0.8, source: "default-subject" });
    }
    return selected.slice(0, 6);
  }

  function normalizeModelRoute(route, queryText) {
    const source = route && typeof route === "object" && !Array.isArray(route) ? route : {};
    const rawCandidates = Array.isArray(source.entityCandidates)
      ? source.entityCandidates
      : (Array.isArray(source.entities) ? source.entities : []);
    const candidates = [];
    rawCandidates.forEach((item) => {
      const entityId = raw(typeof item === "string" ? item : firstDefined(item, ["entityId", "id", "entity", "实体"])).trim();
      if (!businessRoutingById.has(entityId) || candidates.some((entry) => entry.entityId === entityId)) return;
      candidates.push({
        entityId,
        sourceText: raw(typeof item === "string" ? "" : firstDefined(item, ["sourceText", "text", "原文"])).trim(),
        confidence: Math.max(0, Math.min(1, modelNumber(typeof item === "string" ? null : item.confidence) ?? 0.7)),
        source: "model-route",
      });
    });
    localBusinessRoute(queryText).forEach((item) => {
      if (!candidates.some((entry) => entry.entityId === item.entityId)) candidates.push(item);
    });
    return {
      intent: raw(firstDefined(source, ["intent", "主意图"])).trim() || "筛选策略",
      entityCandidates: candidates.slice(0, 8),
      clauses: Array.isArray(source.clauses) ? source.clauses.slice(0, 12) : [],
      logic: source.logic && typeof source.logic === "object" ? source.logic : { type: "and" },
      unresolved: Array.isArray(source.unresolved) ? source.unresolved.slice(0, 8) : [],
      unmatchedSpans: Array.isArray(source.unmatchedSpans) ? source.unmatchedSpans.slice(0, 8) : [],
    };
  }

  function modelFilterFieldNames() {
    const actual = new Set(actualFieldNames());
    const names = [];
    businessRoutingCatalog.forEach((entity) => (entity.fields || []).forEach((field) => {
      if ((actual.has(field) || virtualFields.some((item) => item.field === field)) && !names.includes(field)) names.push(field);
    }));
    return names;
  }

  function businessFieldCard(field) {
    if (businessFieldCardCache.has(field)) return businessFieldCardCache.get(field);
    const values = field.startsWith("__")
      ? []
      : allRows.map((row) => row?.[field]).filter((value) => !isEmptyValue(value));
    const distinct = [...new Set(values.map((value) => raw(value).trim()).filter(Boolean))];
    const numericRatio = values.length ? values.filter((value) => num(value) !== null).length / values.length : 0;
    const dataType = isDateField(field)
      ? "date"
      : (numericRatio >= 0.9 || /收益|回撤|波动|夏普|权重|中枢|偏离|费率|换手率|次数|天数|基金数|指令数|事件数|置信度/.test(field))
        ? "number"
        : (distinct.length > 0 && distinct.length <= 30 ? "enum" : "text");
    const isPercent = /收益|回撤|波动|权重|中枢|偏离|费率|换手率|胜率|置信度/.test(field) && !/次数|数量/.test(field);
    const allowedOperators = dataType === "number" || dataType === "date"
      ? [">=", "<=", ">", "<", "=", "!=", "is empty", "is not empty"]
      : (dataType === "enum"
        ? ["=", "!=", "in", "not in", "contains_any", "is empty", "is not empty"]
        : ["contains", "not contains", "=", "!=", "is empty", "is not empty"]);
    const card = {
      field: fieldLabel(field),
      label: fieldLabel(field),
      definition: raw(summary.fieldDictionary?.[field]).slice(0, 180) || "当前策略筛选数据中的业务字段。",
      dataType,
      unit: isPercent ? "percent_point" : (dataType === "date" ? "YYYY-MM-DD" : "none"),
      valueFormat: dataType === "date" ? "YYYY-MM-DD" : (isPercent ? "number_in_percent_points" : dataType),
      relativeDatePolicy: dataType === "date" ? "相对日期由本地系统按查询基准日期换算；禁止把“最近三个月内”等原文直接写入日期字段值。" : undefined,
      allowedOperators,
      valueDictionary: dataType === "enum" ? distinct.slice(0, 24) : [],
      coverage: {
        nonEmpty: values.length,
        total: allRows.length,
        ratio: allRows.length ? Number((values.length / allRows.length).toFixed(4)) : 0,
      },
      nullPolicy: "空值表示当前没有可执行事实；普通正向筛选默认不命中，查询缺失时使用 is empty。",
    };
    businessFieldCardCache.set(field, card);
    return card;
  }

  function hydrateBusinessRoute(route, queryText) {
    const selectedIds = (route?.entityCandidates || []).map((item) => item.entityId).filter((id) => businessRoutingById.has(id));
    const expandedIds = [...selectedIds];
    selectedIds.forEach((id) => {
      const entity = businessRoutingById.get(id);
      (entity?.neighbors || []).slice(0, 2).forEach((neighborId) => {
        if (!expandedIds.includes(neighborId) && businessRoutingById.has(neighborId)) expandedIds.push(neighborId);
      });
    });
    const entityDetails = expandedIds.slice(0, 10).map((id) => {
      const entity = businessRoutingById.get(id);
      return {
        id: entity.id,
        label: entity.label,
        definition: entity.summary,
        executionSupport: entity.support || "direct",
        relatedEntities: entity.neighbors || [],
        fields: (entity.fields || []).filter((field) => modelFilterFieldNames().includes(field)).map(fieldLabel),
      };
    });
    const orderedFields = [];
    expandedIds.slice(0, 10).forEach((id) => {
      const entity = businessRoutingById.get(id);
      (entity?.fields || []).filter((field) => modelFilterFieldNames().includes(field)).forEach((field) => {
        if (!orderedFields.includes(field)) orderedFields.push(field);
      });
    });
    const semanticEntities = semanticEntityCatalog.filter((entity) => {
      const aliases = [entity.label, ...(entity.aliases || []), ...(entity.queryAliases || [])].filter(Boolean);
      return aliases.some((alias) => containsAlias(queryText, [alias]));
    }).slice(0, 12).map((entity) => ({
      key: entity.key,
      label: entity.label,
      type: entity.type,
      aliases: [...new Set([...(entity.aliases || []), ...(entity.queryAliases || [])])].slice(0, 12),
    }));
    return {
      catalogVersion: "ai-filter-two-stage-v1.20260808",
      entities: entityDetails,
      fieldCards: orderedFields.slice(0, 48).map(businessFieldCard),
      semanticEntities,
      globalRules: {
        defaultLogic: "AND",
        percentageInput: "用户说5%或5个点时，输出数值5；不得输出0.05。",
        dateHandling: "保留用户原始时间表达，标准日期由本地程序计算。",
        missingValue: "除非用户明确查询缺失，否则空值不命中正向条件。",
        unsupported: "具体排名名次、任意自定义区间曲线、基金级历史调仓动作若字段卡不存在，必须放入unsupportedConditions，不得伪造筛选字段。",
      },
    };
  }

  function zhCount(value) {
    return Number(value || 0).toLocaleString("zh-CN");
  }

  let flatSemanticHoldingCache = null;
  function flatSemanticHoldings() {
    if (flatSemanticHoldingCache) return flatSemanticHoldingCache;
    flatSemanticHoldingCache = [];
    semanticHoldingsByStrategy().forEach((rows) => {
      rows.forEach((holding) => {
        if ((num(holding.weight) || 0) > 0.0001) flatSemanticHoldingCache.push(holding);
      });
    });
    return flatSemanticHoldingCache;
  }

  function fundKeyOf(holding) {
    return raw(holding?.fundCode || holding?.fundName).trim();
  }

  function aiMiniTable(headers, rows, emptyText = "暂无数据") {
    return `<div class="table-wrap ai-help-table"><table><thead><tr>${headers.map((header) => `<th>${B.esc(header)}</th>`).join("")}</tr></thead><tbody>${
      rows.length
        ? rows.map((row) => `<tr>${headers.map((header) => `<td>${B.fmt(row[header])}</td>`).join("")}</tr>`).join("")
        : `<tr><td colspan="${headers.length}"><div class="empty">${B.esc(emptyText)}</div></td></tr>`
    }</tbody></table></div>`;
  }

  function strategyFieldDictionaryRows() {
    return actualFieldNames().map((field) => {
      const values = allRows.map((row) => row?.[field]).filter((value) => !isEmptyValue(value));
      const distinct = new Set(values.map((value) => raw(value).trim()).filter(Boolean));
      return {
        字段: fieldLabel(field),
        命中策略数: zhCount(values.length),
        去重值数: zhCount(distinct.size),
        说明: field === "searchText" ? "策略名称、机构、渠道、分类等信息的综合搜索" : "当前产品数据可用于筛选的业务维度",
      };
    });
  }

  function virtualFieldDictionaryRows() {
    const semanticStrategyCount = semanticHoldingsByStrategy().size;
    const semanticFundCount = new Set(flatSemanticHoldings().map(fundKeyOf).filter(Boolean)).size;
    return [
      { 字段: "业绩基准", 命中策略数: zhCount(allRows.filter((row) => benchmarkText(row)).length), 命中基金数: "-", 说明: "识别基准指数、资产名称及基准原文中的实体" },
      { 字段: "最新持仓", 命中策略数: zhCount(semanticStrategyCount), 命中基金数: zhCount(semanticFundCount), 说明: "识别当前持有的基金、基金公司、指数、行业主题和地域资产" },
      { 字段: "策略名称/投顾机构/渠道", 命中策略数: zhCount(allRows.length), 命中基金数: "-", 说明: "分别按名称、管理机构或销售渠道匹配，不再默认合并为持仓条件" },
      { 字段: "海外资产判断/权重", 命中策略数: zhCount(allRows.filter((row) => overseasEvidence(row).hasOverseas).length), 命中基金数: zhCount(new Set(flatSemanticHoldings().filter((holding) => /QDII|海外|全球|港股|美股|德国|日本|DAX|日经|Nikkei|TOPIX/i.test(holdingText(holding))).map(fundKeyOf).filter(Boolean)).size), 说明: "最新持仓快照和语义持仓索引共同核验" },
      { 字段: "黄金判断", 命中策略数: zhCount(allRows.filter((row) => goldEvidence(row).hasGold).length), 命中基金数: zhCount(new Set(flatSemanticHoldings().filter((holding) => /黄金|贵金属|商品黄金/i.test(holdingText(holding))).map(fundKeyOf).filter(Boolean)).size), 说明: "最新持仓快照和基金名称/分类证据共同核验" },
    ];
  }

  function semanticEntityStats(entity) {
    const resolved = resolveSemanticEntity(entity.key || entity.label) || entity;
    const aliases = [...new Set([...entityAliases(resolved, entity.label), ...(resolved?.evidenceAliases || [])].map(raw).filter(Boolean))];
    const holdingStrategyIds = new Set();
    const benchmarkStrategyIds = new Set();
    const nameStrategyIds = new Set();
    const fundKeys = new Set();
    strategyEntitiesByStrategy().forEach((rows, strategyId) => {
      const matched = rows.some((item) => item.entityKey === resolved?.key);
      if (matched) holdingStrategyIds.add(strategyId);
    });
    if (semanticIndex?.fundEntities?.rows && Array.isArray(semanticIndex.fundEntities.rows)) {
      const fields = semanticIndex.fundEntities.fields || [];
      const idxCode = fields.indexOf("基金代码");
      const idxName = fields.indexOf("基金名称");
      const idxKey = fields.indexOf("实体Key");
      if (idxKey >= 0) {
        semanticIndex.fundEntities.rows.forEach((row) => {
          if (raw(row[idxKey]) !== resolved?.key) return;
          const key = `${idxCode >= 0 ? raw(row[idxCode]) : ""}｜${idxName >= 0 ? raw(row[idxName]) : ""}`;
          if (key !== "｜") fundKeys.add(key);
        });
      }
    }
    if (!holdingStrategyIds.size || !fundKeys.size) {
      flatSemanticHoldings().forEach((holding) => {
        if (!entityMatchesEvidence(resolved, holdingText(holding), aliases)) return;
        if (holding.strategyId) holdingStrategyIds.add(holding.strategyId);
        const fundKey = fundKeyOf(holding);
        if (fundKey) fundKeys.add(fundKey);
      });
    }
    allRows.forEach((row) => {
      if (entityMatchesEvidence(resolved, benchmarkText(row), aliases)) benchmarkStrategyIds.add(raw(row.统一策略ID || row.策略代码 || row.策略名称));
      if (entityMatchesEvidence(resolved, raw(row.策略名称), aliases)) nameStrategyIds.add(raw(row.统一策略ID || row.策略代码 || row.策略名称));
    });
    return {
      实体: resolved?.label || entity.label || entity.key,
      类型: resolved?.type || entity.type || "标准实体",
      基准命中策略数: zhCount(benchmarkStrategyIds.size),
      持仓命中策略数: zhCount(holdingStrategyIds.size),
      名称命中策略数: zhCount(nameStrategyIds.size),
      关联基金数: zhCount(fundKeys.size),
      别名: aliases.slice(0, 8).join("、"),
    };
  }

  function semanticFrameworkRows() {
    return [
      { 识别步骤: "条件拆分", 业务判断: "区分收益、回撤、基准、持仓、名称、机构、渠道等条件", 页面结果: "每个条件独立展示" },
      { 识别步骤: "实体归一", 业务判断: "将指数、资产、行业主题、地域、基金公司等别名映射为标准实体", 页面结果: "保留用户原词并统一匹配" },
      { 识别步骤: "关系判断", 业务判断: "识别包含、排除、上下限、当前持仓等关系", 页面结果: "转换为可执行筛选条件" },
      { 识别步骤: "歧义处理", 业务判断: "没有明确关系词时生成2至3个同类候选", 页面结果: "默认最高匹配，可替换不叠加" },
      { 识别步骤: "结果核验", 业务判断: "逐条件计算筛除数，并展示策略命中依据", 页面结果: "可追溯筛选过程" },
    ];
  }

  function fundDimensionDictionaryRows(label, getter) {
    const buckets = new Map();
    flatSemanticHoldings().forEach((holding) => {
      const value = raw(getter(holding)).trim() || "未披露";
      if (!buckets.has(value)) buckets.set(value, { 维度项: value, strategyIds: new Set(), fundKeys: new Set(), totalWeight: 0 });
      const bucket = buckets.get(value);
      if (holding.strategyId) bucket.strategyIds.add(holding.strategyId);
      const fundKey = fundKeyOf(holding);
      if (fundKey) bucket.fundKeys.add(fundKey);
      bucket.totalWeight += num(holding.weight) || 0;
    });
    return Array.from(buckets.values())
      .map((bucket) => ({
        维度项: bucket.维度项,
        命中策略数: zhCount(bucket.strategyIds.size),
        命中基金数: zhCount(bucket.fundKeys.size),
        累计仓位权重: formatPct(bucket.totalWeight),
        字典来源: label,
      }))
      .sort((a, b) => Number(raw(b.命中策略数).replace(/,/g, "")) - Number(raw(a.命中策略数).replace(/,/g, "")) || a.维度项.localeCompare(b.维度项, "zh-CN"));
  }

  function renderFundDimensionBlock(title, rows, open = false) {
    return `<details class="ai-help-detail" ${open ? "open" : ""}>
      <summary>${B.esc(title)} <span>${zhCount(rows.length)} 项</span></summary>
      ${aiMiniTable(["维度项", "命中策略数", "命中基金数", "累计仓位权重", "字典来源"], rows)}
    </details>`;
  }

  function renderAiExplanation() {
    const strategyFields = strategyFieldDictionaryRows();
    const virtualFieldsRows = virtualFieldDictionaryRows();
    const semanticRows = semanticEntityCatalog.filter((entity) => isStandardSemanticEntityKey(entity.key)).map(semanticEntityStats)
      .sort((a, b) => {
        const total = (row) => [row.基准命中策略数, row.持仓命中策略数, row.名称命中策略数]
          .reduce((sum, value) => sum + Number(raw(value).replace(/,/g, "") || 0), 0);
        return total(b) - total(a) || a.实体.localeCompare(b.实体, "zh-CN");
      });
    const dimensionBlocks = [
      ["基金公司", fundDimensionDictionaryRows("最新持仓基金公司", (holding) => holding.fundCompany), false],
      ["基金资产类型", fundDimensionDictionaryRows("最新持仓资产类型", (holding) => holding.assetType), true],
      ["基金二级分类", fundDimensionDictionaryRows("最新持仓基金分类", (holding) => holding.secondaryCategory), true],
      ["基金分组", fundDimensionDictionaryRows("最新持仓基金分组", (holding) => holding.group), false],
      ["基金同类分组/主题", fundDimensionDictionaryRows("最新持仓同类分组", (holding) => holding.peerGroup), false],
      ["基金行业主题", fundDimensionDictionaryRows("基金公开分类与持仓信息", (holding) => holding.profileTheme), false],
      ["基金大类资产", fundDimensionDictionaryRows("基金公开分类与持仓信息", (holding) => holding.profileAsset), false],
      ["基金A股行业", fundDimensionDictionaryRows("基金公开分类与持仓信息", (holding) => holding.profileIndustry), false],
      ["持仓基金", fundDimensionDictionaryRows("最新持仓基金名称", (holding) => holding.fundName || holding.fundCode), false],
    ];
    return `<details class="panel ai-help-panel">
      <summary class="ai-help-summary">
        <div><h2>AI说明：可识别维度与实体字典</h2><p class="desc">按当前语义框架展示可识别业务域、标准实体及实际可匹配数量。</p></div>
        <div class="title-pills"><span class="pill">策略 ${zhCount(allRows.length)}</span><span class="pill">语义持仓 ${zhCount(flatSemanticHoldings().length)}</span></div>
      </summary>
      <div class="ai-help-body">
        <div class="ai-help-grid">
          <section>
            <h3>语义识别框架</h3>
            ${aiMiniTable(["识别步骤", "业务判断", "页面结果"], semanticFrameworkRows())}
          </section>
          <section>
            <h3>可识别业务维度</h3>
            ${aiMiniTable(["字段", "命中策略数", "命中基金数", "说明"], virtualFieldsRows)}
          </section>
        </div>
        <details class="ai-help-detail" open>
          <summary>标准实体字典 <span>${zhCount(semanticRows.length)} 项</span></summary>
          ${aiMiniTable(["实体", "类型", "基准命中策略数", "持仓命中策略数", "名称命中策略数", "关联基金数", "别名"], semanticRows)}
        </details>
        <details class="ai-help-detail">
          <summary>可直接筛选字段 <span>${zhCount(strategyFields.length)} 项</span></summary>
          ${aiMiniTable(["字段", "命中策略数", "去重值数", "说明"], strategyFields)}
        </details>
        ${dimensionBlocks.map(([title, rows, open]) => renderFundDimensionBlock(title, rows, open)).join("")}
      </div>
    </details>`;
  }

  function renderAiExplanationShell() {
    return `<details id="aiExplanationLazyPanel" class="panel ai-help-panel">
      <summary class="ai-help-summary">
        <div><h2>AI说明：可识别维度与实体字典</h2><p class="desc">默认折叠，展开后查看语义识别框架、业务维度和标准实体。</p></div>
        <div class="title-pills"><span class="pill">策略 ${zhCount(allRows.length)}</span><span class="pill">展开后加载</span></div>
      </summary>
      <div id="aiExplanationLazyBody" class="ai-help-body">
        <div class="empty">展开后加载筛选维度、实体字典和基金维度统计。</div>
      </div>
    </details>`;
  }

  function bindAiExplanationLazyLoad() {
    const panel = B.byId("aiExplanationLazyPanel");
    const body = B.byId("aiExplanationLazyBody");
    if (!panel || !body) return;
    panel.addEventListener("toggle", () => {
      if (!panel.open || panel.dataset.loaded === "1" || panel.dataset.loaded === "loading") return;
      panel.dataset.loaded = "loading";
      body.innerHTML = `<div class="empty">正在加载实体字典...</div>`;
      window.setTimeout(() => {
        const holder = document.createElement("div");
        holder.innerHTML = renderAiExplanation();
        const loadedBody = holder.querySelector(".ai-help-body");
        const loadedPills = holder.querySelector(".ai-help-summary .title-pills");
        if (loadedBody) body.innerHTML = loadedBody.innerHTML;
        else body.innerHTML = holder.innerHTML;
        const pills = panel.querySelector(".ai-help-summary .title-pills");
        if (pills && loadedPills) pills.innerHTML = loadedPills.innerHTML;
        panel.dataset.loaded = "1";
      }, 0);
    });
  }

  function renderInitialResultPlaceholder() {
    return `<section class="panel ai-result-placeholder">
      <div class="panel-head">
        <div>
          <h2>候选策略</h2>
          <p class="desc">页面已加载，默认示例会在首屏显示后用本地规则预览；点击“执行筛选”后仍以本地规则优先，仅在条件未识别或存在会改变结果的歧义时调用模型。</p>
        </div>
      </div>
      <div class="empty">正在准备本地筛选预览...</div>
    </section>`;
  }

  let initialSearchTimer = null;
  function scheduleInitialSearchPreview() {
    if (initialSearchTimer) window.clearTimeout(initialSearchTimer);
    const seq = state.searchSeq;
    initialSearchTimer = window.setTimeout(() => {
      initialSearchTimer = null;
      if (state.searchSeq !== seq) return;
      if (!B.byId("aiQuery") || !B.byId("aiResult")) return;
      runSearch({ allowModel: false });
    }, 120);
  }

  function optionHtml(options, selected) {
    const values = selected && !options.includes(selected) ? [selected, ...options] : options;
    return values.map((value) => `<option value="${B.esc(value)}"${value === selected ? " selected" : ""}>${B.esc(fieldLabel(value))}</option>`).join("");
  }

  function operatorOptionHtml(selected) {
    const options = operatorOptions.includes(selected) || !selected ? operatorOptions : [selected, ...operatorOptions];
    return options.map((value) => `<option value="${B.esc(value)}"${value === selected ? " selected" : ""}>${B.esc(operatorLabels[value] || value)}</option>`).join("");
  }

  function returnMetricScope(query) {
    return raw(query).replace(/年化(?:投顾)?费率|年化服务费率|年化管理费率|年化波动(?:率)?|年化换手(?:率)?/g, "");
  }

  function detectReturnMetric(query) {
    const returnQuery = returnMetricScope(query);
    if (/目标年化|年化收益|年化回报|年化收益率|年化/.test(returnQuery)) return { field: "年化收益", explicit: true };
    if (/近\s*(1|一)\s*年|最近\s*(1|一)\s*年|一年收益|1年收益/.test(returnQuery)) return { field: "近1年", explicit: true };
    if (/近\s*(6|六)\s*月|最近\s*(6|六)\s*月|半年收益|近半年|最近半年|近\s*半\s*年/.test(returnQuery)) return { field: "近6月", explicit: true };
    if (/近\s*(3|三)\s*月|最近\s*(3|三)\s*月|一季度|季度收益/.test(returnQuery)) return { field: "近三月", explicit: true };
    if (/近\s*(1|一)\s*月|最近\s*(1|一)\s*月|月收益/.test(returnQuery)) return { field: "近一月", explicit: true };
    if (/近\s*(1|一)\s*周|最近\s*(1|一)\s*周|周收益/.test(returnQuery)) return { field: "近一周", explicit: true };
    if (/今年|年初|YTD/i.test(returnQuery)) return { field: "今年以来", explicit: true };
    if (/累计|成立以来|总收益/.test(returnQuery)) return { field: "累计收益率", explicit: true };
    return { field: "累计收益率", explicit: false };
  }

  const thresholdUnitPattern = "\\s*(?:个百分点|个点|点|%|％|次|倍|年|个月|月|天|日|元|万元)?";

  function findThreshold(query, words, directions) {
    const number = "([0-9]+(?:\\.[0-9]+)?|[一二两三四五六七八九十半]+)";
    const unit = thresholdUnitPattern;
    const dir = directions.join("|");
    const gap = "[^\\s，。；、,.]{0,10}?";
    for (const word of words) {
      const alias = escapedRegExp(word);
      const pattern = new RegExp(`${alias}\\s*${gap}\\s*${number}${unit}\\s*(?:${dir})`);
      const reversePattern = new RegExp(`${alias}\\s*${gap}\\s*(?:${dir})\\s*${number}${unit}`);
      const match = query.match(reversePattern) || query.match(pattern);
      if (match) return chineseNumber(match[1]);
    }
    return null;
  }

  function findBareThreshold(query, words) {
    const number = "([0-9]+(?:\\.[0-9]+)?|[一二两三四五六七八九十半]+)";
    const unit = thresholdUnitPattern;
    const gap = "[^\\s，。；、,.]{0,8}?";
    for (const word of words) {
      const alias = escapedRegExp(word);
      const pattern = new RegExp(`${alias}\\s*${gap}\\s*${number}${unit}(?:\\s|$)`);
      const match = query.match(pattern);
      if (match) return chineseNumber(match[1]);
    }
    return null;
  }

  function findRangeThreshold(query, words) {
    const number = "([0-9]+(?:\\.[0-9]+)?|[一二两三四五六七八九十半]+)";
    const unit = thresholdUnitPattern;
    const gap = "[^\\s，。；、,.]{0,10}?";
    for (const word of words) {
      const alias = escapedRegExp(word);
      const pattern = new RegExp(`${alias}\\s*${gap}\\s*${number}${unit}\\s*(?:到|至|-|~|—)\\s*${number}${unit}(?:之间|区间|左右)?`);
      const match = query.match(pattern);
      if (!match) continue;
      const first = chineseNumber(match[1]);
      const second = chineseNumber(match[2]);
      if (first === null || second === null) continue;
      return { min: Math.min(first, second), max: Math.max(first, second) };
    }
    return null;
  }

  const minDirectionWords = ["以上", "以?上", "不低于", "不少于", "大于等于", "大于", "(?<!不)超过", "(?<!不)超", "至少", "(?<!不)高于", "不小于"];
  const maxDirectionWords = ["以内", "以下", "之内", "不超过", "不要超过", "别超过", "不高于", "小于等于", "(?<!不)小于", "(?<!不)低于", "最多", "至多", "控制在"];
  const numericIntentRules = [
    { field: "波动率", aliases: ["波动率", "年化波动", "波动"], defaultOp: "<=", unit: "%" },
    { field: "夏普比率", aliases: ["夏普比率", "夏普"], defaultOp: ">=" },
    { field: "年化投顾费率", aliases: ["年化投顾费率", "投顾费率", "投顾费", "服务费率", "费率"], defaultOp: "<=", unit: "%" },
    { field: "权益基金权重", aliases: ["权益基金权重", "权益仓位", "权益比例", "股票仓位", "股票基金权重"], unit: "%" },
    { field: "债券基金权重", aliases: ["债券基金权重", "债券仓位", "债券比例", "固收仓位"], unit: "%" },
    { field: "货币基金权重", aliases: ["货币基金权重", "货币仓位", "现金仓位", "现金比例"], unit: "%" },
    { field: "QDII权重", aliases: ["QDII权重", "QDII比例", "海外基金权重"], unit: "%" },
    { field: "指数基金权重", aliases: ["指数基金权重", "指数仓位", "被动基金权重", "ETF权重"], unit: "%" },
    { field: "调仓频率", aliases: ["调仓频率", "换仓频率"], defaultOp: "<=" },
    { field: "年化换手率", aliases: ["年化换手率", "换手率", "换手"], defaultOp: "<=", unit: "%" },
    { field: "最近一年调仓次数", aliases: ["最近一年调仓次数", "一年调仓次数", "近一年调仓次数"], defaultOp: "<=" },
  ];

  const categoricalIntentRules = [
    {
      field: "研报产品类型",
      values: [
        { value: "固收+型", aliases: ["固收+", "固收加", "固收增强型"] },
        { value: "纯债型", aliases: ["纯债", "短债", "纯债型", "短债型"] },
        { value: "股票型", aliases: ["股票型", "股票策略", "偏股型"] },
        { value: "多元配置型", aliases: ["多元配置型", "多元配置"] },
        { value: "股债混合型", aliases: ["股债混合", "股债混合型"] },
      ],
    },
    {
      field: "业务分类",
      values: [
        { value: "现金管理型", aliases: ["现金管理", "活钱", "零钱", "短期闲钱", "流动性管理"] },
        { value: "目标日期/养老型", aliases: ["养老", "目标日期", "退休", "生命周期"] },
        { value: "目标盈系列产品", aliases: ["目标盈", "目标收益", "止盈", "达标"] },
        { value: "海外/全球型", aliases: ["全球配置", "海外配置", "全球型", "海外型"] },
        { value: "主题/行业型", aliases: ["主题", "行业主题", "行业型"] },
        { value: "固收增强型", aliases: ["固收增强", "稳健增强"] },
        { value: "多资产配置型", aliases: ["多资产", "多资产配置"] },
      ],
    },
    {
      field: "市场地域",
      values: [
        { value: "国内", aliases: ["纯国内", "只投国内", "境内", "国内市场", "A股市场"] },
        { value: "海外/全球", aliases: ["纯海外", "海外市场", "全球市场"] },
        { value: "国内+海外", aliases: ["国内海外都要", "国内加海外", "国内+海外", "境内境外"] },
      ],
    },
    {
      field: "主动被动",
      values: [
        { value: "主动为主", aliases: ["主动管理", "主动为主", "主动策略"] },
        { value: "指数/被动为主", aliases: ["指数为主", "被动为主", "ETF为主", "指数策略", "被动策略"] },
        { value: "主动被动混合", aliases: ["主动被动混合", "主动加被动", "主动+被动"] },
      ],
    },
    {
      field: "运作状态",
      values: [
        { value: "正常运作", aliases: ["正常运作", "存续", "还在运作", "可买", "可投", "开放"] },
        { value: "已终止", aliases: ["已终止", "终止", "下架"] },
        { value: "公开披露", aliases: ["公开披露", "有公开披露"] },
      ],
    },
    {
      field: "基础数据等级",
      values: [
        { value: "A", aliases: ["A级数据", "数据A级", "基础数据A", "数据质量A"] },
        { value: "B", aliases: ["B级数据", "数据B级", "基础数据B"] },
      ],
    },
  ];

  const riskProfileRules = [
    { max: 1, aliases: ["超低风险", "现金型", "极低波", "不想亏", "保本倾向", "本金安全"] },
    { max: 2, aliases: ["保守", "低风险", "低波", "稳一点", "少亏", "风险低"] },
    { max: 3, aliases: ["稳健", "中低风险", "均衡稳健", "稳健型", "平衡型"] },
    { min: 4, aliases: ["成长", "进取", "积极", "高风险", "权益进取", "收益弹性"] },
  ];

  function filterKey(filter) {
    return `${filter.field}|${filter.op}|${filterValues(filter).join("|")}|${filter.unit || ""}`;
  }

  function filterValues(filter) {
    const source = Array.isArray(filter?.values) ? filter.values : (Array.isArray(filter?.value) ? filter.value : [filter?.value]);
    return source
      .flatMap((value) => raw(value).split(/\s*(?:\||、|，|,)\s*/))
      .map((value) => raw(value).trim())
      .filter(Boolean);
  }

  function isPositiveCategoricalFilter(filter) {
    if (!filter || filter.system || !singleValueCategoricalFields.has(filter.field)) return false;
    return ["=", "contains", "in", "contains_any"].includes(raw(filter.op || "contains"));
  }

  function isNegativeCategoricalFilter(filter) {
    if (!filter || filter.system || !singleValueCategoricalFields.has(filter.field)) return false;
    return ["!=", "not contains", "not in"].includes(raw(filter.op || "contains"));
  }

  function normalizeLogicalFilters(filters, parsed = null) {
    const result = [];
    const positiveGroups = new Map();
    const negativeValuesByField = new Map();
    (filters || []).forEach((filter) => {
      const normalized = { ...filter, label: filter.label || filterLabel(filter) };
      if (isNegativeCategoricalFilter(normalized)) {
        const set = negativeValuesByField.get(normalized.field) || new Set();
        filterValues(normalized).forEach((value) => set.add(value));
        negativeValuesByField.set(normalized.field, set);
        result.push(normalized);
        return;
      }
      if (isPositiveCategoricalFilter(normalized)) {
        const key = normalized.field;
        const existing = positiveGroups.get(key) || { field: normalized.field, values: [], filters: [], ambiguous: false, unit: normalized.unit || "" };
        filterValues(normalized).forEach((value) => {
          if (!existing.values.includes(value)) existing.values.push(value);
        });
        existing.filters.push(normalized);
        existing.ambiguous = existing.ambiguous || !!normalized.ambiguous;
        positiveGroups.set(key, existing);
        return;
      }
      result.push(normalized);
    });
    positiveGroups.forEach((group) => {
      const negativeValues = negativeValuesByField.get(group.field) || new Set();
      const values = group.values.filter((value) => !negativeValues.has(value));
      if (!values.length) {
        parsed?.warnings?.push(`${fieldLabel(group.field)}存在正向和排除条件冲突，已保留排除条件并移除正向条件。`);
        return;
      }
      if (values.length < group.values.length) {
        parsed?.warnings?.push(`${fieldLabel(group.field)}部分候选值同时被排除，已从正向筛选中移除：${group.values.filter((value) => negativeValues.has(value)).join("、")}。`);
      }
      if (values.length === 1) {
        const original = group.filters.find((filter) => filterValues(filter).includes(values[0])) || group.filters[0];
        result.push({ ...original, value: values[0], values: undefined, label: original.label || filterLabel({ ...original, value: values[0] }) });
        return;
      }
      const exact = group.filters.every((filter) => ["=", "in"].includes(raw(filter.op || "contains")));
      result.push({
        field: group.field,
        op: exact ? "in" : "contains_any",
        value: values.join("|"),
        values,
        unit: group.unit,
        label: `${fieldLabel(group.field)}${exact ? "等于任一" : "包含任一"}：${values.join("、")}`,
        logic: "or",
        ambiguous: group.ambiguous,
      });
    });
    return result;
  }

  function addGenericFilter(parsed, filter) {
    if (!filter?.field) return;
    parsed.thresholds.genericFilters = parsed.thresholds.genericFilters || [];
    const exists = parsed.thresholds.genericFilters.some((item) => filterKey(item) === filterKey(filter))
      || (parsed.filters || []).some((item) => filterKey(item) === filterKey(filter));
    if (!exists) parsed.thresholds.genericFilters.push(filter);
  }

  function addNumericIntentFilters(parsed, query) {
    numericIntentRules.forEach((rule) => {
      if (!filterFieldNames().includes(rule.field)) return;
      const range = findRangeThreshold(query, rule.aliases);
      if (range) {
        addGenericFilter(parsed, { field: rule.field, op: ">=", value: range.min, unit: rule.unit || "", label: `${rule.field} >= ${range.min}${rule.unit || ""}` });
        addGenericFilter(parsed, { field: rule.field, op: "<=", value: range.max, unit: rule.unit || "", label: `${rule.field} <= ${range.max}${rule.unit || ""}` });
        return;
      }
      const maxValue = findThreshold(query, rule.aliases, maxDirectionWords);
      const minValue = findThreshold(query, rule.aliases, minDirectionWords);
      if (maxValue !== null) addGenericFilter(parsed, { field: rule.field, op: "<=", value: maxValue, unit: rule.unit || "", label: `${rule.field} <= ${maxValue}${rule.unit || ""}` });
      if (minValue !== null) addGenericFilter(parsed, { field: rule.field, op: ">=", value: minValue, unit: rule.unit || "", label: `${rule.field} >= ${minValue}${rule.unit || ""}` });
      if (maxValue === null && minValue === null && rule.defaultOp) {
        const bare = findBareThreshold(query, rule.aliases);
        if (bare !== null) addGenericFilter(parsed, { field: rule.field, op: rule.defaultOp, value: bare, unit: rule.unit || "", label: `${rule.field} ${rule.defaultOp} ${bare}${rule.unit || ""}` });
      }
    });
  }

  function addCategoricalIntentFilters(parsed, query) {
    categoricalIntentRules.forEach((rule) => {
      if (!filterFieldNames().includes(rule.field)) return;
      rule.values.forEach((item) => {
        const alias = item.aliases.find((candidate) => containsAlias(query, [candidate]));
        if (!alias) return;
        if (rule.field === "研报产品类型" && !hasProductTypeContext(query)) return;
        if (rule.field === "业务分类" && !hasBusinessClassificationContext(query, item.value, alias)) return;
        if (rule.field === "主动被动" && alias === "指数策略"
          && detectSemanticEntities(query).some((entity) => /指数/.test(raw(entity.type)))
          && !/主动被动|被动为主|指数基金为主|ETF为主/.test(query)) return;
        const negative = hasNegativeCueForAlias(query, alias);
        const exact = /^(研报产品类型|市场地域|运作状态|基础数据等级)$/.test(rule.field);
        const op = negative ? (exact ? "!=" : "not contains") : (exact ? "=" : "contains");
        addGenericFilter(parsed, {
          field: rule.field,
          op,
          value: item.value,
          label: `${fieldLabel(rule.field)} ${operatorLabels[op] || op} ${item.value}`,
          ambiguous: false,
        });
      });
    });
  }

  function hasProductTypeContext(query) {
    const text = raw(query);
    return /策略类型|产品类型|研报产品|类型为|类别为|分类为|策略分类|产品分类/.test(text)
      || /(不要|不选|排除|剔除|非)\s*(固收\+?|固收加|纯债|短债|股票型?|多元配置|股债混合)/.test(text)
      || /(固收|纯债|短债|股票|多元|股债).{0,6}(策略|产品|组合)/.test(text)
      || /(策略|产品|组合).{0,6}(固收|纯债|短债|股票|多元|股债)/.test(text);
  }

  function hasBusinessClassificationContext(query, value, alias) {
    const text = raw(query);
    if (/业务分类|业务类型|产品分类|策略分类|分类为|类型为|类别为|客群类型|产品线/.test(text)) return true;
    if (/主题\/行业型|行业主题型|主题型产品|行业型产品/.test(text)) return value === "主题/行业型";
    if (/现金管理型|活钱产品|零钱产品|流动性管理产品/.test(text)) return value === "现金管理型";
    if (/目标日期|养老型|生命周期产品|养老产品/.test(text)) return value === "目标日期/养老型";
    if (/海外\/全球型|全球型产品|海外型产品/.test(text)) return value === "海外/全球型";
    if (/固收增强型|稳健增强型/.test(text)) return value === "固收增强型";
    if (/多资产配置型/.test(text)) return value === "多资产配置型";
    if (value === "主题/行业型" && /(持仓|配置|投资|涉及|含|包含|寻找|找).{0,12}(AI|人工智能|光模块|算力|半导体|石油|黄金|红利|新能源|美股|纳指|科技|医药|消费)/i.test(text)) return false;
    if (value === "主题/行业型" && /(AI|人工智能|光模块|算力|半导体|石油|黄金|红利|新能源|美股|纳指|科技|医药|消费).{0,8}(主题|相关|策略)/i.test(text)) return false;
    return false;
  }

  function addProductTypeContextFilters(parsed, query) {
    if (!hasProductTypeContext(query)) return;
    const text = raw(query);
    const addReportType = (value, alias) => {
      const negative = hasNegativeCueForAlias(text, alias);
      const op = negative ? "!=" : "=";
      addGenericFilter(parsed, {
      field: "研报产品类型",
      op,
      value,
      label: `研报产品类型 ${op} ${value}`,
      });
    };
    if (/固收(?!\+|加)/.test(text)) {
      addReportType("固收+型", "固收");
      addReportType("纯债型", "固收");
    }
    if (/固收\+|固收加/.test(text)) addReportType("固收+型", /固收\+/.test(text) ? "固收+" : "固收加");
    if (/纯债|短债/.test(text)) addReportType("纯债型", /纯债/.test(text) ? "纯债" : "短债");
    if (/股债/.test(text)) addReportType("股债混合型", "股债");
    if (/多元/.test(text)) addReportType("多元配置型", "多元");
    if (/股票|偏股/.test(text)) addReportType("股票型", /偏股/.test(text) ? "偏股" : "股票");
  }

  function isProductTypeEntityInProductContext(entity, query) {
    if (!hasProductTypeContext(query)) return false;
    const text = `${entity?.label || ""} ${entity?.term || ""}`;
    return /固收|纯债|短债|债券|股票|多元配置|股债混合|现金管理/.test(text);
  }

  function addRiskProfileFilters(parsed, query) {
    const explicitRisk = query.match(/R\s*([0-5]).{0,6}?(以内|以下|不超过|不高于|及以下|以下风险|以内风险)/i)
      || query.match(/风险.{0,4}?([0-5])\s*级.{0,4}?(以内|以下|不超过|不高于|及以下)/);
    if (explicitRisk) {
      addGenericFilter(parsed, { field: "风险等级序号", op: "<=", value: Number(explicitRisk[1]), label: `风险等级 <= R${explicitRisk[1]}` });
      return;
    }
    const explicitRiskMin = query.match(/R\s*([0-5]).{0,6}?(以上|以?上|不低于|高于|及以上|以上风险)/i)
      || query.match(/风险.{0,4}?([0-5])\s*级.{0,4}?(以上|以?上|不低于|高于|及以上)/);
    if (explicitRiskMin) {
      addGenericFilter(parsed, { field: "风险等级序号", op: ">=", value: Number(explicitRiskMin[1]), label: `风险等级 >= R${explicitRiskMin[1]}` });
      return;
    }
    const exactRisk = query.match(/R\s*([0-5])\b/i);
    if (exactRisk) {
      addGenericFilter(parsed, { field: "风险等级序号", op: "=", value: Number(exactRisk[1]), label: `风险等级 = R${exactRisk[1]}` });
      return;
    }
    riskProfileRules.forEach((rule) => {
      const alias = rule.aliases.find((candidate) => containsAlias(query, [candidate]));
      if (!alias) return;
      const negative = hasNegativeCueForAlias(query, alias);
      if (negative && rule.min !== undefined) {
        addGenericFilter(parsed, { field: "风险等级序号", op: "<=", value: Math.max(0, rule.min - 1), label: `排除高风险，风险等级 <= R${Math.max(0, rule.min - 1)}` });
        return;
      }
      if (negative && rule.max !== undefined) {
        addGenericFilter(parsed, { field: "风险等级序号", op: ">=", value: Math.min(5, rule.max + 1), label: `排除低风险，风险等级 >= R${Math.min(5, rule.max + 1)}` });
        return;
      }
      if (rule.max !== undefined) addGenericFilter(parsed, { field: "风险等级序号", op: "<=", value: rule.max, label: `风险偏好 <= R${rule.max}` });
      if (rule.min !== undefined) addGenericFilter(parsed, { field: "风险等级序号", op: ">=", value: rule.min, label: `风险偏好 >= R${rule.min}` });
    });
  }

  function extractEntityTerm(query) {
    const noise = /成立|回撤|收益|回报|持仓|配置|海外|全球|港股|美股|黄金|纳指|纳斯达克|标普|沪深|中证|QDII|固收|纯债|股票|股债|多元|策略|产品|当前|最大|最近|近\d|近一|近三|近六|近半|今年|年初|以内|以上|以下|不含|排除/;
    const segments = raw(query).split(/\s+/).map((item) => item.trim()).filter(Boolean);
    for (const segment of segments) {
      if (noise.test(segment)) continue;
      let match = segment.match(/^(?:找|我要找|只看|仅看|限定|筛选)?(.+?)(?:的)?(?:投顾产品|投顾策略|投顾组合|投顾|机构)$/);
      if (!match) match = segment.match(/^(?:找|我要找|只看|仅看|限定|筛选)([\u4e00-\u9fa5A-Za-z0-9·（）()]{2,})$/);
      if (!match) continue;
      const term = raw(match[1]).replace(/^(找|我要找|只看|仅看|限定|筛选)/, "").replace(/的$/, "").trim();
      if (term.length >= 2 && !noise.test(term)) return term;
    }
    return "";
  }

  function semanticEntityHasAncestor(childKey, ancestorKey, seen = new Set()) {
    if (!childKey || !ancestorKey || childKey === ancestorKey || seen.has(childKey)) return false;
    seen.add(childKey);
    const entity = resolveSemanticEntity(childKey);
    return (entity?.parentKeys || []).some((parentKey) => parentKey === ancestorKey || semanticEntityHasAncestor(parentKey, ancestorKey, seen));
  }

  function isStandardSemanticEntityKey(key) {
    return !!raw(key) && !raw(key).includes(":");
  }

  function semanticMatchSpecificity(item) {
    const entity = resolveSemanticEntity(item.key || item.term || item.label);
    const key = raw(entity?.key || item.key);
    const term = normalizeSearchText(item.term || item.label || "");
    let depth = 0;
    const walk = (entityKey, seen = new Set()) => {
      if (!entityKey || seen.has(entityKey)) return 0;
      seen.add(entityKey);
      const current = resolveSemanticEntity(entityKey);
      const parentDepth = Math.max(0, ...(current?.parentKeys || []).map((parentKey) => walk(parentKey, seen) + 1));
      return parentDepth;
    };
    depth = walk(key);
    const exactLabel = normalizeSearchText(entity?.label || "") === term ? 1 : 0;
    const exactAlias = (entity?.aliases || []).some((alias) => normalizeSearchText(alias) === term) ? 1 : 0;
    const staticScore = isStandardSemanticEntityKey(key) ? 100 : 0;
    return depth * 10 + exactLabel * 4 + exactAlias * 2 + staticScore;
  }

  function shouldReplaceSemanticTermMatch(existing, item) {
    const existingKey = raw(existing?.key);
    const itemKey = raw(item?.key);
    const existingStandard = isStandardSemanticEntityKey(existingKey);
    const itemStandard = isStandardSemanticEntityKey(itemKey);
    if (existingStandard !== itemStandard) return itemStandard;
    if (existingKey && itemKey) {
      if (semanticEntityHasAncestor(itemKey, existingKey)) return true;
      if (semanticEntityHasAncestor(existingKey, itemKey)) return false;
    }
    return semanticMatchSpecificity(item) > semanticMatchSpecificity(existing);
  }

  function detectSemanticEntities(query) {
    const text = raw(query);
    const detections = [];
    semanticEntityCatalog.forEach((entity) => {
      const matchedAlias = (entity.aliases || []).find((alias) => containsAlias(text, [alias]));
      if (!matchedAlias) return;
      const negative = hasNegativeCueForAlias(text, matchedAlias);
      detections.push({
        key: entity.key,
        label: entity.label,
        type: entity.type,
        term: matchedAlias,
        negative,
        note: entity.note,
      });
    });
    const byTerm = new Map();
    detections.forEach((item) => {
      const termKey = `${normalizeSearchText(item.term)}|${item.negative ? "neg" : "pos"}`;
      const existing = byTerm.get(termKey);
      if (!existing) {
        byTerm.set(termKey, item);
        return;
      }
      if (shouldReplaceSemanticTermMatch(existing, item)) byTerm.set(termKey, item);
    });
    const resolved = Array.from(byTerm.values());
    const hasSpecificIndex = resolved.some((item) => isStandardSemanticEntityKey(item.key) && /指数/.test(raw(item.type)));
    if (!hasSpecificIndex) return resolved;
    return resolved.filter((item) => {
      const broadIndex = normalizeSearchText(item.term) === "指数"
        || /^(指数基金|股票指数|债券指数|指数型)$/.test(raw(item.label));
      return !broadIndex;
    });
  }

  function addLowOverseasPreferenceFilter(parsed, query) {
    if (!hasLowOverseasPreference(query)) return;
    const explicitMax = findThreshold(
      query,
      ["海外配置中枢", "海外配置", "海外资产权重", "海外权重", "QDII权重", "QDII比例"],
      maxDirectionWords
    );
    const maxValue = explicitMax === null ? 10 : explicitMax;
    parsed.thresholds.preferLowOverseas = true;
    addGenericFilter(parsed, {
      field: "海外配置中枢",
      op: "<=",
      value: maxValue,
      unit: "%",
      label: `海外配置中枢 <= ${maxValue}%`,
    });
    if (explicitMax === null) {
      parsed.assumptions.push("“海外少一点”未给出具体阈值，默认按海外配置中枢不超过10%筛选，可在已识别条件中微调。");
    }
  }

  const semanticDomainDefinitions = {
    benchmark: { label: "业绩基准", relation: "基准包含", cues: ["业绩基准", "比较基准", "基准"] },
    latest_holding: { label: "最新持仓", relation: "当前配置", cues: ["最新持仓", "当前持仓", "现有持仓", "持仓", "持有", "配置"] },
    strategy_name: { label: "策略名称", relation: "名称包含", cues: ["策略名称", "产品名称", "组合名称", "名称"] },
    advisor: { label: "投顾机构", relation: "机构包含", cues: ["投顾机构", "管理机构", "投顾公司", "机构"] },
    channel: { label: "销售渠道", relation: "渠道包含", cues: ["销售渠道", "渠道", "平台"] },
  };

  function semanticCueDistance(query, term, cue) {
    const text = raw(query);
    const termIndex = text.indexOf(term);
    if (termIndex < 0) return Number.POSITIVE_INFINITY;
    let distance = Number.POSITIVE_INFINITY;
    let cueIndex = text.indexOf(cue);
    while (cueIndex >= 0) {
      distance = Math.min(distance, Math.abs(cueIndex - termIndex));
      cueIndex = text.indexOf(cue, cueIndex + Math.max(1, cue.length));
    }
    return distance;
  }

  function explicitSemanticDomain(query, detection) {
    const ranked = Object.entries(semanticDomainDefinitions)
      .map(([domain, definition]) => ({
        domain,
        distance: Math.min(...definition.cues.map((cue) => semanticCueDistance(query, detection.term, cue))),
      }))
      .filter((item) => Number.isFinite(item.distance) && item.distance <= 14)
      .sort((a, b) => a.distance - b.distance);
    return ranked[0]?.domain || "";
  }

  function semanticDomainOrder(entity, explicitDomain = "") {
    const type = raw(entity?.type);
    let domains = /基金公司/.test(type)
      ? ["advisor", "latest_holding", "channel"]
      : ["latest_holding", "benchmark", "strategy_name"];
    if (/指数/.test(type)) domains = ["benchmark", "latest_holding", "strategy_name"];
    if (explicitDomain) domains = [explicitDomain, ...domains.filter((domain) => domain !== explicitDomain)];
    return domains.slice(0, 3);
  }

  function semanticDomainScore(entity, domain, explicitDomain = "") {
    if (explicitDomain) return domain === explicitDomain ? 0.99 : 0.42;
    const type = raw(entity?.type);
    if (/指数/.test(type)) return { benchmark: 0.84, latest_holding: 0.72, strategy_name: 0.38 }[domain] || 0.3;
    if (/基金公司/.test(type)) return { advisor: 0.78, latest_holding: 0.72, channel: 0.5 }[domain] || 0.3;
    return { latest_holding: 0.82, benchmark: 0.56, strategy_name: 0.42, advisor: 0.4, channel: 0.35 }[domain] || 0.3;
  }

  function semanticCandidateFilter(group, domain, score) {
    const entity = resolveSemanticEntity(group.entityKey || group.term) || {
      key: group.entityKey,
      label: group.entityLabel,
      aliases: [group.term],
    };
    const aliases = entityAliases(entity, group.term);
    const negative = !!group.negative;
    const common = {
      semanticGroupId: group.id,
      semanticDomain: domain,
      semanticEntity: entity.key || group.entityKey || "",
      semanticLabel: entity.label || group.entityLabel || group.term,
      sourceText: group.sourceText || group.term,
      confidence: score,
      ambiguous: !!group.needsConfirmation,
    };
    if (domain === "latest_holding") {
      const hasWeight = !negative && group.minWeight !== undefined;
      return {
        ...common,
        field: "__holding_entity",
        op: negative ? "not contains" : (hasWeight ? "weight_gte" : "contains"),
        value: group.term || entity.aliases?.[0] || entity.key,
        aliases,
        minWeight: group.minWeight,
        matchMode: "single",
        relation: hasWeight ? "latest_holdings_weight" : "latest_holdings_exists",
        label: hasWeight
          ? `最新持仓含${entity.label || group.term}且权重 >= ${group.minWeight}%`
          : `最新持仓${negative ? "不含" : "包含"}${entity.label || group.term}`,
      };
    }
    const value = negative ? (group.term || aliases[0]) : aliases.join("|");
    const op = negative ? "not contains" : "contains_any";
    if (domain === "benchmark") return { ...common, field: "__benchmark_text", op, value, values: negative ? undefined : aliases, label: `业绩基准${negative ? "不包含" : "包含"}${entity.label || group.term}` };
    if (domain === "strategy_name") return { ...common, field: "策略名称", op, value, values: negative ? undefined : aliases, label: `策略名称${negative ? "不包含" : "包含"}${entity.label || group.term}` };
    if (domain === "advisor") return { ...common, field: "投顾机构", op, value, values: negative ? undefined : aliases, label: `投顾机构${negative ? "不包含" : "包含"}${entity.label || group.term}` };
    return { ...common, field: "渠道", op, value, values: negative ? undefined : aliases, label: `销售渠道${negative ? "不包含" : "包含"}${entity.label || group.term}` };
  }

  function createSemanticConditionGroup(query, detection, index, preferredDomain = "", minWeight = undefined) {
    const entity = resolveSemanticEntity(detection.key || detection.term) || detection;
    const explicitDomain = explicitSemanticDomain(query, detection);
    const domainOrder = semanticDomainOrder(entity, explicitDomain || preferredDomain);
    if (minWeight !== undefined && domainOrder.includes("latest_holding")) {
      domainOrder.splice(0, domainOrder.length, "latest_holding");
    }
    const id = `semantic-${index}-${raw(entity.key || detection.term).replace(/[^A-Za-z0-9_\-\u4e00-\u9fa5]/g, "-")}`;
    const group = {
      id,
      kind: "entity",
      sourceText: detection.term,
      entityKey: entity.key || detection.key || "",
      entityLabel: entity.label || detection.label || detection.term,
      entityType: entity.type || detection.type || "实体",
      term: detection.term || entity.label || entity.key,
      negative: !!detection.negative,
      minWeight,
      explicitDomain: explicitDomain || "",
      needsConfirmation: !explicitDomain && domainOrder.length > 1,
      selectedId: "",
      userSelected: false,
      candidates: [],
    };
    group.candidates = domainOrder.map((domain) => {
      const confidence = semanticDomainScore(entity, domain, explicitDomain || preferredDomain);
      const candidateId = `${id}-${domain}`;
      const filter = semanticCandidateFilter(group, domain, confidence);
      return {
        id: candidateId,
        domain,
        domainLabel: semanticDomainDefinitions[domain]?.label || domain,
        relationLabel: semanticDomainDefinitions[domain]?.relation || "包含",
        confidence,
        filter: { ...filter, semanticCandidateId: candidateId },
      };
    }).sort((a, b) => b.confidence - a.confidence);
    group.selectedId = group.candidates[0]?.id || "";
    return group;
  }

  function selectedSemanticCandidate(group) {
    return group?.candidates?.find((candidate) => candidate.id === group.selectedId) || group?.candidates?.[0] || null;
  }

  function semanticGroupFilters(parsed) {
    return (parsed.semanticGroups || []).map((group) => selectedSemanticCandidate(group)?.filter).filter(Boolean);
  }

  function normalizeHoldingEntityConflicts(parsed) {
    const thresholds = parsed.thresholds || {};
    const entities = Array.isArray(thresholds.holdingEntities) ? thresholds.holdingEntities : [];
    const byEntity = new Map();
    entities.forEach((item) => {
      const resolved = resolveSemanticEntity(item.key || item.term || item.label);
      const key = resolved?.key || raw(item.key || item.term || item.label).trim();
      if (!key) return;
      if (key === "gold" && thresholds.excludeGold) return;
      if (key === "overseas" && (thresholds.includeOverseas || thresholds.excludeQdii)) return;
      const normalized = {
        ...item,
        key: resolved?.key || item.key || "",
        label: resolved?.label || item.label || item.term,
        term: item.term || resolved?.aliases?.[0] || item.key || item.label,
        negative: !!item.negative,
        note: resolved?.note || item.note || "",
      };
      const existing = byEntity.get(key);
      if (!existing || (normalized.negative && !existing.negative)) byEntity.set(key, normalized);
    });
    Array.from(byEntity.entries()).forEach(([key, item]) => {
      const resolved = resolveSemanticEntity(key);
      (resolved?.parentKeys || []).forEach((parentKey) => {
        const parent = byEntity.get(parentKey);
        if (parent && !!parent.negative === !!item.negative && normalizeSearchText(parent.term || parent.label) !== normalizeSearchText(parent.label || "")) {
          byEntity.delete(parentKey);
        }
      });
    });
    thresholds.holdingEntities = Array.from(byEntity.values());
    return parsed;
  }

  function hasBusinessFilter(parsed) {
    const thresholds = parsed.thresholds || {};
    return thresholds.minAgeYears !== undefined
      || thresholds.maxDrawdown !== undefined
      || thresholds.minReturn !== undefined
      || thresholds.excludeGold
      || thresholds.excludeQdii
      || thresholds.includeOverseas
      || thresholds.onlyGf
      || thresholds.excludeGf
      || thresholds.gfTerm
      || thresholds.entityTerm
      || (thresholds.holdingEntities || []).length
      || (parsed.semanticGroups || []).length
      || thresholds.holdingEntityWeightMin !== undefined
      || (thresholds.genericFilters || []).length
      || thresholds.reportType
      || (parsed.filters || []).some((filter) => !filter.system);
  }

  function buildFilterList(parsed) {
    const thresholds = parsed.thresholds || {};
    const filters = [];
    if (parsed.completeOnly) {
      filters.push({ field: "业绩完整", op: "=", value: "是", label: "仅业绩完整策略", system: true });
    }
    if (thresholds.minAgeYears !== undefined) {
      filters.push({ field: "成立日期", op: "<=", value: dateText(thresholds.launchBefore), label: `成立满${thresholds.minAgeYears}年` });
    }
    if (thresholds.maxDrawdown !== undefined) {
      const drawdownField = thresholds.drawdownField || "最大回撤";
      filters.push({ field: drawdownField, op: "<=", value: thresholds.maxDrawdown, unit: "%", label: `${drawdownField} <= ${thresholds.maxDrawdown}%` });
    }
    if (thresholds.minReturn !== undefined) {
      filters.push({ field: parsed.returnMetric.field, op: ">=", value: thresholds.minReturn, unit: "%", label: `${parsed.returnMetric.field} >= ${thresholds.minReturn}%` });
    }
    if (thresholds.excludeGold) filters.push({ field: "黄金判断", op: "=", value: "未持有", label: "持仓不含黄金" });
    if (thresholds.excludeQdii) filters.push({ field: "QDII权重", op: "<=", value: 0, unit: "%", label: "不含QDII/海外" });
    if (thresholds.includeOverseas) filters.push({ field: "海外资产权重", op: ">", value: 0, unit: "%", label: "含海外资产" });
    if (thresholds.onlyGf) filters.push({ field: "投顾机构", op: "contains", value: "广发", label: "仅看广发" });
    if (thresholds.excludeGf) filters.push({ field: "投顾机构", op: "not contains", value: "广发", label: "排除广发" });
    if (thresholds.gfTerm) filters.push({ field: "__gf_any", op: "contains", value: thresholds.gfTerm, label: `${fieldLabel("__gf_any")}包含 ${thresholds.gfTerm}`, ambiguous: true });
    if (thresholds.entityTerm) filters.push({ field: "__source_any", op: "contains", value: thresholds.entityTerm, label: `${fieldLabel("__source_any")}包含 ${thresholds.entityTerm}`, ambiguous: true });
    (thresholds.genericFilters || []).forEach((filter) => {
      const exists = filters.some((item) => filterKey(item) === filterKey(filter));
      if (!exists) filters.push({ ...filter, label: filter.label || filterLabel(filter) });
    });
    const holdingEntities = thresholds.holdingEntities || [];
    const positiveHoldingEntities = holdingEntities.filter((entity) => !entity.negative);
    const negativeHoldingEntities = holdingEntities.filter((entity) => entity.negative);
    const holdingWeightMin = thresholds.holdingEntityWeightMin;
    const relationMode = holdingEntityRelationMode(parsed.query, positiveHoldingEntities);
    const weightMode = holdingEntityWeightMode(parsed.query);
    const pushHoldingEntityFilter = (entity) => {
      const resolved = resolveSemanticEntity(entity.key || entity.term);
      const label = `${entity.negative ? "最新持仓不含" : "最新持仓含"}${resolved?.label || entity.label || entity.term}`;
      filters.push({
        field: "__holding_entity",
        op: entity.negative ? "not contains" : (holdingWeightMin !== undefined ? "weight_gte" : "contains"),
        value: entity.term || resolved?.aliases?.[0] || entity.key,
        label: holdingWeightMin !== undefined && !entity.negative ? `${label}且权重 >= ${holdingWeightMin}%` : label,
        semanticEntity: resolved?.key || entity.key || "",
        aliases: entityAliases(resolved, entity.term),
        minWeight: holdingWeightMin,
        matchMode: "single",
        relation: "latest_holdings_exists",
        ambiguous: true,
      });
    };
    if (positiveHoldingEntities.length > 1) {
      const labels = positiveHoldingEntities.map((entity) => {
        const resolved = resolveSemanticEntity(entity.key || entity.term);
        return resolved?.label || entity.label || entity.term || entity.key;
      }).filter(Boolean);
      const op = holdingWeightMin !== undefined ? "weight_gte" : (relationMode === "any" ? "contains_any" : "contains_all");
      const matchMode = holdingWeightMin !== undefined ? (weightMode === "each" ? "all" : "sum") : relationMode;
      const prefix = holdingWeightMin !== undefined
        ? (matchMode === "all" ? "最新持仓分别满足" : "最新持仓合计权重")
        : (relationMode === "any" ? "最新持仓含任一" : "最新持仓同时含");
      filters.push({
        field: "__holding_entity",
        op,
        value: labels.join("|"),
        label: holdingWeightMin !== undefined ? `${prefix}：${labels.join("、")} >= ${holdingWeightMin}%` : `${prefix}：${labels.join("、")}`,
        anyOfEntities: positiveHoldingEntities,
        minWeight: holdingWeightMin,
        matchMode,
        relation: holdingWeightMin !== undefined ? "latest_holdings_weight" : (relationMode === "any" ? "latest_holdings_exists_any" : "latest_holdings_exists_all"),
        ambiguous: true,
      });
    } else {
      positiveHoldingEntities.forEach(pushHoldingEntityFilter);
    }
    negativeHoldingEntities.forEach(pushHoldingEntityFilter);
    semanticGroupFilters(parsed).forEach((filter) => {
      const exists = filters.some((item) => item.semanticGroupId === filter.semanticGroupId || filterKey(item) === filterKey(filter));
      if (!exists) filters.push(filter);
    });
    if (thresholds.reportType) {
      const filter = { field: "研报产品类型", op: "=", value: thresholds.reportType, label: thresholds.reportType };
      if (!filters.some((item) => filterKey(item) === filterKey(filter))) filters.push(filter);
    }
    return normalizeLogicalFilters(filters, parsed);
  }

  function parseQuery(queryText) {
    const query = normalizeQuery(queryText);
    const asOf = asOfDate();
    const parsed = {
      query,
      asOf,
      assumptions: [],
      warnings: [],
      filters: [],
      semanticGroups: [],
      completeOnly: state.completeOnly,
      returnMetric: detectReturnMetric(query),
      thresholds: {},
    };

    const recentEstablished = establishedRelativeDateCondition(query, asOf);
    if (recentEstablished) {
      parsed.thresholds.establishedSince = recentEstablished.value;
      parsed.thresholds.establishedRelativePeriod = recentEstablished.periodLabel;
      addGenericFilter(parsed, {
        field: "成立日期",
        op: ">=",
        value: recentEstablished.value,
        label: `成立日期 >= ${recentEstablished.value}`,
        relativeDateSource: recentEstablished.periodLabel,
      });
      parsed.assumptions.push(`“${recentEstablished.periodLabel}内成立”以查询基准日 ${dateText(asOf)} 计算，转换为成立日期不早于 ${recentEstablished.value}。`);
    }

    explicitDateFilters(query).forEach((filter) => addGenericFilter(parsed, filter));

    const ageMatch = recentEstablished ? null : query.match(/成立.{0,6}?([0-9]+(?:\.[0-9]+)?|[一二两三四五六七八九十半]+)\s*年\s*(?:以上|以?上|满|超过|大于|不低于)?/);
    if (ageMatch) {
      const years = chineseNumber(ageMatch[1]);
      if (years !== null) {
        parsed.thresholds.minAgeYears = years;
        parsed.thresholds.launchBefore = yearsAgo(asOf, years);
        parsed.filters.push({ field: "成立日期", op: "<=", value: dateText(parsed.thresholds.launchBefore), label: `成立满${years}年` });
      }
    }

    const maxDrawdown = findThreshold(query, ["最大回撤", "回撤", "最大亏损", "亏损", "下跌", "跌幅"], maxDirectionWords);
    if (maxDrawdown !== null) {
      const drawdownField = /当前回撤|现时回撤/.test(query) ? "当前回撤" : "最大回撤";
      parsed.thresholds.drawdownField = drawdownField;
      parsed.thresholds.maxDrawdown = maxDrawdown;
      parsed.filters.push({ field: drawdownField, op: "<=", value: maxDrawdown, unit: "%", label: `${drawdownField} <= ${maxDrawdown}%` });
      if (drawdownField === "最大回撤" && !/最大回撤/.test(query)) parsed.assumptions.push("回撤未指定“最大/当前”，默认按最大回撤筛选。");
    }

    const returnQuery = returnMetricScope(query);
    let minReturn = findThreshold(returnQuery, ["目标年化", "年化收益率", "年化收益", "年化回报", "年化", "收益率", "收益", "回报"], minDirectionWords);
    if (minReturn === null) minReturn = findBareThreshold(returnQuery, ["目标年化", "年化收益率", "年化收益", "年化回报", "年化"]);
    if (minReturn !== null) {
      parsed.thresholds.minReturn = minReturn;
      parsed.filters.push({ field: parsed.returnMetric.field, op: ">=", value: minReturn, unit: "%", label: `${parsed.returnMetric.field} >= ${minReturn}%` });
      if (!parsed.returnMetric.explicit) parsed.assumptions.push("收益率未指定区间，默认使用累计收益率。");
    }

    addNumericIntentFilters(parsed, query);
    addCategoricalIntentFilters(parsed, query);
    addProductTypeContextFilters(parsed, query);
    addRiskProfileFilters(parsed, query);
    addKycRecommendationFilters(parsed, query);
    addLowOverseasPreferenceFilter(parsed, query);

    const goldEntity = semanticEntityCatalog.find((entity) => entity.key === "gold");
    if (goldEntity && hasNegativeCueForEntity(query, goldEntity)) {
      parsed.thresholds.excludeGold = true;
      parsed.filters.push({ field: "是否含黄金", op: "=", value: false, label: "持仓不含黄金" });
      if (holdingEvidenceByStrategy().size) {
        parsed.assumptions.push("黄金持仓按最新持仓快照的“研报大类资产/行业主题”分类核验，缺持仓证据的策略不入选。");
      } else {
        parsed.warnings.push("未加载持仓快照包，无法核验黄金持仓；该条件会排除缺少持仓证据的策略。");
      }
    }

    if (["QDII", "海外", "全球", "海外资产", "全球资产"].some((alias) => hasNegativeCueForAlias(query, alias))) {
      parsed.thresholds.excludeQdii = true;
      parsed.filters.push({ field: "QDII权重", op: "<=", value: 0, unit: "%", label: "不含QDII/海外" });
    }

    if (explicitlyRequestsOverseas(query)) {
      parsed.thresholds.includeOverseas = true;
      parsed.filters.push({ field: "海外资产权重", op: ">", value: 0, unit: "%", label: "含海外资产" });
      if (holdingEvidenceByStrategy().size) {
        parsed.assumptions.push("海外资产按最新持仓快照的研报大类资产分类核验，港股、美股、其他发达市场、新兴市场、海外债券、海外REIT 等均计入。");
      }
    }

    const semanticDetections = detectSemanticEntities(query).filter((entity) => {
      if (entity.key === "gold" && parsed.thresholds.excludeGold) return false;
      if (entity.key === "overseas" && (parsed.thresholds.includeOverseas || parsed.thresholds.excludeQdii || parsed.thresholds.preferLowOverseas)) return false;
      if (isProductTypeEntityInProductContext(entity, query)) return false;
      return true;
    });
    if (semanticDetections.length) {
      const holdingWeightMin = findThreshold(query, ["基金公司仓位", "公司仓位", "持仓占比", "持仓比例", "持仓权重", "配置比例"], minDirectionWords);
      parsed.semanticGroups = semanticDetections.map((entity, index) => createSemanticConditionGroup(
        query,
        entity,
        index,
        "",
        holdingWeightMin !== null ? holdingWeightMin : undefined
      ));
      if (holdingWeightMin !== null) parsed.thresholds.holdingEntityWeightMin = holdingWeightMin;
      parsed.semanticGroups.forEach((group) => {
        const selected = selectedSemanticCandidate(group);
        if (group.needsConfirmation) {
          parsed.assumptions.push(`“${group.sourceText}”存在多种业务含义，当前按匹配度最高的“${selected?.domainLabel || "默认条件"}”执行，可在待确认条件中替换。`);
        }
      });
    }

    if (/只看|仅看|限定|筛选/.test(query) && /广发/.test(query)) {
      parsed.thresholds.onlyGf = true;
      parsed.filters.push({ field: "投顾机构", op: "contains", value: "广发", label: "仅看广发" });
    }

    if (/非广发|排除广发|不看广发/.test(query)) {
      parsed.thresholds.excludeGf = true;
      parsed.filters.push({ field: "投顾机构", op: "not contains", value: "广发", label: "排除广发" });
    }

    if (!parsed.thresholds.onlyGf && !parsed.thresholds.excludeGf && /广发/.test(query) && !parsed.semanticGroups.some((group) => /基金公司/.test(group.entityType))) {
      parsed.thresholds.gfTerm = "广发";
      parsed.assumptions.push("“广发基金的投顾产品”可能对应投顾机构、渠道或策略名称。默认按任一相关字段包含“广发”筛选；可在下方条件表改为单独使用“投顾机构”或“渠道”。");
    }

    const entityTerm = extractEntityTerm(query);
    if (entityTerm && !parsed.thresholds.gfTerm && !parsed.thresholds.onlyGf && !parsed.thresholds.excludeGf && !(parsed.thresholds.holdingEntities || []).length && !parsed.semanticGroups.length) {
      parsed.thresholds.entityTerm = entityTerm;
      parsed.assumptions.push(`“${entityTerm}”可能对应投顾机构、渠道或策略名称。默认按任一相关字段包含该关键词筛选；可在下方条件表改为单独使用“投顾机构”或“渠道”。`);
    }

    const poolMap = [
      ["固收+", "固收+型"],
      ["固收加", "固收+型"],
      ["纯债", "纯债型"],
      ["短债", "纯债型"],
      ["股票", "股票型"],
      ["多元", "多元配置型"],
      ["股债", "股债混合型"],
    ];
    const pool = hasProductTypeContext(query)
      ? poolMap.find(([key]) => query.includes(key) && !hasNegativeCueForAlias(query, key))
      : null;
    if (pool) {
      parsed.thresholds.reportType = pool[1];
      parsed.filters.push({ field: "研报产品类型", op: "=", value: pool[1], label: pool[1] });
    }

    normalizeHoldingEntityConflicts(parsed);
    parsed.filters = buildFilterList(parsed);
    normalizeParsedDateFilters(parsed);
    if (!hasBusinessFilter(parsed)) parsed.warnings.push("没有识别到可执行筛选条件，请补充收益、回撤、成立时间、持仓或产品类型条件。");
    return parsed;
  }

  function localParseNeedsModel(parsed) {
    if (!parsed || !raw(parsed.query).trim()) return false;
    const hasResultChangingAmbiguity = (parsed.semanticGroups || []).some((group) => group.needsConfirmation && (group.candidates || []).length > 1);
    if (hasResultChangingAmbiguity) return true;
    return !hasBusinessFilter(parsed);
  }

  function shouldUseModelParser(allowModel = true, localParsed = null) {
    if (!allowModel) return false;
    const mode = raw(aiConfig.mode || "hybrid-parse").toLowerCase();
    const available = aiConfig.enabled !== false
      && !!modelChatEndpoint(aiConfig)
      && mode !== "local-only"
      && mode !== "off"
      && Date.now() >= modelBackoffUntil;
    if (!available) return false;
    if (["model-first", "model-only", "always"].includes(mode)) return true;
    return localParseNeedsModel(localParsed);
  }

  function firstDefined(source, keys) {
    if (!source || typeof source !== "object") return undefined;
    for (const key of keys) {
      if (source[key] !== undefined && source[key] !== null && source[key] !== "") return source[key];
    }
    return undefined;
  }

  function modelNumber(value) {
    if (value === undefined || value === null || value === "") return null;
    if (typeof value === "number") return Number.isFinite(value) ? value : null;
    const cleaned = raw(value).replace(/%|％|个点|点/g, "").trim();
    return chineseNumber(cleaned);
  }

  function modelBool(value) {
    if (value === true || value === false) return value;
    const text = raw(value).trim().toLowerCase();
    if (!text) return null;
    if (/^(true|yes|y|1|是|需要|包含|含|include)$/.test(text)) return true;
    if (/^(false|no|n|0|否|不需要|不含|exclude)$/.test(text)) return false;
    return null;
  }

  function normalizeModelMetric(value) {
    const text = raw(value).replace(/\s+/g, "");
    if (!text) return "";
    if (allowedReturnMetrics.has(text)) return text;
    if (/近?6月|六个月|半年|近半年|最近半年/.test(text)) return "近6月";
    if (/近?1年|一年|最近一年/.test(text)) return "近1年";
    if (/近?3月|三个月|一季度|季度/.test(text)) return "近三月";
    if (/近?1月|一个月|最近一月/.test(text)) return "近一月";
    if (/近?1周|一周|最近一周/.test(text)) return "近一周";
    if (/今年|年初|ytd/i.test(text)) return "今年以来";
    if (/累计|成立以来|总收益/.test(text)) return "累计收益率";
    if (/目标年化|年化收益|年化回报|年化/.test(text)) return "年化收益";
    return "";
  }

  function normalizeModelDrawdownField(value) {
    const text = raw(value).replace(/\s+/g, "");
    if (/当前|现时|最新/.test(text)) return "当前回撤";
    if (/最大|历史/.test(text)) return "最大回撤";
    return "";
  }

  function normalizeModelReportType(value) {
    const text = raw(value).replace(/\s+/g, "");
    if (!text) return "";
    if (allowedReportTypes.has(text)) return text;
    if (/固收\+|固收加/.test(text)) return "固收+型";
    if (/纯债|短债/.test(text)) return "纯债型";
    if (/海外|全球/.test(text)) return "海外/全球型";
    if (/主题|行业/.test(text)) return "主题/行业型";
    if (/偏股/.test(text)) return "偏股配置型";
    if (/股债/.test(text)) return "股债混合型";
    if (/多元|多资产/.test(text)) return "多元配置型";
    if (/现金|货币/.test(text)) return "现金管理型";
    return "";
  }

  function normalizeModelField(value) {
    const text = raw(value).trim();
    if (!text) return "";
    if (filterFieldNames().includes(text)) return text;
    const compact = text.replace(/\s+/g, "");
    const matched = modelFilterFieldNames().find((field) => fieldLabel(field).replace(/\s+/g, "") === compact || field.replace(/\s+/g, "") === compact);
    return matched || "";
  }

  function normalizeModelOperator(value) {
    const text = raw(value).trim().toLowerCase();
    if (operatorOptions.includes(text)) return text;
    if (/不包含|排除|剔除|not/.test(text)) return "not contains";
    if (/包含|含|contains/.test(text)) return "contains";
    if (/大于等于|不低于|不少于|至少|>=/.test(text)) return ">=";
    if (/小于等于|不超过|不高于|以内|<=/.test(text)) return "<=";
    if (/大于|超过|>/.test(text)) return ">";
    if (/小于|低于|</.test(text)) return "<";
    if (/不等于|!=/.test(text)) return "!=";
    if (/等于|=/.test(text)) return "=";
    if (/有值|非空/.test(text)) return "is not empty";
    if (/为空|空值/.test(text)) return "is empty";
    return "contains";
  }

  function normalizeModelFilters(intent) {
    const source = Array.isArray(intent?.filters) ? intent.filters : [];
    return source.map((item) => {
      const field = normalizeModelField(firstDefined(item, ["field", "字段", "name"]));
      if (!field) return null;
      const op = normalizeModelOperator(firstDefined(item, ["op", "operator", "关系", "操作符"]));
      const value = firstDefined(item, ["value", "值", "target"]);
      const normalizedValue = Array.isArray(value) ? value.map((itemValue) => raw(itemValue).trim()).filter(Boolean) : raw(value).trim();
      if (!["is empty", "is not empty"].includes(op) && (value === undefined || value === null || (Array.isArray(normalizedValue) ? !normalizedValue.length : !normalizedValue))) return null;
      return {
        field,
        op,
        value: normalizedValue,
        label: filterLabel({ field, op, value: normalizedValue }),
      };
    }).filter(Boolean);
  }

  function extractModelJson(data) {
    let content = data?.choices?.[0]?.message?.content ?? data?.choices?.[0]?.text ?? data?.output_text ?? data;
    if (Array.isArray(content)) content = content.map((part) => raw(part.text || part.content || "")).join("");
    if (content && typeof content === "object") return content;
    const text = raw(content).trim();
    if (!text) return null;
    const fenced = text.match(/```(?:json)?\s*([\s\S]*?)```/i);
    const source = fenced ? fenced[1].trim() : text;
    try {
      return JSON.parse(source);
    } catch (error) {
      const start = source.indexOf("{");
      const end = source.lastIndexOf("}");
      if (start >= 0 && end > start) return JSON.parse(source.slice(start, end + 1));
      throw error;
    }
  }

  function normalizeModelSemanticDomain(value) {
    const text = raw(value).trim().toLowerCase();
    const aliases = {
      benchmark: "benchmark",
      基准: "benchmark",
      业绩基准: "benchmark",
      latest_holding: "latest_holding",
      holding: "latest_holding",
      最新持仓: "latest_holding",
      当前持仓: "latest_holding",
      strategy_name: "strategy_name",
      策略名称: "strategy_name",
      advisor: "advisor",
      投顾机构: "advisor",
      channel: "channel",
      渠道: "channel",
    };
    return aliases[text] || "";
  }

  function normalizeModelEntityConditions(intent, query) {
    const normalized = [];
    const append = (item, fallbackDomain = "") => {
      const term = raw(firstDefined(item, ["term", "entity", "value", "关键词", "实体"])).trim();
      const key = raw(firstDefined(item, ["key", "entityKey", "semanticEntity", "实体Key"])).trim();
      const resolved = resolveSemanticEntity(key || term);
      if (!resolved || !containsAlias(query, entityAliases(resolved, term))) return;
      const domain = normalizeModelSemanticDomain(firstDefined(item, ["domain", "业务域", "对象"])) || fallbackDomain;
      if (!domain) return;
      normalized.push({
        sourceText: raw(firstDefined(item, ["sourceText", "source_span", "原文"])).trim() || term,
        term: term || resolved.label || key,
        key: resolved.key,
        label: resolved.label,
        type: resolved.type,
        negative: modelBool(firstDefined(item, ["negative", "exclude", "不含"])) === true || hasNegativeCueForEntity(query, resolved),
        preferredDomain: domain,
      });
    };
    (Array.isArray(intent?.entityConditions) ? intent.entityConditions : []).forEach((item) => append(item));
    const legacyHoldings = firstDefined(intent, ["holdingEntities", "holding_entities", "持仓实体"]);
    if (Array.isArray(legacyHoldings)) legacyHoldings.forEach((item) => append(item, "latest_holding"));
    const hasSpecificIndex = normalized.some((item) => isStandardSemanticEntityKey(item.key) && /指数/.test(raw(item.type)));
    if (!hasSpecificIndex) return normalized;
    return normalized.filter((item) => normalizeSearchText(item.term) !== "指数"
      && !/^(指数基金|股票指数|债券指数|指数型)$/.test(raw(item.label)));
  }

  function mergeModelSemanticGroups(parsed, intent) {
    const conditions = normalizeModelEntityConditions(intent, parsed.query);
    parsed.semanticGroups = Array.isArray(parsed.semanticGroups) ? parsed.semanticGroups : [];
    conditions.forEach((condition) => {
      let group = parsed.semanticGroups.find((item) => item.entityKey === condition.key && item.negative === condition.negative);
      if (!group) {
        group = createSemanticConditionGroup(parsed.query, condition, parsed.semanticGroups.length, condition.preferredDomain);
        parsed.semanticGroups.push(group);
      }
      if (group.explicitDomain || !group.needsConfirmation) return;
      const candidate = group.candidates.find((item) => item.domain === condition.preferredDomain);
      if (candidate) group.selectedId = candidate.id;
    });
  }

  async function requestModelJson(messages) {
    const controller = new AbortController();
    const configuredTimeout = Number(aiConfig.timeoutMs);
    const timeoutMs = Math.min(
      Math.max(800, Number.isFinite(configuredTimeout) && configuredTimeout > 0 ? configuredTimeout : 30000),
      120000
    );
    const timer = window.setTimeout(() => controller.abort(), timeoutMs);
    const headers = { "Content-Type": "application/json", ...(aiConfig.headers || {}) };
    if (aiConfig.apiKey) headers.Authorization = `Bearer ${aiConfig.apiKey}`;
    const endpoint = modelChatEndpoint(aiConfig);
    const payload = {
      model: aiConfig.model || "deepseek-v4-flash-inner",
      temperature: 0,
      messages,
    };
    if (aiConfig.responseFormat !== false) payload.response_format = { type: "json_object" };
    try {
      const response = await fetch(endpoint, {
        method: "POST",
        headers,
        body: JSON.stringify(payload),
        signal: controller.signal,
      });
      if (!response.ok) {
        const error = new Error(`模型接口返回 ${response.status}`);
        error.status = response.status;
        throw error;
      }
      return extractModelJson(await response.json());
    } finally {
      window.clearTimeout(timer);
    }
  }

  async function requestModelRoute(queryText) {
    const route = await requestModelJson([
      {
        role: "system",
        content: "你只做中文投顾业务问题路由，不生成筛选字段、SQL、策略名单或答案。只输出JSON对象。先识别主意图，再把原问题拆为少量业务子句，从给定实体ID中返回最多6个entityCandidates。每项为{entityId,sourceText,confidence}。同时返回clauses，每项为{sourceText,entityIds,measure,timeExpression,operatorExpression,valueExpression,negative}；保留无法确认的unresolved和未匹配的unmatchedSpans。不得发明实体ID。",
      },
      {
        role: "user",
        content: `用户问题：${queryText}\n业务实体核心框架：${JSON.stringify(businessRoutingPromptCatalog())}\n严格返回：{"intent":"筛选策略","entityCandidates":[{"entityId":"performance_observation","sourceText":"近一年收益","confidence":0.98}],"clauses":[{"sourceText":"近一年收益大于5%","entityIds":["performance_observation"],"measure":"收益","timeExpression":"近一年","operatorExpression":"大于","valueExpression":"5%","negative":false}],"logic":{"type":"and"},"unresolved":[],"unmatchedSpans":[]}`,
      },
    ]);
    return normalizeModelRoute(route, queryText);
  }

  async function requestModelIntent(queryText, route) {
    const hydrated = hydrateBusinessRoute(route, queryText);
    return requestModelJson([
      {
        role: "system",
        content: "你只把中文投顾问题编译为可执行筛选JSON，不输出策略名单、解释、Markdown或SQL。字段只能取自fieldCards，操作符只能取自该字段allowedOperators；标准持仓或基准实体只能取自semanticEntities。不得自行计算相对日期、收益、排名或创造字段。日期字段的绝对值必须使用YYYY-MM-DD；相对日期可保留原文作为filters.value，由本地系统按查询基准日期统一换算，禁止把自然语言日期直接用于最终比较。returnMetric只能是近一周、近一月、近三月、近6月、近1年、今年以来、累计收益率、年化收益。收益下限、回撤上限和成立年限可分别输出minReturn、maxDrawdown、drawdownField、minAgeYears；其他条件输出filters[{field,op,value}]。所有百分比使用百分数，例如5%输出5。实体条件输出entityConditions[{sourceText,term,key,domain,relation,negative,confidence}]，domain只能为benchmark、latest_holding、strategy_name、advisor、channel。无法由字段卡执行的条件必须放入unsupportedConditions，不能近似替换或静默忽略。只输出JSON对象。",
      },
      {
        role: "user",
        content: `用户问题：${queryText}\n查询基准日期：${dateText(asOfDate())}\n第一轮业务路由：${JSON.stringify(route)}\n相关实体字段卡：${JSON.stringify(hydrated)}\n严格返回示例：{"returnMetric":"近6月","minReturn":10,"drawdownField":"最大回撤","maxDrawdown":5,"minAgeYears":null,"includeOverseas":false,"excludeGold":false,"excludeQdii":false,"entityConditions":[{"sourceText":"基准包含沪深300","term":"沪深300","key":"hs300","domain":"benchmark","relation":"contains_entity","negative":false,"confidence":0.99}],"filters":[],"unsupportedConditions":[]}`,
      },
    ]);
  }

  function modelIntentHasExecutableSignal(intent) {
    if (!intent || typeof intent !== "object" || Array.isArray(intent)) return false;
    if ((Array.isArray(intent.entityConditions) && intent.entityConditions.length) || (Array.isArray(intent.filters) && intent.filters.length)) return true;
    const scalarKeys = [
      "returnMetric", "return_metric", "metric", "收益口径",
      "minReturn", "min_return", "returnMin", "收益率下限",
      "maxDrawdown", "max_drawdown", "drawdownMax", "回撤上限",
      "drawdownField", "drawdown_field", "回撤字段",
      "minAgeYears", "min_age_years", "成立年限下限",
      "reportType", "report_type", "产品类型",
      "gfTerm", "gf_term", "广发关键词",
      "entityTerm", "entity_term", "机构关键词", "关键词",
    ];
    if (scalarKeys.some((key) => intent[key] !== undefined && intent[key] !== null && raw(intent[key]).trim() !== "")) return true;
    return ["includeOverseas", "include_overseas", "excludeGold", "exclude_gold", "excludeQdii", "exclude_qdii", "onlyGf", "only_gf", "excludeGf", "exclude_gf"]
      .some((key) => modelBool(intent[key]) === true);
  }

  function applyModelIntent(parsed, intent, callCount = 1, route = null) {
    if (!intent || typeof intent !== "object") return parsed;
    const metric = normalizeModelMetric(firstDefined(intent, ["returnMetric", "return_metric", "metric", "收益口径"]));
    if (metric && !parsed.returnMetric.explicit) {
      parsed.returnMetric = { field: metric, explicit: true, source: "model" };
    }
    const minReturn = modelNumber(firstDefined(intent, ["minReturn", "min_return", "returnMin", "收益率下限"]));
    if (minReturn !== null && parsed.thresholds.minReturn === undefined) parsed.thresholds.minReturn = minReturn;
    const maxDrawdown = modelNumber(firstDefined(intent, ["maxDrawdown", "max_drawdown", "drawdownMax", "回撤上限"]));
    const drawdownField = normalizeModelDrawdownField(firstDefined(intent, ["drawdownField", "drawdown_field", "回撤字段"]));
    if (maxDrawdown !== null && parsed.thresholds.maxDrawdown === undefined) parsed.thresholds.maxDrawdown = maxDrawdown;
    if (drawdownField && (parsed.thresholds.maxDrawdown === undefined || /最大回撤|当前回撤|现时回撤/.test(parsed.query))) {
      parsed.thresholds.drawdownField = drawdownField;
    }
    if (parsed.thresholds.maxDrawdown !== undefined && !parsed.thresholds.drawdownField) parsed.thresholds.drawdownField = "最大回撤";
    const minAgeYears = modelNumber(firstDefined(intent, ["minAgeYears", "min_age_years", "成立年限下限"]));
    if (minAgeYears !== null && parsed.thresholds.minAgeYears === undefined) {
      parsed.thresholds.minAgeYears = minAgeYears;
      parsed.thresholds.launchBefore = yearsAgo(parsed.asOf, minAgeYears);
    }
    if (modelBool(firstDefined(intent, ["includeOverseas", "include_overseas", "含海外资产"])) === true && explicitlyRequestsOverseas(parsed.query)) parsed.thresholds.includeOverseas = true;
    if (modelBool(firstDefined(intent, ["excludeGold", "exclude_gold", "不含黄金"])) === true) parsed.thresholds.excludeGold = true;
    if (modelBool(firstDefined(intent, ["excludeQdii", "exclude_qdii", "不含QDII"])) === true) parsed.thresholds.excludeQdii = true;
    if (modelBool(firstDefined(intent, ["onlyGf", "only_gf", "仅广发"])) === true) parsed.thresholds.onlyGf = true;
    if (modelBool(firstDefined(intent, ["excludeGf", "exclude_gf", "排除广发"])) === true) parsed.thresholds.excludeGf = true;
    mergeModelSemanticGroups(parsed, intent);
    const gfTerm = raw(firstDefined(intent, ["gfTerm", "gf_term", "广发关键词"])).trim();
    if (gfTerm && !parsed.thresholds.onlyGf && !parsed.thresholds.excludeGf && !parsed.thresholds.gfTerm && !(parsed.semanticGroups || []).length) parsed.thresholds.gfTerm = gfTerm;
    const entityTerm = raw(firstDefined(intent, ["entityTerm", "entity_term", "机构关键词", "关键词"])).trim();
    if (entityTerm && !parsed.thresholds.gfTerm && !parsed.thresholds.onlyGf && !parsed.thresholds.excludeGf && !parsed.thresholds.entityTerm && !(parsed.thresholds.holdingEntities || []).length && !(parsed.semanticGroups || []).length) parsed.thresholds.entityTerm = entityTerm;
    const holdingEntityWeightMin = modelNumber(firstDefined(intent, ["holdingEntityWeightMin", "holding_entity_weight_min", "持仓实体权重下限", "持仓实体仓位下限"]));
    if (holdingEntityWeightMin !== null && (parsed.semanticGroups || []).some((group) => selectedSemanticCandidate(group)?.domain === "latest_holding" && !group.negative)) {
      parsed.thresholds.holdingEntityWeightMin = holdingEntityWeightMin;
      parsed.semanticGroups.forEach((group) => {
        if (group.negative || selectedSemanticCandidate(group)?.domain !== "latest_holding") return;
        group.minWeight = holdingEntityWeightMin;
        group.candidates = group.candidates.map((candidate) => candidate.domain === "latest_holding"
          ? { ...candidate, filter: { ...semanticCandidateFilter(group, candidate.domain, candidate.confidence), semanticCandidateId: candidate.id } }
          : candidate);
      });
    }
    const reportType = normalizeModelReportType(firstDefined(intent, ["reportType", "report_type", "产品类型"]));
    if (reportType && !parsed.thresholds.reportType) parsed.thresholds.reportType = reportType;
    const extraFilters = normalizeModelFilters(intent);
    const unsupportedConditions = (Array.isArray(intent?.unsupportedConditions) ? intent.unsupportedConditions : [])
      .map((item) => raw(typeof item === "string" ? item : firstDefined(item, ["sourceText", "condition", "条件", "reason", "原因"])).trim())
      .filter(Boolean)
      .slice(0, 8);
    normalizeHoldingEntityConflicts(parsed);
    parsed.filters = buildFilterList(parsed);
    extraFilters.forEach((filter) => {
      const exists = parsed.filters.some((item) => item.field === filter.field && item.op === filter.op && raw(item.value) === raw(filter.value));
      if (!exists) parsed.filters.push(filter);
    });
    parsed.filters = normalizeLogicalFilters(parsed.filters, parsed);
    normalizeParsedDateFilters(parsed);
    if (unsupportedConditions.length) parsed.warnings.push(`以下条件当前没有可执行字段，未静默替换：${unsupportedConditions.join("；")}。`);
    if ((route?.unmatchedSpans || []).length) parsed.warnings.push(`第一轮仍有未匹配表达：${route.unmatchedSpans.map(raw).filter(Boolean).join("、")}。`);
    parsed.model = {
      status: "used",
      provider: raw(aiConfig.provider || "local"),
      model: raw(aiConfig.model || "codex"),
      callCount,
      stages: ["business-entity-routing", "field-card-query-plan"],
      routeEntities: (route?.entityCandidates || []).map((item) => item.entityId).filter(Boolean),
    };
    parsed.assumptions.push(`本地规则存在未识别条件或结果歧义，本次按“业务实体路由→字段卡筛选计划”受控调用模型 ${callCount} 次；最终条件仍由本地策略宽表和持仓快照核验。`);
    return parsed;
  }

  async function parseQueryHybrid(queryText, localParsed, allowModel = true) {
    localParsed.model = { status: "local-rule", callCount: 0 };
    if (!allowModel) return localParsed;
    const mode = raw(aiConfig.mode || "hybrid-parse").toLowerCase();
    const modelRequiredByPolicy = ["model-first", "model-only", "always"].includes(mode) || localParseNeedsModel(localParsed);
    if (!modelRequiredByPolicy) {
      localParsed.model = { status: "local-rule", callCount: 0, decision: "local-sufficient" };
      return localParsed;
    }
    if (modelBackoffUntil > Date.now()) {
      localParsed.model = { status: "fallback", error: "model-rate-limited", callCount: 0 };
      localParsed.warnings.push("模型接口正在限流，已临时使用本地规则解析。");
      localParsed.filters = buildFilterList(localParsed);
      return localParsed;
    }
    if (!shouldUseModelParser(true, localParsed)) return localParsed;
    let modelCallCount = 0;
    try {
      modelCallCount += 1;
      const route = await requestModelRoute(queryText);
      modelCallCount += 1;
      const intent = await requestModelIntent(queryText, route);
      if (!modelIntentHasExecutableSignal(intent)) {
        localParsed.model = {
          status: "fallback",
          error: "model-empty-query-plan",
          callCount: modelCallCount,
          stages: ["business-entity-routing", "field-card-query-plan"],
          routeEntities: (route?.entityCandidates || []).map((item) => item.entityId).filter(Boolean),
        };
        localParsed.warnings.push("两轮模型解析没有形成合法的可执行条件，已保留本地规则结果；未继续重试。");
        localParsed.filters = buildFilterList(localParsed);
        return localParsed;
      }
      return applyModelIntent(localParsed, intent, modelCallCount, route);
    } catch (error) {
      let message = raw(error?.name === "AbortError" ? "模型解析超时" : error?.message || error);
      if (/Failed to fetch|NetworkError|Load failed/i.test(message)) {
        message = isCodexProxyConfig(aiConfig)
          ? "本机模型代理未连接，请先运行 scripts/start_ai_strategy_codex_proxy.ps1，并确认 http://127.0.0.1:8787/healthz 可访问"
          : `模型接口无法访问，请检查内网连通性、接口跨域和鉴权配置：${modelBaseUrl(aiConfig) || modelChatEndpoint(aiConfig) || "未配置"}`;
      }
      message = message.slice(0, 160);
      if (error?.status === 429 || /429|Too Many Requests/i.test(message)) {
        modelBackoffUntil = Date.now() + Math.max(10000, Number(aiConfig.rateLimitBackoffMs) || 60000);
      }
      localParsed.model = { status: "fallback", error: message, callCount: Math.max(1, modelCallCount) };
      localParsed.warnings.push(`模型解析不可用，已使用本地规则解析：${message}`);
      localParsed.filters = buildFilterList(localParsed);
      return localParsed;
    }
  }

  function isPerformanceCompleteStrategy(row) {
    return row?.业绩完整 === "是" || row?.业绩完整性 === "完整";
  }

  function isGf(row) {
    return /广发/.test([row.投顾机构, row.渠道, row.策略名称, row.searchText].map(raw).join(" "));
  }

  function hasGold(row) {
    const evidence = goldEvidence(row);
    if (evidence.known) return evidence.hasGold;
    return /黄金|商品黄金|贵金属/.test([
      row.特殊标签,
      row.策略名称,
      row.业务分类,
      row.业务分类依据,
      row.研报分类依据,
      row.分类依据,
      row.searchText,
    ].map(raw).join(" "));
  }

  function goldEvidence(row) {
    const indexed = strategyEntityEvidence(row, "gold");
    if (indexed.known) {
      return {
        known: true,
        hasGold: indexed.hasEntity,
        date: indexed.date,
        goldWeight: indexed.weight,
        labels: indexed.labels || [],
      };
    }
    const evidence = holdingEvidenceByStrategy().get(raw(row.统一策略ID));
    if (!evidence) return { known: false, hasGold: false, date: "", goldWeight: 0, labels: [] };
    return {
      known: evidence.checked > 0,
      hasGold: evidence.hasGold,
      date: evidence.date,
      goldWeight: evidence.goldWeight,
      labels: evidence.goldLabels || [],
    };
  }

  function overseasEvidence(row) {
    const indexed = strategyEntityEvidence(row, "overseas");
    if (indexed.known) {
      return {
        known: true,
        hasOverseas: indexed.hasEntity,
        date: indexed.date,
        overseasWeight: indexed.weight,
        labels: indexed.labels || [],
      };
    }
    const evidence = holdingEvidenceByStrategy().get(raw(row.统一策略ID));
    if (evidence && evidence.checked > 0) {
      return {
        known: true,
        hasOverseas: evidence.hasOverseas,
        date: evidence.date,
        overseasWeight: evidence.overseasWeight,
        labels: evidence.overseasLabels || [],
      };
    }
    const fallbackWeight = num(row.QDII权重) || 0;
    const fallbackText = [row.策略实现标签, row.市场地域, row.特殊标签, row.分类依据, row.searchText].map(raw).join(" ");
    const fallbackHit = fallbackWeight > 0.01 || /QDII|海外|全球|港股|美股/.test(fallbackText);
    return {
      known: fallbackHit || fallbackWeight > 0,
      hasOverseas: fallbackHit,
      date: raw(row.最新持仓日),
      overseasWeight: fallbackWeight,
      labels: fallbackHit ? ["策略宽表:海外/QDII标签"] : [],
    };
  }

  function hasQdii(row) {
    const overseas = overseasEvidence(row);
    return overseas.hasOverseas || (num(row.QDII权重) || 0) > 0.01 || /QDII|海外|全球/.test([row.策略实现标签, row.市场地域, row.searchText].map(raw).join(" "));
  }

  function riskLevelNumber(row) {
    const match = raw(row?.风险等级).match(/R\s*([0-5])/i);
    return match ? Number(match[1]) : null;
  }

  function fieldValue(row, field) {
    if (field === "__benchmark_text") return benchmarkText(row);
    if (field === "__holding_entity") {
      const filter = activeHoldingEntityFilter();
      return filter ? holdingEntityEvidenceForFilter(row, filter).labels.join("；") : "";
    }
    if (field === "__source_any") return [row.投顾机构, row.渠道, row.策略名称, row.searchText].map(raw).filter(Boolean).join(" ");
    if (field === "__gf_any") return [row.投顾机构, row.渠道, row.策略名称, row.searchText].map(raw).filter(Boolean).join(" ");
    if (field === "__any_text") return Object.values(row || {}).map(raw).filter(Boolean).join(" ");
    if (field === "风险等级序号") return riskLevelNumber(row);
    if (field === "持仓实体权重") {
      const filter = activeHoldingEntityFilter();
      return filter ? holdingEntityEvidenceForFilter(row, filter).weight : null;
    }
    if (field === "持仓实体判断") {
      const filter = activeHoldingEntityFilter();
      if (!filter) return "";
      const evidence = holdingEntityEvidenceForFilter(row, filter);
      if (!evidence.known) return "待核验";
      const passed = filter.op === "contains_all" ? evidence.hasAllEntities
        : filter.op === "weight_gte" ? compareFilter(row, filter, null)
          : evidence.hasEntity;
      return passed ? `命中${evidence.entity?.label || filter.value}` : "未命中";
    }
    if (field === "持仓实体证据") {
      const filter = activeHoldingEntityFilter();
      return filter ? holdingEntityEvidenceForFilter(row, filter).labels.join("；") : "";
    }
    if (field === "海外资产权重") return overseasEvidence(row).overseasWeight;
    if (field === "海外资产判断") {
      const overseas = overseasEvidence(row);
      if (!overseas.known) return "待核验";
      return overseas.hasOverseas ? "含海外" : "未命中";
    }
    if (field === "海外资产分类") return overseasEvidence(row).labels.join("；");
    if (field === "黄金判断") {
      const gold = goldEvidence(row);
      if (!gold.known) return "待核验";
      return gold.hasGold ? "含黄金" : "未持有";
    }
    return row?.[field];
  }

  function isDateField(field) {
    return /日期|业绩日|持仓日|调仓日|截至/.test(raw(field));
  }

  function normalizeDateFilterValue(filter, parsed) {
    if (!filter || !isDateField(filter.field) || ["is empty", "is not empty"].includes(filter.op)) return filter;
    if (Array.isArray(filter.value)) return null;
    const exactDate = dateFrom(filter.value);
    if (exactDate) {
      const op = filter.op === "contains" ? "=" : (filter.op === "not contains" ? "!=" : filter.op);
      const value = dateText(exactDate);
      return { ...filter, op, value, label: filterLabel({ ...filter, op, value }) };
    }
    const relative = relativeDateBoundary(filter.value, parsed?.asOf)
      || (filter.field === "成立日期" ? establishedRelativeDateCondition(parsed?.query, parsed?.asOf) : null);
    if (!relative) return null;
    return {
      ...filter,
      op: relative.op,
      value: relative.value,
      label: `${fieldLabel(filter.field)} >= ${relative.value}`,
      relativeDateSource: raw(filter.value),
      relativeDatePeriod: relative.periodLabel,
    };
  }

  function normalizeParsedDateFilters(parsed) {
    if (!parsed || !Array.isArray(parsed.filters)) return parsed;
    const normalized = [];
    parsed.filters.forEach((filter) => {
      const next = normalizeDateFilterValue(filter, parsed);
      if (next) {
        normalized.push(next);
        return;
      }
      const warning = `${fieldLabel(filter.field)}的值“${raw(filter.value)}”无法转换为 YYYY-MM-DD，已拒绝执行该条件。`;
      if (!(parsed.warnings || []).includes(warning)) parsed.warnings.push(warning);
    });
    parsed.filters = normalized;
    return parsed;
  }

  function isEmptyValue(value) {
    return value === null || value === undefined || raw(value).trim() === "";
  }

  function markMissing(missing, row) {
    if (!missing) return;
    missing.generic += 1;
    const id = raw(row?.统一策略ID || row?.策略代码 || row?.策略名称);
    if (id) missing.rowIds.add(id);
  }

  function compareFilter(row, filter, missing) {
    const op = raw(filter.op || "contains");
    if (filter.field === "__holding_entity") {
      const evidence = holdingEntityEvidenceForFilter(row, filter);
      if (!evidence.known) {
        markMissing(missing, row);
        return false;
      }
      if (op === "not contains" || op === "!=") return !evidence.hasEntity;
      if (op === "contains_all") return evidence.hasAllEntities;
      if (op === "weight_gte") {
        const minWeight = num(filter.minWeight ?? filter.value);
        if (minWeight === null) return evidence.hasEntity;
        if (filter.matchMode === "all") return evidence.hasAllEntities && evidence.evidences.every((item) => (num(item.weight) || 0) >= minWeight);
        return evidence.hasEntity && (num(evidence.weight) || 0) >= minWeight;
      }
      if (op === "contains" || op === "contains_any" || op === "=" || op === "is not empty") return evidence.hasEntity;
      if (op === "is empty") return !evidence.hasEntity;
      return evidence.hasEntity;
    }
    const value = fieldValue(row, filter.field);
    if (op === "is empty") return isEmptyValue(value);
    if (op === "is not empty") return !isEmptyValue(value);
    if (isEmptyValue(value)) {
      markMissing(missing, row);
      return false;
    }
    const target = filter.value;
    if ([">=", "<=", ">", "<"].includes(op)) {
      if (isDateField(filter.field) && dateFrom(value) && dateFrom(target)) {
        const left = dateFrom(value).getTime();
        const right = dateFrom(target).getTime();
        if (op === ">=") return left >= right;
        if (op === "<=") return left <= right;
        if (op === ">") return left > right;
        return left < right;
      }
      const left = num(value);
      const right = num(target);
      if (left === null || right === null) {
        markMissing(missing, row);
        return false;
      }
      if (op === ">=") return left >= right;
      if (op === "<=") return left <= right;
      if (op === ">") return left > right;
      return left < right;
    }
    const leftText = raw(value).toLowerCase();
    const rightText = raw(target).toLowerCase();
    const targets = filterValues(filter).map((item) => item.toLowerCase());
    if (op === "contains") return leftText.includes(rightText);
    if (op === "contains_any") return targets.some((item) => leftText.includes(item));
    if (op === "not contains") return !leftText.includes(rightText);
    if (op === "in") return targets.some((item) => leftText === item);
    if (op === "not in") return targets.every((item) => leftText !== item);
    if (op === "=") {
      const leftNumber = num(value);
      const rightNumber = num(target);
      if (leftNumber !== null && rightNumber !== null) return leftNumber === rightNumber;
      return leftText === rightText;
    }
    if (op === "!=") {
      const leftNumber = num(value);
      const rightNumber = num(target);
      if (leftNumber !== null && rightNumber !== null) return leftNumber !== rightNumber;
      return leftText !== rightText;
    }
    return leftText.includes(rightText);
  }

  function activeFilters(parsed) {
    return (parsed.filters || []).filter((filter) => !(parsed.completeOnly && filter.system && filter.field === "业绩完整" && filter.value === "是"));
  }

  function filterMatchScore(row, filter, parsed) {
    const op = raw(filter.op || "contains");
    if (filter.field === "__holding_entity") {
      const evidence = holdingEntityEvidenceForFilter(row, filter);
      if (op === "not contains" || op === "!=") return evidence.hasEntity ? 0 : 5;
      return 8 + Math.min(30, num(evidence.weight) || 0);
    }
    const value = fieldValue(row, filter.field);
    if (isEmptyValue(value)) return 0;
    if ([">=", ">"].includes(op)) {
      if (isDateField(filter.field) && dateFrom(value) && dateFrom(filter.value)) {
        const marginDays = Math.max(0, (dateFrom(value).getTime() - dateFrom(filter.value).getTime()) / 86400000);
        return 4 + Math.min(40, marginDays / 30);
      }
      const left = num(value);
      const right = num(filter.value);
      if (left === null || right === null) return 0;
      const margin = Math.max(0, left - right);
      const factor = filter.field === parsed?.returnMetric?.field ? 1.2 : 0.35;
      return 4 + Math.min(40, margin * factor);
    }
    if (["<=", "<"].includes(op)) {
      if (isDateField(filter.field) && dateFrom(value) && dateFrom(filter.value)) {
        const marginDays = Math.max(0, (dateFrom(filter.value).getTime() - dateFrom(value).getTime()) / 86400000);
        return 4 + Math.min(35, marginDays / 30);
      }
      const left = num(value);
      const right = num(filter.value);
      if (left === null || right === null) return 0;
      const margin = Math.max(0, right - left);
      const factor = /回撤|波动|费率|换手/.test(filter.field) ? 1.4 : 0.35;
      return 4 + Math.min(35, margin * factor);
    }
    if (["=", "in", "contains", "contains_any"].includes(op)) return 6;
    if (["!=", "not in", "not contains"].includes(op)) return 3;
    return 1;
  }

  function rowMatchScore(row, parsed) {
    const filters = activeFilters(parsed).filter((filter) => !filter.system);
    let score = filters.reduce((total, filter) => total + filterMatchScore(row, filter, parsed), 0);
    const sortField = allowedReturnMetrics.has(parsed?.returnMetric?.field) ? parsed.returnMetric.field : state.sortField;
    const returnValue = num(row[sortField]);
    if (returnValue !== null) score += returnValue * 0.08;
    const drawdownField = parsed?.thresholds?.drawdownField || "最大回撤";
    const drawdown = num(row[drawdownField]);
    if (drawdown !== null) score -= drawdown * 0.05;
    return score;
  }

  function filterLabel(filter) {
    const op = raw(filter.op || "contains");
    if (filter.field === "__benchmark_text") {
      const entity = resolveSemanticEntity(filter.semanticEntity || filter.value);
      const verb = op === "not contains" || op === "not in" || op === "!=" ? "不包含" : "包含";
      return `业绩基准${verb}${entity?.label || filter.semanticLabel || filterValues(filter)[0] || raw(filter.value)}`;
    }
    if (filter.field === "__holding_entity") {
      if (op === "weight_gte") {
        const labels = holdingEntityFilterItems(filter).map((item) => item.label || item.term).filter(Boolean);
        const prefix = filter.matchMode === "all" ? "最新持仓分别满足" : "最新持仓合计权重";
        return `${prefix}：${labels.join("、") || raw(filter.value)} >= ${filter.minWeight ?? filter.value}%`;
      }
      if (op === "contains_all") {
        const labels = holdingEntityFilterItems(filter).map((item) => item.label || item.term).filter(Boolean);
        return `最新持仓同时含：${labels.join("、") || raw(filter.value)}`;
      }
      if (op === "contains_any") {
        const labels = holdingEntityFilterItems(filter).map((item) => item.label || item.term).filter(Boolean);
        return `最新持仓含任一：${labels.join("、") || raw(filter.value)}`;
      }
      const entity = resolveSemanticEntity(filter.value || filter.semanticEntity);
      const verb = op === "not contains" || op === "!=" ? "不含" : "含";
      return `最新持仓${verb}${entity?.label || raw(filter.value)}`;
    }
    if (op === "in" || op === "not in" || op === "contains_any") return `${fieldLabel(filter.field)} ${operatorLabels[op] || op} ${filterValues(filter).join("、")}`;
    if (op === "is empty" || op === "is not empty") return `${fieldLabel(filter.field)} ${operatorLabels[op] || op}`;
    return `${fieldLabel(filter.field)} ${operatorLabels[op] || op} ${raw(filter.value)}${filter.unit || ""}`;
  }

  function compactConditionLabel(filter, parsed) {
    const op = raw(filter.op || "contains");
    const label = raw(filter.label);
    if (filter.field === "成立日期") {
      const years = num(parsed?.thresholds?.minAgeYears);
      if (years !== null) return `成立时间满${years}年`;
      if (/^成立满/.test(label)) return label.replace(/^成立满/, "成立时间满");
    }
    if (filter.field === "__benchmark_text" || filter.field === "__holding_entity") {
      return filterLabel(filter).replace(/\s*>=\s*/g, "不低于");
    }
    if (label && !/[<>=]/.test(label)) return label;
    const compactOperators = {
      contains: "包含",
      contains_any: "包含任一",
      contains_all: "同时包含",
      weight_gte: "持仓权重不低于",
      "not contains": "不包含",
      "=": "等于",
      "!=": "不等于",
      in: "等于任一",
      "not in": "不等于任一",
      ">=": "不低于",
      "<=": "不超过",
      ">": "大于",
      "<": "小于",
      "is empty": "为空",
      "is not empty": "有值",
    };
    const value = ["in", "not in", "contains_any", "contains_all"].includes(op)
      ? filterValues(filter).join("、")
      : raw(filter.value);
    return `${fieldLabel(filter.field)}${compactOperators[op] || operatorLabels[op] || op}${value}${filter.unit || ""}`;
  }

  function recognizedConditionSummary(parsed) {
    const labels = conditionFilterStats(parsed)
      .map((item) => compactConditionLabel(item.filter, parsed))
      .filter(Boolean);
    return labels.length ? `已识别：${labels.join("、")}` : "已识别：暂无可执行的筛选条件";
  }

  function zhDate(value) {
    const date = dateFrom(value);
    if (!date) return raw(value) || "未披露";
    return `${date.getFullYear()}年${date.getMonth() + 1}月${date.getDate()}日`;
  }

  function compactEvidenceText(text, entityLabel = "") {
    let value = raw(text)
      .split("；来源")[0]
      .split("；规则")[0]
      .replace(/｜(verified|derived|candidate).*$/i, "")
      .trim();
    if (entityLabel && value.startsWith(entityLabel)) {
      value = value
        .slice(entityLabel.length)
        .replace(/^\s*[0-9]+(?:\.[0-9]+)?%\s*[:：]?\s*/, "")
        .trim();
    }
    return value || raw(text);
  }

  function holdingEvidenceRows(evidence) {
    const rows = [];
    (evidence.evidences || []).forEach((item) => {
      const label = item.entity?.label || evidence.entity?.label || raw(item.term);
      const weight = num(item.weight) || 0;
      if (!item.hasEntity && weight <= 0) return;
      const details = (item.labels || [])
        .map((text) => compactEvidenceText(text, label))
        .filter(Boolean)
        .slice(0, 3);
      rows.push({ label, weight, details });
    });
    if (!rows.length) {
      (evidence.entitySummaries || []).forEach((item) => {
        if (!item.hasEntity) return;
        rows.push({
          label: item.label,
          weight: num(item.weight) || 0,
          details: (item.labels || []).map((text) => compactEvidenceText(text, item.label)).filter(Boolean).slice(0, 2),
        });
      });
    }
    return rows.slice(0, 8);
  }

  function hitReasonItems(row, parsed) {
    return activeFilters(parsed)
      .filter((filter) => !filter.system)
      .map((filter) => {
        const label = filter.label || filterLabel(filter);
        if (filter.field === "__holding_entity") {
          const evidence = holdingEntityEvidenceForFilter(row, filter);
          const isNegative = filter.op === "not contains" || filter.op === "!=";
          const rows = holdingEvidenceRows(evidence);
          return {
            condition: label,
            result: isNegative
              ? `当前最新仓位未发现${evidence.entity?.label || raw(filter.value)}`
              : `当前最新仓位${evidence.weight ? ` ${formatPct(evidence.weight)}` : ""}`,
            meta: evidence.date ? `持仓日 ${zhDate(evidence.date)}` : "",
            details: rows.map((item) => ({
              label: item.label,
              value: item.weight ? formatPct(item.weight) : "",
              notes: item.details,
            })),
          };
        }
        if (filter.field === "__gf_any" || filter.field === "__source_any") {
          return {
            condition: label,
            result: `机构/渠道/策略名称命中“${raw(filter.value)}”`,
            details: [
              { label: "策略名称", value: raw(row.策略名称 || "未披露") },
              { label: "投顾机构", value: raw(row.投顾机构 || "未披露") },
              { label: "渠道", value: raw(row.渠道 || "未披露") },
            ],
          };
        }
        if (filter.field === "黄金判断") {
          const gold = goldEvidence(row);
          return {
            condition: label,
            result: gold.hasGold ? `当前最新仓位含黄金 ${formatPct(gold.goldWeight)}` : "当前最新仓位未持有黄金",
            meta: gold.date ? `持仓日 ${zhDate(gold.date)}` : "",
            details: (gold.labels || []).slice(0, 4).map((item) => ({ label: item, value: "" })),
          };
        }
        if (filter.field === "海外资产判断" || filter.field === "海外资产权重" || filter.field === "海外资产分类") {
          const overseas = overseasEvidence(row);
          return {
            condition: label,
            result: overseas.hasOverseas ? `当前最新仓位含海外资产 ${formatPct(overseas.overseasWeight)}` : "当前最新仓位未命中海外资产",
            meta: overseas.date ? `持仓日 ${zhDate(overseas.date)}` : "",
            details: (overseas.labels || []).slice(0, 6).map((item) => ({ label: item, value: "" })),
          };
        }
        const value = fieldValue(row, filter.field);
        if (filter.field === "成立日期") {
          return { condition: label, result: `成立日 ${zhDate(value)}` };
        }
        if (isDateField(filter.field)) {
          return { condition: label, result: `${fieldLabel(filter.field)} ${zhDate(value)}` };
        }
        if (["最大回撤", "当前回撤", "累计收益率", "近6月", "近1年", "近三月", "近一月", "近一周", "今年以来", "年化收益", "波动率", "权益基金权重", "债券基金权重", "货币基金权重", "QDII权重", "指数基金权重", "主动基金权重", "单次平均换手率", "年化换手率"].includes(filter.field)) {
          return { condition: label, result: `${fieldLabel(filter.field)} ${formatPct(value)}` };
        }
        return { condition: label, result: `${fieldLabel(filter.field)}：${raw(value) || "未披露"}` };
      });
  }

  function syncParsedFromFilters(parsed) {
    normalizeParsedDateFilters(parsed);
    const returnFilter = (parsed.filters || []).find((filter) => allowedReturnMetrics.has(filter.field) && [">=", ">", "=", "<=", "<"].includes(filter.op));
    if (returnFilter) parsed.returnMetric = { field: returnFilter.field, explicit: true, source: "filter" };
    const drawdownFilter = (parsed.filters || []).find((filter) => ["最大回撤", "当前回撤"].includes(filter.field) && [">=", ">", "=", "<=", "<"].includes(filter.op));
    if (drawdownFilter) {
      parsed.thresholds.drawdownField = drawdownFilter.field;
      parsed.thresholds.maxDrawdown = num(drawdownFilter.value);
    }
    parsed.filters.forEach((filter) => {
      filter.label = filter.label || filterLabel(filter);
    });
    return parsed;
  }

  function hitReason(row, parsed) {
    return hitReasonItems(row, parsed)
      .map((item) => `${item.condition}：${item.result}`)
      .join("；");
  }

  function applyFilters(parsed) {
    syncParsedFromFilters(parsed);
    const missing = { generic: 0, rowIds: new Set() };
    let base = allRows.slice();
    if (parsed.completeOnly) base = base.filter(isPerformanceCompleteStrategy);
    const filters = activeFilters(parsed);
    const rows = base.filter((row) => {
      return filters.every((filter) => compareFilter(row, filter, missing));
    });
    rows.forEach((row) => {
      row._aiMatchScore = rowMatchScore(row, parsed);
      row._aiHitReasonItems = hitReasonItems(row, parsed);
      row._aiHitReason = hitReason(row, parsed);
    });
    const sortField = allowedReturnMetrics.has(parsed.returnMetric.field) ? parsed.returnMetric.field : state.sortField;
    rows.sort((a, b) => {
      const scoreDiff = (num(b._aiMatchScore) || 0) - (num(a._aiMatchScore) || 0);
      if (Math.abs(scoreDiff) > 0.0001) return scoreDiff;
      const av = num(a[sortField]);
      const bv = num(b[sortField]);
      if (av !== null && bv !== null && av !== bv) return state.sortDir === "asc" ? av - bv : bv - av;
      const drawdownField = parsed.thresholds.drawdownField || "最大回撤";
      return (num(a[drawdownField]) ?? 9999) - (num(b[drawdownField]) ?? 9999);
    });
    return { rows, missing, baseCount: base.length };
  }

  function formatPct(value) {
    const n = num(value);
    return n === null ? "未披露" : `${n.toFixed(2)}%`;
  }

  function formatValue(row, field) {
    if (field === "策略名称") {
      return `<a class="link" href="./strategy.html?id=${encodeURIComponent(row.统一策略ID || "")}">${B.esc(row.策略名称 || "未命名策略")}</a><div class="small">${B.esc(row.策略代码 || row.统一策略ID || "未披露代码")}</div>`;
    }
    if (field === B.strategyListInstitutionField) return `<span class="strategy-institution-value">${B.esc(B.strategyInstitutionText(row))}</span>`;
    if (["累计收益率", "近1年", "近6月", "近一周", "近一月", "近三月", "今年以来", "最大回撤", "当前回撤", "波动率", "年化收益"].includes(field)) return B.pctSigned(row[field]);
    if (["权益基金权重", "债券基金权重", "货币基金权重", "QDII权重", "指数基金权重", "海外资产权重", "持仓实体权重"].includes(field)) {
      if (field === "海外资产权重") return B.pct(overseasEvidence(row).overseasWeight);
      if (field === "持仓实体权重") {
        const filter = activeHoldingEntityFilter();
        return filter ? B.pct(holdingEntityEvidenceForFilter(row, filter).weight) : '<span class="value-muted">未选择实体</span>';
      }
      return B.pct(row[field]);
    }
    if (field === "持仓实体判断") {
      const filter = activeHoldingEntityFilter();
      if (!filter) return '<span class="value-muted">未选择实体</span>';
      const evidence = holdingEntityEvidenceForFilter(row, filter);
      if (!evidence.known) return '<span class="status-badge bad">待核验</span>';
      const passed = filter.op === "contains_all" ? evidence.hasAllEntities
        : filter.op === "weight_gte" ? compareFilter(row, filter, null)
          : evidence.hasEntity;
      if (passed) return `<span class="status-badge ok">命中${B.esc(evidence.entity?.label || filter.value)}</span><div class="small">${B.esc(evidence.date)}</div>`;
      return `<span class="status-badge bad">未命中</span><div class="small">${B.esc(evidence.date)}</div>`;
    }
    if (field === "持仓实体证据") {
      const filter = activeHoldingEntityFilter();
      if (!filter) return '<span class="value-muted">未选择实体</span>';
      const evidence = holdingEntityEvidenceForFilter(row, filter);
      return B.esc(evidence.labels.join("；") || "未命中");
    }
    if (field === "黄金判断") {
      const gold = goldEvidence(row);
      if (!gold.known) return '<span class="status-badge bad">待核验</span>';
      if (gold.hasGold) return `<span class="status-badge bad">含黄金${gold.goldWeight ? ` ${B.esc(formatPct(gold.goldWeight))}` : ""}</span><div class="small">${B.esc(gold.date)}</div>`;
      return `<span class="status-badge ok">未持有</span><div class="small">${B.esc(gold.date)}</div>`;
    }
    if (field === "海外资产判断") {
      const overseas = overseasEvidence(row);
      if (!overseas.known) return '<span class="status-badge bad">待核验</span>';
      if (overseas.hasOverseas) return `<span class="status-badge ok">含海外${overseas.overseasWeight ? ` ${B.esc(formatPct(overseas.overseasWeight))}` : ""}</span><div class="small">${B.esc(overseas.date)}</div>`;
      return `<span class="status-badge bad">未命中</span><div class="small">${B.esc(overseas.date)}</div>`;
    }
    if (field === "海外资产分类") return B.esc(overseasEvidence(row).labels.join("；") || "未命中");
    if (field === "命中说明") {
      if (!row._aiHitReason) return '<span class="value-muted">暂无</span>';
      return `<button class="ai-hit-reason-btn" type="button" data-ai-hit-id="${B.esc(row.统一策略ID || "")}">查看</button>`;
    }
    if (field === "业绩完整") return B.statusBadge ? B.statusBadge(row[field] === "是" ? "完整" : "缺失") : B.esc(row[field] || "");
    if (field === "数据完整性" || field === "业绩完整性") return B.statusBadge ? B.statusBadge(row[field]) : B.esc(row[field] || "");
    return B.fmt(row[field]);
  }

  function renderChips(parsed) {
    return `<div class="ai-chip-row">${parsed.filters.map((filter) => `<span class="ai-chip">${B.esc(filter.label || filterLabel(filter))}</span>`).join("")}</div>`;
  }

  function displayFilterMatches(row, filter) {
    if (filter.system && filter.field === "业绩完整" && filter.value === "是") return isPerformanceCompleteStrategy(row);
    return compareFilter(row, filter, null);
  }

  function conditionFilterStats(parsed) {
    const pool = parsed.completeOnly ? allRows.filter(isPerformanceCompleteStrategy) : allRows.slice();
    return (parsed.filters || []).map((filter, filterIndex) => ({ filter, filterIndex }))
      .filter((item) => !item.filter.system)
      .map(({ filter, filterIndex }) => {
      const remaining = pool.filter((row) => displayFilterMatches(row, filter)).length;
      return {
        filter,
        filterIndex,
        before: pool.length,
        removed: pool.length - remaining,
        remaining,
      };
    });
  }

  function semanticCandidateStats(parsed, group, candidate) {
    const pool = parsed.completeOnly ? allRows.filter(isPerformanceCompleteStrategy) : allRows.slice();
    const remaining = pool.filter((row) => displayFilterMatches(row, candidate.filter)).length;
    return { before: pool.length, removed: pool.length - remaining, remaining };
  }

  function confidenceText(filter) {
    if (filter?.manualEdited) return "手工调整";
    const confidence = Number(filter?.confidence);
    if (Number.isFinite(confidence)) return `匹配度 ${Math.round(confidence * 100)}%`;
    if (filter?.system) return "数据范围";
    return "明确识别";
  }

  function semanticRecognitionReason(parsed, filter, group) {
    if (filter.manualEdited) return "该条件已在页面内手工调整，当前按修改后的字段、关系和值执行。";
    if (!group) {
      const sourceText = raw(filter.sourceText);
      return sourceText
        ? `原问题中的“${sourceText}”被提取为${fieldLabel(filter.field)}条件。`
        : `根据原问题提取为“${filter.label || filterLabel(filter)}”条件。`;
    }
    const candidate = selectedSemanticCandidate(group);
    if (group.userSelected) return `你已将“${group.sourceText}”切换为${candidate?.domainLabel || "当前"}条件，同组原条件已被替换。`;
    if (group.explicitDomain) {
      const definition = semanticDomainDefinitions[group.explicitDomain];
      const normalizedQuery = normalizeSearchText(parsed.query || state.query);
      const cue = (definition?.cues || []).find((item) => normalizedQuery.includes(normalizeSearchText(item)));
      return `原问题中的“${cue || definition?.label || candidate?.domainLabel}”明确限定了“${group.sourceText}”的业务含义，因此识别为${candidate?.domainLabel || "当前"}条件。`;
    }
    return `原问题只提到“${group.sourceText}”，未明确是${group.candidates.map((item) => item.domainLabel).join("、")}；当前按匹配度最高的${candidate?.domainLabel || "默认含义"}执行，可在本条件内替换。`;
  }

  function renderInlineSemanticOptions(parsed, group) {
    if (!group?.needsConfirmation || group.candidates.length <= 1) return "";
    return `<div class="ai-inline-alternatives">
      <div class="ai-inline-alternatives-head"><strong>同类待确认</strong><span>切换会替代当前条件，不会新增叠加</span></div>
      <div class="ai-semantic-options">${group.candidates.map((candidate) => {
        const selected = candidate.id === group.selectedId;
        const stats = semanticCandidateStats(parsed, group, candidate);
        return `<label class="ai-semantic-option${selected ? " is-selected" : ""}">
          <input type="radio" name="${B.esc(group.id)}" value="${B.esc(candidate.id)}" data-semantic-group="${B.esc(group.id)}"${selected ? " checked" : ""}>
          <span class="ai-semantic-option-main">
            <small>${B.esc(candidate.domainLabel)} · ${B.esc(candidate.relationLabel)}</small>
            <strong>${B.esc(candidate.filter.label || filterLabel(candidate.filter))}</strong>
            <em>独立命中 ${stats.remaining.toLocaleString("zh-CN")} 条 · 独立筛除 ${stats.removed.toLocaleString("zh-CN")} 条</em>
          </span>
          <b>${Math.round(candidate.confidence * 100)}%</b>
        </label>`;
      }).join("")}</div>
    </div>`;
  }

  function editableFilterValue(filter = {}) {
    if (filter.field === "__benchmark_text" && filter.semanticLabel) return raw(filter.semanticLabel);
    return filter.value === undefined || filter.value === null ? "" : raw(filter.value);
  }

  function conditionRowHtml(filter = {}, context = {}) {
    const field = filter.field || "__any_text";
    const op = filter.op || "contains";
    const value = editableFilterValue(filter);
    const stats = context.stats;
    const group = context.group;
    const index = Number(context.index) || 1;
    const filterIndex = Number.isInteger(context.filterIndex) ? context.filterIndex : -1;
    const title = context.blank ? "新增筛选条件" : (filter.label || filterLabel(filter));
    const reason = context.blank ? "手工新增条件，填写后应用即可参与筛选。" : semanticRecognitionReason(context.parsed || state.parsed || {}, filter, group);
    return `<article class="ai-recognized-condition ai-condition-row${group?.needsConfirmation ? " is-pending" : ""}${context.blank ? " is-new" : ""}" data-filter-index="${filterIndex}">
      <div class="ai-recognized-index">${context.blank ? "+" : index}</div>
      <div class="ai-recognized-main">
        <div class="ai-recognized-overview">
          <div class="ai-recognized-copy">
            <div class="ai-recognized-title">
              <strong>${B.esc(title)}</strong>
              ${context.blank ? "" : `<span>${B.esc(confidenceText(filter))}</span>`}
              ${group?.needsConfirmation ? `<em>待确认</em>` : ""}
            </div>
          </div>
          <div class="ai-recognized-counts">
            ${stats
              ? `<span>独立筛除 <strong>${stats.removed.toLocaleString("zh-CN")}</strong></span><span>独立命中 <strong>${stats.remaining.toLocaleString("zh-CN")}</strong></span>`
              : `<span>应用后计算 <strong>-</strong></span>`}
          </div>
        </div>
        <div class="ai-inline-condition-editor">
          <label><span>字段</span><select class="control ai-filter-field" aria-label="筛选字段">${optionHtml(filterFieldNames(), field)}</select></label>
          <label><span>关系</span><select class="control ai-filter-op" aria-label="筛选关系">${operatorOptionHtml(op)}</select></label>
          <label class="ai-inline-value"><span>值</span><input class="control ai-filter-value" aria-label="筛选值" value="${B.esc(value)}"></label>
          <button class="ai-remove-filter" type="button" title="删除条件">删除</button>
        </div>
        <p class="ai-recognition-reason"><b>识别说明：</b>${B.esc(reason)}</p>
        ${renderInlineSemanticOptions(context.parsed || state.parsed || {}, group)}
      </div>
    </article>`;
  }

  function renderRecognizedConditions(parsed) {
    const stats = conditionFilterStats(parsed);
    const groupMap = new Map((parsed.semanticGroups || []).map((group) => [group.id, group]));
    return `<div id="aiConditionBody" class="ai-recognized-grid">
      ${stats.length ? stats.map((item, index) => conditionRowHtml(item.filter, {
        parsed,
        stats: item,
        group: groupMap.get(item.filter.semanticGroupId),
        index: index + 1,
        filterIndex: item.filterIndex,
      })).join("") : `<div class="empty">当前没有识别到业务筛选条件，可手工新增。</div>`}
      </div>
      <div class="ai-condition-actions">
        <span>修改字段、关系或值后，点击应用调整。</span>
        <div class="ai-editor-actions">
          <button id="aiAddFilter" type="button">新增条件</button>
          <button id="aiApplyFilters" type="button">应用调整</button>
        </div>
      </div>`;
  }

  function readEditorFilters() {
    return Array.from(root.querySelectorAll(".ai-condition-row")).map((row) => {
      const field = row.querySelector(".ai-filter-field")?.value || "";
      const op = row.querySelector(".ai-filter-op")?.value || "contains";
      const value = row.querySelector(".ai-filter-value")?.value || "";
      const filterIndex = Number(row.dataset.filterIndex);
      const original = Number.isInteger(filterIndex) && filterIndex >= 0 ? state.parsed?.filters?.[filterIndex] : null;
      const unchanged = original
        && !original.system
        && original.field === field
        && raw(original.op || "contains") === op
        && editableFilterValue(original) === value;
      if (unchanged) return { ...original };
      return {
        field,
        op,
        value,
        label: filterLabel({ field, op, value }),
        unit: original?.field === field ? (original.unit || "") : "",
        minWeight: original?.field === field && op === "weight_gte" ? original.minWeight : undefined,
        manualEdited: true,
      };
    }).filter((filter) => filter.field && (["is empty", "is not empty"].includes(filter.op) || raw(filter.value).trim() !== ""));
  }

  function bindConditionRow(row) {
    const remove = row.querySelector(".ai-remove-filter");
    if (remove) {
      remove.addEventListener("click", () => {
        row.remove();
      });
    }
    row.querySelectorAll(".ai-filter-field, .ai-filter-op, .ai-filter-value").forEach((control) => {
      control.addEventListener("change", () => row.classList.add("is-dirty"));
      if (control.matches("input")) control.addEventListener("input", () => row.classList.add("is-dirty"));
    });
  }

  function applyEditedFilters() {
    const parsed = state.parsed;
    if (!parsed) return;
    const editedFilters = readEditorFilters();
    const activeSemanticGroupIds = new Set(editedFilters.map((filter) => filter.semanticGroupId).filter(Boolean));
    parsed.completeOnly = true;
    parsed.filters = [
      { field: "业绩完整", op: "=", value: "是", label: "仅业绩完整策略", system: true },
      ...editedFilters,
    ];
    parsed.semanticGroups = (parsed.semanticGroups || []).filter((group) => activeSemanticGroupIds.has(group.id));
    parsed.thresholds = parsed.thresholds || {};
    parsed.manualEdited = true;
    parsed.assumptions = (parsed.assumptions || []).filter((item) => !/^已按页面条件表/.test(item));
    parsed.assumptions.push("已按页面条件表重新筛选；自然语言原文不会覆盖手工调整，除非重新点击执行筛选。");
    syncParsedFromFilters(parsed);
    const result = applyFilters(parsed);
    state.rows = result.rows;
    pruneSelectedToRows(result.rows);
    if (state.selectedScatterId && !result.rows.some((row) => raw(row.统一策略ID || row.策略代码 || row.策略名称) === state.selectedScatterId)) {
      state.selectedScatterId = "";
    }
    renderResults(parsed, result);
  }

  function bindFilterEditor() {
    const addButton = B.byId("aiAddFilter");
    const applyButton = B.byId("aiApplyFilters");
    const body = B.byId("aiConditionBody");
    if (addButton && body) {
      addButton.addEventListener("click", () => {
        body.querySelector(".empty")?.remove();
        const nextIndex = body.querySelectorAll(".ai-condition-row").length + 1;
        body.insertAdjacentHTML("beforeend", conditionRowHtml(
          { field: "__any_text", op: "contains", value: "", manualEdited: true },
          { index: nextIndex, filterIndex: -1, blank: true, parsed: state.parsed },
        ));
        const lastRow = body.querySelector(".ai-condition-row:last-child");
        if (lastRow) bindConditionRow(lastRow);
      });
    }
    if (applyButton) applyButton.addEventListener("click", applyEditedFilters);
    root.querySelectorAll(".ai-condition-row").forEach(bindConditionRow);
  }

  function bindSemanticCandidateSwitches() {
    root.querySelectorAll("[data-semantic-group]").forEach((input) => {
      input.addEventListener("change", () => {
        if (!input.checked || !state.parsed) return;
        const groupId = raw(input.dataset.semanticGroup);
        const group = (state.parsed.semanticGroups || []).find((item) => item.id === groupId);
        const candidate = group?.candidates?.find((item) => item.id === input.value);
        if (!group || !candidate || group.selectedId === candidate.id) return;
        group.selectedId = candidate.id;
        group.userSelected = true;
        const filterIndex = state.parsed.filters.findIndex((filter) => filter.semanticGroupId === group.id);
        if (filterIndex >= 0) state.parsed.filters.splice(filterIndex, 1, { ...candidate.filter, ambiguous: true });
        else state.parsed.filters.push({ ...candidate.filter, ambiguous: true });
        state.parsed.assumptions = (state.parsed.assumptions || []).filter((item) => !item.startsWith(`“${group.sourceText}”已手工切换`));
        state.parsed.assumptions.push(`“${group.sourceText}”已手工切换为“${candidate.domainLabel}”，同组原条件已被替换。`);
        const result = applyFilters(state.parsed);
        state.rows = result.rows;
        pruneSelectedToRows(result.rows);
        renderResults(state.parsed, result);
      });
    });
  }

  function renderCandidateScopeNote(parsed, result) {
    const total = allRows.length;
    const performanceComplete = allRows.filter(isPerformanceCompleteStrategy).length;
    const missingRows = result.missing?.rowIds?.size || 0;
    const latest = latestDataDate();
    const holdingEvidenceCount = holdingEvidenceByStrategy().size;
    const modelStatus = parsed.model?.status === "used"
      ? `模型 ${parsed.model.model || "codex"} 已受控调用 ${parsed.model.callCount || 1} 次`
      : parsed.model?.status === "fallback"
        ? "模型不可用，已回退本地规则"
        : "本地规则已完整解析（模型 0 次）";
    const metricMode = parsed.returnMetric.explicit ? "用户指定或明确命中" : "系统默认";
    const qualityText = missingRows
      ? `字段缺失或待核验 ${missingRows.toLocaleString("zh-CN")} 条未进入严格结果`
      : "候选策略已按业绩完整口径核验";
    return `候选命中 ${result.rows.length.toLocaleString("zh-CN")} 条；业绩完整候选池 ${result.baseCount.toLocaleString("zh-CN")} 条（全量 ${total.toLocaleString("zh-CN")}、业绩完整 ${performanceComplete.toLocaleString("zh-CN")}）；收益口径为 ${B.esc(parsed.returnMetric.field)}（${B.esc(metricMode)}）。${B.esc(modelStatus)}；数据基准日 ${dateText(latest)}，持仓核验 ${holdingEvidenceCount.toLocaleString("zh-CN")} 策；${B.esc(qualityText)}。`;
  }

  function renderDsl(parsed) {
    const payload = {
      asOfDate: dateText(parsed.asOf),
      performanceCompleteOnly: parsed.completeOnly,
      modelParse: parsed.model || { status: "local-rule" },
      filters: parsed.filters.map((item) => ({ field: item.field, op: item.op, value: item.value, unit: item.unit || "" })),
      sort: [{ field: parsed.returnMetric.field, direction: "desc" }],
      assumptions: parsed.assumptions,
    };
    return `<pre class="ai-json">${B.esc(JSON.stringify(payload, null, 2))}</pre>`;
  }

  function queryChecks(parsed) {
    const checks = [];
    activeFilters(parsed)
      .filter((filter) => !(filter.system && filter.field === "业绩完整"))
      .forEach((filter, index) => {
        checks.push({
          key: `filter-${index}`,
          field: filter.field,
          filter,
          label: filterLabel(filter),
          test: (row) => compareFilter(row, filter, null),
        });
      });
    return checks;
  }

  function renderNoResultDiagnostics(parsed) {
    const checks = queryChecks(parsed);
    if (!checks.length) return "";
    const diagnosticPool = parsed.completeOnly ? allRows.filter(isPerformanceCompleteStrategy) : allRows.slice();
    let running = diagnosticPool.slice();
    const steps = checks.map((check) => {
      const independentCount = diagnosticPool.filter(check.test).length;
      running = running.filter(check.test);
      return { label: check.label, independentCount, runningCount: running.length };
    });
    const nearMiss = nearMissRows(parsed, checks);
    return `
      <section class="panel">
        <div class="panel-head"><div><h2>无结果诊断</h2><p class="desc">严格命中为 0；下面只展示筛选过程和接近候选，不并入候选结果。</p></div></div>
        <div class="ai-diagnostic-grid">
          ${steps.map((step) => `<div class="ai-diagnostic-card"><strong>${B.esc(step.label)}</strong><span>单条件 ${step.independentCount.toLocaleString("zh-CN")} 条</span><span>逐步剩余 ${step.runningCount.toLocaleString("zh-CN")} 条</span></div>`).join("")}
        </div>
        ${nearMiss.length ? `<h3>接近候选（未入选）</h3><p class="desc">这些策略满足除回撤阈值外的主要条件，供核验阈值是否过严。</p>${renderRowsTable(nearMiss, dynamicHeaders(parsed), "暂无接近候选")}` : ""}
      </section>
    `;
  }

  function selectedRows() {
    const byId = new Map(allRows.map((row) => [raw(row.统一策略ID), row]));
    return state.selectedCompareIds.map((id) => byId.get(id)).filter(Boolean);
  }

  function pruneSelectedToRows(rows) {
    const valid = new Set((rows || []).map((row) => raw(row.统一策略ID)).filter(Boolean));
    state.selectedCompareIds = state.selectedCompareIds.filter((id) => valid.has(id));
  }

  function compareUrl() {
    const ids = state.selectedCompareIds.slice(0, compareMaxCount).map(encodeURIComponent).join(",");
    const script = Array.from(document.scripts).find((item) => /\/ai-strategy\.js(?:\?|$)/.test(item.src || ""));
    const version = script ? new URL(script.src, document.baseURI).searchParams.get("v") || "" : "";
    return `./compare.html?compare=${ids}${version ? `&v=${encodeURIComponent(version)}` : ""}`;
  }

  function renderCompareToolbar(rows) {
    const selected = selectedRows();
    const visible = rows.slice(0, state.limit).map((row) => raw(row.统一策略ID)).filter(Boolean);
    const canAdd = state.selectedCompareIds.length < compareMaxCount;
    return `
      <div class="ai-compare-toolbar">
        <div>
          <strong>已选 ${state.selectedCompareIds.length}/${compareMaxCount}</strong>
          <span>${selected.map((row) => B.esc(row.策略名称 || row.统一策略ID)).join("、") || "从左侧复选框选择策略"}</span>
        </div>
        <div class="ai-compare-actions">
          <button id="aiSelectTop" type="button"${visible.length && canAdd ? "" : " disabled"}>选择前${Math.min(compareMaxCount, visible.length)}条</button>
          <button id="aiClearCompare" type="button"${state.selectedCompareIds.length ? "" : " disabled"}>清空选择</button>
          <button id="aiOpenCompare" type="button"${state.selectedCompareIds.length >= 2 ? "" : " disabled"}>打开策略对比</button>
        </div>
      </div>
    `;
  }

  function selectionCell(row) {
    const id = raw(row.统一策略ID);
    const checked = state.selectedCompareIds.includes(id);
    const disabled = !checked && state.selectedCompareIds.length >= compareMaxCount;
    return `<td class="ai-select-cell ai-sticky-select"><input class="ai-compare-check" type="checkbox" aria-label="选择${B.esc(row.策略名称 || id)}" data-ai-compare-id="${B.esc(id)}"${checked ? " checked" : ""}${disabled ? " disabled" : ""}></td>`;
  }

  function bindCompareSelection(rows) {
    root.querySelectorAll(".ai-compare-check").forEach((input) => {
      input.addEventListener("change", () => {
        const id = input.dataset.aiCompareId || "";
        if (!id) return;
        if (input.checked) {
          if (!state.selectedCompareIds.includes(id) && state.selectedCompareIds.length < compareMaxCount) state.selectedCompareIds.push(id);
        } else {
          state.selectedCompareIds = state.selectedCompareIds.filter((item) => item !== id);
        }
        renderResults(state.parsed, state.lastResult || { rows: state.rows, missing: { generic: 0 }, baseCount: allRows.filter(isPerformanceCompleteStrategy).length });
      });
    });
    const selectTop = B.byId("aiSelectTop");
    if (selectTop) {
      selectTop.addEventListener("click", () => {
        const ids = rows.slice(0, state.limit).map((row) => raw(row.统一策略ID)).filter(Boolean);
        state.selectedCompareIds = [];
        ids.some((id) => {
          if (!state.selectedCompareIds.includes(id)) state.selectedCompareIds.push(id);
          return state.selectedCompareIds.length >= compareMaxCount;
        });
        renderResults(state.parsed, state.lastResult || { rows: state.rows, missing: { generic: 0 }, baseCount: allRows.filter(isPerformanceCompleteStrategy).length });
      });
    }
    const clearButton = B.byId("aiClearCompare");
    if (clearButton) {
      clearButton.addEventListener("click", () => {
        state.selectedCompareIds = [];
        renderResults(state.parsed, state.lastResult || { rows: state.rows, missing: { generic: 0 }, baseCount: allRows.filter(isPerformanceCompleteStrategy).length });
      });
    }
    const openButton = B.byId("aiOpenCompare");
    if (openButton) {
      openButton.addEventListener("click", () => {
        if (state.selectedCompareIds.length >= 2) window.location.href = compareUrl();
      });
    }
  }

  function openHitReasonDialog(id) {
    const row = (state.lastResult?.rows || state.rows || []).find((item) => raw(item.统一策略ID) === raw(id))
      || allRows.find((item) => raw(item.统一策略ID) === raw(id));
    if (!row) return;
    const reasonItems = Array.isArray(row._aiHitReasonItems) && row._aiHitReasonItems.length
      ? row._aiHitReasonItems
      : (row._aiHitReason || "暂无命中说明").split("；").map((item) => ({ condition: "筛选条件", result: item }));
    B.byId("aiHitReasonDialog")?.remove();
    const dialog = document.createElement("dialog");
    dialog.id = "aiHitReasonDialog";
    dialog.className = "ai-hit-dialog";
    dialog.innerHTML = `
      <div class="ai-hit-dialog-head">
        <div>
          <strong>${B.esc(row.策略名称 || "未命名策略")}</strong>
          <span>${B.esc(row.投顾机构 || row.渠道 || row.统一策略ID || "")}</span>
        </div>
        <button type="button" data-ai-close-reason aria-label="关闭">关闭</button>
      </div>
      <div class="ai-hit-dialog-body">
        <div class="ai-hit-reason-list">
          ${reasonItems.map((item) => `
            <section class="ai-hit-reason-item">
              <div class="ai-hit-reason-condition">
                <span>筛选条件</span>
                <strong>${B.esc(item.condition || "筛选条件")}</strong>
              </div>
              <div class="ai-hit-reason-result">
                <span class="status-badge ok">符合</span>
                <p>${B.esc(item.result || "已满足该条件")}</p>
                ${item.meta ? `<em>${B.esc(item.meta)}</em>` : ""}
                ${(item.details || []).length ? `
                  <ul>
                    ${item.details.map((detail) => `
                      <li>
                        <b>${B.esc(detail.label || "")}</b>
                        ${detail.value ? `<strong>${B.esc(detail.value)}</strong>` : ""}
                        ${(detail.notes || []).length ? `<small>${detail.notes.map((note) => B.esc(note)).join("；")}</small>` : ""}
                      </li>
                    `).join("")}
                  </ul>
                ` : ""}
              </div>
            </section>
          `).join("")}
        </div>
      </div>
    `;
    document.body.appendChild(dialog);
    const close = () => {
      dialog.close();
      dialog.remove();
    };
    dialog.querySelector("[data-ai-close-reason]")?.addEventListener("click", close);
    dialog.addEventListener("click", (event) => {
      if (event.target === dialog) close();
    });
    dialog.showModal();
  }

  function bindHitReasonButtons() {
    root.querySelectorAll(".ai-hit-reason-btn").forEach((button) => {
      button.addEventListener("click", () => openHitReasonDialog(button.dataset.aiHitId || ""));
    });
  }

  function hasFilterOnField(parsed, field) {
    return (parsed.filters || []).some((filter) => filter.field === field)
      || (parsed.thresholds.genericFilters || []).some((filter) => filter.field === field);
  }

  function addKycRecommendationFilters(parsed, query) {
    const text = raw(query);
    const retireeContext = /退休老人|退休|老年|老人|养老资金|养老金|60岁|六十岁|父母|长辈/.test(text);
    const depositBenchmark = /跑赢.{0,8}(定存|存款|银行理财)|定存|存款利率|五年期定存|5年期定存/.test(text);
    const preserveCapital = /本金安全|不想亏|少亏|稳一点|稳健|保守|低风险/.test(text);
    if (!retireeContext && !depositBenchmark && !preserveCapital) return;

    if (!hasFilterOnField(parsed, "风险等级序号")) {
      const maxRisk = retireeContext ? 3 : 4;
      addGenericFilter(parsed, {
        field: "风险等级序号",
        op: "<=",
        value: maxRisk,
        label: `KYC稳健约束：风险等级 <= R${maxRisk}`,
      });
    }
    if (parsed.thresholds.maxDrawdown === undefined && !hasFilterOnField(parsed, "最大回撤")) {
      const maxDrawdown = retireeContext || preserveCapital ? 6 : 8;
      parsed.thresholds.drawdownField = "最大回撤";
      parsed.thresholds.maxDrawdown = maxDrawdown;
      parsed.filters.push({ field: "最大回撤", op: "<=", value: maxDrawdown, unit: "%", label: `KYC稳健约束：最大回撤 <= ${maxDrawdown}%` });
    }
    if (depositBenchmark && parsed.thresholds.minReturn === undefined) {
      parsed.returnMetric = { field: "年化收益", explicit: true, source: "kyc_deposit_benchmark" };
      parsed.thresholds.minReturn = 3;
      parsed.filters.push({ field: "年化收益", op: ">=", value: 3, unit: "%", label: "跑赢定存：年化收益 >= 3%" });
    }
    parsed.assumptions.push("识别到KYC/推荐型描述，默认采用稳健可解释约束：控制风险等级和最大回撤，并按收益目标排序；如客户明确给出风险等级、回撤或收益目标，则以客户条件为准。");
  }

  function bindCandidateScrollbars() {
    root.querySelectorAll(".ai-candidate-table-shell").forEach((shell) => {
      const wrap = shell.querySelector("[data-ai-table-wrap]");
      const bars = Array.from(shell.querySelectorAll("[data-ai-scrollbar]"));
      if (!wrap || !bars.length) return;
      const inners = bars.map((bar) => bar.querySelector(".strategy-scrollbar-inner")).filter(Boolean);
      const resize = () => {
        inners.forEach((inner) => {
          inner.style.width = `${wrap.scrollWidth}px`;
        });
      };
      let syncing = false;
      const syncTo = (left, source) => {
        if (syncing) return;
        syncing = true;
        if (source !== wrap) wrap.scrollLeft = left;
        bars.forEach((bar) => {
          if (bar !== source) bar.scrollLeft = left;
        });
        syncing = false;
      };
      wrap.addEventListener("scroll", () => syncTo(wrap.scrollLeft, wrap));
      bars.forEach((bar) => bar.addEventListener("scroll", () => syncTo(bar.scrollLeft, bar)));
      resize();
      requestAnimationFrame(resize);
    });
  }

  function nearMissRows(parsed, checks) {
    const target = checks.find((check) => /回撤/.test(raw(check.field)) && ["<=", "<"].includes(check.filter?.op));
    if (!target) return [];
    const drawdownField = target.field;
    const withoutDrawdown = checks.filter((check) => check !== target);
    const pool = parsed.completeOnly ? allRows.filter(isPerformanceCompleteStrategy) : allRows;
    return pool
      .filter((row) => withoutDrawdown.every((check) => check.test(row)))
      .filter((row) => num(row[drawdownField]) !== null)
      .sort((a, b) => {
        const av = num(a[drawdownField]) ?? 9999;
        const bv = num(b[drawdownField]) ?? 9999;
        if (av !== bv) return av - bv;
        return (num(b[parsed.returnMetric.field]) ?? -9999) - (num(a[parsed.returnMetric.field]) ?? -9999);
      })
      .slice(0, 8);
  }

  function candidateCellClass(field, isHead = false) {
    const classes = [];
    if (isHead) classes.push("ai-candidate-head");
    if (field === "命中说明") classes.push("ai-sticky-hit", "ai-hit-reason-col");
    if (field === "策略名称") classes.push("ai-sticky-name", "strategy-name-cell");
    if (field === B.strategyListInstitutionField) classes.push("ai-sticky-institution", "strategy-institution-cell");
    if ([...B.strategyListFieldGroups.returns, ...B.strategyListFieldGroups.risks, ...B.strategyListFieldGroups.weights].includes(field)) classes.push("narrow");
    if ([B.strategyListInstitutionField, "研报产品类型", "研报股票子类型", "风险等级", "业务分类", "主动被动", "披露策略类型", "天天当前对客展示", "天天展示状态", "基准风险资产权重", "基准可用状态", "业绩基准说明"].includes(field)) classes.push("wide");
    return classes.join(" ");
  }

  function renderRowsTable(rows, headers, emptyText, options = {}) {
    const withSelection = !!options.withSelection;
    if (withSelection) {
      const headerHtml = `${withSelection ? '<th class="ai-select-head ai-sticky-select">选择</th>' : ""}${headers.map((field) => `<th class="${candidateCellClass(field, true)}">${field === "命中说明" ? B.label(field) : B.strategyListHeaderLabel(field)}</th>`).join("")}`;
      const body = rows.length
        ? rows.map((row) => `<tr>${selectionCell(row)}${headers.map((field) => `<td class="${candidateCellClass(field)}">${formatValue(row, field)}</td>`).join("")}</tr>`).join("")
        : `<tr><td colspan="${headers.length + 1}"><div class="empty">${B.esc(emptyText)}</div></td></tr>`;
      return `
        <div class="strategy-table-shell ai-candidate-table-shell">
          <div class="strategy-scrollbar ai-candidate-scrollbar is-top" data-ai-scrollbar aria-label="候选策略横向滚动条（上）"><div class="strategy-scrollbar-inner"></div></div>
          <div class="strategy-table-wrap ai-candidate-table-wrap" data-ai-table-wrap>
            <table class="strategy-table ai-candidate-table">
              <thead><tr>${headerHtml}</tr></thead>
              <tbody>${body}</tbody>
            </table>
          </div>
          <div class="strategy-scrollbar ai-candidate-scrollbar is-bottom" data-ai-scrollbar aria-label="候选策略横向滚动条（下）"><div class="strategy-scrollbar-inner"></div></div>
        </div>
      `;
    }
    const body = rows.length ? rows.map((row) => `<tr>${headers.map((field) => `<td>${formatValue(row, field)}</td>`).join("")}</tr>`).join("") : `<tr><td colspan="${headers.length}"><div class="empty">${B.esc(emptyText)}</div></td></tr>`;
    return `
      <div class="table-wrap ai-result-table">
        <table>
          <thead><tr>${headers.map((field) => `<th>${B.label(field)}</th>`).join("")}</tr></thead>
          <tbody>${body}</tbody>
        </table>
      </div>
    `;
  }

  function chartTicks(min, max, count = 5) {
    if (!Number.isFinite(min) || !Number.isFinite(max) || max <= min) return [];
    return Array.from({ length: count + 1 }, (_, index) => min + ((max - min) * index / count));
  }

  function isNonNegativeScatterField(field) {
    return /回撤|波动|权重|持仓基金数|调仓次数|换手率/.test(raw(field));
  }

  function formatScatterMetric(field, value) {
    const n = num(value);
    if (n === null) return "未披露";
    if (percentMetricFields.has(field)) return `${n.toFixed(2)}%`;
    return n.toLocaleString("zh-CN", { maximumFractionDigits: Math.abs(n) >= 10 ? 1 : 4 });
  }

  function availableScatterFields(rows) {
    return scatterMetricOptions.filter((field) => (rows || []).some((row) => num(row[field]) !== null));
  }

  function resolveScatterField(rows, requested, fallback) {
    const fields = availableScatterFields(rows);
    if (requested && fields.includes(requested)) return requested;
    if (fallback && fields.includes(fallback)) return fallback;
    return fields[0] || fallback || "累计收益率";
  }

  function resolveScatterInput(rows, value, fallback) {
    const fields = availableScatterFields(rows);
    const text = raw(value).trim();
    if (fields.includes(text)) return text;
    const normalized = normalizeSearchText(text);
    const matched = fields.find((field) => normalizeSearchText(field).includes(normalized) || normalized.includes(normalizeSearchText(field)));
    return matched || resolveScatterField(rows, fallback, fields[0]);
  }

  function scatterMetricSelect(id, label, current, fields) {
    return `
      <label class="ai-scatter-control">
        <span>${B.esc(label)}</span>
        <input id="${B.esc(id)}" class="control ai-scatter-metric-input" list="${B.esc(id)}List" value="${B.esc(current)}" placeholder="输入或选择指标">
        <datalist id="${B.esc(id)}List">
          ${fields.map((field) => `<option value="${B.esc(field)}"></option>`).join("")}
        </datalist>
      </label>
    `;
  }

  function scatterGroup(row) {
    return raw(row.风险等级 || row.研报产品类型 || row.业务分类 || "未分类");
  }

  function scatterThresholdForField(field, parsed) {
    const drawdownField = parsed?.thresholds?.drawdownField || "最大回撤";
    if (field === drawdownField) {
      const value = num(parsed?.thresholds?.maxDrawdown);
      return value === null ? null : { value, label: "回撤阈值" };
    }
    const returnField = parsed?.returnMetric?.field || "累计收益率";
    if (field === returnField) {
      const value = num(parsed?.thresholds?.minReturn);
      return value === null ? null : { value, label: "收益阈值" };
    }
    return null;
  }

  function scatterDomain(values, threshold, field) {
    const points = values.slice();
    if (threshold && Number.isFinite(threshold.value)) points.push(threshold.value);
    if (!points.length) return [0, 1];
    let minValue = Math.min(...points);
    let maxValue = Math.max(...points);
    if (minValue === maxValue) {
      minValue -= 1;
      maxValue += 1;
    }
    const padding = Math.max(1, (maxValue - minValue) * 0.08);
    const min = isNonNegativeScatterField(field) ? Math.max(0, minValue - padding) : minValue - padding;
    const max = maxValue + padding;
    return max <= min ? [min, min + 1] : [min, max];
  }

  function renderScatterDetail(row, xField, yField) {
    if (!row) {
      return `<div class="ai-scatter-detail is-empty"><strong>点阵选中策略</strong><p>点击图中的点查看该策略的机构、分类、关键指标和命中说明。</p></div>`;
    }
    const id = raw(row.统一策略ID || row.策略代码);
    const metrics = [
      [xField, row[xField]],
      [yField, row[yField]],
      ["最大回撤", row.最大回撤],
      ["当前回撤", row.当前回撤],
      ["累计收益率", row.累计收益率],
      ["年化收益", row.年化收益],
      ["近6月", row.近6月],
      ["近1年", row.近1年],
    ].filter((item, index, array) => item[0] && array.findIndex((candidate) => candidate[0] === item[0]) === index);
    return `
      <div class="ai-scatter-detail">
        <div class="ai-scatter-detail-head">
          <div>
            <strong><a class="link" href="./strategy.html?id=${encodeURIComponent(id)}">${B.esc(row.策略名称 || "未命名策略")}</a></strong>
            <span>${B.esc(row.投顾机构 || "未披露机构")}｜${B.esc(row.渠道 || "未披露渠道")}｜${B.esc(row.风险等级 || "未分类")}</span>
          </div>
          <button class="ai-hit-reason-btn" type="button" data-ai-hit-id="${B.esc(id)}">查看命中说明</button>
        </div>
        <div class="ai-scatter-detail-grid">
          <div><span>业务分类</span><strong>${B.esc(row.业务分类 || "未分类")}</strong></div>
          <div><span>研报产品类型</span><strong>${B.esc(row.研报产品类型 || "未分类")}</strong></div>
          <div><span>最新业绩日期</span><strong>${B.esc(row.最新业绩日期 || "未披露")}</strong></div>
          <div><span>最新持仓日</span><strong>${B.esc(row.最新持仓日 || "未披露")}</strong></div>
          ${metrics.map(([field, value]) => `<div><span>${B.esc(field)}</span><strong>${B.esc(formatScatterMetric(field, value))}</strong></div>`).join("")}
        </div>
      </div>
    `;
  }

  function renderCandidateScatter(rows, parsed) {
    const fields = availableScatterFields(rows);
    const returnField = parsed?.returnMetric?.field || "累计收益率";
    const drawdownField = parsed?.thresholds?.drawdownField || "最大回撤";
    const xField = resolveScatterField(rows, state.scatterXField, drawdownField);
    const yField = resolveScatterField(rows, state.scatterYField, returnField);
    state.scatterXField = xField;
    state.scatterYField = yField;
    const drawableRows = (rows || []).map((row) => ({
      row,
      id: raw(row.统一策略ID || row.策略代码 || row.策略名称),
      x: num(row[xField]),
      y: num(row[yField]),
      group: scatterGroup(row),
    })).filter((item) => item.id && item.x !== null && item.y !== null);
    const sourceRows = drawableRows.slice(0, 500);
    if (!sourceRows.length) return `<div class="ai-scatter-card"><div class="empty">候选策略缺少可绘制的 ${B.esc(xField)} 或 ${B.esc(yField)} 字段。</div></div>`;
    const palette = ["#2563eb", "#0f766e", "#a855f7", "#ea580c", "#dc2626", "#64748b", "#0891b2", "#9333ea"];
    const groups = Array.from(new Set(sourceRows.map((item) => item.group))).sort((a, b) => a.localeCompare(b, "zh-CN")).slice(0, palette.length);
    const colorByGroup = new Map(groups.map((group, index) => [group, palette[index % palette.length]]));
    const selected = sourceRows.find((item) => item.id === state.selectedScatterId);
    const width = 900;
    const height = 380;
    const pad = { left: 68, right: 28, top: 24, bottom: 58 };
    const plotW = width - pad.left - pad.right;
    const plotH = height - pad.top - pad.bottom;
    const xThreshold = scatterThresholdForField(xField, parsed);
    const yThreshold = scatterThresholdForField(yField, parsed);
    const [xMin, xMax] = scatterDomain(sourceRows.map((item) => item.x), xThreshold, xField);
    const [yMin, yMax] = scatterDomain(sourceRows.map((item) => item.y), yThreshold, yField);
    const xScale = (value) => pad.left + ((value - xMin) / (xMax - xMin)) * plotW;
    const yScale = (value) => pad.top + (1 - ((value - yMin) / (yMax - yMin))) * plotH;
    const xTicks = chartTicks(xMin, xMax, 5);
    const yTicks = chartTicks(yMin, yMax, 5);
    const xLine = xThreshold && xThreshold.value >= xMin && xThreshold.value <= xMax ? xScale(xThreshold.value) : null;
    const yLine = yThreshold && yThreshold.value >= yMin && yThreshold.value <= yMax ? yScale(yThreshold.value) : null;
    return `
      <div class="ai-scatter-card" data-ai-scatter-card>
        <div class="ai-scatter-head">
          <div>
            <strong>候选策略点阵</strong>
            <span>筛选命中 ${(rows || []).length.toLocaleString("zh-CN")} 条，其中 ${drawableRows.length.toLocaleString("zh-CN")} 条具备 ${B.esc(xField)} 与 ${B.esc(yField)}；点阵最多展示前 ${sourceRows.length.toLocaleString("zh-CN")} 条，点击点查看策略说明。</span>
          </div>
          <div class="ai-scatter-controls">
            ${scatterMetricSelect("aiScatterXField", "X轴", xField, fields)}
            ${scatterMetricSelect("aiScatterYField", "Y轴", yField, fields)}
          </div>
        </div>
        <div class="ai-scatter-legend" aria-label="点阵图例">
          ${groups.map((group) => `<span><i style="background:${colorByGroup.get(group)}"></i>${B.esc(group)}</span>`).join("")}
          ${groups.length < new Set(sourceRows.map((item) => item.group)).size ? `<span><i style="background:#475569"></i>其他分类</span>` : ""}
        </div>
        <div class="ai-scatter-layout">
          <div class="ai-scatter-wrap">
            <svg class="ai-scatter-svg" viewBox="0 0 ${width} ${height}" role="img" aria-label="候选策略点阵">
              <rect x="${pad.left}" y="${pad.top}" width="${plotW}" height="${plotH}" class="ai-scatter-bg"></rect>
              ${xTicks.map((tick) => {
                const x = xScale(tick);
                return `<line x1="${x}" y1="${pad.top}" x2="${x}" y2="${pad.top + plotH}" class="ai-scatter-grid"></line><text x="${x}" y="${height - 28}" text-anchor="middle" class="ai-scatter-axis">${B.esc(formatScatterMetric(xField, tick))}</text>`;
              }).join("")}
              ${yTicks.map((tick) => {
                const y = yScale(tick);
                return `<line x1="${pad.left}" y1="${y}" x2="${pad.left + plotW}" y2="${y}" class="ai-scatter-grid"></line><text x="${pad.left - 10}" y="${y + 4}" text-anchor="end" class="ai-scatter-axis">${B.esc(formatScatterMetric(yField, tick))}</text>`;
              }).join("")}
              ${xLine === null ? "" : `<line x1="${xLine}" y1="${pad.top}" x2="${xLine}" y2="${pad.top + plotH}" class="ai-scatter-threshold"></line><text x="${xLine + 6}" y="${pad.top + 15}" class="ai-scatter-threshold-text">${B.esc(xThreshold.label)}</text>`}
              ${yLine === null ? "" : `<line x1="${pad.left}" y1="${yLine}" x2="${pad.left + plotW}" y2="${yLine}" class="ai-scatter-threshold"></line><text x="${pad.left + 8}" y="${yLine - 6}" class="ai-scatter-threshold-text">${B.esc(yThreshold.label)}</text>`}
              <line x1="${pad.left}" y1="${pad.top + plotH}" x2="${pad.left + plotW}" y2="${pad.top + plotH}" class="ai-scatter-axis-line"></line>
              <line x1="${pad.left}" y1="${pad.top}" x2="${pad.left}" y2="${pad.top + plotH}" class="ai-scatter-axis-line"></line>
              ${sourceRows.map((item) => {
                const label = `${item.row.策略名称 || "未命名策略"}｜${xField} ${formatScatterMetric(xField, item.x)}｜${yField} ${formatScatterMetric(yField, item.y)}｜${item.row.投顾机构 || ""}`;
                const color = colorByGroup.get(item.group) || "#475569";
                const selectedClass = item.id === state.selectedScatterId ? " is-selected" : "";
                return `<circle cx="${xScale(item.x).toFixed(2)}" cy="${yScale(item.y).toFixed(2)}" r="4.6" fill="${color}" class="ai-scatter-dot${selectedClass}" data-ai-scatter-id="${B.esc(item.id)}" tabindex="0" role="button" aria-label="${B.esc(label)}"><title>${B.esc(label)}</title></circle>`;
              }).join("")}
              <text x="${pad.left + plotW / 2}" y="${height - 9}" text-anchor="middle" class="ai-scatter-label">${B.esc(xField)}</text>
              <text transform="translate(17 ${pad.top + plotH / 2}) rotate(-90)" text-anchor="middle" class="ai-scatter-label">${B.esc(yField)}</text>
            </svg>
          </div>
          <div id="aiScatterDetail" class="ai-scatter-detail-slot">
            ${renderScatterDetail(selected?.row || null, xField, yField)}
          </div>
        </div>
      </div>
    `;
  }

  function renderCandidateScatterIntoMount() {
    const mount = B.byId("aiScatterMount");
    if (!mount || !state.lastResult) return;
    mount.innerHTML = renderCandidateScatter(state.lastResult.rows || state.rows || [], state.parsed);
    bindCandidateScatter();
  }

  function selectCandidateScatterPoint(id) {
    state.selectedScatterId = raw(id);
    root.querySelectorAll(".ai-scatter-dot").forEach((dot) => {
      dot.classList.toggle("is-selected", dot.getAttribute("data-ai-scatter-id") === state.selectedScatterId);
    });
    const row = (state.lastResult?.rows || state.rows || []).find((item) => raw(item.统一策略ID || item.策略代码 || item.策略名称) === state.selectedScatterId);
    const detail = B.byId("aiScatterDetail");
    if (!detail) return;
    detail.innerHTML = renderScatterDetail(row || null, state.scatterXField, state.scatterYField);
    detail.querySelector(".ai-hit-reason-btn")?.addEventListener("click", (event) => {
      openHitReasonDialog(event.currentTarget?.dataset?.aiHitId || "");
    });
  }

  function bindCandidateScatter() {
    const xSelect = B.byId("aiScatterXField");
    const ySelect = B.byId("aiScatterYField");
    if (xSelect) {
      xSelect.addEventListener("change", () => {
        state.scatterXField = resolveScatterInput(state.lastResult?.rows || state.rows || [], xSelect.value, state.scatterXField);
        xSelect.value = state.scatterXField;
        renderCandidateScatterIntoMount();
      });
    }
    if (ySelect) {
      ySelect.addEventListener("change", () => {
        state.scatterYField = resolveScatterInput(state.lastResult?.rows || state.rows || [], ySelect.value, state.scatterYField);
        ySelect.value = state.scatterYField;
        renderCandidateScatterIntoMount();
      });
    }
    root.querySelectorAll(".ai-scatter-dot").forEach((dot) => {
      const select = () => selectCandidateScatterPoint(dot.getAttribute("data-ai-scatter-id") || "");
      dot.addEventListener("click", select);
      dot.addEventListener("keydown", (event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          select();
        }
      });
    });
    B.byId("aiScatterDetail")?.querySelector(".ai-hit-reason-btn")?.addEventListener("click", (event) => {
      openHitReasonDialog(event.currentTarget?.dataset?.aiHitId || "");
    });
  }

  function dynamicHeaders(_parsed) {
    return ["命中说明", ...B.strategyListHeaders];
  }

  function renderTable(rows) {
    const headers = dynamicHeaders(state.parsed);
    const visible = rows.slice(0, state.limit);
    return `
      ${rows.length ? `<div id="aiScatterMount">${renderCandidateScatter(rows, state.parsed)}</div>` : ""}
      ${renderCompareToolbar(rows)}
      ${renderRowsTable(visible, headers, "当前条件下暂无策略命中", { withSelection: true })}
      ${rows.length > state.limit ? `<p class="desc">当前仅展示前 ${state.limit} 条，建议继续增加机构、产品类型或收益区间条件缩小范围。</p>` : ""}
    `;
  }

  async function runSearch(options = {}) {
    const allowModel = options?.allowModel !== false;
    const seq = ++state.searchSeq;
    state.query = B.byId("aiQuery").value;
    state.completeOnly = true;
    let parsed = parseQuery(state.query);
    if (shouldUseModelParser(allowModel, parsed)) {
      B.byId("aiResult").innerHTML = `
        <section class="panel">
          <div class="panel-head"><div><h2>解析中</h2><p class="desc">本地规则仍有未识别条件或结果歧义，正在受控调用 ${B.esc(aiConfig.model || "模型")}：第一轮识别业务实体和关系，第二轮只读取相关字段卡并生成筛选计划；总上限 2 次，接口不可用不重试并自动回退本地规则。</p></div></div>
        </section>
      `;
    }
    parsed = await parseQueryHybrid(state.query, parsed, allowModel);
    if (seq !== state.searchSeq) return;
    state.parsed = parsed;
    const result = applyFilters(parsed);
    state.rows = result.rows;
    pruneSelectedToRows(result.rows);
    renderResults(parsed, result);
  }

  function renderResults(parsed, result) {
    state.lastResult = result;
    const conditionSummary = recognizedConditionSummary(parsed);
    B.byId("aiResult").innerHTML = `
      <details class="panel ai-semantic-panel ai-condition-panel-details">
        <summary class="ai-condition-panel-summary">
          <div class="ai-condition-summary-copy">
            <h2 class="ai-condition-summary-text">${B.esc(conditionSummary)}</h2>
            <p class="desc ai-condition-summary-help">点击展开后可直接微调字段、关系和值；每个条件均以业绩完整策略池独立统计，基准或仓位缺失不会被默认排除。</p>
          </div>
        </summary>
        <div class="ai-condition-panel-body">
          ${renderRecognizedConditions(parsed)}
        </div>
      </details>
      <section class="panel">
        <div class="panel-head"><div><h2>候选策略</h2><p class="desc">按匹配得分排序：优先展示收益更高、回撤更低、持仓实体权重更贴合条件的策略；${B.esc(parsed.returnMetric.field)} 和回撤作为同分次排序。</p><p class="desc ai-candidate-note">${renderCandidateScopeNote(parsed, result)}</p></div><span class="pill">${result.rows.length.toLocaleString("zh-CN")} 条</span></div>
        ${renderTable(result.rows)}
      </section>
      ${result.rows.length ? "" : renderNoResultDiagnostics(parsed)}
    `;
    bindFilterEditor();
    bindSemanticCandidateSwitches();
    bindCompareSelection(result.rows);
    bindHitReasonButtons();
    bindCandidateScrollbars();
    bindCandidateScatter();
  }

  function modelHeadersText(config = aiConfig) {
    try {
      return JSON.stringify(config.headers || {}, null, 2);
    } catch (error) {
      return "{}";
    }
  }

  function codexBridgeScriptPath(scriptName) {
    try {
      if (window.location.protocol === "file:") {
        let pagePath = decodeURIComponent(window.location.pathname || "");
        if (/^\/[A-Za-z]:\//.test(pagePath)) pagePath = pagePath.slice(1);
        pagePath = pagePath.replace(/\//g, "\\");
        const marker = "\\basic_data\\";
        const markerIndex = pagePath.toLowerCase().lastIndexOf(marker);
        if (markerIndex >= 0) {
          return `${pagePath.slice(0, markerIndex)}\\scripts\\${scriptName}`;
        }
      }
    } catch (error) {
      // Fall through to the deploy-root relative path below.
    }
    return `..\\scripts\\${scriptName}`;
  }

  function codexBridgeCommand(scriptName) {
    return `powershell -NoProfile -ExecutionPolicy Bypass -File "${codexBridgeScriptPath(scriptName)}"`;
  }

  async function copyTextToClipboard(text) {
    if (navigator.clipboard?.writeText) {
      try {
        await navigator.clipboard.writeText(text);
        return true;
      } catch (error) {
        // Some file:// contexts block Clipboard API; use the textarea fallback.
      }
    }
    const textarea = document.createElement("textarea");
    textarea.value = text;
    textarea.setAttribute("readonly", "readonly");
    textarea.style.position = "fixed";
    textarea.style.left = "-9999px";
    textarea.style.top = "0";
    document.body.appendChild(textarea);
    textarea.focus();
    textarea.select();
    const ok = document.execCommand("copy");
    document.body.removeChild(textarea);
    if (!ok) throw new Error("复制失败，请手动复制页面中的命令");
    return true;
  }

  function renderModelSettings() {
    const config = aiConfig;
    return `
      <details class="ai-model-panel">
        <summary class="ai-model-summary">
          <div>
            <strong>AI模型服务</strong>
            <span>页面已统一配置阿里云百炼，参数固定且不接受浏览器修改。</span>
          </div>
        </summary>
        <div class="ai-model-body">
          <div class="ai-model-fixed-grid">
            <div><span>模型来源</span><strong>阿里云百炼</strong></div>
            <div><span>模型</span><strong>${B.esc(config.model || "qwen3.6-flash")}</strong></div>
            <div><span>调用模式</span><strong>本地优先 + 必要时模型</strong></div>
            <div><span>返回格式</span><strong>JSON Object</strong></div>
            <div class="ai-model-fixed-wide"><span>服务地址</span><strong>${B.esc(modelBaseUrl(config))}</strong></div>
            <div><span>接口超时</span><strong>${B.esc(config.timeoutMs || 60000)} 毫秒</strong></div>
            <div><span>API Key</span><strong>已内置测试密钥</strong></div>
          </div>
          <div class="ai-model-actions">
            <button id="aiModelTest" type="button">测试连通性</button>
            <span id="aiModelTestResult" class="ai-model-test-result">未测试</span>
          </div>
          <div class="ai-model-help">
            <strong>配置说明</strong>
            <span>明确的常见问题使用本地字典和规则直接执行，模型调用为 0 次；需要模型时固定分为“业务实体路由”和“相关字段卡生成筛选计划”两轮，单次执行最多 2 次，不按子句重复调用。接口不可用或计划不合法时不继续重试，并自动回退本地规则。</span>
          </div>
        </div>
      </details>
    `;
  }

  function collectModelSettingsForm() {
    const baseUrl = raw(B.byId("aiModelBaseUrl")?.value).trim();
    const endpointInput = raw(B.byId("aiModelEndpoint")?.value).trim();
    const endpoint = normalizeModelEndpoint(endpointInput, baseUrl);
    return {
      enabled: raw(B.byId("aiModelEnabled")?.value) !== "false",
      profile: raw(B.byId("aiModelProfile")?.value || "custom"),
      mode: raw(B.byId("aiModelMode")?.value || "hybrid-parse"),
      provider: raw(B.byId("aiModelProvider")?.value || "inner-ds-openai-compatible").trim(),
      baseUrl: normalizeModelBaseUrl(baseUrl || endpoint),
      endpoint,
      model: raw(B.byId("aiModelName")?.value || "deepseek-v4-flash-inner").trim(),
      apiKey: raw(B.byId("aiModelApiKey")?.value),
      timeoutMs: Number(B.byId("aiModelTimeout")?.value) || 45000,
      responseFormat: raw(B.byId("aiModelResponseFormat")?.value) !== "false",
      rateLimitBackoffMs: Number(B.byId("aiModelBackoff")?.value) || 60000,
      headers: parseHeadersJson(B.byId("aiModelHeaders")?.value),
      modelProfiles: aiConfig.modelProfiles || aiConfigFileDefault.modelProfiles || {},
      codexBridge: aiConfig.codexBridge || aiConfigFileDefault.codexBridge || {},
    };
  }

  function updateModelStatusPill() {
    const pill = B.byId("aiModelStatusPill");
    if (pill) pill.textContent = modelDisplayLabel(aiConfig);
  }

  function setModelTestResult(kind, message) {
    const node = B.byId("aiModelTestResult");
    if (!node) return;
    node.className = `ai-model-test-result is-${kind}`;
    node.textContent = message;
  }

  async function requestModelConnectivity(config) {
    const endpoint = modelChatEndpoint(config);
    if (!endpoint) throw new Error("模型 endpoint 未配置");
    const timeoutMs = Math.min(Math.max(Number(config.timeoutMs) || 45000, 800), 120000);
    const controller = new AbortController();
    const timer = window.setTimeout(() => controller.abort(), timeoutMs);
    const headers = { "Content-Type": "application/json", ...(config.headers || {}) };
    if (config.apiKey) headers.Authorization = `Bearer ${config.apiKey}`;
    const payload = {
      model: config.model || "deepseek-v4-flash-inner",
      temperature: 0,
      max_tokens: 80,
      messages: [
        { role: "system", content: "你是连通性测试服务，只输出 JSON 对象。" },
        { role: "user", content: "请输出 {\"ok\":true,\"message\":\"pong\"}" },
      ],
    };
    if (config.responseFormat !== false) payload.response_format = { type: "json_object" };
    try {
      const response = await fetch(endpoint, {
        method: "POST",
        headers,
        body: JSON.stringify(payload),
        signal: controller.signal,
      });
      const text = await response.text();
      if (!response.ok) throw new Error(`模型接口返回 ${response.status}: ${text.slice(0, 220)}`);
      let data = null;
      try {
        data = text ? JSON.parse(text) : null;
      } catch (error) {
        throw new Error(`模型返回非 JSON：${text.slice(0, 220)}`);
      }
      const content = data?.choices?.[0]?.message?.content || data?.choices?.[0]?.text || data?.output_text || "";
      return {
        model: data?.model || config.model || "",
        content: raw(content).slice(0, 120),
      };
    } finally {
      window.clearTimeout(timer);
    }
  }

  function fillModelSettingsForm(config = aiConfig) {
    const setValue = (id, value) => {
      const node = B.byId(id);
      if (node) node.value = value;
    };
    setValue("aiModelEnabled", config.enabled === false ? "false" : "true");
    setValue("aiModelProfile", inferModelProfileKey(config));
    setValue("aiModelMode", config.mode || "hybrid-parse");
    setValue("aiModelProvider", config.provider || "inner-ds-openai-compatible");
    setValue("aiModelName", config.model || "deepseek-v4-flash-inner");
    setValue("aiModelBaseUrl", modelBaseUrl(config));
    setValue("aiModelEndpoint", modelChatEndpoint(config));
    setValue("aiModelApiKey", config.apiKey || "");
    setValue("aiModelTimeout", config.timeoutMs || 45000);
    setValue("aiModelResponseFormat", config.responseFormat === false ? "false" : "true");
    setValue("aiModelBackoff", config.rateLimitBackoffMs || 60000);
    setValue("aiModelHeaders", modelHeadersText(config));
  }

  function bindModelSettings() {
    B.byId("aiModelTest")?.addEventListener("click", async () => {
      try {
        setModelTestResult("running", "正在测试模型接口...");
        const started = performance.now();
        const result = await requestModelConnectivity(aiConfig);
        const elapsed = Math.round(performance.now() - started);
        setModelTestResult("ok", `连通成功，${result.model || aiConfig.model}，${elapsed}ms`);
      } catch (error) {
        let message = raw(error?.name === "AbortError" ? "模型测试超时" : error?.message || error);
        if (/Failed to fetch|NetworkError|Load failed/i.test(message)) {
          message = "百炼模型接口不可访问，请检查网络、CORS 跨域或测试密钥状态";
        }
        setModelTestResult("bad", message.slice(0, 220));
      }
    });
  }

  function renderShell() {
    const modelLabel = modelDisplayLabel(aiConfig);
    root.innerHTML = `
      <section class="panel ai-query-panel">
        <div class="panel-head">
          <div>
            <h2>AI选策略</h2>
            <p class="desc">输入自然语言条件，系统解析为可核验筛选条件后在当前策略宽表中执行。</p>
          </div>
          <div class="title-pills"><span class="pill">策略宽表 ${allRows.length.toLocaleString("zh-CN")} 条</span><span id="aiModelStatusPill" class="pill">${B.esc(modelLabel)}</span></div>
        </div>
        <textarea id="aiQuery" class="control ai-query-box" rows="3" placeholder="例如：找成立一年以上，回撤在3个点以内，收益率在5个点以上，持仓含黄金的策略。">${B.esc(state.query)}</textarea>
        <div class="ai-action-row">
          <button id="aiRun" type="button">执行筛选</button>
          <button id="aiClear" type="button">清空</button>
        </div>
        ${renderModelSettings()}
      </section>
      <div id="aiResult">${renderInitialResultPlaceholder()}</div>
      ${renderAiExplanationShell()}
    `;
    B.byId("aiRun").addEventListener("click", () => runSearch({ allowModel: true }));
    B.byId("aiClear").addEventListener("click", () => {
      B.byId("aiQuery").value = "";
      runSearch({ allowModel: false });
    });
    B.byId("aiQuery").addEventListener("keydown", (event) => {
      if ((event.ctrlKey || event.metaKey) && event.key === "Enter") runSearch({ allowModel: true });
    });
    bindModelSettings();
    bindAiExplanationLazyLoad();
    scheduleInitialSearchPreview();
  }

  window.__AI_STRATEGY_DEBUG__ = {
    businessRoute(queryText) {
      return normalizeModelRoute({}, queryText);
    },
    businessRouteCatalog() {
      return businessRoutingPromptCatalog();
    },
    hydrateBusinessRoute(queryText) {
      const route = normalizeModelRoute({}, queryText);
      return hydrateBusinessRoute(route, queryText);
    },
    semanticDetections(queryText) {
      return detectSemanticEntities(queryText).map((item) => ({
        key: item.key,
        label: item.label,
        type: item.type,
        term: item.term,
        negative: item.negative,
      }));
    },
    semanticCatalogSample(keyword = "") {
      const text = normalizeSearchText(keyword);
      return semanticEntityCatalog
        .filter((item) => !text
          || normalizeSearchText(item.label).includes(text)
          || (item.aliases || []).some((alias) => normalizeSearchText(alias).includes(text) || text.includes(normalizeSearchText(alias))))
        .slice(0, 50)
        .map((item) => ({ key: item.key, label: item.label, type: item.type, aliases: (item.aliases || []).slice(0, 10), note: item.note || "" }));
    },
    parseLocal(queryText, options = {}) {
      const previousCompleteOnly = state.completeOnly;
      if (Object.prototype.hasOwnProperty.call(options, "performanceCompleteOnly")) state.completeOnly = !!options.performanceCompleteOnly;
      else if (Object.prototype.hasOwnProperty.call(options, "completeOnly")) state.completeOnly = !!options.completeOnly;
      const parsed = parseQuery(queryText);
      const result = applyFilters(parsed);
      state.completeOnly = previousCompleteOnly;
      return {
        query: parsed.query,
        returnMetric: parsed.returnMetric,
        filters: parsed.filters.map((filter) => ({
          field: filter.field,
          op: filter.op,
          value: filter.value,
          unit: filter.unit || "",
          label: filter.label || filterLabel(filter),
          system: !!filter.system,
          ambiguous: !!filter.ambiguous,
        })),
        semanticGroups: (parsed.semanticGroups || []).map((group) => ({
          id: group.id,
          sourceText: group.sourceText,
          entityKey: group.entityKey,
          entityLabel: group.entityLabel,
          explicitDomain: group.explicitDomain,
          needsConfirmation: group.needsConfirmation,
          selectedDomain: selectedSemanticCandidate(group)?.domain || "",
          candidates: group.candidates.map((candidate) => ({
            id: candidate.id,
            domain: candidate.domain,
            confidence: candidate.confidence,
            label: candidate.filter.label,
          })),
        })),
        conditionStats: conditionFilterStats(parsed).map((item) => ({
          label: item.filter.label || filterLabel(item.filter),
          removed: item.removed,
          remaining: item.remaining,
        })),
        assumptions: parsed.assumptions.slice(),
        warnings: parsed.warnings.slice(),
        rowCount: result.rows.length,
        baseCount: result.baseCount,
        sampleRows: result.rows.slice(0, 5).map((row) => ({
          id: row.统一策略ID,
          name: row.策略名称,
          advisor: row.投顾机构,
          risk: row.风险等级,
          reportType: row.研报产品类型,
          returnValue: row[parsed.returnMetric.field],
          maxDrawdown: row.最大回撤,
          currentDrawdown: row.当前回撤,
          hitReason: row._aiHitReason,
        })),
      };
    },
  };

  renderShell();
})();
