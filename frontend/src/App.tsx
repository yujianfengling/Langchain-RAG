import React from 'react';
import { BrowserRouter as Router, Routes, Route, Link, useLocation } from 'react-router-dom';
import { Layout, Menu, Typography, Space, ConfigProvider } from 'antd';
import { motion, AnimatePresence } from 'framer-motion';
import {
  MessageOutlined,
  DatabaseOutlined,
  SettingOutlined,
  FunctionOutlined,
  CalendarOutlined,
} from '@ant-design/icons';
import zhCN from 'antd/locale/zh_CN';
import ChatPage from './pages/ChatPage';
import KnowledgePage from './pages/KnowledgePage';
import SettingsPage from './pages/SettingsPage';
import EducationPage from './pages/EducationPage';
import LearningPlanPage from './pages/LearningPlanPage';

const { Header, Content, Footer } = Layout;
const { Title } = Typography;

// 菜单项配置
const menuItems = [
  {
    key: '/chat',
    icon: <MessageOutlined />,
    label: <Link to="/chat">AI问答</Link>,
  },
  {
    key: '/knowledge',
    icon: <DatabaseOutlined />,
    label: <Link to="/knowledge">知识库</Link>,
  },
  {
    key: '/education',
    icon: <FunctionOutlined />,
    label: <Link to="/education">教学辅助</Link>,
  },
  {
    key: '/learning',
    icon: <CalendarOutlined />,
    label: <Link to="/learning">学习计划</Link>,
  },
  {
    key: '/settings',
    icon: <SettingOutlined />,
    label: <Link to="/settings">设置</Link>,
  },
];

// 页面切换动画配置（taste-skill: Spring Physics）
const pageTransition = {
  initial: { opacity: 0, y: 10 },
  animate: { opacity: 1, y: 0 },
  exit: { opacity: 0, y: -6 },
  transition: { type: 'spring' as const, stiffness: 100, damping: 20 },
};

