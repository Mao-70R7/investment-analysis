window.__AI_STRATEGY_CONFIG__ = Object.assign({
  enabled: true,
  profile: "codex",
  provider: "codex-cli-local-proxy",
  endpoint: "http://127.0.0.1:8787/v1/chat/completions",
  model: "gpt-5.4-mini",
  timeoutMs: 45000,
  mode: "hybrid-parse",
  resultSource: "local-wide-table",
  apiKey: "",
  headers: {},
  rateLimitBackoffMs: 60000,
  responseFormat: true,
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
  }
}, window.__AI_STRATEGY_LOCAL_CONFIG__ || {});
