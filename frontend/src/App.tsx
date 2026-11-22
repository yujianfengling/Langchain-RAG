import React, { Suspense, lazy } from 'react';
import { BrowserRouter as Router, Routes, Route, useLocation, Navigate } from 'react-router-dom';
import { Alert, Layout, ConfigProvider, Button, Spin } from 'antd';
import { motion, AnimatePresence, useReducedMotion } from 'framer-motion';
import { ReloadOutlined } from '@ant-design/icons';
import zhCN from 'antd/locale/zh_CN';
import LoginRequired from './components/LoginRequired';
import CompetitionChrome from './competition/shared/CompetitionChrome';
import { CourseProvider } from './context/CourseContext';
import { AuthProvider, useAuth } from './context/AuthContext';
import { themeConfig } from './theme';

/**
 * 路由级懒加载（首屏只留框架与公共依赖）
 */
// 智慧学伴比赛版：三个一级页面 + 账号页。
// 比赛页面在 src/competition/；旧学生端与教师管理页文件保留不删，仅从路由卸载。
const ClassAnalyticsPage = lazy(() => import('./competition/classAnalytics/ClassAnalyticsPage'));
const StudentAnswersPage = lazy(() => import('./competition/studentAnswers/StudentAnswersPage'));
const CurriculumPage = lazy(() => import('./competition/curriculum/CurriculumPage'));

/** 比赛版统一落点：班级学情（老师看完一次测验的第一步）。 */
const COMPETITION_HOME = '/class-analytics';

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
  const reduceMotion = useReducedMotion();
  // 只用路径做过渡键：查询参数（学生/测验/题目/数据源）变化时由页面内部响应式更新，
  // 否则每次改 URL 都会整页重挂载，弹层、已展开的提示与选中状态会被意外清空。
  const routeKey = location.pathname;

  return (
    <div className="comp-stage">
      <AnimatePresence mode="wait" initial={false}>
        <motion.div
          key={routeKey}
          className="comp-route"
          initial={reduceMotion ? false : { opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={reduceMotion ? undefined : { opacity: 0 }}
          transition={{ duration: reduceMotion ? 0 : 0.2, ease: [0.2, 0, 0, 1] }}
        >
          <Suspense fallback={<PageFallback />}>
            <Routes location={location}>
              {/* 智慧学伴比赛版：一级导航三页 + 账号页 */}
              <Route path="/" element={<Navigate to={COMPETITION_HOME} replace />} />
              <Route path="/class-analytics" element={<Protected><ClassAnalyticsPage /></Protected>} />
              <Route path="/student-answers" element={<Protected><StudentAnswersPage /></Protected>} />
              <Route path="/curriculum" element={<Protected><CurriculumPage /></Protected>} />
              {/* 学生端「我的主页 / 设置」不属于比赛版：一并回收，避免老师误入空白个人页。 */}
              <Route path="/me" element={<Navigate to={COMPETITION_HOME} replace />} />
              <Route path="/settings" element={<Navigate to={COMPETITION_HOME} replace />} />
              {/* 旧业务路径统一回收到比赛首页：不留下空白页、残留菜单或循环重定向。
                  旧学生端/教师管理文件保留在仓库里，仅从比赛入口卸载。 */}
              <Route path="/teacher" element={<Navigate to={COMPETITION_HOME} replace />} />
              <Route path="/student" element={<Navigate to={COMPETITION_HOME} replace />} />
              <Route path="/student/materials" element={<Navigate to={COMPETITION_HOME} replace />} />
              <Route path="/student/tasks" element={<Navigate to={COMPETITION_HOME} replace />} />
              <Route path="/student/assignments" element={<Navigate to={COMPETITION_HOME} replace />} />
              <Route path="/student/courses" element={<Navigate to={COMPETITION_HOME} replace />} />
              <Route path="/student/todo" element={<Navigate to={COMPETITION_HOME} replace />} />
              <Route path="/chat" element={<Navigate to={COMPETITION_HOME} replace />} />
              <Route path="/knowledge" element={<Navigate to={COMPETITION_HOME} replace />} />
              <Route path="/learning" element={<Navigate to={COMPETITION_HOME} replace />} />
              <Route path="*" element={<Navigate to={COMPETITION_HOME} replace />} />
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
          }}>伴</div>
          <div style={{ fontWeight: 700, color: '#182033' }}>智慧学伴</div>
          <div className="app-brand-subtitle" style={{ fontSize: 11, color: '#737d8f' }}>考后分析工作台</div>
        </header>
        <div className="comp-login-intro" role="note">
          七年级数学 · 学情诊断与个性化学习工作台 — 请先用教师账号登录，
          查看两班学情、学生答卷与课标教材依据。
        </div>
        <div className="login-scroll">
          <Suspense fallback={<PageFallback />}>
            <AppRoutes />
          </Suspense>
        </div>
      </Layout>
    );
  }

  // 比赛版独立外壳：常显三页导航、明确的数据来源、可完整滚动的内容区。
  // 不挂载大创的 SystemStatusBanner（语音服务 8100 等与本次比赛无关的降级提示）。
  return (
    <CompetitionChrome>
      <AppRoutes />
    </CompetitionChrome>
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
