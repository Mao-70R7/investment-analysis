// Ai选策略模型服务配置。
// 本机默认使用 Codex 本地桥接；迁移到内网服务器时，通常只需要修改“内网 DS 直连配置”中的 endpoint/model/apiKey。
// 不要把页面字段口径、筛选规则、调仓逻辑放到这里。

window.__AI_STRATEGY_LOCAL_CONFIG__ = {
  // 是否启用模型解析。false 表示只使用本地规则解析，不请求模型。
  enabled: true,

  // 当前电脑默认保留 Codex 桥接模式；内网服务器可改成 "inner-ds-openai-compatible"。
  provider: "codex-cli-local-proxy",

  // 当前电脑 Codex 桥接地址。内网 DS 直连时改成内网 OpenAI 兼容 chat/completions 地址。
  endpoint: "http://127.0.0.1:8787/v1/chat/completions",

  // 当前电脑通过 Codex 桥接调用的模型名。内网 DS 可改成本地服务暴露的模型名，例如 "deepseek-r1"、"deepseek-v3"。
  model: "gpt-5.4-mini",

  // 浏览器等待模型返回的超时时间，单位毫秒。内网大模型响应慢时可调大，但前端最大会限制在 120000。
  timeoutMs: 45000,

  // 建议保留 hybrid-parse：先用本地规则解析，再让模型补充复杂自然语言意图。
  mode: "hybrid-parse",

  // 内网接口需要 Bearer Token 时填写；不需要鉴权时保持空字符串。
  // 注意：这个文件会随页面一起部署，若内网多人可访问，请优先使用无密钥内网网关或服务端代理。
  apiKey: "",

  // 额外请求头。常见用法：{ "X-API-Key": "xxx" }。没有额外头时保持空对象。
  headers: {},

  // 模型限流或接口失败后的退避时间，单位毫秒。
  rateLimitBackoffMs: 60000,

  // 是否要求 OpenAI 兼容接口返回 JSON 对象。多数 DS/OpenAI-compatible 服务支持；不支持时设为 false。
  responseFormat: true,

  // 仅 Codex 桥接脚本读取；内网 DS 直连不需要启动桥接脚本。
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

// 内网 DS 直连配置示例：迁移到内网服务器后，把上面的对应字段改成下面这些值。
// window.__AI_STRATEGY_LOCAL_CONFIG__ = {
//   enabled: true,
//   provider: "inner-ds-openai-compatible",
//   endpoint: "http://10.0.0.8:8000/v1/chat/completions",
//   model: "deepseek-r1",
//   timeoutMs: 90000,
//   mode: "hybrid-parse",
//   apiKey: "",
//   headers: {},
//   rateLimitBackoffMs: 60000,
//   responseFormat: true
// };
