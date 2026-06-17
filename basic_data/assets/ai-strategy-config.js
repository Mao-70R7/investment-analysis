window.__AI_STRATEGY_CONFIG__ = Object.assign({
  enabled: true,
  provider: "codex-cli-local-proxy",
  endpoint: "http://127.0.0.1:8787/v1/chat/completions",
  model: "gpt-5.4-mini",
  timeoutMs: 45000,
  mode: "hybrid-parse",
  resultSource: "local-wide-table",
  apiKey: "",
  headers: {},
  rateLimitBackoffMs: 60000,
  responseFormat: true
}, window.__AI_STRATEGY_LOCAL_CONFIG__ || {});
