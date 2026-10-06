import React, { Suspense, lazy } from 'react';
import { BrowserRouter as Router, Routes, Route, useLocation, useNavigate } from 'react-router-dom';
import { Layout, Menu, ConfigProvider, Drawer, Button, Spin } from 'antd';
import { motion, AnimatePresence } from 'framer-motion';
import {
  ReadOutlined, DatabaseOutlined, CheckSquareOutlined, TeamOutlined,
  MenuOutlined, CloseOutlined,
} from '@ant-design/icons';
import zhCN from 'antd/locale/zh_CN';
import { AUTH_CHANGED_EVENT } from './components/UserAuth';
import AccountMenu from './components/AccountMenu';
import SystemStatusBanner from './components/SystemStatusBanner';
import { CourseProvider } from './context/CourseContext';
import { isLoggedIn, me } from './api/auth';
import { useIsNarrow } from './hooks/useIsNarrow';

/**
 * 路由级懒加载（首屏只留框架与公共依赖）
 */
const WorkbenchPage = lazy(() => import('./pages/WorkbenchPage'));
const MyCoursesPage = lazy(() => import('./pages/MyCoursesPage'));
const TodoPage = lazy(() => import('./pages/TodoPage'));
const AssignmentsPage = lazy(() => import('./pages/AssignmentsPage'));
const SettingsPage = lazy(() => import('./pages/SettingsPage'));
const LearningPlanPage = lazy(() => import('./pages/LearningPlanPage'));
const KnowledgePage = lazy(() => import('./pages/KnowledgePage'));
const TeacherPage = lazy(() => import('./pages/TeacherPage'));

const { Sider } = Layout;

/**
 * 主导航只保留三条学习主线（2026-10-06 改版）：
 *   开始学习 / 我的课程 / 作业与复习
 * 设置、账号、讲解偏好收进头像菜单；错题·掌握度·计划·学习记忆不再平铺为导航。
 */
const sidebarItems = [
  { key: '/student', icon: <ReadOutlined />, label: '开始学习' },
  { key: '/student/courses', icon: <DatabaseOutlined />, label: '我的课程' },
  { key: '/student/todo', icon: <CheckSquareOutlined />, label: '作业与复习' },
  { key: '/teacher', icon: <TeamOutlined />, label: '教师工作台', teacherOnly: true },
];

const PageFallback: React.FC = () => (
  <div style={{ display: 'flex', justifyContent: 'center', padding: 48 }}><Spin /></div>
);

