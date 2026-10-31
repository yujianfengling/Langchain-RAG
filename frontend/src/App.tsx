import React, { Suspense, lazy, useState } from 'react';
import { BrowserRouter as Router, Routes, Route, useLocation, useNavigate, Navigate } from 'react-router-dom';
import { Alert, Layout, Menu, ConfigProvider, Drawer, Button, Spin, Tooltip } from 'antd';
import { motion, AnimatePresence, useReducedMotion } from 'framer-motion';
import {
  DatabaseOutlined, CheckSquareOutlined, RobotOutlined, ReloadOutlined,
  EditOutlined, FormOutlined, BarChartOutlined, MenuOutlined, CloseOutlined,
  MenuFoldOutlined, MenuUnfoldOutlined,
} from '@ant-design/icons';
import zhCN from 'antd/locale/zh_CN';
import AccountMenu from './components/AccountMenu';
import LoginRequired from './components/LoginRequired';
import SystemStatusBanner from './components/SystemStatusBanner';
import { CourseProvider } from './context/CourseContext';
import { AuthProvider, useAuth } from './context/AuthContext';
import { useIsNarrow } from './hooks/useIsNarrow';
import { themeConfig } from './theme';

/**
 * 路由级懒加载（首屏只留框架与公共依赖）
 */
const StudentLearningPage = lazy(() => import('./pages/StudentLearningPage'));
const MaterialsPage = lazy(() => import('./pages/MaterialsPage'));
const TasksPage = lazy(() => import('./pages/TasksPage'));
const ProfilePage = lazy(() => import('./pages/ProfilePage'));
const SettingsPage = lazy(() => import('./pages/SettingsPage'));
const TeacherPage = lazy(() => import('./pages/TeacherPage'));
const AssignmentsPage = lazy(() => import('./pages/AssignmentsPage'));

const { Sider } = Layout;

/**
 * 主导航（2026-10-07 产品结构改版）
 *   学生：AI伴学 / 资料库 / 任务与复习，头像进入我的主页
 *   教师：教学助手 / 课程与备课 / 作业与批改 / 学情分析，头像同样进入我的主页
 * 旧网址通过下方 <Route> 重定向兼容，功能都有明确的新入口。
 */
const STUDENT_ITEMS = [
  { key: '/student', icon: <RobotOutlined />, label: 'AI 伴学' },
  { key: '/student/materials', icon: <DatabaseOutlined />, label: '资料库' },
  { key: '/student/tasks', icon: <CheckSquareOutlined />, label: '任务与复习' },
];

const TEACHER_ITEMS = [
  { key: '/teacher?section=assistant', icon: <RobotOutlined />, label: '教学助手' },
  { key: '/teacher?section=prep', icon: <EditOutlined />, label: '课程与备课' },
  { key: '/teacher?section=assignments', icon: <FormOutlined />, label: '作业与批改' },
  { key: '/teacher?section=analytics', icon: <BarChartOutlined />, label: '学情分析' },
];

const PageFallback: React.FC = () => (
  <div style={{ display: 'flex', justifyContent: 'center', padding: 48 }}><Spin /></div>
);

/**
 * 受保护路由：未登录/已过期时**不挂载页面组件**。
 *
 * 这样页面里的个人接口（课程、任务、作业、掌握度、导入任务…）根本不会发起，
 * 也就不会出现"登录提示 + 加载失败 + 暂无数据"同时刷屏；登录成功后自动渲染原页面。
 */
const Protected: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const { status, bootstrapError, refreshUser } = useAuth();
  if (status === 'authenticated') return <>{children}</>;
  if (status === 'initializing') {
    // 校验登录态时遇到网络/500 等真实故障：不能一直转圈，也不能当成"未登录"清账号，
    // 要如实说明并提供重试（2026-10-07 修复：此前会永久停在"正在恢复登录状态…"）。
    if (bootstrapError) {
      return (
        <div style={{ maxWidth: 520, margin: '64px auto', padding: 16 }}>
          <Alert
            type="warning" showIcon
            message="暂时无法确认登录状态"
            description={bootstrapError}
            action={<Button icon={<ReloadOutlined />} onClick={() => { void refreshUser(); }}>重试</Button>}
          />
        </div>
      );
    }
    return <PageFallback />;
  }
  return <LoginRequired reason={status === 'expired' ? 'expired' : 'anonymous'} />;
};

