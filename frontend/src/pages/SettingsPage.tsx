import React from 'react';
import { Layout, Typography, Space, Card, Divider } from 'antd';
import { SettingOutlined, CloudServerOutlined, InfoCircleOutlined } from '@ant-design/icons';
import ModelSelector from '../components/ModelSelector';

const { Header, Content } = Layout;
const { Title, Text, Paragraph } = Typography;

const SettingsPage: React.FC = () => {
  return (
    <Layout style={{ height: '100vh' }}>
      <Header style={{ 
        background: '#fff',
        padding: '0 24px',
        borderBottom: '1px solid #f0f0f0',
        display: 'flex',
        alignItems: 'center',
      }}>
        <Space>
          <SettingOutlined style={{ fontSize: '20px', color: '#1890ff' }} />
          <Title level={4} style={{ margin: 0 }}>
            系统设置
          </Title>
        </Space>
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
          {/* 模型管理部分 */}
          <div style={{ marginBottom: '32px' }}>
            <Title level={5} style={{ marginBottom: '16px' }}>
              <CloudServerOutlined /> 模型管理
            </Title>
            <Text type="secondary">
              选择和管理AI模型。不同的模型可能在回答质量、速度等方面有所差异。
              您可以根据需要切换不同的模型提供商。
            </Text>
            
            <Divider />
            
            <ModelSelector />
          </div>
          
          {/* 系统信息部分 */}
          <div>
            <Title level={5} style={{ marginBottom: '16px' }}>
              <InfoCircleOutlined /> 系统信息
            </Title>
            
            <Card size="small">
              <Space direction="vertical" style={{ width: '100%' }}>
                <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                  <Text>系统名称</Text>
                  <Text>智慧教育RAG系统</Text>
                </div>
                <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                  <Text>版本</Text>
                  <Text>1.0.0</Text>
                </div>
                <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                  <Text>API文档</Text>
                  <Text>
                    <a href="/docs" target="_blank" rel="noopener noreferrer">
                      查看API文档
                    </a>
                  </Text>
                </div>
                <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                  <Text>健康检查</Text>
                  <Text>
                    <a href="/health" target="_blank" rel="noopener noreferrer">
                      检查系统状态
                    </a>
                  </Text>
                </div>
              </Space>
            </Card>
            
            <Paragraph type="secondary" style={{ marginTop: '16px', fontSize: '12px' }}>
              智慧教育RAG系统是一个基于检索增强生成技术的教育知识问答系统。
              系统支持多学科知识库管理，提供智能问答服务，并支持多种AI模型切换。
            </Paragraph>
          </div>
        </div>
      </Content>
    </Layout>
  );
};

export default SettingsPage;