import React, { useState, useEffect } from 'react';
import { Layout } from 'antd';
import { MessageOutlined } from '@ant-design/icons';
import ChatBox from '../components/ChatBox';
import SubjectSelector from '../components/SubjectSelector';
import { getSubjects } from '../api/subjects';
import { Subject } from '../types';

const { Sider, Content } = Layout;

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
    <Layout style={{ height: '100%' }}>
      {/* 左侧历史对话栏 */}
      <Sider 
        width={260} 
        style={{ 
          background: '#fff',
          borderRight: '1px solid #f0f0f0',
          overflow: 'auto',
        }}
      >
        <div style={{ padding: '16px' }}>
          <div style={{ fontSize: 15, fontWeight: 600, color: '#0f172a', marginBottom: 12 }}>
            历史对话
          </div>
          
          {/* 学科选择器 */}
          <div style={{ marginBottom: '16px' }}>
            <SubjectSelector
              value={selectedSubject}
              onChange={setSelectedSubject}
              style={{ width: '100%' }}
            />
          </div>
          
          {/* 历史对话列表 */}
          <div style={{ 
            background: '#f8fafc',
            borderRadius: '10px',
            padding: '16px',
            minHeight: '200px',
          }}>
            <div style={{ 
              textAlign: 'center', 
              color: '#94a3b8',
              padding: '32px 0',
              fontSize: 13,
            }}>
              <MessageOutlined style={{ fontSize: '28px', marginBottom: '10px', color: '#cbd5e1' }} />
              <div>暂无历史对话</div>
              <div style={{ marginTop: '6px', fontSize: 12 }}>
                选择学科后开始提问
              </div>
            </div>
          </div>
        </div>
      </Sider>

      {/* 右侧聊天区域 */}
      <Layout style={{ overflow: 'hidden', height: '100%' }}>
        <Content style={{ 
          padding: '20px',
          background: '#f8fafc',
          overflow: 'hidden',
          height: '100%',
          display: 'flex',
          flexDirection: 'column',
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