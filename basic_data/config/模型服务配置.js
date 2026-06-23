// Ai选策略模型服务配置。
// 当前部署默认走报表服务同源代理，不依赖 Codex，也不让浏览器跨域直连模型服务。
// 不要把页面字段口径、筛选规则、调仓逻辑放到这里。

window.__AI_STRATEGY_LOCAL_CONFIG__ = {
  // 是否启用模型解析。false 表示只使用本地规则解析，不请求模型。
  enabled: true,

  // 报表服务同源 LLM 代理。
  profile: "local",
  provider: "same-origin-llm-proxy",

  // 浏览器只请求当前报表服务，由 start_basic_data_site_linux.sh 启动的代理转发到模型服务。
  baseUrl: "/llmapi/v1",
  endpoint: "/llmapi/v1/chat/completions",

  // 当前使用的快速模型。
  model: "deepseek-v4-flash-inner",

  // 浏览器等待模型返回的超时时间，单位毫秒。前端最大会限制在 120000。
  timeoutMs: 45000,

  // 建议保留 hybrid-parse：先用本地规则解析，再让模型补充复杂自然语言意图。
  mode: "hybrid-parse",

  // 浏览器侧不填写 API Key；服务器侧代理会从 config/ai_strategy_proxy.env 注入上游鉴权。
  apiKey: "",

  // 额外请求头。常见用法：{ "X-API-Key": "xxx" }。没有额外头时保持空对象。
  headers: {},

  // 模型限流或接口失败后的退避时间，单位毫秒。
  rateLimitBackoffMs: 60000,

  // 是否要求 OpenAI 兼容接口返回 JSON 对象。为兼容内网网关默认不强制；前端仍会从模型文本中抽取 JSON。
  responseFormat: false,

  // 前端“模型来源”下拉使用。local 走报表服务同源代理，codex 走本机 Codex 桥接服务。
  modelProfiles: {
    local: {
      label: "本地模型（同源代理）",
      provider: "same-origin-llm-proxy",
      baseUrl: "/llmapi/v1",
      endpoint: "/llmapi/v1/chat/completions",
      model: "deepseek-v4-flash-inner",
      timeoutMs: 45000,
      responseFormat: false,
      apiKey: "",
      headers: {}
    },
    codex: {
      label: "Codex桥接模型",
      provider: "codex-cli-local-proxy",
      baseUrl: "http://127.0.0.1:8787/v1",
      endpoint: "http://127.0.0.1:8787/v1/chat/completions",
      model: "gpt-5.4-mini",
      timeoutMs: 45000,
      responseFormat: true,
      apiKey: "",
      headers: {}
    }
  },

  // 仅 Codex 桥接脚本读取；当前内网 DS 直连不需要启动桥接脚本。
  codexBridge: {
    host: "127.0.0.1",
    port: 8787,
    model: "gpt-5.4-mini",
    serviceTier: "fast",
    reasoningEffort: "low",
    requestTimeoutMs: 60000,
    maxBodyBytes: 2097152,
    maxConcurrent: 1,
    // 留空表示自动使用当前系统的 codex.cmd/codex。
    codexCmd: "",
    // 留空表示使用系统临时目录下的隔离工作目录。
    cwd: ""
  }
};

// 如确需绕过同源代理、让浏览器直连内网 DS，可改成下面这些值；但模型服务必须允许 CORS。
// window.__AI_STRATEGY_LOCAL_CONFIG__ = {
//   enabled: true,
//   provider: "inner-ds-openai-compatible",
//   baseUrl: "http://10.89.189.109:8000/llmapi/v1",
//   endpoint: "http://10.89.189.109:8000/llmapi/v1/chat/completions",
//   model: "deepseek-r1",
//   timeoutMs: 90000,
//   mode: "hybrid-parse",
//   apiKey: "",
//   headers: {},
//   rateLimitBackoffMs: 60000,
//   responseFormat: true
// };
