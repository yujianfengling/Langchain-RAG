import apiClient from './axios';
import {
  adoptToken, clearAuth, getStatus, getToken, isLoggedIn, markAuthenticated,
  setUser as setAuthUser, subscribe,
} from './authState';
import type { AuthUser } from './authTypes';
import { ApiResponse } from '../types';

/**
 * 认证 API 客户端。
 *
 * 令牌与登录态的**唯一来源**是 api/authState.ts（本轮修复重复提示时收敛），
 * 这里只保留请求方法与同名导出的兼容转发，避免改动大量既有调用点。
 */
export type { AuthUser };

export { getToken, isLoggedIn, getStatus, subscribe };

/** 兼容旧调用：直接写入令牌并进入"校验中" */
export const setToken = (token: string): void => {
  adoptToken(token);
};

export const clearToken = (): void => {
  clearAuth();
};

export interface AuthSession extends AuthUser {
  token: string;
  expires_at: string;
}

export interface LegacyClaimItem {
  owner_label: string;
  data_scope: string;
  record_count: number;
  status: string;
  claimed_by?: string | null;
}

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

/** 获取当前登录用户（令牌失效返回 401，由拦截器统一清理一次） */
export const me = async (): Promise<ApiResponse<AuthUser>> => {
  return apiClient.get('/auth/me');
};

export const legacyClaims = async (): Promise<ApiResponse<LegacyClaimItem[]>> => {
  return apiClient.get('/auth/legacy-claims');
};

export const claimLegacy = async (): Promise<ApiResponse<any>> => {
  return apiClient.post('/auth/claim-legacy');
};

/** 内部使用：把校验成功的用户写回唯一来源 */
export const commitUser = (user: AuthUser | null): void => {
  if (user) markAuthenticated(user);
  else setAuthUser(null);
};
