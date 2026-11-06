import apiClient, { authHeaders } from './axios';
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

/** 只改展示名，登录名不变 */
export const updateProfile = async (displayName: string): Promise<ApiResponse<AuthUser>> => {
  return apiClient.patch('/auth/me', { display_name: displayName });
};

/** 上传头像。用 fetch 而不是 axios，避免默认的 JSON Content-Type 吃掉文件边界。 */
export const uploadAvatar = async (file: File): Promise<ApiResponse<AuthUser>> => {
  const body = new FormData();
  body.append('file', file);
  const res = await fetch('/api/auth/avatar', { method: 'POST', headers: authHeaders(), body });
  const json = await res.json().catch(() => ({}));
  if (!res.ok || json?.success === false) {
    throw new Error(json?.message || '头像上传失败');
  }
  return json;
};

export const fetchAvatarBlob = async (): Promise<Blob> => {
  const res = await fetch('/api/auth/avatar', { headers: authHeaders() });
  if (!res.ok) throw new Error('还没有头像');
  return res.blob();
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