const App: React.FC = () => {
  const location = useLocation();
  const navigate = useNavigate();
  const isNarrow = useIsNarrow();
  const reduceMotion = useReducedMotion();
  const { user } = useAuth();
  const [drawerOpen, setDrawerOpen] = React.useState(false);
  /**
   * 全站导航折叠（桌面）。默认展开；用户选择记在 localStorage，
   * 折叠后 sider 收到 64px，主内容区立刻变宽（需求：导航不再占据过宽区域）。
   */
  const [navCollapsed, setNavCollapsed] = useState<boolean>(() => {
    if (typeof window === 'undefined') return false;
    try { return window.localStorage.getItem('app.nav.collapsed') === '1'; } catch { return false; }
  });
  const navWidth = navCollapsed ? 68 : 200;
  const toggleNav = () => {
    setNavCollapsed((v) => {
      const next = !v;
      try { window.localStorage.setItem('app.nav.collapsed', next ? '1' : '0'); } catch { /* 忽略 */ }
      return next;
    });
  };

  React.useEffect(() => { setDrawerOpen(false); }, [location.pathname, location.search]);

  const role = user?.role || 'student';
  const isTeacher = role === 'teacher' || role === 'admin';
  const items = isTeacher ? TEACHER_ITEMS : STUDENT_ITEMS;

  /** 每次只高亮一个主导航 */
  const getSelectedKey = () => {
    const path = location.pathname;
    if (path.startsWith('/teacher')) {
      const section = new URLSearchParams(location.search).get('section') || 'assistant';
      return '/teacher?section=' + section;
    }
    if (path.startsWith('/me')) return 'me';
    if (path.startsWith('/student/materials')) return '/student/materials';
    if (path.startsWith('/student/tasks')) return '/student/tasks';
    return '/student';
  };

  /**
   * 全站导航品牌区。
   * collapsed=true 时只保留图标，完整项目名通过 Tooltip 仍可查看
   * （需求：导航保持紧凑、提供折叠入口，但完整名称不能丢）。
   */
  const brandNode = (compact = false, collapsed = false) => (
    <div style={{ display: 'flex', alignItems: 'center', gap: 11, minWidth: 0,
                 justifyContent: collapsed ? 'center' : 'flex-start' }}>
      <Tooltip title={collapsed ? '知学 · RAG与LangChain融合的智慧教育系统' : ''} placement="right">
        <div className="app-brand-mark" style={{
          width: 36, height: 36, display: 'flex', alignItems: 'center', justifyContent: 'center',
          color: '#fff', fontWeight: 750, fontSize: 17, flexShrink: 0,
        }}>知</div>
      </Tooltip>
      <div className={'app-brand-copy' + (collapsed ? ' is-collapsed' : '')} aria-hidden={collapsed}>
        <div className="app-brand-name" style={{ color: '#182033', lineHeight: 1.3 }}>
          知学
        </div>
        <div className="app-brand-subtitle" style={{ fontSize: 11, marginTop: 3, lineHeight: 1.4, whiteSpace: 'nowrap' }}>
          {compact ? 'RAG 智慧教育' : (isTeacher ? '教师教学工作台' : 'RAG 智慧学习')}
        </div>
      </div>
    </div>
  );

  const menuNode = (collapsed = false) => (
    <>
      {!collapsed && <div className="app-nav-section">{isTeacher ? '教学工作' : '学习空间'}</div>}
      <Menu
        mode="inline"
        inlineCollapsed={collapsed}
        selectedKeys={[getSelectedKey()]}
        onClick={({ key }) => {
          if (String(key).startsWith('/teacher')) {
            navigate({ pathname: '/teacher', search: String(key).split('?')[1] || '' });
          } else {
            navigate(String(key));
          }
          setDrawerOpen(false);
        }}
        style={{ border: 'none', background: 'transparent' }}
        items={items.map((item: any) => ({
          ...item,
          style: { marginBottom: 2, borderRadius: 8, fontSize: 14, fontWeight: getSelectedKey() === item.key ? 600 : 450 },
        }))}
      />
    </>
  );

  return (
    <Layout style={{ minHeight: '100vh', background: '#f8fafc' }}>
      <motion.div
        className="app-sider-wrap"
        initial={reduceMotion ? { width: navWidth } : { x: -12, opacity: 0, width: navWidth }}
        animate={{ x: 0, opacity: 1, width: navWidth }}
        transition={{ duration: reduceMotion ? 0 : 0.22, ease: [0.2, 0, 0, 1] }}
        style={{ display: 'flex', flexDirection: 'column', flexShrink: 0 }}
      >
        <Sider
          className="app-sider"
          width={navWidth}
          style={{
            background: '#ffffff', borderRight: '1px solid #e2e8f0',
            height: '100vh', position: 'fixed', left: 0, top: 0, bottom: 0, zIndex: 100,
            display: 'flex', flexDirection: 'column', overflow: 'hidden',
            transition: 'width 220ms cubic-bezier(0.2, 0, 0, 1)',
          }}
        >
          <div style={{ padding: navCollapsed ? '18px 8px 14px' : '18px 16px 14px', borderBottom: '1px solid #f1f5f9' }}>
            {brandNode(false, navCollapsed)}
          </div>
          <div style={{ flex: 1, padding: '8px 8px', overflowY: 'auto' }}>{menuNode(navCollapsed)}</div>
          <div className={navCollapsed ? 'app-account-collapsed' : ''}
               style={{ padding: navCollapsed ? '8px 6px' : '10px', borderTop: '1px solid #edf0f3' }}>
            <AccountMenu compact={navCollapsed} onOpenProfile={() => navigate('/me')} />
          </div>
          <div style={{ padding: '6px 8px', textAlign: navCollapsed ? 'center' : 'right' }}>
            <Tooltip title={navCollapsed ? '展开导航' : '收起导航'} placement="right">
              <Button type="text" size="small" aria-label={navCollapsed ? '展开导航' : '收起导航'}
                      icon={navCollapsed ? <MenuUnfoldOutlined /> : <MenuFoldOutlined />}
                      onClick={toggleNav} />
            </Tooltip>
          </div>
        </Sider>
      </motion.div>

      <header className="app-topbar">
        <Button type="text" icon={<MenuOutlined />} aria-label="打开导航菜单" onClick={() => setDrawerOpen(true)} />
        <div className="app-topbar-title" style={{ fontWeight: 700, color: '#182033' }}>知学</div>
        <div style={{ marginLeft: 'auto' }}>
          <AccountMenu compact onOpenProfile={() => navigate('/me')} />
        </div>
      </header>

      <Drawer
        placement="left" open={drawerOpen} onClose={() => setDrawerOpen(false)} width={260}
        closable={false} styles={{ body: { padding: 0, display: 'flex', flexDirection: 'column' } }} title={null}
      >
        <div style={{ display: 'flex', alignItems: 'center', padding: '14px 16px', borderBottom: '1px solid #f1f5f9' }}>
          {brandNode()}
          <Button type="text" icon={<CloseOutlined />} aria-label="关闭导航菜单" style={{ marginLeft: 'auto' }} onClick={() => setDrawerOpen(false)} />
        </div>
        <div style={{ flex: 1, padding: '8px 8px', overflowY: 'auto' }}>{menuNode(false)}</div>
        <div style={{ padding: '8px 10px', borderTop: '1px solid #f1f5f9' }}>
          <AccountMenu onOpenProfile={() => { setDrawerOpen(false); navigate('/me'); }} />
        </div>
      </Drawer>

      <div className={isNarrow ? 'app-content app-content-narrow' : 'app-content'}
           style={{ flex: 1, minWidth: 0, minHeight: '100vh', display: 'flex', flexDirection: 'column' }}>
        <SystemStatusBanner />
        <div style={{ flex: 1, minWidth: 0 }}>
          <AnimatePresence mode="sync">
            <motion.div key={location.pathname} initial={reduceMotion ? { opacity: 1 } : { opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} transition={{ duration: reduceMotion ? 0 : 0.12 }}>
              <Suspense fallback={<PageFallback />}>
                <Routes location={location}>
                  <Route path="/" element={<Navigate to="/student" replace />} />
                  <Route path="/student" element={<Protected><StudentLearningPage /></Protected>} />
                  <Route path="/student/materials" element={<Protected><MaterialsPage /></Protected>} />
                  <Route path="/student/tasks" element={<Protected><TasksPage /></Protected>} />
                  <Route path="/me" element={<Protected><ProfilePage /></Protected>} />
                  <Route path="/teacher" element={<Protected><TeacherPage onNavigate={navigate} /></Protected>} />
                  <Route path="/settings" element={<Protected><SettingsPage /></Protected>} />
                  {/* 旧网址兼容：功能都有明确的新入口，不留下找不到的功能 */}
                  <Route path="/chat" element={<Navigate to="/student" replace />} />
                  <Route path="/knowledge" element={<Navigate to="/student/materials" replace />} />
                  <Route path="/student/courses" element={<Navigate to="/student/materials" replace />} />
                  <Route path="/student/todo" element={<Navigate to="/student/tasks" replace />} />
                  <Route path="/student/assignments" element={<Protected><AssignmentsPage /></Protected>} />
                  <Route path="/learning" element={<Navigate to="/student/tasks" replace />} />
                  <Route path="*" element={<Navigate to="/student" replace />} />
                </Routes>
              </Suspense>
            </motion.div>
          </AnimatePresence>
        </div>
      </div>
    </Layout>
  );
};

const AppWithRouter: React.FC = () => (
  <Router>
    <ConfigProvider locale={zhCN} theme={themeConfig}>
      <AuthProvider>
        <CourseProvider>
          <App />
        </CourseProvider>
      </AuthProvider>
    </ConfigProvider>
  </Router>
);

export default AppWithRouter;
// 2026-10-07 布局轮：导航折叠（触发 Vite 重新编译）
// 2026-10-07 布局轮：导航折叠图标（触发 Vite 重新编译）
