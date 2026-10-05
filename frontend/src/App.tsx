import React, { Suspense, lazy } from 'react';
import { BrowserRouter as Router, Routes, Route, useLocation, useNavigate } from 'react-router-dom';
import { Layout, Menu, ConfigProvider, Drawer, Button, Space, Spin, Typography, Avatar } from 'antd';
import { motion, AnimatePresence } from 'framer-motion';
import {
  MessageOutlined,
  DatabaseOutlined,
  SettingOutlined,
  ReadOutlined,
  TeamOutlined,
  MenuOutlined,
  CloseOutlined,
  UserOutlined,
} from '@ant-design/icons';
import zhCN from 'antd/locale/zh_CN';
// 路由级懒加载（2026-10-02）
//
// 此前所有页面都是静态导入：构建出的首屏 JS 达 3.94MB（gzip 1.30MB），
// vite 直接给出 "chunks are larger than 500 kB" 警告——移动网络首次打开很慢。
// 改成 lazy 之后主包只留框架与公共依赖，各页面按需加载。
const ChatPage = lazy(() => import('./pages/ChatPage'));
const KnowledgePage = lazy(() => import('./pages/KnowledgePage'));
const SettingsPage = lazy(() => import('./pages/SettingsPage'));
const LearningPlanPage = lazy(() => import('./pages/LearningPlanPage'));
const StudentPage = lazy(() => import('./pages/StudentPage'));
const TeacherPage = lazy(() => import('./pages/TeacherPage'));
import UserAuth, { AUTH_CHANGED_EVENT } from './components/UserAuth';
import SystemStatusBanner from './components/SystemStatusBanner';
import { isLoggedIn, me } from './api/auth';
import { useIsNarrow } from './hooks/useIsNarrow';

const { Sider } = Layout;
const { Text } = Typography;

// 侧边栏菜单配置（V2.5.1：教学辅助整合进 AI学习助手）
// 阶段一：新增「我的学习」——今日任务 / 错题本 / 复习 / 掌握度的学生入口
const sidebarItems = [
  /* 主导航只保留三条学习主线 + 设置（2026-10-05）：
     学习首页 / 我的课程 / AI 学习助手。
     学习计划不再单列——它已是「学习首页 → 计划与时间」的次级入口；
     知识库是管理型功能，入口放在「设置 → 课程知识库」。
     不再用"（次级）"这种标签当降级手段，而是让人物/入口各归其位。 */
  { key: '/student',              icon: <ReadOutlined />,     label: '学习首页' },
  { key: '/student?tab=courses',  icon: <DatabaseOutlined />, label: '我的课程' },
  { key: '/chat',                 icon: <MessageOutlined />,  label: '学习助手' },
  // 教师工作台：仅在后端返回的 role 为 teacher/admin 时显示（**显示不等于权限**，
  // 真正的越权拦截一律在后端；直接输入 /teacher 也会被后端拒绝）
  { key: '/teacher',              icon: <TeamOutlined />,     label: '教师工作台', teacherOnly: true },
  { key: '/settings',             icon: <SettingOutlined />,  label: '设置' },
];

/** 懒加载页面时的占位，避免切换路由白屏 */
const PageFallback: React.FC = () => (
  <div style={{ display: 'flex', justifyContent: 'center', padding: 48 }}>
    <Spin />
  </div>
);

