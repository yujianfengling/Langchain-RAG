import React from 'react';
import { Layout, Typography, Space } from 'antd';
import { DatabaseOutlined, CloudUploadOutlined } from '@ant-design/icons';
import KnowledgeManager from '../components/KnowledgeManager';

const { Header, Content } = Layout;
const { Title, Text } = Typography;

const KnowledgePage: React.FC = () => {
  return (
    <Layout style={{ height: '100%' }}>
      <Header style={{ 
        background: '#fff',
        padding: '0 24px',
        borderBottom: '1px solid #f0f0f0',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
      }}>
        <Space>
          <DatabaseOutlined style={{ fontSize: '20px', color: '#0f766e' }} />
          <Title level={4} style={{ margin: 0 }}>
            知识库管理
          </Title>
        </Space>
        
        <Text type="secondary">
          管理和维护系统知识库
        </Text>
      </Header>
      
      <Content style={{ 
        padding: '24px',
        background: '#f5f5f5',
        overflow: 'auto',
      }}>
        <div style={{ 
          background: '#fff',
          borderRadius: '12px',
          padding: '24px',
          boxShadow: '0 2px 8px rgba(0,0,0,0.1)',
        }}>
          <div style={{ marginBottom: '24px' }}>
            <Title level={5} style={{ marginBottom: '8px' }}>
              <CloudUploadOutlined /> 知识库概览
            </Title>
            <Text type="secondary">
              在这里可以管理系统的知识库，包括查看、构建和删除知识库。
              知识库是AI问答系统的基础，包含学科相关的文档和知识。
            </Text>
          </div>
          
          <KnowledgeManager />
        </div>
      </Content>
    </Layout>
  );
};

export default KnowledgePage;