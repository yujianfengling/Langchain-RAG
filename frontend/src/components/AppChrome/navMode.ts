export type NavMode = 'top' | 'cylinder';

export const NAV_MODE_KEY = 'app.nav.mode';
export const NAV_MODE_EVENT = 'app:nav-mode';

export function readNavMode(): NavMode {
  try {
    return window.localStorage.getItem(NAV_MODE_KEY) === 'cylinder' ? 'cylinder' : 'top';
  } catch {
    return 'top';
  }
}

/** 同一页里改导航方式。storage 事件不会通知当前标签，所以另发一个事件。 */
export function writeNavMode(mode: NavMode) {
  try { window.localStorage.setItem(NAV_MODE_KEY, mode); } catch { /* 隐私模式忽略 */ }
  window.dispatchEvent(new CustomEvent<NavMode>(NAV_MODE_EVENT, { detail: mode }));
}