const App: React.FC = () => {
  const location = useLocation();
  const navigate = useNavigate();
  const isNarrow = useIsNarrow();
  // 角色来自后端（/auth/me）；只用于决定显示哪些入口
  const [role, setRole] = React.useState<string>('student');
  const [account, setAccount] = React.useState<{ name: string } | null>(null);
  const [drawerOpen, setDrawerOpen] = React.useState(false);

  React.useEffect(() => {
    let alive = true;
    const load = async () => {
      if (!isLoggedIn()) {
        if (alive) { setRole('student'); setAccount(null); }
        return;
      }
      try {
        const res: any = await me();
        if (!alive) return;
        setRole(res?.data?.role || 'student');
        setAccount({ name: res?.data?.display_name || res?.data?.username || '已登录' });
      } catch {
        if (alive) { setRole('student'); setAccount(null); }
      }
    };
    load();
    window.addEventListener(AUTH_CHANGED_EVENT, load);
    return () => {
      alive = false;
      window.removeEventListener(AUTH_CHANGED_EVENT, load);
    };
  }, []);

  // 切页面后自动收起抽屉（否则点完菜单还挡着内容）
  React.useEffect(() => { setDrawerOpen(false); }, [location.pathname]);

  const canSeeTeacher = role === 'teacher' || role === 'admin';
  const visibleItems = sidebarItems.filter(
    (item: any) => !item.teacherOnly || canSeeTeacher,
  );

  const getSelectedKey = () => {
    const path = location.pathname;
    if (path.startsWith('/teacher')) return '/teacher';
    if (path.startsWith('/student')) {
      /* 学习首页与我的课程共用 /student 路由：用 ?tab= 区分，否则
         /student?tab=courses 也会高亮「学习首页」（2026-10-04 修复） */
      const tab = new URLSearchParams(location.search).get('tab');
      return tab === 'courses' ? '/student?tab=courses' : '/student';
    }
    if (path.startsWith('/chat')) return '/chat';
    if (path.startsWith('/knowledge')) return '/knowledge';
    if (path.startsWith('/learning')) return '/learning';
    if (path.startsWith('/settings')) return '/settings';
    return '/student';
  };

  // ---- 导航内容（侧边栏与手机抽屉共用同一份，避免两处菜单走样）----
  /**
   * 品牌区。完整名称较长，侧边栏/抽屉里换行展示，不硬挤一行；
   * 窄屏顶栏用短名（见下方 app-topbar-title）。
   */
  const brandNode = (compact = false) => (
    <div style={{ display: 'flex', alignItems: 'center', gap: 12, minWidth: 0 }}>
      <div
        style={{
          width: 38, height: 38, borderRadius: 11,
          background: 'linear-gradient(135deg, #0f766e 0%, #14b8a6 60%, #5eead4 100%)',
          display: 'flex', alignItems: 'center', justifyContent: 'center',
          color: '#fff', fontWeight: 800, fontSize: 17, flexShrink: 0,
          boxShadow: '0 3px 12px rgba(15,118,110,0.3)',
        }}
      >
        智
      </div>
      <div style={{ minWidth: 0 }}>
        <div style={{
          fontWeight: 700, fontSize: compact ? 15 : 13, color: '#0f172a',
          letterSpacing: '-0.02em', lineHeight: 1.35, wordBreak: 'break-word',
        }}>
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
      items={visibleItems.map((item) => ({
        ...item,
        style: {
          marginBottom: 2,
          borderRadius: 10,
          fontSize: 14,
          fontWeight: getSelectedKey() === item.key ? 600 : 450,
          transition: 'all 0.2s cubic-bezier(0.16, 1, 0.3, 1)',
        },
      }))}
    />
  );

  const footerNode = (
    <div style={{ padding: '12px 20px', borderTop: '1px solid #f1f5f9', textAlign: 'center' }}>
      <div style={{ fontSize: 11, color: '#cbd5e1', letterSpacing: '0.02em' }}>RAG × Langchain</div>
      <div style={{ fontSize: 10, color: '#e2e8f0', marginTop: 2 }}>v2.3.1</div>
    </div>
  );

  return (
    <ConfigProvider
      locale={zhCN}
      theme={{
        token: {
          colorPrimary: '#0f766e',
          colorPrimaryBg: '#f0fdfa',
          colorLink: '#0f766e',
          colorLinkHover: '#0d9488',
          colorSuccess: '#059669',
          colorWarning: '#d97706',
          colorError: '#dc2626',
          colorBgContainer: '#ffffff',
          colorBgLayout: '#f8fafc',
          colorBgElevated: '#ffffff',
          colorText: '#0f172a',
          colorTextSecondary: '#475569',
          colorTextTertiary: '#94a3b8',
          colorBorder: '#e2e8f0',
          colorBorderSecondary: '#f1f5f9',
          borderRadius: 10,
          borderRadiusLG: 14,
          fontFamily: "'Geist', 'Satoshi', -apple-system, BlinkMacSystemFont, 'PingFang SC', 'Microsoft YaHei', sans-serif",
          fontSize: 14,
          controlHeight: 38,
        },
        components: {
          Menu: {
            /* 浅色底上的可读性（2026-10-04 人工测试反馈：悬浮时文字变白、与浅色背景糊在一起）。
               原实现把 `itemHoverColor` 设成了近白色 #f0fdfa——那是"背景色"却被当成
               文字/图标色，于是悬浮反而看不见。现在：默认深字、悬浮深青字 + 浅青底、
               选中深青字 + 更明显的浅青底，并显式给出 active/focus 底色。 */
            itemColor: '#0f172a',
            itemHoverColor: '#0f766e',
            itemHoverBg: '#f0fdfa',
            itemSelectedColor: '#0f766e',
            itemSelectedBg: '#ccfbf1',
            itemActiveBg: '#ccfbf1',
            horizontalItemSelectedColor: '#0f766e',
            itemHeight: 42,
          },
          Card: { borderRadiusLG: 14, boxShadowTertiary: '0 1px 3px rgba(0,0,0,0.04), 0 1px 2px rgba(0,0,0,0.06)' },
          Button: { borderRadius: 8, controlHeight: 38 },
          Input: { borderRadius: 8, controlHeight: 38 },
          Select: { borderRadius: 8, controlHeight: 38 },
          Modal: { borderRadiusLG: 16 },
          Table: { borderRadius: 12, headerBg: '#f8fafc', rowHoverBg: '#f0fdfa' },
          Tag: { borderRadiusSM: 6 },
          Tabs: { inkBarColor: '#0f766e', itemActiveColor: '#0f766e', itemSelectedColor: '#0f766e', itemHoverColor: '#0d9488' },
        },
      }}
    >
      <Layout style={{ minHeight: '100vh', background: '#f8fafc' }}>
        {/* 侧边栏（≤900px 由 CSS 隐藏，改用顶栏 + 抽屉导航） */}
        <motion.div
          className="app-sider-wrap"
          initial={{ x: -60, opacity: 0 }}
          animate={{ x: 0, opacity: 1 }}
          transition={{ type: 'spring', stiffness: 180, damping: 22 }}
          style={{ display: 'flex', flexDirection: 'column', width: 220, flexShrink: 0 }}
        >
          <Sider
            className="app-sider"
            width={220}
            style={{
              background: '#ffffff',
              borderRight: '1px solid #e2e8f0',
              height: '100vh',
              position: 'fixed',
              left: 0,
              top: 0,
              bottom: 0,
              zIndex: 100,
              display: 'flex',
              flexDirection: 'column',
              overflow: 'hidden',
            }}
          >
            <div style={{ padding: '20px 20px 16px', borderBottom: '1px solid #f1f5f9' }}>
              {brandNode()}
            </div>
            <div style={{ flex: 1, padding: '8px 10px', overflowY: 'auto' }}>{menuNode}</div>
            <div style={{ padding: '10px 14px', borderTop: '1px solid #f1f5f9' }}>
              <UserAuth />
            </div>
            {footerNode}
          </Sider>
        </motion.div>

        {/* 窄屏顶栏：菜单按钮 + 品牌 + 登录入口（手机上没有它就没有任何导航与登录入口） */}
        <header className="app-topbar">
          <Button
            type="text"
            icon={<MenuOutlined />}
            aria-label="打开导航菜单"
            onClick={() => setDrawerOpen(true)}
          />
          <div className="app-topbar-title" style={{ fontWeight: 700, color: '#0f172a' }}>
            RAG智慧教育
          </div>
          <div style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: 8 }}>
            {account ? (
              <Space size={6}>
                <Avatar size={24} icon={<UserOutlined />} style={{ backgroundColor: '#0f766e' }} />
                <Text style={{ maxWidth: 96, fontSize: 13 }} ellipsis>{account.name}</Text>
              </Space>
            ) : (
              <UserAuth />
            )}
          </div>
        </header>

        {/* 窄屏抽屉导航 */}
        <Drawer
          placement="left"
          open={drawerOpen}
          onClose={() => setDrawerOpen(false)}
          width={260}
          closable={false}
          styles={{ body: { padding: 0, display: 'flex', flexDirection: 'column' } }}
          title={null}
        >
          <div style={{ display: 'flex', alignItems: 'center', padding: '14px 16px', borderBottom: '1px solid #f1f5f9' }}>
            {brandNode()}
            <Button
              type="text"
              icon={<CloseOutlined />}
              aria-label="关闭导航菜单"
              style={{ marginLeft: 'auto' }}
              onClick={() => setDrawerOpen(false)}
            />
          </div>
          <div style={{ flex: 1, padding: '8px 10px', overflowY: 'auto' }}>{menuNode}</div>
          <div style={{ padding: '10px 14px', borderTop: '1px solid #f1f5f9' }}>
            <UserAuth />
          </div>
        </Drawer>

        {/* 主内容区 */}
        <div
          className={isNarrow ? 'app-content app-content-narrow' : 'app-content'}
          style={{ flex: 1, minWidth: 0, minHeight: '100vh', display: 'flex', flexDirection: 'column' }}
        >
          {/* 外部能力降级提示：平时不占位，降级时才出现 */}
          <SystemStatusBanner />

          {/* 页面内容 */}
          <div style={{ flex: 1, minWidth: 0 }}>
            <AnimatePresence mode="sync">
              <motion.div
                key={location.pathname}
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                exit={{ opacity: 0 }}
                transition={{ duration: 0.12 }}
              >
                <Suspense fallback={<PageFallback />}>
                  <Routes location={location}>
                    <Route path="/" element={<StudentPage />} />
                    <Route path="/student" element={<StudentPage />} />
                    <Route path="/teacher" element={<TeacherPage onNavigate={navigate} />} />
                    <Route path="/chat" element={<ChatPage />} />
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
    </ConfigProvider>
  );
};

const AppWithRouter: React.FC = () => (
  <Router>
    <App />
  </Router>
);

export default AppWithRouter;
