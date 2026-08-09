import React, { useState, useEffect, useCallback, useMemo } from 'react';
import { Layout, Button, Empty, Space, Typography, message, Popconfirm, Spin } from 'antd';
import { MessageOutlined, PlusOutlined, DeleteOutlined } from '@ant-design/icons';
import ChatBox from '../components/ChatBox';
import SubjectSelector from '../components/SubjectSelector';
import { getSubjects } from '../api/subjects';
import { getConversations, createConversation, deleteConversation } from '../api/conversations';
import { Subject, Conversation } from '../types';

const { Sider, Content } = Layout;
const { Text } = Typography;

const ChatPage: React.FC = () => {
  const [selectedSubject, setSelectedSubject] = useState<string>('');
  const [subjects, setSubjects] = useState<Subject[]>([]);
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [activeConvId, setActiveConvId] = useState<string>('');
  const [loading, setLoading] = useState(false);
  const [creating, setCreating] = useState(false);

  // 加载学科列表
  const loadSubjects = useCallback(async () => {
    try {
      const data = await getSubjects();
      setSubjects(data);
    } catch (error) {
      console.error('加载学科列表失败:', error);
    }
  }, []);

  // 加载会话列表（按学科过滤）
  const loadConversations = useCallback(async (sid?: string) => {
    setLoading(true);
    try {
      const res: any = await getConversations(sid || undefined);
      setConversations(res?.data || []);
    } catch (error) {
      console.error('加载会话列表失败:', error);
      message.error('加载会话列表失败');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadSubjects();
    loadConversations();
  }, [loadSubjects, loadConversations]);

  // 学科切换时不得继续沿用其他学科的 conversation
  const handleSubjectChange = (sid: string) => {
    setSelectedSubject(sid);
    setActiveConvId('');
    loadConversations(sid);
  };

  // 新建对话（绑定当前学科）
  const handleNewConversation = async () => {
    if (!selectedSubject) {
      message.warning('请先选择学科');
      return;
    }
    setCreating(true);
    try {
      const res: any = await createConversation(selectedSubject);
      const conv = res?.data;
      if (conv?.id) {
        setActiveConvId(conv.id);
        await loadConversations(selectedSubject);
      }
    } catch (error) {
      message.error('创建会话失败');
    } finally {
      setCreating(false);
    }
  };

  // 删除会话
  const handleDeleteConversation = async (convId: string) => {
    try {
      await deleteConversation(convId);
      if (activeConvId === convId) setActiveConvId('');
      await loadConversations(selectedSubject || undefined);
      message.success('会话已删除');
    } catch (error) {
      message.error('删除会话失败');
    }
  };

  // 按学科分组
  const grouped = useMemo(() => {
    const map = new Map<string, Conversation[]>();
    for (const c of conversations) {
      const list = map.get(c.subject_id) || [];
      list.push(c);
      map.set(c.subject_id, list);
    }
    return Array.from(map.entries());
  }, [conversations]);

  // 学科名映射
  const subjectName = useCallback((sid: string) => {
    return subjects.find(s => s.subject_id === sid)?.name || sid;
  }, [subjects]);

  return (
    <Layout style={{ height: '100%' }}>
      {/* 左侧历史对话栏 */}
      <Sider
        width={280}
        style={{
          background: '#fff',
          borderRight: '1px solid #f0f0f0',
          overflow: 'auto',
        }}
      >
        <div style={{ padding: '16px' }}>
          <Space style={{ width: '100%', justifyContent: 'space-between', marginBottom: 12 }}>
            <span style={{ fontSize: 15, fontWeight: 600, color: '#0f172a' }}>历史对话</span>
            <Button
              type="primary"
              size="small"
              icon={<PlusOutlined />}
              onClick={handleNewConversation}
              loading={creating}
              disabled={!selectedSubject}
            >
              新建对话
            </Button>
          </Space>

          {/* 学科选择器 */}
          <div style={{ marginBottom: '16px' }}>
            <SubjectSelector
              value={selectedSubject}
              onChange={handleSubjectChange}
              style={{ width: '100%' }}
            />
          </div>

          {/* 历史会话列表（按学科分组） */}
          <div>
            {loading ? (
              <div style={{ textAlign: 'center', padding: '24px 0' }}>
                <Spin size="small" />
              </div>
            ) : grouped.length === 0 ? (
              <div style={{
                background: '#f8fafc',
                borderRadius: '10px',
                padding: '16px',
              }}>
                <Empty
                  image={Empty.PRESENTED_IMAGE_SIMPLE}
                  description={
                    <div>
                      <div style={{ fontSize: 13, color: '#94a3b8' }}>暂无历史对话</div>
                      <div style={{ marginTop: 6, fontSize: 12, color: '#94a3b8' }}>
                        选择学科后点击"新建对话"开始
                      </div>
                    </div>
                  }
                />
              </div>
            ) : (
              grouped.map(([sid, convs]) => (
                <div key={sid} style={{ marginBottom: 8 }}>
                  <div style={{
                    fontSize: 12,
                    color: '#94a3b8',
                    fontWeight: 600,
                    padding: '4px 8px',
                    textTransform: 'uppercase',
                  }}>
                    ▼ {subjectName(sid)}
                  </div>
                  {convs.map(c => (
                    <div
                      key={c.id}
                      onClick={() => setActiveConvId(c.id)}
                      style={{
                        display: 'flex',
                        alignItems: 'center',
                        justifyContent: 'space-between',
                        padding: '8px 10px',
                        borderRadius: 8,
                        cursor: 'pointer',
                        background: activeConvId === c.id ? '#f0fdfa' : 'transparent',
                        transition: 'background 0.2s',
                      }}
                    >
                      <Space size={8} style={{ minWidth: 0, flex: 1 }}>
                        <MessageOutlined style={{ color: activeConvId === c.id ? '#0f766e' : '#94a3b8', fontSize: 13 }} />
                        <Text
                          style={{
                            fontSize: 13,
                            color: activeConvId === c.id ? '#0f766e' : '#0f172a',
                            overflow: 'hidden',
                            textOverflow: 'ellipsis',
                            whiteSpace: 'nowrap',
                          }}
                        >
                          {c.title || '新对话'}
                        </Text>
                      </Space>
                      <Popconfirm
                        title="删除该会话？"
                        description="会话及其全部消息将被删除"
                        okText="删除"
                        cancelText="取消"
                        onConfirm={(e) => { e?.stopPropagation(); handleDeleteConversation(c.id); }}
                        onCancel={(e) => e?.stopPropagation()}
                      >
                        <Button
                          type="text"
                          size="small"
                          danger
                          icon={<DeleteOutlined />}
                          onClick={(e) => e.stopPropagation()}
                        />
                      </Popconfirm>
                    </div>
                  ))}
                </div>
              ))
            )}
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
            conversationId={activeConvId || undefined}
            onConversationCreated={(id) => {
              setActiveConvId(id);
              // 新会话创建后立即刷新历史会话列表
              loadConversations(selectedSubject);
            }}
            onSubjectChange={setSelectedSubject}
          />
        </Content>
      </Layout>
    </Layout>
  );
};

export default ChatPage;