const App: React.FC = () => {
  const location = useLocation();
  const navigate = useNavigate();
  const isNarrow = useIsNarrow();
  const [role, setRole] = React.useState<string>('student');
  const [drawerOpen, setDrawerOpen] = React.useState(false);

  React.useEffect(() => {
    let alive = true;
    const load = async () => {
      if (!isLoggedIn()) { if (alive) setRole('student'); return; }
      try {
        const res: any = await me();
        if (alive) setRole((res && res.data && res.data.role) || 'student');
      } catch (e) { if (alive) setRole('student'); }
    };
    load();
    window.addEventListener(AUTH_CHANGED_EVENT, load);
    return () => { alive = false; window.removeEventListener(AUTH_CHANGED_EVENT, load); };
  }, []);

  React.useEffect(() => { setDrawerOpen(false); }, [location.pathname]);

  const canSeeTeacher = role === 'teacher' || role === 'admin';
  const visibleItems = sidebarItems.filter((item: any) => !item.teacherOnly || canSeeTeacher);

  /** 每次只高亮一个主导航 */
  const getSelectedKey = () => {
    const path = location.pathname;
    if (path.startsWith('/teacher')) return '/teacher';
    if (path.startsWith('/student/courses')) return '/student/courses';
    if (path.startsWith('/student/todo') || path.startsWith('/student/assignments')) return '/student/todo';
    if (path.startsWith('/learning')) return '/student/todo';
    return '/student';
  };

  const brandNode = (compact = false) => (
    <div style={{ display: 'flex', alignItems: 'center', gap: 12, minWidth: 0 }}>
      <div style={{
        width: 38, height: 38, borderRadius: 11,
        background: 'linear-gradient(135deg, #0f766e 0%, #14b8a6 60%, #5eead4 100%)',
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        color: '#fff', fontWeight: 800, fontSize: 17, flexShrink: 0,
      }}>智</div>
      <div style={{ minWidth: 0 }}>
        <div style={{ fontWeight: 700, fontSize: compact ? 15 : 13, color: '#0f172a', lineHeight: 1.35, wordBreak: 'break-word' }}>
          {compact ? 'RAG智慧教育' : 'RAG与LangChain融合的智慧教育系统'}
        </div>
        <div style={{ fontSize: 11, color: '#94a3b8', marginTop: 2, lineHeight: 1.4 }}>
          {compact ? '检索增强问答' : '基于课程资料的检索增强问答与教学辅助'}
        </div>
      </div>
    </div>
  );

  const menuNode = (
    <Menu
      mode="inline"
      selectedKeys={[getSelectedKey()]}
      onClick={({ key }) => { navigate(key); setDrawerOpen(false); }}
      style={{ border: 'none', background: 'transparent' }}
      items={visibleItems.map((item: any) => ({
        ...item,
        style: { marginBottom: 2, borderRadius: 10, fontSize: 14, fontWeight: getSelectedKey() === item.key ? 600 : 450 },
      }))}
    />
  );

  const footerNode = (
    <div style={{ padding: '10px 20px', borderTop: '1px solid #f1f5f9', textAlign: 'center' }}>
      <div style={{ fontSize: 11, color: '#94a3b8', letterSpacing: '0.02em' }}>RAG × LangChain · 智慧教育</div>
    </div>
  );

  return (
    <ConfigProvider
      locale={zhCN}
      theme={{
        token: {
          colorPrimary: '#0f766e', colorPrimaryBg: '#f0fdfa', colorLink: '#0f766e', colorLinkHover: '#0d9488',
          colorSuccess: '#059669', colorWarning: '#d97706', colorError: '#dc2626',
          colorBgContainer: '#ffffff', colorBgLayout: '#f8fafc', colorBgElevated: '#ffffff',
          colorText: '#0f172a', colorTextSecondary: '#475569', colorTextTertiary: '#94a3b8',
          colorBorder: '#e2e8f0', colorBorderSecondary: '#f1f5f9',
          borderRadius: 10, borderRadiusLG: 14,
          fontFamily: "'Geist', 'Satoshi', -apple-system, BlinkMacSystemFont, 'PingFang SC', 'Microsoft YaHei', sans-serif",
          fontSize: 15, controlHeight: 38,
        },
        components: {
          Menu: {
            itemColor: '#0f172a', itemHoverColor: '#0f766e', itemHoverBg: '#f0fdfa',
            itemSelectedColor: '#0f766e', itemSelectedBg: '#ccfbf1', itemActiveBg: '#ccfbf1',
            horizontalItemSelectedColor: '#0f766e', itemHeight: 44,
          },
          Card: { borderRadiusLG: 14 },
          Button: { borderRadius: 8, controlHeight: 38 },
          Input: { borderRadius: 8, controlHeight: 40 },
          Select: { borderRadius: 8, controlHeight: 38 },
          Modal: { borderRadiusLG: 16 },
          Table: { borderRadius: 12, headerBg: '#f8fafc', rowHoverBg: '#f0fdfa' },
          Tag: { borderRadiusSM: 6 },
          Tabs: { inkBarColor: '#0f766e', itemActiveColor: '#0f766e', itemSelectedColor: '#0f766e', itemHoverColor: '#0d9488' },
        },
      }}
    >
      <CourseProvider>
        <Layout style={{ minHeight: '100vh', background: '#f8fafc' }}>
          <motion.div
            className="app-sider-wrap"
            initial={{ x: -40, opacity: 0 }}
            animate={{ x: 0, opacity: 1 }}
            transition={{ duration: 0.18 }}
            style={{ display: 'flex', flexDirection: 'column', width: 200, flexShrink: 0 }}
          >
            <Sider
              className="app-sider"
              width={200}
              style={{
                background: '#ffffff', borderRight: '1px solid #e2e8f0',
                height: '100vh', position: 'fixed', left: 0, top: 0, bottom: 0, zIndex: 100,
                display: 'flex', flexDirection: 'column', overflow: 'hidden',
              }}
            >
              <div style={{ padding: '18px 16px 14px', borderBottom: '1px solid #f1f5f9' }}>{brandNode()}</div>
              <div style={{ flex: 1, padding: '8px 8px', overflowY: 'auto' }}>{menuNode}</div>
              <div style={{ padding: '8px 10px', borderTop: '1px solid #f1f5f9' }}><AccountMenu /></div>
              {footerNode}
            </Sider>
          </motion.div>

          <header className="app-topbar">
            <Button type="text" icon={<MenuOutlined />} aria-label="打开导航菜单" onClick={() => setDrawerOpen(true)} />
            <div className="app-topbar-title" style={{ fontWeight: 700, color: '#0f172a' }}>RAG智慧教育</div>
            <div style={{ marginLeft: 'auto' }}><AccountMenu compact /></div>
          </header>

          <Drawer
            placement="left" open={drawerOpen} onClose={() => setDrawerOpen(false)} width={260}
            closable={false} styles={{ body: { padding: 0, display: 'flex', flexDirection: 'column' } }} title={null}
          >
            <div style={{ display: 'flex', alignItems: 'center', padding: '14px 16px', borderBottom: '1px solid #f1f5f9' }}>
              {brandNode()}
              <Button type="text" icon={<CloseOutlined />} aria-label="关闭导航菜单" style={{ marginLeft: 'auto' }} onClick={() => setDrawerOpen(false)} />
            </div>
            <div style={{ flex: 1, padding: '8px 8px', overflowY: 'auto' }}>{menuNode}</div>
            <div style={{ padding: '8px 10px', borderTop: '1px solid #f1f5f9' }}><AccountMenu /></div>
          </Drawer>

          <div className={isNarrow ? 'app-content app-content-narrow' : 'app-content'}
               style={{ flex: 1, minWidth: 0, minHeight: '100vh', display: 'flex', flexDirection: 'column' }}>
            <SystemStatusBanner />
            <div style={{ flex: 1, minWidth: 0 }}>
              <AnimatePresence mode="sync">
                <motion.div key={location.pathname} initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} transition={{ duration: 0.12 }}>
                  <Suspense fallback={<PageFallback />}>
                    <Routes location={location}>
                      <Route path="/" element={<WorkbenchPage />} />
                      <Route path="/student" element={<WorkbenchPage />} />
                      <Route path="/student/courses" element={<MyCoursesPage />} />
                      <Route path="/student/todo" element={<TodoPage />} />
                      <Route path="/student/assignments" element={<AssignmentsPage />} />
                      <Route path="/chat" element={<WorkbenchPage />} />
                      <Route path="/teacher" element={<TeacherPage onNavigate={navigate} />} />
                      <Route path="/knowledge" element={<KnowledgePage />} />
                      <Route path="/learning" element={<LearningPlanPage />} />
                      <Route path="/settings" element={<SettingsPage />} />
                    </Routes>
                  </Suspense>
                </motion.div>
              </AnimatePresence>
            </div>
          </div>
        </Layout>
      </CourseProvider>
    </ConfigProvider>
  );
};

const AppWithRouter: React.FC = () => (<Router><App /></Router>);

export default AppWithRouter;
