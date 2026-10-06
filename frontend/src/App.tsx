import React, { Suspense, lazy } from 'react';
import { BrowserRouter as Router, Routes, Route, useLocation, useNavigate, Navigate } from 'react-router-dom';
import { Layout, Menu, ConfigProvider, Drawer, Button, Spin } from 'antd';
import { motion, AnimatePresence } from 'framer-motion';
import {
  DatabaseOutlined, CheckSquareOutlined, RobotOutlined,
  EditOutlined, FormOutlined, BarChartOutlined, MenuOutlined, CloseOutlined,
} from '@ant-design/icons';
import zhCN from 'antd/locale/zh_CN';
import { AUTH_CHANGED_EVENT } from './components/UserAuth';
import AccountMenu from './components/AccountMenu';
import SystemStatusBanner from './components/SystemStatusBanner';
import { CourseProvider } from './context/CourseContext';
import { isLoggedIn, me } from './api/auth';
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

  React.useEffect(() => { setDrawerOpen(false); }, [location.pathname, location.search]);

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
          {isTeacher ? '备课 · 作业 · 学情' : '资料问答 · 引导学习'}
        </div>
      </div>
    </div>
  );

  const menuNode = (
    <Menu
      mode="inline"
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
    <ConfigProvider locale={zhCN} theme={themeConfig}>
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
              <div style={{ padding: '8px 10px', borderTop: '1px solid #f1f5f9' }}>
                <AccountMenu onOpenProfile={() => navigate('/me')} />
              </div>
              {footerNode}
            </Sider>
          </motion.div>

          <header className="app-topbar">
            <Button type="text" icon={<MenuOutlined />} aria-label="打开导航菜单" onClick={() => setDrawerOpen(true)} />
            <div className="app-topbar-title" style={{ fontWeight: 700, color: '#0f172a' }}>RAG智慧教育</div>
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
            <div style={{ flex: 1, padding: '8px 8px', overflowY: 'auto' }}>{menuNode}</div>
            <div style={{ padding: '8px 10px', borderTop: '1px solid #f1f5f9' }}>
              <AccountMenu onOpenProfile={() => { setDrawerOpen(false); navigate('/me'); }} />
            </div>
          </Drawer>

          <div className={isNarrow ? 'app-content app-content-narrow' : 'app-content'}
               style={{ flex: 1, minWidth: 0, minHeight: '100vh', display: 'flex', flexDirection: 'column' }}>
            <SystemStatusBanner />
            <div style={{ flex: 1, minWidth: 0 }}>
              <AnimatePresence mode="sync">
                <motion.div key={location.pathname} initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} transition={{ duration: 0.12 }}>
                  <Suspense fallback={<PageFallback />}>
                    <Routes location={location}>
                      <Route path="/" element={<Navigate to="/student" replace />} />
                      <Route path="/student" element={<StudentLearningPage />} />
                      <Route path="/student/materials" element={<MaterialsPage />} />
                      <Route path="/student/tasks" element={<TasksPage />} />
                      <Route path="/me" element={<ProfilePage />} />
                      <Route path="/teacher" element={<TeacherPage onNavigate={navigate} />} />
                      <Route path="/settings" element={<SettingsPage />} />
                      {/* 旧网址兼容：功能都有明确的新入口，不留下找不到的功能 */}
                      <Route path="/chat" element={<Navigate to="/student" replace />} />
                      <Route path="/knowledge" element={<Navigate to="/student/materials" replace />} />
                      <Route path="/student/courses" element={<Navigate to="/student/materials" replace />} />
                      <Route path="/student/todo" element={<Navigate to="/student/tasks" replace />} />
                      <Route path="/student/assignments" element={<Navigate to="/student/tasks" replace />} />
                      <Route path="/learning" element={<Navigate to="/student/tasks" replace />} />
                      <Route path="*" element={<Navigate to="/student" replace />} />
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
