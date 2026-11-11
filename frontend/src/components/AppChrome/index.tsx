import React, { useEffect, useMemo, useRef, useState } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import { useReducedMotion } from 'framer-motion';
import {
  BarChartOutlined, CheckSquareOutlined, DatabaseOutlined, EditOutlined,
  FormOutlined, RobotOutlined, SettingOutlined, UserOutlined,
} from '@ant-design/icons';
import AccountMenu from '../AccountMenu';
import { useAuth } from '../../context/AuthContext';
import { NAV_MODE_EVENT, readNavMode, type NavMode } from './navMode';
import './chrome.css';

/**
 * 全站外壳的两种导航（默认顶栏）。
 *
 * 顶栏：左侧栏挪到上方，平时收成顶部正中的拉条，页面用满宽度。
 * 点一下拉条展开导航；按住继续下拉，越过一截就动画进入「我的」。
 * 左轮：不留导航条。空白处右键（触屏长按）以点击点为圆心弹出弹夹，
 * 页面从点击处向左凸出，当前页整格淡绿。顶栏和左轮的切换在设置里。
 */

interface ShellItem {
  key: string;
  label: string;
  icon: React.ReactNode;
  pathname: string;
  search?: string;
}

const STUDENT_ITEMS: ShellItem[] = [
  { key: '/student', label: 'AI 伴学', icon: <RobotOutlined />, pathname: '/student' },
  { key: '/student/materials', label: '资料库', icon: <DatabaseOutlined />, pathname: '/student/materials' },
  { key: '/student/tasks', label: '任务与复习', icon: <CheckSquareOutlined />, pathname: '/student/tasks' },
];

const TEACHER_ITEMS: ShellItem[] = [
  { key: '/teacher?section=assistant', label: '教学助手', icon: <RobotOutlined />, pathname: '/teacher', search: 'section=assistant' },
  { key: '/teacher?section=prep', label: '课程与备课', icon: <EditOutlined />, pathname: '/teacher', search: 'section=prep' },
  { key: '/teacher?section=assignments', label: '作业与批改', icon: <FormOutlined />, pathname: '/teacher', search: 'section=assignments' },
  { key: '/teacher?section=analytics', label: '学情分析', icon: <BarChartOutlined />, pathname: '/teacher', search: 'section=analytics' },
];

const ME_ITEM: ShellItem = { key: '/me', label: '我的', icon: <UserOutlined />, pathname: '/me' };
const SETTINGS_ITEM: ShellItem = { key: '/settings', label: '设置', icon: <SettingOutlined />, pathname: '/settings' };

const OPEN_H = 56;
/** 栏已经展开之后，再往下拉这么多就进入个人页 */
const PROFILE_PULL = 72;
/** 弹夹在鼠标左边所占宽度（窗口 196 + 与点击点的间隙） */
const CYL_SPAN_L = 208;
const CYL_HALF_H = 78;

type Spin = 'idle' | 'next' | 'prev';

function selectedKey(pathname: string, search: string): string {
  if (pathname.startsWith('/teacher')) {
    const section = new URLSearchParams(search).get('section') || 'assistant';
    return '/teacher?section=' + section;
  }
  if (pathname.startsWith('/me')) return '/me';
  if (pathname.startsWith('/settings')) return '/settings';
  if (pathname.startsWith('/student/materials')) return '/student/materials';
  if (pathname.startsWith('/student/tasks')) return '/student/tasks';
  return '/student';
}

/** 右键只接管真正的空白，输入框、链接和正文仍用浏览器自己的菜单。 */
function isBlank(target: EventTarget | null): boolean {
  const el = target instanceof Element ? target : null;
  if (!el) return false;
  const blocked = [
    'input', 'textarea', 'select', 'option', 'button', 'a', 'label', 'img', 'video', 'canvas',
    'pre', 'code', 'p', 'li', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'td', 'th', 'blockquote',
    '[contenteditable="true"]', '[role="button"]', '[role="link"]', '[role="menuitem"]', '[role="tab"]',
    '.ant-dropdown', '.ant-select', '.ant-modal-root', '.ant-picker', '.ant-popover',
    '.ant-tooltip', '.ant-menu', '.ant-drawer', '.markdown-body', '.ant-typography',
    '.tutor-bubble', '.tutor-welcome', '.page-subtitle', '.cyl-menu', '.app-rail', '.app-pull-tab',
  ].join(',');
  return !el.closest(blocked);
}

