import React from 'react';
import { BrowserRouter as Router, Routes, Route, useLocation, useNavigate } from 'react-router-dom';
import { Layout, Menu, ConfigProvider } from 'antd';
import { motion, AnimatePresence } from 'framer-motion';
import {
  MessageOutlined,
  DatabaseOutlined,
  SettingOutlined,
  CalendarOutlined,
} from '@ant-design/icons';
import zhCN from 'antd/locale/zh_CN';
import ChatPage from './pages/ChatPage';
import KnowledgePage from './pages/KnowledgePage';
import SettingsPage from './pages/SettingsPage';
import LearningPlanPage from './pages/LearningPlanPage';

const { Sider } = Layout;

// 侧边栏菜单配置（V2.5.1：教学辅助整合进 AI学习助手）
const sidebarItems = [
  { key: '/chat',       icon: <MessageOutlined />,  label: 'AI学习助手' },
  { key: '/knowledge',  icon: <DatabaseOutlined />, label: '知识库' },
  { key: '/learning',   icon: <CalendarOutlined />, label: '学习计划' },
  { key: '/settings',   icon: <SettingOutlined />,  label: '设置' },
];

const App: React.FC = () => {
  const location = useLocation();
  const navigate = useNavigate();

  const getSelectedKey = () => {
    const path = location.pathname;
    if (path.startsWith('/chat')) return '/chat';
    if (path.startsWith('/knowledge')) return '/knowledge';
    if (path.startsWith('/learning')) return '/learning';
    if (path.startsWith('/settings')) return '/settings';
    return '/chat';
  };

  return (
    <ConfigProvider
      locale={zhCN}
      theme={{
        token: {
          colorPrimary: '#0f766e',
          colorPrimaryBg: '#f0fdfa',
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
            itemSelectedColor: '#0f766e',
            itemHoverColor: '#f0fdfa',
            itemSelectedBg: '#f0fdfa',
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
        {/* 侧边栏 */}
        <motion.div
          initial={{ x: -60, opacity: 0 }}
          animate={{ x: 0, opacity: 1 }}
          transition={{ type: 'spring', stiffness: 100, damping: 20 }}
          style={{ display: 'flex', flexDirection: 'column', width: 220, flexShrink: 0 }}
        >
          <Sider
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
            {/* Logo 区域 */}
            <motion.div
              initial={{ opacity: 0, y: -10 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ delay: 0.1, type: 'spring', stiffness: 100, damping: 20 }}
              style={{
                padding: '20px 20px 16px',
                borderBottom: '1px solid #f1f5f9',
              }}
            >
              <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
                <motion.div
                  whileHover={{ scale: 1.08, rotate: 5 }}
                  whileTap={{ scale: 0.92 }}
                  transition={{ type: 'spring', stiffness: 200, damping: 15 }}
                  style={{
                    width: 38,
                    height: 38,
                    borderRadius: 11,
                    background: 'linear-gradient(135deg, #0f766e 0%, #14b8a6 60%, #5eead4 100%)',
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    color: '#fff',
                    fontWeight: '800',
                    fontSize: 17,
                    cursor: 'default',
                    boxShadow: '0 3px 12px rgba(15,118,110,0.3)',
                    flexShrink: 0,
                  }}
                >
                  智
                </motion.div>
                <div>
                  <div style={{ fontWeight: 700, fontSize: 15, color: '#0f172a', letterSpacing: '-0.02em', lineHeight: 1.2 }}>
                    RAG智慧教育
                  </div>
                  <div style={{ fontSize: 11, color: '#94a3b8', marginTop: 2, letterSpacing: '0.01em' }}>
                    检索增强 · 智能问答
                  </div>
                </div>
              </div>
            </motion.div>

            {/* 导航菜单 */}
            <div style={{ flex: 1, padding: '8px 10px', overflowY: 'auto' }}>
              <Menu
                mode="inline"
                selectedKeys={[getSelectedKey()]}
                onClick={({ key }) => navigate(key)}
                style={{
                  border: 'none',
                  background: 'transparent',
                }}
                items={sidebarItems.map((item) => ({
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
            </div>

            {/* 底部品牌 */}
            <motion.div
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              transition={{ delay: 0.5, duration: 0.5 }}
              style={{
                padding: '12px 20px',
                borderTop: '1px solid #f1f5f9',
                textAlign: 'center',
              }}
            >
              <div style={{ fontSize: 11, color: '#cbd5e1', letterSpacing: '0.02em' }}>
                RAG × Langchain
              </div>
              <div style={{ fontSize: 10, color: '#e2e8f0', marginTop: 2 }}>
                v2.3.1
              </div>
            </motion.div>
          </Sider>
        </motion.div>

        {/* 主内容区 */}
        <div style={{ flex: 1, minHeight: '100vh', display: 'flex', flexDirection: 'column' }}>
          {/* 页面内容 */}
          <div style={{ flex: 1, overflow: 'hidden' }}>
            <AnimatePresence mode="wait">
              <motion.div
                key={location.pathname}
                initial={{ opacity: 0, y: 10 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, y: -6 }}
                transition={{ type: 'spring', stiffness: 100, damping: 20 }}
                style={{ height: '100%' }}
              >
                <Routes location={location}>
                  <Route path="/" element={<ChatPage />} />
                  <Route path="/chat" element={<ChatPage />} />
                  <Route path="/knowledge" element={<KnowledgePage />} />
                  <Route path="/learning" element={<LearningPlanPage />} />
                  <Route path="/settings" element={<SettingsPage />} />
                </Routes>
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
