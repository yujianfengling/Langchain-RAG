import React, { Suspense, lazy } from 'react';
import { BrowserRouter as Router, Routes, Route, useLocation, useNavigate, Navigate } from 'react-router-dom';
import { Alert, Layout, ConfigProvider, Button, Spin } from 'antd';
import { motion, AnimatePresence, useReducedMotion } from 'framer-motion';
import { ReloadOutlined } from '@ant-design/icons';
import zhCN from 'antd/locale/zh_CN';
import AppChrome from './components/AppChrome';
import LoginRequired from './components/LoginRequired';
import SystemStatusBanner from './components/SystemStatusBanner';
import { CourseProvider } from './context/CourseContext';
import { AuthProvider, useAuth } from './context/AuthContext';
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

const AppRoutes: React.FC = () => {
  const location = useLocation();
  const navigate = useNavigate();
  const reduceMotion = useReducedMotion();
  const routeKey = location.pathname + location.search;

  return (
    <div className="app-stage">
      <AnimatePresence mode="wait" initial={false}>
        <motion.div
          key={routeKey}
          className="app-route"
          initial={reduceMotion ? false : { opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={reduceMotion ? undefined : { opacity: 0 }}
          transition={{ duration: reduceMotion ? 0 : 0.2, ease: [0.2, 0, 0, 1] }}
        >
          <Suspense fallback={<PageFallback />}>
            <Routes location={location}>
              <Route path="/" element={<Navigate to={{ pathname: '/student', search: location.search }} replace />} />
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
  );
};

const App: React.FC = () => {
  const { user } = useAuth();

  // 未登录 / 登录校验中：简洁布局（小品牌条 + 居中内容），不渲染工作区菜单。
  // 登录状态校验/失效逻辑仍由 AuthProvider 与 Protected 处理，这里只改呈现。
  if (!user) {
    return (
      <Layout style={{ height: '100dvh', overflow: 'hidden', display: 'flex', flexDirection: 'column', background: '#f8fafc' }}>
        <header style={{
          height: 56, flexShrink: 0, display: 'flex', alignItems: 'center', gap: 11,
          padding: '0 20px', background: '#fff', borderBottom: '1px solid #eef1f5',
        }}>
          <div className="app-brand-mark" style={{
            width: 30, height: 30, display: 'flex', alignItems: 'center', justifyContent: 'center',
            color: '#fff', fontWeight: 750, fontSize: 14, flexShrink: 0,
          }}>知</div>
          <div style={{ fontWeight: 700, color: '#182033' }}>知学</div>
          <div className="app-brand-subtitle" style={{ fontSize: 11, color: '#737d8f' }}>RAG 智慧学习</div>
        </header>
        <div className="login-scroll">
          <SystemStatusBanner />
          <Suspense fallback={<PageFallback />}>
            <AppRoutes />
          </Suspense>
        </div>
      </Layout>
    );
  }

  return (
    <AppChrome>
      <SystemStatusBanner />
      <AppRoutes />
    </AppChrome>
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