const AppChrome: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const location = useLocation();
  const navigate = useNavigate();
  const reduceMotion = useReducedMotion();
  const { user } = useAuth();
  const isTeacher = user?.role === 'teacher' || user?.role === 'admin';
  const roleItems = isTeacher ? TEACHER_ITEMS : STUDENT_ITEMS;
  const wheelItems = useMemo(() => [...roleItems, ME_ITEM, SETTINGS_ITEM], [roleItems]);

  const [mode, setMode] = useState<NavMode>(readNavMode);
  const [open, setOpen] = useState(false);
  const [pull, setPull] = useState(0);
  const [dragging, setDragging] = useState(false);
  const [diving, setDiving] = useState(false);
  const [diveFade, setDiveFade] = useState(false);
  const [instant, setInstant] = useState(false);
  const [cyl, setCyl] = useState<{ left: number; top: number } | null>(null);
  const [cylHint, setCylHint] = useState(false);
  const [index, setIndex] = useState(0);
  const [phase, setPhase] = useState<Spin>('idle');

  const currentKey = selectedKey(location.pathname, location.search);
  const onMe = location.pathname.startsWith('/me');
  const extra = open ? pull : Math.max(0, pull - OPEN_H);
  const ready = !onMe && extra >= PROFILE_PULL && !diving;
  const railH = diving ? Math.max(pull, OPEN_H) : (open ? OPEN_H + pull : pull);
  const navH = mode === 'cylinder' || diving ? 0 : railH;

  const dragRef = useRef(false);
  const startY = useRef(0);
  const moved = useRef(0);
  const openRef = useRef(open);
  openRef.current = open;
  const divingRef = useRef(false);
  const timers = useRef<number[]>([]);
  const indexRef = useRef(0);
  indexRef.current = index;
  const phaseRef = useRef<Spin>('idle');
  phaseRef.current = phase;
  const itemsRef = useRef(wheelItems);
  itemsRef.current = wheelItems;
  const keyRef = useRef(currentKey);
  keyRef.current = currentKey;
  const queued = useRef<0 | 1 | -1>(0);
  const syncLock = useRef(false);
  const pendingKey = useRef<string | null>(null);
  const reduceRef = useRef(reduceMotion);
  reduceRef.current = reduceMotion;
  const hintTimer = useRef(0);

  useEffect(() => () => {
    timers.current.forEach((id) => window.clearTimeout(id));
    if (hintTimer.current) window.clearTimeout(hintTimer.current);
  }, []);

  const go = (item: ShellItem) => {
    if (item.search) navigate({ pathname: item.pathname, search: item.search });
    else navigate(item.pathname);
  };
  const goRef = useRef(go);
  goRef.current = go;

  useEffect(() => {
    // 弹夹刚转到位时地址可能还没跟上。锁住，避免下标被旧路由拽回去。
    if (syncLock.current) {
      if (!pendingKey.current || pendingKey.current === currentKey) {
        syncLock.current = false;
        pendingKey.current = null;
      }
      return;
    }
    if (phase !== 'idle') return;
    const i = wheelItems.findIndex((item) => item.key === currentKey);
    if (i >= 0) setIndex(i);
  }, [currentKey, phase, wheelItems]);

  const applyMode = (next: NavMode) => {
    setMode(next);
    if (next === 'top') {
      setCyl(null);
      setCylHint(false);
    }
    if (next === 'cylinder') {
      setOpen(false);
      setPull(0);
      setCylHint(true);
      if (hintTimer.current) window.clearTimeout(hintTimer.current);
      hintTimer.current = window.setTimeout(() => setCylHint(false), 2600);
    }
  };

  useEffect(() => {
    const onMode = (event: Event) => {
      const next = (event as CustomEvent<NavMode>).detail;
      if (next === 'top' || next === 'cylinder') applyMode(next);
    };
    window.addEventListener(NAV_MODE_EVENT, onMode);
    return () => window.removeEventListener(NAV_MODE_EVENT, onMode);
  }, []);

  const finishDive = () => {
    setInstant(true);
    setDiving(false);
    divingRef.current = false;
    setDiveFade(false);
    setOpen(false);
    setPull(0);
    window.requestAnimationFrame(() => setInstant(false));
  };

  const startDive = () => {
    if (divingRef.current || onMe) {
      setPull(0);
      setDragging(false);
      return;
    }
    if (reduceMotion) {
      setPull(0);
      setOpen(false);
      setDragging(false);
      navigate('/me');
      return;
    }
    divingRef.current = true;
    setDragging(false);
    setDiving(true);
    navigate('/me');
    timers.current.push(window.setTimeout(() => setDiveFade(true), 300));
    timers.current.push(window.setTimeout(finishDive, 580));
  };

  const onTabDown = (event: React.PointerEvent<HTMLButtonElement>) => {
    if (event.button !== 0 || divingRef.current) return;
    dragRef.current = true;
    startY.current = event.clientY;
    moved.current = 0;
    setDragging(true);
    try { event.currentTarget.setPointerCapture(event.pointerId); } catch { /* 旧浏览器忽略 */ }
  };

  const onTabMove = (event: React.PointerEvent<HTMLButtonElement>) => {
    if (!dragRef.current) return;
    const dy = Math.max(0, event.clientY - startY.current);
    moved.current = dy;
    setPull(Math.min(dy, 240));
  };

  const onTabUp = () => {
    if (!dragRef.current) return;
    dragRef.current = false;
    setDragging(false);
    const dy = moved.current;
    const wasOpen = openRef.current;
    if (dy < 8) {
      setPull(0);
      setOpen((value) => !value);
      return;
    }
    const past = wasOpen ? dy : Math.max(0, dy - OPEN_H);
    if (past >= PROFILE_PULL && !location.pathname.startsWith('/me')) {
      startDive();
      return;
    }
    setPull(0);
    setOpen(true);
  };

  const openCylinder = (clientX: number, clientY: number) => {
    const list = itemsRef.current;
    const found = list.findIndex((item) => item.key === keyRef.current);
    const nextIndex = found >= 0 ? found : 0;
    indexRef.current = nextIndex;
    setIndex(nextIndex);
    setPhase('idle');
    phaseRef.current = 'idle';
    // 原点是点击点。弹夹只向左长，贴边时只平移、不翻面。
    let left = clientX;
    let top = clientY;
    left = Math.max(CYL_SPAN_L + 8, Math.min(left, window.innerWidth - 12));
    top = Math.max(CYL_HALF_H + 8, Math.min(top, window.innerHeight - CYL_HALF_H - 8));
    setCyl({ left, top });
  };

  const step = (dir: 1 | -1) => {
    const list = itemsRef.current;
    if (!list.length) return;
    if (reduceRef.current) {
      const next = (indexRef.current + dir + list.length) % list.length;
      indexRef.current = next;
      setIndex(next);
      goRef.current(list[next]);
      return;
    }
    if (phaseRef.current !== 'idle') {
      queued.current = dir;
      return;
    }
    const nextPhase: Spin = dir === 1 ? 'next' : 'prev';
    phaseRef.current = nextPhase;
    setPhase(nextPhase);
  };
  const stepRef = useRef(step);
  stepRef.current = step;

  const onStripEnd = (event: React.TransitionEvent<HTMLDivElement>) => {
    if (event.target !== event.currentTarget || event.propertyName !== 'transform') return;
    const dir = phaseRef.current === 'next' ? 1 : phaseRef.current === 'prev' ? -1 : 0;
    if (!dir) return;
    const list = itemsRef.current;
    const next = (indexRef.current + dir + list.length) % list.length;
    indexRef.current = next;
    phaseRef.current = 'idle';
    syncLock.current = true;
    pendingKey.current = list[next].key;
    // 同一帧关掉过渡再把条目对齐，避免滚完再弹回去。
    // 过渡要等这一帧画完再打开，否则浏览器会把复位当成新的动画。
    setInstant(true);
    setPhase('idle');
    setIndex(next);
    goRef.current(list[next]);
    window.requestAnimationFrame(() => {
      window.requestAnimationFrame(() => {
        setInstant(false);
        const pending = queued.current;
        queued.current = 0;
        if (pending) stepRef.current(pending);
      });
    });
  };

  useEffect(() => {
    if (mode !== 'cylinder') return undefined;
    const onContext = (event: MouseEvent) => {
      if (!isBlank(event.target)) return;
      event.preventDefault();
      openCylinder(event.clientX, event.clientY);
    };
    let timer = 0;
    let sx = 0;
    let sy = 0;
    const clearTimer = () => {
      if (timer) window.clearTimeout(timer);
      timer = 0;
    };
    const onTouchStart = (event: TouchEvent) => {
      if (event.touches.length !== 1 || !isBlank(event.target)) return;
      sx = event.touches[0].clientX;
      sy = event.touches[0].clientY;
      clearTimer();
      timer = window.setTimeout(() => openCylinder(sx, sy), 520);
    };
    const onTouchMove = (event: TouchEvent) => {
      if (!timer || event.touches.length !== 1) return;
      const dx = event.touches[0].clientX - sx;
      const dy = event.touches[0].clientY - sy;
      if (dx * dx + dy * dy > 100) clearTimer();
    };
    document.addEventListener('contextmenu', onContext, true);
    document.addEventListener('touchstart', onTouchStart, { passive: true });
    document.addEventListener('touchmove', onTouchMove, { passive: true });
    document.addEventListener('touchend', clearTimer);
    document.addEventListener('touchcancel', clearTimer);
    return () => {
      clearTimer();
      document.removeEventListener('contextmenu', onContext, true);
      document.removeEventListener('touchstart', onTouchStart);
      document.removeEventListener('touchmove', onTouchMove);
      document.removeEventListener('touchend', clearTimer);
      document.removeEventListener('touchcancel', clearTimer);
    };
  }, [mode]);

  useEffect(() => {
    if (!cyl) return undefined;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setCyl(null);
      else if (event.key === 'ArrowDown') stepRef.current(1);
      else if (event.key === 'ArrowUp') stepRef.current(-1);
    };
    const onDown = (event: MouseEvent) => {
      const target = event.target instanceof Element ? event.target : null;
      if (target && target.closest('.cyl-menu, .ant-dropdown')) return;
      setCyl(null);
    };
    const onWheel = (event: WheelEvent) => {
      event.preventDefault();
      const scale = event.deltaMode === 1 ? 16 : 1;
      const dy = event.deltaY * scale;
      if (Math.abs(dy) < 8) return;
      stepRef.current(dy > 0 ? 1 : -1);
    };
    document.addEventListener('keydown', onKey);
    document.addEventListener('mousedown', onDown);
    window.addEventListener('wheel', onWheel, { passive: false });
    return () => {
      document.removeEventListener('keydown', onKey);
      document.removeEventListener('mousedown', onDown);
      window.removeEventListener('wheel', onWheel);
    };
  }, [cyl]);

  const hint = diving
    ? '我的主页'
    : onMe && extra > 36
      ? '已经在我的主页'
      : ready
        ? '松开，进入我的主页'
        : '再往下拉一点';
  const hintOpacity = diving ? 1 : Math.max(0, Math.min(1, (extra - 16) / 40));

  const slots = [-2, -1, 0, 1, 2].map((offset) => {
    const n = wheelItems.length || 1;
    const item = wheelItems[(index + offset + n * 8) % n];
    const arriving = phase === 'next' ? 1 : phase === 'prev' ? -1 : 0;
    const distance = Math.abs(offset - arriving);
    const tone = distance === 0 ? 'is-current' : distance === 1 ? 'is-near' : 'is-far';
    return { offset, item, tone };
  });

  const shellClass = [
    'app-shell',
    mode === 'cylinder' ? 'is-cylinder' : 'is-top',
    open ? 'is-nav-open' : '',
    dragging ? 'is-dragging' : '',
    diving ? 'is-diving' : '',
    diveFade ? 'is-dive-fade' : '',
    instant ? 'is-instant' : '',
    ready ? 'is-ready' : '',
  ].filter(Boolean).join(' ');

  return (
    <div
      className={shellClass}
      style={{
        ['--app-nav-h' as string]: navH + 'px',
        ['--rail-h' as string]: railH + 'px',
      }}
    >
      {mode === 'top' && (
        <>
          <div className="app-rail" data-testid="nav-rail" aria-hidden={railH < 24 && !diving}>
            <div className="app-rail-row">
              <div className="app-rail-brand">
                <div className="app-brand-mark" style={{
                  width: 28, height: 28, display: 'flex', alignItems: 'center', justifyContent: 'center',
                  color: '#fff', fontWeight: 750, fontSize: 14, flexShrink: 0,
                }}>知</div>
                <span>知学</span>
              </div>
              <nav className="app-rail-items" aria-label="主导航">
                {roleItems.map((item) => (
                  <button
                    key={item.key}
                    type="button"
                    className={'app-rail-item' + (item.key === currentKey ? ' is-current' : '')}
                    aria-current={item.key === currentKey ? 'page' : undefined}
                    onClick={() => go(item)}
                  >
                    {item.icon}
                    <span>{item.label}</span>
                  </button>
                ))}
              </nav>
              <AccountMenu inline placement="bottomRight" onOpenProfile={() => navigate('/me')} />
            </div>
            <div className="app-rail-hint" style={{ opacity: hintOpacity }}>{hint}</div>
          </div>
          <button
            type="button"
            className="app-pull-tab"
            data-testid="nav-tab"
            aria-expanded={open}
            aria-label={open ? '收起导航，继续下拉进入我的主页' : '展开导航，按住下拉进入我的主页'}
            onPointerDown={onTabDown}
            onPointerMove={onTabMove}
            onPointerUp={onTabUp}
            onPointerCancel={onTabUp}
          >
            <span className="app-pull-grip" />
          </button>
        </>
      )}

      {cyl && mode === 'cylinder' && (
        <div
          className="cyl-menu"
          data-testid="cyl-menu"
          style={{ left: cyl.left, top: cyl.top }}
          onContextMenu={(event) => event.preventDefault()}
        >
          <div className="cyl-window" role="menu" aria-label="页面导航">
            <div
              className={'cyl-strip' + (phase === 'idle' ? '' : ' is-' + phase) + (instant ? ' is-instant' : '')}
              onTransitionEnd={onStripEnd}
            >
              {slots.map(({ offset, item, tone }) => (
                <div className="cyl-slot" key={offset}>
                  <button
                    type="button"
                    role="menuitem"
                    className={'cyl-chamber ' + tone}
                    data-testid={tone === 'is-current' ? 'cyl-current' : undefined}
                    onClick={() => {
                      if (phase !== 'idle') return;
                      if (offset === 0) setCyl(null);
                      else if (offset === 1 || offset === -1) step(offset === 1 ? 1 : -1);
                    }}
                  >
                    {item.icon}
                    <span>{item.label}</span>
                  </button>
                </div>
              ))}
            </div>
          </div>
        </div>
      )}

      {mode === 'cylinder' && cylHint && (
        <div className="app-cyl-hint">空白处右键或长按打开导航，滚轮换页。回到顶栏请打开设置</div>
      )}

      <div className="app-main">
        {children}
      </div>
    </div>
  );
};

export default AppChrome;
