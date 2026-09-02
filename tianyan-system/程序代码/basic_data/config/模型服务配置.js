// Source-control-safe defaults. Configure credentials outside Git before enabling the model service.
window.__AI_STRATEGY_CONFIG__ = Object.freeze({
  enabled: false,
  locked: false,
  profile: "aliyun-bailian",
  provider: "aliyun-bailian-openai-compatible",
  baseUrl: "https://ws-xeqhh9zm5j6xbubs.cn-beijing.maas.aliyuncs.com/compatible-mode/v1",
  endpoint: "https://ws-xeqhh9zm5j6xbubs.cn-beijing.maas.aliyuncs.com/compatible-mode/v1/chat/completions",
  model: "qwen3.6-flash",
  reasonModel: "",
  timeoutMs: 60000,
  mode: "hybrid-parse",
  resultSource: "local-wide-table",
  apiKey: "",
  headers: {},
  rateLimitBackoffMs: 60000,
  responseFormat: true,
  modelProfiles: Object.freeze({
    "aliyun-bailian": Object.freeze({
      label: "阿里云百炼",
      provider: "aliyun-bailian-openai-compatible",
      baseUrl: "https://ws-xeqhh9zm5j6xbubs.cn-beijing.maas.aliyuncs.com/compatible-mode/v1",
      endpoint: "https://ws-xeqhh9zm5j6xbubs.cn-beijing.maas.aliyuncs.com/compatible-mode/v1/chat/completions",
      model: "qwen3.6-flash",
      reasonModel: "",
      timeoutMs: 60000,
      responseFormat: true,
      apiKey: "",
      headers: {}
    })
  })
});
