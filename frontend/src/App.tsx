import React from 'react';
import { BrowserRouter as Router, Routes, Route, Link, useLocation } from 'react-router-dom';
import { Layout, Menu, Typography, Space, ConfigProvider } from 'antd';
import { 
  MessageOutlined, 
  DatabaseOutlined, 
  SettingOutlined,
  FunctionOutlined,
} from '@ant-design/icons';
import zhCN from 'antd/locale/zh_CN';
import ChatPage from './pages/ChatPage';
import KnowledgePage from './pages/KnowledgePage';
import SettingsPage from './pages/SettingsPage';
import EducationPage from './pages/EducationPage';

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
    key: '/settings',
    icon: <SettingOutlined />,
    label: <Link to="/settings">设置</Link>,
  },
];

const App: React.FC = () => {
  const location = useLocation();

  // 获取当前选中的菜单项
  const getSelectedKey = () => {
    const path = location.pathname;
    if (path.startsWith('/chat')) return '/chat';
    if (path.startsWith('/knowledge')) return '/knowledge';
    if (path.startsWith('/education')) return '/education';
    if (path.startsWith('/settings')) return '/settings';
    return '/chat';
  };

  return (
    <ConfigProvider
      locale={zhCN}
      theme={{
        token: {
          colorPrimary: '#1677ff',
          borderRadius: 8,
        },
      }}
    >
      <Layout style={{ minHeight: '100vh' }}>
        {/* 顶部导航栏 */}
        <Header style={{ 
          background: '#fff',
          padding: '0 24px',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          boxShadow: '0 2px 8px rgba(0,0,0,0.1)',
          zIndex: 1000,
          position: 'sticky',
          top: 0,
        }}>
          <Space>
            <div style={{
              width: 32,
              height: 32,
              borderRadius: 8,
              background: 'linear-gradient(135deg, #1677ff, #69b1ff)',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              color: '#fff',
              fontWeight: 'bold',
              fontSize: 16,
            }}>
              智
            </div>
            <Title level={4} style={{ margin: 0, color: '#1677ff' }}>
              智慧教育系统
            </Title>
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
            }}
          />
        </Header>

        {/* 主要内容区域 */}
        <Content style={{ 
          background: '#f5f5f5',
          overflow: 'hidden',
        }}>
          <Routes>
            <Route path="/" element={<ChatPage />} />
            <Route path="/chat" element={<ChatPage />} />
            <Route path="/knowledge" element={<KnowledgePage />} />
            <Route path="/education" element={<EducationPage />} />
            <Route path="/settings" element={<SettingsPage />} />
          </Routes>
        </Content>

        {/* 底部信息 */}
        <Footer style={{ 
          textAlign: 'center',
          background: '#fff',
          borderTop: '1px solid #f0f0f0',
          padding: '16px 24px',
        }}>
          <Space>
            <span>智慧教育RAG系统</span>
            <span style={{ color: '#d9d9d9' }}>|</span>
            <span style={{ color: '#999' }}>基于RAG技术的智能教育问答平台</span>
          </Space>
        </Footer>
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