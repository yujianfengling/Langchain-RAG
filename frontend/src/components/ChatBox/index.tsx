import React, { useState, useRef, useEffect } from 'react';
import { Input, Button, List, Avatar, Typography, Space, Spin, message } from 'antd';
import { SendOutlined, UserOutlined, RobotOutlined } from '@ant-design/icons';
import { ChatMessage, Subject } from '../../types';
import { sendMessage } from '../../api/chat';
import MarkdownIt from 'markdown-it';
import hljs from 'highlight.js';
import './ChatBox.css';

const { Text, Title } = Typography;
const { TextArea } = Input;

// 初始化Markdown渲染器
const md: MarkdownIt = new MarkdownIt({
  html: true,
  linkify: true,
  typographer: true,
  highlight: function (str: string, lang: string): string {
    if (lang && hljs.getLanguage(lang)) {
      try {
        return '<pre class="hljs"><code>' +
               hljs.highlight(str, { language: lang, ignoreIllegals: true }).value +
               '</code></pre>';
      } catch (__) {}
    }
    return '<pre class="hljs"><code>' + md.utils.escapeHtml(str) + '</code></pre>';
  }
});

interface ChatBoxProps {
  subjectId: string;
  subjects: Subject[];
  onSubjectChange?: (subjectId: string) => void;
}

