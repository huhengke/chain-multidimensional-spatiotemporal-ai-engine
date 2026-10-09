// 运行配置：全部来自环境变量，不写死任何个人绝对路径
export const config = {
  engineBaseUrl: (
    process.env.ENGINE_BASE_URL || "http://127.0.0.1:8000"
  ).replace(/\/$/, ""),
  serviceToken:
    process.env.ENGINE_SERVICE_TOKEN || "dev-service-token-please-rotate",
  port: Number(process.env.AGENT_PORT || 8001),
  // 模型适配器
  ollamaBaseUrl: process.env.OLLAMA_BASE_URL || "http://127.0.0.1:11434",
  ollamaModel: process.env.OLLAMA_MODEL || "deepseek-v4.1-flash",
  deepseekApiKey: process.env.DEEPSEEK_API_KEY || "",
  deepseekBaseUrl: process.env.DEEPSEEK_BASE_URL || "https://api.deepseek.com",
};
