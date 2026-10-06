import axios, { AxiosInstance, AxiosResponse } from 'axios';
import {
  StaleAuthError, TOKEN_KEY, getGeneration, getToken, markExpired,
} from './authState';

// 令牌存储键（与 api/authState.ts 保持一致）
export { TOKEN_KEY };
// 登录态失效时通知应用层（单一入口 AuthProvider 消费，避免多实例重复提示）
export const AUTH_EXPIRED_EVENT = 'edu:auth-expired';

/** 请求级别的认证元信息（不发给后端，仅本地用于陈旧响应判定） */
interface AuthMeta {
  __authGen?: number;
  __token?: string | null;
}

// 创建axios实例
const apiClient: AxiosInstance = axios.create({
  baseURL: '/api', // Vite代理会处理这个
  timeout: 180000, // 180s：大资料包解析/建库 + embedding 冷加载可能较慢
  headers: {
    'Content-Type': 'application/json',
  },
});

/**
 * 构造带身份的请求头（供 fetch / SSE / 语音等**绕过 axios 拦截器**的调用复用）
 */
export const authHeaders = (extra?: Record<string, string>): Record<string, string> => {
  const headers: Record<string, string> = { ...(extra || {}) };
  const token = getToken();
  if (token) headers['X-User-Token'] = token;
  return headers;
};

// 请求拦截器：统一携带登录令牌 + 记录发起时的登录代次
apiClient.interceptors.request.use(
  (config) => {
    const token = getToken();
    if (token && !config.headers?.['X-User-Token']) {
      config.headers = config.headers || {};
      (config.headers as Record<string, string>)['X-User-Token'] = token;
    }
    const meta = config as unknown as AuthMeta;
    meta.__authGen = getGeneration();
    meta.__token = token;
    return config;
  },
  (error) => Promise.reject(error)
);

/** 陈旧响应（旧账号/旧登录代次）不得写回当前页面 */
function isStale(config: unknown): boolean {
  const meta = config as AuthMeta | undefined;
  return !!meta && meta.__authGen !== undefined && meta.__authGen !== getGeneration();
}

function messageOf(error: any): string {
  const data = error.response?.data;
  return (
    (data && (data.message || data.detail)) ||
    error.message ||
    '请求失败'
  );
}

// 响应拦截器
apiClient.interceptors.response.use(
  (response: AxiosResponse) => {
    if (isStale(response.config)) {
      return Promise.reject(new StaleAuthError());
    }
    return response.data;
  },
  (error) => {
    if (isStale(error?.config)) {
      return Promise.reject(new StaleAuthError());
    }
    const status = error.response?.status;
    const message = messageOf(error);

    if (status === 401) {
      const meta = error?.config as AuthMeta | undefined;
      // 只有"请求确实带着当前令牌"时才算登录失效；本身就没登录的 401 不算。
      // markExpired 对同一令牌只返回一次 true → 并发 401 只会有一次提示与一次广播。
      if (meta?.__token && markExpired()) {
        try {
          window.dispatchEvent(new CustomEvent(AUTH_EXPIRED_EVENT, { detail: message }));
        } catch {
          /* 忽略 */
        }
      }
      // 401 不是"服务器故障"，交给调用方按登录态处理，不打 error 噪音日志
      return Promise.reject(error);
    }

    // 403 = 已登录但权限不足：**不能**清除账号或进入未登录态
    if (status === 403) {
      console.warn('API 403（权限不足）:', message);
      return Promise.reject(error);
    }

    console.error('API Error:', message);
    return Promise.reject(error);
  }
);

export default apiClient;