const App: React.FC = () => {
  const location = useLocation();

  // 获取当前选中的菜单项
  const getSelectedKey = () => {
    const path = location.pathname;
    if (path.startsWith('/chat')) return '/chat';
    if (path.startsWith('/knowledge')) return '/knowledge';
    if (path.startsWith('/education')) return '/education';
    if (path.startsWith('/learning')) return '/learning';
    if (path.startsWith('/settings')) return '/settings';
    return '/chat';
  };

  return (
    <ConfigProvider
      locale={zhCN}
      theme={{
        token: {
          // 主色：Teal（高级感，非AI蓝/紫）
          colorPrimary: '#0f766e',
          colorPrimaryBg: '#f0fdfa',
          colorPrimaryBgHover: '#ccfbf1',
          colorPrimaryBorder: '#99f6e4',
          colorPrimaryBorderHover: '#5eead4',
          colorPrimaryHover: '#0d9488',
          colorPrimaryActive: '#0f766e',
          colorPrimaryTextHover: '#0d9488',
          colorPrimaryText: '#0f766e',
          colorPrimaryTextActive: '#0f766e',
          // 辅助色
          colorSuccess: '#059669',
          colorWarning: '#d97706',
          colorError: '#dc2626',
          colorInfo: '#0ea5e9',
          // 背景
          colorBgContainer: '#ffffff',
          colorBgLayout: '#f8fafc',
          colorBgElevated: '#ffffff',
          colorBgSpotlight: '#f1f5f9',
          // 文字
          colorText: '#0f172a',
          colorTextSecondary: '#475569',
          colorTextTertiary: '#94a3b8',
          colorTextQuaternary: '#cbd5e1',
          // 边框
          colorBorder: '#e2e8f0',
          colorBorderSecondary: '#f1f5f9',
          // 圆角
          borderRadius: 10,
          borderRadiusLG: 14,
          borderRadiusSM: 6,
          borderRadiusXS: 4,
          // 字体
          fontFamily: "'Geist', 'Satoshi', -apple-system, BlinkMacSystemFont, 'PingFang SC', 'Hiragino Sans GB', 'Microsoft YaHei', 'Helvetica Neue', sans-serif",
          fontSize: 14,
          fontSizeHeading1: 28,
          fontSizeHeading2: 22,
          fontSizeHeading3: 18,
          fontSizeHeading4: 16,
          fontSizeHeading5: 14,
          // 间距
          padding: 16,
          paddingLG: 24,
          paddingSM: 12,
          paddingXS: 8,
          marginLG: 24,
          marginSM: 12,
          // 控件
          controlHeight: 38,
          controlHeightLG: 44,
          controlHeightSM: 30,
          // 阴影
          boxShadow: '0 1px 3px rgba(0,0,0,0.04), 0 1px 2px rgba(0,0,0,0.06)',
          boxShadowSecondary: '0 4px 12px rgba(0,0,0,0.06)',
          boxShadowTertiary: '0 1px 2px rgba(0,0,0,0.04)',
        },
        components: {
          Menu: {
            itemSelectedColor: '#0f766e',
            itemHoverColor: '#f0fdfa',
            itemSelectedBg: '#f0fdfa',
            horizontalItemSelectedColor: '#0f766e',
            itemHeight: 40,
            iconSize: 16,
          },
          Card: {
            borderRadiusLG: 14,
            borderRadius: 10,
            boxShadowTertiary: '0 1px 3px rgba(0,0,0,0.04), 0 1px 2px rgba(0,0,0,0.06)',
            headerFontSize: 15,
          },
          Button: {
            borderRadius: 8,
            controlHeight: 38,
            controlHeightLG: 44,
            controlHeightSM: 30,
            fontWeight: 500,
          },
          Input: {
            borderRadius: 8,
            controlHeight: 38,
          },
          Select: {
            borderRadius: 8,
            controlHeight: 38,
          },
          Modal: {
            borderRadiusLG: 16,
            paddingLG: 24,
          },
          Table: {
            borderRadius: 12,
            headerBg: '#f8fafc',
            headerColor: '#475569',
            rowHoverBg: '#f0fdfa',
          },
          Tag: {
            borderRadiusSM: 6,
          },
          Message: {
            borderRadiusLG: 10,
          },
          Notification: {
            borderRadiusLG: 12,
          },
          Tabs: {
            inkBarColor: '#0f766e',
            itemActiveColor: '#0f766e',
            itemSelectedColor: '#0f766e',
            itemHoverColor: '#0d9488',
          },
          Switch: {
            colorPrimary: '#0f766e',
          },
          Checkbox: {
            colorPrimary: '#0f766e',
          },
          Radio: {
            colorPrimary: '#0f766e',
          },
          Progress: {
            colorSuccess: '#059669',
          },
        },
      }}
    >
      <Layout style={{ minHeight: '100vh', background: '#f8fafc' }}>
        {/* 顶部导航栏 */}
        <motion.div
          initial={{ opacity: 0, y: -10 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ type: 'spring', stiffness: 100, damping: 20 }}
        >
          <Header style={{ 
            background: 'rgba(255,255,255,0.85)',
            backdropFilter: 'blur(12px) saturate(180%)',
            WebkitBackdropFilter: 'blur(12px) saturate(180%)',
            padding: '0 32px',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            boxShadow: '0 1px 0 rgba(0,0,0,0.04)',
            zIndex: 1000,
            position: 'sticky',
            top: 0,
            borderBottom: '1px solid rgba(226,232,240,0.6)',
          }}>
            <Space size={14}>
              <motion.div
                whileHover={{ scale: 1.05, rotate: 3 }}
                whileTap={{ scale: 0.95 }}
                transition={{ type: 'spring', stiffness: 200, damping: 15 }}
                style={{
                  width: 36,
                  height: 36,
                  borderRadius: 10,
                  background: 'linear-gradient(135deg, #0f766e 0%, #14b8a6 50%, #5eead4 100%)',
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  color: '#fff',
                  fontWeight: '700',
                  fontSize: 16,
                  letterSpacing: '-0.03em',
                  cursor: 'default',
                  boxShadow: '0 2px 8px rgba(15,118,110,0.25)',
                }}
              >
                智
              </motion.div>
              <div>
                <Title level={5} style={{ margin: 0, color: '#0f172a', fontWeight: 700, letterSpacing: '-0.02em', fontSize: 16 }}>
                  RAG智慧教育
                </Title>
                <div style={{ fontSize: 11, color: '#94a3b8', marginTop: -2, letterSpacing: '0.02em' }}>
                  检索增强生成 · 智能问答
                </div>
              </div>
            </Space>
            
            <Menu
              mode="horizontal"
              selectedKeys={[getSelectedKey()]}
              items={menuItems}
              style={{ 
                border: 'none',
                background: 'transparent',
                minWidth: '300px',
                justifyContent: 'flex-end',
                fontWeight: 500,
              }}
            />
          </Header>
        </motion.div>

        {/* 主要内容区域 */}
        <Content style={{ 
          background: '#f8fafc',
          overflow: 'hidden',
          minHeight: 'calc(100vh - 140px)',
        }}>
          <AnimatePresence mode="wait">
            <motion.div
              key={location.pathname}
              initial={pageTransition.initial}
              animate={pageTransition.animate}
              exit={pageTransition.exit}
              transition={pageTransition.transition}
              style={{ minHeight: 'calc(100vh - 140px)' }}
            >
              <Routes location={location}>
                <Route path="/" element={<ChatPage />} />
                <Route path="/chat" element={<ChatPage />} />
                <Route path="/knowledge" element={<KnowledgePage />} />
                <Route path="/education" element={<EducationPage />} />
                <Route path="/learning" element={<LearningPlanPage />} />
                <Route path="/settings" element={<SettingsPage />} />
              </Routes>
            </motion.div>
          </AnimatePresence>
        </Content>

        {/* 底部信息 */}
        <motion.div
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          transition={{ delay: 0.3, duration: 0.5 }}
        >
          <Footer style={{ 
            textAlign: 'center',
            background: 'rgba(255,255,255,0.7)',
            backdropFilter: 'blur(8px)',
            borderTop: '1px solid rgba(226,232,240,0.5)',
            padding: '12px 24px',
          }}>
            <Space size={8}>
              <span style={{ color: '#64748b', fontSize: 13, letterSpacing: '-0.01em' }}>RAG与Langchain融合驱动的智慧教育系统</span>
              <span style={{ color: '#e2e8f0' }}>·</span>
              <span style={{ color: '#94a3b8', fontSize: 13 }}>基于检索增强生成技术</span>
            </Space>
          </Footer>
        </motion.div>
      </Layout>
    </ConfigProvider>
  );
};

// 包装App组件以使用Router
const AppWithRouter: React.FC = () => (
  <Router>
    <App />
  </Router>
);

export default AppWithRouter;
