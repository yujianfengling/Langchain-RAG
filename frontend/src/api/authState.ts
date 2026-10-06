import type { AuthUser } from './authTypes';

/**
 * 认证状态单一来源（2026-10-07）
 *
 * 背景（本轮修复的真实缺陷）：
 * 1. 未登录进入受保护页面时，页面仍然并发发起课程/任务/作业/掌握度等**个人接口**请求；
 * 2. Axios 拦截器对**每个** 401 都派发一次"登录失效"事件，而界面上同时挂载了多个
 *    UserAuth 实例（侧栏 + 隐藏的顶栏 + 抽屉），于是 6 个并发 401 × 2 个实例
 *    = 十几条重复 toast；
 * 3. 403 与 401 没有在状态层区分；
 * 4. 注销/切号后，旧账号在途请求的返回仍可能写回新账号的页面。
 *
 * 这里把"当前是谁、登录态处于哪一阶段、第几次登录（generation）"收敛成唯一来源：
 *   anonymous（未登录）→ initializing（正在校验令牌）→ authenticated（已登录）
 *                        ↘ expired（令牌已过期，只处理一次）
 * 并发 401 只会有一次失效处理；每次登录/注销都会推高 generation，
 * 请求携带发起时的 generation，返回时若不匹配即视为陈旧响应并丢弃。
 */

export type AuthStatus = 'anonymous' | 'initializing' | 'authenticated' | 'expired';

export const TOKEN_KEY = 'edu_user_token';

/** 陈旧响应（跨登录代次）错误：调用方应忽略，不要展示为故障 */
export class StaleAuthError extends Error {
  readonly name = 'StaleAuthError';
  constructor() {
    super('该响应属于上一次登录状态，已丢弃');
  }
}

export const isStaleAuthError = (e: unknown): boolean =>
  !!e && (e as { name?: string }).name === 'StaleAuthError';

function readToken(): string | null {
  try {
    return localStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
}

function writeToken(token: string | null): void {
  try {
    if (token) localStorage.setItem(TOKEN_KEY, token);
    else localStorage.removeItem(TOKEN_KEY);
  } catch {
    /* 隐私模式下不可用：本次会话内存里仍然有效 */
  }
}

let token: string | null = readToken();
let user: AuthUser | null = null;
let status: AuthStatus = token ? 'initializing' : 'anonymous';
let generation = 0;
/** 已经处理过失效的令牌：同一个令牌的多个并发 401 只处理一次 */
let expiredToken: string | null = null;

const listeners = new Set<() => void>();

function notify(): void {
  for (const fn of Array.from(listeners)) {
    try {
      fn();
    } catch {
      /* 单个订阅者异常不影响其他订阅者 */
    }
  }
}

export function subscribe(fn: () => void): () => void {
  listeners.add(fn);
  return () => {
    listeners.delete(fn);
  };
}

export const getToken = (): string | null => token;
export const getStatus = (): AuthStatus => status;
export const getUser = (): AuthUser | null => user;
export const getGeneration = (): number => generation;
export const isAuthenticated = (): boolean => status === 'authenticated';
export const isLoggedIn = (): boolean => !!token;

export function snapshot() {
  return { status, user, generation, hasToken: !!token };
}

/** 新令牌（登录/注册成功）：进入"校验中"，并开启新的登录代次 */
export function adoptToken(newToken: string): void {
  token = newToken;
  writeToken(newToken);
  user = null;
  status = 'initializing';
  generation += 1;
  expiredToken = null;
  notify();
}

export function setUser(next: AuthUser | null): void {
  user = next;
  if (next) status = 'authenticated';
  notify();
}

export function markAuthenticated(next: AuthUser | null): void {
  user = next;
  status = next ? 'authenticated' : 'anonymous';
  notify();
}

/** 本地清除（注销、或过期后回收）：进入匿名并开启新代次 */
export function clearAuth(): void {
  token = null;
  writeToken(null);
  user = null;
  status = 'anonymous';
  generation += 1;
  notify();
}

/**
 * 标记"当前令牌已被判定失效"。
 * 返回 true 表示这是该令牌的**第一次**失效处理，调用方应执行清理与提示；
 * 返回 false 表示并发请求里的后续 401，直接忽略即可（不再提示、不再导航）。
 */
export function markExpired(): boolean {
  const current = token;
  if (!current) return false;          // 本来就没登录：不算"登录失效"
  if (expiredToken === current) return false;
  expiredToken = current;
  token = null;
  writeToken(null);
  user = null;
  status = 'expired';
  generation += 1;
  notify();
  return true;
}

/** 过期提示只由单一入口消费（AuthProvider），避免多实例重复 toast */
let pendingExpiredNotice = false;
export function takeExpiredNotice(): boolean {
  const v = pendingExpiredNotice;
  pendingExpiredNotice = false;
  return v;
}
