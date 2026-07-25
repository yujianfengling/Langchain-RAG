import React, { useState, useEffect } from 'react';
import { Layout, Typography, Space } from 'antd';
import { MessageOutlined, HistoryOutlined } from '@ant-design/icons';
import ChatBox from '../components/ChatBox';
import SubjectSelector from '../components/SubjectSelector';
import { getSubjects } from '../api/subjects';
import { Subject } from '../types';

const { Header, Sider, Content } = Layout;
const { Title } = Typography;

const ChatPage: React.FC = () => {
  const [selectedSubject, setSelectedSubject] = useState<string>('');
  const [subjects, setSubjects] = useState<Subject[]>([]);

  // 加载学科列表
  useEffect(() => {
    const loadSubjects = async () => {
      try {
        const data = await getSubjects();
        setSubjects(data);
      } catch (error) {
        console.error('加载学科列表失败:', error);
      }
    };
    loadSubjects();
  }, []);

  return (
    <Layout style={{ height: '100vh' }}>
      {/* 左侧历史对话栏 */}
      <Sider 
        width={300} 
        style={{ 
          background: '#fff',
          borderRight: '1px solid #f0f0f0',
          overflow: 'auto',
        }}
      >
        <div style={{ padding: '24px' }}>
          <Title level={4} style={{ marginBottom: '24px' }}>
            <HistoryOutlined /> 历史对话
          </Title>
          
          {/* 学科选择器 */}
          <div style={{ marginBottom: '24px' }}>
            <SubjectSelector
              value={selectedSubject}
              onChange={setSelectedSubject}
              style={{ width: '100%' }}
            />
          </div>
          
          {/* 历史对话列表 */}
          <div style={{ 
            background: '#fafafa',
            borderRadius: '8px',
            padding: '16px',
            minHeight: '300px',
          }}>
            <div style={{ 
              textAlign: 'center', 
              color: '#999',
              padding: '40px 0',
            }}>
              <MessageOutlined style={{ fontSize: '32px', marginBottom: '16px' }} />
              <div>暂无历史对话</div>
              <div style={{ fontSize: '12px', marginTop: '8px' }}>
                选择学科后开始提问
              </div>
            </div>
          </div>
        </div>
      </Sider>

      {/* 右侧聊天区域 */}
      <Layout>
        <Header style={{ 
          background: '#fff',
          padding: '0 24px',
          borderBottom: '1px solid #f0f0f0',
          display: 'flex',
          alignItems: 'center',
        }}>
          <Space>
            <MessageOutlined style={{ fontSize: '20px', color: '#1890ff' }} />
            <Title level={4} style={{ margin: 0 }}>
              AI智能问答
            </Title>
          </Space>
        </Header>
        
        <Content style={{ 
          padding: '24px',
          background: '#f5f5f5',
          overflow: 'hidden',
        }}>
          <ChatBox
            subjectId={selectedSubject}
            subjects={subjects}
            onSubjectChange={setSelectedSubject}
          />
        </Content>
      </Layout>
    </Layout>
  );
};

export default ChatPage;