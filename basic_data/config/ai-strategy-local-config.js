// 兼容旧入口：新配置请编辑同目录下的“模型服务配置.js”。
// 如果确实需要临时覆盖，可在下面对象里填写少量字段；会覆盖“模型服务配置.js”中的同名字段。
window.__AI_STRATEGY_LOCAL_CONFIG__ = Object.assign(
  {},
  window.__AI_STRATEGY_LOCAL_CONFIG__ || {},
  {
    // enabled: true,
    // provider: "inner-ds-openai-compatible",
    // endpoint: "http://10.0.0.8:8000/v1/chat/completions",
    // model: "deepseek-r1",
    // timeoutMs: 90000,
    // apiKey: "",
    // headers: {}
  }
);
