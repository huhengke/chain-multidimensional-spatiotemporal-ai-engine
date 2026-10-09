import axios from "axios";

// 统一 axios 实例：开发环境经 Vite 代理同源访问
export const TOKEN_KEY = "chain_neuron_token";

export const http = axios.create({
  baseURL: "/",
  timeout: 120000,
});

http.interceptors.request.use((cfg) => {
  const token = localStorage.getItem(TOKEN_KEY);
  if (token) cfg.headers.Authorization = `Bearer ${token}`;
  return cfg;
});

http.interceptors.response.use(
  (res) => res,
  (error) => {
    if (error.response?.status === 401) {
      localStorage.removeItem(TOKEN_KEY);
      if (!location.pathname.startsWith("/login")) {
        location.href = "/login";
      }
    }
    return Promise.reject(error);
  }
);

// 提取后端错误信息
export function errorMessage(e: unknown, fallback = "请求失败"): string {
  if (axios.isAxiosError(e)) {
    return e.response?.data?.message || e.message || fallback;
  }
  return fallback;
}
