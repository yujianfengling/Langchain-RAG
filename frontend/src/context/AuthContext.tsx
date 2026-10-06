import React, { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react';
import { message } from 'antd';
import {
  adoptToken, clearAuth, getStatus, isLoggedIn, markAuthenticated, snapshot,
  subscribe, takeExpiredNotice, type AuthStatus,
} from '../api/authState';
import { AUTH_EXPIRED_EVENT } from '../api/axios';
import { isStaleAuthError } from '../api/authState';
import {
  commitUser, login as loginApi, logout as logoutApi, me, register as registerApi,
} from '../api/auth';
import type { AuthUser } from '../api/authTypes';

/**
 * 认证上下文（2026-10-07）
 *
 * 唯一职责：把"当前登录态"暴露给界面，并保证
 * - 令牌校验只发生一次（启动时 me()）；
 * - 失效提示只发生一次（拦截器 markExpired 已保证单次广播，这里单实例消费）；
 * - 网络/500 等真实故障不会被误判成"未登录"（只有 401 会改变登录态）。
 */

export const AUTH_CHANGED_EVENT = 'edu:auth-changed';

export const notifyAuthChanged = () => {
  try {
    window.dispatchEvent(new Event(AUTH_CHANGED_EVENT));
  } catch {
    /* 忽略 */
  }
};

interface AuthContextValue {
  status: AuthStatus;
  user: AuthUser | null;
  /** 已登录且用户信息就绪 */
  ready: boolean;
  bootstrapError: string | null;
  login: (username: string, password: string) => Promise<AuthUser>;
  register: (username: string, password: string, displayName?: string) => Promise<AuthUser>;
  logout: () => Promise<void>;
  refreshUser: () => Promise<void>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

function toUser(data: any): AuthUser {
  return {
    id: data?.id, username: data?.username, display_name: data?.display_name,
    is_legacy: data?.is_legacy, role: data?.role,
  };
}

export const AuthProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [snap, setSnap] = useState(snapshot());
  const [bootstrapError, setBootstrapError] = useState<string | null>(null);
  const bootstrapped = useRef(false);

  useEffect(() => subscribe(() => setSnap(snapshot())), []);

  /** 启动/新令牌：只校验一次 */
  useEffect(() => {
    if (!snap.hasToken || snap.status !== 'initializing') return;
    let alive = true;
    (async () => {
      try {
        const res: any = await me();
        if (!alive) return;
        bootstrapped.current = true;
        setBootstrapError(null);
        commitUser(res?.data ? toUser(res.data) : null);
        notifyAuthChanged();
      } catch (e) {
        if (!alive || isStaleAuthError(e)) return;
        const httpStatus = (e as any)?.response?.status;
        if (httpStatus === 401) {
          // 拦截器已清理令牌并广播一次；这里不再重复提示
          setBootstrapError(null);
          return;
        }
        // 普通网络错误/500/解析错误：如实提示，但**不改变登录态**
        setBootstrapError(
          (e as any)?.response?.data?.detail
          || (e as any)?.message
          || '无法确认登录状态，请检查网络后重试');
      }
    })();
    return () => { alive = false; };
  }, [snap.hasToken, snap.status]);

  /** 失效提示：全站唯一入口，一次广播一次提示 */
  useEffect(() => {
    const onExpired = (ev: Event) => {
      const detail = (ev as CustomEvent)?.detail;
      message.warning(detail || '登录状态已失效，请重新登录');
      notifyAuthChanged();
    };
    window.addEventListener(AUTH_EXPIRED_EVENT, onExpired);
    return () => window.removeEventListener(AUTH_EXPIRED_EVENT, onExpired);
  }, []);

  // 兼容：若别处仍直接派发 notice，也保证只提示一次
  useEffect(() => {
    if (takeExpiredNotice()) { /* 已被上面的监听消费 */ }
  }, [snap.status]);

  const login = useCallback(async (username: string, password: string) => {
    const res: any = await loginApi(username, password);
    const data = res?.data;
    if (!data?.token) {
      throw new Error(res?.message || '登录失败，请核对账号与密码');
    }
    adoptToken(data.token);
    const user = toUser(data);
    markAuthenticated(user);
    bootstrapped.current = true;
    notifyAuthChanged();
    return user;
  }, []);

  const register = useCallback(async (username: string, password: string, displayName?: string) => {
    const res: any = await registerApi(username, password, displayName);
    const data = res?.data;
    if (!data?.token) {
      throw new Error(res?.message || '注册失败，请稍后重试');
    }
    adoptToken(data.token);
    const user = toUser(data);
    markAuthenticated(user);
    bootstrapped.current = true;
    notifyAuthChanged();
    return user;
  }, []);

  const logout = useCallback(async () => {
    try {
      await logoutApi();
    } catch {
      /* 后端撤销失败也允许本地退出，避免卡在不确定状态 */
    }
    clearAuth();
    setBootstrapError(null);
    notifyAuthChanged();
    message.success('已退出登录');
  }, []);

  const refreshUser = useCallback(async () => {
    if (!isLoggedIn()) {
      markAuthenticated(null);
      return;
    }
    try {
      const res: any = await me();
      commitUser(res?.data ? toUser(res.data) : null);
      setBootstrapError(null);
    } catch (e) {
      if (!isStaleAuthError(e) && (e as any)?.response?.status !== 401) {
        setBootstrapError((e as any)?.message || '刷新登录状态失败');
      }
    }
  }, []);

  const value = useMemo<AuthContextValue>(() => ({
    status: getStatus(),
    user: snap.user,
    ready: snap.status === 'authenticated' && !!snap.user,
    bootstrapError,
    login, register, logout, refreshUser,
  }), [snap, bootstrapError, login, register, logout, refreshUser]);

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
};

export const useAuth = (): AuthContextValue => {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error('useAuth 必须在 AuthProvider 内使用');
  return ctx;
};

export default AuthContext;
