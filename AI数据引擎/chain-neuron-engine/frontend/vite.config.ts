import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// 前端开发服务器 5173；Agent API 代理到 8001，其余 API 代理到引擎 8000
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    host: "127.0.0.1",
    proxy: {
      "/api/v1/agent": {
        target: "http://127.0.0.1:8001",
        changeOrigin: true,
      },
      "/api/v1/chat": {
        target: "http://127.0.0.1:8001",
        changeOrigin: true,
      },
      "/api/v1": {
        target: "http://127.0.0.1:8000",
        changeOrigin: true,
      },
      "/healthz": {
        target: "http://127.0.0.1:8000",
        changeOrigin: true,
      },
    },
  },
});
