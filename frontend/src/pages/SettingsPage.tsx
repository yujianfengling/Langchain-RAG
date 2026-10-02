import React from 'react';
import { Layout, Typography, Space, Card } from 'antd';
import { SettingOutlined, InfoCircleOutlined, UserOutlined } from '@ant-design/icons';
import UserProfileDashboard from '../components/UserProfileDashboard';
import PersonalizationSettings from '../components/PersonalizationSettings';
import SpeechSettings from '../components/SpeechSettings';
import TeacherActivation from '../components/TeacherActivation';
import SubjectSelector from '../components/SubjectSelector';
import SystemStatusPanel from '../components/SystemStatusPanel';
import { getHealth } from '../api/health';

const { Header, Content } = Layout;
const { Title, Text, Paragraph } = Typography;

const SettingsPage: React.FC = () => {
  const [selectedSubject, setSelectedSubject] = React.useState<string>('');
  // 版本从后端取，不要在页面里硬编码（此前写死 1.0.0，而实际已是 2.2.1）
  const [version, setVersion] = React.useState<string>('');

  React.useEffect(() => {
    let alive = true;
    getHealth()
      .then((info) => { if (alive) setVersion(info?.version || ''); })
      .catch(() => { /* 后端未启动时不显示版本，也不弹错打扰用户 */ });
    return () => { alive = false; };
  }, []);

  return (
    <Layout style={{ height: '100%' }}>
      <Header style={{ 
        background: '#fff',
        padding: '0 24px',
        borderBottom: '1px solid #f0f0f0',
        display: 'flex',
        alignItems: 'center',
      }}>
        <Space>
          <SettingOutlined style={{ fontSize: '20px', color: '#0f766e' }} />
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
          {/* 学习仪表盘 */}
          <div style={{ marginBottom: 24 }}>
            <UserProfileDashboard userId="default_user" subjectId={selectedSubject} />
          </div>

          {/* 个性化设置 */}
          <div style={{ marginBottom: 24 }}>
            <PersonalizationSettings userId="default_user" subjectId={selectedSubject} />
          </div>

          {/* 语音设置（语音输入/本地 ASR；回答朗读已移除，2026-10-01） */}
          <div style={{ marginBottom: 24 }}>
            <TeacherActivation />

        <SpeechSettings />
          </div>

          {/* 学科选择 */}
          <div style={{ marginBottom: 24 }}>
            <Title level={5} style={{ marginBottom: '16px' }}>
              <UserOutlined /> 学习学科
            </Title>
            <SubjectSelector value={selectedSubject} onChange={setSelectedSubject} />
          </div>

          {/* 运行状态：外部能力与当日用量（观测数据的用户可见入口） */}
          <div style={{ marginBottom: 24 }}>
            <SystemStatusPanel />
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
                  <Text>{version || '—'}</Text>
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