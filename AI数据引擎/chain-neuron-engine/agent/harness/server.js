// DeepSeek Harness 服务入口（独立进程，默认 8001）
// 路径与响应结构与 python_agent 完全一致：切换 Agent 时前端零改动。
import http from "node:http";

import { config } from "./src/config.js";
import { orchestrate } from "./src/orchestrator.js";

const server = http.createServer(async (req, res) => {
  const send = (status, payload) => {
    res.writeHead(status, { "Content-Type": "application/json" });
    res.end(JSON.stringify(payload));
  };

  if (req.method === "GET" && req.url === "/healthz") {
    return send(200, {
      status: "ok",
      service: "chain-neuron-harness",
      engine: config.engineBaseUrl,
    });
  }

  if (req.method === "POST" &&
      (req.url === "/api/v1/chat" || req.url === "/agent/api/v1/query")) {
    let raw = "";
    req.on("data", (c) => (raw += c));
    req.on("end", async () => {
      try {
        const body = JSON.parse(raw || "{}");
        if (!body.message) return send(422, { code: "E4000",
                                              message: "缺少 message" });
        const result = await orchestrate(body.message);
        send(200, result);
      } catch (e) {
        send(500, { code: "E5000", message: e.message });
      }
    });
    return;
  }

  if (req.method === "POST" && req.url === "/api/v1/analyze/fields") {
    let raw = "";
    req.on("data", (c) => (raw += c));
    req.on("end", () => {
      try {
        const body = JSON.parse(raw || "{}");
        const columns = Array.isArray(body.columns) ? body.columns : [];
        send(200, {
          backend: "harness-rule",
          columns: columns.map((item) => ({
            source: item.source,
            role: item.rule_role || "ignore",
            feature_layer: item.feature_layer || (
              item.rule_role === "ignore" ? "none" :
              item.base_candidate ? "base" :
              item.rule_role === "metric" ? "branch" : "trunk"
            ),
            query_uses: item.query_uses || (
              item.rule_role === "metric"
                ? ["aggregate", "sort", "compare"]
                : item.rule_role === "ignore"
                  ? []
                  : ["filter", "group_by"]
            ),
            reason: "Harness 当前使用规则画像，等待模型通道接入",
          })),
        });
      } catch (e) {
        send(400, { code: "E4000", message: e.message });
      }
    });
    return;
  }

  send(404, { code: "E4040", message: "not found" });
});

const bindHost = process.env.AGENT_BIND_HOST || "127.0.0.1";
server.listen(config.port, bindHost, () => {
  console.log(
    `DeepSeek Harness 运行中 :${config.port} → 引擎 ${config.engineBaseUrl}`
  );
});
