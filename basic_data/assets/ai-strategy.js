(() => {
  const B = window.BasicData;
  const summary = B.state.summary || {};
  const root = B.byId("aiStrategyPage");
  if (!root) return;

  const allRows = summary.strategies || [];
  const holdingPack = window.__BASIC_HOLDING_SNAPSHOT_PACK__ || null;
  const semanticIndex = window.__AI_STRATEGY_SEMANTIC_INDEX__ || null;
  const fundDetailPack = window.__BASIC_DATA__?.fundDetailPack || null;
  const modelConfigStorageKey = "aiStrategyModelConfigV3";
  const aiConfigFileDefault = Object.assign({}, window.__AI_STRATEGY_CONFIG__ || {});
  const aiConfig = Object.assign({}, aiConfigFileDefault, readStoredModelConfig());
  window.__AI_STRATEGY_CONFIG__ = aiConfig;
  let modelBackoffUntil = 0;
  const allowedReturnMetrics = new Set(["近一周", "近一月", "近三月", "近6月", "近1年", "今年以来", "累计收益率", "年化收益"]);
  const allowedReportTypes = new Set(["固收+型", "纯债型", "股票型", "多元配置型", "股债混合型", "海外/全球型", "主题/行业型", "现金管理型", "偏股配置型"]);
  const virtualFields = [
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
  const defaultQuery = "找成立一年以上，回撤在15个点以内，收益率在15个点以上，持仓含德国、日本的策略。";
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
    return `模型解析 ${raw(config.model || "未配置模型")} @ ${endpoint}`;
  }

  function parseHeadersJson(text) {
    const value = raw(text).trim();
    if (!value) return {};
    const parsed = JSON.parse(value);
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) throw new Error("headers 必须是 JSON 对象");
    return parsed;
  }

  function applyRuntimeModelConfig(nextConfig, persist = true) {
    const normalized = {
      enabled: nextConfig.enabled !== false,
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
    const text = raw(value).trim();
    const match = text.match(/^(\d{4})-(\d{2})-(\d{2})/);
    if (!match) return null;
    const date = new Date(Number(match[1]), Number(match[2]) - 1, Number(match[3]));
    return Number.isNaN(date.getTime()) ? null : date;
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
    const negativeCue = "(?:不含|不包含|不持有|未持有|未|没有|无|剔除|排除|不要|不想|不能有|不得持有|避免)";
    return new RegExp(`${negativeCue}[^\\s，。；、,.]{0,10}${aliasPattern}`, "i").test(text);
  }

  function hasNegativeCueForEntity(text, entity) {
    return entityAliases(entity, entity?.label || "").some((alias) => hasNegativeCueForAlias(text, alias));
  }

  function explicitlyRequestsOverseas(query) {
    const text = raw(query);
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
    const weight = matches.reduce((total, item) => total + (num(item.weight) || 0), 0);
    const labels = matches
      .map((item) => {
        const source = [item.sourceField, item.sourceValue].filter(Boolean).join(":");
        const rule = item.ruleId ? `规则:${item.ruleId}` : "";
        return `${item.entityName || entity?.label || key}${item.weight ? ` ${formatPct(item.weight)}` : ""}${item.evidence ? `：${item.evidence}` : ""}${source ? `；来源:${source}` : ""}${rule ? `；${rule}` : ""}`;
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
      weight: weightedMatches.reduce((total, item) => total + (num(item.weight) || 0), 0),
      matches,
      labels,
    };
  }

  function holdingEntityEvidence(row, term) {
    const entity = resolveSemanticEntity(term);
    const aliases = [...new Set([...entityAliases(entity, term), ...(entity?.evidenceAliases || [])].map(raw).filter(Boolean))];
    const strategyId = raw(row.统一策略ID);
    const indexed = strategyEntityEvidence(row, entity || term);
    if (isIndexedStandardEntity(entity)) {
      return {
        known: indexed.known,
        hasEntity: indexed.hasEntity,
        entity: indexed.entity || entity,
        term: raw(term),
        aliases,
        date: indexed.date || raw(row.最新持仓日),
        weight: indexed.weight,
        matches: indexed.matches || [],
        labels: indexed.labels || [],
        strict: true,
      };
    }
    const structured = structuredHoldingEntityEvidence(row, entity, aliases);
    const holdings = semanticHoldingsByStrategy().get(strategyId) || [];
    const matches = holdings.filter((holding) => (holding.weight || 0) > 0.0001 && entityMatchesEvidence(entity, holdingText(holding), aliases));
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
      weight: num(evidence.weight) || 0,
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
      weight: matched.reduce((total, evidence) => total + (num(evidence.weight) || 0), 0),
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
    const today = new Date();
    const latest = latestDataDate();
    if (latest && latest > today) return latest;
    return today;
  }

  function yearsAgo(date, years) {
    const out = new Date(date.getTime());
    out.setFullYear(out.getFullYear() - Math.trunc(years));
    if (!Number.isInteger(years)) out.setDate(out.getDate() - Math.round((years % 1) * 365.25));
    return out;
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
      "策略名称", "投顾机构", "渠道", "业务分类", "研报产品类型", "风险等级", "成立日期", "运作状态", "数据完整性",
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

  function filterFieldNames() {
    const names = [];
    const push = (field) => {
      if (field && !names.includes(field)) names.push(field);
    };
    virtualFields.forEach((item) => push(item.field));
    actualFieldNames().forEach(push);
    return names;
  }

  function fieldLabel(field) {
    const virtual = virtualFields.find((item) => item.field === field);
    if (virtual) return virtual.label;
    if (field === "searchText") return "搜索文本";
    return field || "未选择字段";
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
        说明: field === "searchText" ? "策略名称、机构、渠道、分类等搜索文本合并字段" : "当前策略宽表真实字段",
      };
    });
  }

  function virtualFieldDictionaryRows() {
    const semanticStrategyCount = semanticHoldingsByStrategy().size;
    const semanticFundCount = new Set(flatSemanticHoldings().map(fundKeyOf).filter(Boolean)).size;
    return [
      { 字段: "标准实体索引", 命中策略数: zhCount(semanticStrategyCount), 命中基金数: zhCount(semanticFundCount), 说明: "用于资产、地域、指数、行业主题、产品形态等可回溯实体硬筛；基金公司等动态字段仅作查询辅助" },
      { 字段: "完整可比数据", 命中策略数: zhCount(allRows.filter(isCompleteStrategy).length), 命中基金数: "-", 说明: "数据完整性=完整，默认勾选" },
      { 字段: "机构/渠道/策略名称命中", 命中策略数: zhCount(allRows.filter(isGf).length), 命中基金数: "-", 说明: "投顾机构、渠道、策略名称、搜索文本的通用关键词匹配；不只限广发，基金公司仓位走“最新持仓明细”实体筛选" },
      { 字段: "海外资产判断/权重", 命中策略数: zhCount(allRows.filter((row) => overseasEvidence(row).hasOverseas).length), 命中基金数: zhCount(new Set(flatSemanticHoldings().filter((holding) => /QDII|海外|全球|港股|美股|德国|日本|DAX|日经|Nikkei|TOPIX/i.test(holdingText(holding))).map(fundKeyOf).filter(Boolean)).size), 说明: "最新持仓快照和语义持仓索引共同核验" },
      { 字段: "黄金判断", 命中策略数: zhCount(allRows.filter((row) => goldEvidence(row).hasGold).length), 命中基金数: zhCount(new Set(flatSemanticHoldings().filter((holding) => /黄金|贵金属|商品黄金/i.test(holdingText(holding))).map(fundKeyOf).filter(Boolean)).size), 说明: "最新持仓快照和基金名称/分类证据共同核验" },
    ];
  }

  function semanticEntityStats(entity) {
    const resolved = resolveSemanticEntity(entity.key || entity.label) || entity;
    const aliases = [...new Set([...entityAliases(resolved, entity.label), ...(resolved?.evidenceAliases || [])].map(raw).filter(Boolean))];
    const strategyIds = new Set();
    const fundKeys = new Set();
    strategyEntitiesByStrategy().forEach((rows, strategyId) => {
      const matched = rows.some((item) => item.entityKey === resolved?.key);
      if (matched) strategyIds.add(strategyId);
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
    if (!strategyIds.size && !fundKeys.size && !isIndexedStandardEntity(resolved)) {
      flatSemanticHoldings().forEach((holding) => {
        if (!entityMatchesEvidence(resolved, holdingText(holding), aliases)) return;
        if (holding.strategyId) strategyIds.add(holding.strategyId);
        const fundKey = fundKeyOf(holding);
        if (fundKey) fundKeys.add(fundKey);
      });
    }
    return {
      实体: resolved?.label || entity.label || entity.key,
      类型: resolved?.type || entity.type || "持仓实体",
      命中策略数: zhCount(strategyIds.size),
      命中基金数: zhCount(fundKeys.size),
      别名: aliases.slice(0, 8).join("、"),
    };
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
    const semanticRows = semanticEntityCatalog.map(semanticEntityStats)
      .sort((a, b) => Number(raw(b.命中策略数).replace(/,/g, "")) - Number(raw(a.命中策略数).replace(/,/g, "")) || a.实体.localeCompare(b.实体, "zh-CN"));
    const dimensionBlocks = [
      ["基金公司", fundDimensionDictionaryRows("fund_detail_pack/ai_semantic_index.基金公司", (holding) => holding.fundCompany), false],
      ["基金资产类型", fundDimensionDictionaryRows("ai_semantic_index.资产类型", (holding) => holding.assetType), true],
      ["基金二级分类", fundDimensionDictionaryRows("ai_semantic_index.二级分类", (holding) => holding.secondaryCategory), true],
      ["基金分组", fundDimensionDictionaryRows("ai_semantic_index.分组", (holding) => holding.group), false],
      ["基金同类分组/主题", fundDimensionDictionaryRows("ai_semantic_index.基金同类分组", (holding) => holding.peerGroup), false],
      ["基金画像行业主题", fundDimensionDictionaryRows("fund_detail_pack.行业主题", (holding) => holding.profileTheme), false],
      ["基金画像大类资产", fundDimensionDictionaryRows("fund_detail_pack.研报大类资产", (holding) => holding.profileAsset), false],
      ["基金画像A股行业", fundDimensionDictionaryRows("fund_detail_pack.研报A股行业", (holding) => holding.profileIndustry), false],
      ["基金实体集", fundDimensionDictionaryRows("ai_semantic_index.基金名称", (holding) => holding.fundName || holding.fundCode), false],
    ];
    return `<details class="panel ai-help-panel">
      <summary class="ai-help-summary">
        <div><h2>Ai说明：可识别维度与实体字典</h2><p class="desc">这里不是静态文案；每次打开页面都会基于当前 basic_summary、holding_snapshot_pack 和 ai_semantic_index 重新聚合命中数量。</p></div>
        <div class="title-pills"><span class="pill">策略 ${zhCount(allRows.length)}</span><span class="pill">语义持仓 ${zhCount(flatSemanticHoldings().length)}</span></div>
      </summary>
      <div class="ai-help-body">
        <div class="ai-help-grid">
          <section>
            <h3>派生筛选字段</h3>
            ${aiMiniTable(["字段", "命中策略数", "命中基金数", "说明"], virtualFieldsRows)}
          </section>
          <section>
            <h3>持仓语义实体</h3>
            ${aiMiniTable(["实体", "类型", "命中策略数", "命中基金数", "别名"], semanticRows)}
          </section>
        </div>
        <details class="ai-help-detail">
          <summary>策略宽表字段 <span>${zhCount(strategyFields.length)} 项</span></summary>
          ${aiMiniTable(["字段", "命中策略数", "去重值数", "说明"], strategyFields)}
        </details>
        ${dimensionBlocks.map(([title, rows, open]) => renderFundDimensionBlock(title, rows, open)).join("")}
      </div>
    </details>`;
  }

  function optionHtml(options, selected) {
    return options.map((value) => `<option value="${B.esc(value)}"${value === selected ? " selected" : ""}>${B.esc(fieldLabel(value))}</option>`).join("");
  }

  function operatorOptionHtml(selected) {
    return operatorOptions.map((value) => `<option value="${B.esc(value)}"${value === selected ? " selected" : ""}>${B.esc(operatorLabels[value] || value)}</option>`).join("");
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
      || /(固收|纯债|短债|股票|多元|股债).{0,6}(策略|产品|组合)/.test(text)
      || /(策略|产品|组合).{0,6}(固收|纯债|短债|股票|多元|股债)/.test(text);
  }

  function addProductTypeContextFilters(parsed, query) {
    if (!hasProductTypeContext(query)) return;
    const text = raw(query);
    const addReportType = (value) => addGenericFilter(parsed, {
      field: "研报产品类型",
      op: "=",
      value,
      label: `研报产品类型 = ${value}`,
    });
    if (/固收(?!\+|加)/.test(text)) {
      addReportType("固收+型");
      addReportType("纯债型");
    }
    if (/固收\+|固收加/.test(text)) addReportType("固收+型");
    if (/纯债|短债/.test(text)) addReportType("纯债型");
    if (/股债/.test(text)) addReportType("股债混合型");
    if (/多元/.test(text)) addReportType("多元配置型");
    if (/股票|偏股/.test(text)) addReportType("股票型");
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
      const itemStatic = !raw(item.key).includes(":");
      const existingStatic = !raw(existing.key).includes(":");
      const itemExact = normalizeSearchText(item.label) === normalizeSearchText(item.term);
      const existingExact = normalizeSearchText(existing.label) === normalizeSearchText(existing.term);
      if ((itemStatic && !existingStatic) || (itemExact && !existingExact)) byTerm.set(termKey, item);
    });
    return Array.from(byTerm.values());
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
      || thresholds.holdingEntityWeightMin !== undefined
      || (thresholds.genericFilters || []).length
      || thresholds.reportType
      || (parsed.filters || []).some((filter) => !filter.system);
  }

  function buildFilterList(parsed) {
    const thresholds = parsed.thresholds || {};
    const filters = [];
    if (parsed.completeOnly) {
      filters.push({ field: "数据完整性", op: "=", value: "完整", label: "仅完整可比数据", system: true });
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
      completeOnly: state.completeOnly,
      returnMetric: detectReturnMetric(query),
      thresholds: {},
    };

    const ageMatch = query.match(/成立.{0,6}?([0-9]+(?:\.[0-9]+)?|[一二两三四五六七八九十半]+)\s*年\s*(?:以上|以?上|满|超过|大于|不低于)?/);
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
      if (entity.key === "overseas" && (parsed.thresholds.includeOverseas || parsed.thresholds.excludeQdii)) return false;
      if (isProductTypeEntityInProductContext(entity, query)) return false;
      return true;
    });
    if (semanticDetections.length) {
      parsed.thresholds.holdingEntities = semanticDetections;
      const holdingWeightMin = findThreshold(query, ["基金公司仓位", "公司仓位", "持仓占比", "持仓比例", "仓位", "占比", "比例", "权重", "配置比例", "配置"], minDirectionWords);
      if (holdingWeightMin !== null) {
        parsed.thresholds.holdingEntityWeightMin = holdingWeightMin;
        const relation = holdingEntityRelationMode(query, semanticDetections.filter((entity) => !entity.negative));
        const weightMode = holdingEntityWeightMode(query);
        parsed.assumptions.push(`识别到持仓实体权重阈值 ${holdingWeightMin}%。${weightMode === "each" ? "按每个实体分别达到阈值筛选。" : "默认按这些实体的最新持仓权重合计筛选，不要求每个实体单独出现；如需分别达标，请使用“分别/各自/都超过”。"}${relation === "any" ? "实体关系为任一命中。" : (weightMode === "each" ? "实体关系为同时命中。" : "权重条件按合并口径执行。")}`);
      }
      if (holdingCategoriesByStrategy().size || semanticHoldingsByStrategy().size) {
        semanticDetections.forEach((entity) => {
          const strictNote = isIndexedStandardEntity(resolveSemanticEntity(entity.key || entity.term))
            ? "该条件按标准实体索引硬筛，实体来源、证据字段和规则版本可在命中说明或详情页回溯。"
            : "该条件按客观持仓字段检索，不写入基金/策略标准实体标签。";
          parsed.assumptions.push(`“${entity.term}”识别为${entity.type || "持仓"}实体「${entity.label}」；${strictNote}${entity.note ? ` ${entity.note}` : ""}`);
        });
      } else {
        parsed.warnings.push("未加载 AI 标准实体索引，无法执行基金/策略实体硬筛。");
      }
    }

    if (/只看|仅看|限定|筛选/.test(query) && /广发/.test(query)) {
      parsed.thresholds.onlyGf = true;
      parsed.filters.push({ field: "投顾机构", op: "contains", value: "广发", label: "仅看广发" });
    }

    if (/非广发|排除广发|不看广发/.test(query)) {
      parsed.thresholds.excludeGf = true;
      parsed.filters.push({ field: "投顾机构", op: "not contains", value: "广发", label: "排除广发" });
    }

    if (!parsed.thresholds.onlyGf && !parsed.thresholds.excludeGf && /广发/.test(query)) {
      parsed.thresholds.gfTerm = "广发";
      parsed.assumptions.push("“广发基金的投顾产品”可能对应投顾机构、渠道或策略名称。默认按任一相关字段包含“广发”筛选；可在下方条件表改为单独使用“投顾机构”或“渠道”。");
    }

    const entityTerm = extractEntityTerm(query);
    if (entityTerm && !parsed.thresholds.gfTerm && !parsed.thresholds.onlyGf && !parsed.thresholds.excludeGf && !(parsed.thresholds.holdingEntities || []).length) {
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
    const pool = poolMap.find(([key]) => query.includes(key) && !hasNegativeCueForAlias(query, key));
    if (pool) {
      parsed.thresholds.reportType = pool[1];
      parsed.filters.push({ field: "研报产品类型", op: "=", value: pool[1], label: pool[1] });
    }

    normalizeHoldingEntityConflicts(parsed);
    parsed.filters = buildFilterList(parsed);
    if (!hasBusinessFilter(parsed)) parsed.warnings.push("没有识别到可执行筛选条件，请补充收益、回撤、成立时间、持仓或产品类型条件。");
    return parsed;
  }

  function shouldUseModelParser(allowModel = true) {
    if (!allowModel) return false;
    const mode = raw(aiConfig.mode || "hybrid-parse").toLowerCase();
    return aiConfig.enabled !== false && !!modelChatEndpoint(aiConfig) && mode !== "local-only" && mode !== "off" && Date.now() >= modelBackoffUntil;
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
    const matched = filterFieldNames().find((field) => fieldLabel(field).replace(/\s+/g, "") === compact || field.replace(/\s+/g, "") === compact);
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
      if (!["is empty", "is not empty"].includes(op) && (value === undefined || value === null || raw(value).trim() === "")) return null;
      return {
        field,
        op,
        value: raw(value),
        label: filterLabel({ field, op, value }),
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

  async function requestModelIntent(queryText) {
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
      messages: [
        {
          role: "system",
          content: "你只做中文投顾策略筛选意图解析。只输出 JSON 对象，不要输出策略名单、解释或 Markdown。模型只能输出筛选条件，不能输出候选结果，也不能发明基金/策略实体标签。常用字段：returnMetric,minReturn,drawdownField,maxDrawdown,minAgeYears,includeOverseas,excludeGold,excludeQdii,onlyGf,excludeGf,gfTerm,entityTerm,holdingEntities,holdingEntityWeightMin,reportType,filters。returnMetric 只能是近一周、近一月、近三月、近6月、近1年、今年以来、累计收益率、年化收益；注意年化投顾费率、年化波动、年化换手不是收益口径，应放入 filters。drawdownField 只能是最大回撤或当前回撤；用户只说回撤时按最大回撤。KYC 风险偏好用 filters 输出字段“风险等级序号”：保守/低风险/R2以内 => <=2，稳健/均衡 => <=3，进取/高风险 => >=4。费用、波动率、夏普、权益/债券/货币/QDII/指数基金权重、调仓频率、年化换手率等量化诉求放入 filters。产品/客群偏好可落到研报产品类型、业务分类、市场地域、主动被动、运作状态、基础数据等级等字段；同一单值字段若有多个候选分类，应使用 contains_any 或 in 表达“任一满足”，不要输出多个互斥 AND 条件。holdingEntities 只能引用下方提供的标准实体或查询别名，每项为 {term, key, negative}；key 使用标准实体 key，term 保留用户词，negative 表示不含。若用户词不能唯一映射到标准实体，不要强行输出 holdingEntities，改用 entityTerm 或 filters。基金公司仓位、持仓占比、配置比例等阈值输出 holdingEntityWeightMin。用户说“同时/且/并且/和”默认多个实体都要命中；用户说“或/或者/任一/任意”才是任一命中。多个实体带权重阈值默认按合计仓位判断，只有用户说分别/各自/都超过才按每个实体分别达标。用户说不持有/未持有/不含/没有/无黄金时，excludeGold=true，且不要再输出正向黄金 holdingEntities。只有用户明确说海外/全球/QDII/海外资产/全球资产时 includeOverseas=true；港股、美股这类具体资产输出 holdingEntities，不要同时输出海外宽口径，除非用户也明确说海外资产。不要因为纳指、纳斯达克100或标普500自动增加海外宽口径。gfTerm 仅用于“广发基金/广发投顾/广发产品”等可能匹配机构、渠道或策略名称的歧义条件；其他投顾机构、渠道或策略名称关键词用 entityTerm。基金公司、基金名称、行业、主题、国家地区、指数应优先映射到已知标准实体；无法映射时不要输出标准实体。filters 是数组，每项为 {field,op,value}，字段必须来自用户消息给出的可用字段。所有百分比或“几个点”数值统一输出百分数数值，例如 5个点输出 5，不要输出 0.05。未知字段输出 null 或 false。"
        },
        {
          role: "user",
          content: `用户输入：${queryText}\n可用字段：${filterFieldNames().map(fieldLabel).join("、")}\n可用标准实体/查询别名：${semanticEntityCatalog.map((item) => `${item.key}:${item.label}=${(item.aliases || item.queryAliases || []).join("/")}`).join("；")}\n请输出 JSON，例如 {"returnMetric":"近6月","minReturn":10,"drawdownField":"最大回撤","maxDrawdown":5,"minAgeYears":null,"includeOverseas":true,"excludeGold":false,"excludeQdii":false,"onlyGf":false,"excludeGf":false,"gfTerm":null,"entityTerm":null,"holdingEntities":[{"term":"纳指","key":"nasdaq100","negative":false}],"holdingEntityWeightMin":null,"reportType":null,"filters":[]}`
        }
      ],
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

  function applyModelIntent(parsed, intent) {
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
    const gfTerm = raw(firstDefined(intent, ["gfTerm", "gf_term", "广发关键词"])).trim();
    if (gfTerm && !parsed.thresholds.onlyGf && !parsed.thresholds.excludeGf && !parsed.thresholds.gfTerm) parsed.thresholds.gfTerm = gfTerm;
    const entityTerm = raw(firstDefined(intent, ["entityTerm", "entity_term", "机构关键词", "关键词"])).trim();
    if (entityTerm && !parsed.thresholds.gfTerm && !parsed.thresholds.onlyGf && !parsed.thresholds.excludeGf && !parsed.thresholds.entityTerm && !(parsed.thresholds.holdingEntities || []).length) parsed.thresholds.entityTerm = entityTerm;
    const holdingEntities = [];
    const holdingEntitySource = firstDefined(intent, ["holdingEntities", "holding_entities", "持仓实体"]);
    if (Array.isArray(holdingEntitySource)) {
      holdingEntitySource.forEach((item) => {
        const term = raw(firstDefined(item, ["term", "entity", "value", "关键词", "实体"])).trim();
        const entityKey = raw(firstDefined(item, ["key", "entityKey", "semanticEntity", "实体Key"])).trim();
        if (!term && !entityKey) return;
        const resolved = resolveSemanticEntity(entityKey || term);
        holdingEntities.push({
          key: resolved?.key || "",
          label: resolved?.label || term || entityKey,
          term: term || resolved?.label || entityKey,
          negative: modelBool(firstDefined(item, ["negative", "exclude", "不含"])) === true || (resolved ? hasNegativeCueForEntity(parsed.query, resolved) : false),
          note: resolved?.note || "",
        });
      });
    }
    const holdingEntityTerm = raw(firstDefined(intent, ["holdingEntityTerm", "holdingEntity", "持仓关键词", "持仓实体词"])).trim();
    if (holdingEntityTerm) {
      const resolved = resolveSemanticEntity(holdingEntityTerm);
      holdingEntities.push({
        key: resolved?.key || "",
        label: resolved?.label || holdingEntityTerm,
        term: holdingEntityTerm,
        negative: modelBool(firstDefined(intent, ["excludeHoldingEntity", "不含持仓实体"])) === true || (resolved ? hasNegativeCueForEntity(parsed.query, resolved) : false),
        note: resolved?.note || "",
      });
    }
    if (holdingEntities.length) {
      const existing = new Set((parsed.thresholds.holdingEntities || []).map((item) => `${item.key || item.term}:${item.negative ? "not" : "in"}`));
      parsed.thresholds.holdingEntities = [...(parsed.thresholds.holdingEntities || [])];
      holdingEntities.forEach((item) => {
        const key = `${item.key || item.term}:${item.negative ? "not" : "in"}`;
        if (!existing.has(key)) parsed.thresholds.holdingEntities.push(item);
      });
    }
    const holdingEntityWeightMin = modelNumber(firstDefined(intent, ["holdingEntityWeightMin", "holding_entity_weight_min", "持仓实体权重下限", "持仓实体仓位下限"]));
    if (holdingEntityWeightMin !== null && (parsed.thresholds.holdingEntities || []).some((item) => !item.negative)) {
      parsed.thresholds.holdingEntityWeightMin = holdingEntityWeightMin;
    }
    const reportType = normalizeModelReportType(firstDefined(intent, ["reportType", "report_type", "产品类型"]));
    if (reportType && !parsed.thresholds.reportType) parsed.thresholds.reportType = reportType;
    const extraFilters = normalizeModelFilters(intent);
    normalizeHoldingEntityConflicts(parsed);
    parsed.filters = buildFilterList(parsed);
    extraFilters.forEach((filter) => {
      const exists = parsed.filters.some((item) => item.field === filter.field && item.op === filter.op && raw(item.value) === raw(filter.value));
      if (!exists) parsed.filters.push(filter);
    });
    parsed.filters = normalizeLogicalFilters(parsed.filters, parsed);
    parsed.model = { status: "used", provider: raw(aiConfig.provider || "local"), model: raw(aiConfig.model || "codex") };
    parsed.assumptions.push("已调用本机模型辅助解析；模型只输出筛选条件，候选结果仍由本地策略宽表和持仓快照核验。");
    return parsed;
  }

  async function parseQueryHybrid(queryText, localParsed, allowModel = true) {
    localParsed.model = { status: "local-rule" };
    if (!allowModel) return localParsed;
    if (modelBackoffUntil > Date.now()) {
      localParsed.model = { status: "fallback", error: "model-rate-limited" };
      localParsed.warnings.push("模型接口正在限流，已临时使用本地规则解析。");
      localParsed.filters = buildFilterList(localParsed);
      return localParsed;
    }
    if (!shouldUseModelParser(true)) return localParsed;
    try {
      const intent = await requestModelIntent(queryText);
      return applyModelIntent(localParsed, intent);
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
      localParsed.model = { status: "fallback", error: message };
      localParsed.warnings.push(`模型解析不可用，已使用本地规则解析：${message}`);
      localParsed.filters = buildFilterList(localParsed);
      return localParsed;
    }
  }

  function isCompleteStrategy(row) {
    return row?.数据完整性 === "完整" && row?.风险等级 !== "D0 持仓缺失" && row?.研报产品类型 !== "持仓缺失/不入池";
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
    return (parsed.filters || []).filter((filter) => !(parsed.completeOnly && filter.system && filter.field === "数据完整性" && filter.value === "完整"));
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
      const left = num(value);
      const right = num(filter.value);
      if (left === null || right === null) return 0;
      const margin = Math.max(0, left - right);
      const factor = filter.field === parsed?.returnMetric?.field ? 1.2 : 0.35;
      return 4 + Math.min(40, margin * factor);
    }
    if (["<=", "<"].includes(op)) {
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
    if (parsed.completeOnly) base = base.filter(isCompleteStrategy);
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
    if (field === "数据完整性") return B.statusBadge ? B.statusBadge(row[field]) : B.esc(row[field] || "");
    return B.fmt(row[field]);
  }

  function renderChips(parsed) {
    return `<div class="ai-chip-row">${parsed.filters.map((filter) => `<span class="ai-chip">${B.esc(filter.label || filterLabel(filter))}</span>`).join("")}</div>`;
  }

  function conditionRowHtml(filter = {}) {
    const field = filter.field || "__any_text";
    const op = filter.op || "contains";
    const value = filter.value === undefined || filter.value === null ? "" : raw(filter.value);
    const isSystem = !!filter.system;
    return `
      <tr class="ai-condition-row${isSystem ? " is-system" : ""}">
        <td>
          <select class="control ai-filter-field"${isSystem ? " disabled" : ""}>${optionHtml(filterFieldNames(), field)}</select>
          ${filter.ambiguous ? `<div class="small">字段有歧义，默认合并匹配。</div>` : ""}
        </td>
        <td><select class="control ai-filter-op"${isSystem ? " disabled" : ""}>${operatorOptionHtml(op)}</select></td>
        <td><input class="control ai-filter-value" value="${B.esc(value)}"${isSystem ? " disabled" : ""}></td>
        <td><button class="ai-remove-filter" type="button"${isSystem ? " disabled" : ""}>删除</button></td>
      </tr>
    `;
  }

  function renderFilterEditor(parsed) {
    const editableCount = (parsed.filters || []).filter((filter) => !filter.system).length;
    return `
      <div class="ai-filter-editor">
        <div class="ai-editor-head">
          <div>
            <strong>可微调筛选条件</strong>
            <span>可选字段 ${filterFieldNames().length.toLocaleString("zh-CN")} 个；修改后点击重新筛选。</span>
          </div>
          <div class="ai-editor-actions">
            <button id="aiAddFilter" type="button">新增条件</button>
            <button id="aiApplyFilters" type="button">按调整后条件筛选</button>
          </div>
        </div>
        <div class="table-wrap ai-condition-wrap">
          <table class="ai-condition-table">
            <thead><tr><th>字段</th><th>关系</th><th>值</th><th>操作</th></tr></thead>
            <tbody id="aiConditionBody">
              ${(parsed.filters || []).map((filter) => conditionRowHtml(filter)).join("") || conditionRowHtml({ field: "__any_text", op: "contains", value: "" })}
            </tbody>
          </table>
        </div>
        <p class="desc">固定的“仅完整可比数据”由上方勾选框控制。字段下拉来自当前策略宽表，另包含海外/黄金持仓核验等派生字段。</p>
        ${editableCount ? "" : `<p class="desc">当前没有用户筛选条件，可新增字段条件后执行。</p>`}
      </div>
    `;
  }

  function readEditorFilters() {
    return Array.from(root.querySelectorAll(".ai-condition-row")).map((row) => {
      const field = row.querySelector(".ai-filter-field")?.value || "";
      const op = row.querySelector(".ai-filter-op")?.value || "contains";
      const value = row.querySelector(".ai-filter-value")?.value || "";
      const system = row.classList.contains("is-system");
      return {
        field,
        op,
        value,
        system,
        label: filterLabel({ field, op, value }),
      };
    }).filter((filter) => filter.field && (["is empty", "is not empty"].includes(filter.op) || raw(filter.value).trim() !== "" || filter.system));
  }

  function applyEditedFilters() {
    const parsed = state.parsed;
    if (!parsed) return;
    parsed.filters = readEditorFilters();
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
        body.insertAdjacentHTML("beforeend", conditionRowHtml({ field: "__any_text", op: "contains", value: "" }));
        const lastRemove = body.querySelector(".ai-condition-row:last-child .ai-remove-filter");
        if (lastRemove) {
          lastRemove.addEventListener("click", () => {
            lastRemove.closest(".ai-condition-row")?.remove();
          });
        }
      });
    }
    if (applyButton) applyButton.addEventListener("click", applyEditedFilters);
    root.querySelectorAll(".ai-remove-filter").forEach((button) => {
      button.addEventListener("click", () => {
        button.closest(".ai-condition-row")?.remove();
      });
    });
  }

  function renderKpis(parsed, result) {
    const total = allRows.length;
    const complete = allRows.filter(isCompleteStrategy).length;
    const missingRows = result.missing?.rowIds?.size || 0;
    const latest = latestDataDate();
    const holdingEvidenceCount = holdingEvidenceByStrategy().size;
    const modelStatus = parsed.model?.status === "used"
      ? `模型 ${parsed.model.model || "codex"} 已辅助解析`
      : parsed.model?.status === "fallback"
        ? "模型不可用，已回退本地规则"
        : "本地规则解析";
    return `
      <div class="ai-kpi-grid">
        <section class="ai-kpi"><span>候选结果</span><strong>${result.rows.length.toLocaleString("zh-CN")}</strong><small>按当前条件命中</small></section>
        <section class="ai-kpi"><span>参与样本</span><strong>${result.baseCount.toLocaleString("zh-CN")}</strong><small>全量 ${total.toLocaleString("zh-CN")}，完整 ${complete.toLocaleString("zh-CN")}</small></section>
        <section class="ai-kpi"><span>收益口径</span><strong>${B.esc(parsed.returnMetric.field)}</strong><small>${parsed.returnMetric.explicit ? "用户指定或明确命中" : "系统默认"}；${B.esc(modelStatus)}</small></section>
        <section class="ai-kpi ${missingRows ? "is-warn" : "is-ok"}"><span>数据核验</span><strong>${missingRows ? `字段缺失 ${missingRows} 条` : "通过"}</strong><small>${missingRows ? "字段缺失或待核验的策略未进入严格结果；" : ""}业绩 ${dateText(latest)}；持仓核验 ${holdingEvidenceCount.toLocaleString("zh-CN")} 策</small></section>
      </div>
    `;
  }

  function renderDsl(parsed) {
    const payload = {
      asOfDate: dateText(parsed.asOf),
      completeOnly: parsed.completeOnly,
      modelParse: parsed.model || { status: "local-rule" },
      filters: parsed.filters.map((item) => ({ field: item.field, op: item.op, value: item.value, unit: item.unit || "" })),
      sort: [{ field: parsed.returnMetric.field, direction: "desc" }],
      assumptions: parsed.assumptions,
    };
    return `<pre class="ai-json">${B.esc(JSON.stringify(payload, null, 2))}</pre>`;
  }

  function queryChecks(parsed) {
    const checks = [];
    if (parsed.completeOnly) checks.push({ key: "complete", label: "仅完整可比数据", test: isCompleteStrategy });
    activeFilters(parsed)
      .filter((filter) => !(filter.system && filter.field === "数据完整性"))
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
    let running = allRows.slice();
    const steps = checks.map((check) => {
      const independentCount = allRows.filter(check.test).length;
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
    return `./insights.html?tab=compare&compare=${ids}`;
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
        renderResults(state.parsed, state.lastResult || { rows: state.rows, missing: { generic: 0 }, baseCount: allRows.filter(isCompleteStrategy).length });
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
        renderResults(state.parsed, state.lastResult || { rows: state.rows, missing: { generic: 0 }, baseCount: allRows.filter(isCompleteStrategy).length });
      });
    }
    const clearButton = B.byId("aiClearCompare");
    if (clearButton) {
      clearButton.addEventListener("click", () => {
        state.selectedCompareIds = [];
        renderResults(state.parsed, state.lastResult || { rows: state.rows, missing: { generic: 0 }, baseCount: allRows.filter(isCompleteStrategy).length });
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
    return allRows
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
    if (field === "投顾机构") classes.push("ai-sticky-advisor");
    if (field === "渠道") classes.push("ai-sticky-channel");
    if (["累计收益率", "年化收益", "近6月", "近1年", "当前回撤", "最大回撤", "持仓实体权重"].includes(field)) classes.push("narrow");
    if (["业务分类", "研报产品类型", "风险等级"].includes(field)) classes.push("wide");
    return classes.join(" ");
  }

  function renderRowsTable(rows, headers, emptyText, options = {}) {
    const withSelection = !!options.withSelection;
    if (withSelection) {
      const headerHtml = `${withSelection ? '<th class="ai-select-head ai-sticky-select">选择</th>' : ""}${headers.map((field) => `<th class="${candidateCellClass(field, true)}">${B.label(field)}</th>`).join("")}`;
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
    const sourceRows = (rows || []).slice(0, 500).map((row) => ({
      row,
      id: raw(row.统一策略ID || row.策略代码 || row.策略名称),
      x: num(row[xField]),
      y: num(row[yField]),
      group: scatterGroup(row),
    })).filter((item) => item.id && item.x !== null && item.y !== null);
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
            <span>展示 ${sourceRows.length.toLocaleString("zh-CN")} 条可绘制候选；点击点查看策略说明。</span>
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

  function dynamicHeaders(parsed) {
    const headers = [];
    const push = (field) => {
      if (field && !headers.includes(field)) headers.push(field);
    };
    ["命中说明", "策略名称", "投顾机构", "渠道", "业务分类", "研报产品类型", "风险等级"].forEach(push);
    (parsed?.filters || []).forEach((filter) => {
      if (filter.field === "__holding_entity") {
        push("持仓实体权重");
        return;
      }
      if (filter.field === "__gf_any" || filter.field === "__source_any") {
        push("投顾机构");
        push("渠道");
        return;
      }
      if (filter.field === "__any_text") return;
      if (filter.field === "风险等级序号") {
        push("风险等级");
        return;
      }
      if (filter.field === "海外资产判断" || filter.field === "海外资产权重" || filter.field === "海外资产分类") {
        push("海外资产判断");
        push("海外资产权重");
        push("海外资产分类");
        return;
      }
      push(filter.field);
    });
    ["累计收益率", "年化收益", "近6月", "近1年", "当前回撤", "最新业绩日期", "最新持仓日"].forEach(push);
    return headers;
  }

  function renderTable(rows) {
    const headers = dynamicHeaders(state.parsed);
    const visible = rows.slice(0, state.limit);
    return `
      ${renderCompareToolbar(rows)}
      ${renderRowsTable(visible, headers, "当前条件下暂无策略命中", { withSelection: true })}
      ${rows.length ? `<div id="aiScatterMount">${renderCandidateScatter(rows, state.parsed)}</div>` : ""}
      ${rows.length > state.limit ? `<p class="desc">当前仅展示前 ${state.limit} 条，建议继续增加机构、产品类型或收益区间条件缩小范围。</p>` : ""}
    `;
  }

  async function runSearch(options = {}) {
    const allowModel = options?.allowModel !== false;
    const seq = ++state.searchSeq;
    state.query = B.byId("aiQuery").value;
    state.completeOnly = B.byId("aiCompleteOnly").checked;
    let parsed = parseQuery(state.query);
    if (shouldUseModelParser(allowModel)) {
      B.byId("aiResult").innerHTML = `
        <section class="panel">
          <div class="panel-head"><div><h2>解析中</h2><p class="desc">正在调用 ${B.esc(aiConfig.model || "模型")} 解析筛选条件；若接口不可用会自动回退本地规则。</p></div></div>
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
    const assumptionHtml = [...parsed.assumptions, ...parsed.warnings].length
      ? `<div class="ai-notes">${[...parsed.assumptions, ...parsed.warnings].map((item) => `<p>${B.esc(item)}</p>`).join("")}</div>`
      : "";
    B.byId("aiResult").innerHTML = `
      ${renderKpis(parsed, result)}
      <details class="panel ai-parse-panel">
        <summary class="ai-parse-summary">
          <div><h2>解析条件</h2><p class="desc">自然语言已转换为受控筛选条件；字段可手工调整，结果仍只来自本地真实宽表。</p></div>
          <span>查看筛选详细规则</span>
        </summary>
        <div class="ai-parse-body">
          ${renderChips(parsed)}
          ${assumptionHtml}
          ${renderFilterEditor(parsed)}
        </div>
      </details>
      <section class="panel">
        <div class="panel-head"><div><h2>候选策略</h2><p class="desc">按匹配得分排序：优先展示收益更高、回撤更低、持仓实体权重更贴合条件的策略；${B.esc(parsed.returnMetric.field)} 和回撤作为同分次排序。</p></div><span class="pill">${result.rows.length.toLocaleString("zh-CN")} 条</span></div>
        ${renderTable(result.rows)}
      </section>
      ${result.rows.length ? "" : renderNoResultDiagnostics(parsed)}
    `;
    bindFilterEditor();
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

  function renderModelSettings() {
    const config = aiConfig;
    const base = modelBaseUrl(config);
    return `
      <details class="ai-model-panel">
        <summary class="ai-model-summary">
          <div>
            <strong>模型设置与连通性测试</strong>
            <span>默认使用配置文件中的内网模型；需要调试时展开编辑，本页设置可保存到当前浏览器。</span>
          </div>
        </summary>
        <div class="ai-model-body">
          <div class="ai-model-grid">
            <label>启用模型解析
              <select id="aiModelEnabled" class="control">
                <option value="true"${config.enabled !== false ? " selected" : ""}>启用</option>
                <option value="false"${config.enabled === false ? " selected" : ""}>关闭，仅本地规则</option>
              </select>
            </label>
            <label>调用模式
              <select id="aiModelMode" class="control">
                <option value="hybrid-parse"${raw(config.mode || "hybrid-parse") === "hybrid-parse" ? " selected" : ""}>混合解析</option>
                <option value="local-only"${raw(config.mode) === "local-only" ? " selected" : ""}>仅本地规则</option>
                <option value="off"${raw(config.mode) === "off" ? " selected" : ""}>关闭模型</option>
              </select>
            </label>
            <label>Provider
              <input id="aiModelProvider" class="control" value="${B.esc(config.provider || "inner-ds-openai-compatible")}">
            </label>
            <label>模型
              <input id="aiModelName" class="control" value="${B.esc(config.model || "deepseek-v4-flash-inner")}">
            </label>
            <label class="ai-model-wide">Base URL
              <input id="aiModelBaseUrl" class="control" value="${B.esc(base)}" placeholder="/llmapi/v1">
            </label>
            <label class="ai-model-wide">Chat Completions Endpoint
              <input id="aiModelEndpoint" class="control" value="${B.esc(modelChatEndpoint(config))}" placeholder="/llmapi/v1/chat/completions">
            </label>
            <label>API Key
              <input id="aiModelApiKey" class="control" type="password" autocomplete="off" value="${B.esc(config.apiKey || "")}">
            </label>
            <label>超时毫秒
              <input id="aiModelTimeout" class="control" type="number" min="800" max="120000" step="1000" value="${B.esc(config.timeoutMs || 45000)}">
            </label>
            <label>JSON 返回格式
              <select id="aiModelResponseFormat" class="control">
                <option value="true"${config.responseFormat !== false ? " selected" : ""}>要求 JSON Object</option>
                <option value="false"${config.responseFormat === false ? " selected" : ""}>不强制</option>
              </select>
            </label>
            <label>失败退避毫秒
              <input id="aiModelBackoff" class="control" type="number" min="1000" max="600000" step="1000" value="${B.esc(config.rateLimitBackoffMs || 60000)}">
            </label>
            <label class="ai-model-wide">额外 Headers JSON
              <textarea id="aiModelHeaders" class="control ai-model-headers" rows="3" spellcheck="false">${B.esc(modelHeadersText(config))}</textarea>
            </label>
          </div>
          <div class="ai-model-actions">
            <button id="aiModelSave" type="button">保存配置</button>
            <button id="aiModelTest" type="button">测试连通性</button>
            <button id="aiModelReset" type="button">恢复文件默认</button>
            <span id="aiModelTestResult" class="ai-model-test-result">未测试</span>
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
    B.byId("aiModelSave")?.addEventListener("click", () => {
      try {
        const config = applyRuntimeModelConfig(collectModelSettingsForm(), true);
        fillModelSettingsForm(config);
        updateModelStatusPill();
        setModelTestResult("ok", "已保存到当前浏览器，本页立即生效");
      } catch (error) {
        setModelTestResult("bad", `配置无效：${raw(error?.message || error).slice(0, 180)}`);
      }
    });
    B.byId("aiModelTest")?.addEventListener("click", async () => {
      let config = null;
      try {
        config = applyRuntimeModelConfig(collectModelSettingsForm(), false);
        fillModelSettingsForm(config);
        updateModelStatusPill();
        setModelTestResult("running", "正在测试模型接口...");
        const started = performance.now();
        const result = await requestModelConnectivity(config);
        const elapsed = Math.round(performance.now() - started);
        setModelTestResult("ok", `连通成功，${result.model || config.model}，${elapsed}ms`);
      } catch (error) {
        let message = raw(error?.name === "AbortError" ? "模型测试超时" : error?.message || error);
        if (/Failed to fetch|NetworkError|Load failed/i.test(message)) {
          message = isCodexProxyConfig(config || aiConfig)
            ? "本机模型代理不可访问"
            : "模型接口不可访问，请检查内网、CORS 跨域或 API Key";
        }
        setModelTestResult("bad", message.slice(0, 220));
      }
    });
    B.byId("aiModelReset")?.addEventListener("click", () => {
      clearStoredModelConfig();
      Object.keys(aiConfig).forEach((key) => delete aiConfig[key]);
      Object.assign(aiConfig, aiConfigFileDefault);
      applyRuntimeModelConfig(aiConfig, false);
      fillModelSettingsForm(aiConfig);
      updateModelStatusPill();
      setModelTestResult("ok", "已恢复配置文件默认值");
    });
  }

  function renderShell() {
    const modelLabel = modelDisplayLabel(aiConfig);
    root.innerHTML = `
      <section class="panel ai-query-panel">
        <div class="panel-head">
          <div>
            <h2>Ai选策略</h2>
            <p class="desc">输入自然语言条件，系统解析为可核验筛选条件后在当前策略宽表中执行。</p>
          </div>
          <div class="title-pills"><span class="pill">策略宽表 ${allRows.length.toLocaleString("zh-CN")} 条</span><span id="aiModelStatusPill" class="pill">${B.esc(modelLabel)}</span></div>
        </div>
        <textarea id="aiQuery" class="control ai-query-box" rows="3" placeholder="例如：找成立一年以上，回撤在15个点以内，收益率在15个点以上，持仓含德国、日本的策略。">${B.esc(state.query)}</textarea>
        <div class="ai-action-row">
          <label class="checkline"><input id="aiCompleteOnly" type="checkbox" checked> 仅完整可比数据</label>
          <button id="aiRun" type="button">执行筛选</button>
          <button id="aiClear" type="button">清空</button>
        </div>
        ${renderModelSettings()}
      </section>
      ${renderAiExplanation()}
      <div id="aiResult"></div>
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
    runSearch({ allowModel: false });
  }

  window.__AI_STRATEGY_DEBUG__ = {
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
      if (Object.prototype.hasOwnProperty.call(options, "completeOnly")) state.completeOnly = !!options.completeOnly;
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