const ChatBox: React.FC<ChatBoxProps> = ({ subjectId, subjects }) => {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [inputValue, setInputValue] = useState('');
  const [loading, setLoading] = useState(false);
  const [conversationId, setConversationId] = useState<string>('');
  const messagesEndRef = useRef<HTMLDivElement>(null);

  // 生成会话ID
  useEffect(() => {
    const savedConversationId = localStorage.getItem('chat_conversation_id');
    if (savedConversationId) {
      setConversationId(savedConversationId);
    } else {
      const newId = Date.now().toString();
      setConversationId(newId);
      localStorage.setItem('chat_conversation_id', newId);
    }
  }, []);

  // 恢复历史消息
  useEffect(() => {
    if (conversationId) {
      const savedMessages = localStorage.getItem(`chat_messages_${conversationId}`);
      if (savedMessages) {
        try {
          const parsedMessages = JSON.parse(savedMessages);
          setMessages(parsedMessages);
        } catch (error) {
          console.error('解析历史消息失败:', error);
        }
      }
    }
  }, [conversationId]);

  // 保存消息到localStorage
  useEffect(() => {
    if (conversationId && messages.length > 0) {
      localStorage.setItem(`chat_messages_${conversationId}`, JSON.stringify(messages));
    }
  }, [messages, conversationId]);

  // 自动滚动到底部
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  // 发送消息
  const handleSend = async () => {
    if (!inputValue.trim()) {
      message.warning('请输入问题');
      return;
    }

    if (!subjectId) {
      message.warning('请选择学科');
      return;
    }

    // 添加用户消息
    const userMessage: ChatMessage = {
      id: Date.now().toString(),
      role: 'user',
      content: inputValue.trim(),
      timestamp: Date.now(),
    };

    setMessages(prev => [...prev, userMessage]);
    setInputValue('');
    setLoading(true);

    try {
      // 构建历史消息
      const history = messages
        .filter(msg => msg.role === 'user' || msg.role === 'assistant')
        .map(msg => ({
          role: msg.role,
          content: msg.content,
        }));

      // 调用API
      const response = await sendMessage({
        question: userMessage.content,
        subject_id: subjectId,
        history,
      });

      // 添加AI回复
      const aiMessage: ChatMessage = {
        id: (Date.now() + 1).toString(),
        role: 'assistant',
        content: response.answer,
        timestamp: Date.now(),
        sources: response.sources,
        model: response.model,
        latency: response.latency,
      };

      setMessages(prev => [...prev, aiMessage]);
    } catch (error) {
      console.error('发送消息失败:', error);
      message.error('发送消息失败，请稍后重试');
      
      // 添加错误消息
      const errorMessage: ChatMessage = {
        id: (Date.now() + 1).toString(),
        role: 'assistant',
        content: '抱歉，处理您的问题时出现了错误。请稍后重试。',
        timestamp: Date.now(),
      };
      setMessages(prev => [...prev, errorMessage]);
    } finally {
      setLoading(false);
    }
  };

  // 渲染Markdown内容
  const renderMarkdown = (content: string) => {
    return { __html: md.render(content) };
  };

  // 渲染消息列表
  const renderMessage = (msg: ChatMessage) => {
    const isUser = msg.role === 'user';
    
    return (
      <List.Item style={{ 
        justifyContent: isUser ? 'flex-end' : 'flex-start',
        padding: '12px 0',
        border: 'none',
      }}>
        <div style={{
          display: 'flex',
          alignItems: 'flex-start',
          gap: '12px',
          maxWidth: '80%',
          flexDirection: isUser ? 'row-reverse' : 'row',
        }}>
          <Avatar 
            icon={isUser ? <UserOutlined /> : <RobotOutlined />} 
            style={{ 
              backgroundColor: isUser ? '#1890ff' : '#52c41a',
              flexShrink: 0,
            }}
          />
          
          <div style={{
            background: isUser ? '#1890ff' : '#f6f6f6',
            color: isUser ? '#fff' : '#333',
            padding: '12px 16px',
            borderRadius: '12px',
            maxWidth: '100%',
            wordBreak: 'break-word',
          }}>
            {isUser ? (
              <Text style={{ color: '#fff', margin: 0 }}>{msg.content}</Text>
            ) : (
              <div 
                className="markdown-body"
                dangerouslySetInnerHTML={renderMarkdown(msg.content)}
                style={{
                  background: 'transparent',
                  color: '#333',
                }}
              />
            )}
            
            {/* 显示引用来源 */}
            {msg.sources && msg.sources.length > 0 && (
              <div style={{ 
                marginTop: '8px', 
                padding: '8px', 
                background: 'rgba(0,0,0,0.05)',
                borderRadius: '6px',
                fontSize: '12px',
              }}>
                <Text type="secondary" style={{ fontSize: '12px' }}>
                  参考来源: {msg.sources.join(', ')}
                </Text>
              </div>
            )}
            
            {/* 显示模型和延迟信息 */}
            {msg.model && (
              <div style={{ marginTop: '4px' }}>
                <Text type="secondary" style={{ fontSize: '11px' }}>
                  模型: {msg.model} | 耗时: {msg.latency?.toFixed(2)}s
                </Text>
              </div>
            )}
          </div>
        </div>
      </List.Item>
    );
  };

  return (
    <div style={{ 
      display: 'flex', 
      flexDirection: 'column', 
      height: '100%',
      background: '#fff',
      borderRadius: '12px',
      boxShadow: '0 2px 8px rgba(0,0,0,0.1)',
      overflow: 'hidden',
    }}>
      {/* 头部 */}
      <div style={{
        padding: '16px 24px',
        borderBottom: '1px solid #f0f0f0',
        background: '#fafafa',
      }}>
        <Space>
          <Title level={5} style={{ margin: 0 }}>
            AI智能问答
          </Title>
          <Text type="secondary">
            当前学科: {subjects.find(s => s.subject_id === subjectId)?.name || '未选择'}
          </Text>
        </Space>
      </div>

      {/* 消息列表 */}
      <div style={{
        flex: 1,
        overflow: 'auto',
        padding: '24px',
        background: '#fff',
      }}>
        {messages.length === 0 ? (
          <div style={{
            display: 'flex',
            flexDirection: 'column',
            alignItems: 'center',
            justifyContent: 'center',
            height: '100%',
            color: '#999',
          }}>
            <RobotOutlined style={{ fontSize: '48px', marginBottom: '16px' }} />
            <Text type="secondary">开始你的智能问答之旅</Text>
            <Text type="secondary" style={{ fontSize: '12px', marginTop: '8px' }}>
              选择学科后，输入你的问题
            </Text>
          </div>
        ) : (
          <List
            dataSource={messages}
            renderItem={renderMessage}
            style={{ background: 'transparent' }}
          />
        )}
        {loading && (
          <div style={{ padding: '12px 0', textAlign: 'left' }}>
            <Space>
              <Avatar icon={<RobotOutlined />} style={{ backgroundColor: '#52c41a' }} />
              <Spin tip="AI思考中..." />
            </Space>
          </div>
        )}
        <div ref={messagesEndRef} />
      </div>

      {/* 输入区域 */}
      <div style={{
        padding: '16px 24px',
        borderTop: '1px solid #f0f0f0',
        background: '#fafafa',
      }}>
        <Space.Compact style={{ width: '100%' }}>
          <TextArea
            value={inputValue}
            onChange={(e) => setInputValue(e.target.value)}
            placeholder="输入你的问题... (Ctrl+Enter 发送)"
            autoSize={{ minRows: 1, maxRows: 4 }}
            onPressEnter={(e) => {
              if (e.ctrlKey) {
                handleSend();
              }
            }}
            disabled={loading}
            style={{ flex: 1 }}
          />
          <Button
            type="primary"
            icon={<SendOutlined />}
            onClick={handleSend}
            loading={loading}
            disabled={!inputValue.trim() || !subjectId}
            style={{ height: 'auto' }}
          >
            发送
          </Button>
        </Space.Compact>
      </div>
    </div>
  );
};

export default ChatBox;