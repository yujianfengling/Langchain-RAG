import axios, { AxiosInstance, AxiosResponse } from 'axios';

// 令牌存储键（与 api/auth.ts 保持一致）
const TOKEN_KEY = 'edu_user_token';
// 登录态失效时通知应用层（避免循环依赖，用自定义事件解耦）
export const AUTH_EXPIRED_EVENT = 'edu:auth-expired';

// 创建axios实例
const apiClient: AxiosInstance = axios.create({
  baseURL: '/api', // Vite代理会处理这个
  timeout: 120000, // 120s：大知识库检索+embedding冷加载+LLM生成可能超过30s
  headers: {
    'Content-Type': 'application/json',
  },
});

/**
 * 构造带身份的请求头（供 fetch / SSE / 语音等**绕过 axios 拦截器**的调用复用）
 *
 * 阶段一补验：此前 /api/chat/stream、/api/audio/speech 用 fetch 直连，
 * 没有携带令牌 → 后端退回历史账号，导致会话归属校验失败、学习记录记到错误账号。
 */
export const authHeaders = (extra?: Record<string, string>): Record<string, string> => {
  const headers: Record<string, string> = { ...(extra || {}) };
  try {
    const token = localStorage.getItem(TOKEN_KEY);
    if (token) headers['X-User-Token'] = token;
  } catch {
    /* localStorage 不可用时按未登录处理 */
  }
  return headers;
};

// 请求拦截器：统一携带登录令牌（学习数据归属由后端依据令牌判定）
apiClient.interceptors.request.use(
  (config) => {
    try {
      const token = localStorage.getItem(TOKEN_KEY);
      if (token && !config.headers?.['X-User-Token']) {
        config.headers = config.headers || {};
        (config.headers as any)['X-User-Token'] = token;
      }
    } catch {
      /* localStorage 不可用时按未登录处理 */
    }
    return config;
  },
  (error) => {
    return Promise.reject(error);
  }
);

// 响应拦截器
apiClient.interceptors.response.use(
  (response: AxiosResponse) => {
    return response.data;
  },
  (error) => {
    const status = error.response?.status;
    const message = error.response?.data?.message || error.response?.data?.detail
      || error.message || '请求失败';
    // 401：登录态失效 → 清理本地令牌并广播，让界面切回登录入口
    if (status === 401) {
      try {
        localStorage.removeItem(TOKEN_KEY);
      } catch {
        /* 忽略 */
      }
      try {
        window.dispatchEvent(new CustomEvent(AUTH_EXPIRED_EVENT, { detail: message }));
      } catch {
        /* 忽略 */
      }
    }
    console.error('API Error:', message);
    return Promise.reject(error);
  }
);

export default apiClient;