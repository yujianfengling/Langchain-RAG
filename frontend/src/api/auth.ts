import apiClient from './axios';
import { ApiResponse } from '../types';

// 学习数据必须归属到真实用户：令牌由后端签发，前端只负责保存与随请求携带
const TOKEN_KEY = 'edu_user_token';

export interface AuthUser {
  id: string;
  username: string;
  display_name?: string | null;
  is_legacy?: boolean;
  status?: string;
  created_at?: string;
  /**
   * 角色（由**后端**判定，前端仅用于决定显示哪些入口）
   *
   * 注意：隐藏菜单≠权限。所有越权请求都会在后端被拒绝（403），
   * 前端不承担任何安全职责。
   */
  role?: 'student' | 'teacher' | 'admin';
}

/** 登录/注册返回：用户信息 + 会话令牌（后端为扁平结构） */
export type AuthSession = AuthUser & { token: string; expires_at: string };

export interface LegacyClaimItem {
  owner_label: string;
  data_scope: string;
  record_count: number;
  status: string;
  claimed_by?: string | null;
}

export const getToken = (): string | null => {
  try {
    return localStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
};

export const setToken = (token: string): void => {
  try {
    localStorage.setItem(TOKEN_KEY, token);
  } catch {
    /* 隐私模式下 localStorage 不可用：本次会话仍可用，刷新后需重新登录 */
  }
};

export const clearToken = (): void => {
  try {
    localStorage.removeItem(TOKEN_KEY);
  } catch {
    /* 忽略 */
  }
};

export const isLoggedIn = (): boolean => !!getToken();

export const register = async (username: string, password: string,
                               displayName?: string): Promise<ApiResponse<AuthSession>> => {
  return apiClient.post('/auth/register', {
    username,
    password,
    display_name: displayName || undefined,
  });
};

export const login = async (username: string, password: string): Promise<ApiResponse<AuthSession>> => {
  return apiClient.post('/auth/login', { username, password });
};

export const logout = async (): Promise<ApiResponse<null>> => {
  return apiClient.post('/auth/logout');
};

/** 获取当前登录用户（令牌失效返回 401，由响应拦截器清理本地令牌） */
export const me = async (): Promise<ApiResponse<AuthUser>> => {
  return apiClient.get('/auth/me');
};

/** 历史数据待认领清单（沿用旧 default_user 数据时使用，需用户显式确认） */
export const legacyClaims = async (): Promise<ApiResponse<LegacyClaimItem[]>> => {
  return apiClient.get('/auth/legacy-claims');
};

export const claimLegacy = async (): Promise<ApiResponse<any>> => {
  return apiClient.post('/auth/claim-legacy');
};
